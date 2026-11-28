"""Quotations: sourcing a line, costing it, pricing it and issuing the offer.

The service orchestrates; the arithmetic belongs to ``nickland.domain.pricing`` and
the tax belongs to the tax engine. What is here is the part that cannot be a pure
function: reading the configuration a line is priced by, writing the figures down, and
deciding when a quotation may leave the office.

Three rules shape it.

A quotation is recosted explicitly, not on every edit. The engine is cheap, but a
recost writes the allocation of every expense to every line, and a figure that moves
while the user is still typing is a figure they cannot check.

An issued quotation is never amended. PRD §6.3 requires the costing snapshot to
survive a later change to a supplier price, which is only true if the quotation itself
stops moving. An amendment is therefore a revision: a new quotation that supersedes
the old one, with the old one still readable.

Internal and customer-facing values are separated at the point of reading, not at the
point of rendering. ``costing`` requires ``costing.view``; ``offer`` does not and
returns nothing confidential, so a template bound to the customer projection cannot
expose a margin even by mistake.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.pricing_policy import PricingPolicyService
from nickland.domain.catalogue import ItemClass
from nickland.domain.errors import (
    ApprovalRequiredError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from nickland.domain.money import ZERO, divide, money, multiply, present
from nickland.domain.organisation import DocumentKind
from nickland.domain.pricing import (
    QUOTATION_TRANSITIONS,
    AllocationMethod,
    CommissionMethod,
    CommissionScope,
    CommissionScopeTest,
    CostVariance,
    DiscountMethod,
    DocumentBuildUp,
    DocumentInputs,
    ExpenseInputs,
    InstalmentFrequency,
    InstalmentTerms,
    LineBuildUp,
    LineInputs,
    MarginMethod,
    PaymentPlan,
    QuotationState,
    SourceKind,
    UpliftMethod,
    apply_uplift,
    assert_amendable,
    assert_transition,
    band_for,
    commission_in_scope,
    customer_facing,
    discount_needs_approval,
    instalment_schedule,
    validate_quantity,
    validate_rate,
)
from nickland.persistence.base import as_dict
from nickland.persistence.catalogue_models import (
    AffiliateEligibility,
    AffiliateProfile,
    Contact,
    Party,
    Product,
)
from nickland.persistence.models import Business, Warehouse
from nickland.persistence.pricing_models import (
    Quotation,
    QuotationExpense,
    QuotationInstalment,
    QuotationLine,
    QuotationLineCost,
    QuotationLineSource,
    QuotationRequest,
    QuotationSnapshot,
    SupplierQuotationLine,
)
from nickland.platform.clock import Clock, SystemClock, business_date

ONE = Decimal("1")


# --------------------------------------------------------------------------------------
# Drafts
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class SourceDraft:
    """Where part of a line is supplied from, per PRD §6.2."""

    kind: SourceKind | str
    quantity: Decimal
    unit_cost: Decimal | None = None
    supplier_id: uuid.UUID | None = None
    supplier_quotation_line_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    currency_code: str | None = None
    exchange_rate: Decimal | None = None
    lead_time_days: int | None = None
    selection_reason: str | None = None


@dataclass(slots=True)
class LineDraft:
    """One quotation line as the form collects it."""

    description: str
    quantity: Decimal
    product_id: uuid.UUID | None = None
    request_line_id: uuid.UUID | None = None
    specification: str | None = None
    unit_id: uuid.UUID | None = None
    sources: list[SourceDraft] = field(default_factory=list)

    unit_cost: Decimal | None = None
    """Used where no source is given, so a line can be priced before it is sourced."""
    supplier_discount_rate: Decimal = ZERO
    supplier_discount_amount: Decimal = ZERO
    non_recoverable_tax: Decimal = ZERO
    recoverable_tax: Decimal = ZERO
    line_weight: Decimal = ZERO
    line_volume: Decimal = ZERO

    margin_method: MarginMethod | str | None = None
    margin_rate: Decimal | None = None
    commission_selected: bool = False
    discount_method: DiscountMethod | str = DiscountMethod.PERCENTAGE
    discount_value: Decimal = ZERO


@dataclass(slots=True)
class ExpenseDraft:
    """An allocable cost on the document."""

    category: str
    amount: Decimal
    reference: str | None = None
    description: str | None = None
    method: AllocationMethod | str = AllocationMethod.BY_VALUE
    nominated_line_position: int | None = None
    excluded_line_positions: list[int] = field(default_factory=list)
    manual_amounts: dict[int, Decimal] = field(default_factory=dict)
    supplier_id: uuid.UUID | None = None
    is_disclosed: bool = False
    currency_code: str | None = None


@dataclass(slots=True)
class QuotationDraft:
    """Everything the quotation form collects."""

    customer_id: uuid.UUID
    request_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    customer_reference: str | None = None
    quoted_on: date | None = None
    valid_until: date | None = None
    currency_code: str = "GHS"
    exchange_rate: Decimal = ONE
    precision: int = 4

    document_discount_method: DiscountMethod | str = DiscountMethod.PERCENTAGE
    document_discount_value: Decimal = ZERO

    affiliate_id: uuid.UUID | None = None
    commission_scope: CommissionScope | str | None = None

    payment_plan: PaymentPlan | str = PaymentPlan.OUTRIGHT
    instalment_plan_id: uuid.UUID | None = None
    instalment_months: int | None = None
    instalment_frequency: InstalmentFrequency | str | None = None
    deposit_rate: Decimal | None = None
    first_instalment_on: date | None = None

    terms: str | None = None
    notes: str | None = None
    internal_notes: str | None = None
    lines: list[LineDraft] = field(default_factory=list)
    expenses: list[ExpenseDraft] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostingView:
    """The internal view: the build-up, the allocations and the profitability."""

    quotation: Quotation
    build_up: DocumentBuildUp
    allocations: list[QuotationLineCost]
    tax_by_rule: dict[str, Decimal]
    tax_review: list[tuple[str, str]]
    variances: list[CostVariance]
    margin_sources: dict[str, str]

    @property
    def has_unresolved_tax(self) -> bool:
        return bool(self.tax_review)


class QuotationService:
    """The quotation lifecycle and the costing that sits behind it."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._policy = PricingPolicyService(session, clock=self._clock)

    # -- creation ---------------------------------------------------------------------

    def create(self, actor: AnyActor, draft: QuotationDraft) -> Quotation:
        """Create a draft quotation and cost it."""
        actor.require("quotation.manage")
        customer = self._require_party(actor, draft.customer_id, "customer")
        if not customer.is_customer:
            raise ValidationError(
                f"{customer.name} is not recorded as a customer.", field="customer_id"
            )
        if not draft.lines:
            raise ValidationError("A quotation needs at least one line.", field="lines")
        if draft.contact_id is not None:
            self._require_contact(actor, draft.contact_id, draft.customer_id)
        if not 0 <= draft.precision <= 6:
            raise ValidationError("Presentation precision is between 0 and 6.", field="precision")

        quoted_on = draft.quoted_on or self._today(actor)
        if draft.valid_until is not None and draft.valid_until < quoted_on:
            raise ValidationError(
                "A quotation cannot expire before it was quoted.", field="valid_until"
            )

        request = None
        if draft.request_id is not None:
            request = self._db.get(QuotationRequest, draft.request_id)
            if request is None or request.business_id != actor.business_id:
                raise NotFoundError("Request not found.")
            if request.customer_fk != draft.customer_id:
                raise ValidationError(
                    "That request belongs to another customer.", field="request_id"
                )

        plan_fields = self._instalment_fields(actor, draft)

        quotation = Quotation(
            business_id=actor.business_id,
            branch_id=actor.branch_id,
            number=self._reserve_number(actor),
            revision=1,
            request_fk=draft.request_id,
            customer_fk=draft.customer_id,
            contact_fk=draft.contact_id,
            customer_reference=draft.customer_reference
            or (request.customer_reference if request is not None else None),
            state=QuotationState.DRAFT.value,
            quoted_on=quoted_on,
            valid_until=draft.valid_until,
            currency_code=draft.currency_code.upper(),
            exchange_rate=money(draft.exchange_rate),
            precision=draft.precision,
            document_discount_method=DiscountMethod(str(draft.document_discount_method)).value,
            document_discount_value=money(draft.document_discount_value),
            affiliate_fk=draft.affiliate_id,
            commission_scope=(
                CommissionScope(str(draft.commission_scope)).value
                if draft.commission_scope is not None
                else None
            ),
            terms=draft.terms or None,
            notes=draft.notes or None,
            internal_notes=draft.internal_notes or None,
            **plan_fields,
        )
        self._db.add(quotation)
        self._db.flush()

        for position, line in enumerate(draft.lines, start=1):
            self._db.add(self._build_line(actor, quotation, position, line))
        self._db.flush()

        self._write_expenses(actor, quotation, draft.expenses)
        self.recost(actor, quotation.id)

        self._audit.record(
            actor,
            "quotation_created",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=quotation.number,
            before=None,
            after=as_dict(quotation),
        )
        return quotation

    def from_request(
        self, actor: AnyActor, request_id: uuid.UUID, *, draft: QuotationDraft | None = None
    ) -> Quotation:
        """Open a quotation on a customer's request, carrying its lines across.

        Re-keying the lines is how a quotation comes to say something the customer did
        not ask for, so the request's own words are carried and the sourcing is added
        on top.
        """
        actor.require("quotation.manage")
        request = self._db.get(QuotationRequest, request_id)
        if request is None or request.business_id != actor.business_id:
            raise NotFoundError("Request not found.")

        prepared = draft or QuotationDraft(customer_id=request.customer_fk)
        prepared.customer_id = request.customer_fk
        prepared.request_id = request_id
        prepared.contact_id = prepared.contact_id or request.contact_fk
        prepared.currency_code = prepared.currency_code or request.currency_code
        if not prepared.lines:
            prepared.lines = [
                LineDraft(
                    description=line.description,
                    quantity=line.quantity,
                    product_id=line.product_fk,
                    request_line_id=line.id,
                    specification=line.specification,
                    unit_id=line.unit_fk,
                )
                for line in request.lines
            ]
        return self.create(actor, prepared)

    def revise(self, actor: AnyActor, quotation_id: uuid.UUID, *, reason: str) -> Quotation:
        """Supersede an issued quotation with a new revision.

        The original keeps its number, its figures and its snapshot; the revision
        carries the same number with the next revision index, so the customer can see
        that the two are the same offer at two moments rather than two offers.
        """
        actor.require("quotation.manage")
        if not reason.strip():
            raise ValidationError("A revision records why it was needed.", field="reason")
        original = self.require(actor, quotation_id)
        assert_transition(
            "Quotation",
            QUOTATION_TRANSITIONS,
            QuotationState(original.state),
            QuotationState.SUPERSEDED,
        )

        revision = Quotation(
            business_id=original.business_id,
            branch_id=original.branch_id,
            number=original.number,
            revision=original.revision + 1,
            supersedes_fk=original.id,
            request_fk=original.request_fk,
            customer_fk=original.customer_fk,
            contact_fk=original.contact_fk,
            customer_reference=original.customer_reference,
            state=QuotationState.DRAFT.value,
            quoted_on=self._today(actor),
            valid_until=original.valid_until,
            currency_code=original.currency_code,
            exchange_rate=original.exchange_rate,
            precision=original.precision,
            document_discount_method=original.document_discount_method,
            document_discount_value=original.document_discount_value,
            affiliate_fk=original.affiliate_fk,
            commission_scope=original.commission_scope,
            payment_plan=original.payment_plan,
            instalment_plan_fk=original.instalment_plan_fk,
            instalment_months=original.instalment_months,
            instalment_frequency=original.instalment_frequency,
            deposit_rate=original.deposit_rate,
            first_instalment_on=original.first_instalment_on,
            terms=original.terms,
            notes=original.notes,
            internal_notes=original.internal_notes,
        )
        self._db.add(revision)
        self._db.flush()

        for line in original.lines:
            copy = QuotationLine(
                business_id=actor.business_id,
                quotation_fk=revision.id,
                position=line.position,
                request_line_fk=line.request_line_fk,
                product_fk=line.product_fk,
                description=line.description,
                specification=line.specification,
                quantity=line.quantity,
                unit_fk=line.unit_fk,
                source_kind=line.source_kind,
                unit_cost=line.unit_cost,
                supplier_discount_rate=line.supplier_discount_rate,
                supplier_discount_amount=line.supplier_discount_amount,
                non_recoverable_tax=line.non_recoverable_tax,
                recoverable_tax=line.recoverable_tax,
                line_weight=line.line_weight,
                line_volume=line.line_volume,
                margin_method=line.margin_method,
                margin_rate=line.margin_rate,
                margin_source=line.margin_source,
                commission_selected=line.commission_selected,
                discount_method=line.discount_method,
                discount_value=line.discount_value,
                uplift_method=line.uplift_method,
                uplift_rate=line.uplift_rate,
                uplift_fixed_per_unit=line.uplift_fixed_per_unit,
            )
            self._db.add(copy)
            self._db.flush()
            for source in line.sources:
                self._db.add(
                    QuotationLineSource(
                        business_id=actor.business_id,
                        quotation_line_fk=copy.id,
                        position=source.position,
                        kind=source.kind,
                        supplier_fk=source.supplier_fk,
                        supplier_quotation_line_fk=source.supplier_quotation_line_fk,
                        warehouse_fk=source.warehouse_fk,
                        quantity=source.quantity,
                        unit_cost=source.unit_cost,
                        currency_code=source.currency_code,
                        exchange_rate=source.exchange_rate,
                        lead_time_days=source.lead_time_days,
                        selection_reason=source.selection_reason,
                    )
                )
        self._db.flush()

        positions = {line.position: line.id for line in revision.lines}
        for expense in original.expenses:
            nominated = None
            if expense.nominated_line_fk is not None:
                original_line = self._db.get(QuotationLine, expense.nominated_line_fk)
                if original_line is not None:
                    nominated = positions.get(original_line.position)
            self._db.add(
                QuotationExpense(
                    business_id=actor.business_id,
                    quotation_fk=revision.id,
                    reference=expense.reference,
                    category=expense.category,
                    description=expense.description,
                    amount=expense.amount,
                    currency_code=expense.currency_code,
                    method=expense.method,
                    nominated_line_fk=nominated,
                    excluded_line_ids=list(expense.excluded_line_ids),
                    manual_amounts=dict(expense.manual_amounts),
                    supplier_fk=expense.supplier_fk,
                    is_disclosed=expense.is_disclosed,
                )
            )
        self._db.flush()

        before = as_dict(original)
        original.state = QuotationState.SUPERSEDED.value
        original.decision_reason = reason.strip()
        self._db.flush()

        self.recost(actor, revision.id)
        self._audit.record(
            actor,
            "quotation_revised",
            entity_type="quotation",
            entity_id=revision.id,
            entity_label=f"{revision.number} revision {revision.revision}",
            reason=reason.strip(),
            before=before,
            after=as_dict(revision),
        )
        return revision

    # -- amendment --------------------------------------------------------------------

    def set_sources(
        self, actor: AnyActor, line_id: uuid.UUID, sources: list[SourceDraft]
    ) -> QuotationLine:
        """Choose where a line is supplied from, per PRD §6.2.

        The sources' quantities must add up to the line's, because a line half of
        which has no source is an offer the business cannot fill. The line's unit cost
        becomes the quantity-weighted mean of its sources, so a split line still
        carries one cost into the build-up while each source keeps its own.
        """
        actor.require("quotation.manage")
        line = self._require_line(actor, line_id)
        quotation = self._db.get(Quotation, line.quotation_fk)
        if quotation is None:
            raise NotFoundError("Quotation not found.")
        assert_amendable(QuotationState(quotation.state))
        if not sources:
            raise ValidationError("A line needs at least one source.", field="sources")

        prepared: list[QuotationLineSource] = []
        for position, source in enumerate(sources, start=1):
            prepared.append(self._build_source(actor, line, position, source))

        total = money(sum((item.quantity for item in prepared), ZERO))
        if total != money(line.quantity):
            raise ValidationError(
                f"The sources supply {total} of the {line.quantity} on this line. "
                "A line whose sources do not add up to its quantity is an offer the "
                "business cannot fill.",
                field="quantity",
            )

        before = as_dict(line)
        line.sources.clear()
        self._db.flush()
        for item in prepared:
            line.sources.append(item)
        self._db.flush()

        weighted = money(
            sum(
                (
                    multiply(multiply(item.unit_cost, item.exchange_rate), item.quantity)
                    for item in prepared
                ),
                ZERO,
            )
        )
        line.unit_cost = divide(weighted, money(line.quantity))
        line.source_kind = (
            SourceKind.STOCK.value
            if all(item.kind == SourceKind.STOCK.value for item in prepared)
            else SourceKind.SUPPLIER.value
        )
        self._db.flush()

        self._audit.record(
            actor,
            "quotation_line_sourced",
            entity_type="quotation_line",
            entity_id=line.id,
            entity_label=f"{quotation.number} line {line.position}",
            before=before,
            after=as_dict(line),
        )
        self.recost(actor, quotation.id)
        return line

    def set_expenses(
        self, actor: AnyActor, quotation_id: uuid.UUID, expenses: list[ExpenseDraft]
    ) -> Quotation:
        """Replace the document's allocable expenses and recost."""
        actor.require("quotation.manage")
        quotation = self.require(actor, quotation_id)
        assert_amendable(QuotationState(quotation.state))
        before = {"expenses": [as_dict(row) for row in quotation.expenses]}
        quotation.expenses.clear()
        self._db.flush()
        self._write_expenses(actor, quotation, expenses)
        self.recost(actor, quotation_id)
        self._audit.record(
            actor,
            "quotation_expenses_set",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=quotation.number,
            before=before,
            after={"expenses": [as_dict(row) for row in quotation.expenses]},
        )
        return quotation

    def set_payment_plan(
        self,
        actor: AnyActor,
        quotation_id: uuid.UUID,
        *,
        plan: PaymentPlan | str,
        instalment_plan_id: uuid.UUID | None = None,
        months: int | None = None,
        frequency: InstalmentFrequency | str | None = None,
        deposit_rate: Decimal | None = None,
        first_instalment_on: date | None = None,
    ) -> Quotation:
        """Select outright or instalment pricing, per PRD §6.5."""
        actor.require("quotation.manage")
        quotation = self.require(actor, quotation_id)
        assert_amendable(QuotationState(quotation.state))
        draft = QuotationDraft(
            customer_id=quotation.customer_fk,
            payment_plan=plan,
            instalment_plan_id=instalment_plan_id,
            instalment_months=months,
            instalment_frequency=frequency,
            deposit_rate=deposit_rate,
            first_instalment_on=first_instalment_on,
        )
        fields = self._instalment_fields(actor, draft)
        before = as_dict(quotation)
        for name, value in fields.items():
            setattr(quotation, name, value)
        self._db.flush()
        self.recost(actor, quotation_id)
        self._audit.record(
            actor,
            "quotation_payment_plan_set",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=f"{quotation.number}: {quotation.payment_plan}",
            before=before,
            after=as_dict(quotation),
        )
        return quotation

    def record_actual_cost(
        self,
        actor: AnyActor,
        line_id: uuid.UUID,
        *,
        actual_unit_cost: Decimal,
        reference: str | None = None,
        on: date | None = None,
    ) -> QuotationLine:
        """Record what a line actually cost after procurement, per PRD §6.3.

        This is the one field an issued quotation accepts, and it does not touch a
        priced figure: it is recorded beside the estimate so the variance can be
        reported. The quoted profitability continues to be read from the snapshot.
        """
        actor.require("costing.view")
        actor.require("purchase_order.manage")
        line = self._require_line(actor, line_id)
        if actual_unit_cost < ZERO:
            raise ValidationError("An actual cost cannot be negative.", field="actual_unit_cost")
        before = as_dict(line)
        line.actual_unit_cost = money(actual_unit_cost)
        line.actual_cost_recorded_on = on or self._today(actor)
        line.actual_cost_reference = reference or None
        self._db.flush()
        self._audit.record(
            actor,
            "quotation_actual_cost_recorded",
            entity_type="quotation_line",
            entity_id=line.id,
            entity_label=f"line {line.position}: {actual_unit_cost}",
            before=before,
            after=as_dict(line),
        )
        return line

    # -- costing ----------------------------------------------------------------------

    def recost(self, actor: AnyActor, quotation_id: uuid.UUID) -> DocumentBuildUp:
        """Run the canonical sequence over the quotation and store the result.

        Steps 1 to 6 come from the engine; step 7 is the tax engine's. The allocations
        are written per line per expense, so the build-up screen can show which
        expense put which amount on which line.
        """
        quotation = self.require(actor, quotation_id)
        assert_amendable(QuotationState(quotation.state))

        inputs, expenses_by_reference, margins = self._inputs_for(actor, quotation)
        built = self._engine(inputs)

        self._db.execute(
            delete(QuotationLineCost).where(QuotationLineCost.quotation_fk == quotation.id)
        )
        by_position = {line.position: line for line in quotation.lines}
        for reference, shares in built.allocations.items():
            line = by_position[int(reference)]
            for expense_reference, amount in shares.items():
                if amount == ZERO:
                    continue
                expense = expenses_by_reference[expense_reference]
                self._db.add(
                    QuotationLineCost(
                        business_id=actor.business_id,
                        quotation_fk=quotation.id,
                        quotation_line_fk=line.id,
                        expense_fk=expense.id,
                        category=expense.category,
                        method=expense.method,
                        amount=amount,
                    )
                )

        precision = quotation.precision
        for built_line in built.lines:
            line = by_position[int(built_line.reference)]
            method, rate, source = margins[line.position]
            line.margin_method = method.value
            line.margin_rate = rate
            line.margin_source = source
            line.allocated_cost = present(built_line.allocated_cost, precision)
            line.landed_cost = present(built_line.landed_cost, precision)
            line.margin_amount = present(built_line.margin_amount, precision)
            line.commission_amount = present(built_line.commission, precision)
            line.commission_in_scope = built_line.commission_in_scope
            line.commission_reason = built_line.commission_reason
            line.commission_method = (
                built_line.commission_method.value
                if built_line.commission_method is not None
                else None
            )
            line.commission_rate = built_line.commission_rate
            line.list_price = present(built_line.list_price, precision)
            line.discount_amount = present(built_line.discount, precision)
            line.net_price = present(built_line.net_price, precision)
            line.finance_charge = present(built_line.finance_charge, precision)
            line.payable_before_tax = present(built_line.payable_before_tax, precision)
            line.unit_list_price = built_line.unit_list_price
            line.unit_net_price = built_line.unit_net_price
        self._db.flush()

        tax_by_line, tax_review = self._tax(actor, quotation)
        for line in quotation.lines:
            line.tax_amount = present(tax_by_line.get(line.id, ZERO), precision)
            line.total_payable = present(
                money(line.payable_before_tax + line.tax_amount), precision
            )

        quotation.landed_cost_total = self._total(quotation, "landed_cost")
        quotation.margin_total = self._total(quotation, "margin_amount")
        quotation.commission_total = self._total(quotation, "commission_amount")
        quotation.list_total = self._total(quotation, "list_price")
        quotation.discount_total = self._total(quotation, "discount_amount")
        quotation.net_total = self._total(quotation, "net_price")
        quotation.finance_charge_total = self._total(quotation, "finance_charge")
        quotation.payable_before_tax = self._total(quotation, "payable_before_tax")
        quotation.tax_total = self._total(quotation, "tax_amount")
        quotation.total_payable = self._total(quotation, "total_payable")

        needs, reason = self._approval(actor, quotation)
        quotation.requires_approval = needs
        quotation.approval_reason = reason if needs else None
        self._db.flush()
        del tax_review
        return built

    def costing(self, actor: AnyActor, quotation_id: uuid.UUID) -> CostingView:
        """The internal view. Requires ``costing.view``, per PRD §14."""
        actor.require("costing.view")
        quotation = self.require(actor, quotation_id)
        inputs, _, _ = self._inputs_for(actor, quotation)
        built = self._engine(inputs)
        _, tax_review = self._tax(actor, quotation)
        allocations = list(
            self._db.scalars(
                select(QuotationLineCost)
                .where(QuotationLineCost.quotation_fk == quotation.id)
                .order_by(QuotationLineCost.quotation_line_fk)
            )
        )
        evaluation = self._tax_by_rule(actor, quotation)
        return CostingView(
            quotation=quotation,
            build_up=built,
            allocations=allocations,
            tax_by_rule=evaluation,
            tax_review=tax_review,
            variances=[
                CostVariance(
                    reference=str(line.position),
                    estimated=line.landed_cost,
                    actual=(
                        present(
                            money(
                                multiply(line.actual_unit_cost, line.quantity) + line.allocated_cost
                            ),
                            quotation.precision,
                        )
                        if line.actual_unit_cost is not None
                        else ZERO
                    ),
                )
                for line in quotation.lines
            ],
            margin_sources={
                str(line.position): line.margin_source or "not configured"
                for line in quotation.lines
            },
        )

    def offer(self, actor: AnyActor, quotation_id: uuid.UUID) -> dict[str, Any]:
        """The customer's view: no cost, no margin, no commission.

        Read from the stored figures rather than recomputed. Two reasons. After issue
        the stored figures are the figures that were issued, and re-running the engine
        against today's configuration could quietly produce a different document from
        the one the customer holds. And the engine works at six decimal places, which
        is right between steps and wrong on a page: a customer reading 26,493.787929
        is reading a working figure that escaped.

        Every amount is presented at the quotation's own precision. The disclosure
        boundary is the domain's: the projection names what a customer may see, so a
        field added to the build-up later is confidential until somebody decides
        otherwise.
        """
        actor.require("quotation.view")
        quotation = self.require(actor, quotation_id)
        precision = quotation.precision

        def shown(value: Decimal) -> Decimal:
            return present(money(value), precision)

        lines: list[dict[str, Any]] = []
        for line in quotation.lines:
            disclosed = customer_facing(self._stored_build_up(line))
            lines.append(
                {
                    "position": line.position,
                    "description": line.description,
                    "specification": line.specification,
                    # Everything numeric comes through the projection, so the set of
                    # disclosable figures is decided in one place and this method
                    # cannot widen it by naming a field.
                    **{
                        key: shown(value) if isinstance(value, Decimal) else value
                        for key, value in disclosed.items()
                        if key != "reference"
                    },
                    "tax": shown(line.tax_amount),
                    "total": shown(line.total_payable),
                }
            )

        return {
            "number": quotation.number,
            "revision": quotation.revision,
            "quoted_on": quotation.quoted_on,
            "valid_until": quotation.valid_until,
            "currency_code": quotation.currency_code,
            "lines": lines,
            "discount_total": shown(quotation.discount_total),
            "net_total": shown(quotation.net_total),
            "finance_charge_total": shown(quotation.finance_charge_total),
            "payable_before_tax": shown(quotation.payable_before_tax),
            "tax_total": shown(quotation.tax_total),
            "total_payable": shown(quotation.total_payable),
            "terms": quotation.terms,
            "notes": quotation.notes,
            "schedule": [
                {
                    "sequence": item.sequence,
                    "due_on": item.due_on,
                    "amount": item.amount,
                    "is_deposit": item.is_deposit,
                }
                for item in self.schedule(actor, quotation_id)
            ],
        }

    def _stored_build_up(self, line: QuotationLine) -> LineBuildUp:
        """The stored figures as a build-up, so one projection serves both views."""
        return LineBuildUp(
            reference=str(line.position),
            quantity=line.quantity,
            supplier_cost=multiply(line.unit_cost, line.quantity),
            supplier_discount=line.supplier_discount_amount,
            non_recoverable_tax=line.non_recoverable_tax,
            recoverable_tax=line.recoverable_tax,
            allocated_cost=line.allocated_cost,
            landed_cost=line.landed_cost,
            margin_method=MarginMethod(line.margin_method),
            margin_rate=line.margin_rate,
            margin_amount=line.margin_amount,
            margin_inclusive=money(line.landed_cost + line.margin_amount),
            commission_method=(
                CommissionMethod(line.commission_method)
                if line.commission_method is not None
                else None
            ),
            commission_rate=line.commission_rate,
            commission=line.commission_amount,
            commission_in_scope=line.commission_in_scope,
            commission_reason=line.commission_reason or "",
            list_price=line.list_price,
            discount=line.discount_amount,
            net_price=line.net_price,
            finance_charge=line.finance_charge,
            payable_before_tax=line.payable_before_tax,
            unit_list_price=line.unit_list_price,
            unit_net_price=line.unit_net_price,
        )

    # -- lifecycle --------------------------------------------------------------------

    def submit_for_approval(self, actor: AnyActor, quotation_id: uuid.UUID) -> Quotation:
        actor.require("quotation.manage")
        quotation = self.require(actor, quotation_id)
        assert_transition(
            "Quotation",
            QUOTATION_TRANSITIONS,
            QuotationState(quotation.state),
            QuotationState.PENDING_APPROVAL,
        )
        before = as_dict(quotation)
        quotation.state = QuotationState.PENDING_APPROVAL.value
        self._db.flush()
        self._audit.record(
            actor,
            "quotation_submitted",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=quotation.number,
            reason=quotation.approval_reason,
            before=before,
            after=as_dict(quotation),
        )
        return quotation

    def approve(self, actor: AnyActor, quotation_id: uuid.UUID) -> Quotation:
        """Approve a quotation whose discount exceeded the author's limit."""
        actor.require("quotation.approve")
        actor.require("discount.approve")
        quotation = self.require(actor, quotation_id)
        if QuotationState(quotation.state) is not QuotationState.PENDING_APPROVAL:
            raise ConflictError("Only a quotation pending approval can be approved.")
        before = as_dict(quotation)
        quotation.approved_by_id = getattr(actor, "user_id", None)
        quotation.approved_at = self._clock.now()
        quotation.requires_approval = False
        quotation.state = QuotationState.DRAFT.value
        self._db.flush()
        self._audit.record(
            actor,
            "quotation_approved",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=quotation.number,
            before=before,
            after=as_dict(quotation),
        )
        return quotation

    def issue(
        self, actor: AnyActor, quotation_id: uuid.UUID, *, valid_until: date | None = None
    ) -> Quotation:
        """Issue the quotation: number it, snapshot its costing, build its schedule.

        The snapshot is the point of the method. Everything else could be reconstructed
        from the rows; the snapshot is what makes the promise in PRD §6.3 — that a
        supplier price change afterwards leaves this quotation alone — a fact rather
        than an intention.
        """
        actor.require("quotation.issue")
        quotation = self.require(actor, quotation_id)
        assert_transition(
            "Quotation",
            QUOTATION_TRANSITIONS,
            QuotationState(quotation.state),
            QuotationState.ISSUED,
        )
        if not quotation.lines:
            raise ValidationError("A quotation with no lines offers nothing.", field="lines")
        if quotation.requires_approval and quotation.approved_at is None:
            raise ApprovalRequiredError(
                quotation.approval_reason
                or "This quotation exceeds the discount you may grant and needs approval."
            )
        unsourced = [
            line.position for line in quotation.lines if not line.sources and line.unit_cost == ZERO
        ]
        if unsourced:
            raise ValidationError(
                "Lines "
                + ", ".join(str(position) for position in unsourced)
                + " carry neither a source nor a cost, so what is being offered has "
                "no price behind it.",
                field="sources",
            )

        before = as_dict(quotation)
        quotation.state = QuotationState.ISSUED.value
        quotation.issued_at = self._clock.now()
        quotation.issued_by_id = getattr(actor, "user_id", None)
        if valid_until is not None:
            quotation.valid_until = valid_until
        self._db.flush()

        self._snapshot(actor, quotation)
        self._write_schedule(actor, quotation)

        if quotation.request_fk is not None:
            request = self._db.get(QuotationRequest, quotation.request_fk)
            if request is not None and request.state in {"open", "sourcing"}:
                request.state = "quoted"
        self._db.flush()

        self._audit.record(
            actor,
            "quotation_issued",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=f"{quotation.number} revision {quotation.revision}",
            before=before,
            after=as_dict(quotation),
        )
        return quotation

    def decide(
        self,
        actor: AnyActor,
        quotation_id: uuid.UUID,
        *,
        state: QuotationState | str,
        on: date | None = None,
        reason: str | None = None,
    ) -> Quotation:
        """Record the customer's answer, or the passing of the validity date."""
        actor.require("quotation.manage")
        quotation = self.require(actor, quotation_id)
        requested = QuotationState(str(state))
        assert_transition(
            "Quotation", QUOTATION_TRANSITIONS, QuotationState(quotation.state), requested
        )
        if requested is QuotationState.REJECTED and not (reason or "").strip():
            raise ValidationError(
                "A rejection records why. It is the most useful thing a lost "
                "quotation leaves behind.",
                field="reason",
            )
        before = as_dict(quotation)
        quotation.state = requested.value
        quotation.decided_on = on or self._today(actor)
        quotation.decision_reason = (reason or "").strip() or None
        if requested is QuotationState.ACCEPTED:
            quotation.accepted_on = quotation.decided_on
        self._db.flush()
        self._audit.record(
            actor,
            f"quotation_{requested.value}",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=quotation.number,
            reason=reason,
            before=before,
            after=as_dict(quotation),
        )
        return quotation

    def expire_due(self, actor: AnyActor, *, on: date | None = None) -> int:
        """Expire every issued quotation whose validity date has passed."""
        actor.require("quotation.manage")
        today = on or self._today(actor)
        rows = self._db.scalars(
            select(Quotation).where(
                Quotation.business_id == actor.business_id,
                Quotation.state == QuotationState.ISSUED.value,
                Quotation.valid_until.is_not(None),
                Quotation.valid_until < today,
            )
        )
        count = 0
        for quotation in rows:
            quotation.state = QuotationState.EXPIRED.value
            quotation.decided_on = today
            count += 1
        self._db.flush()
        return count

    # -- schedules --------------------------------------------------------------------

    def schedule(self, actor: AnyActor, quotation_id: uuid.UUID) -> list[QuotationInstalment]:
        """The current instalment schedule, per PRD §6.5."""
        actor.require("quotation.view")
        quotation = self.require(actor, quotation_id)
        return list(
            self._db.scalars(
                select(QuotationInstalment)
                .where(
                    QuotationInstalment.quotation_fk == quotation.id,
                    QuotationInstalment.schedule_version == quotation.schedule_version,
                )
                .order_by(QuotationInstalment.sequence)
            )
        )

    def reschedule(
        self,
        actor: AnyActor,
        quotation_id: uuid.UUID,
        *,
        months: int,
        frequency: InstalmentFrequency | str | None = None,
        first_instalment_on: date | None = None,
        reason: str,
    ) -> list[QuotationInstalment]:
        """Restructure the repayment, keeping the original schedule as a version.

        PRD §25.5: the original is never overwritten. The superseded rows are marked
        rescheduled rather than deleted, so what the customer first agreed to is still
        on the record when they ask why the figure changed.
        """
        actor.require("quotation.manage")
        if not reason.strip():
            raise ValidationError("A rescheduling records why.", field="reason")
        quotation = self.require(actor, quotation_id)
        if quotation.payment_plan != PaymentPlan.INSTALMENT.value:
            raise ConflictError("This quotation is not on an instalment plan.")

        for row in self.schedule(actor, quotation_id):
            if row.state in {"scheduled", "due", "overdue"}:
                row.state = "rescheduled"
        quotation.schedule_version += 1
        quotation.instalment_months = months
        if frequency is not None:
            quotation.instalment_frequency = InstalmentFrequency(str(frequency)).value
        if first_instalment_on is not None:
            quotation.first_instalment_on = first_instalment_on
        self._db.flush()
        self._write_schedule(actor, quotation)
        self._audit.record(
            actor,
            "quotation_rescheduled",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=f"{quotation.number} version {quotation.schedule_version}",
            reason=reason.strip(),
            before=None,
            after={"schedule_version": quotation.schedule_version, "months": months},
        )
        return self.schedule(actor, quotation_id)

    # -- reading ----------------------------------------------------------------------

    def require(self, actor: AnyActor, quotation_id: uuid.UUID) -> Quotation:
        actor.require_any("quotation.view", "quotation.manage")
        quotation = self._db.get(Quotation, quotation_id)
        if quotation is None or quotation.business_id != actor.business_id:
            raise NotFoundError("Quotation not found.")
        return quotation

    def quotations(
        self,
        actor: AnyActor,
        *,
        state: str | None = None,
        customer_id: uuid.UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Quotation], int]:
        actor.require("quotation.view")
        statement = select(Quotation).where(Quotation.business_id == actor.business_id)
        if state:
            statement = statement.where(Quotation.state == state)
        if customer_id is not None:
            statement = statement.where(Quotation.customer_fk == customer_id)
        total = self._db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self._db.scalars(
            statement.order_by(Quotation.quoted_on.desc(), Quotation.number.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    def snapshots(self, actor: AnyActor, quotation_id: uuid.UUID) -> list[QuotationSnapshot]:
        actor.require("costing.view")
        quotation = self.require(actor, quotation_id)
        return list(
            self._db.scalars(
                select(QuotationSnapshot)
                .where(QuotationSnapshot.quotation_fk == quotation.id)
                .order_by(QuotationSnapshot.revision)
            )
        )

    def summary(self, actor: AnyActor) -> dict[str, object]:
        """The sales position: what is out, what was won, and what it is worth."""
        actor.require("quotation.view")
        counts: dict[str, int] = {}
        for state, count in self._db.execute(
            select(Quotation.state, func.count())
            .where(Quotation.business_id == actor.business_id)
            .group_by(Quotation.state)
        ):
            counts[str(state)] = int(count)

        def total_in(state: QuotationState) -> Decimal:
            value = self._db.scalar(
                select(func.sum(Quotation.total_payable)).where(
                    Quotation.business_id == actor.business_id,
                    Quotation.state == state.value,
                )
            )
            return money(value or ZERO)

        issued = total_in(QuotationState.ISSUED)
        accepted = total_in(QuotationState.ACCEPTED)
        decided = counts.get(QuotationState.ACCEPTED.value, 0) + counts.get(
            QuotationState.REJECTED.value, 0
        )
        open_requests = self._db.scalar(
            select(func.count())
            .select_from(QuotationRequest)
            .where(
                QuotationRequest.business_id == actor.business_id,
                QuotationRequest.state.in_(["draft", "open", "sourcing"]),
            )
        )
        return {
            "counts": counts,
            "issued_value": issued,
            "accepted_value": accepted,
            "open_requests": int(open_requests or 0),
            "awaiting_approval": counts.get(QuotationState.PENDING_APPROVAL.value, 0),
            "acceptance_rate": (
                divide(money(counts.get(QuotationState.ACCEPTED.value, 0)), money(decided))
                if decided
                else ZERO
            ),
        }

    # -- the engine -------------------------------------------------------------------

    def _engine(self, inputs: DocumentInputs) -> DocumentBuildUp:
        from nickland.domain.pricing import price_document

        return price_document(inputs)

    def _inputs_for(
        self, actor: AnyActor, quotation: Quotation
    ) -> tuple[
        DocumentInputs, dict[str, QuotationExpense], dict[int, tuple[MarginMethod, Decimal, str]]
    ]:
        """Assemble the engine's inputs from the quotation and the configuration.

        A line's reference is its position as a string, so the engine's output can be
        matched back to the row without the engine knowing what a row is.

        Nothing is written here. An earlier version resolved each line's margin and
        assigned it to the row on the way past, which made ``costing`` — a read, and
        one an issued quotation permits — quietly amend the quotation it was asked to
        explain. The resolved margins are returned instead, and ``recost`` is the only
        caller that stores them.
        """
        eligibility = self._commission_arrangement(actor, quotation)
        plan_uplift = self._uplift_for(actor, quotation)
        margins: dict[int, tuple[MarginMethod, Decimal, str]] = {}

        lines: list[LineInputs] = []
        weights: dict[str, Decimal] = {}
        volumes: dict[str, Decimal] = {}
        for line in quotation.lines:
            reference = str(line.position)
            weights[reference] = line.line_weight
            volumes[reference] = line.line_volume

            margins[line.position] = self._margin_for(quotation, line)

            in_scope, reason, rate, method, fixed = self._commission_for(
                line, quotation, eligibility
            )
            method_value, rate_value, fixed_value = method, rate, fixed

            lines.append(
                LineInputs(
                    reference=reference,
                    quantity=line.quantity,
                    unit_cost=line.unit_cost,
                    source_kind=SourceKind(line.source_kind),
                    supplier_discount_rate=line.supplier_discount_rate,
                    supplier_discount_amount=line.supplier_discount_amount,
                    non_recoverable_tax=line.non_recoverable_tax,
                    recoverable_tax=line.recoverable_tax,
                    margin_method=margins[line.position][0],
                    margin_rate=margins[line.position][1],
                    commission_method=method_value,
                    commission_rate=rate_value,
                    commission_fixed_per_unit=fixed_value,
                    commission_in_scope=in_scope,
                    commission_reason=reason,
                    discount_method=DiscountMethod(line.discount_method),
                    discount_value=line.discount_value,
                    uplift_method=plan_uplift[0],
                    uplift_rate=plan_uplift[1],
                    uplift_fixed_per_unit=plan_uplift[2],
                    payment_plan=PaymentPlan(quotation.payment_plan),
                )
            )

        by_reference: dict[str, QuotationExpense] = {}
        expenses: list[ExpenseInputs] = []
        positions = {line.id: str(line.position) for line in quotation.lines}
        for expense in quotation.expenses:
            by_reference[expense.reference] = expense
            nominated = (
                positions.get(expense.nominated_line_fk)
                if expense.nominated_line_fk is not None
                else None
            )
            expenses.append(
                ExpenseInputs(
                    reference=expense.reference,
                    category=expense.category,
                    amount=expense.amount,
                    method=AllocationMethod(expense.method),
                    nominated_line=nominated,
                    excluded_lines=frozenset(str(item) for item in expense.excluded_line_ids),
                    manual_amounts={
                        str(key): money(str(value))
                        for key, value in (expense.manual_amounts or {}).items()
                    },
                    is_disclosed=expense.is_disclosed,
                )
            )

        return (
            DocumentInputs(
                lines=lines,
                expenses=expenses,
                line_weights=weights,
                line_volumes=volumes,
                document_discount_method=DiscountMethod(quotation.document_discount_method),
                document_discount_value=quotation.document_discount_value,
                precision=quotation.precision,
            ),
            by_reference,
            margins,
        )

    def _margin_for(
        self, quotation: Quotation, line: QuotationLine
    ) -> tuple[MarginMethod, Decimal, str]:
        """The margin governing one line, and where it came from.

        A rate set on the line itself wins, which is how an override works. Otherwise
        the policy hierarchy decides, most specific first. A line marked ``line`` with
        a rate of zero is an override to zero, not an absence: the two are different
        decisions and the earlier code could not tell them apart.
        """
        if line.margin_source == "line":
            return MarginMethod(line.margin_method), line.margin_rate, "line"
        resolved = self._policy.resolve_margin(
            quotation.business_id,
            product_id=line.product_fk,
            category_id=self._category_of(line.product_fk),
        )
        return (
            resolved.method,
            resolved.rate,
            resolved.scope.value if resolved.is_configured else "not configured",
        )

    def _commission_arrangement(
        self, actor: AnyActor, quotation: Quotation
    ) -> tuple[AffiliateProfile | None, CommissionScopeTest]:
        """The referrer's arrangement, and what it is eligible for."""
        if quotation.affiliate_fk is None:
            return None, CommissionScopeTest()
        profile = self._db.scalar(
            select(AffiliateProfile).where(
                AffiliateProfile.business_id == actor.business_id,
                AffiliateProfile.party_fk == quotation.affiliate_fk,
                AffiliateProfile.is_active.is_(True),
            )
        )
        if profile is None:
            return None, CommissionScopeTest()
        rows = list(
            self._db.scalars(
                select(AffiliateEligibility).where(AffiliateEligibility.profile_fk == profile.id)
            )
        )
        return profile, CommissionScopeTest(
            eligible_products=frozenset(
                row.product_fk for row in rows if row.product_fk is not None
            ),
            eligible_categories=frozenset(
                row.category_fk for row in rows if row.category_fk is not None
            ),
        )

    def _commission_for(
        self,
        line: QuotationLine,
        quotation: Quotation,
        arrangement: tuple[AffiliateProfile | None, CommissionScopeTest],
    ) -> tuple[bool, str, Decimal, CommissionMethod, Decimal]:
        """Whether this line pays commission, at what rate, and why."""
        profile, eligibility = arrangement
        if profile is None:
            return (
                False,
                "No referrer is attached to this quotation.",
                ZERO,
                CommissionMethod.PERCENTAGE,
                ZERO,
            )
        scope = CommissionScope(
            quotation.commission_scope or profile.scope or CommissionScope.ALL_ELIGIBLE.value
        )
        test = CommissionScopeTest(
            product_id=line.product_fk,
            category_id=self._category_of(line.product_fk),
            line_selected=line.commission_selected,
            eligible_products=eligibility.eligible_products,
            eligible_categories=eligibility.eligible_categories,
        )
        in_scope, reason = commission_in_scope(scope, test)
        rate = profile.rate
        for row in profile.eligible_products:
            if row.rate_override is None:
                continue
            if row.product_fk is not None and row.product_fk == line.product_fk:
                rate = row.rate_override
                break
            if row.category_fk is not None and row.category_fk == test.category_id:
                rate = row.rate_override
        return (
            in_scope,
            reason,
            rate,
            CommissionMethod(profile.method),
            profile.fixed_amount or ZERO,
        )

    def _uplift_for(
        self, actor: AnyActor, quotation: Quotation
    ) -> tuple[UpliftMethod, Decimal, Decimal]:
        """The finance charge governing this quotation, from its plan's duration band."""
        if quotation.payment_plan != PaymentPlan.INSTALMENT.value:
            return UpliftMethod.PERCENTAGE, ZERO, ZERO
        if quotation.instalment_plan_fk is None or quotation.instalment_months is None:
            raise ValidationError(
                "An instalment quotation names its plan and its duration.",
                field="instalment_plan_id",
            )
        plan = self._policy.require_plan(actor, quotation.instalment_plan_fk)
        band = band_for(self._policy.bands_of(plan), quotation.instalment_months)
        return band.method, band.rate, band.fixed_per_unit

    def _tax(
        self, actor: AnyActor, quotation: Quotation
    ) -> tuple[dict[uuid.UUID, Decimal], list[tuple[str, str]]]:
        """Step 7, delegated to the tax engine.

        Nothing is recorded: a quotation is an offer, not a transaction, and recording
        an assessment against one would put a liability on the register for money
        nobody has agreed to pay.
        """
        from nickland.application.services.taxation import TaxableLine, TaxContext, TaxService

        if not actor.has("tax.view"):
            return {}, []
        customer = self._db.get(Party, quotation.customer_fk)
        lines = [
            TaxableLine(
                reference=str(line.position),
                net=line.payable_before_tax,
                quantity=line.quantity,
                product_tax_code=self._tax_code_of(line.product_fk),
                product_category=self._category_code_of(line.product_fk),
                item_class=self._item_class_of(line.product_fk),
                product_id=line.product_fk,
            )
            for line in quotation.lines
        ]
        context = TaxContext(
            transaction_kind="quotation",
            on=quotation.quoted_on,
            direction="sales",
            party_id=quotation.customer_fk,
            party_tax_treatment=customer.tax_treatment if customer is not None else None,
            party_tax_identifier=customer.tax_identifier if customer is not None else None,
            currency_code=quotation.currency_code,
        )
        evaluation = TaxService(self._db, clock=self._clock).evaluate(actor, lines, context)
        by_line = {
            line.id: next(
                (
                    item.charged
                    for item in evaluation.lines
                    if item.line_reference == str(line.position)
                ),
                ZERO,
            )
            for line in quotation.lines
        }
        review = [
            (reference, assessment.detail) for reference, assessment in evaluation.needs_review
        ]
        return by_line, review

    def _tax_by_rule(self, actor: AnyActor, quotation: Quotation) -> dict[str, Decimal]:
        from nickland.application.services.taxation import TaxableLine, TaxContext, TaxService

        if not actor.has("tax.view"):
            return {}
        lines = [
            TaxableLine(
                reference=str(line.position),
                net=line.payable_before_tax,
                quantity=line.quantity,
                item_class=self._item_class_of(line.product_fk),
                product_id=line.product_fk,
            )
            for line in quotation.lines
        ]
        evaluation = TaxService(self._db, clock=self._clock).evaluate(
            actor,
            lines,
            TaxContext(
                transaction_kind="quotation",
                on=quotation.quoted_on,
                direction="sales",
                party_id=quotation.customer_fk,
                currency_code=quotation.currency_code,
            ),
        )
        return evaluation.by_rule()

    def _approval(self, actor: AnyActor, quotation: Quotation) -> tuple[bool, str]:
        """Whether this quotation's discount exceeds what the author may grant."""
        if quotation.discount_total <= ZERO:
            return False, ""
        rate = (
            divide(quotation.discount_total, quotation.list_total)
            if quotation.list_total > ZERO
            else ZERO
        )
        return discount_needs_approval(
            self._policy.limits_for(quotation.business_id),
            role_codes=frozenset(actor.role_codes),
            rate=rate,
            amount=quotation.discount_total,
            may_approve=actor.has("discount.approve"),
        )

    # -- writing ----------------------------------------------------------------------

    def _build_line(
        self, actor: AnyActor, quotation: Quotation, position: int, draft: LineDraft
    ) -> QuotationLine:
        if not draft.description.strip():
            raise ValidationError("A line needs a description.", field="description")
        if draft.product_id is not None:
            self._require_product(actor, draft.product_id)

        line = QuotationLine(
            business_id=actor.business_id,
            quotation_fk=quotation.id,
            position=position,
            request_line_fk=draft.request_line_id,
            product_fk=draft.product_id,
            description=draft.description.strip(),
            specification=draft.specification or None,
            quantity=validate_quantity(draft.quantity),
            unit_fk=draft.unit_id,
            unit_cost=money(draft.unit_cost or ZERO),
            supplier_discount_rate=validate_rate(
                draft.supplier_discount_rate, field_name="supplier_discount_rate", ceiling=ONE
            ),
            supplier_discount_amount=money(draft.supplier_discount_amount),
            non_recoverable_tax=money(draft.non_recoverable_tax),
            recoverable_tax=money(draft.recoverable_tax),
            line_weight=self._weight_of(draft),
            line_volume=self._volume_of(draft),
            commission_selected=draft.commission_selected,
            discount_method=DiscountMethod(str(draft.discount_method)).value,
            discount_value=money(draft.discount_value),
        )
        if draft.margin_method is not None:
            line.margin_method = MarginMethod(str(draft.margin_method)).value
            line.margin_source = "line"
        if draft.margin_rate is not None:
            line.margin_rate = validate_rate(
                draft.margin_rate,
                field_name="margin_rate",
                ceiling=(
                    ONE
                    if MarginMethod(line.margin_method) is MarginMethod.TARGET_GROSS_MARGIN
                    else None
                ),
            )
            line.margin_source = "line"
        self._db.flush()

        if draft.sources:
            self._db.add(line)
            self._db.flush()
            prepared = [
                self._build_source(actor, line, index, source)
                for index, source in enumerate(draft.sources, start=1)
            ]
            total = money(sum((item.quantity for item in prepared), ZERO))
            if total != money(line.quantity):
                raise ValidationError(
                    f"The sources on line {position} supply {total} of {line.quantity}.",
                    field="sources",
                )
            for item in prepared:
                line.sources.append(item)
            weighted = money(
                sum(
                    (
                        multiply(multiply(item.unit_cost, item.exchange_rate), item.quantity)
                        for item in prepared
                    ),
                    ZERO,
                )
            )
            line.unit_cost = divide(weighted, money(line.quantity))
            line.source_kind = (
                SourceKind.STOCK.value
                if all(item.kind == SourceKind.STOCK.value for item in prepared)
                else SourceKind.SUPPLIER.value
            )
        return line

    def _build_source(
        self, actor: AnyActor, line: QuotationLine, position: int, draft: SourceDraft
    ) -> QuotationLineSource:
        kind = SourceKind(str(draft.kind))
        unit_cost = draft.unit_cost
        currency = draft.currency_code
        rate = draft.exchange_rate
        lead_time = draft.lead_time_days
        supplier_id = draft.supplier_id

        if draft.supplier_quotation_line_id is not None:
            offered = self._db.get(SupplierQuotationLine, draft.supplier_quotation_line_id)
            if offered is None or offered.business_id != actor.business_id:
                raise NotFoundError("Supplier quotation line not found.")
            if not offered.is_available:
                raise ValidationError(
                    f"That supplier cannot supply the line: {offered.availability_note}.",
                    field="supplier_quotation_line_id",
                )
            supplier_quotation = offered.quotation
            supplier_id = supplier_id or supplier_quotation.supplier_fk
            if unit_cost is None:
                unit_cost = money(
                    offered.unit_cost - multiply(offered.unit_cost, offered.discount_rate)
                )
            currency = currency or supplier_quotation.currency_code
            rate = rate if rate is not None else supplier_quotation.exchange_rate
            lead_time = lead_time or offered.lead_time_days or supplier_quotation.lead_time_days

        if kind is SourceKind.STOCK:
            supplier_id = None
            if draft.warehouse_id is None:
                raise ValidationError(
                    "A line sourced from stock names the warehouse it comes out of.",
                    field="warehouse_id",
                )
            warehouse = self._db.get(Warehouse, draft.warehouse_id)
            if warehouse is None or warehouse.business_id != actor.business_id:
                raise NotFoundError("Warehouse not found.")
        elif supplier_id is None:
            raise ValidationError(
                "A line sourced from a supplier names the supplier.", field="supplier_id"
            )
        else:
            self._require_party(actor, supplier_id, "supplier")

        if unit_cost is None:
            raise ValidationError(
                "A source carries a cost, either its own or the supplier quotation's.",
                field="unit_cost",
            )

        return QuotationLineSource(
            business_id=actor.business_id,
            quotation_line_fk=line.id,
            position=position,
            kind=kind.value,
            supplier_fk=supplier_id,
            supplier_quotation_line_fk=draft.supplier_quotation_line_id,
            warehouse_fk=draft.warehouse_id if kind is SourceKind.STOCK else None,
            quantity=validate_quantity(draft.quantity),
            unit_cost=money(unit_cost),
            currency_code=(currency or "GHS").upper(),
            exchange_rate=money(rate if rate is not None else ONE),
            lead_time_days=lead_time,
            selection_reason=draft.selection_reason or None,
        )

    def _write_expenses(
        self, actor: AnyActor, quotation: Quotation, expenses: list[ExpenseDraft]
    ) -> None:
        positions = {line.position: line.id for line in quotation.lines}
        for index, draft in enumerate(expenses, start=1):
            method = AllocationMethod(str(draft.method))
            if draft.amount < ZERO:
                raise ValidationError("An expense cannot be negative.", field="amount")
            nominated = None
            if method is AllocationMethod.BY_LINE:
                if draft.nominated_line_position is None:
                    raise ValidationError(
                        "Allocation by line names the line it is charged to.",
                        field="nominated_line_position",
                    )
                nominated = positions.get(draft.nominated_line_position)
                if nominated is None:
                    raise ValidationError(
                        f"Line {draft.nominated_line_position} is not on this quotation.",
                        field="nominated_line_position",
                    )
            self._db.add(
                QuotationExpense(
                    business_id=actor.business_id,
                    quotation_fk=quotation.id,
                    reference=draft.reference or f"E{index}",
                    category=draft.category,
                    description=draft.description or None,
                    amount=money(draft.amount),
                    currency_code=(draft.currency_code or quotation.currency_code).upper(),
                    method=method.value,
                    nominated_line_fk=nominated,
                    excluded_line_ids=[str(position) for position in draft.excluded_line_positions],
                    manual_amounts={
                        str(position): str(amount)
                        for position, amount in draft.manual_amounts.items()
                    },
                    supplier_fk=draft.supplier_id,
                    is_disclosed=draft.is_disclosed,
                )
            )
        self._db.flush()

    def _snapshot(self, actor: AnyActor, quotation: Quotation) -> QuotationSnapshot:
        """Write the costing input set, hashed, and never touch it again."""
        payload: dict[str, Any] = {
            "number": quotation.number,
            "revision": quotation.revision,
            "quoted_on": quotation.quoted_on.isoformat(),
            "currency_code": quotation.currency_code,
            "exchange_rate": str(quotation.exchange_rate),
            "precision": quotation.precision,
            "document_discount": {
                "method": quotation.document_discount_method,
                "value": str(quotation.document_discount_value),
            },
            "payment_plan": quotation.payment_plan,
            "instalment": {
                "plan_id": str(quotation.instalment_plan_fk)
                if quotation.instalment_plan_fk
                else None,
                "months": quotation.instalment_months,
                "frequency": quotation.instalment_frequency,
                "deposit_rate": str(quotation.deposit_rate),
            },
            "totals": {
                "landed_cost": str(quotation.landed_cost_total),
                "margin": str(quotation.margin_total),
                "commission": str(quotation.commission_total),
                "list": str(quotation.list_total),
                "discount": str(quotation.discount_total),
                "net": str(quotation.net_total),
                "finance_charge": str(quotation.finance_charge_total),
                "payable_before_tax": str(quotation.payable_before_tax),
                "tax": str(quotation.tax_total),
                "total_payable": str(quotation.total_payable),
            },
            "expenses": [
                {
                    "reference": expense.reference,
                    "category": expense.category,
                    "amount": str(expense.amount),
                    "method": expense.method,
                }
                for expense in quotation.expenses
            ],
            "lines": [
                {
                    "position": line.position,
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_cost": str(line.unit_cost),
                    "supplier_discount_rate": str(line.supplier_discount_rate),
                    "supplier_discount_amount": str(line.supplier_discount_amount),
                    "non_recoverable_tax": str(line.non_recoverable_tax),
                    "recoverable_tax": str(line.recoverable_tax),
                    "allocated_cost": str(line.allocated_cost),
                    "landed_cost": str(line.landed_cost),
                    "margin_method": line.margin_method,
                    "margin_rate": str(line.margin_rate),
                    "margin_amount": str(line.margin_amount),
                    "commission_method": line.commission_method,
                    "commission_rate": str(line.commission_rate),
                    "commission_amount": str(line.commission_amount),
                    "list_price": str(line.list_price),
                    "discount_amount": str(line.discount_amount),
                    "net_price": str(line.net_price),
                    "finance_charge": str(line.finance_charge),
                    "payable_before_tax": str(line.payable_before_tax),
                    "tax_amount": str(line.tax_amount),
                    "total_payable": str(line.total_payable),
                    "unit_net_price": str(line.unit_net_price),
                    "sources": [
                        {
                            "kind": source.kind,
                            "supplier_id": str(source.supplier_fk) if source.supplier_fk else None,
                            "warehouse_id": str(source.warehouse_fk)
                            if source.warehouse_fk
                            else None,
                            "quantity": str(source.quantity),
                            "unit_cost": str(source.unit_cost),
                            "currency_code": source.currency_code,
                            "exchange_rate": str(source.exchange_rate),
                        }
                        for source in line.sources
                    ],
                }
                for line in quotation.lines
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        snapshot = QuotationSnapshot(
            business_id=quotation.business_id,
            quotation_fk=quotation.id,
            revision=quotation.revision,
            taken_at=self._clock.now(),
            taken_by_id=getattr(actor, "user_id", None),
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            payload=payload,
        )
        self._db.add(snapshot)
        self._db.flush()
        return snapshot

    def _write_schedule(self, actor: AnyActor, quotation: Quotation) -> None:
        """Generate the instalment rows, per PRD §6.5."""
        if quotation.payment_plan != PaymentPlan.INSTALMENT.value:
            return
        if quotation.instalment_months is None:
            raise ValidationError("An instalment plan needs a duration.", field="instalment_months")
        outcome = instalment_schedule(
            quotation.total_payable,
            InstalmentTerms(
                duration_months=quotation.instalment_months,
                frequency=InstalmentFrequency(quotation.instalment_frequency or "monthly"),
                deposit_rate=quotation.deposit_rate,
                first_due_on=quotation.first_instalment_on or quotation.quoted_on,
            ),
            finance_charge=quotation.finance_charge_total,
            precision=quotation.precision,
        )
        if outcome.deposit > ZERO:
            self._db.add(
                QuotationInstalment(
                    business_id=actor.business_id,
                    quotation_fk=quotation.id,
                    schedule_version=quotation.schedule_version,
                    sequence=0,
                    due_on=quotation.quoted_on,
                    amount=outcome.deposit,
                    is_deposit=True,
                )
            )
        for item in outcome.instalments:
            self._db.add(
                QuotationInstalment(
                    business_id=actor.business_id,
                    quotation_fk=quotation.id,
                    schedule_version=quotation.schedule_version,
                    sequence=item.sequence,
                    due_on=item.due_on,
                    amount=item.amount,
                )
            )
        self._db.flush()

    def _instalment_fields(self, actor: AnyActor, draft: QuotationDraft) -> dict[str, Any]:
        """Validate the payment plan and return the columns it sets."""
        plan = PaymentPlan(str(draft.payment_plan))
        if plan is PaymentPlan.OUTRIGHT:
            return {
                "payment_plan": plan.value,
                "instalment_plan_fk": None,
                "instalment_months": None,
                "instalment_frequency": None,
                "deposit_rate": ZERO,
                "first_instalment_on": None,
            }
        if draft.instalment_plan_id is None:
            raise ValidationError(
                "An instalment quotation names the plan it is priced under.",
                field="instalment_plan_id",
            )
        if draft.instalment_months is None:
            raise ValidationError(
                "An instalment quotation names its repayment duration.",
                field="instalment_months",
            )
        plan_row = self._policy.require_plan(actor, draft.instalment_plan_id)
        band_for(self._policy.bands_of(plan_row), draft.instalment_months)
        frequency = InstalmentFrequency(str(draft.instalment_frequency or plan_row.frequency))
        deposit = draft.deposit_rate if draft.deposit_rate is not None else plan_row.deposit_rate
        return {
            "payment_plan": plan.value,
            "instalment_plan_fk": plan_row.id,
            "instalment_months": draft.instalment_months,
            "instalment_frequency": frequency.value,
            "deposit_rate": validate_rate(deposit, field_name="deposit_rate", ceiling=ONE),
            "first_instalment_on": draft.first_instalment_on,
        }

    # -- helpers ----------------------------------------------------------------------

    def _total(self, quotation: Quotation, attribute: str) -> Decimal:
        """A document total: the sum of the stored line figures, never of displayed ones."""
        return present(
            money(sum((getattr(line, attribute) for line in quotation.lines), ZERO)),
            quotation.precision,
        )

    def _reserve_number(self, actor: AnyActor) -> str:
        from nickland.application.services.organisation import OrganisationService

        allocated = OrganisationService(self._db, clock=self._clock).allocate_number(
            actor, DocumentKind.QUOTATION, branch_id=actor.branch_id
        )
        return allocated.number

    def _today(self, actor: AnyActor) -> date:
        business = self._db.get(Business, actor.business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)

    def _require_party(self, actor: AnyActor, party_id: uuid.UUID, label: str) -> Party:
        party = self._db.get(Party, party_id)
        if party is None or party.business_id != actor.business_id:
            raise NotFoundError(f"{label.capitalize()} not found.")
        return party

    def _require_product(self, actor: AnyActor, product_id: uuid.UUID) -> Product:
        product = self._db.get(Product, product_id)
        if product is None or product.business_id != actor.business_id:
            raise NotFoundError("Product not found.")
        return product

    def _require_contact(
        self, actor: AnyActor, contact_id: uuid.UUID, party_id: uuid.UUID
    ) -> Contact:
        contact = self._db.get(Contact, contact_id)
        if contact is None or contact.business_id != actor.business_id:
            raise NotFoundError("Contact not found.")
        if contact.party_fk != party_id:
            raise ValidationError("That contact belongs to another party.", field="contact_id")
        return contact

    def _require_line(self, actor: AnyActor, line_id: uuid.UUID) -> QuotationLine:
        line = self._db.get(QuotationLine, line_id)
        if line is None or line.business_id != actor.business_id:
            raise NotFoundError("Quotation line not found.")
        return line

    def _weight_of(self, draft: LineDraft) -> Decimal:
        """A line's total weight, from the draft or from the catalogue.

        Freight allocated by weight is the common case, and a user who recorded the
        weight on the product should not have to record it again on every line. The
        catalogue holds it per unit, so it is multiplied by the quantity here.
        """
        if draft.line_weight != ZERO:
            return money(draft.line_weight)
        product = self._product(draft.product_id)
        if product is None or product.weight_kg is None:
            return ZERO
        return multiply(product.weight_kg, money(draft.quantity))

    def _volume_of(self, draft: LineDraft) -> Decimal:
        if draft.line_volume != ZERO:
            return money(draft.line_volume)
        product = self._product(draft.product_id)
        if product is None or product.volume_m3 is None:
            return ZERO
        return multiply(product.volume_m3, money(draft.quantity))

    def _product(self, product_id: uuid.UUID | None) -> Product | None:
        if product_id is None:
            return None
        return self._db.get(Product, product_id)

    def _category_of(self, product_id: uuid.UUID | None) -> uuid.UUID | None:
        product = self._product(product_id)
        return product.category_fk if product is not None else None

    def _category_code_of(self, product_id: uuid.UUID | None) -> str | None:
        from nickland.persistence.catalogue_models import ProductCategory

        category_id = self._category_of(product_id)
        if category_id is None:
            return None
        category = self._db.get(ProductCategory, category_id)
        return category.code if category is not None else None

    def _tax_code_of(self, product_id: uuid.UUID | None) -> str | None:
        product = self._product(product_id)
        return getattr(product, "tax_code", None) if product is not None else None

    def _item_class_of(self, product_id: uuid.UUID | None) -> str | None:
        """The item class the tax conditions test against.

        The withholding rules distinguish a service from everything else, so the
        catalogue's four classes are reported as the two the rules can read. A line
        with no product returns ``None``, which the tax engine treats as a missing
        fact rather than as goods: a rule that depends on it then asks for review
        instead of quietly deciding.
        """
        product = self._product(product_id)
        if product is None:
            return None
        return "service" if product.item_class == ItemClass.SERVICE.value else "goods"


def uplift_preview(
    net: Decimal,
    *,
    method: UpliftMethod,
    rate: Decimal,
    fixed_per_unit: Decimal,
    quantity: Decimal,
) -> tuple[Decimal, Decimal]:
    """What a plan would add to a net price. Used by the calculator in the interface."""
    return apply_uplift(
        net,
        method=method,
        rate=rate,
        fixed_per_unit=fixed_per_unit,
        quantity=quantity,
        plan=PaymentPlan.INSTALMENT,
    )
