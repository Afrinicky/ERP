"""The pricing engine: the canonical sequence from supplier cost to total payable.

Governed by [ADR-0007](../../../docs/decisions/adr-0007-pricing-sequence.md) and
[ADR-0008](../../../docs/decisions/adr-0008-affiliate-commission.md), and specified in
the [pricing engine](../../../docs/04-pricing-engine.md).

Every step's parameters are configurable. The order of the steps is not:

    1  supplier cost
    2  cost allocation        → landed cost base
    3  business margin
    4  affiliate commission   → list price
    5  discount               → net price
    6  instalment uplift      → payable before tax
    7  taxation               → total payable

A configurable order would mean no two quotations are comparable, which PRD §6.3
forbids by requiring the build-up to be transparent. So the sequence is fixed and the
rates are not.

This module is a pure function of its inputs. It reads no clock, no session and no
database, so a quotation priced today from stored inputs prices identically next year,
which is what the cost-preservation requirement of PRD §6.3 actually asks for.

Taxation is step 7 and is not computed here. The engine supplies the taxable base and
consumes what the tax engine returns, because a tax rate is a legal question and
belongs with the rules that record it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final

from nickland.domain.errors import StateTransitionError, ValidationError
from nickland.domain.money import (
    DEFAULT_PRESENTATION_PRECISION,
    ZERO,
    allocate,
    divide,
    gross_up,
    money,
    multiply,
    present,
)

ONE: Final = Decimal("1")


# --------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------


class SourceKind(StrEnum):
    """Where a quotation line is to be supplied from.

    One quotation may draw on both, and on several suppliers, per PRD §6.2.
    """

    STOCK = "stock"
    SUPPLIER = "supplier"


class AllocationMethod(StrEnum):
    """How an expense is apportioned across the lines of a document, per PRD §6.3."""

    BY_LINE = "by_line"
    BY_QUANTITY = "by_quantity"
    BY_VALUE = "by_value"
    BY_WEIGHT = "by_weight"
    BY_VOLUME = "by_volume"
    EQUAL_SHARE = "equal_share"
    MANUAL = "manual"


ALLOCATION_LABELS: Final[dict[AllocationMethod, tuple[str, str]]] = {
    AllocationMethod.BY_LINE: ("By line", "Charged in full to one nominated line"),
    AllocationMethod.BY_QUANTITY: ("By quantity", "In proportion to units"),
    AllocationMethod.BY_VALUE: ("By value", "In proportion to extended line cost"),
    AllocationMethod.BY_WEIGHT: ("By weight", "In proportion to total line weight"),
    AllocationMethod.BY_VOLUME: ("By volume", "In proportion to total line volume"),
    AllocationMethod.EQUAL_SHARE: ("Equal share", "Divided equally across the lines"),
    AllocationMethod.MANUAL: ("Manual", "Entered per line, with the residual shown"),
}


class ExpenseCategory(StrEnum):
    """The allocable cost categories shipped with the system, per PRD §6.3.

    An administrator may configure further categories; these are the ones seeded, so
    that a first quotation can allocate freight without configuration.
    """

    TRANSPORT = "transport"
    HANDLING = "handling"
    PACKAGING = "packaging"
    FREIGHT = "freight"
    CLEARING = "clearing"
    INSURANCE = "insurance"
    BANK_CHARGES = "bank_charges"
    IMPORT_DUTY = "import_duty"
    PORT_FEES = "port_fees"


EXPENSE_CATEGORY_LABELS: Final[dict[ExpenseCategory, str]] = {
    ExpenseCategory.TRANSPORT: "Transport",
    ExpenseCategory.HANDLING: "Handling",
    ExpenseCategory.PACKAGING: "Packaging",
    ExpenseCategory.FREIGHT: "Freight",
    ExpenseCategory.CLEARING: "Clearing",
    ExpenseCategory.INSURANCE: "Insurance",
    ExpenseCategory.BANK_CHARGES: "Bank charges",
    ExpenseCategory.IMPORT_DUTY: "Import duty",
    ExpenseCategory.PORT_FEES: "Port fees",
}

DEFAULT_ALLOCATION: Final[dict[ExpenseCategory, AllocationMethod]] = {
    ExpenseCategory.TRANSPORT: AllocationMethod.BY_WEIGHT,
    ExpenseCategory.HANDLING: AllocationMethod.BY_QUANTITY,
    ExpenseCategory.PACKAGING: AllocationMethod.BY_QUANTITY,
    ExpenseCategory.FREIGHT: AllocationMethod.BY_WEIGHT,
    ExpenseCategory.CLEARING: AllocationMethod.BY_VALUE,
    ExpenseCategory.INSURANCE: AllocationMethod.BY_VALUE,
    ExpenseCategory.BANK_CHARGES: AllocationMethod.BY_VALUE,
    ExpenseCategory.IMPORT_DUTY: AllocationMethod.BY_VALUE,
    ExpenseCategory.PORT_FEES: AllocationMethod.BY_VALUE,
}
"""The basis each category is apportioned on unless configured otherwise.

Freight follows weight because that is what a carrier charges on; duty follows value
because that is what an authority assesses on. Each remains configurable.
"""


class MarginMethod(StrEnum):
    """The two methods PRD §6.3 holds distinct."""

    MARKUP_ON_COST = "markup_on_cost"
    TARGET_GROSS_MARGIN = "target_gross_margin"


MARGIN_LABELS: Final[dict[MarginMethod, tuple[str, str]]] = {
    MarginMethod.MARKUP_ON_COST: ("Markup on cost", "cost * (1 + rate)"),
    MarginMethod.TARGET_GROSS_MARGIN: ("Target gross margin", "cost / (1 - rate)"),
}


class PolicyScope(StrEnum):
    """How specifically a margin policy is addressed. The most specific prevails."""

    BUSINESS = "business"
    CATEGORY = "category"
    PRODUCT = "product"


POLICY_PRECEDENCE: Final[dict[PolicyScope, int]] = {
    PolicyScope.PRODUCT: 0,
    PolicyScope.CATEGORY: 1,
    PolicyScope.BUSINESS: 2,
}
"""Lower wins. A policy set on the product displaces one set on its category."""


class CommissionMethod(StrEnum):
    PERCENTAGE = "percentage"
    FIXED_PER_UNIT = "fixed_per_unit"


class CommissionScope(StrEnum):
    """Which lines a referrer's arrangement reaches, per PRD §6.4."""

    ALL_ELIGIBLE = "all_eligible"
    SELECTED_PRODUCTS = "selected_products"
    SELECTED_CATEGORIES = "selected_categories"
    SELECTED_LINES = "selected_lines"


