"""Dispatch and proof of delivery against a real database. PRD §6.9, AC-08.

The exit criteria this file exists to hold:

1. A waybill and its signed proof of receipt are linked to the originating order and
   reproducible.
2. Partial delivery leaves the correct residual quantity outstanding.
3. A delivery exception posts its stock and financial consequence through the ledger.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.assembly import AssemblyService
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.dispatch import (
    DeliveryDraft,
    DispatchDraft,
    DispatchService,
    OutcomeDraft,
    PrepareDraft,
    ShipmentLineDraft,
)
from nickland.application.services.documents import DocumentRecordService
from nickland.application.services.inventory import (
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.inventory_reports import InventoryReportService
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.application.services.quotations import (
    LineDraft,
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.units import UnitService
from nickland.documents.service import DocumentRenderService
from nickland.domain.documents import DocumentKind
from nickland.domain.errors import (
    ConflictError,
    PermissionDeniedError,
    StateTransitionError,
    ValidationError,
)
from nickland.persistence.dispatch_models import Shipment
from nickland.persistence.document_models import IssuedDocument
from nickland.persistence.ledger_models import Account
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal
LATER = date(2027, 6, 30)
SIGNATURE = b"\x89PNG\r\n\x1a\n signature strokes"


@pytest.fixture
def world(db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]) -> dict:
    """An accepted, converted order of three stock lines, all reserved from the shelf."""
    warehouse = provisioned["warehouse_id"]
    customer = PartyService(db, clock=clock).create(
        admin, PartyDraft(name="Korle Bu Teaching Hospital", is_customer=True)
    )
    unit = UnitService(db, clock=clock).units(admin)[0]
    catalogue = CatalogueService(db, clock=clock)
    gloves = catalogue.create_product(
        admin, ProductDraft(sku="GLV-100", name="Nitrile gloves", stock_unit_id=unit.id)
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
    db.flush()
    InventoryService(db, clock=clock).open_stock(
        admin,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[
                StockLineDraft(product_id=gloves.id, quantity=D("10"), unit_cost=D("20")),
                StockLineDraft(
                    product_id=reagent.id,
                    quantity=D("6"),
                    unit_cost=D("300"),
                    batch="RX-25088",
                    expiry=date(2027, 9, 30),
                ),
                StockLineDraft(
                    product_id=analyser.id,
                    quantity=D("2"),
                    unit_cost=D("9000"),
                    serials=["SN-0001", "SN-0002"],
                ),
            ],
        ),
    )

    def stock_line(product, quantity: str, cost: str) -> LineDraft:
        return LineDraft(
            description=product.name,
            quantity=D(quantity),
            product_id=product.id,
            sources=[
                SourceDraft(
                    kind="stock", quantity=D(quantity), unit_cost=D(cost), warehouse_id=warehouse
                )
            ],
        )

    quotations = QuotationService(db, clock=clock)
    quotation = quotations.create(
        admin,
        QuotationDraft(
            customer_id=customer.id,
            lines=[
                stock_line(gloves, "10", "20"),
                stock_line(reagent, "6", "300"),
                stock_line(analyser, "2", "9000"),
            ],
        ),
    )
    db.flush()
    quotations.issue(admin, quotation.id)
    orders = SalesOrderService(db, clock=clock)
    orders.record_acceptance(admin, quotation.id, AcceptanceDraft(reference="KBTH-PO-118"))
    order = orders.convert(admin, quotation.id)
    db.flush()
    lines = {line.product_fk: line for line in order.lines}
    return {
        "order": order,
        "gloves": lines[gloves.id],
        "reagent": lines[reagent.id],
        "analyser": lines[analyser.id],
    }


@pytest.fixture
def dispatch(db: Session, clock: FixedClock) -> DispatchService:
    return DispatchService(db, clock=clock)


def balance(db: Session, clock: FixedClock, business_id: uuid.UUID, code: str) -> Decimal:
    account = db.scalar(
        select(Account).where(Account.business_id == business_id, Account.code == code)
    )
    assert account is not None
    return LedgerReportService(db, clock=clock).account_balance(
        business_id, account.id, as_at=LATER
    )


def agrees(db: Session, clock: FixedClock, admin: Actor) -> None:
    reports = InventoryReportService(db, clock=clock)
    reconciliation = reports.reconcile(admin)
    assert reconciliation.agrees, reconciliation.discrepancies
    valuation = reports.valuation(admin)
    assert valuation.agrees, [(a.account, a.layers, a.ledger) for a in valuation.accounts]


def ship(
    dispatch: DispatchService,
    admin: Actor,
    world: dict,
    lines: list[tuple[str, str]] | list[tuple[str, str, list[str]]],
    *,
    mode: str = "carrier",
) -> Shipment:
    drafts = []
    for spec in lines:
        key, quantity, *rest = spec
        drafts.append(
            ShipmentLineDraft(
                sales_order_line_id=world[key].id,
                quantity=D(quantity),
                serials=list(rest[0]) if rest else [],
            )
        )
    shipment = dispatch.prepare(
        admin,
        PrepareDraft(
            sales_order_id=world["order"].id,
            lines=drafts,
            destination="Guggisberg Avenue\nKorle Bu, Accra",
        ),
    )
    dispatch.pack(admin, shipment.id, package_count=3)
    return dispatch.dispatch(
        admin,
        shipment.id,
        DispatchDraft(
            mode=mode,
            package_count=3,
            carrier="Speedline Logistics" if mode in ("carrier", "courier") else None,
            driver="K. Boateng",
            vehicle="GR 2214-24",
            tracking_reference="SPL-77310",
        ),
    )


# --------------------------------------------------------------------------------------
# Exit criterion 1: the waybill and its proof
# --------------------------------------------------------------------------------------


class TestWaybillAndProof:
    def test_the_signed_waybill_is_linked_to_the_order_and_reproducible(
        self, db: Session, clock: FixedClock, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = ship(dispatch, admin, world, [("gloves", "10")])
        assert shipment.number == f"{world['order'].number}/1"
        waybill = dispatch.issue_document(admin, shipment.id, DocumentKind.WAYBILL)
        assert waybill.number.startswith("WB-")
        dispatch.confirm_delivery(
            admin,
            shipment.id,
            DeliveryDraft(
                outcomes=[OutcomeDraft(shipment_line_id=shipment.lines[0].id, delivered=D("10"))],
                recipient_name="Sister A. Mensah",
                recipient_role="Stores supervisor",
                evidence=SIGNATURE,
                media_type="image/png",
            ),
        )
        assert shipment.state == "delivered"
        assert shipment.waybill_fk == waybill.id
        # Linked to the order: the shipment names the order, the waybill the shipment.
        assert shipment.sales_order_fk == world["order"].id
        assert (waybill.source_type, waybill.source_id) == ("shipment", shipment.id)
        proofs = DocumentRecordService(db, clock=clock).acknowledgements(admin, waybill.id)
        assert [proof.recipient_name for proof in proofs] == ["Sister A. Mensah"]
        assert proofs[0].evidence_hash == hashlib.sha256(SIGNATURE).hexdigest()
        assert shipment.acknowledgement_fk == proofs[0].id
        # Reproducible: the reprint is the bytes that were issued, byte for byte.
        reprinted = DocumentRenderService(db, clock=clock).reprint(admin, waybill.id)
        assert hashlib.sha256(reprinted.content).hexdigest() == waybill.content_hash
        # The proof cannot be rewritten.
        proofs[0].recipient_name = "Someone else"
        with pytest.raises(ConflictError):
            db.flush()
        db.rollback()

    def test_each_document_is_issued_once(
        self, db: Session, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = dispatch.prepare(
            admin,
            PrepareDraft(
                sales_order_id=world["order"].id,
                lines=[ShipmentLineDraft(sales_order_line_id=world["gloves"].id, quantity=D("4"))],
            ),
        )
        pick = dispatch.issue_document(admin, shipment.id, DocumentKind.PICK_LIST)
        assert dispatch.issue_document(admin, shipment.id, DocumentKind.PICK_LIST).id == pick.id
        with pytest.raises(ConflictError):
            dispatch.issue_document(admin, shipment.id, DocumentKind.WAYBILL)
        with pytest.raises(ConflictError):
            dispatch.issue_document(admin, shipment.id, DocumentKind.PACKING_LIST)
        dispatch.pack(admin, shipment.id, package_count=1)
        packing = dispatch.issue_document(admin, shipment.id, DocumentKind.PACKING_LIST)
        assert packing.kind == "packing_list"

    def test_several_waybills_for_one_order(
        self, db: Session, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        first = ship(dispatch, admin, world, [("gloves", "4")])
        second = ship(dispatch, admin, world, [("gloves", "6")], mode="own_vehicle")
        numbers = {
            dispatch.issue_document(admin, item.id, DocumentKind.WAYBILL).number
            for item in (first, second)
        }
        assert len(numbers) == 2
        assert [item.number for item in dispatch.shipments(admin, order_id=world["order"].id)] == [
            second.number,
            first.number,
        ]

    def test_a_delivery_needs_the_recipient(
        self, db: Session, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = ship(dispatch, admin, world, [("gloves", "2")])
        with pytest.raises(ValidationError):
            dispatch.confirm_delivery(
                admin,
                shipment.id,
                DeliveryDraft(
                    outcomes=[OutcomeDraft(shipment_line_id=shipment.lines[0].id, delivered=D("2"))]
                ),
            )


# --------------------------------------------------------------------------------------
# Exit criterion 2: partial delivery
# --------------------------------------------------------------------------------------


class TestPartialDelivery:
    def test_the_undelivered_remainder_stays_outstanding(
        self, db: Session, clock: FixedClock, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        line = world["gloves"]
        shipment = ship(dispatch, admin, world, [("gloves", "6")])
        dispatch.confirm_delivery(
            admin,
            shipment.id,
            DeliveryDraft(
                outcomes=[
                    OutcomeDraft(
                        shipment_line_id=shipment.lines[0].id,
                        delivered=D("4"),
                        returned=D("2"),
                        reason="Two cartons refused at goods-inwards",
                    )
                ],
                recipient_name="J. Owusu",
            ),
        )
        assert shipment.state == "partially_delivered"
        view = next(
            item
            for item in AssemblyService(db, clock=clock).workspace(admin, world["order"].id).lines
            if item.line_id == line.id
        )
        # Ten ordered, four received: six outstanding — the two that came back and the
        # four never shipped. The two are back at the bay, held for this line.
        assert (view.delivered, view.outstanding) == (D("4"), D("6"))
        assert (view.reserved, view.staged) == (D("6"), D("2"))
        assert dispatch.shippable(line) == D("6")
        # The next shipment carries the remainder, and the line closes.
        second = ship(dispatch, admin, world, [("gloves", "6")])
        dispatch.confirm_delivery(
            admin,
            second.id,
            DeliveryDraft(
                outcomes=[OutcomeDraft(shipment_line_id=second.lines[0].id, delivered=D("6"))],
                recipient_name="J. Owusu",
            ),
        )
        assert line.quantity_delivered == D("10")
        assert balance(db, clock, admin.business_id, "50000") == D("200")
        agrees(db, clock, admin)

    def test_a_shipment_cannot_carry_more_than_is_reserved(
        self, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        with pytest.raises(ConflictError):
            dispatch.prepare(
                admin,
                PrepareDraft(
                    sales_order_id=world["order"].id,
                    lines=[
                        ShipmentLineDraft(sales_order_line_id=world["gloves"].id, quantity=D("11"))
                    ],
                ),
            )
        dispatch.prepare(
            admin,
            PrepareDraft(
                sales_order_id=world["order"].id,
                lines=[ShipmentLineDraft(sales_order_line_id=world["gloves"].id, quantity=D("7"))],
            ),
        )
        with pytest.raises(ConflictError):
            dispatch.prepare(
                admin,
                PrepareDraft(
                    sales_order_id=world["order"].id,
                    lines=[
                        ShipmentLineDraft(sales_order_line_id=world["gloves"].id, quantity=D("4"))
                    ],
                ),
            )

    def test_every_unit_shipped_is_accounted_for(
        self, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = ship(dispatch, admin, world, [("gloves", "5")])
        with pytest.raises(ValidationError):
            dispatch.confirm_delivery(
                admin,
                shipment.id,
                DeliveryDraft(
                    outcomes=[
                        OutcomeDraft(
                            shipment_line_id=shipment.lines[0].id,
                            delivered=D("3"),
                            reason="Short",
                        )
                    ],
                    recipient_name="J. Owusu",
                ),
            )


# --------------------------------------------------------------------------------------
# Exit criterion 3: exceptions post through the ledger
# --------------------------------------------------------------------------------------


class TestExceptions:
    def test_dispatch_carries_stock_in_transit(
        self, db: Session, clock: FixedClock, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        ship(dispatch, admin, world, [("gloves", "10"), ("reagent", "6")])
        business = admin.business_id
        assert balance(db, clock, business, "12200") == D("2000")
        assert balance(db, clock, business, "12000") == D("18000")
        agrees(db, clock, admin)

    def test_damaged_returns_go_to_quarantine_and_losses_are_written_off(
        self, db: Session, clock: FixedClock, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = ship(dispatch, admin, world, [("gloves", "10"), ("reagent", "6")])
        gloves_line, reagent_line = shipment.lines
        dispatch.confirm_delivery(
            admin,
            shipment.id,
            DeliveryDraft(
                outcomes=[
                    OutcomeDraft(
                        shipment_line_id=gloves_line.id,
                        delivered=D("7"),
                        lost=D("3"),
                        reason="One carton missing from the truck",
                    ),
                    OutcomeDraft(
                        shipment_line_id=reagent_line.id,
                        delivered=D("4"),
                        damaged=D("2"),
                        reason="Two bottles cracked",
                    ),
                ],
                recipient_name="J. Owusu",
            ),
        )
        business = admin.business_id
        assert balance(db, clock, business, "12200") == D("0")
        assert balance(db, clock, business, "50000") == D("1340")  # 7 at 20, 4 at 300
        assert balance(db, clock, business, "51000") == D("60")  # 3 lost at 20
        assert balance(db, clock, business, "12100") == D("600")  # 2 damaged at 300
        assert {item.kind for item in shipment.exceptions} == {"lost", "damaged"}
        assert all(
            item.operation_id == shipment.delivery_operation_id for item in shipment.exceptions
        )
        # Neither the lost nor the damaged units will be staged again: the line needs more.
        view = AssemblyService(db, clock=clock).workspace(admin, world["order"].id)
        reagent = next(item for item in view.lines if item.line_id == world["reagent"].id)
        assert (reagent.delivered, reagent.staged, reagent.outstanding) == (D("4"), D("0"), D("2"))
        agrees(db, clock, admin)

    def test_a_failed_delivery_comes_back_whole(
        self, db: Session, clock: FixedClock, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = ship(dispatch, admin, world, [("gloves", "10")])
        dispatch.confirm_delivery(
            admin,
            shipment.id,
            DeliveryDraft(
                outcomes=[
                    OutcomeDraft(
                        shipment_line_id=shipment.lines[0].id,
                        delivered=D("0"),
                        returned=D("10"),
                        reason="Stores closed for audit",
                    )
                ]
            ),
        )
        assert shipment.state == "failed"
        assert shipment.acknowledgement_fk is None
        assert balance(db, clock, admin.business_id, "50000") == D("0")
        assert balance(db, clock, admin.business_id, "12200") == D("0")
        assert dispatch.shippable(world["gloves"]) == D("10")
        agrees(db, clock, admin)

    def test_a_return_after_delivery_reverses_cost_of_sales(
        self, db: Session, clock: FixedClock, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = ship(dispatch, admin, world, [("reagent", "6")])
        dispatch.confirm_delivery(
            admin,
            shipment.id,
            DeliveryDraft(
                outcomes=[OutcomeDraft(shipment_line_id=shipment.lines[0].id, delivered=D("6"))],
                recipient_name="J. Owusu",
            ),
        )
        line = shipment.lines[0]
        dispatch.customer_return(
            admin,
            shipment.id,
            line.id,
            quantity=D("1"),
            reason="Ordered in error",
            replacement_owed=False,
        )
        dispatch.customer_return(
            admin, shipment.id, line.id, quantity=D("1"), reason="Label damaged"
        )
        with pytest.raises(ConflictError):
            dispatch.customer_return(
                admin, shipment.id, line.id, quantity=D("5"), reason="Too many"
            )
        business = admin.business_id
        assert balance(db, clock, business, "50000") == D("1200")
        assert balance(db, clock, business, "12100") == D("600")
        order_line = world["reagent"]
        # One cancelled, one to be replaced: one outstanding.
        assert (order_line.quantity_delivered, order_line.quantity_cancelled) == (D("4"), D("1"))
        view = AssemblyService(db, clock=clock).workspace(admin, world["order"].id)
        reagent = next(item for item in view.lines if item.line_id == order_line.id)
        assert reagent.outstanding == D("1")
        agrees(db, clock, admin)

    def test_serialised_units_are_named_on_the_way_out_and_back(
        self, db: Session, clock: FixedClock, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        with pytest.raises(ValidationError):
            ship(dispatch, admin, world, [("analyser", "2", ["SN-0001"])])
        shipment = ship(dispatch, admin, world, [("analyser", "2", ["SN-0001", "SN-0002"])])
        dispatch.confirm_delivery(
            admin,
            shipment.id,
            DeliveryDraft(
                outcomes=[
                    OutcomeDraft(
                        shipment_line_id=shipment.lines[0].id,
                        delivered=D("1"),
                        damaged=D("1"),
                        reason="Screen cracked",
                        serials_delivered=["SN-0002"],
                    )
                ],
                recipient_name="Dr K. Asante",
            ),
        )
        assert balance(db, clock, admin.business_id, "12100") == D("9000")
        agrees(db, clock, admin)


# --------------------------------------------------------------------------------------
# Modes, states and permissions
# --------------------------------------------------------------------------------------


class TestModesAndControls:
    def _packed(self, admin: Actor, world: dict, dispatch: DispatchService) -> Shipment:
        shipment = dispatch.prepare(
            admin,
            PrepareDraft(
                sales_order_id=world["order"].id,
                lines=[ShipmentLineDraft(sales_order_line_id=world["gloves"].id, quantity=D("1"))],
            ),
        )
        return dispatch.pack(admin, shipment.id, package_count=1)

    def test_each_mode_records_what_it_needs(
        self, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = self._packed(admin, world, dispatch)
        with pytest.raises(ValidationError):
            dispatch.dispatch(
                admin, shipment.id, DispatchDraft(mode="carrier", package_count=1, destination="X")
            )
        with pytest.raises(ValidationError):
            dispatch.dispatch(
                admin,
                shipment.id,
                DispatchDraft(mode="own_vehicle", package_count=1, destination="Accra"),
            )
        collected = dispatch.dispatch(
            admin, shipment.id, DispatchDraft(mode="collection", package_count=1)
        )
        assert collected.state == "dispatched"
        assert collected.mode == "collection"

    def test_a_shipment_is_cancelled_only_before_it_leaves(
        self, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = self._packed(admin, world, dispatch)
        dispatch.cancel(admin, shipment.id, reason="Customer asked to hold")
        assert shipment.state == "cancelled"
        assert dispatch.shippable(world["gloves"]) == D("10")
        sent = ship(dispatch, admin, world, [("gloves", "1")])
        with pytest.raises(StateTransitionError):
            dispatch.cancel(admin, sent.id, reason="Too late")

    def test_a_salesperson_reads_shipments_and_cannot_dispatch(
        self, db: Session, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        clerk = make_actor(admin.business_id, permissions={"dispatch.view", "sales_order.view"})
        with pytest.raises(PermissionDeniedError):
            dispatch.prepare(
                clerk,
                PrepareDraft(
                    sales_order_id=world["order"].id,
                    lines=[
                        ShipmentLineDraft(sales_order_line_id=world["gloves"].id, quantity=D("1"))
                    ],
                ),
            )
        assert dispatch.shipments(clerk) == []

    def test_documents_carry_the_dispatch_detail(
        self, db: Session, admin: Actor, world: dict, dispatch: DispatchService
    ) -> None:
        shipment = ship(dispatch, admin, world, [("reagent", "2")])
        context = dispatch.context(admin, shipment, DocumentKind.DELIVERY_NOTE, number="DN-T-1")
        assert context.delivery is not None
        assert (context.delivery.carrier, context.delivery.package_count) == (
            "Speedline Logistics",
            3,
        )
        assert context.order_reference == world["order"].number
        assert [(line.batch, line.quantity) for line in context.lines] == [("RX-25088", D("2"))]
        note = dispatch.issue_document(admin, shipment.id, DocumentKind.DELIVERY_NOTE)
        stored = db.get(IssuedDocument, note.id)
        assert stored is not None and stored.byte_count > 0
