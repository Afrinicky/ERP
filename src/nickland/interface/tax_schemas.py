"""Request and response contracts for taxation.

Rates and amounts cross the boundary as strings, so no figure passes through a JSON
float, per PRD §9.5.
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


class AuthorityReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    jurisdiction: str | None
    registration_identifier: str | None
    is_active: bool
    display_order: int


class RuleVersionReply(Reply):
    id: uuid.UUID
    effective_from: date
    effective_to: date | None
    method: str
    rate: Decimal
    fixed_amount: Decimal
    threshold: Decimal
    threshold_applies_to_excess: bool
    bands: list[dict[str, Any]] | None
    note: str | None


class ConditionReply(Reply):
    field: str
    operator: str
    value: str | None
    values: list[str] | None


class RuleReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    category: str
    treatment: str
    authority_fk: uuid.UUID | None
    jurisdiction: str | None
    is_mandatory: bool
    applies_to_sales: bool
    applies_to_purchases: bool
    cascade_order: int
    compounds_on_preceding: bool
    liability_account_code: str | None
    recoverable_account_code: str | None
    expense_account_code: str | None
    filing_frequency: str
    due_date_rule: str
    due_days: int
    due_day_of_month: int
    is_active: bool
    is_system: bool
    withdrawal_reason: str | None
    versions: list[RuleVersionReply] = Field(default_factory=list)
    conditions: list[ConditionReply] = Field(default_factory=list)
    current_rate: Decimal | None = None
    """The rate in force today, so a list need not be read version by version."""


class ConditionContract(Contract):
    field: str = Field(max_length=32)
    operator: str = Field(max_length=16)
    value: str | None = Field(default=None, max_length=200)
    values: list[str] = Field(default_factory=list, max_length=50)


class CreateRuleContract(Contract):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(max_length=24)
    treatment: str = Field(default="exclusive", max_length=20)
    method: str = Field(default="percentage", max_length=20)
    rate: Decimal | None = None
    effective_from: date | None = None
    authority_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2000)
    jurisdiction: str | None = Field(default=None, max_length=80)
    is_mandatory: bool = True
    applies_to_sales: bool = True
    applies_to_purchases: bool = False
    cascade_order: int = Field(default=100, ge=0, le=9999)
    compounds_on_preceding: bool = False
    liability_account_code: str | None = Field(default=None, max_length=8)
    recoverable_account_code: str | None = Field(default=None, max_length=8)
    expense_account_code: str | None = Field(default=None, max_length=8)
    filing_frequency: str = Field(default="monthly", max_length=20)
    due_date_rule: str = Field(default="day_of_next_month", max_length=24)
    due_days: int = Field(default=0, ge=0, le=365)
    due_day_of_month: int = Field(default=15, ge=1, le=28)
    fixed_amount: Decimal | None = None
    threshold: Decimal | None = None
    threshold_applies_to_excess: bool = True
    bands: list[dict[str, Any]] | None = None

    @field_validator("rate", "fixed_amount", "threshold", mode="before")
    @classmethod
    def _no_floats(cls, value: Any) -> Any:
        return _refuse_float(value)


class SetRateContract(Contract):
    rate: Decimal
    effective_from: date
    note: str | None = Field(default=None, max_length=500)
    method: str | None = Field(default=None, max_length=20)
    fixed_amount: Decimal | None = None
    threshold: Decimal | None = None
    threshold_applies_to_excess: bool = True
    bands: list[dict[str, Any]] | None = None

    @field_validator("rate", "fixed_amount", "threshold", mode="before")
    @classmethod
    def _no_floats(cls, value: Any) -> Any:
        return _refuse_float(value)


class SetConditionsContract(Contract):
    conditions: list[ConditionContract] = Field(default_factory=list, max_length=6)


class WithdrawRuleContract(Contract):
    reason: str = Field(min_length=1, max_length=2000)


class TaxableLineContract(Contract):
    reference: str = Field(min_length=1, max_length=80)
    net: Decimal
    quantity: Decimal = Decimal("1")
    product_tax_code: str | None = Field(default=None, max_length=40)
    product_category: str | None = Field(default=None, max_length=120)
    item_class: str | None = Field(default=None, max_length=24)
    product_id: uuid.UUID | None = None

    @field_validator("net", "quantity", mode="before")
    @classmethod
    def _no_floats(cls, value: Any) -> Any:
        return _refuse_float(value)


class EvaluateContract(Contract):
    transaction_kind: str = Field(max_length=32)
    on: date
    direction: str = Field(default="sales", pattern="^(sales|purchases)$")
    lines: list[TaxableLineContract] = Field(min_length=1, max_length=500)
    party_id: uuid.UUID | None = None
    party_tax_treatment: str | None = Field(default=None, max_length=24)
    party_is_withholding_agent: bool = False
    branch_code: str | None = Field(default=None, max_length=24)
    jurisdiction: str | None = Field(default=None, max_length=80)
    is_import: bool = False
    currency_code: str = Field(default="GHS", min_length=3, max_length=3)
    exemption_evidence: str | None = Field(default=None, max_length=200)


class AssessmentReply(Reply):
    rule_code: str
    rule_name: str
    category: str
    outcome: str
    treatment: str
    method: str
    base: Decimal
    rate: Decimal
    amount: Decimal
    detail: str
    version_effective_from: date | None


class LineAssessmentReply(Reply):
    line_reference: str
    net: Decimal
    charged: Decimal
    withheld: Decimal
    assessments: list[AssessmentReply]


class EvaluationReply(Reply):
    lines: list[LineAssessmentReply]
    charged: Decimal
    withheld: Decimal
    needs_review: int
    by_rule: dict[str, Decimal]


class RecordedAssessmentReply(Reply):
    id: uuid.UUID
    rule_code: str
    rule_name: str
    category: str
    outcome: str
    treatment: str
    source_type: str
    source_id: uuid.UUID
    source_line_reference: str
    assessed_on: date
    base: Decimal
    rate: Decimal
    amount: Decimal
    currency_code: str
    is_recoverable: bool
    is_withheld: bool
    detail: str


class OverrideContract(Contract):
    amount: Decimal
    reason: str = Field(min_length=1, max_length=2000)
    evidence_reference: str | None = Field(default=None, max_length=200)

    @field_validator("amount", mode="before")
    @classmethod
    def _no_floats(cls, value: Any) -> Any:
        return _refuse_float(value)


class ObligationReply(Reply):
    id: uuid.UUID
    rule_fk: uuid.UUID
    authority_fk: uuid.UUID | None
    period_starts_on: date
    period_ends_on: date
    due_on: date
    label: str
    assessed: Decimal
    recoverable: Decimal
    withheld: Decimal
    adjustments: Decimal
    paid: Decimal
    carried_forward: Decimal
    state: str
    currency_code: str
    net_payable: Decimal = Decimal("0")


class AllocationContract(Contract):
    obligation_id: uuid.UUID
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def _no_floats(cls, value: Any) -> Any:
        return _refuse_float(value)


class RecordPaymentContract(Contract):
    reference: str = Field(min_length=1, max_length=60)
    paid_on: date
    amount: Decimal
    authority_id: uuid.UUID | None = None
    method: str | None = Field(default=None, max_length=32)
    bank_reference: str | None = Field(default=None, max_length=80)
    evidence_reference: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    allocations: list[AllocationContract] = Field(default_factory=list, max_length=100)

    @field_validator("amount", mode="before")
    @classmethod
    def _no_floats(cls, value: Any) -> Any:
        return _refuse_float(value)


class PaymentAllocationReply(Reply):
    obligation_fk: uuid.UUID
    amount: Decimal
    allocated_at: datetime


class PaymentReply(Reply):
    id: uuid.UUID
    reference: str
    paid_on: date
    amount: Decimal
    allocated: Decimal
    authority_fk: uuid.UUID | None
    method: str | None
    bank_reference: str | None
    evidence_reference: str | None
    currency_code: str
    allocations: list[PaymentAllocationReply] = Field(default_factory=list)


class FilingReply(Reply):
    id: uuid.UUID
    obligation_fk: uuid.UUID
    authority_fk: uuid.UUID | None
    reference: str | None
    state: str
    due_on: date
    submitted_at: datetime | None
    acknowledgement_reference: str | None
    declared_amount: Decimal


class SubmitFilingContract(Contract):
    reference: str = Field(min_length=1, max_length=80)
    acknowledgement: str | None = Field(default=None, max_length=120)


class TaxSummaryReply(Reply):
    """The figures the taxation overview leads with."""

    as_at: date
    outstanding: Decimal
    overdue: Decimal
    recoverable: Decimal
    withheld: Decimal
    paid_this_year: Decimal
    obligations_outstanding: int
    obligations_overdue: int
    filings_due: int
    rules_active: int
