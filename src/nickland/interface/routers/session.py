"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from nickland.application.context import Actor
from nickland.interface.dependencies import (
    ActorDependency,
    IdentityDependency,
    LocalGuard,
    SessionDependency,
    sole_business_id,
)
from nickland.interface.schemas import (
    ActorReply,
    ChangePasswordContract,
    SignInContract,
    SignInReply,
)

router = APIRouter(prefix="/session", tags=["session"])


def _actor_reply(actor: Actor) -> ActorReply:
    """Describe the actor, including the permissions in force."""
    return ActorReply(
        user_id=actor.user_id,
        username=actor.username,
        full_name=actor.full_name,
        business_id=actor.business_id,
        branch_id=actor.branch_id,
        role_codes=sorted(actor.role_codes),
        permissions=sorted(actor.permissions),
        is_administrator=actor.is_administrator,
    )


@router.post("", response_model=SignInReply, summary="Sign in")
def sign_in(
    contract: SignInContract,
    identity: IdentityDependency,
    db: SessionDependency,
    _local: LocalGuard,
) -> SignInReply:
    """Authenticate and open a session.

    Every failure returns one message, so the response does not disclose whether a
    username exists.
    """
    result = identity.sign_in(
        contract.username,
        contract.password,
        business_id=sole_business_id(db),
        device=contract.device,
    )
    return SignInReply(
        token=result.token,
        expires_at=result.expires_at,
        must_change_password=result.must_change_password,
        actor=_actor_reply(result.actor),
    )


@router.get("/current", response_model=ActorReply, summary="Describe the current actor")
def current(actor: ActorDependency) -> ActorReply:
    """The signed-in actor and the permissions in force.

    The interface uses the permission list to hide controls. That is a convenience;
    every operation is checked again in the application layer.
    """
    return _actor_reply(actor)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
def sign_out(actor: ActorDependency, identity: IdentityDependency) -> Response:
    identity.sign_out(actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT, summary="Change own password")
def change_password(
    contract: ChangePasswordContract,
    actor: ActorDependency,
    identity: IdentityDependency,
) -> Response:
    """Change the acting user's own password. Every live session is revoked."""
    identity.change_password(
        actor,
        current_password=contract.current_password,
        new_password=contract.new_password,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
