"""Financial management: treasury, receivables, expenses, reconciliation. PRD §20, §29.

Pure. The rule the module rests on: money moving is neither revenue nor expense. A receipt
settles a receivable or creates a liability, a payment settles a payable or an expense
already recognised, and a transfer moves money between two of the business's own
accounts. ``assert_no_income_or_expense`` holds that rule for every treasury posting.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from nickland.domain.errors import ValidationError
from nickland.domain.money import ZERO, money

# Accounts the module posts to, named once.
CASH_ON_HAND: Final = "10100"
BANK: Final = "10200"
MOBILE_MONEY: Final = "10300"
PETTY_CASH: Final = "10400"
TRADE_RECEIVABLES: Final = "11000"
DOUBTFUL_DEBTS_ALLOWANCE: Final = "11010"
RECOVERABLE_INPUT_TAX: Final = "13000"
FIXED_ASSETS: Final = "15000"
OTHER_PAYABLES: Final = "20100"
STAFF_CLAIMS_PAYABLE: Final = "20400"
OUTPUT_TAX: Final = "21000"
CUSTOMER_DEPOSITS: Final = "22000"
QUOTATION_REVENUE: Final = "40000"
SALES_RETURNS: Final = "40950"
INTEREST_INCOME: Final = "70000"
BANK_CHARGES: Final = "71100"
EXCHANGE_DIFFERENCES: Final = "71200"
BAD_DEBT_EXPENSE: Final = "72000"


class FinancialAccountKind(StrEnum):
    CASH_DRAWER = "cash_drawer"
    PETTY_CASH = "petty_cash"
    BANK = "bank"
    MOBILE_MONEY = "mobile_money"

    @property
    def label(self) -> str:
        return _KIND_LABELS[self]

    @property
    def base_account(self) -> str:
        """The ledger account the first account of this kind posts to; later ones post to
        new accounts numbered after it."""
        return _KIND_ACCOUNTS[self]


_KIND_LABELS: Final = {
    FinancialAccountKind.CASH_DRAWER: "Cash",
    FinancialAccountKind.PETTY_CASH: "Petty cash",
    FinancialAccountKind.BANK: "Bank account",
    FinancialAccountKind.MOBILE_MONEY: "Mobile Money wallet",
}

_KIND_ACCOUNTS: Final = {
    FinancialAccountKind.CASH_DRAWER: CASH_ON_HAND,
    FinancialAccountKind.PETTY_CASH: PETTY_CASH,
    FinancialAccountKind.BANK: BANK,
    FinancialAccountKind.MOBILE_MONEY: MOBILE_MONEY,
}

SEEDED_FINANCIAL_ACCOUNTS: Final[tuple[tuple[FinancialAccountKind, str], ...]] = (
    (FinancialAccountKind.CASH_DRAWER, "Cash on hand"),
    (FinancialAccountKind.BANK, "Main bank account"),
    (FinancialAccountKind.MOBILE_MONEY, "Mobile Money wallet"),
    (FinancialAccountKind.PETTY_CASH, "Petty cash"),
)


def next_account_code(base: str, taken: Iterable[str]) -> str:
    """The next free code after a base cash account: 10200 → 10201, 10202, …"""
    used = set(taken)
    stem = int(base)
    for offset in range(1, 100):
        candidate = str(stem + offset)
        if candidate not in used:
            return candidate
    raise ValidationError("No account code is free in this range.")


class TreasuryMovementKind(StrEnum):
    TRANSFER = "transfer"
    BANK_CHARGE = "bank_charge"
    INTEREST = "interest"
    ADJUSTMENT_IN = "adjustment_in"
    ADJUSTMENT_OUT = "adjustment_out"

    @property
    def label(self) -> str:
        return {
            TreasuryMovementKind.TRANSFER: "Transfer",
            TreasuryMovementKind.BANK_CHARGE: "Bank charge",
            TreasuryMovementKind.INTEREST: "Interest received",
            TreasuryMovementKind.ADJUSTMENT_IN: "Adjustment in",
            TreasuryMovementKind.ADJUSTMENT_OUT: "Adjustment out",
        }[self]


INCOME_AND_EXPENSE_KINDS: Final = frozenset({"REV", "COS", "OPEX", "OTH", "TAX"})
"""Account groups whose balances are income or expense."""


def assert_no_income_or_expense(groups: Iterable[str | None], *, what: str) -> None:
    """A transfer touches only the business's own balances."""
    touched = sorted({group for group in groups if group in INCOME_AND_EXPENSE_KINDS})
    if touched:
        raise ValidationError(
            f"{what} moves money between balances and posts no income or expense."
        )


