"""Quotation, costing and pricing: requests, sourcing, quotations and their schedules.

Three ideas govern the shape of these tables.

A quotation line carries both its pricing inputs and its computed figures. Holding
only the inputs would make every list screen re-run the engine; holding only the
figures would make the build-up unexplainable. Both are stored, and the snapshot taken
at issue proves they agree.

Sourcing is a collection, not a column. PRD §6.2 requires one quotation to source its
lines from stock and from several suppliers, and one line may be split between them,
so the source hangs off the line and carries its own quantity and cost.

The costing snapshot is append-only. PRD §6.3 requires a supplier price change after
issue to leave the issued quotation numerically unchanged, and that guarantee is only
as strong as the thing that stops the row being rewritten.
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

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


class MarginPolicy(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A margin method and rate, addressed at a business, a category or a product.

    PRD §6.3: the more specific setting prevails. The scope is stored rather than
    inferred from which foreign key is populated, so a query can order by precedence
    without a case expression.
    """

    __tablename__ = "margin_policies"

    scope: Mapped[str] = mapped_column(String(12), nullable=False, default="business")
    category_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("product_categories.id", ondelete="CASCADE")
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE")
    )

    method: Mapped[str] = mapped_column(String(24), nullable=False, default="markup_on_cost")
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))

    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("business_id", "scope", "category_fk", "product_fk", name=None),
        CheckConstraint("rate >= 0", name="rate_non_negative"),
        CheckConstraint(
            "(scope = 'business' AND category_fk IS NULL AND product_fk IS NULL) OR "
            "(scope = 'category' AND category_fk IS NOT NULL AND product_fk IS NULL) OR "
            "(scope = 'product' AND product_fk IS NOT NULL AND category_fk IS NULL)",
            name="scope_matches_target",
        ),
    )


class DiscountLimitRow(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """What a role may discount without approval, per PRD §6.3.

    A role with no row here may discount nothing on its own authority. The absence of
    a limit is read as zero rather than as permission: a control that defaults open is
    not a control.
    """

    __tablename__ = "discount_limits"

    role_code: Mapped[str] = mapped_column(String(40), nullable=False)
    maximum_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    maximum_amount: Mapped[Decimal | None] = mapped_column(Money)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("business_id", "role_code", name=None),
        CheckConstraint("maximum_rate >= 0", name="maximum_rate_non_negative"),
    )


class InstalmentPlan(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """An instalment arrangement the business offers, per PRD §6.5."""

    __tablename__ = "instalment_plans"

    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    applies_to: Mapped[str] = mapped_column(String(12), nullable=False, default="business")
    category_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("product_categories.id", ondelete="CASCADE")
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE")
    )
    customer_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="CASCADE")
    )
    """PRD §6.5: a plan may be offered to one customer only."""

    frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    deposit_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    bands: Mapped[list[InstalmentBand]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InstalmentBand.minimum_months",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "code", name=None),
        CheckConstraint("deposit_rate >= 0", name="deposit_rate_non_negative"),
    )


class InstalmentBand(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """The finance charge for a range of repayment durations, per PRD §6.5."""

    __tablename__ = "instalment_bands"

    plan_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("instalment_plans.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    minimum_months: Mapped[int] = mapped_column(Integer, nullable=False)
    maximum_months: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="percentage")
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    fixed_per_unit: Mapped[Decimal | None] = mapped_column(Money)

    plan: Mapped[InstalmentPlan] = relationship(back_populates="bands")

    __table_args__ = (
        UniqueConstraint("plan_fk", "minimum_months", name=None),
        CheckConstraint("minimum_months >= 1", name="minimum_months_positive"),
        CheckConstraint("maximum_months >= minimum_months", name="band_is_ordered"),
        CheckConstraint("rate >= 0", name="rate_non_negative"),
    )


# --------------------------------------------------------------------------------------
# The customer's request
# --------------------------------------------------------------------------------------


class QuotationRequest(
    Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin
):
    """A customer's request for quotation, per PRD §6.2."""

    __tablename__ = "quotation_requests"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    contact_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("contacts.id", ondelete="SET NULL")
    )
    customer_reference: Mapped[str | None] = mapped_column(String(120))
    """The customer's own reference, which is how they will refer to it."""

    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    required_by: Mapped[date | None] = mapped_column(Date)
    delivery_address: Mapped[str | None] = mapped_column(Text)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[QuotationRequestLine]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="QuotationRequestLine.position",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_quotation_requests_customer", "business_id", "customer_fk"),
        Index("ix_quotation_requests_state", "business_id", "state"),
    )


class QuotationRequestLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One requested item.

    ``product_fk`` is nullable because a customer may ask for something the catalogue
    does not hold yet, and refusing to record the request until somebody creates the
    product would lose the enquiry.
    """

    __tablename__ = "quotation_request_lines"

    request_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotation_requests.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT")
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    specification: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )
    required_by: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    request: Mapped[QuotationRequest] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("request_fk", "position", name=None),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )


# --------------------------------------------------------------------------------------
# Supplier sourcing
# --------------------------------------------------------------------------------------


class SupplierRequest(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A request for quotation put to one supplier, per PRD §6.2."""

    __tablename__ = "supplier_requests"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    supplier_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    request_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_requests.id", ondelete="SET NULL")
    )
    """The customer request this sourcing serves, where there is one."""

    issued_on: Mapped[date | None] = mapped_column(Date)
    respond_by: Mapped[date | None] = mapped_column(Date)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[SupplierRequestLine]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SupplierRequestLine.position",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_supplier_requests_supplier", "business_id", "supplier_fk"),
        Index("ix_supplier_requests_request", "business_id", "request_fk"),
    )


class SupplierRequestLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One item put to a supplier."""

    __tablename__ = "supplier_request_lines"

    supplier_request_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_requests.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_request_lines.id", ondelete="SET NULL")
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT")
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    specification: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )

    request: Mapped[SupplierRequest] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("supplier_request_fk", "position", name=None),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )


class SupplierQuotation(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """What a supplier offered, as they offered it, per PRD §6.2.

    The document-level charges are held here and allocated to lines when a quotation
    is costed, so a supplier's freight is not silently buried in their unit price.
    """

    __tablename__ = "supplier_quotations"

    reference: Mapped[str] = mapped_column(String(60), nullable=False)
    """The supplier's own quotation number, which is what a query to them will cite."""

    supplier_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_request_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("supplier_requests.id", ondelete="SET NULL")
    )
    request_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_requests.id", ondelete="SET NULL")
    )

    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    exchange_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("1"))

    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    freight_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    other_charges: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    payment_terms: Mapped[str | None] = mapped_column(String(200))
    tax_treatment: Mapped[str] = mapped_column(String(20), nullable=False, default="exclusive")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[SupplierQuotationLine]] = relationship(
        back_populates="quotation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SupplierQuotationLine.position",
    )

    __table_args__ = (
        UniqueConstraint("business_id", "supplier_fk", "reference", name=None),
        Index("ix_supplier_quotations_request", "business_id", "request_fk"),
        CheckConstraint("exchange_rate > 0", name="exchange_rate_positive"),
        CheckConstraint("freight_amount >= 0", name="freight_non_negative"),
        CheckConstraint("other_charges >= 0", name="other_charges_non_negative"),
    )


class SupplierQuotationLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One line of a supplier's offer.

    ``is_available`` with a reason, rather than an absent line, because "they do not
    stock it" is an answer the comparison screen needs to show.
    """

    __tablename__ = "supplier_quotation_lines"

    supplier_quotation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("supplier_quotations.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_request_lines.id", ondelete="SET NULL")
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT")
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)

    quantity_offered: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False)
    discount_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    recoverable_tax_rate: Mapped[Decimal] = mapped_column(
        Rate, nullable=False, default=Decimal("0")
    )
    non_recoverable_tax_rate: Mapped[Decimal] = mapped_column(
        Rate, nullable=False, default=Decimal("0")
    )

    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    minimum_order_quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    availability_note: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)

    quotation: Mapped[SupplierQuotation] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("supplier_quotation_fk", "position", name=None),
        Index("ix_supplier_quotation_lines_request_line", "business_id", "request_line_fk"),
        CheckConstraint("quantity_offered > 0", name="quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_non_negative"),
        CheckConstraint("discount_rate >= 0", name="discount_rate_non_negative"),
    )


# --------------------------------------------------------------------------------------
# The quotation
# --------------------------------------------------------------------------------------


class Quotation(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin, BranchScopedMixin):
    """A priced offer to a customer, per PRD §6.2 and §6.3.

    The totals are stored because they are the figures that were issued. Recomputing
    them for a list screen would be slower and, after a rate change, wrong.
    """

    __tablename__ = "quotations"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="SET NULL")
    )

    request_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_requests.id", ondelete="SET NULL")
    )
    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    contact_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("contacts.id", ondelete="SET NULL")
    )
    customer_reference: Mapped[str | None] = mapped_column(String(120))

    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    quoted_on: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    exchange_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("1"))
    precision: Mapped[int] = mapped_column(Integer, nullable=False, default=4)

    # Pricing configuration for the document as a whole.
    document_discount_method: Mapped[str] = mapped_column(
        String(12), nullable=False, default="percentage"
    )
    document_discount_value: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    affiliate_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="SET NULL")
    )
    commission_scope: Mapped[str | None] = mapped_column(String(24))

    payment_plan: Mapped[str] = mapped_column(String(12), nullable=False, default="outright")
    instalment_plan_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("instalment_plans.id", ondelete="SET NULL")
    )
    instalment_months: Mapped[int | None] = mapped_column(Integer)
    instalment_frequency: Mapped[str | None] = mapped_column(String(16))
    deposit_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    first_instalment_on: Mapped[date | None] = mapped_column(Date)
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Totals, in the order of the canonical sequence.
    landed_cost_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    margin_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    commission_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    list_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    net_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    finance_charge_total: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    payable_before_tax: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total_payable: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    # Approval and issue.
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approval_reason: Mapped[str | None] = mapped_column(Text)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    issued_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    accepted_on: Mapped[date | None] = mapped_column(Date)
    decided_on: Mapped[date | None] = mapped_column(Date)
    decision_reason: Mapped[str | None] = mapped_column(Text)

    terms: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    internal_notes: Mapped[str | None] = mapped_column(Text)
    """Not customer-facing. Segregated from ``notes`` so a template cannot print it."""

    lines: Mapped[list[QuotationLine]] = relationship(
        back_populates="quotation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="QuotationLine.position",
    )
    expenses: Mapped[list[QuotationExpense]] = relationship(
        back_populates="quotation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="QuotationExpense.reference",
    )

    __table_args__ = (
        # Unique on the number and the revision together. A revision deliberately
        # reuses the number: QTN-2026-0002 revision 2 is the same offer at a later
        # moment, and the customer holding revision 1 needs the two to be
        # recognisably one document. Uniqueness on the number alone would make the
        # revision PRD §6.2 requires impossible to write.
        UniqueConstraint("business_id", "number", "revision", name=None),
        Index("ix_quotations_customer", "business_id", "customer_fk"),
        Index("ix_quotations_state", "business_id", "state"),
        Index("ix_quotations_request", "business_id", "request_fk"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("precision >= 0 AND precision <= 6", name="precision_in_range"),
        CheckConstraint("exchange_rate > 0", name="exchange_rate_positive"),
    )


class QuotationLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One line: its pricing inputs, then what the engine made of them."""

    __tablename__ = "quotation_lines"

    quotation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_request_lines.id", ondelete="SET NULL")
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT")
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    specification: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )

    # Step 1 — supplier cost.
    source_kind: Mapped[str] = mapped_column(String(12), nullable=False, default="supplier")
    unit_cost: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False, default=Decimal("0"))
    supplier_discount_rate: Mapped[Decimal] = mapped_column(
        Rate, nullable=False, default=Decimal("0")
    )
    supplier_discount_amount: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    non_recoverable_tax: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    recoverable_tax: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    line_weight: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))
    line_volume: Mapped[Decimal] = mapped_column(Quantity, nullable=False, default=Decimal("0"))

    # Steps 3 to 6 — configuration.
    margin_method: Mapped[str] = mapped_column(String(24), nullable=False, default="markup_on_cost")
    margin_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    margin_source: Mapped[str | None] = mapped_column(String(12))
    """Which policy scope supplied the rate, so the figure is explicable."""

    commission_method: Mapped[str | None] = mapped_column(String(16))
    commission_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    commission_fixed_per_unit: Mapped[Decimal | None] = mapped_column(Money)
    commission_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    commission_in_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    commission_reason: Mapped[str | None] = mapped_column(String(200))

    discount_method: Mapped[str] = mapped_column(String(12), nullable=False, default="percentage")
    discount_value: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    uplift_method: Mapped[str] = mapped_column(String(16), nullable=False, default="percentage")
    uplift_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    uplift_fixed_per_unit: Mapped[Decimal | None] = mapped_column(Money)

    # Computed.
    allocated_cost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    landed_cost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    margin_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    commission_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    list_price: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    discount_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    net_price: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    finance_charge: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    payable_before_tax: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total_payable: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    unit_list_price: Mapped[Decimal] = mapped_column(
        UnitPrice, nullable=False, default=Decimal("0")
    )
    unit_net_price: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False, default=Decimal("0"))

    # Cost preservation, per PRD §6.3 step 3.
    actual_unit_cost: Mapped[Decimal | None] = mapped_column(UnitPrice)
    actual_cost_recorded_on: Mapped[date | None] = mapped_column(Date)
    actual_cost_reference: Mapped[str | None] = mapped_column(String(120))

    quotation: Mapped[Quotation] = relationship(back_populates="lines")
    sources: Mapped[list[QuotationLineSource]] = relationship(
        back_populates="line",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="QuotationLineSource.position",
    )

    __table_args__ = (
        UniqueConstraint("quotation_fk", "position", name=None),
        Index("ix_quotation_lines_product", "business_id", "product_fk"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_non_negative"),
        CheckConstraint("discount_amount >= 0", name="discount_non_negative"),
    )


class QuotationLineSource(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """Where part or all of a line is to be supplied from, per PRD §6.2.

    One line may carry several of these: four units from stock and six from a supplier
    is one offer to the customer and two sourcing decisions internally.
    """

    __tablename__ = "quotation_line_sources"

    quotation_line_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotation_lines.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="supplier")

    supplier_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT")
    )
    supplier_quotation_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("supplier_quotation_lines.id", ondelete="SET NULL")
    )
    warehouse_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT")
    )

    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(UnitPrice, nullable=False, default=Decimal("0"))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    exchange_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("1"))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    selection_reason: Mapped[str | None] = mapped_column(String(200))
    """Why this source was chosen over the others, per PRD §6.2."""

    line: Mapped[QuotationLine] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint("quotation_line_fk", "position", name=None),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_non_negative"),
        CheckConstraint("exchange_rate > 0", name="exchange_rate_positive"),
        CheckConstraint(
            "(kind = 'stock' AND supplier_fk IS NULL) OR (kind = 'supplier' AND "
            "supplier_fk IS NOT NULL)",
            name="source_names_its_origin",
        ),
    )


