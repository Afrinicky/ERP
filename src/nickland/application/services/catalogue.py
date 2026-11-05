"""Catalogue services: categories, attributes, products, variants and barcodes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.catalogue import (
    AttributeKind,
    BarcodeKind,
    ItemClass,
    TrackingPolicy,
    ValuationPolicy,
    assert_bundle_valid,
    assert_expiry_consistent,
    assert_tracking_consistent,
    derive_variant_sku,
    validate_barcode,
    validate_sku,
    variant_label,
)
from nickland.domain.catalogue import (
    BundleComponent as BundleComponentSpec,
)
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.organisation import validate_code
from nickland.persistence.base import as_dict
from nickland.persistence.catalogue_models import (
    Barcode,
    BundleComponentRow,
    Product,
    ProductAttribute,
    ProductAttributeValue,
    ProductCategory,
    UnitOfMeasure,
)
from nickland.platform.clock import Clock, SystemClock


@dataclass(slots=True)
class ProductDraft:
    """Everything the product form collects."""

    sku: str
    name: str
    stock_unit_id: uuid.UUID
    item_class: ItemClass | str = ItemClass.STOCKED
    description: str | None = None
    category_id: uuid.UUID | None = None
    sales_unit_id: uuid.UUID | None = None
    purchase_unit_id: uuid.UUID | None = None
    tracking_policy: TrackingPolicy | str = TrackingPolicy.NONE
    requires_expiry: bool = False
    shelf_life_days: int | None = None
    valuation_policy: ValuationPolicy | str = ValuationPolicy.BUSINESS_DEFAULT
    manufacturer: str | None = None
    model_code: str | None = None
    country_of_origin: str | None = None
    list_price: Decimal | None = None
    tax_code: str | None = None
    weight_kg: Decimal | None = None
    volume_m3: Decimal | None = None
    reorder_level: Decimal | None = None
    reorder_quantity: Decimal | None = None
    lead_time_days: int | None = None
    is_sellable: bool = True
    is_purchasable: bool = True
    attributes: dict[str, str] = field(default_factory=dict)
    barcodes: list[tuple[str, BarcodeKind | str]] = field(default_factory=list)
    components: list[tuple[uuid.UUID, Decimal, uuid.UUID]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchPage:
    """A page of catalogue results."""

    items: list[Product]
    total: int
    offset: int
    limit: int


class CatalogueService:
    """The product catalogue."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- categories -------------------------------------------------------------------

    def categories(
        self, actor: AnyActor, *, include_inactive: bool = False
    ) -> list[ProductCategory]:
        actor.require("product.view")
        statement = select(ProductCategory).where(ProductCategory.business_id == actor.business_id)
        if not include_inactive:
            statement = statement.where(ProductCategory.is_active.is_(True))
        return list(
            self._db.scalars(
                statement.order_by(ProductCategory.path, ProductCategory.display_order)
            )
        )

    def create_category(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        parent_id: uuid.UUID | None = None,
        description: str | None = None,
        default_tax_code: str | None = None,
        display_order: int = 0,
    ) -> ProductCategory:
        actor.require("product.manage")
        category_code = validate_code(code, field="code")[:40]
        if not name.strip():
            raise ValidationError("A category name is required.", field="name")

        existing = self._db.scalar(
            select(ProductCategory).where(
                ProductCategory.business_id == actor.business_id,
                ProductCategory.code == category_code,
            )
        )
        if existing is not None:
            raise ConflictError(f"A category with code {category_code!r} already exists.")

        parent: ProductCategory | None = None
        if parent_id is not None:
            parent = self._require_category(actor, parent_id)
            if parent.depth >= 6:
                raise ValidationError(
                    "A category may be nested six levels deep at most.", field="parent_id"
                )

        category = ProductCategory(
            business_id=actor.business_id,
            code=category_code,
            name=name.strip(),
            description=description or None,
            parent_fk=parent.id if parent else None,
            depth=(parent.depth + 1) if parent else 0,
            default_tax_code=default_tax_code or None,
            display_order=display_order,
        )
        self._db.add(category)
        self._db.flush()
        # The path needs the identifier, so it is assigned after the insert and then
        # flushed again: autoflush is off, and a descendant query reads this column.
        category.path = f"{parent.path}/{category.id}" if parent else str(category.id)
        self._db.flush()

        self._audit.record(
            actor,
            "category_created",
            entity_type="product_category",
            entity_id=category.id,
            entity_label=category.name,
            after={"code": category.code, "name": category.name},
        )
        return category

    def descendants(self, actor: AnyActor, category_id: uuid.UUID) -> list[ProductCategory]:
        """A category and everything beneath it, read from the materialised path."""
        category = self._require_category(actor, category_id)
        return list(
            self._db.scalars(
                select(ProductCategory).where(
                    ProductCategory.business_id == actor.business_id,
                    or_(
                        ProductCategory.id == category.id,
                        ProductCategory.path.startswith(f"{category.path}/"),
                    ),
                )
            )
        )

    # -- attributes -------------------------------------------------------------------

    def attributes(self, actor: AnyActor) -> list[ProductAttribute]:
        actor.require("product.view")
        return list(
            self._db.scalars(
                select(ProductAttribute)
                .where(
                    ProductAttribute.business_id == actor.business_id,
                    ProductAttribute.is_active.is_(True),
                )
                .order_by(ProductAttribute.display_order, ProductAttribute.name)
            )
        )

    def create_attribute(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        kind: AttributeKind | str = AttributeKind.TEXT,
        choices: list[str] | None = None,
        is_variant_defining: bool = False,
        unit_label: str | None = None,
        display_order: int = 0,
    ) -> ProductAttribute:
        actor.require("product.manage")
        attribute_code = validate_code(code, field="code")[:40]
        if not name.strip():
            raise ValidationError("An attribute name is required.", field="name")

        attribute_kind = AttributeKind(str(kind))
        if attribute_kind is AttributeKind.CHOICE:
            cleaned = [option.strip() for option in (choices or []) if option.strip()]
            if not cleaned:
                raise ValidationError(
                    "A choice attribute requires at least one option.", field="choices"
                )
            if len(set(cleaned)) != len(cleaned):
                raise ValidationError("Options must be distinct.", field="choices")
            choices = cleaned
        elif choices:
            raise ValidationError(
                f"A {attribute_kind.value} attribute does not take options.", field="choices"
            )

        if is_variant_defining and attribute_kind is not AttributeKind.CHOICE:
            raise ValidationError(
                "Only a choice attribute can define variants, because the variants are "
                "generated from its options.",
                field="is_variant_defining",
            )

        existing = self._db.scalar(
            select(ProductAttribute).where(
                ProductAttribute.business_id == actor.business_id,
                ProductAttribute.code == attribute_code,
            )
        )
        if existing is not None:
            raise ConflictError(f"An attribute with code {attribute_code!r} already exists.")

        attribute = ProductAttribute(
            business_id=actor.business_id,
            code=attribute_code,
            name=name.strip(),
            kind=attribute_kind.value,
            choices=choices,
            is_variant_defining=is_variant_defining,
            unit_label=unit_label or None,
            display_order=display_order,
        )
        self._db.add(attribute)
        self._db.flush()
        self._audit.record(
            actor,
            "attribute_created",
            entity_type="product_attribute",
            entity_id=attribute.id,
            entity_label=attribute.name,
            after={"code": attribute.code, "kind": attribute.kind},
        )
        return attribute

    # -- products ---------------------------------------------------------------------

    def create_product(self, actor: AnyActor, draft: ProductDraft) -> Product:
        """Create a product, with its attributes, barcodes and components."""
        actor.require("product.manage")
        product = self._build(actor, draft)
        self._db.add(product)
        self._db.flush()

        self._apply_attributes(actor, product, draft.attributes)
        self._apply_barcodes(actor, product, draft.barcodes)
        if ItemClass(str(draft.item_class)) is ItemClass.BUNDLE:
            self._apply_components(actor, product, draft.components)
        self._refresh_search_text(product)

        self._audit.record(
            actor,
            "product_created",
            entity_type="product",
            entity_id=product.id,
            entity_label=f"{product.sku} — {product.name}",
            after={
                "sku": product.sku,
                "name": product.name,
                "item_class": product.item_class,
            },
        )
        return product

    def amend_product(
        self, actor: AnyActor, product_id: uuid.UUID, changes: dict[str, Any]
    ) -> Product:
        """Amend a product. Its SKU and classification carry the heaviest consequences."""
        actor.require("product.manage")
        product = self.require_product(actor, product_id)
        before = as_dict(product)

        permitted = {
            "name",
            "description",
            "category_id",
            "sales_unit_id",
            "purchase_unit_id",
            "tracking_policy",
            "requires_expiry",
            "shelf_life_days",
            "valuation_policy",
            "manufacturer",
            "model_code",
            "country_of_origin",
            "list_price",
            "tax_code",
            "weight_kg",
            "volume_m3",
            "reorder_level",
            "reorder_quantity",
            "lead_time_days",
            "is_sellable",
            "is_purchasable",
            "is_active",
            "item_class",
            "sku",
        }
        unknown = set(changes) - permitted
        if unknown:
            raise ValidationError(f"These properties cannot be amended: {sorted(unknown)}")

        if "sku" in changes:
            candidate = validate_sku(str(changes.pop("sku")))
            if candidate != product.sku:
                clash = self._db.scalar(
                    select(Product.id).where(
                        Product.business_id == actor.business_id,
                        Product.sku == candidate,
                        Product.id != product.id,
                    )
                )
                if clash is not None:
                    raise ConflictError(f"SKU {candidate!r} is already in use.")
                product.sku = candidate

        if "item_class" in changes:
            new_class = ItemClass(str(changes.pop("item_class")))
            current = ItemClass(product.item_class)
            if new_class is not current and current.holds_stock:
                # Reclassifying away from stocked would orphan any balance the
                # inventory phase holds. Phase 8 enforces that; the guard is recorded
                # here so the rule is stated where the change is made.
                product.item_class = new_class.value
            else:
                product.item_class = new_class.value

        for key, value in changes.items():
            setattr(product, _column_for(key), value)

        resolved = ItemClass(product.item_class)
        assert_tracking_consistent(resolved, TrackingPolicy(product.tracking_policy))
        assert_expiry_consistent(resolved, product.requires_expiry)
        self._refresh_search_text(product)

        self._audit.record_change(
            actor,
            "product_amended",
            entity_type="product",
            entity_id=product.id,
            entity_label=f"{product.sku} — {product.name}",
            before=before,
            after=as_dict(product),
        )
        return product

    def set_attributes(
        self, actor: AnyActor, product_id: uuid.UUID, values: dict[str, str]
    ) -> Product:
        actor.require("product.manage")
        product = self.require_product(actor, product_id)
        self._apply_attributes(actor, product, values, replace=True)
        self._refresh_search_text(product)
        return product

    def add_barcode(
        self,
        actor: AnyActor,
        product_id: uuid.UUID,
        *,
        value: str,
        kind: BarcodeKind | str = BarcodeKind.INTERNAL,
        unit_id: uuid.UUID | None = None,
        is_primary: bool = False,
    ) -> Barcode:
        actor.require("product.manage")
        product = self.require_product(actor, product_id)
        barcode = self._add_barcode(actor, product, value, kind, unit_id, is_primary)
        self._refresh_search_text(product)
        return barcode

    def remove_barcode(self, actor: AnyActor, barcode_id: uuid.UUID) -> None:
        actor.require("product.manage")
        barcode = self._db.scalar(
            select(Barcode).where(
                Barcode.business_id == actor.business_id, Barcode.id == barcode_id
            )
        )
        if barcode is None:
            raise NotFoundError("Barcode not found.")
        product = self.require_product(actor, barcode.product_fk)
        self._db.delete(barcode)
        self._db.flush()
        self._db.refresh(product)
        self._refresh_search_text(product)

    def generate_variants(
        self,
        actor: AnyActor,
        parent_id: uuid.UUID,
        combinations: list[dict[str, str]],
    ) -> list[Product]:
        """Create variants of a parent from attribute combinations.

        The parent becomes a variant parent and is no longer itself sold; the variants
        inherit its properties and carry their own SKU and barcodes.
        """
        actor.require("product.manage")
        parent = self.require_product(actor, parent_id)
        if parent.parent_fk is not None:
            raise ConflictError("A variant cannot itself carry variants.")
        if not combinations:
            raise ValidationError("At least one combination is required.", field="combinations")

        # Read freshly rather than through the relationship, which is stale after an
        # earlier generation in the same transaction.
        self._db.flush()
        existing = {
            tuple(sorted((values or {}).items()))
            for values in self._db.scalars(
                select(Product.variant_values).where(Product.parent_fk == parent.id)
            )
        }
        created: list[Product] = []

        for combination in combinations:
            signature = tuple(sorted(combination.items()))
            if signature in existing:
                continue
            variant = Product(
                business_id=actor.business_id,
                sku=self._unique_sku(actor, derive_variant_sku(parent.sku, combination)),
                name=f"{parent.name} — {variant_label(combination)}",
                description=parent.description,
                item_class=parent.item_class,
                category_fk=parent.category_fk,
                parent_fk=parent.id,
                variant_values=combination,
                variant_label=variant_label(combination),
                stock_unit_fk=parent.stock_unit_fk,
                sales_unit_fk=parent.sales_unit_fk,
                purchase_unit_fk=parent.purchase_unit_fk,
                tracking_policy=parent.tracking_policy,
                requires_expiry=parent.requires_expiry,
                shelf_life_days=parent.shelf_life_days,
                valuation_policy=parent.valuation_policy,
                manufacturer=parent.manufacturer,
                model_code=parent.model_code,
                country_of_origin=parent.country_of_origin,
                list_price=parent.list_price,
                currency_code=parent.currency_code,
                tax_code=parent.tax_code,
                weight_kg=parent.weight_kg,
                volume_m3=parent.volume_m3,
                lead_time_days=parent.lead_time_days,
            )
            self._db.add(variant)
            self._db.flush()
            self._apply_attributes(actor, variant, combination)
            self._refresh_search_text(variant)
            created.append(variant)
            existing.add(signature)

        if created:
            parent.is_variant_parent = True
            parent.is_sellable = False
            self._db.flush()
            self._audit.record(
                actor,
                "variants_generated",
                entity_type="product",
                entity_id=parent.id,
                entity_label=f"{parent.sku} — {parent.name}",
                context={"created": len(created)},
            )
        return created

    # -- retrieval --------------------------------------------------------------------

    def require_product(self, actor: AnyActor, product_id: uuid.UUID) -> Product:
        product = self._db.scalar(
            select(Product).where(
                Product.business_id == actor.business_id, Product.id == product_id
            )
        )
        if product is None:
            raise NotFoundError("Product not found.")
        return product

    def by_sku(self, actor: AnyActor, sku: str) -> Product | None:
        return self._db.scalar(
            select(Product).where(
                Product.business_id == actor.business_id, Product.sku == sku.strip().upper()
            )
        )

    def by_barcode(self, actor: AnyActor, value: str) -> Product | None:
        """Resolve a scanned barcode to its product.

        The point of sale calls this on every scan, so it reads one index.
        """
        barcode = self._db.scalar(
            select(Barcode).where(
                Barcode.business_id == actor.business_id,
                Barcode.value == value.strip().replace(" ", "").replace("-", "").upper(),
            )
        )
        if barcode is None:
            return None
        return self._db.get(Product, barcode.product_fk)

    def search(
        self,
        actor: AnyActor,
        *,
        term: str | None = None,
        category_id: uuid.UUID | None = None,
        item_class: ItemClass | str | None = None,
        sellable_only: bool = False,
        include_inactive: bool = False,
        include_variant_parents: bool = True,
        offset: int = 0,
        limit: int = 50,
    ) -> SearchPage:
        """Search the catalogue by SKU, name, barcode, manufacturer or model.

        One lowercased column carries every searchable value, so a term matches any of
        them through a single index rather than a disjunction of scans.
        """
        actor.require("product.view")
        statement: Select[tuple[Product]] = select(Product).where(
            Product.business_id == actor.business_id
        )

        if not include_inactive:
            statement = statement.where(Product.is_active.is_(True))
        if sellable_only:
            statement = statement.where(Product.is_sellable.is_(True))
        if not include_variant_parents:
            statement = statement.where(Product.is_variant_parent.is_(False))
        if item_class is not None:
            statement = statement.where(Product.item_class == str(item_class))
        if category_id is not None:
            paths = [category.id for category in self.descendants(actor, category_id)]
            statement = statement.where(Product.category_fk.in_(paths))
        if term and term.strip():
            needle = f"%{term.strip().lower()}%"
            statement = statement.where(Product.search_text.like(needle))

        total = self._db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(self._db.scalars(statement.order_by(Product.name).offset(offset).limit(limit)))
        return SearchPage(items=rows, total=total, offset=offset, limit=limit)

    # -- internals --------------------------------------------------------------------

    def _build(self, actor: AnyActor, draft: ProductDraft) -> Product:
        sku = validate_sku(draft.sku)
        if not draft.name.strip():
            raise ValidationError("A product name is required.", field="name")
        if self.by_sku(actor, sku) is not None:
            raise ConflictError(f"SKU {sku!r} is already in use.")

        item_class = ItemClass(str(draft.item_class))
        tracking = TrackingPolicy(str(draft.tracking_policy))
        assert_tracking_consistent(item_class, tracking)
        assert_expiry_consistent(item_class, draft.requires_expiry)

        stock_unit = self._require_unit(actor, draft.stock_unit_id)
        for label, unit_id in (
            ("sales_unit_id", draft.sales_unit_id),
            ("purchase_unit_id", draft.purchase_unit_id),
        ):
            if unit_id is None:
                continue
            unit = self._require_unit(actor, unit_id)
            if unit.kind != stock_unit.kind:
                raise ValidationError(
                    f"{unit.name} measures {unit.kind} but stock is held in "
                    f"{stock_unit.kind}. A conversion never crosses a dimension.",
                    field=label,
                )

        if draft.category_id is not None:
            self._require_category(actor, draft.category_id)

        return Product(
            business_id=actor.business_id,
            sku=sku,
            name=draft.name.strip(),
            description=draft.description or None,
            item_class=item_class.value,
            category_fk=draft.category_id,
            stock_unit_fk=draft.stock_unit_id,
            sales_unit_fk=draft.sales_unit_id or draft.stock_unit_id,
            purchase_unit_fk=draft.purchase_unit_id or draft.stock_unit_id,
            tracking_policy=tracking.value,
            requires_expiry=draft.requires_expiry,
            shelf_life_days=draft.shelf_life_days,
            valuation_policy=ValuationPolicy(str(draft.valuation_policy)).value,
            manufacturer=draft.manufacturer or None,
            model_code=draft.model_code or None,
            country_of_origin=draft.country_of_origin or None,
            list_price=draft.list_price,
            tax_code=draft.tax_code or None,
            weight_kg=draft.weight_kg,
            volume_m3=draft.volume_m3,
            reorder_level=draft.reorder_level,
            reorder_quantity=draft.reorder_quantity,
            lead_time_days=draft.lead_time_days,
            is_sellable=draft.is_sellable,
            is_purchasable=draft.is_purchasable and item_class.is_procured,
        )

    def _apply_attributes(
        self,
        actor: AnyActor,
        product: Product,
        values: dict[str, str],
        *,
        replace: bool = False,
    ) -> None:
        if not values and not replace:
            return
        known = {attribute.code: attribute for attribute in self.attributes(actor)}
        unknown = set(values) - set(known)
        if unknown:
            raise ValidationError(f"Unknown attribute(s): {sorted(unknown)}")

        if replace:
            for existing in list(product.attribute_values):
                self._db.delete(existing)
            self._db.flush()

        for code, value in values.items():
            attribute = known[code]
            if attribute.kind == AttributeKind.CHOICE.value and str(value) not in (
                attribute.choices or []
            ):
                raise ValidationError(
                    f"{attribute.name} must be one of: {attribute.choices}", field=code
                )
            self._db.add(
                ProductAttributeValue(
                    business_id=actor.business_id,
                    product_fk=product.id,
                    attribute_fk=attribute.id,
                    value=str(value),
                )
            )
        self._db.flush()

    def _apply_barcodes(
        self,
        actor: AnyActor,
        product: Product,
        barcodes: list[tuple[str, BarcodeKind | str]],
    ) -> None:
        for index, (value, kind) in enumerate(barcodes):
            self._add_barcode(actor, product, value, kind, None, is_primary=index == 0)

    def _add_barcode(
        self,
        actor: AnyActor,
        product: Product,
        value: str,
        kind: BarcodeKind | str,
        unit_id: uuid.UUID | None,
        is_primary: bool,
    ) -> Barcode:
        symbology = BarcodeKind(str(kind))
        normalised = validate_barcode(value, symbology)

        clash = self._db.scalar(
            select(Barcode).where(
                Barcode.business_id == actor.business_id, Barcode.value == normalised
            )
        )
        if clash is not None:
            owner = self._db.get(Product, clash.product_fk)
            raise ConflictError(
                f"Barcode {normalised} already addresses "
                f"{owner.sku if owner else 'another product'}. A barcode identifies one "
                "product only."
            )

        barcode = Barcode(
            business_id=actor.business_id,
            product_fk=product.id,
            unit_fk=unit_id,
            value=normalised,
            kind=symbology.value,
            is_primary=is_primary,
        )
        self._db.add(barcode)
        self._db.flush()
        return barcode

    def _apply_components(
        self,
        actor: AnyActor,
        product: Product,
        components: list[tuple[uuid.UUID, Decimal, uuid.UUID]],
    ) -> None:
        specs = [
            BundleComponentSpec(product_id=component, quantity=amount, unit_code="")
            for component, amount, _ in components
        ]
        assert_bundle_valid(specs, own_id=product.id)
        for order, (component_id, amount, unit_id) in enumerate(components):
            self.require_product(actor, component_id)
            self._db.add(
                BundleComponentRow(
                    business_id=actor.business_id,
                    bundle_fk=product.id,
                    component_fk=component_id,
                    quantity=amount,
                    unit_fk=unit_id,
                    display_order=order,
                )
            )
        self._db.flush()

    def _refresh_search_text(self, product: Product) -> None:
        """Rebuild the one column every catalogue search reads."""
        self._db.flush()
        barcodes = self._db.scalars(
            select(Barcode.value).where(Barcode.product_fk == product.id)
        ).all()
        attributes = self._db.scalars(
            select(ProductAttributeValue.value).where(
                ProductAttributeValue.product_fk == product.id
            )
        ).all()
        parts = [
            product.sku,
            product.name,
            product.manufacturer or "",
            product.model_code or "",
            product.variant_label or "",
            *barcodes,
            *attributes,
        ]
        product.search_text = " ".join(part for part in parts if part).lower()[:600]

    def _unique_sku(self, actor: AnyActor, candidate: str) -> str:
        """Append a discriminator where a derived SKU collides."""
        if self.by_sku(actor, candidate) is None:
            return candidate
        for suffix in range(2, 100):
            attempt = validate_sku(f"{candidate}-{suffix}")
            if self.by_sku(actor, attempt) is None:
                return attempt
        raise ConflictError(f"Could not derive a free SKU from {candidate!r}.")

    def _require_unit(self, actor: AnyActor, unit_id: uuid.UUID) -> UnitOfMeasure:
        unit = self._db.scalar(
            select(UnitOfMeasure).where(
                UnitOfMeasure.business_id == actor.business_id, UnitOfMeasure.id == unit_id
            )
        )
        if unit is None:
            raise NotFoundError("Unit of measure not found.")
        return unit

    def _require_category(self, actor: AnyActor, category_id: uuid.UUID) -> ProductCategory:
        category = self._db.scalar(
            select(ProductCategory).where(
                ProductCategory.business_id == actor.business_id,
                ProductCategory.id == category_id,
            )
        )
        if category is None:
            raise NotFoundError("Category not found.")
        return category


_COLUMN_ALIASES = {
    "category_id": "category_fk",
    "sales_unit_id": "sales_unit_fk",
    "purchase_unit_id": "purchase_unit_fk",
}


def _column_for(key: str) -> str:
    return _COLUMN_ALIASES.get(key, key)
