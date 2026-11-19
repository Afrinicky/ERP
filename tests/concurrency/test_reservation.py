"""Reserving the last unit under contention. Phase 8 exit criterion 6.

Two tills that both promise the final analyser to two customers have sold one analyser
twice. The engine locks the pool before it reads it, so of several workers asking for
the last unit at once exactly one is given it; the others wait and find nothing left,
or are told to try again. Never two.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.inventory import (
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.inventory_reports import InventoryReportService
from nickland.application.services.units import UnitService
from nickland.domain.errors import ConflictError
from nickland.platform.clock import FixedClock

WORKERS = 8


def reserve_in_its_own_session(
    factory: sessionmaker[Session],
    clock: FixedClock,
    actor: Actor,
    product_id: uuid.UUID,
    warehouse_id: uuid.UUID,
) -> str:
    session = factory()
    try:
        InventoryService(session, clock=clock).reserve(
            actor, product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal("1")
        )
        session.commit()
        return "reserved"
    except ConflictError:
        session.rollback()
        return "none left"
    except (OperationalError, DBAPIError):
        session.rollback()
        return "try again"
    finally:
        session.close()


class TestLastUnit:
    def test_the_final_unit_is_reserved_exactly_once(
        self,
        db: Session,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        admin: Actor,
        provisioned: dict[str, uuid.UUID],
    ) -> None:
        unit = UnitService(db, clock=clock).units(admin)[0]
        product = CatalogueService(db, clock=clock).create_product(
            admin, ProductDraft(sku="ANL-900", name="Blood gas analyser", stock_unit_id=unit.id)
        )
        warehouse_id = provisioned["warehouse_id"]
        InventoryService(db, clock=clock).open_stock(
            admin,
            OpeningDraft(
                warehouse_id=warehouse_id,
                lines=[
                    StockLineDraft(
                        product_id=product.id, quantity=Decimal("1"), unit_cost=Decimal("42000")
                    )
                ],
            ),
        )
        db.commit()

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(
                pool.map(
                    lambda _: reserve_in_its_own_session(
                        session_factory, clock, admin, product.id, warehouse_id
                    ),
                    range(WORKERS),
                )
            )

        assert results.count("reserved") == 1, results
        check = session_factory()
        try:
            rows = InventoryReportService(check, clock=clock).positions(admin)
            row = next(item for item in rows if item.product_id == product.id)
            assert (row.reserved, row.available) == (Decimal("1"), Decimal("0"))
            assert InventoryReportService(check, clock=clock).reconcile(admin).agrees
        finally:
            check.close()