class QuotationExpense(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """An allocable cost on the document, and the basis it is apportioned on."""

    __tablename__ = "quotation_expenses"

    quotation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="GHS")
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="by_value")
    nominated_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_lines.id", ondelete="SET NULL")
    )
    excluded_line_ids: Mapped[list[str]] = mapped_column(SA_JSON, nullable=False, default=list)
    manual_amounts: Mapped[dict[str, Any]] = mapped_column(SA_JSON, nullable=False, default=dict)
    supplier_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="SET NULL")
    )
    is_disclosed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Whether the customer sees it as a separate charge. Internal by default, per the
    confidentiality table of the pricing engine specification."""

    quotation: Mapped[Quotation] = relationship(back_populates="expenses")

    __table_args__ = (
        UniqueConstraint("quotation_fk", "reference", name=None),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )


class QuotationLineCost(Base, IdentifiedMixin, BusinessScopedMixin):
    """One expense's share of one line.

    Stored rather than recomputed so that the build-up screen can show which expense
    put which amount on which line, which is what "every component separately visible"
    requires.
    """

    __tablename__ = "quotation_line_costs"

    quotation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False
    )
    quotation_line_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotation_lines.id", ondelete="CASCADE"), nullable=False
    )
    expense_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotation_expenses.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    __table_args__ = (
        UniqueConstraint("quotation_line_fk", "expense_fk", name=None),
        Index("ix_quotation_line_costs_quotation", "business_id", "quotation_fk"),
    )


class QuotationSnapshot(Base, IdentifiedMixin, BusinessScopedMixin):
    """The costing input set as it stood when the quotation was issued.

    Append-only, enforced by the session guard. PRD §6.3 requires a later change to a
    supplier price to leave the issued quotation unchanged; this is the record that
    makes the claim checkable rather than merely asserted. The hash is over the
    canonical form of the payload, so a row altered outside the application is
    detectable.
    """

    __tablename__ = "quotation_snapshots"

    quotation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    taken_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    taken_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(SA_JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("quotation_fk", "revision", name=None),
        Index("ix_quotation_snapshots_business", "business_id", "quotation_fk"),
    )


class QuotationInstalment(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One scheduled payment, per PRD §6.5.

    A rescheduling writes a new version rather than amending these rows, per PRD
    §25.5, so what the customer originally signed up to remains readable.
    """

    __tablename__ = "quotation_instalments"

    quotation_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False
    )
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    paid_on: Mapped[date | None] = mapped_column(Date)
    paid_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    waiver_reason: Mapped[str | None] = mapped_column(Text)
    is_deposit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("quotation_fk", "schedule_version", "sequence", name=None),
        Index("ix_quotation_instalments_due", "business_id", "due_on"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
    )


