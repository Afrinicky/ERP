"""Catalogue rules: item classification, units of measure and barcodes.

PRD 6.1 requires generic products and services, categories, variants, attributes, SKUs,
barcodes and units of measure, and requires that a non-stock item carry no stock
balance anywhere in the system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.money import divide, multiply, quantity


class ItemClass(StrEnum):
    """What kind of thing is being sold, per PRD 6.1."""

    STOCKED = "stocked"
    """Held in inventory. Carries a stock balance and a valuation."""

    NON_STOCK = "non_stock"
    """Procured on demand against a customer order. Never carries a stock balance."""

    SERVICE = "service"
    """Labour or another intangible. Never carries a stock balance."""

    BUNDLE = "bundle"
    """A kit assembled from component items. Its components carry the balances."""

    @property
    def holds_stock(self) -> bool:
        """Whether an item of this class may carry a stock balance.

        PRD 6.1: a non-stock item requires no stock balance. The inventory module reads
        this rather than inferring from the absence of a balance.
        """
        return self is ItemClass.STOCKED

    @property
    def is_procured(self) -> bool:
        """Whether the item is bought from a supplier."""
        return self in (ItemClass.STOCKED, ItemClass.NON_STOCK)


class TrackingPolicy(StrEnum):
    """How individual units are identified, per PRD 6.8."""

    NONE = "none"
    BATCH = "batch"
    """Batch or lot, as medical and laboratory supplies generally require."""
    SERIAL = "serial"
    """Each unit individually identified."""


class ValuationPolicy(StrEnum):
    """Inventory valuation, per ADR-0004.

    Moving weighted average is the configured default; FIFO is selectable per product
    over the same layer-capable ledger.
    """

    BUSINESS_DEFAULT = "business_default"
    WEIGHTED_AVERAGE = "weighted_average"
    FIFO = "fifo"


class UnitKind(StrEnum):
    """The dimension a unit measures. Conversions cross no dimension."""

    COUNT = "count"
    MASS = "mass"
    VOLUME = "volume"
    LENGTH = "length"
    AREA = "area"
    TIME = "time"


class AttributeKind(StrEnum):
    """How an attribute's value is captured."""

    TEXT = "text"
    NUMBER = "number"
    CHOICE = "choice"
    BOOLEAN = "boolean"


class BarcodeKind(StrEnum):
    """Barcode symbologies the catalogue recognises."""

    EAN_13 = "ean_13"
    EAN_8 = "ean_8"
    UPC_A = "upc_a"
    CODE_128 = "code_128"
    CODE_39 = "code_39"
    ITF_14 = "itf_14"
    INTERNAL = "internal"
    """An internally assigned code, subject to no external check."""


_SKU = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{1,38}[A-Z0-9]$")
_DIGITS = re.compile(r"^\d+$")

_FIXED_LENGTH: dict[BarcodeKind, int] = {
    BarcodeKind.EAN_13: 13,
    BarcodeKind.EAN_8: 8,
    BarcodeKind.UPC_A: 12,
    BarcodeKind.ITF_14: 14,
}


def validate_sku(sku: str, *, field: str = "sku") -> str:
    """Normalise and validate a stock-keeping unit. Returns the normalised form."""
    candidate = sku.strip().upper().replace(" ", "-")
    if not _SKU.match(candidate):
        raise ValidationError(
            "SKU must be 3 to 40 characters, beginning and ending with a letter or "
            "digit, using letters, digits, full stops, hyphens, slashes and underscores.",
            field=field,
        )
    return candidate


def check_digit(digits: str) -> int:
    """The GS1 modulo-10 check digit for a body of digits.

    Weights alternate 3 and 1 from the rightmost body digit leftwards.
    """
    total = 0
    for position, character in enumerate(reversed(digits)):
        weight = 3 if position % 2 == 0 else 1
        total += int(character) * weight
    return (10 - total % 10) % 10


def validate_barcode(value: str, kind: BarcodeKind, *, field: str = "barcode") -> str:
    """Normalise and validate a barcode.

    A fixed-length numeric symbology is checked against its GS1 check digit, so a
    mistyped code is rejected at entry rather than discovered at the till.
    """
    expected = _FIXED_LENGTH.get(kind)

    # A hyphen or a space in a fixed-length numeric code is formatting, and is removed.
    # In CODE-128 and CODE-39 a hyphen is part of the code itself, so only surrounding
    # whitespace is trimmed.
    candidate = (
        value.strip().replace(" ", "").replace("-", "") if expected is not None else value.strip()
    )
    if not candidate:
        raise ValidationError("A barcode is required.", field=field)
    if len(candidate) > 48:
        raise ValidationError("Barcode exceeds the maximum length.", field=field)

    if expected is None:
        return candidate.upper()

    name = kind.value.replace("_", "-").upper()
    article = "An" if name[0] in "AEIOU" else "A"
    if not _DIGITS.match(candidate):
        raise ValidationError(f"{article} {name} barcode contains digits only.", field=field)
    if len(candidate) != expected:
        raise ValidationError(
            f"{article} {name} barcode is {expected} digits; this one is {len(candidate)}.",
            field=field,
        )
    body, supplied = candidate[:-1], int(candidate[-1])
    if check_digit(body) != supplied:
        raise ValidationError(
            f"The check digit is wrong. It should be {check_digit(body)}, not {supplied}. "
            "Check the code was read or typed correctly.",
            field=field,
        )
    return candidate


