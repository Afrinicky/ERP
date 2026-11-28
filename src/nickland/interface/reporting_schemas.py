"""Contracts for reports, saved runs, schedules and the period-close checklist.

A report crosses the boundary in the shape ``domain.reporting.to_record`` gives it: every
figure as an exact decimal string beside the kind of figure it is, so the screen formats
what the exports write.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import Field

from nickland.interface.ledger_schemas import CloseCheckReply, PeriodReply
from nickland.interface.schemas import Contract, Reply


class OptionReply(Reply):
    key: str
    label: str


class CatalogueReply(Reply):
    id: str
    title: str
    group: str
    group_label: str
    summary: str
    sensitive: bool
    available: bool
    exportable: bool
    groupings: list[OptionReply]
    filters: list[str]
    comparisons: list[OptionReply]
    as_at: bool
    ledger: bool


class ParametersContract(Contract):
    granularity: str = Field(default="month", pattern="^(day|week|month|quarter|year|custom)$")
    anchor: date | None = None
    start: date | None = None
    end: date | None = None
    comparison: str = Field(default="none", pattern="^(none|prior|year|budget)$")
    grouping: str | None = Field(default=None, max_length=24)
    filters: dict[str, str] = Field(default_factory=dict)
    basis: str = Field(default="accrual", pattern="^(accrual|cash)$")

    def as_record(self) -> dict[str, Any]:
        return {
            "granularity": self.granularity,
            "anchor": self.anchor.isoformat() if self.anchor else None,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "comparison": self.comparison,
            "grouping": self.grouping,
            "filters": {key: value for key, value in self.filters.items() if value},
            "basis": self.basis,
        }


class RunContract(ParametersContract):
    report_id: str = Field(min_length=1, max_length=40)


class ReportReply(Reply):
    id: str
    title: str
    sensitive: bool
    withheld: bool
    reconciled: bool
    header: dict[str, Any]
    sections: list[dict[str, Any]]


class RunSummaryReply(Reply):
    id: uuid.UUID
    report_id: str
    title: str
    period_label: str
    starts_on: date
    ends_on: date
    generated_at: datetime
    generated_by: str | None
    reconciled: bool
    sensitive: bool
    scheduled: bool
    notes: int


class SavedRunReply(RunSummaryReply):
    report: ReportReply
    section_notes: dict[str, str]
    parameters: dict[str, Any]


class RunNoteContract(Contract):
    section: str = Field(min_length=1, max_length=40)
    text: str | None = Field(default=None, max_length=4000)


class InboxReply(RunSummaryReply):
    delivered_at: datetime
    read_at: datetime | None


class ScheduleContract(Contract):
    name: str = Field(min_length=1, max_length=120)
    report_id: str = Field(min_length=1, max_length=40)
    frequency: str = Field(pattern="^(daily|weekly|monthly)$")
    run_day: int = Field(default=1, ge=1, le=28)
    run_hour: int = Field(default=7, ge=0, le=23)
    recipients: list[uuid.UUID] = Field(min_length=1, max_length=50)
    formats: list[str] = Field(default_factory=lambda: ["pdf"], max_length=3)
    parameters: ParametersContract = Field(default_factory=ParametersContract)
    is_active: bool = True


class ScheduleReply(Reply):
    id: uuid.UUID
    name: str
    report_id: str
    report_title: str
    frequency: str
    run_day: int
    run_hour: int
    recipients: list[uuid.UUID]
    recipient_names: list[str]
    formats: list[str]
    parameters: dict[str, Any]
    is_active: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_error: str | None


class RecipientReply(Reply):
    id: uuid.UUID
    name: str


# -- period close --------------------------------------------------------------------------


class StepReply(Reply):
    key: str
    label: str
    done: bool
    done_by: str | None
    done_at: datetime | None
    note: str | None


class StepContract(Contract):
    done: bool
    note: str | None = Field(default=None, max_length=1000)


class ReopenRequestReply(Reply):
    id: uuid.UUID
    period_id: uuid.UUID
    period_label: str
    reason: str
    state: str
    requested_by: str | None
    requested_by_id: uuid.UUID | None
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    self_approved: bool


class ChecklistReply(Reply):
    period: PeriodReply
    checks: list[CloseCheckReply]
    steps: list[StepReply]
    ready: bool
    closed_by: str | None
    reopened_by: str | None
    reopen_approved_by: str | None
    requests: list[ReopenRequestReply]


class ReopenRequestContract(Contract):
    reason: str = Field(min_length=10, max_length=2000)


class ReopenDecisionContract(Contract):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)
