"""Debt, loans and financing records. PRD §25.

The ledger holds the balances; these tables hold the agreement behind them: the terms as
signed, every version of the schedule, each drawdown and repayment, and how each repayment
was split between penalties, fees, interest and principal.
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
from nickland.persistence.types import Money, Rate, UtcDateTime, Uuid


class LoanAgreement(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Money borrowed by the business, or lent or advanced by it, under a formal agreement."""

    __tablename__ = "loan_agreements"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    loan_type: Mapped[str] = mapped_column(String(20), nullable=False)
    counterparty_name: Mapped[str] = mapped_column(String(200), nullable=False)
    counterparty_party_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT")
    )
    counterparty_employee_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    """For a staff advance: the employee who owes it."""
    agreement_reference: Mapped[str | None] = mapped_column(String(80))
    """The agreement number as the counterparty writes it."""
    purpose: Mapped[str | None] = mapped_column(Text)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    principal: Mapped[Decimal] = mapped_column(Money, nullable=False)
    agreement_date: Mapped[date] = mapped_column(Date, nullable=False)
    disbursement_date: Mapped[date | None] = mapped_column(Date)
    maturity_date: Mapped[date | None] = mapped_column(Date)

    model: Mapped[str] = mapped_column(String(20), nullable=False)
    annual_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    """Percent a year, as written in the agreement."""
    frequency: Mapped[str] = mapped_column(String(12), nullable=False)
    compounding: Mapped[str | None] = mapped_column(String(12))
    instalments: Mapped[int] = mapped_column(Integer, nullable=False)
    first_due_on: Mapped[date] = mapped_column(Date, nullable=False)
    grace_periods: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interest_only_periods: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    balloon: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    periodic_fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    fixed_interest: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    upfront_fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    penalty_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    """Percent a year on arrears, only where the agreement provides for it."""
    early_settlement_percent: Mapped[Decimal] = mapped_column(
        Rate, nullable=False, default=Decimal("0")
    )
    prepayment_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allocation_order: Mapped[list[str]] = mapped_column(SA_JSON, nullable=False)
    custom_rows: Mapped[list[dict[str, str]] | None] = mapped_column(SA_JSON)

    financial_account_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("financial_accounts.id", ondelete="RESTRICT")
    )
    """The account the money arrives in or leaves from, unless a transaction names another."""
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    state: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    approval_note: Mapped[str | None] = mapped_column(Text)

    agreement_attachment_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approval_attachment_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    refinances_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    refinanced_by_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    disputed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    closed_on: Mapped[date | None] = mapped_column(Date)
    closing_note: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    schedules: Mapped[list[LoanSchedule]] = relationship(
        back_populates="agreement", order_by="LoanSchedule.version"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        CheckConstraint("principal > 0", name="principal_positive"),
    )


class LoanSchedule(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One version of an agreement's schedule. The first is the schedule as signed; each
    later one is a revision, kept beside the versions before it and never written over."""

    __tablename__ = "loan_schedules"

    agreement_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("loan_agreements.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    principal_basis: Mapped[Decimal] = mapped_column(Money, nullable=False)
    """The principal the version was built on: the agreement's for the original, what
    remained outstanding for a revision."""
    terms: Mapped[dict[str, object]] = mapped_column(SA_JSON, nullable=False)
    """The terms the version was built from, so it can be rebuilt and checked."""
    proposed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    decision_note: Mapped[str | None] = mapped_column(Text)
    evidence_attachment_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    agreement: Mapped[LoanAgreement] = relationship(back_populates="schedules")
    instalments: Mapped[list[LoanInstalment]] = relationship(
        back_populates="schedule", order_by="LoanInstalment.sequence"
    )

    __table_args__ = (UniqueConstraint("agreement_fk", "version", name=None),)


class LoanInstalment(Base, IdentifiedMixin, BusinessScopedMixin):
    """One row of a schedule version, with what has been paid against it."""

    __tablename__ = "loan_instalments"

    schedule_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("loan_schedules.id", ondelete="CASCADE"), nullable=False
    )
    agreement_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("loan_agreements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    opening: Mapped[Decimal] = mapped_column(Money, nullable=False)
    interest: Mapped[Decimal] = mapped_column(Money, nullable=False)
    fees: Mapped[Decimal] = mapped_column(Money, nullable=False)
    principal: Mapped[Decimal] = mapped_column(Money, nullable=False)
    capitalised: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    closing: Mapped[Decimal] = mapped_column(Money, nullable=False)
    penalty: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """Charged afterwards, and only where the agreement provides for it."""
    arrears: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """A row carrying unpaid charges from an earlier version into a revision."""

    paid_penalty: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    paid_fees: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    paid_interest: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    paid_principal: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    accrued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Its interest, fees and any capitalised interest are in the ledger."""
    accrual_journal_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Superseded by a revision or released by settlement; owes nothing more."""

    schedule: Mapped[LoanSchedule] = relationship(back_populates="instalments")

    __table_args__ = (UniqueConstraint("schedule_fk", "sequence", name=None),)

    @property
    def owed(self) -> Decimal:
        return self.penalty + self.fees + self.interest + self.principal

    @property
    def paid(self) -> Decimal:
        return self.paid_penalty + self.paid_fees + self.paid_interest + self.paid_principal


class LoanTransaction(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A drawdown, repayment, charge, refinancing transfer or write-off on an agreement."""

    __tablename__ = "loan_transactions"

    agreement_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("loan_agreements.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    financial_account_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("financial_accounts.id", ondelete="RESTRICT")
    )
    reference: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    evidence_attachment_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    related_agreement_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    unapplied: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    allocations: Mapped[list[LoanAllocation]] = relationship(back_populates="transaction")

    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)


class LoanAllocation(Base, IdentifiedMixin, BusinessScopedMixin):
    """The part of a repayment that settled one component of one instalment."""

    __tablename__ = "loan_allocations"

    transaction_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("loan_transactions.id", ondelete="CASCADE"), nullable=False
    )
    instalment_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("loan_instalments.id", ondelete="RESTRICT")
    )
    """None for a charge outside the schedule: interest to the day of an early settlement,
    or the settlement fee the agreement provides for."""
    component: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)

    transaction: Mapped[LoanTransaction] = relationship(back_populates="allocations")


class LoanSecurity(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Collateral, a guarantor or a covenant the agreement rests on."""

    __tablename__ = "loan_securities"

    agreement_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("loan_agreements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    party_name: Mapped[str | None] = mapped_column(String(200))
    value: Mapped[Decimal | None] = mapped_column(Money)
    review_on: Mapped[date | None] = mapped_column(Date)
    attachment_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    released_on: Mapped[date | None] = mapped_column(Date)


class OtherDebt(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """An amount owed to or by the business that no invoice, expense, tax obligation or
    agreement already records: a refundable deposit paid or held, an advance to a supplier.

    Anything a source document already records is in the register through that document,
    so this table refuses to describe it a second time.
    """

    __tablename__ = "other_debts"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    owed_to_us: Mapped[bool] = mapped_column(Boolean, nullable=False)
    counterparty_name: Mapped[str] = mapped_column(String(200), nullable=False)
    counterparty_party_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120))
    original: Mapped[Decimal] = mapped_column(Money, nullable=False)
    settled: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date | None] = mapped_column(Date)
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    financial_account_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    disputed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    written_off: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attachment_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        CheckConstraint("original > 0", name="original_positive"),
    )


class OtherDebtSettlement(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Money received against, or paid against, an other debt."""

    __tablename__ = "other_debt_settlements"

    debt_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("other_debts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    financial_account_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reference: Mapped[str | None] = mapped_column(String(120))
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)
