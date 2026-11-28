"""Password hashing.

Argon2id, per PRD 9.6. Parameters are tuned for the target hardware and are recorded
in the hash itself, so a later parameter change does not invalidate existing hashes.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type


def normalise(password: str) -> str:
    """Normalise to NFKC, so visually identical passwords hash identically.

    An encoding concern rather than a business rule, so it lives here alongside the
    hashing it serves. The domain re-exports it for its policy checks.
    """
    import unicodedata

    return unicodedata.normalize("NFKC", password)


_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def hash_password(password: str) -> str:
    """Hash a password. The salt and parameters are embedded in the result."""
    return _hasher.hash(normalise(password))


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash. Never raises on a mismatch."""
    try:
        return _hasher.verify(stored_hash, normalise(password))
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Whether the hash was produced with superseded parameters."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return True


def hash_token(token: str) -> str:
    """Hash a session token.

    A session token is already high-entropy, so SHA-256 is sufficient and fast; a
    password-stretching function would add latency to every authenticated request
    without adding security.
    """
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
