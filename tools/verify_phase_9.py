#!/usr/bin/env python3
"""Verify each Phase 9 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_9.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase9-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.assembly import AssemblyService  # noqa: E402
from nickland.application.services.catalogue import CatalogueService, ProductDraft  # noqa: E402
from nickland.application.services.dispatch import (  # noqa: E402
    DeliveryDraft,
    DispatchDraft,
    DispatchService,
    OutcomeDraft,
    PrepareDraft,
    ShipmentLineDraft,
)
from nickland.application.services.documents import DocumentRecordService  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.inventory import (  # noqa: E402
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.inventory_reports import (  # noqa: E402
    InventoryReportService,
)
from nickland.application.services.ledger_reports import LedgerReportService  # noqa: E402
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService  # noqa: E402
from nickland.application.services.parties import PartyDraft, PartyService  # noqa: E402
from nickland.application.services.quotations import (  # noqa: E402
    LineDraft,
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.documents.service import DocumentRenderService  # noqa: E402
from nickland.domain.documents import DocumentKind  # noqa: E402
from nickland.domain.money import plain  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.immutability import register_append_only_guards  # noqa: E402
from nickland.persistence.ledger_models import Account  # noqa: E402
from nickland.persistence.models import Business, Warehouse  # noqa: E402
from nickland.persistence.order_models import SalesOrder, SalesOrderLine  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
D = Decimal
LATER = date(2027, 12, 31)
SIGNATURE = b"\x89PNG\r\n\x1a\n recipient signature"
Report = tuple[str, bool, str]


def build() -> tuple[Session, Actor, SalesOrder]:
    """A converted order of two stock lines, reserved from opening stock."""
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-9')"))
    factory = create_session_factory(engine)
    register_append_only_guards(factory)
    session = factory()
    SetupService(session).provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password=ADMIN,
            theme_preset="nickland",
        )
    )
    session.commit()
    business_id = session.scalar(select(Business.id))
    assert business_id is not None
    actor = IdentityService(session).sign_in("proprietor", ADMIN, business_id=business_id).actor
    warehouse = session.scalar(select(Warehouse.id))
    assert warehouse is not None
    customer = PartyService(session).create(
        actor, PartyDraft(name="Korle Bu Teaching Hospital", is_customer=True)
    )
    unit = UnitService(session).units(actor)[0]
    catalogue = CatalogueService(session)
    gloves = catalogue.create_product(
        actor, ProductDraft(sku="GLV-100", name="Nitrile gloves", stock_unit_id=unit.id)
    )
    reagent = catalogue.create_product(
        actor,
        ProductDraft(
            sku="RGT-200",
            name="Glucose reagent",
            stock_unit_id=unit.id,
            tracking_policy="batch",
            requires_expiry=True,
        ),
    )
    session.flush()
    InventoryService(session).open_stock(
        actor,
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
            ],
        ),
    )
    quotations = QuotationService(session)
    quotation = quotations.create(
        actor,
        QuotationDraft(
            customer_id=customer.id,
            lines=[
                LineDraft(
                    description=product.name,
                    quantity=D(quantity),
                    product_id=product.id,
                    sources=[
                        SourceDraft(
                            kind="stock",
                            quantity=D(quantity),
                            unit_cost=D(cost),
                            warehouse_id=warehouse,
                        )
                    ],
                )
                for product, quantity, cost in ((gloves, "10", "20"), (reagent, "6", "300"))
            ],
        ),
    )
    session.flush()
    quotations.issue(actor, quotation.id)
    orders = SalesOrderService(session)
    orders.record_acceptance(actor, quotation.id, AcceptanceDraft(reference="KBTH-PO-118"))
    order = orders.convert(actor, quotation.id)
    session.commit()
    return session, actor, order


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, actor, order = build()
    business_id = actor.business_id
    assert business_id is not None
    dispatch = DispatchService(session)
    render = DocumentRenderService(session)
    lines = {line.description: line for line in order.lines}
    gloves: SalesOrderLine = lines["Nitrile gloves"]
    reagent: SalesOrderLine = lines["Glucose reagent"]

    def balance(code: str) -> Decimal:
        account = session.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )
        assert account is not None
        return LedgerReportService(session).account_balance(business_id, account.id, as_at=LATER)

    def send(pairs: list[tuple[SalesOrderLine, str]]) -> uuid.UUID:
        shipment = dispatch.prepare(
            actor,
            PrepareDraft(
                sales_order_id=order.id,
                destination="Stores, Korle Bu Teaching Hospital, Accra",
                lines=[
                    ShipmentLineDraft(sales_order_line_id=line.id, quantity=D(quantity))
                    for line, quantity in pairs
                ],
            ),
        )
        dispatch.pack(actor, shipment.id, package_count=2)
        dispatch.dispatch(
            actor,
            shipment.id,
            DispatchDraft(
                mode="own_vehicle", package_count=2, driver="K. Boateng", vehicle="GR 2214-24"
            ),
        )
        render.issue_for_shipment(actor, shipment.id, DocumentKind.WAYBILL)
        session.commit()
        return shipment.id

    # -- 1. The signed waybill is linked to the order and reproducible ----------------
    first_id = send([(gloves, "6")])
    first = dispatch.require(actor, first_id)
    dispatch.confirm_delivery(
        actor,
        first_id,
        DeliveryDraft(
            outcomes=[
                OutcomeDraft(
                    shipment_line_id=first.lines[0].id,
                    delivered=D("4"),
                    remainder_as="returned",
                    reason="Two cartons refused at goods-inwards",
                )
            ],
            recipient_name="Sister A. Mensah",
            recipient_role="Stores supervisor",
            evidence=SIGNATURE,
            media_type="image/png",
        ),
    )
    session.commit()
    assert first.waybill_fk is not None
    waybill = DocumentRecordService(session).require_issued(actor, first.waybill_fk)
    proofs = DocumentRecordService(session).acknowledgements(actor, waybill.id)
    reprinted = render.reprint(actor, waybill.id)
    record(
        "A waybill and its signed proof of receipt are linked to the order and reproducible",
        first.sales_order_fk == order.id
        and (waybill.source_type, waybill.source_id) == ("shipment", first.id)
        and len(proofs) == 1
        and proofs[0].evidence_hash == hashlib.sha256(SIGNATURE).hexdigest()
        and first.acknowledgement_fk == proofs[0].id
        and hashlib.sha256(reprinted.content).hexdigest() == waybill.content_hash,
        f"{waybill.number} for {first.number} on {order.number}, signed by "
        f"{proofs[0].recipient_name if proofs else '—'}; reprint identical by hash",
    )

    # -- 2. Partial delivery leaves the correct residual outstanding ------------------
    view = next(
        item
        for item in AssemblyService(session).workspace(actor, order.id).lines
        if item.line_id == gloves.id
    )
    after_first = (view.delivered, view.owed, dispatch.shippable(gloves))
    second_id = send([(gloves, "6")])
    second = dispatch.require(actor, second_id)
    dispatch.confirm_delivery(
        actor,
        second_id,
        DeliveryDraft(
            outcomes=[OutcomeDraft(shipment_line_id=second.lines[0].id, delivered=D("6"))],
            recipient_name="Sister A. Mensah",
        ),
    )
    session.commit()
    record(
        "Partial delivery leaves the correct residual quantity outstanding",
        after_first == (D("4"), D("6"), D("6")) and gloves.quantity_delivered == D("10"),
        f"10 ordered, 6 shipped, 4 signed for: {plain(after_first[1])} outstanding and "
        f"{plain(after_first[2])} ready to ship; second shipment closes the line at "
        f"{plain(gloves.quantity_delivered)}",
    )

    # -- 3. An exception posts its stock and financial consequence --------------------
    before = {code: balance(code) for code in ("50000", "51000", "12100", "12200")}
    third_id = send([(reagent, "6")])
    in_transit = balance("12200")
    third = dispatch.require(actor, third_id)
    dispatch.confirm_delivery(
        actor,
        third_id,
        DeliveryDraft(
            outcomes=[
                OutcomeDraft(
                    shipment_line_id=third.lines[0].id,
                    delivered=D("3"),
                    damaged=D("2"),
                    lost=D("1"),
                    reason="Two bottles cracked, one missing",
                )
            ],
            recipient_name="Sister A. Mensah",
        ),
    )
    session.commit()
    dispatch.customer_return(
        actor, third_id, third.lines[0].id, quantity=D("1"), reason="Ordered in error"
    )
    session.commit()
    after = {code: balance(code) for code in ("50000", "51000", "12100", "12200")}
    moved = {code: after[code] - before[code] for code in after}
    reports = InventoryReportService(session)
    record(
        "A delivery exception posts its stock and financial consequence through the ledger",
        in_transit == D("1800")
        and moved == {"50000": D("600"), "51000": D("300"), "12100": D("900"), "12200": D("0")}
        and len(third.exceptions) == 3
        and all(item.operation_id for item in third.exceptions)
        and reports.reconcile(actor).agrees
        and reports.valuation(actor).agrees,
        "6 reagent in transit at 1800; 3 delivered and 1 returned after: cost of sales "
        f"{moved['50000']}; 1 lost: write-off {moved['51000']}; 2 damaged and 1 returned: "
        f"quarantine {moved['12100']}; ledger and valuation agree",
    )

    session.close()
    return outcomes


def main() -> int:
    try:
        outcomes = verify()
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)

    width = max(len(label) for label, _, _ in outcomes)
    print()
    for label, passed, detail in outcomes:
        print(f"  {'PASS' if passed else 'FAIL'}  {label.ljust(width)}  {detail}")
    print()
    broken = [label for label, passed, _ in outcomes if not passed]
    if broken:
        print(f"{len(broken)} criterion(a) not met: {broken}")
        return 1
    print("Every Phase 9 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
