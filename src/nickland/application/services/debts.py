"""The debt registers and their analysis. PRD §25.2, §25.3, §25.6, AC-23, AC-28 to AC-30.

Two registers: what is owed to the business and what the business owes. Each line is read
from the one record that creates the debt — an invoice, an expense, a tax obligation, an
agreement — and from nowhere else, so a debt cannot appear twice. An amount no source
records can be entered by hand, and the hand entry is refused if it describes something a
source already holds.

The registers are never netted. A customer who is also a supplier owes and is owed in two
places, and the totals say so: set-off is a legal act, not an arithmetic one.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.loans import LoanService, TransactionKind
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.treasury import TreasuryService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.finance import ExpenseSettlement, ExpenseState
from nickland.domain.financing import (
    AGEING_BUCKETS,
    MATURITY_BANDS,
    AgreementState,
    DebtKind,
    DebtStanding,
    LoanDirection,
    ageing_bucket,
    debt_standing,
    maturity_band,
)
from nickland.domain.ledger import CashFlowCategory, EntryLine, JournalSource, Side
from nickland.domain.money import ZERO, money
from nickland.domain.orders import PAYABLE_STATES
from nickland.domain.pricing import CommissionState
from nickland.persistence.catalogue_models import Party
from nickland.persistence.finance_models import CustomerReceipt, Expense, SalesInvoice
from nickland.persistence.financing_models import (
    LoanAgreement,
    LoanInstalment,
    LoanSchedule,
    LoanTransaction,
    OtherDebt,
    OtherDebtSettlement,
)
from nickland.persistence.ledger_models import Account, JournalLine
from nickland.persistence.models import Business, User
from nickland.persistence.order_models import SupplierInvoice, SupplierPayment
from nickland.persistence.pricing_models import CommissionAccrual
from nickland.persistence.tax_models import TaxAuthority, TaxObligation
from nickland.platform.clock import Clock, SystemClock, business_date

OTHER_RECEIVABLES = "11100"
AMOUNTS_HELD = "20500"


@dataclass(frozen=True, slots=True)
class DebtLine:
    kind: DebtKind
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
    """Interest, fees and penalties due on an agreement, apart from its principal."""
    issued_on: date
    due_on: date | None
    standing: DebtStanding
    bucket: str
    responsible_id: uuid.UUID | None
    branch_id: uuid.UUID | None

    @property
    def owed_to_us(self) -> bool:
        return self.kind.owed_to_us


@dataclass(frozen=True, slots=True)
class Split:
    code: str
    label: str
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True)
class Counterparty:
    name: str
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True)
class CommitmentLine:
    due_on: date
    inward: bool
    kind: DebtKind
    reference: str
    counterparty: str
    amount: Decimal
    source_type: str
    source_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LoanBook:
    principal: Decimal
    charges: Decimal
    count: int

    @property
    def total(self) -> Decimal:
        return self.principal + self.charges


@dataclass(frozen=True, slots=True)
class TrendPoint:
    month: str
    collected: Decimal
    settled: Decimal
    borrowed: Decimal
    lent: Decimal


@dataclass(frozen=True, slots=True)
class DebtSummary:
    """The debt dashboard of PRD §25.6. Receivables and payables stand apart."""

    on: date
    receivables: Decimal
    receivables_by_kind: list[Split]
    payables: Decimal
    payables_by_kind: list[Split]
    loans_receivable: LoanBook
    loans_payable: LoanBook
    receivable_due_soon: Decimal
    receivable_overdue: Decimal
    payable_due_soon: Decimal
    payable_overdue: Decimal
    collected: Decimal
    repaid: Decimal
    receivable_ageing: list[Split]
    payable_ageing: list[Split]
    largest_debtors: list[Counterparty]
    largest_creditors: list[Counterparty]
    debt_service: Decimal
    cash_available: Decimal
    coverage: Decimal | None
    window_days: int
    period_start: date
    period_end: date
    trend: list[TrendPoint]


@dataclass(frozen=True, slots=True)
class MaturityRow:
    code: str
    label: str
    borrowed: Decimal
    lent: Decimal


@dataclass(frozen=True, slots=True)
class CashClass:
    code: str
    label: str
    category: str
    inflow: Decimal
    outflow: Decimal

    @property
    def net(self) -> Decimal:
        return self.inflow - self.outflow


@dataclass(slots=True)
class OtherDebtDraft:
    owed_to_us: bool
    counterparty_name: str
    description: str
    amount: Decimal
    issued_on: date | None = None
    due_on: date | None = None
    reference: str | None = None
    counterparty_party_id: uuid.UUID | None = None
    financial_account_id: uuid.UUID | None = None
    responsible_id: uuid.UUID | None = None
    attachment_id: uuid.UUID | None = None


# The classes of cash movement AC-29 asks to be kept apart, by the account opposite cash.
CASH_CLASSES: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    ("trade_collections", "Collections from customers", "operating", frozenset({"11000", "22000"})),
    ("supplier_payments", "Payments to suppliers", "operating", frozenset({"20000", "20150"})),
    ("expenses", "Expenses and staff claims", "operating", frozenset({"20100", "20200", "20400"})),
    ("commissions", "Commissions paid", "operating", frozenset({"20300"})),
    (
        "tax",
        "Tax paid and refunded",
        "operating",
        frozenset({"11300", "11400", "13000", "21000", "21100", "21200"}),
    ),
    (
        "loan_principal_borrowed",
        "Loan principal borrowed and repaid",
        "financing",
        frozenset({"23000"}),
    ),
    (
        "interest_paid",
        "Interest and finance charges paid",
        "financing",
        frozenset({"23100", "71000", "71300"}),
    ),
    ("loan_principal_lent", "Loan principal lent and collected", "investing", frozenset({"11200"})),
    (
        "interest_received",
        "Interest and loan fees received",
        "investing",
        frozenset({"11210", "70000", "70200"}),
    ),
    ("owners", "Owner's capital and drawings", "financing", frozenset({"30000", "30100"})),
)


class DebtService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._loans = LoanService(session, clock=self._clock)
        self._treasury = TreasuryService(session, clock=self._clock)

    # -- the registers ----------------------------------------------------------------------

    def register(
        self,
        actor: AnyActor,
        *,
        owed_to_us: bool,
        on: date | None = None,
        kind: str | None = None,
        standing: str | None = None,
        counterparty: str | None = None,
        branch_id: uuid.UUID | None = None,
        responsible_id: uuid.UUID | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        include_settled: bool = False,
    ) -> list[DebtLine]:
        _require_view(actor)
        business_id = _business_of(actor)
        today = on or self._today(business_id)
        lines = (
            self._receivables(business_id, today)
            if owed_to_us
            else self._payables(business_id, today)
        )
        if not include_settled:
            lines = [line for line in lines if line.outstanding > ZERO]
        if kind:
            wanted = DebtKind(kind)
            lines = [line for line in lines if line.kind is wanted]
        if standing:
            state = DebtStanding(standing)
            lines = [line for line in lines if line.standing is state]
        if counterparty:
            needle = counterparty.casefold()
            lines = [line for line in lines if needle in line.counterparty.casefold()]
        if branch_id:
            lines = [line for line in lines if line.branch_id == branch_id]
        if responsible_id:
            lines = [line for line in lines if line.responsible_id == responsible_id]
        if due_from:
            lines = [line for line in lines if line.due_on and line.due_on >= due_from]
        if due_to:
            lines = [line for line in lines if line.due_on and line.due_on <= due_to]
        _assert_unique(lines)
        return sorted(lines, key=lambda line: (line.due_on or date.max, line.reference))

    def _receivables(self, business_id: uuid.UUID, today: date) -> list[DebtLine]:
        lines: list[DebtLine] = []
        names = self._party_names(business_id)
        for invoice in self._db.scalars(
            select(SalesInvoice).where(
                SalesInvoice.business_id == business_id, SalesInvoice.state != "cancelled"
            )
        ):
            settled = invoice.amount_received + invoice.amount_credited
            written_off = invoice.state == "written_off"
            lines.append(
                DebtLine(
                    kind=DebtKind.TRADE_RECEIVABLE,
                    source_type="sales_invoice",
                    source_id=invoice.id,
                    reference=invoice.number,
                    counterparty=names.get(invoice.customer_fk, "Customer"),
                    counterparty_id=invoice.customer_fk,
                    description=invoice.customer_reference or "Customer invoice",
                    original=invoice.total,
                    settled=settled + invoice.amount_written_off,
                    outstanding=max(ZERO, invoice.outstanding),
                    charges=ZERO,
                    issued_on=invoice.issued_on,
                    due_on=invoice.due_on,
                    standing=debt_standing(
                        invoice.total,
                        invoice.total - max(ZERO, invoice.outstanding),
                        invoice.due_on,
                        today,
                        disputed=invoice.disputed,
                        written_off=written_off,
                    ),
                    bucket=ageing_bucket(invoice.due_on, today),
                    responsible_id=None,
                    branch_id=invoice.branch_id,
                )
            )
        lines += self._agreements(business_id, LoanDirection.LENDING, today)
        lines += self._others(business_id, owed_to_us=True, today=today)
        return lines

    def _payables(self, business_id: uuid.UUID, today: date) -> list[DebtLine]:
        lines: list[DebtLine] = []
        names = self._party_names(business_id)
        for invoice in self._db.scalars(
            select(SupplierInvoice).where(
                SupplierInvoice.business_id == business_id,
                SupplierInvoice.state.in_([state.value for state in PAYABLE_STATES] + ["paid"]),
            )
        ):
            outstanding = max(ZERO, invoice.total - invoice.amount_paid - invoice.amount_credited)
            lines.append(
                DebtLine(
                    kind=DebtKind.SUPPLIER,
                    source_type="supplier_invoice",
                    source_id=invoice.id,
                    reference=invoice.number or invoice.supplier_reference,
                    counterparty=names.get(invoice.supplier_fk, "Supplier"),
                    counterparty_id=invoice.supplier_fk,
                    description=f"Supplier invoice {invoice.supplier_reference}",
                    original=invoice.total,
                    settled=invoice.amount_paid + invoice.amount_credited,
                    outstanding=outstanding,
                    charges=ZERO,
                    issued_on=invoice.invoiced_on,
                    due_on=invoice.due_on,
                    standing=debt_standing(
                        invoice.total,
                        invoice.total - outstanding,
                        invoice.due_on,
                        today,
                        disputed=invoice.priority == "held",
                    ),
                    bucket=ageing_bucket(invoice.due_on, today),
                    responsible_id=None,
                    branch_id=invoice.branch_id,
                )
            )
        for expense in self._db.scalars(
            select(Expense).where(
                Expense.business_id == business_id,
                Expense.settlement != ExpenseSettlement.PAID.value,
                Expense.state.in_([ExpenseState.RECOGNISED.value, ExpenseState.SETTLED.value]),
            )
        ):
            settled = expense.state == ExpenseState.SETTLED.value
            claim = expense.settlement == ExpenseSettlement.STAFF_CLAIM.value
            lines.append(
                DebtLine(
                    kind=DebtKind.STAFF_CLAIM if claim else DebtKind.EXPENSE,
                    source_type="expense",
                    source_id=expense.id,
                    reference=expense.number,
                    counterparty=expense.payee_name,
                    counterparty_id=expense.payee_party_fk or expense.employee_id,
                    description=expense.description,
                    original=expense.total,
                    settled=expense.total if settled else ZERO,
                    outstanding=ZERO if settled else expense.total,
                    charges=ZERO,
                    issued_on=expense.incurred_on,
                    due_on=expense.incurred_on,
                    standing=debt_standing(
                        expense.total,
                        expense.total if settled else ZERO,
                        None,
                        today,
                    ),
                    bucket=ageing_bucket(expense.incurred_on if not settled else None, today),
                    responsible_id=expense.submitted_by_id,
                    branch_id=expense.branch_id,
                )
            )
        authorities = {
            row.id: row.name
            for row in self._db.scalars(
                select(TaxAuthority).where(TaxAuthority.business_id == business_id)
            )
        }
        for obligation in self._db.scalars(
            select(TaxObligation).where(
                TaxObligation.business_id == business_id,
                TaxObligation.state != "written_off",
            )
        ):
            owed = money(
                obligation.assessed
                - obligation.recoverable
                - obligation.withheld
                + obligation.adjustments
            )
            if owed <= ZERO:
                continue
            outstanding = max(ZERO, owed - obligation.paid)
            lines.append(
                DebtLine(
                    kind=DebtKind.TAX,
                    source_type="tax_obligation",
                    source_id=obligation.id,
                    reference=obligation.label,
                    counterparty=authorities.get(obligation.authority_fk, "Tax authority")
                    if obligation.authority_fk
                    else "Tax authority",
                    counterparty_id=obligation.authority_fk,
                    description=f"{obligation.label}, {obligation.period_starts_on:%b %Y}",
                    original=owed,
                    settled=min(owed, obligation.paid),
                    outstanding=outstanding,
                    charges=ZERO,
                    issued_on=obligation.period_ends_on,
                    due_on=obligation.due_on,
                    standing=debt_standing(owed, owed - outstanding, obligation.due_on, today),
                    bucket=ageing_bucket(obligation.due_on, today),
                    responsible_id=None,
                    branch_id=None,
                )
            )
        for accrual in self._db.scalars(
            select(CommissionAccrual).where(
                CommissionAccrual.business_id == business_id,
                CommissionAccrual.state.in_(
                    [
                        CommissionState.APPROVED.value,
                        CommissionState.PAYABLE.value,
                        CommissionState.PAID.value,
                    ]
                ),
            )
        ):
            owed = accrual.entitlement or accrual.accrued_amount
            outstanding = max(ZERO, owed - accrual.settled_amount)
            lines.append(
                DebtLine(
                    kind=DebtKind.COMMISSION,
                    source_type="commission",
                    source_id=accrual.id,
                    reference=accrual.source_reference or "Commission",
                    counterparty=names.get(accrual.affiliate_fk, "Affiliate"),
                    counterparty_id=accrual.affiliate_fk,
                    description="Referral commission",
                    original=owed,
                    settled=accrual.settled_amount,
                    outstanding=outstanding,
                    charges=ZERO,
                    issued_on=accrual.accrued_on,
                    due_on=None,
                    standing=debt_standing(owed, accrual.settled_amount, None, today),
                    bucket="current",
                    responsible_id=None,
                    branch_id=None,
                )
            )
        for receipt in self._db.scalars(
            select(CustomerReceipt).where(CustomerReceipt.business_id == business_id)
        ):
            if receipt.unapplied <= ZERO:
                continue
            lines.append(
                DebtLine(
                    kind=DebtKind.REFUND,
                    source_type="customer_receipt",
                    source_id=receipt.id,
                    reference=receipt.number,
                    counterparty=names.get(receipt.customer_fk, "Customer"),
                    counterparty_id=receipt.customer_fk,
                    description="Received and not yet applied: owed back or to be applied",
                    original=receipt.amount,
                    settled=receipt.allocated + receipt.refunded,
                    outstanding=receipt.unapplied,
                    charges=ZERO,
                    issued_on=receipt.received_on,
                    due_on=None,
                    standing=DebtStanding.ACTIVE,
                    bucket="current",
                    responsible_id=receipt.recorded_by_id,
                    branch_id=receipt.branch_id,
                )
            )
        lines += self._agreements(business_id, LoanDirection.BORROWING, today)
        lines += self._others(business_id, owed_to_us=False, today=today)
        return lines

    def _agreements(
        self, business_id: uuid.UUID, direction: LoanDirection, today: date
    ) -> list[DebtLine]:
        lines: list[DebtLine] = []
        for agreement in self._db.scalars(
            select(LoanAgreement).where(
                LoanAgreement.business_id == business_id,
                LoanAgreement.direction == direction.value,
                LoanAgreement.state.in_(
                    [
                        AgreementState.ACTIVE.value,
                        AgreementState.SETTLED.value,
                        AgreementState.REFINANCED.value,
                        AgreementState.WRITTEN_OFF.value,
                    ]
                ),
            )
        ):
            position = self._loans.position(agreement, today)
            charges = position.interest_due + position.fees_due + position.penalties_due
            restructured = len(agreement.schedules) > 1 and any(
                version.kind not in {"original", "prepayment"} and version.state == "current"
                for version in agreement.schedules
            )
            original = position.principal_drawn + position.capitalised
            due = (
                min(
                    (
                        row.due_on
                        for row in self._loans.open_instalments(agreement)
                        if row.owed > row.paid
                    ),
                    default=None,
                )
                if agreement.state == AgreementState.ACTIVE.value
                else None
            )
            lines.append(
                DebtLine(
                    kind=DebtKind.LOAN_ISSUED
                    if direction is LoanDirection.LENDING
                    else DebtKind.BORROWING,
                    source_type="loan_agreement",
                    source_id=agreement.id,
                    reference=agreement.number,
                    counterparty=agreement.counterparty_name,
                    counterparty_id=agreement.counterparty_party_fk
                    or agreement.counterparty_employee_id,
                    description=agreement.purpose
                    or agreement.loan_type.replace("_", " ").capitalize(),
                    original=original,
                    settled=position.principal_repaid,
                    outstanding=position.principal_outstanding + charges,
                    charges=charges,
                    issued_on=agreement.disbursement_date or agreement.agreement_date,
                    due_on=due,
                    standing=DebtStanding.ACTIVE
                    if original <= ZERO
                    else debt_standing(
                        original + charges,
                        original - position.principal_outstanding,
                        due,
                        today,
                        disputed=agreement.disputed,
                        restructured=restructured,
                        written_off=agreement.state == AgreementState.WRITTEN_OFF.value,
                    ),
                    bucket=ageing_bucket(due, today),
                    responsible_id=agreement.responsible_id,
                    branch_id=agreement.branch_id,
                )
            )
        return lines

    def _others(self, business_id: uuid.UUID, *, owed_to_us: bool, today: date) -> list[DebtLine]:
        lines: list[DebtLine] = []
        for debt in self._db.scalars(
            select(OtherDebt).where(
                OtherDebt.business_id == business_id, OtherDebt.owed_to_us.is_(owed_to_us)
            )
        ):
            outstanding = ZERO if debt.written_off else max(ZERO, debt.original - debt.settled)
            lines.append(
                DebtLine(
                    kind=DebtKind.OTHER_RECEIVABLE if owed_to_us else DebtKind.OTHER_PAYABLE,
                    source_type="other_debt",
                    source_id=debt.id,
                    reference=debt.number,
                    counterparty=debt.counterparty_name,
                    counterparty_id=debt.counterparty_party_fk,
                    description=debt.description,
                    original=debt.original,
                    settled=debt.settled,
                    outstanding=outstanding,
                    charges=ZERO,
                    issued_on=debt.issued_on,
                    due_on=debt.due_on,
                    standing=debt_standing(
                        debt.original,
                        debt.settled,
                        debt.due_on,
                        today,
                        disputed=debt.disputed,
                        written_off=debt.written_off,
                    ),
                    bucket=ageing_bucket(debt.due_on, today),
                    responsible_id=debt.responsible_id,
                    branch_id=None,
                )
            )
        return lines

    def _party_names(self, business_id: uuid.UUID) -> dict[uuid.UUID, str]:
        return {
            row[0]: row[1]
            for row in self._db.execute(
                select(Party.id, Party.name).where(Party.business_id == business_id)
            ).all()
        }

    # -- the dashboard ----------------------------------------------------------------------

    def summary(
        self,
        actor: AnyActor,
        *,
        on: date | None = None,
        window_days: int = 30,
        start: date | None = None,
        end: date | None = None,
    ) -> DebtSummary:
        _require_view(actor)
        business_id = _business_of(actor)
        today = on or self._today(business_id)
        if window_days < 1 or window_days > 366:
            raise ValidationError("A window of 1 to 366 days.", field="window_days")
        period_end = end or today
        period_start = start or period_end.replace(day=1)
        receivables = self.register(actor, owed_to_us=True, on=today)
        payables = self.register(actor, owed_to_us=False, on=today)
        horizon = today + timedelta(days=window_days)

        def due_soon(lines: list[DebtLine]) -> Decimal:
            return sum(
                (
                    line.outstanding
                    for line in lines
                    if line.due_on is not None and today <= line.due_on <= horizon
                ),
                ZERO,
            )

        def overdue(lines: list[DebtLine]) -> Decimal:
            return sum(
                (line.outstanding for line in lines if line.due_on and line.due_on < today), ZERO
            )

        borrowed = [line for line in payables if line.kind is DebtKind.BORROWING]
        lent = [line for line in receivables if line.kind is DebtKind.LOAN_ISSUED]
        debt_service = self._debt_service(business_id, today, horizon)
        cash = self._cash(business_id, today)
        return DebtSummary(
            on=today,
            receivables=_total(receivables),
            receivables_by_kind=_by_kind(receivables),
            payables=_total(payables),
            payables_by_kind=_by_kind(payables),
            loans_receivable=LoanBook(
                principal=sum((line.outstanding - line.charges for line in lent), ZERO),
                charges=sum((line.charges for line in lent), ZERO),
                count=len(lent),
            ),
            loans_payable=LoanBook(
                principal=sum((line.outstanding - line.charges for line in borrowed), ZERO),
                charges=sum((line.charges for line in borrowed), ZERO),
                count=len(borrowed),
            ),
            receivable_due_soon=due_soon(receivables),
            receivable_overdue=overdue(receivables),
            payable_due_soon=due_soon(payables),
            payable_overdue=overdue(payables),
            collected=self._collected(business_id, period_start, period_end),
            repaid=self._repaid(business_id, period_start, period_end),
            receivable_ageing=_ageing(receivables),
            payable_ageing=_ageing(payables),
            largest_debtors=_largest(receivables),
            largest_creditors=_largest(payables),
            debt_service=debt_service,
            cash_available=cash,
            coverage=(cash / debt_service).quantize(Decimal("0.01"))
            if debt_service > ZERO
            else None,
            window_days=window_days,
            period_start=period_start,
            period_end=period_end,
            trend=self._trend(business_id, period_end),
        )

    def calendar(
        self, actor: AnyActor, *, start: date | None = None, days: int = 90
    ) -> list[CommitmentLine]:
        """Money expected in and out by date: instalments, invoices, tax and other debts."""
        _require_view(actor)
        business_id = _business_of(actor)
        first = start or self._today(business_id)
        last = first + timedelta(days=days)
        lines: list[CommitmentLine] = []
        agreements = {
            row.id: row
            for row in self._db.scalars(
                select(LoanAgreement).where(
                    LoanAgreement.business_id == business_id,
                    LoanAgreement.state == AgreementState.ACTIVE.value,
                )
            )
        }
        for row in self._db.scalars(
            select(LoanInstalment)
            .join(LoanSchedule, LoanSchedule.id == LoanInstalment.schedule_fk)
            .where(
                LoanInstalment.business_id == business_id,
                LoanInstalment.closed.is_(False),
                LoanSchedule.state == "current",
                LoanInstalment.due_on <= last,
            )
        ):
            agreement = agreements.get(row.agreement_fk)
            if agreement is None or row.owed <= row.paid:
                continue
            lending = agreement.direction == LoanDirection.LENDING.value
            lines.append(
                CommitmentLine(
                    due_on=max(row.due_on, first) if row.due_on < first else row.due_on,
                    inward=lending,
                    kind=DebtKind.LOAN_ISSUED if lending else DebtKind.BORROWING,
                    reference=f"{agreement.number} #{row.sequence}"
                    if row.sequence
                    else agreement.number,
                    counterparty=agreement.counterparty_name,
                    amount=row.owed - row.paid,
                    source_type="loan_agreement",
                    source_id=agreement.id,
                )
            )
        for owed_to_us in (True, False):
            for line in self.register(actor, owed_to_us=owed_to_us, on=first):
                if line.source_type == "loan_agreement" or line.due_on is None:
                    continue
                if line.due_on > last:
                    continue
                lines.append(
                    CommitmentLine(
                        due_on=max(line.due_on, first),
                        inward=owed_to_us,
                        kind=line.kind,
                        reference=line.reference,
                        counterparty=line.counterparty,
                        amount=line.outstanding,
                        source_type=line.source_type,
                        source_id=line.source_id,
                    )
                )
        return sorted(lines, key=lambda line: (line.due_on, not line.inward, line.reference))

    def maturity(self, actor: AnyActor, *, on: date | None = None) -> list[MaturityRow]:
        """Principal still to be repaid, by when it falls due, borrowed and lent apart."""
        _require_view(actor)
        business_id = _business_of(actor)
        today = on or self._today(business_id)
        totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
        for agreement in self._db.scalars(
            select(LoanAgreement).where(
                LoanAgreement.business_id == business_id,
                LoanAgreement.state == AgreementState.ACTIVE.value,
            )
        ):
            for row in self._loans.open_instalments(agreement):
                left = row.principal - row.paid_principal
                if left > ZERO:
                    totals[(agreement.direction, maturity_band(row.due_on, today))] += left
        return [
            MaturityRow(
                code=code,
                label=label,
                borrowed=totals[(LoanDirection.BORROWING.value, code)],
                lent=totals[(LoanDirection.LENDING.value, code)],
            )
            for code, label, _, _ in MATURITY_BANDS
        ]

    def cash_flow(self, actor: AnyActor, *, start: date, end: date) -> list[CashClass]:
        """Cash in and out, classed by the account on the other side, so loan principal,
        interest, trade collections, supplier payments and transfers never mix (AC-29)."""
        actor.require_any("report.view", "loan.view", "receivable.view")
        business_id = _business_of(actor)
        if end < start:
            raise ConflictError("A reporting window cannot end before it begins.")
        rows = self._db.execute(
            select(
                JournalLine.entry_fk,
                Account.code,
                Account.cash_flow_category,
                JournalLine.base_debit,
                JournalLine.base_credit,
            )
            .join(Account, Account.id == JournalLine.account_fk)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state == "posted",
                JournalLine.posting_date >= start,
                JournalLine.posting_date <= end,
            )
        ).all()
        entries: dict[uuid.UUID, list[tuple[str, str, Decimal]]] = defaultdict(list)
        for entry, code, category, debit, credit in rows:
            entries[entry].append((code, category, debit - credit))

        lookup = {code: key for key, _, _, codes in CASH_CLASSES for code in codes}
        inflow: dict[str, Decimal] = defaultdict(lambda: ZERO)
        outflow: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for lines in entries.values():
            cash = [
                amount for _, category, amount in lines if category == CashFlowCategory.CASH.value
            ]
            if not cash:
                continue
            others = [
                (code, amount)
                for code, category, amount in lines
                if category != CashFlowCategory.CASH.value
            ]
            if not others:
                # Only cash accounts: money moved between the business's own accounts.
                moved = sum((amount for amount in cash if amount > ZERO), ZERO)
                inflow["transfers"] += moved
                outflow["transfers"] += moved
                continue
            weight = sum((abs(amount) for _, amount in others), ZERO)
            if weight == ZERO:
                continue
            net_cash = sum(cash, ZERO)
            for code, amount in others:
                share = money(net_cash * abs(amount) / weight)
                key = lookup.get(code) or _class_of(code)
                if share > ZERO:
                    inflow[key] += share
                elif share < ZERO:
                    outflow[key] += -share
        labels = {key: (label, category) for key, label, category, _ in CASH_CLASSES}
        labels.update(
            {
                "operating_other": ("Other operating receipts and payments", "operating"),
                "investing_other": ("Other investing", "investing"),
                "financing_other": ("Other financing", "financing"),
                "transfers": ("Transfers between the business's own accounts", "transfer"),
            }
        )
        order = [key for key, *_ in CASH_CLASSES] + [
            "operating_other",
            "investing_other",
            "financing_other",
            "transfers",
        ]
        return [
            CashClass(
                code=key,
                label=labels[key][0],
                category=labels[key][1],
                inflow=money(inflow[key]),
                outflow=money(outflow[key]),
            )
            for key in order
            if inflow[key] or outflow[key]
        ]

    def _debt_service(self, business_id: uuid.UUID, today: date, horizon: date) -> Decimal:
        rows = self._db.execute(
            select(LoanInstalment, LoanAgreement.direction)
            .join(LoanSchedule, LoanSchedule.id == LoanInstalment.schedule_fk)
            .join(LoanAgreement, LoanAgreement.id == LoanInstalment.agreement_fk)
            .where(
                LoanInstalment.business_id == business_id,
                LoanInstalment.closed.is_(False),
                LoanSchedule.state == "current",
                LoanAgreement.state == AgreementState.ACTIVE.value,
                LoanAgreement.direction == LoanDirection.BORROWING.value,
                LoanInstalment.due_on <= horizon,
            )
        ).all()
        return sum((row[0].owed - row[0].paid for row in rows), ZERO)

    def _cash(self, business_id: uuid.UUID, today: date) -> Decimal:
        rows = self._db.execute(
            select(func.sum(JournalLine.base_debit), func.sum(JournalLine.base_credit))
            .join(Account, Account.id == JournalLine.account_fk)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state == "posted",
                JournalLine.posting_date <= today,
                Account.cash_flow_category == CashFlowCategory.CASH.value,
            )
        ).one()
        return money(Decimal(rows[0] or 0) - Decimal(rows[1] or 0))

    def _collected(self, business_id: uuid.UUID, start: date, end: date) -> Decimal:
        receipts = self._db.scalar(
            select(func.sum(CustomerReceipt.amount)).where(
                CustomerReceipt.business_id == business_id,
                CustomerReceipt.received_on >= start,
                CustomerReceipt.received_on <= end,
            )
        )
        return money(
            Decimal(receipts or 0) + self._loan_flow(business_id, LoanDirection.LENDING, start, end)
        )

    def _repaid(self, business_id: uuid.UUID, start: date, end: date) -> Decimal:
        payments = self._db.scalar(
            select(func.sum(SupplierPayment.amount)).where(
                SupplierPayment.business_id == business_id,
                SupplierPayment.paid_on >= start,
                SupplierPayment.paid_on <= end,
            )
        )
        expenses = self._db.scalar(
            select(func.sum(Expense.total)).where(
                Expense.business_id == business_id,
                Expense.settlement != ExpenseSettlement.PAID.value,
                Expense.state == ExpenseState.SETTLED.value,
                Expense.settled_on >= start,
                Expense.settled_on <= end,
            )
        )
        return money(
            Decimal(payments or 0)
            + Decimal(expenses or 0)
            + self._loan_flow(business_id, LoanDirection.BORROWING, start, end)
        )

    def _loan_flow(
        self, business_id: uuid.UUID, direction: LoanDirection, start: date, end: date
    ) -> Decimal:
        total = self._db.scalar(
            select(func.sum(LoanTransaction.amount))
            .join(LoanAgreement, LoanAgreement.id == LoanTransaction.agreement_fk)
            .where(
                LoanTransaction.business_id == business_id,
                LoanAgreement.direction == direction.value,
                LoanTransaction.kind.in_([TransactionKind.REPAYMENT, TransactionKind.SETTLEMENT]),
                LoanTransaction.occurred_on >= start,
                LoanTransaction.occurred_on <= end,
            )
        )
        return Decimal(total or 0)

    def _trend(self, business_id: uuid.UUID, end: date) -> list[TrendPoint]:
        """Six months: what was collected and settled, and loan balances at each month end."""
        points: list[TrendPoint] = []
        year, month = end.year, end.month
        months: list[tuple[date, date]] = []
        for _ in range(6):
            first = date(year, month, 1)
            last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
            months.append((first, min(last, end)))
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        for first, last in reversed(months):
            points.append(
                TrendPoint(
                    month=first.strftime("%Y-%m"),
                    collected=self._collected(business_id, first, last),
                    settled=self._repaid(business_id, first, last),
                    borrowed=self._balance(business_id, "23000", last, credit_normal=True),
                    lent=self._balance(business_id, "11200", last, credit_normal=False),
                )
            )
        return points

    def _balance(
        self, business_id: uuid.UUID, code: str, on: date, *, credit_normal: bool
    ) -> Decimal:
        row = self._db.execute(
            select(func.sum(JournalLine.base_debit), func.sum(JournalLine.base_credit))
            .join(Account, Account.id == JournalLine.account_fk)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state == "posted",
                JournalLine.posting_date <= on,
                Account.code == code,
            )
        ).one()
        debit, credit = Decimal(row[0] or 0), Decimal(row[1] or 0)
        return money(credit - debit if credit_normal else debit - credit)

    # -- other debts ------------------------------------------------------------------------

    def record_other(self, actor: AnyActor, draft: OtherDebtDraft) -> OtherDebt:
        """An amount owed that no source document records: a refundable deposit paid or
        held, an advance to a supplier. Refused if a source already records it.

        Owed to us: Dr other receivables, Cr the account the money left. Owed by us: Dr the
        account the money arrived in, Cr amounts held.
        """
        actor.require("loan.manage")
        business_id = _business_of(actor)
        name = " ".join((draft.counterparty_name or "").split())
        if not name:
            raise ValidationError("Name who owes or is owed.", field="counterparty_name")
        description = (draft.description or "").strip()
        if not description:
            raise ValidationError("Say what the amount is.", field="description")
        amount = money(draft.amount)
        if amount <= ZERO:
            raise ValidationError("An amount above zero.", field="amount")
        reference = (draft.reference or "").strip() or None
        self._refuse_duplicate(business_id, name, reference, amount, draft.owed_to_us)
        if draft.financial_account_id is None:
            raise ValidationError(
                "Name the account the money moved through.", field="financial_account_id"
            )
        account = self._treasury.require(actor, draft.financial_account_id)
        issued = draft.issued_on or self._today(business_id)
        number = (
            OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "other_debt", branch_id=actor.branch_id, on=issued)
            .number
        )
        debt = OtherDebt(
            business_id=business_id,
            number=number,
            owed_to_us=draft.owed_to_us,
            counterparty_name=name,
            counterparty_party_fk=draft.counterparty_party_id,
            description=description,
            reference=reference,
            original=amount,
            issued_on=issued,
            due_on=draft.due_on,
            responsible_id=draft.responsible_id,
            financial_account_fk=account.id,
            attachment_fk=draft.attachment_id,
        )
        self._db.add(debt)
        self._db.flush()
        cash = self._treasury.ledger_code(account)
        lines = (
            [
                EntryLine(OTHER_RECEIVABLES, Side.DEBIT, amount, f"{name}: {description}"[:200]),
                EntryLine(cash, Side.CREDIT, amount, reference or debt.number),
            ]
            if draft.owed_to_us
            else [
                EntryLine(cash, Side.DEBIT, amount, reference or debt.number),
                EntryLine(AMOUNTS_HELD, Side.CREDIT, amount, f"{name}: {description}"[:200]),
            ]
        )
        debt.journal_entry_fk = self._post(actor, debt, lines, issued, f"other_debt:{debt.id}")
        self._audit.record(
            actor,
            "other_debt_recorded",
            entity_type="other_debt",
            entity_id=debt.id,
            entity_label=debt.number,
            after={"owed_to_us": draft.owed_to_us, "counterparty": name, "amount": str(amount)},
        )
        return debt

    def settle_other(
        self,
        actor: AnyActor,
        debt_id: uuid.UUID,
        *,
        amount: Decimal,
        financial_account_id: uuid.UUID,
        on: date | None = None,
        reference: str | None = None,
    ) -> OtherDebtSettlement:
        actor.require("repayment.manage")
        debt = self.require_other(actor, debt_id)
        if debt.written_off:
            raise ConflictError(f"{debt.number} has been written off.")
        value = money(amount)
        left = debt.original - debt.settled
        if value <= ZERO or value > left:
            raise ValidationError(f"Between 0 and the {left} still owed.", field="amount")
        account = self._treasury.require(actor, financial_account_id)
        when = on or self._today(debt.business_id)
        row = OtherDebtSettlement(
            business_id=debt.business_id,
            debt_fk=debt.id,
            occurred_on=when,
            amount=value,
            financial_account_fk=account.id,
            reference=(reference or "").strip() or None,
            recorded_by_id=actor.user_id,
        )
        self._db.add(row)
        self._db.flush()
        cash = self._treasury.ledger_code(account)
        label = f"{debt.number}, {debt.counterparty_name}"
        lines = (
            [
                EntryLine(cash, Side.DEBIT, value, reference or debt.number),
                EntryLine(OTHER_RECEIVABLES, Side.CREDIT, value, label),
            ]
            if debt.owed_to_us
            else [
                EntryLine(AMOUNTS_HELD, Side.DEBIT, value, label),
                EntryLine(cash, Side.CREDIT, value, reference or debt.number),
            ]
        )
        row.journal_entry_fk = self._post(actor, debt, lines, when, f"other_debt_settled:{row.id}")
        debt.settled += value
        self._db.flush()
        self._audit.record(
            actor,
            "other_debt_settled",
            entity_type="other_debt",
            entity_id=debt.id,
            entity_label=debt.number,
            after={"amount": str(value), "left": str(debt.original - debt.settled)},
        )
        return row

    def require_other(self, actor: AnyActor, debt_id: uuid.UUID) -> OtherDebt:
        _require_view(actor)
        debt = self._db.get(OtherDebt, debt_id)
        if debt is None or debt.business_id != actor.business_id:
            raise NotFoundError("Debt not found.")
        return debt

    def _refuse_duplicate(
        self,
        business_id: uuid.UUID,
        name: str,
        reference: str | None,
        amount: Decimal,
        owed_to_us: bool,
    ) -> None:
        """AC-30: a debt a source document records is in the register through it already."""
        if reference:
            needle = reference.casefold()
            sources: Iterable[tuple[str, str | None]] = [
                *(
                    ("customer invoice", number)
                    for number in self._db.scalars(
                        select(SalesInvoice.number).where(SalesInvoice.business_id == business_id)
                    )
                ),
                *(
                    ("supplier invoice", reference_)
                    for reference_ in self._db.scalars(
                        select(SupplierInvoice.supplier_reference).where(
                            SupplierInvoice.business_id == business_id
                        )
                    )
                ),
                *(
                    ("supplier invoice", number)
                    for number in self._db.scalars(
                        select(SupplierInvoice.number).where(
                            SupplierInvoice.business_id == business_id
                        )
                    )
                ),
                *(
                    ("expense", number)
                    for number in self._db.scalars(
                        select(Expense.number).where(Expense.business_id == business_id)
                    )
                ),
                *(
                    ("expense", payee_reference)
                    for payee_reference in self._db.scalars(
                        select(Expense.payee_reference).where(Expense.business_id == business_id)
                    )
                ),
                *(
                    ("agreement", number)
                    for number in self._db.scalars(
                        select(LoanAgreement.number).where(LoanAgreement.business_id == business_id)
                    )
                ),
                *(
                    ("agreement", agreement_reference)
                    for agreement_reference in self._db.scalars(
                        select(LoanAgreement.agreement_reference).where(
                            LoanAgreement.business_id == business_id
                        )
                    )
                ),
            ]
            for what, value in sources:
                if value and value.casefold() == needle:
                    raise ConflictError(
                        f"{reference} is already in the register as a {what}. A debt is "
                        "recorded once, by the document that creates it."
                    )
        twin = self._db.scalar(
            select(OtherDebt).where(
                OtherDebt.business_id == business_id,
                OtherDebt.owed_to_us.is_(owed_to_us),
                func.lower(OtherDebt.counterparty_name) == name.casefold(),
                OtherDebt.original == amount,
                OtherDebt.written_off.is_(False),
                (OtherDebt.reference == reference) if reference else OtherDebt.reference.is_(None),
            )
        )
        if twin is not None and twin.settled < twin.original:
            raise ConflictError(
                f"{twin.number} already records {amount} for {twin.counterparty_name}."
            )

    def _post(
        self,
        actor: AnyActor,
        debt: OtherDebt,
        lines: list[EntryLine],
        on: date,
        key: str,
    ) -> uuid.UUID:
        business = self._db.get(Business, debt.business_id)
        return (
            JournalService(self._db, clock=self._clock)
            .post(
                actor,
                JournalDraft(
                    posting_date=on,
                    narration=f"{debt.number}: {debt.counterparty_name}, {debt.description}"[:240],
                    lines=lines,
                    source=JournalSource.FINANCING,
                    source_reference=debt.number,
                    source_id=debt.id,
                    idempotency_key=key,
                    currency_code=business.base_currency if business else None,
                    branch_id=actor.branch_id,
                ),
            )
            .entry.id
        )

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")

    def responsible_names(self, business_id: uuid.UUID) -> dict[uuid.UUID, str]:
        return {
            row[0]: row[1]
            for row in self._db.execute(
                select(User.id, User.full_name).where(User.business_id == business_id)
            ).all()
        }


def _class_of(code: str) -> str:
    if code.startswith(("15", "16")):
        return "investing_other"
    if code.startswith(("23", "3")):
        return "financing_other"
    return "operating_other"


def _total(lines: list[DebtLine]) -> Decimal:
    return sum((line.outstanding for line in lines), ZERO)


def _by_kind(lines: list[DebtLine]) -> list[Split]:
    totals: dict[DebtKind, tuple[Decimal, int]] = {}
    for line in lines:
        amount, count = totals.get(line.kind, (ZERO, 0))
        totals[line.kind] = (amount + line.outstanding, count + 1)
    return [
        Split(kind.value, kind.label, amount, count)
        for kind, (amount, count) in sorted(totals.items(), key=lambda item: -item[1][0])
    ]


def _ageing(lines: list[DebtLine]) -> list[Split]:
    totals: dict[str, tuple[Decimal, int]] = defaultdict(lambda: (ZERO, 0))
    for line in lines:
        amount, count = totals[line.bucket]
        totals[line.bucket] = (amount + line.outstanding, count + 1)
    return [
        Split(code, label, totals[code][0], totals[code][1]) for code, label, _, _ in AGEING_BUCKETS
    ]


def _largest(lines: list[DebtLine], limit: int = 5) -> list[Counterparty]:
    totals: dict[str, tuple[Decimal, int]] = defaultdict(lambda: (ZERO, 0))
    for line in lines:
        amount, count = totals[line.counterparty]
        totals[line.counterparty] = (amount + line.outstanding, count + 1)
    ranked = sorted(totals.items(), key=lambda item: (-item[1][0], item[0]))[:limit]
    return [Counterparty(name, amount, count) for name, (amount, count) in ranked]


def _assert_unique(lines: list[DebtLine]) -> None:
    """A source appears once. If two lines share one, the register is wrong, and showing a
    total built on it would be worse than refusing to show one."""
    seen: set[tuple[str, uuid.UUID]] = set()
    for line in lines:
        key = (line.source_type, line.source_id)
        if key in seen:
            raise ConflictError(f"{line.reference} appears twice in the register.")
        seen.add(key)


def _require_view(actor: AnyActor) -> None:
    actor.require_any("loan.view", "receivable.view", "journal.view", "report.view")


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
