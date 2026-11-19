"""Dispatch: shipment states, modes, delivery outcomes. PRD §6.9.

Pure. The arithmetic that matters here is the reconciliation of a shipment line: every
unit that left is delivered, returned to the store, returned damaged, or lost, and the
four never sum to more or less than what was shipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from nickland.domain.errors import StateTransitionError, ValidationError
from nickland.domain.money import ZERO, money, plain


class ShipmentState(StrEnum):
    PREPARED = "prepared"
    PACKED = "packed"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    PARTIALLY_DELIVERED = "partially_delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_closed(self) -> bool:
        return self in CLOSED_STATES

    @property
    def label(self) -> str:
        return _STATE_LABELS[self]


CLOSED_STATES: Final = frozenset(
    {
        ShipmentState.DELIVERED,
        ShipmentState.PARTIALLY_DELIVERED,
        ShipmentState.FAILED,
        ShipmentState.CANCELLED,
    }
)

_STATE_LABELS: Final = {
    ShipmentState.PREPARED: "Prepared",
    ShipmentState.PACKED: "Packed",
    ShipmentState.DISPATCHED: "In transit",
    ShipmentState.DELIVERED: "Delivered",
    ShipmentState.PARTIALLY_DELIVERED: "Partially delivered",
    ShipmentState.FAILED: "Delivery failed",
    ShipmentState.CANCELLED: "Cancelled",
}

_TRANSITIONS: Final[dict[ShipmentState, frozenset[ShipmentState]]] = {
    ShipmentState.PREPARED: frozenset({ShipmentState.PACKED, ShipmentState.CANCELLED}),
    ShipmentState.PACKED: frozenset({ShipmentState.DISPATCHED, ShipmentState.CANCELLED}),
    ShipmentState.DISPATCHED: frozenset(
        {ShipmentState.DELIVERED, ShipmentState.PARTIALLY_DELIVERED, ShipmentState.FAILED}
    ),
}


def assert_shipment_transition(current: ShipmentState, target: ShipmentState) -> None:
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise StateTransitionError("A shipment", current.label.lower(), target.label.lower())


class DispatchMode(StrEnum):
    CARRIER = "carrier"
    OWN_VEHICLE = "own_vehicle"
    COURIER = "courier"
    COLLECTION = "collection"
    """The customer collects. No driver or vehicle of ours."""

    @property
    def label(self) -> str:
        return {
            DispatchMode.CARRIER: "Carrier",
            DispatchMode.OWN_VEHICLE: "Own vehicle",
            DispatchMode.COURIER: "Courier",
            DispatchMode.COLLECTION: "Customer collection",
        }[self]


@dataclass(frozen=True, slots=True)
class DispatchDetail:
    mode: DispatchMode
    carrier: str | None
    driver: str | None
    vehicle: str | None
    destination: str | None
    package_count: int
    tracking_reference: str | None

    def validate(self) -> None:
        if self.package_count < 1:
            raise ValidationError("A consignment has at least one package.", field="package_count")
        if self.mode in (DispatchMode.CARRIER, DispatchMode.COURIER) and not self.carrier:
            raise ValidationError(
                f"A {self.mode.label.lower()} dispatch names the {self.mode.label.lower()}.",
                field="carrier",
            )
        if self.mode is DispatchMode.OWN_VEHICLE and not (self.driver and self.vehicle):
            raise ValidationError(
                "A dispatch in our own vehicle records the driver and the vehicle.",
                field="driver",
            )
        if self.mode is not DispatchMode.COLLECTION and not self.destination:
            raise ValidationError("A delivery records where it is going.", field="destination")


class ExceptionKind(StrEnum):
    """What can go wrong between the bay and the customer's signature."""

    RETURNED = "returned"
    """Came back to the store whole: refused, nobody to receive, the wrong address."""
    DAMAGED = "damaged"
    """Came back damaged: held in quarantine for inspection."""
    LOST = "lost"
    """Did not arrive and did not come back."""
    CUSTOMER_RETURN = "customer_return"
    """Received, then returned by the customer."""

    @property
    def label(self) -> str:
        return {
            ExceptionKind.RETURNED: "Returned to store",
            ExceptionKind.DAMAGED: "Returned damaged",
            ExceptionKind.LOST: "Lost in transit",
            ExceptionKind.CUSTOMER_RETURN: "Returned by customer",
        }[self]


@dataclass(frozen=True, slots=True)
class LineOutcome:
    """What became of the units a shipment line carried."""

    shipped: Decimal
    delivered: Decimal
    returned: Decimal = ZERO
    damaged: Decimal = ZERO
    lost: Decimal = ZERO

    def validate(self, *, position: int) -> None:
        for name in ("delivered", "returned", "damaged", "lost"):
            if getattr(self, name) < ZERO:
                raise ValidationError(f"Line {position}: quantities are not negative.")
        accounted = money(self.delivered + self.returned + self.damaged + self.lost)
        if accounted != money(self.shipped):
            raise ValidationError(
                f"Line {position}: {plain(money(self.shipped))} was shipped, and "
                f"{plain(accounted)} is accounted for. Every unit is delivered, "
                "returned, returned damaged, or lost.",
                field="lines",
            )

    @property
    def short(self) -> Decimal:
        return money(self.shipped - self.delivered)


def state_after_delivery(outcomes: list[LineOutcome]) -> ShipmentState:
    shipped = sum((outcome.shipped for outcome in outcomes), ZERO)
    delivered = sum((outcome.delivered for outcome in outcomes), ZERO)
    if delivered == ZERO:
        return ShipmentState.FAILED
    if delivered < shipped:
        return ShipmentState.PARTIALLY_DELIVERED
    return ShipmentState.DELIVERED


def outstanding(ordered: Decimal, cancelled: Decimal, delivered: Decimal) -> Decimal:
    """What the customer is still owed on a line."""
    return money(max(ZERO, ordered - cancelled - delivered))
