"""Receivables: invoices, receipts, deposits, credit notes, provisions and collections.
PRD §20.3, §29.3.

Revenue is earned when an invoice is issued, and only then. A receipt settles what an
invoice made owing and recognises nothing; a deposit is owed back to the customer until its
condition is met, and meeting it moves the deposit against the invoice, still recognising
nothing. The customer's balance is therefore the ledger's trade receivables subledger, and
every figure here reconciles to it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.tax_postings import (
    SaleLine,
    by_rule,
    tax_entry_lines,
    tax_sale,
)
from nickland.application.services.treasury import TreasuryService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.finance import (
    BAD_DEBT_EXPENSE,
    CUSTOMER_DEPOSITS,
    DOUBTFUL_DEBTS_ALLOWANCE,
    QUOTATION_REVENUE,
    SALES_RETURNS,
    TRADE_RECEIVABLES,
    CollectionNoteKind,
    CreditPosition,
    DepositCondition,
    DepositState,
    FinancialAccountKind,
    InvoiceOrigin,
    InvoiceState,
    deposit_condition_met,
    invoice_state,
)
from nickland.domain.ledger import EntryLine, JournalSource, Side, SubledgerKind
from nickland.domain.money import ZERO, allocate, money, multiply, present
from nickland.domain.orders import AgeingBucket, ageing_bucket
from nickland.domain.parties import PaymentTermKind, PaymentTerms
from nickland.persistence.catalogue_models import Party
from nickland.persistence.finance_models import (
    CollectionNote,
    CustomerCreditNote,
    CustomerDeposit,
    CustomerReceipt,
    DebtProvision,
    DepositApplication,
    FinancialAccount,
    ReceiptAllocation,
    SalesInvoice,
    SalesInvoiceLine,
)
from nickland.persistence.ledger_models import Account, JournalEntry, JournalLine
from nickland.persistence.models import Business
from nickland.persistence.order_models import SalesOrder, SalesOrderLine
from nickland.persistence.pos_models import PosSale
from nickland.platform.clock import Clock, SystemClock, business_date
from nickland.platform.ids import new_id

# -- drafts ---------------------------------------------------------------------------------


@dataclass(slots=True)
class InvoiceLineDraft:
    sales_order_line_id: uuid.UUID
    quantity: Decimal


@dataclass(slots=True)
class InvoiceDraft:
    sales_order_id: uuid.UUID
    lines: list[InvoiceLineDraft] = field(default_factory=list)
    """Empty: everything delivered and not yet invoiced."""
    basis: str = "delivered"
    """``delivered`` bills what has been signed for; ``ordered`` bills ahead of delivery."""
    issued_on: date | None = None
    customer_reference: str | None = None
    credit_override_reason: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class ReceiptDraft:
    customer_id: uuid.UUID
    amount: Decimal
    financial_account_id: uuid.UUID | None = None
    received_on: date | None = None
    reference: str | None = None
    allocations: list[tuple[uuid.UUID, Decimal]] | None = None
    """Which invoices it settles and by how much. None: the oldest first."""
    notes: str | None = None


@dataclass(slots=True)
class DepositDraft:
    customer_id: uuid.UUID
    amount: Decimal
    financial_account_id: uuid.UUID | None = None
    sales_order_id: uuid.UUID | None = None
    condition: DepositCondition | str = DepositCondition.ON_INVOICE
    received_on: date | None = None
    reference: str | None = None


@dataclass(slots=True)
class CreditLineDraft:
    invoice_line_id: uuid.UUID
    quantity: Decimal


@dataclass(slots=True)
class CreditDraft:
    invoice_id: uuid.UUID
    lines: list[CreditLineDraft]
    reason: str


# -- views ----------------------------------------------------------------------------------


@dataclass(slots=True)
class InvoiceableLine:
    line: SalesOrderLine
    ordered: Decimal
    delivered: Decimal
    invoiced: Decimal
    invoiceable: Decimal


@dataclass(slots=True)
class AgeingRow:
    customer_id: uuid.UUID
    customer: str
    buckets: dict[AgeingBucket, Decimal]
    total: Decimal
    unapplied: Decimal
    """Receipts and deposits not yet set against an invoice."""
    credit_limit: Decimal | None
    on_hold: bool


@dataclass(slots=True)
class WorklistItem:
    invoice: SalesInvoice
    customer: str
    days_overdue: int
    outstanding: Decimal
    latest: CollectionNote | None
    promised_on: date | None
    reminder_due: bool


@dataclass(slots=True)
class StatementEntry:
    posted_on: date
    reference: str | None
    narration: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


@dataclass(slots=True)
class CustomerStatement:
    customer: Party
    starts_on: date
    ends_on: date
    opening: Decimal
    entries: list[StatementEntry]
    closing: Decimal
    ageing: dict[AgeingBucket, Decimal]


class ReceivablesService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._treasury = TreasuryService(session, clock=self._clock)

    # -- invoices -------------------------------------------------------------------------

    def invoiceable(self, actor: AnyActor, order_id: uuid.UUID) -> list[InvoiceableLine]:
        """What each line of an order may still be billed for."""
        actor.require_any("sales_invoice.issue", "receivable.view")
        order = self._order(actor, order_id)
        rows = []
        for line in order.lines:
            ordered = money(line.quantity_ordered - line.quantity_cancelled)
            rows.append(
                InvoiceableLine(
                    line=line,
                    ordered=ordered,
                    delivered=line.quantity_delivered,
                    invoiced=line.quantity_invoiced,
                    invoiceable=money(max(ZERO, line.quantity_delivered - line.quantity_invoiced)),
                )
            )
        return rows

    def issue_invoice(self, actor: AnyActor, draft: InvoiceDraft) -> SalesInvoice:
        """Bill a sales order: Dr trade receivables, Cr revenue and output tax."""
        actor.require("sales_invoice.issue")
        business_id = _business_of(actor)
        order = self._order(actor, draft.sales_order_id)
        if order.state == "cancelled":
            raise ConflictError(f"Order {order.number} is cancelled.")
        if draft.basis not in ("delivered", "ordered"):
            raise ValidationError("Bill what was delivered, or what was ordered.", field="basis")
        lines_by_id = {line.id: line for line in order.lines}
        wanted: list[tuple[SalesOrderLine, Decimal]] = []
        requested = draft.lines or [
            InvoiceLineDraft(sales_order_line_id=line.id, quantity=self._ceiling(line, draft.basis))
            for line in order.lines
        ]
        for item in requested:
            line = lines_by_id.get(item.sales_order_line_id)
            if line is None:
                raise NotFoundError("That line is not on this order.")
            quantity = money(item.quantity)
            if quantity <= ZERO:
                continue
            ceiling = self._ceiling(line, draft.basis)
            if quantity > ceiling:
                raise ConflictError(
                    f"{line.description}: {ceiling.normalize():f} remains to be billed "
                    + ("on what was delivered." if draft.basis == "delivered" else "on the order.")
                )
            wanted.append((line, quantity))
        if not wanted:
            raise ConflictError(
                "Nothing on this order remains to be billed"
                + (" on what has been delivered." if draft.basis == "delivered" else ".")
            )

        business = self._db.get(Business, business_id)
        precision = business.currency_precision if business else 2
        issued_on = draft.issued_on or self._today(business_id)
        customer = self._customer(actor, order.customer_fk)
        invoice = SalesInvoice(
            id=new_id(),
            business_id=business_id,
            branch_id=actor.branch_id,
            origin=InvoiceOrigin.SALES_ORDER.value,
            customer_fk=customer.id,
            sales_order_fk=order.id,
            issued_on=issued_on,
            due_on=self._due(customer, issued_on),
            currency_code=order.currency_code,
            customer_reference=_clean(draft.customer_reference) or order.customer_reference,
            notes=_clean(draft.notes),
            issued_by_id=actor.user_id,
        )
        for position, (line, quantity) in enumerate(wanted, start=1):
            net = present(multiply(line.unit_price, quantity), precision)
            invoice.lines.append(
                SalesInvoiceLine(
                    id=new_id(),
                    business_id=business_id,
                    position=position,
                    sales_order_line_fk=line.id,
                    product_fk=line.product_fk,
                    description=line.description,
                    quantity=quantity,
                    unit_price=line.unit_price,
                    net=net,
                    total=net,
                )
            )
        # Priced and taxed before anything is kept, so an invoice refused for credit
        # leaves nothing behind.
        taxed = [
            SaleLine(str(item.id), item.net, item.quantity, item.product_fk)
            for item in invoice.lines
        ]
        taxes = tax_sale(
            self._db,
            self._clock,
            business_id,
            taxed,
            customer_id=customer.id,
            transaction_kind="sales_invoice",
            on=issued_on,
            currency_code=invoice.currency_code,
            precision=precision,
        )
        for billed in invoice.lines:
            charged = taxes.get(str(billed.id))
            billed.tax = charged.total if charged else ZERO
            billed.tax_breakdown = charged.breakdown if charged and charged.breakdown else None
            billed.total = money(billed.net + billed.tax)
        invoice.net_total = money(sum((item.net for item in invoice.lines), ZERO))
        invoice.tax_total = money(sum((item.tax for item in invoice.lines), ZERO))
        invoice.total = money(invoice.net_total + invoice.tax_total)

        refusal = self.credit_position(customer).refuses(invoice.total)
        if refusal is not None:
            reason = _clean(draft.credit_override_reason)
            if reason is None or not actor.has("payment.approve"):
                raise ConflictError(
                    f"{refusal} An invoice beyond the limit needs approval: someone holding "
                    "payment approval, with a reason."
                )
            invoice.credit_override_reason = reason

        invoice.number = (
            OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "sales_invoice", branch_id=actor.branch_id, on=issued_on)
            .number
        )
        self._db.add(invoice)
        self._db.flush()
        tax_sale(
            self._db,
            self._clock,
            business_id,
            taxed,
            customer_id=customer.id,
            transaction_kind="sales_invoice",
            on=issued_on,
            currency_code=invoice.currency_code,
            precision=precision,
            record_as=("sales_invoice", invoice.id),
        )
        lines = [
            EntryLine(
                TRADE_RECEIVABLES,
                Side.DEBIT,
                invoice.total,
                f"Invoice {invoice.number}",
                party_id=customer.id,
                subledger=SubledgerKind.CUSTOMER,
            ),
            EntryLine(QUOTATION_REVENUE, Side.CREDIT, invoice.net_total, f"Sales, {order.number}"),
            *tax_entry_lines(
                self._db,
                business_id,
                by_rule([item.tax_breakdown for item in invoice.lines]),
                Side.CREDIT,
            ),
        ]
        invoice.journal_entry_fk = self._post(
            actor,
            narration=f"Invoice {invoice.number} to {customer.name}",
            lines=lines,
            on=issued_on,
            source=JournalSource.SALES,
            reference=invoice.number,
            source_id=invoice.id,
            key=f"sales_invoice:{invoice.id}",
        )
        for line, quantity in wanted:
            line.quantity_invoiced = money(line.quantity_invoiced + quantity)
        self._db.flush()
        self._apply_held_deposits(actor, invoice)
        self._audit.record(
            actor,
            "sales_invoice_issued",
            entity_type="sales_invoice",
            entity_id=invoice.id,
            entity_label=invoice.number,
            reason=invoice.credit_override_reason,
            after={"order": order.number, "total": str(invoice.total), "lines": len(wanted)},
        )
        return invoice

    def record_pos_sale(self, sale: PosSale, amount: Decimal) -> SalesInvoice:
        """A register sale on account, so it ages and is collected like any invoice. The
        receivable was posted with the sale; nothing is posted here."""
        assert sale.customer_fk is not None and sale.number is not None
        customer = self._db.get(Party, sale.customer_fk)
        assert customer is not None
        issued_on = sale.completed_on or self._today(sale.business_id)
        invoice = SalesInvoice(
            business_id=sale.business_id,
            branch_id=sale.branch_id,
            number=sale.number,
            origin=InvoiceOrigin.POS_SALE.value,
            customer_fk=customer.id,
            pos_sale_fk=sale.id,
            issued_on=issued_on,
            due_on=self._due(customer, issued_on),
            currency_code=sale.currency_code,
            net_total=amount,
            tax_total=ZERO,
            total=amount,
            journal_entry_fk=sale.journal_entry_fk,
            issued_by_id=sale.cashier_id,
            notes="Register sale on account; tax is accounted for on the sale.",
        )
        invoice.lines.append(
            SalesInvoiceLine(
                business_id=sale.business_id,
                position=1,
                description=f"Register sale {sale.number}, on account",
                quantity=Decimal("1"),
                unit_price=amount,
                net=amount,
                total=amount,
            )
        )
        self._db.add(invoice)
        self._db.flush()
        return invoice

    def require_invoice(self, actor: AnyActor, invoice_id: uuid.UUID) -> SalesInvoice:
        actor.require_any("receivable.view", "sales_invoice.issue", "receipt.manage")
        invoice = self._db.get(SalesInvoice, invoice_id)
        if invoice is None or invoice.business_id != actor.business_id:
            raise NotFoundError("Invoice not found.")
        return invoice

    def invoices(
        self,
        actor: AnyActor,
        *,
        customer_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        open_only: bool = False,
        limit: int = 200,
    ) -> list[SalesInvoice]:
        actor.require("receivable.view")
        query = select(SalesInvoice).where(SalesInvoice.business_id == actor.business_id)
        if customer_id:
            query = query.where(SalesInvoice.customer_fk == customer_id)
        if order_id:
            query = query.where(SalesInvoice.sales_order_fk == order_id)
        if open_only:
            query = query.where(
                SalesInvoice.state.in_((InvoiceState.OPEN.value, InvoiceState.PART_SETTLED.value))
            )
        return list(
            self._db.scalars(
                query.order_by(SalesInvoice.issued_on.desc(), SalesInvoice.number.desc()).limit(
                    max(min(limit, 1000), 1)
                )
            ).all()
        )

    # -- receipts -------------------------------------------------------------------------

    def receive(self, actor: AnyActor, draft: ReceiptDraft) -> CustomerReceipt:
        """Money from a customer: Dr the account it entered, Cr trade receivables. Allocated
        to invoices; what is not allocated stays as the customer's credit."""
        actor.require("receipt.manage")
        business_id = _business_of(actor)
        customer = self._customer(actor, draft.customer_id)
        amount = money(draft.amount)
        if amount <= ZERO:
            raise ValidationError("A receipt carries an amount above zero.", field="amount")
        account = self._account(actor, draft.financial_account_id)
        received_on = draft.received_on or self._today(business_id)
        receipt = CustomerReceipt(
            business_id=business_id,
            branch_id=actor.branch_id,
            number=OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "receipt", branch_id=actor.branch_id, on=received_on)
            .number,
            customer_fk=customer.id,
            financial_account_fk=account.id,
            received_on=received_on,
            amount=amount,
            reference=_clean(draft.reference),
            notes=_clean(draft.notes),
            recorded_by_id=actor.user_id,
        )
        self._db.add(receipt)
        self._db.flush()
        receipt.journal_entry_fk = self._post(
            actor,
            narration=f"Receipt {receipt.number} from {customer.name}",
            lines=[
                EntryLine(
                    self._treasury.ledger_code(account),
                    Side.DEBIT,
                    amount,
                    receipt.reference or f"Receipt {receipt.number}",
                ),
                EntryLine(
                    TRADE_RECEIVABLES,
                    Side.CREDIT,
                    amount,
                    f"Receipt {receipt.number}",
                    party_id=customer.id,
                    subledger=SubledgerKind.CUSTOMER,
                ),
            ],
            on=received_on,
            source=JournalSource.RECEIPT,
            reference=receipt.number,
            source_id=receipt.id,
            key=f"customer_receipt:{receipt.id}",
        )
        if draft.allocations is None:
            open_invoices = self._open_invoices(customer.id)
            spread = amount
            for invoice in open_invoices:
                if spread <= ZERO:
                    break
                portion = min(spread, money(invoice.outstanding))
                if portion > ZERO:
                    self._allocate(actor, receipt, invoice, portion, received_on)
                    spread = money(spread - portion)
        else:
            for invoice_id, portion in draft.allocations:
                invoice = self.require_invoice(actor, invoice_id)
                self._allocate(actor, receipt, invoice, money(portion), received_on)
        self._db.flush()
        self._audit.record(
            actor,
            "customer_receipt_recorded",
            entity_type="customer_receipt",
            entity_id=receipt.id,
            entity_label=receipt.number,
            after={
                "customer": customer.name,
                "amount": str(amount),
                "allocated": str(receipt.allocated),
                "account": account.name,
            },
        )
        return receipt

    def allocate(
        self, actor: AnyActor, receipt_id: uuid.UUID, invoice_id: uuid.UUID, amount: Decimal
    ) -> CustomerReceipt:
        """Set a receipt's unapplied credit against an invoice. Posts nothing: both sides
        are the same customer's trade receivable."""
        actor.require("receipt.manage")
        receipt = self._receipt(actor, receipt_id)
        invoice = self.require_invoice(actor, invoice_id)
        self._allocate(actor, receipt, invoice, money(amount), self._today(receipt.business_id))
        self._db.flush()
        return receipt

    def refund_credit(
        self,
        actor: AnyActor,
        receipt_id: uuid.UUID,
        *,
        amount: Decimal,
        reason: str,
        financial_account_id: uuid.UUID | None = None,
    ) -> CustomerReceipt:
        """Pay back what a customer overpaid: Dr trade receivables, Cr the account."""
        actor.require("receipt.manage")
        receipt = self._receipt(actor, receipt_id)
        value = money(amount)
        if value <= ZERO or value > money(receipt.unapplied):
            raise ValidationError(
                f"Up to {money(receipt.unapplied)} of this receipt is unapplied.", field="amount"
            )
        if not reason.strip():
            raise ValidationError("A refund records why.", field="reason")
        account = self._account(actor, financial_account_id or receipt.financial_account_fk)
        customer = self._customer(actor, receipt.customer_fk)
        receipt.refunded = money(receipt.refunded + value)
        self._post(
            actor,
            narration=f"Refund of overpayment on {receipt.number}: {reason.strip()}",
            lines=[
                EntryLine(
                    TRADE_RECEIVABLES,
                    Side.DEBIT,
                    value,
                    f"Refund on {receipt.number}",
                    party_id=customer.id,
                    subledger=SubledgerKind.CUSTOMER,
                ),
                EntryLine(self._treasury.ledger_code(account), Side.CREDIT, value, reason.strip()),
            ],
            on=self._today(receipt.business_id),
            source=JournalSource.RECEIPT,
            reference=receipt.number,
            source_id=receipt.id,
            key=f"receipt_refund:{receipt.id}:{receipt.refunded}",
        )
        self._db.flush()
        return receipt

    def receipts(
        self, actor: AnyActor, *, customer_id: uuid.UUID | None = None, limit: int = 200
    ) -> list[CustomerReceipt]:
        actor.require_any("receivable.view", "receipt.manage")
        query = select(CustomerReceipt).where(CustomerReceipt.business_id == actor.business_id)
        if customer_id:
            query = query.where(CustomerReceipt.customer_fk == customer_id)
        return list(
            self._db.scalars(
                query.order_by(
                    CustomerReceipt.received_on.desc(), CustomerReceipt.number.desc()
                ).limit(max(min(limit, 1000), 1))
            ).all()
        )

    # -- deposits -------------------------------------------------------------------------

    def take_deposit(self, actor: AnyActor, draft: DepositDraft) -> CustomerDeposit:
        """An advance: Dr the account, Cr customer deposits. Not revenue, not a settlement."""
        actor.require("receipt.manage")
        business_id = _business_of(actor)
        customer = self._customer(actor, draft.customer_id)
        amount = money(draft.amount)
        if amount <= ZERO:
            raise ValidationError("A deposit carries an amount above zero.", field="amount")
        order = self._order(actor, draft.sales_order_id) if draft.sales_order_id else None
        if order is not None and order.customer_fk != customer.id:
            raise ConflictError("That order belongs to another customer.")
        account = self._account(actor, draft.financial_account_id)
        received_on = draft.received_on or self._today(business_id)
        deposit = CustomerDeposit(
            business_id=business_id,
            branch_id=actor.branch_id,
            number=OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "receipt", branch_id=actor.branch_id, on=received_on)
            .number,
            customer_fk=customer.id,
            sales_order_fk=order.id if order else None,
            financial_account_fk=account.id,
            received_on=received_on,
            amount=amount,
            condition=DepositCondition(draft.condition).value,
            state=DepositState.HELD.value,
            reference=_clean(draft.reference),
            recorded_by_id=actor.user_id,
        )
        self._db.add(deposit)
        self._db.flush()
        deposit.journal_entry_fk = self._post(
            actor,
            narration=f"Deposit {deposit.number} from {customer.name}"
            + (f" against {order.number}" if order else ""),
            lines=[
                EntryLine(
                    self._treasury.ledger_code(account),
                    Side.DEBIT,
                    amount,
                    deposit.reference or f"Deposit {deposit.number}",
                ),
                EntryLine(
                    CUSTOMER_DEPOSITS,
                    Side.CREDIT,
                    amount,
                    f"Deposit {deposit.number}",
                    party_id=customer.id,
                    subledger=SubledgerKind.CUSTOMER,
                ),
            ],
            on=received_on,
            source=JournalSource.RECEIPT,
            reference=deposit.number,
            source_id=deposit.id,
            key=f"customer_deposit:{deposit.id}",
        )
        self._db.flush()
        self._audit.record(
            actor,
            "customer_deposit_recorded",
            entity_type="customer_deposit",
            entity_id=deposit.id,
            entity_label=deposit.number,
            after={"amount": str(amount), "condition": deposit.condition},
        )
        return deposit

    def apply_deposit(
        self,
        actor: AnyActor,
        deposit_id: uuid.UUID,
        invoice_id: uuid.UUID,
        *,
        amount: Decimal | None = None,
    ) -> DepositApplication:
        """Set a deposit against an invoice, once its condition is met."""
        actor.require("receipt.manage")
        deposit = self._deposit(actor, deposit_id)
        invoice = self.require_invoice(actor, invoice_id)
        return self._apply(actor, deposit, invoice, amount)

    def refund_deposit(
        self,
        actor: AnyActor,
        deposit_id: uuid.UUID,
        *,
        amount: Decimal,
        reason: str,
        financial_account_id: uuid.UUID | None = None,
    ) -> CustomerDeposit:
        actor.require("receipt.manage")
        deposit = self._deposit(actor, deposit_id)
        value = money(amount)
        if value <= ZERO or value > money(deposit.held):
            raise ValidationError(f"{money(deposit.held)} of this deposit is held.", field="amount")
        if not reason.strip():
            raise ValidationError("A refund records why.", field="reason")
        account = self._account(actor, financial_account_id or deposit.financial_account_fk)
        deposit.refunded = money(deposit.refunded + value)
        deposit.state = self._deposit_state(deposit).value
        self._post(
            actor,
            narration=f"Deposit {deposit.number} refunded: {reason.strip()}",
            lines=[
                EntryLine(
                    CUSTOMER_DEPOSITS,
                    Side.DEBIT,
                    value,
                    f"Refund of {deposit.number}",
                    party_id=deposit.customer_fk,
                    subledger=SubledgerKind.CUSTOMER,
                ),
                EntryLine(self._treasury.ledger_code(account), Side.CREDIT, value, reason.strip()),
            ],
            on=self._today(deposit.business_id),
            source=JournalSource.RECEIPT,
            reference=deposit.number,
            source_id=deposit.id,
            key=f"deposit_refund:{deposit.id}:{deposit.refunded}",
        )
        self._db.flush()
        return deposit

    def deposits(
        self, actor: AnyActor, *, customer_id: uuid.UUID | None = None, held_only: bool = False
    ) -> list[CustomerDeposit]:
        actor.require_any("receivable.view", "receipt.manage")
        query = select(CustomerDeposit).where(CustomerDeposit.business_id == actor.business_id)
        if customer_id:
            query = query.where(CustomerDeposit.customer_fk == customer_id)
        if held_only:
            query = query.where(
                CustomerDeposit.state.in_(
                    (DepositState.HELD.value, DepositState.PART_APPLIED.value)
                )
            )
        return list(self._db.scalars(query.order_by(CustomerDeposit.received_on.desc())).all())

    def condition_met(self, deposit: CustomerDeposit, invoice: SalesInvoice) -> bool:
        """Whether the invoice meets the deposit's condition: always, once invoiced; or
        once every order line it bills has been delivered as far as it has been billed."""
        delivered = True
        for item in invoice.lines:
            if item.sales_order_line_fk is None:
                continue
            line = self._db.get(SalesOrderLine, item.sales_order_line_fk)
            if line is not None and line.quantity_delivered < line.quantity_invoiced:
                delivered = False
        return deposit_condition_met(
            DepositCondition(deposit.condition), invoiced=True, delivered=delivered
        )

    # -- credit notes, provisions, write-offs ------------------------------------------------

    def credit(self, actor: AnyActor, draft: CreditDraft) -> CustomerCreditNote:
        """Credit part of an invoice: Dr sales returns and output tax, Cr trade receivables."""
        actor.require("sales_return.manage")
        invoice = self.require_invoice(actor, draft.invoice_id)
        if invoice.origin == InvoiceOrigin.POS_SALE.value:
            raise ConflictError("A register sale is credited by a refund at the register.")
        reason = draft.reason.strip()
        if not reason:
            raise ValidationError("A credit note records why.", field="reason")
        business = self._db.get(Business, invoice.business_id)
        precision = business.currency_precision if business else 2
        lines = {line.id: line for line in invoice.lines}
        credited: list[dict[str, str]] = []
        net_total = ZERO
        tax_totals: dict[str, Decimal] = {}
        for item in draft.lines:
            line = lines.get(item.invoice_line_id)
            if line is None:
                raise NotFoundError("That line is not on this invoice.")
            quantity = money(item.quantity)
            if quantity <= ZERO:
                continue
            remaining = money(line.quantity - line.quantity_credited)
            if quantity > remaining:
                raise ConflictError(
                    f"{line.description}: {remaining.normalize():f} remains to credit."
                )
            share = quantity / line.quantity
            last = quantity == remaining
            net = present(money(line.net * share), precision)
            breakdown = sorted((line.tax_breakdown or {}).items())
            tax = present(money(line.tax * share), precision)
            parts = (
                allocate(tax, [Decimal(str(v)) for _, v in breakdown], precision)
                if breakdown
                else []
            )
            if last and line.quantity_credited == ZERO:
                net, tax = line.net, line.tax
                parts = [Decimal(str(v)) for _, v in breakdown]
            for (code, _), part in zip(breakdown, parts, strict=True):
                tax_totals[code] = money(tax_totals.get(code, ZERO) + part)
            net_total = money(net_total + net)
            line.quantity_credited = money(line.quantity_credited + quantity)
            credited.append(
                {
                    "invoice_line_id": str(line.id),
                    "quantity": str(quantity),
                    "net": str(net),
                    "tax": str(tax),
                }
            )
        if not credited:
            raise ValidationError("A credit note credits at least one line.", field="lines")
        tax_total = money(sum(tax_totals.values(), ZERO))
        total = money(net_total + tax_total)
        if total > money(invoice.outstanding):
            raise ConflictError(
                f"{money(invoice.outstanding)} of {invoice.number} is outstanding. Credit no more "
                "than is owed; an overpayment is refunded from the receipt that settled it."
            )
        note = CustomerCreditNote(
            business_id=invoice.business_id,
            number=OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "credit_note", branch_id=actor.branch_id)
            .number,
            invoice_fk=invoice.id,
            customer_fk=invoice.customer_fk,
            issued_on=self._today(invoice.business_id),
            reason=reason,
            net=net_total,
            tax=tax_total,
            total=total,
            lines=credited,
            issued_by_id=actor.user_id,
        )
        self._db.add(note)
        self._db.flush()
        note.journal_entry_fk = self._post(
            actor,
            narration=f"Credit note {note.number} on {invoice.number}: {reason}",
            lines=[
                EntryLine(SALES_RETURNS, Side.DEBIT, net_total, f"Credit {note.number}"),
                *tax_entry_lines(self._db, invoice.business_id, tax_totals, Side.DEBIT),
                EntryLine(
                    TRADE_RECEIVABLES,
                    Side.CREDIT,
                    total,
                    f"Credit {note.number}",
                    party_id=invoice.customer_fk,
                    subledger=SubledgerKind.CUSTOMER,
                ),
            ],
            on=note.issued_on,
            source=JournalSource.SALES,
            reference=note.number,
            source_id=note.id,
            key=f"customer_credit_note:{note.id}",
        )
        invoice.amount_credited = money(invoice.amount_credited + total)
        self._settle_state(invoice)
        self._db.flush()
        self._audit.record(
            actor,
            "customer_credit_note_issued",
            entity_type="sales_invoice",
            entity_id=invoice.id,
            entity_label=invoice.number,
            reason=reason,
            after={"credit_note": note.number, "total": str(total)},
        )
        return note

    def provide(
        self, actor: AnyActor, invoice_id: uuid.UUID, *, amount: Decimal, reason: str
    ) -> DebtProvision:
        """Provide against a doubtful invoice (a positive amount) or release a provision
        (a negative one): Dr bad debt expense, Cr the allowance, or the reverse."""
        actor.require("debt.write_off")
        invoice = self.require_invoice(actor, invoice_id)
        value = money(amount)
        if value == ZERO:
            raise ValidationError("Provide or release an amount.", field="amount")
        if not reason.strip():
            raise ValidationError("A provision records why.", field="reason")
        if value > ZERO and money(invoice.amount_provided + value) > money(invoice.outstanding):
            raise ConflictError(
                f"No more than the {money(invoice.outstanding)} outstanding is provided."
            )
        if value < ZERO and -value > invoice.amount_provided:
            raise ConflictError(f"{invoice.amount_provided} is provided against this invoice.")
        on = self._today(invoice.business_id)
        provision = DebtProvision(
            business_id=invoice.business_id,
            invoice_fk=invoice.id,
            amount=value,
            recorded_on=on,
            reason=reason.strip(),
            recorded_by_id=actor.user_id,
        )
        self._db.add(provision)
        self._db.flush()
        expense_side, allowance_side = (
            (Side.DEBIT, Side.CREDIT) if value > ZERO else (Side.CREDIT, Side.DEBIT)
        )
        provision.journal_entry_fk = self._post(
            actor,
            narration=(
                f"{'Provision against' if value > ZERO else 'Provision released on'} "
                f"{invoice.number}: {reason.strip()}"
            ),
            lines=[
                EntryLine(BAD_DEBT_EXPENSE, expense_side, abs(value), invoice.number),
                EntryLine(DOUBTFUL_DEBTS_ALLOWANCE, allowance_side, abs(value), invoice.number),
            ],
            on=on,
            source=JournalSource.SALES,
            reference=invoice.number,
            source_id=provision.id,
            key=f"debt_provision:{provision.id}",
        )
        invoice.amount_provided = money(invoice.amount_provided + value)
        self._db.flush()
        return provision

    def write_off(self, actor: AnyActor, invoice_id: uuid.UUID, *, reason: str) -> SalesInvoice:
        """Write off what remains: the provision first, bad debt expense for the rest."""
        actor.require("debt.write_off")
        invoice = self.require_invoice(actor, invoice_id)
        if not reason.strip():
            raise ValidationError("A write-off records why.", field="reason")
        owed = money(invoice.outstanding)
        if owed <= ZERO:
            raise ConflictError(f"Nothing is outstanding on {invoice.number}.")
        from_provision = min(invoice.amount_provided, owed)
        rest = money(owed - from_provision)
        lines: list[EntryLine] = []
        if from_provision > ZERO:
            lines.append(
                EntryLine(DOUBTFUL_DEBTS_ALLOWANCE, Side.DEBIT, from_provision, invoice.number)
            )
        if rest > ZERO:
            lines.append(EntryLine(BAD_DEBT_EXPENSE, Side.DEBIT, rest, invoice.number))
        lines.append(
            EntryLine(
                TRADE_RECEIVABLES,
                Side.CREDIT,
                owed,
                f"Written off {invoice.number}",
                party_id=invoice.customer_fk,
                subledger=SubledgerKind.CUSTOMER,
            )
        )
        self._post(
            actor,
            narration=f"{invoice.number} written off: {reason.strip()}",
            lines=lines,
            on=self._today(invoice.business_id),
            source=JournalSource.SALES,
            reference=invoice.number,
            source_id=invoice.id,
            key=f"write_off:{invoice.id}",
        )
        invoice.amount_provided = money(invoice.amount_provided - from_provision)
        invoice.amount_written_off = money(invoice.amount_written_off + owed)
        self._settle_state(invoice)
        self._db.flush()
        self._audit.record(
            actor,
            "sales_invoice_written_off",
            entity_type="sales_invoice",
            entity_id=invoice.id,
            entity_label=invoice.number,
            reason=reason.strip(),
            after={"amount": str(owed), "from_provision": str(from_provision)},
        )
        return invoice

    # -- collections ------------------------------------------------------------------------

    def note(
        self,
        actor: AnyActor,
        customer_id: uuid.UUID,
        *,
        kind: CollectionNoteKind | str,
        note: str,
        invoice_id: uuid.UUID | None = None,
        promised_on: date | None = None,
        promised_amount: Decimal | None = None,
    ) -> CollectionNote:
        actor.require("receipt.manage")
        customer = self._customer(actor, customer_id)
        chosen = CollectionNoteKind(kind)
        text = note.strip()
        if not text:
            raise ValidationError("A note says what was said or agreed.", field="note")
        invoice = self.require_invoice(actor, invoice_id) if invoice_id else None
        if invoice is not None and invoice.customer_fk != customer.id:
            raise ConflictError("That invoice belongs to another customer.")
        if chosen is CollectionNoteKind.PROMISE and promised_on is None:
            raise ValidationError("A promise to pay names its date.", field="promised_on")
        if chosen in (CollectionNoteKind.DISPUTE, CollectionNoteKind.RESOLVED) and invoice is None:
            raise ValidationError("A dispute is raised against an invoice.", field="invoice_id")
        row = CollectionNote(
            business_id=customer.business_id,
            customer_fk=customer.id,
            invoice_fk=invoice.id if invoice else None,
            kind=chosen.value,
            note=text,
            promised_on=promised_on,
            promised_amount=money(promised_amount) if promised_amount is not None else None,
            recorded_at=self._clock.now(),
            recorded_by_id=actor.user_id,
        )
        self._db.add(row)
        if invoice is not None and chosen is CollectionNoteKind.DISPUTE:
            invoice.disputed = True
        if invoice is not None and chosen is CollectionNoteKind.RESOLVED:
            invoice.disputed = False
        self._db.flush()
        return row

    def notes(self, actor: AnyActor, customer_id: uuid.UUID) -> list[CollectionNote]:
        actor.require("receivable.view")
        return list(
            self._db.scalars(
                select(CollectionNote)
                .where(
                    CollectionNote.business_id == actor.business_id,
                    CollectionNote.customer_fk == customer_id,
                )
                .order_by(CollectionNote.recorded_at.desc())
            ).all()
        )

    def worklist(self, actor: AnyActor, *, as_at: date | None = None) -> list[WorklistItem]:
        """Overdue invoices, most overdue first, with the latest word on each."""
        actor.require("receivable.view")
        business_id = _business_of(actor)
        today = as_at or self._today(business_id)
        items = []
        for invoice in self._db.scalars(
            select(SalesInvoice).where(
                SalesInvoice.business_id == business_id,
                SalesInvoice.state.in_((InvoiceState.OPEN.value, InvoiceState.PART_SETTLED.value)),
                SalesInvoice.due_on < today,
            )
        ).all():
            latest = self._db.scalar(
                select(CollectionNote)
                .where(
                    CollectionNote.customer_fk == invoice.customer_fk,
                    (CollectionNote.invoice_fk == invoice.id) | CollectionNote.invoice_fk.is_(None),
                )
                .order_by(CollectionNote.recorded_at.desc())
                .limit(1)
            )
            promise = self._db.scalar(
                select(func.max(CollectionNote.promised_on)).where(
                    CollectionNote.customer_fk == invoice.customer_fk,
                    CollectionNote.kind == CollectionNoteKind.PROMISE.value,
                )
            )
            customer = self._db.get(Party, invoice.customer_fk)
            items.append(
                WorklistItem(
                    invoice=invoice,
                    customer=customer.name if customer else "",
                    days_overdue=(today - invoice.due_on).days,
                    outstanding=money(invoice.outstanding),
                    latest=latest,
                    promised_on=promise,
                    reminder_due=not invoice.disputed and (promise is None or promise < today),
                )
            )
        return sorted(items, key=lambda item: (-item.days_overdue, item.invoice.number))

    def ageing(self, actor: AnyActor, *, as_at: date | None = None) -> list[AgeingRow]:
        """What each customer owes, by how overdue it is."""
        actor.require("receivable.view")
        business_id = _business_of(actor)
        today = as_at or self._today(business_id)
        rows: dict[uuid.UUID, AgeingRow] = {}
        for invoice in self._db.scalars(
            select(SalesInvoice).where(
                SalesInvoice.business_id == business_id,
                SalesInvoice.state.in_((InvoiceState.OPEN.value, InvoiceState.PART_SETTLED.value)),
            )
        ).all():
            row = rows.get(invoice.customer_fk) or self._ageing_row(invoice.customer_fk)
            rows[invoice.customer_fk] = row
            bucket = ageing_bucket(invoice.due_on, today)
            owed = money(invoice.outstanding)
            row.buckets[bucket] = money(row.buckets[bucket] + owed)
            row.total = money(row.total + owed)
        for receipt in self._db.scalars(
            select(CustomerReceipt).where(CustomerReceipt.business_id == business_id)
        ).all():
            if receipt.unapplied > ZERO:
                row = rows.get(receipt.customer_fk) or self._ageing_row(receipt.customer_fk)
                rows[receipt.customer_fk] = row
                row.unapplied = money(row.unapplied + receipt.unapplied)
        return sorted(rows.values(), key=lambda item: (-item.total, item.customer))

    def statement(
        self,
        actor: AnyActor,
        customer_id: uuid.UUID,
        *,
        start: date,
        end: date | None = None,
    ) -> CustomerStatement:
        """The customer's account as the ledger holds it: every movement on their trade
        receivable, with the balance brought forward."""
        actor.require("receivable.view")
        customer = self._customer(actor, customer_id)
        business_id = customer.business_id
        ends_on = end or self._today(business_id)
        account = self._db.scalar(
            select(Account).where(
                Account.business_id == business_id, Account.code == TRADE_RECEIVABLES
            )
        )
        assert account is not None
        base = select(JournalLine).where(
            JournalLine.business_id == business_id,
            JournalLine.account_fk == account.id,
            JournalLine.party_fk == customer.id,
            JournalLine.state == "posted",
        )
        before = self._db.scalars(base.where(JournalLine.posting_date < start)).all()
        opening = money(sum((line.base_debit - line.base_credit for line in before), ZERO))
        balance = opening
        entries = []
        for line in self._db.scalars(
            base.where(
                JournalLine.posting_date >= start, JournalLine.posting_date <= ends_on
            ).order_by(JournalLine.posting_date, JournalLine.entry_fk, JournalLine.display_order)
        ).all():
            entry = self._db.get(JournalEntry, line.entry_fk)
            balance = money(balance + line.base_debit - line.base_credit)
            entries.append(
                StatementEntry(
                    posted_on=line.posting_date,
                    reference=entry.source_reference if entry else None,
                    narration=line.narration or (entry.narration if entry else ""),
                    debit=line.base_debit,
                    credit=line.base_credit,
                    balance=balance,
                )
            )
        ageing = self._ageing_row(customer.id).buckets
        for invoice in self._open_invoices(customer.id):
            bucket = ageing_bucket(invoice.due_on, ends_on)
            ageing[bucket] = money(ageing[bucket] + money(invoice.outstanding))
        return CustomerStatement(
            customer=customer,
            starts_on=start,
            ends_on=ends_on,
            opening=opening,
            entries=entries,
            closing=balance,
            ageing=ageing,
        )

    def credit_position(self, customer: Party) -> CreditPosition:
        outstanding = money(
            sum((money(invoice.outstanding) for invoice in self._open_invoices(customer.id)), ZERO)
        )
        unapplied = money(
            sum(
                (
                    receipt.unapplied
                    for receipt in self._db.scalars(
                        select(CustomerReceipt).where(CustomerReceipt.customer_fk == customer.id)
                    ).all()
                ),
                ZERO,
            )
        )
        return CreditPosition(
            limit=customer.credit_limit,
            outstanding=money(outstanding - unapplied),
            on_hold=customer.credit_on_hold,
        )

    def customer_balance(self, actor: AnyActor, customer_id: uuid.UUID) -> Decimal:
        """The customer's trade receivable in the ledger: positive is owed to us."""
        actor.require("receivable.view")
        customer = self._customer(actor, customer_id)
        account = self._db.scalar(
            select(Account).where(
                Account.business_id == customer.business_id, Account.code == TRADE_RECEIVABLES
            )
        )
        assert account is not None
        debit, credit = self._db.execute(
            select(
                func.coalesce(func.sum(JournalLine.base_debit), 0),
                func.coalesce(func.sum(JournalLine.base_credit), 0),
            ).where(
                JournalLine.account_fk == account.id,
                JournalLine.party_fk == customer.id,
                JournalLine.state == "posted",
            )
        ).one()
        return money(Decimal(str(debit)) - Decimal(str(credit)))

    # -- internals --------------------------------------------------------------------------

    def _apply_held_deposits(self, actor: AnyActor, invoice: SalesInvoice) -> None:
        """Deposits held against the invoice's order, whose condition the invoice meets."""
        if invoice.sales_order_fk is None:
            return
        for deposit in self._db.scalars(
            select(CustomerDeposit)
            .where(
                CustomerDeposit.sales_order_fk == invoice.sales_order_fk,
                CustomerDeposit.state.in_(
                    (DepositState.HELD.value, DepositState.PART_APPLIED.value)
                ),
            )
            .order_by(CustomerDeposit.received_on)
        ).all():
            if invoice.outstanding <= ZERO:
                break
            if self.condition_met(deposit, invoice):
                self._apply(actor, deposit, invoice, None)

    def _apply(
        self,
        actor: AnyActor,
        deposit: CustomerDeposit,
        invoice: SalesInvoice,
        amount: Decimal | None,
    ) -> DepositApplication:
        if deposit.customer_fk != invoice.customer_fk:
            raise ConflictError("The deposit and the invoice belong to different customers.")
        if not self.condition_met(deposit, invoice):
            raise ConflictError(
                f"Deposit {deposit.number} is applied once the goods are delivered; "
                f"{invoice.number} bills goods not yet delivered."
            )
        value = (
            money(amount)
            if amount is not None
            else min(money(deposit.held), money(invoice.outstanding))
        )
        if value <= ZERO:
            raise ConflictError("Nothing remains to apply.")
        if value > money(deposit.held) or value > money(invoice.outstanding):
            raise ValidationError(
                f"Up to {min(money(deposit.held), money(invoice.outstanding))} may be applied.",
                field="amount",
            )
        on = self._today(invoice.business_id)
        application = DepositApplication(
            business_id=invoice.business_id,
            deposit_fk=deposit.id,
            invoice_fk=invoice.id,
            amount=value,
            applied_on=on,
        )
        self._db.add(application)
        self._db.flush()
        application.journal_entry_fk = self._post(
            actor,
            narration=f"Deposit {deposit.number} applied to {invoice.number}",
            lines=[
                EntryLine(
                    CUSTOMER_DEPOSITS,
                    Side.DEBIT,
                    value,
                    f"Applied to {invoice.number}",
                    party_id=deposit.customer_fk,
                    subledger=SubledgerKind.CUSTOMER,
                ),
                EntryLine(
                    TRADE_RECEIVABLES,
                    Side.CREDIT,
                    value,
                    f"Deposit {deposit.number}",
                    party_id=invoice.customer_fk,
                    subledger=SubledgerKind.CUSTOMER,
                ),
            ],
            on=on,
            source=JournalSource.RECEIPT,
            reference=deposit.number,
            source_id=application.id,
            key=f"deposit_application:{application.id}",
        )
        deposit.applied = money(deposit.applied + value)
        deposit.state = self._deposit_state(deposit).value
        invoice.amount_received = money(invoice.amount_received + value)
        self._settle_state(invoice)
        self._db.flush()
        return application

    def _allocate(
        self,
        actor: AnyActor,
        receipt: CustomerReceipt,
        invoice: SalesInvoice,
        amount: Decimal,
        on: date,
    ) -> None:
        if invoice.customer_fk != receipt.customer_fk:
            raise ConflictError(f"{invoice.number} belongs to another customer.")
        if amount <= ZERO:
            raise ValidationError("An allocation above zero.", field="amount")
        if amount > money(receipt.unapplied):
            raise ConflictError(f"{money(receipt.unapplied)} of {receipt.number} is unapplied.")
        if amount > money(invoice.outstanding):
            raise ConflictError(f"{money(invoice.outstanding)} is outstanding on {invoice.number}.")
        receipt.allocations.append(
            ReceiptAllocation(
                business_id=receipt.business_id,
                invoice_fk=invoice.id,
                amount=amount,
                allocated_on=on,
                allocated_by_id=actor.user_id,
            )
        )
        receipt.allocated = money(receipt.allocated + amount)
        invoice.amount_received = money(invoice.amount_received + amount)
        self._settle_state(invoice)

    def _settle_state(self, invoice: SalesInvoice) -> None:
        invoice.state = invoice_state(
            invoice.total,
            money(invoice.amount_received + invoice.amount_credited + invoice.amount_written_off),
            invoice.amount_written_off,
        ).value

    def _deposit_state(self, deposit: CustomerDeposit) -> DepositState:
        if deposit.held <= ZERO:
            return DepositState.APPLIED if deposit.applied > ZERO else DepositState.REFUNDED
        return DepositState.PART_APPLIED if deposit.applied > ZERO else DepositState.HELD

    def _ceiling(self, line: SalesOrderLine, basis: str) -> Decimal:
        if basis == "ordered":
            return money(
                max(ZERO, line.quantity_ordered - line.quantity_cancelled - line.quantity_invoiced)
            )
        return money(max(ZERO, line.quantity_delivered - line.quantity_invoiced))

    def _open_invoices(self, customer_id: uuid.UUID) -> list[SalesInvoice]:
        return list(
            self._db.scalars(
                select(SalesInvoice)
                .where(
                    SalesInvoice.customer_fk == customer_id,
                    SalesInvoice.state.in_(
                        (InvoiceState.OPEN.value, InvoiceState.PART_SETTLED.value)
                    ),
                )
                .order_by(SalesInvoice.due_on, SalesInvoice.number)
            ).all()
        )

    def _ageing_row(self, customer_id: uuid.UUID) -> AgeingRow:
        customer = self._db.get(Party, customer_id)
        return AgeingRow(
            customer_id=customer_id,
            customer=customer.name if customer else "",
            buckets=dict.fromkeys(AgeingBucket, ZERO),
            total=ZERO,
            unapplied=ZERO,
            credit_limit=customer.credit_limit if customer else None,
            on_hold=customer.credit_on_hold if customer else False,
        )

    def _due(self, customer: Party, issued_on: date) -> date:
        return PaymentTerms(
            PaymentTermKind(customer.payment_term_kind), customer.payment_term_days
        ).due_date(issued_on)

    def _order(self, actor: AnyActor, order_id: uuid.UUID | None) -> SalesOrder:
        order = self._db.get(SalesOrder, order_id) if order_id else None
        if order is None or order.business_id != actor.business_id:
            raise NotFoundError("Sales order not found.")
        return order

    def _customer(self, actor: AnyActor, customer_id: uuid.UUID) -> Party:
        party = self._db.get(Party, customer_id)
        if party is None or party.business_id != actor.business_id or not party.is_customer:
            raise NotFoundError("Customer not found.")
        return party

    def _receipt(self, actor: AnyActor, receipt_id: uuid.UUID) -> CustomerReceipt:
        receipt = self._db.get(CustomerReceipt, receipt_id)
        if receipt is None or receipt.business_id != actor.business_id:
            raise NotFoundError("Receipt not found.")
        return receipt

    def require_deposit(self, actor: AnyActor, deposit_id: uuid.UUID) -> CustomerDeposit:
        actor.require_any("receivable.view", "receipt.manage")
        return self._deposit(actor, deposit_id)

    def _deposit(self, actor: AnyActor, deposit_id: uuid.UUID) -> CustomerDeposit:
        deposit = self._db.get(CustomerDeposit, deposit_id)
        if deposit is None or deposit.business_id != actor.business_id:
            raise NotFoundError("Deposit not found.")
        return deposit

    def _account(self, actor: AnyActor, account_id: uuid.UUID | None) -> FinancialAccount:
        if account_id is None:
            return self._treasury.default_for(_business_of(actor), FinancialAccountKind.BANK)
        account = self._treasury.require(actor, account_id)
        if not account.is_active:
            raise ConflictError(f"{account.name} is closed.")
        return account

    def _post(
        self,
        actor: AnyActor,
        *,
        narration: str,
        lines: list[EntryLine],
        on: date,
        source: JournalSource,
        reference: str,
        source_id: uuid.UUID,
        key: str,
    ) -> uuid.UUID:
        business = self._db.get(Business, actor.business_id)
        return (
            JournalService(self._db, clock=self._clock)
            .post(
                actor,
                JournalDraft(
                    posting_date=on,
                    narration=narration,
                    lines=lines,
                    source=source,
                    source_reference=reference,
                    source_id=source_id,
                    idempotency_key=key,
                    currency_code=business.base_currency if business else None,
                    branch_id=actor.branch_id,
                ),
            )
            .entry.id
        )

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
