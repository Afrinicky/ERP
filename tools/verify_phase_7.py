#!/usr/bin/env python3
"""Verify each Phase 7 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_7.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase7-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.attachments import AttachmentService  # noqa: E402
from nickland.application.services.catalogue import (  # noqa: E402
    CatalogueService,
    ProductDraft,
)
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.ledger_reports import LedgerReportService  # noqa: E402
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService  # noqa: E402
from nickland.application.services.parties import PartyDraft, PartyService  # noqa: E402
from nickland.application.services.payables import PayablesService, PaymentDraft  # noqa: E402
from nickland.application.services.procurement import (  # noqa: E402
    ProcurementService,
    ReceiptDraft,
    ReceiptLineDraft,
)
from nickland.application.services.quotations import (  # noqa: E402
    LineDraft,
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.sourcing import (  # noqa: E402
    RequestDraft,
    RequestLineDraft,
    SourcingService,
    SupplierQuotationDraft,
    SupplierQuotationLineDraft,
)
from nickland.application.services.supplier_billing import (  # noqa: E402
    InvoiceDraft,
    InvoiceLineDraft,
    SupplierBillingService,
)
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.domain.errors import ConflictError, StateTransitionError  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.ledger_models import Account  # noqa: E402
from nickland.persistence.models import Business, Warehouse  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
D = Decimal
LATER = date(2027, 6, 30)
Report = tuple[str, bool, str]


def build() -> tuple[Session, Actor, dict[str, uuid.UUID]]:
    """A provisioned business with an issued, sourced quotation: one line split between
    stock and a supplier, one line wholly from another supplier."""
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-7')"))
    session = create_session_factory(engine)()
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
    warehouse_id = session.scalar(select(Warehouse.id))

    parties = PartyService(session)
    customer = parties.create(actor, PartyDraft(name="Banhart Hospital", is_customer=True))
    gamma = parties.create(actor, PartyDraft(name="Gamma Laboratories", is_supplier=True))
    beta = parties.create(actor, PartyDraft(name="Beta Medical", is_supplier=True))
    unit = UnitService(session).units(actor)[0]
    catalogue = CatalogueService(session)
    analyser = catalogue.create_product(
        actor, ProductDraft(sku="ANL-001", name="Haematology analyser", stock_unit_id=unit.id)
    )
    reagent = catalogue.create_product(
        actor, ProductDraft(sku="RGT-002", name="Reagent pack", stock_unit_id=unit.id)
    )
    session.flush()

    sourcing = SourcingService(session)
    request = sourcing.create_request(
        actor,
        RequestDraft(
            customer_id=customer.id,
            lines=[
                RequestLineDraft(
                    description="Haematology analyser", quantity=D("2"), product_id=analyser.id
                ),
                RequestLineDraft(
                    description="Reagent pack", quantity=D("40"), product_id=reagent.id
                ),
            ],
        ),
    )
    sourcing.set_request_state(actor, request.id, "open")
    lines = {line.description: line for line in request.lines}
    for supplier, prices in (
        (gamma, {"Reagent pack": "172"}),
        (beta, {"Haematology analyser": "9300"}),
    ):
        sourcing.record_supplier_quotation(
            actor,
            SupplierQuotationDraft(
                supplier_id=supplier.id,
                reference=f"{supplier.code}-Q1",
                request_id=request.id,
                lines=[
                    SupplierQuotationLineDraft(
                        description=name,
                        quantity_offered=lines[name].quantity,
                        unit_cost=D(price),
                        request_line_id=lines[name].id,
                        product_id=lines[name].product_fk,
                    )
                    for name, price in prices.items()
                ],
            ),
        )
    session.flush()
    offers = {
        item.description: {offer.supplier_name: offer for offer in item.offers}
        for item in sourcing.compare(actor, request.id)
    }
    quotations = QuotationService(session)
    quotation = quotations.create(
        actor,
        QuotationDraft(
            customer_id=customer.id,
            request_id=request.id,
            lines=[
                LineDraft(
                    description="Haematology analyser",
                    quantity=D("2"),
                    product_id=analyser.id,
                    sources=[
                        SourceDraft(
                            kind="supplier",
                            quantity=D("1"),
                            supplier_quotation_line_id=offers["Haematology analyser"][
                                "Beta Medical"
                            ].supplier_quotation_line_id,
                        ),
                        SourceDraft(
                            kind="stock",
                            quantity=D("1"),
                            unit_cost=D("8900"),
                            warehouse_id=warehouse_id,
                        ),
                    ],
                ),
                LineDraft(
                    description="Reagent pack",
                    quantity=D("40"),
                    product_id=reagent.id,
                    sources=[
                        SourceDraft(
                            kind="supplier",
                            quantity=D("40"),
                            supplier_quotation_line_id=offers["Reagent pack"][
                                "Gamma Laboratories"
                            ].supplier_quotation_line_id,
                        )
                    ],
                ),
            ],
        ),
    )
    quotations.issue(actor, quotation.id)
    session.commit()
    return session, actor, {"quotation": quotation.id, "gamma": gamma.id, "beta": beta.id}


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, actor, world = build()
    business_id = actor.business_id
    assert business_id is not None

    def balance(code: str) -> Decimal:
        account = session.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )
        assert account is not None
        return LedgerReportService(session).account_balance(business_id, account.id, as_at=LATER)

    orders = SalesOrderService(session)
    procurement = ProcurementService(session)
    billing = SupplierBillingService(session)
    payables = PayablesService(session)

    # -- 1. An accepted order links to its purchase orders and to its stock ------------

    refused = False
    try:
        orders.convert(actor, world["quotation"])
    except ConflictError:
        refused = True
    evidence = AttachmentService(session).store(
        actor,
        content=b"%PDF-1.4 customer purchase order",
        filename="BH-PO-2026-0441.pdf",
        media_type="application/pdf",
        owner_type="quotation",
        owner_id=world["quotation"],
        purpose="acceptance",
        immutable=True,
    )
    orders.record_acceptance(
        actor,
        world["quotation"],
        AcceptanceDraft(reference="BH-PO-2026-0441", evidence_attachment_id=evidence.id),
    )
    order = orders.convert(actor, world["quotation"], required_by=date(2026, 12, 15))
    session.commit()
    record(
        "Conversion needs the customer's acceptance on record, and carries every figure",
        refused and order.number.startswith("SO-") and len(order.lines) == 2,
        f"{order.number}: refused without evidence, then converted with "
        f"{evidence.original_filename}",
    )

    raised = []
    for draft in procurement.suggest_from_order(actor, order.id).values():
        purchase = procurement.raise_order(actor, draft)
        procurement.approve(actor, purchase.id)
        raised.append(procurement.issue(actor, purchase.id))
    session.commit()
    linked = orders.purchase_orders_for(actor, order.id)
    order = orders.require(actor, order.id)
    analyser = order.lines[0]
    record(
        "An accepted order links to its supplier purchase orders and to the stock it draws on",
        len(linked) == 2
        and {item.number for item in linked} == {item.number for item in raised}
        and analyser.quantity_received == D("1")
        and analyser.quantity_procured == D("2"),
        f"{order.number} served by {', '.join(sorted(item.number for item in linked))}; "
        "one analyser drawn from stock, one on order",
    )

    # -- 2. Variance between order, receipt and invoice is detected and approved -------

    gamma = next(item for item in raised if item.supplier_fk == world["gamma"])
    line = gamma.lines[0]
    procurement.receive(
        actor,
        ReceiptDraft(
            purchase_order_id=gamma.id,
            lines=[ReceiptLineDraft(purchase_order_line_id=line.id, quantity_received=D("30"))],
        ),
    )
    session.commit()
    short = billing.record(
        actor,
        InvoiceDraft(
            purchase_order_id=gamma.id,
            supplier_reference="GL-7731",
            invoiced_on=date(2026, 11, 20),
            lines=[
                InvoiceLineDraft(
                    purchase_order_line_id=line.id, quantity=D("40"), unit_cost=D("175")
                )
            ],
        ),
    )
    session.commit()
    kinds = sorted(item.kind for item in short.variances)
    held = False
    try:
        billing.post(actor, short.id)
    except StateTransitionError:
        held = True
    record(
        "Quantity and price variance between order, receipt and invoice is detected",
        kinds == ["price", "quantity"] and short.state == "variance" and held,
        f"invoiced 40 at 175 against 30 received at 172: {', '.join(kinds)} variances, "
        f"posting held",
    )

    procurement.receive(
        actor,
        ReceiptDraft(
            purchase_order_id=gamma.id,
            lines=[ReceiptLineDraft(purchase_order_line_id=line.id, quantity_received=D("10"))],
        ),
    )
    for variance in list(short.variances):
        billing.decide_variance(actor, variance.id, approve=True, reason="Agreed with Gamma")
    session.commit()
    at_stake = sum((item.amount_at_stake for item in short.variances), D("0"))
    record(
        "A variance requires approval, and the amount at stake is shown with it",
        short.state == "matched" and at_stake > D("0"),
        f"{len(short.variances)} approved, GH₵{at_stake} at stake",
    )

    # -- 3. A credit purchase creates a payable; payment reduces it without an expense --

    expense_before = balance("50000")
    posted = billing.post(actor, short.id)
    session.commit()
    payable_after_invoice = balance("20000")
    accrual_after_invoice = balance("20150")
    record(
        "A supplier credit purchase creates a payable through the ledger",
        payable_after_invoice == posted.base_total and accrual_after_invoice == D("0"),
        f"{posted.number}: trade payables {payable_after_invoice}, accrual cleared",
    )

    cash_before = balance("10200")
    payment = payables.pay(
        actor,
        PaymentDraft(supplier_id=world["gamma"], amount=D("5000"), method="bank"),
    )
    session.commit()
    record(
        "Payment reduces the payable and the cash without creating a second expense",
        balance("20000") == payable_after_invoice - D("5000")
        and balance("10200") == cash_before - D("5000")
        and balance("50000") == expense_before,
        f"{payment.number}: payables {balance('20000')}, bank {balance('10200')}, "
        f"cost of sales unchanged at {balance('50000')}",
    )

    register = payables.register(actor, as_at=date(2026, 12, 31))
    record(
        "The payables register reconciles to the control account and ages by due date",
        register.total_outstanding == balance("20000")
        and register.by_bucket["31_60"] == register.total_outstanding,
        f"outstanding {register.total_outstanding}, all in 31 to 60 days",
    )

    # -- 4. Cost variance per line and per order ---------------------------------------

    beta = next(item for item in raised if item.supplier_fk == world["beta"])
    beta_line = beta.lines[0]
    procurement.receive(
        actor,
        ReceiptDraft(
            purchase_order_id=beta.id,
            lines=[ReceiptLineDraft(purchase_order_line_id=beta_line.id, quantity_received=D("1"))],
        ),
    )
    beta_invoice = billing.record(
        actor,
        InvoiceDraft(
            purchase_order_id=beta.id,
            supplier_reference="BM-4410",
            invoiced_on=date(2026, 11, 22),
            lines=[
                InvoiceLineDraft(
                    purchase_order_line_id=beta_line.id, quantity=D("1"), unit_cost=D("9300")
                )
            ],
        ),
    )
    billing.post(actor, beta_invoice.id)
    session.commit()
    report = orders.cost_variance(actor, order.id)
    reagent = next(item for item in report.lines if item.description == "Reagent pack")
    record(
        "Cost variance between quoted and actual is reported per line and per order",
        report.lines_known == 2
        and report.variance_total is not None
        and reagent.actual_unit_cost == D("175.00")
        and reagent.total is not None,
        f"reagent {reagent.expected_unit_cost} → {reagent.actual_unit_cost} per unit; "
        f"order {report.expected_total} → {report.actual_total} ({report.variance_total})",
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
    print("Every Phase 7 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
