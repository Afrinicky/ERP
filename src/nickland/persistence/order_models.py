"""Sales orders and procurement: acceptance, orders, receipts, invoices and payments.

PRD §6.8. Three ideas govern the shape.

An order carries the figures it was converted with. The quotation stays the record of
what was offered; the order is the record of what was agreed, and if the two were one
row a later revision of the quotation would quietly change the order.

Every quantity position is stored, not derived at read time: ordered, procured,
received, accepted, rejected, invoiced, cancelled. A list of forty orders that had to
sum each one's receipts to say where it stood would be slow, and the same figure
derived in two screens would eventually disagree.

Posting is recorded on the document that caused it. A goods receipt, a supplier
invoice and a payment each hold the identifier of the journal entry they produced, so
the ledger can be read back from the document and the document from the ledger.
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
from nickland.persistence.types import Money, Quantity, Rate, UnitPrice, UtcDateTime, Uuid

# --------------------------------------------------------------------------------------
# Acceptance and the sales order
# --------------------------------------------------------------------------------------


class CustomerAcceptance(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """The evidence that a quotation was accepted. PRD §6.8, AC-07.

    The order points at its acceptance, not the other way round: two tables each
    holding a key to the other cannot be created in any order, and the one link is
    enough to walk both ways.
    """

    __tablename__ = "customer_acceptances"

    quotation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="RESTRICT"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(20), nullable=False, default="purchase_order")
    reference: Mapped[str | None] = mapped_column(String(120))
    """The customer's purchase order number, or whatever they call their confirmation."""
    accepted_on: Mapped[date] = mapped_column(Date, nullable=False)
    evidence_attachment_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("attachments.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (Index("ix_customer_acceptances_quotation", "business_id", "quotation_fk"),)


class SalesOrder(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin):
    """What the customer has agreed to buy, converted from the quotation without re-entry."""

    __tablename__ = "sales_orders"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    quotation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="RESTRICT"), nullable=False
    )
    acceptance_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("customer_acceptances.id", ondelete="SET NULL")
    )
    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    customer_reference: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    ordered_on: Mapped[date] = mapped_column(Date, nullable=False)
    required_by: Mapped[date | None] = mapped_column(Date)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    precision: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    payment_plan: Mapped[str] = mapped_column(String(12), nullable=False, default="outright")

    net_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total_payable: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    expected_cost_total: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    """The landed cost the quotation expected, summed. Internal: never on a document."""

    notes: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    converted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    lines: Mapped[list[SalesOrderLine]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SalesOrderLine.position",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_sales_orders_customer", "business_id", "customer_fk"),
        Index("ix_sales_orders_state", "business_id", "state"),
    )


class SalesOrderLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One line of an order, with its fulfilment position."""

    __tablename__ = "sales_order_lines"

    order_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quotation_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_lines.id", ondelete="SET NULL")
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL")
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    specification: Mapped[str | None] = mapped_column(Text)
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="SET NULL")
    )
    source_kind: Mapped[str] = mapped_column(String(12), nullable=False, default="supplier")

    quantity_ordered: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    quantity_cancelled: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_procured: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    """Placed on purchase orders, or drawn from stock, against this line."""
    quantity_received: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_assembled: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    """Staged at the warehouse's assembly location for this line."""
    quantity_dispatched: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    """Left the warehouse for the customer, and not come back."""
    quantity_delivered: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    """Signed for by the customer, less what they later returned."""
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")

    unit_price: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False, default=Decimal("0"))
    """The net unit price the customer agreed, at the quotation's precision."""
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    expected_unit_cost: Mapped[Decimal] = mapped_column(
        UnitPrice, nullable=False, default=Decimal("0")
    )
    """The landed unit cost the quotation expected. Internal: never on a document."""
    actual_unit_cost: Mapped[Decimal | None] = mapped_column(UnitPrice)
    """Known once the supplier's invoice is posted. The cost variance is the difference."""

    # An approved substitution. The original product is kept, so the order still says
    # what the customer asked for.
    substituted_from_product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL")
    )
    substitution_reason: Mapped[str | None] = mapped_column(String(300))
    substitution_approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    substitution_approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    order: Mapped[SalesOrder] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("order_fk", "position", name=None),
        CheckConstraint("quantity_ordered > 0", name="sales_order_line_quantity_positive"),
    )


