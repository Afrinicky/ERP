"""The pricing engine.

The twelve obligations of the pricing engine specification, which PRD §9.8 makes
mandatory at Phase 5 exit, are each covered by a class here. The engine is a pure
function, so every one of these runs without a database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import ClassVar

import pytest

from nickland.domain.errors import StateTransitionError, ValidationError
from nickland.domain.money import ZERO, money, present
from nickland.domain.pricing import (
    QUOTATION_TRANSITIONS,
    AllocationBasis,
    AllocationMethod,
    CommissionMethod,
    CommissionScope,
    CommissionScopeTest,
    CommissionState,
    CostVariance,
    DiscountLimit,
    DiscountMethod,
    DocumentInputs,
    DurationBand,
    ExpenseInputs,
    InstalmentFrequency,
    InstalmentTerms,
    LineInputs,
    MarginMethod,
    PaymentPlan,
    QuotationState,
    UpliftMethod,
    add_months,
    allocate_expense,
    apply_commission,
    apply_discount,
    apply_margin,
    apply_uplift,
    assert_amendable,
    assert_commission_transition,
    assert_transition,
    band_for,
    commission_entitlement,
    commission_in_scope,
    customer_facing,
    discount_needs_approval,
    fraction,
    instalment_count,
    instalment_schedule,
    price_document,
    price_line,
    realised_margin_rate,
)

D = Decimal


def _bases(*rows: tuple[str, str, str, str, str]) -> list[AllocationBasis]:
    """Reference, quantity, value, weight, volume."""
    return [
        AllocationBasis(
            reference=reference,
            quantity=D(quantity),
            value=D(value),
            weight=D(weight),
            volume=D(volume),
        )
        for reference, quantity, value, weight, volume in rows
    ]


# -- 1. The seven allocation methods, including residue assignment ----------------------


class TestAllocation:
    BASES: ClassVar[list[AllocationBasis]] = _bases(
        ("L1", "10", "1140", "180", "2.1"),
        ("L2", "3", "150", "6", "0.03"),
        ("L3", "2", "710", "14", "0.4"),
    )

    def test_by_quantity(self) -> None:
        parts = allocate_expense(
            D("300"),
            AllocationMethod.BY_QUANTITY,
            [
                AllocationBasis(reference=item.reference, quantity=item.quantity)
                for item in self.BASES
            ],
        )
        assert parts == [D("200.0000"), D("60.0000"), D("40.0000")]
        assert sum(parts) == D("300.0000")

    def test_by_value(self) -> None:
        parts = allocate_expense(D("400"), AllocationMethod.BY_VALUE, self.BASES)
        assert sum(parts) == D("400.0000")
        assert parts[0] > parts[2] > parts[1]

    def test_by_weight(self) -> None:
        parts = allocate_expense(D("1200"), AllocationMethod.BY_WEIGHT, self.BASES)
        assert sum(parts) == D("1200.0000")
        assert parts[0] == D("1080.0000")

    def test_by_volume(self) -> None:
        parts = allocate_expense(D("253"), AllocationMethod.BY_VOLUME, self.BASES)
        assert sum(parts) == D("253.0000")

    def test_equal_share(self) -> None:
        parts = allocate_expense(D("300"), AllocationMethod.EQUAL_SHARE, self.BASES)
        assert parts == [D("100.0000")] * 3

    def test_by_line_charges_one_nominated_line(self) -> None:
        bases = [
            AllocationBasis(reference="L1"),
            AllocationBasis(reference="L2", nominated=True),
        ]
        assert allocate_expense(D("90"), AllocationMethod.BY_LINE, bases) == [
            ZERO,
            D("90.0000"),
        ]

    def test_by_line_refuses_an_ambiguous_nomination(self) -> None:
        bases = [
            AllocationBasis(reference="L1", nominated=True),
            AllocationBasis(reference="L2", nominated=True),
        ]
        with pytest.raises(ValidationError, match="one nominated line"):
            allocate_expense(D("90"), AllocationMethod.BY_LINE, bases)

    def test_manual(self) -> None:
        bases = [
            AllocationBasis(reference="L1", manual_amount=D("70")),
            AllocationBasis(reference="L2", manual_amount=D("30")),
        ]
        assert allocate_expense(D("100"), AllocationMethod.MANUAL, bases) == [
            D("70.0000"),
            D("30.0000"),
        ]

    def test_manual_refuses_an_unplaced_residual(self) -> None:
        bases = [AllocationBasis(reference="L1", manual_amount=D("70"))]
        with pytest.raises(ValidationError, match=r"30\.0000 remains unplaced"):
            allocate_expense(D("100"), AllocationMethod.MANUAL, bases)

    def test_residue_reconciles_to_the_total(self) -> None:
        """An indivisible total still sums exactly, per the pricing specification."""
        bases = _bases(
            ("L1", "1", "1", "1", "1"), ("L2", "1", "1", "1", "1"), ("L3", "1", "1", "1", "1")
        )
        parts = allocate_expense(D("100"), AllocationMethod.BY_VALUE, bases, precision=2)
        assert sum(parts) == D("100.00")
        assert sorted(parts) == [D("33.33"), D("33.33"), D("33.34")]

    def test_an_absent_basis_is_refused_rather_than_guessed(self) -> None:
        """Freight by weight across weightless lines is a configuration fault."""
        bases = [AllocationBasis(reference="L1"), AllocationBasis(reference="L2")]
        with pytest.raises(ValidationError, match="record no weight"):
            allocate_expense(D("100"), AllocationMethod.BY_WEIGHT, bases)

    def test_an_excluded_line_bears_nothing(self) -> None:
        bases = [
            AllocationBasis(reference="L1", quantity=D("1")),
            AllocationBasis(reference="L2", quantity=D("1"), participates=False),
        ]
        assert allocate_expense(D("50"), AllocationMethod.BY_QUANTITY, bases) == [
            D("50.0000"),
            ZERO,
        ]


# -- 2. Both margin methods and their precedence ---------------------------------------


class TestMargin:
    def test_markup_and_target_margin_are_different_arithmetic(self) -> None:
        cost = D("1200")
        assert apply_margin(cost, MarginMethod.MARKUP_ON_COST, D("0.25")) == D("1500.000000")
        assert apply_margin(cost, MarginMethod.TARGET_GROSS_MARGIN, D("0.25")) == D("1600.000000")

    def test_a_target_margin_of_one_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not permitted"):
            apply_margin(D("100"), MarginMethod.TARGET_GROSS_MARGIN, D("1"))

    def test_a_markup_above_one_is_permitted(self) -> None:
        """A 150% markup is a business decision, not an arithmetic impossibility."""
        assert apply_margin(D("100"), MarginMethod.MARKUP_ON_COST, D("1.5")) == D("250.000000")

    def test_realised_margin_reports_what_a_price_achieved(self) -> None:
        assert realised_margin_rate(D("1200"), D("1600")) == D("0.250000")
        assert realised_margin_rate(D("1200"), ZERO) == ZERO


# -- 3. Commission scope and the gross-up ----------------------------------------------


class TestCommission:
    def test_the_gross_up_preserves_the_margin_exactly(self) -> None:
        """ADR-0007: the business recovers the commission in full."""
        inclusive = D("1786.666667")
        outcome = apply_commission(inclusive, rate=D("0.05"), in_scope=True)
        assert outcome.list_price - outcome.commission == inclusive

    def test_a_fixed_commission_is_added_without_a_gross_up(self) -> None:
        outcome = apply_commission(
            D("1000"),
            method=CommissionMethod.FIXED_PER_UNIT,
            fixed_per_unit=D("12"),
            quantity=D("5"),
            in_scope=True,
        )
        assert outcome.commission == D("60.000000")
        assert outcome.list_price == D("1060.000000")

    def test_a_line_out_of_scope_pays_nothing_and_says_why(self) -> None:
        outcome = apply_commission(
            D("1000"), rate=D("0.05"), in_scope=False, reason="Out of scope."
        )
        assert outcome.commission == ZERO
        assert outcome.list_price == D("1000.000000")
        assert outcome.reason == "Out of scope."

    @pytest.mark.parametrize(
        ("scope", "test", "expected"),
        [
            (CommissionScope.ALL_ELIGIBLE, CommissionScopeTest(), True),
            (CommissionScope.SELECTED_LINES, CommissionScopeTest(line_selected=True), True),
            (CommissionScope.SELECTED_LINES, CommissionScopeTest(line_selected=False), False),
            (
                CommissionScope.SELECTED_PRODUCTS,
                CommissionScopeTest(product_id="p1", eligible_products=frozenset({"p1"})),
                True,
            ),
            (
                CommissionScope.SELECTED_PRODUCTS,
                CommissionScopeTest(product_id="p2", eligible_products=frozenset({"p1"})),
                False,
            ),
            (
                CommissionScope.SELECTED_CATEGORIES,
                CommissionScopeTest(category_id="c1", eligible_categories=frozenset({"c1"})),
                True,
            ),
            (
                CommissionScope.SELECTED_CATEGORIES,
                CommissionScopeTest(category_id="c2", eligible_categories=frozenset({"c1"})),
                False,
            ),
        ],
    )
    def test_every_scope_decides_and_explains(
        self, scope: CommissionScope, test: CommissionScopeTest, expected: bool
    ) -> None:
        in_scope, reason = commission_in_scope(scope, test)
        assert in_scope is expected
        assert reason

    def test_a_commission_rate_of_one_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not permitted"):
            apply_commission(D("100"), rate=D("1"), in_scope=True)


# -- 4. Partial fulfilment and partial payment -----------------------------------------


class TestEntitlement:
    def test_half_delivered_and_half_paid_is_a_quarter_owed(self) -> None:
        assert commission_entitlement(
            D("100"), fulfilled_fraction=D("0.5"), paid_fraction=D("0.5")
        ) == D("25.000000")

    def test_full_delivery_and_full_payment_owes_the_whole_accrual(self) -> None:
        assert commission_entitlement(D("100")) == D("100.000000")

    def test_nothing_collected_owes_nothing(self) -> None:
        assert commission_entitlement(D("100"), paid_fraction=ZERO) == ZERO

    @pytest.mark.parametrize("value", [D("-0.1"), D("1.1")])
    def test_a_fraction_outside_the_unit_interval_is_refused(self, value: Decimal) -> None:
        with pytest.raises(ValidationError, match="between zero and one"):
            commission_entitlement(D("100"), paid_fraction=value)

    def test_fraction_clamps_and_tolerates_zero(self) -> None:
        assert fraction(D("5"), D("10")) == D("0.500000")
        assert fraction(D("15"), D("10")) == D("1")
        assert fraction(D("5"), ZERO) == ZERO

    def test_the_states_run_in_one_direction(self) -> None:
        assert_commission_transition(CommissionState.ACCRUED, CommissionState.APPROVED)
        with pytest.raises(StateTransitionError):
            assert_commission_transition(CommissionState.ACCRUED, CommissionState.PAID)
        with pytest.raises(StateTransitionError):
            assert_commission_transition(CommissionState.PAID, CommissionState.PAYABLE)


# -- 5. Uplift by duration band ---------------------------------------------------------


class TestUplift:
    BANDS: ClassVar[list[DurationBand]] = [
        DurationBand(minimum_months=1, maximum_months=3, rate=D("0.03")),
        DurationBand(minimum_months=4, maximum_months=6, rate=D("0.08")),
        DurationBand(minimum_months=7, maximum_months=12, rate=D("0.15")),
    ]

    @pytest.mark.parametrize(
        ("months", "rate"), [(1, "0.03"), (3, "0.03"), (4, "0.08"), (6, "0.08"), (12, "0.15")]
    )
    def test_each_band_governs_its_own_durations(self, months: int, rate: str) -> None:
        assert band_for(self.BANDS, months).rate == D(rate)

    def test_a_duration_no_band_covers_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="No duration band covers 24"):
            band_for(self.BANDS, 24)

    def test_an_outright_sale_carries_no_charge(self) -> None:
        payable, charge = apply_uplift(D("1000"), rate=D("0.08"), plan=PaymentPlan.OUTRIGHT)
        assert (payable, charge) == (D("1000.000000"), ZERO)

    def test_a_percentage_uplift(self) -> None:
        payable, charge = apply_uplift(D("1824.2807"), rate=D("0.08"), plan=PaymentPlan.INSTALMENT)
        assert charge == D("145.942456")
        assert payable == D("1970.223156")

    def test_a_fixed_uplift_is_per_unit(self) -> None:
        payable, charge = apply_uplift(
            D("1000"),
            method=UpliftMethod.FIXED_PER_UNIT,
            fixed_per_unit=D("20"),
            quantity=D("4"),
            plan=PaymentPlan.INSTALMENT,
        )
        assert (payable, charge) == (D("1080.000000"), D("80.000000"))


# -- 6. Discount within and beyond approval limits -------------------------------------


class TestDiscount:
    LIMITS: ClassVar[list[DiscountLimit]] = [
        DiscountLimit(role_code="sales_person", maximum_rate=D("0.05"))
    ]

    def test_a_percentage_and_a_fixed_discount(self) -> None:
        assert apply_discount(D("1000"), DiscountMethod.PERCENTAGE, D("0.1")) == (
            D("900.000000"),
            D("100.000000"),
        )
        assert apply_discount(D("1000"), DiscountMethod.FIXED, D("250")) == (
            D("750.000000"),
            D("250.000000"),
        )

    def test_a_discount_cannot_exceed_the_price(self) -> None:
        with pytest.raises(ValidationError, match="cannot exceed"):
            apply_discount(D("1000"), DiscountMethod.FIXED, D("1001"))

    def test_within_the_limit_needs_no_approval(self) -> None:
        needs, _ = discount_needs_approval(
            self.LIMITS,
            role_codes=frozenset({"sales_person"}),
            rate=D("0.04"),
            amount=D("40"),
        )
        assert needs is False

    def test_beyond_the_limit_needs_approval(self) -> None:
        needs, reason = discount_needs_approval(
            self.LIMITS,
            role_codes=frozenset({"sales_person"}),
            rate=D("0.09"),
            amount=D("90"),
        )
        assert needs is True
        assert "exceeds" in reason

    def test_a_role_with_no_limit_may_grant_nothing(self) -> None:
        """Silence is read as zero. A control that defaults open is not a control."""
        needs, reason = discount_needs_approval(
            self.LIMITS, role_codes=frozenset({"storekeeper"}), rate=D("0.01"), amount=D("1")
        )
        assert needs is True
        assert "No discount limit" in reason

    def test_an_approver_is_not_bound_by_a_limit(self) -> None:
        needs, _ = discount_needs_approval(
            self.LIMITS,
            role_codes=frozenset({"storekeeper"}),
            rate=D("0.9"),
            amount=D("900"),
            may_approve=True,
        )
        assert needs is False

    def test_no_discount_needs_no_approval(self) -> None:
        needs, _ = discount_needs_approval([], role_codes=frozenset(), rate=ZERO, amount=ZERO)
        assert needs is False


# -- 7. The full sequence, in its canonical order ---------------------------------------


WORKED_EXAMPLE = LineInputs(
    reference="L1",
    quantity=D("10"),
    unit_cost=D("120"),
    supplier_discount_rate=D("0.05"),
    allocated_cost=D("200"),
    margin_method=MarginMethod.TARGET_GROSS_MARGIN,
    margin_rate=D("0.25"),
    commission_rate=D("0.05"),
    commission_in_scope=True,
    discount_value=D("0.03"),
    uplift_rate=D("0.08"),
    payment_plan=PaymentPlan.INSTALMENT,
)


class TestTheSequence:
    """The worked example of the pricing engine specification, figure for figure."""

    @pytest.mark.parametrize(
        ("attribute", "expected"),
        [
            ("supplier_cost", "1200.0000"),
            ("supplier_discount", "60.0000"),
            ("allocated_cost", "200.0000"),
            ("landed_cost", "1340.0000"),
            ("margin_inclusive", "1786.6667"),
            ("commission", "94.0351"),
            ("list_price", "1880.7018"),
            ("discount", "56.4211"),
            ("net_price", "1824.2807"),
            ("finance_charge", "145.9425"),
            ("payable_before_tax", "1970.2232"),
            ("contribution_before_commission", "484.2807"),
            ("contribution_after_commission", "390.2456"),
        ],
    )
    def test_each_step_matches_the_specification(self, attribute: str, expected: str) -> None:
        built = price_line(WORKED_EXAMPLE)
        assert present(getattr(built, attribute), 4) == D(expected)

    def test_commission_precedes_discount(self) -> None:
        """A discount does not reduce the affiliate's entitlement, per ADR-0007."""
        undiscounted = price_line(
            LineInputs(
                reference="L1",
                quantity=D("10"),
                unit_cost=D("120"),
                commission_rate=D("0.05"),
                commission_in_scope=True,
            )
        )
        discounted = price_line(
            LineInputs(
                reference="L1",
                quantity=D("10"),
                unit_cost=D("120"),
                commission_rate=D("0.05"),
                commission_in_scope=True,
                discount_value=D("0.2"),
            )
        )
        assert discounted.commission == undiscounted.commission

    def test_discount_precedes_the_uplift(self) -> None:
        """The customer is not financed on money they were discounted."""
        built = price_line(
            LineInputs(
                reference="L1",
                quantity=D("1"),
                unit_cost=D("1000"),
                discount_value=D("0.1"),
                uplift_rate=D("0.1"),
                payment_plan=PaymentPlan.INSTALMENT,
            )
        )
        assert built.net_price == D("900.000000")
        assert built.finance_charge == D("90.000000")

    def test_allocation_precedes_margin(self) -> None:
        """Margin is earned on the landed cost, not on the supplier invoice alone."""
        without = price_line(
            LineInputs(reference="L1", quantity=D("1"), unit_cost=D("100"), margin_rate=D("0.5"))
        )
        with_freight = price_line(
            LineInputs(
                reference="L1",
                quantity=D("1"),
                unit_cost=D("100"),
                allocated_cost=D("20"),
                margin_rate=D("0.5"),
            )
        )
        assert without.margin_amount == D("50.000000")
        assert with_freight.margin_amount == D("60.000000")

    def test_the_steps_are_reported_in_order(self) -> None:
        labels = [label for _, label, _ in price_line(WORKED_EXAMPLE).steps()]
        assert labels.index("Landed cost") < labels.index("List price")
        assert labels.index("List price") < labels.index("Payable before tax")

    def test_a_non_recoverable_tax_enters_the_cost_base(self) -> None:
        built = price_line(
            LineInputs(
                reference="L1", quantity=D("1"), unit_cost=D("100"), non_recoverable_tax=D("15")
            )
        )
        assert built.landed_cost == D("115.000000")

    def test_a_recoverable_tax_does_not(self) -> None:
        built = price_line(
            LineInputs(reference="L1", quantity=D("1"), unit_cost=D("100"), recoverable_tax=D("15"))
        )
        assert built.landed_cost == D("100.000000")


