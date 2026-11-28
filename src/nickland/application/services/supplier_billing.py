"""Supplier invoices, the three-way match, variance approval, posting and credit notes.

PRD §6.8 and §30. The invoice is matched against the order and the receipts before it
is posted: a quantity or a price that moved between the three documents is a variance,
recorded with the money at stake and approved by somebody entitled to, and the
payable is raised only once every variance has been answered.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.orders import SalesOrderService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.procurement import ProcurementService
from nickland.application.services.stock import StockEngine
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.inventory import MovementType
from nickland.domain.ledger import EntryLine, JournalSource, Side, SubledgerKind
from nickland.domain.money import WORKING_PRECISION, ZERO, allocate, divide, money, multiply
from nickland.domain.orders import (
    GOODS_RECEIVED_NOT_INVOICED,
    RECOVERABLE_INPUT_TAX,
    TRADE_PAYABLES,
    MatchTolerance,
    PurchaseOrderState,
    SupplierInvoiceState,
    VarianceKind,
    VarianceState,
    assert_supplier_invoice_transition,
    match_line,
)
from nickland.domain.parties import PaymentTermKind, PaymentTerms
from nickland.persistence.base import as_dict
from nickland.persistence.models import Business, Party
from nickland.persistence.order_models import (
    GoodsReceiptLine,
    MatchingPolicy,
    MatchVariance,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrderLine,
    SupplierCreditNote,
    SupplierInvoice,
    SupplierInvoiceLine,
)
from nickland.platform.clock import Clock, SystemClock, business_date


@dataclass(slots=True)
class InvoiceLineDraft:
    """One line as the supplier printed it."""

    purchase_order_line_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal
    recoverable_tax: Decimal = ZERO
    non_recoverable_tax: Decimal = ZERO
    description: str | None = None


@dataclass(slots=True)
class InvoiceDraft:
    purchase_order_id: uuid.UUID
    supplier_reference: str
    invoiced_on: date
    lines: list[InvoiceLineDraft] = field(default_factory=list)
    due_on: date | None = None
    """Left empty, the supplier's payment terms decide it."""
    evidence_attachment_id: uuid.UUID | None = None
    notes: str | None = None


@dataclass(slots=True)
class CreditNoteDraft:
    invoice_id: uuid.UUID
    supplier_reference: str
    amount: Decimal
    reason: str
    credited_on: date | None = None


