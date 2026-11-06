"""Column types.

Money is fixed-precision decimal everywhere, including on SQLite, which has no native
numeric type, per PRD 9.5.

SQLite holds a monetary value as a scaled integer: the amount multiplied by ten to the
power of the column's scale, so 1,150.00 at six places is stored as 1150000000. An
earlier revision stored a zero-padded decimal string instead. That sorted correctly
and never lost a digit on a single value, but it was wrong in two ways that only
appear in aggregate. SQLite compares by storage class before value, so text sorts
after every number whatever the digits say and ``CHECK (debit >= 0)`` asserted
nothing; and ``SUM()`` over text coerces to REAL, which put the whole of financial
reporting through binary floating-point — the one thing this module exists to
prevent. A trial balance over a large ledger would then report itself out by a
fraction of a pesewa that no entry accounts for.

Scaled integers fix both. Comparison and ordering are native, ``SUM()`` is exact
integer arithmetic, and the result arrives as an integer that is scaled back to a
Decimal on the way out — including the result of an aggregate, since SQLAlchemy
applies the column's type to it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import CHAR, BigInteger, DateTime, Dialect, TypeDecorator
from sqlalchemy.dialects.postgresql import NUMERIC as PG_NUMERIC
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from nickland.domain.money import DEFAULT_PRESENTATION_PRECISION, WORKING_PRECISION


class Uuid(TypeDecorator[uuid.UUID]):
    """UUID stored natively on PostgreSQL and as a 32-character hex string on SQLite."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return value.hex

    def process_result_value(self, value: Any, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class Money(TypeDecorator[Decimal]):
    """Fixed-precision decimal.

    PostgreSQL uses NUMERIC(18, 4) by default. SQLite holds the amount as an integer
    scaled by the column's number of decimal places, so comparison, ordering and
    summation are exact integer operations rather than text or floating-point ones.
    """

    impl = BigInteger
    cache_ok = True

    def __init__(self, precision: int = 18, scale: int = DEFAULT_PRESENTATION_PRECISION) -> None:
        self.precision = precision
        self.scale = scale
        super().__init__()

    @property
    def _exponent(self) -> Decimal:
        return Decimal(1).scaleb(-self.scale)

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_NUMERIC(self.precision, self.scale))
        return dialect.type_descriptor(BigInteger())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError(
                "Floating-point values cannot be persisted to a monetary column. "
                "Pass a Decimal, int or str."
            )
        as_decimal = value if isinstance(value, Decimal) else Decimal(str(value))
        as_decimal = as_decimal.quantize(self._exponent, rounding=ROUND_HALF_UP)
        if dialect.name == "postgresql":
            return as_decimal
        scaled = as_decimal.scaleb(self.scale)
        if scaled != scaled.to_integral_value():
            raise ValueError(f"{value} cannot be held to {self.scale} decimal places without loss.")
        return int(scaled)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        """Scale an integer back to a Decimal.

        An aggregate arrives here too — SQLAlchemy applies the column's type to the
        result of ``SUM`` — so a summed column is scaled back exactly once, by the
        same factor, and comes out right.
        """
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, int):
            return Decimal(value).scaleb(-self.scale).quantize(self._exponent)
        if isinstance(value, float):
            raise TypeError(
                "A monetary column returned a floating-point value, which means the "
                "query performed arithmetic SQLite could not keep exact. Aggregate "
                "the column itself, or compute in Python."
            )
        return Decimal(str(value)).quantize(self._exponent)


class Quantity(Money):
    """A quantity. Six decimal places, so fractional units of measure are exact."""

    cache_ok = True
    """Declared on the subclass: without it SQLAlchemy cannot cache a compiled
    statement that uses this type, which costs a recompilation on every query."""

    def __init__(self) -> None:
        super().__init__(precision=18, scale=WORKING_PRECISION)


class PostedAmount(Money):
    """A monetary amount in the ledger. Six decimal places.

    Elsewhere four places is right: a price is a presentation figure and the pricing
    engine rounds to presentation precision at the boundary. The ledger is different.
    An entry is validated as balanced at working precision, so storing it at four
    places could round a balanced entry into an unbalanced one on disk, and the one
    invariant the whole accounting model rests on would be broken by the act of saving
    it. Amounts are stored exactly as validated and rounded only for display.
    """

    cache_ok = True

    def __init__(self) -> None:
        super().__init__(precision=18, scale=WORKING_PRECISION)


class UnitPrice(Money):
    """A unit price. Six decimal places.

    A price presented at four places is right for a figure a customer reads. It is
    wrong for the figure a line total is computed from, because the pricing engine
    requires the line total to be derived from the stored unit price and a unit price
    rounded to four places does not reproduce it: 1,824.2807 over ten units is
    182.428070 a unit, and 182.4281 times ten is 1,824.2810. Three pesewas on one line
    is three cedis on a hundred, and the customer adds the column up.
    """

    cache_ok = True

    def __init__(self) -> None:
        super().__init__(precision=18, scale=WORKING_PRECISION)


class Rate(Money):
    """A rate or percentage held as a fraction. Six decimal places."""

    cache_ok = True

    def __init__(self) -> None:
        super().__init__(precision=12, scale=WORKING_PRECISION)


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime stored in UTC.

    SQLite discards timezone information, so it is reattached on read. A naive
    datetime is rejected on write rather than silently assumed to be UTC.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"Expected a datetime, received {type(value).__name__}")
        if value.tzinfo is None:
            raise ValueError(
                "A naive datetime cannot be persisted. Obtain the instant from "
                "nickland.platform.clock."
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"Expected a datetime from the driver, received {type(value)!r}")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
