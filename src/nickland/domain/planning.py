"""Budgets, forecasts and profitability. PRD §20.7, §29.6, §29.8.

Pure. Four kinds of figure meet here and must never be taken for one another: what was
posted (actual), what was planned (budget), what is expected (forecast), and what a
quotation estimated. Each value the module produces carries its ``Basis``, so a screen or
an export always says which it is showing.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

from nickland.domain.errors import ValidationError
from nickland.domain.money import ZERO

CENT: Final = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class Basis(StrEnum):
    """What a figure is. Shown beside every figure the module reports."""

    ACTUAL = "actual"
    BUDGET = "budget"
    FORECAST = "forecast"
    ESTIMATE = "estimate"

    @property
    def label(self) -> str:
        return {
            Basis.ACTUAL: "Actual, posted",
            Basis.BUDGET: "Budget",
            Basis.FORECAST: "Forecast",
            Basis.ESTIMATE: "Quotation estimate",
        }[self]


# -- analysis codes ------------------------------------------------------------------------


class AnalysisKind(StrEnum):
    DEPARTMENT = "department"
    PROJECT = "project"
    COST_CENTRE = "cost_centre"

    @property
    def label(self) -> str:
        return {
            AnalysisKind.DEPARTMENT: "Department",
            AnalysisKind.PROJECT: "Project",
            AnalysisKind.COST_CENTRE: "Cost centre",
        }[self]


# -- budgets -------------------------------------------------------------------------------


class BudgetState(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        return {
            BudgetState.DRAFT: "Draft",
            BudgetState.IN_REVIEW: "In review",
            BudgetState.APPROVED: "Approved",
            BudgetState.SUPERSEDED: "Superseded",
            BudgetState.REJECTED: "Rejected",
        }[self]

    @property
    def editable(self) -> bool:
        return self in {BudgetState.DRAFT, BudgetState.REJECTED}


class PeriodKind(StrEnum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        return self.value.capitalize()


@dataclass(frozen=True, slots=True)
class BudgetPeriod:
    key: str
    label: str
    starts_on: date
    ends_on: date

    @property
    def days(self) -> int:
        return (self.ends_on - self.starts_on).days + 1


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def budget_periods(kind: PeriodKind, starts_on: date, ends_on: date) -> list[BudgetPeriod]:
    """The columns of a budget. Monthly and quarterly budgets run from the first of a month;
    an annual budget is one column; a custom budget is the whole window as one."""
    if ends_on < starts_on:
        raise ValidationError("A budget ends after it begins.", field="ends_on")
    if (ends_on - starts_on).days > 366 * 3:
        raise ValidationError("A budget covers at most three years.", field="ends_on")
    if kind in {PeriodKind.ANNUAL, PeriodKind.CUSTOM}:
        label = (
            f"{starts_on.year}"
            if kind is PeriodKind.ANNUAL
            else f"{starts_on:%d %b %Y} to {ends_on:%d %b %Y}"
        )
        return [BudgetPeriod("all", label, starts_on, ends_on)]
    if starts_on.day != 1:
        raise ValidationError(
            "A monthly or quarterly budget begins on the first of a month.", field="starts_on"
        )
    step = 1 if kind is PeriodKind.MONTHLY else 3
    periods: list[BudgetPeriod] = []
    year, month = starts_on.year, starts_on.month
    while True:
        first = date(year, month, 1)
        if first > ends_on:
            break
        end_month = month + step - 1
        end_year = year + (end_month - 1) // 12
        end_month = (end_month - 1) % 12 + 1
        last = min(_month_end(end_year, end_month), ends_on)
        if kind is PeriodKind.MONTHLY:
            key, label = f"{year}-{month:02d}", f"{_MONTHS[month - 1]} {year}"
        else:
            key = f"{year}-{month:02d}"
            label = f"{_MONTHS[month - 1]} to {_MONTHS[end_month - 1]} {end_year}"
        periods.append(BudgetPeriod(key, label, first, last))
        month += step
        while month > 12:
            month -= 12
            year += 1
    return periods


def spread(total: Decimal, periods: list[BudgetPeriod], how: str = "even") -> dict[str, Decimal]:
    """Divide an annual figure across the columns: evenly, or by the days in each."""
    if not periods:
        return {}
    if how == "days":
        days = sum(period.days for period in periods)
        shares = [cents(total * period.days / days) for period in periods]
    else:
        share = cents(total / len(periods))
        shares = [share] * len(periods)
    shares[-1] += cents(total) - sum(shares, ZERO)
    return {period.key: amount for period, amount in zip(periods, shares, strict=True)}


class Nature(StrEnum):
    """Whether more is better (income) or worse (cost), so variance can say which way it
    went without the reader having to know the account."""

    INCOME = "income"
    COST = "cost"


@dataclass(frozen=True, slots=True)
class Variance:
    actual: Decimal
    budget: Decimal
    absolute: Decimal
    percent: Decimal | None
    favourable: bool | None
    utilisation: Decimal | None


def variance(actual: Decimal, budget: Decimal, nature: Nature) -> Variance:
    """Actual less budget, and whether that is good news. A cost under budget and income
    over budget are favourable."""
    absolute = cents(actual - budget)
    percent = (
        (absolute / abs(budget) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if budget != ZERO
        else None
    )
    favourable = (
        None
        if absolute == ZERO
        else (absolute > ZERO)
        if nature is Nature.INCOME
        else (absolute < ZERO)
    )
    utilisation = (
        (actual / budget * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if budget > ZERO
        else None
    )
    return Variance(cents(actual), cents(budget), absolute, percent, favourable, utilisation)


def outturn(actual_to_date: Decimal, remaining_budget: Decimal) -> Decimal:
    """The forecast outturn: what has been spent or earned so far, plus the budget for the
    periods still to come. Labelled forecast wherever it is shown."""
    return cents(actual_to_date + remaining_budget)


class RequestState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        return self.value.capitalize()


# -- cash forecast -------------------------------------------------------------------------


class ForecastClass(StrEnum):
    """The lines a cash forecast is built from, in and out."""

    COLLECTIONS = "collections"
    LOAN_RECEIPTS = "loan_receipts"
    PLANNED_IN = "planned_in"
    SUPPLIER_PAYMENTS = "supplier_payments"
    EXPENSES = "expenses"
    RECURRING = "recurring"
    TAX = "tax"
    COMMISSIONS = "commissions"
    LOAN_REPAYMENTS = "loan_repayments"
    PLANNED_OUT = "planned_out"

    @property
    def label(self) -> str:
        return _CLASS_LABELS[self]

    @property
    def inward(self) -> bool:
        return self in {
            ForecastClass.COLLECTIONS,
            ForecastClass.LOAN_RECEIPTS,
            ForecastClass.PLANNED_IN,
        }


_CLASS_LABELS: Final = {
    ForecastClass.COLLECTIONS: "Customer collections",
    ForecastClass.LOAN_RECEIPTS: "Loan repayments received",
    ForecastClass.PLANNED_IN: "Planned receipts and borrowing",
    ForecastClass.SUPPLIER_PAYMENTS: "Supplier payments",
    ForecastClass.EXPENSES: "Expenses owed and staff claims",
    ForecastClass.RECURRING: "Recurring costs",
    ForecastClass.TAX: "Tax obligations",
    ForecastClass.COMMISSIONS: "Commissions payable",
    ForecastClass.LOAN_REPAYMENTS: "Loan instalments",
    ForecastClass.PLANNED_OUT: "Planned purchases and payments",
}


class ScenarioKind(StrEnum):
    BEST = "best"
    BASE = "base"
    DOWNSIDE = "downside"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        return {
            ScenarioKind.BEST: "Best case",
            ScenarioKind.BASE: "Base case",
            ScenarioKind.DOWNSIDE: "Downside",
            ScenarioKind.CUSTOM: "Custom",
        }[self]


@dataclass(frozen=True, slots=True)
class PlannedFlow:
    on: date
    amount: Decimal
    inward: bool
    label: str


@dataclass(frozen=True, slots=True)
class Assumptions:
    """What a scenario supposes. Every assumption is stated, so a forecast can be read
    back and argued with."""

    collection_delay_days: int = 0
    collection_rate: Decimal = Decimal(100)
    """Percent of what customers owe that arrives within the horizon."""
    payment_delay_days: int = 0
    include_recurring: bool = True
    include_overdue_payables: bool = True
    extra_flows: tuple[PlannedFlow, ...] = field(default_factory=tuple)
    """New borrowing, a large purchase: what-ifs the scenario adds."""
    settle_loan_id: str | None = None
    settle_on: date | None = None

    def validate(self) -> None:
        if not -90 <= self.collection_delay_days <= 365:
            raise ValidationError(
                "Collections are brought forward up to 90 days or delayed up to 365.",
                field="collection_delay_days",
            )
        if not -90 <= self.payment_delay_days <= 365:
            raise ValidationError(
                "Payments are brought forward up to 90 days or delayed up to 365.",
                field="payment_delay_days",
            )
        if not ZERO <= self.collection_rate <= Decimal(100):
            raise ValidationError(
                "The share of collections received is between 0 and 100 percent.",
                field="collection_rate",
            )
        if (self.settle_loan_id is None) != (self.settle_on is None):
            raise ValidationError(
                "An early settlement names both the agreement and the date.", field="settle_on"
            )

    def as_record(self) -> dict[str, object]:
        return {
            "collection_delay_days": self.collection_delay_days,
            "collection_rate": str(self.collection_rate),
            "payment_delay_days": self.payment_delay_days,
            "include_recurring": self.include_recurring,
            "include_overdue_payables": self.include_overdue_payables,
            "extra_flows": [
                {
                    "on": flow.on.isoformat(),
                    "amount": str(flow.amount),
                    "inward": flow.inward,
                    "label": flow.label,
                }
                for flow in self.extra_flows
            ],
            "settle_loan_id": self.settle_loan_id,
            "settle_on": self.settle_on.isoformat() if self.settle_on else None,
        }

    @classmethod
    def from_record(cls, record: dict[str, object] | None) -> Assumptions:
        if not record:
            return cls()
        flows = record.get("extra_flows") or []
        assert isinstance(flows, list)
        settle_on = record.get("settle_on")
        return cls(
            collection_delay_days=int(str(record.get("collection_delay_days", 0))),
            collection_rate=Decimal(str(record.get("collection_rate", "100"))),
            payment_delay_days=int(str(record.get("payment_delay_days", 0))),
            include_recurring=bool(record.get("include_recurring", True)),
            include_overdue_payables=bool(record.get("include_overdue_payables", True)),
            extra_flows=tuple(
                PlannedFlow(
                    on=date.fromisoformat(str(item["on"])),
                    amount=Decimal(str(item["amount"])),
                    inward=bool(item["inward"]),
                    label=str(item.get("label") or "What-if"),
                )
                for item in flows
                if isinstance(item, dict)
            ),
            settle_loan_id=(
                str(record["settle_loan_id"]) if record.get("settle_loan_id") else None
            ),
            settle_on=date.fromisoformat(str(settle_on)) if settle_on else None,
        )


PRESET_ASSUMPTIONS: Final = {
    ScenarioKind.BEST: Assumptions(collection_delay_days=-3, payment_delay_days=7),
    ScenarioKind.BASE: Assumptions(),
    ScenarioKind.DOWNSIDE: Assumptions(
        collection_delay_days=30, collection_rate=Decimal(80), payment_delay_days=0
    ),
}

HORIZONS: Final = (7, 30, 60, 90)


def shift(on: date, days: int, start: date) -> date:
    """A date moved by an assumption, never earlier than the forecast's first day: money
    already late is expected today at the soonest."""
    moved = on + timedelta(days=days)
    return max(moved, start)


def bucket_size(horizon_days: int) -> int:
    """Days per row: daily up to a month, weekly beyond."""
    return 1 if horizon_days <= 31 else 7


# -- profitability -------------------------------------------------------------------------


class ProfitDimension(StrEnum):
    PRODUCT = "product"
    CATEGORY = "category"
    ORDER = "order"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    BRANCH = "branch"
    SALESPERSON = "salesperson"
    AFFILIATE = "affiliate"
    TERMINAL = "terminal"
    CHANNEL = "channel"

    @property
    def label(self) -> str:
        return {
            ProfitDimension.PRODUCT: "Product",
            ProfitDimension.CATEGORY: "Category",
            ProfitDimension.ORDER: "Order",
            ProfitDimension.CUSTOMER: "Customer",
            ProfitDimension.SUPPLIER: "Supplier or source",
            ProfitDimension.BRANCH: "Branch",
            ProfitDimension.SALESPERSON: "Sales person",
            ProfitDimension.AFFILIATE: "Affiliate",
            ProfitDimension.TERMINAL: "Register",
            ProfitDimension.CHANNEL: "Channel",
        }[self]


class ErosionCause(StrEnum):
    """Why a realised margin differs from the margin quoted, each named and measured."""

    PRICE = "price"
    DISCOUNT = "discount"
    SUPPLIER_COST = "supplier_cost"
    FREIGHT = "freight"
    TAX = "tax"
    RETURNS = "returns"
    COMMISSION = "commission"
    VOLUME = "volume"

    @property
    def label(self) -> str:
        return {
            ErosionCause.PRICE: "Price changed after quotation",
            ErosionCause.DISCOUNT: "Further discount",
            ErosionCause.SUPPLIER_COST: "Supplier cost",
            ErosionCause.FREIGHT: "Freight and landed charges",
            ErosionCause.TAX: "Non-recoverable tax",
            ErosionCause.RETURNS: "Returns and credit notes",
            ErosionCause.COMMISSION: "Commission",
            ErosionCause.VOLUME: "Quantity not yet invoiced",
        }[self]


@dataclass(frozen=True, slots=True)
class ErosionInputs:
    """One order line: what was quoted, and what has happened since."""

    quantity_quoted: Decimal
    quantity_invoiced: Decimal
    quoted_unit_net: Decimal
    realised_revenue: Decimal
    quoted_unit_cost: Decimal
    actual_unit_cost: Decimal | None
    quoted_freight: Decimal
    actual_freight: Decimal | None
    quoted_tax: Decimal
    actual_tax: Decimal | None
    quoted_commission: Decimal
    actual_commission: Decimal
    credited: Decimal
    further_discount: Decimal = ZERO


def erosion(inputs: ErosionInputs) -> dict[ErosionCause, Decimal]:
    """Each cause's effect on margin, negative where it cost margin. On the quantity that
    has been invoiced, so a part-delivered order is not blamed for what is still to come;
    the shortfall in quantity is its own line. Causes with no actual figure yet contribute
    nothing rather than a guess."""
    q = inputs.quantity_invoiced
    share = q / inputs.quantity_quoted if inputs.quantity_quoted > ZERO else ZERO
    realised_unit = inputs.realised_revenue / q if q > ZERO else inputs.quoted_unit_net
    causes: dict[ErosionCause, Decimal] = {
        ErosionCause.PRICE: cents(
            (realised_unit - inputs.quoted_unit_net) * q + inputs.further_discount
        ),
        ErosionCause.DISCOUNT: cents(-inputs.further_discount),
        ErosionCause.SUPPLIER_COST: (
            cents((inputs.quoted_unit_cost - inputs.actual_unit_cost) * q)
            if inputs.actual_unit_cost is not None
            else ZERO
        ),
        ErosionCause.FREIGHT: (
            cents(inputs.quoted_freight * share - inputs.actual_freight)
            if inputs.actual_freight is not None
            else ZERO
        ),
        ErosionCause.TAX: (
            cents(inputs.quoted_tax * share - inputs.actual_tax)
            if inputs.actual_tax is not None
            else ZERO
        ),
        ErosionCause.RETURNS: cents(-inputs.credited),
        ErosionCause.COMMISSION: cents(inputs.quoted_commission * share - inputs.actual_commission),
    }
    return causes


# -- charts --------------------------------------------------------------------------------


class Interval(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"


def interval_start(on: date, interval: Interval) -> date:
    if interval is Interval.DAY:
        return on
    if interval is Interval.WEEK:
        return on - timedelta(days=on.weekday())
    if interval is Interval.MONTH:
        return on.replace(day=1)
    return date(on.year, (on.month - 1) // 3 * 3 + 1, 1)


def intervals(start: date, end: date, interval: Interval) -> list[tuple[date, date]]:
    if end < start:
        raise ValidationError("A window ends after it begins.", field="end")
    rows: list[tuple[date, date]] = []
    first = interval_start(start, interval)
    while first <= end:
        if interval is Interval.DAY:
            last = first
        elif interval is Interval.WEEK:
            last = first + timedelta(days=6)
        elif interval is Interval.MONTH:
            last = _month_end(first.year, first.month)
        else:
            end_month = first.month + 2
            last = _month_end(first.year, end_month)
        rows.append((max(first, start), min(last, end)))
        first = last + timedelta(days=1)
        if len(rows) > 400:
            raise ValidationError(
                "Choose a longer interval for so long a window.", field="interval"
            )
    return rows


def prior_window(start: date, end: date) -> tuple[date, date]:
    """The window of the same length immediately before."""
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


def year_before(start: date, end: date) -> tuple[date, date]:
    def back(day: date) -> date:
        try:
            return day.replace(year=day.year - 1)
        except ValueError:
            return day.replace(year=day.year - 1, day=28)

    return back(start), back(end)
