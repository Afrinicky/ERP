"""The template model: the palette, conditions, validation and portability."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import ClassVar

import pytest

from nickland.domain.binding import (
    format_value,
    holds,
    line_values,
)
from nickland.domain.documents import Classification, DocumentKind, Line
from nickland.domain.errors import ValidationError
from nickland.domain.templates import (
    BY_KEY,
    GROUP_LABELS,
    INTERNAL_KEYS,
    PALETTE,
    Alignment,
    Column,
    Condition,
    Element,
    ElementKind,
    FieldGroup,
    Join,
    Operator,
    PaletteField,
    Section,
    SectionKind,
    TemplateDefinition,
    TextCase,
    ValueFormat,
    ValueKind,
    Visibility,
    add_column,
    assert_valid,
    compile_expressions,
    default_definition,
    export_template,
    import_template,
    palette_for,
    remove_column,
    reorder,
    validate,
)

D = Decimal


def _items(*columns: Column) -> Section:
    return Section(id="items", kind=SectionKind.ITEMS, title="Items", columns=columns)


def _column(identifier: str, binding: str, width: int = 10, **extra: object) -> Column:
    return Column(
        id=identifier, label=BY_KEY[binding].label, binding=binding, width_percent=width, **extra
    )  # type: ignore[arg-type]


def _definition(*sections: Section) -> TemplateDefinition:
    return TemplateDefinition(
        sections=sections or (_items(_column("c1", "line.description", 100)),)
    )


# --------------------------------------------------------------------------------------


class TestPalette:
    def test_every_group_the_requirement_names_is_present(self) -> None:
        present = {item.group for item in PALETTE}
        # Custom is the group a document type's own fields land in, so it is filled at
        # run time by palette_for rather than shipped in the static palette.
        assert present == set(FieldGroup) - {FieldGroup.CUSTOM}
        assert all(GROUP_LABELS[group] for group in FieldGroup)

    def test_keys_are_unique_and_well_formed(self) -> None:
        keys = [item.key for item in PALETTE]
        assert len(keys) == len(set(keys))
        assert all("." in key and key.islower() for key in keys)

    def test_a_customer_facing_template_is_not_offered_internal_fields(self) -> None:
        offered = {item.key for item in palette_for(Classification.CUSTOMER_FACING)}
        assert offered.isdisjoint(INTERNAL_KEYS)
        internal = {item.key for item in palette_for(Classification.INTERNAL)}
        assert internal >= INTERNAL_KEYS

    def test_the_internal_group_is_what_the_confidentiality_table_names(self) -> None:
        labels = {BY_KEY[key].label for key in INTERNAL_KEYS}
        assert {"Supplier cost", "Margin", "Affiliate commission"} <= labels

    def test_a_document_types_own_field_joins_the_custom_group(self) -> None:
        own = PaletteField(key="custom.inspector", label="Inspector", group=FieldGroup.CUSTOM)
        offered = palette_for(Classification.CUSTOMER_FACING, custom=[own])
        assert own in offered
        assert FieldGroup.CUSTOM in {item.group for item in offered}

    def test_every_per_line_field_belongs_to_a_line(self) -> None:
        for item in PALETTE:
            if item.per_line:
                assert item.key.startswith(("line.", "internal.line_"))

    def test_a_count_is_not_a_quantity(self) -> None:
        assert BY_KEY["line.number"].kind is ValueKind.COUNT
        assert BY_KEY["line.quantity"].kind is ValueKind.QUANTITY


class TestConditions:
    VALUES: ClassVar[dict[str, object]] = {
        "line.manufacturer": "Sysmex",
        "line.batch": None,
        "totals.grand_total": D("1380"),
        "payment.plan": "instalment",
    }

    @pytest.mark.parametrize(
        ("field", "operator", "value", "expected"),
        [
            ("line.manufacturer", Operator.IS_NOT_EMPTY, None, True),
            ("line.batch", Operator.IS_NOT_EMPTY, None, False),
            ("line.batch", Operator.IS_EMPTY, None, True),
            ("payment.plan", Operator.EQUALS, "instalment", True),
            ("payment.plan", Operator.EQUALS, "outright", False),
            ("payment.plan", Operator.NOT_EQUALS, "outright", True),
            ("totals.grand_total", Operator.GREATER_THAN, "1000", True),
            ("totals.grand_total", Operator.LESS_THAN, "1000", False),
        ],
    )
    def test_each_operator(
        self, field: str, operator: Operator, value: str | None, expected: bool
    ) -> None:
        visibility = Visibility(
            mode="conditional",
            conditions=(Condition(field_key=field, operator=operator, value=value),),
        )
        assert holds(visibility, self.VALUES) is expected

    def test_in_and_not_in(self) -> None:
        inside = Visibility(
            mode="conditional",
            conditions=(
                Condition(
                    field_key="payment.plan",
                    operator=Operator.IN,
                    values=("instalment", "deferred"),
                ),
            ),
        )
        assert holds(inside, self.VALUES) is True
        assert (
            holds(
                replace(
                    inside, conditions=(replace(inside.conditions[0], operator=Operator.NOT_IN),)
                ),
                self.VALUES,
            )
            is False
        )

    def test_all_and_any(self) -> None:
        both = (
            Condition(field_key="line.manufacturer", operator=Operator.IS_NOT_EMPTY),
            Condition(field_key="line.batch", operator=Operator.IS_NOT_EMPTY),
        )
        assert (
            holds(Visibility(mode="conditional", join=Join.ALL, conditions=both), self.VALUES)
            is False
        )
        assert (
            holds(Visibility(mode="conditional", join=Join.ANY, conditions=both), self.VALUES)
            is True
        )

    def test_always_and_never(self) -> None:
        assert holds(Visibility(), self.VALUES) is True
        assert holds(Visibility(mode="never"), self.VALUES) is False

    def test_a_comparison_against_text_simply_does_not_hold(self) -> None:
        visibility = Visibility(
            mode="conditional",
            conditions=(
                Condition(field_key="payment.plan", operator=Operator.GREATER_THAN, value="5"),
            ),
        )
        assert holds(visibility, self.VALUES) is False


class TestFormatting:
    def test_money(self) -> None:
        assert (
            format_value(
                D("1234.5"),
                ValueFormat(precision=2, currency_symbol=True),
                kind=ValueKind.MONEY,
                currency_symbol="GH₵",
            )
            == "GH₵1\u00a0234.50"
        )

    def test_a_quantity_keeps_only_the_places_it_needs(self) -> None:
        assert format_value(D("2"), ValueFormat(), kind=ValueKind.QUANTITY) == "2"
        assert format_value(D("2.50"), ValueFormat(), kind=ValueKind.QUANTITY) == "2.5"

    def test_a_count_is_whole(self) -> None:
        assert format_value(D("3"), ValueFormat(precision=0), kind=ValueKind.COUNT) == "3"

    def test_a_rate_is_shown_as_a_percentage(self) -> None:
        assert (
            format_value(D("0.15"), ValueFormat(precision=2, suffix="%"), kind=ValueKind.RATE)
            == "15.00%"
        )

    @pytest.mark.parametrize(
        ("style", "expected"),
        [("long", "06 Nov 2026"), ("short", "06/11/2026"), ("iso", "2026-11-06")],
    )
    def test_dates(self, style: str, expected: str) -> None:
        assert format_value(date(2026, 11, 6), ValueFormat(date_format=style)) == expected

    def test_text_case_and_affixes(self) -> None:
        assert format_value("accra", ValueFormat(text_case=TextCase.UPPER)) == "ACCRA"
        assert format_value("x", ValueFormat(prefix="[", suffix="]")) == "[x]"

    def test_an_absent_value_prints_nothing(self) -> None:
        assert format_value(None, ValueFormat()) == ""


class TestEditing:
    def test_reorder(self) -> None:
        columns = (
            _column("a", "line.sku"),
            _column("b", "line.description"),
            _column("c", "line.quantity"),
        )
        assert [item.id for item in reorder(columns, "c", -2)] == ["c", "a", "b"]
        assert [item.id for item in reorder(columns, "a", 1)] == ["b", "a", "c"]

    def test_reorder_past_the_end_is_a_no_op(self) -> None:
        columns = (_column("a", "line.sku"), _column("b", "line.description"))
        assert [item.id for item in reorder(columns, "a", -5)] == ["a", "b"]

    def test_reorder_of_something_absent_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not in this template"):
            reorder((_column("a", "line.sku"),), "zz", 1)

    def test_add_derives_a_label_alignment_and_format(self) -> None:
        section = add_column(_items(), BY_KEY["line.total"], identifier="new")
        column = section.columns[0]
        assert column.label == "Line total"
        assert column.alignment is Alignment.RIGHT
        assert column.format.currency_symbol
        assert column.subtotal

    def test_a_field_that_does_not_vary_by_line_cannot_be_a_column(self) -> None:
        with pytest.raises(ValidationError, match="same on every line"):
            add_column(_items(), BY_KEY["business.name"], identifier="new")

    def test_remove_redistributes_and_caps_the_width(self) -> None:
        section = _items(
            _column("a", "line.sku", 50),
            _column("b", "line.description", 30),
            _column("c", "line.quantity", 40),
        )
        after = remove_column(section, "a")
        assert sum(item.width_percent for item in after.columns) == 100

    def test_remove_keeps_a_deliberate_total_below_a_hundred(self) -> None:
        section = _items(_column("a", "line.sku", 30), _column("b", "line.description", 30))
        after = remove_column(section, "a")
        assert sum(item.width_percent for item in after.columns) == 60

    def test_removing_the_last_column_leaves_none(self) -> None:
        assert remove_column(_items(_column("a", "line.sku")), "a").columns == ()


class TestValidation:
    def test_a_well_formed_template_has_no_findings(self) -> None:
        assert (
            validate(
                default_definition(DocumentKind.PRO_FORMA, priced=True),
                classification=Classification.CUSTOMER_FACING,
            )
            == []
        )

    def test_an_internal_binding_on_a_customer_facing_template(self) -> None:
        definition = _definition(_items(_column("c1", "internal.line_cost", 100)))
        findings = validate(definition, classification=Classification.CUSTOMER_FACING)
        assert any("internal figure" in item.message for item in findings)
        assert validate(definition, classification=Classification.INTERNAL) == []

    def test_an_internal_field_inside_an_expression(self) -> None:
        definition = _definition(
            _items(_column("c1", "line.description", 100)),
            Section(
                id="t",
                kind=SectionKind.TOTALS,
                elements=(
                    Element(
                        id="e",
                        kind=ElementKind.CALCULATED,
                        label="Margin",
                        expression="[totals.grand_total] - [internal.landed_cost]",
                    ),
                ),
            ),
        )
        findings = validate(definition, classification=Classification.CUSTOMER_FACING)
        assert any("internal figure" in item.message for item in findings)

    def test_an_unparseable_expression(self) -> None:
        definition = _definition(
            _items(Column(id="c1", label="X", expression="1 +", width_percent=100))
        )
        findings = validate(definition, classification=Classification.CUSTOMER_FACING)
        assert any("Unexpected" in item.message for item in findings)

    def test_a_column_with_neither_binding_nor_formula(self) -> None:
        definition = _definition(_items(Column(id="c1", label="X", width_percent=100)))
        findings = validate(definition, classification=Classification.CUSTOMER_FACING)
        assert any("binds to a field or carries a formula" in item.message for item in findings)

    def test_a_column_needs_a_heading(self) -> None:
        definition = _definition(
            _items(Column(id="c1", label="  ", binding="line.sku", width_percent=100))
        )
        findings = validate(definition, classification=Classification.CUSTOMER_FACING)
        assert any("needs a heading" in item.message for item in findings)

    def test_an_overfull_table_is_advisory_not_blocking(self) -> None:
        definition = _definition(
            _items(_column("a", "line.sku", 70), _column("b", "line.description", 70))
        )
        findings = validate(definition, classification=Classification.CUSTOMER_FACING)
        over = [item for item in findings if "per cent" in item.message]
        assert over and not any(item.is_blocking for item in over)

    def test_too_many_conditions(self) -> None:
        visibility = Visibility(
            mode="conditional",
            conditions=tuple(
                Condition(field_key="line.sku", operator=Operator.IS_NOT_EMPTY) for _ in range(4)
            ),
        )
        definition = _definition(_items(_column("c1", "line.sku", 100, visibility=visibility)))
        findings = validate(definition, classification=Classification.CUSTOMER_FACING)
        assert any("at most 3 conditions" in item.message for item in findings)

    def test_duplicate_section_identifiers(self) -> None:
        definition = TemplateDefinition(
            sections=(
                Section(id="x", kind=SectionKind.MASTHEAD),
                Section(id="x", kind=SectionKind.NOTES),
            )
        )
        findings = validate(definition, classification=Classification.CUSTOMER_FACING)
        assert any("share the identifier" in item.message for item in findings)

    def test_an_empty_template(self) -> None:
        findings = validate(TemplateDefinition(), classification=Classification.CUSTOMER_FACING)
        assert any("at least one section" in item.message for item in findings)

    def test_assert_valid_names_everything_wrong(self) -> None:
        definition = _definition(_items(_column("c1", "internal.margin", 100)))
        with pytest.raises(ValidationError) as caught:
            assert_valid(definition, classification=Classification.CUSTOMER_FACING)
        assert "internal figure" in caught.value.message
        assert caught.value.context["findings"]

    def test_assert_valid_passes_a_sound_template(self) -> None:
        assert_valid(
            default_definition(DocumentKind.INVOICE, priced=True),
            classification=Classification.CUSTOMER_FACING,
        )


class TestShippedDefaults:
    @pytest.mark.parametrize("kind", list(DocumentKind))
    def test_every_kind_composes_and_validates(self, kind: DocumentKind) -> None:
        definition = default_definition(kind, priced=kind.prices_by_default)
        assert definition.sections
        assert validate(definition, classification=Classification.CUSTOMER_FACING) == []

    def test_an_unpriced_kind_carries_no_money_column(self) -> None:
        definition = default_definition(DocumentKind.WAYBILL, priced=False)
        bindings = {column.binding for column in definition.section(SectionKind.ITEMS).columns}
        assert "line.unit_price" not in bindings
        assert "line.total" not in bindings

    def test_the_conditions_the_requirement_names_by_example(self) -> None:
        """Manufacturer and batch appear only where populated; the plan only on one."""
        definition = default_definition(DocumentKind.PRO_FORMA, priced=True)
        items = definition.section(SectionKind.ITEMS)
        conditional = {
            column.binding for column in items.columns if column.visibility.is_conditional
        }
        assert {"line.manufacturer", "line.batch"} <= conditional
        plan = definition.section(SectionKind.PAYMENT_PLAN)
        assert plan is not None and plan.visibility.is_conditional

    def test_every_expression_compiles(self) -> None:
        for kind in DocumentKind:
            compile_expressions(default_definition(kind, priced=kind.prices_by_default))


class TestPortability:
    def test_an_export_carries_structure_and_no_business_data(self) -> None:
        definition = default_definition(DocumentKind.PRO_FORMA, priced=True)
        portable = export_template(
            definition,
            kind=DocumentKind.PRO_FORMA,
            classification=Classification.CUSTOMER_FACING,
            name="House pro forma",
        )
        text = json.dumps(portable.as_dict())
        assert "line.description" in text
        for business_data in ("Banhart", "Nickland", "9300", "GH₵"):
            assert business_data not in text

    def test_a_round_trip_preserves_the_template(self) -> None:
        definition = default_definition(DocumentKind.INVOICE, priced=True)
        portable = export_template(
            definition,
            kind=DocumentKind.INVOICE,
            classification=Classification.CUSTOMER_FACING,
            name="Invoice",
        )
        report = import_template(json.loads(json.dumps(portable.as_dict())))
        assert report.is_acceptable
        assert report.definition.as_dict() == definition.as_dict()

    def test_an_unresolved_binding_is_reported_before_acceptance(self) -> None:
        definition = _definition(
            _items(
                Column(
                    id="c1",
                    label="Colour",
                    binding="line.colour",
                    width_percent=100,
                )
            )
        )
        portable = export_template(
            definition,
            kind=DocumentKind.INVOICE,
            classification=Classification.CUSTOMER_FACING,
            name="X",
        )
        report = import_template(portable.as_dict())
        assert report.unresolved == ("line.colour",)
        assert not report.is_acceptable

    def test_a_file_in_another_format_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="template format"):
            import_template({"format": 99, "definition": {}})

    def test_a_file_with_no_definition_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="no template definition"):
            import_template({"format": 1})


class TestBindings:
    def test_a_line_resolves_every_per_line_key(self) -> None:
        from nickland.domain.documents import DocumentContext, Issuer

        context = DocumentContext(
            kind=DocumentKind.PRO_FORMA,
            number="X-1",
            issued_on=date(2026, 11, 6),
            issuer=Issuer(name="N"),
        )
        line = Line(sku="A", name="B", quantity=D("2"), unit_price=D("10"), line_total=D("20"))
        values = line_values(line, 1, context)
        per_line = {item.key for item in PALETTE if item.per_line and not item.is_internal}
        assert per_line <= set(values)

    def test_the_definition_reports_every_key_it_reads(self) -> None:
        definition = _definition(
            _items(
                _column("c1", "line.description", 50),
                Column(id="c2", label="Net", expression="sum([line.total])", width_percent=50),
            ),
        )
        assert {"line.description", "line.total"} <= definition.bindings()
