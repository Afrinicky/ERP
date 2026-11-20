"""Request and response contracts for treasury, receivables, expenses, reconciliation and
the command centre.

Money crosses the boundary as strings, never as JSON numbers, per PRD §9.5.
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


class ReasonContract(Contract):
    reason: str = Field(min_length=1, max_length=500)


# -- treasury -----------------------------------------------------------------------------


class FinancialAccountContract(Contract):
    kind: str = Field(pattern="^(cash_drawer|petty_cash|bank|mobile_money)$")
    name: str = Field(min_length=2, max_length=120)
    institution: str | None = Field(default=None, max_length=120)
    number: str | None = Field(default=None, max_length=60)
    branch_name: str | None = Field(default=None, max_length=120)
    custodian_id: uuid.UUID | None = None
    notes: str | None = None


class AmendFinancialAccountContract(Contract):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    institution: str | None = Field(default=None, max_length=120)
    number: str | None = Field(default=None, max_length=60)
    branch_name: str | None = Field(default=None, max_length=120)
    custodian_id: uuid.UUID | None = None
    is_active: bool | None = None


class FinancialAccountReply(Reply):
    id: uuid.UUID
    kind: str
    kind_label: str
    name: str
    ledger_code: str
    institution: str | None
    number: str | None
    branch_name: str | None
    custodian: str | None
    balance: Decimal
    is_active: bool


class MovementContract(_Figures):
    kind: str = Field(pattern="^(transfer|bank_charge|interest|adjustment_in|adjustment_out)$")
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)
    from_account_id: uuid.UUID | None = None
    to_account_id: uuid.UUID | None = None
    charge: Decimal = Field(default=Decimal("0"), ge=0)
    occurred_on: date | None = None
    reference: str | None = Field(default=None, max_length=120)


class MovementReply(Reply):
    id: uuid.UUID
    number: str
    kind: str
    kind_label: str
    occurred_on: date
    from_account: str | None
    to_account: str | None
    amount: Decimal
    charge: Decimal
    reference: str | None
    reason: str


class CashbookLineReply(Reply):
    entry_id: uuid.UUID
    number: str | None
    posting_date: date
    narration: str
    source: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


class CashbookReply(Reply):
    account: FinancialAccountReply
    starts_on: date | None
    ends_on: date
    opening_balance: Decimal
    closing_balance: Decimal
    total_in: Decimal
    total_out: Decimal
    lines: list[CashbookLineReply]


# -- receivables --------------------------------------------------------------------------


class InvoiceableLineReply(Reply):
    line_id: uuid.UUID
    description: str
    unit_price: Decimal
    ordered: Decimal
    delivered: Decimal
    invoiced: Decimal
    invoiceable: Decimal


class InvoiceLineContract(_Figures):
    sales_order_line_id: uuid.UUID
    quantity: Decimal = Field(gt=0)


class InvoiceContract(Contract):
    sales_order_id: uuid.UUID
    lines: list[InvoiceLineContract] = Field(default_factory=list)
    basis: str = Field(default="delivered", pattern="^(delivered|ordered)$")
    issued_on: date | None = None
    customer_reference: str | None = Field(default=None, max_length=120)
    credit_override_reason: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class InvoiceLineReply(Reply):
    id: uuid.UUID
    position: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    net: Decimal
    tax: Decimal
    total: Decimal
    quantity_credited: Decimal


class InvoiceReply(Reply):
    id: uuid.UUID
    number: str
    origin: str
    customer_id: uuid.UUID
    customer: str | None
    order_number: str | None
    issued_on: date
    due_on: date
    net_total: Decimal
    tax_total: Decimal
    total: Decimal
    amount_received: Decimal
    amount_credited: Decimal
    amount_written_off: Decimal
    amount_provided: Decimal
    outstanding: Decimal
    state: str
    state_label: str
    disputed: bool
    days_overdue: int
    credit_override_reason: str | None
    lines: list[InvoiceLineReply]


class CreditLineContract(_Figures):
    invoice_line_id: uuid.UUID
    quantity: Decimal = Field(gt=0)


class CreditContract(Contract):
    lines: list[CreditLineContract] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class CreditNoteReply(Reply):
    id: uuid.UUID
    number: str
    invoice_id: uuid.UUID
    issued_on: date
    reason: str
    net: Decimal
    tax: Decimal
    total: Decimal


class ProvisionContract(_Figures):
    amount: Decimal
    reason: str = Field(min_length=1, max_length=500)


class AllocationContract(_Figures):
    invoice_id: uuid.UUID
    amount: Decimal = Field(gt=0)


class ReceiptContract(_Figures):
    customer_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    financial_account_id: uuid.UUID | None = None
    received_on: date | None = None
    reference: str | None = Field(default=None, max_length=120)
    allocations: list[AllocationContract] | None = None
    notes: str | None = None


class ReceiptReply(Reply):
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    customer: str | None
    account: str | None
    received_on: date
    amount: Decimal
    allocated: Decimal
    refunded: Decimal
    unapplied: Decimal
    reference: str | None


class RefundContract(_Figures):
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)
    financial_account_id: uuid.UUID | None = None


class DepositContract(_Figures):
    customer_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    financial_account_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    condition: str = Field(default="on_invoice", pattern="^(on_invoice|on_delivery)$")
    received_on: date | None = None
    reference: str | None = Field(default=None, max_length=120)


class ApplyDepositContract(_Figures):
    invoice_id: uuid.UUID
    amount: Decimal | None = Field(default=None, gt=0)


class DepositReply(Reply):
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    customer: str | None
    order_number: str | None
    received_on: date
    amount: Decimal
    applied: Decimal
    refunded: Decimal
    held: Decimal
    condition: str
    condition_label: str
    state: str


class AgeingRowReply(Reply):
    customer_id: uuid.UUID
    customer: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    over_90: Decimal
    total: Decimal
    unapplied: Decimal
    credit_limit: Decimal | None
    on_hold: bool


class NoteContract(_Figures):
    kind: str = Field(pattern="^(contact|promise|dispute|resolved)$")
    note: str = Field(min_length=1, max_length=2000)
    invoice_id: uuid.UUID | None = None
    promised_on: date | None = None
    promised_amount: Decimal | None = None


class NoteReply(Reply):
    id: uuid.UUID
    kind: str
    kind_label: str
    note: str
    invoice_number: str | None
    promised_on: date | None
    promised_amount: Decimal | None
    recorded_at: datetime
    recorded_by: str | None


class WorklistReply(Reply):
    invoice: InvoiceReply
    days_overdue: int
    outstanding: Decimal
    latest_note: str | None
    latest_kind: str | None
    promised_on: date | None
    reminder_due: bool


class StatementEntryReply(Reply):
    posted_on: date
    reference: str | None
    narration: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


class PartyStatementReply(Reply):
    party_id: uuid.UUID
    party: str
    starts_on: date
    ends_on: date
    opening: Decimal
    closing: Decimal
    entries: list[StatementEntryReply]
    ageing: dict[str, Decimal] = Field(default_factory=dict)


# -- expenses -----------------------------------------------------------------------------


class ExpenseContract(_Figures):
    payee_name: str = Field(min_length=1, max_length=200)
    category_code: str = Field(min_length=5, max_length=8)
    description: str = Field(min_length=1, max_length=2000)
    net: Decimal = Field(gt=0)
    settlement: str = Field(default="paid", pattern="^(paid|on_credit|staff_claim)$")
    input_tax: Decimal = Field(default=Decimal("0"), ge=0)
    classification: str = Field(default="operating", pattern="^(operating|capital)$")
    incurred_on: date | None = None
    payee_reference: str | None = Field(default=None, max_length=120)
    payee_party_id: uuid.UUID | None = None
    employee_id: uuid.UUID | None = None
    financial_account_id: uuid.UUID | None = None
    duplicate_reason: str | None = Field(default=None, max_length=500)
    evidence_attachment_id: uuid.UUID | None = None


class ExpenseReply(Reply):
    id: uuid.UUID
    number: str
    payee_name: str
    category_code: str
    category: str | None
    description: str
    classification: str
    net: Decimal
    input_tax: Decimal
    total: Decimal
    incurred_on: date
    payee_reference: str | None
    settlement: str
    settlement_label: str
    account: str | None
    employee: str | None
    state: str
    state_label: str
    submitted_by: str | None
    approved_by: str | None
    rejection_reason: str | None
    duplicate_of: str | None
    duplicate_reason: str | None
    settled_on: date | None


class SettleContract(Contract):
    financial_account_id: uuid.UUID
    settled_on: date | None = None


class ThresholdContract(_Figures):
    amount: Decimal | None = Field(default=None, gt=0)


class ThresholdReply(Reply):
    amount: Decimal | None


class RecurringContract(_Figures):
    name: str = Field(min_length=1, max_length=120)
    payee_name: str = Field(min_length=1, max_length=200)
    category_code: str = Field(min_length=5, max_length=8)
    description: str = Field(min_length=1, max_length=2000)
    net: Decimal = Field(gt=0)
    frequency: str = Field(pattern="^(weekly|monthly|quarterly)$")
    next_due: date
    settlement: str = Field(default="on_credit", pattern="^(paid|on_credit)$")
    input_tax: Decimal = Field(default=Decimal("0"), ge=0)
    payee_party_id: uuid.UUID | None = None
    financial_account_id: uuid.UUID | None = None
    ends_on: date | None = None


class RecurringReply(Reply):
    id: uuid.UUID
    name: str
    payee_name: str
    category_code: str
    description: str
    net: Decimal
    input_tax: Decimal
    settlement: str
    frequency: str
    next_due: date
    ends_on: date | None
    is_active: bool


# -- reconciliation -----------------------------------------------------------------------


class ImportStatementContract(_Figures):
    financial_account_id: uuid.UUID
    content: str = Field(min_length=1, max_length=2_000_000)
    closing_balance: Decimal
    reference: str = Field(min_length=1, max_length=120)


class StatementLineReply(Reply):
    id: uuid.UUID
    position: int
    line_date: date
    description: str
    reference: str | None
    amount: Decimal
    state: str
    note: str | None


class StatementReply(Reply):
    id: uuid.UUID
    account_id: uuid.UUID
    account: str | None
    reference: str
    starts_on: date
    ends_on: date
    closing_balance: Decimal
    ledger_balance: Decimal
    difference: Decimal
    unmatched: int
    duplicates: int
    state: str
    reconciled: bool
    lines: list[StatementLineReply] = Field(default_factory=list)


class LedgerItemReply(Reply):
    journal_line_id: uuid.UUID
    entry_number: str | None
    posting_date: date
    narration: str
    reference: str | None
    amount: Decimal


class SuggestionReply(Reply):
    item: LedgerItemReply
    score: int


class MatchContract(Contract):
    journal_line_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class PostFromLineContract(Contract):
    kind: str = Field(pattern="^(bank_charge|interest|adjustment_in|adjustment_out)$")
    reason: str = Field(default="", max_length=500)


class AutoMatchReply(Reply):
    matched: int


class SourceRowReply(Reply):
    source: str
    label: str
    records: int
    recorded: Decimal
    posted: Decimal
    difference: Decimal
    unposted: int
    agrees: bool
    notes: list[str]


# -- the command centre ------------------------------------------------------------------


class MeasureReply(Reply):
    code: str
    label: str
    basis: str
    description: str
    value: Decimal
    starts_on: date | None
    ends_on: date


class DrillLineReply(Reply):
    entry_id: uuid.UUID
    number: str | None
    posting_date: date
    narration: str
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    contribution: Decimal


class DrillReply(Reply):
    measure: MeasureReply
    lines: list[DrillLineReply]
    total: Decimal
    truncated: bool


class BreakdownReply(Reply):
    key: str
    label: str
    amount: Decimal


class WidgetContract(Contract):
    measure: str = Field(max_length=40)
    size: str = Field(default="medium", pattern="^(small|medium|large)$")


class LayoutContract(Contract):
    widgets: list[WidgetContract] = Field(min_length=1, max_length=24)
    preset: str = Field(default="owner", max_length=24)
    role_code: str | None = Field(default=None, max_length=48)


class LayoutReply(Reply):
    preset: str
    widgets: list[dict[str, str]]
    source: str


class PresetReply(Reply):
    code: str
    label: str
    widgets: list[dict[str, str]]


# -- payables -----------------------------------------------------------------------------


class PriorityContract(Contract):
    priority: str = Field(pattern="^(urgent|normal|hold)$")
    reason: str = Field(min_length=1, max_length=500)


class BatchItemContract(_Figures):
    supplier_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    invoice_ids: list[uuid.UUID] = Field(default_factory=list)
    reference: str | None = Field(default=None, max_length=120)


class BatchContract(Contract):
    financial_account_id: uuid.UUID
    items: list[BatchItemContract] = Field(min_length=1, max_length=100)
    pay_on: date | None = None


class BatchReply(Reply):
    id: uuid.UUID
    number: str
    state: str
    account: str | None
    pay_on: date
    items: list[dict[str, Any]]
    total: Decimal
    prepared_by: str | None
    approved_by: str | None
    rejection_reason: str | None
