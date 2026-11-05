"""Catalogue rules: classification, barcodes and unit conversion. PRD 6.1."""

from __future__ import annotations

from decimal import Decimal

import pytest

from nickland.domain.catalogue import (
    BarcodeKind,
    ItemClass,
    TrackingPolicy,
    assert_bundle_valid,
    assert_expiry_consistent,
    assert_stock_permitted,
    assert_tracking_consistent,
    check_digit,
    convert,
    derive_variant_sku,
    validate_barcode,
    validate_sku,
    variant_label,
)
from nickland.domain.catalogue import BundleComponent as Component
from nickland.domain.errors import ConflictError, ValidationError


class TestItemClass:
    """PRD 6.1: a non-stock item requires no stock balance anywhere in the system."""

    def test_only_a_stocked_item_holds_stock(self) -> None:
        assert ItemClass.STOCKED.holds_stock
        assert not ItemClass.NON_STOCK.holds_stock
        assert not ItemClass.SERVICE.holds_stock
        assert not ItemClass.BUNDLE.holds_stock

    @pytest.mark.parametrize(
        "item_class", [ItemClass.NON_STOCK, ItemClass.SERVICE, ItemClass.BUNDLE]
    )
    def test_stock_is_refused_for_a_non_stock_class(self, item_class: ItemClass) -> None:
        with pytest.raises(ConflictError, match="carries no stock balance"):
            assert_stock_permitted(item_class, "SKU-1")

    def test_stock_is_permitted_for_a_stocked_item(self) -> None:
        assert_stock_permitted(ItemClass.STOCKED, "SKU-1")

    def test_only_stocked_and_non_stock_items_are_procured(self) -> None:
        assert ItemClass.STOCKED.is_procured
        assert ItemClass.NON_STOCK.is_procured
        assert not ItemClass.SERVICE.is_procured

    @pytest.mark.parametrize("tracking", [TrackingPolicy.BATCH, TrackingPolicy.SERIAL])
    def test_tracking_requires_stock(self, tracking: TrackingPolicy) -> None:
        with pytest.raises(ValidationError, match="holds no stock"):
            assert_tracking_consistent(ItemClass.SERVICE, tracking)
        assert_tracking_consistent(ItemClass.STOCKED, tracking)

    def test_expiry_requires_stock(self) -> None:
        with pytest.raises(ValidationError, match="holds no stock"):
            assert_expiry_consistent(ItemClass.NON_STOCK, True)
        assert_expiry_consistent(ItemClass.STOCKED, True)
        assert_expiry_consistent(ItemClass.SERVICE, False)


class TestSku:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (" med-glove 100 ", "MED-GLOVE-100"),
            ("abc", "ABC"),
            ("a.b/c_d", "A.B/C_D"),
        ],
    )
    def test_normalisation(self, raw: str, expected: str) -> None:
        assert validate_sku(raw) == expected

    @pytest.mark.parametrize("raw", ["ab", "-abc", "abc-", "a b!", "x" * 50, ""])
    def test_rejection(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            validate_sku(raw)


class TestBarcode:
    """A mistyped code is rejected at entry rather than discovered at the till."""

    def test_check_digit_follows_gs1(self) -> None:
        assert check_digit("501234567890") == 0
        assert check_digit("00012345678") == 4

    @pytest.mark.parametrize(
        ("value", "kind"),
        [
            ("5012345678900", BarcodeKind.EAN_13),
            ("96385074", BarcodeKind.EAN_8),
            ("036000291452", BarcodeKind.UPC_A),
        ],
    )
    def test_valid_codes_are_accepted(self, value: str, kind: BarcodeKind) -> None:
        assert validate_barcode(value, kind) == value

    def test_a_wrong_check_digit_is_refused_and_the_right_one_named(self) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_barcode("5012345678901", BarcodeKind.EAN_13)
        assert "should be 0" in str(caught.value)

    def test_the_wrong_length_is_refused_with_both_lengths(self) -> None:
        with pytest.raises(ValidationError, match="13 digits; this one is 11"):
            validate_barcode("50123456789", BarcodeKind.EAN_13)

    def test_letters_are_refused_in_a_numeric_symbology(self) -> None:
        with pytest.raises(ValidationError, match="digits only"):
            validate_barcode("ABCDEFGHIJKLM", BarcodeKind.EAN_13)

    def test_spaces_and_hyphens_are_stripped(self) -> None:
        assert validate_barcode("501 234-567 8900", BarcodeKind.EAN_13) == "5012345678900"

    @pytest.mark.parametrize(
        "kind", [BarcodeKind.CODE_128, BarcodeKind.CODE_39, BarcodeKind.INTERNAL]
    )
    def test_a_free_form_symbology_retains_its_hyphens(self, kind: BarcodeKind) -> None:
        """A hyphen is part of a CODE-128 or CODE-39 code, not formatting."""
        assert validate_barcode("  lab-ref-7 ", kind) == "LAB-REF-7"

    def test_an_empty_barcode_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="required"):
            validate_barcode("   ", BarcodeKind.INTERNAL)


