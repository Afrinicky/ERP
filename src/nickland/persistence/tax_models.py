"""Taxation: authorities, rules and their versions, assessments, obligations and filings.

A rule's rate lives in a version with effective dates, not on the rule, because PRD
§19.2 requires a rate change to be prospective. An assessment is a snapshot of what
one rule concluded about one line at the moment it was posted, so a later amendment
cannot change what a posted transaction was charged.
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


class TaxAuthority(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A body the business is registered with and remits to, per PRD §19.1."""

    __tablename__ = "tax_authorities"

    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(String(80))
    registration_identifier: Mapped[str | None] = mapped_column(String(60))
    """The business's own number with this authority, printed on returns."""

    contact_name: Mapped[str | None] = mapped_column(String(120))
    telephone: Mapped[str | None] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(200))
    portal_url: Mapped[str | None] = mapped_column(String(300))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class TaxRule(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A charge the business is subject to, configured rather than coded.

    The rule holds what does not change with the rate: what it is, who it is remitted
    to, how it is treated, where it posts and when it is filed. The rate lives in a
    version.
    """

    __tablename__ = "tax_rules"

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    treatment: Mapped[str] = mapped_column(String(20), nullable=False, default="exclusive")

    authority_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tax_authorities.id", ondelete="RESTRICT"), index=True
    )
    jurisdiction: Mapped[str | None] = mapped_column(String(80))
    currency_code: Mapped[str | None] = mapped_column(String(3))

    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """A mandatory rule applies wherever it matches; an optional one must be chosen."""

    applies_to_sales: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    applies_to_purchases: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    cascade_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    """Lower runs first. A rule computed on the charges before it needs them already
    calculated, which is the whole reason the order is configured and not implied."""

    compounds_on_preceding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Whether this rule's base includes the charges that ran before it."""

    liability_account_code: Mapped[str | None] = mapped_column(String(8))
    recoverable_account_code: Mapped[str | None] = mapped_column(String(8))
    expense_account_code: Mapped[str | None] = mapped_column(String(8))
    """Where a non-recoverable charge lands: a cost of the purchase, per PRD §19.6."""

    filing_frequency: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    due_date_rule: Mapped[str] = mapped_column(
        String(24), nullable=False, default="days_after_period"
    )
    due_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_day_of_month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    withdrawn_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    withdrawal_reason: Mapped[str | None] = mapped_column(Text)

    versions: Mapped[list[TaxRuleVersion]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TaxRuleVersion.effective_from",
    )
    conditions: Mapped[list[TaxRuleCondition]] = relationship(
        back_populates="rule",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TaxRuleCondition.display_order",
    )
    authority: Mapped[TaxAuthority | None] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("business_id", "code", name=None),
        Index("ix_tax_rules_active", "business_id", "is_active", "cascade_order"),
        CheckConstraint("due_days >= 0", name="due_days_non_negative"),
        CheckConstraint("due_day_of_month >= 1 AND due_day_of_month <= 28", name="due_day_range"),
    )


