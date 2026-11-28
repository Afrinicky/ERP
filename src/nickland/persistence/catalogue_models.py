"""Catalogue and party models for Phase 2.

Every business-owned table carries ``business_id`` from its first migration, per
ADR-0003. Monetary columns use fixed-precision decimal, per PRD 9.5.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON as SA_JSON,
)
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
    BusinessScopedMixin,
    IdentifiedMixin,
    TimestampedMixin,
)
from nickland.persistence.types import Money, Quantity, Rate, UtcDateTime, Uuid

# --------------------------------------------------------------------------------------
# Units of measure
# --------------------------------------------------------------------------------------


class UnitOfMeasure(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A unit of measure.

    ``base_fk`` points at the base unit of its dimension; a base unit points at
    itself. A conversion therefore never crosses a dimension.
    """

    __tablename__ = "units_of_measure"

    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    plural_name: Mapped[str | None] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="count")

    base_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )
    factor: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("1"))
    """How many of the base unit one of this unit contains."""

    decimal_places: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Whether fractional amounts are meaningful: zero for countable things."""

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("business_id", "code", name=None),
        CheckConstraint("factor > 0", name="factor_positive"),
        CheckConstraint("decimal_places >= 0 AND decimal_places <= 6", name="decimal_places_range"),
    )


# --------------------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------------------


class ProductCategory(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A catalogue category. Hierarchical, with a materialised path for fast descent."""

    __tablename__ = "product_categories"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("product_categories.id", ondelete="RESTRICT")
    )
    path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    """Slash-separated ancestry, so descendants are found without recursion."""
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    default_tax_code: Mapped[str | None] = mapped_column(String(40))
    """Consumed by the taxation engine of Phase 4."""

    __table_args__ = (
        UniqueConstraint("business_id", "code", name=None),
        Index("ix_product_categories_path", "business_id", "path"),
        CheckConstraint("depth >= 0 AND depth <= 6", name="depth_range"),
    )


class ProductAttribute(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """An attribute a product may carry, and from which variants may be generated."""

    __tablename__ = "product_attributes"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="text")
    choices: Mapped[list[str] | None] = mapped_column(SA_JSON)
    unit_label: Mapped[str | None] = mapped_column(String(16))
    is_variant_defining: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Whether a distinct value of this attribute produces a distinct variant."""
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class Product(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A product or service.

    ``item_class`` governs whether the item may carry a stock balance at all. PRD 6.1
    requires that a non-stock item require none anywhere in the system.
    """

    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    item_class: Mapped[str] = mapped_column(String(16), nullable=False, default="stocked")

    category_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("product_categories.id", ondelete="RESTRICT"), index=True
    )
    parent_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    """A variant's parent. A parent is not itself sold."""
    is_variant_parent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    variant_values: Mapped[dict[str, str] | None] = mapped_column(SA_JSON)
    variant_label: Mapped[str | None] = mapped_column(String(160))

    stock_unit_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=False
    )
    """The unit in which stock is held and valued."""
    sales_unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )
    purchase_unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )

    tracking_policy: Mapped[str] = mapped_column(String(12), nullable=False, default="none")
    requires_expiry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    valuation_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="business_default"
    )

    manufacturer: Mapped[str | None] = mapped_column(String(160))
    model_code: Mapped[str | None] = mapped_column(String(80))
    country_of_origin: Mapped[str | None] = mapped_column(String(80))

    list_price: Mapped[Decimal | None] = mapped_column(Money)
    """An indicative price. The quotation engine of Phase 5 computes the actual one."""
    currency_code: Mapped[str | None] = mapped_column(String(3))
    tax_code: Mapped[str | None] = mapped_column(String(40))

    weight_kg: Mapped[Decimal | None] = mapped_column(Quantity)
    volume_m3: Mapped[Decimal | None] = mapped_column(Quantity)
    """Carried because PRD 6.3 allows transport to be allocated by weight or volume."""

    reorder_level: Mapped[Decimal | None] = mapped_column(Quantity)
    reorder_quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)

    image_attachment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_sellable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_purchasable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    search_text: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    """SKU, name, manufacturer, model and barcodes, lowercased for one-index search."""

    category: Mapped[ProductCategory | None] = relationship(lazy="joined")
    barcodes: Mapped[list[Barcode]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    attribute_values: Mapped[list[ProductAttributeValue]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    variants: Mapped[list[Product]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", lazy="selectin"
    )
    parent: Mapped[Product | None] = relationship(
        back_populates="variants", remote_side="Product.id"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "sku", name=None),
        Index("ix_products_search", "business_id", "search_text"),
        Index("ix_products_name", "business_id", "name"),
        Index("ix_products_class_active", "business_id", "item_class", "is_active"),
        CheckConstraint(
            "shelf_life_days IS NULL OR shelf_life_days > 0", name="shelf_life_positive"
        ),
        CheckConstraint(
            "lead_time_days IS NULL OR lead_time_days >= 0", name="lead_time_non_negative"
        ),
    )


class ProductAttributeValue(Base, IdentifiedMixin, BusinessScopedMixin):
    """One attribute value on one product."""

    __tablename__ = "product_attribute_values"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    attribute_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("product_attributes.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[str] = mapped_column(String(200), nullable=False)

    product: Mapped[Product] = relationship(back_populates="attribute_values")
    attribute: Mapped[ProductAttribute] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("product_fk", "attribute_fk", name=None),)


class Barcode(Base, IdentifiedMixin, BusinessScopedMixin):
    """A barcode. Several may address one product, in different units."""

    __tablename__ = "barcodes"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )
    value: Mapped[str] = mapped_column(String(48), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="internal")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    product: Mapped[Product] = relationship(back_populates="barcodes")

    __table_args__ = (
        UniqueConstraint("business_id", "value", name=None),
        Index("ix_barcodes_lookup", "business_id", "value"),
    )


