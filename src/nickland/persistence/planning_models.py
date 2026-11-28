"""Budgets, analysis codes and cash-forecast scenarios. PRD §29.6.

A budget is a plan, never a posting: nothing here reaches the ledger. Actuals are always
read from posted journal lines at the moment they are compared.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON as SA_JSON
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nickland.persistence.base import (
    Base,
    BusinessScopedMixin,
    IdentifiedMixin,
    TimestampedMixin,
)
from nickland.persistence.types import Money, UtcDateTime, Uuid


class AnalysisCode(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A department, project or cost centre that postings and budgets can be tagged with."""

    __tablename__ = "analysis_codes"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class Budget(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One version of a budget. A revision is a new row, so an approved version is kept as
    it was approved."""

    __tablename__ = "budgets"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    period_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    branch_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    assumptions: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lineage_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    """The first version's id, shared by every version of the same budget."""
    supersedes_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    lines: Mapped[list[BudgetLine]] = relationship(
        back_populates="budget", order_by="BudgetLine.position", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("ends_on >= starts_on", name="window_ordered"),)


class BudgetLine(Base, IdentifiedMixin, BusinessScopedMixin):
    """What is planned for one account or account group, optionally for one branch or
    analysis code, column by column."""

    __tablename__ = "budget_lines"

    budget_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    account_code: Mapped[str | None] = mapped_column(String(8))
    group_code: Mapped[str | None] = mapped_column(String(8))
    """A whole category of accounts, where the plan is not drawn account by account."""
    branch_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    analysis_code_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("analysis_codes.id", ondelete="RESTRICT")
    )
    amounts: Mapped[dict[str, str]] = mapped_column(SA_JSON, nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    note: Mapped[str | None] = mapped_column(Text)

    budget: Mapped[Budget] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint("(account_code IS NULL) <> (group_code IS NULL)", name="one_target"),
    )


class BudgetDecision(Base, IdentifiedMixin, BusinessScopedMixin):
    """The approval history: each submission, approval, rejection and revision."""

    __tablename__ = "budget_decisions"

    budget_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    note: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class BudgetRequest(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A transfer between two lines, or leave to spend beyond one, awaiting a decision."""

    __tablename__ = "budget_requests"

    budget_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    """``transfer`` or ``overspend``."""
    line_fk: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    from_line_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    period_key: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    expense_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)


class ForecastScenario(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A named set of assumptions a cash forecast is run under."""

    __tablename__ = "forecast_scenarios"

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    assumptions: Mapped[dict[str, object]] = mapped_column(SA_JSON, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


class PlannedCashItem(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Money expected that no record yet holds: a planned purchase, a capital injection."""

    __tablename__ = "planned_cash_items"

    expected_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    inward: Mapped[bool] = mapped_column(Boolean, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    scenario_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    """Only in this scenario; in every scenario when empty."""
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)
