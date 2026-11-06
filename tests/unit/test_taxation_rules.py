"""Taxation rules in isolation: calculation, conditions, dates. PRD 19, 24."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.taxation import (
    MAXIMUM_CONDITIONS,
    Assessment,
    Band,
    CalculationMethod,
    Condition,
    ConditionField,
    ConditionOperator,
    DueDateRule,
    FilingFrequency,
    LineAssessment,
    Outcome,
    TaxCategory,
    TaxTreatment,
    assert_bands_valid,
    assert_conditions_valid,
    assert_prospective,
    assert_ready_to_post,
    calculate,
    cascade_base,
    due_date,
    period_end,
    validate_authority_identifier,
    validate_rate,
)


class TestRates:
    @pytest.mark.parametrize("rate", ["0", "0.025", "0.15", "1"])
    def test_a_fraction_is_accepted(self, rate: str) -> None:
        assert validate_rate(Decimal(rate)) == Decimal(rate)

    def test_a_percentage_entered_as_a_whole_number_is_refused(self) -> None:
        """Entering 15 for fifteen per cent would charge fifteen times the amount."""
        with pytest.raises(ValidationError, match="fraction"):
            validate_rate(Decimal("15"))

    def test_a_negative_rate_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            validate_rate(Decimal("-0.1"))


class TestCalculation:
    def test_a_percentage_of_the_base(self) -> None:
        charge = calculate(CalculationMethod.PERCENTAGE, base=Decimal("1000"), rate=Decimal("0.15"))
        assert charge.amount == Decimal("150")
        assert charge.base == Decimal("1000")

    def test_an_inclusive_price_is_extracted_rather_than_increased(self) -> None:
        charge = calculate(
            CalculationMethod.PERCENTAGE,
            base=Decimal("1150"),
            rate=Decimal("0.15"),
            treatment=TaxTreatment.INCLUSIVE,
        )
        assert charge.base == Decimal("1000")
        assert charge.amount == Decimal("150")
        assert charge.base + charge.amount == Decimal("1150")

    def test_a_fixed_amount_ignores_the_base(self) -> None:
        charge = calculate(
            CalculationMethod.FIXED_AMOUNT, base=Decimal("9999"), fixed_amount=Decimal("20")
        )
        assert charge.amount == Decimal("20")

    def test_a_per_unit_charge_multiplies_by_the_quantity(self) -> None:
        charge = calculate(
            CalculationMethod.PER_UNIT,
            base=Decimal("500"),
            fixed_amount=Decimal("2.50"),
            quantity=Decimal("12"),
        )
        assert charge.amount == Decimal("30")

    def test_a_threshold_charges_nothing_below_the_floor(self) -> None:
        charge = calculate(
            CalculationMethod.THRESHOLD,
            base=Decimal("400"),
            rate=Decimal("0.10"),
            threshold=Decimal("500"),
        )
        assert charge.amount == Decimal("0")

    def test_a_threshold_charges_the_excess(self) -> None:
        charge = calculate(
            CalculationMethod.THRESHOLD,
            base=Decimal("1500"),
            rate=Decimal("0.10"),
            threshold=Decimal("500"),
        )
        assert charge.amount == Decimal("100")

    def test_a_threshold_may_charge_the_whole_base_once_crossed(self) -> None:
        charge = calculate(
            CalculationMethod.THRESHOLD,
            base=Decimal("1500"),
            rate=Decimal("0.10"),
            threshold=Decimal("500"),
            threshold_applies_to_excess=False,
        )
        assert charge.amount == Decimal("150")

    def test_tiered_bands_each_charge_their_own_part(self) -> None:
        bands = [
            Band(Decimal("1000"), Decimal("0.05")),
            Band(Decimal("5000"), Decimal("0.10")),
            Band(None, Decimal("0.20")),
        ]
        charge = calculate(CalculationMethod.TIERED, base=Decimal("7000"), bands=bands)
        assert charge.amount == Decimal("850")  # 50 + 400 + 400

    def test_a_tiered_base_inside_the_first_band(self) -> None:
        bands = [Band(Decimal("1000"), Decimal("0.05")), Band(None, Decimal("0.20"))]
        assert calculate(
            CalculationMethod.TIERED, base=Decimal("600"), bands=bands
        ).amount == Decimal("30")

    def test_exempt_charges_nothing(self) -> None:
        charge = calculate(
            CalculationMethod.PERCENTAGE,
            base=Decimal("1000"),
            rate=Decimal("0.15"),
            treatment=TaxTreatment.EXEMPT,
        )
        assert charge.amount == Decimal("0")

    def test_zero_rated_charges_nothing_but_is_not_exempt(self) -> None:
        """Zero-rated is reported; exempt is relieved. The distinction is the point."""
        assert TaxTreatment.ZERO_RATED.is_charged
        assert not TaxTreatment.EXEMPT.is_charged

    def test_a_negative_base_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            calculate(CalculationMethod.PERCENTAGE, base=Decimal("-1"), rate=Decimal("0.15"))

    def test_the_ghana_stack_charges_vat_on_the_levies(self) -> None:
        """NHIL, GETFund and COVID on the net; VAT on the net plus those."""
        net = Decimal("1000")
        levies = [
            calculate(CalculationMethod.PERCENTAGE, base=net, rate=Decimal(rate)).amount
            for rate in ("0.025", "0.025", "0.01")
        ]
        assert sum(levies) == Decimal("60")
        base = cascade_base(net, levies)
        assert base == Decimal("1060")
        vat = calculate(CalculationMethod.PERCENTAGE, base=base, rate=Decimal("0.15"))
        assert vat.amount == Decimal("159")
        assert net + sum(levies) + vat.amount == Decimal("1219")


class TestBands:
    def test_the_final_band_must_be_open(self) -> None:
        with pytest.raises(ValidationError, match="open-ended"):
            assert_bands_valid([Band(Decimal("1000"), Decimal("0.05"))])

    def test_bands_must_ascend(self) -> None:
        with pytest.raises(ValidationError, match="not above"):
            assert_bands_valid(
                [
                    Band(Decimal("5000"), Decimal("0.05")),
                    Band(Decimal("1000"), Decimal("0.10")),
                    Band(None, Decimal("0.20")),
                ]
            )

    def test_only_the_last_band_may_be_open(self) -> None:
        with pytest.raises(ValidationError, match="final band"):
            assert_bands_valid([Band(None, Decimal("0.05")), Band(None, Decimal("0.10"))])

    def test_no_bands_at_all(self) -> None:
        with pytest.raises(ValidationError):
            assert_bands_valid([])


class TestConditions:
    def test_equals_compares_without_case_or_spacing(self) -> None:
        condition = Condition(
            ConditionField.PRODUCT_TAX_CODE, ConditionOperator.EQUALS, value="std"
        )
        assert condition.holds({"product_tax_code": " STD "})

    def test_in_matches_any(self) -> None:
        condition = Condition(
            ConditionField.ITEM_CLASS, ConditionOperator.IN, values=("service", "bundle")
        )
        assert condition.holds({"item_class": "service"})
        assert not condition.holds({"item_class": "stocked"})

    def test_is_empty_is_true_for_an_absent_field(self) -> None:
        condition = Condition(ConditionField.PRODUCT_TAX_CODE, ConditionOperator.IS_EMPTY)
        assert condition.holds({})

    def test_equals_is_false_for_an_absent_field(self) -> None:
        condition = Condition(
            ConditionField.PRODUCT_TAX_CODE, ConditionOperator.EQUALS, value="STD"
        )
        assert not condition.holds({})

    def test_a_numeric_comparison(self) -> None:
        condition = Condition(
            ConditionField.LINE_TOTAL, ConditionOperator.GREATER_THAN, value="1000"
        )
        assert condition.holds({"line_total": "2000"})
        assert not condition.holds({"line_total": "500"})

    def test_a_numeric_comparison_against_text_is_simply_false(self) -> None:
        condition = Condition(
            ConditionField.LINE_TOTAL, ConditionOperator.GREATER_THAN, value="1000"
        )
        assert not condition.holds({"line_total": "not a number"})

    def test_an_operator_that_needs_a_value_demands_one(self) -> None:
        with pytest.raises(ValidationError, match="needs a value"):
            Condition(ConditionField.PRODUCT_TAX_CODE, ConditionOperator.EQUALS)

    def test_in_demands_at_least_one_value(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            Condition(ConditionField.ITEM_CLASS, ConditionOperator.IN)

    def test_too_many_conditions_are_refused(self) -> None:
        many = [
            Condition(field, ConditionOperator.IS_NOT_EMPTY)
            for field in list(ConditionField)[: MAXIMUM_CONDITIONS + 1]
        ]
        with pytest.raises(ValidationError, match="at most"):
            assert_conditions_valid(many)

    def test_the_same_test_twice_is_refused(self) -> None:
        """One of the two has no effect, which is worth saying rather than ignoring."""
        duplicated = [
            Condition(ConditionField.ITEM_CLASS, ConditionOperator.EQUALS, value="service"),
            Condition(ConditionField.ITEM_CLASS, ConditionOperator.EQUALS, value="stocked"),
        ]
        with pytest.raises(ValidationError, match="twice"):
            assert_conditions_valid(duplicated)


class TestDueDates:
    def test_a_monthly_period_ends_on_the_last_day(self) -> None:
        assert period_end(FilingFrequency.MONTHLY, date(2026, 11, 1)) == date(2026, 11, 30)
        assert period_end(FilingFrequency.MONTHLY, date(2028, 2, 1)) == date(2028, 2, 29)

    def test_a_quarter_spans_three_months(self) -> None:
        assert period_end(FilingFrequency.QUARTERLY, date(2026, 10, 1)) == date(2026, 12, 31)

    def test_a_year(self) -> None:
        assert period_end(FilingFrequency.ANNUAL, date(2026, 1, 1)) == date(2026, 12, 31)

    def test_days_after_the_period(self) -> None:
        assert due_date(
            DueDateRule.DAYS_AFTER_PERIOD, period_ends=date(2026, 11, 30), days=15
        ) == date(2026, 12, 15)

    def test_a_nominated_day_of_the_following_month(self) -> None:
        assert due_date(
            DueDateRule.DAY_OF_NEXT_MONTH, period_ends=date(2026, 11, 30), day_of_month=20
        ) == date(2026, 12, 20)

    def test_the_end_of_the_following_month(self) -> None:
        assert due_date(DueDateRule.END_OF_NEXT_MONTH, period_ends=date(2026, 11, 30)) == date(
            2026, 12, 31
        )

    def test_a_december_period_rolls_into_the_next_year(self) -> None:
        assert due_date(
            DueDateRule.DAY_OF_NEXT_MONTH, period_ends=date(2026, 12, 31), day_of_month=15
        ) == date(2027, 1, 15)

    def test_a_nominated_day_beyond_the_28th_is_refused(self) -> None:
        """Every month has a 28th; not every month has a 31st."""
        with pytest.raises(ValidationError, match="between 1 and 28"):
            due_date(DueDateRule.DAY_OF_NEXT_MONTH, period_ends=date(2026, 11, 30), day_of_month=31)


class TestEffectiveDating:
    def test_a_future_date_is_always_allowed(self) -> None:
        assert_prospective(date(2027, 1, 1), today=date(2026, 11, 6), posted_exists=True)

    def test_a_past_date_is_allowed_before_anything_is_posted(self) -> None:
        """Configuring last year's rate during setup is legitimate."""
        assert_prospective(date(2026, 1, 1), today=date(2026, 11, 6), posted_exists=False)

    def test_a_past_date_is_refused_once_the_rule_has_been_used(self) -> None:
        with pytest.raises(ConflictError, match="past"):
            assert_prospective(date(2026, 1, 1), today=date(2026, 11, 6), posted_exists=True)


