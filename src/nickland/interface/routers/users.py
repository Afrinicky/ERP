"""User and role administration endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from nickland.domain.permissions import REGISTRY
from nickland.interface.dependencies import (
    ActorDependency,
    IdentityDependency,
    SessionDependency,
)
from nickland.interface.schemas import (
    AmendUserContract,
    AssignRoleContract,
    CreateUserContract,
    PermissionReply,
    ResetPasswordContract,
    RoleReply,
    SetActiveContract,
    SetRolePermissionsContract,
    UserReply,
)
from nickland.persistence.models import Role, User, UserRole

router = APIRouter(tags=["users"])


def _user_reply(db: SessionDependency, user: User) -> UserReply:
    codes = db.scalars(
        select(Role.code).join(UserRole, UserRole.role_fk == Role.id).where(
            UserRole.user_fk == user.id
        )
    ).all()
    return UserReply(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        telephone=user.telephone,
        branch_id=user.branch_id,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        locked_until=user.locked_until,
        last_signed_in_at=user.last_signed_in_at,
        role_codes=sorted(codes),
    )


@router.get("/users", response_model=list[UserReply], summary="List users")
def list_users(
    actor: ActorDependency, db: SessionDependency, include_inactive: bool = True
) -> list[UserReply]:
    actor.require("user.view")
    statement = select(User).where(User.business_id == actor.business_id)
    if not include_inactive:
        statement = statement.where(User.is_active.is_(True))
    users = db.scalars(statement.order_by(User.full_name)).all()
    return [_user_reply(db, user) for user in users]


@router.post("/users", response_model=UserReply, status_code=201, summary="Create a user")
def create_user(
    contract: CreateUserContract,
    actor: ActorDependency,
    identity: IdentityDependency,
    db: SessionDependency,
) -> UserReply:
    user = identity.create_user(actor, **contract.model_dump())
    return _user_reply(db, user)


@router.patch("/users/{user_id}", response_model=UserReply, summary="Amend a user")
def amend_user(
    user_id: uuid.UUID,
    contract: AmendUserContract,
    actor: ActorDependency,
    identity: IdentityDependency,
    db: SessionDependency,
) -> UserReply:
    user = identity.amend_user(
        actor, user_id, **contract.model_dump(exclude_unset=True)
    )
    return _user_reply(db, user)


@router.post(
    "/users/{user_id}/active", response_model=UserReply, summary="Activate or deactivate"
)
def set_active(
    user_id: uuid.UUID,
    contract: SetActiveContract,
    actor: ActorDependency,
    identity: IdentityDependency,
    db: SessionDependency,
) -> UserReply:
    """Deactivation revokes every live session of that user."""
    user = identity.set_active(
        actor, user_id, active=contract.active, reason=contract.reason
    )
    return _user_reply(db, user)


@router.post("/users/{user_id}/unlock", response_model=UserReply, summary="Clear a lockout")
def unlock(
    user_id: uuid.UUID,
    actor: ActorDependency,
    identity: IdentityDependency,
    db: SessionDependency,
) -> UserReply:
    return _user_reply(db, identity.unlock(actor, user_id))


@router.post(
    "/users/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reset another user's password",
)
def reset_password(
    user_id: uuid.UUID,
    contract: ResetPasswordContract,
    actor: ActorDependency,
    identity: IdentityDependency,
) -> Response:
    """Reset a password. A reason is mandatory and is recorded in the audit trail."""
    identity.reset_password(
        actor, user_id, contract.new_password, reason=contract.reason
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users/{user_id}/roles",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign a role",
)
def assign_role(
    user_id: uuid.UUID,
    contract: AssignRoleContract,
    actor: ActorDependency,
    identity: IdentityDependency,
) -> Response:
    identity.assign_role(actor, user_id, contract.role_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/users/{user_id}/roles/{role_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a role",
)
def withdraw_role(
    user_id: uuid.UUID,
    role_code: str,
    actor: ActorDependency,
    identity: IdentityDependency,
) -> Response:
    identity.withdraw_role(actor, user_id, role_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/roles", response_model=list[RoleReply], summary="List roles")
def list_roles(actor: ActorDependency, db: SessionDependency) -> list[RoleReply]:
    actor.require("user.view")
    roles = db.scalars(
        select(Role).where(Role.business_id == actor.business_id).order_by(Role.name)
    ).all()
    return [
        RoleReply(
            id=role.id,
            code=role.code,
            name=role.name,
            description=role.description,
            is_administrator=role.is_administrator,
            is_read_only=role.is_read_only,
            is_system=role.is_system,
            permissions=sorted(item.permission_code for item in role.permissions),
        )
        for role in roles
    ]


@router.put(
    "/roles/{role_code}/permissions",
    response_model=RoleReply,
    summary="Replace a role's permissions",
)
def set_role_permissions(
    role_code: str,
    contract: SetRolePermissionsContract,
    actor: ActorDependency,
    identity: IdentityDependency,
) -> RoleReply:
    role = identity.set_role_permissions(
        actor, role_code, set(contract.permission_codes), reason=contract.reason
    )
    return RoleReply(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        is_administrator=role.is_administrator,
        is_read_only=role.is_read_only,
        is_system=role.is_system,
        permissions=sorted(item.permission_code for item in role.permissions),
    )


@router.get(
    "/permissions", response_model=list[PermissionReply], summary="List every permission"
)
def list_permissions(actor: ActorDependency) -> list[PermissionReply]:
    """The permission registry, for the role administration screen."""
    actor.require("user.view")
    return [
        PermissionReply(
            code=item.code,
            name=item.name,
            group=item.group.value,
            description=item.description,
            sensitive=item.sensitive,
        )
        for item in REGISTRY
    ]