class CommissionState(StrEnum):
    """ADR-0008. Accrued at invoice, payable on the customer's payment."""

    ACCRUED = "accrued"
    APPROVED = "approved"
    PAYABLE = "payable"
    PAID = "paid"
    CANCELLED = "cancelled"


COMMISSION_TRANSITIONS: Final[dict[CommissionState, frozenset[CommissionState]]] = {
    CommissionState.ACCRUED: frozenset({CommissionState.APPROVED, CommissionState.CANCELLED}),
    CommissionState.APPROVED: frozenset({CommissionState.PAYABLE, CommissionState.CANCELLED}),
    CommissionState.PAYABLE: frozenset({CommissionState.PAID, CommissionState.CANCELLED}),
    CommissionState.PAID: frozenset(),
    CommissionState.CANCELLED: frozenset(),
}


class DiscountMethod(StrEnum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class DiscountLevel(StrEnum):
    LINE = "line"
    DOCUMENT = "document"


class UpliftMethod(StrEnum):
    PERCENTAGE = "percentage"
    FIXED_PER_UNIT = "fixed_per_unit"


class PaymentPlan(StrEnum):
    OUTRIGHT = "outright"
    INSTALMENT = "instalment"


class InstalmentFrequency(StrEnum):
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


INSTALMENT_FREQUENCY_LABELS: Final[dict[InstalmentFrequency, str]] = {
    InstalmentFrequency.WEEKLY: "Weekly",
    InstalmentFrequency.FORTNIGHTLY: "Fortnightly",
    InstalmentFrequency.MONTHLY: "Monthly",
    InstalmentFrequency.QUARTERLY: "Quarterly",
}

INSTALMENTS_PER_MONTH: Final[dict[InstalmentFrequency, int]] = {
    InstalmentFrequency.WEEKLY: 4,
    InstalmentFrequency.FORTNIGHTLY: 2,
    InstalmentFrequency.MONTHLY: 1,
    InstalmentFrequency.QUARTERLY: 0,
}
"""How many instalments a month of duration produces.

A weekly plan is counted at four instalments per month rather than at 52 over 12,
because a schedule a customer signs has to be a whole number of payments at a stated
interval, and four weeks to the month is the arrangement that holds for every month.
Quarterly is handled separately: a month does not produce a whole quarter.
"""


class InstalmentState(StrEnum):
    SCHEDULED = "scheduled"
    DUE = "due"
    OVERDUE = "overdue"
    PAID = "paid"
    WAIVED = "waived"
    RESCHEDULED = "rescheduled"


INSTALMENT_TRANSITIONS: Final[dict[InstalmentState, frozenset[InstalmentState]]] = {
    InstalmentState.SCHEDULED: frozenset(
        {InstalmentState.DUE, InstalmentState.PAID, InstalmentState.RESCHEDULED}
    ),
    InstalmentState.DUE: frozenset(
        {InstalmentState.PAID, InstalmentState.OVERDUE, InstalmentState.WAIVED}
    ),
    InstalmentState.OVERDUE: frozenset({InstalmentState.PAID, InstalmentState.WAIVED}),
    InstalmentState.PAID: frozenset(),
    InstalmentState.WAIVED: frozenset(),
    InstalmentState.RESCHEDULED: frozenset(),
}


class RequestState(StrEnum):
    """A customer's request for quotation, per PRD §6.2."""

    DRAFT = "draft"
    OPEN = "open"
    SOURCING = "sourcing"
    QUOTED = "quoted"
    CLOSED = "closed"
    CANCELLED = "cancelled"


REQUEST_TRANSITIONS: Final[dict[RequestState, frozenset[RequestState]]] = {
    RequestState.DRAFT: frozenset({RequestState.OPEN, RequestState.CANCELLED}),
    RequestState.OPEN: frozenset(
        {RequestState.SOURCING, RequestState.QUOTED, RequestState.CANCELLED}
    ),
    RequestState.SOURCING: frozenset({RequestState.QUOTED, RequestState.CANCELLED}),
    RequestState.QUOTED: frozenset({RequestState.CLOSED, RequestState.CANCELLED}),
    RequestState.CLOSED: frozenset(),
    RequestState.CANCELLED: frozenset(),
}


class SupplierRequestState(StrEnum):
    """A request the business puts to a supplier."""

    DRAFT = "draft"
    SENT = "sent"
    ANSWERED = "answered"
    CLOSED = "closed"
    CANCELLED = "cancelled"


SUPPLIER_REQUEST_TRANSITIONS: Final[dict[SupplierRequestState, frozenset[SupplierRequestState]]] = {
    SupplierRequestState.DRAFT: frozenset(
        {SupplierRequestState.SENT, SupplierRequestState.CANCELLED}
    ),
    SupplierRequestState.SENT: frozenset(
        {SupplierRequestState.ANSWERED, SupplierRequestState.CLOSED, SupplierRequestState.CANCELLED}
    ),
    SupplierRequestState.ANSWERED: frozenset({SupplierRequestState.CLOSED}),
    SupplierRequestState.CLOSED: frozenset(),
    SupplierRequestState.CANCELLED: frozenset(),
}


class QuotationState(StrEnum):
    """The lifecycle PRD §6.2 requires."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ISSUED = "issued"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


QUOTATION_TRANSITIONS: Final[dict[QuotationState, frozenset[QuotationState]]] = {
    QuotationState.DRAFT: frozenset({QuotationState.PENDING_APPROVAL, QuotationState.ISSUED}),
    QuotationState.PENDING_APPROVAL: frozenset({QuotationState.DRAFT, QuotationState.ISSUED}),
    QuotationState.ISSUED: frozenset(
        {
            QuotationState.ACCEPTED,
            QuotationState.REJECTED,
            QuotationState.EXPIRED,
            QuotationState.SUPERSEDED,
        }
    ),
    QuotationState.ACCEPTED: frozenset({QuotationState.SUPERSEDED}),
    QuotationState.REJECTED: frozenset({QuotationState.SUPERSEDED}),
    QuotationState.EXPIRED: frozenset({QuotationState.SUPERSEDED}),
    QuotationState.SUPERSEDED: frozenset(),
}

SETTLED_QUOTATION_STATES: Final = frozenset(
    {
        QuotationState.ISSUED,
        QuotationState.ACCEPTED,
        QuotationState.REJECTED,
        QuotationState.EXPIRED,
        QuotationState.SUPERSEDED,
    }
)
"""Once a quotation has left the office, its figures no longer move.

An amendment is a revision, which is a new quotation superseding this one, so that
what the customer was shown remains on the record.
"""


def assert_transition[State: StrEnum](
    entity: str,
    table: Mapping[State, frozenset[State]],
    current: State,
    requested: State,
) -> None:
    """Refuse a transition the lifecycle does not allow."""
    permitted = table.get(current)
    if permitted is None or requested not in permitted:
        raise StateTransitionError(entity, str(current), str(requested))


# --------------------------------------------------------------------------------------
# Step 1 — supplier cost
# --------------------------------------------------------------------------------------


def validate_quantity(value: Decimal, *, field_name: str = "quantity") -> Decimal:
    if value <= ZERO:
        raise ValidationError("A quantity must be greater than zero.", field=field_name)
    return money(value)


def validate_rate(value: Decimal, *, field_name: str, ceiling: Decimal | None = None) -> Decimal:
    """A rate is held as a fraction, so 15% is 0.15."""
    if value < ZERO:
        raise ValidationError("A rate cannot be negative.", field=field_name)
    if ceiling is not None and value >= ceiling:
        raise ValidationError(
            f"A rate of {ceiling} or above is not permitted here: the division it "
            "implies has no finite answer.",
            field=field_name,
        )
    return money(value)


def supplier_cost(
    *,
    unit_cost: Decimal,
    quantity: Decimal,
    discount_rate: Decimal = ZERO,
    discount_amount: Decimal = ZERO,
    non_recoverable_tax: Decimal = ZERO,
) -> tuple[Decimal, Decimal, Decimal]:
    """Step 1. Returns the extended cost, the supplier's discount, and the cost base.

    Recoverable tax is not an argument: it never enters the cost base, because it is
    reclaimed from the authority rather than borne by the business. Non-recoverable tax
    is borne, so it is part of what the goods cost and part of what margin is earned on.
    """
    quantity = validate_quantity(quantity)
    if unit_cost < ZERO:
        raise ValidationError("A unit cost cannot be negative.", field="unit_cost")
    discount_rate = validate_rate(discount_rate, field_name="discount_rate", ceiling=ONE)
    if discount_amount < ZERO:
        raise ValidationError("A discount cannot be negative.", field="discount_amount")

    extended = multiply(money(unit_cost), quantity)
    discount = money(multiply(extended, discount_rate) + discount_amount)
    if discount > extended:
        raise ValidationError(
            "A supplier discount cannot exceed the line's own cost.", field="discount_amount"
        )
    base = money(extended - discount + non_recoverable_tax)
    return extended, discount, base


# --------------------------------------------------------------------------------------
# Step 2 — cost allocation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllocationBasis:
    """What one line contributes to each basis an expense may be apportioned on.

    All five bases are carried together rather than selected by the caller, because
    one document allocates freight by weight and duty by value in the same breath, and
    a line that could not answer both would force the caller to re-describe it.
    """

    reference: str
    quantity: Decimal = ONE
    value: Decimal = ZERO
    weight: Decimal = ZERO
    volume: Decimal = ZERO
    participates: bool = True
    """A line may be excluded from one expense while bearing another."""
    nominated: bool = False
    """For ``BY_LINE``: the single line the expense is charged to."""
    manual_amount: Decimal = ZERO
    """For ``MANUAL``: what the user entered against this line."""

    def weight_for(self, method: AllocationMethod) -> Decimal:
        if method is AllocationMethod.BY_QUANTITY:
            return self.quantity
        if method is AllocationMethod.BY_VALUE:
            return self.value
        if method is AllocationMethod.BY_WEIGHT:
            return self.weight
        if method is AllocationMethod.BY_VOLUME:
            return self.volume
        if method is AllocationMethod.EQUAL_SHARE:
            return ONE
        return ZERO


BASIS_FIELD: Final[dict[AllocationMethod, str]] = {
    AllocationMethod.BY_QUANTITY: "quantity",
    AllocationMethod.BY_VALUE: "value",
    AllocationMethod.BY_WEIGHT: "weight",
    AllocationMethod.BY_VOLUME: "volume",
}


def manual_residual(total: Decimal, bases: list[AllocationBasis], precision: int) -> Decimal:
    """What a manual allocation has left to place.

    Shown to the user until it reaches zero, per PRD §6.3, rather than being quietly
    absorbed somewhere.
    """
    entered = sum((item.manual_amount for item in bases if item.participates), ZERO)
    return present(money(total - entered), precision)


def allocate_expense(
    total: Decimal,
    method: AllocationMethod,
    bases: list[AllocationBasis],
    *,
    precision: int = DEFAULT_PRESENTATION_PRECISION,
) -> list[Decimal]:
    """Apportion one expense across the lines of a document.

    The result is per line in the order given, with a zero for every line that does
    not participate. The parts sum to the total exactly: rounding residue is assigned
    to the largest participating line, per the pricing engine specification, so a
    document total never disagrees with the sum of its lines by a rounding artefact.
    """
    if total < ZERO:
        raise ValidationError("An allocable expense cannot be negative.", field="amount")
    if not bases:
        return []

    participating = [index for index, item in enumerate(bases) if item.participates]
    if not participating:
        raise ValidationError(
            "No line participates in this expense, so there is nothing to allocate it to.",
            field="lines",
        )

    parts = [ZERO] * len(bases)

    if method is AllocationMethod.BY_LINE:
        nominated = [index for index in participating if bases[index].nominated]
        if len(nominated) != 1:
            raise ValidationError(
                "Allocation by line charges the whole expense to one nominated line, "
                f"and {len(nominated)} were nominated.",
                field="nominated",
            )
        parts[nominated[0]] = present(money(total), precision)
        return parts

    if method is AllocationMethod.MANUAL:
        residual = manual_residual(total, bases, precision)
        if residual != ZERO:
            raise ValidationError(
                f"A manual allocation must account for the whole expense. "
                f"{residual} remains unplaced.",
                field="manual_amount",
                residual=str(residual),
            )
        for index in participating:
            parts[index] = present(money(bases[index].manual_amount), precision)
        return parts

    weights = [bases[index].weight_for(method) for index in participating]
    if method is not AllocationMethod.EQUAL_SHARE and sum(weights, ZERO) == ZERO:
        # Dividing equally here would be a guess dressed as an answer. Freight
        # apportioned by weight across lines that record no weight is a configuration
        # fault, and the user is the only one who can say what the basis should be.
        raise ValidationError(
            f"These lines record no {BASIS_FIELD[method]}, so an expense cannot be "
            f"apportioned by {BASIS_FIELD[method]}. Record the "
            f"{BASIS_FIELD[method]} on the lines, or choose another basis.",
            field="method",
            method=method.value,
        )

    shares = allocate(money(total), weights, precision)
    for position, index in enumerate(participating):
        parts[index] = shares[position]
    return parts


# --------------------------------------------------------------------------------------
# Step 3 — business margin
# --------------------------------------------------------------------------------------


def apply_margin(cost_base: Decimal, method: MarginMethod, rate: Decimal) -> Decimal:
    """Step 3. The margin-inclusive price.

    The two methods are held distinct because they are not the same arithmetic and a
    business that says "25% margin" does not mean "25% markup": on a cost of 1,200 the
    first gives 1,600 and the second 1,500.
    """
    if cost_base < ZERO:
        raise ValidationError("A cost base cannot be negative.", field="cost_base")
    if method is MarginMethod.MARKUP_ON_COST:
        rate = validate_rate(rate, field_name="margin_rate")
        return money(multiply(money(cost_base), ONE + rate))
    rate = validate_rate(rate, field_name="margin_rate", ceiling=ONE)
    return gross_up(money(cost_base), rate)


def realised_margin_rate(cost_base: Decimal, price: Decimal) -> Decimal:
    """The gross margin a price actually achieves, as a fraction of the price.

    Reported rather than configured, so that the effect of a discount on margin is a
    figure on the screen and not an inference.
    """
    if price == ZERO:
        return ZERO
    return divide(money(price - cost_base), money(price))


# --------------------------------------------------------------------------------------
# Step 4 — affiliate commission
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommissionScopeTest:
    """What is known about a line for the purpose of deciding scope."""

    product_id: object | None = None
    category_id: object | None = None
    line_selected: bool = False
    eligible_products: frozenset[object] = field(default_factory=frozenset)
    eligible_categories: frozenset[object] = field(default_factory=frozenset)


def commission_in_scope(scope: CommissionScope, test: CommissionScopeTest) -> tuple[bool, str]:
    """Whether a referrer's arrangement reaches this line, and why.

    The reason is returned with the verdict because a referrer asking why a line paid
    nothing is entitled to an answer, and because "no commission" and "out of scope"
    are different facts.
    """
    if scope is CommissionScope.ALL_ELIGIBLE:
        return True, "Every eligible line is within this arrangement."
    if scope is CommissionScope.SELECTED_LINES:
        if test.line_selected:
            return True, "This line was selected for commission."
        return False, "This line was not selected for commission."
    if scope is CommissionScope.SELECTED_PRODUCTS:
        if test.product_id is not None and test.product_id in test.eligible_products:
            return True, "The product is within the arrangement."
        return False, "The product is outside the arrangement."
    if test.category_id is not None and test.category_id in test.eligible_categories:
        return True, "The product's category is within the arrangement."
    return False, "The product's category is outside the arrangement."


@dataclass(frozen=True, slots=True)
class CommissionOutcome:
    """The list price, the commission within it, and the reasoning."""

    list_price: Decimal
    commission: Decimal
    method: CommissionMethod | None
    rate: Decimal
    in_scope: bool
    reason: str


def apply_commission(
    margin_inclusive: Decimal,
    *,
    method: CommissionMethod = CommissionMethod.PERCENTAGE,
    rate: Decimal = ZERO,
    fixed_per_unit: Decimal = ZERO,
    quantity: Decimal = ONE,
    in_scope: bool = False,
    reason: str = "No referrer is attached to this line.",
) -> CommissionOutcome:
    """Step 4. A percentage commission is grossed up; a fixed one is added.

    The gross-up is the whole point of ADR-0007's placement. A 5% commission expressed
    as a percentage of the selling price, subtracted from a margin-inclusive price,
    would take the 5% out of the business's own margin. Dividing by ``1 - rate``
    instead means the business recovers the commission in full and its margin survives
    the referral, which is what "the business pays 5% commission" is understood to mean.
    """
    base = money(margin_inclusive)
    if not in_scope:
        return CommissionOutcome(
            list_price=base,
            commission=ZERO,
            method=None,
            rate=ZERO,
            in_scope=False,
            reason=reason,
        )

    if method is CommissionMethod.FIXED_PER_UNIT:
        if fixed_per_unit < ZERO:
            raise ValidationError("A commission cannot be negative.", field="fixed_per_unit")
        commission = multiply(money(fixed_per_unit), validate_quantity(quantity))
        return CommissionOutcome(
            list_price=money(base + commission),
            commission=commission,
            method=method,
            rate=ZERO,
            in_scope=True,
            reason=reason,
        )

    rate = validate_rate(rate, field_name="commission_rate", ceiling=ONE)
    list_price = gross_up(base, rate)
    return CommissionOutcome(
        list_price=list_price,
        commission=multiply(list_price, rate),
        method=method,
        rate=rate,
        in_scope=True,
        reason=reason,
    )


def commission_entitlement(
    accrued: Decimal, *, fulfilled_fraction: Decimal = ONE, paid_fraction: Decimal = ONE
) -> Decimal:
    """What a referrer is owed given partial fulfilment and partial payment.

    ADR-0008: accrual follows the fulfilled portion, and the entitlement becomes
    payable on the portion the customer has actually paid. The two are multiplied
    because they are independent: half delivered and half paid for is a quarter owed.
    """
    for name, value in (
        ("fulfilled_fraction", fulfilled_fraction),
        ("paid_fraction", paid_fraction),
    ):
        if value < ZERO or value > ONE:
            raise ValidationError("A fraction lies between zero and one.", field=name)
    return multiply(multiply(money(accrued), money(fulfilled_fraction)), money(paid_fraction))


def fraction(part: Decimal, whole: Decimal) -> Decimal:
    """``part / whole``, clamped to the unit interval, with zero over zero as zero."""
    if whole <= ZERO:
        return ZERO
    ratio = divide(money(part), money(whole))
    if ratio <= ZERO:
        return ZERO
    return min(ratio, ONE)


def assert_commission_transition(current: CommissionState, requested: CommissionState) -> None:
    assert_transition("Commission", COMMISSION_TRANSITIONS, current, requested)


# --------------------------------------------------------------------------------------
# Step 5 — discount
# --------------------------------------------------------------------------------------


def apply_discount(
    list_price: Decimal, method: DiscountMethod, value: Decimal
) -> tuple[Decimal, Decimal]:
    """Step 5. Returns the net price and the discount given."""
    base = money(list_price)
    if value < ZERO:
        raise ValidationError("A discount cannot be negative.", field="discount_value")
    if method is DiscountMethod.PERCENTAGE:
        rate = validate_rate(value, field_name="discount_value", ceiling=ONE)
        amount = multiply(base, rate)
    else:
        amount = money(value)
        if amount > base:
            raise ValidationError(
                "A discount cannot exceed the line's own price.", field="discount_value"
            )
    return money(base - amount), amount


@dataclass(frozen=True, slots=True)
class DiscountLimit:
    """What a role may grant without approval.

    A role with no limit recorded may grant nothing: the absence of a limit is read as
    zero rather than as permission, because a control that defaults open is not a
    control. An actor holding ``discount.approve`` is not bound by a limit.
    """

    role_code: str
    maximum_rate: Decimal = ZERO
    maximum_amount: Decimal | None = None


def discount_needs_approval(
    limits: list[DiscountLimit],
    *,
    role_codes: frozenset[str],
    rate: Decimal,
    amount: Decimal,
    may_approve: bool = False,
) -> tuple[bool, str]:
    """Whether a discount exceeds what the actor may grant on their own authority."""
    if rate <= ZERO and amount <= ZERO:
        return False, "No discount was granted."
    if may_approve:
        return False, "The actor may approve discounts."

    applicable = [limit for limit in limits if limit.role_code in role_codes]
    if not applicable:
        return True, "No discount limit is recorded for this role, so approval is required."

    best_rate = max((limit.maximum_rate for limit in applicable), default=ZERO)
    amounts = [limit.maximum_amount for limit in applicable if limit.maximum_amount is not None]
    best_amount = max(amounts) if amounts else None

    if rate > best_rate:
        return True, f"{rate} exceeds the {best_rate} this role may grant."
    if best_amount is not None and amount > best_amount:
        return True, f"{amount} exceeds the {best_amount} this role may grant."
    return False, "The discount is within this role's limit."


# --------------------------------------------------------------------------------------
# Step 6 — instalment uplift
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DurationBand:
    """An uplift that applies to repayment durations within a range, per PRD §6.5."""

    minimum_months: int
    maximum_months: int
    method: UpliftMethod = UpliftMethod.PERCENTAGE
    rate: Decimal = ZERO
    fixed_per_unit: Decimal = ZERO
    label: str = ""

    def covers(self, months: int) -> bool:
        return self.minimum_months <= months <= self.maximum_months


def band_for(bands: list[DurationBand], months: int) -> DurationBand:
    """The band governing a repayment duration.

    A duration no band covers is refused rather than priced at zero: an instalment
    plan with no finance charge because nobody configured one is a loan the business
    made by accident.
    """
    if months <= 0:
        raise ValidationError("A repayment duration is at least one month.", field="duration")
    for band in sorted(bands, key=lambda item: item.minimum_months):
        if band.covers(months):
            return band
    raise ValidationError(
        f"No duration band covers {months} month(s). Configure one before quoting this plan.",
        field="duration",
        months=months,
    )


def apply_uplift(
    net_price: Decimal,
    *,
    method: UpliftMethod = UpliftMethod.PERCENTAGE,
    rate: Decimal = ZERO,
    fixed_per_unit: Decimal = ZERO,
    quantity: Decimal = ONE,
    plan: PaymentPlan = PaymentPlan.OUTRIGHT,
) -> tuple[Decimal, Decimal]:
    """Step 6. Returns the amount payable before tax, and the finance charge within it.

    An outright sale carries no charge at all, rather than a charge of zero computed
    from a rate nobody chose.
    """
    base = money(net_price)
    if plan is PaymentPlan.OUTRIGHT:
        return base, ZERO
    if method is UpliftMethod.FIXED_PER_UNIT:
        if fixed_per_unit < ZERO:
            raise ValidationError("A finance charge cannot be negative.", field="fixed_per_unit")
        charge = multiply(money(fixed_per_unit), validate_quantity(quantity))
    else:
        rate = validate_rate(rate, field_name="uplift_rate")
        charge = multiply(base, rate)
    return money(base + charge), charge


# --------------------------------------------------------------------------------------
# The sequence
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineInputs:
    """The complete stored input set for one line.

    Reproducibility rests on this type: the engine reads nothing else, so the same
    instance prices identically for ever. It is what the snapshot at issue preserves.
    """

    reference: str
    quantity: Decimal
    unit_cost: Decimal
    source_kind: SourceKind = SourceKind.SUPPLIER

    supplier_discount_rate: Decimal = ZERO
    supplier_discount_amount: Decimal = ZERO
    non_recoverable_tax: Decimal = ZERO
    recoverable_tax: Decimal = ZERO
    allocated_cost: Decimal = ZERO
    """The sum of this line's step-2 allocations. Computed by ``price_document``."""

    margin_method: MarginMethod = MarginMethod.MARKUP_ON_COST
    margin_rate: Decimal = ZERO

    commission_method: CommissionMethod = CommissionMethod.PERCENTAGE
    commission_rate: Decimal = ZERO
    commission_fixed_per_unit: Decimal = ZERO
    commission_in_scope: bool = False
    commission_reason: str = "No referrer is attached to this line."

    discount_method: DiscountMethod = DiscountMethod.PERCENTAGE
    discount_value: Decimal = ZERO

    uplift_method: UpliftMethod = UpliftMethod.PERCENTAGE
    uplift_rate: Decimal = ZERO
    uplift_fixed_per_unit: Decimal = ZERO
    payment_plan: PaymentPlan = PaymentPlan.OUTRIGHT


@dataclass(frozen=True, slots=True)
class LineBuildUp:
    """Every step of one line, separately visible.

    PRD §6.3 requires the build-up to be transparent, which means no component is ever
    merged into another. A figure the user cannot decompose is a figure they cannot
    defend to a customer.
    """

    reference: str
    quantity: Decimal

    supplier_cost: Decimal
    supplier_discount: Decimal
    non_recoverable_tax: Decimal
    recoverable_tax: Decimal
    allocated_cost: Decimal
    landed_cost: Decimal

    margin_method: MarginMethod
    margin_rate: Decimal
    margin_amount: Decimal
    margin_inclusive: Decimal

    commission_method: CommissionMethod | None
    commission_rate: Decimal
    commission: Decimal
    commission_in_scope: bool
    commission_reason: str

    list_price: Decimal
    discount: Decimal
    net_price: Decimal
    finance_charge: Decimal
    payable_before_tax: Decimal

    unit_list_price: Decimal
    unit_net_price: Decimal
    """Held at working precision. A unit price rounded to presentation precision and
    multiplied by a quantity does not reproduce the presented line total, and the
    specification requires the line total to be derived from the stored unit price."""

    @property
    def contribution_before_commission(self) -> Decimal:
        """Net price less landed cost. The finance charge is excluded: it is a charge
        for time, not a margin on goods, and conflating them overstates trading
        performance."""
        return money(self.net_price - self.landed_cost)

    @property
    def contribution_after_commission(self) -> Decimal:
        return money(self.contribution_before_commission - self.commission)

    @property
    def realised_margin_rate(self) -> Decimal:
        return realised_margin_rate(self.landed_cost, self.net_price)

    def steps(self) -> list[tuple[str, str, Decimal]]:
        """The build-up as it is displayed: step, description, amount."""
        rows: list[tuple[str, str, Decimal]] = [
            ("1", "Supplier cost", self.supplier_cost),
        ]
        if self.supplier_discount != ZERO:
            rows.append(("1", "Supplier discount", -self.supplier_discount))
        if self.non_recoverable_tax != ZERO:
            rows.append(("1", "Non-recoverable tax", self.non_recoverable_tax))
        if self.allocated_cost != ZERO:
            rows.append(("2", "Allocated costs", self.allocated_cost))
        rows.append(("—", "Landed cost", self.landed_cost))
        rows.append(("3", MARGIN_LABELS[self.margin_method][0], self.margin_amount))
        if self.commission != ZERO:
            rows.append(("4", "Affiliate commission", self.commission))
        rows.append(("—", "List price", self.list_price))
        if self.discount != ZERO:
            rows.append(("5", "Discount", -self.discount))
            rows.append(("—", "Net price", self.net_price))
        if self.finance_charge != ZERO:
            rows.append(("6", "Finance charge", self.finance_charge))
        rows.append(("—", "Payable before tax", self.payable_before_tax))
        return rows


CONFIDENTIAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "supplier_cost",
        "supplier_discount",
        "allocated_cost",
        "landed_cost",
        "margin_method",
        "margin_rate",
        "margin_amount",
        "margin_inclusive",
        "commission",
        "commission_rate",
        "commission_method",
        "commission_in_scope",
        "commission_reason",
        "non_recoverable_tax",
        "recoverable_tax",
        "contribution_before_commission",
        "contribution_after_commission",
        "realised_margin_rate",
        "unit_cost",
    }
)
"""What a customer-facing document may never carry, per PRD §14.

Enforced in three places: the customer-facing projection below omits them, the
document renderer cannot bind to them, and a test asserts their absence from rendered
output. The list is here because it is a business rule, not a template detail.
"""

DISCLOSABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "reference",
        "quantity",
        "list_price",
        "discount",
        "net_price",
        "finance_charge",
        "payable_before_tax",
        "unit_list_price",
        "unit_net_price",
    }
)
"""What the customer sees. The finance charge appears because PRD §6.5 requires the
instalment plan to be disclosed in full; the customer is entitled to know what the
credit costs."""


DISCLOSED_AS: Final[dict[str, str]] = {"unit_net_price": "unit_price"}
"""Where a disclosed field is named differently to a customer.

The build-up calls it the unit net price because that is what the sequence produced;
an invoice calls it the unit price, because the customer has no other.
"""


def customer_facing(build_up: LineBuildUp) -> dict[str, Decimal | str]:
    """The line as a customer may see it.

    Built from ``DISCLOSABLE_FIELDS`` rather than from a list written out here, so the
    disclosure boundary is decided in exactly one place. A field added to the build-up
    later is confidential until its name is added to that set, which is the right way
    round: forgetting to disclose something is a complaint, and forgetting to withhold
    something is a competitor learning the margin.
    """
    disclosed: dict[str, Decimal | str] = {}
    for field_name in sorted(DISCLOSABLE_FIELDS):
        if field_name == "unit_list_price":
            # Internal: the list price before discount, per unit. The customer sees
            # the line's list price and the unit price they will actually pay.
            continue
        disclosed[DISCLOSED_AS.get(field_name, field_name)] = getattr(build_up, field_name)
    return disclosed


