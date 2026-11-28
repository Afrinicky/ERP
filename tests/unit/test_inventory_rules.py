"""The layer arithmetic both valuation policies share. ADR-0004."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.inventory import (
    CountVariance,
    ExpiryStatus,
    LayerView,
    MovementType,
    StockState,
    available,
    consume,
    expiry_status,
    fifo_order,
    reaverage,
    split_revaluation,
    unit_cost,
    value_of_part,
)

D = Decimal


def layer(key: str, quantity: str, value: str, aged: date) -> LayerView:
    return LayerView(key=key, quantity=D(quantity), value=D(value), aged_on=aged, order=key)


class TestValueOfPart:
    def test_whole_layer_takes_whole_value(self) -> None:
        assert value_of_part(D("3"), D("10"), D("3")) == D("10")

    def test_parts_leave_no_residue(self) -> None:
        quantity, value = D("3"), D("10")
        first = value_of_part(quantity, value, D("1"))
        second = value_of_part(quantity - 1, value - first, D("1"))
        third = value_of_part(quantity - 2, value - first - second, D("1"))
        assert first + second + third == value

    def test_refuses_more_than_held(self) -> None:
        with pytest.raises(ConflictError):
            value_of_part(D("2"), D("4"), D("3"))

    def test_refuses_nothing(self) -> None:
        with pytest.raises(ValidationError):
            value_of_part(D("2"), D("4"), D("0"))


class TestConsume:
    def test_oldest_first_by_age_then_identifier(self) -> None:
        layers = [
            layer("c", "5", "50", date(2026, 3, 1)),
            layer("b", "5", "60", date(2026, 1, 1)),
            layer("a", "5", "70", date(2026, 1, 1)),
        ]
        assert [item.key for item in fifo_order(layers)] == ["a", "b", "c"]
        taken = consume(layers, D("7"))
        assert [(item.key, item.quantity, item.value) for item in taken] == [
            ("a", D("5"), D("70")),
            ("b", D("2"), D("24")),
        ]

    def test_skips_empty_layers(self) -> None:
        layers = [layer("a", "0", "0", date(2026, 1, 1)), layer("b", "2", "8", date(2026, 2, 1))]
        assert [item.key for item in consume(layers, D("1"))] == ["b"]

    def test_refuses_more_than_held(self) -> None:
        with pytest.raises(ConflictError):
            consume([layer("a", "2", "8", date(2026, 1, 1))], D("3"))

    def test_consuming_everything_takes_every_cedi(self) -> None:
        layers = [
            layer("a", "3", "10", date(2026, 1, 1)),
            layer("b", "7", "13.333333", date(2026, 1, 2)),
        ]
        taken = consume(layers, D("10"))
        assert sum(item.value for item in taken) == D("23.333333")


class TestReaverage:
    def test_total_is_preserved_exactly(self) -> None:
        layers = [layer("a", "3", "10", date(2026, 1, 1)), layer("b", "4", "20", date(2026, 1, 2))]
        spread = reaverage(layers)
        assert sum(item.value for item in spread) == D("30")
        assert {item.key for item in spread} == {"a", "b"}

    def test_each_layer_carries_the_average(self) -> None:
        layers = [layer("a", "2", "10", date(2026, 1, 1)), layer("b", "2", "30", date(2026, 1, 2))]
        assert [item.value for item in reaverage(layers)] == [D("20"), D("20")]

    def test_consuming_after_reaverage_costs_the_average(self) -> None:
        layers = [layer("a", "2", "10", date(2026, 1, 1)), layer("b", "6", "42", date(2026, 1, 2))]
        spread = reaverage(layers)
        rebuilt = [
            LayerView(key=item.key, quantity=item.quantity, value=item.value, aged_on=old.aged_on)
            for item, old in zip(spread, layers, strict=True)
        ]
        taken = consume(rebuilt, D("4"))
        assert sum(item.value for item in taken) == D("26")

    def test_empty_pool(self) -> None:
        assert reaverage([layer("a", "0", "0", date(2026, 1, 1))]) == []


class TestRevaluation:
    def test_all_on_the_shelf(self) -> None:
        assert split_revaluation(D("90"), D("10"), D("10")) == (D("90"), D("0"))

    def test_all_issued(self) -> None:
        assert split_revaluation(D("90"), D("10"), D("0")) == (D("0"), D("90"))

    def test_split_sums_exactly(self) -> None:
        to_layer, to_cogs = split_revaluation(D("100"), D("3"), D("1"))
        assert to_layer == D("33.333333")
        assert to_layer + to_cogs == D("100")

    def test_refuses_impossible_remaining(self) -> None:
        with pytest.raises(ValidationError):
            split_revaluation(D("10"), D("2"), D("3"))


class TestExpiry:
    def test_usable_on_the_day_itself(self) -> None:
        assert expiry_status(date(2026, 6, 30), date(2026, 6, 30), 30) is ExpiryStatus.NEAR

    def test_expired_the_day_after(self) -> None:
        assert expiry_status(date(2026, 6, 30), date(2026, 7, 1), 30) is ExpiryStatus.EXPIRED

    def test_good_outside_window(self) -> None:
        assert expiry_status(date(2026, 12, 31), date(2026, 6, 1), 30) is ExpiryStatus.GOOD

    def test_no_date(self) -> None:
        assert expiry_status(None, date(2026, 6, 1), 30) is ExpiryStatus.NONE


class TestAvailabilityAndCounts:
    def test_available_never_negative(self) -> None:
        assert available(D("2"), D("5")) == D("0")
        assert available(D("5"), D("2")) == D("3")

    def test_count_variance_direction(self) -> None:
        assert CountVariance(D("5"), D("7")).movement is MovementType.COUNT_GAIN
        assert CountVariance(D("5"), D("4")).movement is MovementType.COUNT_LOSS
        assert CountVariance(D("5"), D("5")).movement is None

    def test_unit_cost(self) -> None:
        assert unit_cost(D("3"), D("10")) == D("3.333333")
        assert unit_cost(D("0"), D("10")) == D("0")

    def test_reserved_carries_no_value(self) -> None:
        assert not StockState.RESERVED.is_valued
        assert StockState.QUARANTINE.is_valued