# -- 8. Document pricing ----------------------------------------------------------------


class TestDocument:
    def test_a_document_discount_is_apportioned_by_value(self) -> None:
        document = DocumentInputs(
            lines=[
                LineInputs(reference="1", quantity=D("1"), unit_cost=D("300")),
                LineInputs(reference="2", quantity=D("1"), unit_cost=D("100")),
            ],
            document_discount_value=D("0.1"),
        )
        built = price_document(document)
        assert built.document_discount == D("40.000000")
        assert built.line("1").discount == D("30.000000")
        assert built.line("2").discount == D("10.000000")
        assert built.discount == built.document_discount

    def test_expenses_are_allocated_before_lines_are_priced(self) -> None:
        document = DocumentInputs(
            lines=[
                LineInputs(
                    reference="1", quantity=D("2"), unit_cost=D("100"), margin_rate=D("0.5")
                ),
                LineInputs(
                    reference="2", quantity=D("2"), unit_cost=D("100"), margin_rate=D("0.5")
                ),
            ],
            expenses=[
                ExpenseInputs(
                    reference="E1",
                    category="transport",
                    amount=D("100"),
                    method=AllocationMethod.EQUAL_SHARE,
                )
            ],
        )
        built = price_document(document)
        assert built.line("1").landed_cost == D("250.000000")
        assert built.line("1").margin_amount == D("125.000000")

    def test_a_line_and_a_document_discount_combine(self) -> None:
        document = DocumentInputs(
            lines=[
                LineInputs(
                    reference="1",
                    quantity=D("1"),
                    unit_cost=D("1000"),
                    discount_value=D("0.1"),
                )
            ],
            document_discount_value=D("0.1"),
        )
        built = price_document(document)
        assert built.line("1").discount == D("200.000000")
        assert built.line("1").net_price == D("800.000000")

    def test_duplicate_references_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="own reference"):
            price_document(
                DocumentInputs(
                    lines=[
                        LineInputs(reference="1", quantity=D("1"), unit_cost=D("1")),
                        LineInputs(reference="1", quantity=D("1"), unit_cost=D("1")),
                    ]
                )
            )

    def test_an_empty_document_prices_to_nothing(self) -> None:
        built = price_document(DocumentInputs(lines=[]))
        assert built.lines == []
        assert built.payable_before_tax == ZERO


