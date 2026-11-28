"""Pricing configuration: margin policies, discount limits and instalment plans.

These are the parameters the engine reads. They are separated from the quotation
service because configuration changes on a different timescale from quoting, and
because a quotation must be able to record which policy supplied its rate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.money import ZERO
from nickland.domain.organisation import validate_code
from nickland.domain.pricing import (
    DiscountLimit,
    DurationBand,
    InstalmentFrequency,
    MarginMethod,
    PolicyScope,
    UpliftMethod,
    validate_rate,
)
from nickland.persistence.base import as_dict
from nickland.persistence.catalogue_models import Product, ProductCategory
from nickland.persistence.pricing_models import (
    DiscountLimitRow,
    InstalmentBand,
    InstalmentPlan,
    MarginPolicy,
)
from nickland.platform.clock import Clock, SystemClock

ONE: Final = Decimal("1")

SEEDED_DISCOUNT_LIMITS: Final[tuple[tuple[str, str], ...]] = (("sales_person", "0.05"),)
"""What a sales person may discount on their own authority, per PRD §6.3.

The administrator holds ``discount.approve`` and is therefore not limited; a role with
no row may discount nothing without approval, which is the safe reading of silence.
Five per cent is a starting figure the proprietor is expected to change, not a claim
about this business.
"""

SEEDED_PLAN_BANDS: Final[tuple[tuple[str, int, int, str], ...]] = (
    ("Up to three months", 1, 3, "0.03"),
    ("Four to six months", 4, 6, "0.08"),
    ("Seven to twelve months", 7, 12, "0.15"),
)
"""Duration bands for the seeded plan, per PRD §6.5.

