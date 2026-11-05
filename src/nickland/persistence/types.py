"""Column types.

Money is fixed-precision decimal everywhere, including on SQLite, which has no native
numeric type. ``Money`` stores as text and returns Decimal, so arithmetic never passes
through binary floating-point, per PRD 9.5.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, DateTime, Dialect, String, TypeDecorator
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

    PostgreSQL uses NUMERIC(18, 4) by default. SQLite stores a zero-padded decimal
    string, which sorts and compares correctly and never loses precision.
    """

    impl = String
    cache_ok = True

    def __init__(self, precision: int = 18, scale: int = DEFAULT_PRESENTATION_PRECISION) -> None:
        self.precision = precision
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_NUMERIC(self.precision, self.scale))
        return dialect.type_descriptor(String(self.precision + self.scale + 4))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError(
                "Floating-point values cannot be persisted to a monetary column. "
                "Pass a Decimal, int or str."
            )
        as_decimal = value if isinstance(value, Decimal) else Decimal(str(value))
        as_decimal = as_decimal.quantize(Decimal(1).scaleb(-self.scale))
        if dialect.name == "postgresql":
            return as_decimal
        # Sign-preserving, zero-padded so lexical order matches numeric order.
        sign = "-" if as_decimal < 0 else "0"
        digits = f"{abs(as_decimal):0{self.precision + self.scale + 1}.{self.scale}f}"
        return f"{sign}{digits}"

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        text = str(value)
        if text.startswith(("0", "-")) and dialect.name != "postgresql":
            sign, text = text[0], text[1:]
            return Decimal(text) * (-1 if sign == "-" else 1)
        return Decimal(text)


class Quantity(Money):
    """A quantity. Six decimal places, so fractional units of measure are exact."""

    def __init__(self) -> None:
        super().__init__(precision=18, scale=WORKING_PRECISION)


class Rate(Money):
    """A rate or percentage held as a fraction. Six decimal places."""

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
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
