"""Request and response contracts for sales orders, procurement, billing and payables.

Amounts and quantities cross the boundary as strings, so no figure passes through a
JSON float, per PRD §9.5. The expected and actual unit costs on an order line are
internal figures and appear only on the variance report, which sits behind
``costing.view``; the order reply the sales role reads carries neither.
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
        raise ValueError("Send an amount or a quantity as a string, not a JSON number.")
    return value


# --------------------------------------------------------------------------------------
# Attachments
# --------------------------------------------------------------------------------------


class AttachmentContract(Contract):
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1)
    owner_type: str | None = Field(default=None, max_length=40)
    owner_id: uuid.UUID | None = None
    purpose: str | None = Field(default=None, max_length=40)
    immutable: bool = False


class AttachmentReply(Reply):
    id: uuid.UUID
    owner_type: str | None
    owner_id: uuid.UUID | None
    original_filename: str
    media_type: str
    size_bytes: int
    content_hash: str
    purpose: str | None
    is_immutable: bool
    created_at: datetime


# --------------------------------------------------------------------------------------
# Sales orders
# --------------------------------------------------------------------------------------


class AcceptanceContract(Contract):
    method: str = "purchase_order"
    reference: str | None = Field(default=None, max_length=120)
    accepted_on: date | None = None
    evidence_attachment_id: uuid.UUID | None = None
    notes: str | None = None


class AcceptanceReply(Reply):
    id: uuid.UUID
    quotation_fk: uuid.UUID
    method: str
    reference: str | None
    accepted_on: date
    evidence_attachment_fk: uuid.UUID | None
    notes: str | None
    recorded_by_id: uuid.UUID | None


class ConvertContract(Contract):
    required_by: date | None = None
    notes: str | None = None


class CancelLineContract(Contract):
    quantity: Decimal
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("quantity", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class ReasonContract(Contract):
    reason: str = Field(min_length=1, max_length=300)


class SubstituteContract(Contract):
    product_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=300)


class SalesOrderLineReply(Reply):
    id: uuid.UUID
    position: int
    product_fk: uuid.UUID | None
    description: str
    specification: str | None
    source_kind: str
    quantity_ordered: Decimal
    quantity_cancelled: Decimal
    quantity_procured: Decimal
    quantity_received: Decimal
    state: str
    unit_price: Decimal
    line_total: Decimal
    tax_amount: Decimal
    substituted_from_product_fk: uuid.UUID | None
    substitution_reason: str | None


class SalesOrderReply(Reply):
    id: uuid.UUID
    number: str
    quotation_fk: uuid.UUID
    acceptance_fk: uuid.UUID | None
    customer_fk: uuid.UUID
    customer_name: str | None = None
    customer_reference: str | None
    state: str
    ordered_on: date
    required_by: date | None
    currency_code: str
    precision: int
    payment_plan: str
    net_total: Decimal
    tax_total: Decimal
    total_payable: Decimal
    notes: str | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    lines: list[SalesOrderLineReply]
    purchase_order_numbers: list[str] = Field(default_factory=list)


class LineVarianceReply(Reply):
    line_id: uuid.UUID
    position: int
    description: str
    quantity: Decimal
    expected_unit_cost: Decimal
    actual_unit_cost: Decimal | None
    per_unit: Decimal | None
    total: Decimal | None


class OrderVarianceReply(Reply):
    order_id: uuid.UUID
    number: str
    lines: list[LineVarianceReply]
    expected_total: Decimal
    actual_total: Decimal | None
    variance_total: Decimal | None
    lines_known: int


# --------------------------------------------------------------------------------------
# Purchase orders and receipts
# --------------------------------------------------------------------------------------


class PurchaseLineContract(Contract):
    description: str = Field(min_length=1, max_length=300)
    quantity: Decimal
    unit_cost: Decimal
    product_id: uuid.UUID | None = None
    sales_order_line_id: uuid.UUID | None = None
    supplier_quotation_line_id: uuid.UUID | None = None
    discount_rate: Decimal = Decimal("0")
    unit_id: uuid.UUID | None = None
    expected_on: date | None = None
    notes: str | None = Field(default=None, max_length=300)

    @field_validator("quantity", "unit_cost", "discount_rate", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class PurchaseOrderContract(Contract):
    supplier_id: uuid.UUID
    lines: list[PurchaseLineContract] = Field(min_length=1)
    sales_order_id: uuid.UUID | None = None
    supplier_quotation_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    purpose: str = "order"
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    exchange_rate: Decimal = Decimal("1")
    expected_on: date | None = None
    supplier_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = None

    @field_validator("exchange_rate", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class PurchaseOrderLineReply(Reply):
    id: uuid.UUID
    position: int
    sales_order_line_fk: uuid.UUID | None
    product_fk: uuid.UUID | None
    description: str
    quantity_ordered: Decimal
    quantity_received: Decimal
    quantity_accepted: Decimal
    quantity_rejected: Decimal
    quantity_invoiced: Decimal
    unit_cost: Decimal
    discount_rate: Decimal
    net_unit_cost: Decimal
    line_total: Decimal
    expected_on: date | None
    notes: str | None


class PurchaseOrderReply(Reply):
    id: uuid.UUID
    number: str | None
    supplier_fk: uuid.UUID
    supplier_name: str | None = None
    sales_order_fk: uuid.UUID | None
    sales_order_number: str | None = None
    supplier_quotation_fk: uuid.UUID | None
    warehouse_fk: uuid.UUID | None
    purpose: str
    state: str
    ordered_on: date
    expected_on: date | None
    supplier_reference: str | None
    currency_code: str
    exchange_rate: Decimal
    net_total: Decimal
    tax_total: Decimal
    total: Decimal
    notes: str | None
    approved_at: datetime | None
    issued_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    lines: list[PurchaseOrderLineReply]


class SuggestedOrderReply(Reply):
    """A purchase order the buyer may raise, from the quotation's chosen sources."""

    supplier_id: uuid.UUID
    supplier_name: str
    currency_code: str | None
    exchange_rate: Decimal
    expected_on: date | None
    supplier_quotation_id: uuid.UUID | None
    lines: list[PurchaseLineContract]


