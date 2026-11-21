"""The charts of PRD §20.7 and §29.8, as data.

Each chart is a set of series over a set of points, every series labelled with its basis:
actual, budget, forecast or estimate. A point carries the filter that opens the records it
is made of, so selecting a bar or a segment always leads to the postings, sales or debts
behind it. The interface draws; nothing here is computed for the screen alone.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.debts import DebtService
from nickland.application.services.profitability import ProfitabilityService
from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.ledger import CashFlowCategory
from nickland.domain.money import ZERO, money
from nickland.domain.planning import (
    Basis,
    BudgetState,
    Interval,
    PeriodKind,
    budget_periods,
    cents,
    intervals,
    prior_window,
    year_before,
)
from nickland.persistence.ledger_models import Account, AccountGroup, JournalEntry, JournalLine
from nickland.persistence.planning_models import Budget
from nickland.persistence.tax_models import TaxPayment
from nickland.platform.clock import Clock, SystemClock

INCOME_CODES = frozenset({"70000", "70100", "70200"})
OUTPUT_TAX = "21000"
INPUT_TAX = "13000"
REPORTABLE = ("posted", "reversed")

CHARTS: tuple[tuple[str, str], ...] = (
    ("revenue_profit", "Revenue and net profit"),
    ("income_expenses", "Income against expenses"),
    ("cash_flow", "Cash in, cash out and the balance"),
    ("expense_composition", "Where the money goes"),
    ("profit_by_product", "Contribution by product"),
    ("profit_by_category", "Contribution by category"),
    ("channels", "Sales by channel"),
    ("tax_position", "Tax position"),
    ("ageing", "Receivables and payables by age"),
    ("supplier_variance", "Quoted cost against actual cost, by supplier"),
    ("monthly_performance", "This month against last month and budget"),
)


@dataclass(frozen=True, slots=True)
class Series:
    key: str
    label: str
    basis: Basis
    role: str = "primary"
    """``primary``, ``comparison`` or ``supporting``: which the eye should land on."""


@dataclass(slots=True)
class Point:
    key: str
    label: str
    values: dict[str, Decimal]
    drill: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Chart:
    id: str
    title: str
    kind: str
    """``line``, ``columns``, ``bars`` or ``grouped_bars``."""
    unit: str
    starts_on: date
    ends_on: date
    interval: str | None
    series: list[Series]
    points: list[Point]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DrillLine:
    posting_date: date
    entry_id: uuid.UUID
    number: str | None
    account_code: str
    account_name: str
    narration: str | None
    amount: Decimal


@dataclass(frozen=True, slots=True)
class _Row:
    on: date
    code: str
    group: str | None
    kind: str
    category: str
    debit: Decimal
    credit: Decimal

    @property
    def income(self) -> bool:
        return self.kind == "revenue" or self.code in INCOME_CODES

    @property
    def expense(self) -> bool:
        return self.kind == "expense" and self.code not in INCOME_CODES


class AnalyticsService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()

    def chart(
        self,
        actor: AnyActor,
        chart_id: str,
        *,
        start: date,
        end: date,
        interval: str = "month",
        compare: bool = True,
    ) -> Chart:
        actor.require("report.view")
        business_id = _business_of(actor)
        if end < start:
            raise ValidationError("A window ends after it begins.", field="end")
        builders = {
            "revenue_profit": self._revenue_profit,
            "income_expenses": self._income_expenses,
            "cash_flow": self._cash_flow,
            "expense_composition": self._expense_composition,
            "profit_by_product": self._profit_by_product,
            "profit_by_category": self._profit_by_category,
            "channels": self._channels,
            "tax_position": self._tax_position,
            "ageing": self._ageing,
            "supplier_variance": self._supplier_variance,
            "monthly_performance": self._monthly_performance,
        }
        builder: Callable[[AnyActor, uuid.UUID, date, date, Interval, bool], Chart] | None
        builder = builders.get(chart_id)
        if builder is None:
            raise ValidationError("No such chart.", field="chart")
        return builder(actor, business_id, start, end, Interval(interval), compare)

    # -- ledger-built charts ----------------------------------------------------------------

    def _revenue_profit(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        rows = self._rows(business_id, start, end)
        prior_rows = self._rows(business_id, *year_before(start, end)) if compare else []
        series = [
            Series("revenue", "Revenue", Basis.ACTUAL),
            Series("net_profit", "Net profit", Basis.ACTUAL, "supporting"),
        ]
        if compare:
            series.append(
                Series("revenue_prior", "Revenue a year before", Basis.ACTUAL, "comparison")
            )
        budget = self._budget_by_interval(business_id, start, end, interval)
        if budget:
            series.append(Series("revenue_budget", "Revenue budget", Basis.BUDGET, "comparison"))
        points: list[Point] = []
        for first, last in intervals(start, end, interval):
            inside = [row for row in rows if first <= row.on <= last]
            income = _income(inside)
            values = {"revenue": income, "net_profit": cents(income - _expenses(inside))}
            if compare:
                p_first, p_last = year_before(first, last)
                values["revenue_prior"] = _income(
                    [row for row in prior_rows if p_first <= row.on <= p_last]
                )
            if budget:
                values["revenue_budget"] = budget.get(first, {}).get("income", ZERO)
            points.append(
                Point(
                    first.isoformat(),
                    _label(first, last, interval),
                    values,
                    _drill("income", first, last),
                )
            )
        return Chart(
            "revenue_profit",
            _title("revenue_profit"),
            "line",
            "currency",
            start,
            end,
            interval.value,
            series,
            points,
        )

    def _income_expenses(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        rows = self._rows(business_id, start, end)
        points = []
        for first, last in intervals(start, end, interval):
            inside = [row for row in rows if first <= row.on <= last]
            points.append(
                Point(
                    first.isoformat(),
                    _label(first, last, interval),
                    {"income": _income(inside), "expenses": _expenses(inside)},
                    _drill("income_expenses", first, last),
                )
            )
        return Chart(
            "income_expenses",
            _title("income_expenses"),
            "columns",
            "currency",
            start,
            end,
            interval.value,
            [
                Series("income", "Income", Basis.ACTUAL),
                Series("expenses", "Expenses", Basis.ACTUAL, "supporting"),
            ],
            points,
        )

    def _cash_flow(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        cash_codes = set(
            self._db.scalars(
                select(Account.code).where(
                    Account.business_id == business_id,
                    Account.cash_flow_category == CashFlowCategory.CASH.value,
                )
            )
        )
        opening = self._balance(business_id, cash_codes, start - timedelta(days=1))
        rows = [
            row
            for row in self._rows(business_id, start, end, by_entry=True)
            if row.code in cash_codes
        ]
        running = opening
        points = []
        for first, last in intervals(start, end, interval):
            inside = [row for row in rows if first <= row.on <= last]
            inflow = cents(sum((row.debit for row in inside), ZERO))
            outflow = cents(sum((row.credit for row in inside), ZERO))
            running = cents(running + inflow - outflow)
            points.append(
                Point(
                    first.isoformat(),
                    _label(first, last, interval),
                    {"inflow": inflow, "outflow": outflow, "balance": running},
                    _drill("cash", first, last),
                )
            )
        return Chart(
            "cash_flow",
            _title("cash_flow"),
            "columns",
            "currency",
            start,
            end,
            interval.value,
            [
                Series("inflow", "Cash in", Basis.ACTUAL),
                Series("outflow", "Cash out", Basis.ACTUAL, "supporting"),
                Series("balance", "Cash and bank balance", Basis.ACTUAL, "comparison"),
            ],
            points,
            note="Transfers between the business's own accounts appear on both sides.",
        )

    def _expense_composition(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for row in self._rows(business_id, start, end):
            if row.expense:
                totals[row.code] += row.debit - row.credit
        names = self._account_names(business_id)
        ranked = sorted(
            ((code, cents(amount)) for code, amount in totals.items() if amount != ZERO),
            key=lambda item: -item[1],
        )
        points = [
            Point(
                code,
                f"{code} {names.get(code, '')}".strip(),
                {"amount": amount},
                {"measure": f"account:{code}", "start": start.isoformat(), "end": end.isoformat()},
            )
            for code, amount in ranked
        ]
        return Chart(
            "expense_composition",
            _title("expense_composition"),
            "bars",
            "currency",
            start,
            end,
            None,
            [Series("amount", "Expense", Basis.ACTUAL)],
            points,
        )

    def _tax_position(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        rows = self._rows(business_id, start, end)
        payments = self._db.execute(
            select(TaxPayment.paid_on, TaxPayment.amount).where(
                TaxPayment.business_id == business_id,
                TaxPayment.paid_on >= start,
                TaxPayment.paid_on <= end,
            )
        ).all()
        points = []
        for first, last in intervals(start, end, interval):
            inside = [row for row in rows if first <= row.on <= last]
            collected = cents(
                sum((row.credit - row.debit for row in inside if row.code == OUTPUT_TAX), ZERO)
            )
            recoverable = cents(
                sum((row.debit - row.credit for row in inside if row.code == INPUT_TAX), ZERO)
            )
            paid = cents(sum((amount for on, amount in payments if first <= on <= last), ZERO))
            owed = cents(
                self._balance(business_id, {OUTPUT_TAX}, last, credit_normal=True)
                - self._balance(business_id, {INPUT_TAX}, last)
            )
            points.append(
                Point(
                    first.isoformat(),
                    _label(first, last, interval),
                    {
                        "collected": collected,
                        "recoverable": recoverable,
                        "paid": paid,
                        "outstanding": owed,
                    },
                    _drill("tax", first, last),
                )
            )
        return Chart(
            "tax_position",
            _title("tax_position"),
            "columns",
            "currency",
            start,
            end,
            interval.value,
            [
                Series("collected", "Output tax charged", Basis.ACTUAL),
                Series("recoverable", "Input tax recoverable", Basis.ACTUAL, "supporting"),
                Series("paid", "Paid to the authority", Basis.ACTUAL, "supporting"),
                Series(
                    "outstanding", "Net owed at the end of the period", Basis.ACTUAL, "comparison"
                ),
            ],
            points,
        )

    def _monthly_performance(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        this_start = end.replace(day=1)
        last_start, last_end = prior_window(this_start, end)
        last_start = last_end.replace(day=1)
        current = self._rows(business_id, this_start, end)
        previous = self._rows(business_id, last_start, last_end)

        def figures(rows: list[_Row]) -> dict[str, Decimal]:
            income = _income(rows)
            cost_of_sales = cents(
                sum((row.debit - row.credit for row in rows if row.group == "COS"), ZERO)
            )
            expenses = _expenses(rows)
            return {
                "revenue": income,
                "gross_profit": cents(income - cost_of_sales),
                "expenses": expenses,
                "net_profit": cents(income - expenses),
            }

        now, before = figures(current), figures(previous)
        budget = self._budget_by_interval(business_id, this_start, end, Interval.MONTH).get(
            this_start
        )
        series = [
            Series("current", f"{this_start:%b %Y} to date", Basis.ACTUAL),
            Series("previous", f"{last_start:%b %Y}", Basis.ACTUAL, "comparison"),
        ]
        if budget:
            series.append(
                Series("budget", f"{this_start:%b %Y} budget", Basis.BUDGET, "supporting")
            )
        labels = {
            "revenue": "Revenue",
            "gross_profit": "Gross profit",
            "expenses": "Expenses",
            "net_profit": "Net profit",
        }
        points = []
        for key, label in labels.items():
            values = {"current": now[key], "previous": before[key]}
            if budget:
                values["budget"] = {
                    "revenue": budget.get("income", ZERO),
                    "gross_profit": cents(budget.get("income", ZERO) - budget.get("cos", ZERO)),
                    "expenses": budget.get("cost", ZERO),
                    "net_profit": cents(budget.get("income", ZERO) - budget.get("cost", ZERO)),
                }[key]
            points.append(
                Point(
                    key,
                    label,
                    values,
                    _drill("income" if key == "revenue" else "income_expenses", this_start, end),
                )
            )
        return Chart(
            "monthly_performance",
            _title("monthly_performance"),
            "grouped_bars",
            "currency",
            this_start,
            end,
            None,
            series,
            points,
        )

    # -- charts from the registers ----------------------------------------------------------

    def _profit_by(
        self, actor: AnyActor, dimension: str, start: date, end: date, chart_id: str
    ) -> Chart:
        made = ProfitabilityService(self._db).profitability(
            actor, dimension=dimension, start=start, end=end
        )
        points = [
            Point(
                row.key,
                row.label,
                {"contribution": row.after_commission, "revenue": row.revenue},
                {
                    "profit_dimension": dimension,
                    "key": row.key,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            )
            for row in made.rows
        ]
        return Chart(
            chart_id,
            _title(chart_id),
            "bars",
            "currency",
            start,
            end,
            None,
            [
                Series("contribution", "Contribution after commission", Basis.ACTUAL),
                Series("revenue", "Revenue", Basis.ACTUAL, "supporting"),
            ],
            points,
            note=(
                f"{made.total.pending_lines} lines await their actual cost and are left out of "
                "contribution."
                if made.total.pending_lines
                else None
            ),
        )

    def _profit_by_product(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        return self._profit_by(actor, "product", start, end, "profit_by_product")

    def _profit_by_category(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        return self._profit_by(actor, "category", start, end, "profit_by_category")

    def _channels(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        facts = ProfitabilityService(self._db).facts(business_id, start, end)
        points = []
        for first, last in intervals(start, end, interval):
            inside = [fact for fact in facts if first <= fact.on <= last]
            points.append(
                Point(
                    first.isoformat(),
                    _label(first, last, interval),
                    {
                        "quotation": cents(
                            sum((f.revenue for f in inside if f.channel == "quotation"), ZERO)
                        ),
                        "register": cents(
                            sum((f.revenue for f in inside if f.channel == "register"), ZERO)
                        ),
                    },
                    {
                        "profit_dimension": "channel",
                        "start": first.isoformat(),
                        "end": last.isoformat(),
                    },
                )
            )
        return Chart(
            "channels",
            _title("channels"),
            "columns",
            "currency",
            start,
            end,
            interval.value,
            [
                Series("quotation", "Quotation-led sales", Basis.ACTUAL),
                Series("register", "Point of sale", Basis.ACTUAL, "supporting"),
            ],
            points,
        )

    def _ageing(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        summary = DebtService(self._db, clock=self._clock).summary(actor, on=end)
        points = [
            Point(
                owed.code,
                owed.label,
                {"receivable": owed.amount, "payable": due.amount},
                {"register": "ageing", "bucket": owed.code},
            )
            for owed, due in zip(summary.receivable_ageing, summary.payable_ageing, strict=True)
        ]
        return Chart(
            "ageing",
            _title("ageing"),
            "columns",
            "currency",
            end,
            end,
            None,
            [
                Series("receivable", "Owed to the business", Basis.ACTUAL),
                Series("payable", "Owed by the business", Basis.ACTUAL, "supporting"),
            ],
            points,
            note="Each side stands apart; neither is netted against the other.",
        )

    def _supplier_variance(
        self,
        actor: AnyActor,
        business_id: uuid.UUID,
        start: date,
        end: date,
        interval: Interval,
        compare: bool,
    ) -> Chart:
        report = ProfitabilityService(self._db).erosion(actor, start=start, end=end)
        quoted: dict[str, Decimal] = defaultdict(lambda: ZERO)
        actual: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for row in report.rows:
            if row.actual_cost is None:
                continue
            quoted[row.supplier] += row.quoted_cost
            actual[row.supplier] += row.actual_cost
        points = [
            Point(
                supplier,
                supplier,
                {"quoted": cents(quoted[supplier]), "actual": cents(actual[supplier])},
                {"erosion_supplier": supplier, "start": start.isoformat(), "end": end.isoformat()},
            )
            for supplier in sorted(quoted, key=lambda name: -(actual[name] - quoted[name]))
        ]
        return Chart(
            "supplier_variance",
            _title("supplier_variance"),
            "grouped_bars",
            "currency",
            start,
            end,
            None,
            [
                Series("quoted", "Cost estimated in the quotation", Basis.ESTIMATE, "comparison"),
                Series("actual", "Actual landed cost", Basis.ACTUAL),
            ],
            points,
            note=(
                f"{report.pending} order lines await their actual cost and are not shown."
                if report.pending
                else None
            ),
        )

    # -- drill-down -------------------------------------------------------------------------

    def drill(
        self, actor: AnyActor, *, measure: str, start: date, end: date, limit: int = 500
    ) -> list[DrillLine]:
        """The postings behind a ledger-built point."""
        actor.require("report.view")
        business_id = _business_of(actor)
        query = (
            select(JournalLine, JournalEntry, Account)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_fk)
            .join(Account, Account.id == JournalLine.account_fk)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state.in_(REPORTABLE),
                JournalLine.posting_date >= start,
                JournalLine.posting_date <= end,
            )
        )
        if measure == "income":
            query = query.where(
                (Account.kind == "revenue") | Account.code.in_(sorted(INCOME_CODES))
            )
        elif measure == "income_expenses":
            query = query.where(Account.kind.in_(["revenue", "expense"]))
        elif measure == "cash":
            query = query.where(Account.cash_flow_category == CashFlowCategory.CASH.value)
        elif measure == "tax":
            query = query.where(Account.code.in_([OUTPUT_TAX, INPUT_TAX]))
        elif measure.startswith("account:"):
            query = query.where(Account.code == measure.split(":", 1)[1])
        else:
            raise ValidationError("No such measure.", field="measure")
        lines: list[DrillLine] = []
        for line, entry, account in self._db.execute(
            query.order_by(JournalLine.posting_date, JournalEntry.number).limit(
                max(1, min(limit, 2000))
            )
        ).all():
            income = (
                account.kind == "revenue"
                or account.code in INCOME_CODES
                or account.code == OUTPUT_TAX
            )
            amount = (
                line.base_credit - line.base_debit if income else line.base_debit - line.base_credit
            )
            lines.append(
                DrillLine(
                    line.posting_date,
                    entry.id,
                    entry.number,
                    account.code,
                    account.name,
                    line.narration or entry.narration,
                    money(amount),
                )
            )
        return lines

    # -- internals --------------------------------------------------------------------------

    def _rows(
        self, business_id: uuid.UUID, start: date, end: date, *, by_entry: bool = False
    ) -> list[_Row]:
        del by_entry
        result = self._db.execute(
            select(
                JournalLine.posting_date,
                Account.code,
                AccountGroup.code,
                Account.kind,
                Account.cash_flow_category,
                JournalLine.base_debit,
                JournalLine.base_credit,
            )
            .join(Account, Account.id == JournalLine.account_fk)
            .outerjoin(AccountGroup, AccountGroup.id == Account.group_fk)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state.in_(REPORTABLE),
                JournalLine.posting_date >= start,
                JournalLine.posting_date <= end,
            )
        ).all()
        return [_Row(*row) for row in result]

    def _balance(
        self, business_id: uuid.UUID, codes: set[str], on: date, *, credit_normal: bool = False
    ) -> Decimal:
        if not codes:
            return ZERO
        debit, credit = self._db.execute(
            select(func.sum(JournalLine.base_debit), func.sum(JournalLine.base_credit))
            .join(Account, Account.id == JournalLine.account_fk)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state.in_(REPORTABLE),
                JournalLine.posting_date <= on,
                Account.code.in_(sorted(codes)),
            )
        ).one()
        debit, credit = Decimal(debit or 0), Decimal(credit or 0)
        return money(credit - debit if credit_normal else debit - credit)

    def _account_names(self, business_id: uuid.UUID) -> dict[str, str]:
        return {
            row[0]: row[1]
            for row in self._db.execute(
                select(Account.code, Account.name).where(Account.business_id == business_id)
            ).all()
        }

    def _budget_by_interval(
        self, business_id: uuid.UUID, start: date, end: date, interval: Interval
    ) -> dict[date, dict[str, Decimal]]:
        """Approved monthly budget figures, as income, cost and cost of sales, by month.
        Only monthly budgets are drawn against a time axis; a quarterly or annual budget
        has no monthly figure to draw without inventing one."""
        if interval is not Interval.MONTH:
            return {}
        from nickland.application.services.budgets import BudgetService

        service = BudgetService(self._db, clock=self._clock)
        result: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
        for budget in self._db.scalars(
            select(Budget).where(
                Budget.business_id == business_id,
                Budget.state == BudgetState.APPROVED.value,
                Budget.period_kind == PeriodKind.MONTHLY.value,
                Budget.starts_on <= end,
                Budget.ends_on >= start,
            )
        ):
            periods = budget_periods(
                PeriodKind(budget.period_kind), budget.starts_on, budget.ends_on
            )
            for line in budget.lines:
                nature = service.nature_of(budget.business_id, line)
                group = line.group_code
                if line.account_code:
                    account = self._db.scalar(
                        select(Account).where(
                            Account.business_id == business_id, Account.code == line.account_code
                        )
                    )
                    parent = (
                        self._db.get(AccountGroup, account.group_fk)
                        if account and account.group_fk
                        else None
                    )
                    group = parent.code if parent else None
                for period in periods:
                    if not (start <= period.starts_on <= end):
                        continue
                    amount = money(line.amounts.get(period.key, "0"))
                    bucket = result[period.starts_on]
                    if nature.value == "income":
                        bucket["income"] += amount
                    else:
                        bucket["cost"] += amount
                        if group == "COS":
                            bucket["cos"] += amount
        return {key: dict(value) for key, value in result.items()}


def _income(rows: list[_Row]) -> Decimal:
    return cents(sum((row.credit - row.debit for row in rows if row.income), ZERO))


def _expenses(rows: list[_Row]) -> Decimal:
    return cents(sum((row.debit - row.credit for row in rows if row.expense), ZERO))


def _label(first: date, last: date, interval: Interval) -> str:
    if interval is Interval.DAY:
        return f"{first:%d %b}"
    if interval is Interval.WEEK:
        return f"w/c {first:%d %b}"
    if interval is Interval.MONTH:
        return f"{first:%b %Y}"
    return f"Q{(first.month - 1) // 3 + 1} {first.year}"


def _drill(measure: str, first: date, last: date) -> dict[str, str]:
    return {"measure": measure, "start": first.isoformat(), "end": last.isoformat()}


def _title(chart_id: str) -> str:
    return dict(CHARTS)[chart_id]


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
