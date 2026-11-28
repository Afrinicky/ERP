"""Receivables endpoints: invoices, receipts, deposits, credits, collections, statements."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter

from nickland.application.services.receivables import (
    CreditDraft,
    CreditLineDraft,
    DepositDraft,
    InvoiceDraft,
    InvoiceLineDraft,
    ReceiptDraft,
)
from nickland.domain.finance import CollectionNoteKind
from nickland.domain.orders import AgeingBucket
from nickland.interface.dependencies import (
    ActorDependency,
    ReceivablesDependency,
    SessionDependency,
    TreasuryDependency,
)
from nickland.interface.finance_replies import (
    Names,
    customer_statement,
    deposit,
    invoice,
    receipt,
)
from nickland.interface.finance_schemas import (
    AgeingRowReply,
    AllocationContract,
    ApplyDepositContract,
    CreditContract,
    CreditNoteReply,
    DepositContract,
    DepositReply,
    InvoiceableLineReply,
    InvoiceContract,
    InvoiceReply,
    NoteContract,
    NoteReply,
    PartyStatementReply,
    ProvisionContract,
    ReasonContract,
    ReceiptContract,
    ReceiptReply,
    RefundContract,
    WorklistReply,
)
from nickland.persistence.finance_models import SalesInvoice

router = APIRouter(prefix="/receivables", tags=["receivables"])


def _today(treasury: TreasuryDependency, actor: ActorDependency) -> date:
    assert actor.business_id is not None
    return treasury.today(actor.business_id)


@router.get(
    "/orders/{order_id}/invoiceable",
    response_model=list[InvoiceableLineReply],
    summary="What an order may be billed for",
)
def invoiceable(
    order_id: uuid.UUID, actor: ActorDependency, receivables: ReceivablesDependency
) -> list[InvoiceableLineReply]:
    return [
        InvoiceableLineReply(
            line_id=row.line.id,
            description=row.line.description,
            unit_price=row.line.unit_price,
            ordered=row.ordered,
            delivered=row.delivered,
            invoiced=row.invoiced,
            invoiceable=row.invoiceable,
        )
        for row in receivables.invoiceable(actor, order_id)
    ]


@router.post("/invoices", response_model=InvoiceReply, status_code=201, summary="Issue an invoice")
def issue_invoice(
    body: InvoiceContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    treasury: TreasuryDependency,
    db: SessionDependency,
) -> InvoiceReply:
    made = receivables.issue_invoice(
        actor,
        InvoiceDraft(
            sales_order_id=body.sales_order_id,
            lines=[
                InvoiceLineDraft(
                    sales_order_line_id=item.sales_order_line_id, quantity=item.quantity
                )
                for item in body.lines
            ],
            basis=body.basis,
            issued_on=body.issued_on,
            customer_reference=body.customer_reference,
            credit_override_reason=body.credit_override_reason,
            notes=body.notes,
        ),
    )
    return invoice(Names(db), made, _today(treasury, actor))


@router.get("/invoices", response_model=list[InvoiceReply], summary="Invoices")
def invoices(
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    treasury: TreasuryDependency,
    db: SessionDependency,
    customer_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    open_only: bool = False,
) -> list[InvoiceReply]:
    names = Names(db)
    today = _today(treasury, actor)
    return [
        invoice(names, item, today)
        for item in receivables.invoices(
            actor, customer_id=customer_id, order_id=order_id, open_only=open_only
        )
    ]


@router.get("/invoices/{invoice_id}", response_model=InvoiceReply, summary="An invoice")
def one_invoice(
    invoice_id: uuid.UUID,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    treasury: TreasuryDependency,
    db: SessionDependency,
) -> InvoiceReply:
    return invoice(
        Names(db), receivables.require_invoice(actor, invoice_id), _today(treasury, actor)
    )


@router.post(
    "/invoices/{invoice_id}/credit",
    response_model=CreditNoteReply,
    status_code=201,
    summary="Credit an invoice",
)
def credit(
    invoice_id: uuid.UUID,
    body: CreditContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
) -> CreditNoteReply:
    note = receivables.credit(
        actor,
        CreditDraft(
            invoice_id=invoice_id,
            lines=[CreditLineDraft(item.invoice_line_id, item.quantity) for item in body.lines],
            reason=body.reason,
        ),
    )
    return CreditNoteReply(
        id=note.id,
        number=note.number,
        invoice_id=note.invoice_fk,
        issued_on=note.issued_on,
        reason=note.reason,
        net=note.net,
        tax=note.tax,
        total=note.total,
    )


@router.post("/invoices/{invoice_id}/provision", response_model=InvoiceReply, summary="Provide")
def provide(
    invoice_id: uuid.UUID,
    body: ProvisionContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    treasury: TreasuryDependency,
    db: SessionDependency,
) -> InvoiceReply:
    receivables.provide(actor, invoice_id, amount=body.amount, reason=body.reason)
    return invoice(
        Names(db), receivables.require_invoice(actor, invoice_id), _today(treasury, actor)
    )


@router.post("/invoices/{invoice_id}/write-off", response_model=InvoiceReply, summary="Write off")
def write_off(
    invoice_id: uuid.UUID,
    body: ReasonContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    treasury: TreasuryDependency,
    db: SessionDependency,
) -> InvoiceReply:
    made = receivables.write_off(actor, invoice_id, reason=body.reason)
    return invoice(Names(db), made, _today(treasury, actor))


@router.post("/receipts", response_model=ReceiptReply, status_code=201, summary="Record a receipt")
def receive(
    body: ReceiptContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
) -> ReceiptReply:
    made = receivables.receive(
        actor,
        ReceiptDraft(
            customer_id=body.customer_id,
            amount=body.amount,
            financial_account_id=body.financial_account_id,
            received_on=body.received_on,
            reference=body.reference,
            allocations=(
                [(item.invoice_id, item.amount) for item in body.allocations]
                if body.allocations is not None
                else None
            ),
            notes=body.notes,
        ),
    )
    return receipt(Names(db), made)


@router.get("/receipts", response_model=list[ReceiptReply], summary="Receipts")
def receipts(
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
    customer_id: uuid.UUID | None = None,
) -> list[ReceiptReply]:
    names = Names(db)
    return [receipt(names, item) for item in receivables.receipts(actor, customer_id=customer_id)]


@router.post("/receipts/{receipt_id}/allocate", response_model=ReceiptReply, summary="Allocate")
def allocate(
    receipt_id: uuid.UUID,
    body: AllocationContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
) -> ReceiptReply:
    return receipt(Names(db), receivables.allocate(actor, receipt_id, body.invoice_id, body.amount))


@router.post("/receipts/{receipt_id}/refund", response_model=ReceiptReply, summary="Refund credit")
def refund_credit(
    receipt_id: uuid.UUID,
    body: RefundContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
) -> ReceiptReply:
    made = receivables.refund_credit(
        actor,
        receipt_id,
        amount=body.amount,
        reason=body.reason,
        financial_account_id=body.financial_account_id,
    )
    return receipt(Names(db), made)


@router.post("/deposits", response_model=DepositReply, status_code=201, summary="Take a deposit")
def take_deposit(
    body: DepositContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
) -> DepositReply:
    return deposit(Names(db), receivables.take_deposit(actor, DepositDraft(**body.model_dump())))


@router.get("/deposits", response_model=list[DepositReply], summary="Deposits")
def deposits(
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
    customer_id: uuid.UUID | None = None,
    held_only: bool = False,
) -> list[DepositReply]:
    names = Names(db)
    return [
        deposit(names, item)
        for item in receivables.deposits(actor, customer_id=customer_id, held_only=held_only)
    ]


@router.post("/deposits/{deposit_id}/apply", response_model=DepositReply, summary="Apply")
def apply_deposit(
    deposit_id: uuid.UUID,
    body: ApplyDepositContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
) -> DepositReply:
    receivables.apply_deposit(actor, deposit_id, body.invoice_id, amount=body.amount)
    return deposit(Names(db), receivables.require_deposit(actor, deposit_id))


@router.post("/deposits/{deposit_id}/refund", response_model=DepositReply, summary="Refund")
def refund_deposit(
    deposit_id: uuid.UUID,
    body: RefundContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
) -> DepositReply:
    made = receivables.refund_deposit(
        actor,
        deposit_id,
        amount=body.amount,
        reason=body.reason,
        financial_account_id=body.financial_account_id,
    )
    return deposit(Names(db), made)


@router.get("/ageing", response_model=list[AgeingRowReply], summary="Ageing")
def ageing(
    actor: ActorDependency, receivables: ReceivablesDependency, as_at: date | None = None
) -> list[AgeingRowReply]:
    return [
        AgeingRowReply(
            customer_id=row.customer_id,
            customer=row.customer,
            current=row.buckets[AgeingBucket.CURRENT],
            days_1_30=row.buckets[AgeingBucket.DAYS_1_30],
            days_31_60=row.buckets[AgeingBucket.DAYS_31_60],
            days_61_90=row.buckets[AgeingBucket.DAYS_61_90],
            over_90=row.buckets[AgeingBucket.OVER_90],
            total=row.total,
            unapplied=row.unapplied,
            credit_limit=row.credit_limit,
            on_hold=row.on_hold,
        )
        for row in receivables.ageing(actor, as_at=as_at)
    ]


@router.get("/worklist", response_model=list[WorklistReply], summary="Collection worklist")
def worklist(
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    treasury: TreasuryDependency,
    db: SessionDependency,
) -> list[WorklistReply]:
    names = Names(db)
    today = _today(treasury, actor)
    return [
        WorklistReply(
            invoice=invoice(names, item.invoice, today),
            days_overdue=item.days_overdue,
            outstanding=item.outstanding,
            latest_note=item.latest.note if item.latest else None,
            latest_kind=CollectionNoteKind(item.latest.kind).label if item.latest else None,
            promised_on=item.promised_on,
            reminder_due=item.reminder_due,
        )
        for item in receivables.worklist(actor)
    ]


@router.post(
    "/customers/{customer_id}/notes",
    response_model=NoteReply,
    status_code=201,
    summary="Record a contact, promise or dispute",
)
def add_note(
    customer_id: uuid.UUID,
    body: NoteContract,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
) -> NoteReply:
    row = receivables.note(actor, customer_id, **body.model_dump())
    return _note(db, row)


@router.get("/customers/{customer_id}/notes", response_model=list[NoteReply], summary="Notes")
def notes(
    customer_id: uuid.UUID,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    db: SessionDependency,
) -> list[NoteReply]:
    return [_note(db, row) for row in receivables.notes(actor, customer_id)]


@router.get(
    "/customers/{customer_id}/statement",
    response_model=PartyStatementReply,
    summary="Statement of account",
)
def statement(
    customer_id: uuid.UUID,
    actor: ActorDependency,
    receivables: ReceivablesDependency,
    start: date,
    end: date | None = None,
) -> PartyStatementReply:
    return customer_statement(receivables.statement(actor, customer_id, start=start, end=end))


def _note(db: SessionDependency, row) -> NoteReply:  # type: ignore[no-untyped-def]
    names = Names(db)
    number = None
    if row.invoice_fk:
        found = db.get(SalesInvoice, row.invoice_fk)
        number = found.number if found else None
    return NoteReply(
        id=row.id,
        kind=row.kind,
        kind_label=CollectionNoteKind(row.kind).label,
        note=row.note,
        invoice_number=number,
        promised_on=row.promised_on,
        promised_amount=row.promised_amount,
        recorded_at=row.recorded_at,
        recorded_by=names.user(row.recorded_by_id),
    )
