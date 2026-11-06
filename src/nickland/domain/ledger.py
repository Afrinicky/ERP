"""Accounting rules: account classification, balance direction and journal validity.

Satisfies PRD §29.2. Every financial consequence in the system is expressed as a
balanced journal entry; this module decides what balanced means, what an account is,
and which way a figure moves it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.money import ZERO, money


class AccountKind(StrEnum):
    """The five classes of account, and the statement each belongs to."""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"

    @property
    def normal_balance(self) -> Side:
        """The side on which this class of account normally stands.

        An asset or an expense is increased by a debit; everything else by a credit.
        """
        if self in (AccountKind.ASSET, AccountKind.EXPENSE):
            return Side.DEBIT
        return Side.CREDIT

    @property
    def statement(self) -> Statement:
        if self in (AccountKind.ASSET, AccountKind.LIABILITY, AccountKind.EQUITY):
            return Statement.BALANCE_SHEET
        return Statement.PROFIT_AND_LOSS

    @property
    def closes_at_year_end(self) -> bool:
        """Whether the account's balance is carried to retained earnings at year end.

        Revenue and expense measure a period and begin the next at nothing. An asset,
        liability or equity account carries forward.
        """
        return self.statement is Statement.PROFIT_AND_LOSS


class Side(StrEnum):
    """Which side of an entry a figure stands on."""

    DEBIT = "debit"
    CREDIT = "credit"

    @property
    def opposite(self) -> Side:
        return Side.CREDIT if self is Side.DEBIT else Side.DEBIT


class Statement(StrEnum):
    """The statement an account appears on."""

    BALANCE_SHEET = "balance_sheet"
    PROFIT_AND_LOSS = "profit_and_loss"


class CashFlowCategory(StrEnum):
    """Where a movement appears in the cash-flow statement, per PRD §20.6.

    The classification is a property of the account, because the same payment is
    operating, investing or financing according to what it was for.
    """

    OPERATING = "operating"
    INVESTING = "investing"
    FINANCING = "financing"
    CASH = "cash"
    """A cash or bank account: the thing the statement reconciles to, not a category."""
    NONE = "none"


class JournalState(StrEnum):
    """A journal entry's standing.

    A posted entry is never amended. Correction is by reversal, per PRD §10.
    """

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    POSTED = "posted"
    REVERSED = "reversed"
    """Posted, and subsequently undone by a reversing entry."""
    REJECTED = "rejected"


class PeriodState(StrEnum):
    """An accounting period's standing, per PRD §29.2."""

    OPEN = "open"
    CLOSED = "closed"
    REOPENED = "reopened"
    """Closed and then reopened under authority. Posting is permitted again."""

    @property
    def permits_posting(self) -> bool:
        return self in (PeriodState.OPEN, PeriodState.REOPENED)


class AccountingBasis(StrEnum):
    """Which basis a report is prepared on, per PRD §29.2.

    Every report states its basis. The two are never mixed within one statement.
    """

    ACCRUAL = "accrual"
    CASH = "cash"


class JournalSource(StrEnum):
    """What produced an entry. A manual journal is the only one a person writes."""

    MANUAL = "manual"
    SALES = "sales"
    POINT_OF_SALE = "point_of_sale"
    PURCHASES = "purchases"
    INVENTORY = "inventory"
    TAXATION = "taxation"
    PAYMENT = "payment"
    RECEIPT = "receipt"
    COMMISSION = "commission"
    FINANCING = "financing"
    OPENING_BALANCE = "opening_balance"
    YEAR_END = "year_end"
    REVERSAL = "reversal"


class SubledgerKind(StrEnum):
    """The parties whose balances are tracked against a control account, per PRD §29.2."""

    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    EMPLOYEE = "employee"
    AFFILIATE = "affiliate"
    AUTHORITY = "authority"
    LENDER = "lender"
    BORROWER = "borrower"


_ACCOUNT_CODE = re.compile(r"^\d{4,8}$")