class ReceiptLineContract(Contract):
    purchase_order_line_id: uuid.UUID
    quantity_received: Decimal
    quantity_rejected: Decimal = Decimal("0")
    rejection_reason: str | None = Field(default=None, max_length=300)
    batch: str | None = Field(default=None, max_length=80)
    expiry: date | None = None
    serials: str | None = None
    manufacturer: str | None = Field(default=None, max_length=160)

    @field_validator("quantity_received", "quantity_rejected", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class ReceiptContract(Contract):
    purchase_order_id: uuid.UUID
    lines: list[ReceiptLineContract] = Field(min_length=1)
    received_on: date | None = None
    warehouse_id: uuid.UUID | None = None
    delivery_reference: str | None = Field(default=None, max_length=120)
    inspection_pending: bool = False
    notes: str | None = None


class ReceiptLineReply(Reply):
    id: uuid.UUID
    position: int
    purchase_order_line_fk: uuid.UUID
    quantity_received: Decimal
    quantity_accepted: Decimal
    quantity_rejected: Decimal
    rejection_reason: str | None
    unit_cost: Decimal
    batch: str | None
    expiry: date | None
    serials: str | None
    manufacturer: str | None


class ReceiptReply(Reply):
    id: uuid.UUID
    number: str | None
    purchase_order_fk: uuid.UUID
    purchase_order_number: str | None = None
    supplier_fk: uuid.UUID
    supplier_name: str | None = None
    warehouse_fk: uuid.UUID | None
    state: str
    received_on: date
    delivery_reference: str | None
    inspection_pending: bool
    accepted_value: Decimal
    journal_entry_fk: uuid.UUID | None
    notes: str | None
    lines: list[ReceiptLineReply]


# --------------------------------------------------------------------------------------
# Supplier billing
# --------------------------------------------------------------------------------------


class InvoiceLineContract(Contract):
    purchase_order_line_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal
    recoverable_tax: Decimal = Decimal("0")
    non_recoverable_tax: Decimal = Decimal("0")
    description: str | None = Field(default=None, max_length=300)

    @field_validator(
        "quantity", "unit_cost", "recoverable_tax", "non_recoverable_tax", mode="before"
    )
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class InvoiceContract(Contract):
    purchase_order_id: uuid.UUID
    supplier_reference: str = Field(min_length=1, max_length=120)
    invoiced_on: date
    lines: list[InvoiceLineContract] = Field(min_length=1)
    due_on: date | None = None
    evidence_attachment_id: uuid.UUID | None = None
    notes: str | None = None


class VarianceDecisionContract(Contract):
    approve: bool
    reason: str = Field(min_length=1, max_length=300)


class ToleranceContract(Contract):
    absolute: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")

    @field_validator("absolute", "rate", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class ToleranceReply(Reply):
    absolute: Decimal
    rate: Decimal


class CreditNoteContract(Contract):
    invoice_id: uuid.UUID
    supplier_reference: str = Field(min_length=1, max_length=120)
    amount: Decimal
    reason: str = Field(min_length=1, max_length=300)
    credited_on: date | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class InvoiceLineReply(Reply):
    id: uuid.UUID
    position: int
    purchase_order_line_fk: uuid.UUID
    description: str
    quantity: Decimal
    unit_cost: Decimal
    line_total: Decimal
    recoverable_tax: Decimal
    non_recoverable_tax: Decimal


class VarianceReply(Reply):
    id: uuid.UUID
    invoice_fk: uuid.UUID
    invoice_line_fk: uuid.UUID
    kind: str
    expected: Decimal
    actual: Decimal
    amount_at_stake: Decimal
    state: str
    decided_at: datetime | None
    reason: str | None


class SupplierInvoiceReply(Reply):
    id: uuid.UUID
    number: str | None
    supplier_reference: str
    supplier_fk: uuid.UUID
    supplier_name: str | None = None
    purchase_order_fk: uuid.UUID
    purchase_order_number: str | None = None
    state: str
    invoiced_on: date
    due_on: date
    currency_code: str
    exchange_rate: Decimal
    net_total: Decimal
    recoverable_tax_total: Decimal
    non_recoverable_tax_total: Decimal
    total: Decimal
    base_total: Decimal
    amount_paid: Decimal
    amount_credited: Decimal
    evidence_attachment_fk: uuid.UUID | None
    journal_entry_fk: uuid.UUID | None
    notes: str | None
    posted_at: datetime | None
    lines: list[InvoiceLineReply]
    variances: list[VarianceReply]


class CreditNoteReply(Reply):
    id: uuid.UUID
    supplier_reference: str
    supplier_fk: uuid.UUID
    invoice_fk: uuid.UUID
    credited_on: date
    reason: str
    amount: Decimal
    base_amount: Decimal
    journal_entry_fk: uuid.UUID | None


# --------------------------------------------------------------------------------------
# Payables
# --------------------------------------------------------------------------------------


class PaymentContract(Contract):
    supplier_id: uuid.UUID
    amount: Decimal
    method: str = "bank"
    withheld: Decimal = Decimal("0")
    paid_on: date | None = None
    reference: str | None = Field(default=None, max_length=120)
    evidence_attachment_id: uuid.UUID | None = None
    invoice_ids: list[uuid.UUID] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("amount", "withheld", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _refuse_float(value)


class AllocationReply(Reply):
    invoice_fk: uuid.UUID
    amount: Decimal


class PaymentReply(Reply):
    id: uuid.UUID
    number: str | None
    supplier_fk: uuid.UUID
    supplier_name: str | None = None
    paid_on: date
    method: str
    reference: str | None
    amount: Decimal
    withheld: Decimal
    evidence_attachment_fk: uuid.UUID | None
    journal_entry_fk: uuid.UUID | None
    notes: str | None
    allocations: list[AllocationReply]


class PayableRowReply(Reply):
    invoice_id: uuid.UUID
    number: str | None
    supplier_id: uuid.UUID
    supplier_name: str
    supplier_reference: str
    invoiced_on: date
    due_on: date
    total: Decimal
    paid: Decimal
    credited: Decimal
    outstanding: Decimal
    bucket: str
    days_overdue: int


class PayablesRegisterReply(Reply):
    as_at: date
    rows: list[PayableRowReply]
    total_outstanding: Decimal
    by_bucket: dict[str, Decimal]
    by_supplier: dict[str, Decimal]
    suggested_amounts: list[dict[str, Any]] = Field(default_factory=list)
    """For the payment screen: the exact total outstanding and the next round notes."""
