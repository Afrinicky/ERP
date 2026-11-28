"""Receivables against a real database: invoices, receipts, deposits, credit notes,
provisions, write-offs, ageing and collection. PRD §20.3, AC-18.

The exit criteria this file holds:

1. A credit sale creates a receivable; its later collection reduces the receivable and
   increases cash without recognising revenue a second time.
3. A customer deposit is not recognised as earned revenue before its condition is met.
5. Cash received is never equated with revenue.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.inventory import (
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.application.services.quotations import (
    LineDraft,
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.receivables import (
    CreditDraft,
    CreditLineDraft,
    DepositDraft,
    InvoiceDraft,
    InvoiceLineDraft,
    ReceiptDraft,
    ReceivablesService,
)
from nickland.application.services.units import UnitService
from nickland.domain.errors import ConflictError, PermissionDeniedError, ValidationError
from nickland.domain.finance import InvoiceState
from nickland.domain.money import money
from nickland.domain.orders import AgeingBucket
from nickland.persistence.catalogue_models import Party
from nickland.persistence.ledger_models import Account
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal
LATER = date(2027, 12, 31)


@pytest.fixture
def world(db: Session, clock: FixedClock, admin: Actor, provisioned: dict[str, uuid.UUID]) -> dict:
    """A converted order for gloves and reagent, to a customer on 30-day terms."""
    warehouse = provisioned["warehouse_id"]
    customer = PartyService(db, clock=clock).create(
        admin,
        PartyDraft(
            name="Korle Bu Teaching Hospital",
            is_customer=True,
            payment_term_kind="net_days",
            payment_term_days=30,
        ),
    )
    unit = UnitService(db, clock=clock).units(admin)[0]
    catalogue = CatalogueService(db, clock=clock)
    gloves = catalogue.create_product(
        admin, ProductDraft(sku="GLV-100", name="Nitrile gloves", stock_unit_id=unit.id)
    )
    reagent = catalogue.create_product(
        admin, ProductDraft(sku="RGT-200", name="Glucose reagent", stock_unit_id=unit.id)
    )
    db.flush()
    InventoryService(db, clock=clock).open_stock(
        admin,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[
                StockLineDraft(product_id=gloves.id, quantity=D("10"), unit_cost=D("20")),
                StockLineDraft(product_id=reagent.id, quantity=D("6"), unit_cost=D("300")),
            ],
        ),
    )

    def stock_line(product, quantity: str, cost: str) -> LineDraft:  # type: ignore[no-untyped-def]
        return LineDraft(
            description=product.name,
            quantity=D(quantity),
            product_id=product.id,
            sources=[
                SourceDraft(
                    kind="stock", quantity=D(quantity), unit_cost=D(cost), warehouse_id=warehouse
                )
            ],
        )

    quotations = QuotationService(db, clock=clock)
    quotation = quotations.create(
        admin,
        QuotationDraft(
            customer_id=customer.id,
            lines=[stock_line(gloves, "10", "20"), stock_line(reagent, "6", "300")],
        ),
    )
    db.flush()
    quotations.issue(admin, quotation.id)
    orders = SalesOrderService(db, clock=clock)
    orders.record_acceptance(admin, quotation.id, AcceptanceDraft(reference="KBTH-PO-118"))
    order = orders.convert(admin, quotation.id)
    db.flush()
    lines = {line.product_fk: line for line in order.lines}
    return {
        "order": order,
        "customer": customer,
        "gloves": lines[gloves.id],
        "reagent": lines[reagent.id],
    }


@pytest.fixture
def receivables(db: Session, clock: FixedClock) -> ReceivablesService:
    return ReceivablesService(db, clock=clock)


def balance(db: Session, clock: FixedClock, admin: Actor, code: str) -> Decimal:
    account = db.scalar(
        select(Account).where(Account.business_id == admin.business_id, Account.code == code)
    )
    assert account is not None
    return LedgerReportService(db, clock=clock).account_balance(
        admin.business_id,  # type: ignore[arg-type]
        account.id,
        as_at=LATER,
    )


def revenue(db: Session, clock: FixedClock, admin: Actor) -> Decimal:
    return (
        LedgerReportService(db, clock=clock)
        .profit_and_loss(admin, start=date(2026, 1, 1), end=LATER)
        .total_revenue
    )


def bill_ordered(receivables: ReceivablesService, admin: Actor, world: dict):  # type: ignore[no-untyped-def]
    return receivables.issue_invoice(
        admin, InvoiceDraft(sales_order_id=world["order"].id, basis="ordered")
    )


class TestInvoices:
    def test_a_credit_sale_creates_a_receivable_and_earns_its_revenue_once(
        self,
        db: Session,
        clock: FixedClock,
        receivables: ReceivablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        invoice = bill_ordered(receivables, admin, world)
        expected_net = money(
            world["gloves"].unit_price * D("10") + world["reagent"].unit_price * D("6")
        )
        assert invoice.net_total == money(expected_net.quantize(D("0.01")))
        assert invoice.tax_total > D("0")
        assert invoice.due_on == invoice.issued_on + timedelta(days=30)
        assert balance(db, clock, admin, "11000") == invoice.total
        earned = revenue(db, clock, admin)
        assert earned == invoice.net_total
        assert world["gloves"].quantity_invoiced == D("10")

        receipt = receivables.receive(
            admin, ReceiptDraft(customer_id=world["customer"].id, amount=invoice.total)
        )
        assert receipt.allocated == invoice.total and invoice.state == InvoiceState.SETTLED
        assert balance(db, clock, admin, "11000") == D("0")
        assert balance(db, clock, admin, "10200") == invoice.total
        assert revenue(db, clock, admin) == earned  # cash received is not revenue

    def test_only_what_was_delivered_is_billed_by_default(
        self, receivables: ReceivablesService, admin: Actor, world: dict
    ) -> None:
        with pytest.raises(ConflictError, match="delivered"):
            receivables.issue_invoice(admin, InvoiceDraft(sales_order_id=world["order"].id))
        world["gloves"].quantity_delivered = D("4")
        invoice = receivables.issue_invoice(admin, InvoiceDraft(sales_order_id=world["order"].id))
        assert [line.quantity for line in invoice.lines] == [D("4")]
        with pytest.raises(ConflictError):
            receivables.issue_invoice(
                admin,
                InvoiceDraft(
                    sales_order_id=world["order"].id,
                    lines=[InvoiceLineDraft(world["gloves"].id, D("1"))],
                ),
            )

    def test_the_credit_limit_needs_approval_and_a_reason(
        self, db: Session, receivables: ReceivablesService, admin: Actor, world: dict
    ) -> None:
        customer = db.get(Party, world["customer"].id)
        assert customer is not None
        customer.credit_limit = D("100")
        clerk = make_actor(admin.business_id, permissions={"sales_invoice.issue"})
        with pytest.raises(ConflictError, match="credit limit"):
            receivables.issue_invoice(
                clerk,
                InvoiceDraft(
                    sales_order_id=world["order"].id, basis="ordered", credit_override_reason="x"
                ),
            )
        invoice = receivables.issue_invoice(
            admin,
            InvoiceDraft(
                sales_order_id=world["order"].id,
                basis="ordered",
                credit_override_reason="Hospital PO confirmed by the director",
            ),
        )
        assert invoice.credit_override_reason

    def test_issuing_needs_its_permission(
        self, receivables: ReceivablesService, admin: Actor, world: dict
    ) -> None:
        viewer = make_actor(admin.business_id, permissions={"receivable.view"})
        with pytest.raises(PermissionDeniedError):
            receivables.issue_invoice(
                viewer, InvoiceDraft(sales_order_id=world["order"].id, basis="ordered")
            )


class TestReceipts:
    def test_a_part_payment_leaves_the_balance_owed(
        self, receivables: ReceivablesService, admin: Actor, world: dict
    ) -> None:
        invoice = bill_ordered(receivables, admin, world)
        receivables.receive(admin, ReceiptDraft(customer_id=world["customer"].id, amount=D("100")))
        assert invoice.state == InvoiceState.PART_SETTLED
        assert money(invoice.outstanding) == money(invoice.total - D("100"))

    def test_an_overpayment_stays_as_credit_and_can_be_refunded(
        self,
        db: Session,
        clock: FixedClock,
        receivables: ReceivablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        invoice = bill_ordered(receivables, admin, world)
        receipt = receivables.receive(
            admin,
            ReceiptDraft(customer_id=world["customer"].id, amount=money(invoice.total + D("50"))),
        )
        assert receipt.unapplied == D("50")
        assert receivables.customer_balance(admin, world["customer"].id) == D("-50")
        receivables.refund_credit(admin, receipt.id, amount=D("50"), reason="Overpaid")
        assert receivables.customer_balance(admin, world["customer"].id) == D("0")

    def test_a_receipt_allocated_later(
        self, receivables: ReceivablesService, admin: Actor, world: dict
    ) -> None:
        receipt = receivables.receive(
            admin, ReceiptDraft(customer_id=world["customer"].id, amount=D("200"))
        )
        assert receipt.unapplied == D("200")
        invoice = bill_ordered(receivables, admin, world)
        receivables.allocate(admin, receipt.id, invoice.id, D("200"))
        assert receipt.unapplied == D("0") and invoice.amount_received == D("200")


class TestDeposits:
    def test_a_deposit_is_a_liability_not_revenue_until_its_condition_is_met(
        self,
        db: Session,
        clock: FixedClock,
        receivables: ReceivablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        deposit = receivables.take_deposit(
            admin,
            DepositDraft(
                customer_id=world["customer"].id,
                amount=D("500"),
                sales_order_id=world["order"].id,
                condition="on_delivery",
            ),
        )
        assert balance(db, clock, admin, "22000") == D("500")
        assert revenue(db, clock, admin) == D("0")

        invoice = bill_ordered(receivables, admin, world)  # billed ahead of delivery
        assert deposit.applied == D("0")
        with pytest.raises(ConflictError, match="delivered"):
            receivables.apply_deposit(admin, deposit.id, invoice.id)

        world["gloves"].quantity_delivered = D("10")
        world["reagent"].quantity_delivered = D("6")
        receivables.apply_deposit(admin, deposit.id, invoice.id)
        assert deposit.state == "applied"
        assert balance(db, clock, admin, "22000") == D("0")
        assert balance(db, clock, admin, "11000") == money(invoice.total - D("500"))
        assert revenue(db, clock, admin) == invoice.net_total

    def test_a_deposit_on_invoice_is_applied_when_the_invoice_is_issued(
        self, receivables: ReceivablesService, admin: Actor, world: dict
    ) -> None:
        deposit = receivables.take_deposit(
            admin,
            DepositDraft(
                customer_id=world["customer"].id, amount=D("300"), sales_order_id=world["order"].id
            ),
        )
        invoice = bill_ordered(receivables, admin, world)
        assert deposit.applied == D("300") and invoice.amount_received == D("300")

    def test_a_held_deposit_can_be_refunded(
        self,
        db: Session,
        clock: FixedClock,
        receivables: ReceivablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        deposit = receivables.take_deposit(
            admin, DepositDraft(customer_id=world["customer"].id, amount=D("120"))
        )
        receivables.refund_deposit(admin, deposit.id, amount=D("120"), reason="Order withdrawn")
        assert deposit.state == "refunded"
        assert balance(db, clock, admin, "22000") == D("0")


class TestCreditsAndWriteOffs:
    def test_a_credit_note_reverses_its_share_of_revenue_and_tax(
        self,
        db: Session,
        clock: FixedClock,
        receivables: ReceivablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        invoice = bill_ordered(receivables, admin, world)
        gloves = next(line for line in invoice.lines if line.description == "Nitrile gloves")
        note = receivables.credit(
            admin,
            CreditDraft(
                invoice_id=invoice.id,
                lines=[CreditLineDraft(gloves.id, D("10"))],
                reason="Wrong size supplied",
            ),
        )
        assert note.net == gloves.net and note.tax == gloves.tax
        assert balance(db, clock, admin, "11000") == money(invoice.total - gloves.total)
        assert balance(db, clock, admin, "40950") == -gloves.net  # a contra-revenue debit
        with pytest.raises(ConflictError, match="remains to credit"):
            receivables.credit(
                admin,
                CreditDraft(
                    invoice_id=invoice.id, lines=[CreditLineDraft(gloves.id, D("1"))], reason="x"
                ),
            )

    def test_a_provision_is_used_first_on_write_off(
        self,
        db: Session,
        clock: FixedClock,
        receivables: ReceivablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        invoice = bill_ordered(receivables, admin, world)
        receivables.provide(admin, invoice.id, amount=D("1000"), reason="Ninety days overdue")
        assert balance(db, clock, admin, "11010") == D("-1000")
        receivables.write_off(admin, invoice.id, reason="Customer insolvent")
        assert invoice.state == InvoiceState.WRITTEN_OFF
        assert balance(db, clock, admin, "11000") == D("0")
        assert balance(db, clock, admin, "11010") == D("0")
        assert balance(db, clock, admin, "72000") == invoice.total

    def test_writing_off_needs_its_permission(
        self, receivables: ReceivablesService, admin: Actor, world: dict
    ) -> None:
        invoice = bill_ordered(receivables, admin, world)
        clerk = make_actor(admin.business_id, permissions={"receivable.view", "receipt.manage"})
        with pytest.raises(PermissionDeniedError):
            receivables.write_off(clerk, invoice.id, reason="x")


class TestCollection:
    def test_ageing_worklist_and_statement(
        self,
        clock: FixedClock,
        receivables: ReceivablesService,
        admin: Actor,
        world: dict,
    ) -> None:
        invoice = bill_ordered(receivables, admin, world)
        later = invoice.due_on + timedelta(days=45)
        rows = receivables.ageing(admin, as_at=later)
        assert rows[0].buckets[AgeingBucket.DAYS_31_60] == invoice.total

        receivables.note(
            admin,
            world["customer"].id,
            kind="promise",
            note="Accounts officer promised payment",
            invoice_id=invoice.id,
            promised_on=later + timedelta(days=5),
        )
        item = receivables.worklist(admin, as_at=later)[0]
        assert item.days_overdue == 45 and not item.reminder_due
        receivables.note(
            admin,
            world["customer"].id,
            kind="dispute",
            note="Short delivery",
            invoice_id=invoice.id,
        )
        assert invoice.disputed
        assert not receivables.worklist(admin, as_at=later + timedelta(days=10))[0].reminder_due
        with pytest.raises(ValidationError):
            receivables.note(admin, world["customer"].id, kind="promise", note="Soon")

        receivables.receive(admin, ReceiptDraft(customer_id=world["customer"].id, amount=D("100")))
        statement = receivables.statement(
            admin, world["customer"].id, start=date(2026, 1, 1), end=LATER
        )
        assert statement.opening == D("0")
        assert statement.entries[0].debit == invoice.total
        assert statement.closing == money(invoice.total - D("100"))
