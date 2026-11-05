"""Password, lockout and session policy. PRD 9.6."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nickland.domain.errors import ValidationError
from nickland.domain.identity import (
    LockoutPolicy,
    PasswordPolicy,
    SessionPolicy,
    normalise_password,
    validate_password,
    validate_username,
)

NOW = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)


class TestPasswordPolicy:
    def test_accepts_a_compliant_password(self) -> None:
        validate_password("Correct9Horse", PasswordPolicy())

    @pytest.mark.parametrize(
        ("password", "message"),
        [
            ("Short9A", "at least 10"),
            ("alllowercase1", "uppercase"),
            ("ALLUPPERCASE1", "lowercase"),
            ("NoDigitsHere", "digit"),
            ("password1A", "too common"),
        ],
    )
    def test_rejects_non_compliant(self, password: str, message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            validate_password(password, PasswordPolicy())

    def test_rejects_a_password_containing_the_username(self) -> None:
        with pytest.raises(ValidationError, match="username"):
            validate_password("Proprietor99", PasswordPolicy(), username="proprietor")

    def test_symbol_requirement_is_configurable(self) -> None:
        policy = PasswordPolicy(require_symbol=True)
        with pytest.raises(ValidationError, match="symbol"):
            validate_password("Correct9Horse", policy)
        validate_password("Correct9Horse!", policy)

    def test_rejects_an_absurdly_long_password(self) -> None:
        with pytest.raises(ValidationError, match="maximum length"):
            validate_password("Aa1" + "x" * 2000, PasswordPolicy())

    def test_normalises_equivalent_forms(self) -> None:
        assert normalise_password("ﬁre9Brigade") == normalise_password("fire9Brigade")

    def test_describes_its_requirements(self) -> None:
        described = PasswordPolicy().describe()
        assert "At least 10 characters" in described
        assert len(described) == 4


class TestLockout:
    def test_locks_at_the_threshold(self) -> None:
        policy = LockoutPolicy(threshold=3, duration=timedelta(minutes=15))
        assert policy.lock_until(2, NOW) is None
        assert policy.lock_until(3, NOW) == NOW + timedelta(minutes=15)

    def test_is_locked_only_while_the_lock_stands(self) -> None:
        policy = LockoutPolicy()
        until = NOW + timedelta(minutes=15)
        assert policy.is_locked(until, NOW)
        assert not policy.is_locked(until, until + timedelta(seconds=1))
        assert not policy.is_locked(None, NOW)

    def test_minutes_remaining_rounds_up_and_never_reports_zero(self) -> None:
        policy = LockoutPolicy()
        assert policy.minutes_remaining(NOW + timedelta(seconds=30), NOW) == 1
        assert policy.minutes_remaining(NOW + timedelta(minutes=14, seconds=10), NOW) == 15


class TestSessionPolicy:
    def test_absolute_expiry(self) -> None:
        policy = SessionPolicy(idle=timedelta(minutes=30), absolute=timedelta(hours=12))
        assert policy.expires_at(NOW) == NOW + timedelta(hours=12)
        assert not policy.is_valid(NOW, NOW, NOW + timedelta(hours=12, seconds=1))

    def test_idle_expiry(self) -> None:
        policy = SessionPolicy(idle=timedelta(minutes=30), absolute=timedelta(hours=12))
        assert policy.is_valid(NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=1, minutes=29))
        assert not policy.is_valid(NOW, NOW, NOW + timedelta(minutes=31))


class TestUsername:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("  Proprietor ", "proprietor"), ("Store.Officer-1", "store.officer-1"),
         ("a_b_c", "a_b_c")],
    )
    def test_normalisation(self, raw: str, expected: str) -> None:
        assert validate_username(raw) == expected

    @pytest.mark.parametrize("raw", ["ab", ".abc", "abc.", "a b", "abc!", "x" * 40, ""])
    def test_rejection(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            validate_username(raw)