# --------------------------------------------------------------------------------------
# Purchase orders
# --------------------------------------------------------------------------------------


class PurchaseOrder(
    Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin
):
    """What is bought from one supplier, for one customer order or for the shelf."""

    __tablename__ = "purchase_orders"

    number: Mapped[str | None] = mapped_column(String(40))
    """Allocated at issue, so a draft that is discarded leaves no gap."""
    supplier_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    sales_order_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sales_orders.id", ondelete="SET NULL")
    )
    supplier_quotation_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("supplier_quotations.id", ondelete="SET NULL")
    )
    warehouse_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="order")
    """``order`` for a customer order, ``replenishment`` for the shelf."""
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    ordered_on: Mapped[date] = mapped_column(Date, nullable=False)
    expected_on: Mapped[date | None] = mapped_column(Date)
    supplier_reference: Mapped[str | None] = mapped_column(String(120))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    exchange_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("1"))

    net_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    notes: Mapped[str | None] = mapped_column(Text)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    issued_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PurchaseOrderLine.position",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_purchase_orders_supplier", "business_id", "supplier_fk"),
        Index("ix_purchase_orders_sales_order", "business_id", "sales_order_fk"),
        Index("ix_purchase_orders_state", "business_id", "state"),
    )


class PurchaseOrderLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One line bought, with everything that has happened against it since."""

    __tablename__ = "purchase_order_lines"

    order_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sales_order_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sales_order_lines.id", ondelete="SET NULL")
    )
    supplier_quotation_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("supplier_quotation_lines.id", ondelete="SET NULL")
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="SET NULL")
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="SET NULL")
    )

    quantity_ordered: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    quantity_received: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_accepted: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_rejected: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    quantity_invoiced: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )

    unit_cost: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False, default=Decimal("0"))
    """In the order's currency, before the supplier's discount."""
    discount_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    net_unit_cost: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    expected_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(300))

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("order_fk", "position", name=None),
        CheckConstraint("quantity_ordered > 0", name="purchase_order_line_quantity_positive"),
        Index("ix_purchase_order_lines_sales_line", "business_id", "sales_order_line_fk"),
    )


# --------------------------------------------------------------------------------------
# Goods receipts
# --------------------------------------------------------------------------------------


class GoodsReceipt(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin):
    """What arrived against a purchase order. PRD §6.8."""

    __tablename__ = "goods_receipts"

    number: Mapped[str | None] = mapped_column(String(40))
    purchase_order_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_reference: Mapped[str | None] = mapped_column(String(120))
    """The supplier's delivery note or waybill number."""
    inspection_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Goods held in quarantine until inspected; posted to 12100 rather than 12000."""
    notes: Mapped[str | None] = mapped_column(Text)
    accepted_value: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """Accepted quantity at order cost, in base currency: what was posted."""
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    posted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    posted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    lines: Mapped[list[GoodsReceiptLine]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="GoodsReceiptLine.position",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_goods_receipts_order", "business_id", "purchase_order_fk"),
    )


class GoodsReceiptLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One line received, with what was accepted and what was turned away."""

    __tablename__ = "goods_receipt_lines"

    receipt_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    purchase_order_line_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("purchase_order_lines.id", ondelete="RESTRICT"), nullable=False
    )
    quantity_received: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    quantity_accepted: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    quantity_rejected: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(300))
    unit_cost: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False, default=Decimal("0"))
    """The order's net unit cost in base currency, at which the goods were valued."""

    batch: Mapped[str | None] = mapped_column(String(80))
    expiry: Mapped[date | None] = mapped_column(Date)
    serials: Mapped[str | None] = mapped_column(Text)
    """Serial numbers as supplied, one per line. Phase 8 gives each its own row."""
    manufacturer: Mapped[str | None] = mapped_column(String(160))

    receipt: Mapped[GoodsReceipt] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("receipt_fk", "position", name=None),
        CheckConstraint("quantity_received >= 0", name="goods_receipt_line_received_non_negative"),
        CheckConstraint(
            "quantity_accepted + quantity_rejected = quantity_received",
            name="goods_receipt_line_adds_up",
        ),
    )


# --------------------------------------------------------------------------------------
# Supplier billing
# --------------------------------------------------------------------------------------


