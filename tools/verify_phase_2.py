#!/usr/bin/env python3
"""Verify each Phase 2 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_2.py

The search criterion is a timing measurement against 50,000 catalogue rows, so this
takes a minute or so to build its fixture.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import sys
import tempfile
import time
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase2-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import func, select, text  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.catalogue import (  # noqa: E402
    CatalogueService,
    ProductDraft,
)
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.importing import ImportKind, ImportService  # noqa: E402
from nickland.application.services.parties import PartyDraft, PartyService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.domain.catalogue import (  # noqa: E402
    AttributeKind,
    BarcodeKind,
    ItemClass,
    assert_stock_permitted,
)
from nickland.domain.errors import ConflictError  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.catalogue_models import Product  # noqa: E402
from nickland.persistence.engine import create_database_engine, create_session_factory  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
CATALOGUE_ROWS = 50_000
SEARCH_BUDGET_MS = 200.0
Outcome = tuple[str, bool, str]


def build_session() -> tuple[object, Actor]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-2')"))

    session = create_session_factory(engine)()
    SetupService(session).provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password=ADMIN,
            theme_preset="charcoal",
        )
    )
    session.commit()
    actor = (
        IdentityService(session)
        .sign_in(
            "proprietor",
            ADMIN,
            business_id=session.scalar(select(Product.business_id)) or _business_id(session),
        )
        .actor
    )
    session.commit()
    return session, actor


def _business_id(session: object) -> uuid.UUID:
    from nickland.persistence.models import Business

    identifier = session.scalar(select(Business.id))  # type: ignore[attr-defined]
    assert identifier is not None
    return identifier


def verify() -> list[Outcome]:
    outcomes: list[Outcome] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, actor = build_session()
    catalogue = CatalogueService(session)  # type: ignore[arg-type]
    units = UnitService(session)  # type: ignore[arg-type]
    parties = PartyService(session)  # type: ignore[arg-type]
    importer = ImportService(session)  # type: ignore[arg-type]

    by_code = {unit.code: unit for unit in units.units(actor)}
    each, box = by_code["ea"].id, by_code["box"].id

    # --- Criterion 1 ------------------------------------------------------------------
    # A product carrying variants, attributes, two units, a barcode and three custom
    # fields is created, amended and retrieved without source-code change.
    from nickland.application.services.custom_fields import CustomFieldService

    fields = CustomFieldService(session)  # type: ignore[arg-type]
    for code, label, kind in (
        ("lot_policy", "Lot policy", "text"),
        ("storage_celsius", "Storage temperature", "number"),
        ("sterile", "Sterile", "boolean"),
    ):
        fields.define(actor, target="product", code=code, label=label, field_type=kind)

    catalogue.create_attribute(
        actor,
        code="size",
        name="Size",
        kind=AttributeKind.CHOICE,
        choices=["Small", "Medium", "Large"],
        is_variant_defining=True,
    )
    category = catalogue.create_category(actor, code="gloves", name="Gloves")

    product = catalogue.create_product(
        actor,
        ProductDraft(
            sku="GLV-NITRILE",
            name="Nitrile examination glove",
            stock_unit_id=each,
            category_id=category.id,
            manufacturer="Medisafe",
            barcodes=[("5012345678900", BarcodeKind.EAN_13)],
        ),
    )
    units.set_product_factor(actor, product_id=product.id, unit_id=box, factor=Decimal("100"))
    fields.set_values(
        actor,
        target="product",
        owner_id=product.id,
        values={"lot_policy": "Per shipment", "storage_celsius": "25", "sterile": "yes"},
    )
    variants = catalogue.generate_variants(
        actor,
        product.id,
        [{"size": "Small"}, {"size": "Medium"}, {"size": "Large"}],
    )
    catalogue.amend_product(actor, product.id, {"manufacturer": "Medisafe International"})
    session.commit()  # type: ignore[attr-defined]

    retrieved = catalogue.require_product(actor, product.id)
    values = fields.get_values(actor, target="product", owner_id=product.id)
    converted = units.convert_quantity(
        actor, amount=Decimal("2"), from_unit_id=box, to_unit_id=each, product_id=product.id
    )
    record(
        "Product with variants, attributes, two units, barcode and custom fields",
        len(variants) == 3
        and len(retrieved.barcodes) == 1
        and retrieved.manufacturer == "Medisafe International"
        and len(values) == 3
        and converted == Decimal("200.000000"),
        f"{len(variants)} variants, {len(values)} custom fields, 2 boxes = {converted:.0f} each",
    )

    # --- Criterion 2 ------------------------------------------------------------------
    # A non-stock item requires no stock balance anywhere in the system.
    refusals: list[str] = []
    component = catalogue.create_product(
        actor,
        ProductDraft(sku="KIT-COMPONENT", name="Kit component", stock_unit_id=each),
    )
    for item_class in (ItemClass.NON_STOCK, ItemClass.SERVICE, ItemClass.BUNDLE):
        created = catalogue.create_product(
            actor,
            ProductDraft(
                sku=f"NS-{item_class.value.upper().replace('_', '')}",
                name=f"A {item_class.value} item",
                stock_unit_id=each,
                item_class=item_class,
                # A bundle carries components; its components hold the balances.
                components=[(component.id, Decimal("2"), each)]
                if item_class is ItemClass.BUNDLE
                else [],
            ),
        )
        try:
            assert_stock_permitted(ItemClass(created.item_class), created.sku)
            refusals.append(f"{item_class.value} was NOT refused")
        except ConflictError:
            pass
    session.commit()  # type: ignore[attr-defined]
    record(
        "A non-stock, service or bundle item carries no stock balance",
        not refusals,
        "; ".join(refusals) or "all three classes refuse a stock balance",
    )

    # --- Criterion 3 ------------------------------------------------------------------
    # A 1,000-line import completes with a pre-commit error report and a clean rollback.
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["sku", "name", "stock_unit", "item_class", "list_price"])
    for index in range(1, 1001):
        writer.writerow([f"IMP-{index:05d}", f"Imported product {index}", "ea", "stocked", "99.50"])
    writer.writerow(["IMP-BAD-1", "Unknown unit", "nonexistent", "stocked", ""])
    writer.writerow(["IMP-BAD-2", "Bad price", "ea", "stocked", "twelve"])
    content = buffer.getvalue().encode("utf-8")

    before = session.scalar(select(func.count()).select_from(Product))  # type: ignore[attr-defined]
    started = time.perf_counter()
    preview = importer.preview(
        actor, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=content
    )
    preview_ms = (time.perf_counter() - started) * 1000
    rejected_commit = importer.commit(
        actor, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=content
    )
    session.commit()  # type: ignore[attr-defined]
    after_rejection = session.scalar(select(func.count()).select_from(Product))  # type: ignore[attr-defined]

    clean = content.replace(b"nonexistent", b"ea").replace(b"twelve", b"12.00")
    started = time.perf_counter()
    committed = importer.commit(
        actor, kind=ImportKind.PRODUCTS, filename="catalogue.csv", content=clean
    )
    commit_ms = (time.perf_counter() - started) * 1000
    session.commit()  # type: ignore[attr-defined]
    after_commit = session.scalar(select(func.count()).select_from(Product))  # type: ignore[attr-defined]

    record(
        "A 1,000-line import previews its faults and rolls back cleanly",
        preview.total == 1002
        and preview.rejected == 2
        and not preview.acceptable
        and not rejected_commit.committed
        and after_rejection == before
        and committed.committed
        and after_commit == before + 1002,
        f"preview {preview_ms:.0f} ms reporting {preview.rejected} faults; "
        f"rejection wrote nothing; commit {commit_ms:.0f} ms wrote {committed.to_create}",
    )

    # --- Criterion 4 ------------------------------------------------------------------
    # Search by SKU, name, barcode and party returns under 200 ms against 50,000 rows.
    present = session.scalar(select(func.count()).select_from(Product))  # type: ignore[attr-defined]
    needed = CATALOGUE_ROWS - int(present or 0)
    if needed > 0:
        _bulk_load(session, actor, each, needed)
    session.commit()  # type: ignore[attr-defined]
    total = session.scalar(select(func.count()).select_from(Product))  # type: ignore[attr-defined]

    parties.create(
        actor,
        PartyDraft(
            name="Banhart Specialist Hospital",
            is_customer=True,
            tax_identifier="C0009876543",
        ),
    )
    for index in range(2000):
        parties.create(
            actor, PartyDraft(name=f"Bulk party {index}", code=f"BP-{index:05d}", is_customer=True)
        )
    session.commit()  # type: ignore[attr-defined]

    measurements: dict[str, float] = {}
    for label, call in (
        ("SKU", lambda: catalogue.search(actor, term="GLV-NITRILE")),
        ("name", lambda: catalogue.search(actor, term="examination glove")),
        ("barcode", lambda: catalogue.by_barcode(actor, "5012345678900")),
        ("party", lambda: parties.search(actor, term="banhart")),
    ):
        call()  # prime the statement cache, as a running application would be
        started = time.perf_counter()
        call()
        measurements[label] = (time.perf_counter() - started) * 1000

    slowest = max(measurements.values())
    record(
        f"Search by SKU, name, barcode and party under {SEARCH_BUDGET_MS:.0f} ms "
        f"against {total:,} rows",
        slowest < SEARCH_BUDGET_MS,
        ", ".join(f"{label} {value:.1f} ms" for label, value in measurements.items()),
    )

    session.close()  # type: ignore[attr-defined]
    return outcomes


def _bulk_load(session: object, actor: Actor, unit_id: uuid.UUID, count: int) -> None:
    """Insert catalogue rows directly, to build a fixture of realistic size."""
    from nickland.platform.ids import new_id

    batch: list[dict[str, object]] = []
    for index in range(count):
        sku = f"BULK-{index:06d}"
        name = f"Bulk catalogue item {index}"
        manufacturer = f"Maker {index % 97}"
        batch.append(
            {
                "id": new_id(),
                "business_id": actor.business_id,
                "sku": sku,
                "name": name,
                "item_class": "stocked",
                "stock_unit_fk": unit_id,
                "sales_unit_fk": unit_id,
                "purchase_unit_fk": unit_id,
                "tracking_policy": "none",
                "requires_expiry": False,
                "valuation_policy": "business_default",
                "manufacturer": manufacturer,
                "is_active": True,
                "is_sellable": True,
                "is_purchasable": True,
                "is_variant_parent": False,
                "search_text": f"{sku} {name} {manufacturer}".lower(),
            }
        )
        if len(batch) >= 2000:
            session.execute(Product.__table__.insert(), batch)  # type: ignore[attr-defined]
            batch.clear()
    if batch:
        session.execute(Product.__table__.insert(), batch)  # type: ignore[attr-defined]


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

    failures = [label for label, passed, _ in outcomes if not passed]
    if failures:
        print(f"{len(failures)} criterion(a) not met: {failures}")
        return 1
    print("Every Phase 2 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
