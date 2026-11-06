"""Taxation rules: configuration, applicability, calculation and evaluation outcome.

Satisfies PRD §19, §21, §24. Every tax and statutory charge the business is subject
to is configured rather than coded, per PRD §19.1 — the rates, the thresholds and the
authorities change, and a change of rate must never be a change of software.

Three rules govern everything here:

* a rate change is prospective, so a posted transaction keeps the tax it was charged;
* a charge is calculated once, from a rule version, and the result is a snapshot;
* where the engine cannot decide, it says so rather than guessing at zero.

That last one matters most. A tax engine that returns nothing when it does not
understand a transaction is indistinguishable from one that correctly found no tax
due, and the difference is a liability nobody notices until the authority does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.money import ZERO, money


class TaxCategory(StrEnum):
    """What kind of charge a rule represents, per PRD §19.1."""

    VALUE_ADDED = "value_added"
    LEVY = "levy"
    """A charge computed alongside VAT, often on the same base, at its own rate."""
    WITHHOLDING = "withholding"
    IMPORT_DUTY = "import_duty"
    EXCISE = "excise"
    INCOME_TAX = "income_tax"
    PAYROLL = "payroll"
    STATUTORY_FEE = "statutory_fee"
    OTHER = "other"

    @property
    def is_withholding(self) -> bool:
        return self is TaxCategory.WITHHOLDING


class TaxTreatment(StrEnum):
    """How a charge behaves on a transaction, per PRD §19.1 and §19.6."""

    EXCLUSIVE = "exclusive"
    """Added to the stated price."""
    INCLUSIVE = "inclusive"
    """Already within the stated price, and extracted from it."""
    EXEMPT = "exempt"
    """Relieved. No charge, and the reason is recorded."""
    ZERO_RATED = "zero_rated"
    """Chargeable at nil. Reported, unlike exempt."""
    WITHHELD = "withheld"
    """Deducted at source by the payer and remitted by them."""
    RECOVERABLE = "recoverable"
    """Input tax the business reclaims; an asset, not a cost."""
    NON_RECOVERABLE = "non_recoverable"
    """Input tax the business cannot reclaim; a cost of the purchase."""

    @property
    def is_charged(self) -> bool:
        """Whether the treatment produces an amount above zero.

        Zero-rated is charged at nil but still reported, which is why it is not the
        same as exempt and why both exist.
        """
        return self not in (TaxTreatment.EXEMPT,)


class CalculationMethod(StrEnum):
    """How an amount is derived, per PRD §19.1."""

    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"
    PER_UNIT = "per_unit"
    TIERED = "tiered"
    """Bands, each taxed at its own rate on the part of the base falling within it."""
    THRESHOLD = "threshold"
    """Nil below a floor, then a rate on the whole base or on the excess."""


class Outcome(StrEnum):
    """What the engine concluded for one rule on one line, per PRD §19.2.

    Every one of these appears on the transaction. ``NOT_APPLICABLE`` and
    ``NEEDS_REVIEW`` both produce no charge, and conflating them would hide the
    difference between "this rule does not apply here" and "this rule might apply
    here and nobody can tell".
    """

    APPLIED = "applied"
    NOT_APPLICABLE = "not_applicable"
    EXEMPT = "exempt"
    NEEDS_REVIEW = "needs_review"
    OVERRIDDEN = "overridden"


class FilingFrequency(StrEnum):
    """How often a return is due, per PRD §19.3."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    BIANNUAL = "biannual"
    ANNUAL = "annual"
    ON_TRANSACTION = "on_transaction"
    """Remitted with the transaction rather than on a return."""

    @property
    def months(self) -> int:
        return {
            FilingFrequency.MONTHLY: 1,
            FilingFrequency.QUARTERLY: 3,
            FilingFrequency.BIANNUAL: 6,
            FilingFrequency.ANNUAL: 12,
            FilingFrequency.ON_TRANSACTION: 0,
        }[self]


