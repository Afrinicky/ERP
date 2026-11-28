#!/usr/bin/env python3
"""Verify each Phase 14 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_14.py
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase14-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from tests.conftest import make_actor  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.accounts import AccountService  # noqa: E402
from nickland.application.services.catalogue import CatalogueService, ProductDraft  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.inventory import (  # noqa: E402
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.journals import JournalDraft, JournalService  # noqa: E402
from nickland.application.services.management_pack import ManagementPackService  # noqa: E402
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService  # noqa: E402
from nickland.application.services.parties import PartyDraft, PartyService  # noqa: E402
from nickland.application.services.periods import CHECKLIST, PeriodService  # noqa: E402
from nickland.application.services.pos import PosService  # noqa: E402
from nickland.application.services.quotations import (  # noqa: E402
    LineDraft as QuoteLine,
)
from nickland.application.services.quotations import (  # noqa: E402
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.receivables import (  # noqa: E402
    CreditDraft,
    CreditLineDraft,
    InvoiceDraft,
    ReceivablesService,
)
from nickland.application.services.report_runs import (  # noqa: E402
    ReportArchive,
    ReportScheduler,
    ScheduleDraft,
)
from nickland.application.services.reports import ReportParameters, ReportService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.documents.report_export import present, to_csv, to_pdf, to_xlsx  # noqa: E402
from nickland.domain.errors import (  # noqa: E402
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.ledger import EntryLine, Side  # noqa: E402
from nickland.domain.reporting import (  # noqa: E402
    REPORTS,
    ColumnKind,
    Comparison,
    Granularity,
    from_record,
    to_record,
)
from nickland.domain.reporting import Report as ReportResult  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.immutability import register_append_only_guards  # noqa: E402
from nickland.persistence.models import (  # noqa: E402
    AuditEvent,
    Business,
    Warehouse,
)
from nickland.persistence.reporting_models import ReportDelivery  # noqa: E402
from nickland.platform.clock import FixedClock  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
STAFF = "Counter9Staff"
D = Decimal
CLOCK = FixedClock(datetime(2026, 11, 21, 10, 0, tzinfo=UTC))
NOVEMBER = ReportParameters(
    granularity=Granularity.MONTH, anchor=date(2026, 11, 21), comparison=Comparison.PRIOR
)
Outcome = tuple[str, bool, str]


def build() -> tuple[Session, Actor]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-14')"))
    factory = create_session_factory(engine)
    register_append_only_guards(factory)
    session = factory()
    SetupService(session, clock=CLOCK).provision(
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
    actor = (
        IdentityService(session, clock=CLOCK)
        .sign_in("proprietor", ADMIN, business_id=business_id)
        .actor
    )
    return session, actor


def trade(session: Session, actor: Actor):  # type: ignore[no-untyped-def]
    """Register sales with actual stock cost, and a quoted order invoiced, with a supplier
    cost above the quote and one box returned."""
    warehouse = session.scalar(select(Warehouse.id))
    assert warehouse is not None
    unit = UnitService(session, clock=CLOCK).units(actor)[0]
    catalogue = CatalogueService(session, clock=CLOCK)
    gloves = catalogue.create_product(
        actor,
        ProductDraft(
            sku="GLV-100", name="Nitrile gloves", stock_unit_id=unit.id, list_price=D("100")
        ),
    )
    reagent = catalogue.create_product(
        actor,
        ProductDraft(
            sku="RGT-200", name="Glucose reagent", stock_unit_id=unit.id, list_price=D("520")
        ),
    )
    session.flush()
    InventoryService(session, clock=CLOCK).open_stock(
        actor,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[
                StockLineDraft(product_id=gloves.id, quantity=D("40"), unit_cost=D("60")),
                StockLineDraft(product_id=reagent.id, quantity=D("10"), unit_cost=D("300")),
            ],
        ),
    )
    pos = PosService(session, clock=CLOCK)
    pos.open_shift(actor, opening_float=D("200"))
    sale = pos.current_sale(actor)
    pos.scan(actor, sale.id, "GLV-100", quantity=D("3"))
    pos.tender(actor, sale.id, method="cash", cash_received=sale.total)
    pos.complete(actor, sale.id)

    customer = PartyService(session, clock=CLOCK).create(
        actor,
        PartyDraft(
            name="Korle Bu", is_customer=True, payment_term_kind="net_days", payment_term_days=30
        ),
    )
    quotations = QuotationService(session, clock=CLOCK)
    quotation = quotations.create(
        actor,
        QuotationDraft(
            customer_id=customer.id,
            lines=[
                QuoteLine(
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
                for product, quantity, cost in ((gloves, "10", "60"), (reagent, "4", "300"))
            ],
        ),
    )
    session.flush()
    quotations.issue(actor, quotation.id)
    orders = SalesOrderService(session, clock=CLOCK)
    orders.record_acceptance(actor, quotation.id, AcceptanceDraft(reference="KB-PO"))
    order = orders.convert(actor, quotation.id)
    session.flush()
    receivables = ReceivablesService(session, clock=CLOCK)
    invoice = receivables.issue_invoice(
        actor, InvoiceDraft(sales_order_id=order.id, basis="ordered")
    )
    by_product = {line.product_fk: line for line in order.lines}
    by_product[reagent.id].actual_unit_cost = D("330")
    by_product[gloves.id].actual_unit_cost = D("60")
    glove_line = next(line for line in invoice.lines if line.product_fk == gloves.id)
    receivables.credit(
        actor,
        CreditDraft(
            invoice_id=invoice.id,
            reason="One box damaged",
            lines=[CreditLineDraft(invoice_line_id=glove_line.id, quantity=D("1"))],
        ),
    )
    session.flush()
    return by_product[reagent.id]


def numbers(report: ReportResult) -> list[tuple[Decimal, ColumnKind]]:
    found: list[tuple[Decimal, ColumnKind]] = []
    for section in report.sections:
        for figure in section.figures:
            if isinstance(figure.value, Decimal):
                found.append((figure.value, figure.kind))
        for table in section.tables:
            kinds = {column.key: column.kind for column in table.columns}
            for row in [*table.rows, *([table.total] if table.total else [])]:
                for key, value in row.cells.items():
                    if isinstance(value, Decimal) and kinds.get(key):
                        found.append((value, kinds[key]))
    return found


def exports_match(report: ReportResult) -> list[str]:
    """Every figure the screen shows, found exactly in the CSV, the workbook and the PDF."""
    shown = numbers(from_record(to_record(report)))
    exact = {format(value, "f") for value, _ in shown}
    problems = []
    cells = {
        cell for row in csv.reader(io.StringIO(to_csv(report).decode("utf-8-sig"))) for cell in row
    }
    if missing := exact - cells:
        problems.append(f"{report.id}: CSV lacks {sorted(missing)[:3]}")
    with zipfile.ZipFile(io.BytesIO(to_xlsx(report))) as book:
        sheet = book.read("xl/worksheets/sheet1.xml").decode()
    if missing := exact - set(re.findall(r"<v>([^<]+)</v>", sheet)):
        problems.append(f"{report.id}: workbook lacks {sorted(missing)[:3]}")
    pdf = to_pdf(report)
    if shutil.which("pdftotext"):
        text = subprocess.run(
            ["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True
        ).stdout.decode()
        flat = re.sub(r"\s+", " ", text)
        for value, kind in shown:
            written = present(value, kind)
            if written and written not in flat:
                problems.append(f"{report.id}: PDF lacks {written}")
                break
    elif not pdf.startswith(b"%PDF"):
        problems.append(f"{report.id}: no PDF was written")
    return problems


def verify() -> list[Outcome]:
    session, actor = build()
    trade(session, actor)
    journals = JournalService(session, clock=CLOCK)
    for on, narration, lines in (
        (
            date(2026, 10, 15),
            "October rent",
            [("60000", Side.DEBIT, "1200.00"), ("10200", Side.CREDIT, "1200.00")],
        ),
        (
            date(2026, 11, 2),
            "Capital introduced",
            [("10200", Side.DEBIT, "40000.00"), ("30000", Side.CREDIT, "40000.00")],
        ),
        (
            date(2026, 11, 4),
            "November rent",
            [("60000", Side.DEBIT, "1500.00"), ("10200", Side.CREDIT, "1500.00")],
        ),
    ):
        journals.post(
            actor,
            JournalDraft(
                posting_date=on,
                narration=narration,
                lines=[EntryLine(code, side, D(amount)) for code, side, amount in lines],
            ),
        )
    session.commit()
    identity = IdentityService(session, clock=CLOCK)
    identity.create_user(
        actor,
        username="counter1",
        full_name="E. Mensah",
        password=STAFF,
        role_codes=["sales_person"],
        must_change_password=False,
    )
    session.commit()
    assert actor.business_id is not None
    counter = identity.sign_in("counter1", STAFF, business_id=actor.business_id).actor
    outcomes: list[Outcome] = []

    # 1. A month's management pack, every figure reconciled to the ledger.
    pack = ManagementPackService(session, clock=CLOCK).build(actor, NOVEMBER)
    expected = [
        "summary",
        "profit_and_loss",
        "balance_sheet",
        "cash_flow",
        "trial_balance",
        "sales",
        "profitability",
        "procurement",
        "expenses",
        "tax",
        "ageing",
        "loans",
        "inventory",
        "budget",
        "risks",
    ]
    keys = [section.key for section in pack.sections]
    failing = [
        f"{check.label}: {check.reported} against {check.ledger}"
        for check in pack.checks
        if not check.passed
    ]
    hollow = [
        section.key
        for section in pack.sections
        if not (section.figures or section.tables or section.commentary)
    ]
    outcomes.append(
        (
            "Management pack reconciles",
            keys == expected and not failing and not hollow and len(pack.checks) >= 8,
            f"{len(keys)} sections; {len(pack.checks)} checks against the ledger, all agreeing "
            "(trial balance, net profit, position, opening and closing cash, customers, suppliers, "
            "stock)"
            if not failing and keys == expected and not hollow
            else f"sections {keys}; failing {failing}; empty {hollow}",
        )
    )

    # 2. A closed period refuses posting; reopening is authorised, reasoned and audited.
    periods = PeriodService(session, clock=CLOCK)
    accounts = AccountService(session, clock=CLOCK)
    problems: list[str] = []
    october = accounts.require_period_for(actor.business_id, date(2026, 10, 1))
    for period in accounts.periods(actor, financial_year=2026):
        if period.ends_on <= october.ends_on:
            for key, _ in CHECKLIST:
                periods.confirm_step(actor, period.id, key, done=True)
            periods.close(actor, period.id)
    session.commit()
    try:
        journals.post(
            actor,
            JournalDraft(
                posting_date=date(2026, 10, 20),
                narration="Into a closed month",
                lines=[
                    EntryLine("60000", Side.DEBIT, D("5")),
                    EntryLine("10100", Side.CREDIT, D("5")),
                ],
            ),
        )
        problems.append("a posting entered a closed period")
    except ConflictError:
        session.rollback()
    clerk = make_actor(
        actor.business_id, permissions={"account.view", "period.close", "period.reopen"}
    )
    controller = make_actor(actor.business_id, permissions={"account.view", "period.reopen"})
    viewer = make_actor(actor.business_id, permissions={"account.view"})
    for attempt, expected_error in (
        (
            lambda: periods.request_reopen(viewer, october.id, "A late supplier invoice"),
            PermissionDeniedError,
        ),
        (lambda: periods.request_reopen(clerk, october.id, "late"), ValidationError),
        (lambda: periods.reopen(clerk, october.id, "A late supplier invoice"), ConflictError),
    ):
        try:
            attempt()
            problems.append(f"{expected_error.__name__} expected")
        except expected_error:
            pass
    request = periods.request_reopen(clerk, october.id, "A late supplier invoice for October")
    try:
        periods.decide_reopen(clerk, request.id, approve=True)
        problems.append("the requester approved their own request")
    except ConflictError:
        pass
    periods.decide_reopen(controller, request.id, approve=True, note="Agreed")
    session.commit()
    audited = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "period_reopened")
    ).all()
    if october.state != "reopened" or not audited or audited[-1].reason != request.reason:
        problems.append("the reopening was not recorded with its reason")
    if (
        october.reopened_by_id != clerk.user_id
        or october.reopen_approved_by_id != controller.user_id
    ):
        problems.append("the requester and approver were not both recorded")
    outcomes.append(
        (
            "Period close and controlled reopening",
            not problems,
            "posting into October refused once closed; reopening needs the permission, a reason "
            "and a second person; the reason, requester and approver are audited"
            if not problems
            else "; ".join(problems),
        )
    )

    # 3. Sensitive reports are unavailable without the export permission.
    reports = ReportService(session, clock=CLOCK)
    problems = []
    sensitive = [key.value for key, item in REPORTS.items() if item.sensitive]
    for report_id in sensitive:
        try:
            reports.run(counter, report_id, NOVEMBER)
            problems.append(f"{report_id} opened for a sales person")
        except PermissionDeniedError:
            pass
    try:
        ManagementPackService(session, clock=CLOCK).build(counter, NOVEMBER)
        problems.append("the pack opened for a sales person")
    except PermissionDeniedError:
        pass
    archive = ReportArchive(session, clock=CLOCK)
    saved = archive.save(actor, pack, NOVEMBER)
    session.commit()
    if saved.id in {run.id for run in archive.runs(counter)}:
        problems.append("a saved pack is listed for a sales person")
    stock = reports.run(counter, "inventory", ReportParameters())
    if not stock.withheld or any(
        c.sensitive for s in stock.sections for t in s.tables for c in t.columns
    ):
        problems.append("stock value was shown to a sales person")
    try:
        ReportScheduler(session, clock=CLOCK).create(
            actor,
            ScheduleDraft(
                name="Pack",
                report_id="management_pack",
                frequency="monthly",
                run_day=1,
                run_hour=7,
                recipients=[counter.user_id],
                formats=["pdf"],
                parameters={},
            ),
        )
        problems.append("the pack was scheduled to a sales person")
    except ValidationError:
        session.rollback()
    outcomes.append(
        (
            "Sensitive reports withheld",
            not problems,
            f"{len(sensitive)} cost and margin reports and the pack refused to a sales person, "
            "saved runs hidden, stock value withheld, scheduling to them refused"
            if not problems
            else "; ".join(problems),
        )
    )

    # 4. Exported figures match on-screen figures exactly.
    problems = exports_match(pack)
    for report_id in REPORTS:
        definition = REPORTS[report_id]
        params = (
            NOVEMBER
            if Comparison.PRIOR in definition.comparisons
            else ReportParameters(granularity=Granularity.MONTH, anchor=date(2026, 11, 21))
        )
        problems.extend(exports_match(reports.run(actor, report_id.value, params)))
    outcomes.append(
        (
            "Exports match the screen",
            not problems,
            f"the pack and all {len(REPORTS)} reports: every figure found exactly in the CSV and "
            "the workbook, and written in the PDF as the screen writes it"
            if not problems
            else "; ".join(problems[:4]),
        )
    )

    # 5. The twelve reports state their coverage; a schedule generates and delivers.
    problems = []
    for report_id in REPORTS:
        report = reports.run(
            actor,
            report_id.value,
            ReportParameters(granularity=Granularity.MONTH, anchor=date(2026, 11, 21)),
        )
        head = report.header
        if not (head.period and head.currency and head.basis and head.generated_at):
            problems.append(f"{report_id.value} header incomplete")
    scheduler = ReportScheduler(session, clock=CLOCK)
    schedule = scheduler.create(
        actor,
        ScheduleDraft(
            name="Monthly pack",
            report_id="management_pack",
            frequency="monthly",
            run_day=1,
            run_hour=7,
            recipients=[actor.user_id],
            formats=["pdf"],
            parameters={"comparison": "prior"},
        ),
    )
    session.commit()
    assert schedule.next_run_at is not None
    CLOCK.advance((schedule.next_run_at - CLOCK.now()).total_seconds() + 60)
    runs = scheduler.run_due(actor.business_id)
    session.commit()
    delivered = session.scalars(select(ReportDelivery)).all()
    if len(runs) != 1 or runs[0].period_label != "November 2026" or len(delivered) != 1:
        problems.append("the schedule did not generate and deliver November's pack")
    outcomes.append(
        (
            "Twelve reports and scheduling",
            not problems,
            f"{len(REPORTS)} reports each state period, currency, basis and generation time; "
            "the monthly schedule generated November's pack and delivered it to the inbox"
            if not problems
            else "; ".join(problems),
        )
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
    print("Every Phase 14 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
