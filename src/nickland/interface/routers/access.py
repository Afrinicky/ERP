"""Access control endpoints: roles, a person's exceptions, sessions, the sign-in policy."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter

from nickland.application.services.access import UserAccess
from nickland.domain.identity import AccessPolicyTerms, OverrideEffect
from nickland.interface.access_schemas import (
    AccessPolicyContract,
    AccessPolicyReply,
    AmendRoleContract,
    CreateRoleContract,
    EndedReply,
    ExceptionReply,
    ReasonContract,
    SessionReply,
    SetExceptionsContract,
    UserAccessReply,
)
from nickland.interface.dependencies import AccessDependency, ActorDependency
from nickland.interface.schemas import RoleReply
from nickland.persistence.models import Role

router = APIRouter(tags=["access control"])


def _role(role: Role, holders: int = 0) -> RoleReply:
    return RoleReply(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_administrator=role.is_administrator,
        is_read_only=role.is_read_only,
        is_system=role.is_system,
        permissions=sorted(item.permission_code for item in role.permissions),
        holders=holders,
    )


def _access(found: UserAccess) -> UserAccessReply:
    return UserAccessReply(
        user_id=found.user.id,
        full_name=found.user.full_name,
        username=found.user.username,
        is_administrator=found.is_administrator,
        roles=[role.code for role in found.roles],
        role_names=[role.name for role in found.roles],
        from_roles=sorted(found.from_roles),
        exceptions=[
            ExceptionReply(
                permission_code=item.permission_code,
                effect=item.effect.value,
                reason=item.reason,
                set_at=item.set_at,
                set_by=item.set_by,
            )
            for item in found.overrides
        ],
        effective=sorted(found.effective),
    )


def _policy(terms: AccessPolicyTerms, is_default: bool) -> AccessPolicyReply:
    return AccessPolicyReply(
        **asdict(terms), is_default=is_default, requirements=terms.password.describe()
    )


# -- roles ----------------------------------------------------------------------------------


@router.post("/roles", response_model=RoleReply, status_code=201, summary="Create a role")
def create_role(
    body: CreateRoleContract, actor: ActorDependency, access: AccessDependency
) -> RoleReply:
    role = access.create_role(
        actor, name=body.name, description=body.description, copy_from=body.copy_from
    )
    return _role(role)


@router.patch("/roles/{role_code}", response_model=RoleReply, summary="Rename a role")
def amend_role(
    role_code: str, body: AmendRoleContract, actor: ActorDependency, access: AccessDependency
) -> RoleReply:
    role = access.amend_role(actor, role_code, name=body.name, description=body.description)
    return _role(role, access.holders(actor).get(role.id, 0))


@router.post("/roles/{role_code}/remove", status_code=204, summary="Remove a role")
def remove_role(
    role_code: str, body: ReasonContract, actor: ActorDependency, access: AccessDependency
) -> None:
    access.remove_role(actor, role_code, reason=body.reason)


# -- a person's access ------------------------------------------------------------------------


@router.get("/users/{user_id}/access", response_model=UserAccessReply, summary="A user's access")
def user_access(
    user_id: uuid.UUID, actor: ActorDependency, access: AccessDependency
) -> UserAccessReply:
    return _access(access.user_access(actor, user_id))


@router.put(
    "/users/{user_id}/access", response_model=UserAccessReply, summary="Set a user's exceptions"
)
def set_exceptions(
    user_id: uuid.UUID,
    body: SetExceptionsContract,
    actor: ActorDependency,
    access: AccessDependency,
) -> UserAccessReply:
    found = access.set_overrides(
        actor,
        user_id,
        {item.permission_code: OverrideEffect(item.effect) for item in body.exceptions},
        reason=body.reason,
    )
    return _access(found)


@router.get(
    "/users/{user_id}/sessions", response_model=list[SessionReply], summary="Sessions in force"
)
def sessions(
    user_id: uuid.UUID, actor: ActorDependency, access: AccessDependency
) -> list[SessionReply]:
    return [SessionReply.model_validate(item) for item in access.sessions(actor, user_id)]


@router.post(
    "/users/{user_id}/sessions/end", response_model=EndedReply, summary="Sign out everywhere"
)
def end_sessions(
    user_id: uuid.UUID, body: ReasonContract, actor: ActorDependency, access: AccessDependency
) -> EndedReply:
    return EndedReply(ended=access.sign_out_everywhere(actor, user_id, reason=body.reason))


# -- the sign-in policy -----------------------------------------------------------------------


@router.get("/access-policy", response_model=AccessPolicyReply, summary="The sign-in policy")
def access_policy(actor: ActorDependency, access: AccessDependency) -> AccessPolicyReply:
    terms, stored = access.policy(actor)
    return _policy(terms, not stored)


@router.put("/access-policy", response_model=AccessPolicyReply, summary="Set the sign-in policy")
def set_access_policy(
    body: AccessPolicyContract, actor: ActorDependency, access: AccessDependency
) -> AccessPolicyReply:
    values = body.model_dump(exclude={"reason"})
    terms = access.set_policy(actor, AccessPolicyTerms(**values), reason=body.reason)
    return _policy(terms, False)
