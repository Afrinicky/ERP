"""The point of sale: tenders and their confirmation, sales, shifts, the drawer. PRD §6.10.

Pure. Two rules carry the weight: a sale is complete only when every tender on it is
confirmed and the confirmed money equals the total (ADR-0009), and a drawer's expected cash
is the float plus what came in less what went out, so a count can be reconciled to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from nickland.domain.errors import ConflictError, StateTransitionError, ValidationError
from nickland.domain.money import ZERO, money, multiply

CASH_ON_HAND: Final = "10100"
BANK: Final = "10200"
MOBILE_MONEY: Final = "10300"
PETTY_CASH: Final = "10400"
TRADE_RECEIVABLES: Final = "11000"
POS_REVENUE: Final = "40100"
SALES_RETURNS: Final = "40950"
CASH_OVER_AND_SHORT: Final = "60900"


class TenderMethod(StrEnum):
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    ON_ACCOUNT = "on_account"

    @property
    def label(self) -> str:
        return _METHOD_LABELS[self]

    @property
    def confirmed_on_entry(self) -> bool:
        """Cash is in the hand and an account sale is a promise on the record: neither
        waits for anyone else to confirm it. Everything else does (ADR-0009)."""
        return self in (TenderMethod.CASH, TenderMethod.ON_ACCOUNT)

    @property
    def account(self) -> str:
        return _METHOD_ACCOUNTS[self]


_METHOD_LABELS: Final = {
    TenderMethod.CASH: "Cash",
    TenderMethod.MOBILE_MONEY: "Mobile Money",
    TenderMethod.CARD: "Card",
    TenderMethod.BANK_TRANSFER: "Bank transfer",
    TenderMethod.ON_ACCOUNT: "On account",
}

_METHOD_ACCOUNTS: Final = {
    TenderMethod.CASH: CASH_ON_HAND,
    TenderMethod.MOBILE_MONEY: MOBILE_MONEY,
    TenderMethod.CARD: BANK,
    TenderMethod.BANK_TRANSFER: BANK,
    TenderMethod.ON_ACCOUNT: TRADE_RECEIVABLES,
}


class TenderState(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    ABANDONED = "abandoned"

    @property
    def label(self) -> str:
        return {
            TenderState.PENDING: "Awaiting confirmation",
            TenderState.CONFIRMED: "Confirmed",
            TenderState.FAILED: "Failed",
            TenderState.ABANDONED: "Abandoned",
        }[self]


_TENDER_TRANSITIONS: Final = {
    TenderState.PENDING: frozenset(
        {TenderState.CONFIRMED, TenderState.FAILED, TenderState.ABANDONED}
    ),
}


def assert_tender_transition(current: TenderState, target: TenderState) -> None:
    if target not in _TENDER_TRANSITIONS.get(current, frozenset()):
        raise StateTransitionError("A tender", current.label.lower(), target.label.lower())


class SaleState(StrEnum):
    OPEN = "open"
    PARKED = "parked"
    COMPLETED = "completed"
    VOIDED = "voided"
    """Abandoned before completion (nothing posted), or reversed in full in its shift."""
    REFUNDED = "refunded"
    """Every line returned after completion."""
    PARTIALLY_REFUNDED = "partially_refunded"


EDITABLE_SALE_STATES: Final = frozenset({SaleState.OPEN})


class ShiftState(StrEnum):
    OPEN = "open"
    SUBMITTED = "submitted"
    CLOSED = "closed"


class RefundKind(StrEnum):
    REFUND = "refund"
    EXCHANGE = "exchange"
    VOID = "void"


class CashMovementKind(StrEnum):
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"


DENOMINATIONS: Final[tuple[Decimal, ...]] = tuple(
    Decimal(value)
    for value in ("200", "100", "50", "20", "10", "5", "2", "1", "0.50", "0.20", "0.10", "0.05")
)
"""Ghana cedi notes and coins, largest first: the drawer is counted in these."""


# --------------------------------------------------------------------------------------
# Tender arithmetic
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TenderView:
    method: TenderMethod
    state: TenderState
    amount: Decimal
    """What the tender settles of the sale. For cash, the amount received less change."""


def settled(tenders: list[TenderView]) -> Decimal:
    """Money confirmed against the sale."""
    return money(sum((t.amount for t in tenders if t.state is TenderState.CONFIRMED), ZERO))


def outstanding(total: Decimal, tenders: list[TenderView]) -> Decimal:
    """What is still to be tendered: the total less every tender that has not failed."""
    live = sum(
        (t.amount for t in tenders if t.state in (TenderState.PENDING, TenderState.CONFIRMED)),
        ZERO,
    )
    return money(max(ZERO, total - live))


def assert_completable(total: Decimal, tenders: list[TenderView]) -> None:
    """ADR-0009: every tender still on the sale is confirmed, and they equal the total."""
    pending = [t for t in tenders if t.state is TenderState.PENDING]
    if pending:
        methods = ", ".join(sorted({t.method.label for t in pending}))
        raise ConflictError(
            f"{methods} payment is still awaiting confirmation. A sale completes only when "
            "every payment on it is confirmed; until then nothing is posted and no receipt "
            "is issued.",
            pending=len(pending),
        )
    paid = settled(tenders)
    if paid != money(total):
        raise ConflictError(
            f"{paid} is confirmed against a total of {money(total)}. Take the balance before "
            "completing the sale.",
            settled=str(paid),
            total=str(money(total)),
        )


def cash_change(due: Decimal, received: Decimal) -> tuple[Decimal, Decimal]:
    """For cash handed over against what is due: ``(applied, change)``."""
    due = money(due)
    received = money(received)
    if received <= ZERO:
        raise ValidationError("Cash received is above zero.", field="cash_received")
    if received <= due:
        return received, ZERO
    return due, money(received - due)


# --------------------------------------------------------------------------------------
# The drawer
# --------------------------------------------------------------------------------------


def count_total(counted: dict[Decimal, int]) -> Decimal:
    """A denomination count, totalled."""
    total = ZERO
    for denomination, pieces in counted.items():
        if pieces < 0:
            raise ValidationError("A count is not negative.", field="denominations")
        if money(denomination) not in DENOMINATIONS:
            raise ValidationError(f"{denomination} is not a cedi note or coin.")
        total = money(total + multiply(money(denomination), Decimal(pieces)))
    return total


@dataclass(frozen=True, slots=True)
class DrawerPosition:
    opening_float: Decimal
    cash_sales: Decimal
    """Cash kept from sales: received less change."""
    cash_in: Decimal
    cash_out: Decimal
    cash_refunds: Decimal

    @property
    def expected(self) -> Decimal:
        return money(
            self.opening_float + self.cash_sales + self.cash_in - self.cash_out - self.cash_refunds
        )

    def variance(self, counted: Decimal) -> Decimal:
        """Counted less expected: negative is a shortage."""
        return money(counted - self.expected)
