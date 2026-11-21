"""Saved runs, notes, the report inbox and scheduled reports. PRD §20.6, §29.8.

A saved run is the report exactly as generated: reopening it shows the figures of the day
it was run, not today's. Notes are the one thing that may be added afterwards, and each is
audited.

A schedule runs a report on a cadence, as the system, keeps the run, and delivers it to
each named person's inbox, provided that person may read it: a report disclosing cost or
margin is delivered only to those who may export such reports.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor, SystemActor
from nickland.application.services.management_pack import (
    SECTION_TITLES,
    ManagementPackService,
)
from nickland.application.services.reports import ReportParameters, ReportService
from nickland.domain.errors import NotFoundError, ValidationError
from nickland.domain.reporting import (
    EXPORT_FORMATS,
    SENSITIVE_PERMISSION,
    Frequency,
    Report,
    ReportId,
    definition_of,
    from_record,
    next_run,
    reported_window,
    required_permissions,
    to_record,
    withhold,
)
from nickland.persistence.models import Business, User
from nickland.persistence.reporting_models import ReportDelivery, ReportRun, ReportSchedule
from nickland.platform.clock import Clock, SystemClock

logger = logging.getLogger(__name__)

MAX_NOTE = 4000


def run_any(
    session: DbSession,
    actor: AnyActor,
    report_id: str,
    params: ReportParameters,
    *,
    clock: Clock | None = None,
) -> Report:
    """The management pack or any operational report."""
    if report_id == ReportId.MANAGEMENT_PACK.value:
        return ManagementPackService(session, clock=clock).build(actor, params)
    return ReportService(session, clock=clock).run(actor, report_id, params)


@dataclass(frozen=True, slots=True)
class ScheduleDraft:
    name: str
    report_id: str
    frequency: str
    run_day: int
    run_hour: int
    recipients: list[uuid.UUID]
    formats: list[str]
    parameters: dict[str, Any]
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class InboxItem:
    delivery: ReportDelivery
    run: ReportRun


class ReportArchive:
    __slots__ = ("_audit", "_clock", "_db")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    def save(
        self,
        actor: AnyActor,
        report: Report,
        params: ReportParameters,
        *,
        schedule_id: uuid.UUID | None = None,
    ) -> ReportRun:
        actor.require("report.view")
        run = ReportRun(
            business_id=actor.business_id,
            report_id=report.id,
            title=report.title,
            period_label=report.header.period,
            starts_on=report.header.starts_on,
            ends_on=report.header.ends_on,
            parameters=params.as_record(),
            payload=to_record(report),
            notes={},
            reconciled=report.reconciled,
            sensitive=report.sensitive,
            generated_at=report.header.generated_at,
            generated_by_id=actor.user_id,
            schedule_fk=schedule_id,
        )
        self._db.add(run)
        self._db.flush()
        self._audit.record(
            actor,
            "report_saved",
            entity_type="report_run",
            entity_id=run.id,
            entity_label=f"{run.title}, {run.period_label}",
            after={"report": run.report_id, "reconciled": run.reconciled},
        )
        return run

    def runs(
        self, actor: AnyActor, *, report_id: str | None = None, limit: int = 100
    ) -> list[ReportRun]:
        actor.require("report.view")
        query = select(ReportRun).where(ReportRun.business_id == actor.business_id)
        if report_id:
            query = query.where(ReportRun.report_id == report_id)
        if not actor.has(SENSITIVE_PERMISSION):
            query = query.where(ReportRun.sensitive.is_(False))
        return list(
            self._db.scalars(
                query.order_by(ReportRun.generated_at.desc()).limit(max(1, min(limit, 500)))
            ).all()
        )

    def require(self, actor: AnyActor, run_id: uuid.UUID) -> ReportRun:
        actor.require("report.view")
        run = self._db.get(ReportRun, run_id)
        if run is None or run.business_id != actor.business_id:
            raise NotFoundError("Saved report not found.")
        definition = definition_of(run.report_id)
        ReportService.authorise(actor, definition, export=False)
        return run

    def open(self, actor: AnyActor, run_id: uuid.UUID) -> tuple[ReportRun, Report]:
        run = self.require(actor, run_id)
        report = from_record(run.payload)
        if not actor.has(SENSITIVE_PERMISSION):
            withhold(report)
        if actor.user_id is not None:
            delivery = self._db.scalar(
                select(ReportDelivery).where(
                    ReportDelivery.run_fk == run.id, ReportDelivery.recipient_id == actor.user_id
                )
            )
            if delivery is not None and delivery.read_at is None:
                delivery.read_at = self._clock.now()
        return run, report

    def annotate(
        self, actor: AnyActor, run_id: uuid.UUID, section: str, text: str | None
    ) -> ReportRun:
        """Add, replace or remove the note beneath one section of a saved run."""
        run = self.require(actor, run_id)
        actor.require("report.export")
        sections = {item["key"] for item in run.payload.get("sections", [])}
        if section not in sections:
            raise ValidationError("That section is not in this report.", field="section")
        cleaned = (text or "").strip()
        if len(cleaned) > MAX_NOTE:
            raise ValidationError(f"Keep a note to {MAX_NOTE} characters.", field="text")
        before = dict(run.notes or {})
        notes = dict(before)
        if cleaned:
            notes[section] = cleaned
        else:
            notes.pop(section, None)
        run.notes = notes
        self._audit.record(
            actor,
            "report_note",
            entity_type="report_run",
            entity_id=run.id,
            entity_label=f"{run.title}, {run.period_label}",
            before={"note": before.get(section)},
            after={"section": SECTION_TITLES.get(section, section), "note": cleaned or None},
        )
        return run

    def remove(self, actor: AnyActor, run_id: uuid.UUID) -> None:
        run = self.require(actor, run_id)
        actor.require("report.export")
        self._audit.record(
            actor,
            "report_removed",
            entity_type="report_run",
            entity_id=run.id,
            entity_label=f"{run.title}, {run.period_label}",
        )
        self._db.delete(run)

    def inbox(self, actor: AnyActor, *, limit: int = 50) -> list[InboxItem]:
        actor.require("report.view")
        if actor.user_id is None:
            return []
        rows = self._db.execute(
            select(ReportDelivery, ReportRun)
            .join(ReportRun, ReportRun.id == ReportDelivery.run_fk)
            .where(
                ReportDelivery.business_id == actor.business_id,
                ReportDelivery.recipient_id == actor.user_id,
            )
            .order_by(ReportDelivery.delivered_at.desc())
            .limit(limit)
        ).all()
        return [
            InboxItem(delivery, run)
            for delivery, run in rows
            if not run.sensitive or actor.has(SENSITIVE_PERMISSION)
        ]


class ReportScheduler:
    __slots__ = ("_archive", "_audit", "_clock", "_db")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._archive = ReportArchive(session, clock=self._clock)

    # -- the schedules --------------------------------------------------------------------

    def schedules(self, actor: AnyActor) -> list[ReportSchedule]:
        actor.require("report.view")
        query = select(ReportSchedule).where(ReportSchedule.business_id == actor.business_id)
        return [
            schedule
            for schedule in self._db.scalars(query.order_by(ReportSchedule.name)).all()
            if all(
                actor.has(code)
                for code in required_permissions(definition_of(schedule.report_id), export=False)
            )
        ]

    def require(self, actor: AnyActor, schedule_id: uuid.UUID) -> ReportSchedule:
        actor.require("report.export")
        schedule = self._db.get(ReportSchedule, schedule_id)
        if schedule is None or schedule.business_id != actor.business_id:
            raise NotFoundError("Schedule not found.")
        return schedule

    def create(self, actor: AnyActor, draft: ScheduleDraft) -> ReportSchedule:
        schedule = ReportSchedule(business_id=actor.business_id, created_by_id=actor.user_id)
        self._apply(actor, schedule, draft)
        self._db.add(schedule)
        self._db.flush()
        self._audit.record(
            actor,
            "report_schedule_created",
            entity_type="report_schedule",
            entity_id=schedule.id,
            entity_label=schedule.name,
            after=self._describe(schedule),
        )
        return schedule

    def amend(
        self, actor: AnyActor, schedule_id: uuid.UUID, draft: ScheduleDraft
    ) -> ReportSchedule:
        schedule = self.require(actor, schedule_id)
        before = self._describe(schedule)
        self._apply(actor, schedule, draft)
        self._audit.record(
            actor,
            "report_schedule_amended",
            entity_type="report_schedule",
            entity_id=schedule.id,
            entity_label=schedule.name,
            before=before,
            after=self._describe(schedule),
        )
        return schedule

    def delete(self, actor: AnyActor, schedule_id: uuid.UUID) -> None:
        schedule = self.require(actor, schedule_id)
        self._audit.record(
            actor,
            "report_schedule_deleted",
            entity_type="report_schedule",
            entity_id=schedule.id,
            entity_label=schedule.name,
            before=self._describe(schedule),
        )
        self._db.delete(schedule)

    def _apply(self, actor: AnyActor, schedule: ReportSchedule, draft: ScheduleDraft) -> None:
        actor.require("report.export")
        definition = definition_of(draft.report_id)
        ReportService.authorise(actor, definition, export=True)
        try:
            frequency = Frequency(draft.frequency)
        except ValueError:
            raise ValidationError("Choose daily, weekly or monthly.", field="frequency") from None
        if not draft.name.strip():
            raise ValidationError("Name the schedule.", field="name")
        if not draft.recipients:
            raise ValidationError("Name at least one recipient.", field="recipients")
        formats = [item for item in draft.formats if item]
        if any(item not in EXPORT_FORMATS for item in formats):
            raise ValidationError("Formats are PDF, Excel and CSV.", field="formats")
        recipients = []
        for user_id in dict.fromkeys(draft.recipients):
            user = self._db.get(User, user_id)
            if user is None or user.business_id != actor.business_id or not user.is_active:
                raise ValidationError("A recipient is not an active user.", field="recipients")
            if not self._may_read(user.id, definition.id.value):
                raise ValidationError(
                    f"{user.full_name} may not read {definition.title}.", field="recipients"
                )
            recipients.append(str(user.id))
        params = ReportParameters.from_record(
            {**draft.parameters, "granularity": frequency.granularity.value}
        )
        # Validate the parameters against the report now, not at three in the morning.
        ReportService(self._db, clock=self._clock).scope(actor, definition, params)
        business = self._db.get(Business, actor.business_id)
        assert business is not None
        schedule.name = draft.name.strip()
        schedule.report_id = definition.id.value
        schedule.frequency = frequency.value
        schedule.run_day = draft.run_day
        schedule.run_hour = draft.run_hour
        schedule.recipients = recipients
        schedule.formats = formats or ["pdf"]
        schedule.parameters = params.as_record()
        schedule.is_active = draft.is_active
        schedule.next_run_at = (
            self._next(business, frequency, draft.run_day, draft.run_hour)
            if draft.is_active
            else None
        )
        schedule.last_error = None

    def _next(self, business: Business, frequency: Frequency, day: int, hour: int) -> datetime:
        zone = ZoneInfo(business.timezone)
        local = self._clock.now().astimezone(zone).replace(tzinfo=None)
        due = next_run(frequency, day, hour, local)
        return due.replace(tzinfo=zone).astimezone(UTC)

    @staticmethod
    def _describe(schedule: ReportSchedule) -> dict[str, Any]:
        return {
            "report": schedule.report_id,
            "frequency": schedule.frequency,
            "run_day": schedule.run_day,
            "run_hour": schedule.run_hour,
            "recipients": list(schedule.recipients or []),
            "formats": list(schedule.formats or []),
            "active": schedule.is_active,
        }

    # -- running --------------------------------------------------------------------------

    def run_due(self, business_id: uuid.UUID) -> list[ReportRun]:
        """Every active schedule that has fallen due, run once, as the system."""
        now = self._clock.now()
        due = self._db.scalars(
            select(ReportSchedule).where(
                ReportSchedule.business_id == business_id,
                ReportSchedule.is_active.is_(True),
                ReportSchedule.next_run_at.is_not(None),
                ReportSchedule.next_run_at <= now,
            )
        ).all()
        runs = []
        for schedule in due:
            try:
                runs.append(self._execute(schedule))
            except Exception as error:  # a failing schedule must not stop the others
                logger.exception("Scheduled report %s failed", schedule.id)
                schedule.last_error = str(error)[:500]
                business = self._db.get(Business, business_id)
                assert business is not None
                schedule.next_run_at = self._next(
                    business, Frequency(schedule.frequency), schedule.run_day, schedule.run_hour
                )
        return runs

    def run_now(self, actor: AnyActor, schedule_id: uuid.UUID) -> ReportRun:
        schedule = self.require(actor, schedule_id)
        return self._execute(schedule, advance=False)

    def _execute(self, schedule: ReportSchedule, *, advance: bool = True) -> ReportRun:
        business = self._db.get(Business, schedule.business_id)
        assert business is not None
        system = SystemActor(business_id=business.id, reason=f"Scheduled report: {schedule.name}")
        frequency = Frequency(schedule.frequency)
        local_today = self._clock.now().astimezone(ZoneInfo(business.timezone)).date()
        record = dict(schedule.parameters or {})
        record.update(
            granularity=frequency.granularity.value,
            anchor=reported_window(frequency, local_today).isoformat(),
            start=None,
            end=None,
        )
        params = ReportParameters.from_record(record)
        report = run_any(self._db, system, schedule.report_id, params, clock=self._clock)
        report.header.generated_by = f"Schedule: {schedule.name}"
        run = self._archive.save(system, report, params, schedule_id=schedule.id)
        now = self._clock.now()
        for recipient in schedule.recipients or []:
            user_id = uuid.UUID(recipient)
            if not self._may_read(user_id, schedule.report_id):
                continue
            self._db.add(
                ReportDelivery(
                    business_id=business.id,
                    run_fk=run.id,
                    recipient_id=user_id,
                    delivered_at=now,
                )
            )
        schedule.last_run_at = now
        schedule.last_error = None
        if advance:
            schedule.next_run_at = self._next(
                business, frequency, schedule.run_day, schedule.run_hour
            )
        return run

    def _may_read(self, user_id: uuid.UUID, report_id: str) -> bool:
        user = self._db.get(User, user_id)
        if user is None or not user.is_active:
            return False
        held = self.permissions_of(user_id)
        return all(
            code in held for code in required_permissions(definition_of(report_id), export=False)
        )

    def permissions_of(self, user_id: uuid.UUID) -> frozenset[str]:
        from nickland.application.services.identity import IdentityService

        user = self._db.get(User, user_id)
        if user is None:
            return frozenset()
        return IdentityService(self._db, clock=self._clock).actor_for(user).permissions


def run_schedules(session: DbSession, *, clock: Clock | None = None) -> int:
    """One pass over every business: the background runner's unit of work."""
    count = 0
    for business_id in session.scalars(select(Business.id)).all():
        count += len(ReportScheduler(session, clock=clock).run_due(business_id))
    return count


def unread(session: DbSession, actor: AnyActor) -> int:
    if actor.user_id is None:
        return 0
    return len(
        [item for item in ReportArchive(session).inbox(actor) if item.delivery.read_at is None]
    )


__all__ = [
    "InboxItem",
    "ReportArchive",
    "ReportScheduler",
    "ScheduleDraft",
    "run_any",
    "run_schedules",
    "unread",
]
