"""Shipment states, modes and the reconciliation of a shipment line. PRD §6.9."""

from __future__ import annotations

from decimal import Decimal

import pytest

from nickland.domain.dispatch import (
    DispatchDetail,
    DispatchMode,
    LineOutcome,
    ShipmentState,
    assert_shipment_transition,
    outstanding,
    state_after_delivery,
)
from nickland.domain.errors import StateTransitionError, ValidationError
from nickland.domain.money import plain

D = Decimal


class TestTransitions:
    def test_the_forward_path(self) -> None:
        assert_shipment_transition(ShipmentState.PREPARED, ShipmentState.PACKED)
        assert_shipment_transition(ShipmentState.PACKED, ShipmentState.DISPATCHED)
        assert_shipment_transition(ShipmentState.DISPATCHED, ShipmentState.PARTIALLY_DELIVERED)

    def test_no_cancelling_once_dispatched(self) -> None:
        with pytest.raises(StateTransitionError):
            assert_shipment_transition(ShipmentState.DISPATCHED, ShipmentState.CANCELLED)

    def test_no_dispatch_before_packing(self) -> None:
        with pytest.raises(StateTransitionError):
            assert_shipment_transition(ShipmentState.PREPARED, ShipmentState.DISPATCHED)

    def test_a_closed_shipment_goes_nowhere(self) -> None:
        for state in (ShipmentState.DELIVERED, ShipmentState.FAILED, ShipmentState.CANCELLED):
            assert state.is_closed
            with pytest.raises(StateTransitionError):
                assert_shipment_transition(state, ShipmentState.DISPATCHED)


class TestModes:
    def _detail(self, mode: DispatchMode, **extra: object) -> DispatchDetail:
        values: dict[str, object] = {
            "carrier": None,
            "driver": None,
            "vehicle": None,
            "destination": "Accra",
            "package_count": 1,
            "tracking_reference": None,
        }
        values.update(extra)
        return DispatchDetail(mode=mode, **values)  # type: ignore[arg-type]

    def test_a_carrier_is_named(self) -> None:
        with pytest.raises(ValidationError):
            self._detail(DispatchMode.CARRIER).validate()
        self._detail(DispatchMode.CARRIER, carrier="Speedline").validate()

    def test_our_vehicle_records_driver_and_vehicle(self) -> None:
        with pytest.raises(ValidationError):
            self._detail(DispatchMode.OWN_VEHICLE, driver="K. Boateng").validate()
        self._detail(DispatchMode.OWN_VEHICLE, driver="K. Boateng", vehicle="GR 1").validate()

    def test_a_collection_needs_no_destination(self) -> None:
        self._detail(DispatchMode.COLLECTION, destination=None).validate()
        with pytest.raises(ValidationError):
            self._detail(DispatchMode.COURIER, carrier="DHL", destination=None).validate()

    def test_at_least_one_package(self) -> None:
        with pytest.raises(ValidationError):
            self._detail(DispatchMode.COLLECTION, package_count=0).validate()


class TestOutcomes:
    def test_every_unit_is_accounted_for(self) -> None:
        LineOutcome(shipped=D("10"), delivered=D("7"), returned=D("2"), lost=D("1")).validate(
            position=1
        )
        with pytest.raises(ValidationError):
            LineOutcome(shipped=D("10"), delivered=D("7")).validate(position=1)
        with pytest.raises(ValidationError):
            LineOutcome(shipped=D("10"), delivered=D("11")).validate(position=1)

    def test_the_shipment_state_follows_what_was_received(self) -> None:
        whole = LineOutcome(shipped=D("4"), delivered=D("4"))
        part = LineOutcome(shipped=D("4"), delivered=D("1"), returned=D("3"))
        none = LineOutcome(shipped=D("4"), delivered=D("0"), returned=D("4"))
        assert state_after_delivery([whole]) is ShipmentState.DELIVERED
        assert state_after_delivery([whole, part]) is ShipmentState.PARTIALLY_DELIVERED
        assert state_after_delivery([none]) is ShipmentState.FAILED

    def test_outstanding_is_what_was_not_signed_for(self) -> None:
        assert outstanding(D("10"), D("1"), D("4")) == D("5")
        assert outstanding(D("10"), D("0"), D("10")) == D("0")


class TestPlain:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("10.000000", "10"), ("2.500000", "2.5"), ("0", "0"), ("100", "100")],
    )
    def test_a_quantity_reads_as_a_person_writes_it(self, value: str, expected: str) -> None:
        assert plain(D(value)) == expected
