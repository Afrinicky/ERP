"""Monetary arithmetic. PRD 9.5 prohibits binary floating-point in this path."""

from __future__ import annotations

from decimal import Decimal

import pytest

from nickland.domain.money import (
    allocate,
    apply_rate,
    divide,
    gross_up,
    money,
    multiply,
    present,
    suggested_tenders,
)


def test_float_is_rejected() -> None:
    with pytest.raises(TypeError, match="Floating-point"):
        money(1.5)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "0.000000"),
        ("1200", "1200.000000"),
        ("-45.5", "-45.500000"),
        ("0.0000005", "0.000001"),
        ("0.0000004", "0.000000"),
    ],
)
def test_working_precision(value: str, expected: str) -> None:
    assert money(value) == Decimal(expected)


def test_presentation_rounds_half_up() -> None:
    assert present(Decimal("1.23455"), 4) == Decimal("1.2346")
    assert present(Decimal("1.23445"), 4) == Decimal("1.2345")
    assert present(Decimal("2.5"), 0) == Decimal("3")


def test_multiply_and_divide_are_exact() -> None:
    assert multiply(money("120"), money("10")) == Decimal("1200.000000")
    assert divide(money("1340"), money("0.75")) == Decimal("1786.666667")


def test_divide_by_zero_raises() -> None:
    with pytest.raises(ZeroDivisionError):
        divide(money("1"), money("0"))


def test_gross_up_preserves_margin() -> None:
    """ADR-0007: a percentage commission is grossed up so margin is preserved."""
    base = money("1786.666700")
    rate = Decimal("0.05")
    price = gross_up(base, rate)
    commission = apply_rate(price, rate)
    assert present(price - commission, 4) == present(base, 4)


@pytest.mark.parametrize("rate", ["1", "1.5", "-0.1"])
def test_gross_up_rejects_impossible_rates(rate: str) -> None:
    with pytest.raises(ValueError):
        gross_up(money("100"), Decimal(rate))


class TestAllocate:
    """Rounding residue goes to the largest weight, so totals reconcile exactly."""

    def test_proportional_by_value(self) -> None:
        parts = allocate(money("400"), [money("1200"), money("300"), money("100")], 4)
        assert parts == [Decimal("300.0000"), Decimal("75.0000"), Decimal("25.0000")]
        assert sum(parts) == Decimal("400.0000")

    def test_indivisible_total_still_reconciles(self) -> None:
        parts = allocate(money("100"), [money("1"), money("1"), money("1")], 2)
        assert sum(parts) == Decimal("100.00")
        assert parts[0] == Decimal("33.34")

    def test_residue_goes_to_largest_weight(self) -> None:
        parts = allocate(money("10"), [money("1"), money("5"), money("1")], 2)
        assert sum(parts) == Decimal("10.00")
        assert parts[1] == max(parts)

    def test_zero_weights_divide_equally(self) -> None:
        parts = allocate(money("90"), [money("0"), money("0"), money("0")], 2)
        assert sum(parts) == Decimal("90.00")
        assert parts[0] == Decimal("30.00")

    def test_empty_weights(self) -> None:
        assert allocate(money("100"), [], 2) == []

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            allocate(money("100"), [money("1"), money("-1")], 2)

    @pytest.mark.parametrize("precision", [0, 2, 4, 6])
    def test_reconciles_at_every_precision(self, precision: int) -> None:
        total = money("1000")
        parts = allocate(total, [money("7"), money("11"), money("13"), money("17")], precision)
        assert sum(parts) == present(total, precision)


class TestSuggestedTenders:
    """What a cash tender offers as one-press choices: the exact figure, then that figure
    rounded up to each note."""

    def test_a_counter_sale_offers_the_amounts_people_hand_over(self) -> None:
        assert suggested_tenders(Decimal("304.75")) == [
            Decimal("304.75"),
            Decimal("305.00"),
            Decimal("310.00"),
            Decimal("320.00"),
            Decimal("350.00"),
            Decimal("400.00"),
        ]

    def test_the_exact_figure_comes_first_then_each_note_above_it(self) -> None:
        assert suggested_tenders(Decimal("48.23")) == [
            Decimal("48.23"),
            Decimal("49.00"),
            Decimal("50.00"),
            Decimal("60.00"),
            Decimal("100.00"),
            Decimal("200.00"),
        ]

    def test_a_small_sale_offers_small_notes(self) -> None:
        assert suggested_tenders(Decimal("3.40"))[:4] == [
            Decimal("3.40"),
            Decimal("4.00"),
            Decimal("5.00"),
            Decimal("10.00"),
        ]

    def test_a_round_figure_is_not_offered_twice(self) -> None:
        offered = suggested_tenders(Decimal("50"))
        assert offered[0] == Decimal("50.00")
        assert len(offered) == len(set(offered))

    def test_a_large_round_figure_offers_the_next_bundle(self) -> None:
        assert suggested_tenders(Decimal("1380")) == [Decimal("1380.00"), Decimal("1400.00")]

    def test_nothing_is_offered_when_nothing_is_due(self) -> None:
        assert suggested_tenders(Decimal("0")) == []
        assert suggested_tenders(Decimal("-5")) == []

    def test_the_currency_decides_its_notes(self) -> None:
        offered = suggested_tenders(
            Decimal("7.10"), denominations=(Decimal("5"), Decimal("20"), Decimal("100"))
        )
        assert offered == [
            Decimal("7.10"),
            Decimal("10.00"),
            Decimal("20.00"),
            Decimal("100.00"),
        ]
