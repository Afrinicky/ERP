"""Financial management records: treasury, receivables, expenses, reconciliation. PRD §20.

Every amount here is also in the ledger; these tables hold what the ledger does not —
which account is which bank, which invoice a receipt settled, which statement line a
posting was matched to — so that every figure can be traced to its document and back.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nickland.persistence.base import (
    Base,
    BranchScopedMixin,
    BusinessScopedMixin,
    IdentifiedMixin,
    TimestampedMixin,
)
from nickland.persistence.types import Money, UtcDateTime, Uuid

# -- treasury -----------------------------------------------------------------------------


class FinancialAccount(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A cash drawer, petty cash box, bank account or Mobile Money wallet."""

    __tablename__ = "financial_accounts"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    """The ledger account it posts to. Its own, so it has its own balance."""
    institution: Mapped[str | None] = mapped_column(String(120))
    """The bank or the Mobile Money provider."""
    number: Mapped[str | None] = mapped_column(String(60))
    """The account or wallet number, as the institution writes it."""
    branch_name: Mapped[str | None] = mapped_column(String(120))
    custodian_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    """Who holds it: the cashier for a drawer, the officer for petty cash."""
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("business_id", "account_fk", name=None),
        UniqueConstraint("business_id", "name", name=None),
    )


class TreasuryMovement(
    Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin
):
    """Money moved between the business's own accounts, or a charge, interest or adjustment
    on one of them."""

    __tablename__ = "treasury_movements"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    from_account_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("financial_accounts.id", ondelete="RESTRICT")
    )
    to_account_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("financial_accounts.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    charge: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """A fee on a transfer, borne by the sending account."""
    reference: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("charge >= 0", name="charge_non_negative"),
        UniqueConstraint("business_id", "number", name=None),
    )