# -- 9. Rounding at every precision -----------------------------------------------------


class TestRounding:
    @pytest.mark.parametrize("precision", [2, 4])
    def test_the_document_total_reconciles_to_its_lines(self, precision: int) -> None:
        document = DocumentInputs(
            lines=[
                LineInputs(
                    reference=str(index),
                    quantity=D("7"),
                    unit_cost=D("13.33"),
                    margin_rate=D("0.37"),
                )
                for index in range(1, 6)
            ],
            expenses=[
                ExpenseInputs(
                    reference="E1",
                    category="transport",
                    amount=D("1000"),
                    method=AllocationMethod.BY_QUANTITY,
                )
            ],
            precision=precision,
        )
        built = price_document(document)
        allocated = sum(
            (amount for shares in built.allocations.values() for amount in shares.values()),
            ZERO,
        )
        assert present(allocated, precision) == present(D("1000"), precision)
        assert present(built.payable_before_tax, precision) == present(
            money(sum((line.payable_before_tax for line in built.lines), ZERO)), precision
        )

    def test_the_line_total_derives_from_the_stored_unit_price(self) -> None:
        """A unit price at four places would not reproduce the presented line total."""
        built = price_line(WORKED_EXAMPLE)
        assert present(built.unit_net_price * built.quantity, 4) == present(built.net_price, 4)