def validate_account_code(code: str, *, field_name: str = "code") -> str:
    """Normalise and validate an account code.

    A numeric code is required because the chart is read in code order and the leading
    digit carries the class.
    """
    candidate = code.strip()
    if not _ACCOUNT_CODE.match(candidate):
        raise ValidationError(
            "An account code is 4 to 8 digits. The leading digit denotes the class: "
            "1 asset, 2 liability, 3 equity, 4 revenue, 5 to 8 expense.",
            field=field_name,
        )
    return candidate


def kind_for_code(code: str) -> AccountKind:
    """The class implied by an account code's leading digit."""
    leading = validate_account_code(code)[0]
    return {
        "1": AccountKind.ASSET,
        "2": AccountKind.LIABILITY,
        "3": AccountKind.EQUITY,
        "4": AccountKind.REVENUE,
    }.get(leading, AccountKind.EXPENSE)


def assert_code_matches_kind(code: str, kind: AccountKind) -> None:
    """Raise where a code's leading digit contradicts the class it is given.

    The chart is read in code order, so a liability numbered in the asset range would
    appear among the assets on every report.
    """
    implied = kind_for_code(code)
    if implied is not kind:
        raise ValidationError(
            f"Code {code} is in the {implied.value} range but the account is a "
            f"{kind.value}. Renumber it, or correct the classification.",
            field="code",
        )


@dataclass(frozen=True, slots=True)
class EntryLine:
    """One line of a journal entry.

    A line carries an amount on exactly one side. A line that is neither a debit nor a
    credit, or both, is not a line.
    """

    account_code: str
    side: Side
    amount: Decimal
    narration: str | None = None
    party_id: object | None = None
    subledger: SubledgerKind | None = None
    branch_id: object | None = None

    def __post_init__(self) -> None:
        if self.amount <= ZERO:
            raise ValidationError(
                "A journal line carries an amount above zero. To move a figure the "
                "other way, put it on the other side.",
                field="amount",
            )

    @property
    def debit(self) -> Decimal:
        return self.amount if self.side is Side.DEBIT else ZERO

    @property
    def credit(self) -> Decimal:
        return self.amount if self.side is Side.CREDIT else ZERO

    def reversed(self) -> EntryLine:
        """The same line on the opposite side."""
        return EntryLine(
            account_code=self.account_code,
            side=self.side.opposite,
            amount=self.amount,
            narration=self.narration,
            party_id=self.party_id,
            subledger=self.subledger,
            branch_id=self.branch_id,
        )


@dataclass(frozen=True, slots=True)
class BalanceCheck:
    """The outcome of checking an entry balances."""

    debits: Decimal
    credits: Decimal
    difference: Decimal
    balanced: bool
    detail: str


def check_balance(lines: list[EntryLine]) -> BalanceCheck:
    """Check that debits equal credits.

    The difference is reported with its side, because a user correcting an entry wants
    to know which way it is out, not merely that it is.
    """
    if not lines:
        return BalanceCheck(ZERO, ZERO, ZERO, False, "An entry requires at least two lines.")

    debit_total = money(sum((line.debit for line in lines), ZERO))
    credit_total = money(sum((line.credit for line in lines), ZERO))
    difference = money(debit_total - credit_total)

    if len(lines) < 2:
        return BalanceCheck(
            debit_total,
            credit_total,
            difference,
            False,
            "An entry requires at least two lines.",
        )
    if difference == ZERO:
        return BalanceCheck(debit_total, credit_total, difference, True, "Debits equal credits.")

    heavier = "Debits" if difference > ZERO else "Credits"
    return BalanceCheck(
        debit_total,
        credit_total,
        difference,
        False,
        f"{heavier} exceed the other side by {abs(difference)}. "
        "An entry posts only when the two are equal.",
    )


def assert_balanced(lines: list[EntryLine]) -> BalanceCheck:
    """Raise unless the entry balances. Called before every posting."""
    outcome = check_balance(lines)
    if not outcome.balanced:
        raise ValidationError(
            outcome.detail,
            field="lines",
            debits=str(outcome.debits),
            credits=str(outcome.credits),
            difference=str(outcome.difference),
        )
    return outcome


