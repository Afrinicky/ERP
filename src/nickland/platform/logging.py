"""Logging configuration.

Credentials, tokens, card data, bank identifiers and personal identifiers are redacted
before a record is emitted, per PRD 9.6.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from pathlib import Path
from typing import Any

REDACTED = "[redacted]"

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "new_password",
        "current_password",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "card_number",
        "cvv",
        "pin",
        "account_number",
        "iban",
        "swift",
        "tax_identifier",
        "mobile_money_number",
    }
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{13,19}\b"), REDACTED),  # card-length digit runs
    (re.compile(r"(?i)\bbearer\s+[\w\-._~+/]+=*"), f"Bearer {REDACTED}"),
    (re.compile(r"(?i)(password|token|secret|api[_-]?key)=\S+"), rf"\1={REDACTED}"),
)


def redact(value: Any) -> Any:
    """Recursively redact sensitive values."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in _SENSITIVE_KEYS else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    if isinstance(value, str):
        result = value
        for pattern, replacement in _PATTERNS:
            result = pattern.sub(replacement, result)
        return result
    return value


class RedactingFilter(logging.Filter):
    """Redact the message and arguments of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if record.args:
            record.args = redact(record.args)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for attribute in ("actor_id", "business_id", "request_id", "operation"):
            value = getattr(record, attribute, None)
            if value is not None:
                payload[attribute] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(
    *,
    level: str = "INFO",
    directory: Path | None = None,
    as_json: bool = False,
) -> None:
    """Install handlers on the root logger. Idempotent."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level.upper())

    formatter: logging.Formatter = (
        JsonFormatter()
        if as_json
        else logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(name)-34s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    redacting = RedactingFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redacting)
    root.addHandler(console)

    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            directory / "nickland.log",
            maxBytes=8 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        rotating.addFilter(redacting)
        root.addHandler(rotating)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
