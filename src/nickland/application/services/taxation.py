"""The taxation engine: configuration, evaluation, assessment and settlement.

Satisfies PRD §19, §21, §24.

Three services' worth of behaviour, kept together because they are one subject:
configuring what the business owes, deciding what a transaction owes, and settling
it. Splitting them would mean three services passing rule versions to each other.

The engine's contract with the rest of the system is narrow. A caller hands it the
facts about a line — what was sold, to whom, where, for how much — and receives an
assessment per rule, each of which is ``applied``, ``not applicable``, ``exempt``,
``needs review`` or ``overridden``. It never returns silence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.money import ZERO, money
from nickland.domain.organisation import validate_code
from nickland.domain.taxation import (
    Assessment,
    Band,
    CalculationMethod,
    Condition,
    ConditionField,
    ConditionOperator,
    DueDateRule,
    FilingFrequency,
    FilingState,
    LineAssessment,
    ObligationState,
    Outcome,
    TaxCategory,
    TaxTreatment,
    assert_conditions_valid,
    assert_prospective,
    calculate,
    cascade_base,
    due_date,
    period_end,
    validate_rate,
)
from nickland.persistence.models import Business
from nickland.persistence.tax_models import (
    TaxAssessment,
    TaxAuthority,
    TaxFiling,
    TaxObligation,
    TaxOverride,
    TaxPayment,
    TaxPaymentAllocation,
    TaxRule,
    TaxRuleCondition,
    TaxRuleVersion,
)
from nickland.platform.clock import Clock, SystemClock, business_date

# --------------------------------------------------------------------------------------
# What a caller hands the engine
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class TaxableLine:
    """One line, described in the terms the conditions can test.

    A field left as ``None`` is unknown rather than absent, and the engine says so
    when a rule depended on it. That distinction is the point: a missing tax code on
    a product is not the same as a product with no tax code.
    """

    reference: str
    net: Decimal
    quantity: Decimal = Decimal("1")
    product_tax_code: str | None = None
    product_category: str | None = None
    item_class: str | None = None
    product_id: uuid.UUID | None = None

    def facts(self, context: TaxContext) -> dict[str, object]:
        return {
            ConditionField.PRODUCT_TAX_CODE.value: self.product_tax_code,
            ConditionField.PRODUCT_CATEGORY.value: self.product_category,
            ConditionField.ITEM_CLASS.value: self.item_class,
            ConditionField.PARTY_TAX_TREATMENT.value: context.party_tax_treatment,
            ConditionField.PARTY_IS_WITHHOLDING_AGENT.value: (
                "true" if context.party_is_withholding_agent else "false"
            ),
            ConditionField.TRANSACTION_KIND.value: context.transaction_kind,
            ConditionField.BRANCH_CODE.value: context.branch_code,
            ConditionField.JURISDICTION.value: context.jurisdiction,
            ConditionField.IS_IMPORT.value: "true" if context.is_import else "false",
            ConditionField.LINE_TOTAL.value: str(self.net),
        }


@dataclass(slots=True)
class TaxContext:
    """What is true of the transaction as a whole."""

    transaction_kind: str
    on: date
    direction: str = "sales"
    """``sales`` or ``purchases``: a rule declares which it applies to."""
    party_id: uuid.UUID | None = None
    party_tax_treatment: str | None = None
    party_is_withholding_agent: bool = False
    party_tax_identifier: str | None = None
    branch_code: str | None = None
    jurisdiction: str | None = None
    is_import: bool = False
    currency_code: str = "GHS"
    exemption_evidence: str | None = None


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Everything the engine concluded about a transaction."""

    lines: list[LineAssessment]
    context: TaxContext

    @property
    def charged(self) -> Decimal:
        return money(sum((line.charged for line in self.lines), ZERO))

    @property
    def withheld(self) -> Decimal:
        return money(sum((line.withheld for line in self.lines), ZERO))

    @property
    def needs_review(self) -> list[tuple[str, Assessment]]:
        return [(line.line_reference, item) for line in self.lines for item in line.needs_review]

    def by_rule(self) -> dict[str, Decimal]:
        """Totals per rule, which is how a document's tax summary is printed."""
        totals: dict[str, Decimal] = {}
        for line in self.lines:
            for item in line.assessments:
                if item.is_charged:
                    totals[item.rule_code] = money(totals.get(item.rule_code, ZERO) + item.amount)
        return totals


# --------------------------------------------------------------------------------------
# Seeded configuration
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeededRule:
    """A rule that ships, so a Ghanaian installation can invoice on its first day."""

    code: str
    name: str
    category: TaxCategory
    treatment: TaxTreatment
    rate: str
    cascade_order: int
    compounds: bool
    liability_account: str | None
    recoverable_account: str | None = None
    expense_account: str | None = None
    applies_to_sales: bool = True
    applies_to_purchases: bool = False
    frequency: FilingFrequency = FilingFrequency.MONTHLY
    due_rule: DueDateRule = DueDateRule.DAY_OF_NEXT_MONTH
    due_day: int = 15
    description: str = ""
    conditions: tuple[Condition, ...] = ()


GHANA_REVENUE_AUTHORITY = "gra"