class DueDateRule(StrEnum):
    """When payment falls due after a period ends, per PRD §19.3."""

    DAYS_AFTER_PERIOD = "days_after_period"
    DAY_OF_NEXT_MONTH = "day_of_next_month"
    END_OF_NEXT_MONTH = "end_of_next_month"
    ON_TRANSACTION = "on_transaction"


class ObligationState(StrEnum):
    """Where an obligation stands, per PRD §19.3."""

    OUTSTANDING = "outstanding"
    PART_PAID = "part_paid"
    PAID = "paid"
    WRITTEN_OFF = "written_off"


class FilingState(StrEnum):
    """Where a return stands."""

    DUE = "due"
    PREPARED = "prepared"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ConditionField(StrEnum):
    """What an applicability condition may test, per PRD §19.1.

    A closed list, not free text. An administrator composes conditions from the
    fields the engine can actually read; anything else would be a promise the
    evaluator could not keep.
    """

    PRODUCT_TAX_CODE = "product_tax_code"
    PRODUCT_CATEGORY = "product_category"
    ITEM_CLASS = "item_class"
    PARTY_TAX_TREATMENT = "party_tax_treatment"
    PARTY_IS_WITHHOLDING_AGENT = "party_is_withholding_agent"
    TRANSACTION_KIND = "transaction_kind"
    BRANCH_CODE = "branch_code"
    JURISDICTION = "jurisdiction"
    IS_IMPORT = "is_import"
    LINE_TOTAL = "line_total"


class ConditionOperator(StrEnum):
    """How a condition compares, per PRD §6.6's constrained builder."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"


MAXIMUM_CONDITIONS = 6
"""A rule's conditions are a list, all of which must hold. Beyond this it is not a
rule an administrator can reason about, and the limit is kinder than the debugging."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-._ ]{0,59}$")


def validate_authority_identifier(value: str, *, field_name: str = "identifier") -> str:
    """Normalise a registration or account number held with an authority."""
    candidate = " ".join(value.split())
    if not _IDENTIFIER.match(candidate):
        raise ValidationError(
            "An authority identifier is 1 to 60 characters: letters, digits, spaces "
            "and the separators / - . and _",
            field=field_name,
        )
    return candidate


def validate_rate(rate: Decimal, *, field_name: str = "rate") -> Decimal:
    """A rate is a fraction, not a percentage: 0.15 is fifteen per cent.

    Rates above one are refused because the mistake they almost always represent —
    entering 15 for fifteen per cent — would otherwise charge fifteen times the base
    and look deliberate.
    """
    if rate < ZERO:
        raise ValidationError("A rate cannot be negative.", field=field_name)
    if rate > Decimal("1"):
        raise ValidationError(
            f"A rate is a fraction: {rate} would charge {rate * 100:.0f} times the "
            "amount. Enter 0.15 for fifteen per cent.",
            field=field_name,
        )
    return rate


# --------------------------------------------------------------------------------------
# Applicability
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Condition:
    """One test a transaction line must satisfy for a rule to apply."""

    field: ConditionField
    operator: ConditionOperator
    value: str | None = None
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        needs_value = self.operator in (
            ConditionOperator.EQUALS,
            ConditionOperator.NOT_EQUALS,
            ConditionOperator.GREATER_THAN,
            ConditionOperator.LESS_THAN,
        )
        needs_values = self.operator in (ConditionOperator.IN, ConditionOperator.NOT_IN)
        if needs_value and self.value is None:
            raise ValidationError(
                f"{self.operator.value} needs a value to compare against.", field="value"
            )
        if needs_values and not self.values:
            raise ValidationError(
                f"{self.operator.value} needs at least one value.", field="values"
            )

    def holds(self, facts: dict[str, object]) -> bool:
        """Whether the condition is satisfied by a line's facts.

        A field the facts do not carry is treated as absent rather than as a
        mismatch, so ``is empty`` is true for it and ``equals`` is false. The
        evaluator decides separately whether an absent fact is a reason to ask for
        review; the condition only reports what it sees.
        """
        present = facts.get(self.field.value)
        text = "" if present is None else str(present)

        if self.operator is ConditionOperator.IS_EMPTY:
            return text.strip() == ""
        if self.operator is ConditionOperator.IS_NOT_EMPTY:
            return text.strip() != ""
        if self.operator is ConditionOperator.EQUALS:
            return _same(text, self.value)
        if self.operator is ConditionOperator.NOT_EQUALS:
            return not _same(text, self.value)
        if self.operator is ConditionOperator.IN:
            return any(_same(text, candidate) for candidate in self.values)
        if self.operator is ConditionOperator.NOT_IN:
            return not any(_same(text, candidate) for candidate in self.values)

        # The remaining two are numeric, and a field that is not a number simply
        # does not satisfy them.
        try:
            left = Decimal(text)
            right = Decimal(str(self.value))
        except (ArithmeticError, ValueError, TypeError):
            return False
        if self.operator is ConditionOperator.GREATER_THAN:
            return left > right
        return left < right