@dataclass(frozen=True, slots=True)
class UnitConversion:
    """A conversion from one unit to another, within one dimension.

    ``factor`` is how many of the base unit one of this unit contains: a box of 24
    has a factor of 24 against each.
    """

    unit_code: str
    base_code: str
    factor: Decimal

    def __post_init__(self) -> None:
        if self.factor <= 0:
            raise ValidationError("A conversion factor must be greater than zero.", field="factor")
        if self.unit_code == self.base_code and self.factor != Decimal("1"):
            raise ValidationError("A unit converts to itself at a factor of one.", field="factor")

    def to_base(self, amount: Decimal) -> Decimal:
        """Express an amount of this unit in the base unit."""
        return multiply(quantity(amount), self.factor)

    def from_base(self, amount: Decimal) -> Decimal:
        """Express an amount of the base unit in this unit."""
        return divide(quantity(amount), self.factor)


def convert(
    amount: Decimal,
    *,
    from_factor: Decimal,
    to_factor: Decimal,
) -> Decimal:
    """Convert between two units sharing a base, by way of the base.

    Both factors express the unit in terms of the shared base, so the conversion is
    exact and reversible. Rounding occurs at presentation only.
    """
    if from_factor <= 0 or to_factor <= 0:
        raise ValidationError("A conversion factor must be greater than zero.")
    return divide(multiply(quantity(amount), from_factor), to_factor)


def assert_stock_permitted(item_class: ItemClass, sku: str) -> None:
    """Raise unless the item may carry a stock balance.

    PRD 6.1 requires that a non-stock item carry no balance. Enforcing it here means a
    later phase cannot create one by oversight.
    """
    if not item_class.holds_stock:
        raise ConflictError(
            f"{sku} is a {item_class.value.replace('_', '-')} item and carries no stock "
            "balance. Change its classification if it is to be held in inventory.",
            sku=sku,
            item_class=item_class.value,
        )


def assert_tracking_consistent(item_class: ItemClass, tracking: TrackingPolicy) -> None:
    """Raise where a tracking policy cannot apply to the classification."""
    if tracking is not TrackingPolicy.NONE and not item_class.holds_stock:
        raise ValidationError(
            f"A {item_class.value.replace('_', '-')} item cannot carry "
            f"{tracking.value} tracking, because it holds no stock.",
            field="tracking_policy",
        )


def assert_expiry_consistent(item_class: ItemClass, requires_expiry: bool) -> None:
    """Raise where an expiry requirement cannot apply to the classification."""
    if requires_expiry and not item_class.holds_stock:
        raise ValidationError(
            f"A {item_class.value.replace('_', '-')} item cannot require an expiry date, "
            "because it holds no stock.",
            field="requires_expiry",
        )


@dataclass(frozen=True, slots=True)
class BundleComponent:
    """One line of a kit."""

    product_id: object
    quantity: Decimal
    unit_code: str


def assert_bundle_valid(
    components: list[BundleComponent], *, own_id: object, depth: int = 0
) -> None:
    """Validate a kit's composition.

    A kit may not contain itself, nor be empty, nor carry a component twice.
    """
    if depth > 0:
        return
    if not components:
        raise ValidationError("A bundle requires at least one component.", field="components")
    identifiers = [component.product_id for component in components]
    if own_id in identifiers:
        raise ValidationError("A bundle cannot contain itself.", field="components")
    if len(set(identifiers)) != len(identifiers):
        raise ValidationError(
            "A component appears more than once. Combine the quantities instead.",
            field="components",
        )
    for component in components:
        if component.quantity <= 0:
            raise ValidationError(
                "A component quantity must be greater than zero.", field="components"
            )


def variant_label(attributes: dict[str, str]) -> str:
    """A readable label for a variant: ``Blue · Large``."""
    return " · ".join(str(value) for _, value in sorted(attributes.items()))


def derive_variant_sku(parent_sku: str, attributes: dict[str, str]) -> str:
    """Derive a variant SKU from its parent and its attribute values."""
    suffix = "-".join(
        re.sub(r"[^A-Z0-9]+", "", str(value).upper())[:6]
        for _, value in sorted(attributes.items())
        if str(value).strip()
    )
    return validate_sku(f"{parent_sku}-{suffix}" if suffix else parent_sku)