class CommissionAccrual(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A referrer's entitlement on one line, and where it has reached, per PRD §6.4.

    The basis and the rate are stored beside the amount, so a referrer asking how a
    figure arose is answered from the row rather than from a recalculation that may no
    longer reproduce it.
    """

    __tablename__ = "commission_accruals"

    affiliate_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    affiliate_profile_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("affiliate_profiles.id", ondelete="SET NULL")
    )
    quotation_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="SET NULL")
    )
    quotation_line_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("quotation_lines.id", ondelete="SET NULL")
    )
    source_reference: Mapped[str | None] = mapped_column(String(60))
    """The invoice the accrual arose on, once there is one."""

    state: Mapped[str] = mapped_column(String(12), nullable=False, default="accrued")
    accrued_on: Mapped[date] = mapped_column(Date, nullable=False)
    basis_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="percentage")
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    accrued_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    fulfilled_fraction: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("1"))
    paid_fraction: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    entitlement: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    settled_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    paid_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    payment_reference: Mapped[str | None] = mapped_column(String(120))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    prior_rate: Mapped[Decimal | None] = mapped_column(Rate)
    override_reason: Mapped[str | None] = mapped_column(Text)
    overridden_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    overridden_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    """PRD §6.4: a rate change or override records actor, timestamp, reason and the
    prior value."""

    __table_args__ = (
        UniqueConstraint("quotation_line_fk", "affiliate_fk", name=None),
        Index("ix_commission_accruals_affiliate", "business_id", "affiliate_fk", "state"),
        Index("ix_commission_accruals_state", "business_id", "state"),
        CheckConstraint("accrued_amount >= 0", name="accrued_non_negative"),
        CheckConstraint("entitlement >= 0", name="entitlement_non_negative"),
        CheckConstraint("settled_amount >= 0", name="settled_non_negative"),
    )