class TestReadiness:
    def _assessment(self, outcome: Outcome) -> Assessment:
        return Assessment(
            rule_code="vat",
            rule_name="Value Added Tax",
            category=TaxCategory.VALUE_ADDED,
            outcome=outcome,
            treatment=TaxTreatment.EXCLUSIVE,
            method=CalculationMethod.PERCENTAGE,
            base=Decimal("0"),
            rate=Decimal("0"),
            amount=Decimal("0"),
            detail="Something is unresolved.",
        )

    def test_a_settled_transaction_may_post(self) -> None:
        line = LineAssessment("L1", Decimal("100"), [self._assessment(Outcome.APPLIED)])
        assert_ready_to_post([line])

    def test_an_unresolved_decision_blocks_posting(self) -> None:
        line = LineAssessment("L1", Decimal("100"), [self._assessment(Outcome.NEEDS_REVIEW)])
        with pytest.raises(ConflictError, match="unresolved"):
            assert_ready_to_post([line])

    def test_an_authorised_exception_lets_it_through(self) -> None:
        line = LineAssessment("L1", Decimal("100"), [self._assessment(Outcome.NEEDS_REVIEW)])
        assert_ready_to_post([line], exception_reason="Approved by the proprietor")

    def test_a_blank_exception_does_not_count(self) -> None:
        line = LineAssessment("L1", Decimal("100"), [self._assessment(Outcome.NEEDS_REVIEW)])
        with pytest.raises(ConflictError):
            assert_ready_to_post([line], exception_reason="   ")


class TestIdentifiers:
    @pytest.mark.parametrize("value", ["C0003086224", "GRA/VAT/2026-01", "A.1_2"])
    def test_a_usable_identifier(self, value: str) -> None:
        assert validate_authority_identifier(value) == value

    @pytest.mark.parametrize("value", ["", "   ", "has;semicolon", "x" * 61])
    def test_an_unusable_identifier(self, value: str) -> None:
        with pytest.raises(ValidationError):
            validate_authority_identifier(value)

    def test_internal_spacing_is_collapsed(self) -> None:
        assert validate_authority_identifier("GRA   VAT  01") == "GRA VAT 01"
