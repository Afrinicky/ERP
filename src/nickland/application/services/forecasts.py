"""The rolling cash forecast, its scenarios, and the alerts that watch the business.
PRD §29.1, §29.6.

A forecast begins from the cash and bank balance the ledger holds today, then lays out
what the records say will come in and go out: customer invoices by their due dates (or a
customer's promised date), loan instalments, supplier invoices, unpaid expenses, recurring
costs, tax obligations, commissions, and whatever has been planned by hand. A scenario
moves those dates and amounts by stated assumptions. Every figure it returns is a
forecast and says so; none is ever posted.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.finance import ExpenseSettlement, ExpenseState, Frequency, advance
from nickland.domain.financing import AgreementState, LoanDirection
from nickland.domain.ledger import CashFlowCategory
from nickland.domain.money import ZERO, money
from nickland.domain.orders import PAYABLE_STATES
from nickland.domain.planning import (
    PRESET_ASSUMPTIONS,
    Assumptions,
    Basis,
    BudgetState,
    ForecastClass,
    PlannedFlow,
    ScenarioKind,
    bucket_size,
    cents,
    shift,
)
from nickland.domain.pricing import CommissionState
from nickland.persistence.catalogue_models import Party
from nickland.persistence.finance_models import (
    BankStatement,
    CollectionNote,
    Expense,
    RecurringExpense,
    SalesInvoice,
)
from nickland.persistence.financing_models import LoanAgreement, LoanInstalment, LoanSchedule
from nickland.persistence.ledger_models import Account, JournalLine
from nickland.persistence.models import Business, Setting
from nickland.persistence.order_models import SupplierInvoice
from nickland.persistence.planning_models import Budget, ForecastScenario, PlannedCashItem
from nickland.persistence.pricing_models import CommissionAccrual
from nickland.persistence.tax_models import TaxObligation
from nickland.platform.clock import Clock, SystemClock, business_date

THRESHOLD_KEY = "forecast.low_cash_threshold"


@dataclass(frozen=True, slots=True)
class FlowItem:
    on: date
    cls: ForecastClass
    amount: Decimal
    reference: str
    counterparty: str | None
    source_type: str
    source_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ForecastRow:
    starts_on: date
    ends_on: date
    opening: Decimal
    inflow: Decimal
    outflow: Decimal
    closing: Decimal
    by_class: dict[str, Decimal]
    below_threshold: bool


@dataclass(frozen=True, slots=True)
class Forecast:
    basis: Basis
    scenario: str
    scenario_kind: str
    assumptions: dict[str, object]
    starts_on: date
    ends_on: date
    opening_cash: Decimal
    closing_cash: Decimal
    lowest: Decimal
    lowest_on: date
    threshold: Decimal | None
    bucket_days: int
    rows: list[ForecastRow]
    totals: dict[str, Decimal]
    items: list[FlowItem]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class Alert:
    severity: str
    """``critical``, ``warning`` or ``notice``."""
    kind: str
    title: str
    detail: str
    amount: Decimal | None
    count: int
    view: str
    """Where it opens: a screen the interface knows."""


class ForecastService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- scenarios --------------------------------------------------------------------------

    def scenarios(self, actor: AnyActor) -> list[ForecastScenario]:
        _require_view(actor)
        business_id = _business_of(actor)
        self._ensure_presets(business_id)
        return list(
            self._db.scalars(
                select(ForecastScenario)
                .where(ForecastScenario.business_id == business_id)
                .order_by(ForecastScenario.created_at)
            )
        )

    def save_scenario(
        self,
        actor: AnyActor,
        *,
        name: str,
        assumptions: Assumptions,
        scenario_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> ForecastScenario:
        actor.require("budget.manage")
        business_id = _business_of(actor)
        assumptions.validate()
        label = " ".join((name or "").split())
        if not label:
            raise ValidationError("Name the scenario.", field="name")
        if scenario_id is None:
            row = ForecastScenario(
                business_id=business_id,
                name=label,
                kind=ScenarioKind.CUSTOM.value,
                assumptions=assumptions.as_record(),
                notes=(notes or "").strip() or None,
            )
            self._db.add(row)
        else:
            row = self._scenario(business_id, scenario_id)
            row.name = label
            row.assumptions = assumptions.as_record()
            row.notes = (notes or "").strip() or None
        self._db.flush()
        self._audit.record(
            actor,
            "forecast_scenario_saved",
            entity_type="forecast_scenario",
            entity_id=row.id,
            entity_label=label,
            after=row.assumptions,
        )
        return row

    def planned(self, actor: AnyActor) -> list[PlannedCashItem]:
        _require_view(actor)
        return list(
            self._db.scalars(
                select(PlannedCashItem)
                .where(PlannedCashItem.business_id == actor.business_id)
                .order_by(PlannedCashItem.expected_on)
            )
        )

    def plan(
        self,
        actor: AnyActor,
        *,
        expected_on: date,
        amount: Decimal,
        inward: bool,
        label: str,
        scenario_id: uuid.UUID | None = None,
    ) -> PlannedCashItem:
        actor.require("budget.manage")
        business_id = _business_of(actor)
        value = cents(money(amount))
        if value <= ZERO:
            raise ValidationError("An amount above zero.", field="amount")
        text = " ".join((label or "").split())
        if not text:
            raise ValidationError("Say what it is.", field="label")
        if scenario_id is not None:
            self._scenario(business_id, scenario_id)
        row = PlannedCashItem(
            business_id=business_id,
            expected_on=expected_on,
            amount=value,
            inward=inward,
            label=text,
            scenario_fk=scenario_id,
            created_by_id=actor.user_id,
        )
        self._db.add(row)
        self._db.flush()
        return row

    def unplan(self, actor: AnyActor, item_id: uuid.UUID) -> None:
        actor.require("budget.manage")
        row = self._db.get(PlannedCashItem, item_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Planned item not found.")
        self._db.delete(row)
        self._db.flush()

    def threshold(self, business_id: uuid.UUID) -> Decimal | None:
        stored = self._db.scalar(
            select(Setting).where(Setting.business_id == business_id, Setting.key == THRESHOLD_KEY)
        )
        return money(str(stored.value)) if stored and stored.value is not None else None

    def set_threshold(self, actor: AnyActor, amount: Decimal | None) -> Decimal | None:
        actor.require("budget.manage")
        business_id = _business_of(actor)
        if amount is not None and money(amount) < ZERO:
            raise ValidationError("A threshold is not negative.", field="amount")
        stored = self._db.scalar(
            select(Setting).where(Setting.business_id == business_id, Setting.key == THRESHOLD_KEY)
        )
        value = str(money(amount)) if amount is not None else None
        if stored is None:
            self._db.add(
                Setting(
                    business_id=business_id,
                    key=THRESHOLD_KEY,
                    value=value,
                    description="Warn when forecast cash falls below this.",
                )
            )
        else:
            stored.value = value
        self._db.flush()
        self._audit.record(
            actor,
            "low_cash_threshold_amended",
            entity_type="setting",
            entity_label=THRESHOLD_KEY,
            after={"value": value},
        )
        return self.threshold(business_id)

    # -- the forecast -----------------------------------------------------------------------

    def forecast(
        self,
        actor: AnyActor,
        *,
        horizon_days: int = 30,
        scenario_id: uuid.UUID | None = None,
        assumptions: Assumptions | None = None,
        start: date | None = None,
    ) -> Forecast:
        _require_view(actor)
        business_id = _business_of(actor)
        if not 1 <= horizon_days <= 366:
            raise ValidationError("A horizon of 1 to 366 days.", field="horizon_days")
        self._ensure_presets(business_id)
        scenario: ForecastScenario | None
        if scenario_id is not None:
            scenario = self._scenario(business_id, scenario_id)
        else:
            scenario = self._db.scalar(
                select(ForecastScenario).where(
                    ForecastScenario.business_id == business_id,
                    ForecastScenario.is_default.is_(True),
                )
            )
        assert scenario is not None
        terms = assumptions or Assumptions.from_record(scenario.assumptions)
        terms.validate()
        first = start or self._today(business_id)
        last = first + timedelta(days=horizon_days - 1)

        items = self._items(business_id, first, last, terms, scenario.id)
        opening = self._cash(business_id, first)
        threshold = self.threshold(business_id)
        size = bucket_size(horizon_days)
        rows: list[ForecastRow] = []
        running = opening
        lowest, lowest_on = opening, first
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        day = first
        while day <= last:
            end = min(day + timedelta(days=size - 1), last)
            inside = [item for item in items if day <= item.on <= end]
            by_class: dict[str, Decimal] = defaultdict(lambda: ZERO)
            inflow = outflow = ZERO
            for item in inside:
                by_class[item.cls.value] += item.amount
                totals[item.cls.value] += item.amount
                if item.cls.inward:
                    inflow += item.amount
                else:
                    outflow += item.amount
            closing = cents(running + inflow - outflow)
            rows.append(
                ForecastRow(
                    starts_on=day,
                    ends_on=end,
                    opening=cents(running),
                    inflow=cents(inflow),
                    outflow=cents(outflow),
                    closing=closing,
                    by_class={key: cents(value) for key, value in by_class.items()},
                    below_threshold=threshold is not None and closing < threshold,
                )
            )
            if closing < lowest:
                lowest, lowest_on = closing, end
            running = closing
            day = end + timedelta(days=1)

        warnings: list[str] = []
        if lowest < ZERO:
            warnings.append(
                f"Cash is forecast to run out: {cents(lowest):,.2f} by {lowest_on:%d %b %Y}."
            )
        elif threshold is not None and lowest < threshold:
            warnings.append(
                f"Cash is forecast to fall to {cents(lowest):,.2f} by {lowest_on:%d %b %Y}, "
                f"below the {threshold:,.2f} the business keeps in hand."
            )
        return Forecast(
            basis=Basis.FORECAST,
            scenario=scenario.name,
            scenario_kind=scenario.kind,
            assumptions=terms.as_record(),
            starts_on=first,
            ends_on=last,
            opening_cash=cents(opening),
            closing_cash=cents(running),
            lowest=cents(lowest),
            lowest_on=lowest_on,
            threshold=threshold,
            bucket_days=size,
            rows=rows,
            totals={key: cents(value) for key, value in totals.items()},
            items=sorted(items, key=lambda item: (item.on, item.cls.value, item.reference)),
            warnings=warnings,
        )

    def _items(
        self,
        business_id: uuid.UUID,
        first: date,
        last: date,
        terms: Assumptions,
        scenario_id: uuid.UUID,
    ) -> list[FlowItem]:
        names = {
            row[0]: row[1]
            for row in self._db.execute(
                select(Party.id, Party.name).where(Party.business_id == business_id)
            ).all()
        }
        items: list[FlowItem] = []
        rate = terms.collection_rate / Decimal(100)

        def collect(on: date, cls: ForecastClass, amount: Decimal, **detail: object) -> None:
            if amount > ZERO and on <= last:
                items.append(FlowItem(on=on, cls=cls, amount=cents(amount), **detail))  # type: ignore[arg-type]

        promises = {
            row.invoice_fk: row
            for row in self._db.scalars(
                select(CollectionNote)
                .where(
                    CollectionNote.business_id == business_id,
                    CollectionNote.kind == "promise",
                    CollectionNote.promised_on.is_not(None),
                )
                .order_by(CollectionNote.recorded_at)
            )
            if row.invoice_fk is not None
        }
        for invoice in self._db.scalars(
            select(SalesInvoice).where(
                SalesInvoice.business_id == business_id,
                SalesInvoice.state.in_(["open", "part_settled"]),
            )
        ):
            owed = invoice.outstanding
            if owed <= ZERO:
                continue
            promise = promises.get(invoice.id)
            expected = (
                promise.promised_on
                if promise is not None and promise.promised_on and promise.promised_on >= first
                else invoice.due_on
            )
            assert expected is not None
            collect(
                shift(expected, terms.collection_delay_days, first),
                ForecastClass.COLLECTIONS,
                owed * rate,
                reference=invoice.number,
                counterparty=names.get(invoice.customer_fk),
                source_type="sales_invoice",
                source_id=invoice.id,
            )

        agreements = {
            row.id: row
            for row in self._db.scalars(
                select(LoanAgreement).where(
                    LoanAgreement.business_id == business_id,
                    LoanAgreement.state == AgreementState.ACTIVE.value,
                )
            )
        }
        settle_id = uuid.UUID(terms.settle_loan_id) if terms.settle_loan_id else None
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
            if settle_id == agreement.id and terms.settle_on and row.due_on >= terms.settle_on:
                continue
            lending = agreement.direction == LoanDirection.LENDING.value
            on = (
                shift(row.due_on, terms.collection_delay_days, first)
                if lending
                else shift(row.due_on, terms.payment_delay_days, first)
            )
            collect(
                on,
                ForecastClass.LOAN_RECEIPTS if lending else ForecastClass.LOAN_REPAYMENTS,
                row.owed - row.paid,
                reference=f"{agreement.number} #{row.sequence}",
                counterparty=agreement.counterparty_name,
                source_type="loan_agreement",
                source_id=agreement.id,
            )
        if settle_id is not None and terms.settle_on is not None:
            agreement = agreements.get(settle_id)
            if agreement is None:
                raise NotFoundError("The agreement to settle is not active.")
            from nickland.application.services.loans import LoanService

            loans = LoanService(self._db, clock=self._clock)
            quote = loans.quote_for(agreement, terms.settle_on)
            lending = agreement.direction == LoanDirection.LENDING.value
            collect(
                max(terms.settle_on, first),
                ForecastClass.LOAN_RECEIPTS if lending else ForecastClass.LOAN_REPAYMENTS,
                quote.total,
                reference=f"{agreement.number}, early settlement",
                counterparty=agreement.counterparty_name,
                source_type="loan_agreement",
                source_id=agreement.id,
            )

        for bill in self._db.scalars(
            select(SupplierInvoice).where(
                SupplierInvoice.business_id == business_id,
                SupplierInvoice.state.in_([state.value for state in PAYABLE_STATES]),
            )
        ):
            owed = bill.total - bill.amount_paid - bill.amount_credited
            if owed <= ZERO or (bill.due_on < first and not terms.include_overdue_payables):
                continue
            collect(
                shift(bill.due_on, terms.payment_delay_days, first),
                ForecastClass.SUPPLIER_PAYMENTS,
                owed,
                reference=bill.number or bill.supplier_reference,
                counterparty=names.get(bill.supplier_fk),
                source_type="supplier_invoice",
                source_id=bill.id,
            )
        for expense in self._db.scalars(
            select(Expense).where(
                Expense.business_id == business_id,
                Expense.state == ExpenseState.RECOGNISED.value,
                Expense.settlement != ExpenseSettlement.PAID.value,
            )
        ):
            collect(
                shift(first, terms.payment_delay_days, first),
                ForecastClass.EXPENSES,
                expense.total,
                reference=expense.number,
                counterparty=expense.payee_name,
                source_type="expense",
                source_id=expense.id,
            )
        if terms.include_recurring:
            for recurring in self._db.scalars(
                select(RecurringExpense).where(
                    RecurringExpense.business_id == business_id,
                    RecurringExpense.is_active.is_(True),
                )
            ):
                due = recurring.next_due
                guard = 0
                while due <= last and guard < 400:
                    guard += 1
                    if recurring.ends_on is not None and due > recurring.ends_on:
                        break
                    if due >= first:
                        collect(
                            shift(due, terms.payment_delay_days, first),
                            ForecastClass.RECURRING,
                            recurring.net + recurring.input_tax,
                            reference=recurring.name,
                            counterparty=recurring.payee_name,
                            source_type="recurring_expense",
                            source_id=recurring.id,
                        )
                    due = advance(due, Frequency(recurring.frequency))
        for obligation in self._db.scalars(
            select(TaxObligation).where(
                TaxObligation.business_id == business_id,
                TaxObligation.state.in_(["outstanding", "part_paid"]),
            )
        ):
            owed = money(
                obligation.assessed
                - obligation.recoverable
                - obligation.withheld
                + obligation.adjustments
                - obligation.paid
            )
            collect(
                shift(obligation.due_on, 0, first),
                ForecastClass.TAX,
                owed,
                reference=obligation.label,
                counterparty=None,
                source_type="tax_obligation",
                source_id=obligation.id,
            )
        for accrual in self._db.scalars(
            select(CommissionAccrual).where(
                CommissionAccrual.business_id == business_id,
                CommissionAccrual.state == CommissionState.PAYABLE.value,
            )
        ):
            collect(
                shift(first, terms.payment_delay_days, first),
                ForecastClass.COMMISSIONS,
                accrual.entitlement - accrual.settled_amount,
                reference=accrual.source_reference or "Commission",
                counterparty=names.get(accrual.affiliate_fk),
                source_type="commission",
                source_id=accrual.id,
            )
        for planned in self._db.scalars(
            select(PlannedCashItem).where(
                PlannedCashItem.business_id == business_id,
                PlannedCashItem.expected_on <= last,
                (PlannedCashItem.scenario_fk.is_(None))
                | (PlannedCashItem.scenario_fk == scenario_id),
            )
        ):
            collect(
                max(planned.expected_on, first),
                ForecastClass.PLANNED_IN if planned.inward else ForecastClass.PLANNED_OUT,
                planned.amount,
                reference=planned.label,
                counterparty=None,
                source_type="planned",
                source_id=planned.id,
            )
        for flow in terms.extra_flows:
            collect(
                max(flow.on, first),
                ForecastClass.PLANNED_IN if flow.inward else ForecastClass.PLANNED_OUT,
                flow.amount,
                reference=flow.label,
                counterparty=None,
                source_type="what_if",
                source_id=None,
            )
        return items

    # -- alerts -----------------------------------------------------------------------------

    def alerts(self, actor: AnyActor) -> list[Alert]:
        """What needs attention today, most pressing first."""
        _require_view(actor)
        business_id = _business_of(actor)
        today = self._today(business_id)
        soon = today + timedelta(days=14)
        alerts: list[Alert] = []
        outlook = self.forecast(actor, horizon_days=30)
        if outlook.warnings:
            alerts.append(
                Alert(
                    "critical" if outlook.lowest < ZERO else "warning",
                    "low_cash",
                    "Cash is forecast to run low",
                    outlook.warnings[0],
                    outlook.lowest,
                    1,
                    "forecast",
                )
            )

        def add(
            severity: str, kind: str, title: str, rows: list[Decimal], view: str, detail: str
        ) -> None:
            if rows:
                alerts.append(
                    Alert(severity, kind, title, detail, cents(sum(rows, ZERO)), len(rows), view)
                )

        overdue_invoices = [
            invoice.outstanding
            for invoice in self._db.scalars(
                select(SalesInvoice).where(
                    SalesInvoice.business_id == business_id,
                    SalesInvoice.state.in_(["open", "part_settled"]),
                    SalesInvoice.due_on < today,
                )
            )
            if invoice.outstanding > ZERO
        ]
        add(
            "warning",
            "receivables_overdue",
            "Customer invoices overdue",
            overdue_invoices,
            "receivables",
            "Past their due date and unpaid.",
        )
        broken = [
            row.promised_amount or ZERO
            for row in self._db.scalars(
                select(CollectionNote).where(
                    CollectionNote.business_id == business_id,
                    CollectionNote.kind == "promise",
                    CollectionNote.promised_on < today,
                    CollectionNote.promised_on >= today - timedelta(days=30),
                )
            )
            if row.invoice_fk is not None
            and (invoice := self._db.get(SalesInvoice, row.invoice_fk)) is not None
            and invoice.outstanding > ZERO
        ]
        add(
            "warning",
            "promises_missed",
            "Payment promises missed",
            broken,
            "receivables",
            "A customer promised payment by a date now past.",
        )
        payables = [
            invoice.total - invoice.amount_paid - invoice.amount_credited
            for invoice in self._db.scalars(
                select(SupplierInvoice).where(
                    SupplierInvoice.business_id == business_id,
                    SupplierInvoice.state.in_([state.value for state in PAYABLE_STATES]),
                    SupplierInvoice.due_on < soon,
                )
            )
        ]
        add(
            "notice",
            "payables_due",
            "Supplier invoices due within 14 days or overdue",
            [value for value in payables if value > ZERO],
            "payables",
            "Due to suppliers.",
        )
        tax = [
            money(o.assessed - o.recoverable - o.withheld + o.adjustments - o.paid)
            for o in self._db.scalars(
                select(TaxObligation).where(
                    TaxObligation.business_id == business_id,
                    TaxObligation.state.in_(["outstanding", "part_paid"]),
                    TaxObligation.due_on < soon,
                )
            )
        ]
        add(
            "warning",
            "tax_due",
            "Tax due within 14 days or overdue",
            [value for value in tax if value > ZERO],
            "taxation",
            "Returns and payments to make.",
        )
        instalments = self._db.execute(
            select(LoanInstalment, LoanAgreement.direction)
            .join(LoanSchedule, LoanSchedule.id == LoanInstalment.schedule_fk)
            .join(LoanAgreement, LoanAgreement.id == LoanInstalment.agreement_fk)
            .where(
                LoanInstalment.business_id == business_id,
                LoanInstalment.closed.is_(False),
                LoanSchedule.state == "current",
                LoanAgreement.state == AgreementState.ACTIVE.value,
                LoanInstalment.due_on < soon,
            )
        ).all()
        owed_by_us = [
            row.owed - row.paid
            for row, direction in instalments
            if direction == "borrowing" and row.owed > row.paid
        ]
        owed_to_us = [
            row.owed - row.paid
            for row, direction in instalments
            if direction == "lending" and row.owed > row.paid and row.due_on < today
        ]
        add(
            "warning",
            "loan_instalments",
            "Loan instalments due within 14 days or overdue",
            owed_by_us,
            "financing",
            "Instalments the business owes.",
        )
        add(
            "warning",
            "loan_arrears",
            "Loan repayments owed to the business overdue",
            owed_to_us,
            "financing",
            "Borrowers behind on their schedules.",
        )
        open_statements = self._db.scalar(
            select(func.count(BankStatement.id)).where(
                BankStatement.business_id == business_id, BankStatement.state == "open"
            )
        )
        if open_statements:
            alerts.append(
                Alert(
                    "notice",
                    "unreconciled",
                    "Statements not yet reconciled",
                    "Imported statements still open.",
                    None,
                    int(open_statements),
                    "reconciliation",
                )
            )
        from nickland.application.services.budgets import BudgetService

        budgets = BudgetService(self._db, clock=self._clock)
        over: list[Decimal] = []
        for budget in self._db.scalars(
            select(Budget).where(
                Budget.business_id == business_id,
                Budget.state == BudgetState.APPROVED.value,
                Budget.starts_on <= today,
                Budget.ends_on >= today,
            )
        ):
            comparison = budgets.comparison(actor, budget.id, as_of=today)
            current = next(
                (
                    index
                    for index, period in enumerate(comparison.periods)
                    if period.starts_on <= today <= period.ends_on
                ),
                None,
            )
            if current is None:
                continue
            for line in comparison.lines:
                cell = line.cells[current]
                if line.nature.value == "cost" and cell.actual > cell.budget > ZERO:
                    over.append(cell.actual - cell.budget)
        add(
            "warning",
            "budget_overrun",
            "Budget lines overspent this period",
            over,
            "budgets",
            "Spending beyond what was planned for the period.",
        )
        order = {"critical": 0, "warning": 1, "notice": 2}
        return sorted(alerts, key=lambda alert: (order[alert.severity], alert.title))

    # -- internals --------------------------------------------------------------------------

    def _cash(self, business_id: uuid.UUID, on: date) -> Decimal:
        row = self._db.execute(
            select(func.sum(JournalLine.base_debit), func.sum(JournalLine.base_credit))
            .join(Account, Account.id == JournalLine.account_fk)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state.in_(["posted", "reversed"]),
                JournalLine.posting_date <= on,
                Account.cash_flow_category == CashFlowCategory.CASH.value,
            )
        ).one()
        return money(Decimal(row[0] or 0) - Decimal(row[1] or 0))

    def _ensure_presets(self, business_id: uuid.UUID) -> None:
        present = {
            row.kind
            for row in self._db.scalars(
                select(ForecastScenario).where(ForecastScenario.business_id == business_id)
            )
        }
        for kind, terms in PRESET_ASSUMPTIONS.items():
            if kind.value in present:
                continue
            self._db.add(
                ForecastScenario(
                    business_id=business_id,
                    name=kind.label,
                    kind=kind.value,
                    assumptions=terms.as_record(),
                    is_default=kind is ScenarioKind.BASE,
                )
            )
        self._db.flush()

    def _scenario(self, business_id: uuid.UUID, scenario_id: uuid.UUID) -> ForecastScenario:
        row = self._db.get(ForecastScenario, scenario_id)
        if row is None or row.business_id != business_id:
            raise NotFoundError("Scenario not found.")
        return row

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


def _require_view(actor: AnyActor) -> None:
    actor.require_any("report.view", "budget.manage", "journal.view")


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id


__all__ = ["Alert", "Forecast", "ForecastService", "PlannedFlow"]
