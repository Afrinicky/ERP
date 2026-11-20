"""Procurement endpoints: purchase orders, receipts, supplier billing and payables."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter

from nickland.application.services.payables import BatchItem, PaymentDraft
from nickland.application.services.procurement import (
    PurchaseLineDraft,
    PurchaseOrderDraft,
    ReceiptDraft,
    ReceiptLineDraft,
)
from nickland.application.services.supplier_billing import (
    CreditNoteDraft,
    InvoiceDraft,
    InvoiceLineDraft,
)
from nickland.domain.money import suggested_tenders
from nickland.interface.dependencies import (
    ActorDependency,
    PartyDependency,
    PayablesDependency,
    ProcurementDependency,
    SalesOrderDependency,
    SessionDependency,
    SupplierBillingDependency,
)
from nickland.interface.finance_replies import Names
from nickland.interface.finance_schemas import (
    BatchContract,
    BatchReply,
    PartyStatementReply,
    PriorityContract,
    StatementEntryReply,
)
from nickland.interface.order_schemas import (
    CreditNoteContract,
    CreditNoteReply,
    InvoiceContract,
    InvoiceLineReply,
    PayableRowReply,
    PayablesRegisterReply,
    PaymentContract,
    PaymentReply,
    PurchaseLineContract,
    PurchaseOrderContract,
    PurchaseOrderLineReply,
    PurchaseOrderReply,
    ReasonContract,
    ReceiptContract,
    ReceiptLineReply,
    ReceiptReply,
    SuggestedOrderReply,
    SupplierInvoiceReply,
    ToleranceContract,
    ToleranceReply,
    VarianceDecisionContract,
    VarianceReply,
)
from nickland.persistence.order_models import (
    GoodsReceipt,
    PurchaseOrder,
    SupplierInvoice,
    SupplierPayment,
)

router = APIRouter(prefix="/purchases", tags=["purchases"])


# -- replies ---------------------------------------------------------------------------


def _order_reply(
    actor: ActorDependency,
    parties: PartyDependency,
    orders: SalesOrderDependency,
    order: PurchaseOrder,
) -> PurchaseOrderReply:
    supplier = parties.require(actor, order.supplier_fk)
    sales_order = (
        orders.require(actor, order.sales_order_fk) if order.sales_order_fk is not None else None
    )
    return PurchaseOrderReply(
        id=order.id,
        number=order.number,
        supplier_fk=order.supplier_fk,
        supplier_name=supplier.name,
        sales_order_fk=order.sales_order_fk,
        sales_order_number=sales_order.number if sales_order else None,
        supplier_quotation_fk=order.supplier_quotation_fk,
        warehouse_fk=order.warehouse_fk,
        purpose=order.purpose,
        state=order.state,
        ordered_on=order.ordered_on,
        expected_on=order.expected_on,
        supplier_reference=order.supplier_reference,
        currency_code=order.currency_code,
        exchange_rate=order.exchange_rate,
        net_total=order.net_total,
        tax_total=order.tax_total,
        total=order.total,
        notes=order.notes,
        approved_at=order.approved_at,
        issued_at=order.issued_at,
        cancelled_at=order.cancelled_at,
        cancellation_reason=order.cancellation_reason,
        lines=[PurchaseOrderLineReply.model_validate(line) for line in order.lines],
    )


def _receipt_reply(
    actor: ActorDependency,
    parties: PartyDependency,
    procurement: ProcurementDependency,
    receipt: GoodsReceipt,
) -> ReceiptReply:
    supplier = parties.require(actor, receipt.supplier_fk)
    order = procurement.require(actor, receipt.purchase_order_fk)
    return ReceiptReply(
        id=receipt.id,
        number=receipt.number,
        purchase_order_fk=receipt.purchase_order_fk,
        purchase_order_number=order.number,
        supplier_fk=receipt.supplier_fk,
        supplier_name=supplier.name,
        warehouse_fk=receipt.warehouse_fk,
        state=receipt.state,
        received_on=receipt.received_on,
        delivery_reference=receipt.delivery_reference,
        inspection_pending=receipt.inspection_pending,
        accepted_value=receipt.accepted_value,
        journal_entry_fk=receipt.journal_entry_fk,
        notes=receipt.notes,
        lines=[ReceiptLineReply.model_validate(line) for line in receipt.lines],
    )


def _invoice_reply(
    actor: ActorDependency,
    parties: PartyDependency,
    procurement: ProcurementDependency,
    invoice: SupplierInvoice,
) -> SupplierInvoiceReply:
    supplier = parties.require(actor, invoice.supplier_fk)
    order = procurement.require(actor, invoice.purchase_order_fk)
    return SupplierInvoiceReply(
        id=invoice.id,
        number=invoice.number,
        supplier_reference=invoice.supplier_reference,
        supplier_fk=invoice.supplier_fk,
        supplier_name=supplier.name,
        purchase_order_fk=invoice.purchase_order_fk,
        purchase_order_number=order.number,
        state=invoice.state,
        invoiced_on=invoice.invoiced_on,
        due_on=invoice.due_on,
        currency_code=invoice.currency_code,
        exchange_rate=invoice.exchange_rate,
        net_total=invoice.net_total,
        recoverable_tax_total=invoice.recoverable_tax_total,
        non_recoverable_tax_total=invoice.non_recoverable_tax_total,
        total=invoice.total,
        base_total=invoice.base_total,
        amount_paid=invoice.amount_paid,
        amount_credited=invoice.amount_credited,
        evidence_attachment_fk=invoice.evidence_attachment_fk,
        journal_entry_fk=invoice.journal_entry_fk,
        notes=invoice.notes,
        posted_at=invoice.posted_at,
        lines=[InvoiceLineReply.model_validate(line) for line in invoice.lines],
        variances=[VarianceReply.model_validate(item) for item in invoice.variances],
    )


def _payment_reply(
    actor: ActorDependency, parties: PartyDependency, payment: SupplierPayment
) -> PaymentReply:
    supplier = parties.require(actor, payment.supplier_fk)
    return PaymentReply(
        id=payment.id,
        number=payment.number,
        supplier_fk=payment.supplier_fk,
        supplier_name=supplier.name,
        paid_on=payment.paid_on,
        method=payment.method,
        reference=payment.reference,
        amount=payment.amount,
        withheld=payment.withheld,
        evidence_attachment_fk=payment.evidence_attachment_fk,
        journal_entry_fk=payment.journal_entry_fk,
        notes=payment.notes,
        allocations=[
            {"invoice_fk": item.invoice_fk, "amount": item.amount} for item in payment.allocations
        ],
    )


# -- purchase orders -------------------------------------------------------------------


@router.get(
    "/orders/suggest/{sales_order_id}",
    response_model=list[SuggestedOrderReply],
    summary="Purchase orders the sales order calls for",
)
def suggest(
    sales_order_id: uuid.UUID,
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
) -> list[SuggestedOrderReply]:
    """One draft per supplier, from the sources the quotation chose. Nothing is raised."""
    found = []
    for supplier_id, draft in procurement.suggest_from_order(actor, sales_order_id).items():
        supplier = parties.require(actor, supplier_id)
        found.append(
            SuggestedOrderReply(
                supplier_id=supplier_id,
                supplier_name=supplier.name,
                currency_code=draft.currency_code,
                exchange_rate=draft.exchange_rate,
                expected_on=draft.expected_on,
                supplier_quotation_id=draft.supplier_quotation_id,
                lines=[
                    PurchaseLineContract(
                        description=line.description,
                        quantity=line.quantity,
                        unit_cost=line.unit_cost,
                        product_id=line.product_id,
                        sales_order_line_id=line.sales_order_line_id,
                        supplier_quotation_line_id=line.supplier_quotation_line_id,
                        discount_rate=line.discount_rate,
                        unit_id=line.unit_id,
                    )
                    for line in draft.lines
                ],
            )
        )
    return found


@router.post(
    "/orders", response_model=PurchaseOrderReply, status_code=201, summary="Raise a purchase order"
)
def raise_order(
    payload: PurchaseOrderContract,
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    orders: SalesOrderDependency,
) -> PurchaseOrderReply:
    order = procurement.raise_order(
        actor,
        PurchaseOrderDraft(
            supplier_id=payload.supplier_id,
            lines=[
                PurchaseLineDraft(
                    description=line.description,
                    quantity=line.quantity,
                    unit_cost=line.unit_cost,
                    product_id=line.product_id,
                    sales_order_line_id=line.sales_order_line_id,
                    supplier_quotation_line_id=line.supplier_quotation_line_id,
                    discount_rate=line.discount_rate,
                    unit_id=line.unit_id,
                    expected_on=line.expected_on,
                    notes=line.notes,
                )
                for line in payload.lines
            ],
            sales_order_id=payload.sales_order_id,
            supplier_quotation_id=payload.supplier_quotation_id,
            warehouse_id=payload.warehouse_id,
            purpose=payload.purpose,
            currency_code=payload.currency_code,
            exchange_rate=payload.exchange_rate,
            expected_on=payload.expected_on,
            supplier_reference=payload.supplier_reference,
            notes=payload.notes,
        ),
    )
    return _order_reply(actor, parties, orders, order)


@router.get("/orders", response_model=list[PurchaseOrderReply], summary="List purchase orders")
def list_orders(
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    orders: SalesOrderDependency,
    state: str | None = None,
    supplier_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[PurchaseOrderReply]:
    return [
        _order_reply(actor, parties, orders, item)
        for item in procurement.orders(actor, state=state, supplier_id=supplier_id, limit=limit)
    ]


@router.get(
    "/orders/{order_id}", response_model=PurchaseOrderReply, summary="Read a purchase order"
)
def read_order(
    order_id: uuid.UUID,
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    orders: SalesOrderDependency,
) -> PurchaseOrderReply:
    return _order_reply(actor, parties, orders, procurement.require(actor, order_id))


@router.post("/orders/{order_id}/approve", response_model=PurchaseOrderReply, summary="Approve")
def approve_order(
    order_id: uuid.UUID,
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    orders: SalesOrderDependency,
) -> PurchaseOrderReply:
    return _order_reply(actor, parties, orders, procurement.approve(actor, order_id))


@router.post("/orders/{order_id}/issue", response_model=PurchaseOrderReply, summary="Issue")
def issue_order(
    order_id: uuid.UUID,
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    orders: SalesOrderDependency,
) -> PurchaseOrderReply:
    return _order_reply(actor, parties, orders, procurement.issue(actor, order_id))


@router.post("/orders/{order_id}/cancel", response_model=PurchaseOrderReply, summary="Cancel")
def cancel_order(
    order_id: uuid.UUID,
    payload: ReasonContract,
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    orders: SalesOrderDependency,
) -> PurchaseOrderReply:
    return _order_reply(
        actor, parties, orders, procurement.cancel(actor, order_id, reason=payload.reason)
    )


@router.post("/orders/{order_id}/close", response_model=PurchaseOrderReply, summary="Close short")
def close_order(
    order_id: uuid.UUID,
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    orders: SalesOrderDependency,
) -> PurchaseOrderReply:
    return _order_reply(actor, parties, orders, procurement.close(actor, order_id))


# -- receipts --------------------------------------------------------------------------


@router.post("/receipts", response_model=ReceiptReply, status_code=201, summary="Receive goods")
def receive(
    payload: ReceiptContract,
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
) -> ReceiptReply:
    """Record what arrived and post the inventory accrual."""
    receipt = procurement.receive(
        actor,
        ReceiptDraft(
            purchase_order_id=payload.purchase_order_id,
            lines=[
                ReceiptLineDraft(
                    purchase_order_line_id=line.purchase_order_line_id,
                    quantity_received=line.quantity_received,
                    quantity_rejected=line.quantity_rejected,
                    rejection_reason=line.rejection_reason,
                    batch=line.batch,
                    expiry=line.expiry,
                    serials=line.serials,
                    manufacturer=line.manufacturer,
                )
                for line in payload.lines
            ],
            received_on=payload.received_on,
            warehouse_id=payload.warehouse_id,
            delivery_reference=payload.delivery_reference,
            inspection_pending=payload.inspection_pending,
            notes=payload.notes,
        ),
    )
    return _receipt_reply(actor, parties, procurement, receipt)


@router.get("/receipts", response_model=list[ReceiptReply], summary="List goods receipts")
def list_receipts(
    actor: ActorDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    order_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[ReceiptReply]:
    return [
        _receipt_reply(actor, parties, procurement, item)
        for item in procurement.receipts(actor, order_id=order_id, limit=limit)
    ]


# -- supplier invoices -----------------------------------------------------------------


@router.get("/tolerance", response_model=ToleranceReply, summary="Matching tolerance")
def read_tolerance(actor: ActorDependency, billing: SupplierBillingDependency) -> ToleranceReply:
    tolerance = billing.tolerance(actor)
    return ToleranceReply(absolute=tolerance.absolute, rate=tolerance.rate)


@router.put("/tolerance", response_model=ToleranceReply, summary="Set the matching tolerance")
def set_tolerance(
    payload: ToleranceContract, actor: ActorDependency, billing: SupplierBillingDependency
) -> ToleranceReply:
    tolerance = billing.set_tolerance(actor, absolute=payload.absolute, rate=payload.rate)
    return ToleranceReply(absolute=tolerance.absolute, rate=tolerance.rate)


@router.post(
    "/invoices",
    response_model=SupplierInvoiceReply,
    status_code=201,
    summary="Record a supplier invoice",
)
def record_invoice(
    payload: InvoiceContract,
    actor: ActorDependency,
    billing: SupplierBillingDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
) -> SupplierInvoiceReply:
    """Recorded and matched three ways. Posted separately, once every variance is answered."""
    invoice = billing.record(actor, _invoice_draft(payload))
    return _invoice_reply(actor, parties, procurement, invoice)


def _invoice_draft(payload: InvoiceContract) -> InvoiceDraft:
    return InvoiceDraft(
        purchase_order_id=payload.purchase_order_id,
        supplier_reference=payload.supplier_reference,
        invoiced_on=payload.invoiced_on,
        lines=[
            InvoiceLineDraft(
                purchase_order_line_id=line.purchase_order_line_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                recoverable_tax=line.recoverable_tax,
                non_recoverable_tax=line.non_recoverable_tax,
                description=line.description,
            )
            for line in payload.lines
        ],
        due_on=payload.due_on,
        evidence_attachment_id=payload.evidence_attachment_id,
        notes=payload.notes,
    )


@router.get(
    "/invoices", response_model=list[SupplierInvoiceReply], summary="List supplier invoices"
)
def list_invoices(
    actor: ActorDependency,
    billing: SupplierBillingDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
    state: str | None = None,
    supplier_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[SupplierInvoiceReply]:
    return [
        _invoice_reply(actor, parties, procurement, item)
        for item in billing.invoices(actor, state=state, supplier_id=supplier_id, limit=limit)
    ]


@router.get(
    "/invoices/{invoice_id}", response_model=SupplierInvoiceReply, summary="Read an invoice"
)
def read_invoice(
    invoice_id: uuid.UUID,
    actor: ActorDependency,
    billing: SupplierBillingDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
) -> SupplierInvoiceReply:
    return _invoice_reply(actor, parties, procurement, billing.require(actor, invoice_id))


@router.put("/invoices/{invoice_id}", response_model=SupplierInvoiceReply, summary="Amend a draft")
def amend_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceContract,
    actor: ActorDependency,
    billing: SupplierBillingDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
) -> SupplierInvoiceReply:
    invoice = billing.amend(actor, invoice_id, _invoice_draft(payload))
    return _invoice_reply(actor, parties, procurement, invoice)


@router.post("/invoices/{invoice_id}/post", response_model=SupplierInvoiceReply, summary="Post")
def post_invoice(
    invoice_id: uuid.UUID,
    actor: ActorDependency,
    billing: SupplierBillingDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
) -> SupplierInvoiceReply:
    """Raise the payable through the ledger."""
    return _invoice_reply(actor, parties, procurement, billing.post(actor, invoice_id))


@router.post("/invoices/{invoice_id}/cancel", response_model=SupplierInvoiceReply, summary="Cancel")
def cancel_invoice(
    invoice_id: uuid.UUID,
    payload: ReasonContract,
    actor: ActorDependency,
    billing: SupplierBillingDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
) -> SupplierInvoiceReply:
    return _invoice_reply(
        actor, parties, procurement, billing.cancel(actor, invoice_id, reason=payload.reason)
    )


@router.get(
    "/variances", response_model=list[VarianceReply], summary="Variances awaiting a decision"
)
def pending_variances(
    actor: ActorDependency, billing: SupplierBillingDependency
) -> list[VarianceReply]:
    return [VarianceReply.model_validate(item) for item in billing.pending_variances(actor)]


@router.post(
    "/variances/{variance_id}/decide",
    response_model=SupplierInvoiceReply,
    summary="Approve or reject a variance",
)
def decide_variance(
    variance_id: uuid.UUID,
    payload: VarianceDecisionContract,
    actor: ActorDependency,
    billing: SupplierBillingDependency,
    procurement: ProcurementDependency,
    parties: PartyDependency,
) -> SupplierInvoiceReply:
    invoice = billing.decide_variance(
        actor, variance_id, approve=payload.approve, reason=payload.reason
    )
    return _invoice_reply(actor, parties, procurement, invoice)


@router.post(
    "/credit-notes",
    response_model=CreditNoteReply,
    status_code=201,
    summary="Record a supplier credit note",
)
def credit_note(
    payload: CreditNoteContract, actor: ActorDependency, billing: SupplierBillingDependency
) -> CreditNoteReply:
    note = billing.credit(
        actor,
        CreditNoteDraft(
            invoice_id=payload.invoice_id,
            supplier_reference=payload.supplier_reference,
            amount=payload.amount,
            reason=payload.reason,
            credited_on=payload.credited_on,
        ),
    )
    return CreditNoteReply.model_validate(note)


# -- payables --------------------------------------------------------------------------


@router.get("/payables", response_model=PayablesRegisterReply, summary="The payables register")
def register(
    actor: ActorDependency,
    payables: PayablesDependency,
    as_at: date | None = None,
    supplier_id: uuid.UUID | None = None,
) -> PayablesRegisterReply:
    """What is owed, aged by due date, with the amounts a payment screen offers."""
    report = payables.register(actor, as_at=as_at)
    rows = [row for row in report.rows if supplier_id is None or row.supplier_id == supplier_id]
    due = sum((row.outstanding for row in rows), report.total_outstanding * 0)
    return PayablesRegisterReply(
        as_at=report.as_at,
        rows=[PayableRowReply.model_validate(row) for row in rows],
        total_outstanding=report.total_outstanding if supplier_id is None else due,
        by_bucket=report.by_bucket,
        by_supplier=report.by_supplier,
        # Presented at the two places money is written at, not the four it is held at:
        # a suggestion taken into the amount field is what the person will pay.
        suggested_amounts=[
            {"amount": str(amount), "exact": index == 0}
            for index, amount in enumerate(suggested_tenders(due, precision=2))
        ],
    )


@router.post("/payments", response_model=PaymentReply, status_code=201, summary="Pay a supplier")
def pay(
    payload: PaymentContract,
    actor: ActorDependency,
    payables: PayablesDependency,
    parties: PartyDependency,
) -> PaymentReply:
    """Reduces the payable and the cash. Posts no expense: the purchase was recognised at
    receipt."""
    payment = payables.pay(
        actor,
        PaymentDraft(
            supplier_id=payload.supplier_id,
            amount=payload.amount,
            method=payload.method,
            withheld=payload.withheld,
            paid_on=payload.paid_on,
            reference=payload.reference,
            evidence_attachment_id=payload.evidence_attachment_id,
            invoice_ids=payload.invoice_ids,
            notes=payload.notes,
        ),
    )
    return _payment_reply(actor, parties, payment)


@router.get("/payments", response_model=list[PaymentReply], summary="List supplier payments")
def list_payments(
    actor: ActorDependency,
    payables: PayablesDependency,
    parties: PartyDependency,
    supplier_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[PaymentReply]:
    return [
        _payment_reply(actor, parties, item)
        for item in payables.payments(actor, supplier_id=supplier_id, limit=limit)
    ]


@router.post(
    "/payables/invoices/{invoice_id}/priority",
    response_model=PayableRowReply | None,
    summary="Pay first, or hold back",
)
def set_priority(
    invoice_id: uuid.UUID,
    body: PriorityContract,
    actor: ActorDependency,
    payables: PayablesDependency,
) -> PayableRowReply | None:
    payables.set_priority(actor, invoice_id, body.priority, reason=body.reason)
    found = [row for row in payables.register(actor).rows if row.invoice_id == invoice_id]
    return PayableRowReply.model_validate(found[0]) if found else None


@router.get(
    "/payables/suppliers/{supplier_id}/statement",
    response_model=PartyStatementReply,
    summary="Supplier statement",
)
def supplier_statement(
    supplier_id: uuid.UUID,
    actor: ActorDependency,
    payables: PayablesDependency,
    start: date,
    end: date | None = None,
) -> PartyStatementReply:
    found = payables.statement(actor, supplier_id, start=start, end=end)
    return PartyStatementReply(
        party_id=found.supplier.id,
        party=found.supplier.name,
        starts_on=found.starts_on,
        ends_on=found.ends_on,
        opening=found.opening,
        closing=found.closing,
        entries=[StatementEntryReply.model_validate(item) for item in found.entries],
    )


def _batch(db: SessionDependency, batch) -> BatchReply:  # type: ignore[no-untyped-def]
    names = Names(db)
    return BatchReply(
        id=batch.id,
        number=batch.number,
        state=batch.state,
        account=names.account(batch.financial_account_fk),
        pay_on=batch.pay_on,
        items=batch.items,
        total=batch.total,
        prepared_by=names.user(batch.prepared_by_id),
        approved_by=names.user(batch.approved_by_id),
        rejection_reason=batch.rejection_reason,
    )


@router.get("/payables/batches", response_model=list[BatchReply], summary="Payment batches")
def batches(
    actor: ActorDependency, payables: PayablesDependency, db: SessionDependency
) -> list[BatchReply]:
    return [_batch(db, item) for item in payables.batches(actor)]


@router.post(
    "/payables/batches", response_model=BatchReply, status_code=201, summary="Prepare a batch"
)
def prepare_batch(
    body: BatchContract,
    actor: ActorDependency,
    payables: PayablesDependency,
    db: SessionDependency,
) -> BatchReply:
    batch = payables.prepare_batch(
        actor,
        financial_account_id=body.financial_account_id,
        items=[
            BatchItem(
                supplier_id=item.supplier_id,
                amount=item.amount,
                invoice_ids=item.invoice_ids,
                reference=item.reference,
            )
            for item in body.items
        ],
        pay_on=body.pay_on,
    )
    return _batch(db, batch)


@router.post(
    "/payables/batches/{batch_id}/approve", response_model=BatchReply, summary="Approve and pay"
)
def approve_batch(
    batch_id: uuid.UUID,
    actor: ActorDependency,
    payables: PayablesDependency,
    db: SessionDependency,
) -> BatchReply:
    return _batch(db, payables.approve_batch(actor, batch_id))


@router.post(
    "/payables/batches/{batch_id}/reject", response_model=BatchReply, summary="Reject a batch"
)
def reject_batch(
    batch_id: uuid.UUID,
    body: ReasonContract,
    actor: ActorDependency,
    payables: PayablesDependency,
    db: SessionDependency,
) -> BatchReply:
    return _batch(db, payables.reject_batch(actor, batch_id, reason=body.reason))


__all__ = ["router"]