# -- 10. Reproducibility -----------------------------------------------------------------


class TestReproducibility:
    def test_identical_inputs_produce_identical_output(self) -> None:
        first = price_line(WORKED_EXAMPLE)
        second = price_line(WORKED_EXAMPLE)
        assert first == second

    def test_the_engine_reads_no_clock(self) -> None:
        """A schedule with no first due date is refused rather than dated from today."""
        with pytest.raises(ValidationError, match="first due date"):
            instalment_schedule(D("1000"), InstalmentTerms(duration_months=3))


# -- 11. Confidentiality ------------------------------------------------------------------


class TestConfidentiality:
    def test_the_customer_projection_discloses_no_cost_margin_or_commission(self) -> None:
        disclosed = customer_facing(price_line(WORKED_EXAMPLE))
        for forbidden in ("landed_cost", "margin", "commission", "supplier_cost", "unit_cost"):
            assert not any(forbidden in key for key in disclosed)

    def test_the_finance_charge_is_disclosed(self) -> None:
        """PRD §6.5 requires the instalment plan to be disclosed in full."""
        assert "finance_charge" in customer_facing(price_line(WORKED_EXAMPLE))


# -- 12. Instalment schedules --------------------------------------------------------------


class TestSchedule:
    TERMS: ClassVar[InstalmentTerms] = InstalmentTerms(
        duration_months=6,
        frequency=InstalmentFrequency.MONTHLY,
        deposit_rate=D("0.20"),
        first_due_on=date(2026, 12, 1),
    )

    def test_the_schedule_reconciles_to_the_total(self) -> None:
        outcome = instalment_schedule(D("1970.2232"), self.TERMS)
        assert outcome.deposit + outcome.financed == outcome.total_payable

    def test_the_regular_figure_repeats_and_the_residue_falls_at_the_end(self) -> None:
        outcome = instalment_schedule(D("1970.2232"), self.TERMS)
        regular = {item.amount for item in outcome.instalments[:-1]}
        assert len(regular) == 1
        assert outcome.instalments[-1].amount != outcome.instalment_amount

    def test_every_disclosure_the_specification_requires(self) -> None:
        outcome = instalment_schedule(D("1970.2232"), self.TERMS, finance_charge=D("145.9425"))
        assert outcome.deposit == D("394.0446")
        assert outcome.instalment_amount > ZERO
        assert outcome.frequency is InstalmentFrequency.MONTHLY
        assert outcome.first_due_on == date(2026, 12, 1)
        assert outcome.final_due_on == date(2027, 5, 1)
        assert outcome.finance_charge == D("145.9425")
        assert outcome.total_payable == D("1970.2232")

    @pytest.mark.parametrize(
        ("frequency", "months", "count"),
        [
            (InstalmentFrequency.MONTHLY, 6, 6),
            (InstalmentFrequency.QUARTERLY, 6, 2),
            (InstalmentFrequency.FORTNIGHTLY, 3, 6),
            (InstalmentFrequency.WEEKLY, 3, 12),
        ],
    )
    def test_each_frequency_produces_its_own_count(
        self, frequency: InstalmentFrequency, months: int, count: int
    ) -> None:
        assert instalment_count(frequency, months) == count

    def test_a_quarterly_plan_runs_in_whole_quarters(self) -> None:
        with pytest.raises(ValidationError, match="whole quarters"):
            instalment_count(InstalmentFrequency.QUARTERLY, 4)

    def test_a_deposit_cannot_exceed_the_total(self) -> None:
        with pytest.raises(ValidationError, match="cannot exceed"):
            instalment_schedule(
                D("100"),
                InstalmentTerms(
                    duration_months=3, deposit_amount=D("200"), first_due_on=date(2027, 1, 1)
                ),
            )

    def test_a_monthly_instalment_falls_once_in_each_month(self) -> None:
        """31 January plus a month is 28 February, not 3 March."""
        assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
        assert add_months(date(2026, 12, 15), 2) == date(2027, 2, 15)