A longer term costs more because the business carries the money for longer. The rates
are configuration and the proprietor sets their own; what the seed establishes is that
a plan has bands at all, so an instalment quotation is possible before anyone has
configured anything.
"""


@dataclass(frozen=True, slots=True)
class ResolvedMargin:
    """A margin rate and where it came from.

    The source is carried because PRD §6.3 requires the build-up to be explicable, and
    "25%" is not an explanation while "25%, set on this product's category" is.
    """

    method: MarginMethod
    rate: Decimal
    scope: PolicyScope
    policy_id: uuid.UUID | None

    @property
    def is_configured(self) -> bool:
        return self.policy_id is not None


class PricingPolicyService:
    """Margin policies, discount limits and instalment plans."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- seeding ----------------------------------------------------------------------

    def seed(self, business_id: uuid.UUID) -> tuple[int, int]:
        """Seed a business-level margin policy, the discount limits and one plan.

        The margin policy is seeded at zero rather than at a plausible figure. A rate
        nobody chose that nonetheless prices every quotation is worse than an obvious
        zero: the first quotation then shows cost as its own price, which is wrong in a
        way the user will notice and fix, rather than wrong in a way they will not.
        """
        existing = self._db.scalar(
            select(MarginPolicy).where(
                MarginPolicy.business_id == business_id, MarginPolicy.scope == "business"
            )
        )
        if existing is not None:
            raise ConflictError("Pricing policy has already been seeded for this business.")

        self._db.add(
            MarginPolicy(
                business_id=business_id,
                scope=PolicyScope.BUSINESS.value,
                method=MarginMethod.MARKUP_ON_COST.value,
                rate=ZERO,
                is_active=True,
                notes="Set the house margin here. Every quotation uses it unless a "
                "category or a product overrides it.",
            )
        )

        for role_code, rate in SEEDED_DISCOUNT_LIMITS:
            self._db.add(
                DiscountLimitRow(
                    business_id=business_id,
                    role_code=role_code,
                    maximum_rate=Decimal(rate),
                )
            )

        plan = InstalmentPlan(
            business_id=business_id,
            code="standard",
            name="Standard instalment plan",
            description="Monthly repayment with a deposit, charged by duration band.",
            applies_to="business",
            frequency=InstalmentFrequency.MONTHLY.value,
            deposit_rate=Decimal("0.20"),
            is_active=True,
        )
        self._db.add(plan)
        self._db.flush()
        for label, minimum, maximum, rate in SEEDED_PLAN_BANDS:
            self._db.add(
                InstalmentBand(
                    business_id=business_id,
                    plan_fk=plan.id,
                    label=label,
                    minimum_months=minimum,
                    maximum_months=maximum,
                    method=UpliftMethod.PERCENTAGE.value,
                    rate=Decimal(rate),
                )
            )
        self._db.flush()
        return 1 + len(SEEDED_DISCOUNT_LIMITS), len(SEEDED_PLAN_BANDS)

    # -- margin policies --------------------------------------------------------------

    def margin_policies(self, actor: AnyActor) -> list[MarginPolicy]:
        actor.require("costing.view")
        statement = (
            select(MarginPolicy)
            .where(MarginPolicy.business_id == actor.business_id)
            .order_by(MarginPolicy.scope, MarginPolicy.id)
        )
        return list(self._db.scalars(statement))

    def set_margin_policy(
        self,
        actor: AnyActor,
        *,
        scope: PolicyScope | str,
        method: MarginMethod | str,
        rate: Decimal,
        category_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> MarginPolicy:
        """Create or amend the policy at one scope."""
        actor.require("price_list.manage")
        resolved_scope = PolicyScope(str(scope))
        resolved_method = MarginMethod(str(method))

        if resolved_scope is PolicyScope.CATEGORY:
            if category_id is None:
                raise ValidationError("A category policy names its category.", field="category_id")
            product_id = None
            self._require_category(actor, category_id)
        elif resolved_scope is PolicyScope.PRODUCT:
            if product_id is None:
                raise ValidationError("A product policy names its product.", field="product_id")
            category_id = None
            self._require_product(actor, product_id)
        else:
            category_id = None
            product_id = None

        ceiling = ONE if resolved_method is MarginMethod.TARGET_GROSS_MARGIN else None
        rate = validate_rate(rate, field_name="rate", ceiling=ceiling)

        policy = self._db.scalar(
            select(MarginPolicy).where(
                MarginPolicy.business_id == actor.business_id,
                MarginPolicy.scope == resolved_scope.value,
                MarginPolicy.category_fk == category_id,
                MarginPolicy.product_fk == product_id,
            )
        )
        before = as_dict(policy) if policy is not None else None
        if policy is None:
            policy = MarginPolicy(
                business_id=actor.business_id,
                scope=resolved_scope.value,
                category_fk=category_id,
                product_fk=product_id,
            )
            self._db.add(policy)
        policy.method = resolved_method.value
        policy.rate = rate
        policy.notes = notes or policy.notes
        policy.is_active = True
        self._db.flush()

        self._audit.record(
            actor,
            "margin_policy_set",
            entity_type="margin_policy",
            entity_id=policy.id,
            entity_label=f"{resolved_scope.value}: {resolved_method.value} {rate}",
            before=before,
            after=as_dict(policy),
        )
        return policy

    def withdraw_margin_policy(self, actor: AnyActor, policy_id: uuid.UUID) -> None:
        """Withdraw a policy, so the next-broadest scope applies again.

        The business-level policy cannot be withdrawn: something has to price a line
        that no category or product policy reaches.
        """
        actor.require("price_list.manage")
        policy = self._db.get(MarginPolicy, policy_id)
        if policy is None or policy.business_id != actor.business_id:
            raise NotFoundError("Margin policy not found.")
        if policy.scope == PolicyScope.BUSINESS.value:
            raise ConflictError(
                "The business margin policy cannot be withdrawn. Amend its rate "
                "instead: every line that no narrower policy reaches is priced by it."
            )
        before = as_dict(policy)
        self._db.delete(policy)
        self._db.flush()
        self._audit.record(
            actor,
            "margin_policy_withdrawn",
            entity_type="margin_policy",
            entity_id=policy_id,
            before=before,
            after=None,
        )

    def resolve_margin(
        self,
        business_id: uuid.UUID,
        *,
        product_id: uuid.UUID | None = None,
        category_id: uuid.UUID | None = None,
    ) -> ResolvedMargin:
        """The margin that governs one line. The most specific policy prevails.

        Where nothing is configured at all, markup at zero is returned with no policy
        identifier, so the caller can tell "configured as zero" from "not configured",
        and the build-up can say which it was.
        """
        policies = list(
            self._db.scalars(
                select(MarginPolicy).where(
                    MarginPolicy.business_id == business_id,
                    MarginPolicy.is_active.is_(True),
                )
            )
        )
        candidates: list[tuple[int, MarginPolicy]] = []
        for policy in policies:
            scope = PolicyScope(policy.scope)
            if scope is PolicyScope.PRODUCT and policy.product_fk == product_id:
                candidates.append((0, policy))
            elif scope is PolicyScope.CATEGORY and policy.category_fk == category_id:
                candidates.append((1, policy))
            elif scope is PolicyScope.BUSINESS:
                candidates.append((2, policy))
        if not candidates:
            return ResolvedMargin(
                method=MarginMethod.MARKUP_ON_COST,
                rate=ZERO,
                scope=PolicyScope.BUSINESS,
                policy_id=None,
            )
        _, chosen = min(candidates, key=lambda item: item[0])
        return ResolvedMargin(
            method=MarginMethod(chosen.method),
            rate=chosen.rate,
            scope=PolicyScope(chosen.scope),
            policy_id=chosen.id,
        )

    # -- discount limits --------------------------------------------------------------

    def discount_limits(self, actor: AnyActor) -> list[DiscountLimitRow]:
        actor.require("quotation.view")
        return list(
            self._db.scalars(
                select(DiscountLimitRow)
                .where(DiscountLimitRow.business_id == actor.business_id)
                .order_by(DiscountLimitRow.role_code)
            )
        )

    def set_discount_limit(
        self,
        actor: AnyActor,
        *,
        role_code: str,
        maximum_rate: Decimal,
        maximum_amount: Decimal | None = None,
    ) -> DiscountLimitRow:
        actor.require("role.manage")
        maximum_rate = validate_rate(maximum_rate, field_name="maximum_rate", ceiling=ONE)
        if maximum_amount is not None and maximum_amount < ZERO:
            raise ValidationError("A limit cannot be negative.", field="maximum_amount")

        row = self._db.scalar(
            select(DiscountLimitRow).where(
                DiscountLimitRow.business_id == actor.business_id,
                DiscountLimitRow.role_code == role_code,
            )
        )
        before = as_dict(row) if row is not None else None
        if row is None:
            row = DiscountLimitRow(business_id=actor.business_id, role_code=role_code)
            self._db.add(row)
        row.maximum_rate = maximum_rate
        row.maximum_amount = maximum_amount
        self._db.flush()
        self._audit.record(
            actor,
            "discount_limit_set",
            entity_type="discount_limit",
            entity_id=row.id,
            entity_label=f"{role_code}: {maximum_rate}",
            before=before,
            after=as_dict(row),
        )
        return row

    def limits_for(self, business_id: uuid.UUID) -> list[DiscountLimit]:
        """The limits as the domain understands them."""
        rows = self._db.scalars(
            select(DiscountLimitRow).where(DiscountLimitRow.business_id == business_id)
        )
        return [
            DiscountLimit(
                role_code=row.role_code,
                maximum_rate=row.maximum_rate,
                maximum_amount=row.maximum_amount,
            )
            for row in rows
        ]

    # -- instalment plans -------------------------------------------------------------

    def plans(self, actor: AnyActor, *, include_inactive: bool = False) -> list[InstalmentPlan]:
        actor.require("quotation.view")
        statement = select(InstalmentPlan).where(InstalmentPlan.business_id == actor.business_id)
        if not include_inactive:
            statement = statement.where(InstalmentPlan.is_active.is_(True))
        return list(self._db.scalars(statement.order_by(InstalmentPlan.name)))

    def create_plan(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        frequency: InstalmentFrequency | str = InstalmentFrequency.MONTHLY,
        deposit_rate: Decimal = ZERO,
        applies_to: str = "business",
        category_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        customer_id: uuid.UUID | None = None,
        description: str | None = None,
    ) -> InstalmentPlan:
        actor.require("price_list.manage")
        code = validate_code(code)
        if not name.strip():
            raise ValidationError("A plan needs a name.", field="name")
        if self._db.scalar(
            select(InstalmentPlan).where(
                InstalmentPlan.business_id == actor.business_id, InstalmentPlan.code == code
            )
        ):
            raise ConflictError(f"An instalment plan with code {code!r} already exists.")

        plan = InstalmentPlan(
            business_id=actor.business_id,
            code=code,
            name=name.strip(),
            description=description or None,
            applies_to=applies_to,
            category_fk=category_id,
            product_fk=product_id,
            customer_fk=customer_id,
            frequency=InstalmentFrequency(str(frequency)).value,
            deposit_rate=validate_rate(deposit_rate, field_name="deposit_rate", ceiling=ONE),
        )
        self._db.add(plan)
        self._db.flush()
        self._audit.record(
            actor,
            "instalment_plan_created",
            entity_type="instalment_plan",
            entity_id=plan.id,
            entity_label=plan.name,
            before=None,
            after=as_dict(plan),
        )
        return plan

    def set_bands(
        self,
        actor: AnyActor,
        plan_id: uuid.UUID,
        bands: list[dict[str, object]],
    ) -> InstalmentPlan:
        """Replace a plan's duration bands.

        Replaced rather than amended one at a time, because overlapping bands are the
        failure mode and they can only be checked against the whole set.
        """
        actor.require("price_list.manage")
        plan = self.require_plan(actor, plan_id)

        prepared: list[DurationBand] = []
        for entry in bands:
            method = UpliftMethod(str(entry.get("method", "percentage")))
            band = DurationBand(
                minimum_months=int(str(entry["minimum_months"])),
                maximum_months=int(str(entry["maximum_months"])),
                method=method,
                rate=validate_rate(
                    Decimal(str(entry.get("rate", "0"))), field_name="rate", ceiling=None
                ),
                fixed_per_unit=Decimal(str(entry.get("fixed_per_unit", "0"))),
                label=str(entry.get("label", "")),
            )
            if band.minimum_months < 1:
                raise ValidationError(
                    "A band starts at one month or later.", field="minimum_months"
                )
            if band.maximum_months < band.minimum_months:
                raise ValidationError(
                    "A band's last month cannot precede its first.", field="maximum_months"
                )
            prepared.append(band)

        prepared.sort(key=lambda item: item.minimum_months)
        for earlier, later in pairwise(prepared):
            if later.minimum_months <= earlier.maximum_months:
                raise ValidationError(
                    f"The bands overlap between months {later.minimum_months} and "
                    f"{earlier.maximum_months}. A duration must fall in exactly one "
                    "band, or the finance charge depends on which row was read first.",
                    field="minimum_months",
                )

        before = {"bands": [as_dict(row) for row in plan.bands]}
        plan.bands.clear()
        self._db.flush()
        for band in prepared:
            plan.bands.append(
                InstalmentBand(
                    business_id=actor.business_id,
                    label=band.label,
                    minimum_months=band.minimum_months,
                    maximum_months=band.maximum_months,
                    method=band.method.value,
                    rate=band.rate,
                    fixed_per_unit=band.fixed_per_unit or None,
                )
            )
        self._db.flush()
        self._audit.record(
            actor,
            "instalment_bands_set",
            entity_type="instalment_plan",
            entity_id=plan.id,
            entity_label=plan.name,
            before=before,
            after={"bands": [as_dict(row) for row in plan.bands]},
        )
        return plan

    def require_plan(self, actor: AnyActor, plan_id: uuid.UUID) -> InstalmentPlan:
        plan = self._db.get(InstalmentPlan, plan_id)
        if plan is None or plan.business_id != actor.business_id:
            raise NotFoundError("Instalment plan not found.")
        return plan

    def bands_of(self, plan: InstalmentPlan) -> list[DurationBand]:
        return [
            DurationBand(
                minimum_months=row.minimum_months,
                maximum_months=row.maximum_months,
                method=UpliftMethod(row.method),
                rate=row.rate,
                fixed_per_unit=row.fixed_per_unit or ZERO,
                label=row.label,
            )
            for row in plan.bands
        ]

    def applicable_plans(
        self,
        business_id: uuid.UUID,
        *,
        customer_id: uuid.UUID | None = None,
        on: date | None = None,
    ) -> list[InstalmentPlan]:
        """The plans a customer may be offered, per PRD §6.5."""
        del on
        plans = self._db.scalars(
            select(InstalmentPlan).where(
                InstalmentPlan.business_id == business_id, InstalmentPlan.is_active.is_(True)
            )
        )
        return [
            plan for plan in plans if plan.customer_fk is None or plan.customer_fk == customer_id
        ]

    # -- helpers ----------------------------------------------------------------------

    def _require_category(self, actor: AnyActor, category_id: uuid.UUID) -> ProductCategory:
        category = self._db.get(ProductCategory, category_id)
        if category is None or category.business_id != actor.business_id:
            raise NotFoundError("Category not found.")
        return category

    def _require_product(self, actor: AnyActor, product_id: uuid.UUID) -> Product:
        product = self._db.get(Product, product_id)
        if product is None or product.business_id != actor.business_id:
            raise NotFoundError("Product not found.")
        return product