class UnitConversionRow(Base, IdentifiedMixin, BusinessScopedMixin):
    """A product-specific conversion, where the general one does not apply.

    A supplier's case of 24 for one product and 12 for another is a product-specific
    fact, not a property of the unit.
    """

    __tablename__ = "product_unit_conversions"

    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    unit_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=False
    )
    factor: Mapped[Decimal] = mapped_column(Rate, nullable=False)
    """How many stock units one of this unit contains, for this product."""

    __table_args__ = (
        UniqueConstraint("product_fk", "unit_fk", name=None),
        CheckConstraint("factor > 0", name="factor_positive"),
    )


class BundleComponentRow(Base, IdentifiedMixin, BusinessScopedMixin):
    """One component of a kit."""

    __tablename__ = "bundle_components"

    bundle_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    component_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    unit_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("bundle_fk", "component_fk", name=None),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("bundle_fk <> component_fk", name="bundle_not_self"),
    )


# --------------------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------------------


class PriceList(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A named set of prices, effective between dates."""

    __tablename__ = "price_lists"

    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    tax_inclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """Whether the prices on this list already carry tax, per PRD 6.3."""

    lines: Mapped[list[PriceListLine]] = relationship(
        back_populates="price_list", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class PriceListLine(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One product's price on one list, optionally by quantity break."""

    __tablename__ = "price_list_lines"

    price_list_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("price_lists.id", ondelete="CASCADE"), nullable=False
    )
    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )
    minimum_quantity: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)

    price_list: Mapped[PriceList] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("price_list_fk", "product_fk", "minimum_quantity", name=None),
        Index("ix_price_list_lines_product", "business_id", "product_fk"),
        CheckConstraint("minimum_quantity >= 0", name="minimum_quantity_non_negative"),
    )


# --------------------------------------------------------------------------------------
# Parties
# --------------------------------------------------------------------------------------


class Party(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A customer, supplier or affiliate.

    One organisation may be several of these at once, so the roles are flags rather
    than separate tables. That keeps one set of contacts and addresses per
    organisation, which is what a user expects.
    """

    __tablename__ = "parties"

    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    party_type: Mapped[str] = mapped_column(String(16), nullable=False, default="organisation")

    is_customer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_affiliate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    email: Mapped[str | None] = mapped_column(String(200))
    telephone: Mapped[str | None] = mapped_column(String(60))
    website: Mapped[str | None] = mapped_column(String(200))

    tax_identifier: Mapped[str | None] = mapped_column(String(60))
    registration_number: Mapped[str | None] = mapped_column(String(60))
    tax_treatment: Mapped[str] = mapped_column(String(24), nullable=False, default="standard")
    tax_exemption_reference: Mapped[str | None] = mapped_column(String(120))
    tax_exemption_expires: Mapped[date | None] = mapped_column(Date)

    currency_code: Mapped[str | None] = mapped_column(String(3))

    payment_term_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="immediate")
    payment_term_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    credit_limit: Mapped[Decimal | None] = mapped_column(Money)
    credit_on_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credit_hold_reason: Mapped[str | None] = mapped_column(Text)

    price_list_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("price_lists.id", ondelete="SET NULL")
    )
    discount_percent: Mapped[Decimal | None] = mapped_column(Rate)
    """A standing discount. The pricing engine applies it at step five, per ADR-0007."""

    default_lead_time_days: Mapped[int | None] = mapped_column(Integer)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    bank_name: Mapped[str | None] = mapped_column(String(120))
    bank_account_name: Mapped[str | None] = mapped_column(String(160))
    bank_account_number: Mapped[str | None] = mapped_column(String(60))
    mobile_money_provider: Mapped[str | None] = mapped_column(String(60))
    mobile_money_number: Mapped[str | None] = mapped_column(String(60))

    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    search_text: Mapped[str] = mapped_column(String(600), nullable=False, default="")

    contacts: Mapped[list[Contact]] = relationship(
        back_populates="party", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "code", name=None),
        Index("ix_parties_search", "business_id", "search_text"),
        Index("ix_parties_name", "business_id", "name"),
        Index("ix_parties_customer", "business_id", "is_customer", "is_active"),
        Index("ix_parties_supplier", "business_id", "is_supplier", "is_active"),
        CheckConstraint("is_customer OR is_supplier OR is_affiliate", name="party_holds_a_role"),
        CheckConstraint(
            "credit_limit IS NULL OR credit_limit >= 0", name="credit_limit_non_negative"
        ),
    )


class Contact(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A named person at a party."""

    __tablename__ = "contacts"

    party_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    position: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200))
    telephone: Mapped[str | None] = mapped_column(String(60))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    party: Mapped[Party] = relationship(back_populates="contacts")

    __table_args__ = (Index("ix_contacts_party", "business_id", "party_fk"),)


