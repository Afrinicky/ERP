"""Request and response contracts for inventory, receiving and assembly.

Quantities and values cross the boundary as strings, never as JSON numbers, per PRD §9.5.
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
        raise ValueError("Send a quantity or an amount as a string, not a JSON number.")
    return value


class _Figures(Contract):
    """A contract whose Decimal fields refuse floats."""

    @field_validator("*", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


# -- configuration and places ----------------------------------------------------------


class InventorySettingsContract(Contract):
    valuation_policy: str = Field(pattern="^(weighted_average|fifo)$")
    expiry_warning_days: int = Field(ge=0, le=730)


class InventorySettingsReply(Reply):
    valuation_policy: str
    expiry_warning_days: int


class LocationContract(Contract):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=120)


class BinContract(Contract):
    code: str = Field(min_length=1, max_length=24)
    name: str | None = Field(default=None, max_length=120)


class BinReply(Reply):
    id: uuid.UUID
    code: str
    name: str | None
    is_active: bool


class LocationReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    kind: str
    is_active: bool
    bins: list[BinReply]


class WarehouseStockReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    is_default: bool
    locations: list[LocationReply]


# -- reports ---------------------------------------------------------------------------


class PositionReply(Reply):
    product_id: uuid.UUID
    sku: str
    name: str
    warehouse_id: uuid.UUID
    warehouse: str
    usable: Decimal
    quarantine: Decimal
    rejected: Decimal
    in_transit: Decimal
    reserved: Decimal
    available: Decimal
    incoming: Decimal
    value: Decimal | None
    """Cost figures appear only to those who hold ``costing.view``."""
    unit_cost: Decimal | None
    reorder_level: Decimal | None
    tracking: str


class BatchReply(Reply):
    batch_id: uuid.UUID
    batch: str
    product_id: uuid.UUID
    sku: str
    name: str
    warehouse: str
    expires_on: date | None
    quantity: Decimal
    status: str
    days: int | None


class MovementReply(Reply):
    id: uuid.UUID
    operation_id: uuid.UUID
    movement_type: str
    state: str
    product_id: uuid.UUID
    sku: str
    warehouse: str
    location: str | None
    batch: str | None
    serial: str | None
    quantity: Decimal
    value: Decimal | None
    reference: str | None
    reason: str | None
    override_reason: str | None
    occurred_on: date
    created_at: datetime
    reversible: bool


class ValuationRowReply(Reply):
    product_id: uuid.UUID
    sku: str
    name: str
    warehouse: str
    state: str
    account: str
    quantity: Decimal
    value: Decimal
    unit_cost: Decimal


class AccountAgreementReply(Reply):
    account: str
    name: str
    layers: Decimal
    ledger: Decimal
    difference: Decimal
    agrees: bool


class ValuationReply(Reply):
    rows: list[ValuationRowReply]
    accounts: list[AccountAgreementReply]
    total: Decimal
    agrees: bool


class DiscrepancyReply(Reply):
    record: str
    key: str
    expected: Decimal
    found: Decimal


class ReconciliationReply(Reply):
    movements: int
    agrees: bool
    discrepancies: list[DiscrepancyReply]


# -- operations ------------------------------------------------------------------------


class StockLineContract(_Figures):
    product_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal | None = None
    batch: str | None = Field(default=None, max_length=80)
    batch_id: uuid.UUID | None = None
    expiry: date | None = None
    manufacturer: str | None = Field(default=None, max_length=160)
    serials: list[str] = Field(default_factory=list)
    location_id: uuid.UUID | None = None
    bin_id: uuid.UUID | None = None


class OpeningContract(Contract):
    warehouse_id: uuid.UUID
    lines: list[StockLineContract] = Field(min_length=1)
    occurred_on: date | None = None
    reference: str | None = Field(default=None, max_length=80)


class AdjustmentContract(Contract):
    warehouse_id: uuid.UUID
    kind: str = Field(pattern="^(gain|loss|damage|expiry)$")
    reason: str = Field(min_length=1, max_length=300)
    lines: list[StockLineContract] = Field(min_length=1)
    occurred_on: date | None = None


class RelocationContract(_Figures):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    to_location_id: uuid.UUID
    to_bin_id: uuid.UUID | None = None
    from_location_id: uuid.UUID | None = None
    from_bin_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    serials: list[str] = Field(default_factory=list)


class ReverseContract(Contract):
    reason: str = Field(min_length=1, max_length=300)


class OperationReply(Reply):
    operation_id: uuid.UUID
    movements: int
    journal_entry_id: uuid.UUID | None


class TransferLineContract(_Figures):
    product_id: uuid.UUID
    quantity: Decimal
    batch_id: uuid.UUID | None = None
    serials: list[str] = Field(default_factory=list)


class TransferContract(Contract):
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    lines: list[TransferLineContract] = Field(min_length=1)
    notes: str | None = None


class TransferLineReply(Reply):
    id: uuid.UUID
    position: int
    product_id: uuid.UUID
    sku: str
    name: str
    batch: str | None
    quantity: Decimal


class TransferReply(Reply):
    id: uuid.UUID
    number: str | None
    from_warehouse: str
    to_warehouse: str
    state: str
    requested_on: date
    dispatched_on: date | None
    received_on: date | None
    notes: str | None
    lines: list[TransferLineReply]


class InspectionContract(_Figures):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    passed: Decimal
    failed: Decimal = Decimal("0")
    batch_id: uuid.UUID | None = None
    receipt_line_id: uuid.UUID | None = None
    serials_passed: list[str] = Field(default_factory=list)
    serials_failed: list[str] = Field(default_factory=list)
    criteria: str | None = None
    findings: str | None = None
    evidence_attachment_id: uuid.UUID | None = None
    inspected_on: date | None = None


class InspectionReply(Reply):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    name: str
    warehouse: str
    batch: str | None
    quantity_passed: Decimal
    quantity_failed: Decimal
    criteria: str | None
    findings: str | None
    inspected_on: date
    inspector: str | None


class SupplierReturnContract(_Figures):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    reason: str = Field(min_length=1, max_length=300)
    batch_id: uuid.UUID | None = None
    serials: list[str] = Field(default_factory=list)
    receipt_line_id: uuid.UUID | None = None


class CustomerReturnContract(_Figures):
    issue_operation_id: uuid.UUID
    product_id: uuid.UUID
    quantity: Decimal
    reason: str = Field(min_length=1, max_length=300)
    serials: list[str] = Field(default_factory=list)


class ReservationContract(_Figures):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    sales_order_line_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=300)


class ReleaseContract(_Figures):
    quantity: Decimal | None = None


class ReservationReply(Reply):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    name: str
    warehouse: str
    batch: str | None
    sales_order_line_id: uuid.UUID | None
    order_number: str | None
    quantity: Decimal
    quantity_consumed: Decimal
    quantity_released: Decimal
    state: str
    created_at: datetime


class IssueContract(_Figures):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    batch_id: uuid.UUID | None = None
    serials: list[str] = Field(default_factory=list)
    reservation_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    override_reason: str | None = Field(default=None, max_length=300)
    reference: str | None = Field(default=None, max_length=80)


class CountOpenContract(Contract):
    warehouse_id: uuid.UUID
    location_id: uuid.UUID | None = None
    product_ids: list[uuid.UUID] = Field(default_factory=list)
    notes: str | None = None


class CountedLineContract(_Figures):
    line_id: uuid.UUID
    counted_quantity: Decimal | None = None
    reason: str | None = Field(default=None, max_length=300)


class CountRecordContract(Contract):
    lines: list[CountedLineContract]


class CountLineReply(Reply):
    id: uuid.UUID
    position: int
    product_id: uuid.UUID
    sku: str
    name: str
    location: str | None
    batch: str | None
    system_quantity: Decimal
    counted_quantity: Decimal | None
    difference: Decimal | None
    reason: str | None


class CountReply(Reply):
    id: uuid.UUID
    number: str | None
    warehouse: str
    location: str | None
    state: str
    counted_on: date
    notes: str | None
    lines: list[CountLineReply]


class LandedChargeContract(_Figures):
    receipt_id: uuid.UUID
    category: str = Field(pattern="^(freight|duty|clearing|port|handling|insurance|other)$")
    amount: Decimal
    method: str = Field(default="by_value", pattern="^(by_value|by_quantity)$")
    reference: str | None = Field(default=None, max_length=120)
    payee_id: uuid.UUID | None = None
    occurred_on: date | None = None


class LandedChargeReply(Reply):
    id: uuid.UUID
    category: str
    method: str
    reference: str | None
    amount: Decimal
    to_inventory: Decimal
    to_cost_of_sales: Decimal
    created_at: datetime


# -- assembly --------------------------------------------------------------------------


class AssemblyQuantityContract(_Figures):
    quantity: Decimal
    batch_id: uuid.UUID | None = None
    serials: list[str] = Field(default_factory=list)
    override_reason: str | None = Field(default=None, max_length=300)


class AssemblyLineReply(Reply):
    line_id: uuid.UUID
    position: int
    description: str
    product_id: uuid.UUID | None
    sku: str | None
    tracking: str
    holds_stock: bool
    required: Decimal
    available: Decimal
    reserved: Decimal
    incoming: Decimal
    assembled: Decimal
    staged: Decimal
    dispatched: Decimal
    outstanding: Decimal
    to_reserve: Decimal
    stageable: Decimal


class AssemblyReply(Reply):
    order_id: uuid.UUID
    order_number: str | None
    customer: str | None
    warehouse: str
    ready: bool
    lines: list[AssemblyLineReply]