def _same(left: str, right: str | None) -> bool:
    """Compare as the user would read them: trimmed, and without case."""
    return left.strip().casefold() == ("" if right is None else right).strip().casefold()


def assert_conditions_valid(conditions: list[Condition]) -> None:
    """Refuse a rule nobody could reason about."""
    if len(conditions) > MAXIMUM_CONDITIONS:
        raise ValidationError(
            f"A rule carries at most {MAXIMUM_CONDITIONS} conditions; this one has "
            f"{len(conditions)}. Split it into two rules.",
            field="conditions",
        )
    seen: set[tuple[str, str]] = set()
    for condition in conditions:
        key = (condition.field.value, condition.operator.value)
        if key in seen:
            raise ValidationError(
                f"{condition.field.value} is tested twice with {condition.operator.value}. "
                "One of the two has no effect.",
                field="conditions",
            )
        seen.add(key)


# --------------------------------------------------------------------------------------
# Calculation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Band:
    """One band of a tiered or threshold calculation."""

    upper: Decimal | None
    """The top of the band, or ``None`` for the final open-ended one."""
    rate: Decimal

    def __post_init__(self) -> None:
        validate_rate(self.rate)
        if self.upper is not None and self.upper <= ZERO:
            raise ValidationError("A band's upper bound is above zero.", field="upper")


def assert_bands_valid(bands: list[Band]) -> None:
    """Bands must ascend and end open, or the base has nowhere to fall."""
    if not bands:
        raise ValidationError("A tiered rule needs at least one band.", field="bands")
    previous = ZERO
    for index, band in enumerate(bands):
        last = index == len(bands) - 1
        if band.upper is None:
            if not last:
                raise ValidationError("Only the final band may be open-ended.", field="bands")
            continue
        if band.upper <= previous:
            raise ValidationError(
                f"Band {index + 1} ends at {band.upper}, which is not above the "
                f"previous band's {previous}.",
                field="bands",
            )
        previous = band.upper
    if bands[-1].upper is not None:
        raise ValidationError(
            "The final band is open-ended, so that every amount falls within one.",
            field="bands",
        )


@dataclass(frozen=True, slots=True)
class Charge:
    """The result of one calculation."""

    base: Decimal
    amount: Decimal
    rate: Decimal
    """The effective rate: the amount over the base, for presentation."""


