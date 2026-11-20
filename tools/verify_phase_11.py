#!/usr/bin/env python3
"""Verify each Phase 11 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_11.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase11-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.catalogue import CatalogueService, ProductDraft  # noqa: E402
from nickland.application.services.command_centre import CommandCentreService  # noqa: E402
from nickland.application.services.expenses import ExpenseDraft, ExpenseService  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.inventory import (  # noqa: E402
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.ledger_reports import LedgerReportService  # noqa: E402
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService  # noqa: E402
from nickland.application.services.parties import PartyDraft, PartyService  # noqa: E402
from nickland.application.services.pos import PosService  # noqa: E402
from nickland.application.services.quotations import (  # noqa: E402
    LineDraft,
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.receivables import (  # noqa: E402
    DepositDraft,
    InvoiceDraft,
    ReceiptDraft,
    ReceivablesService,
)
from nickland.application.services.reconciliation import ReconciliationService  # noqa: E402
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.taxation import TaxService  # noqa: E402
from nickland.application.services.treasury import (  # noqa: E402
    FinancialAccountDraft,
    MovementDraft,
    TreasuryService,
)
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.domain.errors import ConflictError  # noqa: E402
from nickland.domain.finance import MEASURES, FinancialAccountKind  # noqa: E402
from nickland.domain.money import money, present  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.immutability import register_append_only_guards  # noqa: E402
from nickland.persistence.ledger_models import Account  # noqa: E402
from nickland.persistence.models import Business, Warehouse  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
D = Decimal
START = date(2026, 1, 1)
END = date(2027, 12, 31)
Report = tuple[str, bool, str]


def build() -> tuple[Session, Actor]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-11')"))
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
    return session, actor


def order_for(session: Session, actor: Actor, customer_id, products):  # type: ignore[no-untyped-def]
    warehouse = session.scalar(select(Warehouse.id))
    quotations = QuotationService(session)
    quotation = quotations.create(
        actor,
        QuotationDraft(
            customer_id=customer_id,
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
                for product, quantity, cost in products
            ],
        ),
    )
    session.flush()
    quotations.issue(actor, quotation.id)
    orders = SalesOrderService(session)
    orders.record_acceptance(actor, quotation.id, AcceptanceDraft(reference="PO"))
    order = orders.convert(actor, quotation.id)
    session.flush()
    return order


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, actor = build()
    business_id = actor.business_id
    assert business_id is not None
    reports = LedgerReportService(session)
    treasury = TreasuryService(session)
    receivables = ReceivablesService(session)

    def balance(code: str) -> Decimal:
        account = session.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )
        assert account is not None
        return reports.account_balance(business_id, account.id, as_at=END)

    def revenue() -> Decimal:
        return reports.profit_and_loss(actor, start=START, end=END).total_revenue

    def expenses() -> Decimal:
        return reports.profit_and_loss(actor, start=START, end=END).total_expenses

    warehouse = session.scalar(select(Warehouse.id))
    unit = UnitService(session).units(actor)[0]
    catalogue = CatalogueService(session)
    gloves = catalogue.create_product(
        actor,
        ProductDraft(
            sku="GLV-100", name="Nitrile gloves", stock_unit_id=unit.id, list_price=D("95")
        ),
    )
    reagent = catalogue.create_product(
        actor, ProductDraft(sku="RGT-200", name="Glucose reagent", stock_unit_id=unit.id)
    )
    session.flush()
    InventoryService(session).open_stock(
        actor,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[
                StockLineDraft(product_id=gloves.id, quantity=D("50"), unit_cost=D("20")),
                StockLineDraft(product_id=reagent.id, quantity=D("20"), unit_cost=D("300")),
            ],
        ),
    )
    parties = PartyService(session)
    hospital = parties.create(
        actor,
        PartyDraft(
            name="Korle Bu Teaching Hospital",
            is_customer=True,
            payment_term_kind="net_days",
            payment_term_days=30,
        ),
    )
    clinic = parties.create(actor, PartyDraft(name="Ridge Clinic", is_customer=True))
    session.commit()

    # 1. A credit sale, then its collection.
    order = order_for(session, actor, hospital.id, [(gloves, "10", "20"), (reagent, "2", "300")])
    invoice = receivables.issue_invoice(
        actor, InvoiceDraft(sales_order_id=order.id, basis="ordered")
    )
    earned = revenue()
    owed = balance("11000")
    bank_before = balance("10200")
    receivables.receive(actor, ReceiptDraft(customer_id=hospital.id, amount=invoice.total))
    record(
        "A credit sale creates a receivable; collection does not earn it again",
        owed == invoice.total
        and earned == invoice.net_total
        and balance("11000") == D("0")
        and balance("10200") == money(bank_before + invoice.total)
        and revenue() == earned,
        f"invoice {invoice.number}: receivable {present(owed, 2)}, revenue {present(earned, 2)}; "
        f"after receipt: receivable 0.00, bank +{present(invoice.total, 2)}, "
        f"revenue still {present(revenue(), 2)}",
    )
    session.commit()

    # 2. A transfer between two business accounts.
    bank = treasury.default_for(business_id, FinancialAccountKind.BANK)
    ecobank = treasury.create_account(actor, FinancialAccountDraft(kind="bank", name="Ecobank Osu"))
    before = (revenue(), expenses())
    treasury.record(
        actor,
        MovementDraft(
            kind="transfer",
            amount=D("1000"),
            from_account_id=bank.id,
            to_account_id=ecobank.id,
            reason="Fund the supplier account",
            reference="TRF-0001",
        ),
    )
    record(
        "A transfer moves balances and affects neither income nor expense",
        balance(treasury.ledger_code(ecobank)) == D("1000") and (revenue(), expenses()) == before,
        f"main bank -1,000.00, Ecobank +1,000.00 ({treasury.ledger_code(ecobank)}); "
        "revenue and expenses unchanged",
    )
    session.commit()

    # 3. A deposit is not revenue before its condition is met.
    second = order_for(session, actor, clinic.id, [(gloves, "5", "20")])
    deposit = receivables.take_deposit(
        actor,
        DepositDraft(
            customer_id=clinic.id,
            amount=D("100"),
            sales_order_id=second.id,
            condition="on_delivery",
        ),
    )
    held_revenue = revenue()
    ahead = receivables.issue_invoice(
        actor, InvoiceDraft(sales_order_id=second.id, basis="ordered")
    )
    refused = False
    try:
        receivables.apply_deposit(actor, deposit.id, ahead.id)
    except ConflictError:
        refused = True
    for line in second.lines:
        line.quantity_delivered = line.quantity_ordered
    receivables.apply_deposit(actor, deposit.id, ahead.id)
    record(
        "A deposit is not earned revenue before its condition is met",
        held_revenue == earned
        and refused
        and deposit.state == "applied"
        and balance("22000") == D("0")
        and revenue() == money(earned + ahead.net_total),
        "deposit 100.00 held in 22000 with revenue unchanged; not applied to an invoice for "
        "undelivered goods; applied once delivered, revenue only from the invoice",
    )
    session.commit()

    # 5. Cash received is not revenue; cash paid is not expense.
    spend = ExpenseService(session)
    rent = spend.record(
        actor,
        ExpenseDraft(
            payee_name="Osu Properties Ltd",
            category_code="60000",
            description="Shop rent, November",
            net=D("1500"),
            settlement="on_credit",
        ),
    )
    expensed = expenses()
    spend.settle(actor, rent.id, financial_account_id=bank.id)
    advance = revenue()
    receivables.take_deposit(actor, DepositDraft(customer_id=hospital.id, amount=D("750")))
    record(
        "Cash received is never revenue, nor cash paid expense",
        expenses() == expensed and revenue() == advance,
        "rent recognised once (1,500.00) and paying it moved only cash and the payable; "
        "an advance of 750.00 moved cash and deposits, not revenue",
    )
    session.commit()

    # 4. Every measure drills to its lines and reconciles to the ledger.
    centre = CommandCentreService(session)
    pnl = reports.profit_and_loss(actor, start=START, end=END)
    values = {
        item.definition.code: item.value for item in centre.measures(actor, start=START, end=END)
    }
    drilled = all(
        centre.drill(actor, item.code, start=START, end=END).total == values[item.code]
        for item in MEASURES
    )
    cash_codes = [treasury.ledger_code(item.account) for item in treasury.accounts(actor)]
    record(
        "Every measure drills to its transactions and reconciles to the ledger",
        drilled
        and values["net_profit"] == pnl.net_result
        and values["cash_and_bank"] == money(sum((balance(code) for code in cash_codes), D("0")))
        and values["receivables"] == money(balance("11000") + balance("11010")),
        f"{len(MEASURES)} measures, each equal to the sum of its drill-down; net profit "
        f"{present(values['net_profit'], 2)} as the P&L; cash and bank "
        f"{present(values['cash_and_bank'], 2)} as the accounts",
    )
    session.commit()

    # 6. Every source of money reconciles.
    pos = PosService(session)
    shift = pos.open_shift(actor, opening_float=D("100"))
    sale = pos.current_sale(actor)
    pos.scan(actor, sale.id, "GLV-100")
    pos.tender(actor, sale.id, method="cash", cash_received=D("200"))
    pos.complete(actor, sale.id)
    expected = pos.drawer(shift).expected
    pieces: dict[str, int] = {}
    remaining = expected
    for note in ("200", "100", "50", "20", "10", "5", "2", "1", "0.50", "0.20", "0.10", "0.05"):
        count = int(remaining // D(note))
        if count:
            pieces[note] = count
            remaining = money(remaining - D(note) * count)
    pos.submit_count(
        actor, shift.id, denominations=pieces, variance_reason="Below the smallest coin"
    )
    pos.approve_shift(actor, shift.id)
    TaxService(session).record_payment(
        actor, reference="GRA-2026-10", paid_on=date(2026, 11, 5), amount=D("300")
    )
    statement_lines = ["Date,Description,Reference,Amount"]
    recon = ReconciliationService(session)
    for item in recon.unreconciled(actor, bank.id):
        statement_lines.append(
            f"{item.line.posting_date.isoformat()},{item.entry.narration[:40].replace(',', ' ')},"
            f"{item.entry.source_reference or ''},{item.amount}"
        )
    closing = reports.account_balance(business_id, bank.account_fk, as_at=END)
    statement = recon.import_statement(
        actor,
        bank.id,
        content="\n".join(statement_lines),
        closing_balance=closing,
        reference="GCB 11",
    )
    recon.auto_match(actor, statement.id)
    for line in statement.lines:
        if line.state == "unmatched":
            choices = recon.suggestions(actor, line.id)
            recon.match(actor, line.id, [choices[0].item.line.id])
    recon.complete(actor, statement.id)
    rows = recon.sources(actor, start=START, end=END)
    wanted = {
        "pos_shifts",
        "customer_receipts",
        "supplier_payments",
        "tax_payments",
        f"account:{bank.id}",
    }
    checked = [row for row in rows if row.source in wanted]
    record(
        "Shift, bank, receipt, supplier payment and tax payment reconciliation complete",
        len(checked) == len(wanted) and all(row.agrees for row in checked),
        "; ".join(
            f"{row.label}: {row.records} record(s), agrees"
            if row.agrees
            else f"{row.label}: differs by {row.difference}"
            for row in checked
        ),
    )
    session.commit()

    trial = reports.trial_balance(actor, as_at=END)
    record(
        "The trial balance agrees",
        trial.balanced,
        f"debits {present(trial.total_debit, 2)} equal credits {present(trial.total_credit, 2)}",
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
    print("Every Phase 11 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
