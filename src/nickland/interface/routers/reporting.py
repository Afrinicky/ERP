"""Reports, the management pack, exports, saved runs, the inbox and schedules; and the
period-close checklist with its reopening requests."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Query, Response

from nickland.application.context import AnyActor
from nickland.application.services.periods import Checklist
from nickland.application.services.report_runs import (
    ReportArchive,
    ReportScheduler,
    ScheduleDraft,
    run_any,
)
from nickland.application.services.reports import ReportParameters, ReportService
from nickland.documents.report_export import export
from nickland.domain.errors import ValidationError
from nickland.domain.reporting import (
    EXPORT_FORMATS,
    Report,
    definition_of,
    required_permissions,
    to_record,
)
from nickland.interface.dependencies import (
    ActorDependency,
    PeriodDependency,
    RuntimeDependency,
    SessionDependency,
)
from nickland.interface.finance_replies import Names
from nickland.interface.ledger_schemas import CloseCheckReply, PeriodReply
from nickland.interface.reporting_schemas import (
    CatalogueReply,
    ChecklistReply,
    InboxReply,
    OptionReply,
    ParametersContract,
    RecipientReply,
    ReopenDecisionContract,
    ReopenRequestContract,
    ReopenRequestReply,
    ReportReply,
    RunContract,
    RunNoteContract,
    RunSummaryReply,
    SavedRunReply,
    ScheduleContract,
    ScheduleReply,
    StepContract,
    StepReply,
)
from nickland.persistence.ledger_models import AccountingPeriod
from nickland.persistence.models import User
from nickland.persistence.reporting_models import (
    PeriodReopenRequest,
    ReportRun,
    ReportSchedule,
)

router = APIRouter(tags=["reporting"])

FILE: dict[int | str, dict[str, Any]] = {200: {"content": {"application/pdf": {}, "text/csv": {}}}}


def _report(report: Report) -> ReportReply:
    return ReportReply.model_validate(to_record(report))


def _summary(run: ReportRun, names: Names) -> dict[str, Any]:
    return {
        "id": run.id,
        "report_id": run.report_id,
        "title": run.title,
        "period_label": run.period_label,
        "starts_on": run.starts_on,
        "ends_on": run.ends_on,
        "generated_at": run.generated_at,
        "generated_by": run.payload.get("header", {}).get("generated_by")
        or names.user(run.generated_by_id),
        "reconciled": run.reconciled,
        "sensitive": run.sensitive,
        "scheduled": run.schedule_fk is not None,
        "notes": len(run.notes or {}),
    }


def _parameters(contract: ParametersContract) -> ReportParameters:
    return ReportParameters.from_record(contract.as_record())


def _file(report: Report, fmt: str, notes: dict[str, str] | None = None) -> Response:
    if fmt not in EXPORT_FORMATS:
        raise ValidationError("Export as PDF, Excel or CSV.", field="format")
    content, media_type, name = export(report, fmt, notes)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# -- running ---------------------------------------------------------------------------


@router.get("/reports/catalogue", response_model=list[CatalogueReply], summary="Reports offered")
def catalogue(
    actor: ActorDependency, session: SessionDependency, runtime: RuntimeDependency
) -> list[CatalogueReply]:
    from nickland.domain.reporting import MANAGEMENT_PACK

    entries = ReportService(session, clock=runtime.clock).catalogue(actor)
    pack_available = all(
        actor.has(code) for code in required_permissions(MANAGEMENT_PACK, export=False)
    )
    pack_exportable = all(
        actor.has(code) for code in required_permissions(MANAGEMENT_PACK, export=True)
    )
    rows = [(MANAGEMENT_PACK, pack_available, pack_exportable)] + [
        (entry.definition, entry.available, entry.exportable) for entry in entries
    ]
    return [
        CatalogueReply(
            id=definition.id.value,
            title=definition.title,
            group=definition.group.value,
            group_label=definition.group.label,
            summary=definition.summary,
            sensitive=definition.sensitive,
            available=available,
            exportable=exportable,
            groupings=[OptionReply(key=key, label=label) for key, label in definition.groupings],
            filters=list(definition.filters),
            comparisons=[
                OptionReply(key=item.value, label=item.label) for item in definition.comparisons
            ],
            as_at=definition.as_at,
            ledger=definition.ledger,
        )
        for definition, available, exportable in rows
    ]


@router.post("/reports/run", response_model=ReportReply, summary="Run a report")
def run_report(
    contract: RunContract,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> ReportReply:
    report = run_any(session, actor, contract.report_id, _parameters(contract), clock=runtime.clock)
    return _report(report)


@router.get(
    "/reports/export",
    summary="Export a report to PDF, Excel or CSV",
    response_class=Response,
    responses=FILE,
)
def export_report(
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
    report_id: str = Query(min_length=1, max_length=40),
    format: str = Query(default="pdf", pattern="^(pdf|xlsx|csv)$"),  # noqa: A002
    granularity: str = Query(default="month"),
    anchor: str | None = None,
    start: str | None = None,
    end: str | None = None,
    comparison: str = Query(default="none"),
    grouping: str | None = None,
    filters: str | None = Query(default=None, max_length=2000),
    basis: str = Query(default="accrual"),
) -> Response:
    ReportService.authorise(actor, definition_of(report_id), export=True)
    try:
        chosen = json.loads(filters) if filters else {}
    except ValueError:
        raise ValidationError("The filters could not be read.", field="filters") from None
    contract = ParametersContract.model_validate(
        {
            "granularity": granularity,
            "anchor": anchor,
            "start": start,
            "end": end,
            "comparison": comparison,
            "grouping": grouping,
            "filters": chosen,
            "basis": basis,
        }
    )
    report = run_any(session, actor, report_id, _parameters(contract), clock=runtime.clock)
    return _file(report, format)


# -- saved runs ------------------------------------------------------------------------


@router.post(
    "/reports/runs",
    response_model=RunSummaryReply,
    status_code=201,
    summary="Run a report and keep it",
)
def save_run(
    contract: RunContract,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> RunSummaryReply:
    params = _parameters(contract)
    report = run_any(session, actor, contract.report_id, params, clock=runtime.clock)
    run = ReportArchive(session, clock=runtime.clock).save(actor, report, params)
    return RunSummaryReply.model_validate(_summary(run, Names(session)))


@router.get("/reports/runs", response_model=list[RunSummaryReply], summary="Saved reports")
def list_runs(
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
    report_id: str | None = None,
) -> list[RunSummaryReply]:
    names = Names(session)
    return [
        RunSummaryReply.model_validate(_summary(run, names))
        for run in ReportArchive(session, clock=runtime.clock).runs(actor, report_id=report_id)
    ]


def _saved(run: ReportRun, report: Report, names: Names) -> SavedRunReply:
    return SavedRunReply.model_validate(
        {
            **_summary(run, names),
            "report": to_record(report),
            "section_notes": dict(run.notes or {}),
            "parameters": dict(run.parameters or {}),
        }
    )


@router.get("/reports/runs/{run_id}", response_model=SavedRunReply, summary="A saved report")
def open_run(
    run_id: uuid.UUID,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> SavedRunReply:
    run, report = ReportArchive(session, clock=runtime.clock).open(actor, run_id)
    return _saved(run, report, Names(session))


@router.put(
    "/reports/runs/{run_id}/notes",
    response_model=SavedRunReply,
    summary="Write the note beneath a section",
)
def annotate_run(
    run_id: uuid.UUID,
    contract: RunNoteContract,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> SavedRunReply:
    archive = ReportArchive(session, clock=runtime.clock)
    archive.annotate(actor, run_id, contract.section, contract.text)
    run, report = archive.open(actor, run_id)
    return _saved(run, report, Names(session))


@router.delete("/reports/runs/{run_id}", status_code=204, summary="Remove a saved report")
def remove_run(
    run_id: uuid.UUID,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> Response:
    ReportArchive(session, clock=runtime.clock).remove(actor, run_id)
    return Response(status_code=204)


@router.get(
    "/reports/runs/{run_id}/export",
    summary="Export a saved report",
    response_class=Response,
    responses=FILE,
)
def export_run(
    run_id: uuid.UUID,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
    format: str = Query(default="pdf", pattern="^(pdf|xlsx|csv)$"),  # noqa: A002
) -> Response:
    archive = ReportArchive(session, clock=runtime.clock)
    run = archive.require(actor, run_id)
    ReportService.authorise(actor, definition_of(run.report_id), export=True)
    run, report = archive.open(actor, run_id)
    return _file(report, format, dict(run.notes or {}))


@router.get("/reports/inbox", response_model=list[InboxReply], summary="Reports delivered to me")
def inbox(
    actor: ActorDependency, session: SessionDependency, runtime: RuntimeDependency
) -> list[InboxReply]:
    names = Names(session)
    return [
        InboxReply.model_validate(
            {
                **_summary(item.run, names),
                "delivered_at": item.delivery.delivered_at,
                "read_at": item.delivery.read_at,
            }
        )
        for item in ReportArchive(session, clock=runtime.clock).inbox(actor)
    ]


# -- schedules -------------------------------------------------------------------------


def _schedule(schedule: ReportSchedule, names: Names) -> ScheduleReply:
    recipients = [uuid.UUID(value) for value in schedule.recipients or []]
    return ScheduleReply(
        id=schedule.id,
        name=schedule.name,
        report_id=schedule.report_id,
        report_title=definition_of(schedule.report_id).title,
        frequency=schedule.frequency,
        run_day=schedule.run_day,
        run_hour=schedule.run_hour,
        recipients=recipients,
        recipient_names=[names.user(user) or "Former user" for user in recipients],
        formats=list(schedule.formats or []),
        parameters=dict(schedule.parameters or {}),
        is_active=schedule.is_active,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_error=schedule.last_error,
    )


def _draft(contract: ScheduleContract) -> ScheduleDraft:
    return ScheduleDraft(
        name=contract.name,
        report_id=contract.report_id,
        frequency=contract.frequency,
        run_day=contract.run_day,
        run_hour=contract.run_hour,
        recipients=list(contract.recipients),
        formats=list(contract.formats),
        parameters=contract.parameters.as_record(),
        is_active=contract.is_active,
    )


@router.get("/reports/schedules", response_model=list[ScheduleReply], summary="Report schedules")
def list_schedules(
    actor: ActorDependency, session: SessionDependency, runtime: RuntimeDependency
) -> list[ScheduleReply]:
    names = Names(session)
    return [
        _schedule(item, names)
        for item in ReportScheduler(session, clock=runtime.clock).schedules(actor)
    ]


@router.post(
    "/reports/schedules", response_model=ScheduleReply, status_code=201, summary="Schedule a report"
)
def create_schedule(
    contract: ScheduleContract,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> ScheduleReply:
    schedule = ReportScheduler(session, clock=runtime.clock).create(actor, _draft(contract))
    return _schedule(schedule, Names(session))


@router.put(
    "/reports/schedules/{schedule_id}", response_model=ScheduleReply, summary="Amend a schedule"
)
def amend_schedule(
    schedule_id: uuid.UUID,
    contract: ScheduleContract,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> ScheduleReply:
    schedule = ReportScheduler(session, clock=runtime.clock).amend(
        actor, schedule_id, _draft(contract)
    )
    return _schedule(schedule, Names(session))


@router.delete("/reports/schedules/{schedule_id}", status_code=204, summary="Delete a schedule")
def delete_schedule(
    schedule_id: uuid.UUID,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> Response:
    ReportScheduler(session, clock=runtime.clock).delete(actor, schedule_id)
    return Response(status_code=204)


@router.post(
    "/reports/schedules/{schedule_id}/run",
    response_model=RunSummaryReply,
    summary="Run a schedule now",
)
def run_schedule(
    schedule_id: uuid.UUID,
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
) -> RunSummaryReply:
    run = ReportScheduler(session, clock=runtime.clock).run_now(actor, schedule_id)
    return RunSummaryReply.model_validate(_summary(run, Names(session)))


@router.get(
    "/reports/recipients",
    response_model=list[RecipientReply],
    summary="People who may receive a report",
)
def recipients(
    actor: ActorDependency,
    session: SessionDependency,
    runtime: RuntimeDependency,
    report_id: str = Query(min_length=1, max_length=40),
) -> list[RecipientReply]:
    from sqlalchemy import select

    actor.require("report.export")
    scheduler = ReportScheduler(session, clock=runtime.clock)
    needed = required_permissions(definition_of(report_id), export=False)
    users = session.scalars(
        select(User)
        .where(User.business_id == actor.business_id, User.is_active.is_(True))
        .order_by(User.full_name)
    ).all()
    return [
        RecipientReply(id=user.id, name=user.full_name)
        for user in users
        if all(code in scheduler.permissions_of(user.id) for code in needed)
    ]


# -- the period close ------------------------------------------------------------------


def _request(
    item: PeriodReopenRequest, names: Names, labels: dict[uuid.UUID, str]
) -> ReopenRequestReply:
    return ReopenRequestReply(
        id=item.id,
        period_id=item.period_fk,
        period_label=labels.get(item.period_fk, ""),
        reason=item.reason,
        state=item.state,
        requested_by=names.user(item.requested_by_id),
        requested_by_id=item.requested_by_id,
        requested_at=item.requested_at,
        decided_by=names.user(item.decided_by_id),
        decided_at=item.decided_at,
        decision_note=item.decision_note,
        self_approved=item.self_approved,
    )


def _labels(session: SessionDependency, actor: AnyActor) -> dict[uuid.UUID, str]:
    from sqlalchemy import select

    return {
        period.id: period.label
        for period in session.scalars(
            select(AccountingPeriod).where(AccountingPeriod.business_id == actor.business_id)
        ).all()
    }


def _checklist(
    checklist: Checklist, actor: AnyActor, periods: PeriodDependency, session: SessionDependency
) -> ChecklistReply:
    names = Names(session)
    period = checklist.period
    labels = _labels(session, actor)
    return ChecklistReply(
        period=PeriodReply.model_validate(period),
        checks=[CloseCheckReply.model_validate(check) for check in checklist.checks],
        steps=[
            StepReply(
                key=step.key,
                label=step.label,
                done=step.done,
                done_by=names.user(step.done_by_id),
                done_at=step.done_at,
                note=step.note,
            )
            for step in checklist.steps
        ],
        ready=checklist.ready,
        closed_by=names.user(period.closed_by_id),
        reopened_by=names.user(period.reopened_by_id),
        reopen_approved_by=names.user(period.reopen_approved_by_id),
        requests=[
            _request(item, names, labels)
            for item in periods.reopen_requests(actor, period_id=period.id)
        ],
    )


@router.get(
    "/ledger/periods/{period_id}/checklist",
    response_model=ChecklistReply,
    summary="The close checklist of a period",
)
def period_checklist(
    period_id: uuid.UUID,
    actor: ActorDependency,
    periods: PeriodDependency,
    session: SessionDependency,
) -> ChecklistReply:
    return _checklist(periods.checklist(actor, period_id), actor, periods, session)


@router.put(
    "/ledger/periods/{period_id}/checklist/{key}",
    response_model=ChecklistReply,
    summary="Confirm or withdraw a step of the close",
)
def confirm_step(
    period_id: uuid.UUID,
    key: str,
    contract: StepContract,
    actor: ActorDependency,
    periods: PeriodDependency,
    session: SessionDependency,
) -> ChecklistReply:
    checklist = periods.confirm_step(actor, period_id, key, done=contract.done, note=contract.note)
    return _checklist(checklist, actor, periods, session)


@router.post(
    "/ledger/periods/{period_id}/reopen-requests",
    response_model=ReopenRequestReply,
    status_code=201,
    summary="Ask for a closed period to be reopened",
)
def request_reopen(
    period_id: uuid.UUID,
    contract: ReopenRequestContract,
    actor: ActorDependency,
    periods: PeriodDependency,
    session: SessionDependency,
) -> ReopenRequestReply:
    item = periods.request_reopen(actor, period_id, contract.reason)
    return _request(item, Names(session), _labels(session, actor))


@router.get(
    "/ledger/reopen-requests",
    response_model=list[ReopenRequestReply],
    summary="Requests to reopen closed periods",
)
def list_reopen_requests(
    actor: ActorDependency, periods: PeriodDependency, session: SessionDependency
) -> list[ReopenRequestReply]:
    names = Names(session)
    labels = _labels(session, actor)
    return [_request(item, names, labels) for item in periods.reopen_requests(actor)]


@router.post(
    "/ledger/reopen-requests/{request_id}/decision",
    response_model=ReopenRequestReply,
    summary="Approve or decline a reopening",
)
def decide_reopen(
    request_id: uuid.UUID,
    contract: ReopenDecisionContract,
    actor: ActorDependency,
    periods: PeriodDependency,
    session: SessionDependency,
) -> ReopenRequestReply:
    item = periods.decide_reopen(actor, request_id, approve=contract.approve, note=contract.note)
    return _request(item, Names(session), _labels(session, actor))
