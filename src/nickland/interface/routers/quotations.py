"""Quotation endpoints: the offer, the costing behind it, and the lifecycle.

The costing endpoint is separate from the quotation endpoint, and requires
``costing.view``. That is the confidentiality boundary of PRD §14 expressed in the
API: a client that never calls ``/costing`` has never been sent a margin.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nickland.application.services.quotations import (
    ExpenseDraft,
    LineDraft,
    QuotationDraft,
    SourceDraft,
)
from nickland.domain.money import ZERO, present
from nickland.interface.dependencies import (
    ActorDependency,
    CatalogueDependency,
    PartyDependency,
    PricingPolicyDependency,
    QuotationDependency,
)
from nickland.interface.pricing_schemas import (
    ActualCostContract,
    AllocationReply,
    BuildUpStepReply,
    CostingReply,
    CreatePlanContract,
    CreateQuotationContract,
    DecideContract,
    DiscountLimitReply,
    ExpenseContract,
    InstalmentPlanReply,
    InstalmentReply,
    IssueContract,
    LineCostingReply,
    MarginPolicyReply,
    QuotationPageReply,
    QuotationReply,
    RescheduleContract,
    ReviseContract,
    SalesSummaryReply,
    ScheduleReply,
    SetBandsContract,
    SetDiscountLimitContract,
    SetExpensesContract,
    SetMarginPolicyContract,
    SetPaymentPlanContract,
    SetSourcesContract,
    SnapshotReply,
    SourceContract,
    SourceReply,
)
from nickland.persistence.pricing_models import MarginPolicy

router = APIRouter(prefix="/quotations", tags=["quotations"])


# -- the offer -------------------------------------------------------------------------


@router.get("", response_model=QuotationPageReply, summary="List quotations")
def list_quotations(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    state: str | None = None,
    customer_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> QuotationPageReply:
    items, total = quotations.quotations(
        actor, state=state, customer_id=customer_id, limit=limit, offset=offset
    )
    return QuotationPageReply(
        items=[_quotation(actor, parties, item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/summary", response_model=SalesSummaryReply, summary="The sales position")
def summary(actor: ActorDependency, quotations: QuotationDependency) -> SalesSummaryReply:
    return SalesSummaryReply.model_validate(quotations.summary(actor))


@router.post("", response_model=QuotationReply, summary="Create a quotation")
def create(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    body: CreateQuotationContract,
) -> QuotationReply:
    quotation = quotations.create(actor, _draft(body))
    return _quotation(actor, parties, quotation)


@router.post(
    "/from-request/{request_id}", response_model=QuotationReply, summary="Quote on a request"
)
def from_request(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    request_id: uuid.UUID,
    body: CreateQuotationContract | None = None,
) -> QuotationReply:
    quotation = quotations.from_request(
        actor, request_id, draft=_draft(body) if body is not None else None
    )
    return _quotation(actor, parties, quotation)


@router.get("/{quotation_id}", response_model=QuotationReply, summary="Read a quotation")
def read(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    quotation_id: uuid.UUID,
) -> QuotationReply:
    return _quotation(actor, parties, quotations.require(actor, quotation_id))


# -- the costing behind it -------------------------------------------------------------


@router.get(
    "/{quotation_id}/costing", response_model=CostingReply, summary="The internal cost build-up"
)
def costing(
    actor: ActorDependency, quotations: QuotationDependency, quotation_id: uuid.UUID
) -> CostingReply:
    view = quotations.costing(actor, quotation_id)
    quotation = view.quotation
    precision = quotation.precision
    by_position = {line.position: line for line in quotation.lines}
    variances = {item.reference: item for item in view.variances}
    expenses = {expense.id: expense for expense in quotation.expenses}
    positions = {line.id: line.position for line in quotation.lines}

    lines: list[LineCostingReply] = []
    for built in view.build_up.lines:
        position = int(built.reference)
        row = by_position[position]
        variance = variances[built.reference]
        lines.append(
            LineCostingReply(
                position=position,
                description=row.description,
                quantity=row.quantity,
                supplier_cost=present(built.supplier_cost, precision),
                supplier_discount=present(built.supplier_discount, precision),
                allocated_cost=present(built.allocated_cost, precision),
                landed_cost=present(built.landed_cost, precision),
                margin_method=built.margin_method.value,
                margin_rate=built.margin_rate,
                margin_source=row.margin_source or "not configured",
                margin_amount=present(built.margin_amount, precision),
                commission=present(built.commission, precision),
                commission_rate=built.commission_rate,
                commission_in_scope=built.commission_in_scope,
                commission_reason=built.commission_reason,
                list_price=present(built.list_price, precision),
                discount=present(built.discount, precision),
                net_price=present(built.net_price, precision),
                finance_charge=present(built.finance_charge, precision),
                payable_before_tax=present(built.payable_before_tax, precision),
                contribution_before_commission=present(
                    built.contribution_before_commission, precision
                ),
                contribution_after_commission=present(
                    built.contribution_after_commission, precision
                ),
                realised_margin_rate=built.realised_margin_rate,
                estimated_cost=present(variance.estimated, precision),
                actual_cost=present(variance.actual, precision),
                variance=present(variance.variance, precision) if variance.is_known else ZERO,
                variance_rate=variance.variance_rate if variance.is_known else ZERO,
                steps=[
                    BuildUpStepReply(step=step, label=label, amount=present(amount, precision))
                    for step, label, amount in built.steps()
                ],
                sources=[SourceReply.model_validate(source) for source in row.sources],
            )
        )

    return CostingReply(
        quotation_id=quotation.id,
        number=quotation.number,
        state=quotation.state,
        currency_code=quotation.currency_code,
        landed_cost_total=quotation.landed_cost_total,
        margin_total=quotation.margin_total,
        commission_total=quotation.commission_total,
        list_total=quotation.list_total,
        discount_total=quotation.discount_total,
        net_total=quotation.net_total,
        finance_charge_total=quotation.finance_charge_total,
        payable_before_tax=quotation.payable_before_tax,
        tax_total=quotation.tax_total,
        total_payable=quotation.total_payable,
        contribution_before_commission=present(
            view.build_up.contribution_before_commission, precision
        ),
        contribution_after_commission=present(
            view.build_up.contribution_after_commission, precision
        ),
        requires_approval=quotation.requires_approval,
        approval_reason=quotation.approval_reason,
        tax_by_rule=view.tax_by_rule,
        tax_review=[f"Line {reference}: {detail}" for reference, detail in view.tax_review],
        lines=lines,
        allocations=[
            AllocationReply(
                line_position=positions.get(row.quotation_line_fk, 0),
                expense_reference=(
                    expenses[row.expense_fk].reference if row.expense_fk in expenses else ""
                ),
                category=row.category,
                method=row.method,
                amount=row.amount,
            )
            for row in view.allocations
        ],
    )


@router.get(
    "/{quotation_id}/snapshots", response_model=list[SnapshotReply], summary="Costing snapshots"
)
def snapshots(
    actor: ActorDependency, quotations: QuotationDependency, quotation_id: uuid.UUID
) -> list[SnapshotReply]:
    return [
        SnapshotReply.model_validate(item) for item in quotations.snapshots(actor, quotation_id)
    ]


# -- amendment -------------------------------------------------------------------------


@router.put(
    "/lines/{line_id}/sources", response_model=QuotationReply, summary="Choose a line's sources"
)
def set_sources(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    line_id: uuid.UUID,
    body: SetSourcesContract,
) -> QuotationReply:
    line = quotations.set_sources(actor, line_id, [_source(item) for item in body.sources])
    return _quotation(actor, parties, quotations.require(actor, line.quotation_fk))


@router.put(
    "/{quotation_id}/expenses", response_model=QuotationReply, summary="Set allocable expenses"
)
def set_expenses(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    quotation_id: uuid.UUID,
    body: SetExpensesContract,
) -> QuotationReply:
    quotation = quotations.set_expenses(
        actor, quotation_id, [_expense(item) for item in body.expenses]
    )
    return _quotation(actor, parties, quotation)


@router.put(
    "/{quotation_id}/payment-plan", response_model=QuotationReply, summary="Set the payment plan"
)
def set_payment_plan(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    quotation_id: uuid.UUID,
    body: SetPaymentPlanContract,
) -> QuotationReply:
    quotation = quotations.set_payment_plan(
        actor,
        quotation_id,
        plan=body.plan,
        instalment_plan_id=body.instalment_plan_id,
        months=body.months,
        frequency=body.frequency,
        deposit_rate=body.deposit_rate,
        first_instalment_on=body.first_instalment_on,
    )
    return _quotation(actor, parties, quotation)


@router.post(
    "/lines/{line_id}/actual-cost", response_model=QuotationReply, summary="Record the actual cost"
)
def record_actual_cost(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    line_id: uuid.UUID,
    body: ActualCostContract,
) -> QuotationReply:
    line = quotations.record_actual_cost(
        actor,
        line_id,
        actual_unit_cost=body.actual_unit_cost,
        reference=body.reference,
        on=body.on,
    )
    return _quotation(actor, parties, quotations.require(actor, line.quotation_fk))


# -- lifecycle -------------------------------------------------------------------------


@router.post("/{quotation_id}/submit", response_model=QuotationReply, summary="Submit for approval")
def submit(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    quotation_id: uuid.UUID,
) -> QuotationReply:
    return _quotation(actor, parties, quotations.submit_for_approval(actor, quotation_id))


@router.post("/{quotation_id}/approve", response_model=QuotationReply, summary="Approve")
def approve(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    quotation_id: uuid.UUID,
) -> QuotationReply:
    return _quotation(actor, parties, quotations.approve(actor, quotation_id))


@router.post(
    "/{quotation_id}/issue", response_model=QuotationReply, summary="Issue to the customer"
)
def issue(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    quotation_id: uuid.UUID,
    body: IssueContract,
) -> QuotationReply:
    return _quotation(
        actor, parties, quotations.issue(actor, quotation_id, valid_until=body.valid_until)
    )


@router.post("/{quotation_id}/decide", response_model=QuotationReply, summary="Record the answer")
def decide(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    quotation_id: uuid.UUID,
    body: DecideContract,
) -> QuotationReply:
    quotation = quotations.decide(
        actor, quotation_id, state=body.state, on=body.on, reason=body.reason
    )
    return _quotation(actor, parties, quotation)


@router.post("/{quotation_id}/revise", response_model=QuotationReply, summary="Open a revision")
def revise(
    actor: ActorDependency,
    quotations: QuotationDependency,
    parties: PartyDependency,
    quotation_id: uuid.UUID,
    body: ReviseContract,
) -> QuotationReply:
    return _quotation(actor, parties, quotations.revise(actor, quotation_id, reason=body.reason))


# -- instalments -----------------------------------------------------------------------


@router.get(
    "/{quotation_id}/schedule", response_model=ScheduleReply, summary="The instalment schedule"
)
def schedule(
    actor: ActorDependency, quotations: QuotationDependency, quotation_id: uuid.UUID
) -> ScheduleReply:
    quotation = quotations.require(actor, quotation_id)
    rows = quotations.schedule(actor, quotation_id)
    payments = [row for row in rows if not row.is_deposit]
    deposit = next((row.amount for row in rows if row.is_deposit), ZERO)
    return ScheduleReply(
        deposit=deposit,
        instalment_amount=payments[0].amount if payments else ZERO,
        frequency=quotation.instalment_frequency,
        duration_months=quotation.instalment_months,
        first_due_on=payments[0].due_on if payments else None,
        final_due_on=payments[-1].due_on if payments else None,
        finance_charge=quotation.finance_charge_total,
        total_payable=quotation.total_payable,
        instalments=[InstalmentReply.model_validate(row) for row in rows],
    )


@router.post(
    "/{quotation_id}/reschedule", response_model=ScheduleReply, summary="Restructure the repayment"
)
def reschedule(
    actor: ActorDependency,
    quotations: QuotationDependency,
    quotation_id: uuid.UUID,
    body: RescheduleContract,
) -> ScheduleReply:
    quotations.reschedule(
        actor,
        quotation_id,
        months=body.months,
        frequency=body.frequency,
        first_instalment_on=body.first_instalment_on,
        reason=body.reason,
    )
    return schedule(actor, quotations, quotation_id)


# -- configuration ---------------------------------------------------------------------


@router.get("/policy/margins", response_model=list[MarginPolicyReply], summary="Margin policies")
def margin_policies(
    actor: ActorDependency,
    policy: PricingPolicyDependency,
    catalogue: CatalogueDependency,
) -> list[MarginPolicyReply]:
    return [_policy(actor, catalogue, item) for item in policy.margin_policies(actor)]


@router.put("/policy/margins", response_model=MarginPolicyReply, summary="Set a margin policy")
def set_margin_policy(
    actor: ActorDependency,
    policy: PricingPolicyDependency,
    catalogue: CatalogueDependency,
    body: SetMarginPolicyContract,
) -> MarginPolicyReply:
    return _policy(
        actor,
        catalogue,
        policy.set_margin_policy(
            actor,
            scope=body.scope,
            method=body.method,
            rate=body.rate,
            category_id=body.category_id,
            product_id=body.product_id,
            notes=body.notes,
        ),
    )


@router.delete("/policy/margins/{policy_id}", status_code=204, summary="Withdraw a margin policy")
def withdraw_margin_policy(
    actor: ActorDependency, policy: PricingPolicyDependency, policy_id: uuid.UUID
) -> None:
    policy.withdraw_margin_policy(actor, policy_id)


@router.get(
    "/policy/discount-limits", response_model=list[DiscountLimitReply], summary="Discount limits"
)
def discount_limits(
    actor: ActorDependency, policy: PricingPolicyDependency
) -> list[DiscountLimitReply]:
    return [DiscountLimitReply.model_validate(item) for item in policy.discount_limits(actor)]


@router.put(
    "/policy/discount-limits", response_model=DiscountLimitReply, summary="Set a discount limit"
)
def set_discount_limit(
    actor: ActorDependency, policy: PricingPolicyDependency, body: SetDiscountLimitContract
) -> DiscountLimitReply:
    return DiscountLimitReply.model_validate(
        policy.set_discount_limit(
            actor,
            role_code=body.role_code,
            maximum_rate=body.maximum_rate,
            maximum_amount=body.maximum_amount,
        )
    )


@router.get("/policy/plans", response_model=list[InstalmentPlanReply], summary="Instalment plans")
def plans(actor: ActorDependency, policy: PricingPolicyDependency) -> list[InstalmentPlanReply]:
    return [InstalmentPlanReply.model_validate(item) for item in policy.plans(actor)]


@router.post("/policy/plans", response_model=InstalmentPlanReply, summary="Create a plan")
def create_plan(
    actor: ActorDependency, policy: PricingPolicyDependency, body: CreatePlanContract
) -> InstalmentPlanReply:
    return InstalmentPlanReply.model_validate(
        policy.create_plan(
            actor,
            code=body.code,
            name=body.name,
            frequency=body.frequency,
            deposit_rate=body.deposit_rate,
            applies_to=body.applies_to,
            category_id=body.category_id,
            product_id=body.product_id,
            customer_id=body.customer_id,
            description=body.description,
        )
    )


@router.put(
    "/policy/plans/{plan_id}/bands",
    response_model=InstalmentPlanReply,
    summary="Set duration bands",
)
def set_bands(
    actor: ActorDependency,
    policy: PricingPolicyDependency,
    plan_id: uuid.UUID,
    body: SetBandsContract,
) -> InstalmentPlanReply:
    return InstalmentPlanReply.model_validate(
        policy.set_bands(
            actor,
            plan_id,
            [
                {
                    "minimum_months": band.minimum_months,
                    "maximum_months": band.maximum_months,
                    "method": band.method,
                    "rate": band.rate,
                    "fixed_per_unit": band.fixed_per_unit,
                    "label": band.label,
                }
                for band in body.bands
            ],
        )
    )


# -- projection ------------------------------------------------------------------------


def _policy(
    actor: ActorDependency, catalogue: CatalogueDependency, row: MarginPolicy
) -> MarginPolicyReply:
    reply = MarginPolicyReply.model_validate(row)
    try:
        if row.product_fk is not None:
            product = catalogue.require_product(actor, row.product_fk)
            reply.target_name = f"{product.sku} — {product.name}"
        elif row.category_fk is not None:
            category = next(
                item for item in catalogue.categories(actor) if item.id == row.category_fk
            )
            reply.target_name = category.name
    except Exception:
        reply.target_name = None
    return reply


def _quotation(actor: ActorDependency, parties: PartyDependency, row: object) -> QuotationReply:
    reply = QuotationReply.model_validate(row)
    try:
        reply.customer_name = parties.require(actor, reply.customer_fk).name
    except Exception:
        reply.customer_name = None
    return reply


def _draft(body: CreateQuotationContract) -> QuotationDraft:
    return QuotationDraft(
        customer_id=body.customer_id,
        request_id=body.request_id,
        contact_id=body.contact_id,
        customer_reference=body.customer_reference,
        quoted_on=body.quoted_on,
        valid_until=body.valid_until,
        currency_code=body.currency_code,
        exchange_rate=body.exchange_rate,
        precision=body.precision,
        document_discount_method=body.document_discount_method,
        document_discount_value=body.document_discount_value,
        affiliate_id=body.affiliate_id,
        commission_scope=body.commission_scope,
        payment_plan=body.payment_plan,
        instalment_plan_id=body.instalment_plan_id,
        instalment_months=body.instalment_months,
        instalment_frequency=body.instalment_frequency,
        deposit_rate=body.deposit_rate,
        first_instalment_on=body.first_instalment_on,
        terms=body.terms,
        notes=body.notes,
        internal_notes=body.internal_notes,
        lines=[
            LineDraft(
                description=line.description,
                quantity=line.quantity,
                product_id=line.product_id,
                request_line_id=line.request_line_id,
                specification=line.specification,
                unit_id=line.unit_id,
                sources=[_source(item) for item in line.sources],
                unit_cost=line.unit_cost,
                supplier_discount_rate=line.supplier_discount_rate,
                supplier_discount_amount=line.supplier_discount_amount,
                non_recoverable_tax=line.non_recoverable_tax,
                recoverable_tax=line.recoverable_tax,
                line_weight=line.line_weight,
                line_volume=line.line_volume,
                margin_method=line.margin_method,
                margin_rate=line.margin_rate,
                commission_selected=line.commission_selected,
                discount_method=line.discount_method,
                discount_value=line.discount_value,
            )
            for line in body.lines
        ],
        expenses=[_expense(item) for item in body.expenses],
    )


def _source(item: SourceContract) -> SourceDraft:
    return SourceDraft(
        kind=item.kind,
        quantity=item.quantity,
        unit_cost=item.unit_cost,
        supplier_id=item.supplier_id,
        supplier_quotation_line_id=item.supplier_quotation_line_id,
        warehouse_id=item.warehouse_id,
        currency_code=item.currency_code,
        exchange_rate=item.exchange_rate,
        lead_time_days=item.lead_time_days,
        selection_reason=item.selection_reason,
    )


def _expense(item: ExpenseContract) -> ExpenseDraft:
    return ExpenseDraft(
        category=item.category,
        amount=item.amount,
        reference=item.reference,
        description=item.description,
        method=item.method,
        nominated_line_position=item.nominated_line_position,
        excluded_line_positions=item.excluded_line_positions,
        manual_amounts=item.manual_amounts,
        supplier_id=item.supplier_id,
        is_disclosed=item.is_disclosed,
        currency_code=item.currency_code,
    )
