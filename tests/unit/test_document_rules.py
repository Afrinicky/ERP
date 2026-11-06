"""Document composition rules in isolation. PRD 6.6, 6.7."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nickland.domain.documents import (
    Classification,
    DocumentContext,
    DocumentKind,
    Issuer,
    PageFormat,
    TemplateOptions,
    amount_in_words,
    spell_number,
    validate_document_number,
)
from nickland.domain.errors import ValidationError


class TestKinds:
    def test_every_kind_has_a_heading(self) -> None:
        for kind in DocumentKind:
            assert kind.heading
            assert kind.heading[0].isupper()

    def test_heading_does_not_shadow_the_string_method(self) -> None:
        """``title`` is ``str.title``; the document's own is ``heading``."""
        assert DocumentKind.PRO_FORMA.heading == "Pro Forma Invoice"
        assert DocumentKind.PRO_FORMA.title() == "Pro_Forma"

    @pytest.mark.parametrize(
        "kind", [DocumentKind.WAYBILL, DocumentKind.DELIVERY_NOTE, DocumentKind.PACKING_LIST]
    )
    def test_a_document_that_travels_with_the_goods_carries_no_prices(
        self, kind: DocumentKind
    ) -> None:
        """A driver and a storekeeper handle these; commercial terms are not theirs."""
        assert not kind.carries_prices

    @pytest.mark.parametrize(
        "kind", [DocumentKind.PRO_FORMA, DocumentKind.INVOICE, DocumentKind.RECEIPT]
    )
    def test_a_commercial_document_carries_prices(self, kind: DocumentKind) -> None:
        assert kind.carries_prices

    @pytest.mark.parametrize(
        "kind",
        [
            DocumentKind.PURCHASE_ORDER,
            DocumentKind.GOODS_RECEIVED_NOTE,
            DocumentKind.PAYMENT_VOUCHER,
        ],
    )
    def test_a_document_sent_to_a_supplier_is_not_outbound(self, kind: DocumentKind) -> None:
        assert not kind.is_outbound


class TestPageGeometry:
    def test_a4_is_a4(self) -> None:
        geometry = PageFormat.A4.geometry
        assert (geometry.width_mm, geometry.height_mm) == (Decimal("210"), Decimal("297"))

    @pytest.mark.parametrize("page", [PageFormat.ROLL_80, PageFormat.ROLL_58])
    def test_a_roll_has_no_fixed_height(self, page: PageFormat) -> None:
        """The paper advances until the content ends."""
        assert page.is_roll
        assert page.geometry.height_mm is None

    def test_a_roll_is_narrower_than_its_printable_width_suggests(self) -> None:
        """An 80mm roll has about 72mm of print head, not 80."""
        assert PageFormat.ROLL_80.geometry.printable_width_mm == Decimal("72")
        assert PageFormat.ROLL_58.geometry.printable_width_mm == Decimal("48")

    def test_a_sheet_is_not_a_roll(self) -> None:
        for page in (PageFormat.A4, PageFormat.A5, PageFormat.LETTER, PageFormat.LEGAL):
            assert not page.is_roll
            assert page.geometry.height_mm is not None


class TestDocumentNumber:
    @pytest.mark.parametrize("number", ["QTN9568", "INV-2026-00001", "POS/01/4471", "A.1_2"])
    def test_a_usable_number_is_accepted(self, number: str) -> None:
        assert validate_document_number(number) == number

    @pytest.mark.parametrize("number", ["", "   ", "has space", "semi;colon", "a" * 41])
    def test_a_number_that_would_not_survive_a_filename_is_refused(self, number: str) -> None:
        with pytest.raises(ValidationError):
            validate_document_number(number)

    def test_a_context_requires_a_number(self) -> None:
        with pytest.raises(ValidationError):
            DocumentContext(
                kind=DocumentKind.INVOICE,
                number="   ",
                issued_on=date(2026, 11, 6),
                issuer=Issuer(name="X"),
            )


class TestAmountInWords:
    @pytest.mark.parametrize(
        ("value", "words"),
        [
            (0, "zero"),
            (7, "seven"),
            (13, "thirteen"),
            (20, "twenty"),
            (42, "forty-two"),
            (100, "one hundred"),
            (101, "one hundred and one"),
            (999, "nine hundred and ninety-nine"),
            (1000, "one thousand"),
            (1001, "one thousand and one"),
            (10932, "ten thousand, nine hundred and thirty-two"),
            (1000000, "one million"),
        ],
    )
    def test_integers_are_spelled(self, value: int, words: str) -> None:
        assert spell_number(value) == words

    def test_an_amount_states_its_subunit_even_at_zero(self) -> None:
        """'Forty cedis' and 'forty cedis and no pesewas' are not equally safe to sign."""
        assert amount_in_words(Decimal("40.00")) == "Forty cedis and zero pesewas"

    def test_an_amount_spells_its_pesewas(self) -> None:
        assert amount_in_words(Decimal("9623.94")) == (
            "Nine thousand, six hundred and twenty-three cedis and ninety-four pesewas"
        )

    def test_a_negative_amount_says_so(self) -> None:
        assert amount_in_words(Decimal("-5.50")).startswith("Minus five cedis")

    def test_the_subunit_names_are_configurable(self) -> None:
        assert amount_in_words(Decimal("2.05"), unit="dollars", subunit="cents") == (
            "Two dollars and five cents"
        )


class TestTemplateOptions:
    def test_the_defaults_round_trip(self) -> None:
        options = TemplateOptions()
        assert TemplateOptions.from_dict(options.as_dict()) == options

    def test_a_populated_template_round_trips(self) -> None:
        options = TemplateOptions(
            page=PageFormat.ROLL_58,
            classification=Classification.INTERNAL,
            show_banking=False,
            show_batch_and_expiry=True,
            terms=(("Validity", "30 days"), ("Payment", "On receipt")),
            notes=("Goods sold are not returnable.",),
            footer_note="Thank you.",
        )
        assert TemplateOptions.from_dict(options.as_dict()) == options

    def test_a_definition_missing_a_field_takes_the_default(self) -> None:
        """A template written by an older version must still render."""
        options = TemplateOptions.from_dict({"page": "a5"})
        assert options.page is PageFormat.A5
        assert options.show_totals is True
        assert options.terms == ()

    def test_a_malformed_term_is_discarded_rather_than_crashing_the_render(self) -> None:
        options = TemplateOptions.from_dict(
            {"terms": [["Validity", "30 days"], ["only one"], "not a pair"]}
        )
        assert options.terms == (("Validity", "30 days"),)
