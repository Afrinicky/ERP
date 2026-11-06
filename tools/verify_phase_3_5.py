#!/usr/bin/env python3
"""Verify each Phase 3.5 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database and writes its sample documents to a temporary
directory, so it may be run at any time:

    .venv/bin/python tools/verify_phase_3_5.py --keep /tmp/documents

With ``--keep`` the rendered PDFs are left behind for inspection.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase35-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.documents import DocumentRecordService  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.documents.render import render_html, render_thermal_text  # noqa: E402
from nickland.documents.service import DocumentRenderService  # noqa: E402
from nickland.domain.documents import DocumentKind, PageFormat  # noqa: E402
from nickland.domain.errors import ConflictError  # noqa: E402
from nickland.domain.theme import ThemeDefinition, failures, get_preset, load_presets  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.models import Business  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
Outcome = tuple[str, bool, str]


def build_session() -> tuple[object, Actor, uuid.UUID]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-3-5')"))

    session = create_session_factory(engine)()
    SetupService(session).provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password=ADMIN,
            theme_preset="nickland",
            tax_identifier="C0003086224",
            email="nicklandsales@example.test",
            telephone="+233 24 071 9356",
        )
    )
    session.commit()
    business_id = session.scalar(select(Business.id))
    assert business_id is not None
    actor = IdentityService(session).sign_in("proprietor", ADMIN, business_id=business_id).actor
    session.commit()
    return session, actor, business_id


def verify(keep: Path | None) -> list[Outcome]:
    outcomes: list[Outcome] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, actor, business_id = build_session()
    records = DocumentRecordService(session)  # type: ignore[arg-type]
    documents = DocumentRenderService(session)  # type: ignore[arg-type]

    # --- Criterion 1 ------------------------------------------------------------------
    types = records.types(actor)
    rendered: dict[str, bytes] = {}
    failed: list[str] = []
    for document_type in types:
        try:
            result = documents.preview(actor, document_type.id)
            if not result.content.startswith(b"%PDF"):
                failed.append(f"{document_type.code}: not a PDF")
                continue
            rendered[document_type.code] = result.content
            if keep:
                (keep / f"{document_type.code}.pdf").write_bytes(result.content)
        except Exception as error:
            failed.append(f"{document_type.code}: {error}")
    record(
        "Every default type renders to a PDF",
        len(types) == 12 and not failed,
        f"{len(rendered)} of {len(types)} rendered" + (f"; {failed}" if failed else ""),
    )

    # --- Criterion 2 ------------------------------------------------------------------
    receipt = records.type_by_code(business_id, "receipt")
    assert receipt is not None
    rolls: dict[str, int] = {}
    for page in (PageFormat.ROLL_80, PageFormat.ROLL_58):
        result = documents.preview(actor, receipt.id, page=page)
        rolls[page.value] = len(result.content)
        if keep:
            (keep / f"receipt-{page.value}.pdf").write_bytes(result.content)
    widths: dict[int, bool] = {}
    for width in (42, 32):
        body = documents.preview_thermal(actor, receipt.id, width=width)
        widths[width] = bool(body) and all(len(line) <= width for line in body.splitlines())
        if keep:
            (keep / f"receipt-{width}col.txt").write_text(body)
    record(
        "A receipt renders on both rolls, and as text at 42 and 32 columns",
        all(rolls.values()) and all(widths.values()),
        f"80mm {rolls['roll_80']} bytes, 58mm {rolls['roll_58']} bytes; "
        f"text fits at {sorted(w for w, ok in widths.items() if ok)}",
    )

    # --- Criterion 3 ------------------------------------------------------------------
    leaked: list[str] = []
    for code in ("waybill", "delivery_note", "packing_list"):
        document_type = records.type_by_code(business_id, code)
        assert document_type is not None
        context = records.sample_context(business_id, DocumentKind(document_type.kind))
        markup = render_html(context, options=records.options_for(document_type))
        plain = re.sub(r"<[^>]+>", " ", markup)
        for line in context.lines:
            for amount in (str(line.unit_price), str(line.line_total)):
                if amount in plain:
                    leaked.append(f"{code}: {amount}")
        if str(context.totals.grand_total) in plain:
            leaked.append(f"{code}: grand total")
    record(
        "A document that travels with the goods discloses no price",
        not leaked,
        "three kinds checked against every line and total" if not leaked else str(leaked[:3]),
    )

    # --- Criterion 4 ------------------------------------------------------------------
    context = records.sample_context(business_id, DocumentKind.INVOICE)
    first, second = render_html(context), render_html(context)
    record(
        "The same context renders identical markup",
        first == second,
        f"{len(first)} characters, byte for byte",
    )

    # --- Criteria 5 and 6 -------------------------------------------------------------
    invoice_context = records.sample_context(business_id, DocumentKind.INVOICE)
    invoice_context.number = "INV-VERIFY-0001"
    issued = documents.issue(actor, "invoice", invoice_context)
    session.commit()
    original = bytes(issued.content)

    records.amend_template(actor, issued.type_fk, {"show_banking": False, "show_totals": False})
    session.commit()
    reprinted = documents.reprint(actor, issued.id).content
    fresh = documents.preview(actor, issued.type_fk)
    record(
        "A reprint serves the issued bytes, not a re-render",
        reprinted == original and fresh.content != original,
        f"{len(original)} bytes returned unchanged after the template was amended",
    )

    duplicate = records.sample_context(business_id, DocumentKind.INVOICE)
    duplicate.number = "INV-VERIFY-0001"
    try:
        documents.issue(actor, "invoice", duplicate)
        record("A document number is never reused", False, "the number was accepted twice")
    except ConflictError as error:
        session.rollback()
        record("A document number is never reused", True, str(error)[:70])

    # --- Criterion 7 ------------------------------------------------------------------
    brand = get_preset("nickland")
    brand_failures = failures(ThemeDefinition(preset=brand))
    every = {
        code: failures(ThemeDefinition(preset=preset)) for code, preset in load_presets().items()
    }
    record(
        "The brand palette passes the contrast audit",
        not brand_failures and not any(every.values()),
        f"{len(every)} presets audited, {sum(len(v) for v in every.values())} failures",
    )
    record(
        "The brand palette is the one offered first",
        next(iter(load_presets())) == "nickland" and brand.recommended,
        f"recommended: {brand.name}, from {brand.source}",
    )

    # --- Beyond the criteria ----------------------------------------------------------
    thermal = render_thermal_text(
        records.sample_context(business_id, DocumentKind.RECEIPT), width=42
    )
    record(
        "The thermal receipt states its total",
        "TOTAL" in thermal and "RECEIPT" in thermal,
        f"{len(thermal.splitlines())} lines at 42 columns",
    )

    hostile = records.sample_context(business_id, DocumentKind.INVOICE)
    hostile.lines[0] = type(hostile.lines[0])(
        sku="X",
        name='<script>alert("x")</script>',
        quantity=hostile.lines[0].quantity,
        unit_price=hostile.lines[0].unit_price,
        line_total=hostile.lines[0].line_total,
    )
    markup = render_html(hostile)
    record(
        "A product name is escaped, not interpreted",
        "<script>" not in markup and "&lt;script&gt;" in markup,
        "markup in a field reaches the page as text",
    )

    session.close()
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=Path, help="write the rendered documents here")
    arguments = parser.parse_args()
    if arguments.keep:
        arguments.keep.mkdir(parents=True, exist_ok=True)

    try:
        outcomes = verify(arguments.keep)
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)

    width = max(len(label) for label, _, _ in outcomes)
    print()
    for label, passed, detail in outcomes:
        print(f"  {'PASS' if passed else 'FAIL'}  {label.ljust(width)}  {detail}")
    print()
    if arguments.keep:
        print(f"Documents written to {arguments.keep}")

    broken = [label for label, passed, _ in outcomes if not passed]
    if broken:
        print(f"{len(broken)} criterion(a) not met: {broken}")
        return 1
    print("Every Phase 3.5 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
