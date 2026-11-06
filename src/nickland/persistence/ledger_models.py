"""Ledger models for Phase 3.

Journal entries and lines are append-only: there is no update or delete path, and a
posted entry is corrected by reversal, per PRD §10.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON as SA_JSON
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nickland.persistence.base import (
    Base,
    BranchScopedMixin,
    BusinessScopedMixin,
    IdentifiedMixin,
    TimestampedMixin,
)
from nickland.persistence.types import PostedAmount, Rate, UtcDateTime, Uuid


class AccountGroup(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A heading on a financial statement, under which accounts are gathered."""

    __tablename__ = "account_groups"

    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    statement: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("account_groups.id", ondelete="RESTRICT")
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class Account(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A ledger account.

    ``kind`` governs the direction a figure moves the balance and which statement the
    account appears on. ``control_subledger`` marks an account whose balance must equal
    the sum of its subledger, per PRD §29.2.
    """

    __tablename__ = "accounts"

    code: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    statement: Mapped[str] = mapped_column(String(20), nullable=False)
    group_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("account_groups.id", ondelete="RESTRICT"), index=True
    )

    cash_flow_category: Mapped[str] = mapped_column(String(12), nullable=False, default="none")
    control_subledger: Mapped[str | None] = mapped_column(String(16))
    """Where set, this account's balance must equal the sum of that subledger."""

    currency_code: Mapped[str | None] = mapped_column(String(3))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """A seeded account the posting rules depend upon. It may be renamed, not removed."""

    permits_manual_posting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """A control account is written by its subledger, not by hand."""

    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    search_text: Mapped[str] = mapped_column(String(300), nullable=False, default="")

    group: Mapped[AccountGroup | None] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("business_id", "code", name=None),
        Index("ix_accounts_search", "business_id", "search_text"),
        Index("ix_accounts_kind", "business_id", "kind", "is_active"),
    )


class AccountingPeriod(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """An accounting period. Posting is refused once it is closed, per PRD §29.2."""

    __tablename__ = "accounting_periods"

    financial_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    """Its position within the financial year, 1 to 12."""
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="open")

    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reopened_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    reopened_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reopen_reason: Mapped[str | None] = mapped_column(Text)
    reopen_approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    is_year_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    year_end_posted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (
        UniqueConstraint("business_id", "financial_year", "sequence", name=None),
        Index("ix_periods_dates", "business_id", "starts_on", "ends_on"),
        CheckConstraint("ends_on >= starts_on", name="period_dates_ordered"),
        CheckConstraint("sequence >= 1 AND sequence <= 12", name="sequence_range"),
    )


class JournalEntry(Base, IdentifiedMixin, BusinessScopedMixin, BranchScopedMixin):
    """A journal entry.

    Append-only. A posted entry is never amended; correction is by a reversing entry
    that references it, per PRD §10 and §29.2.
    """

    __tablename__ = "journal_entries"

    number: Mapped[str | None] = mapped_column(String(40))
    posting_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("accounting_periods.id", ondelete="RESTRICT"), index=True
    )

    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    source_reference: Mapped[str | None] = mapped_column(String(120))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    """A repeated key returns the original entry and creates nothing, per PRD §29.9."""

    narration: Mapped[str] = mapped_column(Text, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    total_debit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    total_credit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    posted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    posted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    reverses_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT"), index=True
    )
    reversed_by_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    reversal_reason: Mapped[str | None] = mapped_column(Text)

    attachment_ids: Mapped[list[str] | None] = mapped_column(SA_JSON)

    lines: Mapped[list[JournalLine]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="JournalLine.display_order",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "idempotency_key", name=None),
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_journal_entries_state", "business_id", "state", "posting_date"),
        Index("ix_journal_entries_source", "business_id", "source", "source_id"),
        CheckConstraint("total_debit >= 0 AND total_credit >= 0", name="totals_non_negative"),
    )


class JournalLine(Base, IdentifiedMixin, BusinessScopedMixin, BranchScopedMixin):
    """One line of a journal entry. Append-only with its entry."""

    __tablename__ = "journal_lines"

    entry_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False
    )
    account_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    debit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))

    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("1"))
    base_debit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    base_credit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))

    narration: Mapped[str | None] = mapped_column(Text)
    party_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    subledger: Mapped[str | None] = mapped_column(String(16))

    posting_date: Mapped[date] = mapped_column(Date, nullable=False)
    """Copied from the entry, so an account's activity reads without a join."""
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    """Copied from the entry, so a balance query need not join either."""

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_journal_lines_account_date", "business_id", "account_fk", "posting_date"),
        Index("ix_journal_lines_party", "business_id", "subledger", "party_fk"),
        Index("ix_journal_lines_posted", "business_id", "state", "posting_date"),
        CheckConstraint("debit >= 0 AND credit >= 0", name="line_amounts_non_negative"),
        CheckConstraint(
            "(debit = 0) <> (credit = 0) OR (debit = 0 AND credit = 0)",
            name="line_on_one_side",
        ),
    )


class JournalApproval(Base, IdentifiedMixin, BusinessScopedMixin):
    """A maker-checker decision on a journal entry, per PRD §29.9."""

    __tablename__ = "journal_approvals"

    entry_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    decided_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    threshold_amount: Mapped[Decimal | None] = mapped_column(PostedAmount)

    __table_args__ = (Index("ix_journal_approvals_entry", "entry_fk", "decided_at"),)


class ApprovalThreshold(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Above what amount a journal requires a second pair of eyes, per PRD §29.9."""

    __tablename__ = "approval_thresholds"

    scope: Mapped[str] = mapped_column(String(24), nullable=False, default="manual_journal")
    amount: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    requires_distinct_checker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("business_id", "scope", name=None),
        CheckConstraint("amount >= 0", name="threshold_non_negative"),
    )


class OpeningBalance(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """An account's balance brought in when the system was adopted.

    Recorded as a posted journal entry like any other, but marked so that the first
    period's movement can be distinguished from what preceded the system.
    """

    __tablename__ = "opening_balances"

    account_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    as_at: Mapped[date] = mapped_column(Date, nullable=False)
    debit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )

    __table_args__ = (UniqueConstraint("business_id", "account_fk", "as_at", name=None),)


class SubledgerBalance(Base, IdentifiedMixin, BusinessScopedMixin):
    """A party's balance on a control account.

    Materialised so that ageing and statements do not aggregate the whole ledger, and
    reconciled to the control account as a period-close gate, per PRD §29.2.
    """

    __tablename__ = "subledger_balances"

    subledger: Mapped[str] = mapped_column(String(16), nullable=False)
    party_fk: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    account_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    debit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("business_id", "subledger", "party_fk", "account_fk", name=None),
        Index("ix_subledger_balances_account", "business_id", "account_fk"),
    )


class PeriodCloseCheck(Base, IdentifiedMixin, BusinessScopedMixin):
    """One reconciliation gate recorded at a period close, per PRD §29.2."""

    __tablename__ = "period_close_checks"

    period_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounting_periods.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    measurements: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON)
    checked_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (Index("ix_period_close_checks_period", "period_fk", "name"),)