# --------------------------------------------------------------------------------------
# Receivables
# --------------------------------------------------------------------------------------


class InvoiceOrigin(StrEnum):
    SALES_ORDER = "sales_order"
    POS_SALE = "pos_sale"
    """A register sale on account: the receivable already posted with the sale."""


class InvoiceState(StrEnum):
    OPEN = "open"
    PART_SETTLED = "part_settled"
    SETTLED = "settled"
    WRITTEN_OFF = "written_off"

    @property
    def label(self) -> str:
        return {
            InvoiceState.OPEN: "Open",
            InvoiceState.PART_SETTLED: "Part settled",
            InvoiceState.SETTLED: "Settled",
            InvoiceState.WRITTEN_OFF: "Written off",
        }[self]


def invoice_state(total: Decimal, settled: Decimal, written_off: Decimal) -> InvoiceState:
    """Where an invoice stands from what has been received, credited and written off."""
    if written_off > ZERO and settled + written_off >= total:
        return InvoiceState.WRITTEN_OFF
    if settled >= total:
        return InvoiceState.SETTLED
    if settled > ZERO:
        return InvoiceState.PART_SETTLED
    return InvoiceState.OPEN


class DepositCondition(StrEnum):
    """When an advance stops being owed back to the customer and may meet an invoice."""

    ON_INVOICE = "on_invoice"
    """When the goods it was paid for are invoiced."""
    ON_DELIVERY = "on_delivery"
    """When the goods it was paid for have been delivered and invoiced."""

    @property
    def label(self) -> str:
        return {
            DepositCondition.ON_INVOICE: "Applied when invoiced",
            DepositCondition.ON_DELIVERY: "Applied when delivered",
        }[self]


class DepositState(StrEnum):
    HELD = "held"
    PART_APPLIED = "part_applied"
    APPLIED = "applied"
    REFUNDED = "refunded"


def deposit_condition_met(condition: DepositCondition, *, invoiced: bool, delivered: bool) -> bool:
    if condition is DepositCondition.ON_INVOICE:
        return invoiced
    return invoiced and delivered


class CollectionNoteKind(StrEnum):
    CONTACT = "contact"
    PROMISE = "promise"
    DISPUTE = "dispute"
    RESOLVED = "resolved"

    @property
    def label(self) -> str:
        return {
            CollectionNoteKind.CONTACT: "Contact",
            CollectionNoteKind.PROMISE: "Promise to pay",
            CollectionNoteKind.DISPUTE: "Dispute raised",
            CollectionNoteKind.RESOLVED: "Dispute resolved",
        }[self]


@dataclass(frozen=True, slots=True)
class CreditPosition:
    """What a customer owes and may still owe."""

    limit: Decimal | None
    outstanding: Decimal
    on_hold: bool

    def headroom(self) -> Decimal | None:
        return None if self.limit is None else money(self.limit - self.outstanding)

    def refuses(self, amount: Decimal) -> str | None:
        """Why a further invoice of ``amount`` needs approval, or None."""
        if self.on_hold:
            return "The customer's credit is on hold."
        if self.limit is not None and money(self.outstanding + amount) > self.limit:
            return (
                f"This invoice takes the customer to {money(self.outstanding + amount)} "
                f"against a credit limit of {money(self.limit)}."
            )
        return None