def signed_movement(kind: AccountKind, debit: Decimal, credit: Decimal) -> Decimal:
    """The movement in an account's own direction.

    An asset rises on a debit; a liability rises on a credit. Reporting a balance
    without applying the account's direction is how a credit balance comes to be shown
    as negative on a statement where it is perfectly normal.
    """
    if kind.normal_balance is Side.DEBIT:
        return money(debit - credit)
    return money(credit - debit)


def presentation_side(kind: AccountKind, balance: Decimal) -> Side:
    """Which column a balance belongs in on a trial balance."""
    if balance >= ZERO:
        return kind.normal_balance
    return kind.normal_balance.opposite


@dataclass(frozen=True, slots=True)
class PeriodWindow:
    """An accounting period's dates."""

    starts_on: date
    ends_on: date

    def __post_init__(self) -> None:
        if self.ends_on < self.starts_on:
            raise ValidationError("A period cannot end before it begins.", field="ends_on")

    def contains(self, on: date) -> bool:
        return self.starts_on <= on <= self.ends_on

    @property
    def label(self) -> str:
        if self.starts_on.year != self.ends_on.year:
            return f"{self.starts_on:%b %Y} to {self.ends_on:%b %Y}"
        if self.starts_on.month == self.ends_on.month:
            return f"{self.starts_on:%B %Y}"
        return f"{self.starts_on:%b} to {self.ends_on:%b %Y}"


def assert_posting_permitted(state: PeriodState, window: PeriodWindow, on: date) -> None:
    """Raise where a period will not accept a posting, per PRD §29.2."""
    if not state.permits_posting:
        raise ConflictError(
            f"{window.label} is closed. Post to an open period, or ask an "
            "administrator to reopen it, which is recorded.",
            period=window.label,
            state=state.value,
        )


def months_in_year(start_month: int) -> list[tuple[int, int]]:
    """The twelve month-offsets of a financial year beginning in ``start_month``.

    Returns ``(month, year_offset)`` pairs, so a year beginning in July runs July to
    December of one calendar year and January to June of the next.
    """
    if not 1 <= start_month <= 12:
        raise ValidationError(
            "A financial year begins in a month between 1 and 12.",
            field="financial_year_start_month",
        )
    return [
        ((start_month - 1 + offset) % 12 + 1, (start_month - 1 + offset) // 12)
        for offset in range(12)
    ]


@dataclass(slots=True)
class TrialBalanceRow:
    """One account on a trial balance."""

    code: str
    name: str
    kind: AccountKind
    debit: Decimal = ZERO
    credit: Decimal = ZERO

    @property
    def balance(self) -> Decimal:
        """The movement in the account's own direction."""
        return signed_movement(self.kind, self.debit, self.credit)

    @property
    def presented_debit(self) -> Decimal:
        net = money(self.debit - self.credit)
        return net if net > ZERO else ZERO

    @property
    def presented_credit(self) -> Decimal:
        net = money(self.credit - self.debit)
        return net if net > ZERO else ZERO


@dataclass(slots=True)
class TrialBalance:
    """A trial balance. It sums to zero or it is not one.

    It carries the date and basis it was prepared on. A column of figures with no
    date against it cannot be checked, filed or compared with the one before.
    """

    as_at: date
    basis: AccountingBasis = AccountingBasis.ACCRUAL
    starts_on: date | None = None
    rows: list[TrialBalanceRow] = field(default_factory=list)

    @property
    def total_debit(self) -> Decimal:
        return money(sum((row.presented_debit for row in self.rows), ZERO))

    @property
    def total_credit(self) -> Decimal:
        return money(sum((row.presented_credit for row in self.rows), ZERO))

    @property
    def difference(self) -> Decimal:
        return money(self.total_debit - self.total_credit)

    @property
    def balanced(self) -> bool:
        return self.difference == ZERO


def assert_trial_balance_balanced(trial: TrialBalance) -> None:
    """Raise where a trial balance does not sum to zero.

    This should be impossible, because no unbalanced entry can post. It is asserted at
    period close so that an impossibility is caught rather than carried forward.
    """
    if not trial.balanced:
        raise ConflictError(
            f"The trial balance is out by {trial.difference}. No unbalanced entry can "
            "post, so this indicates a fault rather than a bookkeeping error. "
            "Report it before closing the period.",
            difference=str(trial.difference),
        )
