"""The register: shifts, sales, tenders and refunds. PRD §6.10, ADR-0009.

A sale is posted once, when it completes; until then it is a cart. A tender carries its own
state and an idempotency key, so a confirmation that arrives twice is recognised the second
time. Stock leaves through the inventory engine under the sale's stock operation.
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
from nickland.persistence.types import Money, Quantity, Rate, UnitPrice, UtcDateTime, Uuid


class PosShift(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin):
    """One cashier at one register, from opening float to signed-off count."""

    __tablename__ = "pos_shifts"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    cashier_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    register: Mapped[str] = mapped_column(String(40), nullable=False, default="Register 1")
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    business_day: Mapped[date] = mapped_column(Date, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    opening_float: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    counted_denominations: Mapped[dict[str, Any] | None] = mapped_column(SA_JSON)
    counted_cash: Mapped[Decimal | None] = mapped_column(Money)
    expected_cash: Mapped[Decimal | None] = mapped_column(Money)
    variance: Mapped[Decimal | None] = mapped_column(Money)
    variance_reason: Mapped[str | None] = mapped_column(String(300))
    submitted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    variance_journal_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_pos_shifts_open", "business_id", "cashier_id", "state"),
    )


class PosCashMovement(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Cash paid into, or taken out of, the drawer during a shift."""

    __tablename__ = "pos_cash_movements"

    shift_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pos_shifts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    counter_account: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )

    __table_args__ = (CheckConstraint("amount > 0", name="pos_cash_movement_positive"),)


class PosSale(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin):
    """A counter sale, from the first scan to the receipt."""

    __tablename__ = "pos_sales"

    number: Mapped[str | None] = mapped_column(String(40))
    """Allocated on completion: an abandoned cart takes no number."""
    shift_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pos_shifts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    customer_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT")
    )
    """Absent for a walk-in customer."""
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    parked_label: Mapped[str | None] = mapped_column(String(80))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")

    gross_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """Before discount."""
    discount_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    net_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    refunded_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    completed_on: Mapped[date | None] = mapped_column(Date, index=True)
    cashier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    stock_operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    receipt_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("issued_documents.id", ondelete="RESTRICT")
    )
    exchange_of_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    """For an exchange: the refund this sale replaces the goods of. Not a foreign key, so
    sales and refunds do not refer to each other in a cycle."""
    void_reason: Mapped[str | None] = mapped_column(String(300))

    lines: Mapped[list[PosSaleLine]] = relationship(
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PosSaleLine.position",
    )
    tenders: Mapped[list[PosTender]] = relationship(
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PosTender.created_at",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_pos_sales_state", "business_id", "shift_fk", "state"),
    )


class PosSaleLine(Base, IdentifiedMixin, BusinessScopedMixin):
    __tablename__ = "pos_sale_lines"

    sale_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pos_sales.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    list_price: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False)
    """The price as resolved, before any override."""
    price_source: Mapped[str] = mapped_column(String(160), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False)
    override_reason: Mapped[str | None] = mapped_column(String(300))
    override_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    discount_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    discount_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    gross: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    discount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    net: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    serials: Mapped[str | None] = mapped_column(Text)
    quantity_refunded: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )

    sale: Mapped[PosSale] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="pos_sale_line_positive"),
        CheckConstraint(
            "quantity_refunded <= quantity", name="pos_sale_line_refund_within_quantity"
        ),
    )


class PosTender(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One part of a payment, with its own state."""

    __tablename__ = "pos_tenders"

    sale_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pos_sales.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    """What the tender settles of the sale."""
    cash_received: Mapped[Decimal | None] = mapped_column(Money)
    change_given: Mapped[Decimal | None] = mapped_column(Money)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(120))
    """The provider's own receipt, transaction or terminal reference."""
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    confirmation_source: Mapped[str | None] = mapped_column(String(16))
    """``cashier`` against the provider's receipt, or ``callback`` from an adapter."""
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    abandoned_reason: Mapped[str | None] = mapped_column(String(300))

    sale: Mapped[PosSale] = relationship(back_populates="tenders")

    __table_args__ = (
        UniqueConstraint("business_id", "idempotency_key", name=None),
        CheckConstraint("amount > 0", name="pos_tender_positive"),
    )


class PosRefund(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A refund, exchange or void, always traceable to the sale it reverses."""

    __tablename__ = "pos_refunds"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="refund")
    sale_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pos_sales.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    shift_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pos_shifts.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    """How the money went back."""
    restock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """Back to usable stock, or to quarantine when not fit to resell."""
    net_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    authorised_by_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    refunded_on: Mapped[date] = mapped_column(Date, nullable=False)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    stock_operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    lines: Mapped[list[PosRefundLine]] = relationship(
        back_populates="refund", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (UniqueConstraint("business_id", "number", name=None),)


class PosRefundLine(Base, IdentifiedMixin, BusinessScopedMixin):
    __tablename__ = "pos_refund_lines"

    refund_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pos_refunds.id", ondelete="CASCADE"), nullable=False
    )
    sale_line_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pos_sale_lines.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    net: Mapped[Decimal] = mapped_column(Money, nullable=False)
    tax: Mapped[Decimal] = mapped_column(Money, nullable=False)

    refund: Mapped[PosRefund] = relationship(back_populates="lines")

    __table_args__ = (CheckConstraint("quantity > 0", name="pos_refund_line_positive"),)