class SupplierBillingService:
    """Invoices and credit notes from suppliers, matched and posted."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._procurement = ProcurementService(session, clock=self._clock)
        self._orders = SalesOrderService(session, clock=self._clock)

    # -- tolerance --------------------------------------------------------------------

    def tolerance(self, actor: AnyActor) -> MatchTolerance:
        row = self._db.scalar(
            select(MatchingPolicy).where(MatchingPolicy.business_id == _business_of(actor))
        )
        if row is None:
            return MatchTolerance()
        return MatchTolerance(absolute=row.absolute_tolerance, rate=row.rate_tolerance)

    def set_tolerance(
        self, actor: AnyActor, *, absolute: Decimal = ZERO, rate: Decimal = ZERO
    ) -> MatchTolerance:
        """How far an invoiced price may stray before it is a variance. A decision to
        pay small differences without asking, so taken by whoever approves variances."""
        actor.require("match_variance.approve")
        if absolute < ZERO or rate < ZERO or rate >= Decimal("1"):
            raise ValidationError(
                "A tolerance is zero or above, and a rate is below one.", field="tolerance"
            )
        business_id = _business_of(actor)
        row = self._db.scalar(
            select(MatchingPolicy).where(MatchingPolicy.business_id == business_id)
        )
        before = as_dict(row) if row else None
        if row is None:
            row = MatchingPolicy(business_id=business_id)
            self._db.add(row)
        row.absolute_tolerance = money(absolute)
        row.rate_tolerance = rate
        self._db.flush()
        self._audit.record(
            actor,
            "matching_tolerance_set",
            entity_type="matching_policy",
            entity_id=row.id,
            before=before,
            after=as_dict(row),
        )
        return MatchTolerance(absolute=row.absolute_tolerance, rate=row.rate_tolerance)

    # -- recording --------------------------------------------------------------------

    def record(self, actor: AnyActor, draft: InvoiceDraft) -> SupplierInvoice:
        """Record the supplier's invoice and match it. Nothing is posted yet."""
        actor.require("supplier_invoice.manage")
        order = self._procurement.require(actor, draft.purchase_order_id)
        if PurchaseOrderState(order.state) in (
            PurchaseOrderState.DRAFT,
            PurchaseOrderState.APPROVED,
            PurchaseOrderState.CANCELLED,
        ):
            raise ConflictError(
                f"Purchase order {order.number or 'in draft'} has not been issued; an "
                "invoice is recorded against an issued order.",
                state=order.state,
            )
        reference = draft.supplier_reference.strip()
        if not reference:
            raise ValidationError(
                "The supplier's invoice number is required.", field="supplier_reference"
            )
        if not draft.lines:
            raise ValidationError("An invoice carries at least one line.", field="lines")
        duplicate = self._db.scalar(
            select(SupplierInvoice.id).where(
                SupplierInvoice.business_id == order.business_id,
                SupplierInvoice.supplier_fk == order.supplier_fk,
                SupplierInvoice.supplier_reference == reference,
            )
        )
        if duplicate is not None:
            raise ConflictError(
                f"Invoice {reference} from this supplier is already on record. A supplier's "
                "invoice is recorded once; paying it twice is the mistake this refusal prevents.",
                supplier_reference=reference,
            )

        supplier = self._db.get(Party, order.supplier_fk)
        due_on = draft.due_on or self._due_date(supplier, draft.invoiced_on)
        invoice = SupplierInvoice(
            business_id=order.business_id,
            branch_id=actor.branch_id,
            supplier_reference=reference,
            supplier_fk=order.supplier_fk,
            purchase_order_fk=order.id,
            state=SupplierInvoiceState.DRAFT.value,
            invoiced_on=draft.invoiced_on,
            due_on=due_on,
            currency_code=order.currency_code,
            exchange_rate=order.exchange_rate,
            evidence_attachment_fk=draft.evidence_attachment_id,
            notes=(draft.notes or "").strip() or None,
        )
        for position, item in enumerate(draft.lines, start=1):
            line = next(
                (
                    candidate
                    for candidate in order.lines
                    if candidate.id == item.purchase_order_line_id
                ),
                None,
            )
            if line is None:
                raise NotFoundError(f"Invoice line {position}: that line is not on this order.")
            if item.quantity <= ZERO:
                raise ValidationError(
                    f"Line {position}: the quantity is above zero.", field="quantity"
                )
            if (
                item.unit_cost < ZERO
                or item.recoverable_tax < ZERO
                or item.non_recoverable_tax < ZERO
            ):
                raise ValidationError(
                    f"Line {position}: amounts are not negative.", field="unit_cost"
                )
            invoice.lines.append(
                SupplierInvoiceLine(
                    business_id=order.business_id,
                    position=position,
                    purchase_order_line_fk=line.id,
                    description=(item.description or line.description).strip(),
                    quantity=item.quantity,
                    unit_cost=item.unit_cost,
                    line_total=money(item.unit_cost * item.quantity),
                    recoverable_tax=money(item.recoverable_tax),
                    non_recoverable_tax=money(item.non_recoverable_tax),
                )
            )
        self._total(invoice)
        self._db.add(invoice)
        self._db.flush()
        self._match(actor, invoice, order)
        self._audit.record(
            actor,
            "supplier_invoice_recorded",
            entity_type="supplier_invoice",
            entity_id=invoice.id,
            entity_label=f"{supplier.name if supplier else 'supplier'} {reference}",
            after={
                "total": str(invoice.total),
                "state": invoice.state,
                "variances": len(invoice.variances),
            },
        )
        return invoice

    def _total(self, invoice: SupplierInvoice) -> None:
        invoice.net_total = money(sum((line.line_total for line in invoice.lines), ZERO))
        invoice.recoverable_tax_total = money(
            sum((line.recoverable_tax for line in invoice.lines), ZERO)
        )
        invoice.non_recoverable_tax_total = money(
            sum((line.non_recoverable_tax for line in invoice.lines), ZERO)
        )
        invoice.total = money(
            invoice.net_total + invoice.recoverable_tax_total + invoice.non_recoverable_tax_total
        )
        invoice.base_total = money(multiply(invoice.total, invoice.exchange_rate))

    def _match(self, actor: AnyActor, invoice: SupplierInvoice, order: PurchaseOrder) -> None:
        """The three-way match: every line against its order line and the receipts."""
        tolerance = self.tolerance(actor)
        invoice.variances.clear()
        for line in invoice.lines:
            ordered = self._db.get(PurchaseOrderLine, line.purchase_order_line_fk)
            assert ordered is not None
            previously_invoiced = ordered.quantity_invoiced
            for found in match_line(
                ordered_quantity=money(ordered.quantity_ordered - previously_invoiced),
                accepted_quantity=money(ordered.quantity_accepted - previously_invoiced),
                ordered_unit_cost=ordered.net_unit_cost,
                invoiced_quantity=line.quantity,
                invoiced_unit_cost=line.unit_cost,
                tolerance=tolerance,
            ):
                invoice.variances.append(
                    MatchVariance(
                        business_id=invoice.business_id,
                        invoice_line_fk=line.id,
                        kind=found.kind.value,
                        expected=found.expected,
                        actual=found.actual,
                        amount_at_stake=money(
                            multiply(
                                found.amount_at_stake(
                                    quantity=line.quantity, unit_cost=line.unit_cost
                                ),
                                invoice.exchange_rate,
                            )
                        ),
                        state=VarianceState.PENDING.value,
                    )
                )
        invoice.state = (
            SupplierInvoiceState.VARIANCE.value
            if invoice.variances
            else SupplierInvoiceState.MATCHED.value
        )
        self._db.flush()

    # -- variances --------------------------------------------------------------------

    def decide_variance(
        self,
        actor: AnyActor,
        variance_id: uuid.UUID,
        *,
        approve: bool,
        reason: str,
    ) -> SupplierInvoice:
        """Approve or reject one variance. The invoice matches once none is pending."""
        actor.require("match_variance.approve")
        variance = self._db.get(MatchVariance, variance_id)
        if variance is None or variance.business_id != _business_of(actor):
            raise NotFoundError("Variance not found.")
        if variance.state != VarianceState.PENDING.value:
            raise ConflictError(f"This variance is already {variance.state}.")
        if not reason.strip():
            raise ValidationError("A decision on a variance records why.", field="reason")
        invoice = variance.invoice
        variance.state = (VarianceState.APPROVED if approve else VarianceState.REJECTED).value
        variance.decided_by_id = actor.user_id
        variance.decided_at = self._clock.now()
        variance.reason = reason.strip()
        if not approve:
            assert_supplier_invoice_transition(
                SupplierInvoiceState(invoice.state), SupplierInvoiceState.DRAFT
            )
            invoice.state = SupplierInvoiceState.DRAFT.value
        elif all(item.state == VarianceState.APPROVED.value for item in invoice.variances):
            assert_supplier_invoice_transition(
                SupplierInvoiceState(invoice.state), SupplierInvoiceState.MATCHED
            )
            invoice.state = SupplierInvoiceState.MATCHED.value
        self._db.flush()
        self._audit.record(
            actor,
            "match_variance_approved" if approve else "match_variance_rejected",
            entity_type="supplier_invoice",
            entity_id=invoice.id,
            entity_label=invoice.supplier_reference,
            reason=reason,
            after={
                "kind": variance.kind,
                "expected": str(variance.expected),
                "actual": str(variance.actual),
                "amount_at_stake": str(variance.amount_at_stake),
            },
        )
        return invoice

    def amend(self, actor: AnyActor, invoice_id: uuid.UUID, draft: InvoiceDraft) -> SupplierInvoice:
        """Re-record a draft invoice's lines after a rejected variance, and match again."""
        actor.require("supplier_invoice.manage")
        invoice = self.require(actor, invoice_id)
        if invoice.state != SupplierInvoiceState.DRAFT.value:
            raise ConflictError("Only a draft invoice can be amended.", state=invoice.state)
        order = self._procurement.require(actor, invoice.purchase_order_fk)
        # The variances go first, through the session, so the database's own cascade
        # from the lines does not remove rows the session still expects to delete.
        invoice.variances.clear()
        self._db.flush()
        invoice.lines.clear()
        self._db.flush()
        for position, item in enumerate(draft.lines, start=1):
            line = next(
                (
                    candidate
                    for candidate in order.lines
                    if candidate.id == item.purchase_order_line_id
                ),
                None,
            )
            if line is None:
                raise NotFoundError(f"Invoice line {position}: that line is not on this order.")
            invoice.lines.append(
                SupplierInvoiceLine(
                    business_id=order.business_id,
                    position=position,
                    purchase_order_line_fk=line.id,
                    description=(item.description or line.description).strip(),
                    quantity=item.quantity,
                    unit_cost=item.unit_cost,
                    line_total=money(item.unit_cost * item.quantity),
                    recoverable_tax=money(item.recoverable_tax),
                    non_recoverable_tax=money(item.non_recoverable_tax),
                )
            )
        invoice.invoiced_on = draft.invoiced_on
        if draft.due_on:
            invoice.due_on = draft.due_on
        self._total(invoice)
        self._db.flush()
        self._match(actor, invoice, order)
        return invoice

    # -- posting ----------------------------------------------------------------------

    def post(self, actor: AnyActor, invoice_id: uuid.UUID) -> SupplierInvoice:
        """Raise the payable. The accrual from receipt is replaced by the supplier's figure.

        Debits: the accrual at order cost for what was received; inventory for an
        approved price variance and for non-recoverable tax, which capitalises into
        landed cost; recoverable input tax. Credit: trade payables, on the supplier's
        subledger, for the invoice total. No expense: PRD §30.
        """
        actor.require("supplier_invoice.manage")
        invoice = self.require(actor, invoice_id)
        assert_supplier_invoice_transition(
            SupplierInvoiceState(invoice.state), SupplierInvoiceState.POSTED
        )
        order = self._procurement.require(actor, invoice.purchase_order_fk)
        supplier = self._db.get(Party, invoice.supplier_fk)
        rate = invoice.exchange_rate

        allocated = OrganisationService(self._db, clock=self._clock).allocate_number(
            actor, "supplier_invoice", branch_id=actor.branch_id
        )
        invoice.number = allocated.number
        operation = StockEngine(self._db, clock=self._clock).begin(
            actor,
            MovementType.REVALUATION,
            occurred_on=invoice.invoiced_on,
            source_type="supplier_invoice",
            source_id=invoice.id,
            reference=invoice.number,
        )

        # Non-recoverable tax capitalises into landed cost, line by line; the parts are
        # apportioned so they sum to the invoice's own figure exactly.
        non_recoverable = money(multiply(invoice.non_recoverable_tax_total, rate))
        capitalised = (
            allocate(
                non_recoverable,
                [money(line.non_recoverable_tax) for line in invoice.lines],
                WORKING_PRECISION,
            )
            if non_recoverable > ZERO
            else [ZERO] * len(invoice.lines)
        )
        accrual = ZERO
        for line, tax in zip(invoice.lines, capitalised, strict=True):
            ordered = self._db.get(PurchaseOrderLine, line.purchase_order_line_fk)
            assert ordered is not None
            at_order_cost = money(multiply(ordered.net_unit_cost * line.quantity, rate))
            at_invoice_cost = money(multiply(line.line_total, rate))
            accrual = money(accrual + at_order_cost)
            # The approved price variance and the capitalised tax follow the goods:
            # into their layers where still held, to cost of sales where already gone.
            operation.revalue(
                self._receipt_lines_of(ordered), money(at_invoice_cost - at_order_cost + tax)
            )
            ordered.quantity_invoiced = ordered.quantity_invoiced + line.quantity
            # The sales order line now knows what its goods actually cost.
            if ordered.sales_order_line_fk is not None:
                sales_line = self._db.get(SalesOrderLine, ordered.sales_order_line_fk)
                if sales_line is not None:
                    landed_unit = money(
                        multiply(line.unit_cost, rate)
                        + divide(multiply(line.non_recoverable_tax, rate), line.quantity)
                    )
                    self._orders.record_actual_cost(sales_line, landed_unit)

        recoverable = money(multiply(invoice.recoverable_tax_total, rate))
        if accrual > ZERO:
            operation.extra.append(
                EntryLine(
                    account_code=GOODS_RECEIVED_NOT_INVOICED,
                    side=Side.DEBIT,
                    amount=accrual,
                    narration=f"{order.number} accrual cleared",
                )
            )
        if recoverable > ZERO:
            # Recoverable input tax is a claim on the authority, and the account is
            # the authority subledger's control account, so the line names which one.
            authority_id = self._vat_authority(invoice.business_id)
            operation.extra.append(
                EntryLine(
                    account_code=RECOVERABLE_INPUT_TAX,
                    side=Side.DEBIT,
                    amount=recoverable,
                    narration="Recoverable input tax",
                    party_id=authority_id,
                    subledger=SubledgerKind.AUTHORITY,
                )
            )
        operation.extra.append(
            EntryLine(
                account_code=TRADE_PAYABLES,
                side=Side.CREDIT,
                amount=invoice.base_total,
                narration=(
                    f"{supplier.name if supplier else 'Supplier'} {invoice.supplier_reference}"
                ),
                party_id=invoice.supplier_fk,
                subledger=SubledgerKind.SUPPLIER,
            )
        )
        entry = operation.finish(
            f"Supplier invoice {invoice.supplier_reference} against {order.number}",
            source=JournalSource.PURCHASES,
            idempotency_key=f"supplier_invoice:{invoice.id}",
            attachment_ids=(
                [str(invoice.evidence_attachment_fk)] if invoice.evidence_attachment_fk else []
            ),
        )
        assert entry is not None
        invoice.journal_entry_fk = entry.id
        invoice.state = SupplierInvoiceState.POSTED.value
        invoice.posted_by_id = actor.user_id
        invoice.posted_at = self._clock.now()
        self._db.flush()
        self._audit.record(
            actor,
            "supplier_invoice_posted",
            entity_type="supplier_invoice",
            entity_id=invoice.id,
            entity_label=invoice.number,
            after={
                "payable": str(invoice.base_total),
                "journal": str(entry.id),
            },
        )
        return invoice

    def cancel(self, actor: AnyActor, invoice_id: uuid.UUID, *, reason: str) -> SupplierInvoice:
        actor.require("supplier_invoice.manage")
        invoice = self.require(actor, invoice_id)
        if not reason.strip():
            raise ValidationError("A cancellation records why.", field="reason")
        assert_supplier_invoice_transition(
            SupplierInvoiceState(invoice.state), SupplierInvoiceState.CANCELLED
        )
        invoice.state = SupplierInvoiceState.CANCELLED.value
        invoice.cancelled_at = self._clock.now()
        invoice.cancellation_reason = reason.strip()
        self._db.flush()
        self._audit.record(
            actor,
            "supplier_invoice_cancelled",
            entity_type="supplier_invoice",
            entity_id=invoice.id,
            entity_label=invoice.supplier_reference,
            reason=reason,
        )
        return invoice

    # -- credit notes -----------------------------------------------------------------

    def credit(self, actor: AnyActor, draft: CreditNoteDraft) -> SupplierCreditNote:
        """A supplier's credit against a posted invoice: Dr payables, Cr inventory."""
        actor.require("supplier_invoice.manage")
        invoice = self.require(actor, draft.invoice_id)
        if invoice.state not in (
            SupplierInvoiceState.POSTED.value,
            SupplierInvoiceState.PARTIALLY_PAID.value,
        ):
            raise ConflictError(
                "A credit note is recorded against a posted invoice with something still "
                "owing on it.",
                state=invoice.state,
            )
        if draft.amount <= ZERO:
            raise ValidationError("A credit note carries an amount above zero.", field="amount")
        outstanding = money(invoice.base_total - invoice.amount_paid - invoice.amount_credited)
        base_amount = money(multiply(draft.amount, invoice.exchange_rate))
        if base_amount > outstanding:
            raise ConflictError(
                f"Only {outstanding} is outstanding on {invoice.supplier_reference}; a credit "
                "beyond it is a refund, which is recorded as money received.",
                outstanding=str(outstanding),
            )
        if not draft.reason.strip():
            raise ValidationError("A credit note records why.", field="reason")
        reference = draft.supplier_reference.strip()
        if not reference:
            raise ValidationError(
                "The supplier's credit note number is required.", field="supplier_reference"
            )

        supplier = self._db.get(Party, invoice.supplier_fk)
        note = SupplierCreditNote(
            business_id=invoice.business_id,
            supplier_reference=reference,
            supplier_fk=invoice.supplier_fk,
            invoice_fk=invoice.id,
            credited_on=draft.credited_on or self._today(actor),
            reason=draft.reason.strip(),
            amount=money(draft.amount),
            base_amount=base_amount,
            recorded_by_id=actor.user_id,
        )
        self._db.add(note)
        self._db.flush()
        operation = StockEngine(self._db, clock=self._clock).begin(
            actor,
            MovementType.REVALUATION,
            occurred_on=note.credited_on,
            source_type="supplier_credit_note",
            source_id=note.id,
            reference=reference,
        )
        # The credit reduces what the invoiced goods cost, apportioned across the
        # invoice's lines by value, and follows the goods as a price variance does.
        weights = [
            money(multiply(line.line_total, invoice.exchange_rate)) for line in invoice.lines
        ]
        if sum(weights, ZERO) <= ZERO:
            weights = [line.quantity for line in invoice.lines]
        for line, share in zip(
            invoice.lines, allocate(base_amount, weights, WORKING_PRECISION), strict=True
        ):
            ordered = self._db.get(PurchaseOrderLine, line.purchase_order_line_fk)
            assert ordered is not None
            operation.revalue(self._receipt_lines_of(ordered), -share)
        operation.extra.append(
            EntryLine(
                account_code=TRADE_PAYABLES,
                side=Side.DEBIT,
                amount=base_amount,
                party_id=invoice.supplier_fk,
                subledger=SubledgerKind.SUPPLIER,
                narration=f"{supplier.name if supplier else 'Supplier'} credit",
            )
        )
        entry = operation.finish(
            f"Supplier credit {reference} against {invoice.supplier_reference}",
            source=JournalSource.PURCHASES,
            idempotency_key=f"supplier_credit_note:{note.id}",
        )
        assert entry is not None
        note.journal_entry_fk = entry.id
        invoice.amount_credited = money(invoice.amount_credited + base_amount)
        self._settle_state(invoice)
        self._db.flush()
        self._audit.record(
            actor,
            "supplier_credit_note_recorded",
            entity_type="supplier_invoice",
            entity_id=invoice.id,
            entity_label=invoice.supplier_reference,
            reason=note.reason,
            after={"credit": str(base_amount)},
        )
        return note

    # -- reading ----------------------------------------------------------------------

    def require(self, actor: AnyActor, invoice_id: uuid.UUID) -> SupplierInvoice:
        actor.require("supplier_invoice.view")
        invoice = self._db.get(SupplierInvoice, invoice_id)
        if invoice is None or invoice.business_id != _business_of(actor):
            raise NotFoundError("Supplier invoice not found.")
        return invoice

    def invoices(
        self,
        actor: AnyActor,
        *,
        state: str | None = None,
        supplier_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[SupplierInvoice]:
        actor.require("supplier_invoice.view")
        query = select(SupplierInvoice).where(SupplierInvoice.business_id == _business_of(actor))
        if state:
            query = query.where(SupplierInvoice.state == state)
        if supplier_id:
            query = query.where(SupplierInvoice.supplier_fk == supplier_id)
        return list(
            self._db.scalars(
                query.order_by(
                    SupplierInvoice.invoiced_on.desc(), SupplierInvoice.created_at.desc()
                ).limit(max(min(limit, 200), 1))
            ).all()
        )

    def pending_variances(self, actor: AnyActor) -> list[MatchVariance]:
        actor.require("supplier_invoice.view")
        return list(
            self._db.scalars(
                select(MatchVariance)
                .where(
                    MatchVariance.business_id == _business_of(actor),
                    MatchVariance.state == VarianceState.PENDING.value,
                )
                .order_by(MatchVariance.created_at)
            ).all()
        )

    # -- helpers shared with payables -------------------------------------------------

    def _receipt_lines_of(self, ordered: PurchaseOrderLine) -> list[uuid.UUID]:
        return list(
            self._db.scalars(
                select(GoodsReceiptLine.id).where(
                    GoodsReceiptLine.purchase_order_line_fk == ordered.id
                )
            ).all()
        )

    def _settle_state(self, invoice: SupplierInvoice) -> None:
        """Posted, partially paid or paid, from what has been paid and credited."""
        if invoice.state not in (
            SupplierInvoiceState.POSTED.value,
            SupplierInvoiceState.PARTIALLY_PAID.value,
            SupplierInvoiceState.PAID.value,
        ):
            return
        settled = money(invoice.amount_paid + invoice.amount_credited)
        if settled >= invoice.base_total:
            invoice.state = SupplierInvoiceState.PAID.value
        elif settled > ZERO:
            invoice.state = SupplierInvoiceState.PARTIALLY_PAID.value
        else:
            invoice.state = SupplierInvoiceState.POSTED.value

    def _vat_authority(self, business_id: uuid.UUID) -> uuid.UUID:
        """The authority recoverable input tax is claimed from: the VAT rule's."""
        from nickland.application.services.taxation import TaxService

        rule = TaxService(self._db, clock=self._clock).rule_by_code(business_id, "vat")
        if rule is None or rule.authority_fk is None:
            raise ValidationError(
                "Recoverable input tax cannot be posted until a VAT rule names the authority "
                "it is claimed from. Configure the rule under Taxation, or record the tax as "
                "non-recoverable.",
                field="recoverable_tax",
            )
        return rule.authority_fk

    def _due_date(self, supplier: Party | None, invoiced_on: date) -> date:
        if supplier is None or not supplier.payment_term_kind:
            return invoiced_on
        terms = PaymentTerms(
            kind=PaymentTermKind(supplier.payment_term_kind),
            days=supplier.payment_term_days or 0,
        )
        return terms.due_date(invoiced_on)

    def _today(self, actor: AnyActor) -> date:
        business = self._db.get(Business, actor.business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id


__all__ = [
    "CreditNoteDraft",
    "InvoiceDraft",
    "InvoiceLineDraft",
    "SupplierBillingService",
    "VarianceKind",
]
