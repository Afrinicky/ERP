"""Identifier generation.

UUIDv7 is used for every primary key: time-ordered, so index locality is preserved,
without exposing a countable business volume the way a serial integer does.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime


def new_id() -> uuid.UUID:
    """Generate a UUIDv7.

    Layout per RFC 9562: a 48-bit big-endian Unix millisecond timestamp, the version
    and variant bits, and 74 bits of randomness.
    """
    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)
    random_bytes = os.urandom(10)

    value = timestamp_ms.to_bytes(6, "big") + random_bytes
    as_int = int.from_bytes(value, "big")

    # Version 7 in bits 48-51.
    as_int &= ~(0xF << 76)
    as_int |= 0x7 << 76
    # Variant 0b10 in bits 64-65.
    as_int &= ~(0x3 << 62)
    as_int |= 0x2 << 62

    return uuid.UUID(int=as_int)


def timestamp_of(identifier: uuid.UUID) -> datetime:
    """Recover the creation instant from a UUIDv7."""
    if identifier.version != 7:
        raise ValueError(f"not a UUIDv7: version {identifier.version}")
    timestamp_ms = identifier.int >> 80
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC)


def new_reference(length: int = 32) -> str:
    """Generate an opaque, URL-safe reference. Used for session tokens."""
    import secrets

    return secrets.token_urlsafe(length)
