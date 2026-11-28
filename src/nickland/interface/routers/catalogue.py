"""Catalogue endpoints: units, categories, attributes, products and price lists."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from nickland.application.services.catalogue import ProductDraft
from nickland.domain.catalogue import ItemClass
from nickland.interface.catalogue_schemas import (
    AmendProductContract,
    AttributeReply,
    BarcodeContract,
    BarcodeReply,
    CategoryReply,
    ConvertContract,
    CreateAttributeContract,
    CreateCategoryContract,
    CreatePriceListContract,
    CreateProductContract,
    CreateUnitContract,
    GenerateVariantsContract,
    PriceListLineReply,
    PriceListReply,
    ProductFactorContract,
    ProductPageReply,
    ProductReply,
    ResolvedPriceReply,
    SetAttributesContract,
    SetPriceContract,
    UnitReply,
)
from nickland.interface.dependencies import (
    ActorDependency,
    CatalogueDependency,
    PriceListDependency,
    UnitDependency,
)

router = APIRouter(tags=["catalogue"])


def _product_reply(product: object) -> ProductReply:
    reply = ProductReply.model_validate(product)
    reply.holds_stock = ItemClass(reply.item_class).holds_stock
    return reply


# -- units -----------------------------------------------------------------------------


@router.get("/units", response_model=list[UnitReply], summary="List units of measure")
def list_units(
    actor: ActorDependency, units: UnitDependency, include_inactive: bool = False
) -> list[UnitReply]:
    return [
        UnitReply.model_validate(unit)
        for unit in units.units(actor, include_inactive=include_inactive)
    ]


@router.post("/units", response_model=UnitReply, status_code=201, summary="Add a unit")
def create_unit(
    contract: CreateUnitContract, actor: ActorDependency, units: UnitDependency
) -> UnitReply:
    return UnitReply.model_validate(units.create(actor, **contract.model_dump()))


@router.post("/units/convert", response_model=Decimal, summary="Convert between units")
def convert_units(
    contract: ConvertContract, actor: ActorDependency, units: UnitDependency
) -> Decimal:
    """Convert an amount, preferring a product-specific factor where one is recorded."""
    return units.convert_quantity(
        actor,
        amount=contract.amount,
        from_unit_id=contract.from_unit_id,
        to_unit_id=contract.to_unit_id,
        product_id=contract.product_id,
    )


# -- categories and attributes ---------------------------------------------------------


@router.get("/categories", response_model=list[CategoryReply], summary="List categories")
def list_categories(
    actor: ActorDependency, catalogue: CatalogueDependency, include_inactive: bool = False
) -> list[CategoryReply]:
    return [
        CategoryReply.model_validate(category)
        for category in catalogue.categories(actor, include_inactive=include_inactive)
    ]


@router.post("/categories", response_model=CategoryReply, status_code=201, summary="Add a category")
def create_category(
    contract: CreateCategoryContract,
    actor: ActorDependency,
    catalogue: CatalogueDependency,
) -> CategoryReply:
    return CategoryReply.model_validate(catalogue.create_category(actor, **contract.model_dump()))


@router.get("/attributes", response_model=list[AttributeReply], summary="List attributes")
def list_attributes(actor: ActorDependency, catalogue: CatalogueDependency) -> list[AttributeReply]:
    return [AttributeReply.model_validate(attribute) for attribute in catalogue.attributes(actor)]


@router.post(
    "/attributes", response_model=AttributeReply, status_code=201, summary="Add an attribute"
)
def create_attribute(
    contract: CreateAttributeContract,
    actor: ActorDependency,
    catalogue: CatalogueDependency,
) -> AttributeReply:
    return AttributeReply.model_validate(catalogue.create_attribute(actor, **contract.model_dump()))


# -- products --------------------------------------------------------------------------


@router.get("/products", response_model=ProductPageReply, summary="Search the catalogue")
def search_products(
    actor: ActorDependency,
    catalogue: CatalogueDependency,
    term: str | None = None,
    category_id: uuid.UUID | None = None,
    item_class: str | None = None,
    sellable_only: bool = False,
    include_inactive: bool = False,
    include_variant_parents: bool = True,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ProductPageReply:
    """Search by SKU, name, barcode, manufacturer, model or attribute value."""
    page = catalogue.search(
        actor,
        term=term,
        category_id=category_id,
        item_class=item_class,
        sellable_only=sellable_only,
        include_inactive=include_inactive,
        include_variant_parents=include_variant_parents,
        offset=offset,
        limit=limit,
    )
    return ProductPageReply(
        items=[_product_reply(product) for product in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post("/products", response_model=ProductReply, status_code=201, summary="Add a product")
def create_product(
    contract: CreateProductContract,
    actor: ActorDependency,
    catalogue: CatalogueDependency,
) -> ProductReply:
    payload = contract.model_dump()
    barcodes = [(item["value"], item["kind"]) for item in payload.pop("barcodes", [])]
    components = [
        (item["product_id"], item["quantity"], item["unit_id"])
        for item in payload.pop("components", [])
    ]
    draft = ProductDraft(**payload, barcodes=barcodes, components=components)
    return _product_reply(catalogue.create_product(actor, draft))


@router.get(
    "/products/by-barcode/{value}",
    response_model=ProductReply,
    summary="Resolve a scanned barcode",
)
def product_by_barcode(
    value: str, actor: ActorDependency, catalogue: CatalogueDependency
) -> ProductReply:
    """Resolve a barcode to its product. The register calls this on every scan."""
    from nickland.domain.errors import NotFoundError

    product = catalogue.by_barcode(actor, value)
    if product is None:
        raise NotFoundError(f"No product carries the barcode {value}.")
    return _product_reply(product)


@router.get("/products/{product_id}", response_model=ProductReply, summary="Read a product")
def read_product(
    product_id: uuid.UUID, actor: ActorDependency, catalogue: CatalogueDependency
) -> ProductReply:
    actor.require("product.view")
    return _product_reply(catalogue.require_product(actor, product_id))


@router.patch("/products/{product_id}", response_model=ProductReply, summary="Amend a product")
def amend_product(
    product_id: uuid.UUID,
    contract: AmendProductContract,
    actor: ActorDependency,
    catalogue: CatalogueDependency,
) -> ProductReply:
    changes = contract.model_dump(exclude_unset=True)
    return _product_reply(catalogue.amend_product(actor, product_id, changes))


@router.put(
    "/products/{product_id}/attributes",
    response_model=ProductReply,
    summary="Replace a product's attributes",
)
def set_attributes(
    product_id: uuid.UUID,
    contract: SetAttributesContract,
    actor: ActorDependency,
    catalogue: CatalogueDependency,
) -> ProductReply:
    return _product_reply(catalogue.set_attributes(actor, product_id, contract.values))


@router.post(
    "/products/{product_id}/barcodes",
    response_model=BarcodeReply,
    status_code=201,
    summary="Add a barcode",
)
def add_barcode(
    product_id: uuid.UUID,
    contract: BarcodeContract,
    actor: ActorDependency,
    catalogue: CatalogueDependency,
) -> BarcodeReply:
    return BarcodeReply.model_validate(
        catalogue.add_barcode(actor, product_id, **contract.model_dump())
    )


@router.delete(
    "/barcodes/{barcode_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a barcode",
)
def remove_barcode(
    barcode_id: uuid.UUID, actor: ActorDependency, catalogue: CatalogueDependency
) -> Response:
    catalogue.remove_barcode(actor, barcode_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/products/{product_id}/variants",
    response_model=list[ProductReply],
    status_code=201,
    summary="Generate variants",
)
def generate_variants(
    product_id: uuid.UUID,
    contract: GenerateVariantsContract,
    actor: ActorDependency,
    catalogue: CatalogueDependency,
) -> list[ProductReply]:
    """Create variants from attribute combinations. The parent ceases to be sold."""
    created = catalogue.generate_variants(actor, product_id, contract.combinations)
    return [_product_reply(variant) for variant in created]


@router.put(
    "/products/{product_id}/unit-factor",
    response_model=Decimal,
    summary="Set a product-specific conversion factor",
)
def set_product_factor(
    product_id: uuid.UUID,
    contract: ProductFactorContract,
    actor: ActorDependency,
    units: UnitDependency,
) -> Decimal:
    """Record how many stock units one of a unit contains, for this product."""
    row = units.set_product_factor(
        actor,
        product_id=product_id,
        unit_id=contract.unit_id,
        factor=contract.factor,
    )
    return row.factor


# -- price lists -------------------------------------------------------------------------


@router.get("/price-lists", response_model=list[PriceListReply], summary="List price lists")
def list_price_lists(
    actor: ActorDependency, pricing: PriceListDependency, include_inactive: bool = False
) -> list[PriceListReply]:
    return [
        PriceListReply.model_validate(item)
        for item in pricing.price_lists(actor, include_inactive=include_inactive)
    ]


@router.post(
    "/price-lists",
    response_model=PriceListReply,
    status_code=201,
    summary="Add a price list",
)
def create_price_list(
    contract: CreatePriceListContract,
    actor: ActorDependency,
    pricing: PriceListDependency,
) -> PriceListReply:
    return PriceListReply.model_validate(pricing.create(actor, **contract.model_dump()))


@router.get(
    "/price-lists/{price_list_id}/lines",
    response_model=list[PriceListLineReply],
    summary="Read a price list's lines",
)
def price_list_lines(
    price_list_id: uuid.UUID, actor: ActorDependency, pricing: PriceListDependency
) -> list[PriceListLineReply]:
    return [PriceListLineReply.model_validate(line) for line in pricing.lines(actor, price_list_id)]


@router.put(
    "/price-lists/{price_list_id}/lines",
    response_model=PriceListLineReply,
    summary="Set a price on a list",
)
def set_price(
    price_list_id: uuid.UUID,
    contract: SetPriceContract,
    actor: ActorDependency,
    pricing: PriceListDependency,
) -> PriceListLineReply:
    return PriceListLineReply.model_validate(
        pricing.set_price(actor, price_list_id=price_list_id, **contract.model_dump())
    )


@router.get("/prices/resolve", response_model=ResolvedPriceReply, summary="Resolve a selling price")
def resolve_price(
    actor: ActorDependency,
    pricing: PriceListDependency,
    product_id: uuid.UUID,
    customer_id: uuid.UUID | None = None,
    quantity: Decimal = Decimal("1"),
) -> ResolvedPriceReply:
    """Resolve a price and report which source produced it.

    The order of precedence is fixed: an agreement with the customer, the customer's
    assigned list, the default list, then the product's indicative price.
    """
    from nickland.domain.errors import NotFoundError

    resolved = pricing.resolve(
        actor, product_id=product_id, customer_id=customer_id, quantity=quantity
    )
    if resolved is None:
        raise NotFoundError(
            "No price is recorded for this product. Set one on a price list, or record "
            "an indicative price on the product."
        )
    return ResolvedPriceReply.model_validate(resolved)