class TaxRuleVersion(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A rate, in force between two dates.

    Changing a rate adds a version and closes the previous one; it never edits the
    one that is already in force, because posted transactions name the version they
    were charged under.
    """

    __tablename__ = "tax_rule_versions"

    rule_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_rules.id", ondelete="CASCADE"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    """``None`` while this is the current version."""

    method: Mapped[str] = mapped_column(String(20), nullable=False, default="percentage")
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    fixed_amount: Mapped[Decimal] = mapped_column(
        PostedAmount, nullable=False, default=Decimal("0")
    )
    threshold: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    threshold_applies_to_excess: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bands: Mapped[list[dict[str, Any]] | None] = mapped_column(SA_JSON)

    note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    rule: Mapped[TaxRule] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("rule_fk", "effective_from", name=None),
        Index("ix_tax_rule_versions_window", "rule_fk", "effective_from", "effective_to"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="version_dates_ordered"
        ),
    )


class TaxRuleCondition(Base, IdentifiedMixin, BusinessScopedMixin):
    """One test a line must satisfy for its rule to apply, per PRD §19.1."""

    __tablename__ = "tax_rule_conditions"

    rule_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_rules.id", ondelete="CASCADE"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    operator: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[str | None] = mapped_column(String(200))
    values: Mapped[list[str] | None] = mapped_column(SA_JSON)

    rule: Mapped[TaxRule] = relationship(back_populates="conditions")


class TaxAssessment(Base, IdentifiedMixin, BusinessScopedMixin, BranchScopedMixin):
    """What one rule concluded about one transaction line, as it stood then.

    A snapshot, and append-only in practice. It names the rule version it used, so a
    later rate change leaves it untouched, and it names the source line, so every
    amount traces back to what caused it, per PRD §19.2 and §24.
    """

    __tablename__ = "tax_assessments"

    rule_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_rules.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tax_rule_versions.id", ondelete="RESTRICT")
    )
    obligation_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tax_obligations.id", ondelete="RESTRICT"), index=True
    )

    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_line_reference: Mapped[str] = mapped_column(String(80), nullable=False)
    """Which line of which document. The trace PRD §24 requires."""

    party_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    assessed_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    rule_code: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    treatment: Mapped[str] = mapped_column(String(20), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)

    base: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    amount: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")

    is_recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_withheld: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settled_amount: Mapped[Decimal] = mapped_column(
        PostedAmount, nullable=False, default=Decimal("0")
    )

    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "source_type",
            "source_id",
            "source_line_reference",
            "rule_fk",
            name=None,
        ),
        Index("ix_tax_assessments_source", "business_id", "source_type", "source_id"),
        Index("ix_tax_assessments_period", "business_id", "rule_fk", "assessed_on"),
    )


class TaxOverride(Base, IdentifiedMixin, BusinessScopedMixin):
    """A reasoned departure from what the engine calculated, per PRD §19.2.

    Kept apart from the assessment it changed, so that the original figure and the
    hand that altered it are both on the record.
    """

    __tablename__ = "tax_overrides"

    assessment_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_assessments.id", ondelete="CASCADE"), nullable=False
    )
    calculated_amount: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    overridden_amount: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(200))
    attachment_ids: Mapped[list[str] | None] = mapped_column(SA_JSON)

    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (Index("ix_tax_overrides_assessment", "assessment_fk", "recorded_at"),)


class TaxObligation(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """What is owed to one authority under one rule for one period, per PRD §19.3."""

    __tablename__ = "tax_obligations"

    rule_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_rules.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    authority_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tax_authorities.id", ondelete="RESTRICT")
    )

    period_starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    period_ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)

    assessed: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    recoverable: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    withheld: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    """Tax a customer deducted at source, which reduces what we remit."""
    adjustments: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    paid: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    carried_forward: Mapped[Decimal] = mapped_column(
        PostedAmount, nullable=False, default=Decimal("0")
    )
    """A credit that exceeds the liability is carried, not refunded, per PRD §19.5."""

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="outstanding")
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    note: Mapped[str | None] = mapped_column(Text)

    rule: Mapped[TaxRule] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("business_id", "rule_fk", "period_starts_on", name=None),
        Index("ix_tax_obligations_state", "business_id", "state", "due_on"),
    )


class TaxPayment(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A payment made to an authority, per PRD §19.5."""

    __tablename__ = "tax_payments"

    authority_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tax_authorities.id", ondelete="RESTRICT"), index=True
    )
    reference: Mapped[str] = mapped_column(String(60), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    allocated: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    """What has been applied to obligations. The remainder sits unapplied."""

    method: Mapped[str | None] = mapped_column(String(32))
    bank_reference: Mapped[str | None] = mapped_column(String(80))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    evidence_reference: Mapped[str | None] = mapped_column(String(200))
    attachment_ids: Mapped[list[str] | None] = mapped_column(SA_JSON)
    note: Mapped[str | None] = mapped_column(Text)

    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    allocations: Mapped[list[TaxPaymentAllocation]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "reference", name=None),
        CheckConstraint("amount > 0", name="payment_positive"),
    )


class TaxPaymentAllocation(Base, IdentifiedMixin, BusinessScopedMixin):
    """Part of a payment applied to one obligation."""

    __tablename__ = "tax_payment_allocations"

    payment_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_payments.id", ondelete="CASCADE"), nullable=False
    )
    obligation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_obligations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    allocated_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    payment: Mapped[TaxPayment] = relationship(back_populates="allocations")

    __table_args__ = (
        UniqueConstraint("payment_fk", "obligation_fk", name=None),
        CheckConstraint("amount > 0", name="allocation_positive"),
    )


class TaxFiling(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A return lodged with an authority for a period, per PRD §19.3."""

    __tablename__ = "tax_filings"

    obligation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tax_obligations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    authority_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tax_authorities.id", ondelete="RESTRICT")
    )

    reference: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="due")
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    prepared_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    prepared_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    submitted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    acknowledgement_reference: Mapped[str | None] = mapped_column(String(120))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    declared_amount: Mapped[Decimal] = mapped_column(
        PostedAmount, nullable=False, default=Decimal("0")
    )
    evidence_reference: Mapped[str | None] = mapped_column(String(200))
    attachment_ids: Mapped[list[str] | None] = mapped_column(SA_JSON)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("business_id", "obligation_fk", name=None),
        Index("ix_tax_filings_state", "business_id", "state", "due_on"),
    )
