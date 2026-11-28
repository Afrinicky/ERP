"""Request and response contracts for sourcing, quotations and commission.

Amounts and rates cross the boundary as strings, so no figure passes through a JSON
float, per PRD §9.5. The split between ``QuotationReply`` and ``CostingReply`` is the
confidentiality boundary of PRD §14 made structural: the first carries nothing the
customer may not see, and an interface that only ever fetched the first could not
disclose a margin if it tried.
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
        raise ValueError("Send an amount or a rate as a string, not a JSON number.")
    return value


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


class MarginPolicyReply(Reply):
    id: uuid.UUID
    scope: str
    category_fk: uuid.UUID | None
    product_fk: uuid.UUID | None
    method: str
    rate: Decimal
    is_active: bool
    notes: str | None
    target_name: str | None = None
    """The product or category the policy governs, by name.

    The build-up tells the reader a rate was "set on the product"; this screen has to
    be able to say which one, or the two cannot be reconciled."""


class SetMarginPolicyContract(Contract):
    scope: str
    method: str
    rate: Decimal
    category_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    notes: str | None = None

    _no_floats = field_validator("rate", mode="before")(_refuse_float)


class DiscountLimitReply(Reply):
    id: uuid.UUID
    role_code: str
    maximum_rate: Decimal
    maximum_amount: Decimal | None


class SetDiscountLimitContract(Contract):
    role_code: str
    maximum_rate: Decimal
    maximum_amount: Decimal | None = None

    _no_floats = field_validator("maximum_rate", "maximum_amount", mode="before")(_refuse_float)


class InstalmentBandReply(Reply):
    id: uuid.UUID
    label: str
    minimum_months: int
    maximum_months: int
    method: str
    rate: Decimal
    fixed_per_unit: Decimal | None


class InstalmentPlanReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    applies_to: str
    frequency: str
    deposit_rate: Decimal
    is_active: bool
    bands: list[InstalmentBandReply] = Field(default_factory=list)


class BandContract(Contract):
    minimum_months: int
    maximum_months: int
    method: str = "percentage"
    rate: Decimal = Decimal("0")
    fixed_per_unit: Decimal = Decimal("0")
    label: str = ""

    _no_floats = field_validator("rate", "fixed_per_unit", mode="before")(_refuse_float)


class CreatePlanContract(Contract):
    code: str
    name: str
    frequency: str = "monthly"
    deposit_rate: Decimal = Decimal("0")
    applies_to: str = "business"
    category_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    description: str | None = None

    _no_floats = field_validator("deposit_rate", mode="before")(_refuse_float)


class SetBandsContract(Contract):
    bands: list[BandContract]


# --------------------------------------------------------------------------------------
# The customer's request
# --------------------------------------------------------------------------------------


class RequestLineReply(Reply):
    id: uuid.UUID
    position: int
    product_fk: uuid.UUID | None
    description: str
    specification: str | None
    quantity: Decimal
    unit_fk: uuid.UUID | None
    required_by: date | None
    notes: str | None


class RequestReply(Reply):
    id: uuid.UUID
    number: str
    customer_fk: uuid.UUID
    contact_fk: uuid.UUID | None
    customer_reference: str | None
    received_on: date
    required_by: date | None
    delivery_address: str | None
    currency_code: str
    state: str
    cancellation_reason: str | None
    notes: str | None
    lines: list[RequestLineReply] = Field(default_factory=list)
    customer_name: str | None = None


class RequestPageReply(Reply):
    items: list[RequestReply]
    total: int
    offset: int
    limit: int


class RequestLineContract(Contract):
    description: str
    quantity: Decimal
    product_id: uuid.UUID | None = None
    specification: str | None = None
    unit_id: uuid.UUID | None = None
    required_by: date | None = None
    notes: str | None = None

    _no_floats = field_validator("quantity", mode="before")(_refuse_float)


class CreateRequestContract(Contract):
    customer_id: uuid.UUID
    received_on: date | None = None
    contact_id: uuid.UUID | None = None
    customer_reference: str | None = None
    required_by: date | None = None
    delivery_address: str | None = None
    currency_code: str = "GHS"
    notes: str | None = None
    lines: list[RequestLineContract]


class RequestStateContract(Contract):
    state: str
    reason: str | None = None


# --------------------------------------------------------------------------------------
# Supplier sourcing
# --------------------------------------------------------------------------------------


class SupplierRequestLineReply(Reply):
    id: uuid.UUID
    position: int
    request_line_fk: uuid.UUID | None
    product_fk: uuid.UUID | None
    description: str
    quantity: Decimal


class SupplierRequestReply(Reply):
    id: uuid.UUID
    number: str
    supplier_fk: uuid.UUID
    request_fk: uuid.UUID | None
    issued_on: date | None
    respond_by: date | None
    currency_code: str
    state: str
    notes: str | None
    lines: list[SupplierRequestLineReply] = Field(default_factory=list)
    supplier_name: str | None = None


class CreateSupplierRequestContract(Contract):
    supplier_id: uuid.UUID
    request_id: uuid.UUID | None = None
    request_line_ids: list[uuid.UUID] | None = None
    respond_by: date | None = None
    currency_code: str = "GHS"
    notes: str | None = None


class SupplierQuotationLineContract(Contract):
    description: str
    quantity_offered: Decimal
    unit_cost: Decimal
    request_line_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    discount_rate: Decimal = Decimal("0")
    recoverable_tax_rate: Decimal = Decimal("0")
    non_recoverable_tax_rate: Decimal = Decimal("0")
    lead_time_days: int | None = None
    minimum_order_quantity: Decimal | None = None
    is_available: bool = True
    availability_note: str | None = None
    notes: str | None = None

    _no_floats = field_validator(
        "quantity_offered",
        "unit_cost",
        "discount_rate",
        "recoverable_tax_rate",
        "non_recoverable_tax_rate",
        "minimum_order_quantity",
        mode="before",
    )(_refuse_float)


class RecordSupplierQuotationContract(Contract):
    supplier_id: uuid.UUID
    reference: str
    received_on: date | None = None
    supplier_request_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    valid_until: date | None = None
    currency_code: str = "GHS"
    exchange_rate: Decimal = Decimal("1")
    lead_time_days: int | None = None
    freight_amount: Decimal = Decimal("0")
    other_charges: Decimal = Decimal("0")
    payment_terms: str | None = None
    tax_treatment: str = "exclusive"
    notes: str | None = None
    lines: list[SupplierQuotationLineContract]

    _no_floats = field_validator("exchange_rate", "freight_amount", "other_charges", mode="before")(
        _refuse_float
    )


class SupplierQuotationLineReply(Reply):
    id: uuid.UUID
    position: int
    request_line_fk: uuid.UUID | None
    product_fk: uuid.UUID | None
    description: str
    quantity_offered: Decimal
    unit_cost: Decimal
    discount_rate: Decimal
    lead_time_days: int | None
    is_available: bool
    availability_note: str | None


class SupplierQuotationReply(Reply):
    id: uuid.UUID
    reference: str
    supplier_fk: uuid.UUID
    request_fk: uuid.UUID | None
    received_on: date
    valid_until: date | None
    currency_code: str
    exchange_rate: Decimal
    lead_time_days: int | None
    freight_amount: Decimal
    other_charges: Decimal
    payment_terms: str | None
    state: str
    lines: list[SupplierQuotationLineReply] = Field(default_factory=list)
    supplier_name: str | None = None


class OfferReply(Reply):
    supplier_quotation_id: uuid.UUID
    supplier_quotation_line_id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    reference: str
    currency_code: str
    exchange_rate: Decimal
    unit_cost: Decimal
    discount_rate: Decimal
    net_unit_cost: Decimal
    base_unit_cost: Decimal
    charges_per_unit: Decimal
    comparable_unit_cost: Decimal
    quantity_offered: Decimal
    lead_time_days: int | None
    is_available: bool
    availability_note: str | None
    valid_until: date | None
    payment_terms: str | None
    meets_quantity: bool
    minimum_order_quantity: Decimal | None


class ComparisonReply(Reply):
    request_line_id: uuid.UUID
    description: str
    quantity: Decimal
    offers: list[OfferReply]
    best_offer_id: uuid.UUID | None = None


# --------------------------------------------------------------------------------------
# Quotations
# --------------------------------------------------------------------------------------


class SourceContract(Contract):
    kind: str
    quantity: Decimal
    unit_cost: Decimal | None = None
    supplier_id: uuid.UUID | None = None
    supplier_quotation_line_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    currency_code: str | None = None
    exchange_rate: Decimal | None = None
    lead_time_days: int | None = None
    selection_reason: str | None = None

    _no_floats = field_validator("quantity", "unit_cost", "exchange_rate", mode="before")(
        _refuse_float
    )


class QuotationLineContract(Contract):
    description: str
    quantity: Decimal
    product_id: uuid.UUID | None = None
    request_line_id: uuid.UUID | None = None
    specification: str | None = None
    unit_id: uuid.UUID | None = None
    sources: list[SourceContract] = Field(default_factory=list)
    unit_cost: Decimal | None = None
    supplier_discount_rate: Decimal = Decimal("0")
    supplier_discount_amount: Decimal = Decimal("0")
    non_recoverable_tax: Decimal = Decimal("0")
    recoverable_tax: Decimal = Decimal("0")
    line_weight: Decimal = Decimal("0")
    line_volume: Decimal = Decimal("0")
    margin_method: str | None = None
    margin_rate: Decimal | None = None
    commission_selected: bool = False
    discount_method: str = "percentage"
    discount_value: Decimal = Decimal("0")

    _no_floats = field_validator(
        "quantity",
        "unit_cost",
        "supplier_discount_rate",
        "supplier_discount_amount",
        "non_recoverable_tax",
        "recoverable_tax",
        "line_weight",
        "line_volume",
        "margin_rate",
        "discount_value",
        mode="before",
    )(_refuse_float)


class ExpenseContract(Contract):
    category: str
    amount: Decimal
    reference: str | None = None
    description: str | None = None
    method: str = "by_value"
    nominated_line_position: int | None = None
    excluded_line_positions: list[int] = Field(default_factory=list)
    manual_amounts: dict[int, Decimal] = Field(default_factory=dict)
    supplier_id: uuid.UUID | None = None
    is_disclosed: bool = False
    currency_code: str | None = None

    _no_floats = field_validator("amount", mode="before")(_refuse_float)


class CreateQuotationContract(Contract):
    customer_id: uuid.UUID
    request_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    customer_reference: str | None = None
    quoted_on: date | None = None
    valid_until: date | None = None
    currency_code: str = "GHS"
    exchange_rate: Decimal = Decimal("1")
    precision: int = 4
    document_discount_method: str = "percentage"
    document_discount_value: Decimal = Decimal("0")
    affiliate_id: uuid.UUID | None = None
    commission_scope: str | None = None
    payment_plan: str = "outright"
    instalment_plan_id: uuid.UUID | None = None
    instalment_months: int | None = None
    instalment_frequency: str | None = None
    deposit_rate: Decimal | None = None
    first_instalment_on: date | None = None
    terms: str | None = None
    notes: str | None = None
    internal_notes: str | None = None
    lines: list[QuotationLineContract] = Field(default_factory=list)
    expenses: list[ExpenseContract] = Field(default_factory=list)

    _no_floats = field_validator(
        "exchange_rate", "document_discount_value", "deposit_rate", mode="before"
    )(_refuse_float)


class QuotationLineReply(Reply):
    """A line as the customer may see it. Nothing here is confidential."""

    id: uuid.UUID
    position: int
    product_fk: uuid.UUID | None
    description: str
    specification: str | None
    quantity: Decimal
    unit_net_price: Decimal
    list_price: Decimal
    discount_amount: Decimal
    net_price: Decimal
    finance_charge: Decimal
    payable_before_tax: Decimal
    tax_amount: Decimal
    total_payable: Decimal


class QuotationReply(Reply):
    id: uuid.UUID
    number: str
    revision: int
    supersedes_fk: uuid.UUID | None
    request_fk: uuid.UUID | None
    customer_fk: uuid.UUID
    customer_reference: str | None
    state: str
    quoted_on: date
    valid_until: date | None
    currency_code: str
    precision: int
    payment_plan: str
    instalment_months: int | None
    instalment_frequency: str | None
    deposit_rate: Decimal
    requires_approval: bool
    approval_reason: str | None
    approved_at: datetime | None
    issued_at: datetime | None
    accepted_on: date | None
    decided_on: date | None
    decision_reason: str | None
    discount_total: Decimal
    net_total: Decimal
    finance_charge_total: Decimal
    payable_before_tax: Decimal
    tax_total: Decimal
    total_payable: Decimal
    terms: str | None
    notes: str | None
    lines: list[QuotationLineReply] = Field(default_factory=list)
    customer_name: str | None = None


class QuotationPageReply(Reply):
    items: list[QuotationReply]
    total: int
    offset: int
    limit: int


class BuildUpStepReply(Reply):
    step: str
    label: str
    amount: Decimal


class SourceReply(Reply):
    id: uuid.UUID
    position: int
    kind: str
    supplier_fk: uuid.UUID | None
    warehouse_fk: uuid.UUID | None
    quantity: Decimal
    unit_cost: Decimal
    currency_code: str
    exchange_rate: Decimal
    lead_time_days: int | None
    selection_reason: str | None
    supplier_name: str | None = None


class AllocationReply(Reply):
    line_position: int
    expense_reference: str
    category: str
    method: str
    amount: Decimal


class LineCostingReply(Reply):
    """The internal view of one line. Requires ``costing.view``."""

    position: int
    description: str
    quantity: Decimal
    supplier_cost: Decimal
    supplier_discount: Decimal
    allocated_cost: Decimal
    landed_cost: Decimal
    margin_method: str
    margin_rate: Decimal
    margin_source: str
    margin_amount: Decimal
    commission: Decimal
    commission_rate: Decimal
    commission_in_scope: bool
    commission_reason: str
    list_price: Decimal
    discount: Decimal
    net_price: Decimal
    finance_charge: Decimal
    payable_before_tax: Decimal
    contribution_before_commission: Decimal
    contribution_after_commission: Decimal
    realised_margin_rate: Decimal
    estimated_cost: Decimal
    actual_cost: Decimal
    variance: Decimal
    variance_rate: Decimal
    steps: list[BuildUpStepReply] = Field(default_factory=list)
    sources: list[SourceReply] = Field(default_factory=list)


class CostingReply(Reply):
    quotation_id: uuid.UUID
    number: str
    state: str
    currency_code: str
    landed_cost_total: Decimal
    margin_total: Decimal
    commission_total: Decimal
    list_total: Decimal
    discount_total: Decimal
    net_total: Decimal
    finance_charge_total: Decimal
    payable_before_tax: Decimal
    tax_total: Decimal
    total_payable: Decimal
    contribution_before_commission: Decimal
    contribution_after_commission: Decimal
    requires_approval: bool
    approval_reason: str | None
    tax_by_rule: dict[str, Decimal] = Field(default_factory=dict)
    tax_review: list[str] = Field(default_factory=list)
    lines: list[LineCostingReply] = Field(default_factory=list)
    allocations: list[AllocationReply] = Field(default_factory=list)


class InstalmentReply(Reply):
    id: uuid.UUID
    sequence: int
    due_on: date
    amount: Decimal
    state: str
    paid_on: date | None
    paid_amount: Decimal
    is_deposit: bool


class ScheduleReply(Reply):
    """Everything PRD §6.5 requires an instalment quotation to disclose."""

    deposit: Decimal
    instalment_amount: Decimal
    frequency: str | None
    duration_months: int | None
    first_due_on: date | None
    final_due_on: date | None
    finance_charge: Decimal
    total_payable: Decimal
    instalments: list[InstalmentReply] = Field(default_factory=list)


class SetSourcesContract(Contract):
    sources: list[SourceContract]


class SetExpensesContract(Contract):
    expenses: list[ExpenseContract]


class SetPaymentPlanContract(Contract):
    plan: str
    instalment_plan_id: uuid.UUID | None = None
    months: int | None = None
    frequency: str | None = None
    deposit_rate: Decimal | None = None
    first_instalment_on: date | None = None

    _no_floats = field_validator("deposit_rate", mode="before")(_refuse_float)


class DecideContract(Contract):
    state: str
    on: date | None = None
    reason: str | None = None


class IssueContract(Contract):
    valid_until: date | None = None


class ReviseContract(Contract):
    reason: str


class RescheduleContract(Contract):
    months: int
    frequency: str | None = None
    first_instalment_on: date | None = None
    reason: str


class ActualCostContract(Contract):
    actual_unit_cost: Decimal
    reference: str | None = None
    on: date | None = None

    _no_floats = field_validator("actual_unit_cost", mode="before")(_refuse_float)


class SnapshotReply(Reply):
    id: uuid.UUID
    revision: int
    taken_at: datetime
    content_hash: str
    payload: dict[str, Any]


class SalesSummaryReply(Reply):
    counts: dict[str, int]
    issued_value: Decimal
    accepted_value: Decimal
    open_requests: int
    awaiting_approval: int
    acceptance_rate: Decimal


# --------------------------------------------------------------------------------------
# Commission
# --------------------------------------------------------------------------------------


class AccrualReply(Reply):
    id: uuid.UUID
    affiliate_fk: uuid.UUID
    quotation_fk: uuid.UUID | None
    quotation_line_fk: uuid.UUID | None
    source_reference: str | None
    state: str
    accrued_on: date
    basis_amount: Decimal
    method: str
    rate: Decimal
    accrued_amount: Decimal
    fulfilled_fraction: Decimal
    paid_fraction: Decimal
    entitlement: Decimal
    settled_amount: Decimal
    prior_rate: Decimal | None
    override_reason: str | None
    cancellation_reason: str | None
    payment_reference: str | None
    affiliate_name: str | None = None
    quotation_number: str | None = None


class CommissionTotalsReply(Reply):
    accrued: Decimal
    approved: Decimal
    payable: Decimal
    cancelled: Decimal
    settled: Decimal
    outstanding: Decimal


class AccrueContract(Contract):
    quotation_id: uuid.UUID
    source_reference: str
    fulfilled_fraction: Decimal = Decimal("1")
    on: date | None = None

    _no_floats = field_validator("fulfilled_fraction", mode="before")(_refuse_float)


class CollectionContract(Contract):
    paid_fraction: Decimal

    _no_floats = field_validator("paid_fraction", mode="before")(_refuse_float)


class PayCommissionContract(Contract):
    amount: Decimal
    reference: str | None = None

    _no_floats = field_validator("amount", mode="before")(_refuse_float)


class CancelCommissionContract(Contract):
    reason: str


class OverrideCommissionContract(Contract):
    rate: Decimal
    reason: str

    _no_floats = field_validator("rate", mode="before")(_refuse_float)
