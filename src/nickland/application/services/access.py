"""Access control: roles, a person's own exceptions, sessions and the sign-in policy.

Roles carry the rule; an exception carries the person. The sales person trusted with
discounts, or the cashier kept off refunds, is an exception with a reason rather than a
new role made for one person. Nobody amends their own access: a grant to oneself is how
an account escalates, so it is another administrator's decision.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditAction, AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.identity import IdentityService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.identity import AccessPolicyTerms, OverrideEffect
from nickland.domain.permissions import require_registered
from nickland.persistence.models import (
    AccessPolicy,
    Role,
    RolePermission,
    User,
    UserPermissionOverride,
    UserRole,
)
from nickland.persistence.models import Session as SessionRecord
from nickland.platform.clock import Clock, SystemClock


@dataclass(slots=True)
class OverrideView:
    permission_code: str
    effect: OverrideEffect
    reason: str
    set_at: datetime
    set_by: str | None


@dataclass(slots=True)
class UserAccess:
    user: User
    roles: list[Role]
    is_administrator: bool
    from_roles: set[str]
    overrides: list[OverrideView] = field(default_factory=list)
    effective: set[str] = field(default_factory=set)


@dataclass(slots=True)
class SessionView:
    id: uuid.UUID
    issued_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    device: str | None
    address: str | None


class AccessControlService:
    def __init__(
        self,
        session: DbSession,
        *,
        clock: Clock | None = None,
        identity: IdentityService | None = None,
    ) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._identity = identity or IdentityService(session, clock=self._clock)

    # -- roles ----------------------------------------------------------------------------

    def create_role(
        self,
        actor: AnyActor,
        *,
        name: str,
        description: str | None = None,
        copy_from: str | None = None,
    ) -> Role:
        """A new role, empty or starting from another role's permissions."""
        actor.require("role.manage")
        title = " ".join(name.split())
        if not 2 <= len(title) <= 80:
            raise ValidationError("A role is named in 2 to 80 characters.", field="name")
        if self._db.scalar(
            select(Role.id).where(
                Role.business_id == actor.business_id, func.lower(Role.name) == title.lower()
            )
        ):
            raise ConflictError(f"A role named {title!r} already exists.")
        source = self._role(actor, copy_from) if copy_from else None
        role = Role(
            business_id=actor.business_id,
            code=self._code_for(actor.business_id, title),
            name=title,
            description=(description or "").strip() or None,
            is_administrator=False,
            is_read_only=False,
            is_system=False,
        )
        self._db.add(role)
        self._db.flush()
        granted = sorted(item.permission_code for item in source.permissions) if source else []
        for code in granted:
            self._db.add(
                RolePermission(business_id=actor.business_id, role_fk=role.id, permission_code=code)
            )
        self._db.flush()
        self._db.refresh(role)
        self._audit.record(
            actor,
            AuditAction.ROLE_CREATED,
            entity_type="role",
            entity_id=role.id,
            entity_label=role.code,
            after={
                "name": role.name,
                "copied_from": source.code if source else None,
                "permissions": len(granted),
            },
        )
        return role

    def amend_role(self, actor: AnyActor, code: str, *, name: str, description: str | None) -> Role:
        actor.require("role.manage")
        role = self._role(actor, code)
        title = " ".join(name.split())
        if not 2 <= len(title) <= 80:
            raise ValidationError("A role is named in 2 to 80 characters.", field="name")
        clash = self._db.scalar(
            select(Role.id).where(
                Role.business_id == actor.business_id,
                func.lower(Role.name) == title.lower(),
                Role.id != role.id,
            )
        )
        if clash:
            raise ConflictError(f"A role named {title!r} already exists.")
        before = {"name": role.name, "description": role.description}
        role.name = title
        role.description = (description or "").strip() or None
        self._db.flush()
        self._audit.record(
            actor,
            AuditAction.ROLE_AMENDED,
            entity_type="role",
            entity_id=role.id,
            entity_label=role.code,
            before=before,
            after={"name": role.name, "description": role.description},
        )
        return role

    def remove_role(self, actor: AnyActor, code: str, *, reason: str) -> None:
        """Remove a role nobody holds. The supplied roles stay."""
        actor.require("role.manage")
        if not reason.strip():
            raise ValidationError("A reason is required.", field="reason")
        role = self._role(actor, code)
        if role.is_system:
            raise ConflictError(f"{role.name} is supplied with the system and cannot be removed.")
        holders = self._db.scalar(
            select(func.count(UserRole.id)).where(UserRole.role_fk == role.id)
        )
        if holders:
            raise ConflictError(
                f"{holders} user(s) hold {role.name}. Move them to another role first."
            )
        self._audit.record(
            actor,
            AuditAction.ROLE_REMOVED,
            entity_type="role",
            entity_id=role.id,
            entity_label=role.code,
            reason=reason.strip(),
            before={"name": role.name, "permissions": len(role.permissions)},
        )
        self._db.delete(role)
        self._db.flush()

    def holders(self, actor: AnyActor) -> dict[uuid.UUID, int]:
        """How many users hold each role."""
        actor.require("user.view")
        rows = self._db.execute(
            select(UserRole.role_fk, func.count(UserRole.id))
            .where(UserRole.business_id == actor.business_id)
            .group_by(UserRole.role_fk)
        ).all()
        return {role_id: int(count) for role_id, count in rows}

    # -- a person's access --------------------------------------------------------------

    def user_access(self, actor: AnyActor, user_id: uuid.UUID) -> UserAccess:
        actor.require("user.view")
        user = self._user(actor, user_id)
        roles = list(
            self._db.scalars(
                select(Role)
                .join(UserRole, UserRole.role_fk == Role.id)
                .where(UserRole.user_fk == user.id)
                .order_by(Role.name)
            ).all()
        )
        from_roles = {item.permission_code for role in roles for item in role.permissions}
        overrides = [
            OverrideView(
                permission_code=row.permission_code,
                effect=OverrideEffect(row.effect),
                reason=row.reason,
                set_at=row.set_at,
                set_by=self._name(row.set_by_id),
            )
            for row in self._overrides(user.id)
        ]
        effective = set(self._identity.actor_for(user).permissions)
        return UserAccess(
            user=user,
            roles=roles,
            is_administrator=any(role.is_administrator for role in roles),
            from_roles=from_roles,
            overrides=overrides,
            effective=effective,
        )

    def set_overrides(
        self,
        actor: AnyActor,
        user_id: uuid.UUID,
        overrides: dict[str, OverrideEffect | str],
        *,
        reason: str,
    ) -> UserAccess:
        """Replace a person's exceptions. A permission left out follows their roles."""
        actor.require("role.manage")
        if not reason.strip():
            raise ValidationError("A reason is required.", field="reason")
        user = self._user(actor, user_id)
        if actor.user_id is not None and user.id == actor.user_id:
            raise ConflictError("Your own access is amended by another administrator.")
        access = self.user_access(actor, user.id)
        if access.is_administrator and overrides:
            raise ConflictError(
                f"{user.full_name} is an administrator and holds every permission. Give them "
                "another role to narrow what they may do."
            )
        wanted = {code: OverrideEffect(effect) for code, effect in overrides.items()}
        require_registered(set(wanted))
        for code, effect in wanted.items():
            in_roles = code in access.from_roles
            if effect is OverrideEffect.GRANT and in_roles:
                raise ValidationError(f"{code} already comes with {user.full_name}'s roles.")
            if effect is OverrideEffect.WITHHOLD and not in_roles:
                raise ValidationError(f"{code} does not come with {user.full_name}'s roles.")

        existing = {row.permission_code: row for row in self._overrides(user.id)}
        before = {code: row.effect for code, row in existing.items()}
        now = self._clock.now()
        for code, kept in existing.items():
            if code not in wanted:
                self._db.delete(kept)
        for code, effect in wanted.items():
            row = existing.get(code)
            if row is None:
                self._db.add(
                    UserPermissionOverride(
                        business_id=actor.business_id,
                        user_fk=user.id,
                        permission_code=code,
                        effect=effect.value,
                        reason=reason.strip(),
                        set_at=now,
                        set_by_id=actor.user_id,
                    )
                )
            elif row.effect != effect.value:
                row.effect = effect.value
                row.reason = reason.strip()
                row.set_at = now
                row.set_by_id = actor.user_id
        self._db.flush()
        self._audit.record(
            actor,
            AuditAction.ACCESS_OVERRIDDEN,
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            reason=reason.strip(),
            before={"exceptions": _describe(before)},
            after={"exceptions": _describe({c: e.value for c, e in wanted.items()})},
        )
        return self.user_access(actor, user.id)

    # -- sessions -------------------------------------------------------------------------

    def sessions(self, actor: AnyActor, user_id: uuid.UUID) -> list[SessionView]:
        """A person's sessions still in force."""
        actor.require("user.view")
        user = self._user(actor, user_id)
        now = self._clock.now()
        policy = self._identity.terms(user.business_id).session
        records = self._db.scalars(
            select(SessionRecord)
            .where(SessionRecord.user_fk == user.id, SessionRecord.revoked_at.is_(None))
            .order_by(SessionRecord.last_seen_at.desc())
        ).all()
        return [
            SessionView(
                id=record.id,
                issued_at=record.issued_at,
                last_seen_at=record.last_seen_at,
                expires_at=record.expires_at,
                device=record.device,
                address=record.address,
            )
            for record in records
            if policy.is_valid(record.issued_at, record.last_seen_at, now)
        ]

    def sign_out_everywhere(self, actor: AnyActor, user_id: uuid.UUID, *, reason: str) -> int:
        actor.require("user.manage")
        if not reason.strip():
            raise ValidationError("A reason is required.", field="reason")
        user = self._user(actor, user_id)
        ended = self._identity.revoke_all_sessions(actor, user.id)
        self._audit.record(
            actor,
            AuditAction.SIGN_OUT,
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            reason=reason.strip(),
            after={"sessions_ended": ended},
        )
        return ended

    # -- the sign-in policy ---------------------------------------------------------------

    def policy(self, actor: AnyActor) -> tuple[AccessPolicyTerms, bool]:
        """The policy in force, and whether the business has set its own."""
        actor.require("user.view")
        assert actor.business_id is not None
        stored = self._db.scalar(
            select(AccessPolicy.id).where(AccessPolicy.business_id == actor.business_id)
        )
        return self._identity.terms(actor.business_id), stored is not None

    def set_policy(
        self, actor: AnyActor, terms: AccessPolicyTerms, *, reason: str
    ) -> AccessPolicyTerms:
        actor.require("role.manage")
        if not reason.strip():
            raise ValidationError("A reason is required.", field="reason")
        terms.validate()
        assert actor.business_id is not None
        before = self._identity.terms(actor.business_id)
        stored = self._db.scalar(
            select(AccessPolicy).where(AccessPolicy.business_id == actor.business_id)
        )
        if stored is None:
            stored = AccessPolicy(business_id=actor.business_id)
            self._db.add(stored)
        for name in (
            "minimum_length",
            "require_uppercase",
            "require_lowercase",
            "require_digit",
            "require_symbol",
            "reuse_window",
            "lockout_threshold",
            "lockout_minutes",
            "idle_minutes",
            "absolute_hours",
        ):
            setattr(stored, name, getattr(terms, name))
        stored.amended_by_id = actor.user_id
        self._db.flush()
        self._audit.record(
            actor,
            AuditAction.ACCESS_POLICY_AMENDED,
            entity_type="access_policy",
            entity_id=stored.id,
            entity_label="Sign-in policy",
            reason=reason.strip(),
            before=_terms(before),
            after=_terms(terms),
        )
        return self._identity.terms(actor.business_id)

    # -- internals ------------------------------------------------------------------------

    def _role(self, actor: AnyActor, code: str) -> Role:
        role = self._db.scalar(
            select(Role).where(Role.business_id == actor.business_id, Role.code == code)
        )
        if role is None:
            raise NotFoundError("Role not found.")
        return role

    def _user(self, actor: AnyActor, user_id: uuid.UUID) -> User:
        user = self._db.get(User, user_id)
        if user is None or user.business_id != actor.business_id:
            raise NotFoundError("User not found.")
        return user

    def _overrides(self, user_id: uuid.UUID) -> list[UserPermissionOverride]:
        return list(
            self._db.scalars(
                select(UserPermissionOverride)
                .where(UserPermissionOverride.user_fk == user_id)
                .order_by(UserPermissionOverride.permission_code)
            ).all()
        )

    def _name(self, user_id: uuid.UUID | None) -> str | None:
        user = self._db.get(User, user_id) if user_id else None
        return user.full_name if user else None

    def _code_for(self, business_id: uuid.UUID, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "role"
        code, suffix = base, 2
        while self._db.scalar(
            select(Role.id).where(Role.business_id == business_id, Role.code == code)
        ):
            code = f"{base}_{suffix}"
            suffix += 1
        return code


def _describe(exceptions: dict[str, str]) -> str:
    return ", ".join(f"{effect} {code}" for code, effect in sorted(exceptions.items())) or "none"


def _terms(terms: AccessPolicyTerms) -> dict[str, object]:
    return {
        "minimum_length": terms.minimum_length,
        "require_uppercase": terms.require_uppercase,
        "require_lowercase": terms.require_lowercase,
        "require_digit": terms.require_digit,
        "require_symbol": terms.require_symbol,
        "reuse_window": terms.reuse_window,
        "lockout_threshold": terms.lockout_threshold,
        "lockout_minutes": terms.lockout_minutes,
        "idle_minutes": terms.idle_minutes,
        "absolute_hours": terms.absolute_hours,
    }
