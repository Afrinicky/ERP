"""Monetary and quantity arithmetic.

Fixed-precision decimal throughout. Binary floating-point is prohibited in schema,
domain and transport, per PRD 9.5.

Six working decimal places; rounding to presentation precision occurs only at the
boundary, never between steps, per the pricing engine specification.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Final

WORKING_PRECISION: Final = 6
"""Decimal places retained between calculation steps."""

DEFAULT_PRESENTATION_PRECISION: Final = 4
"""Decimal places used when presenting an amount, unless the currency says otherwise."""

ZERO: Final = Decimal("0")

_WORKING_EXPONENT: Final = Decimal(1).scaleb(-WORKING_PRECISION)


def money(value: Decimal | int | str) -> Decimal:
    """Coerce a value to working precision.

    A float is rejected outright: admitting one would introduce binary floating-point
    into the calculation path.
    """
    if isinstance(value, float):
        raise TypeError(
            "Floating-point values are not permitted in monetary arithmetic. "
            "Pass a Decimal, int or str."
        )
    try:
        as_decimal = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Not a valid amount: {value!r}") from error
    if not as_decimal.is_finite():
        raise ValueError(f"Not a finite amount: {value!r}")
    return as_decimal.quantize(_WORKING_EXPONENT, rounding=ROUND_HALF_UP)


def present(value: Decimal, precision: int = DEFAULT_PRESENTATION_PRECISION) -> Decimal:
    """Round to presentation precision. Half-up, per the pricing engine specification."""
    if precision < 0:
        raise ValueError("Precision cannot be negative")
    return value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)


def multiply(left: Decimal, right: Decimal) -> Decimal:
    """Multiply at working precision with guard digits."""
    with localcontext() as context:
        context.prec = 34
        return money(left * right)


def divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Divide at working precision with guard digits."""
    if denominator == ZERO:
        raise ZeroDivisionError("Division by zero in monetary arithmetic")
    with localcontext() as context:
        context.prec = 34
        return money(numerator / denominator)


def apply_rate(base: Decimal, rate: Decimal) -> Decimal:
    """Return ``base * rate``. Used for percentages held as fractions."""
    return multiply(base, rate)


def gross_up(base: Decimal, rate: Decimal) -> Decimal:
    """Return ``base / (1 - rate)``.

    Used where a charge is expressed as a percentage of the resulting figure rather
    than of the figure beneath it, so that margin is preserved. See ADR-0007.
    """
    if rate >= Decimal("1"):
        raise ValueError("A gross-up rate of 1.0 or above is not permitted")
    if rate < ZERO:
        raise ValueError("A gross-up rate cannot be negative")
    return divide(base, Decimal("1") - rate)


def allocate(total: Decimal, weights: list[Decimal], precision: int) -> list[Decimal]:
    """Apportion ``total`` across ``weights`` so the parts sum to the total exactly.

    Rounding residue is assigned to the largest weight, per the pricing engine
    specification, so a document total always reconciles to the sum of its lines.
    """
    if not weights:
        return []
    if any(weight < ZERO for weight in weights):
        raise ValueError("Allocation weights cannot be negative")

    weight_total = sum(weights, ZERO)
    if weight_total == ZERO:
        # No basis on which to apportion: divide equally, residue to the first part.
        share = present(divide(total, Decimal(len(weights))), precision)
        parts = [share] * len(weights)
    else:
        # The product is formed before the division. Dividing first would quantize the
        # ratio to working precision, and an error of 5e-7 in a ratio becomes an error
        # of 5e-7 multiplied by the total in the share: apportioning 1,576.1786 over
        # six equal parts that way leaves a residue of 0.0028 rather than 0.0002, and
        # the whole of it lands on one part. Multiplying first keeps each share within
        # half a unit of the last place of its own figure, which is what rounding a
        # share is supposed to cost.
        parts = [
            present(divide(multiply(total, weight), weight_total), precision) for weight in weights
        ]

    residue = present(total, precision) - sum(parts, ZERO)
    if residue != ZERO:
        largest = max(range(len(weights)), key=lambda index: (weights[index], -index))
        parts[largest] = parts[largest] + residue
    return parts


def quantity(value: Decimal | int | str) -> Decimal:
    """Coerce a quantity to working precision. Fractional units are permitted."""
    return money(value)


def is_zero(value: Decimal) -> bool:
    return value == ZERO
