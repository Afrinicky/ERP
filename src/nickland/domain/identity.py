"""Identity rules: password policy, lockout and session validity.

Hashing itself is an integration concern; this module decides policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from nickland.domain.errors import ValidationError
from nickland.platform.passwords import normalise as normalise_password

__all__ = [
    "LockoutPolicy",
    "PasswordPolicy",
    "SessionPolicy",
    "normalise_password",
    "validate_password",
    "validate_username",
]

_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "passw0rd",
        "qwerty",
        "12345678",
        "123456789",
        "1234567890",
        "letmein",
        "welcome",
        "admin",
        "administrator",
        "nickland",
        "changeme",
        "iloveyou",
        "monkey",
        "dragon",
        "sunshine",
        "princess",
        "football",
        "baseball",
    }
)


_LEET = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
        "!": "i",
    }
)
_DECORATION = "0123456789!@#$%^&*()_+-=.,?"
_SUBSTRING_THRESHOLD = 6
"""Common passwords at least this long are caught anywhere in the candidate."""


def _is_common(candidate: str) -> bool:
    """Whether a password is a common one, however decorated.

    A trailing digit, a character substitution or an appended letter does not make a
    common password uncommon: "password1A" is "password" with decoration, and
    "Passw0rd12" is the same word again.
    """
    lowered = candidate.lower()
    variants = {
        lowered,
        lowered.strip(_DECORATION),
        lowered.translate(_LEET),
        lowered.strip(_DECORATION).translate(_LEET),
        "".join(character for character in lowered.translate(_LEET) if character.isalpha()),
    }
    if variants & _COMMON_PASSWORDS:
        return True
    return any(
        common in variant
        for common in _COMMON_PASSWORDS
        if len(common) >= _SUBSTRING_THRESHOLD
        for variant in variants
    )


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    """Password requirements. Configurable by the administrator, per PRD 9.6."""

    minimum_length: int = 10
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_digit: bool = True
    require_symbol: bool = False
    reuse_window: int = 5
    """How many previous hashes are retained and refused."""

    def describe(self) -> list[str]:
        """Human-readable requirements, for display beside the field."""
        requirements = [f"At least {self.minimum_length} characters"]
        if self.require_uppercase:
            requirements.append("One uppercase letter")
        if self.require_lowercase:
            requirements.append("One lowercase letter")
        if self.require_digit:
            requirements.append("One digit")
        if self.require_symbol:
            requirements.append("One symbol")
        return requirements


def validate_password(
    password: str,
    policy: PasswordPolicy,
    *,
    username: str | None = None,
) -> None:
    """Raise if the password fails policy. Returns nothing on success."""
    candidate = normalise_password(password)

    if len(candidate) < policy.minimum_length:
        raise ValidationError(
            f"Password must be at least {policy.minimum_length} characters.",
            field="password",
        )
    if len(candidate) > 1024:
        raise ValidationError("Password exceeds the maximum length.", field="password")
    if policy.require_uppercase and not re.search(r"[A-Z]", candidate):
        raise ValidationError("Password must contain an uppercase letter.", field="password")
    if policy.require_lowercase and not re.search(r"[a-z]", candidate):
        raise ValidationError("Password must contain a lowercase letter.", field="password")
    if policy.require_digit and not re.search(r"\d", candidate):
        raise ValidationError("Password must contain a digit.", field="password")
    if policy.require_symbol and not re.search(r"[^\w\s]", candidate):
        raise ValidationError("Password must contain a symbol.", field="password")
    if _is_common(candidate):
        raise ValidationError("Password is too common. Choose another.", field="password")
    if username and username.lower() in candidate.lower():
        raise ValidationError("Password must not contain the username.", field="password")


@dataclass(frozen=True, slots=True)
class LockoutPolicy:
    """Failed-attempt lockout, per PRD 9.6."""

    threshold: int = 5
    duration: timedelta = timedelta(minutes=15)

    def is_locked(self, locked_until: datetime | None, now: datetime) -> bool:
        """Whether the account is presently locked."""
        return locked_until is not None and locked_until > now

    def lock_until(self, failed_attempts: int, now: datetime) -> datetime | None:
        """The instant the lock expires, or None if the threshold is not reached."""
        if failed_attempts >= self.threshold:
            return now + self.duration
        return None

    def minutes_remaining(self, locked_until: datetime, now: datetime) -> int:
        remaining = (locked_until - now).total_seconds() / 60
        return max(1, int(remaining) + (1 if remaining % 1 else 0))


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """Session expiry, per PRD 9.6."""

    idle: timedelta = timedelta(minutes=30)
    absolute: timedelta = timedelta(hours=12)

    def expires_at(self, issued_at: datetime) -> datetime:
        return issued_at + self.absolute

    def idle_deadline(self, last_seen_at: datetime) -> datetime:
        return last_seen_at + self.idle

    def is_valid(self, issued_at: datetime, last_seen_at: datetime, now: datetime) -> bool:
        return now < self.expires_at(issued_at) and now < self.idle_deadline(last_seen_at)


_USERNAME = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30})[a-z0-9]$")


def validate_username(username: str) -> str:
    """Normalise and validate a username. Returns the normalised form."""
    candidate = username.strip().lower()
    if not _USERNAME.match(candidate):
        raise ValidationError(
            "Username must be 3 to 32 characters, using letters, digits, "
            "full stops, hyphens and underscores.",
            field="username",
        )
    return candidate