class SupplierProduct(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """What a supplier calls a product, and on what terms they supply it.

    The comparison screen of Phase 5 reads these, so the sourcing decision rests on
    recorded terms rather than recollection.
    """

    __tablename__ = "supplier_products"

    supplier_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False
    )
    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )

    supplier_sku: Mapped[str | None] = mapped_column(String(60))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )
    units_per_purchase_unit: Mapped[Decimal] = mapped_column(
        Rate, nullable=False, default=Decimal("1")
    )

    last_price: Mapped[Decimal | None] = mapped_column(Money)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    price_recorded_on: Mapped[date | None] = mapped_column(Date)
    """Preserved so a stale price is visible as stale, per PRD 14."""

    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    minimum_order_quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("supplier_fk", "product_fk", name=None),
        Index("ix_supplier_products_product", "business_id", "product_fk"),
        CheckConstraint("units_per_purchase_unit > 0", name="units_per_purchase_positive"),
    )


class CustomerPrice(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A price agreed with one customer for one product, per PRD 6.10."""

    __tablename__ = "customer_prices"

    customer_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False
    )
    product_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    unit_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("units_of_measure.id", ondelete="RESTRICT")
    )
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    minimum_quantity: Mapped[Decimal] = mapped_column(
        Quantity, nullable=False, default=Decimal("0")
    )
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    agreement_reference: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        UniqueConstraint(
            "customer_fk", "product_fk", "minimum_quantity", "effective_from", name=None
        ),
        Index("ix_customer_prices_lookup", "business_id", "customer_fk", "product_fk"),
    )


class AffiliateProfile(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A referrer's commission arrangement, per PRD 6.4.

    The rate and scope are captured here; the calculation is the pricing engine's,
    and the accrual states are the commission register's, both in Phase 5.
    """

    __tablename__ = "affiliate_profiles"

    party_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False
    )

    scope: Mapped[str] = mapped_column(String(24), nullable=False, default="selected_lines")
    """Selected products, selected categories, selected lines, or all eligible lines."""
    method: Mapped[str] = mapped_column(String(12), nullable=False, default="percentage")
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False, default=Decimal("0"))
    fixed_amount: Mapped[Decimal | None] = mapped_column(Money)
    currency_code: Mapped[str | None] = mapped_column(String(3))

    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)

    payout_trigger: Mapped[str] = mapped_column(
        String(24), nullable=False, default="customer_payment"
    )
    """ADR-0008: payable on receipt of the customer's payment by default."""

    agreement_reference: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    eligible_products: Mapped[list[AffiliateEligibility]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("business_id", "party_fk", "effective_from", name=None),
        CheckConstraint("rate >= 0", name="rate_non_negative"),
    )


class AffiliateEligibility(Base, IdentifiedMixin, BusinessScopedMixin):
    """One product or category within a referrer's eligible scope."""

    __tablename__ = "affiliate_eligibility"

    profile_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("affiliate_profiles.id", ondelete="CASCADE"), nullable=False
    )
    product_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE")
    )
    category_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("product_categories.id", ondelete="CASCADE")
    )
    rate_override: Mapped[Decimal | None] = mapped_column(Rate)
    """PRD 6.4: rates may vary by product."""

    profile: Mapped[AffiliateProfile] = relationship(back_populates="eligible_products")

    __table_args__ = (
        CheckConstraint(
            "(product_fk IS NOT NULL) <> (category_fk IS NOT NULL)",
            name="exactly_one_target",
        ),
    )


# --------------------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------------------


class ImportRun(Base, IdentifiedMixin, BusinessScopedMixin):
    """One import, its dry run and its outcome, per PRD 9.6.

    An import is previewed before it commits, and a rejected import leaves nothing
    behind.
    """

    __tablename__ = "import_runs"

    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="previewed")

    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amended_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    errors: Mapped[list[dict[str, Any]] | None] = mapped_column(SA_JSON)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_import_runs_started", "business_id", "started_at"),)
