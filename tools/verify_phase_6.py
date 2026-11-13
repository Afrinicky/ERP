#!/usr/bin/env python3
"""Verify each Phase 6 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_6.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase6-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError, OperationalError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.designer import DesignerService  # noqa: E402
from nickland.application.services.documents import DocumentRecordService  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.organisation import OrganisationService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.documents.service import DocumentRenderService  # noqa: E402
from nickland.domain.documents import Classification, DocumentKind  # noqa: E402
from nickland.domain.errors import ConflictError, ValidationError  # noqa: E402
from nickland.domain.expressions import (  # noqa: E402
    FUNCTIONS,
    EvaluationContext,
    Node,
    evaluate,
    parse,
)
from nickland.domain.organisation import DocumentKind as SequenceKind  # noqa: E402
from nickland.domain.templates import INTERNAL_KEYS, SectionKind  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.models import Business  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
Report = tuple[str, bool, str]

ESCAPES = [
    "__import__('os').system('id')",
    "().__class__",
    "[].__class__.__base__.__subclasses__()",
    "''.__class__.__mro__",
    "[x for x in range(10)]",
    "lambda: 1",
    "exec('print(1)')",
    "eval('1')",
    "open('/etc/passwd')",
    "globals()",
    "getattr(1, 'real')",
    "compile('1', '', 'eval')",
    "import os",
    "print(1)",
    "[a].b",
    "[a][0]",
    "round.__call__(1)",
    "1;2",
    "`id`",
    "$(id)",
    "{1:2}",
    "1 ** 2",
    "~1",
    "[os.environ]",
]
"""Every construct by which an expression language is ordinarily escaped."""


def build() -> tuple[Session, sessionmaker[Session], Actor, uuid.UUID]:
    """A provisioned business, with the twelve document types seeded."""
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-6')"))

    factory = create_session_factory(engine)
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
    return session, factory, actor, business_id


def allocate(factory: sessionmaker[Session], actor: Actor, kind: str) -> str | None:
    """One worker's allocation, in a session of its own. ``None`` where it was refused."""
    session = factory()
    try:
        number = OrganisationService(session).allocate_number(actor, kind, branch_id=None).number
        session.commit()
        return number
    except (OperationalError, DBAPIError):
        session.rollback()
        return None
    finally:
        session.close()


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, factory, actor, business_id = build()
    designer = DesignerService(session)
    records = DocumentRecordService(session)
    renderer = DocumentRenderService(session)
    pro_forma = records.type_by_code(business_id, "pro_forma")
    assert pro_forma is not None

    # -- 1. Columns and sections are composed, and the result previewed ----------------

    draft = designer.draft(actor, pro_forma.id)
    session.commit()
    items = draft.definition.section(SectionKind.ITEMS)
    original = [column.id for column in items.columns]

    designer.add_table_column(actor, draft.template.id, items.id, "line.weight")
    designer.move_table_column(actor, draft.template.id, items.id, "line_batch", -2)
    designer.remove_table_column(actor, draft.template.id, items.id, "line_sku")
    state = designer.amend_column(
        actor,
        draft.template.id,
        items.id,
        "line_description",
        {"label": "Particulars of supply", "width_percent": 30},
    )
    session.commit()
    after = state.definition.section(SectionKind.ITEMS)
    identifiers = [column.id for column in after.columns]
    bindings = [column.binding for column in after.columns]
    renamed = next(item for item in after.columns if item.id == "line_description")
    record(
        "Columns are added, removed, reordered, re-labelled and resized",
        "line.weight" in bindings
        and "line_sku" not in identifiers
        and identifiers.index("line_batch") < original.index("line_batch")
        and renamed.label == "Particulars of supply"
        and renamed.width_percent == 30,
        f"{len(original)} columns became {len(identifiers)}",
    )

    before_move = [section.id for section in state.definition.sections].index("terms")
    designer.move_section(actor, draft.template.id, "terms", -3)
    state = designer.add_section(actor, draft.template.id, SectionKind.CUSTOM, title="Compliance")
    session.commit()
    added = next(item for item in state.definition.sections if item.title == "Compliance")
    state = designer.add_element(
        actor,
        draft.template.id,
        added.id,
        {
            "kind": "text",
            "label": "Registration",
            "text": "Supplied under FDA registration 2026/MD/0441.",
        },
    )
    session.commit()
    order = [section.id for section in state.definition.sections]
    record(
        "Sections are reordered and added",
        order.index("terms") == before_move - 3
        and added.id in order
        and any(
            len(section.elements) == 1
            for section in state.definition.sections
            if section.id == added.id
        ),
        f"terms moved from position {before_move + 1} to {order.index('terms') + 1}; "
        f"{len(order)} sections",
    )

    preview = renderer.preview_version(actor, draft.template.id)
    record(
        "The result is previewed through the same composer as final output",
        "Particulars of supply" in preview and "Preview" in preview and "Compliance" in preview,
        f"{len(preview):,} characters of watermarked HTML",
    )

    published = designer.publish(actor, draft.template.id)
    session.commit()
    record(
        "The amended version publishes and supersedes the one before it",
        published.version == 2
        and [item.is_published for item in designer.versions(actor, pro_forma.id)] == [False, True],
        "v1 superseded by v2",
    )

    # -- 2. A wholly new document type, with its own fields and numbering --------------

    created = designer.create_type(
        actor,
        code="site_report",
        name="Installation site report",
        kind=DocumentKind.DELIVERY_NOTE,
        classification=Classification.CUSTOMER_FACING,
        prefix="SR",
        fields=[
            {"key": "engineer", "label": "Attending engineer"},
            {"key": "commissioned_on", "label": "Commissioned on", "value_kind": "date"},
            {
                "key": "reading",
                "label": "Calibration reading",
                "value_kind": "quantity",
                "per_line": True,
            },
        ],
    )
    session.commit()
    offered = {field.key for field in designer.palette(actor, created.id)}
    numbers = [
        OrganisationService(session).allocate_number(actor, created.code, branch_id=None).number
        for _ in range(3)
    ]
    session.commit()
    own_draft = designer.draft(actor, created.id)
    session.commit()
    bound = designer.add_table_column(
        actor,
        own_draft.template.id,
        own_draft.definition.section(SectionKind.ITEMS).id,
        "custom.reading",
    )
    designer.publish(actor, own_draft.template.id)
    session.commit()
    record(
        "A wholly new document type carries its own fields and numbering",
        len(created.fields) == 3
        and {"custom.engineer", "custom.commissioned_on", "custom.reading"} <= offered
        and numbers == ["SR-2026-0001", "SR-2026-0002", "SR-2026-0003"]
        and bound.is_publishable,
        f"{created.code}: 3 fields, numbered {numbers[0]} to {numbers[-1]}",
    )

    # -- 3. A customer-facing template cannot expose cost, margin or commission --------

    facing = {field.key for field in designer.palette(actor, pro_forma.id)}
    record(
        "A customer-facing palette offers no internal figure",
        facing.isdisjoint(INTERNAL_KEYS),
        f"{len(INTERNAL_KEYS)} internal fields withheld",
    )

    second = designer.draft(actor, pro_forma.id)
    session.commit()
    table = second.definition.section(SectionKind.ITEMS)
    refused_at_palette = False
    try:
        designer.add_table_column(actor, second.template.id, table.id, "internal.line_cost")
    except ValidationError:
        refused_at_palette = True

    # Force it past the editor by writing the definition directly, then try to publish.
    raw = second.definition.as_dict()
    section = next(item for item in raw["sections"] if item["kind"] == SectionKind.ITEMS.value)
    section["columns"].append(
        {
            "id": "forced",
            "label": "Our cost",
            "binding": "internal.line_cost",
            "width_percent": 8,
            "alignment": "right",
            "format": {},
            "visibility": {},
            "subtotal": False,
        }
    )
    saved = designer.save(actor, second.template.id, raw)
    session.commit()
    refused_at_publication = False
    try:
        designer.publish(actor, second.template.id)
    except ValidationError:
        refused_at_publication = True
    record(
        "An internal binding is refused at the editor and again at publication",
        refused_at_palette and refused_at_publication and not saved.is_publishable,
        "refused twice; the draft itself is kept but is not publishable",
    )

    designer.revert(actor, second.template.id)
    session.commit()

    rendered = renderer.preview_version(actor, records.published_template(pro_forma).id)
    leaks = [
        word
        for word in ("Supplier cost", "Margin", "Affiliate commission", "internal.")
        if word in rendered
    ]
    record(
        "No internal figure appears in rendered customer output",
        not leaks,
        "cost, margin and commission absent from the rendered pro forma",
    )

    # -- 4. A document issued under v1 reprints identically after v2 ------------------

    context = records.sample_context(business_id, DocumentKind.DELIVERY_NOTE)
    issued = renderer.issue(actor, "delivery_note", context)
    session.commit()
    first_hash = issued.content_hash
    first_version = issued.template_version
    first_bytes = bytes(issued.content)

    third = designer.draft(actor, records.type_by_code(business_id, "delivery_note").id)  # type: ignore[union-attr]
    session.commit()
    designer.remove_table_column(
        actor,
        third.template.id,
        third.definition.section(SectionKind.ITEMS).id,
        "line_unit",
    )
    advanced = designer.publish(actor, third.template.id)
    session.commit()
    reprint = records.require_issued(actor, issued.id)
    record(
        "A document issued under v1 reprints identically after v2 is published",
        reprint.content_hash == first_hash
        and bytes(reprint.content) == first_bytes
        and reprint.template_version == first_version
        and advanced.version == first_version + 1,
        f"{len(first_bytes):,} bytes unchanged; hash {first_hash[:12]}",
    )

    # -- 5. Expression evaluation cannot execute arbitrary code -----------------------

    escaped: list[str] = []
    for source in ESCAPES:
        try:
            tree = parse(source)
        except ValidationError:
            continue
        try:
            evaluate(tree, EvaluationContext())
        except ValidationError:
            continue
        escaped.append(source)
    record(
        "No expression reaches outside the language",
        not escaped,
        f"{len(ESCAPES)} known escapes refused" + (f"; ACCEPTED: {escaped}" if escaped else ""),
    )

    from typing import get_args

    node_types = {item.__name__ for item in get_args(Node)}
    record(
        "The language has no node for attribute access, indexing or assignment",
        node_types == {"Literal", "FieldReference", "Unary", "Binary", "Call"}
        and {"sum", "round", "min", "max", "if", "abs", "words"} == FUNCTIONS,
        f"{len(node_types)} node types, {len(FUNCTIONS)} functions, no eval and no ast",
    )

    source_text = (ROOT / "src" / "nickland" / "domain" / "expressions.py").read_text(
        encoding="utf-8"
    )
    record(
        "The evaluator uses neither eval, exec nor the ast module",
        "import ast" not in source_text
        and "eval(" not in source_text
        and "exec(" not in source_text
        and "compile(" not in source_text,
        "hand-written tokeniser, parser and interpreter",
    )

    # -- 6. Numbers are unique under concurrent issue ---------------------------------

    session.commit()
    workers = 8
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda _: allocate(factory, actor, SequenceKind.SALES_INVOICE.value),
                range(workers),
            )
        )
    allocated = [number for number in results if number is not None]
    counters = sorted(int(number.rsplit("-", 1)[-1]) for number in allocated)
    record(
        "A document number is never handed out twice under concurrent issue",
        bool(allocated)
        and len(set(allocated)) == len(allocated)
        and counters == list(range(1, len(counters) + 1)),
        f"{workers} workers at once: {len(allocated)} numbered, {len(set(allocated))} distinct",
    )

    reused = False
    try:
        renderer.issue(actor, "delivery_note", context)
    except ConflictError:
        reused = True
    session.rollback()
    record(
        "A number already issued cannot be issued again",
        reused,
        f"{issued.number} refused on a second issue",
    )

    # -- Portability ------------------------------------------------------------------

    portable = json.dumps(designer.export(actor, published.id).as_dict())
    record(
        "A template exports its definition and carries no business data",
        "Nicholas Nickland" not in portable
        and "proprietor" not in portable
        and str(business_id) not in portable
        and "sections" in portable,
        f"{len(portable):,} bytes of definition only",
    )

    broken = json.loads(portable)
    target = next(
        item for item in broken["definition"]["sections"] if item["kind"] == SectionKind.ITEMS.value
    )
    target["columns"][0]["binding"] = "line.colour"
    report = designer.inspect_import(actor, created.id, broken)
    unresolved_refused = False
    try:
        designer.accept_import(actor, created.id, broken)
    except ValidationError:
        unresolved_refused = True
    record(
        "An import reports every binding this installation does not have",
        "line.colour" in report.unresolved and not report.is_acceptable and unresolved_refused,
        f"unresolved: {list(report.unresolved)}",
    )

    delivery_note = records.type_by_code(business_id, "delivery_note")
    assert delivery_note is not None
    locked = False
    try:
        designer.set_classification(actor, delivery_note.id, Classification.INTERNAL)
    except ConflictError:
        locked = True
    record(
        "Classification is fixed once a type has issued a document",
        locked,
        "a type that has issued cannot be reclassified",
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
    print("Every Phase 6 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
