#!/usr/bin/env python3
"""Verify each Phase 8 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_8.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase8-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError, OperationalError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.catalogue import (  # noqa: E402
    CatalogueService,
    ProductDraft,
)
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.inventory import (  # noqa: E402
    AdjustmentDraft,
    InspectionDraft,
    InventoryService,
    IssueDraft,
    OpeningDraft,
    StockLineDraft,
    TransferDraft,
    TransferLineDraft,
)
from nickland.application.services.inventory_reports import (  # noqa: E402
    InventoryReportService,
)
from nickland.application.services.organisation import OrganisationService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.domain.errors import ConflictError, ValidationError  # noqa: E402
from nickland.domain.inventory import MovementType, StockState  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.catalogue_models import Product  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.immutability import register_append_only_guards  # noqa: E402
from nickland.persistence.inventory_models import Batch, StockMovement  # noqa: E402
from nickland.persistence.models import Branch, Business, Warehouse  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
D = Decimal
Report = tuple[str, bool, str]


def build() -> tuple[sessionmaker[Session], Session, Actor, dict[str, uuid.UUID]]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-8')"))
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
    main = session.scalar(select(Warehouse.id))
    branch = session.scalar(select(Branch.id))
    assert main is not None and branch is not None
    second = OrganisationService(session).create_warehouse(
        actor, branch_id=branch, code="KSI", name="Kumasi depot"
    )
    unit = UnitService(session).units(actor)[0]
    catalogue = CatalogueService(session)
    products = {
        "gloves": catalogue.create_product(
            actor, ProductDraft(sku="GLV-100", name="Nitrile gloves", stock_unit_id=unit.id)
        ),
        "tips": catalogue.create_product(
            actor,
            ProductDraft(
                sku="PIP-200", name="Pipette tips", stock_unit_id=unit.id, valuation_policy="fifo"
            ),
        ),
        "reagent": catalogue.create_product(
            actor,
            ProductDraft(
                sku="RGT-300",
                name="Glucose reagent",
                stock_unit_id=unit.id,
                tracking_policy="batch",
                requires_expiry=True,
            ),
        ),
        "analyser": catalogue.create_product(
            actor, ProductDraft(sku="ANL-400", name="Blood gas analyser", stock_unit_id=unit.id)
        ),
    }
    session.commit()
    ids = {key: product.id for key, product in products.items()}
    ids["main"] = main
    ids["second"] = second.id
    return factory, session, actor, ids


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    factory, session, actor, ids = build()
    inventory = InventoryService(session)
    reports = InventoryReportService(session)

    def agrees() -> tuple[bool, bool]:
        return reports.reconcile(actor).agrees, reports.valuation(actor).agrees

    inventory.open_stock(
        actor,
        OpeningDraft(
            warehouse_id=ids["main"],
            lines=[
                StockLineDraft(product_id=ids["gloves"], quantity=D("10"), unit_cost=D("2")),
                StockLineDraft(product_id=ids["gloves"], quantity=D("20"), unit_cost=D("3.5")),
                StockLineDraft(product_id=ids["tips"], quantity=D("10"), unit_cost=D("1")),
                StockLineDraft(
                    product_id=ids["reagent"],
                    quantity=D("4"),
                    unit_cost=D("15"),
                    batch="LOT-OLD",
                    expiry=date(2026, 1, 31),
                ),
                StockLineDraft(
                    product_id=ids["reagent"],
                    quantity=D("6"),
                    unit_cost=D("16"),
                    batch="LOT-NEW",
                    expiry=date(2028, 6, 30),
                ),
                StockLineDraft(product_id=ids["analyser"], quantity=D("1"), unit_cost=D("42000")),
            ],
        ),
    )
    session.commit()
    instants: list[tuple[bool, bool]] = [agrees()]

    # -- 1. Append-only, with reversals ------------------------------------------------
    damage = inventory.adjust(
        actor,
        AdjustmentDraft(
            warehouse_id=ids["main"],
            kind="damage",
            reason="Water damage",
            lines=[StockLineDraft(product_id=ids["gloves"], quantity=D("3"))],
        ),
    )
    session.commit()
    instants.append(agrees())
    movement = session.scalar(select(StockMovement).where(StockMovement.operation_id == damage.id))
    assert movement is not None
    movement.quantity = D("1")
    try:
        session.flush()
        rewritten = True
    except ConflictError:
        rewritten = False
    session.rollback()
    reversal = inventory.reverse(actor, damage.id, reason="Recorded against the wrong item")
    session.commit()
    instants.append(agrees())
    gloves = next(
        row
        for row in reports.positions(actor)
        if row.product_id == ids["gloves"] and row.warehouse_id == ids["main"]
    )
    record(
        "The stock ledger is append-only; a correction is a reversal",
        not rewritten
        and len(reversal.movements) == 1
        and gloves.usable == D("30")
        and gloves.value == D("90"),
        f"edit refused; reversal restored {gloves.usable} gloves at {gloves.value}",
    )

    # -- 2 and 3. Quantities and valuation agree at every instant ---------------------
    transfer = inventory.raise_transfer(
        actor,
        TransferDraft(
            from_warehouse_id=ids["main"],
            to_warehouse_id=ids["second"],
            lines=[TransferLineDraft(product_id=ids["tips"], quantity=D("4"))],
        ),
    )
    inventory.dispatch_transfer(actor, transfer.id)
    session.commit()
    instants.append(agrees())
    inventory.receive_transfer(actor, transfer.id)
    inventory.issue(
        actor,
        IssueDraft(product_id=ids["gloves"], warehouse_id=ids["main"], quantity=D("7")),
    )
    session.commit()
    instants.append(agrees())
    count = inventory.open_count(actor, warehouse_id=ids["main"])
    inventory.record_count(
        actor,
        count.id,
        {
            line.id: (
                line.system_quantity - (1 if line.product_fk == ids["tips"] else 0),
                "One rack missing" if line.product_fk == ids["tips"] else None,
            )
            for line in count.lines
        },
    )
    inventory.submit_count(actor, count.id)
    inventory.approve_count(actor, count.id)
    session.commit()
    instants.append(agrees())
    record(
        "Quantity states reconcile to the ledger at every instant",
        all(quantities for quantities, _ in instants),
        f"{len(instants)} instants checked: after opening, damage, reversal, transfer "
        "dispatch and receipt, issue and count",
    )
    valuation = reports.valuation(actor)
    record(
        "Valuation reconciles to the inventory control accounts",
        all(values for _, values in instants) and valuation.agrees,
        "; ".join(f"{a.account} layers {a.layers} = ledger {a.ledger}" for a in valuation.accounts),
    )

    # -- 4. Quarantined and rejected stock excluded from availability -----------------
    today = inventory.today(actor)
    receipt = inventory.engine.begin(actor, MovementType.RECEIPT, occurred_on=today)
    analyser = session.get(Product, ids["analyser"])
    assert analyser is not None and actor.business_id is not None
    receipt.add(analyser, ids["main"], StockState.QUARANTINE, D("2"), D("84000"), counter="20150")
    receipt.finish("Analysers received into quarantine")
    session.commit()
    before = inventory.engine.available(actor.business_id, ids["analyser"], ids["main"], today)
    try:
        inventory.reserve(
            actor, product_id=ids["analyser"], warehouse_id=ids["main"], quantity=D("2")
        )
        reserved_quarantine = True
    except ConflictError:
        reserved_quarantine = False
        session.rollback()
    inventory.inspect(
        actor,
        InspectionDraft(
            product_id=ids["analyser"],
            warehouse_id=ids["main"],
            passed=D("0"),
            failed=D("2"),
            findings="Shipping damage to both housings",
        ),
    )
    session.commit()
    after = inventory.engine.available(actor.business_id, ids["analyser"], ids["main"], today)
    try:
        inventory.reserve(
            actor, product_id=ids["analyser"], warehouse_id=ids["main"], quantity=D("2")
        )
        reserved_rejected = True
    except ConflictError:
        reserved_rejected = False
        session.rollback()
    record(
        "Rejected and quarantined stock is excluded from availability and reservation",
        before == D("1") and after == D("1") and not reserved_quarantine and not reserved_rejected,
        f"1 usable and 2 quarantined: available {before}; after failing inspection: {after}; "
        "reservation of either refused",
    )

    # -- 5. Batch required; expired batch blocked without override --------------------
    try:
        inventory.issue(
            actor,
            IssueDraft(product_id=ids["reagent"], warehouse_id=ids["main"], quantity=D("1")),
        )
        unbatched = True
    except ValidationError:
        unbatched = False
        session.rollback()
    old = session.scalar(select(Batch.id).where(Batch.code == "LOT-OLD"))
    try:
        inventory.issue(
            actor,
            IssueDraft(
                product_id=ids["reagent"], warehouse_id=ids["main"], quantity=D("1"), batch_id=old
            ),
        )
        expired_issued = True
    except ConflictError:
        expired_issued = False
        session.rollback()
    overridden = inventory.issue(
        actor,
        IssueDraft(
            product_id=ids["reagent"],
            warehouse_id=ids["main"],
            quantity=D("1"),
            batch_id=old,
            override_reason="Customer accepts for training use",
        ),
    )
    session.commit()
    recorded = [m for m in overridden.movements if m.override_reason]
    record(
        "A batch-tracked item needs a batch, and an expired batch needs an authorised override",
        not unbatched and not expired_issued and bool(recorded) and recorded[0].override_by_id,
        "no batch: refused; LOT-OLD expired: refused; with override: issued and recorded",
    )

    # -- 6. Concurrent reservation of the final unit ----------------------------------
    def reserve_last() -> str:
        own = factory()
        try:
            InventoryService(own).reserve(
                actor, product_id=ids["analyser"], warehouse_id=ids["main"], quantity=D("1")
            )
            own.commit()
            return "reserved"
        except ConflictError:
            own.rollback()
            return "none left"
        except (OperationalError, DBAPIError):
            own.rollback()
            return "try again"
        finally:
            own.close()

    session.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: reserve_last(), range(8)))
    record(
        "Concurrent reservation of the final available unit succeeds exactly once",
        results.count("reserved") == 1,
        f"8 workers: {results.count('reserved')} reserved, "
        f"{results.count('none left')} found none left, {results.count('try again')} retried",
    )
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
    print("Every Phase 8 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
