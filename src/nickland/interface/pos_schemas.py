"""Request and response contracts for the register.

Money and quantities cross the boundary as strings, never as JSON numbers, per PRD §9.5.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from nickland.interface.schemas import Contract, Reply


class _Figures(Contract):
    @field_validator("*", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("Send a figure as a string, not a JSON number.")
        return value


# -- requests ---------------------------------------------------------------------------


class OpenShiftContract(_Figures):
    opening_float: Decimal = Field(ge=0)
    register_name: str = Field(default="Register 1", max_length=60)


class CashMovementContract(_Figures):
    kind: str = Field(pattern="^(cash_in|cash_out)$")
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=300)
    counter: str = Field(default="bank", pattern="^(bank|petty_cash)$")


class CountContract(Contract):
    denominations: dict[str, int]
    variance_reason: str | None = Field(default=None, max_length=500)


class ScanContract(_Figures):
    code: str = Field(min_length=1, max_length=64)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)


class AddLineContract(_Figures):
    product_id: uuid.UUID
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    serials: list[str] = Field(default_factory=list)


class QuantityContract(_Figures):
    quantity: Decimal = Field(ge=0)


class OverrideContract(_Figures):
    unit_price: Decimal = Field(ge=0)
    reason: str = Field(min_length=1, max_length=300)


class DiscountContract(_Figures):
    rate: Decimal = Field(ge=0, lt=1)


class CustomerContract(Contract):
    customer_id: uuid.UUID | None = None


class ParkContract(Contract):
    label: str | None = Field(default=None, max_length=80)


class ReasonContract(Contract):
    reason: str = Field(min_length=1, max_length=500)


class TenderContract(_Figures):
    method: str = Field(pattern="^(cash|mobile_money|card|bank_transfer|on_account)$")
    amount: Decimal | None = Field(default=None, gt=0)
    cash_received: Decimal | None = Field(default=None, gt=0)
    reference: str | None = Field(default=None, max_length=120)


class ConfirmTenderContract(Contract):
    provider_reference: str = Field(min_length=1, max_length=120)


class Supervisor(Contract):
    """Someone holding ``pos.refund``, authorising at the cashier's register."""

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class RefundLineContract(_Figures):
    sale_line_id: uuid.UUID
    quantity: Decimal = Field(gt=0)


class RefundContract(Contract):
    lines: list[RefundLineContract] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    method: str | None = Field(
        default=None, pattern="^(cash|mobile_money|card|bank_transfer|on_account)$"
    )
    restock: bool = True
    supervisor: Supervisor | None = None


class VoidContract(Contract):
    reason: str = Field(min_length=1, max_length=500)
    supervisor: Supervisor | None = None


# -- replies ----------------------------------------------------------------------------


class SuggestedAmountReply(Reply):
    amount: Decimal
    exact: bool


class CountPreviewReply(Reply):
    counted: Decimal
    expected: Decimal
    variance: Decimal


class SaleLineReply(Reply):
    id: uuid.UUID
    position: int
    product_id: uuid.UUID
    sku: str
    description: str
    quantity: Decimal
    list_price: Decimal
    price_source: str | None
    unit_price: Decimal
    override_reason: str | None
    discount_rate: Decimal
    discount: Decimal
    net: Decimal
    tax: Decimal
    total: Decimal
    returnable: Decimal


class TenderReply(Reply):
    id: uuid.UUID
    method: str
    method_label: str
    state: str
    state_label: str
    amount: Decimal
    cash_received: Decimal | None
    change_given: Decimal | None
    provider_reference: str | None
    failure_reason: str | None


class SaleReply(Reply):
    id: uuid.UUID
    number: str | None
    state: str
    customer_id: uuid.UUID | None
    customer: str | None
    parked_label: str | None
    lines: list[SaleLineReply]
    tenders: list[TenderReply]
    gross_total: Decimal
    discount_total: Decimal
    net_total: Decimal
    tax_total: Decimal
    total: Decimal
    due: Decimal
    change: Decimal
    suggested_cash: list[SuggestedAmountReply]
    refunded_total: Decimal
    awaiting_confirmation: bool
    completed_at: datetime | None
    cashier: str | None
    receipt_id: uuid.UUID | None
    void_reason: str | None


class SaleSummaryReply(Reply):
    id: uuid.UUID
    number: str | None
    state: str
    customer: str | None
    parked_label: str | None
    total: Decimal
    refunded_total: Decimal
    items: int
    completed_at: datetime | None
    methods: list[str]


class DrawerReply(Reply):
    opening_float: Decimal
    cash_sales: Decimal
    cash_in: Decimal
    cash_out: Decimal
    cash_refunds: Decimal
    expected: Decimal


class ShiftReply(Reply):
    id: uuid.UUID
    number: str
    register_name: str
    cashier: str | None
    state: str
    business_day: date
    opened_at: datetime
    opening_float: Decimal
    counted_cash: Decimal | None
    expected_cash: Decimal | None
    variance: Decimal | None
    variance_reason: str | None
    submitted_at: datetime | None
    closed_at: datetime | None
    approved_by: str | None
    counted_denominations: dict[str, int] | None


class MethodTotalReply(Reply):
    method: str
    label: str
    amount: Decimal


class ShiftSummaryReply(Reply):
    shift: ShiftReply
    drawer: DrawerReply
    sales: int
    pending_tenders: int
    by_method: list[MethodTotalReply]
    denominations: list[Decimal]


class CashMovementReply(Reply):
    id: uuid.UUID
    kind: str
    amount: Decimal
    reason: str


class RefundLineReply(Reply):
    sale_line_id: uuid.UUID
    quantity: Decimal
    net: Decimal
    tax: Decimal


class RefundReply(Reply):
    id: uuid.UUID
    number: str
    kind: str
    sale_id: uuid.UUID
    reason: str
    method: str
    restock: bool
    net_total: Decimal
    tax_total: Decimal
    total: Decimal
    requested_by: str | None
    authorised_by: str | None
    refunded_on: date
    lines: list[RefundLineReply]
