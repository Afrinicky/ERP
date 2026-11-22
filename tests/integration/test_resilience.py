"""What is left when things go wrong partway. Phase 15 exit criterion 4; PRD §9.4, §9.5.

An interrupted transaction, a payment callback delivered twice (or twice at once), and a
client that disconnects mid-request must each leave the record whole: a sale is posted
entirely or not at all, a payment is confirmed once, stock and ledger agree. The host
being killed outright is exercised by ``tools/verify_phase_15.py``, which needs a separate
process to kill.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.integrity import IntegrityService
from nickland.application.services.inventory import InventoryService, OpeningDraft, StockLineDraft
from nickland.application.services.journals import JournalService
from nickland.application.services.pos import PosService, ProviderStatus
from nickland.application.services.units import UnitService
from nickland.domain.pos import TenderState
from nickland.interface.application import build_runtime, create_application
from nickland.persistence.inventory_models import StockMovement
from nickland.persistence.ledger_models import JournalEntry
from nickland.persistence.models import AuditEvent, Warehouse
from nickland.persistence.pos_models import PosSale, PosTender
from nickland.platform.clock import FixedClock
from nickland.platform.config import Settings

D = Decimal


@pytest.fixture
def stocked(db: Session, clock: FixedClock, admin: Actor) -> str:
    warehouse = db.scalar(select(Warehouse.id))
    assert warehouse is not None
    unit = UnitService(db, clock=clock).units(admin)[0]
    product = CatalogueService(db, clock=clock).create_product(
        admin,
        ProductDraft(sku="RS-1", name="Test strips", stock_unit_id=unit.id, list_price=D("80")),
    )
    db.flush()
    InventoryService(db, clock=clock).open_stock(
        admin,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[StockLineDraft(product_id=product.id, quantity=D("20"), unit_cost=D("30"))],
        ),
    )
    PosService(db, clock=clock).open_shift(admin, opening_float=D("100"))
    db.commit()
    return product.sku


def whole(factory: sessionmaker[Session], clock: FixedClock, admin: Actor) -> list[str]:
    session = factory()
    try:
        return IntegrityService(session, clock=clock).check(admin).problems
    finally:
        session.close()


class TestInterruptedTransaction:
    def test_a_sale_cut_short_after_its_stock_moved_leaves_nothing(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
        stocked: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pos = PosService(db, clock=clock)
        sale = pos.current_sale(admin)
        pos.scan(admin, sale.id, stocked, quantity=D("2"))
        pos.tender(admin, sale.id, method="cash", cash_received=D("500"))
        db.commit()
        sale_id = sale.id

        def power_cut(*_: object, **__: object) -> None:
            raise RuntimeError("The power failed between the stock and the ledger.")

        session = session_factory()
        monkeypatch.setattr(JournalService, "post", power_cut)
        try:
            with pytest.raises(RuntimeError):
                PosService(session, clock=clock).complete(admin, sale_id)
            session.rollback()
        finally:
            session.close()
        monkeypatch.undo()

        check = session_factory()
        try:
            stored = check.get(PosSale, sale_id)
            assert stored is not None and stored.state == "open"
            moved = check.scalar(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.source_id == sale_id)
            )
            assert moved == 0
            # And the same sale, completed afterwards, posts once and whole.
            PosService(check, clock=clock).complete(admin, sale_id)
            check.commit()
        finally:
            check.close()
        assert not whole(session_factory, clock, admin)


class TestDuplicatedCallbacks:
    def test_a_callback_delivered_twice_confirms_once(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
        stocked: str,
        engine_name: str,
    ) -> None:
        pos = PosService(db, clock=clock)
        sale = pos.current_sale(admin)
        pos.scan(admin, sale.id, stocked)
        pos.tender(admin, sale.id, method="mobile_money", reference="0244000000")
        db.commit()
        tender = db.scalar(select(PosTender).where(PosTender.sale_fk == sale.id))
        assert tender is not None and tender.state == "pending"
        status = ProviderStatus(tender.idempotency_key, TenderState.CONFIRMED, "MM-778812")

        def deliver(_: int) -> str:
            session = session_factory()
            try:
                PosService(session, clock=clock).provider_update(admin, status)
                session.commit()
                return "ok"
            except DBAPIError:
                session.rollback()
                return "retry"
            finally:
                session.close()

        workers = 6 if engine_name == "postgresql" else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(deliver, range(6)))
        assert "ok" in results
        check = session_factory()
        try:
            confirmations = check.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action == "pos_tender_confirmed", AuditEvent.entity_id == tender.id
                )
            )
            assert confirmations == 1
            stored = check.get(PosTender, tender.id)
            assert stored is not None and stored.state == "confirmed"
        finally:
            check.close()

    def test_completing_twice_at_once_posts_once(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
        stocked: str,
        engine_name: str,
    ) -> None:
        pos = PosService(db, clock=clock)
        sale = pos.current_sale(admin)
        pos.scan(admin, sale.id, stocked, quantity=D("3"))
        pos.tender(admin, sale.id, method="cash", cash_received=D("500"))
        db.commit()

        def complete(_: int) -> str:
            session = session_factory()
            try:
                PosService(session, clock=clock).complete(admin, sale.id)
                session.commit()
                return "ok"
            except DBAPIError:
                session.rollback()
                return "retry"
            finally:
                session.close()

        workers = 5 if engine_name == "postgresql" else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(complete, range(5)))
        assert "ok" in results
        check = session_factory()
        try:
            stored = check.get(PosSale, sale.id)
            assert stored is not None and stored.state == "completed"
            entries = check.scalar(
                select(func.count())
                .select_from(JournalEntry)
                .where(JournalEntry.idempotency_key == f"pos_sale:{sale.id}")
            )
            assert entries == 1
        finally:
            check.close()
        assert not whole(session_factory, clock, admin)


@pytest.fixture
def served(db: Session, settings: Settings, admin: Actor) -> Iterator[int]:
    db.commit()
    runtime = build_runtime(settings)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            create_application(runtime=runtime),
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
            ws="none",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield port
    server.should_exit = True
    thread.join(timeout=10)


class TestClientDisconnection:
    def test_a_client_that_vanishes_mid_request_leaves_the_sale_whole(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
        stocked: str,
        served: int,
    ) -> None:
        import httpx

        base = f"http://127.0.0.1:{served}/api/v1"
        token = httpx.post(
            f"{base}/session", json={"username": "proprietor", "password": "Provision9Admin"}
        ).json()["token"]
        headers = {"X-Nickland-Session": token}
        sale = httpx.get(f"{base}/pos/sale", headers=headers).json()
        httpx.post(
            f"{base}/pos/sales/{sale['id']}/scan",
            headers=headers,
            json={"code": stocked, "quantity": "2"},
        )
        httpx.post(
            f"{base}/pos/sales/{sale['id']}/tenders",
            headers=headers,
            json={"method": "cash", "cash_received": "500"},
        )

        # Send the completion and hang up without waiting for the answer.
        body = b"{}"
        request = (
            f"POST /api/v1/pos/sales/{sale['id']}/complete HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{served}\r\nX-Nickland-Session: {token}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        with socket.create_connection(("127.0.0.1", served)) as raw:
            raw.sendall(request)
        time.sleep(1.5)

        check = session_factory()
        try:
            stored = check.get(PosSale, uuid.UUID(sale["id"]))
            assert stored is not None and stored.state in {"open", "completed"}
        finally:
            check.close()
        assert not whole(session_factory, clock, admin)