SEEDED_RULES: tuple[SeededRule, ...] = (
    SeededRule(
        "nhil",
        "National Health Insurance Levy",
        TaxCategory.LEVY,
        TaxTreatment.EXCLUSIVE,
        "0.025",
        10,
        False,
        "21000",
        description="Charged on the net, and forms part of the base VAT is charged on.",
    ),
    SeededRule(
        "getfund",
        "GETFund Levy",
        TaxCategory.LEVY,
        TaxTreatment.EXCLUSIVE,
        "0.025",
        20,
        False,
        "21000",
        description="Charged on the net, and forms part of the base VAT is charged on.",
    ),
    SeededRule(
        "covid",
        "COVID-19 Health Recovery Levy",
        TaxCategory.LEVY,
        TaxTreatment.EXCLUSIVE,
        "0.01",
        30,
        False,
        "21000",
        description="Charged on the net, and forms part of the base VAT is charged on.",
    ),
    SeededRule(
        "vat",
        "Value Added Tax",
        TaxCategory.VALUE_ADDED,
        TaxTreatment.EXCLUSIVE,
        "0.15",
        40,
        True,
        "21000",
        recoverable_account="13000",
        expense_account="50100",
        applies_to_purchases=True,
        description="Charged on the net plus the levies that precede it.",
    ),
    SeededRule(
        "wht_services",
        "Withholding Tax — services supplied to us",
        TaxCategory.WITHHOLDING,
        TaxTreatment.WITHHELD,
        "0.075",
        50,
        False,
        "21100",
        applies_to_sales=False,
        applies_to_purchases=True,
        due_rule=DueDateRule.DAY_OF_NEXT_MONTH,
        due_day=15,
        description=(
            "Deducted from what we pay a service provider, and remitted by us. It is "
            "a liability of ours, not a reduction of one."
        ),
        conditions=(
            Condition(
                field=ConditionField.ITEM_CLASS,
                operator=ConditionOperator.EQUALS,
                value="service",
            ),
        ),
    ),
    SeededRule(
        "wht_goods",
        "Withholding Tax — goods supplied to us",
        TaxCategory.WITHHOLDING,
        TaxTreatment.WITHHELD,
        "0.03",
        60,
        False,
        "21100",
        applies_to_sales=False,
        applies_to_purchases=True,
        due_rule=DueDateRule.DAY_OF_NEXT_MONTH,
        due_day=15,
        description="Deducted from what we pay a supplier of goods, and remitted by us.",
        conditions=(
            Condition(
                field=ConditionField.ITEM_CLASS,
                operator=ConditionOperator.NOT_EQUALS,
                value="service",
            ),
        ),
    ),
    SeededRule(
        "wht_suffered",
        "Withholding Tax suffered on our sales",
        TaxCategory.WITHHOLDING,
        TaxTreatment.WITHHELD,
        "0.075",
        70,
        False,
        None,
        recoverable_account="11300",
        applies_to_sales=True,
        applies_to_purchases=False,
        due_rule=DueDateRule.DAY_OF_NEXT_MONTH,
        due_day=15,
        description=(
            "Deducted by a customer who is a withholding agent. We receive less and "
            "carry a credit against our own tax, so it reduces what we remit rather "
            "than adding to it."
        ),
        conditions=(
            Condition(
                field=ConditionField.PARTY_IS_WITHHOLDING_AGENT,
                operator=ConditionOperator.EQUALS,
                value="true",
            ),
        ),
    ),
)
"""Ghana's standard indirect taxes at the rates in force when this was written.

They are seeded as ordinary configured rules with no special status: an
administrator amends the rate, changes the accounts, adds conditions or withdraws
any of them without a code change, which is the whole point of PRD §19.1. The
levies run before VAT and VAT compounds on them, which is the arrangement the law
requires and the reason ``cascade_order`` and ``compounds_on_preceding`` exist.

The two purchase-side withholding rules are conditioned on what was bought, because
goods and services are withheld at different rates and a purchase line that does not
say which it is cannot be settled by guessing. Such a line is reported for review,
which is the engine's whole posture: it would rather be asked than be wrong.
"""


