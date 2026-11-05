"""Domain errors.

These carry business meaning. The interface layer translates them to HTTP responses;
the domain never raises an HTTP error itself.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for every business-rule violation."""

    code = "domain_error"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context


class ValidationError(DomainError):
    """A value failed a business rule."""

    code = "validation_error"

    def __init__(self, message: str, *, field: str | None = None, **context: Any) -> None:
        super().__init__(message, field=field, **context)
        self.field = field


class NotFoundError(DomainError):
    """The requested record does not exist, or is outside the actor's scope.

    The two cases are deliberately indistinguishable, per PRD 10.
    """

    code = "not_found"


class ConflictError(DomainError):
    """The operation conflicts with the current state of the record."""

    code = "conflict"


class PermissionDeniedError(DomainError):
    """The actor does not hold the required permission."""

    code = "permission_denied"

    def __init__(self, permission: str) -> None:
        super().__init__(f"Permission required: {permission}", permission=permission)
        self.permission = permission


class AuthenticationError(DomainError):
    """Credentials were not accepted."""

    code = "authentication_failed"


class AccountLockedError(AuthenticationError):
    """The account is locked after repeated failed attempts."""

    code = "account_locked"

    def __init__(self, minutes_remaining: int) -> None:
        super().__init__(
            f"Account locked. Try again in {minutes_remaining} minute(s).",
            minutes_remaining=minutes_remaining,
        )
        self.minutes_remaining = minutes_remaining


class StateTransitionError(ConflictError):
    """The requested state transition is not permitted."""

    code = "invalid_transition"

    def __init__(self, entity: str, current: str, requested: str) -> None:
        super().__init__(
            f"{entity} cannot move from {current} to {requested}.",
            entity=entity,
            current=current,
            requested=requested,
        )


class ApprovalRequiredError(ConflictError):
    """The operation exceeds the actor's limit and requires approval."""

    code = "approval_required"


class PeriodClosedError(ConflictError):
    """The accounting period is closed to ordinary posting."""

    code = "period_closed"
