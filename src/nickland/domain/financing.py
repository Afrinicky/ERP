"""Debt, loans and financing. PRD §25, §29.7.

Pure. A loan is a formal agreement, never income or expense: money borrowed is a liability,
money lent is an asset, and repaying either moves the balance and the cash together. Only
interest, fees and charges reach profit, and only when the agreement says they are owed.

The schedule is the agreement's arithmetic. It is built once from the terms, approved, and
kept; a later change produces a new version beside it, never an edit of the old one.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

from nickland.domain.errors import ValidationError
from nickland.domain.money import ZERO

CENT: Final = Decimal("0.01")

# Accounts, named once. Borrowing posts to the lender subledger, lending to the borrower's.
LOANS_ISSUED: Final = "11200"
LOAN_INTEREST_RECEIVABLE: Final = "11210"
LOANS_PAYABLE: Final = "23000"
ACCRUED_INTEREST_PAYABLE: Final = "23100"
INTEREST_INCOME: Final = "70000"
LOAN_FEE_INCOME: Final = "70200"
INTEREST_EXPENSE: Final = "71000"
FINANCE_CHARGES: Final = "71300"
BAD_DEBT_EXPENSE: Final = "72000"


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class LoanDirection(StrEnum):
    BORROWING = "borrowing"
    LENDING = "lending"

    @property
    def label(self) -> str:
        return "Borrowing" if self is LoanDirection.BORROWING else "Lending"


@dataclass(frozen=True, slots=True)
class LoanAccounts:
    principal: str
    accrued: str
    interest: str
    fees: str
    subledger: str


ACCOUNTS: Final = {
    LoanDirection.BORROWING: LoanAccounts(
        LOANS_PAYABLE, ACCRUED_INTEREST_PAYABLE, INTEREST_EXPENSE, FINANCE_CHARGES, "lender"
    ),
    LoanDirection.LENDING: LoanAccounts(
        LOANS_ISSUED, LOAN_INTEREST_RECEIVABLE, INTEREST_INCOME, LOAN_FEE_INCOME, "borrower"
    ),
}


class LoanType(StrEnum):
    TERM_LOAN = "term_loan"
    OVERDRAFT = "overdraft"
    REVOLVING_CREDIT = "revolving_credit"
    ASSET_FINANCE = "asset_finance"
    STAFF_ADVANCE = "staff_advance"
    RELATED_PARTY = "related_party"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _TYPE_LABELS[self]


_TYPE_LABELS: Final = {
    LoanType.TERM_LOAN: "Term loan",
    LoanType.OVERDRAFT: "Overdraft",
    LoanType.REVOLVING_CREDIT: "Revolving credit",
    LoanType.ASSET_FINANCE: "Asset finance",
    LoanType.STAFF_ADVANCE: "Staff advance",
    LoanType.RELATED_PARTY: "Related-party loan",
    LoanType.OTHER: "Other",
}


class InterestModel(StrEnum):
    NONE = "none"
    FIXED = "fixed"
    FLAT = "flat"
    REDUCING_BALANCE = "reducing_balance"
    INTEREST_ONLY = "interest_only"
    BALLOON = "balloon"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        return _MODEL_LABELS[self]


_MODEL_LABELS: Final = {
    InterestModel.NONE: "No interest",
    InterestModel.FIXED: "Fixed charge",
    InterestModel.FLAT: "Flat rate",
    InterestModel.REDUCING_BALANCE: "Reducing balance",
    InterestModel.INTEREST_ONLY: "Interest only, principal at maturity",
    InterestModel.BALLOON: "Reducing balance with balloon",
    InterestModel.CUSTOM: "Custom schedule",
}


class RepaymentFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"

    @property
    def per_year(self) -> int:
        return _PER_YEAR[self]

    @property
    def label(self) -> str:
        return _FREQUENCY_LABELS[self]


_PER_YEAR: Final = {
    RepaymentFrequency.DAILY: 365,
    RepaymentFrequency.WEEKLY: 52,
    RepaymentFrequency.FORTNIGHTLY: 26,
    RepaymentFrequency.MONTHLY: 12,
    RepaymentFrequency.QUARTERLY: 4,
    RepaymentFrequency.SEMI_ANNUAL: 2,
    RepaymentFrequency.ANNUAL: 1,
}

_FREQUENCY_LABELS: Final = {
    RepaymentFrequency.DAILY: "Daily",
    RepaymentFrequency.WEEKLY: "Weekly",
    RepaymentFrequency.FORTNIGHTLY: "Fortnightly",
    RepaymentFrequency.MONTHLY: "Monthly",
    RepaymentFrequency.QUARTERLY: "Quarterly",
    RepaymentFrequency.SEMI_ANNUAL: "Half-yearly",
    RepaymentFrequency.ANNUAL: "Annually",
}

_MONTHS: Final = {
    RepaymentFrequency.MONTHLY: 1,
    RepaymentFrequency.QUARTERLY: 3,
    RepaymentFrequency.SEMI_ANNUAL: 6,
    RepaymentFrequency.ANNUAL: 12,
}
_DAYS: Final = {
    RepaymentFrequency.DAILY: 1,
    RepaymentFrequency.WEEKLY: 7,
    RepaymentFrequency.FORTNIGHTLY: 14,
}


def nth_due(first: date, frequency: RepaymentFrequency, index: int) -> date:
    """The ``index``-th due date counted from ``first``. Months are measured from the first
    date, so a schedule anchored on the 31st returns to the 31st wherever the month allows."""
    if frequency in _DAYS:
        return first + timedelta(days=_DAYS[frequency] * index)
    months = _MONTHS[frequency] * index
    year, month = divmod(first.month - 1 + months, 12)
    target_year, target_month = first.year + year, month + 1
    return date(target_year, target_month, min(first.day, monthrange(target_year, target_month)[1]))


def period_days(frequency: RepaymentFrequency) -> int:
    """The nominal length of one period in days, for accruing part of a period."""
    if frequency in _DAYS:
        return _DAYS[frequency]
    return round(365 * _MONTHS[frequency] / 12)


def periodic_rate(
    annual_percent: Decimal,
    frequency: RepaymentFrequency,
    compounding: RepaymentFrequency | None = None,
) -> Decimal:
    """The rate for one repayment period. Where interest compounds on a different cycle from
    the repayments, the effective rate for the repayment period is derived from it."""
    annual = annual_percent / Decimal(100)
    if compounding is None or compounding is frequency:
        return annual / Decimal(frequency.per_year)
    per = Decimal(compounding.per_year)
    exponent = per / Decimal(frequency.per_year)
    return (1 + annual / per) ** exponent - 1


class Component(StrEnum):
    """The parts an instalment is made of, in the order an agreement may settle them."""

    PENALTY = "penalty"
    FEES = "fees"
    INTEREST = "interest"
    PRINCIPAL = "principal"

    @property
    def label(self) -> str:
        return {
            Component.PENALTY: "Penalties",
            Component.FEES: "Fees",
            Component.INTEREST: "Interest",
            Component.PRINCIPAL: "Principal",
        }[self]


DEFAULT_ORDER: Final = (Component.PENALTY, Component.FEES, Component.INTEREST, Component.PRINCIPAL)


def parse_order(values: Iterable[str]) -> tuple[Component, ...]:
    """An allocation order names every component exactly once."""
    order = tuple(Component(value) for value in values)
    if sorted(order) != sorted(Component):
        raise ValidationError(
            "The allocation order must name penalties, fees, interest and principal, each once.",
            field="allocation_order",
        )
    return order


# -- terms and schedules -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CustomRow:
    due_on: date
    principal: Decimal
    interest: Decimal = ZERO
    fees: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class LoanTerms:
    principal: Decimal
    model: InterestModel
    frequency: RepaymentFrequency
    instalments: int
    first_due_on: date
    annual_rate: Decimal = ZERO
    compounding: RepaymentFrequency | None = None
    grace_periods: int = 0
    interest_only_periods: int = 0
    balloon: Decimal = ZERO
    periodic_fee: Decimal = ZERO
    fixed_interest: Decimal = ZERO
    custom: tuple[CustomRow, ...] = ()

    def validate(self) -> None:
        if self.principal <= ZERO:
            raise ValidationError("The principal must be more than nothing.", field="principal")
        if self.annual_rate < ZERO or self.annual_rate > Decimal(1000):
            raise ValidationError(
                "The interest rate must be between 0 and 1,000 percent a year.",
                field="annual_rate",
            )
        if self.model is InterestModel.CUSTOM:
            if not self.custom:
                raise ValidationError("A custom schedule needs its instalments.", field="custom")
            dates = [row.due_on for row in self.custom]
            if dates != sorted(dates) or len(set(dates)) != len(dates):
                raise ValidationError(
                    "Custom instalments must fall on distinct dates, in order.", field="custom"
                )
            total = sum((row.principal for row in self.custom), ZERO)
            if cents(total) != cents(self.principal):
                raise ValidationError(
                    f"The custom instalments repay {cents(total)} of principal, not the "
                    f"{cents(self.principal)} agreed.",
                    field="custom",
                )
            return
        if self.instalments < 1 or self.instalments > 1200:
            raise ValidationError(
                "There must be between 1 and 1,200 instalments.", field="instalments"
            )
        if self.grace_periods < 0 or self.interest_only_periods < 0:
            raise ValidationError(
                "Grace and interest-only periods cannot be negative.", field="grace_periods"
            )
        if self.balloon < ZERO or self.balloon >= self.principal:
            raise ValidationError("A balloon must be less than the principal.", field="balloon")
        if self.model is InterestModel.BALLOON and self.balloon <= ZERO:
            raise ValidationError("A balloon structure needs the balloon amount.", field="balloon")
        if self.model is InterestModel.FIXED and self.fixed_interest <= ZERO:
            raise ValidationError("A fixed charge needs the amount agreed.", field="fixed_interest")
        if (
            self.model in {InterestModel.FLAT, InterestModel.REDUCING_BALANCE}
            or self.model in {InterestModel.INTEREST_ONLY, InterestModel.BALLOON}
        ) and self.annual_rate <= ZERO:
            raise ValidationError(
                f"{self.model.label} needs an interest rate.", field="annual_rate"
            )
        if self.periodic_fee < ZERO:
            raise ValidationError("A fee cannot be negative.", field="periodic_fee")


@dataclass(frozen=True, slots=True)
class ScheduledInstalment:
    sequence: int
    due_on: date
    opening: Decimal
    interest: Decimal
    fees: Decimal
    principal: Decimal
    closing: Decimal
    capitalised: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.interest + self.fees + self.principal


def build_schedule(terms: LoanTerms) -> list[ScheduledInstalment]:
    """The amortisation schedule the terms describe, in whole pesewas.

    Each figure is rounded as it is produced and the last instalment takes whatever
    principal is left, so the principal column always sums to exactly the amount lent.
    """
    terms.validate()
    if terms.model is InterestModel.CUSTOM:
        return _custom(terms)
    if terms.model in {
        InterestModel.REDUCING_BALANCE,
        InterestModel.BALLOON,
        InterestModel.INTEREST_ONLY,
    }:
        return _reducing(terms)
    return _even(terms)


def _custom(terms: LoanTerms) -> list[ScheduledInstalment]:
    rows: list[ScheduledInstalment] = []
    balance = cents(terms.principal)
    for index, row in enumerate(terms.custom, start=1):
        principal = cents(row.principal)
        closing = balance - principal
        rows.append(
            ScheduledInstalment(
                index,
                row.due_on,
                balance,
                cents(row.interest),
                cents(row.fees),
                principal,
                closing,
            )
        )
        balance = closing
    return rows


def _even(terms: LoanTerms) -> list[ScheduledInstalment]:
    """No interest, a fixed charge or a flat rate: interest is known at the outset and spread
    evenly over the instalments that pay it."""
    principal = cents(terms.principal)
    balloon = cents(terms.balloon)
    paying = terms.interest_only_periods + terms.instalments
    total_periods = terms.grace_periods + paying

    if terms.model is InterestModel.FLAT:
        years = Decimal(total_periods) / Decimal(terms.frequency.per_year)
        interest_total = cents(principal * terms.annual_rate / Decimal(100) * years)
    elif terms.model is InterestModel.FIXED:
        interest_total = cents(terms.fixed_interest)
    else:
        interest_total = ZERO

    interest_shares = _even_split(interest_total, paying)
    principal_shares = _even_split(principal - balloon, terms.instalments)
    principal_shares[-1] += balloon
    fee = cents(terms.periodic_fee)

    rows: list[ScheduledInstalment] = []
    balance = principal
    for index in range(total_periods):
        due = nth_due(terms.first_due_on, terms.frequency, index)
        if index < terms.grace_periods:
            rows.append(ScheduledInstalment(index + 1, due, balance, ZERO, ZERO, ZERO, balance))
            continue
        paying_index = index - terms.grace_periods
        interest = interest_shares[paying_index]
        amortising = paying_index - terms.interest_only_periods
        part = principal_shares[amortising] if amortising >= 0 else ZERO
        closing = balance - part
        rows.append(ScheduledInstalment(index + 1, due, balance, interest, fee, part, closing))
        balance = closing
    return rows


def _even_split(total: Decimal, parts: int) -> list[Decimal]:
    """Equal shares in whole pesewas, the rounding left on the last so the earlier
    instalments are the round figure a borrower is quoted."""
    if parts <= 0:
        return []
    share = cents(total / Decimal(parts))
    return [share] * (parts - 1) + [total - share * (parts - 1)]


def _reducing(terms: LoanTerms) -> list[ScheduledInstalment]:
    """Interest on the balance outstanding each period. Grace periods capitalise it;
    interest-only periods pay it without touching the principal; the amortising periods pay
    an even instalment that leaves the balloon, if any, for the last."""
    rate = periodic_rate(terms.annual_rate, terms.frequency, terms.compounding)
    fee = cents(terms.periodic_fee)
    balance = cents(terms.principal)
    rows: list[ScheduledInstalment] = []
    index = 0

    for _ in range(terms.grace_periods):
        accrued = cents(balance * rate)
        due = nth_due(terms.first_due_on, terms.frequency, index)
        rows.append(
            ScheduledInstalment(
                index + 1, due, balance, ZERO, ZERO, ZERO, balance + accrued, capitalised=accrued
            )
        )
        balance += accrued
        index += 1

    bullet = terms.model is InterestModel.INTEREST_ONLY
    interest_only = terms.interest_only_periods + (terms.instalments - 1 if bullet else 0)
    for _ in range(interest_only):
        due = nth_due(terms.first_due_on, terms.frequency, index)
        interest = cents(balance * rate)
        rows.append(ScheduledInstalment(index + 1, due, balance, interest, fee, ZERO, balance))
        index += 1

    amortising = 1 if bullet else terms.instalments
    balloon = balance if bullet else cents(terms.balloon)
    payment = _annuity(balance, balloon, rate, amortising)
    for step in range(amortising):
        due = nth_due(terms.first_due_on, terms.frequency, index)
        interest = cents(balance * rate)
        last = step == amortising - 1
        part = balance if last else max(ZERO, min(balance, payment - interest))
        closing = balance - part
        rows.append(ScheduledInstalment(index + 1, due, balance, interest, fee, part, closing))
        balance = closing
        index += 1
    return rows


def _annuity(balance: Decimal, balloon: Decimal, rate: Decimal, periods: int) -> Decimal:
    """The level instalment that repays ``balance`` over ``periods``, leaving ``balloon``."""
    if rate == ZERO:
        return cents((balance - balloon) / Decimal(periods))
    growth = (1 + rate) ** periods
    present = balance - balloon / growth
    return cents(present * rate / (1 - 1 / growth))


@dataclass(frozen=True, slots=True)
class ScheduleSummary:
    principal: Decimal
    interest: Decimal
    fees: Decimal
    capitalised: Decimal
    total: Decimal
    instalments: int
    final_due_on: date | None


def summarise(rows: Sequence[ScheduledInstalment]) -> ScheduleSummary:
    return ScheduleSummary(
        principal=sum((row.principal for row in rows), ZERO),
        interest=sum((row.interest for row in rows), ZERO),
        fees=sum((row.fees for row in rows), ZERO),
        capitalised=sum((row.capitalised for row in rows), ZERO),
        total=sum((row.total for row in rows), ZERO),
        instalments=len(rows),
        final_due_on=rows[-1].due_on if rows else None,
    )


# -- allocation ----------------------------------------------------------------------------


@dataclass(slots=True)
class Outstanding:
    """What an instalment still asks for, by component."""

    instalment_id: object
    sequence: int
    due_on: date
    owed: dict[Component, Decimal] = field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return sum(self.owed.values(), ZERO)


@dataclass(frozen=True, slots=True)
class Allocation:
    instalment_id: object
    sequence: int
    component: Component
    amount: Decimal


def allocate_repayment(
    amount: Decimal,
    instalments: Sequence[Outstanding],
    order: Sequence[Component] = DEFAULT_ORDER,
) -> tuple[list[Allocation], Decimal]:
    """Settle the oldest instalment first, and within it each component in the agreed order.

    Returns the allocations and whatever the instalments could not absorb.
    """
    remaining = cents(amount)
    if remaining <= ZERO:
        raise ValidationError("A repayment must be more than nothing.", field="amount")
    allocations: list[Allocation] = []
    for item in sorted(instalments, key=lambda each: (each.due_on, each.sequence)):
        for component in order:
            owed = item.owed.get(component, ZERO)
            if owed <= ZERO or remaining <= ZERO:
                continue
            taken = min(owed, remaining)
            allocations.append(Allocation(item.instalment_id, item.sequence, component, taken))
            remaining -= taken
        if remaining <= ZERO:
            break
    return allocations, remaining


# -- standing ------------------------------------------------------------------------------


class AgreementState(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTIVE = "active"
    SETTLED = "settled"
    REFINANCED = "refinanced"
    WRITTEN_OFF = "written_off"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()

    @property
    def closed(self) -> bool:
        return self in {
            AgreementState.SETTLED,
            AgreementState.REFINANCED,
            AgreementState.WRITTEN_OFF,
            AgreementState.REJECTED,
        }


class InstalmentState(StrEnum):
    SCHEDULED = "scheduled"
    DUE_SOON = "due_soon"
    DUE = "due"
    OVERDUE = "overdue"
    PART_PAID = "part_paid"
    PAID = "paid"
    CLOSED = "closed"

    @property
    def label(self) -> str:
        return {
            InstalmentState.SCHEDULED: "Scheduled",
            InstalmentState.DUE_SOON: "Due soon",
            InstalmentState.DUE: "Due today",
            InstalmentState.OVERDUE: "Overdue",
            InstalmentState.PART_PAID: "Part paid",
            InstalmentState.PAID: "Paid",
            InstalmentState.CLOSED: "Closed",
        }[self]


DUE_SOON_DAYS: Final = 14


def instalment_state(
    due_on: date, owed: Decimal, paid: Decimal, today: date, *, closed: bool = False
) -> InstalmentState:
    if closed:
        return InstalmentState.CLOSED
    if owed > ZERO and paid >= owed:
        return InstalmentState.PAID
    if owed <= ZERO:
        return InstalmentState.PAID if due_on <= today else InstalmentState.SCHEDULED
    if due_on < today:
        return InstalmentState.OVERDUE
    if paid > ZERO:
        return InstalmentState.PART_PAID
    if due_on == today:
        return InstalmentState.DUE
    if (due_on - today).days <= DUE_SOON_DAYS:
        return InstalmentState.DUE_SOON
    return InstalmentState.SCHEDULED


class ScheduleState(StrEnum):
    PROPOSED = "proposed"
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DECLINED = "declined"


class RevisionKind(StrEnum):
    ORIGINAL = "original"
    RESCHEDULE = "reschedule"
    PAYMENT_HOLIDAY = "payment_holiday"
    DEFERMENT = "deferment"
    INTEREST_CHANGE = "interest_change"
    RESTRUCTURE = "restructure"
    PREPAYMENT = "prepayment"

    @property
    def label(self) -> str:
        return {
            RevisionKind.ORIGINAL: "Original signed schedule",
            RevisionKind.RESCHEDULE: "Rescheduled",
            RevisionKind.PAYMENT_HOLIDAY: "Payment holiday",
            RevisionKind.DEFERMENT: "Deferment",
            RevisionKind.INTEREST_CHANGE: "Interest change",
            RevisionKind.RESTRUCTURE: "Restructured",
            RevisionKind.PREPAYMENT: "Prepayment recalculation",
        }[self]


class SecurityKind(StrEnum):
    COLLATERAL = "collateral"
    GUARANTOR = "guarantor"
    COVENANT = "covenant"

    @property
    def label(self) -> str:
        return self.value.capitalize()


# -- early settlement ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SettlementQuote:
    on: date
    principal: Decimal
    interest_due: Decimal
    interest_accrued: Decimal
    fees_due: Decimal
    penalties_due: Decimal
    early_settlement_fee: Decimal

    @property
    def total(self) -> Decimal:
        return (
            self.principal
            + self.interest_due
            + self.interest_accrued
            + self.fees_due
            + self.penalties_due
            + self.early_settlement_fee
        )


def accrued_since(
    principal: Decimal,
    annual_percent: Decimal,
    model: InterestModel,
    last_due: date,
    on: date,
) -> Decimal:
    """Interest earned on the outstanding principal since the last instalment fell due.

    Only a reducing-balance agreement accrues day by day; a flat or fixed charge is a sum
    agreed in advance, and the agreement, not the calendar, decides how much of it a
    borrower settling early still owes.
    """
    if model not in {
        InterestModel.REDUCING_BALANCE,
        InterestModel.BALLOON,
        InterestModel.INTEREST_ONLY,
    }:
        return ZERO
    days = (on - last_due).days
    if days <= 0 or principal <= ZERO:
        return ZERO
    return cents(principal * annual_percent / Decimal(100) * Decimal(days) / Decimal(365))


def early_settlement_fee(principal: Decimal, percent: Decimal) -> Decimal:
    return cents(principal * percent / Decimal(100)) if percent > ZERO else ZERO


# -- registers and analysis ----------------------------------------------------------------


class DebtKind(StrEnum):
    """What a register line is, so totals can be split as PRD §25.6 asks."""

    TRADE_RECEIVABLE = "trade_receivable"
    LOAN_ISSUED = "loan_issued"
    OTHER_RECEIVABLE = "other_receivable"
    SUPPLIER = "supplier"
    EXPENSE = "expense"
    STAFF_CLAIM = "staff_claim"
    TAX = "tax"
    COMMISSION = "commission"
    REFUND = "refund"
    BORROWING = "borrowing"
    OTHER_PAYABLE = "other_payable"

    @property
    def label(self) -> str:
        return _DEBT_LABELS[self]

    @property
    def owed_to_us(self) -> bool:
        return self in RECEIVABLE_KINDS


_DEBT_LABELS: Final = {
    DebtKind.TRADE_RECEIVABLE: "Customer invoice",
    DebtKind.LOAN_ISSUED: "Loan or advance issued",
    DebtKind.OTHER_RECEIVABLE: "Other receivable",
    DebtKind.SUPPLIER: "Supplier invoice",
    DebtKind.EXPENSE: "Unpaid expense",
    DebtKind.STAFF_CLAIM: "Staff claim",
    DebtKind.TAX: "Tax payable",
    DebtKind.COMMISSION: "Commission payable",
    DebtKind.REFUND: "Refund due",
    DebtKind.BORROWING: "Borrowing",
    DebtKind.OTHER_PAYABLE: "Other payable",
}

RECEIVABLE_KINDS: Final = frozenset(
    {DebtKind.TRADE_RECEIVABLE, DebtKind.LOAN_ISSUED, DebtKind.OTHER_RECEIVABLE}
)


class DebtStanding(StrEnum):
    ACTIVE = "active"
    PART_PAID = "part_paid"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"
    DISPUTED = "disputed"
    RESTRUCTURED = "restructured"
    SETTLED = "settled"
    WRITTEN_OFF = "written_off"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


def debt_standing(
    original: Decimal,
    settled: Decimal,
    due_on: date | None,
    today: date,
    *,
    disputed: bool = False,
    restructured: bool = False,
    written_off: bool = False,
) -> DebtStanding:
    if written_off:
        return DebtStanding.WRITTEN_OFF
    if settled >= original:
        return DebtStanding.SETTLED
    if disputed:
        return DebtStanding.DISPUTED
    if due_on is not None and due_on < today:
        return DebtStanding.OVERDUE
    if restructured:
        return DebtStanding.RESTRUCTURED
    if due_on is not None and (due_on - today).days <= DUE_SOON_DAYS:
        return DebtStanding.DUE_SOON
    if settled > ZERO:
        return DebtStanding.PART_PAID
    return DebtStanding.ACTIVE


AGEING_BUCKETS: Final = (
    ("current", "Not yet due", None, 0),
    ("1_30", "1\u201330", 1, 30),
    ("31_60", "31\u201360", 31, 60),
    ("61_90", "61\u201390", 61, 90),
    ("over_90", "Over 90", 91, None),
)


def ageing_bucket(due_on: date | None, today: date) -> str:
    if due_on is None or due_on >= today:
        return "current"
    days = (today - due_on).days
    for code, _, low, high in AGEING_BUCKETS[1:]:
        if low is not None and days >= low and (high is None or days <= high):
            return code
    return "over_90"


MATURITY_BANDS: Final = (
    ("within_1", "Within a year", 0, 365),
    ("1_2", "One to two years", 366, 730),
    ("2_5", "Two to five years", 731, 1826),
    ("over_5", "Over five years", 1827, None),
)


def maturity_band(due_on: date, today: date) -> str:
    days = max(0, (due_on - today).days)
    for code, _, low, high in MATURITY_BANDS:
        if days >= low and (high is None or days <= high):
            return code
    return "over_5"
