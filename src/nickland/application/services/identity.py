"""Authentication, session and user services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditAction, AuditRecorder
from nickland.application.context import Actor, AnyActor, UnauthenticatedActor
from nickland.domain.errors import (
    AccountLockedError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from nickland.domain.identity import (
    LockoutPolicy,
    PasswordPolicy,
    SessionPolicy,
    validate_password,
    validate_username,
)
from nickland.domain.permissions import require_registered
from nickland.persistence.base import as_dict
from nickland.persistence.models import (
    PasswordHistory,
    Role,
    RolePermission,
    User,
    UserRole,
)
from nickland.persistence.models import (
    Session as SessionRecord,
)
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.ids import new_reference
from nickland.platform.logging import get_logger
from nickland.platform.passwords import hash_password, hash_token, verify_password

logger = get_logger(__name__)

_GENERIC_FAILURE = "Username or password is incorrect."
"""One message for every failure mode, so the response does not disclose whether a
username exists."""


@dataclass(frozen=True, slots=True)
class SignInResult:
    """A successful authentication."""

    token: str
    actor: Actor
    expires_at: object
    must_change_password: bool


class IdentityService:
    """Authentication, sessions and user administration."""

    def __init__(
        self,
        session: DbSession,
        *,
        clock: Clock | None = None,
        password_policy: PasswordPolicy | None = None,
        lockout_policy: LockoutPolicy | None = None,
        session_policy: SessionPolicy | None = None,
    ) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self.password_policy = password_policy or PasswordPolicy()
        self.lockout_policy = lockout_policy or LockoutPolicy()
        self.session_policy = session_policy or SessionPolicy()

    # -- authentication ---------------------------------------------------------------

    def sign_in(
        self,
        username: str,
        password: str,
        *,
        business_id: uuid.UUID,
        device: str | None = None,
        address: str | None = None,
    ) -> SignInResult:
        """Authenticate and open a session.

        A failed attempt increments the counter and may lock the account. Every failure
        mode returns the same message.
        """
        now = self._clock.now()
        candidate = username.strip().lower()

        user = self._db.scalar(
            select(User).where(User.business_id == business_id, User.username == candidate)
        )

        if user is None:
            # Hash anyway, so the response time does not reveal that the user is absent.
            verify_password(password, _DUMMY_HASH)
            self._record_failure(business_id, candidate, device, "unknown username")
            self._persist_failure()
            raise AuthenticationError(_GENERIC_FAILURE)

        if self.lockout_policy.is_locked(user.locked_until, now):
            assert user.locked_until is not None
            raise AccountLockedError(self.lockout_policy.minutes_remaining(user.locked_until, now))

        if not user.is_active:
            self._record_failure(business_id, candidate, device, "account inactive")
            raise AuthenticationError(_GENERIC_FAILURE)

        if not verify_password(password, user.password_hash):
            user.failed_attempts += 1
            locked_until = self.lockout_policy.lock_until(user.failed_attempts, now)
            if locked_until is not None:
                user.locked_until = locked_until
                user.failed_attempts = 0
                self._audit.record(
                    UnauthenticatedActor(business_id=business_id, username=candidate),
                    AuditAction.ACCOUNT_LOCKED,
                    entity_type="user",
                    entity_id=user.id,
                    entity_label=user.username,
                    context={"locked_until": locked_until, "device": device},
                )
            self._record_failure(business_id, candidate, device, "incorrect password")
            self._persist_failure()
            raise AuthenticationError(_GENERIC_FAILURE)

        user.failed_attempts = 0
        user.locked_until = None
        user.last_signed_in_at = now

        token = new_reference()
        record = SessionRecord(
            business_id=business_id,
            user_fk=user.id,
            token_hash=hash_token(token),
            issued_at=now,
            last_seen_at=now,
            expires_at=self.session_policy.expires_at(now),
            device=device,
            address=address,
        )
        self._db.add(record)
        self._db.flush()

        actor = self._build_actor(user, session_id=record.id, device=device)
        self._audit.record(
            actor,
            AuditAction.SIGN_IN,
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            context={"device": device, "address": address},
        )
        return SignInResult(
            token=token,
            actor=actor,
            expires_at=record.expires_at,
            must_change_password=user.must_change_password,
        )

    def resolve_session(self, token: str, *, device: str | None = None) -> Actor:
        """Validate a session token and return its actor, refreshing the idle deadline."""
        now = self._clock.now()
        record = self._db.scalar(
            select(SessionRecord).where(SessionRecord.token_hash == hash_token(token))
        )
        if record is None or record.revoked_at is not None:
            raise AuthenticationError("Session is not valid.")
        if not self.session_policy.is_valid(record.issued_at, record.last_seen_at, now):
            record.revoked_at = now
            raise AuthenticationError("Session has expired. Sign in again.")

        user = self._db.get(User, record.user_fk)
        if user is None or not user.is_active:
            record.revoked_at = now
            raise AuthenticationError("Session is not valid.")

        record.last_seen_at = now
        return self._build_actor(user, session_id=record.id, device=device or record.device)

    def sign_out(self, actor: Actor) -> None:
        """Revoke the actor's session."""
        if actor.session_id is None:
            return
        record = self._db.get(SessionRecord, actor.session_id)
        if record is not None and record.revoked_at is None:
            record.revoked_at = self._clock.now()
        self._audit.record(
            actor,
            AuditAction.SIGN_OUT,
            entity_type="session",
            entity_id=actor.session_id,
        )

    def revoke_all_sessions(self, actor: AnyActor, user_id: uuid.UUID) -> int:
        """Revoke every live session of a user. Used on deactivation and password change."""
        now = self._clock.now()
        records = self._db.scalars(
            select(SessionRecord).where(
                SessionRecord.business_id == actor.business_id,
                SessionRecord.user_fk == user_id,
                SessionRecord.revoked_at.is_(None),
            )
        ).all()
        for record in records:
            record.revoked_at = now
        return len(records)

    # -- password ---------------------------------------------------------------------

    def change_password(
        self, actor: Actor, *, current_password: str, new_password: str
    ) -> None:
        """Change the acting user's own password."""
        user = self._require_user(actor.business_id, actor.user_id)
        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect.")
        self._set_password(actor, user, new_password)

    def reset_password(
        self, actor: AnyActor, user_id: uuid.UUID, new_password: str, *, reason: str
    ) -> None:
        """Reset another user's password. Requires the user administration permission."""
        actor.require("user.manage")
        if not reason.strip():
            raise ValidationError("A reason is required to reset a password.", field="reason")
        user = self._require_user(actor.business_id, user_id)
        self._set_password(actor, user, new_password, reason=reason, force_change=True)

    def _set_password(
        self,
        actor: AnyActor,
        user: User,
        new_password: str,
        *,
        reason: str | None = None,
        force_change: bool = False,
    ) -> None:
        validate_password(new_password, self.password_policy, username=user.username)

        window = self.password_policy.reuse_window
        if window > 0:
            # Autoflush is off, so pending history rows must be flushed to be visible.
            self._db.flush()
            previous = self._db.scalars(
                select(PasswordHistory)
                .where(PasswordHistory.user_fk == user.id)
                .order_by(PasswordHistory.created_at.desc())
                .limit(window)
            ).all()
            if verify_password(new_password, user.password_hash) or any(
                verify_password(new_password, entry.password_hash) for entry in previous
            ):
                raise ValidationError(
                    f"Password was used recently. Choose one not among your last "
                    f"{window} passwords.",
                    field="password",
                )

        now = self._clock.now()
        self._db.add(
            PasswordHistory(
                business_id=user.business_id,
                user_fk=user.id,
                password_hash=user.password_hash,
                created_at=now,
            )
        )
        user.password_hash = hash_password(new_password)
        user.password_changed_at = now
        user.must_change_password = force_change
        self.revoke_all_sessions(actor, user.id)

        self._audit.record(
            actor,
            AuditAction.PASSWORD_CHANGED,
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            reason=reason,
        )

    # -- user administration ----------------------------------------------------------

    def create_user(
        self,
        actor: AnyActor,
        *,
        username: str,
        full_name: str,
        password: str,
        role_codes: list[str],
        email: str | None = None,
        telephone: str | None = None,
        branch_id: uuid.UUID | None = None,
        must_change_password: bool = True,
    ) -> User:
        """Create a user and assign roles."""
        actor.require("user.manage")
        candidate = validate_username(username)
        if not full_name.strip():
            raise ValidationError("Full name is required.", field="full_name")
        if not role_codes:
            raise ValidationError("At least one role is required.", field="role_codes")

        existing = self._db.scalar(
            select(User).where(
                User.business_id == actor.business_id, User.username == candidate
            )
        )
        if existing is not None:
            raise ConflictError(f"Username {candidate!r} is already in use.")

        validate_password(password, self.password_policy, username=candidate)

        user = User(
            business_id=actor.business_id,
            branch_id=branch_id,
            username=candidate,
            full_name=full_name.strip(),
            email=email,
            telephone=telephone,
            password_hash=hash_password(password),
            password_changed_at=self._clock.now(),
            must_change_password=must_change_password,
        )
        self._db.add(user)
        self._db.flush()

        for code in role_codes:
            self.assign_role(actor, user.id, code, audit=False)

        self._db.flush()
        self._audit.record(
            actor,
            AuditAction.USER_CREATED,
            entity_type="user",
            entity_id=user.id,
            entity_label=candidate,
            after={
                "username": candidate,
                "full_name": user.full_name,
                "roles": ", ".join(sorted(role_codes)),
            },
        )
        return user

    def amend_user(
        self,
        actor: AnyActor,
        user_id: uuid.UUID,
        *,
        full_name: str | None = None,
        email: str | None = None,
        telephone: str | None = None,
        branch_id: uuid.UUID | None = None,
    ) -> User:
        """Amend a user's details."""
        actor.require("user.manage")
        user = self._require_user(actor.business_id, user_id)
        before = as_dict(user)

        if full_name is not None:
            if not full_name.strip():
                raise ValidationError("Full name is required.", field="full_name")
            user.full_name = full_name.strip()
        if email is not None:
            user.email = email or None
        if telephone is not None:
            user.telephone = telephone or None
        if branch_id is not None:
            user.branch_id = branch_id

        self._audit.record_change(
            actor,
            AuditAction.USER_AMENDED,
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            before=before,
            after=as_dict(user),
        )
        return user

    def set_active(
        self, actor: AnyActor, user_id: uuid.UUID, *, active: bool, reason: str
    ) -> User:
        """Deactivate or reactivate a user. Deactivation revokes live sessions."""
        actor.require("user.manage")
        if not reason.strip():
            raise ValidationError("A reason is required.", field="reason")
        user = self._require_user(actor.business_id, user_id)

        if not active and user.id == actor.user_id:
            raise ConflictError("You cannot deactivate your own account.")
        if not active and self._is_last_active_administrator(user):
            raise ConflictError(
                "This is the only active administrator. Appoint another before "
                "deactivating this account."
            )

        user.is_active = active
        if not active:
            self.revoke_all_sessions(actor, user.id)
        else:
            user.failed_attempts = 0
            user.locked_until = None

        self._audit.record(
            actor,
            AuditAction.USER_REACTIVATED if active else AuditAction.USER_DEACTIVATED,
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            reason=reason,
        )
        return user

    def unlock(self, actor: AnyActor, user_id: uuid.UUID) -> User:
        """Clear a lockout."""
        actor.require("user.manage")
        user = self._require_user(actor.business_id, user_id)
        user.failed_attempts = 0
        user.locked_until = None
        self._audit.record(
            actor,
            AuditAction.USER_AMENDED,
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            context={"unlocked": True},
        )
        return user

    def assign_role(
        self, actor: AnyActor, user_id: uuid.UUID, role_code: str, *, audit: bool = True
    ) -> None:
        """Assign a role to a user."""
        actor.require("user.manage")
        user = self._require_user(actor.business_id, user_id)
        role = self._require_role(actor.business_id, role_code)

        existing = self._db.scalar(
            select(UserRole).where(UserRole.user_fk == user.id, UserRole.role_fk == role.id)
        )
        if existing is not None:
            return

        self._db.add(
            UserRole(
                business_id=actor.business_id,
                user_fk=user.id,
                role_fk=role.id,
                assigned_at=self._clock.now(),
                assigned_by_id=actor.user_id,
            )
        )
        if audit:
            self._audit.record(
                actor,
                AuditAction.ROLE_ASSIGNED,
                entity_type="user",
                entity_id=user.id,
                entity_label=user.username,
                after={"role": role.code},
            )

    def withdraw_role(self, actor: AnyActor, user_id: uuid.UUID, role_code: str) -> None:
        """Withdraw a role from a user."""
        actor.require("user.manage")
        user = self._require_user(actor.business_id, user_id)
        role = self._require_role(actor.business_id, role_code)

        assignment = self._db.scalar(
            select(UserRole).where(UserRole.user_fk == user.id, UserRole.role_fk == role.id)
        )
        if assignment is None:
            return

        if role.is_administrator and self._is_last_active_administrator(user):
            raise ConflictError(
                "This is the only active administrator. Appoint another before "
                "withdrawing this role."
            )
        remaining = [item for item in self._roles_of(user.id) if item.id != role.id]
        if not remaining:
            raise ConflictError("A user must retain at least one role.")

        self._db.delete(assignment)
        self._audit.record(
            actor,
            AuditAction.ROLE_WITHDRAWN,
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            before={"role": role.code},
        )

    def set_role_permissions(
        self, actor: AnyActor, role_code: str, permission_codes: set[str], *, reason: str
    ) -> Role:
        """Replace a role's permission set."""
        actor.require("role.manage")
        if not reason.strip():
            raise ValidationError("A reason is required.", field="reason")
        require_registered(permission_codes)
        role = self._require_role(actor.business_id, role_code)

        before = {item.permission_code for item in role.permissions}
        if role.is_administrator and before - permission_codes:
            raise ConflictError(
                "The administrator role cannot have permissions withdrawn. "
                "Create a separate role instead."
            )

        for item in list(role.permissions):
            if item.permission_code not in permission_codes:
                self._db.delete(item)
        for code in permission_codes - before:
            self._db.add(
                RolePermission(
                    business_id=actor.business_id, role_fk=role.id, permission_code=code
                )
            )

        self._audit.record(
            actor,
            AuditAction.PERMISSION_AMENDED,
            entity_type="role",
            entity_id=role.id,
            entity_label=role.code,
            reason=reason,
            before={"granted": ", ".join(sorted(before))},
            after={"granted": ", ".join(sorted(permission_codes))},
        )
        return role

    # -- helpers ----------------------------------------------------------------------

    def _roles_of(self, user_id: uuid.UUID) -> list[Role]:
        """The roles assigned to a user, read freshly from the database."""
        return list(
            self._db.scalars(
                select(Role)
                .join(UserRole, UserRole.role_fk == Role.id)
                .where(UserRole.user_fk == user_id)
            )
        )

    def _build_actor(
        self, user: User, *, session_id: uuid.UUID | None, device: str | None
    ) -> Actor:
        """Resolve the actor's effective permissions.

        Read with an explicit join rather than by traversing relationships, so that a
        role assigned earlier in the same transaction is reflected immediately.
        """
        rows = self._db.execute(
            select(Role.code, Role.is_administrator, RolePermission.permission_code)
            .join(UserRole, UserRole.role_fk == Role.id)
            .outerjoin(RolePermission, RolePermission.role_fk == Role.id)
            .where(UserRole.user_fk == user.id)
        ).all()

        permissions: set[str] = set()
        role_codes: set[str] = set()
        is_administrator = False
        for code, administrator, permission_code in rows:
            role_codes.add(code)
            is_administrator = is_administrator or bool(administrator)
            if permission_code is not None:
                permissions.add(permission_code)

        return Actor(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            business_id=user.business_id,
            branch_id=user.branch_id,
            permissions=frozenset(permissions),
            role_codes=frozenset(role_codes),
            session_id=session_id,
            device=device,
            is_administrator=is_administrator,
        )

    def _require_user(self, business_id: uuid.UUID, user_id: uuid.UUID) -> User:
        user = self._db.scalar(
            select(User).where(User.business_id == business_id, User.id == user_id)
        )
        if user is None:
            raise NotFoundError("User not found.")
        return user

    def _require_role(self, business_id: uuid.UUID, role_code: str) -> Role:
        role = self._db.scalar(
            select(Role).where(Role.business_id == business_id, Role.code == role_code)
        )
        if role is None:
            raise NotFoundError(f"Role not found: {role_code!r}")
        return role

    def _is_last_active_administrator(self, user: User) -> bool:
        """Whether this user is the only active administrator left."""
        if not any(role.is_administrator for role in self._roles_of(user.id)):
            return False
        other = self._db.scalar(
            select(User.id)
            .join(UserRole, UserRole.user_fk == User.id)
            .join(Role, Role.id == UserRole.role_fk)
            .where(
                User.business_id == user.business_id,
                User.id != user.id,
                User.is_active.is_(True),
                Role.is_administrator.is_(True),
            )
            .limit(1)
        )
        return other is None

    def _persist_failure(self) -> None:
        """Commit the record of a failed attempt before refusing the request.

        A failed sign-in raises, and the request's transaction is then rolled back. The
        attempt counter and its audit entry must nevertheless survive, or lockout would
        never accumulate and PRD 10's requirement to audit failed sign-ins would not be
        met. This is the one place a service commits on a failing path.
        """
        self._db.commit()

    def _record_failure(
        self, business_id: uuid.UUID, username: str, device: str | None, detail: str
    ) -> None:
        self._audit.record(
            UnauthenticatedActor(business_id=business_id, username=username[:32], device=device),
            AuditAction.SIGN_IN_FAILED,
            entity_type="user",
            entity_label=username,
            context={"detail": detail, "device": device},
        )
        logger.warning("Sign-in failed for %r: %s", username, detail)


_DUMMY_HASH = hash_password("fixed-constant-for-timing-equalisation")
