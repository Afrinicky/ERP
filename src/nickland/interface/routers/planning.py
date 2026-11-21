"""Budgets, the cash forecast, alerts, profitability and the charts."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter

from nickland.application.context import AnyActor
from nickland.application.services.budgets import BudgetDraft, BudgetService, LineDraft
from nickland.application.services.forecasts import Forecast
from nickland.domain.planning import (
    AnalysisKind,
    Assumptions,
    BudgetState,
    ErosionCause,
    ForecastClass,
    PlannedFlow,
    ScenarioKind,
)
from nickland.interface.dependencies import (
    ActorDependency,
    AnalyticsDependency,
    BudgetDependency,
    ForecastDependency,
    ProfitabilityDependency,
    SessionDependency,
)
from nickland.interface.finance_replies import Names
from nickland.interface.planning_schemas import (
    AlertReply,
    AmendAnalysisCodeContract,
    AnalysisCodeContract,
    AnalysisCodeReply,
    AssumptionsContract,
    BudgetContract,
    BudgetDetailReply,
    BudgetLineReply,
    BudgetLinesContract,
    BudgetRequestContract,
    BudgetRequestReply,
    BudgetSummaryReply,
    CellReply,
    ChartListReply,
    ChartReply,
    ComparisonReply,
    DecisionContract,
    DecisionReply,
    ErosionReply,
    ErosionRowReply,
    FactReply,
    FlowItemReply,
    ForecastReply,
    ForecastRowReply,
    LineComparisonReply,
    NoteContract,
    PeriodReply,
    PlannedContract,
    PlannedReply,
    PointReply,
    PostingReply,
    ProfitabilityReply,
    ProfitRowReply,
    ReasonContract,
    ScenarioContract,
    ScenarioReply,
    SeriesReply,
    ThresholdContract,
    ThresholdReply,
    ToggleContract,
    ToggleReply,
    VarianceReply,
    WhatIfContract,
)

router = APIRouter(tags=["planning"])


# -- analysis codes ------------------------------------------------------------------------


@router.get("/planning/analysis-codes", response_model=list[AnalysisCodeReply], summary="Codes")
def codes(
    actor: ActorDependency, service: BudgetDependency, kind: str | None = None
) -> list[AnalysisCodeReply]:
    return [_code(row) for row in service.codes(actor, kind=kind)]


@router.post(
    "/planning/analysis-codes", response_model=AnalysisCodeReply, status_code=201, summary="Add"
)
def create_code(
    body: AnalysisCodeContract, actor: ActorDependency, service: BudgetDependency
) -> AnalysisCodeReply:
    return _code(service.create_code(actor, kind=body.kind, code=body.code, name=body.name))


@router.patch(
    "/planning/analysis-codes/{code_id}", response_model=AnalysisCodeReply, summary="Amend"
)
def amend_code(
    code_id: uuid.UUID,
    body: AmendAnalysisCodeContract,
    actor: ActorDependency,
    service: BudgetDependency,
) -> AnalysisCodeReply:
    return _code(service.amend_code(actor, code_id, name=body.name, is_active=body.is_active))


# -- budgets -------------------------------------------------------------------------------


@router.get("/planning/budgets", response_model=list[BudgetSummaryReply], summary="Budgets")
def budgets(
    actor: ActorDependency, service: BudgetDependency, db: SessionDependency
) -> list[BudgetSummaryReply]:
    names = Names(db)
    return [_summary(service, names, row) for row in service.budgets(actor)]


@router.post(
    "/planning/budgets", response_model=BudgetDetailReply, status_code=201, summary="Create"
)
def create_budget(
    body: BudgetContract, actor: ActorDependency, service: BudgetDependency, db: SessionDependency
) -> BudgetDetailReply:
    return _detail(
        service, Names(db), actor, service.create(actor, BudgetDraft(**body.model_dump()))
    )


@router.get("/planning/budgets/{budget_id}", response_model=BudgetDetailReply, summary="Budget")
def budget(
    budget_id: uuid.UUID, actor: ActorDependency, service: BudgetDependency, db: SessionDependency
) -> BudgetDetailReply:
    return _detail(service, Names(db), actor, service.require(actor, budget_id))


@router.put("/planning/budgets/{budget_id}", response_model=BudgetDetailReply, summary="Amend")
def amend_budget(
    budget_id: uuid.UUID,
    body: BudgetContract,
    actor: ActorDependency,
    service: BudgetDependency,
    db: SessionDependency,
) -> BudgetDetailReply:
    made = service.amend(actor, budget_id, BudgetDraft(**body.model_dump()))
    return _detail(service, Names(db), actor, made)


@router.put(
    "/planning/budgets/{budget_id}/lines", response_model=BudgetDetailReply, summary="Set lines"
)
def set_lines(
    budget_id: uuid.UUID,
    body: BudgetLinesContract,
    actor: ActorDependency,
    service: BudgetDependency,
    db: SessionDependency,
) -> BudgetDetailReply:
    drafts = [LineDraft(**line.model_dump()) for line in body.lines]
    return _detail(service, Names(db), actor, service.set_lines(actor, budget_id, drafts))


@router.post(
    "/planning/budgets/{budget_id}/submit", response_model=BudgetDetailReply, summary="Submit"
)
def submit_budget(
    budget_id: uuid.UUID, actor: ActorDependency, service: BudgetDependency, db: SessionDependency
) -> BudgetDetailReply:
    return _detail(service, Names(db), actor, service.submit(actor, budget_id))


@router.post(
    "/planning/budgets/{budget_id}/approve", response_model=BudgetDetailReply, summary="Approve"
)
def approve_budget(
    budget_id: uuid.UUID,
    body: NoteContract,
    actor: ActorDependency,
    service: BudgetDependency,
    db: SessionDependency,
) -> BudgetDetailReply:
    return _detail(service, Names(db), actor, service.approve(actor, budget_id, note=body.note))


@router.post(
    "/planning/budgets/{budget_id}/reject", response_model=BudgetDetailReply, summary="Return"
)
def reject_budget(
    budget_id: uuid.UUID,
    body: ReasonContract,
    actor: ActorDependency,
    service: BudgetDependency,
    db: SessionDependency,
) -> BudgetDetailReply:
    return _detail(service, Names(db), actor, service.reject(actor, budget_id, reason=body.reason))


@router.post(
    "/planning/budgets/{budget_id}/revise", response_model=BudgetDetailReply, summary="Revise"
)
def revise_budget(
    budget_id: uuid.UUID, actor: ActorDependency, service: BudgetDependency, db: SessionDependency
) -> BudgetDetailReply:
    return _detail(service, Names(db), actor, service.revise(actor, budget_id))


@router.get(
    "/planning/budgets/{budget_id}/comparison",
    response_model=ComparisonReply,
    summary="Budget against actual",
)
def comparison(
    budget_id: uuid.UUID, actor: ActorDependency, service: BudgetDependency
) -> ComparisonReply:
    made = service.comparison(actor, budget_id)
    return ComparisonReply(
        budget_id=made.budget.id,
        as_of=made.as_of,
        periods=[_period(row) for row in made.periods],
        lines=[
            LineComparisonReply(
                line_id=line.line_id,
                label=line.label,
                nature=line.nature.value,
                analysis=line.analysis,
                cells=[_cell(cell) for cell in line.cells],
                total=_cell(line.total),
                forecast=line.forecast,
            )
            for line in made.lines
        ],
        income=_cell(made.income),
        costs=_cell(made.costs),
        bases=made.bases,
    )


@router.get(
    "/planning/budgets/{budget_id}/drill",
    response_model=list[PostingReply],
    summary="The postings behind an actual",
)
def budget_drill(
    budget_id: uuid.UUID,
    line_id: uuid.UUID,
    period_key: str,
    actor: ActorDependency,
    service: BudgetDependency,
) -> list[PostingReply]:
    rows = service.drill(actor, budget_id, line_id, period_key)
    budget = service.require(actor, budget_id)
    line = next(row for row in budget.lines if row.id == line_id)
    income = service.nature_of(budget.business_id, line).value == "income"
    return [
        PostingReply(
            posting_date=posting.posting_date,
            entry_id=entry.id,
            number=entry.number,
            account_code=account.code,
            account_name=account.name,
            narration=posting.narration or entry.narration,
            amount=(posting.base_credit - posting.base_debit)
            if income
            else (posting.base_debit - posting.base_credit),
        )
        for posting, entry, account in rows
    ]


@router.post(
    "/planning/budgets/{budget_id}/requests",
    response_model=BudgetRequestReply,
    status_code=201,
    summary="Ask to transfer or overspend",
)
def request(
    budget_id: uuid.UUID,
    body: BudgetRequestContract,
    actor: ActorDependency,
    service: BudgetDependency,
    db: SessionDependency,
) -> BudgetRequestReply:
    made = service.request(actor, budget_id, **body.model_dump())
    return _request(Names(db), made)


@router.get("/planning/requests", response_model=list[BudgetRequestReply], summary="Requests")
def requests(
    actor: ActorDependency, service: BudgetDependency, db: SessionDependency
) -> list[BudgetRequestReply]:
    names = Names(db)
    return [_request(names, row) for row in service.requests(actor)]


@router.post(
    "/planning/requests/{request_id}/decision",
    response_model=BudgetRequestReply,
    summary="Decide a request",
)
def decide(
    request_id: uuid.UUID,
    body: DecisionContract,
    actor: ActorDependency,
    service: BudgetDependency,
    db: SessionDependency,
) -> BudgetRequestReply:
    made = service.decide(actor, request_id, approve=body.approve, note=body.note)
    return _request(Names(db), made)


@router.get("/planning/budget-control", response_model=ToggleReply, summary="Overspend control")
def budget_control(actor: ActorDependency, service: BudgetDependency) -> ToggleReply:
    assert actor.business_id is not None
    return ToggleReply(enabled=service.control_enabled(actor.business_id))


@router.put("/planning/budget-control", response_model=ToggleReply, summary="Set it")
def set_budget_control(
    body: ToggleContract, actor: ActorDependency, service: BudgetDependency
) -> ToggleReply:
    return ToggleReply(enabled=service.set_control(actor, body.enabled))


# -- forecast ------------------------------------------------------------------------------


@router.get("/planning/scenarios", response_model=list[ScenarioReply], summary="Scenarios")
def scenarios(actor: ActorDependency, service: ForecastDependency) -> list[ScenarioReply]:
    return [_scenario(row) for row in service.scenarios(actor)]


@router.post(
    "/planning/scenarios", response_model=ScenarioReply, status_code=201, summary="Add scenario"
)
def create_scenario(
    body: ScenarioContract, actor: ActorDependency, service: ForecastDependency
) -> ScenarioReply:
    made = service.save_scenario(
        actor, name=body.name, assumptions=_assumptions(body.assumptions), notes=body.notes
    )
    return _scenario(made)


@router.put(
    "/planning/scenarios/{scenario_id}", response_model=ScenarioReply, summary="Amend scenario"
)
def amend_scenario(
    scenario_id: uuid.UUID,
    body: ScenarioContract,
    actor: ActorDependency,
    service: ForecastDependency,
) -> ScenarioReply:
    made = service.save_scenario(
        actor,
        name=body.name,
        assumptions=_assumptions(body.assumptions),
        scenario_id=scenario_id,
        notes=body.notes,
    )
    return _scenario(made)


@router.get("/planning/forecast", response_model=ForecastReply, summary="Cash forecast")
def forecast(
    actor: ActorDependency,
    service: ForecastDependency,
    horizon_days: int = 30,
    scenario_id: uuid.UUID | None = None,
) -> ForecastReply:
    return _forecast(service.forecast(actor, horizon_days=horizon_days, scenario_id=scenario_id))


@router.post("/planning/forecast", response_model=ForecastReply, summary="Test a what-if")
def what_if(
    body: WhatIfContract, actor: ActorDependency, service: ForecastDependency
) -> ForecastReply:
    return _forecast(
        service.forecast(
            actor,
            horizon_days=body.horizon_days,
            scenario_id=body.scenario_id,
            assumptions=_assumptions(body.assumptions),
        )
    )


@router.get("/planning/planned", response_model=list[PlannedReply], summary="Planned items")
def planned(actor: ActorDependency, service: ForecastDependency) -> list[PlannedReply]:
    return [_planned(row) for row in service.planned(actor)]


@router.post(
    "/planning/planned", response_model=PlannedReply, status_code=201, summary="Plan an item"
)
def plan(
    body: PlannedContract, actor: ActorDependency, service: ForecastDependency
) -> PlannedReply:
    return _planned(service.plan(actor, **body.model_dump()))


@router.delete("/planning/planned/{item_id}", status_code=204, summary="Remove a planned item")
def unplan(item_id: uuid.UUID, actor: ActorDependency, service: ForecastDependency) -> None:
    service.unplan(actor, item_id)


@router.get("/planning/cash-threshold", response_model=ThresholdReply, summary="Low-cash line")
def cash_threshold(actor: ActorDependency, service: ForecastDependency) -> ThresholdReply:
    assert actor.business_id is not None
    return ThresholdReply(amount=service.threshold(actor.business_id))


@router.put("/planning/cash-threshold", response_model=ThresholdReply, summary="Set it")
def set_cash_threshold(
    body: ThresholdContract, actor: ActorDependency, service: ForecastDependency
) -> ThresholdReply:
    return ThresholdReply(amount=service.set_threshold(actor, body.amount))


@router.get("/planning/alerts", response_model=list[AlertReply], summary="What needs attention")
def alerts(actor: ActorDependency, service: ForecastDependency) -> list[AlertReply]:
    return [
        AlertReply(
            severity=row.severity,
            kind=row.kind,
            title=row.title,
            detail=row.detail,
            amount=row.amount,
            count=row.count,
            view=row.view,
        )
        for row in service.alerts(actor)
    ]


# -- analytics -----------------------------------------------------------------------------


@router.get("/analytics/charts", response_model=list[ChartListReply], summary="Charts")
def charts(actor: ActorDependency) -> list[ChartListReply]:
    from nickland.application.services.analytics import CHARTS

    actor.require("report.view")
    return [ChartListReply(id=key, title=title) for key, title in CHARTS]


@router.get("/analytics/charts/{chart_id}", response_model=ChartReply, summary="A chart")
def chart(
    chart_id: str,
    start: date,
    end: date,
    actor: ActorDependency,
    service: AnalyticsDependency,
    interval: str = "month",
    compare: bool = True,
) -> ChartReply:
    made = service.chart(actor, chart_id, start=start, end=end, interval=interval, compare=compare)
    return ChartReply(
        id=made.id,
        title=made.title,
        kind=made.kind,
        unit=made.unit,
        starts_on=made.starts_on,
        ends_on=made.ends_on,
        interval=made.interval,
        series=[
            SeriesReply(
                key=row.key,
                label=row.label,
                basis=row.basis.value,
                basis_label=row.basis.label,
                role=row.role,
            )
            for row in made.series
        ],
        points=[
            PointReply(key=row.key, label=row.label, values=row.values, drill=row.drill)
            for row in made.points
        ],
        note=made.note,
    )


@router.get("/analytics/drill", response_model=list[PostingReply], summary="Postings")
def drill(
    measure: str, start: date, end: date, actor: ActorDependency, service: AnalyticsDependency
) -> list[PostingReply]:
    return [
        PostingReply(
            posting_date=row.posting_date,
            entry_id=row.entry_id,
            number=row.number,
            account_code=row.account_code,
            account_name=row.account_name,
            narration=row.narration,
            amount=row.amount,
        )
        for row in service.drill(actor, measure=measure, start=start, end=end)
    ]


@router.get("/analytics/profitability", response_model=ProfitabilityReply, summary="Profit")
def profitability(
    dimension: str,
    start: date,
    end: date,
    actor: ActorDependency,
    service: ProfitabilityDependency,
    allocate: bool = False,
) -> ProfitabilityReply:
    made = service.profitability(
        actor, dimension=dimension, start=start, end=end, allocate=allocate
    )
    return ProfitabilityReply(
        basis=made.basis.value,
        basis_label=made.basis.label,
        dimension=made.dimension.value,
        dimension_label=made.dimension.label,
        starts_on=made.starts_on,
        ends_on=made.ends_on,
        rows=[_profit(row) for row in made.rows],
        total=_profit(made.total),
        allocated=made.allocated,
        direct_pool=made.direct_pool,
        overhead_pool=made.overhead_pool,
    )


@router.get(
    "/analytics/profitability/records", response_model=list[FactReply], summary="Sale lines"
)
def profit_records(
    dimension: str,
    key: str,
    start: date,
    end: date,
    actor: ActorDependency,
    service: ProfitabilityDependency,
) -> list[FactReply]:
    return [
        FactReply(
            source_type=row.source_type,
            source_id=row.source_id,
            reference=row.reference,
            on=row.on,
            description=row.description,
            channel=row.channel,
            quantity=row.quantity,
            revenue=row.revenue,
            cost=row.cost,
            commission=row.commission,
        )
        for row in service.records(actor, dimension=dimension, key=key, start=start, end=end)
    ]


@router.get("/analytics/erosion", response_model=ErosionReply, summary="Margin erosion")
def erosion(
    start: date, end: date, actor: ActorDependency, service: ProfitabilityDependency
) -> ErosionReply:
    made = service.erosion(actor, start=start, end=end)
    return ErosionReply(
        rows=[
            ErosionRowReply(
                order_id=row.order_id,
                order_number=row.order_number,
                order_line_id=row.order_line_id,
                customer=row.customer,
                description=row.description,
                supplier=row.supplier,
                quantity_quoted=row.quantity_quoted,
                quantity_invoiced=row.quantity_invoiced,
                quoted_margin=row.quoted_margin,
                realised_margin=row.realised_margin,
                difference=row.difference,
                quoted_cost=row.quoted_cost,
                actual_cost=row.actual_cost,
                causes={cause.value: amount for cause, amount in row.causes.items()},
                complete=row.complete,
            )
            for row in made.rows
        ],
        totals={cause.value: amount for cause, amount in made.totals.items()},
        cause_labels={cause.value: cause.label for cause in ErosionCause},
        quoted_margin=made.quoted_margin,
        realised_margin=made.realised_margin,
        pending=made.pending,
        bases=made.bases,
    )


# -- replies -------------------------------------------------------------------------------


def _code(row) -> AnalysisCodeReply:  # type: ignore[no-untyped-def]
    return AnalysisCodeReply(
        id=row.id,
        kind=row.kind,
        kind_label=AnalysisKind(row.kind).label,
        code=row.code,
        name=row.name,
        is_active=row.is_active,
    )


def _period(row) -> PeriodReply:  # type: ignore[no-untyped-def]
    return PeriodReply(key=row.key, label=row.label, starts_on=row.starts_on, ends_on=row.ends_on)


def _cell(cell) -> CellReply:  # type: ignore[no-untyped-def]
    return CellReply(
        period_key=cell.period_key,
        original=cell.original,
        budget=cell.budget,
        actual=cell.actual,
        prior=cell.prior,
        variance=VarianceReply(
            absolute=cell.variance.absolute,
            percent=cell.variance.percent,
            favourable=cell.variance.favourable,
            utilisation=cell.variance.utilisation,
        ),
    )


def _summary(service: BudgetService, names: Names, row) -> BudgetSummaryReply:  # type: ignore[no-untyped-def]
    return BudgetSummaryReply(**_summary_fields(service, names, row))


def _summary_fields(service: BudgetService, names: Names, row) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "id": row.id,
        "name": row.name,
        "period_kind": row.period_kind,
        "starts_on": row.starts_on,
        "ends_on": row.ends_on,
        "state": row.state,
        "state_label": BudgetState(row.state).label,
        "version": row.version,
        "total": service.total(row),
        "lines": len(row.lines),
        "owner": names.user(row.owner_id),
        "approved_by": names.user(row.approved_by_id),
    }


def _detail(service: BudgetService, names: Names, actor: AnyActor, row) -> BudgetDetailReply:  # type: ignore[no-untyped-def]
    versions = service.versions(actor, row.id)
    by_id = {version.id: version.version for version in versions}
    return BudgetDetailReply(
        **_summary_fields(service, names, row),
        branch_id=row.branch_fk,
        owner_id=row.owner_id,
        assumptions=row.assumptions,
        periods=[_period(period) for period in service.periods(row)],
        budget_lines=[
            BudgetLineReply(
                id=line.id,
                position=line.position,
                account_code=line.account_code,
                group_code=line.group_code,
                label=service.label_of(row.business_id, line),
                branch_id=line.branch_fk,
                analysis_code_id=line.analysis_code_fk,
                analysis=service.analysis_label(line.analysis_code_fk),
                amounts={key: Decimal(value) for key, value in line.amounts.items()},
                total=line.total,
                note=line.note,
            )
            for line in row.lines
        ],
        decisions=[
            DecisionReply(
                action=item.action,
                actor=names.user(item.actor_id),
                note=item.note,
                occurred_at=item.occurred_at,
                version=by_id.get(item.budget_fk, row.version),
            )
            for item in service.decisions(actor, row.id)
        ],
        versions=[_summary(service, names, version) for version in versions],
        requests=[_request(names, item) for item in service.requests(actor, row.id)],
    )


def _request(names: Names, row) -> BudgetRequestReply:  # type: ignore[no-untyped-def]
    return BudgetRequestReply(
        id=row.id,
        budget_id=row.budget_fk,
        kind=row.kind,
        line_id=row.line_fk,
        from_line_id=row.from_line_fk,
        period_key=row.period_key,
        amount=row.amount,
        reason=row.reason,
        state=row.state,
        expense_id=row.expense_fk,
        requested_by=names.user(row.requested_by_id),
        decided_by=names.user(row.decided_by_id),
        decision_note=row.decision_note,
    )


def _assumptions(body: AssumptionsContract) -> Assumptions:
    return Assumptions(
        collection_delay_days=body.collection_delay_days,
        collection_rate=body.collection_rate,
        payment_delay_days=body.payment_delay_days,
        include_recurring=body.include_recurring,
        include_overdue_payables=body.include_overdue_payables,
        extra_flows=tuple(
            PlannedFlow(on=flow.on, amount=flow.amount, inward=flow.inward, label=flow.label)
            for flow in body.extra_flows
        ),
        settle_loan_id=str(body.settle_loan_id) if body.settle_loan_id else None,
        settle_on=body.settle_on,
    )


def _scenario(row) -> ScenarioReply:  # type: ignore[no-untyped-def]
    return ScenarioReply(
        id=row.id,
        name=row.name,
        kind=row.kind,
        kind_label=ScenarioKind(row.kind).label,
        is_default=row.is_default,
        assumptions=row.assumptions,
        notes=row.notes,
    )


def _planned(row) -> PlannedReply:  # type: ignore[no-untyped-def]
    return PlannedReply(
        id=row.id,
        expected_on=row.expected_on,
        amount=row.amount,
        inward=row.inward,
        label=row.label,
        scenario_id=row.scenario_fk,
    )


def _forecast(made: Forecast) -> ForecastReply:
    return ForecastReply(
        basis=made.basis.value,
        basis_label=made.basis.label,
        scenario=made.scenario,
        scenario_kind=made.scenario_kind,
        assumptions=made.assumptions,
        starts_on=made.starts_on,
        ends_on=made.ends_on,
        opening_cash=made.opening_cash,
        closing_cash=made.closing_cash,
        lowest=made.lowest,
        lowest_on=made.lowest_on,
        threshold=made.threshold,
        bucket_days=made.bucket_days,
        rows=[
            ForecastRowReply(
                starts_on=row.starts_on,
                ends_on=row.ends_on,
                opening=row.opening,
                inflow=row.inflow,
                outflow=row.outflow,
                closing=row.closing,
                by_class=row.by_class,
                below_threshold=row.below_threshold,
            )
            for row in made.rows
        ],
        totals=made.totals,
        class_labels={cls.value: cls.label for cls in ForecastClass},
        items=[
            FlowItemReply(
                on=item.on,
                cls=item.cls.value,
                cls_label=item.cls.label,
                inward=item.cls.inward,
                amount=item.amount,
                reference=item.reference,
                counterparty=item.counterparty,
                source_type=item.source_type,
                source_id=item.source_id,
            )
            for item in made.items
        ],
        warnings=made.warnings,
    )


def _profit(row) -> ProfitRowReply:  # type: ignore[no-untyped-def]
    return ProfitRowReply(
        key=row.key,
        label=row.label,
        lines=row.lines,
        revenue=row.revenue,
        cost=row.cost,
        gross=row.gross,
        commission=row.commission,
        after_commission=row.after_commission,
        direct=row.direct,
        overheads=row.overheads,
        after_allocation=row.after_allocation,
        margin=row.margin,
        pending_lines=row.pending_lines,
        pending_revenue=row.pending_revenue,
    )