# -- Lifecycle and variance ----------------------------------------------------------------


class TestLifecycle:
    def test_an_issued_quotation_cannot_be_amended(self) -> None:
        assert_amendable(QuotationState.DRAFT)
        with pytest.raises(StateTransitionError):
            assert_amendable(QuotationState.ISSUED)

    def test_the_transitions_the_lifecycle_allows(self) -> None:
        assert_transition(
            "Quotation", QUOTATION_TRANSITIONS, QuotationState.DRAFT, QuotationState.ISSUED
        )
        with pytest.raises(StateTransitionError):
            assert_transition(
                "Quotation", QUOTATION_TRANSITIONS, QuotationState.ACCEPTED, QuotationState.DRAFT
            )

    def test_variance_leads_with_the_direction_that_erodes_margin(self) -> None:
        adverse = CostVariance(reference="1", estimated=D("1000"), actual=D("1100"))
        assert adverse.variance == D("100.000000")
        assert adverse.variance_rate == D("0.100000")
        assert adverse.is_adverse

        favourable = CostVariance(reference="1", estimated=D("1000"), actual=D("900"))
        assert favourable.variance == D("-100.000000")
        assert not favourable.is_adverse

    def test_an_unrecorded_actual_cost_is_not_a_variance(self) -> None:
        assert not CostVariance(reference="1", estimated=D("1000"), actual=ZERO).is_known
