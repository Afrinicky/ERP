"""Inventory: the movement ledger, what it sums to, and the documents that drive it.

PRD §6.11, ADR-0004, docs/modules/inventory.md.

``stock_movements`` is the record, and is append-only at the session guard. The
balances, positions and cost layers are derived from it and kept by the stock engine
alone, in the same unit of work as the movement that changes them, so a reader never
sees one without the other.

No unique constraint spans a nullable column here. A bin, a batch or a location is
often absent, and two absent values are distinct to a unique index on both engines, so
the constraint would admit the duplicates it was written to stop. The engine looks a
balance up with explicit ``IS NULL`` tests instead, under the pool's lock.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

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
from nickland.persistence.types import PostedAmount, Quantity, UnitPrice, UtcDateTime, Uuid

# --------------------------------------------------------------------------------------
# Configuration and places
# --------------------------------------------------------------------------------------


class InventorySettings(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """How the business values stock and warns of expiry. One row per business."""

    __tablename__ = "inventory_settings"

    valuation_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="weighted_average"
    )
    expiry_warning_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)

    __table_args__ = (UniqueConstraint("business_id", name=None),)


class StockLocation(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A place within a warehouse: the main store, quarantine, the assembly bay."""

    __tablename__ = "stock_locations"

    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="storage")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    bins: Mapped[list[StockBin]] = relationship(
        back_populates="location", lazy="selectin", order_by="StockBin.code"
    )

    __table_args__ = (UniqueConstraint("warehouse_fk", "code", name=None),)


class StockBin(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """An optional shelf or bin within a location, per PRD §6.11."""

    __tablename__ = "stock_bins"

    location_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_locations.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    location: Mapped[StockLocation] = relationship(back_populates="bins")

    __table_args__ = (UniqueConstraint("location_fk", "code", name=None),)


# --------------------------------------------------------------------------------------
# Traceability
# --------------------------------------------------------------------------------------


class Batch(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A batch or lot of one product, with the dates that govern it."""

    __tablename__ = "batches"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(160))
    manufactured_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date, index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("business_id", "product_fk", "code", name=None),)


class StockSerial(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One serially identified unit, and where it is now."""

    __tablename__ = "stock_serials"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    serial_number: Mapped[str] = mapped_column(String(120), nullable=False)
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    warehouse_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT")
    )
    location_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    bin_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_bins.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="in_stock")

    __table_args__ = (UniqueConstraint("business_id", "product_fk", "serial_number", name=None),)


# --------------------------------------------------------------------------------------
# The ledger and what it sums to
# --------------------------------------------------------------------------------------


class StockMovement(Base, IdentifiedMixin, BusinessScopedMixin, BranchScopedMixin):
    """One change in what is held, where, in what state, and at what value.

    Append-only. The rows of one action share an operation identifier, a pool sequence
    per pool touched, and one journal entry. A correction is a reversing operation, never
    an amendment.
    """

    __tablename__ = "stock_movements"

    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    """The pool's sequence after this operation: a reversal is permitted while it is the
    pool's latest."""
    movement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    location_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    bin_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_bins.id", ondelete="RESTRICT")
    )
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    serial_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_serials.id", ondelete="RESTRICT")
    )

    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    """Signed: into the state positive, out of it negative."""
    value: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False, default=Decimal("0"))
    """Signed, at the precision the journal is posted at. Zero for a reservation."""
    unit_cost: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False, default=Decimal("0"))
    """Informational: value over quantity, for a reader. Never multiplied back."""

    source_type: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reference: Mapped[str | None] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(String(300))
    reverses_operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    override_reason: Mapped[str | None] = mapped_column(String(300))
    """Where an expired batch was issued, why, and by whom below."""
    override_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        Index("ix_stock_movements_pool", "business_id", "product_fk", "warehouse_fk", "sequence"),
        Index("ix_stock_movements_source", "business_id", "source_type", "source_id"),
        Index("ix_stock_movements_batch", "business_id", "batch_fk"),
    )


class StockBalance(Base, IdentifiedMixin, BusinessScopedMixin):
    """Quantity by product, warehouse, location, bin, batch and state: the ledger summed."""

    __tablename__ = "stock_balances"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    location_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    bin_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_bins.id", ondelete="RESTRICT")
    )
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))

    __table_args__ = (
        Index("ix_stock_balances_pool", "business_id", "product_fk", "warehouse_fk", "state"),
        CheckConstraint("quantity >= 0", name="stock_balance_not_negative"),
    )


class StockPosition(Base, IdentifiedMixin, BusinessScopedMixin):
    """A product in a warehouse, in each state: the row the engine locks to change it."""

    __tablename__ = "stock_positions"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    usable: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    quarantine: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    rejected: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    in_transit: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    reserved: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("business_id", "product_fk", "warehouse_fk", name=None),
        CheckConstraint(
            "usable >= 0 AND quarantine >= 0 AND rejected >= 0 AND in_transit >= 0 "
            "AND reserved >= 0",
            name="stock_position_not_negative",
        ),
        CheckConstraint("reserved <= usable", name="stock_position_reserved_within_usable"),
    )


