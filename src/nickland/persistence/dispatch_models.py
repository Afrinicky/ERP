"""Shipments, their lines, and what went wrong on the way. PRD §6.9.

The stock each shipment moved is in the stock ledger under the shipment's operations;
the documents issued for it are issued documents whose source is the shipment. These
tables hold what neither of those records: the consignment, and the outcome per line.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nickland.persistence.base import (
    Base,
    BranchScopedMixin,
    BusinessScopedMixin,
    IdentifiedMixin,
    TimestampedMixin,
)
from nickland.persistence.types import Quantity, Uuid


class Shipment(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin):
    """One consignment for one sales order."""

    __tablename__ = "shipments"

    number: Mapped[str] = mapped_column(String(48), nullable=False)
    """The order's number and the shipment's place among its shipments: SO-2026-0001/2."""
    sales_order_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="prepared")

    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="own_vehicle")
    carrier: Mapped[str | None] = mapped_column(String(160))
    driver: Mapped[str | None] = mapped_column(String(160))
    vehicle: Mapped[str | None] = mapped_column(String(80))
    destination: Mapped[str | None] = mapped_column(Text)
    package_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tracking_reference: Mapped[str | None] = mapped_column(String(120))

    prepared_on: Mapped[date] = mapped_column(Date, nullable=False)
    packed_on: Mapped[date | None] = mapped_column(Date)
    dispatched_on: Mapped[date | None] = mapped_column(Date)
    delivered_on: Mapped[date | None] = mapped_column(Date)

    dispatch_operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    delivery_operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    pick_list_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("issued_documents.id", ondelete="RESTRICT")
    )
    packing_list_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("issued_documents.id", ondelete="RESTRICT")
    )
    waybill_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("issued_documents.id", ondelete="RESTRICT")
    )
    delivery_note_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("issued_documents.id", ondelete="RESTRICT")
    )
    acknowledgement_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_acknowledgements.id", ondelete="RESTRICT")
    )
    """The proof of delivery: the recipient's acknowledgement of the waybill."""

    notes: Mapped[str | None] = mapped_column(Text)
    prepared_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    dispatched_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    cancellation_reason: Mapped[str | None] = mapped_column(String(300))

    lines: Mapped[list[ShipmentLine]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ShipmentLine.position",
    )
    exceptions: Mapped[list[DeliveryException]] = relationship(
        back_populates="shipment",
        lazy="selectin",
        order_by="DeliveryException.created_at",
    )

    __table_args__ = (
        Index("ix_shipments_number", "business_id", "number", unique=True),
        Index("ix_shipments_state", "business_id", "state"),
        CheckConstraint("package_count >= 0", name="shipment_packages_not_negative"),
    )


class ShipmentLine(Base, IdentifiedMixin, BusinessScopedMixin):
    """One order line on one shipment, and what became of it."""

    __tablename__ = "shipment_lines"

    shipment_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sales_order_line_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_order_lines.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    """Shipped."""
    serials: Mapped[str | None] = mapped_column(Text)
    """For a serial-tracked product, the units on the truck, one per line."""
    quantity_delivered: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_returned: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_damaged: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_lost: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    quantity_customer_returned: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )

    shipment: Mapped[Shipment] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="shipment_line_positive"),
        CheckConstraint(
            "quantity_delivered + quantity_returned + quantity_damaged + quantity_lost <= quantity",
            name="shipment_line_accounted_within_shipped",
        ),
        CheckConstraint(
            "quantity_customer_returned <= quantity_delivered",
            name="shipment_line_returns_within_delivered",
        ),
    )


class DeliveryException(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A failed, short or damaged delivery, a loss, or a return after delivery."""

    __tablename__ = "delivery_exceptions"

    shipment_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    shipment_line_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("shipment_lines.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    replacement_owed: Mapped[bool] = mapped_column(nullable=False, default=True)
    """Whether the customer is still owed the quantity, or it is cancelled from the order."""
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    shipment: Mapped[Shipment] = relationship(back_populates="exceptions")

    __table_args__ = (CheckConstraint("quantity > 0", name="delivery_exception_positive"),)
