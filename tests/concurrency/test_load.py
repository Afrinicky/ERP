"""Ten people at once against one dataset. Phase 15 exit criterion 2; PRD §9.5, AC-12.

Ten workers, each in sessions of their own, mix the work of a busy day: register sales on
their own tills over a small shared stock, reservations and releases, purchase orders,
journal postings (some deliberately retried with the same idempotency key, as a lost reply
would be), and customer receipts allocated to one shared invoice. Afterwards the record
must be whole: every document number distinct, every reservation accounted for, no stock
below nothing, no invoice paid beyond its total, the ledger balanced.

A worker told to try again (a deadlock broken by the database, a write lock busy on
SQLite) tries again; being told to retry is correct behaviour. What must never happen is
a duplicate, a lost reservation or a half-written transaction.
"""

from __future__ import annotations

import random
import threading
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, MetaData, func, select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.inventory import InventoryService, OpeningDraft, StockLineDraft
from nickland.application.services.inventory_reports import InventoryReportService
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.application.services.pos import PosService
from nickland.application.services.procurement import (
    ProcurementService,
    PurchaseLineDraft,
    PurchaseOrderDraft,
)
from nickland.application.services.quotations import LineDraft as QuoteLine
from nickland.application.services.quotations import (
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.receivables import (
    InvoiceDraft,
    ReceiptDraft,
    ReceivablesService,
)
from nickland.application.services.units import UnitService
from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.ledger import EntryLine, JournalSource, Side
from nickland.persistence.finance_models import SalesInvoice
from nickland.persistence.inventory_models import StockMovement, StockPosition
from nickland.persistence.ledger_models import JournalEntry
from nickland.persistence.models import Warehouse
from nickland.persistence.pos_models import PosSale
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

USERS = 10
ROUNDS = 6
ATTEMPTS = 8
D = Decimal


class Day:
    """The shared state the workers act on, and what happened."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.outcomes: Counter[str] = Counter()
        self.keys: list[str] = []
        self.reasons: Counter[str] = Counter()

    def count(self, name: str) -> None:
        with self.lock:
            self.outcomes[name] += 1

    def note(self, text: str) -> None:
        with self.lock:
            self.reasons[text] += 1


def attempt(
    factory: sessionmaker[Session], day: Day, name: str, work: Callable[[Session], None]
) -> None:
    for _ in range(ATTEMPTS):
        session = factory()
        try:
            work(session)
            session.commit()
            day.count(name)
            return
        except (ConflictError, ValidationError) as refusal:
            session.rollback()
            day.count(f"{name}: refused")
            day.note(f"{name}: {str(refusal)[:90]}")
            return
        except (OperationalError, DBAPIError) as error:
            session.rollback()
            day.count(f"{name}: retried")
            day.note(f"{name}: {str(error.orig)[:120]}")
        finally:
            session.close()
    day.count(f"{name}: gave up")


def stock_up(db: Session, clock: FixedClock, admin: Actor) -> dict[str, object]:
    warehouse = db.scalar(select(Warehouse.id))
    assert warehouse is not None
    unit = UnitService(db, clock=clock).units(admin)[0]
    catalogue = CatalogueService(db, clock=clock)
    products = [
        catalogue.create_product(
            admin,
            ProductDraft(
                sku=f"LD-{n}", name=f"Item {n}", stock_unit_id=unit.id, list_price=D("50")
            ),
        )
        for n in range(3)
    ]
    db.flush()
    InventoryService(db, clock=clock).open_stock(
        admin,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[
                StockLineDraft(product_id=item.id, quantity=D("40"), unit_cost=D("20"))
                for item in products
            ],
        ),
    )
    parties = PartyService(db, clock=clock)
    supplier = parties.create(admin, PartyDraft(name="Medisupply", is_supplier=True))
    customer = parties.create(
        admin,
        PartyDraft(
            name="Korle Bu", is_customer=True, payment_term_kind="net_days", payment_term_days=30
        ),
    )
    quotations = QuotationService(db, clock=clock)
    quotation = quotations.create(
        admin,
        QuotationDraft(
            customer_id=customer.id,
            lines=[
                QuoteLine(
                    description="Reagent",
                    quantity=D("10"),
                    product_id=products[0].id,
                    sources=[
                        SourceDraft(
                            kind="stock",
                            quantity=D("10"),
                            unit_cost=D("20"),
                            warehouse_id=warehouse,
                        )
                    ],
                )
            ],
        ),
    )
    db.flush()
    quotations.issue(admin, quotation.id)
    orders = SalesOrderService(db, clock=clock)
    orders.record_acceptance(admin, quotation.id, AcceptanceDraft(reference="KB-1"))
    order = orders.convert(admin, quotation.id)
    db.flush()
    invoice = ReceivablesService(db, clock=clock).issue_invoice(
        admin, InvoiceDraft(sales_order_id=order.id, basis="ordered")
    )
    db.commit()
    return {
        "warehouse": warehouse,
        "products": [item.id for item in products],
        "skus": [item.sku for item in products],
        "supplier": supplier.id,
        "customer": customer.id,
        "invoice": invoice.id,
        "total": invoice.total,
    }


def worker(
    index: int,
    factory: sessionmaker[Session],
    clock: FixedClock,
    business: uuid.UUID,
    shop: dict[str, object],
    day: Day,
) -> None:
    person = make_actor(business)
    chance = random.Random(index)
    products: list[uuid.UUID] = shop["products"]  # type: ignore[assignment]
    skus: list[str] = shop["skus"]  # type: ignore[assignment]

    def open_shift(session: Session) -> None:
        PosService(session, clock=clock).open_shift(
            person, opening_float=D("100"), register=f"Till {index + 1}"
        )

    attempt(factory, day, "shift", open_shift)

    for round_ in range(ROUNDS):

        def sell(session: Session) -> None:
            pos = PosService(session, clock=clock)
            sale = pos.current_sale(person)
            pos.scan(person, sale.id, chance.choice(skus), quantity=D(chance.randint(1, 3)))
            pos.tender(person, sale.id, method="cash", cash_received=sale.total + D("50"))
            pos.complete(person, sale.id)

        def reserve(session: Session) -> None:
            InventoryService(session, clock=clock).reserve(
                person,
                product_id=chance.choice(products),
                warehouse_id=shop["warehouse"],
                quantity=D("1"),
            )  # type: ignore[arg-type]

        def buy(session: Session) -> None:
            procurement = ProcurementService(session, clock=clock)
            order = procurement.raise_order(
                person,
                PurchaseOrderDraft(
                    supplier_id=shop["supplier"],  # type: ignore[arg-type]
                    lines=[
                        PurchaseLineDraft(
                            description="Gloves",
                            quantity=D("5"),
                            unit_cost=D("18"),
                            product_id=products[1],
                        )
                    ],
                ),
            )
            session.flush()
            procurement.approve(person, order.id)
            procurement.issue(person, order.id)

        key = f"load:{round_}:{index // 2}"

        def post(session: Session, key: str = key) -> None:
            JournalService(session, clock=clock).post(
                person,
                JournalDraft(
                    posting_date=date(2026, 11, 5),
                    narration="Petty cash top-up",
                    source=JournalSource.MANUAL,
                    idempotency_key=key,
                    lines=[
                        EntryLine("10100", Side.DEBIT, D("10.00")),
                        EntryLine("10200", Side.CREDIT, D("10.00")),
                    ],
                ),
            )

        def receive(session: Session) -> None:
            ReceivablesService(session, clock=clock).receive(
                person,
                ReceiptDraft(
                    customer_id=shop["customer"],
                    amount=D("20.00"),  # type: ignore[arg-type]
                    allocations=[(shop["invoice"], D("20.00"))],
                ),
            )  # type: ignore[list-item]

        for name, work in (
            ("sale", sell),
            ("reservation", reserve),
            ("purchase", buy),
            ("posting", post),
            ("receipt", receive),
        ):
            attempt(factory, day, name, work)
        with day.lock:
            day.keys.append(key)


def numbered_tables(engine: Engine) -> list[str]:
    metadata = MetaData()
    metadata.reflect(engine)
    return [
        name
        for name, table in metadata.tables.items()
        if "number" in table.columns and name not in {"number_sequences"}
    ]


class TestTenUsers:
    def test_a_busy_day_leaves_the_record_whole(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        engine: Engine,
        clock: FixedClock,
        admin: Actor,
        engine_name: str,
    ) -> None:
        if engine_name != "postgresql":
            pytest.skip(
                "ten users share a LAN host's PostgreSQL; the standalone SQLite edition serves "
                "one person, and its numbering and reservations are proved under contention "
                "by the tests beside this one"
            )
        shop = stock_up(db, clock, admin)
        assert admin.business_id is not None
        day = Day()
        with ThreadPoolExecutor(max_workers=USERS) as pool:
            for future in [
                pool.submit(worker, index, session_factory, clock, admin.business_id, shop, day)
                for index in range(USERS)
            ]:
                future.result()

        outcomes = day.outcomes
        assert outcomes["sale"] >= USERS * ROUNDS * 0.5, outcomes
        assert outcomes["purchase"] >= USERS * ROUNDS * 0.8, outcomes
        assert not [name for name in outcomes if name.endswith("gave up")], outcomes
        assert outcomes["receipt"] >= 5, outcomes

        check = session_factory()
        try:
            # No document number handed out twice, in any numbered table.
            for name in numbered_tables(engine):
                table = MetaData()
                table.reflect(engine, only=[name])
                column = table.tables[name].c.number
                rows = check.execute(
                    select(column, func.count())
                    .where(column.is_not(None))
                    .group_by(table.tables[name].c.business_id, column)
                    .having(func.count() > 1)
                ).all()
                assert not rows, f"{name} repeats {rows}"

            # One entry per idempotency key, however often it was retried.
            for key in set(day.keys):
                entries = check.scalar(
                    select(func.count())
                    .select_from(JournalEntry)
                    .where(JournalEntry.idempotency_key == key)
                )
                assert entries == 1, key

            # Every reservation accounted for, and no stock below nothing.
            for position in check.scalars(select(StockPosition)).all():
                reserved = check.scalar(
                    select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
                        StockMovement.product_fk == position.product_fk,
                        StockMovement.warehouse_fk == position.warehouse_fk,
                        StockMovement.state == "reserved",
                    )
                )
                assert position.reserved == D(reserved), position.product_fk
                assert position.usable >= 0 and position.reserved <= position.usable
            assert InventoryReportService(check, clock=clock).reconcile(admin).agrees

            # Every completed sale posted, and no invoice paid beyond its total.
            unposted = check.scalar(
                select(func.count())
                .select_from(PosSale)
                .where(PosSale.state == "completed", PosSale.journal_entry_fk.is_(None))
            )
            assert unposted == 0
            invoice = check.get(SalesInvoice, shop["invoice"])
            assert invoice is not None and invoice.amount_received <= invoice.total
            assert LedgerReportService(check, clock=clock).trial_balance(admin).balanced
        finally:
            check.close()

    def test_one_register_holds_one_open_shift_per_cashier(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
    ) -> None:
        db.commit()
        assert admin.business_id is not None
        cashier = make_actor(admin.business_id)
        day = Day()

        def open_shift(session: Session) -> None:
            PosService(session, clock=clock).open_shift(cashier, opening_float=D("50"))

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: attempt(session_factory, day, "shift", open_shift), range(6)))
        assert day.outcomes["shift"] == 1, day.outcomes
