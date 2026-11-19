"""Inventory: states, movements, and the layer arithmetic both valuation policies share.

PRD §6.11, ADR-0004. Pure, like the rest of the domain. The rule that keeps valuation in
agreement with the general ledger is arithmetic — what a part of a layer is worth, how an
average pool is re-spread, which layer FIFO takes first — and arithmetic is tested
without a database.

A layer holds its remaining *value*, not a unit cost. A unit cost is a quotient, and a
quotient held at six places and multiplied back by a quantity leaves a residue in the
sixth place; summed over a year of movements, the residue is a difference between the
valuation report and the inventory account that nobody can explain. Holding the value and
taking a proportion of it, with the whole of it on the last unit, leaves none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.money import WORKING_PRECISION, ZERO, allocate, divide, money, multiply

# --------------------------------------------------------------------------------------
# States and where each is valued
# --------------------------------------------------------------------------------------


class StockState(StrEnum):
    """The quantity states of PRD §6.11, as dimensions of the ledger."""

    USABLE = "usable"
    """On hand, passed, sellable. Available less what is reserved."""
    QUARANTINE = "quarantine"
    """Received pending inspection. Owned, valued, and not available."""
    REJECTED = "rejected"
    """Failed inspection and held for return. Owned, valued, and never available."""
    IN_TRANSIT = "in_transit"
    """Dispatched between warehouses and not yet received at the destination."""
    RESERVED = "reserved"
    """An overlay on usable stock held for an order line. Carries no value of its own."""

    @property
    def is_valued(self) -> bool:
        return self is not StockState.RESERVED

    @property
    def is_physical(self) -> bool:
        """Whether the state is a place stock is in, rather than a claim on it."""
        return self is not StockState.RESERVED


VALUED_STATES: Final = frozenset(state for state in StockState if state.is_valued)

INVENTORY: Final = "12000"
GOODS_IN_QUARANTINE: Final = "12100"
GOODS_IN_TRANSIT: Final = "12200"
COST_OF_GOODS_SOLD: Final = "50000"
WRITE_OFF: Final = "51000"
OTHER_PAYABLES: Final = "20100"
GOODS_RECEIVED_NOT_INVOICED: Final = "20150"
OWNERS_CAPITAL: Final = "30000"

STATE_ACCOUNT: Final[dict[StockState, str]] = {
    StockState.USABLE: INVENTORY,
    StockState.QUARANTINE: GOODS_IN_QUARANTINE,
    StockState.REJECTED: GOODS_IN_QUARANTINE,
    StockState.IN_TRANSIT: GOODS_IN_TRANSIT,
}
"""The account each valued state is carried in. Rejected goods are still owned, and
held in quarantine until they leave."""

INVENTORY_ACCOUNTS: Final = frozenset(STATE_ACCOUNT.values())
"""The control accounts the valuation report reconciles to. None accepts a manual journal."""


class MovementType(StrEnum):
    """Every reason stock moves, per PRD §6.11."""

    OPENING = "opening"
    RECEIPT = "receipt"
    INSPECTION_PASS = "inspection_pass"
    INSPECTION_FAIL = "inspection_fail"
    RETURN_TO_SUPPLIER = "return_to_supplier"
    RELOCATION = "relocation"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    RESERVATION = "reservation"
    RELEASE = "release"
    ISSUE = "issue"
    CUSTOMER_RETURN = "customer_return"
    DAMAGE = "damage"
    EXPIRY = "expiry"
    COUNT_GAIN = "count_gain"
    COUNT_LOSS = "count_loss"
    ADJUSTMENT_GAIN = "adjustment_gain"
    ADJUSTMENT_LOSS = "adjustment_loss"
    REVALUATION = "revaluation"
    """Value into or out of existing layers with no change in quantity: a landed charge,
    an approved invoice price variance, capitalised tax, a supplier credit."""
    REVERSAL = "reversal"


LOSS_MOVEMENTS: Final = frozenset(
    {
        MovementType.DAMAGE,
        MovementType.EXPIRY,
        MovementType.COUNT_LOSS,
        MovementType.ADJUSTMENT_LOSS,
    }
)
GAIN_MOVEMENTS: Final = frozenset({MovementType.COUNT_GAIN, MovementType.ADJUSTMENT_GAIN})


class LocationKind(StrEnum):
    STORAGE = "storage"
    QUARANTINE = "quarantine"
    ASSEMBLY = "assembly"
    """Where reserved stock is staged for an order before dispatch."""


SEEDED_LOCATIONS: Final[tuple[tuple[str, str, LocationKind], ...]] = (
    ("MAIN", "Main store", LocationKind.STORAGE),
    ("QUAR", "Quarantine", LocationKind.QUARANTINE),
    ("ASSY", "Assembly", LocationKind.ASSEMBLY),
)
"""The places every warehouse has from the first movement, so a receipt into quarantine
and an order staged for dispatch each have somewhere to go."""


class SerialState(StrEnum):
    IN_STOCK = "in_stock"
    QUARANTINE = "quarantine"
    REJECTED = "rejected"
    IN_TRANSIT = "in_transit"
    ISSUED = "issued"
    RETURNED_TO_SUPPLIER = "returned_to_supplier"
    WRITTEN_OFF = "written_off"


SERIAL_STATE_FOR: Final[dict[StockState, SerialState]] = {
    StockState.USABLE: SerialState.IN_STOCK,
    StockState.QUARANTINE: SerialState.QUARANTINE,
    StockState.REJECTED: SerialState.REJECTED,
    StockState.IN_TRANSIT: SerialState.IN_TRANSIT,
}


class ReservationState(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    RELEASED = "released"


class TransferState(StrEnum):
    DRAFT = "draft"
    DISPATCHED = "dispatched"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class CountState(StrEnum):
    OPEN = "open"
    SUBMITTED = "submitted"
    POSTED = "posted"
    CANCELLED = "cancelled"


# --------------------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------------------


class ExpiryStatus(StrEnum):
    NONE = "none"
    """No expiry date is recorded."""
    GOOD = "good"
    NEAR = "near"
    """Inside the warning window."""
    EXPIRED = "expired"


def expiry_status(expires_on: date | None, today: date, warning_days: int) -> ExpiryStatus:
    """Where a batch stands against its expiry date.

    A batch expires at the end of its expiry date, so it may be issued on the day itself:
    a label reading 30 June means usable on 30 June.
    """
    if expires_on is None:
        return ExpiryStatus.NONE
    if expires_on < today:
        return ExpiryStatus.EXPIRED
    if expires_on <= today + timedelta(days=warning_days):
        return ExpiryStatus.NEAR
    return ExpiryStatus.GOOD


# --------------------------------------------------------------------------------------
# Layers
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayerView:
    """What the arithmetic needs of a layer."""

    key: object
    quantity: Decimal
    """Remaining."""
    value: Decimal
    """Remaining."""
    aged_on: date
    """When the stock first arrived, carried across transfers, so FIFO is by age of stock
    rather than by when it last moved."""
    order: str = ""
    """A tie-break among layers aged the same day: the layer identifier."""


@dataclass(frozen=True, slots=True)
class Take:
    """Quantity and value taken from one layer."""

    key: object
    quantity: Decimal
    value: Decimal


def value_of_part(layer_quantity: Decimal, layer_value: Decimal, taken: Decimal) -> Decimal:
    """What ``taken`` units of a layer are worth.

    The whole of a layer takes the whole of its value, so nothing is ever left behind:
    the identity that keeps the sum of the layers equal to the ledger.
    """
    if taken <= ZERO:
        raise ValidationError("A quantity taken from stock is above zero.", field="quantity")
    if taken > layer_quantity:
        raise ConflictError("More cannot be taken from a cost layer than it holds.")
    if taken == layer_quantity:
        return money(layer_value)
    return money(divide(multiply(layer_value, taken), layer_quantity))


def fifo_order(layers: list[LayerView]) -> list[LayerView]:
    """Oldest stock first, by arrival date and then by layer identifier."""
    return sorted(layers, key=lambda layer: (layer.aged_on, layer.order))


def consume(layers: list[LayerView], quantity: Decimal) -> list[Take]:
    """Take ``quantity`` from layers, oldest first, and say what each part was worth.

    The same function serves both policies. In a moving-average pool every layer carries
    the same unit cost, so taking the oldest first costs exactly the average; in a FIFO
    pool it costs what the oldest stock cost. The policy decides how value is spread when
    it enters a pool, not how it leaves.
    """
    if quantity <= ZERO:
        raise ValidationError("A quantity taken from stock is above zero.", field="quantity")
    remaining = money(quantity)
    taken: list[Take] = []
    for layer in fifo_order([item for item in layers if item.quantity > ZERO]):
        if remaining <= ZERO:
            break
        portion = min(layer.quantity, remaining)
        taken.append(
            Take(
                key=layer.key,
                quantity=portion,
                value=value_of_part(layer.quantity, layer.value, portion),
            )
        )
        remaining = money(remaining - portion)
    if remaining > ZERO:
        held = money(sum((item.quantity for item in layers), ZERO))
        raise ConflictError(
            f"Only {held.normalize()} is held at cost; "
            f"{money(quantity).normalize()} was asked for.",
            held=str(held),
        )
    return taken


def reaverage(layers: list[LayerView]) -> list[Take]:
    """Spread a pool's whole value across its layers by quantity: the moving average.

    Returns each layer's new remaining value. The parts sum to the pool's total exactly —
    the residue of the division goes to the largest layer — so re-averaging never changes
    what the pool is worth, only how its worth is distributed.
    """
    live = [layer for layer in layers if layer.quantity > ZERO]
    if not live:
        return []
    total = money(sum((layer.value for layer in live), ZERO))
    shares = allocate(total, [layer.quantity for layer in live], WORKING_PRECISION)
    return [
        Take(key=layer.key, quantity=layer.quantity, value=share)
        for layer, share in zip(live, shares, strict=True)
    ]


def unit_cost(quantity: Decimal, value: Decimal) -> Decimal:
    """A pool's cost per unit, for display and for valuing a gain. Zero when empty."""
    if quantity <= ZERO:
        return ZERO
    return money(divide(value, quantity))


