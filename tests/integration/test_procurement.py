"""Sales orders and procurement against a real database. PRD §6.8, §30, AC-07.

The exit criteria this file exists to hold:

1. An accepted order links to its supplier purchase orders and to the stock it draws on.
2. Quantity and price variance between order, receipt and invoice is detected and
   requires approval.
3. A supplier credit purchase creates a payable; payment reduces the payable and the
   cash without creating a second expense.
4. Cost variance between quoted and actual procurement cost is reported per line and
   per order.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.attachments import AttachmentService
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.inventory import (
    InventoryService,
    LandedChargeDraft,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.inventory_reports import InventoryReportService
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.application.services.payables import PayablesService, PaymentDraft
from nickland.application.services.procurement import (
    ProcurementService,
    PurchaseLineDraft,
    PurchaseOrderDraft,
    ReceiptDraft,
    ReceiptLineDraft,
)
from nickland.application.services.quotations import (
    LineDraft,
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.sourcing import (
    RequestDraft,
    RequestLineDraft,
    SourcingService,
    SupplierQuotationDraft,
    SupplierQuotationLineDraft,
)
from nickland.application.services.supplier_billing import (
    CreditNoteDraft,
    InvoiceDraft,
    InvoiceLineDraft,
    SupplierBillingService,
)
from nickland.application.services.units import UnitService
from nickland.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    StateTransitionError,
    ValidationError,
)
from nickland.domain.orders import (
    LinePosition,
    MatchTolerance,
    SalesOrderLineState,
    SalesOrderState,
    VarianceKind,
    ageing_bucket,
    allocate_payment,
    match_line,
    order_state,
)
from nickland.persistence.ledger_models import Account
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal
LATER = date(2027, 6, 30)
ON_THE_SHELF = D("8900")
"""The analyser held as opening stock, already in 12000 before anything is received."""
"""Balances are read well after every posting date, so nothing is excluded by the clock."""


# --------------------------------------------------------------------------------------
# A world: a sourced, issued quotation with a split line
# --------------------------------------------------------------------------------------


@pytest.fixture
def world(db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]) -> dict:
    parties = PartyService(db, clock=clock)
    customer = parties.create(admin, PartyDraft(name="Banhart Hospital", is_customer=True))
    gamma = parties.create(admin, PartyDraft(name="Gamma Laboratories", is_supplier=True))
    beta = parties.create(admin, PartyDraft(name="Beta Medical", is_supplier=True))
    db.flush()
    unit = UnitService(db, clock=clock).units(admin)[0]
    catalogue = CatalogueService(db, clock=clock)
    analyser = catalogue.create_product(
        admin, ProductDraft(sku="ANL-001", name="Haematology analyser", stock_unit_id=unit.id)
    )
    reagent = catalogue.create_product(
        admin, ProductDraft(sku="RGT-002", name="Reagent pack", stock_unit_id=unit.id)
    )
    db.flush()
    # One analyser on the shelf: the stock part of the split line draws on it.
    InventoryService(db, clock=clock).open_stock(
        admin,
        OpeningDraft(
            warehouse_id=provisioned["warehouse_id"],
            lines=[StockLineDraft(product_id=analyser.id, quantity=D("1"), unit_cost=D("8900"))],
        ),
    )

    sourcing = SourcingService(db, clock=clock)
    request = sourcing.create_request(
        admin,
        RequestDraft(
            customer_id=customer.id,
            lines=[
                RequestLineDraft(
                    description="Haematology analyser", quantity=D("2"), product_id=analyser.id
                ),
                RequestLineDraft(
                    description="Reagent pack", quantity=D("40"), product_id=reagent.id
                ),
            ],
        ),
    )
    sourcing.set_request_state(admin, request.id, "open")
    db.flush()
    lines = {line.description: line for line in request.lines}
    for supplier, prices in (
        (gamma, {"Reagent pack": "172"}),
        (beta, {"Haematology analyser": "9300"}),
    ):
        sourcing.record_supplier_quotation(
            admin,
            SupplierQuotationDraft(
                supplier_id=supplier.id,
                reference=f"{supplier.code}-Q1",
                request_id=request.id,
                lines=[
                    SupplierQuotationLineDraft(
                        description=name,
                        quantity_offered=lines[name].quantity,
                        unit_cost=D(price),
                        request_line_id=lines[name].id,
                        product_id=lines[name].product_fk,
                    )
                    for name, price in prices.items()
                ],
            ),
        )
    db.flush()
    offers = {
        item.description: {offer.supplier_name: offer for offer in item.offers}
        for item in sourcing.compare(admin, request.id)
    }
    quotations = QuotationService(db, clock=clock)
    quotation = quotations.create(
        admin,
        QuotationDraft(
            customer_id=customer.id,
            request_id=request.id,
            lines=[
                LineDraft(
                    description="Haematology analyser",
                    quantity=D("2"),
                    product_id=analyser.id,
                    sources=[
                        SourceDraft(
                            kind="supplier",
                            quantity=D("1"),
                            supplier_quotation_line_id=offers["Haematology analyser"][
                                "Beta Medical"
                            ].supplier_quotation_line_id,
                        ),
                        SourceDraft(
                            kind="stock",
                            quantity=D("1"),
                            unit_cost=D("8900"),
                            warehouse_id=provisioned["warehouse_id"],
                        ),
                    ],
                ),
                LineDraft(
                    description="Reagent pack",
                    quantity=D("40"),
                    product_id=reagent.id,
                    sources=[
                        SourceDraft(
                            kind="supplier",
                            quantity=D("40"),
                            supplier_quotation_line_id=offers["Reagent pack"][
                                "Gamma Laboratories"
                            ].supplier_quotation_line_id,
                        )
                    ],
                ),
            ],
        ),
    )
    db.flush()
    quotations.issue(admin, quotation.id)
    db.flush()
    return {
        "customer": customer,
        "gamma": gamma,
        "beta": beta,
        "analyser": analyser,
        "reagent": reagent,
        "quotation": quotation,
        "business_id": admin.business_id,
    }


@pytest.fixture
def orders(db: Session, clock: FixedClock) -> SalesOrderService:
    return SalesOrderService(db, clock=clock)


@pytest.fixture
def procurement(db: Session, clock: FixedClock) -> ProcurementService:
    return ProcurementService(db, clock=clock)


@pytest.fixture
def billing(db: Session, clock: FixedClock) -> SupplierBillingService:
    return SupplierBillingService(db, clock=clock)


@pytest.fixture
def payables(db: Session, clock: FixedClock) -> PayablesService:
    return PayablesService(db, clock=clock)


def balance(db: Session, clock: FixedClock, business_id: uuid.UUID, code: str) -> Decimal:
    account = db.scalar(
        select(Account).where(Account.business_id == business_id, Account.code == code)
    )
    assert account is not None
    return LedgerReportService(db, clock=clock).account_balance(
        business_id, account.id, as_at=LATER
    )


def stock_agrees(db: Session, clock: FixedClock, admin: Actor) -> None:
    reports = InventoryReportService(db, clock=clock)
    reconciliation = reports.reconcile(admin)
    assert reconciliation.agrees, reconciliation.discrepancies
    valuation = reports.valuation(admin)
    assert valuation.agrees, [(a.account, a.layers, a.ledger) for a in valuation.accounts]


def accepted_order(db: Session, orders: SalesOrderService, admin: Actor, world: dict):
    orders.record_acceptance(
        admin, world["quotation"].id, AcceptanceDraft(reference="BH-PO-2026-0441")
    )
    order = orders.convert(admin, world["quotation"].id, required_by=date(2026, 12, 15))
    db.flush()
    return order


def issued_purchase_orders(db, orders, procurement, admin, world):
    order = accepted_order(db, orders, admin, world)
    raised = []
    for draft in procurement.suggest_from_order(admin, order.id).values():
        purchase = procurement.raise_order(admin, draft)
        procurement.approve(admin, purchase.id)
        raised.append(procurement.issue(admin, purchase.id))
    db.flush()
    return order, raised


# --------------------------------------------------------------------------------------
# Pure rules
# --------------------------------------------------------------------------------------


class TestRules:
    def test_a_line_drawn_from_stock_is_fulfilled_at_once(self) -> None:
        position = LinePosition(ordered=D("2"), cancelled=D("0"), procured=D("2"), received=D("2"))
        assert position.state is SalesOrderLineState.FULFILLED

    def test_a_short_delivery_is_a_backorder_and_an_unasked_quantity_is_open(self) -> None:
        waiting = LinePosition(
            ordered=D("40"), cancelled=D("0"), procured=D("40"), received=D("30")
        )
        assert waiting.state is SalesOrderLineState.BACKORDERED
        unasked = LinePosition(ordered=D("40"), cancelled=D("0"), procured=D("0"), received=D("0"))
        assert unasked.state is SalesOrderLineState.OPEN

    def test_the_order_state_follows_its_lines(self) -> None:
        done = LinePosition(ordered=D("1"), cancelled=D("0"), procured=D("1"), received=D("1"))
        open_ = LinePosition(ordered=D("1"), cancelled=D("0"), procured=D("0"), received=D("0"))
        gone = LinePosition(ordered=D("1"), cancelled=D("1"), procured=D("0"), received=D("0"))
        assert order_state([done, done]) is SalesOrderState.FULFILLED
        assert order_state([done, open_]) is SalesOrderState.PARTIALLY_FULFILLED
        assert order_state([open_, open_]) is SalesOrderState.OPEN
        assert order_state([gone, gone]) is SalesOrderState.CANCELLED
        assert order_state([gone, done]) is SalesOrderState.FULFILLED

    def test_the_match_raises_a_price_variance_with_the_money_at_stake(self) -> None:
        found = match_line(
            ordered_quantity=D("40"),
            accepted_quantity=D("40"),
            ordered_unit_cost=D("172"),
            invoiced_quantity=D("40"),
            invoiced_unit_cost=D("175"),
        )
        assert [item.kind for item in found] == [VarianceKind.PRICE]
        assert found[0].amount_at_stake(quantity=D("40"), unit_cost=D("175")) == D("120")

    def test_the_match_raises_a_quantity_variance_against_what_was_accepted(self) -> None:
        found = match_line(
            ordered_quantity=D("40"),
            accepted_quantity=D("30"),
            ordered_unit_cost=D("172"),
            invoiced_quantity=D("40"),
            invoiced_unit_cost=D("172"),
        )
        assert [item.kind for item in found] == [VarianceKind.QUANTITY]
        assert found[0].amount_at_stake(quantity=D("40"), unit_cost=D("172")) == D("1720")

    def test_a_tolerance_lets_a_small_price_difference_through(self) -> None:
        found = match_line(
            ordered_quantity=D("40"),
            accepted_quantity=D("40"),
            ordered_unit_cost=D("172"),
            invoiced_quantity=D("40"),
            invoiced_unit_cost=D("172.50"),
            tolerance=MatchTolerance(absolute=D("1")),
        )
        assert found == []

    def test_ageing_buckets(self) -> None:
        due = date(2026, 11, 20)
        assert ageing_bucket(due, date(2026, 11, 20)).value == "current"
        assert ageing_bucket(due, date(2026, 12, 1)).value == "1_30"
        assert ageing_bucket(due, date(2026, 12, 31)).value == "31_60"
        assert ageing_bucket(due, date(2027, 2, 1)).value == "61_90"
        assert ageing_bucket(due, date(2027, 6, 1)).value == "over_90"

    def test_a_payment_is_spread_oldest_first_and_never_beyond_what_is_owed(self) -> None:
        spread = allocate_payment(D("150"), [("a", D("100")), ("b", D("100"))])
        assert spread == [("a", D("100")), ("b", D("50"))]
        with pytest.raises(ValidationError):
            allocate_payment(D("250"), [("a", D("100")), ("b", D("100"))])


# --------------------------------------------------------------------------------------
# Acceptance and conversion
# --------------------------------------------------------------------------------------


class TestAcceptanceAndConversion:
    def test_conversion_needs_an_acceptance_on_record(
        self, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        with pytest.raises(ConflictError):
            orders.convert(admin, world["quotation"].id)

    def test_a_written_acceptance_needs_a_reference_or_the_document(
        self, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        with pytest.raises(ValidationError):
            orders.record_acceptance(admin, world["quotation"].id, AcceptanceDraft(method="email"))

    def test_a_verbal_acceptance_records_who_said_what(
        self, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        with pytest.raises(ValidationError):
            orders.record_acceptance(admin, world["quotation"].id, AcceptanceDraft(method="verbal"))
        acceptance = orders.record_acceptance(
            admin,
            world["quotation"].id,
            AcceptanceDraft(method="verbal", notes="Dr Owusu confirmed by telephone at 10:15."),
        )
        assert acceptance.method == "verbal"

    def test_every_quoted_figure_is_carried_across(
        self, db: Session, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        order = accepted_order(db, orders, admin, world)
        quotation = world["quotation"]
        assert order.number.startswith("SO-")
        assert order.total_payable == quotation.total_payable
        assert order.net_total == quotation.net_total
        assert order.customer_reference == "BH-PO-2026-0441"
        assert [line.description for line in order.lines] == [
            line.description for line in quotation.lines
        ]
        for ours, theirs in zip(order.lines, quotation.lines, strict=True):
            assert ours.unit_price == theirs.unit_net_price
            assert ours.line_total == theirs.net_price
            assert ours.quantity_ordered == theirs.quantity

    def test_the_stock_part_of_a_split_line_is_drawn_on_conversion(
        self, db: Session, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        order = accepted_order(db, orders, admin, world)
        analyser = order.lines[0]
        assert analyser.quantity_procured == D("1")
        assert analyser.quantity_received == D("1")
        assert analyser.state == SalesOrderLineState.OPEN.value
        assert order.state == SalesOrderState.PARTIALLY_FULFILLED.value
        assert InventoryService(db).reserved_for(analyser.id) == D("1")

    def test_the_stock_part_is_not_assumed_when_the_shelf_is_empty(
        self, db: Session, clock: FixedClock, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        inventory = InventoryService(db, clock=clock)
        held = inventory.reserve(
            admin,
            product_id=world["analyser"].id,
            warehouse_id=inventory.default_warehouse(admin.business_id, admin.branch_id).id,
            quantity=D("1"),
        )
        order = accepted_order(db, orders, admin, world)
        analyser = order.lines[0]
        assert (analyser.quantity_procured, analyser.quantity_received) == (D("0"), D("0"))
        assert inventory.reserved_for(analyser.id) == D("0")
        inventory.release(admin, held.id)

    def test_a_quotation_converts_once(
        self, db: Session, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        accepted_order(db, orders, admin, world)
        with pytest.raises(ConflictError):
            orders.convert(admin, world["quotation"].id)

    def test_evidence_is_stored_hashed_and_immutable(
        self, db: Session, clock: FixedClock, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        attachments = AttachmentService(db, clock=clock)
        stored = attachments.store(
            admin,
            content=b"%PDF-1.4 customer purchase order",
            filename="BH-PO.pdf",
            media_type="application/pdf",
            owner_type="quotation",
            owner_id=world["quotation"].id,
            purpose="acceptance",
            immutable=True,
        )
        acceptance = orders.record_acceptance(
            admin,
            world["quotation"].id,
            AcceptanceDraft(reference="BH-PO-2026-0441", evidence_attachment_id=stored.id),
        )
        assert acceptance.evidence_attachment_fk == stored.id
        read = attachments.read(admin, stored.id)
        assert read.content == b"%PDF-1.4 customer purchase order"
        assert len(stored.content_hash) == 64
        with pytest.raises(ConflictError):
            attachments.remove(admin, stored.id)

    def test_an_executable_cannot_be_attached(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError):
            AttachmentService(db, clock=clock).store(
                admin, content=b"MZ", filename="setup.exe", media_type="application/x-msdownload"
            )

    def test_a_salesperson_sees_orders_and_cannot_cancel_them(
        self, db: Session, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        order = accepted_order(db, orders, admin, world)
        seller = make_actor(admin.business_id, permissions={"sales_order.view"})
        assert orders.require(seller, order.id).number == order.number
        with pytest.raises(PermissionDeniedError):
            orders.cancel(seller, order.id, reason="Customer withdrew")


# --------------------------------------------------------------------------------------
# Exit criterion 1: the order links to its purchase orders and its stock
# --------------------------------------------------------------------------------------


class TestPurchaseOrders:
    def test_one_order_is_served_by_several_purchase_orders(
        self,
        db: Session,
        orders: SalesOrderService,
        procurement: ProcurementService,
        admin: Actor,
        world: dict,
    ) -> None:
        order, raised = issued_purchase_orders(db, orders, procurement, admin, world)
        linked = orders.purchase_orders_for(admin, order.id)
        assert len(raised) == 2
        assert {item.number for item in linked} == {item.number for item in raised}
        assert all(item.number.startswith("PO-") for item in raised)
        by_supplier = {item.supplier_fk: item for item in raised}
        assert by_supplier[world["beta"].id].lines[0].quantity_ordered == D("1")
        assert by_supplier[world["gamma"].id].lines[0].quantity_ordered == D("40")
        order = orders.require(admin, order.id)
        assert [line.quantity_procured for line in order.lines] == [D("2"), D("40")]
        assert all(line.state == SalesOrderLineState.BACKORDERED.value for line in order.lines)

    def test_a_suggestion_covers_only_what_is_not_yet_procured(
        self,
        db: Session,
        orders: SalesOrderService,
        procurement: ProcurementService,
        admin: Actor,
        world: dict,
    ) -> None:
        issued_purchase_orders(db, orders, procurement, admin, world)
        order = orders.orders(admin)[0]
        assert procurement.suggest_from_order(admin, order.id) == {}

    def test_a_purchase_order_is_numbered_at_issue_not_before(
        self,
        db: Session,
        orders: SalesOrderService,
        procurement: ProcurementService,
        admin: Actor,
        world: dict,
    ) -> None:
        order = accepted_order(db, orders, admin, world)
        draft = next(iter(procurement.suggest_from_order(admin, order.id).values()))
        purchase = procurement.raise_order(admin, draft)
        assert purchase.number is None
        with pytest.raises(StateTransitionError):
            procurement.issue(admin, purchase.id)  # not yet approved
        procurement.approve(admin, purchase.id)
        assert procurement.issue(admin, purchase.id).number is not None

    def test_approval_is_a_separate_permission_from_raising(
        self,
        db: Session,
        orders: SalesOrderService,
        procurement: ProcurementService,
        admin: Actor,
        world: dict,
    ) -> None:
        order = accepted_order(db, orders, admin, world)
        buyer = make_actor(
            admin.business_id,
            permissions={"purchase_order.manage", "purchase_order.view", "sales_order.view"},
        )
        draft = next(iter(procurement.suggest_from_order(buyer, order.id).values()))
        purchase = procurement.raise_order(buyer, draft)
        with pytest.raises(PermissionDeniedError):
            procurement.approve(buyer, purchase.id)

    def test_a_replenishment_order_needs_no_sales_order(
        self, db: Session, procurement: ProcurementService, admin: Actor, world: dict
    ) -> None:
        purchase = procurement.raise_order(
            admin,
            PurchaseOrderDraft(
                supplier_id=world["gamma"].id,
                purpose="replenishment",
                lines=[
                    PurchaseLineDraft(
                        description="Reagent pack",
                        quantity=D("20"),
                        unit_cost=D("172"),
                        product_id=world["reagent"].id,
                    )
                ],
            ),
        )
        assert purchase.sales_order_fk is None
        assert purchase.net_total == D("3440")

    def test_cancelling_an_issued_order_releases_what_it_procured(
        self,
        db: Session,
        orders: SalesOrderService,
        procurement: ProcurementService,
        admin: Actor,
        world: dict,
    ) -> None:
        order, raised = issued_purchase_orders(db, orders, procurement, admin, world)
        gamma = next(item for item in raised if item.supplier_fk == world["gamma"].id)
        procurement.cancel(admin, gamma.id, reason="Supplier cannot deliver in time")
        order = orders.require(admin, order.id)
        assert order.lines[1].quantity_procured == D("0")
        assert order.lines[1].state == SalesOrderLineState.OPEN.value


# --------------------------------------------------------------------------------------
# Receiving
# --------------------------------------------------------------------------------------


class TestReceiving:
    def test_a_short_receipt_backorders_and_a_further_receipt_fulfils(
        self, db: Session, clock: FixedClock, orders, procurement, admin: Actor, world: dict
    ) -> None:
        order, raised = issued_purchase_orders(db, orders, procurement, admin, world)
        gamma = next(item for item in raised if item.supplier_fk == world["gamma"].id)
        line = gamma.lines[0]
        first = procurement.receive(
            admin,
            ReceiptDraft(
                purchase_order_id=gamma.id,
                lines=[ReceiptLineDraft(purchase_order_line_id=line.id, quantity_received=D("30"))],
            ),
        )
        db.flush()
        assert first.number.startswith("GRN-")
        assert first.accepted_value == D("5160")
        assert gamma.state == "partially_received"
        order = orders.require(admin, order.id)
        assert order.lines[1].state == SalesOrderLineState.BACKORDERED.value
        assert order.lines[1].quantity_received == D("30")

        procurement.receive(
            admin,
            ReceiptDraft(
                purchase_order_id=gamma.id,
                lines=[ReceiptLineDraft(purchase_order_line_id=line.id, quantity_received=D("10"))],
            ),
        )
        db.flush()
        order = orders.require(admin, order.id)
        assert gamma.state == "received"
        assert order.lines[1].state == SalesOrderLineState.FULFILLED.value

    def test_a_receipt_posts_inventory_against_the_accrual(
        self, db: Session, clock: FixedClock, orders, procurement, admin: Actor, world: dict
    ) -> None:
        _, raised = issued_purchase_orders(db, orders, procurement, admin, world)
        gamma = next(item for item in raised if item.supplier_fk == world["gamma"].id)
        receipt = procurement.receive(
            admin,
            ReceiptDraft(
                purchase_order_id=gamma.id,
                lines=[
                    ReceiptLineDraft(
                        purchase_order_line_id=gamma.lines[0].id, quantity_received=D("40")
                    )
                ],
            ),
        )
        db.flush()
        assert receipt.journal_entry_fk is not None
        business_id = admin.business_id
        assert balance(db, clock, business_id, "12000") == ON_THE_SHELF + D("6880")
        assert balance(db, clock, business_id, "20150") == D("6880")
        stock_agrees(db, clock, admin)
        # Bought for the order line, so held for it as soon as it is usable.
        assert InventoryService(db).reserved_for(gamma.lines[0].sales_order_line_fk) == D("40")

    def test_rejected_goods_are_counted_but_not_valued(
        self, db: Session, clock: FixedClock, orders, procurement, admin: Actor, world: dict
    ) -> None:
        _, raised = issued_purchase_orders(db, orders, procurement, admin, world)
        gamma = next(item for item in raised if item.supplier_fk == world["gamma"].id)
        receipt = procurement.receive(
            admin,
            ReceiptDraft(
                purchase_order_id=gamma.id,
                lines=[
                    ReceiptLineDraft(
                        purchase_order_line_id=gamma.lines[0].id,
                        quantity_received=D("40"),
                        quantity_rejected=D("4"),
                        rejection_reason="Four packs water damaged",
                    )
                ],
            ),
        )
        db.flush()
        assert receipt.lines[0].quantity_accepted == D("36")
        assert receipt.accepted_value == D("6192")
        assert gamma.lines[0].quantity_rejected == D("4")

    def test_over_delivery_is_refused(
        self, db: Session, orders, procurement, admin: Actor, world: dict
    ) -> None:
        _, raised = issued_purchase_orders(db, orders, procurement, admin, world)
        gamma = next(item for item in raised if item.supplier_fk == world["gamma"].id)
        with pytest.raises(ConflictError):
            procurement.receive(
                admin,
                ReceiptDraft(
                    purchase_order_id=gamma.id,
                    lines=[
                        ReceiptLineDraft(
                            purchase_order_line_id=gamma.lines[0].id, quantity_received=D("41")
                        )
                    ],
                ),
            )

    def test_goods_pending_inspection_go_to_quarantine(
        self, db: Session, clock: FixedClock, orders, procurement, admin: Actor, world: dict
    ) -> None:
        _, raised = issued_purchase_orders(db, orders, procurement, admin, world)
        gamma = next(item for item in raised if item.supplier_fk == world["gamma"].id)
        procurement.receive(
            admin,
            ReceiptDraft(
                purchase_order_id=gamma.id,
                inspection_pending=True,
                lines=[
                    ReceiptLineDraft(
                        purchase_order_line_id=gamma.lines[0].id, quantity_received=D("40")
                    )
                ],
            ),
        )
        db.flush()
        assert balance(db, clock, admin.business_id, "12100") == D("6880")
        assert balance(db, clock, admin.business_id, "12000") == ON_THE_SHELF + D("0")


# --------------------------------------------------------------------------------------
# Exit criteria 2 and 3: the match, the payable, the payment
# --------------------------------------------------------------------------------------


def received_gamma(db, orders, procurement, admin, world, quantity="40"):
    order, raised = issued_purchase_orders(db, orders, procurement, admin, world)
    gamma = next(item for item in raised if item.supplier_fk == world["gamma"].id)
    procurement.receive(
        admin,
        ReceiptDraft(
            purchase_order_id=gamma.id,
            lines=[
                ReceiptLineDraft(
                    purchase_order_line_id=gamma.lines[0].id, quantity_received=D(quantity)
                )
            ],
        ),
    )
    db.flush()
    return order, gamma


class TestThreeWayMatch:
    def test_a_price_that_moved_is_a_variance_that_needs_approval(
        self,
        db: Session,
        orders,
        procurement,
        billing: SupplierBillingService,
        admin: Actor,
        world: dict,
    ) -> None:
        _, gamma = received_gamma(db, orders, procurement, admin, world)
        invoice = billing.record(
            admin,
            InvoiceDraft(
                purchase_order_id=gamma.id,
                supplier_reference="GL-7731",
                invoiced_on=date(2026, 11, 20),
                lines=[
                    InvoiceLineDraft(
                        purchase_order_line_id=gamma.lines[0].id,
                        quantity=D("40"),
                        unit_cost=D("175"),
                    )
                ],
            ),
        )
        db.flush()
        assert invoice.state == "variance"
        assert [item.kind for item in invoice.variances] == ["price"]
        assert invoice.variances[0].amount_at_stake == D("120")
        with pytest.raises(StateTransitionError):
            billing.post(admin, invoice.id)

    def test_approving_the_variance_lets_the_invoice_post(
        self,
        db: Session,
        clock: FixedClock,
        orders,
        procurement,
        billing: SupplierBillingService,
        admin: Actor,
        world: dict,
    ) -> None:
        _, gamma = received_gamma(db, orders, procurement, admin, world)
        invoice = billing.record(
            admin,
            InvoiceDraft(
                purchase_order_id=gamma.id,
                supplier_reference="GL-7731",
                invoiced_on=date(2026, 11, 20),
                lines=[
                    InvoiceLineDraft(
                        purchase_order_line_id=gamma.lines[0].id,
                        quantity=D("40"),
                        unit_cost=D("175"),
                    )
                ],
            ),
        )
        db.flush()
        billing.decide_variance(
            admin, invoice.variances[0].id, approve=True, reason="Rise agreed on 18 Nov"
        )
        assert invoice.state == "matched"
        posted = billing.post(admin, invoice.id)
        assert posted.state == "posted"
        assert posted.number.startswith("SIN-")
        # The approved rise of 3 a pack follows the 40 packs into their layer.
        assert balance(db, clock, admin.business_id, "12000") == ON_THE_SHELF + D("7000")
        stock_agrees(db, clock, admin)

    def test_a_rejected_variance_returns_the_invoice_for_correction(
        self,
        db: Session,
        orders,
        procurement,
        billing: SupplierBillingService,
        admin: Actor,
        world: dict,
    ) -> None:
        _, gamma = received_gamma(db, orders, procurement, admin, world)
        invoice = billing.record(
            admin,
            InvoiceDraft(
                purchase_order_id=gamma.id,
                supplier_reference="GL-7731",
                invoiced_on=date(2026, 11, 20),
                lines=[
                    InvoiceLineDraft(
                        purchase_order_line_id=gamma.lines[0].id,
                        quantity=D("40"),
                        unit_cost=D("175"),
                    )
                ],
            ),
        )
        db.flush()
        billing.decide_variance(admin, invoice.variances[0].id, approve=False, reason="Not agreed")
        assert invoice.state == "draft"
        amended = billing.amend(
            admin,
            invoice.id,
            InvoiceDraft(
                purchase_order_id=gamma.id,
                supplier_reference="GL-7731",
                invoiced_on=date(2026, 11, 20),
                lines=[
                    InvoiceLineDraft(
                        purchase_order_line_id=gamma.lines[0].id,
                        quantity=D("40"),
                        unit_cost=D("172"),
                    )
                ],
            ),
        )
        assert amended.state == "matched"

    def test_an_invoice_for_more_than_arrived_is_a_quantity_variance(
        self,
        db: Session,
        orders,
        procurement,
        billing: SupplierBillingService,
        admin: Actor,
        world: dict,
    ) -> None:
        _, gamma = received_gamma(db, orders, procurement, admin, world, quantity="30")
        invoice = billing.record(
            admin,
            InvoiceDraft(
                purchase_order_id=gamma.id,
                supplier_reference="GL-7731",
                invoiced_on=date(2026, 11, 20),
                lines=[
                    InvoiceLineDraft(
                        purchase_order_line_id=gamma.lines[0].id,
                        quantity=D("40"),
                        unit_cost=D("172"),
                    )
                ],
            ),
        )
        assert [item.kind for item in invoice.variances] == ["quantity"]
        assert invoice.variances[0].amount_at_stake == D("1720")

    def test_variance_approval_is_its_own_permission(
        self,
        db: Session,
        orders,
        procurement,
        billing: SupplierBillingService,
        admin: Actor,
        world: dict,
    ) -> None:
        _, gamma = received_gamma(db, orders, procurement, admin, world)
        invoice = billing.record(
            admin,
            InvoiceDraft(
                purchase_order_id=gamma.id,
                supplier_reference="GL-7731",
                invoiced_on=date(2026, 11, 20),
                lines=[
                    InvoiceLineDraft(
                        purchase_order_line_id=gamma.lines[0].id,
                        quantity=D("40"),
                        unit_cost=D("175"),
                    )
                ],
            ),
        )
        clerk = make_actor(
            admin.business_id, permissions={"supplier_invoice.manage", "supplier_invoice.view"}
        )
        with pytest.raises(PermissionDeniedError):
            billing.decide_variance(clerk, invoice.variances[0].id, approve=True, reason="ok")

    def test_the_same_supplier_invoice_is_recorded_once(
        self,
        db: Session,
        orders,
        procurement,
        billing: SupplierBillingService,
        admin: Actor,
        world: dict,
    ) -> None:
        _, gamma = received_gamma(db, orders, procurement, admin, world)
        draft = InvoiceDraft(
            purchase_order_id=gamma.id,
            supplier_reference="GL-7731",
            invoiced_on=date(2026, 11, 20),
            lines=[
                InvoiceLineDraft(
                    purchase_order_line_id=gamma.lines[0].id, quantity=D("40"), unit_cost=D("172")
                )
            ],
        )
        billing.record(admin, draft)
        db.flush()
        with pytest.raises(ConflictError):
            billing.record(admin, draft)

    def test_a_tolerance_is_set_by_whoever_approves_variances(
        self, db: Session, billing: SupplierBillingService, admin: Actor
    ) -> None:
        clerk = make_actor(admin.business_id, permissions={"supplier_invoice.manage"})
        with pytest.raises(PermissionDeniedError):
            billing.set_tolerance(clerk, absolute=D("1"))
        assert billing.set_tolerance(admin, absolute=D("1")).absolute == D("1")


class TestPayables:
    def _posted_invoice(self, db, orders, procurement, billing, admin, world):
        order, gamma = received_gamma(db, orders, procurement, admin, world)
        invoice = billing.record(
            admin,
            InvoiceDraft(
                purchase_order_id=gamma.id,
                supplier_reference="GL-7731",
                invoiced_on=date(2026, 11, 20),
                lines=[
                    InvoiceLineDraft(
                        purchase_order_line_id=gamma.lines[0].id,
                        quantity=D("40"),
                        unit_cost=D("172"),
                        recoverable_tax=D("1032"),
                    )
                ],
            ),
        )
        db.flush()
        return order, billing.post(admin, invoice.id)

    def test_a_credit_purchase_creates_a_payable_through_the_ledger(
        self,
        db: Session,
        clock: FixedClock,
        orders,
        procurement,
        billing,
        admin: Actor,
        world: dict,
    ) -> None:
        _, invoice = self._posted_invoice(db, orders, procurement, billing, admin, world)
        db.flush()
        business_id = admin.business_id
        assert invoice.base_total == D("7912")
        assert balance(db, clock, business_id, "20000") == D("7912")
        assert balance(db, clock, business_id, "20150") == D("0"), "the accrual is cleared"
        assert balance(db, clock, business_id, "12000") == ON_THE_SHELF + D("6880")
        assert balance(db, clock, business_id, "13000") == D("1032")

    def test_payment_reduces_the_payable_and_the_cash_and_posts_no_expense(
        self,
        db: Session,
        clock: FixedClock,
        orders,
        procurement,
        billing,
        payables: PayablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        _, invoice = self._posted_invoice(db, orders, procurement, billing, admin, world)
        db.flush()
        business_id = admin.business_id
        expenses_before = balance(db, clock, business_id, "50000")
        payment = payables.pay(
            admin,
            PaymentDraft(
                supplier_id=world["gamma"].id,
                amount=D("5000"),
                method="bank",
                reference="GCB-558812",
            ),
        )
        db.flush()
        assert payment.number.startswith("PV-")
        assert invoice.state == "partially_paid"
        assert invoice.amount_paid == D("5000")
        assert balance(db, clock, business_id, "20000") == D("2912")
        assert balance(db, clock, business_id, "10200") == D("-5000")
        assert balance(db, clock, business_id, "50000") == expenses_before
        assert balance(db, clock, business_id, "12000") == ON_THE_SHELF + D("6880"), (
            "inventory is untouched by paying"
        )

    def test_withholding_settles_the_supplier_but_not_the_cash(
        self,
        db: Session,
        clock: FixedClock,
        orders,
        procurement,
        billing,
        payables: PayablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        _, invoice = self._posted_invoice(db, orders, procurement, billing, admin, world)
        db.flush()
        payables.pay(
            admin,
            PaymentDraft(
                supplier_id=world["gamma"].id,
                amount=D("7318.60"),
                withheld=D("593.40"),
                method="bank",
            ),
        )
        db.flush()
        business_id = admin.business_id
        assert invoice.state == "paid"
        assert balance(db, clock, business_id, "20000") == D("0")
        assert balance(db, clock, business_id, "21100") == D("593.40")
        assert balance(db, clock, business_id, "10200") == D("-7318.60")

    def test_an_overpayment_is_refused(
        self,
        db: Session,
        orders,
        procurement,
        billing,
        payables: PayablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        self._posted_invoice(db, orders, procurement, billing, admin, world)
        db.flush()
        with pytest.raises(ValidationError):
            payables.pay(admin, PaymentDraft(supplier_id=world["gamma"].id, amount=D("99999")))

    def test_the_register_ages_by_due_date_and_reconciles_to_the_control_account(
        self,
        db: Session,
        clock: FixedClock,
        orders,
        procurement,
        billing,
        payables: PayablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        self._posted_invoice(db, orders, procurement, billing, admin, world)
        db.flush()
        register = payables.register(admin, as_at=date(2026, 12, 31))
        assert register.total_outstanding == D("7912")
        assert register.by_bucket["31_60"] == D("7912")
        assert register.rows[0].supplier_name == "Gamma Laboratories"
        assert register.total_outstanding == balance(db, clock, admin.business_id, "20000")

    def test_a_credit_note_reduces_the_payable_and_inventory(
        self,
        db: Session,
        clock: FixedClock,
        orders,
        procurement,
        billing,
        admin: Actor,
        world: dict,
    ) -> None:
        _, invoice = self._posted_invoice(db, orders, procurement, billing, admin, world)
        db.flush()
        billing.credit(
            admin,
            CreditNoteDraft(
                invoice_id=invoice.id,
                supplier_reference="GL-CN-12",
                amount=D("344"),
                reason="Two packs short",
            ),
        )
        db.flush()
        business_id = admin.business_id
        assert invoice.amount_credited == D("344")
        assert balance(db, clock, business_id, "20000") == D("7568")
        assert balance(db, clock, business_id, "12000") == ON_THE_SHELF + D("6536")
        stock_agrees(db, clock, admin)

    def test_a_landed_charge_is_carried_into_the_goods(
        self,
        db: Session,
        clock: FixedClock,
        orders,
        procurement,
        admin: Actor,
        world: dict,
    ) -> None:
        _, gamma = received_gamma(db, orders, procurement, admin, world)
        receipt = procurement.receipts(admin, order_id=gamma.id)[0]
        InventoryService(db, clock=clock).landed_charge(
            admin,
            LandedChargeDraft(receipt_id=receipt.id, category="freight", amount=D("120")),
        )
        assert balance(db, clock, admin.business_id, "12000") == ON_THE_SHELF + D("7000")
        assert balance(db, clock, admin.business_id, "20100") == D("120")
        stock_agrees(db, clock, admin)


# --------------------------------------------------------------------------------------
# Exit criterion 4: cost variance
# --------------------------------------------------------------------------------------


class TestCostVariance:
    def test_variance_is_reported_per_line_and_per_order(
        self,
        db: Session,
        orders: SalesOrderService,
        procurement,
        billing,
        admin: Actor,
        world: dict,
    ) -> None:
        order, raised = issued_purchase_orders(db, orders, procurement, admin, world)
        before = orders.cost_variance(admin, order.id)
        assert before.lines_known == 0
        assert before.actual_total is None

        for purchase, price in ((world["gamma"], "175"), (world["beta"], "9300")):
            po = next(item for item in raised if item.supplier_fk == purchase.id)
            line = po.lines[0]
            procurement.receive(
                admin,
                ReceiptDraft(
                    purchase_order_id=po.id,
                    lines=[
                        ReceiptLineDraft(
                            purchase_order_line_id=line.id, quantity_received=line.quantity_ordered
                        )
                    ],
                ),
            )
            invoice = billing.record(
                admin,
                InvoiceDraft(
                    purchase_order_id=po.id,
                    supplier_reference=f"{purchase.code}-INV",
                    invoiced_on=date(2026, 11, 20),
                    lines=[
                        InvoiceLineDraft(
                            purchase_order_line_id=line.id,
                            quantity=line.quantity_ordered,
                            unit_cost=D(price),
                        )
                    ],
                ),
            )
            for variance in list(invoice.variances):
                billing.decide_variance(admin, variance.id, approve=True, reason="Agreed")
            billing.post(admin, invoice.id)
        db.flush()

        after = orders.cost_variance(admin, order.id)
        assert after.lines_known == 2
        reagent = next(line for line in after.lines if line.description == "Reagent pack")
        assert reagent.actual_unit_cost == D("175.00")
        assert reagent.per_unit == D("175.00") - reagent.expected_unit_cost
        assert reagent.total == reagent.per_unit * D("40")
        assert after.variance_total == after.actual_total - after.expected_total

    def test_the_report_sits_behind_the_costing_permission(
        self, db: Session, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        order = accepted_order(db, orders, admin, world)
        seller = make_actor(admin.business_id, permissions={"sales_order.view"})
        with pytest.raises(PermissionDeniedError):
            orders.cost_variance(seller, order.id)

    def test_another_business_s_order_is_not_found(
        self, db: Session, orders: SalesOrderService, admin: Actor, world: dict
    ) -> None:
        order = accepted_order(db, orders, admin, world)
        with pytest.raises(NotFoundError):
            orders.require(make_actor(uuid.uuid4()), order.id)