def price_line(inputs: LineInputs) -> LineBuildUp:
    """The canonical sequence, steps 1 to 6, for one line.

    Step 7 is taxation, which the tax engine performs on ``payable_before_tax``.
    """
    extended, discount_given, cost_after_discount = supplier_cost(
        unit_cost=inputs.unit_cost,
        quantity=inputs.quantity,
        discount_rate=inputs.supplier_discount_rate,
        discount_amount=inputs.supplier_discount_amount,
        non_recoverable_tax=inputs.non_recoverable_tax,
    )
    quantity = money(inputs.quantity)
    if inputs.allocated_cost < ZERO:
        raise ValidationError("An allocated cost cannot be negative.", field="allocated_cost")
    landed = money(cost_after_discount + inputs.allocated_cost)

    margin_inclusive = apply_margin(landed, inputs.margin_method, inputs.margin_rate)
    margin_amount = money(margin_inclusive - landed)

    outcome = apply_commission(
        margin_inclusive,
        method=inputs.commission_method,
        rate=inputs.commission_rate,
        fixed_per_unit=inputs.commission_fixed_per_unit,
        quantity=quantity,
        in_scope=inputs.commission_in_scope,
        reason=inputs.commission_reason,
    )

    net, discount = apply_discount(
        outcome.list_price, inputs.discount_method, inputs.discount_value
    )
    payable, finance_charge = apply_uplift(
        net,
        method=inputs.uplift_method,
        rate=inputs.uplift_rate,
        fixed_per_unit=inputs.uplift_fixed_per_unit,
        quantity=quantity,
        plan=inputs.payment_plan,
    )

    return LineBuildUp(
        reference=inputs.reference,
        quantity=quantity,
        supplier_cost=extended,
        supplier_discount=discount_given,
        non_recoverable_tax=money(inputs.non_recoverable_tax),
        recoverable_tax=money(inputs.recoverable_tax),
        allocated_cost=money(inputs.allocated_cost),
        landed_cost=landed,
        margin_method=inputs.margin_method,
        margin_rate=money(inputs.margin_rate),
        margin_amount=margin_amount,
        margin_inclusive=margin_inclusive,
        commission_method=outcome.method,
        commission_rate=outcome.rate,
        commission=outcome.commission,
        commission_in_scope=outcome.in_scope,
        commission_reason=outcome.reason,
        list_price=outcome.list_price,
        discount=discount,
        net_price=net,
        finance_charge=finance_charge,
        payable_before_tax=payable,
        unit_list_price=divide(outcome.list_price, quantity),
        unit_net_price=divide(net, quantity),
    )


