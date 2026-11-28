"""Request and response contracts for debt, loans and financing. PRD §25.

Money crosses the boundary as strings, never as JSON numbers, per PRD §9.5.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field

from nickland.interface.finance_schemas import _Figures
from nickland.interface.schemas import Contract, Reply

_FREQUENCY = "^(daily|weekly|fortnightly|monthly|quarterly|semi_annual|annual)$"
_MODEL = "^(none|fixed|flat|reducing_balance|interest_only|balloon|custom)$"


class CustomRowContract(_Figures):
    due_on: date
    principal: Decimal = Field(ge=0)
    interest: Decimal = Field(default=Decimal("0"), ge=0)
    fees: Decimal = Field(default=Decimal("0"), ge=0)


class LoanContract(_Figures):
    direction: str = Field(pattern="^(borrowing|lending)$")
    loan_type: str = Field(
        pattern="^(term_loan|overdraft|revolving_credit|asset_finance|staff_advance|related_party|other)$"
    )
    counterparty_name: str = Field(min_length=1, max_length=200)
    principal: Decimal = Field(gt=0)
    agreement_date: date
    model: str = Field(pattern=_MODEL)
    frequency: str = Field(pattern=_FREQUENCY)
    instalments: int = Field(ge=1, le=1200)
    first_due_on: date
    annual_rate: Decimal = Field(default=Decimal("0"), ge=0)
    compounding: str | None = Field(default=None, pattern=_FREQUENCY)
    grace_periods: int = Field(default=0, ge=0, le=120)
    interest_only_periods: int = Field(default=0, ge=0, le=600)
    balloon: Decimal = Field(default=Decimal("0"), ge=0)
    periodic_fee: Decimal = Field(default=Decimal("0"), ge=0)
    fixed_interest: Decimal = Field(default=Decimal("0"), ge=0)
    upfront_fee: Decimal = Field(default=Decimal("0"), ge=0)
    penalty_rate: Decimal = Field(default=Decimal("0"), ge=0)
    early_settlement_percent: Decimal = Field(default=Decimal("0"), ge=0)
    prepayment_allowed: bool = True
    allocation_order: list[str] = Field(
        default_factory=lambda: ["penalty", "fees", "interest", "principal"]
    )
    custom_rows: list[CustomRowContract] = Field(default_factory=list)
    counterparty_party_id: uuid.UUID | None = None
    counterparty_employee_id: uuid.UUID | None = None
    agreement_reference: str | None = Field(default=None, max_length=80)
    purpose: str | None = Field(default=None, max_length=2000)
    disbursement_date: date | None = None
    financial_account_id: uuid.UUID | None = None
    responsible_id: uuid.UUID | None = None
    agreement_attachment_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)


class ApprovalContract(Contract):
    note: str | None = Field(default=None, max_length=1000)
    attachment_id: uuid.UUID | None = None


class MovementContract(_Figures):
    amount: Decimal = Field(gt=0)
    on: date | None = None
    financial_account_id: uuid.UUID | None = None
    fee: Decimal = Field(default=Decimal("0"), ge=0)
    reference: str | None = Field(default=None, max_length=120)
    evidence_attachment_id: uuid.UUID | None = None


class SettleContract(Contract):
    on: date | None = None
    financial_account_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=120)
    evidence_attachment_id: uuid.UUID | None = None


class RefinanceContract(Contract):
    replacement_id: uuid.UUID
    on: date | None = None


class PenaltyContract(_Figures):
    reason: str = Field(min_length=1, max_length=500)
    amount: Decimal | None = Field(default=None, gt=0)
    on: date | None = None


class WriteOffContract(Contract):
    reason: str = Field(min_length=1, max_length=500)
    on: date | None = None


class RevisionContract(_Figures):
    kind: str = Field(
        pattern="^(reschedule|payment_holiday|deferment|interest_change|restructure)$"
    )
    reason: str = Field(min_length=1, max_length=1000)
    effective_on: date
    first_due_on: date
    instalments: int = Field(ge=1, le=1200)
    annual_rate: Decimal | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, pattern=_MODEL)
    frequency: str | None = Field(default=None, pattern=_FREQUENCY)
    grace_periods: int = Field(default=0, ge=0, le=120)
    interest_only_periods: int = Field(default=0, ge=0, le=600)
    balloon: Decimal = Field(default=Decimal("0"), ge=0)
    periodic_fee: Decimal | None = Field(default=None, ge=0)
    evidence_attachment_id: uuid.UUID | None = None


class DecisionContract(Contract):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


class SecurityContract(_Figures):
    kind: str = Field(pattern="^(collateral|guarantor|covenant)$")
    description: str = Field(min_length=1, max_length=2000)
    party_name: str | None = Field(default=None, max_length=200)
    value: Decimal | None = Field(default=None, ge=0)
    review_on: date | None = None
    attachment_id: uuid.UUID | None = None


class DisputeContract(Contract):
    disputed: bool
    note: str = Field(min_length=1, max_length=1000)


class AccrueContract(Contract):
    as_of: date | None = None


class LoanThresholdContract(_Figures):
    amount: Decimal | None = Field(default=None, gt=0)


class OtherDebtContract(_Figures):
    owed_to_us: bool
    counterparty_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    amount: Decimal = Field(gt=0)
    issued_on: date | None = None
    due_on: date | None = None
    reference: str | None = Field(default=None, max_length=120)
    counterparty_party_id: uuid.UUID | None = None
    financial_account_id: uuid.UUID
    responsible_id: uuid.UUID | None = None
    attachment_id: uuid.UUID | None = None


class OtherSettlementContract(_Figures):
    amount: Decimal = Field(gt=0)
    financial_account_id: uuid.UUID
    on: date | None = None
    reference: str | None = Field(default=None, max_length=120)


# -- replies -------------------------------------------------------------------------------


class ScheduleRowReply(Reply):
    id: uuid.UUID | None = None
    sequence: int
    due_on: date
    opening: Decimal
    interest: Decimal
    fees: Decimal
    principal: Decimal
    capitalised: Decimal
    penalty: Decimal = Decimal("0")
    total: Decimal
    closing: Decimal
    paid: Decimal = Decimal("0")
    arrears: bool = False
    state: str = "scheduled"
    state_label: str = "Scheduled"


class ScheduleSummaryReply(Reply):
    principal: Decimal
    interest: Decimal
    fees: Decimal
    capitalised: Decimal
    total: Decimal
    instalments: int
    final_due_on: date | None


class PreviewReply(Reply):
    rows: list[ScheduleRowReply]
    summary: ScheduleSummaryReply


class LoanPositionReply(Reply):
    principal_agreed: Decimal
    principal_drawn: Decimal
    undrawn: Decimal
    capitalised: Decimal
    principal_repaid: Decimal
    principal_outstanding: Decimal
    interest_due: Decimal
    fees_due: Decimal
    penalties_due: Decimal
    total_outstanding: Decimal
    arrears: Decimal
    next_due_on: date | None
    next_due_amount: Decimal
    days_overdue: int
    settlement_today: Decimal
    ledger_principal: Decimal
    ledger_accrued: Decimal
    agrees_with_ledger: bool


class LoanSummaryReply(Reply):
    id: uuid.UUID
    number: str
    direction: str
    direction_label: str
    loan_type: str
    loan_type_label: str
    counterparty_name: str
    agreement_reference: str | None
    principal: Decimal
    annual_rate: Decimal
    model: str
    model_label: str
    frequency: str
    frequency_label: str
    agreement_date: date
    maturity_date: date | None
    state: str
    state_label: str
    disputed: bool
    outstanding: Decimal
    charges: Decimal
    next_due_on: date | None
    next_due_amount: Decimal
    days_overdue: int
    revision_pending: bool


class VersionReply(Reply):
    id: uuid.UUID
    version: int
    kind: str
    kind_label: str
    state: str
    reason: str | None
    effective_on: date
    principal_basis: Decimal
    annual_rate: Decimal
    instalments: int
    proposed_by: str | None
    decided_by: str | None
    decided_at: str | None
    decision_note: str | None
    rows: list[ScheduleRowReply]
    summary: ScheduleSummaryReply


class TransactionReply(Reply):
    id: uuid.UUID
    kind: str
    kind_label: str
    occurred_on: date
    amount: Decimal
    fee: Decimal
    account: str | None
    reference: str | None
    note: str | None
    recorded_by: str | None
    principal: Decimal
    interest: Decimal
    fees: Decimal
    penalty: Decimal
    journal_entry_id: uuid.UUID | None


class SecurityReply(Reply):
    id: uuid.UUID
    kind: str
    kind_label: str
    description: str
    party_name: str | None
    value: Decimal | None
    review_on: date | None
    released_on: date | None


class LoanDetailReply(LoanSummaryReply):
    counterparty_party_id: uuid.UUID | None
    counterparty_employee_id: uuid.UUID | None
    purpose: str | None
    currency_code: str
    disbursement_date: date | None
    compounding: str | None
    instalments: int
    first_due_on: date
    grace_periods: int
    interest_only_periods: int
    balloon: Decimal
    periodic_fee: Decimal
    fixed_interest: Decimal
    upfront_fee: Decimal
    penalty_rate: Decimal
    early_settlement_percent: Decimal
    prepayment_allowed: bool
    allocation_order: list[str]
    financial_account_id: uuid.UUID | None
    account: str | None
    responsible_id: uuid.UUID | None
    responsible: str | None
    created_by: str | None
    submitted_by: str | None
    approved_by: str | None
    approval_note: str | None
    rejection_reason: str | None
    refinances: str | None
    refinanced_by: str | None
    closed_on: date | None
    closing_note: str | None
    notes: str | None
    position: LoanPositionReply
    current: VersionReply
    proposed: VersionReply | None
    versions: list[VersionReply]
    transactions: list[TransactionReply]
    securities: list[SecurityReply]


class QuoteReply(Reply):
    on: date
    principal: Decimal
    interest_due: Decimal
    interest_accrued: Decimal
    fees_due: Decimal
    penalties_due: Decimal
    early_settlement_fee: Decimal
    total: Decimal


class AccrueReply(Reply):
    accrued: int


class LoanThresholdReply(Reply):
    amount: Decimal | None


class DebtLineReply(Reply):
    kind: str
    kind_label: str
    source_type: str
    source_id: uuid.UUID
    reference: str
    counterparty: str
    counterparty_id: uuid.UUID | None
    description: str
    original: Decimal
    settled: Decimal
    outstanding: Decimal
    charges: Decimal
    issued_on: date
    due_on: date | None
    standing: str
    standing_label: str
    bucket: str
    responsible: str | None


class SplitReply(Reply):
    code: str
    label: str
    amount: Decimal
    count: int


class CounterpartyReply(Reply):
    name: str
    amount: Decimal
    count: int


class LoanBookReply(Reply):
    principal: Decimal
    charges: Decimal
    total: Decimal
    count: int


class TrendReply(Reply):
    month: str
    collected: Decimal
    settled: Decimal
    borrowed: Decimal
    lent: Decimal


class DebtSummaryReply(Reply):
    on: date
    receivables: Decimal
    receivables_by_kind: list[SplitReply]
    payables: Decimal
    payables_by_kind: list[SplitReply]
    loans_receivable: LoanBookReply
    loans_payable: LoanBookReply
    receivable_due_soon: Decimal
    receivable_overdue: Decimal
    payable_due_soon: Decimal
    payable_overdue: Decimal
    collected: Decimal
    repaid: Decimal
    receivable_ageing: list[SplitReply]
    payable_ageing: list[SplitReply]
    largest_debtors: list[CounterpartyReply]
    largest_creditors: list[CounterpartyReply]
    debt_service: Decimal
    cash_available: Decimal
    coverage: Decimal | None
    window_days: int
    period_start: date
    period_end: date
    trend: list[TrendReply]


class CommitmentReply(Reply):
    due_on: date
    inward: bool
    kind: str
    kind_label: str
    reference: str
    counterparty: str
    amount: Decimal
    source_type: str
    source_id: uuid.UUID


class MaturityReply(Reply):
    code: str
    label: str
    borrowed: Decimal
    lent: Decimal


class CashClassReply(Reply):
    code: str
    label: str
    category: str
    inflow: Decimal
    outflow: Decimal
    net: Decimal


class OtherDebtReply(Reply):
    id: uuid.UUID
    number: str
    owed_to_us: bool
    counterparty_name: str
    description: str
    original: Decimal
    settled: Decimal
    outstanding: Decimal
    issued_on: date
    due_on: date | None
