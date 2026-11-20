#!/usr/bin/env python3
"""Verify each Phase 10 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_10.py
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase10-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.catalogue import CatalogueService, ProductDraft  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.inventory import (  # noqa: E402
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.inventory_reports import (  # noqa: E402
    InventoryReportService,
)
from nickland.application.services.pos import (  # noqa: E402
    PosService,
    ProviderStatus,
    RefundDraft,
    RefundLineDraft,
)
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.documents.service import DocumentRenderService  # noqa: E402
from nickland.domain.errors import (  # noqa: E402
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.money import present  # noqa: E402
from nickland.domain.pos import SaleState, ShiftState, TenderState  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.immutability import register_append_only_guards  # noqa: E402
from nickland.persistence.ledger_models import Account, JournalEntry, JournalLine  # noqa: E402
from nickland.persistence.models import Business, Warehouse  # noqa: E402
from nickland.persistence.pos_models import PosSale  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
STAFF = "Tamale4Harbour"
D = Decimal
Report = tuple[str, bool, str]


def build() -> tuple[Session, Actor, Actor]:
    """A business with two priced items on the shelf, its proprietor and a sales person."""
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-10')"))
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
    identity = IdentityService(session)
    admin = identity.sign_in("proprietor", ADMIN, business_id=business_id).actor
    identity.create_user(
        admin,
        username="counter1",
        full_name="E. Mensah",
        password=STAFF,
        role_codes=["sales_person"],
        must_change_password=False,
    )
    session.commit()
    seller = identity.sign_in("counter1", STAFF, business_id=business_id).actor

    warehouse = session.scalar(select(Warehouse.id))
    assert warehouse is not None
    unit = UnitService(session).units(admin)[0]
    catalogue = CatalogueService(session)
    gloves = catalogue.create_product(
        admin,
        ProductDraft(
            sku="GLV-100",
            name="Nitrile gloves, box of 100",
            stock_unit_id=unit.id,
            list_price=D("100"),
            barcodes=[("6001234567899", "ean_13")],
        ),
    )
    swabs = catalogue.create_product(
        admin,
        ProductDraft(
            sku="SWB-020", name="Alcohol swabs", stock_unit_id=unit.id, list_price=D("12.50")
        ),
    )
    session.flush()
    InventoryService(session).open_stock(
        admin,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[
                StockLineDraft(product_id=gloves.id, quantity=D("40"), unit_cost=D("60")),
                StockLineDraft(product_id=swabs.id, quantity=D("100"), unit_cost=D("5")),
            ],
        ),
    )
    session.commit()
    return session, admin, seller


def postings(session: Session, entry_id: uuid.UUID | None) -> dict[str, tuple[Decimal, Decimal]]:
    rows = session.execute(
        select(Account.code, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .join(Account, Account.id == JournalLine.account_fk)
        .where(JournalLine.entry_fk == entry_id)
        .group_by(Account.code)
    ).all()
    return {code: (D(str(debit)), D(str(credit))) for code, debit, credit in rows}


def ring_up(pos: PosService, actor: Actor) -> PosSale:
    sale = pos.current_sale(actor)
    pos.scan(actor, sale.id, "6001234567899")
    pos.scan(actor, sale.id, "6001234567899")
    pos.scan(actor, sale.id, "SWB-020", quantity=D("4"))
    return sale


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, admin, seller = build()
    pos = PosService(session)
    render = DocumentRenderService(session)
    shift = pos.open_shift(seller, opening_float=D("200"))

    # 1. A cash sale, scan to receipt, in under five seconds, posting all four.
    started = time.perf_counter()
    sale = ring_up(pos, seller)
    pos.tender(seller, sale.id, method="cash", cash_received=D("310"))
    pos.complete(seller, sale.id)
    receipt = render.issue_for_pos_sale(seller, sale.id)
    elapsed = time.perf_counter() - started
    posted = postings(session, sale.journal_entry_fk)
    record(
        "Cash sale, scan to receipt, posts through the ledger",
        elapsed < 5.0
        and sale.state == SaleState.COMPLETED
        and receipt.number == sale.number
        and posted.get("10100") == (D("304.75"), D("0"))
        and posted.get("40100") == (D("0"), D("250"))
        and posted.get("21000", (D("0"), D("0")))[1] == D("54.75")
        and posted.get("50000") == (D("140"), D("0"))
        and posted.get("12000") == (D("0"), D("140")),
        f"{elapsed:.2f}s by a sales person; cash 304.75 (change 5.25), revenue 250.00, "
        f"output tax 54.75, cost of sales 140.00 in one entry; receipt {receipt.number}",
    )
    session.commit()

    # 2. With every outbound connection refused.
    original_connect = socket.socket.connect
    original_create = socket.create_connection

    def refused(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("Network is unreachable")

    socket.socket.connect = refused  # type: ignore[method-assign,assignment]
    socket.create_connection = refused  # type: ignore[assignment]
    try:
        offline = ring_up(pos, seller)
        pos.tender(seller, offline.id, method="cash", cash_received=D("400"))
        pos.complete(seller, offline.id)
        render.issue_for_pos_sale(seller, offline.id)
        passed = offline.state == SaleState.COMPLETED and offline.journal_entry_fk is not None
        detail = f"{offline.number} completed and its receipt issued with sockets refused"
    except Exception as error:  # pragma: no cover - reported, not raised
        passed, detail = False, f"{type(error).__name__}: {error}"
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.create_connection = original_create
    record("Cash sale completes with no network", passed, detail)
    session.commit()

    # 3. A pending electronic payment leaves the sale incomplete.
    pending = ring_up(pos, seller)
    pos.tender(seller, pending.id, method="mobile_money")
    refused_completion = False
    try:
        pos.complete(seller, pending.id)
    except ConflictError:
        refused_completion = True
    record(
        "Unconfirmed electronic payment leaves the sale incomplete",
        refused_completion
        and pending.state == SaleState.OPEN
        and pending.tenders[0].state == TenderState.PENDING
        and pending.journal_entry_fk is None
        and pending.number is None,
        "completion refused; tender pending; no entry, no number, no stock moved",
    )

    # 4. The provider's callback, delivered three times, posts once.
    status = ProviderStatus(
        idempotency_key=pending.tenders[0].idempotency_key,
        state=TenderState.CONFIRMED,
        provider_reference="MP261120.1224.C4411",
    )
    pos.provider_update(seller, status)
    pos.complete(seller, pending.id)
    entries = session.scalar(select(func.count(JournalEntry.id)))
    pos.provider_update(seller, status)
    pos.provider_update(seller, status)
    pos.complete(seller, pending.id)
    after = session.scalar(select(func.count(JournalEntry.id)))
    wallet = postings(session, pending.journal_entry_fk).get("10300")
    record(
        "Duplicated payment callback posts once",
        entries == after and wallet == (D("304.75"), D("0")),
        f"three callbacks, one entry: 10300 Mobile Money debited "
        f"{present(wallet[0], 2) if wallet else '—'}",
    )
    session.commit()

    # 6. A refund needs authorisation and is traceable to the sale.
    gloves_line = next(line for line in sale.lines if line.description.startswith("Nitrile"))
    draft = RefundDraft(
        sale_id=sale.id,
        lines=[RefundLineDraft(sale_line_id=gloves_line.id, quantity=D("1"))],
        reason="Box damaged",
    )
    denied = False
    try:
        pos.refund(seller, draft)
    except PermissionDeniedError:
        denied = True
    refund = pos.refund(seller, draft, authoriser=admin)
    traced = postings(session, refund.journal_entry_fk)
    record(
        "Refund needs authorisation and traces to the sale",
        denied
        and refund.sale_fk == sale.id
        and refund.lines[0].sale_line_fk == gloves_line.id
        and refund.requested_by_id == seller.user_id
        and refund.authorised_by_id == admin.user_id
        and traced.get("40950") == (D("100"), D("0"))
        and traced.get("10100") == (D("0"), D("121.90")),
        f"refused to the sales person alone; {refund.number} authorised by the proprietor "
        f"against {sale.number} line 1: returns 100.00, tax 21.90, cash 121.90, goods back "
        "at 60.00",
    )
    session.commit()

    # 5. Shift close reconciles, and a variance needs a reason and sign-off.
    drawer = pos.drawer(shift)
    count = {"200": 3, "50": 1, "20": 1, "10": 1, "5": 1}  # 685.00: a shortage
    without_reason = False
    try:
        pos.submit_count(seller, shift.id, denominations=count)
    except ValidationError:
        without_reason = True
    submitted = pos.submit_count(
        seller, shift.id, denominations=count, variance_reason="Change given short"
    )
    awaiting = submitted.state == ShiftState.SUBMITTED
    self_signoff = False
    try:
        pos.approve_shift(seller, shift.id)
    except PermissionDeniedError:
        self_signoff = True
    closed = pos.approve_shift(admin, shift.id)
    variance = postings(session, closed.variance_journal_fk)
    shortage = abs(closed.variance or D("0"))
    record(
        "Shift close reconciles; a variance needs a reason and sign-off",
        without_reason
        and awaiting
        and self_signoff
        and closed.state == ShiftState.CLOSED
        and closed.approved_by_id == admin.user_id
        and variance.get("60900") == (shortage, D("0")),
        f"expected {present(drawer.expected, 2)}, "
        f"counted {present(closed.counted_cash or D(0), 2)}, "
        f"short {present(shortage, 2)}: refused without a reason, not signed off by the cashier, "
        f"posted to 60900 on the proprietor's sign-off",
    )
    session.commit()

    agrees = InventoryReportService(session).reconcile(admin).agrees
    record("Ledger and stock agree after the day", agrees, "inventory reconciles to 12000/12100")
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
    print("Every Phase 10 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