@dataclass(frozen=True, slots=True)
class ExpenseInputs:
    """One allocable expense on a document."""

    reference: str
    category: str
    amount: Decimal
    method: AllocationMethod = AllocationMethod.BY_VALUE
    nominated_line: str | None = None
    excluded_lines: frozenset[str] = field(default_factory=frozenset)
    manual_amounts: dict[str, Decimal] = field(default_factory=dict)
    is_disclosed: bool = False
    """Whether the charge is shown to the customer as a separate charge, per the
    confidentiality table. Allocated costs are internal unless configured otherwise."""


@dataclass(frozen=True, slots=True)
class DocumentInputs:
    """A whole document's pricing inputs."""

    lines: list[LineInputs]
    expenses: list[ExpenseInputs] = field(default_factory=list)
    line_weights: dict[str, Decimal] = field(default_factory=dict)
    line_volumes: dict[str, Decimal] = field(default_factory=dict)
    document_discount_method: DiscountMethod = DiscountMethod.PERCENTAGE
    document_discount_value: Decimal = ZERO
    precision: int = DEFAULT_PRESENTATION_PRECISION


@dataclass(frozen=True, slots=True)
class DocumentBuildUp:
    """A priced document: its lines, its allocations, and its totals."""

    lines: list[LineBuildUp]
    allocations: dict[str, dict[str, Decimal]]
    """By line reference, then by expense reference."""
    document_discount: Decimal
    precision: int

    def _sum(self, attribute: str) -> Decimal:
        return money(sum((getattr(line, attribute) for line in self.lines), ZERO))

    @property
    def landed_cost(self) -> Decimal:
        return self._sum("landed_cost")

    @property
    def list_price(self) -> Decimal:
        return self._sum("list_price")

    @property
    def discount(self) -> Decimal:
        return self._sum("discount")

    @property
    def net_price(self) -> Decimal:
        return self._sum("net_price")

    @property
    def commission(self) -> Decimal:
        return self._sum("commission")

    @property
    def finance_charge(self) -> Decimal:
        return self._sum("finance_charge")

    @property
    def payable_before_tax(self) -> Decimal:
        return self._sum("payable_before_tax")

    @property
    def contribution_before_commission(self) -> Decimal:
        return money(sum((line.contribution_before_commission for line in self.lines), ZERO))

    @property
    def contribution_after_commission(self) -> Decimal:
        return money(sum((line.contribution_after_commission for line in self.lines), ZERO))

    def line(self, reference: str) -> LineBuildUp:
        for item in self.lines:
            if item.reference == reference:
                return item
        raise KeyError(reference)


