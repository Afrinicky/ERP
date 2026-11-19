"""Request and response contracts for shipments and proof of delivery.

Quantities cross the boundary as strings, never as JSON numbers, per PRD §9.5.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from nickland.interface.schemas import Contract, Reply


def _refuse_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("Send a quantity as a string, not a JSON number.")
    return value


class _Figures(Contract):
    @field_validator("*", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class ShipmentLineContract(_Figures):
    sales_order_line_id: uuid.UUID
    quantity: Decimal
    serials: list[str] = Field(default_factory=list)


class PrepareContract(Contract):
    sales_order_id: uuid.UUID
    lines: list[ShipmentLineContract] = Field(min_length=1)
    destination: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class PackContract(Contract):
    package_count: int = Field(ge=1, le=9999)


class DispatchContract(Contract):
    mode: str = Field(pattern="^(carrier|own_vehicle|courier|collection)$")
    package_count: int = Field(ge=1, le=9999)
    carrier: str | None = Field(default=None, max_length=160)
    driver: str | None = Field(default=None, max_length=160)
    vehicle: str | None = Field(default=None, max_length=80)
    destination: str | None = Field(default=None, max_length=500)
    tracking_reference: str | None = Field(default=None, max_length=120)
    override_reason: str | None = Field(default=None, max_length=300)


class OutcomeContract(_Figures):
    shipment_line_id: uuid.UUID
    delivered: Decimal
    returned: Decimal = Decimal("0")
    damaged: Decimal = Decimal("0")
    lost: Decimal = Decimal("0")
    reason: str | None = Field(default=None, max_length=300)
    serials_delivered: list[str] = Field(default_factory=list)


class DeliveryContract(Contract):
    outcomes: list[OutcomeContract] = Field(min_length=1)
    recipient_name: str | None = Field(default=None, max_length=160)
    recipient_role: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=200)
    comments: str | None = None
    evidence_kind: str = Field(default="signature", max_length=16)
    evidence_base64: str | None = None
    media_type: str | None = Field(default=None, max_length=80)


class CancelShipmentContract(Contract):
    reason: str = Field(min_length=1, max_length=300)


class CustomerReturnContract(_Figures):
    quantity: Decimal
    reason: str = Field(min_length=1, max_length=300)
    replacement_owed: bool = True
    serials: list[str] = Field(default_factory=list)


class ShippableLineReply(Reply):
    line_id: uuid.UUID
    position: int
    description: str
    sku: str | None
    tracking: str
    holds_stock: bool
    ordered: Decimal
    delivered: Decimal
    outstanding: Decimal
    staged: Decimal
    shippable: Decimal


class DocumentLinkReply(Reply):
    id: uuid.UUID
    number: str
    kind: str


class ProofReply(Reply):
    recipient_name: str
    recipient_role: str | None
    acknowledged_at: datetime
    location: str | None
    evidence_kind: str
    has_evidence: bool
    evidence_hash: str | None


class ShipmentLineReply(Reply):
    id: uuid.UUID
    position: int
    sales_order_line_id: uuid.UUID
    sku: str
    description: str
    tracking: str
    quantity: Decimal
    delivered: Decimal
    returned: Decimal
    damaged: Decimal
    lost: Decimal
    customer_returned: Decimal
    serials: list[str]


class DeliveryExceptionReply(Reply):
    id: uuid.UUID
    line_position: int
    kind: str
    kind_label: str
    quantity: Decimal
    reason: str
    replacement_owed: bool
    occurred_on: date


class ShipmentReply(Reply):
    id: uuid.UUID
    number: str
    order_id: uuid.UUID
    order_number: str | None
    customer: str | None
    warehouse: str
    state: str
    state_label: str
    mode: str
    mode_label: str
    carrier: str | None
    driver: str | None
    vehicle: str | None
    destination: str | None
    package_count: int
    tracking_reference: str | None
    prepared_on: date
    packed_on: date | None
    dispatched_on: date | None
    delivered_on: date | None
    notes: str | None
    pick_list: DocumentLinkReply | None
    packing_list: DocumentLinkReply | None
    waybill: DocumentLinkReply | None
    delivery_note: DocumentLinkReply | None
    proof: ProofReply | None
    lines: list[ShipmentLineReply]
    exceptions: list[DeliveryExceptionReply]
