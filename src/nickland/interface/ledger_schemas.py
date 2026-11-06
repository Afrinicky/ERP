"""Request and response contracts for the chart of accounts, the journal and reports.

Monetary values cross the boundary as strings, so no amount passes through a JSON
float, per PRD 9.5.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from nickland.interface.schemas import Contract, Reply

# --------------------------------------------------------------------------------------
# Chart of accounts
# --------------------------------------------------------------------------------------


class AccountGroupReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    kind: str
    statement: str
    display_order: int
    is_active: bool


class AccountReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    kind: str
    statement: str
    group_fk: uuid.UUID | None
    cash_flow_category: str
    control_subledger: str | None
    currency_code: str | None
    is_active: bool
    is_system: bool
    permits_manual_posting: bool
    display_order: int


class AccountBalanceReply(AccountReply):
    """An account with the balance it carries, for the chart screen."""

    balance: Decimal = Decimal("0")
    presented_side: str = "debit"


class CreateAccountContract(Contract):
    code: str = Field(min_length=4, max_length=8)
    name: str = Field(min_length=1, max_length=160)
    group_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2000)
    cash_flow_category: str = Field(default="none", max_length=12)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    permits_manual_posting: bool = True
    display_order: int = 0


class AmendAccountContract(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    group_id: uuid.UUID | None = None
    cash_flow_category: str | None = Field(default=None, max_length=12)
    permits_manual_posting: bool | None = None
    is_active: bool | None = None
    display_order: int | None = None

    def changes(self) -> dict[str, Any]:
        """Only the properties the form actually submitted."""
        return self.model_dump(exclude_unset=True, exclude_none=False)


# --------------------------------------------------------------------------------------
# Accounting calendar
# --------------------------------------------------------------------------------------


class PeriodReply(Reply):
    id: uuid.UUID
    financial_year: int
    sequence: int
    label: str
    starts_on: date
    ends_on: date
    state: str
    closed_at: datetime | None
    reopened_at: datetime | None
    reopen_reason: str | None
    is_year_end: bool
    year_end_posted_at: datetime | None


class OpenYearContract(Contract):
    financial_year: int = Field(ge=2000, le=2200)
    start_month: int | None = Field(default=None, ge=1, le=12)


class CloseCheckReply(Reply):
    name: str
    passed: bool
    detail: str
    measurements: dict[str, Any]


class ReadinessReply(Reply):
    period: PeriodReply
    checks: list[CloseCheckReply]
    ready: bool


class ClosePeriodContract(Contract):
    note: str | None = Field(default=None, max_length=2000)


class ReopenPeriodContract(Contract):
    reason: str = Field(min_length=10, max_length=2000)
    approved_by_id: uuid.UUID | None = None


class CloseYearContract(Contract):
    financial_year: int = Field(ge=2000, le=2200)
    note: str | None = Field(default=None, max_length=2000)


class YearEndReply(Reply):
    period: PeriodReply
    entry_number: str | None
    accounts_closed: int
    net_result: Decimal


# --------------------------------------------------------------------------------------
# Journal entries
# --------------------------------------------------------------------------------------


class JournalLineContract(Contract):
    """One line as the journal form submits it."""

    account_code: str = Field(min_length=4, max_length=8)
    side: str = Field(pattern="^(debit|credit)$")
    amount: Decimal = Field(gt=0)
    narration: str | None = Field(default=None, max_length=500)
    party_id: uuid.UUID | None = None
    subledger: str | None = Field(default=None, max_length=16)
    branch_id: uuid.UUID | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _refuse_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("Send an amount as a string, not a JSON number.")
        return value


class PostJournalContract(Contract):
    posting_date: date
    narration: str = Field(min_length=1, max_length=2000)
    lines: list[JournalLineContract] = Field(min_length=2, max_length=200)
    source: str = Field(default="manual", max_length=24)
    source_reference: str | None = Field(default=None, max_length=120)
    idempotency_key: str | None = Field(default=None, max_length=120)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    branch_id: uuid.UUID | None = None


class JournalLineReply(Reply):
    id: uuid.UUID
    account_fk: uuid.UUID
    display_order: int
    debit: Decimal
    credit: Decimal
    currency_code: str
    narration: str | None
    party_fk: uuid.UUID | None
    subledger: str | None
    state: str
    account_code: str = ""
    account_name: str = ""


class JournalEntryReply(Reply):
    id: uuid.UUID
    number: str | None
    posting_date: date
    period_fk: uuid.UUID | None
    state: str
    source: str
    source_reference: str | None
    narration: str
    currency_code: str
    total_debit: Decimal
    total_credit: Decimal
    created_at: datetime
    created_by_id: uuid.UUID | None
    posted_at: datetime | None
    approved_at: datetime | None
    approved_by_id: uuid.UUID | None
    rejection_reason: str | None
    reverses_fk: uuid.UUID | None
    reversed_by_fk: uuid.UUID | None
    reversal_reason: str | None
    lines: list[JournalLineReply] = Field(default_factory=list)


class PostingOutcomeReply(Reply):
    entry: JournalEntryReply
    created: bool
    awaiting_approval: bool


class JournalPageReply(Reply):
    entries: list[JournalEntryReply]
    total: int
    offset: int
    limit: int


class ApproveContract(Contract):
    note: str | None = Field(default=None, max_length=2000)


class RejectContract(Contract):
    reason: str = Field(min_length=1, max_length=2000)


class ReverseContract(Contract):
    reason: str = Field(min_length=1, max_length=2000)
    posting_date: date | None = None


class ApprovalReply(Reply):
    id: uuid.UUID
    decided_at: datetime
    decided_by_id: uuid.UUID
    decision: str
    reason: str | None
    threshold_amount: Decimal | None


class ThresholdReply(Reply):
    id: uuid.UUID
    scope: str
    amount: Decimal
    requires_distinct_checker: bool
    is_active: bool


class SetThresholdContract(Contract):
    amount: Decimal | None = Field(default=None, ge=0)
    requires_distinct_checker: bool = True

    @field_validator("amount", mode="before")
    @classmethod
    def _refuse_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("Send an amount as a string, not a JSON number.")
        return value


# --------------------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------------------


class TrialBalanceRowReply(Reply):
    code: str
    name: str
    kind: str
    debit: Decimal
    credit: Decimal


class TrialBalanceReply(Reply):
    as_at: date
    starts_on: date | None
    basis: str
    rows: list[TrialBalanceRowReply]
    total_debit: Decimal
    total_credit: Decimal
    difference: Decimal
    balanced: bool


class MovementReply(Reply):
    entry_id: uuid.UUID
    number: str | None
    posting_date: date
    narration: str
    line_narration: str | None
    source: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    party_id: uuid.UUID | None
    state: str


class AccountLedgerReply(Reply):
    account: AccountReply
    starts_on: date | None
    ends_on: date
    basis: str
    opening_balance: Decimal
    movements: list[MovementReply]
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    presented_side: str


class StatementLineReply(Reply):
    code: str
    name: str
    amount: Decimal


class StatementSectionReply(Reply):
    code: str
    name: str
    lines: list[StatementLineReply]
    total: Decimal


class ProfitAndLossReply(Reply):
    starts_on: date
    ends_on: date
    basis: str
    revenue: list[StatementSectionReply]
    expenses: list[StatementSectionReply]
    total_revenue: Decimal
    total_cost_of_sales: Decimal
    gross_profit: Decimal
    total_expenses: Decimal
    net_result: Decimal


class BalanceSheetReply(Reply):
    as_at: date
    basis: str
    assets: list[StatementSectionReply]
    liabilities: list[StatementSectionReply]
    equity: list[StatementSectionReply]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    total_liabilities_and_equity: Decimal
    retained_result: Decimal
    difference: Decimal
    balanced: bool


class CashFlowReply(Reply):
    starts_on: date
    ends_on: date
    opening_cash: Decimal
    operating: Decimal
    investing: Decimal
    financing: Decimal
    unclassified: Decimal
    net_movement: Decimal
    closing_cash: Decimal
    reconciles: bool


class SubledgerVarianceReply(Reply):
    account_code: str
    account_name: str
    subledger: str
    control_balance: Decimal
    subledger_balance: Decimal
    difference: Decimal
    agrees: bool


class LedgerSummaryReply(Reply):
    as_at: date
    basis: str
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    cash_on_hand: Decimal
    receivables: Decimal
    payables: Decimal
    revenue_year_to_date: Decimal
    expenses_year_to_date: Decimal
    net_result_year_to_date: Decimal
    trial_balance_difference: Decimal
    accounting_equation_difference: Decimal
    pending_approvals: int
    open_period_label: str | None
    financial_year: int | None