def split_revaluation(
    amount: Decimal, received: Decimal, remaining: Decimal
) -> tuple[Decimal, Decimal]:
    """How a change in a receipt's cost divides between the shelf and cost of sales.

    A landed charge, an invoice price variance or a supplier credit relates to every unit
    the receipt brought in. The share for units still on the shelf goes into their layer;
    the share for units already issued goes to cost of sales, where those units now are.
    Returns ``(to_layer, to_cost_of_sales)``, summing to ``amount`` exactly.
    """
    if received <= ZERO:
        raise ValidationError("A receipt revalued must have received something.", field="received")
    if remaining < ZERO or remaining > received:
        raise ValidationError("Remaining stock lies between nothing and what was received.")
    if remaining == received:
        return money(amount), ZERO
    if remaining == ZERO:
        return ZERO, money(amount)
    to_layer = money(divide(multiply(amount, remaining), received))
    return to_layer, money(amount - to_layer)


# --------------------------------------------------------------------------------------
# Availability and counts
# --------------------------------------------------------------------------------------


def available(usable_unexpired: Decimal, reserved: Decimal) -> Decimal:
    """What may be promised: usable stock in good batches, less what is already promised."""
    return money(max(ZERO, usable_unexpired - reserved))


@dataclass(frozen=True, slots=True)
class CountVariance:
    system: Decimal
    counted: Decimal

    @property
    def difference(self) -> Decimal:
        return money(self.counted - self.system)

    @property
    def movement(self) -> MovementType | None:
        if self.difference > ZERO:
            return MovementType.COUNT_GAIN
        if self.difference < ZERO:
            return MovementType.COUNT_LOSS
        return None


def assert_quantity(
    value: Decimal, *, field: str = "quantity", allow_zero: bool = False
) -> Decimal:
    """A quantity at working precision, positive (or zero where the caller permits)."""
    quantity = money(value)
    if quantity < ZERO or (quantity == ZERO and not allow_zero):
        raise ValidationError("A quantity is above zero.", field=field)
    return quantity