def price_document(inputs: DocumentInputs) -> DocumentBuildUp:
    """Price a whole document: allocate its expenses, then price each line.

    Allocation precedes pricing because margin is earned on the landed cost, and a
    line cannot know its share of the document's freight until every line is known.
    A document-level discount is apportioned across lines by value, so that tax and
    reporting see it on the lines it actually reduced rather than as a floating figure.
    """
    if not inputs.lines:
        return DocumentBuildUp(
            lines=[], allocations={}, document_discount=ZERO, precision=inputs.precision
        )

    references = [line.reference for line in inputs.lines]
    if len(set(references)) != len(references):
        raise ValidationError("Each line needs its own reference.", field="reference")

    allocations: dict[str, dict[str, Decimal]] = {reference: {} for reference in references}
    totals: dict[str, Decimal] = dict.fromkeys(references, ZERO)

    for expense in inputs.expenses:
        bases = [
            AllocationBasis(
                reference=line.reference,
                quantity=money(line.quantity),
                value=_cost_base_of(line),
                weight=money(inputs.line_weights.get(line.reference, ZERO)),
                volume=money(inputs.line_volumes.get(line.reference, ZERO)),
                participates=line.reference not in expense.excluded_lines,
                nominated=line.reference == expense.nominated_line,
                manual_amount=money(expense.manual_amounts.get(line.reference, ZERO)),
            )
            for line in inputs.lines
        ]
        shares = allocate_expense(expense.amount, expense.method, bases, precision=inputs.precision)
        for reference, share in zip(references, shares, strict=True):
            allocations[reference][expense.reference] = share
            totals[reference] = money(totals[reference] + share)

    # Steps 1 to 4, with the line's own discount held back: a document discount is
    # apportioned on list price, which is not known until commission has been applied.
    priced = [
        price_line(
            _with(
                line,
                allocated_cost=totals[line.reference],
                discount_method=DiscountMethod.PERCENTAGE,
                discount_value=ZERO,
                payment_plan=PaymentPlan.OUTRIGHT,
            )
        )
        for line in inputs.lines
    ]

    document_discount = _document_discount(
        inputs, money(sum((line.list_price for line in priced), ZERO))
    )
    apportioned = (
        allocate(
            document_discount,
            [line.list_price for line in priced],
            inputs.precision,
        )
        if document_discount != ZERO
        else [ZERO] * len(priced)
    )

    final: list[LineBuildUp] = []
    for line, base, share in zip(inputs.lines, priced, apportioned, strict=True):
        own = (
            multiply(
                base.list_price,
                validate_rate(line.discount_value, field_name="discount_value", ceiling=ONE),
            )
            if line.discount_method is DiscountMethod.PERCENTAGE
            else money(line.discount_value)
        )
        combined = money(own + share)
        if combined > base.list_price:
            raise ValidationError(
                f"The discounts on line {line.reference} exceed its own price.",
                field="discount_value",
            )
        final.append(
            price_line(
                _with(
                    line,
                    allocated_cost=totals[line.reference],
                    discount_method=DiscountMethod.FIXED,
                    discount_value=combined,
                )
            )
        )

    return DocumentBuildUp(
        lines=final,
        allocations=allocations,
        document_discount=document_discount,
        precision=inputs.precision,
    )