class TestConversion:
    def test_a_box_of_twenty_four_converts_to_each(self) -> None:
        assert convert(Decimal("2"), from_factor=Decimal("24"), to_factor=Decimal("1")) == (
            Decimal("48.000000")
        )

    def test_each_converts_back_to_boxes(self) -> None:
        assert convert(Decimal("48"), from_factor=Decimal("1"), to_factor=Decimal("24")) == (
            Decimal("2.000000")
        )

    def test_conversion_is_reversible(self) -> None:
        there = convert(Decimal("7"), from_factor=Decimal("12"), to_factor=Decimal("1"))
        back = convert(there, from_factor=Decimal("1"), to_factor=Decimal("12"))
        assert back == Decimal("7.000000")

    @pytest.mark.parametrize(("source", "target"), [("0", "1"), ("1", "0"), ("-1", "1")])
    def test_a_non_positive_factor_is_refused(self, source: str, target: str) -> None:
        with pytest.raises(ValidationError, match="greater than zero"):
            convert(Decimal("1"), from_factor=Decimal(source), to_factor=Decimal(target))


class TestBundle:
    def test_a_valid_bundle_is_accepted(self) -> None:
        assert_bundle_valid(
            [
                Component(product_id="a", quantity=Decimal("2"), unit_code="ea"),
                Component(product_id="b", quantity=Decimal("1"), unit_code="ea"),
            ],
            own_id="kit",
        )

    def test_an_empty_bundle_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="at least one component"):
            assert_bundle_valid([], own_id="kit")

    def test_a_bundle_cannot_contain_itself(self) -> None:
        with pytest.raises(ValidationError, match="cannot contain itself"):
            assert_bundle_valid(
                [Component(product_id="kit", quantity=Decimal("1"), unit_code="ea")],
                own_id="kit",
            )

    def test_a_repeated_component_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="more than once"):
            assert_bundle_valid(
                [
                    Component(product_id="a", quantity=Decimal("1"), unit_code="ea"),
                    Component(product_id="a", quantity=Decimal("2"), unit_code="ea"),
                ],
                own_id="kit",
            )

    def test_a_non_positive_quantity_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="greater than zero"):
            assert_bundle_valid(
                [Component(product_id="a", quantity=Decimal("0"), unit_code="ea")],
                own_id="kit",
            )


class TestVariants:
    def test_label_is_ordered_and_readable(self) -> None:
        assert variant_label({"size": "Large", "colour": "Blue"}) == "Blue · Large"

    def test_sku_is_derived_from_parent_and_values(self) -> None:
        assert derive_variant_sku("GLOVE", {"size": "Large", "colour": "Blue"}) == (
            "GLOVE-BLUE-LARGE"
        )

    def test_values_are_cleaned_for_a_sku(self) -> None:
        assert derive_variant_sku("GLOVE", {"size": "x-large (xl)"}) == "GLOVE-XLARGE"

    def test_an_empty_combination_returns_the_parent(self) -> None:
        assert derive_variant_sku("GLOVE", {}) == "GLOVE"