def calculate(
    method: CalculationMethod,
    *,
    base: Decimal,
    rate: Decimal = ZERO,
    fixed_amount: Decimal = ZERO,
    quantity: Decimal = ZERO,
    threshold: Decimal = ZERO,
    threshold_applies_to_excess: bool = True,
    bands: list[Band] | None = None,
    treatment: TaxTreatment = TaxTreatment.EXCLUSIVE,
) -> Charge:
    """Compute one charge.

    An inclusive treatment extracts the tax from the stated price rather than adding
    to it, so the base is the price less the tax it already contains.
    """
    if base < ZERO:
        raise ValidationError("A tax base cannot be negative.", field="base")

    if treatment is TaxTreatment.EXEMPT:
        return Charge(base=money(base), amount=ZERO, rate=ZERO)
    if treatment is TaxTreatment.ZERO_RATED:
        return Charge(base=money(base), amount=ZERO, rate=ZERO)

    if method is CalculationMethod.FIXED_AMOUNT:
        amount = money(fixed_amount)
        return Charge(base=money(base), amount=amount, rate=_effective(amount, base))

    if method is CalculationMethod.PER_UNIT:
        amount = money(fixed_amount * quantity)
        return Charge(base=money(base), amount=amount, rate=_effective(amount, base))

    if method is CalculationMethod.PERCENTAGE:
        validate_rate(rate)
        if treatment is TaxTreatment.INCLUSIVE:
            net = _extract_net(base, rate)
            return Charge(base=net, amount=money(base - net), rate=rate)
        return Charge(base=money(base), amount=money(base * rate), rate=rate)

    if method is CalculationMethod.THRESHOLD:
        validate_rate(rate)
        if base <= threshold:
            return Charge(base=money(base), amount=ZERO, rate=ZERO)
        chargeable = base - threshold if threshold_applies_to_excess else base
        amount = money(chargeable * rate)
        return Charge(base=money(chargeable), amount=amount, rate=rate)

    # Tiered.
    ordered = bands or []
    assert_bands_valid(ordered)
    remaining = base
    floor = ZERO
    amount = ZERO
    for band in ordered:
        ceiling = band.upper
        width = remaining if ceiling is None else min(remaining, ceiling - floor)
        if width <= ZERO:
            break
        amount += width * band.rate
        remaining -= width
        floor = ceiling if ceiling is not None else floor
    amount = money(amount)
    return Charge(base=money(base), amount=amount, rate=_effective(amount, base))


def _effective(amount: Decimal, base: Decimal) -> Decimal:
    """The rate the charge works out at, for a reader comparing it with the headline."""
    if base == ZERO:
        return ZERO
    return money(amount / base)


def _extract_net(gross: Decimal, rate: Decimal) -> Decimal:
    """The amount before tax, where the stated price already contains it."""
    if rate <= ZERO:
        return money(gross)
    if rate >= Decimal("1"):
        raise ValidationError(
            "A tax-inclusive price cannot be extracted at a rate of 1 or above.",
            field="rate",
        )
    return money(gross / (Decimal("1") + rate))


def cascade_base(net: Decimal, preceding: list[Decimal]) -> Decimal:
    """The base for a rule that is computed on top of the charges before it.

    Ghana's NHIL, GETFund and COVID levies are computed on the net, and VAT is then
    computed on the net plus those levies. Which charges cascade onto which is a
    legal question, so the rule states its own order and whether it compounds; this
    only adds up what it is given.
    """
    return money(net + sum(preceding, ZERO))


# --------------------------------------------------------------------------------------
# Due dates
# --------------------------------------------------------------------------------------


def period_end(frequency: FilingFrequency, start: date) -> date:
    """The last day of the period beginning on ``start``."""
    months = frequency.months
    if months == 0:
        return start
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1) - timedelta(days=1)


def due_date(
    rule: DueDateRule,
    *,
    period_ends: date,
    days: int = 0,
    day_of_month: int = 1,
) -> date:
    """When a liability for a period falls due."""
    if rule is DueDateRule.ON_TRANSACTION:
        return period_ends
    if rule is DueDateRule.DAYS_AFTER_PERIOD:
        if days < 0:
            raise ValidationError("A due date cannot fall before the period ends.", field="days")
        return period_ends + timedelta(days=days)
    if rule is DueDateRule.END_OF_NEXT_MONTH:
        first_of_next = _first_of_next_month(period_ends)
        return _first_of_next_month(first_of_next) - timedelta(days=1)
    if not 1 <= day_of_month <= 28:
        raise ValidationError(
            "A nominated day is between 1 and 28, so that every month has one.",
            field="day_of_month",
        )
    return _first_of_next_month(period_ends).replace(day=day_of_month)


