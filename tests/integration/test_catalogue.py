"""Catalogue services against a real database. PRD 6.1."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.units import UnitService
from nickland.domain.catalogue import AttributeKind, BarcodeKind, ItemClass, TrackingPolicy
from nickland.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor


@pytest.fixture
def catalogue(db: Session, clock: FixedClock) -> CatalogueService:
    return CatalogueService(db, clock=clock)


@pytest.fixture
def units(db: Session, clock: FixedClock) -> UnitService:
    return UnitService(db, clock=clock)


@pytest.fixture
def each(units: UnitService, admin: Actor) -> uuid.UUID:
    return next(unit for unit in units.units(admin) if unit.code == "ea").id


@pytest.fixture
def box(units: UnitService, admin: Actor) -> uuid.UUID:
    return next(unit for unit in units.units(admin) if unit.code == "box").id


def _draft(each_id: uuid.UUID, **overrides: object) -> ProductDraft:
    values: dict[str, object] = {
        "sku": "GLV-NITRILE-M",
        "name": "Nitrile examination glove, medium",
        "stock_unit_id": each_id,
    }
    values.update(overrides)
    return ProductDraft(**values)  # type: ignore[arg-type]


class TestSeededUnits:
    def test_provisioning_seeds_the_conventional_units(
        self, units: UnitService, admin: Actor
    ) -> None:
        codes = {unit.code for unit in units.units(admin)}
        assert {"ea", "box", "kg", "g", "l", "ml", "hour"} <= codes

    def test_seeding_runs_once(self, units: UnitService, admin: Actor) -> None:
        assert units.seed(admin.business_id) == 0

    def test_a_derived_unit_carries_its_factor(self, units: UnitService, admin: Actor) -> None:
        kilogram = next(unit for unit in units.units(admin) if unit.code == "kg")
        assert kilogram.factor == Decimal("1000.000000")

    def test_a_unit_cannot_derive_across_a_dimension(
        self, units: UnitService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="never crosses a dimension"):
            units.create(
                admin, code="odd", name="Odd", kind="mass", base_code="ea", factor=Decimal("5")
            )


class TestProductCreation:
    def test_creates_a_product(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        product = catalogue.create_product(admin, _draft(each))
        assert product.sku == "GLV-NITRILE-M"
        assert product.item_class == ItemClass.STOCKED.value
        assert product.sales_unit_fk == each

    def test_the_sku_is_normalised(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        product = catalogue.create_product(admin, _draft(each, sku=" glv nitrile m "))
        assert product.sku == "GLV-NITRILE-M"

    def test_a_duplicate_sku_is_refused(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        catalogue.create_product(admin, _draft(each))
        with pytest.raises(ConflictError, match="already in use"):
            catalogue.create_product(admin, _draft(each))

    def test_creating_a_product_requires_the_permission(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        weak = make_actor(admin.business_id, permissions={"product.view"})
        with pytest.raises(PermissionDeniedError, match=r"product\.manage"):
            catalogue.create_product(weak, _draft(each))

    def test_a_service_cannot_carry_batch_tracking(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        """PRD 6.1: tracking presupposes stock, which a service does not hold."""
        with pytest.raises(ValidationError, match="holds no stock"):
            catalogue.create_product(
                admin,
                _draft(each, item_class=ItemClass.SERVICE, tracking_policy=TrackingPolicy.BATCH),
            )

    def test_a_non_stock_item_cannot_require_expiry(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        with pytest.raises(ValidationError, match="holds no stock"):
            catalogue.create_product(
                admin, _draft(each, item_class=ItemClass.NON_STOCK, requires_expiry=True)
            )

    def test_a_service_is_not_purchasable_as_stock(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        product = catalogue.create_product(
            admin, _draft(each, item_class=ItemClass.SERVICE, is_purchasable=True)
        )
        assert not product.is_purchasable

    def test_units_must_share_a_dimension(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID, units: UnitService
    ) -> None:
        kilogram = next(unit for unit in units.units(admin) if unit.code == "kg")
        with pytest.raises(ValidationError, match="never crosses a dimension"):
            catalogue.create_product(admin, _draft(each, sales_unit_id=kilogram.id))


class TestBarcodes:
    def test_a_barcode_is_validated_on_entry(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        with pytest.raises(ValidationError, match="check digit"):
            catalogue.create_product(
                admin, _draft(each, barcodes=[("5012345678901", BarcodeKind.EAN_13)])
            )

    def test_a_barcode_addresses_one_product_only(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        catalogue.create_product(
            admin, _draft(each, barcodes=[("5012345678900", BarcodeKind.EAN_13)])
        )
        with pytest.raises(ConflictError, match="already addresses"):
            catalogue.create_product(
                admin,
                _draft(each, sku="OTHER-1", barcodes=[("5012345678900", BarcodeKind.EAN_13)]),
            )

    def test_a_scan_resolves_to_its_product(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        created = catalogue.create_product(
            admin, _draft(each, barcodes=[("5012345678900", BarcodeKind.EAN_13)])
        )
        assert catalogue.by_barcode(admin, "501 234 567 8900") == created
        assert catalogue.by_barcode(admin, "0000000000000") is None

    def test_a_barcode_can_be_removed(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        product = catalogue.create_product(
            admin, _draft(each, barcodes=[("5012345678900", BarcodeKind.EAN_13)])
        )
        catalogue.remove_barcode(admin, product.barcodes[0].id)
        assert catalogue.by_barcode(admin, "5012345678900") is None


class TestAttributesAndVariants:
    def test_an_attribute_value_must_be_a_permitted_choice(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        catalogue.create_attribute(
            admin,
            code="size",
            name="Size",
            kind=AttributeKind.CHOICE,
            choices=["Small", "Medium", "Large"],
            is_variant_defining=True,
        )
        with pytest.raises(ValidationError, match="must be one of"):
            catalogue.create_product(admin, _draft(each, attributes={"size": "Enormous"}))

    def test_only_a_choice_attribute_may_define_variants(
        self, catalogue: CatalogueService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="Only a choice attribute"):
            catalogue.create_attribute(
                admin,
                code="notes",
                name="Notes",
                kind=AttributeKind.TEXT,
                is_variant_defining=True,
            )

    def test_variants_are_generated_and_the_parent_withdrawn_from_sale(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        catalogue.create_attribute(
            admin,
            code="size",
            name="Size",
            kind=AttributeKind.CHOICE,
            choices=["Small", "Medium", "Large"],
            is_variant_defining=True,
        )
        parent = catalogue.create_product(admin, _draft(each, sku="GLV-NITRILE"))
        created = catalogue.generate_variants(
            admin, parent.id, [{"size": "Small"}, {"size": "Medium"}, {"size": "Large"}]
        )
        assert len(created) == 3
        assert {variant.sku for variant in created} == {
            "GLV-NITRILE-SMALL",
            "GLV-NITRILE-MEDIUM",
            "GLV-NITRILE-LARGE",
        }
        assert parent.is_variant_parent
        assert not parent.is_sellable
        assert all(variant.parent_fk == parent.id for variant in created)

    def test_generating_the_same_combination_twice_adds_nothing(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        catalogue.create_attribute(
            admin,
            code="size",
            name="Size",
            kind=AttributeKind.CHOICE,
            choices=["Small"],
            is_variant_defining=True,
        )
        parent = catalogue.create_product(admin, _draft(each, sku="GLV-NITRILE"))
        catalogue.generate_variants(admin, parent.id, [{"size": "Small"}])
        assert catalogue.generate_variants(admin, parent.id, [{"size": "Small"}]) == []

    def test_a_variant_cannot_itself_carry_variants(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        catalogue.create_attribute(
            admin,
            code="size",
            name="Size",
            kind=AttributeKind.CHOICE,
            choices=["Small"],
            is_variant_defining=True,
        )
        parent = catalogue.create_product(admin, _draft(each, sku="GLV-NITRILE"))
        variant = catalogue.generate_variants(admin, parent.id, [{"size": "Small"}])[0]
        with pytest.raises(ConflictError, match="cannot itself carry variants"):
            catalogue.generate_variants(admin, variant.id, [{"size": "Small"}])


class TestSearch:
    @pytest.fixture(autouse=True)
    def _catalogue(self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID) -> None:
        catalogue.create_product(
            admin,
            _draft(
                each,
                sku="GLV-NITRILE-M",
                name="Nitrile examination glove, medium",
                manufacturer="Medisafe",
                barcodes=[("5012345678900", BarcodeKind.EAN_13)],
            ),
        )
        catalogue.create_product(
            admin,
            _draft(
                each,
                sku="SYR-10ML",
                name="Disposable syringe, 10 ml",
                manufacturer="Medisafe",
                model_code="DS-10",
            ),
        )
        catalogue.create_product(
            admin, _draft(each, sku="SRV-CALIB", name="Analyser calibration", item_class="service")
        )

    @pytest.mark.parametrize(
        ("term", "expected"),
        [
            ("glove", {"GLV-NITRILE-M"}),
            ("GLV", {"GLV-NITRILE-M"}),
            ("5012345678900", {"GLV-NITRILE-M"}),
            ("medisafe", {"GLV-NITRILE-M", "SYR-10ML"}),
            ("DS-10", {"SYR-10ML"}),
            ("syringe", {"SYR-10ML"}),
        ],
    )
    def test_one_term_searches_every_field(
        self, catalogue: CatalogueService, admin: Actor, term: str, expected: set[str]
    ) -> None:
        found = {product.sku for product in catalogue.search(admin, term=term).items}
        assert found == expected

    def test_search_is_case_insensitive(self, catalogue: CatalogueService, admin: Actor) -> None:
        assert catalogue.search(admin, term="GLOVE").total == 1
        assert catalogue.search(admin, term="glove").total == 1

    def test_filtering_by_class(self, catalogue: CatalogueService, admin: Actor) -> None:
        page = catalogue.search(admin, item_class=ItemClass.SERVICE)
        assert {product.sku for product in page.items} == {"SRV-CALIB"}

    def test_paging_reports_the_whole_total(
        self, catalogue: CatalogueService, admin: Actor
    ) -> None:
        page = catalogue.search(admin, limit=2)
        assert page.total == 3
        assert len(page.items) == 2

    def test_search_requires_the_permission(
        self, catalogue: CatalogueService, admin: Actor
    ) -> None:
        weak = make_actor(admin.business_id, permissions=set())
        with pytest.raises(PermissionDeniedError, match=r"product\.view"):
            catalogue.search(weak)


class TestCategories:
    def test_a_category_tree_carries_its_path(
        self, catalogue: CatalogueService, admin: Actor
    ) -> None:
        root = catalogue.create_category(admin, code="consumables", name="Consumables")
        child = catalogue.create_category(admin, code="gloves", name="Gloves", parent_id=root.id)
        assert child.depth == 1
        assert child.path.startswith(root.path)

    def test_descendants_are_found_from_the_path(
        self, catalogue: CatalogueService, admin: Actor
    ) -> None:
        root = catalogue.create_category(admin, code="consumables", name="Consumables")
        child = catalogue.create_category(admin, code="gloves", name="Gloves", parent_id=root.id)
        grandchild = catalogue.create_category(
            admin, code="nitrile", name="Nitrile", parent_id=child.id
        )
        found = {item.id for item in catalogue.descendants(admin, root.id)}
        assert found == {root.id, child.id, grandchild.id}

    def test_searching_a_category_includes_its_descendants(
        self, catalogue: CatalogueService, admin: Actor, each: uuid.UUID
    ) -> None:
        root = catalogue.create_category(admin, code="consumables", name="Consumables")
        child = catalogue.create_category(admin, code="gloves", name="Gloves", parent_id=root.id)
        catalogue.create_product(admin, _draft(each, category_id=child.id))
        assert catalogue.search(admin, category_id=root.id).total == 1

    def test_an_unknown_parent_is_not_found(
        self, catalogue: CatalogueService, admin: Actor
    ) -> None:
        with pytest.raises(NotFoundError):
            catalogue.create_category(admin, code="orphan", name="Orphan", parent_id=uuid.uuid4())


class TestProductUnitFactors:
    def test_a_box_holds_a_different_count_per_product(
        self,
        catalogue: CatalogueService,
        units: UnitService,
        admin: Actor,
        each: uuid.UUID,
        box: uuid.UUID,
    ) -> None:
        """A box of 24 for one product and 12 for another is a product fact."""
        gloves = catalogue.create_product(admin, _draft(each, sku="GLV-1"))
        syringes = catalogue.create_product(admin, _draft(each, sku="SYR-1"))
        units.set_product_factor(admin, product_id=gloves.id, unit_id=box, factor=Decimal("24"))
        units.set_product_factor(admin, product_id=syringes.id, unit_id=box, factor=Decimal("12"))

        assert units.convert_quantity(
            admin,
            amount=Decimal("2"),
            from_unit_id=box,
            to_unit_id=each,
            product_id=gloves.id,
        ) == Decimal("48.000000")
        assert units.convert_quantity(
            admin,
            amount=Decimal("2"),
            from_unit_id=box,
            to_unit_id=each,
            product_id=syringes.id,
        ) == Decimal("24.000000")

    def test_the_stock_unit_converts_to_itself_at_one(
        self,
        catalogue: CatalogueService,
        units: UnitService,
        admin: Actor,
        each: uuid.UUID,
    ) -> None:
        product = catalogue.create_product(admin, _draft(each))
        with pytest.raises(ValidationError, match="factor of one"):
            units.set_product_factor(
                admin, product_id=product.id, unit_id=each, factor=Decimal("5")
            )