class SupplierInvoice(
    Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin
):
    """The supplier's bill, matched three ways and posted to payables."""

    __tablename__ = "supplier_invoices"

    number: Mapped[str | None] = mapped_column(String(40))
    """This installation's own number, allocated when the invoice is posted."""
    supplier_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    """The number the supplier printed on it."""
    supplier_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_order_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    invoiced_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    exchange_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("1"))

    net_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    recoverable_tax_total: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    non_recoverable_tax_total: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """In the invoice currency."""
    base_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """The same in base currency: what the payable is."""
    amount_paid: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    amount_credited: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    evidence_attachment_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("attachments.id", ondelete="SET NULL")
    )
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    posted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    posted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[SupplierInvoiceLine]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SupplierInvoiceLine.position",
    )
    variances: Mapped[list[MatchVariance]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MatchVariance.created_at",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        UniqueConstraint("business_id", "supplier_fk", "supplier_reference", name=None),
        Index("ix_supplier_invoices_state", "business_id", "state"),
        Index("ix_supplier_invoices_due", "business_id", "due_on"),
    )


class SupplierInvoiceLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    __tablename__ = "supplier_invoice_lines"

    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_invoices.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    purchase_order_line_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("purchase_order_lines.id", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    recoverable_tax: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    non_recoverable_tax: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )

    invoice: Mapped[SupplierInvoice] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("invoice_fk", "position", name=None),
        CheckConstraint("quantity > 0", name="supplier_invoice_line_quantity_positive"),
    )


class MatchVariance(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A difference between order, receipt and invoice, and what was decided about it."""

    __tablename__ = "match_variances"

    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_invoices.id", ondelete="CASCADE"), nullable=False
    )
    invoice_line_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_invoice_lines.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    expected: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    actual: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    amount_at_stake: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    reason: Mapped[str | None] = mapped_column(String(300))

    invoice: Mapped[SupplierInvoice] = relationship(back_populates="variances")

    __table_args__ = (Index("ix_match_variances_state", "business_id", "state"),)


class SupplierCreditNote(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A supplier's credit against an invoice: short delivery, return, price correction."""

    __tablename__ = "supplier_credit_notes"

    supplier_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    supplier_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_invoices.id", ondelete="RESTRICT"), nullable=False
    )
    credited_on: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    base_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        UniqueConstraint("business_id", "supplier_fk", "supplier_reference", name=None),
        CheckConstraint("amount > 0", name="supplier_credit_note_amount_positive"),
    )


# --------------------------------------------------------------------------------------
# Supplier payments
# --------------------------------------------------------------------------------------


class SupplierPayment(
    Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin
):
    """Money paid to a supplier, allocated across invoices. Posts no expense: PRD §30."""

    __tablename__ = "supplier_payments"

    number: Mapped[str | None] = mapped_column(String(40))
    supplier_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="bank")
    reference: Mapped[str | None] = mapped_column(String(120))
    """The bank or Mobile Money transaction reference."""
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    """What left the account, in base currency."""
    withheld: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    """Withholding tax retained from the supplier and owed to the authority."""
    evidence_attachment_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("attachments.id", ondelete="SET NULL")
    )
    journal_entry_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    allocations: Mapped[list[SupplierPaymentAllocation]] = relationship(
        back_populates="payment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_supplier_payments_supplier", "business_id", "supplier_fk"),
        CheckConstraint("amount > 0", name="supplier_payment_amount_positive"),
        CheckConstraint("withheld >= 0", name="supplier_payment_withheld_non_negative"),
    )


class SupplierPaymentAllocation(Base, IdentifiedMixin, BusinessScopedMixin):
    """How much of one payment settled one invoice."""

    __tablename__ = "supplier_payment_allocations"

    payment_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_payments.id", ondelete="CASCADE"), nullable=False
    )
    invoice_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_invoices.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)

    payment: Mapped[SupplierPayment] = relationship(back_populates="allocations")

    __table_args__ = (
        UniqueConstraint("payment_fk", "invoice_fk", name=None),
        CheckConstraint("amount > 0", name="supplier_payment_allocation_positive"),
    )


# --------------------------------------------------------------------------------------
# Matching tolerance
# --------------------------------------------------------------------------------------


class MatchingPolicy(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """How far an invoiced price may stray from the ordered one. One row per business."""

    __tablename__ = "matching_policies"

    absolute_tolerance: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    rate_tolerance: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))

    __table_args__ = (UniqueConstraint("business_id", name=None),)
