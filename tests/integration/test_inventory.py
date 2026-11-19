"""Inventory against a real database. PRD §6.11, ADR-0004.

The exit criteria this file exists to hold:

1. The ledger is append-only, and an operation is corrected by reversing it.
2. Quantity states reconcile to the ledger at every instant.
3. Valuation reconciles to the inventory control accounts.
4. Rejected and quarantined stock is excluded from availability and reservation.
5. A batch-tracked item cannot be issued without a batch, and an expired batch is
   blocked unless an authorised override is recorded.

The sixth, concurrent reservation of the final unit, is in tests/concurrency.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.inventory import (
    AdjustmentDraft,
    InspectionDraft,
    InventoryService,
    IssueDraft,
    OpeningDraft,
    StockLineDraft,
    TransferDraft,
    TransferLineDraft,
)
from nickland.application.services.inventory_reports import InventoryReportService
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.units import UnitService
from nickland.domain.errors import (
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.inventory import MovementType, StockState
from nickland.domain.ledger import EntryLine, JournalSource, Side
from nickland.persistence.catalogue_models import Product
from nickland.persistence.inventory_models import StockMovement
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal


@pytest.fixture
def stock(db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]) -> dict:
    unit = UnitService(db, clock=clock).units(admin)[0]
    catalogue = CatalogueService(db, clock=clock)
    gloves = catalogue.create_product(
        admin,
        ProductDraft(sku="GLV-100", name="Nitrile gloves", stock_unit_id=unit.id),
    )
    reagent = catalogue.create_product(
        admin,
        ProductDraft(
            sku="RGT-200",
            name="Glucose reagent",
            stock_unit_id=unit.id,
            tracking_policy="batch",
            requires_expiry=True,
        ),
    )
    analyser = catalogue.create_product(
        admin,
        ProductDraft(
            sku="ANL-300",
            name="Chemistry analyser",
            stock_unit_id=unit.id,
            tracking_policy="serial",
        ),
    )
    fifo = catalogue.create_product(
        admin,
        ProductDraft(
            sku="PIP-400", name="Pipette tips", stock_unit_id=unit.id, valuation_policy="fifo"
        ),
    )
    service_item = catalogue.create_product(
        admin,
        ProductDraft(
            sku="SRV-500", name="Installation", stock_unit_id=unit.id, item_class="service"
        ),
    )
    db.flush()
    second = OrganisationService(db, clock=clock).create_warehouse(
        admin, branch_id=provisioned["branch_id"], code="KSI", name="Kumasi store"
    )
    return {
        "gloves": gloves,
        "reagent": reagent,
        "analyser": analyser,
        "fifo": fifo,
        "service": service_item,
        "main": provisioned["warehouse_id"],
        "second": second.id,
    }


def _service(db: Session, clock: FixedClock) -> InventoryService:
    return InventoryService(db, clock=clock)


def _reports(db: Session, clock: FixedClock) -> InventoryReportService:
    return InventoryReportService(db, clock=clock)


def _assert_agrees(db: Session, clock: FixedClock, admin: Actor) -> None:
    reports = _reports(db, clock)
    reconciliation = reports.reconcile(admin)
    assert reconciliation.agrees, reconciliation.discrepancies
    valuation = reports.valuation(admin)
    assert valuation.agrees, [(a.account, a.layers, a.ledger) for a in valuation.accounts]


def _open(db: Session, clock: FixedClock, admin: Actor, stock: dict, **lines: object) -> None:
    drafts = []
    for key, spec in lines.items():
        quantity, cost, *rest = spec  # type: ignore[misc]
        extra = rest[0] if rest else {}
        drafts.append(
            StockLineDraft(
                product_id=stock[key].id, quantity=D(quantity), unit_cost=D(cost), **extra
            )
        )
    _service(db, clock).open_stock(admin, OpeningDraft(warehouse_id=stock["main"], lines=drafts))


def _position(db: Session, clock: FixedClock, admin: Actor, product: Product, warehouse: uuid.UUID):
    rows = _reports(db, clock).positions(admin, include_empty=True)
    return next(
        row for row in rows if row.product_id == product.id and row.warehouse_id == warehouse
    )


# --------------------------------------------------------------------------------------
# Valuation
# --------------------------------------------------------------------------------------


class TestValuation:
    def test_opening_stock_posts_to_inventory_and_capital(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("100", "2.50"))
        row = _position(db, clock, admin, stock["gloves"], stock["main"])
        assert row.usable == D("100")
        assert row.value == D("250")
        assert row.unit_cost == D("2.5")
        _assert_agrees(db, clock, admin)

    def test_moving_average_reaverages_on_receipt(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("10", "2"))
        _open(db, clock, admin, stock, gloves=("20", "3.5"))
        row = _position(db, clock, admin, stock["gloves"], stock["main"])
        assert row.unit_cost == D("3")
        operation = _service(db, clock).issue(
            admin,
            IssueDraft(product_id=stock["gloves"].id, warehouse_id=stock["main"], quantity=D("7")),
        )
        issued = [m for m in operation.movements if m.state == StockState.USABLE.value]
        assert sum(-m.value for m in issued) == D("21")
        _assert_agrees(db, clock, admin)

    def test_fifo_costs_the_oldest_first(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, fifo=("10", "1"))
        _open(db, clock, admin, stock, fifo=("10", "2"))
        operation = _service(db, clock).issue(
            admin,
            IssueDraft(product_id=stock["fifo"].id, warehouse_id=stock["main"], quantity=D("15")),
        )
        issued = [m for m in operation.movements if m.state == StockState.USABLE.value]
        assert sum(-m.value for m in issued) == D("20")
        assert _position(db, clock, admin, stock["fifo"], stock["main"]).value == D("10")
        _assert_agrees(db, clock, admin)

    def test_thirds_leave_no_residue(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("3", "3.333333"), fifo=("3", "3.333333"))
        service = _service(db, clock)
        for _ in range(3):
            for key in ("gloves", "fifo"):
                service.issue(
                    admin,
                    IssueDraft(
                        product_id=stock[key].id, warehouse_id=stock["main"], quantity=D("1")
                    ),
                )
        for key in ("gloves", "fifo"):
            assert _position(db, clock, admin, stock[key], stock["main"]).value == D("0")
        _assert_agrees(db, clock, admin)

    def test_inventory_accounts_refuse_manual_journals(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises((ConflictError, ValidationError)):
            JournalService(db, clock=clock).post(
                admin,
                JournalDraft(
                    posting_date=date(2026, 11, 5),
                    narration="Hand-posted stock",
                    lines=[
                        EntryLine(account_code="12000", side=Side.DEBIT, amount=D("10")),
                        EntryLine(account_code="30000", side=Side.CREDIT, amount=D("10")),
                    ],
                    source=JournalSource.MANUAL,
                ),
            )

    def test_count_gain_and_loss(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("50", "2"), fifo=("10", "1"))
        service = _service(db, clock)
        count = service.open_count(admin, warehouse_id=stock["main"])
        by_product = {line.product_fk: line for line in count.lines}
        service.record_count(
            admin,
            count.id,
            {
                by_product[stock["gloves"].id].id: (D("47"), "Damaged cartons found"),
                by_product[stock["fifo"].id].id: (D("12"), "Uncounted box"),
            },
        )
        service.submit_count(admin, count.id)
        service.approve_count(admin, count.id)
        assert _position(db, clock, admin, stock["gloves"], stock["main"]).usable == D("47")
        fifo = _position(db, clock, admin, stock["fifo"], stock["main"])
        assert fifo.usable == D("12")
        assert fifo.value == D("12")
        _assert_agrees(db, clock, admin)

    def test_service_items_hold_no_stock(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        with pytest.raises(ConflictError):
            _open(db, clock, admin, stock, service=("1", "10"))


# --------------------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------------------


class TestLedger:
    def test_movements_cannot_be_altered_or_deleted(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("5", "2"))
        movement = db.scalars(select(StockMovement)).first()
        assert movement is not None
        movement.quantity = D("6")
        with pytest.raises(ConflictError):
            db.flush()
        db.rollback()

    def test_movement_cannot_be_deleted(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("5", "2"))
        movement = db.scalars(select(StockMovement)).first()
        db.delete(movement)
        with pytest.raises(ConflictError):
            db.flush()
        db.rollback()

    def test_reversal_restores_quantity_value_and_ledger(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("10", "2"))
        service = _service(db, clock)
        loss = service.adjust(
            admin,
            AdjustmentDraft(
                warehouse_id=stock["main"],
                kind="damage",
                reason="Water damage",
                lines=[StockLineDraft(product_id=stock["gloves"].id, quantity=D("4"))],
            ),
        )
        assert _position(db, clock, admin, stock["gloves"], stock["main"]).usable == D("6")
        service.reverse(admin, loss.id, reason="Recorded against the wrong product")
        row = _position(db, clock, admin, stock["gloves"], stock["main"])
        assert (row.usable, row.value) == (D("10"), D("20"))
        assert (
            len(
                db.scalars(select(StockMovement).where(StockMovement.operation_id == loss.id)).all()
            )
            == 1
        )
        _assert_agrees(db, clock, admin)
        with pytest.raises(ConflictError):
            service.reverse(admin, loss.id, reason="Twice")

    def test_reversal_refused_once_stock_has_moved_since(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        service = _service(db, clock)
        _open(db, clock, admin, stock, gloves=("10", "2"))
        first = db.scalars(select(StockMovement)).first()
        assert first is not None
        service.issue(
            admin,
            IssueDraft(product_id=stock["gloves"].id, warehouse_id=stock["main"], quantity=D("1")),
        )
        with pytest.raises(ConflictError):
            service.reverse(admin, first.operation_id, reason="Too late")

    def test_stock_never_goes_below_zero(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("2", "2"))
        with pytest.raises(ConflictError):
            _service(db, clock).issue(
                admin,
                IssueDraft(
                    product_id=stock["gloves"].id, warehouse_id=stock["main"], quantity=D("3")
                ),
            )


# --------------------------------------------------------------------------------------
# States and availability
# --------------------------------------------------------------------------------------


class TestAvailability:
    def _quarantine(self, db: Session, clock: FixedClock, admin: Actor, stock: dict) -> None:
        service = _service(db, clock)
        operation = service.engine.begin(admin, MovementType.RECEIPT, occurred_on=date(2026, 11, 5))
        operation.add(
            stock["gloves"], stock["main"], StockState.QUARANTINE, D("10"), D("20"), counter="20150"
        )
        operation.finish("Received into quarantine")

    def test_quarantine_and_rejected_are_not_available(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        self._quarantine(db, clock, admin, stock)
        service = _service(db, clock)
        row = _position(db, clock, admin, stock["gloves"], stock["main"])
        assert (row.quarantine, row.available) == (D("10"), D("0"))
        with pytest.raises(ConflictError):
            service.reserve(
                admin, product_id=stock["gloves"].id, warehouse_id=stock["main"], quantity=D("1")
            )
        service.inspect(
            admin,
            InspectionDraft(
                product_id=stock["gloves"].id,
                warehouse_id=stock["main"],
                passed=D("7"),
                failed=D("3"),
                findings="Torn seals",
            ),
        )
        row = _position(db, clock, admin, stock["gloves"], stock["main"])
        assert (row.usable, row.rejected, row.available) == (D("7"), D("3"), D("7"))
        with pytest.raises(ConflictError):
            service.reserve(
                admin, product_id=stock["gloves"].id, warehouse_id=stock["main"], quantity=D("8")
            )
        service.reserve(
            admin, product_id=stock["gloves"].id, warehouse_id=stock["main"], quantity=D("7")
        )
        assert _position(db, clock, admin, stock["gloves"], stock["main"]).available == D("0")
        service.return_to_supplier(
            admin,
            product_id=stock["gloves"].id,
            warehouse_id=stock["main"],
            quantity=D("3"),
            reason="Torn seals",
        )
        assert _position(db, clock, admin, stock["gloves"], stock["main"]).rejected == D("0")
        _assert_agrees(db, clock, admin)

    def test_reserved_stock_cannot_be_issued_to_someone_else(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("5", "2"))
        service = _service(db, clock)
        reservation = service.reserve(
            admin, product_id=stock["gloves"].id, warehouse_id=stock["main"], quantity=D("4")
        )
        with pytest.raises(ConflictError):
            service.issue(
                admin,
                IssueDraft(
                    product_id=stock["gloves"].id, warehouse_id=stock["main"], quantity=D("2")
                ),
            )
        service.issue(
            admin,
            IssueDraft(
                product_id=stock["gloves"].id,
                warehouse_id=stock["main"],
                quantity=D("4"),
                reservation_id=reservation.id,
            ),
        )
        row = _position(db, clock, admin, stock["gloves"], stock["main"])
        assert (row.usable, row.reserved, row.available) == (D("1"), D("0"), D("1"))
        _assert_agrees(db, clock, admin)

    def test_transfer_passes_through_transit(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        _open(db, clock, admin, stock, gloves=("10", "2"))
        service = _service(db, clock)
        transfer = service.raise_transfer(
            admin,
            TransferDraft(
                from_warehouse_id=stock["main"],
                to_warehouse_id=stock["second"],
                lines=[TransferLineDraft(product_id=stock["gloves"].id, quantity=D("4"))],
            ),
        )
        service.dispatch_transfer(admin, transfer.id)
        arriving = _position(db, clock, admin, stock["gloves"], stock["second"])
        assert (arriving.in_transit, arriving.available, arriving.value) == (D("4"), D("0"), D("8"))
        _assert_agrees(db, clock, admin)
        service.receive_transfer(admin, transfer.id)
        arrived = _position(db, clock, admin, stock["gloves"], stock["second"])
        assert (arrived.usable, arrived.in_transit, arrived.available) == (D("4"), D("0"), D("4"))
        _assert_agrees(db, clock, admin)


# --------------------------------------------------------------------------------------
# Traceability
# --------------------------------------------------------------------------------------


class TestTraceability:
    def _batches(self, db: Session, clock: FixedClock, admin: Actor, stock: dict) -> None:
        _open(
            db,
            clock,
            admin,
            stock,
            reagent=("5", "10", {"batch": "LOT-OLD", "expiry": date(2026, 10, 31)}),
        )
        _open(
            db,
            clock,
            admin,
            stock,
            reagent=("5", "12", {"batch": "LOT-NEW", "expiry": date(2027, 6, 30)}),
        )

    def _batch_id(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict, code: str
    ) -> uuid.UUID:
        from nickland.persistence.inventory_models import Batch

        batch = db.scalar(select(Batch).where(Batch.code == code))
        assert batch is not None
        return batch.id

    def test_batch_is_required_on_receipt_and_issue(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        with pytest.raises(ValidationError):
            _open(db, clock, admin, stock, reagent=("5", "10"))
        self._batches(db, clock, admin, stock)
        with pytest.raises(ValidationError):
            _service(db, clock).issue(
                admin,
                IssueDraft(
                    product_id=stock["reagent"].id, warehouse_id=stock["main"], quantity=D("1")
                ),
            )

    def test_expired_batch_is_blocked_without_override(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        self._batches(db, clock, admin, stock)
        old = self._batch_id(db, clock, admin, stock, "LOT-OLD")
        service = _service(db, clock)
        with pytest.raises(ConflictError):
            service.issue(
                admin,
                IssueDraft(
                    product_id=stock["reagent"].id,
                    warehouse_id=stock["main"],
                    quantity=D("1"),
                    batch_id=old,
                ),
            )
        clerk = make_actor(admin.business_id, permissions={"assembly.manage", "stock.view"})
        with pytest.raises(PermissionDeniedError):
            service.issue(
                clerk,
                IssueDraft(
                    product_id=stock["reagent"].id,
                    warehouse_id=stock["main"],
                    quantity=D("1"),
                    batch_id=old,
                    override_reason="Customer accepts for training use",
                ),
            )
        operation = service.issue(
            admin,
            IssueDraft(
                product_id=stock["reagent"].id,
                warehouse_id=stock["main"],
                quantity=D("1"),
                batch_id=old,
                override_reason="Customer accepts for training use",
            ),
        )
        recorded = [m for m in operation.movements if m.state == StockState.USABLE.value]
        assert recorded[0].override_reason == "Customer accepts for training use"
        assert recorded[0].override_by_id == admin.user_id

    def test_expired_stock_is_never_available_or_reserved(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        self._batches(db, clock, admin, stock)
        row = _position(db, clock, admin, stock["reagent"], stock["main"])
        assert (row.usable, row.available) == (D("10"), D("5"))
        with pytest.raises(ConflictError):
            _service(db, clock).reserve(
                admin, product_id=stock["reagent"].id, warehouse_id=stock["main"], quantity=D("6")
            )
        expiring = _reports(db, clock).expiry(admin)
        assert [item.batch for item in expiring] == ["LOT-OLD"]

    def test_serials_are_one_per_unit(
        self, db: Session, clock: FixedClock, admin: Actor, stock: dict
    ) -> None:
        with pytest.raises(ValidationError):
            _open(db, clock, admin, stock, analyser=("2", "9000", {"serials": ["SN-1"]}))
        _open(db, clock, admin, stock, analyser=("2", "9000", {"serials": ["SN-1", "SN-2"]}))
        service = _service(db, clock)
        service.issue(
            admin,
            IssueDraft(
                product_id=stock["analyser"].id,
                warehouse_id=stock["main"],
                quantity=D("1"),
                serials=["SN-2"],
            ),
        )
        with pytest.raises(ConflictError):
            service.issue(
                admin,
                IssueDraft(
                    product_id=stock["analyser"].id,
                    warehouse_id=stock["main"],
                    quantity=D("1"),
                    serials=["SN-2"],
                ),
            )
        _assert_agrees(db, clock, admin)
