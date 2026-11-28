"""The actor and the scoping context.

Every service call carries an ``Actor``. Scoping is derived from the actor, never from
a request parameter, per ADR-0003. Permission checks occur here, in the application
layer, and not by hiding interface controls, per PRD 10.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from nickland.domain.errors import PermissionDeniedError
from nickland.domain.permissions import permission as lookup_permission


@dataclass(frozen=True, slots=True)
class Actor:
    """The authenticated user on whose behalf a service acts."""

    user_id: uuid.UUID
    username: str
    full_name: str
    business_id: uuid.UUID
    branch_id: uuid.UUID | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    role_codes: frozenset[str] = field(default_factory=frozenset)
    session_id: uuid.UUID | None = None
    device: str | None = None
    is_administrator: bool = False

    def has(self, code: str) -> bool:
        """Whether the actor holds a permission.

        The code is looked up in the registry, so a typographical error in a call site
        raises rather than silently denying.
        """
        lookup_permission(code)
        return code in self.permissions

    def require(self, code: str) -> None:
        """Raise unless the actor holds the permission."""
        if not self.has(code):
            raise PermissionDeniedError(code)

    def require_any(self, *codes: str) -> None:
        """Raise unless the actor holds at least one of the permissions."""
        for code in codes:
            if self.has(code):
                return
        raise PermissionDeniedError(" or ".join(codes))

    def require_all(self, *codes: str) -> None:
        for code in codes:
            self.require(code)


@dataclass(frozen=True, slots=True)
class SystemActor:
    """The actor used by background work and by first-run provisioning.

    It holds every permission, so it is never constructed from a request. Its audit
    entries record no user, which distinguishes them from a person's actions.
    """

    business_id: uuid.UUID
    reason: str
    user_id: uuid.UUID | None = None
    username: str | None = None
    full_name: str = "System"
    branch_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    device: str | None = None
    is_administrator: bool = True

    @property
    def permissions(self) -> frozenset[str]:
        from nickland.domain.permissions import ALL_CODES

        return ALL_CODES

    @property
    def role_codes(self) -> frozenset[str]:
        return frozenset({"system"})

    def has(self, code: str) -> bool:
        lookup_permission(code)
        return True

    def require(self, code: str) -> None:
        lookup_permission(code)

    def require_any(self, *codes: str) -> None:
        for code in codes:
            lookup_permission(code)

    def require_all(self, *codes: str) -> None:
        for code in codes:
            lookup_permission(code)


@dataclass(frozen=True, slots=True)
class UnauthenticatedActor:
    """A failed or not-yet-authenticated attempt.

    It exists so that a sign-in failure can be audited with the username that was
    offered. It holds no permissions, so it cannot perform any action.
    """

    business_id: uuid.UUID
    username: str | None = None
    user_id: None = None
    full_name: str = "(unauthenticated)"
    branch_id: None = None
    session_id: None = None
    device: str | None = None
    is_administrator: bool = False
    permissions: frozenset[str] = field(default_factory=frozenset)
    role_codes: frozenset[str] = field(default_factory=frozenset)

    def has(self, code: str) -> bool:
        lookup_permission(code)
        return False

    def require(self, code: str) -> None:
        raise PermissionDeniedError(code)

    def require_any(self, *codes: str) -> None:
        raise PermissionDeniedError(" or ".join(codes))

    def require_all(self, *codes: str) -> None:
        raise PermissionDeniedError(" and ".join(codes))


AnyActor = Actor | SystemActor | UnauthenticatedActor
