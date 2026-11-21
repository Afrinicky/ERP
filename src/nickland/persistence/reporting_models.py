"""Saved report runs, schedules and their deliveries; the period-close checklist and
requests to reopen a closed period. PRD §20.6, §29.8, §29.9, AC-22.

A saved run keeps the figures exactly as they were generated. Its notes may be added to
afterwards; its figures never change.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON as SA_JSON
from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from nickland.persistence.base import (
    Base,
    BusinessScopedMixin,
    IdentifiedMixin,
    TimestampedMixin,
)
from nickland.persistence.types import UtcDateTime, Uuid


class ReportSchedule(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A report generated on a cadence and delivered to the people named."""

    __tablename__ = "report_schedules"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    report_id: Mapped[str] = mapped_column(String(40), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(SA_JSON, nullable=False, default=dict)
    frequency: Mapped[str] = mapped_column(String(12), nullable=False)
    """``daily``, ``weekly`` or ``monthly``. A monthly schedule reports the month just
    ended; a weekly one the week just ended; a daily one the day before."""
    run_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    """The weekday (1 Monday to 7 Sunday) or the day of the month it runs on."""
    run_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    recipients: Mapped[list[str]] = mapped_column(SA_JSON, nullable=False, default=list)
    formats: Mapped[list[str]] = mapped_column(SA_JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ReportRun(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A report as it was generated, kept."""

    __tablename__ = "report_runs"

    report_id: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    period_label: Mapped[str] = mapped_column(String(80), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(SA_JSON, nullable=False, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(SA_JSON, nullable=False)
    notes: Mapped[dict[str, str]] = mapped_column(SA_JSON, nullable=False, default=dict)
    reconciled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    generated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    schedule_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("report_schedules.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_report_runs_listing", "business_id", "report_id", "generated_at"),)


class ReportDelivery(Base, IdentifiedMixin, BusinessScopedMixin):
    """A run delivered to one person's report inbox."""

    __tablename__ = "report_deliveries"

    run_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("report_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    delivered_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (UniqueConstraint("run_fk", "recipient_id", name=None),)


class PeriodChecklistItem(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A step of the close that a person confirms, with who confirmed it and when."""

    __tablename__ = "period_checklist_items"

    period_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounting_periods.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    done_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    done_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("period_fk", "key", name=None),)


class PeriodReopenRequest(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A request to reopen a closed period, decided by a second person."""

    __tablename__ = "period_reopen_requests"

    period_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounting_periods.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    self_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """The administrator approved a request of their own: permitted, and recorded."""


__all__ = [
    "PeriodChecklistItem",
    "PeriodReopenRequest",
    "ReportDelivery",
    "ReportRun",
    "ReportSchedule",
]