def _document_discount(inputs: DocumentInputs, list_total: Decimal) -> Decimal:
    if inputs.document_discount_value <= ZERO:
        return ZERO
    if inputs.document_discount_method is DiscountMethod.PERCENTAGE:
        rate = validate_rate(
            inputs.document_discount_value, field_name="document_discount_value", ceiling=ONE
        )
        return multiply(list_total, rate)
    amount = money(inputs.document_discount_value)
    if amount > list_total:
        raise ValidationError(
            "A document discount cannot exceed the document's own value.",
            field="document_discount_value",
        )
    return amount


def _cost_base_of(line: LineInputs) -> Decimal:
    """The extended cost after the supplier's own discount, used as the value basis."""
    _, _, base = supplier_cost(
        unit_cost=line.unit_cost,
        quantity=line.quantity,
        discount_rate=line.supplier_discount_rate,
        discount_amount=line.supplier_discount_amount,
        non_recoverable_tax=line.non_recoverable_tax,
    )
    return base


def _with(line: LineInputs, **changes: object) -> LineInputs:
    from dataclasses import replace

    return replace(line, **changes)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Instalment schedules
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstalmentTerms:
    """What the customer agreed to, per PRD §6.5."""

    duration_months: int
    frequency: InstalmentFrequency = InstalmentFrequency.MONTHLY
    deposit_rate: Decimal = ZERO
    deposit_amount: Decimal | None = None
    first_due_on: date | None = None


