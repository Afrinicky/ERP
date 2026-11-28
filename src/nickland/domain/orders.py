"""Sales orders and procurement: the states, transitions and rules. PRD §6.8.

Pure, like the rest of the domain. The fulfilment position of an order, the variance
between three documents, and the age of a debt are each a function of figures, and a
function of figures is tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final

from nickland.domain.errors import ValidationError
from nickland.domain.money import ZERO, money, present
from nickland.domain.pricing import assert_transition

# --------------------------------------------------------------------------------------
# States
# --------------------------------------------------------------------------------------


class AcceptanceMethod(StrEnum):
    """How the customer's acceptance arrived."""

    PURCHASE_ORDER = "purchase_order"
    SIGNED_QUOTATION = "signed_quotation"
    EMAIL = "email"
    VERBAL = "verbal"


class SalesOrderState(StrEnum):
    OPEN = "open"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class SalesOrderLineState(StrEnum):
    OPEN = "open"
    BACKORDERED = "backordered"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class SourceKind(StrEnum):
    STOCK = "stock"
    SUPPLIER = "supplier"


class PurchaseOrderState(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    ISSUED = "issued"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CLOSED = "closed"
    CANCELLED = "cancelled"


PURCHASE_ORDER_TRANSITIONS: Final[dict[PurchaseOrderState, frozenset[PurchaseOrderState]]] = {
    PurchaseOrderState.DRAFT: frozenset(
        {PurchaseOrderState.APPROVED, PurchaseOrderState.CANCELLED}
    ),
    PurchaseOrderState.APPROVED: frozenset(
        {PurchaseOrderState.ISSUED, PurchaseOrderState.DRAFT, PurchaseOrderState.CANCELLED}
    ),
    PurchaseOrderState.ISSUED: frozenset(
        {
            PurchaseOrderState.PARTIALLY_RECEIVED,
            PurchaseOrderState.RECEIVED,
            PurchaseOrderState.CANCELLED,
        }
    ),
    PurchaseOrderState.PARTIALLY_RECEIVED: frozenset(
        {PurchaseOrderState.RECEIVED, PurchaseOrderState.CLOSED}
    ),
    PurchaseOrderState.RECEIVED: frozenset({PurchaseOrderState.CLOSED}),
    PurchaseOrderState.CLOSED: frozenset(),
    PurchaseOrderState.CANCELLED: frozenset(),
}

RECEIVABLE_STATES: Final = frozenset(
    {PurchaseOrderState.ISSUED, PurchaseOrderState.PARTIALLY_RECEIVED}
)
"""The states in which goods may be received against an order."""


class GoodsReceiptState(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"


class SupplierInvoiceState(StrEnum):
    DRAFT = "draft"
    VARIANCE = "variance"
    MATCHED = "matched"
    POSTED = "posted"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    CANCELLED = "cancelled"


SUPPLIER_INVOICE_TRANSITIONS: Final[dict[SupplierInvoiceState, frozenset[SupplierInvoiceState]]] = {
    SupplierInvoiceState.DRAFT: frozenset(
        {
            SupplierInvoiceState.VARIANCE,
            SupplierInvoiceState.MATCHED,
            SupplierInvoiceState.CANCELLED,
        }
    ),
    SupplierInvoiceState.VARIANCE: frozenset(
        {SupplierInvoiceState.MATCHED, SupplierInvoiceState.DRAFT, SupplierInvoiceState.CANCELLED}
    ),
    SupplierInvoiceState.MATCHED: frozenset({SupplierInvoiceState.POSTED}),
    SupplierInvoiceState.POSTED: frozenset(
        {SupplierInvoiceState.PARTIALLY_PAID, SupplierInvoiceState.PAID}
    ),
    SupplierInvoiceState.PARTIALLY_PAID: frozenset({SupplierInvoiceState.PAID}),
    SupplierInvoiceState.PAID: frozenset(),
    SupplierInvoiceState.CANCELLED: frozenset(),
}

PAYABLE_STATES: Final = frozenset(
    {SupplierInvoiceState.POSTED, SupplierInvoiceState.PARTIALLY_PAID}
)
"""The states in which an invoice carries an outstanding payable."""


class VarianceKind(StrEnum):
    QUANTITY = "quantity"
    PRICE = "price"


class VarianceState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PaymentMethod(StrEnum):
    """Where the money left from. Each maps to a cash account in the chart."""

    CASH = "cash"
    BANK = "bank"
    MOBILE_MONEY = "mobile_money"


PAYMENT_ACCOUNTS: Final[dict[PaymentMethod, str]] = {
    PaymentMethod.CASH: "10100",
    PaymentMethod.BANK: "10200",
    PaymentMethod.MOBILE_MONEY: "10300",
}

# Account codes the procurement postings use, named once.
INVENTORY: Final = "12000"
GOODS_IN_QUARANTINE: Final = "12100"
RECOVERABLE_INPUT_TAX: Final = "13000"
TRADE_PAYABLES: Final = "20000"
GOODS_RECEIVED_NOT_INVOICED: Final = "20150"
WITHHOLDING_TAX_PAYABLE: Final = "21100"


def assert_purchase_order_transition(
    current: PurchaseOrderState, requested: PurchaseOrderState
) -> None:
    assert_transition("Purchase order", PURCHASE_ORDER_TRANSITIONS, current, requested)


def assert_supplier_invoice_transition(
    current: SupplierInvoiceState, requested: SupplierInvoiceState
) -> None:
    assert_transition("Supplier invoice", SUPPLIER_INVOICE_TRANSITIONS, current, requested)


# --------------------------------------------------------------------------------------
# Fulfilment
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinePosition:
    """Where one order line stands."""

    ordered: Decimal
    cancelled: Decimal
    procured: Decimal
    """Ordered from suppliers, or drawn from stock, against this line."""
    received: Decimal

    @property
    def outstanding(self) -> Decimal:
        """What is still owed to the customer."""
        return money(max(ZERO, self.ordered - self.cancelled - self.received))

    @property
    def unprocured(self) -> Decimal:
        """What nobody has yet been asked to supply."""
        return money(max(ZERO, self.ordered - self.cancelled - self.procured))

    @property
    def state(self) -> SalesOrderLineState:
        if self.ordered - self.cancelled <= ZERO:
            return SalesOrderLineState.CANCELLED
        if self.outstanding <= ZERO:
            return SalesOrderLineState.FULFILLED
        if self.procured > self.received:
            # Something has been asked of a supplier and has not arrived: the customer
            # waits on a delivery, which is what a backorder is. What nobody has yet
            # been asked to supply is merely open.
            return SalesOrderLineState.BACKORDERED
        return SalesOrderLineState.OPEN


def order_state(lines: list[LinePosition]) -> SalesOrderState:
    """The order's state, derived from its lines and never set by hand."""
    if not lines:
        return SalesOrderState.OPEN
    states = [line.state for line in lines]
    if all(state is SalesOrderLineState.CANCELLED for state in states):
        return SalesOrderState.CANCELLED
    live = [state for state in states if state is not SalesOrderLineState.CANCELLED]
    if all(state is SalesOrderLineState.FULFILLED for state in live):
        return SalesOrderState.FULFILLED
    if any(line.received > ZERO for line in lines) or any(
        state is SalesOrderLineState.FULFILLED for state in live
    ):
        return SalesOrderState.PARTIALLY_FULFILLED
    return SalesOrderState.OPEN


def purchase_order_state_after_receipt(
    ordered: Decimal, received: Decimal, current: PurchaseOrderState
) -> PurchaseOrderState:
    """Where an issued order stands once a receipt has been posted against it."""
    if current not in RECEIVABLE_STATES:
        return current
    if received >= ordered:
        return PurchaseOrderState.RECEIVED
    if received > ZERO:
        return PurchaseOrderState.PARTIALLY_RECEIVED
    return current


# --------------------------------------------------------------------------------------
# Three-way match
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchTolerance:
    """How far an invoiced price may stray from the ordered one before it is a variance.

    Business-wide and zero by default: a tolerance is a decision to pay small differences
    without asking, and it should be taken on purpose.
    """

    absolute: Decimal = ZERO
    """Per unit, in base currency."""
    rate: Decimal = ZERO
    """A fraction of the ordered unit cost."""

    def allows(self, ordered: Decimal, invoiced: Decimal) -> bool:
        difference = abs(invoiced - ordered)
        return difference <= self.absolute or (
            ordered > ZERO and difference <= money(ordered * self.rate)
        )


@dataclass(frozen=True, slots=True)
class Variance:
    """One difference between the three documents, with the money at stake."""

    kind: VarianceKind
    expected: Decimal
    actual: Decimal

    @property
    def difference(self) -> Decimal:
        return money(self.actual - self.expected)

    def amount_at_stake(self, *, quantity: Decimal, unit_cost: Decimal) -> Decimal:
        """What approving this variance pays for.

        A quantity variance costs its extra units at the invoiced price; a price
        variance costs the difference on every unit invoiced.
        """
        if self.kind is VarianceKind.QUANTITY:
            return money(max(ZERO, self.difference) * unit_cost)
        return money(self.difference * quantity)


def match_line(
    *,
    ordered_quantity: Decimal,
    accepted_quantity: Decimal,
    ordered_unit_cost: Decimal,
    invoiced_quantity: Decimal,
    invoiced_unit_cost: Decimal,
    tolerance: MatchTolerance = MatchTolerance(),
) -> list[Variance]:
    """The variances one invoice line raises against its order and receipts.

    Quantity is checked against what was accepted on receipt rather than what was
    ordered, because an invoice for goods that have not arrived is the case the match
    exists to catch; where nothing has been received the accepted quantity is zero and
    the whole invoiced quantity is a variance.
    """
    if invoiced_quantity <= ZERO:
        raise ValidationError("An invoice line carries a quantity above zero.", field="quantity")
    found: list[Variance] = []
    allowed = min(ordered_quantity, accepted_quantity)
    if invoiced_quantity > allowed:
        found.append(
            Variance(
                kind=VarianceKind.QUANTITY,
                expected=present(allowed, 6),
                actual=present(invoiced_quantity, 6),
            )
        )
    if not tolerance.allows(ordered_unit_cost, invoiced_unit_cost):
        found.append(
            Variance(
                kind=VarianceKind.PRICE,
                expected=money(ordered_unit_cost),
                actual=money(invoiced_unit_cost),
            )
        )
    return found


# --------------------------------------------------------------------------------------
# Payables and cost variance
# --------------------------------------------------------------------------------------


class AgeingBucket(StrEnum):
    CURRENT = "current"
    DAYS_1_30 = "1_30"
    DAYS_31_60 = "31_60"
    DAYS_61_90 = "61_90"
    OVER_90 = "over_90"


def ageing_bucket(due_on: date, as_at: date) -> AgeingBucket:
    """How overdue a debt is, by the buckets the register reports."""
    overdue = (as_at - due_on).days
    if overdue <= 0:
        return AgeingBucket.CURRENT
    if overdue <= 30:
        return AgeingBucket.DAYS_1_30
    if overdue <= 60:
        return AgeingBucket.DAYS_31_60
    if overdue <= 90:
        return AgeingBucket.DAYS_61_90
    return AgeingBucket.OVER_90


@dataclass(frozen=True, slots=True)
class CostVariance:
    """Quoted landed cost against what procurement actually cost, per unit."""

    expected_unit_cost: Decimal
    actual_unit_cost: Decimal | None
    quantity: Decimal

    @property
    def is_known(self) -> bool:
        return self.actual_unit_cost is not None

    @property
    def per_unit(self) -> Decimal | None:
        if self.actual_unit_cost is None:
            return None
        return money(self.actual_unit_cost - self.expected_unit_cost)

    @property
    def total(self) -> Decimal | None:
        per_unit = self.per_unit
        return None if per_unit is None else money(per_unit * self.quantity)


def allocate_payment(
    amount: Decimal, outstanding: list[tuple[object, Decimal]]
) -> list[tuple[object, Decimal]]:
    """Spread a payment across invoices, oldest first, never beyond what each owes.

    Returns what was allocated to each. The caller refuses an amount that exceeds the
    total outstanding; an overpayment is a prepayment, which is a different record.
    """
    if amount <= ZERO:
        raise ValidationError("A payment carries an amount above zero.", field="amount")
    remaining = money(amount)
    allocated: list[tuple[object, Decimal]] = []
    for key, owed in outstanding:
        if remaining <= ZERO:
            break
        if owed <= ZERO:
            continue
        portion = min(owed, remaining)
        allocated.append((key, money(portion)))
        remaining = money(remaining - portion)
    if remaining > ZERO:
        raise ValidationError(
            f"The payment exceeds what is outstanding by {remaining}. Record the excess as "
            "a prepayment rather than against these invoices.",
            field="amount",
        )
    return allocated
