"""Domain-to-HTTP error translation."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from nickland.domain.errors import (
    AccountLockedError,
    ApprovalRequiredError,
    AuthenticationError,
    ConflictError,
    DomainError,
    NotFoundError,
    PeriodClosedError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.platform.config import Environment, get_settings
from nickland.platform.logging import get_logger

logger = get_logger(__name__)

UNPROCESSABLE = 422
"""Written as a numeric code: the HTTP status is stable, its Starlette constant is not."""

_STATUS: dict[type[DomainError], int] = {
    ValidationError: UNPROCESSABLE,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    ApprovalRequiredError: status.HTTP_409_CONFLICT,
    PeriodClosedError: status.HTTP_409_CONFLICT,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    AccountLockedError: status.HTTP_423_LOCKED,
}


def _status_for(error: DomainError) -> int:
    for kind in type(error).__mro__:
        if kind in _STATUS:
            return _STATUS[kind]
    return status.HTTP_400_BAD_REQUEST


def _payload(error: DomainError) -> dict[str, Any]:
    body: dict[str, Any] = {"code": error.code, "message": error.message}
    if isinstance(error, ValidationError) and error.field:
        body["field"] = error.field
    context = {
        key: value
        for key, value in error.context.items()
        if key != "field" and value is not None
    }
    if context:
        body["detail"] = {key: str(value) for key, value in context.items()}
    return body


def install_error_handlers(application: FastAPI) -> None:
    """Register the handlers. Called once by the application factory."""

    @application.exception_handler(DomainError)
    async def _domain(_request: Request, error: DomainError) -> JSONResponse:
        code = _status_for(error)
        if code >= 500:
            logger.exception("Domain error", exc_info=error)
        return JSONResponse(status_code=code, content={"error": _payload(error)})

    @application.exception_handler(RequestValidationError)
    async def _request_validation(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        first = error.errors()[0] if error.errors() else {}
        location = [str(part) for part in first.get("loc", []) if part not in ("body", "query")]
        return JSONResponse(
            status_code=UNPROCESSABLE,
            content={
                "error": {
                    "code": "validation_error",
                    "message": first.get("msg", "The request could not be accepted."),
                    "field": ".".join(location) or None,
                }
            },
        )

    @application.exception_handler(Exception)
    async def _unexpected(_request: Request, error: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=error)
        development = get_settings().environment is not Environment.PRODUCTION
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": (
                        str(error)
                        if development
                        else "The operation could not be completed. "
                        "The event has been recorded."
                    ),
                }
            },
        )