class TaxService:
    """Configures rules, evaluates transactions, and settles what is owed."""

    __slots__ = ("_audit", "_clock", "_db")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # ------------------------------------------------------------------- seeding

    def seed(self, business_id: uuid.UUID) -> tuple[int, int]:
        """Create the revenue authority and the standard rules."""
        existing = self._db.scalar(
            select(TaxRule.id).where(TaxRule.business_id == business_id).limit(1)
        )
        if existing is not None:
            return 0, 0

        authority = TaxAuthority(
            business_id=business_id,
            code=GHANA_REVENUE_AUTHORITY,
            name="Ghana Revenue Authority",
            jurisdiction="Ghana",
            is_system=True,
            display_order=10,
        )
        self._db.add(authority)
        self._db.flush()

        today = self._today(business_id)
        start_of_year = date(today.year, 1, 1)
        for seeded in SEEDED_RULES:
            rule = TaxRule(
                business_id=business_id,
                code=seeded.code,
                name=seeded.name,
                description=seeded.description or None,
                category=seeded.category.value,
                treatment=seeded.treatment.value,
                authority_fk=authority.id,
                jurisdiction="Ghana",
                is_mandatory=True,
                applies_to_sales=seeded.applies_to_sales,
                applies_to_purchases=seeded.applies_to_purchases,
                cascade_order=seeded.cascade_order,
                compounds_on_preceding=seeded.compounds,
                liability_account_code=seeded.liability_account,
                recoverable_account_code=seeded.recoverable_account,
                expense_account_code=seeded.expense_account,
                filing_frequency=seeded.frequency.value,
                due_date_rule=seeded.due_rule.value,
                due_day_of_month=seeded.due_day,
                is_system=True,
            )
            rule.versions.append(
                TaxRuleVersion(
                    business_id=business_id,
                    effective_from=start_of_year,
                    method=CalculationMethod.PERCENTAGE.value,
                    rate=Decimal(seeded.rate),
                    note="Seeded at provisioning.",
                )
            )
            for order, condition in enumerate(seeded.conditions):
                rule.conditions.append(
                    TaxRuleCondition(
                        business_id=business_id,
                        display_order=order,
                        field=condition.field.value,
                        operator=condition.operator.value,
                        value=condition.value,
                        values=list(condition.values) or None,
                    )
                )
            self._db.add(rule)
        self._db.flush()
        return 1, len(SEEDED_RULES)

    # ------------------------------------------------------------------- reading

    def authorities(self, actor: AnyActor) -> list[TaxAuthority]:
        actor.require("tax.view")
        return list(
            self._db.scalars(
                select(TaxAuthority)
                .where(TaxAuthority.business_id == _business_of(actor))
                .order_by(TaxAuthority.display_order, TaxAuthority.name)
            )
            .unique()
            .all()
        )

    def rules(self, actor: AnyActor, *, include_withdrawn: bool = False) -> list[TaxRule]:
        actor.require("tax.view")
        query = select(TaxRule).where(TaxRule.business_id == _business_of(actor))
        if not include_withdrawn:
            query = query.where(TaxRule.is_active.is_(True))
        return list(
            self._db.scalars(query.order_by(TaxRule.cascade_order, TaxRule.code)).unique().all()
        )

    def require_rule(self, actor: AnyActor, rule_id: uuid.UUID) -> TaxRule:
        actor.require("tax.view")
        rule = self._db.get(TaxRule, rule_id)
        if rule is None or rule.business_id != _business_of(actor):
            raise NotFoundError("Tax rule not found.")
        return rule

    def rule_by_code(self, business_id: uuid.UUID, code: str) -> TaxRule | None:
        return self._db.scalar(
            select(TaxRule).where(TaxRule.business_id == business_id, TaxRule.code == code)
        )

    def current_rate(self, rule: TaxRule) -> Decimal | None:
        """The rate in force today, in the business's own timezone.

        Here rather than at the interface boundary, which has no business deciding
        what day it is in Accra.
        """
        version = self.version_on(rule, self._today(rule.business_id))
        return version.rate if version else None

    def version_on(self, rule: TaxRule, on: date) -> TaxRuleVersion | None:
        """The version in force on a date. ``None`` where the rule had not begun."""
        candidates = [
            version
            for version in rule.versions
            if version.effective_from <= on
            and (version.effective_to is None or on <= version.effective_to)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda version: version.effective_from)

    # ------------------------------------------------------------------- writing

    def create_rule(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        category: str,
        treatment: str,
        rate: Decimal | None = None,
        method: str = CalculationMethod.PERCENTAGE.value,
        effective_from: date | None = None,
        authority_id: uuid.UUID | None = None,
        **options: Any,
    ) -> TaxRule:
        """Configure a new charge, with its first version."""
        actor.require("tax_rule.manage")
        business_id = _business_of(actor)
        normalised = validate_code(code, field="code")
        if self.rule_by_code(business_id, normalised) is not None:
            raise ConflictError(f"A rule already carries the code {normalised}.", code=normalised)

        chosen = CalculationMethod(method)
        if chosen is CalculationMethod.PERCENTAGE:
            validate_rate(rate if rate is not None else ZERO)

        rule = TaxRule(
            business_id=business_id,
            code=normalised,
            name=name.strip(),
            description=options.get("description"),
            category=TaxCategory(category).value,
            treatment=TaxTreatment(treatment).value,
            authority_fk=authority_id,
            jurisdiction=options.get("jurisdiction"),
            is_mandatory=bool(options.get("is_mandatory", True)),
            applies_to_sales=bool(options.get("applies_to_sales", True)),
            applies_to_purchases=bool(options.get("applies_to_purchases", False)),
            cascade_order=int(options.get("cascade_order", 100)),
            compounds_on_preceding=bool(options.get("compounds_on_preceding", False)),
            liability_account_code=options.get("liability_account_code"),
            recoverable_account_code=options.get("recoverable_account_code"),
            expense_account_code=options.get("expense_account_code"),
            filing_frequency=FilingFrequency(
                options.get("filing_frequency", FilingFrequency.MONTHLY.value)
            ).value,
            due_date_rule=DueDateRule(
                options.get("due_date_rule", DueDateRule.DAY_OF_NEXT_MONTH.value)
            ).value,
            due_days=int(options.get("due_days", 0)),
            due_day_of_month=int(options.get("due_day_of_month", 15)),
        )
        rule.versions.append(
            TaxRuleVersion(
                business_id=business_id,
                effective_from=effective_from or self._today(business_id),
                method=chosen.value,
                rate=rate if rate is not None else ZERO,
                fixed_amount=options.get("fixed_amount") or ZERO,
                threshold=options.get("threshold") or ZERO,
                threshold_applies_to_excess=bool(options.get("threshold_applies_to_excess", True)),
                bands=options.get("bands"),
                created_by_id=actor.user_id,
            )
        )
        self._db.add(rule)
        self._db.flush()

        self._audit.record(
            actor,
            "tax_rule_created",
            entity_type="tax_rule",
            entity_id=rule.id,
            entity_label=f"{rule.code} — {rule.name}",
            after={"code": rule.code, "category": rule.category, "rate": str(rate)},
        )
        return rule

    def set_rate(
        self,
        actor: AnyActor,
        rule_id: uuid.UUID,
        *,
        rate: Decimal,
        effective_from: date,
        note: str | None = None,
        method: str | None = None,
        **options: Any,
    ) -> TaxRuleVersion:
        """Add a version. The rate changes from a date; what was charged stays charged.

        PRD §19.2. A retrospective change is refused once the rule has been used,
        because the documents the customers hold cannot be recalled and recomputed.
        """
        actor.require("tax_rule.manage")
        rule = self.require_rule(actor, rule_id)
        chosen = (
            CalculationMethod(method) if method else CalculationMethod(rule.versions[-1].method)
        )
        if chosen is CalculationMethod.PERCENTAGE:
            validate_rate(rate)

        assert_prospective(
            effective_from,
            today=self._today(rule.business_id),
            posted_exists=self._has_assessments(rule.id),
        )
        if any(version.effective_from == effective_from for version in rule.versions):
            raise ConflictError(
                f"A version of {rule.code} already takes effect on "
                f"{effective_from:%d %B %Y}. Amend that one, or choose another date.",
                code=rule.code,
            )

        current = self.version_on(rule, effective_from)
        if current is not None and current.effective_to is None:
            current.effective_to = effective_from - _ONE_DAY

        version = TaxRuleVersion(
            business_id=rule.business_id,
            effective_from=effective_from,
            method=chosen.value,
            rate=rate,
            fixed_amount=options.get("fixed_amount") or ZERO,
            threshold=options.get("threshold") or ZERO,
            threshold_applies_to_excess=bool(options.get("threshold_applies_to_excess", True)),
            bands=options.get("bands"),
            note=note,
            created_by_id=actor.user_id,
        )
        rule.versions.append(version)
        self._db.flush()

        self._audit.record(
            actor,
            "tax_rate_changed",
            entity_type="tax_rule",
            entity_id=rule.id,
            entity_label=f"{rule.code} — {rule.name}",
            reason=note,
            before={"rate": str(current.rate) if current else None},
            after={"rate": str(rate), "effective_from": effective_from.isoformat()},
        )
        return version

    def set_conditions(
        self, actor: AnyActor, rule_id: uuid.UUID, conditions: list[Condition]
    ) -> TaxRule:
        """Replace a rule's applicability conditions."""
        actor.require("tax_rule.manage")
        rule = self.require_rule(actor, rule_id)
        assert_conditions_valid(conditions)

        before = [
            {"field": row.field, "operator": row.operator, "value": row.value}
            for row in rule.conditions
        ]
        rule.conditions.clear()
        for order, condition in enumerate(conditions):
            rule.conditions.append(
                TaxRuleCondition(
                    business_id=rule.business_id,
                    display_order=order,
                    field=condition.field.value,
                    operator=condition.operator.value,
                    value=condition.value,
                    values=list(condition.values) or None,
                )
            )
        self._db.flush()
        self._audit.record(
            actor,
            "tax_rule_conditions_set",
            entity_type="tax_rule",
            entity_id=rule.id,
            entity_label=f"{rule.code} — {rule.name}",
            before={"conditions": before},
            after={
                "conditions": [
                    {"field": c.field.value, "operator": c.operator.value, "value": c.value}
                    for c in conditions
                ]
            },
        )
        return rule

    def withdraw_rule(self, actor: AnyActor, rule_id: uuid.UUID, reason: str) -> TaxRule:
        """Stop a rule applying to future transactions.

        Withdrawal, never deletion. A rule named by a posted assessment is part of
        the explanation of a figure somebody has already been charged, and deleting
        it would leave that figure unexplained, per PRD §19.2.
        """
        actor.require("tax_rule.manage")
        if not reason.strip():
            raise ValidationError("Give a reason for withdrawing the rule.", field="reason")
        rule = self.require_rule(actor, rule_id)
        if not rule.is_active:
            raise ConflictError(f"{rule.code} is already withdrawn.", code=rule.code)

        now = self._clock.now()
        rule.is_active = False
        rule.withdrawn_at = now
        rule.withdrawn_by_id = actor.user_id
        rule.withdrawal_reason = reason.strip()
        current = self.version_on(rule, self._today(rule.business_id))
        if current is not None and current.effective_to is None:
            current.effective_to = self._today(rule.business_id)
        self._db.flush()

        self._audit.record(
            actor,
            "tax_rule_withdrawn",
            entity_type="tax_rule",
            entity_id=rule.id,
            entity_label=f"{rule.code} — {rule.name}",
            reason=reason.strip(),
            after={"is_active": False},
        )
        return rule

    def delete_rule(self, actor: AnyActor, rule_id: uuid.UUID) -> None:
        """Remove a rule that was never used.

        A rule named by an assessment cannot go: the assessment explains a charge,
        and an explanation that points at nothing is worse than none.
        """
        actor.require("tax_rule.manage")
        rule = self.require_rule(actor, rule_id)
        if self._has_assessments(rule.id):
            raise ConflictError(
                f"{rule.code} has been applied to posted transactions, so it cannot be "
                "deleted. Withdraw it instead: it stops applying, and the charges it "
                "explains stay explained.",
                code=rule.code,
            )
        label = f"{rule.code} — {rule.name}"
        self._db.delete(rule)
        self._db.flush()
        self._audit.record(
            actor,
            "tax_rule_deleted",
            entity_type="tax_rule",
            entity_id=rule_id,
            entity_label=label,
            before={"code": rule.code},
        )

    # ---------------------------------------------------------------- evaluation

    def evaluate(
        self, actor: AnyActor, lines: list[TaxableLine], context: TaxContext
    ) -> Evaluation:
        """Decide what each rule concludes about each line.

        Rules run in cascade order so that one computed on the charges before it has
        them. Nothing is written: evaluation is a question, and a caller may ask it
        on a draft as often as it likes.
        """
        actor.require("tax.view")
        business_id = _business_of(actor)
        applicable = [
            rule
            for rule in self.rules(actor)
            if (
                rule.applies_to_sales if context.direction == "sales" else rule.applies_to_purchases
            )
        ]

        assessed: list[LineAssessment] = []
        for line in lines:
            facts = line.facts(context)
            outcome = LineAssessment(line_reference=line.reference, net=money(line.net))
            preceding: list[Decimal] = []
            for rule in applicable:
                assessment = self._assess(rule, line, context, facts, preceding)
                outcome.assessments.append(assessment)
                if assessment.is_charged and rule.compounds_on_preceding is False:
                    # Only a non-compounding charge feeds the base of a later
                    # compounding one; adding a compounded charge to that base again
                    # would tax the same money twice.
                    preceding.append(assessment.amount)
            assessed.append(outcome)

        del business_id
        return Evaluation(lines=assessed, context=context)

    def _assess(
        self,
        rule: TaxRule,
        line: TaxableLine,
        context: TaxContext,
        facts: dict[str, object],
        preceding: list[Decimal],
    ) -> Assessment:
        """One rule against one line."""
        version = self.version_on(rule, context.on)
        if version is None:
            return _conclude(
                rule,
                Outcome.NOT_APPLICABLE,
                detail=(f"{rule.name} was not in force on {context.on:%d %B %Y}."),
            )

        # A missing fact is checked before the conditions are. A condition that
        # tests an absent field simply fails, and the rule would then read as not
        # applicable — which asserts the engine knows the rule does not apply when
        # in truth it does not know. Two rules conditioned on the same absent field
        # would even disagree, one saying "not applicable" and the other "needs
        # review", on nothing but whether the test was equals or not-equals.
        missing = _missing_facts(rule, facts)
        if missing:
            return _conclude(
                rule,
                Outcome.NEEDS_REVIEW,
                detail=(
                    f"{rule.name} depends on {', '.join(missing)}, which this line does "
                    "not carry. Supply it, or record an authorised exception."
                ),
                version=version,
            )

        unmet = [condition for condition in _conditions_of(rule) if not condition.holds(facts)]
        if unmet:
            first = unmet[0]
            return _conclude(
                rule,
                Outcome.NOT_APPLICABLE,
                detail=(
                    f"{rule.name} requires {first.field.value.replace('_', ' ')} "
                    f"{first.operator.value.replace('_', ' ')} "
                    f"{first.value or ', '.join(first.values) or ''}".strip()
                    + "."
                ),
                version=version,
            )

        treatment = TaxTreatment(rule.treatment)
        if context.party_tax_treatment == "exempt":
            if not (context.exemption_evidence or "").strip():
                return _conclude(
                    rule,
                    Outcome.NEEDS_REVIEW,
                    detail=(
                        f"The customer is marked exempt but no evidence is on file, so "
                        f"{rule.name} cannot be relieved. Record the certificate "
                        "reference, or charge the tax."
                    ),
                    version=version,
                )
            return _conclude(
                rule,
                Outcome.EXEMPT,
                detail=(f"Relieved: the customer holds exemption {context.exemption_evidence}."),
                version=version,
                treatment=TaxTreatment.EXEMPT,
            )
        if context.party_tax_treatment == "zero_rated":
            treatment = TaxTreatment.ZERO_RATED

        base = cascade_base(line.net, preceding) if rule.compounds_on_preceding else money(line.net)
        charge = calculate(
            CalculationMethod(version.method),
            base=base,
            rate=version.rate,
            fixed_amount=version.fixed_amount,
            quantity=line.quantity,
            threshold=version.threshold,
            threshold_applies_to_excess=version.threshold_applies_to_excess,
            bands=_bands_of(version),
            treatment=treatment,
        )
        shown = f"{charge.rate * 100:.4f}".rstrip("0").rstrip(".")
        # The detail is read by a person, so the base is stated to two places rather
        # than to the six the ledger keeps.
        readable = f"{charge.base:.2f}"
        return Assessment(
            rule_code=rule.code,
            rule_name=rule.name,
            category=TaxCategory(rule.category),
            outcome=Outcome.APPLIED,
            treatment=treatment,
            method=CalculationMethod(version.method),
            base=charge.base,
            rate=charge.rate,
            amount=charge.amount,
            detail=(
                f"{rule.name} at {shown}% on {readable}"
                + (" (including the levies before it)" if rule.compounds_on_preceding else "")
                + "."
            ),
            version_effective_from=version.effective_from,
        )

    # ---------------------------------------------------------------- assessment

    def record(
        self,
        actor: AnyActor,
        evaluation: Evaluation,
        *,
        source_type: str,
        source_id: uuid.UUID,
        exception_reason: str | None = None,
    ) -> list[TaxAssessment]:
        """Persist an evaluation against a posted transaction.

        Every charge is written once per source line per rule; a repeated call
        returns what is already there rather than doubling it, which is what makes a
        supplier invoice entered after the goods receipt safe, per PRD §24.
        """
        actor.require("tax.view")
        business_id = _business_of(actor)

        from nickland.domain.taxation import assert_ready_to_post

        assert_ready_to_post(evaluation.lines, exception_reason=exception_reason)

        existing = {
            (row.source_line_reference, row.rule_code): row
            for row in self._db.scalars(
                select(TaxAssessment).where(
                    TaxAssessment.business_id == business_id,
                    TaxAssessment.source_type == source_type,
                    TaxAssessment.source_id == source_id,
                )
            ).all()
        }

        now = self._clock.now()
        written: list[TaxAssessment] = []
        for line in evaluation.lines:
            for item in line.assessments:
                if item.outcome is Outcome.NOT_APPLICABLE:
                    continue
                key = (line.line_reference, item.rule_code)
                if key in existing:
                    written.append(existing[key])
                    continue
                rule = self.rule_by_code(business_id, item.rule_code)
                version = self.version_on(rule, evaluation.context.on) if rule else None
                row = TaxAssessment(
                    business_id=business_id,
                    branch_id=actor.branch_id,
                    rule_fk=rule.id if rule else None,
                    version_fk=version.id if version else None,
                    source_type=source_type,
                    source_id=source_id,
                    source_line_reference=line.line_reference,
                    party_fk=evaluation.context.party_id,
                    assessed_on=evaluation.context.on,
                    rule_code=item.rule_code,
                    rule_name=item.rule_name,
                    category=item.category.value,
                    treatment=item.treatment.value,
                    method=item.method.value,
                    outcome=item.outcome.value,
                    detail=item.detail,
                    base=item.base,
                    rate=item.rate,
                    amount=item.amount,
                    currency_code=evaluation.context.currency_code,
                    # Which side of the obligation this lands on. Withholding runs
                    # in two directions and they are opposite: tax we deduct from a
                    # supplier is ours to remit, while tax a customer deducts from us
                    # reduces what we remit. Treating both as "withheld" made every
                    # purchase look like a credit.
                    is_recoverable=_is_recoverable(rule, item, evaluation.context),
                    is_withheld=_is_suffered(item, evaluation.context),
                    recorded_at=now,
                    recorded_by_id=actor.user_id,
                )
                self._db.add(row)
                written.append(row)
        self._db.flush()

        for row in written:
            if row.obligation_fk is None and row.amount != ZERO:
                obligation = self.obligation_for(actor, row.rule_fk, row.assessed_on)
                row.obligation_fk = obligation.id
        self._db.flush()
        self._refresh_obligations(business_id, {row.obligation_fk for row in written})

        if exception_reason and evaluation.needs_review:
            self._audit.record(
                actor,
                "tax_exception_recorded",
                entity_type=source_type,
                entity_id=source_id,
                entity_label=str(source_id),
                reason=exception_reason,
                after={
                    "unresolved": [reference for reference, _ in evaluation.needs_review],
                },
            )
        return written

    def override(
        self,
        actor: AnyActor,
        assessment_id: uuid.UUID,
        *,
        amount: Decimal,
        reason: str,
        evidence_reference: str | None = None,
    ) -> tuple[TaxAssessment, TaxOverride]:
        """Replace a calculated amount with a reasoned one, per PRD §19.2.

        Returns the amended assessment and the record of the change, so a caller
        needs neither a second query nor knowledge of how the two relate.
        """
        actor.require("tax_rule.override")
        if not reason.strip():
            raise ValidationError("Give a reason for the override.", field="reason")
        if amount < ZERO:
            raise ValidationError("An overridden amount cannot be negative.", field="amount")

        assessment = self._db.get(TaxAssessment, assessment_id)
        if assessment is None or assessment.business_id != _business_of(actor):
            raise NotFoundError("Tax assessment not found.")

        calculated = assessment.amount
        now = self._clock.now()
        record = TaxOverride(
            business_id=assessment.business_id,
            assessment_fk=assessment.id,
            calculated_amount=calculated,
            overridden_amount=money(amount),
            reason=reason.strip(),
            evidence_reference=evidence_reference,
            recorded_at=now,
            recorded_by_id=actor.user_id,
        )
        self._db.add(record)
        assessment.amount = money(amount)
        assessment.outcome = Outcome.OVERRIDDEN.value
        assessment.detail = f"Overridden from {calculated} to {money(amount)}: {reason.strip()}"
        self._db.flush()
        self._refresh_obligations(assessment.business_id, {assessment.obligation_fk})

        self._audit.record(
            actor,
            "tax_overridden",
            entity_type="tax_assessment",
            entity_id=assessment.id,
            entity_label=f"{assessment.rule_code} on {assessment.source_line_reference}",
            reason=reason.strip(),
            before={"amount": str(calculated)},
            after={"amount": str(money(amount)), "evidence": evidence_reference},
        )
        return assessment, record

    # --------------------------------------------------------------- obligations

    def obligation_for(self, actor: AnyActor, rule_id: uuid.UUID, on: date) -> TaxObligation:
        """The obligation covering a date, opened if it does not yet exist."""
        rule = self._db.get(TaxRule, rule_id)
        if rule is None:
            raise NotFoundError("Tax rule not found.")
        frequency = FilingFrequency(rule.filing_frequency)
        starts = _period_start(frequency, on)
        ends = period_end(frequency, starts)

        existing = self._db.scalar(
            select(TaxObligation).where(
                TaxObligation.business_id == rule.business_id,
                TaxObligation.rule_fk == rule.id,
                TaxObligation.period_starts_on == starts,
            )
        )
        if existing is not None:
            return existing

        obligation = TaxObligation(
            business_id=rule.business_id,
            rule_fk=rule.id,
            authority_fk=rule.authority_fk,
            period_starts_on=starts,
            period_ends_on=ends,
            due_on=due_date(
                DueDateRule(rule.due_date_rule),
                period_ends=ends,
                days=rule.due_days,
                day_of_month=rule.due_day_of_month,
            ),
            label=f"{rule.name} — {starts:%b %Y}",
            state=ObligationState.OUTSTANDING.value,
        )
        self._db.add(obligation)
        self._db.flush()
        del actor
        return obligation

    def obligations(
        self, actor: AnyActor, *, state: str | None = None, rule_id: uuid.UUID | None = None
    ) -> list[TaxObligation]:
        actor.require("tax.view")
        query = select(TaxObligation).where(TaxObligation.business_id == _business_of(actor))
        if state:
            query = query.where(TaxObligation.state == state)
        if rule_id:
            query = query.where(TaxObligation.rule_fk == rule_id)
        return list(self._db.scalars(query.order_by(TaxObligation.due_on.desc())).unique().all())

    def _refresh_obligations(
        self, business_id: uuid.UUID, obligation_ids: set[uuid.UUID | None]
    ) -> None:
        """Recompute an obligation's totals from the assessments that make it up.

        Derived, never accumulated: a running total that drifts from its own source
        is the failure mode PRD §19.5's reconciliation exists to catch, and the
        cheapest way to never have it is to not keep one.
        """
        for obligation_id in {item for item in obligation_ids if item is not None}:
            obligation = self._db.get(TaxObligation, obligation_id)
            if obligation is None or obligation.business_id != business_id:
                continue
            rows = self._db.execute(
                select(
                    func.coalesce(
                        func.sum(TaxAssessment.amount).filter(
                            TaxAssessment.is_recoverable.is_(False),
                            TaxAssessment.is_withheld.is_(False),
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(TaxAssessment.amount).filter(
                            TaxAssessment.is_recoverable.is_(True),
                            # Withholding suffered is both a credit and withheld;
                            # counting it in each bucket would subtract it from the
                            # payable twice. The three buckets partition the
                            # assessments rather than overlapping.
                            TaxAssessment.is_withheld.is_(False),
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(TaxAssessment.amount).filter(TaxAssessment.is_withheld.is_(True)),
                        0,
                    ),
                ).where(TaxAssessment.obligation_fk == obligation_id)
            ).one()
            obligation.assessed = money(Decimal(rows[0]))
            obligation.recoverable = money(Decimal(rows[1]))
            obligation.withheld = money(Decimal(rows[2]))

            paid = self._db.scalar(
                select(func.coalesce(func.sum(TaxPaymentAllocation.amount), 0)).where(
                    TaxPaymentAllocation.obligation_fk == obligation_id
                )
            )
            obligation.paid = money(Decimal(paid or 0))
            obligation.state = _state_of(obligation)
        self._db.flush()

    def net_payable(self, obligation: TaxObligation) -> Decimal:
        """What remains to be remitted on an obligation."""
        return money(
            obligation.assessed
            - obligation.recoverable
            - obligation.withheld
            + obligation.adjustments
            - obligation.paid
        )

    # ---------------------------------------------------------------- settlement

    def record_payment(
        self,
        actor: AnyActor,
        *,
        reference: str,
        paid_on: date,
        amount: Decimal,
        authority_id: uuid.UUID | None = None,
        allocations: list[tuple[uuid.UUID, Decimal]] | None = None,
        **options: Any,
    ) -> TaxPayment:
        """Record a payment, and apply it to obligations, per PRD §19.5.

        An unallocated remainder is kept as such rather than forced onto an
        obligation it may not belong to.
        """
        actor.require("tax_payment.manage")
        business_id = _business_of(actor)
        if amount <= ZERO:
            raise ValidationError("A payment is above zero.", field="amount")
        if self._db.scalar(
            select(TaxPayment.id).where(
                TaxPayment.business_id == business_id, TaxPayment.reference == reference.strip()
            )
        ):
            raise ConflictError(
                f"Payment {reference.strip()} has already been recorded.",
                reference=reference.strip(),
            )

        payment = TaxPayment(
            business_id=business_id,
            authority_fk=authority_id,
            reference=reference.strip(),
            paid_on=paid_on,
            amount=money(amount),
            method=options.get("method"),
            bank_reference=options.get("bank_reference"),
            evidence_reference=options.get("evidence_reference"),
            note=options.get("note"),
            recorded_by_id=actor.user_id,
        )
        self._db.add(payment)
        self._db.flush()

        if allocations:
            self.allocate(actor, payment.id, allocations)

        self._audit.record(
            actor,
            "tax_payment_recorded",
            entity_type="tax_payment",
            entity_id=payment.id,
            entity_label=payment.reference,
            after={"amount": str(payment.amount), "paid_on": paid_on.isoformat()},
        )
        return payment

    def allocate(
        self, actor: AnyActor, payment_id: uuid.UUID, allocations: list[tuple[uuid.UUID, Decimal]]
    ) -> TaxPayment:
        """Apply a payment, or part of it, to obligations."""
        actor.require("tax_payment.manage")
        payment = self._db.get(TaxPayment, payment_id)
        if payment is None or payment.business_id != _business_of(actor):
            raise NotFoundError("Tax payment not found.")

        now = self._clock.now()
        touched: set[uuid.UUID | None] = set()
        for obligation_id, amount in allocations:
            if amount <= ZERO:
                raise ValidationError("An allocation is above zero.", field="amount")
            obligation = self._db.get(TaxObligation, obligation_id)
            if obligation is None or obligation.business_id != payment.business_id:
                raise NotFoundError("Tax obligation not found.")
            payment.allocations.append(
                TaxPaymentAllocation(
                    business_id=payment.business_id,
                    obligation_fk=obligation_id,
                    amount=money(amount),
                    allocated_at=now,
                    allocated_by_id=actor.user_id,
                )
            )
            touched.add(obligation_id)

        applied = money(sum((item.amount for item in payment.allocations), ZERO))
        if applied > payment.amount:
            raise ConflictError(
                f"Allocations total {applied}, which exceeds the payment of "
                f"{payment.amount}. A payment cannot settle more than it was.",
                reference=payment.reference,
            )
        payment.allocated = applied
        self._db.flush()
        self._refresh_obligations(payment.business_id, touched)

        self._audit.record(
            actor,
            "tax_payment_allocated",
            entity_type="tax_payment",
            entity_id=payment.id,
            entity_label=payment.reference,
            after={"allocated": str(applied), "obligations": len(allocations)},
        )
        return payment

    def payments(self, actor: AnyActor, *, limit: int = 100) -> list[TaxPayment]:
        actor.require("tax.view")
        return list(
            self._db.scalars(
                select(TaxPayment)
                .where(TaxPayment.business_id == _business_of(actor))
                .order_by(TaxPayment.paid_on.desc())
                .limit(max(min(limit, 500), 1))
            )
            .unique()
            .all()
        )

    # ------------------------------------------------------------------- filings

    def open_filing(self, actor: AnyActor, obligation_id: uuid.UUID) -> TaxFiling:
        """Start a return for an obligation."""
        actor.require("tax_filing.manage")
        obligation = self._db.get(TaxObligation, obligation_id)
        if obligation is None or obligation.business_id != _business_of(actor):
            raise NotFoundError("Tax obligation not found.")
        existing = self._db.scalar(
            select(TaxFiling).where(TaxFiling.obligation_fk == obligation_id)
        )
        if existing is not None:
            return existing

        filing = TaxFiling(
            business_id=obligation.business_id,
            obligation_fk=obligation.id,
            authority_fk=obligation.authority_fk,
            due_on=obligation.due_on,
            state=FilingState.DUE.value,
            declared_amount=self.net_payable(obligation),
        )
        self._db.add(filing)
        self._db.flush()
        return filing

    def submit_filing(
        self,
        actor: AnyActor,
        filing_id: uuid.UUID,
        *,
        reference: str,
        acknowledgement: str | None = None,
    ) -> TaxFiling:
        """Record that a return was lodged."""
        actor.require("tax_filing.manage")
        filing = self._db.get(TaxFiling, filing_id)
        if filing is None or filing.business_id != _business_of(actor):
            raise NotFoundError("Tax filing not found.")
        if filing.state in (FilingState.SUBMITTED.value, FilingState.ACCEPTED.value):
            raise ConflictError(
                f"This return was already {filing.state}. Record a correction as a "
                "further return rather than restating this one.",
                state=filing.state,
            )

        now = self._clock.now()
        filing.reference = reference.strip()
        filing.state = FilingState.SUBMITTED.value
        filing.submitted_at = now
        filing.submitted_by_id = actor.user_id
        filing.acknowledgement_reference = acknowledgement
        self._db.flush()

        self._audit.record(
            actor,
            "tax_filing_submitted",
            entity_type="tax_filing",
            entity_id=filing.id,
            entity_label=reference.strip(),
            after={"declared": str(filing.declared_amount), "acknowledgement": acknowledgement},
        )
        return filing

    def filings(self, actor: AnyActor, *, state: str | None = None) -> list[TaxFiling]:
        actor.require("tax.view")
        query = select(TaxFiling).where(TaxFiling.business_id == _business_of(actor))
        if state:
            query = query.where(TaxFiling.state == state)
        return list(self._db.scalars(query.order_by(TaxFiling.due_on.desc())).unique().all())

    # ------------------------------------------------------------------- overview

    def summary(self, actor: AnyActor, *, as_at: date | None = None) -> dict[str, object]:
        """The figures the taxation overview leads with, per PRD §19.4.

        Derived from the obligations rather than kept alongside them, so the
        dashboard and the registers cannot disagree.
        """
        actor.require("tax.view")
        business_id = _business_of(actor)
        today = as_at or self._today(business_id)

        outstanding = ZERO
        overdue = ZERO
        recoverable = ZERO
        withheld = ZERO
        open_count = 0
        overdue_count = 0
        for obligation in self.obligations(actor):
            payable = self.net_payable(obligation)
            recoverable = money(recoverable + obligation.recoverable)
            withheld = money(withheld + obligation.withheld)
            if (
                obligation.state
                in (
                    ObligationState.OUTSTANDING.value,
                    ObligationState.PART_PAID.value,
                )
                and payable > ZERO
            ):
                outstanding = money(outstanding + payable)
                open_count += 1
                if obligation.due_on < today:
                    overdue = money(overdue + payable)
                    overdue_count += 1

        paid = self._db.scalar(
            select(func.coalesce(func.sum(TaxPayment.amount), 0)).where(
                TaxPayment.business_id == business_id,
                TaxPayment.paid_on >= date(today.year, 1, 1),
                TaxPayment.paid_on <= today,
            )
        )
        filings_due = self._db.scalar(
            select(func.count())
            .select_from(TaxFiling)
            .where(
                TaxFiling.business_id == business_id,
                TaxFiling.state.in_((FilingState.DUE.value, FilingState.PREPARED.value)),
            )
        )
        return {
            "as_at": today,
            "outstanding": outstanding,
            "overdue": overdue,
            "recoverable": recoverable,
            "withheld": withheld,
            "paid_this_year": money(Decimal(paid or 0)),
            "obligations_outstanding": open_count,
            "obligations_overdue": overdue_count,
            "filings_due": int(filings_due or 0),
            "rules_active": len(self.rules(actor)),
        }

    # ------------------------------------------------------------------ internals

    def assessments(
        self,
        actor: AnyActor,
        *,
        rule_id: uuid.UUID | None = None,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 200,
    ) -> list[TaxAssessment]:
        """The register behind every figure: one row per rule per source line."""
        actor.require("tax.view")
        query = select(TaxAssessment).where(TaxAssessment.business_id == _business_of(actor))
        if rule_id:
            query = query.where(TaxAssessment.rule_fk == rule_id)
        if source_type:
            query = query.where(TaxAssessment.source_type == source_type)
        if source_id:
            query = query.where(TaxAssessment.source_id == source_id)
        if start:
            query = query.where(TaxAssessment.assessed_on >= start)
        if end:
            query = query.where(TaxAssessment.assessed_on <= end)
        return list(
            self._db.scalars(
                query.order_by(TaxAssessment.assessed_on.desc()).limit(max(min(limit, 1000), 1))
            )
            .unique()
            .all()
        )

    def _has_assessments(self, rule_id: uuid.UUID) -> bool:
        return (
            self._db.scalar(
                select(TaxAssessment.id).where(TaxAssessment.rule_fk == rule_id).limit(1)
            )
            is not None
        )

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


_ONE_DAY = timedelta(days=1)


def _conditions_of(rule: TaxRule) -> list[Condition]:
    return [
        Condition(
            field=ConditionField(row.field),
            operator=ConditionOperator(row.operator),
            value=row.value,
            values=tuple(row.values or ()),
        )
        for row in rule.conditions
    ]


def _bands_of(version: TaxRuleVersion) -> list[Band] | None:
    if not version.bands:
        return None
    return [
        Band(
            upper=Decimal(str(item["upper"])) if item.get("upper") is not None else None,
            rate=Decimal(str(item["rate"])),
        )
        for item in version.bands
    ]


def _missing_facts(rule: TaxRule, facts: dict[str, object]) -> list[str]:
    """Fields a rule's conditions test that the line does not carry.

    A condition that tests an absent field has already been evaluated as false, so
    the rule would read as not applicable. That is the wrong answer when the field is
    merely missing rather than genuinely different, so it is reported for review.
    """
    missing: list[str] = []
    for row in rule.conditions:
        if row.operator in (ConditionOperator.IS_EMPTY.value, ConditionOperator.IS_NOT_EMPTY.value):
            continue
        if facts.get(row.field) is None:
            missing.append(row.field.replace("_", " "))
    return sorted(set(missing))


def _conclude(
    rule: TaxRule,
    outcome: Outcome,
    *,
    detail: str,
    version: TaxRuleVersion | None = None,
    treatment: TaxTreatment | None = None,
) -> Assessment:
    """An assessment that charges nothing, with the reason it charges nothing."""
    return Assessment(
        rule_code=rule.code,
        rule_name=rule.name,
        category=TaxCategory(rule.category),
        outcome=outcome,
        treatment=treatment or TaxTreatment(rule.treatment),
        method=CalculationMethod(version.method) if version else CalculationMethod.PERCENTAGE,
        base=ZERO,
        rate=ZERO,
        amount=ZERO,
        detail=detail,
        version_effective_from=version.effective_from if version else None,
    )


def _is_suffered(item: Assessment, context: TaxContext) -> bool:
    """Whether a customer deducted this from what they owe us.

    Only on the sales side. The same treatment on a purchase is tax we deduct from a
    supplier and must hand over ourselves.
    """
    return item.treatment is TaxTreatment.WITHHELD and context.direction == "sales"


def _is_recoverable(rule: TaxRule | None, item: Assessment, context: TaxContext) -> bool:
    """Whether the charge is an asset rather than a liability.

    Input tax on a purchase is reclaimed; withholding suffered on a sale is a credit
    against our own tax. Withholding we deduct from a supplier is neither.
    """
    if rule is None or not rule.recoverable_account_code:
        return False
    if _is_suffered(item, context):
        return True
    return context.direction == "purchases" and item.treatment is not TaxTreatment.WITHHELD


def _period_start(frequency: FilingFrequency, on: date) -> date:
    """The first day of the period a date falls in."""
    months = frequency.months
    if months <= 1:
        return date(on.year, on.month, 1)
    index = (on.month - 1) // months
    return date(on.year, index * months + 1, 1)


def _state_of(obligation: TaxObligation) -> str:
    """Where an obligation stands, from its own figures."""
    payable = money(
        obligation.assessed - obligation.recoverable - obligation.withheld + obligation.adjustments
    )
    if obligation.paid <= ZERO:
        return ObligationState.OUTSTANDING.value
    if obligation.paid >= payable:
        return ObligationState.PAID.value
    return ObligationState.PART_PAID.value


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id


__all__ = [
    "SEEDED_RULES",
    "Evaluation",
    "TaxContext",
    "TaxService",
    "TaxableLine",
]