@dataclass(frozen=True, slots=True)
class ScheduledInstalment:
    sequence: int
    due_on: date
    amount: Decimal


@dataclass(frozen=True, slots=True)
class InstalmentScheduleOutcome:
    """Everything PRD §6.5 requires an instalment quotation to display."""

    deposit: Decimal
    instalments: list[ScheduledInstalment]
    frequency: InstalmentFrequency
    duration_months: int
    total_payable: Decimal
    finance_charge: Decimal

    @property
    def instalment_amount(self) -> Decimal:
        """The regular figure. The final instalment may differ by the rounding residue."""
        return self.instalments[0].amount if self.instalments else ZERO

    @property
    def first_due_on(self) -> date | None:
        return self.instalments[0].due_on if self.instalments else None

    @property
    def final_due_on(self) -> date | None:
        return self.instalments[-1].due_on if self.instalments else None

    @property
    def financed(self) -> Decimal:
        return money(sum((item.amount for item in self.instalments), ZERO))


def instalment_count(frequency: InstalmentFrequency, duration_months: int) -> int:
    """How many payments a duration produces at a frequency."""
    if duration_months <= 0:
        raise ValidationError("A repayment duration is at least one month.", field="duration")
    if frequency is InstalmentFrequency.QUARTERLY:
        if duration_months % 3 != 0:
            raise ValidationError(
                "A quarterly plan runs in whole quarters, so its duration is a "
                "multiple of three months.",
                field="duration",
            )
        return duration_months // 3
    return duration_months * INSTALMENTS_PER_MONTH[frequency]


def add_months(start: date, months: int) -> date:
    """Advance by whole months, holding the day where the target month has one.

    The 31st of January plus one month is the 28th of February, not the 3rd of March:
    a monthly instalment falls once in each month, and carrying into the next one
    would put two payments in March and none in February.
    """
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return (date(year + 1, 1, 1) - date(year, 12, 1)).days
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def instalment_due_dates(
    first_due_on: date, frequency: InstalmentFrequency, count: int
) -> list[date]:
    """The due date of each payment."""
    if frequency is InstalmentFrequency.MONTHLY:
        return [add_months(first_due_on, index) for index in range(count)]
    if frequency is InstalmentFrequency.QUARTERLY:
        return [add_months(first_due_on, index * 3) for index in range(count)]
    step = 7 if frequency is InstalmentFrequency.WEEKLY else 14
    return [first_due_on + timedelta(days=index * step) for index in range(count)]


def instalment_schedule(
    total_payable: Decimal,
    terms: InstalmentTerms,
    *,
    finance_charge: Decimal = ZERO,
    precision: int = DEFAULT_PRESENTATION_PRECISION,
) -> InstalmentScheduleOutcome:
    """Build the schedule a customer signs.

    The rounding residue falls on the final instalment, not the first: the customer
    needs to know the figure that will be collected month after month, and an odd
    pesewa at the end is the one place it does not disturb that.
    """
    total = money(total_payable)
    if total < ZERO:
        raise ValidationError("A total payable cannot be negative.", field="total_payable")

    if terms.deposit_amount is not None:
        deposit = present(money(terms.deposit_amount), precision)
    else:
        rate = validate_rate(terms.deposit_rate, field_name="deposit_rate", ceiling=ONE)
        deposit = present(multiply(total, rate), precision)
    if deposit > present(total, precision):
        raise ValidationError("A deposit cannot exceed the total payable.", field="deposit_amount")

    count = instalment_count(terms.frequency, terms.duration_months)
    financed = money(present(total, precision) - deposit)
    amounts = allocate(financed, [ONE] * count, precision)

    # ``allocate`` places its residue on the first equal weight; an instalment
    # schedule wants it on the last, so it is moved explicitly.
    residue = money(amounts[0] - amounts[-1])
    if residue != ZERO and count > 1:
        amounts[0] = present(money(amounts[0] - residue), precision)
        amounts[-1] = present(money(amounts[-1] + residue), precision)

    if terms.first_due_on is None:
        # Defaulting to a month from today would make the engine read the clock, and
        # a schedule that depends on when it was computed is not reproducible from
        # its stored inputs. The caller holds a clock; this does not.
        raise ValidationError(
            "An instalment schedule needs its first due date.", field="first_due_on"
        )
    dates = instalment_due_dates(terms.first_due_on, terms.frequency, count)
    return InstalmentScheduleOutcome(
        deposit=deposit,
        instalments=[
            ScheduledInstalment(sequence=index + 1, due_on=due, amount=amount)
            for index, (due, amount) in enumerate(zip(dates, amounts, strict=True))
        ],
        frequency=terms.frequency,
        duration_months=terms.duration_months,
        total_payable=present(total, precision),
        finance_charge=present(money(finance_charge), precision),
    )


def assert_instalment_transition(current: InstalmentState, requested: InstalmentState) -> None:
    assert_transition("Instalment", INSTALMENT_TRANSITIONS, current, requested)


# --------------------------------------------------------------------------------------
# Cost preservation and variance
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostVariance:
    """What was quoted against what it turned out to cost, per PRD §6.3 and §14."""

    reference: str
    estimated: Decimal
    actual: Decimal

    @property
    def variance(self) -> Decimal:
        """Positive where the actual cost exceeded the estimate, which is the direction
        that erodes margin and therefore the one worth leading with."""
        return money(self.actual - self.estimated)

    @property
    def variance_rate(self) -> Decimal:
        if self.estimated == ZERO:
            return ZERO
        return divide(self.variance, self.estimated)

    @property
    def is_adverse(self) -> bool:
        return self.variance > ZERO

    @property
    def is_known(self) -> bool:
        return self.actual != ZERO


def assert_amendable(state: QuotationState) -> None:
    """Refuse an amendment to a quotation that has been issued.

    PRD §6.3 requires the costing snapshot taken at issue to survive a later change to
    a supplier price. That guarantee is worth nothing if the quotation itself can be
    edited, so the two rules are enforced together: an issued quotation is revised, and
    a revision is a new document that supersedes it.
    """
    if state in SETTLED_QUOTATION_STATES:
        raise StateTransitionError("Quotation", str(state), "amended")