def _first_of_next_month(on: date) -> date:
    if on.month == 12:
        return date(on.year + 1, 1, 1)
    return date(on.year, on.month + 1, 1)


# --------------------------------------------------------------------------------------
# Effective dating
# --------------------------------------------------------------------------------------


def assert_prospective(effective_from: date, *, today: date, posted_exists: bool) -> None:
    """Refuse a rate change dated into a period that already carries postings.

    PRD §19.2: a rate change is prospective. A retrospective change would silently
    alter what a posted transaction should have been charged, and the document the
    customer holds would no longer agree with the ledger.
    """
    if effective_from < today and posted_exists:
        raise ConflictError(
            f"A rate cannot take effect on {effective_from:%d %B %Y}, which is in the "
            "past and already carries posted transactions. Date the change from today "
            "or later; what was charged stays charged.",
            effective_from=effective_from.isoformat(),
        )


def version_in_force(versions: list[tuple[date, date | None, object]], on: date) -> object | None:
    """The version effective on a date, from ``(from, to, payload)`` triples.

    Returns ``None`` where none covers the date, which is a real answer: a rule that
    had not yet come into force charged nothing.
    """
    for starts, ends, payload in sorted(versions, key=lambda item: item[0], reverse=True):
        if starts <= on and (ends is None or on <= ends):
            return payload
    return None


# --------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Assessment:
    """What one rule concluded about one line."""

    rule_code: str
    rule_name: str
    category: TaxCategory
    outcome: Outcome
    treatment: TaxTreatment
    method: CalculationMethod
    base: Decimal
    rate: Decimal
    amount: Decimal
    detail: str
    """Why, in a sentence a user can act on. Always populated, including on success."""
    version_effective_from: date | None = None
    overridden_from: Decimal | None = None
    override_reason: str | None = None

    @property
    def is_charged(self) -> bool:
        return self.outcome in (Outcome.APPLIED, Outcome.OVERRIDDEN) and self.amount != ZERO


@dataclass(slots=True)
class LineAssessment:
    """Every rule's conclusion about one line, and the totals that follow."""

    line_reference: str
    net: Decimal
    assessments: list[Assessment] = field(default_factory=list)

    @property
    def charged(self) -> Decimal:
        """Tax added to the line, excluding what is withheld or merely reported."""
        return money(
            sum(
                (
                    item.amount
                    for item in self.assessments
                    if item.is_charged and item.treatment is not TaxTreatment.WITHHELD
                ),
                ZERO,
            )
        )

    @property
    def withheld(self) -> Decimal:
        """Tax the payer deducts at source, which reduces what they remit to us."""
        return money(
            sum(
                (
                    item.amount
                    for item in self.assessments
                    if item.is_charged and item.treatment is TaxTreatment.WITHHELD
                ),
                ZERO,
            )
        )

    @property
    def needs_review(self) -> list[Assessment]:
        """Rules the engine could not settle. A posting is blocked while any remain."""
        return [item for item in self.assessments if item.outcome is Outcome.NEEDS_REVIEW]


def assert_ready_to_post(
    lines: list[LineAssessment], *, exception_reason: str | None = None
) -> None:
    """Refuse a posting while any line awaits a tax decision, per PRD §19.2.

    An authorised exception may let it through, and then the reason is on the record.
    Returning zero and carrying on is the behaviour this exists to prevent.
    """
    unresolved = [(line.line_reference, item) for line in lines for item in line.needs_review]
    if not unresolved:
        return
    if exception_reason and exception_reason.strip():
        return
    first = unresolved[0]
    raise ConflictError(
        f"{len(unresolved)} tax decision(s) are unresolved, the first on "
        f"{first[0]}: {first[1].detail} Resolve them, or record an authorised "
        "exception with a reason.",
        unresolved=[reference for reference, _ in unresolved],
    )