class CostLayer(Base, IdentifiedMixin, BusinessScopedMixin):
    """Stock that arrived together, at one cost, and what remains of it. ADR-0004."""

    __tablename__ = "cost_layers"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    movement_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_movements.id", ondelete="RESTRICT"), nullable=False
    )
    """The movement that created the layer."""
    origin_layer_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cost_layers.id", ondelete="RESTRICT")
    )
    """Where a layer was carried from another warehouse or state: the layer it came out
    of. A revaluation of the original receipt follows the chain to wherever the stock is."""
    source_type: Mapped[str | None] = mapped_column(String(32))
    source_line_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    """For a receipt, the goods receipt line: the key a landed charge or an invoice price
    variance finds the layer by."""
    aged_on: Mapped[date] = mapped_column(Date, nullable=False)

    quantity_received: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    quantity_remaining: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    value_received: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    value_remaining: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)

    __table_args__ = (
        Index("ix_cost_layers_pool", "business_id", "product_fk", "warehouse_fk", "state"),
        CheckConstraint(
            "quantity_remaining >= 0 AND quantity_remaining <= quantity_received",
            name="cost_layer_remaining_within_received",
        ),
        CheckConstraint("value_remaining >= 0", name="cost_layer_value_not_negative"),
    )


class LayerConsumption(Base, IdentifiedMixin, BusinessScopedMixin):
    """What a movement took from a layer, or gave back to it. Append-only.

    Kept so a FIFO cost is traceable to the receipts it came from, and so a reversal can
    return exactly what was taken.
    """

    __tablename__ = "layer_consumptions"

    movement_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_movements.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    layer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("cost_layers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    """Signed: taken from the layer positive, given back negative."""
    value: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    """Signed with the quantity. A revaluation moves value with no quantity."""


# --------------------------------------------------------------------------------------
# Claims on stock
# --------------------------------------------------------------------------------------


class StockReservation(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Usable stock held for an order line. Every change is mirrored in the ledger."""

    __tablename__ = "stock_reservations"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    sales_order_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sales_order_lines.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    quantity_consumed: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_released: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_assembled: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    reserved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notes: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        Index("ix_stock_reservations_pool", "business_id", "product_fk", "warehouse_fk", "state"),
        CheckConstraint(
            "quantity_consumed + quantity_released <= quantity",
            name="stock_reservation_within_quantity",
        ),
    )


# --------------------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------------------


class StockTransfer(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Stock sent from one warehouse to another, in transit until it is received."""

    __tablename__ = "stock_transfers"

    number: Mapped[str | None] = mapped_column(String(40))
    from_warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    to_warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    requested_on: Mapped[date] = mapped_column(Date, nullable=False)
    dispatched_on: Mapped[date | None] = mapped_column(Date)
    received_on: Mapped[date | None] = mapped_column(Date)
    dispatch_operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    receipt_operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    lines: Mapped[list[StockTransferLine]] = relationship(
        back_populates="transfer",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="StockTransferLine.position",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        CheckConstraint("from_warehouse_fk <> to_warehouse_fk", name="stock_transfer_two_places"),
    )


class StockTransferLine(Base, IdentifiedMixin, BusinessScopedMixin):
    __tablename__ = "stock_transfer_lines"

    transfer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_transfers.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    serials: Mapped[str | None] = mapped_column(Text)
    """Serial numbers, one per line, for a serial-tracked product."""
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)

    transfer: Mapped[StockTransfer] = relationship(back_populates="lines")

    __table_args__ = (CheckConstraint("quantity > 0", name="stock_transfer_line_positive"),)


class StockCount(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A physical count of a warehouse or one of its locations, and its variance review."""

    __tablename__ = "stock_counts"

    number: Mapped[str | None] = mapped_column(String(40))
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    location_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    counted_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    opened_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    posted_operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    lines: Mapped[list[StockCountLine]] = relationship(
        back_populates="count",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="StockCountLine.position",
    )

    __table_args__ = (UniqueConstraint("business_id", "number", name=None),)


class StockCountLine(Base, IdentifiedMixin, BusinessScopedMixin):
    __tablename__ = "stock_count_lines"

    count_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stock_counts.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    location_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    bin_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_bins.id", ondelete="RESTRICT")
    )
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    system_quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    """What the ledger said when the count was opened."""
    counted_quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    reason: Mapped[str | None] = mapped_column(String(300))

    count: Mapped[StockCount] = relationship(back_populates="lines")


class Inspection(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """The outcome of inspecting quarantined stock. PRD §6.8."""

    __tablename__ = "inspections"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    batch_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="RESTRICT")
    )
    receipt_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("goods_receipt_lines.id", ondelete="RESTRICT")
    )
    quantity_passed: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    quantity_failed: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    criteria: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[str | None] = mapped_column(Text)
    inspected_on: Mapped[date] = mapped_column(Date, nullable=False)
    inspector_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    evidence_attachment_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("attachments.id", ondelete="SET NULL")
    )
    operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class LandedCharge(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Freight, duty, clearing, port, handling or insurance, carried into the stock it
    brought in, per §20.3, §29.4 and ADR-0004 rule 5."""

    __tablename__ = "landed_charges"

    receipt_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("goods_receipts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="by_value")
    reference: Mapped[str | None] = mapped_column(String(120))
    payee_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    to_inventory: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    to_cost_of_sales: Mapped[Decimal] = mapped_column(PostedAmount, nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="RESTRICT")
    )
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (CheckConstraint("amount > 0", name="landed_charge_positive"),)
