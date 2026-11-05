"""Time access.

Every timestamp in the application is obtained here, never from ``datetime.now``
directly, so that time can be controlled in tests and so that no naive datetime
enters the system.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    """Source of the current instant."""

    def now(self) -> datetime:
        """Return the current instant, timezone-aware in UTC."""
        ...


class SystemClock:
    """The real clock."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock frozen at a given instant. For tests only."""

    __slots__ = ("_instant",)

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware instant")
        self._instant = instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._instant = self._instant + timedelta(seconds=seconds)


def in_timezone(instant: datetime, timezone: str) -> datetime:
    """Present a UTC instant in a named timezone."""
    return instant.astimezone(ZoneInfo(timezone))


def business_date(instant: datetime, timezone: str) -> date:
    """The calendar date of an instant in the business's own timezone.

    A sale completed at 23:30 in Accra belongs to that day, not to the next UTC day.
    """
    return in_timezone(instant, timezone).date()
