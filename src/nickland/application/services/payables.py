"""Supplier payments and the payables register. PRD §6.8, §30.

A payment reduces the payable and the cash, and recognises no expense: the purchase
was recognised when the goods arrived. The register is derived from the invoices —
what each still owes, aged by its due date — and reconciles to the trade payables
control account because every movement on the one is a posting to the other.
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
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.supplier_billing import SupplierBillingService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.ledger import EntryLine, JournalSource, Side, SubledgerKind
from nickland.domain.money import ZERO, money
from nickland.domain.orders import (
    PAYABLE_STATES,
    PAYMENT_ACCOUNTS,
    TRADE_PAYABLES,
    WITHHOLDING_TAX_PAYABLE,
    AgeingBucket,
    PaymentMethod,
    ageing_bucket,
    allocate_payment,
)
from nickland.persistence.finance_models import PaymentBatch
from nickland.persistence.ledger_models import Account, JournalEntry, JournalLine
from nickland.persistence.locking import advisory_lock
from nickland.persistence.models import Business, Party
from nickland.persistence.order_models import (
    SupplierInvoice,
    SupplierPayment,
    SupplierPaymentAllocation,
)
from nickland.platform.clock import Clock, SystemClock, business_date


@dataclass(slots=True)
class PaymentDraft:
    supplier_id: uuid.UUID
    amount: Decimal
    """What leaves the account."""
    method: PaymentMethod | str = PaymentMethod.BANK
    withheld: Decimal = ZERO
    """Withholding tax retained from the supplier. Settles the payable but not the cash."""
    paid_on: date | None = None
    reference: str | None = None
    evidence_attachment_id: uuid.UUID | None = None
    invoice_ids: list[uuid.UUID] = field(default_factory=list)
    """Which invoices to settle, in order. Empty: the oldest outstanding first."""
    notes: str | None = None
    financial_account_id: uuid.UUID | None = None
    """The account it leaves. None: the method's default account."""


@dataclass(frozen=True, slots=True)
class PayableRow:
    invoice_id: uuid.UUID
    number: str | None
    supplier_id: uuid.UUID
    supplier_name: str
    supplier_reference: str
    invoiced_on: date
    due_on: date
    total: Decimal
    paid: Decimal
    credited: Decimal
    outstanding: Decimal
    bucket: AgeingBucket
    days_overdue: int


@dataclass(frozen=True, slots=True)
class PayablesRegister:
    as_at: date
    rows: list[PayableRow]
    total_outstanding: Decimal
    by_bucket: dict[str, Decimal]
    by_supplier: dict[str, Decimal]


@dataclass(slots=True)
class BatchItem:
    supplier_id: uuid.UUID
    amount: Decimal
    invoice_ids: list[uuid.UUID] = field(default_factory=list)
    reference: str | None = None


@dataclass(slots=True)
class StatementEntry:
    posted_on: date
    reference: str | None
    narration: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


@dataclass(slots=True)
class SupplierStatement:
    supplier: Party
    starts_on: date
    ends_on: date
    opening: Decimal
    entries: list[StatementEntry]
    closing: Decimal


class PayablesService:
    """Payments to suppliers, their allocation, and the register."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._billing = SupplierBillingService(session, clock=self._clock)

    def pay(self, actor: AnyActor, draft: PaymentDraft) -> SupplierPayment:
        """Record a payment, allocate it and post it.

        Debit trade payables for what the payment settles — the amount paid plus the
        tax withheld — credit the withholding liability for what was retained, and
        credit the cash account for what left. No expense line: PRD §30.
        """
        actor.require("payment.manage")
        business_id = _business_of(actor)
        supplier = self._db.get(Party, draft.supplier_id)
        if supplier is None or supplier.business_id != business_id or not supplier.is_supplier:
            raise NotFoundError("Supplier not found.")
        if draft.amount <= ZERO:
            raise ValidationError("A payment carries an amount above zero.", field="amount")
        if draft.withheld < ZERO:
            raise ValidationError("Withholding is not negative.", field="withheld")
        method = PaymentMethod(str(draft.method))
        settles = money(draft.amount + draft.withheld)

        # Two payments to one supplier allocate in turn, never against the same balance.
        advisory_lock(self._db, "supplier-payments", business_id, supplier.id)
        candidates = self._outstanding_for(business_id, supplier.id)
        if not draft.invoice_ids:
            # Paying on account: urgent invoices first, held ones not at all.
            candidates = sorted(
                (row for row in candidates if row[0].priority != "hold"),
                key=lambda row: (row[0].priority != "urgent", row[0].due_on),
            )
        if draft.invoice_ids:
            chosen = dict.fromkeys(draft.invoice_ids, True)
            candidates = [row for row in candidates if row[0].id in chosen]
            if len(candidates) != len(chosen):
                raise NotFoundError(
                    "One of the invoices named is not outstanding for this supplier."
                )
        allocations = allocate_payment(settles, [(invoice, owed) for invoice, owed in candidates])

        business = self._db.get(Business, business_id)
        payment = SupplierPayment(
            business_id=business_id,
            branch_id=actor.branch_id,
            supplier_fk=supplier.id,
            paid_on=draft.paid_on or self._today(actor),
            method=method.value,
            reference=(draft.reference or "").strip() or None,
            amount=money(draft.amount),
            withheld=money(draft.withheld),
            evidence_attachment_fk=draft.evidence_attachment_id,
            notes=(draft.notes or "").strip() or None,
            recorded_by_id=actor.user_id,
        )
        for invoice_obj, portion in allocations:
            invoice = invoice_obj
            assert isinstance(invoice, SupplierInvoice)
            payment.allocations.append(
                SupplierPaymentAllocation(
                    business_id=business_id, invoice_fk=invoice.id, amount=portion
                )
            )
            invoice.amount_paid = money(invoice.amount_paid + portion)
            self._billing._settle_state(invoice)
        self._db.add(payment)
        self._db.flush()

        allocated = OrganisationService(self._db, clock=self._clock).allocate_number(
            actor, "payment_voucher", branch_id=actor.branch_id
        )
        payment.number = allocated.number

        lines = [
            EntryLine(
                account_code=TRADE_PAYABLES,
                side=Side.DEBIT,
                amount=settles,
                party_id=supplier.id,
                subledger=SubledgerKind.SUPPLIER,
                narration=f"{supplier.name} paid",
            )
        ]
        if payment.withheld > ZERO:
            # The withholding liability is owed to the authority, and the account is
            # the authority subledger's control account, so the line names which one.
            lines.append(
                EntryLine(
                    account_code=WITHHOLDING_TAX_PAYABLE,
                    side=Side.CREDIT,
                    amount=payment.withheld,
                    narration=f"Withheld from {supplier.name}",
                    party_id=self._withholding_authority(business_id),
                    subledger=SubledgerKind.AUTHORITY,
                )
            )
        paid_from = PAYMENT_ACCOUNTS[method]
        if draft.financial_account_id is not None:
            from nickland.application.services.treasury import TreasuryService

            treasury = TreasuryService(self._db, clock=self._clock)
            paid_from = treasury.ledger_code(treasury.require(actor, draft.financial_account_id))
        lines.append(
            EntryLine(
                account_code=paid_from,
                side=Side.CREDIT,
                amount=payment.amount,
                narration=payment.reference or f"Payment {payment.number}",
            )
        )
        outcome = JournalService(self._db, clock=self._clock).post(
            actor,
            JournalDraft(
                posting_date=payment.paid_on,
                narration=f"Payment {payment.number} to {supplier.name}",
                lines=lines,
                source=JournalSource.PAYMENT,
                source_reference=payment.number,
                source_id=payment.id,
                idempotency_key=f"supplier_payment:{payment.id}",
                currency_code=business.base_currency if business else None,
                attachment_ids=(
                    [str(payment.evidence_attachment_fk)] if payment.evidence_attachment_fk else []
                ),
                branch_id=actor.branch_id,
            ),
        )
        payment.journal_entry_fk = outcome.entry.id
        self._db.flush()
        self._audit.record(
            actor,
            "supplier_payment_recorded",
            entity_type="supplier_payment",
            entity_id=payment.id,
            entity_label=payment.number,
            after={
                "supplier": supplier.name,
                "amount": str(payment.amount),
                "withheld": str(payment.withheld),
                "invoices": [str(item.invoice_fk) for item in payment.allocations],
            },
        )
        return payment

    def payments(
        self, actor: AnyActor, *, supplier_id: uuid.UUID | None = None, limit: int = 50
    ) -> list[SupplierPayment]:
        actor.require("payable.view")
        query = select(SupplierPayment).where(SupplierPayment.business_id == _business_of(actor))
        if supplier_id:
            query = query.where(SupplierPayment.supplier_fk == supplier_id)
        return list(
            self._db.scalars(
                query.order_by(
                    SupplierPayment.paid_on.desc(), SupplierPayment.created_at.desc()
                ).limit(max(min(limit, 200), 1))
            ).all()
        )

    def register(self, actor: AnyActor, *, as_at: date | None = None) -> PayablesRegister:
        """What is owed to suppliers, aged by due date."""
        actor.require("payable.view")
        business_id = _business_of(actor)
        today = as_at or self._today(actor)
        rows: list[PayableRow] = []
        by_bucket: dict[str, Decimal] = {bucket.value: ZERO for bucket in AgeingBucket}
        by_supplier: dict[str, Decimal] = {}
        total = ZERO
        names: dict[uuid.UUID, str] = {}
        for invoice, outstanding in self._outstanding_for(business_id, None):
            if invoice.supplier_fk not in names:
                supplier = self._db.get(Party, invoice.supplier_fk)
                names[invoice.supplier_fk] = supplier.name if supplier else "Supplier"
            bucket = ageing_bucket(invoice.due_on, today)
            rows.append(
                PayableRow(
                    invoice_id=invoice.id,
                    number=invoice.number,
                    supplier_id=invoice.supplier_fk,
                    supplier_name=names[invoice.supplier_fk],
                    supplier_reference=invoice.supplier_reference,
                    invoiced_on=invoice.invoiced_on,
                    due_on=invoice.due_on,
                    total=invoice.base_total,
                    paid=invoice.amount_paid,
                    credited=invoice.amount_credited,
                    outstanding=outstanding,
                    bucket=bucket,
                    days_overdue=max(0, (today - invoice.due_on).days),
                )
            )
            by_bucket[bucket.value] = money(by_bucket[bucket.value] + outstanding)
            name = names[invoice.supplier_fk]
            by_supplier[name] = money(by_supplier.get(name, ZERO) + outstanding)
            total = money(total + outstanding)
        return PayablesRegister(
            as_at=today,
            rows=rows,
            total_outstanding=total,
            by_bucket=by_bucket,
            by_supplier=by_supplier,
        )

    def set_priority(
        self, actor: AnyActor, invoice_id: uuid.UUID, priority: str, *, reason: str
    ) -> SupplierInvoice:
        """Pay an invoice first, or hold it back until released."""
        actor.require("payment.manage")
        if priority not in ("urgent", "normal", "hold"):
            raise ValidationError("Urgent, normal or on hold.", field="priority")
        if not reason.strip():
            raise ValidationError("A change of priority records why.", field="reason")
        invoice = self._db.get(SupplierInvoice, invoice_id)
        if invoice is None or invoice.business_id != actor.business_id:
            raise NotFoundError("Supplier invoice not found.")
        before = invoice.priority
        invoice.priority = priority
        self._db.flush()
        self._audit.record(
            actor,
            "supplier_invoice_priority_set",
            entity_type="supplier_invoice",
            entity_id=invoice.id,
            entity_label=invoice.number or invoice.supplier_reference,
            reason=reason.strip(),
            before={"priority": before},
            after={"priority": priority},
        )
        return invoice

    def statement(
        self, actor: AnyActor, supplier_id: uuid.UUID, *, start: date, end: date | None = None
    ) -> SupplierStatement:
        """What the ledger holds for one supplier: every movement on their payable."""
        actor.require("payable.view")
        supplier = self._db.get(Party, supplier_id)
        if (
            supplier is None
            or supplier.business_id != actor.business_id
            or not supplier.is_supplier
        ):
            raise NotFoundError("Supplier not found.")
        ends_on = end or self._today(actor)
        account = self._db.scalar(
            select(Account).where(
                Account.business_id == supplier.business_id, Account.code == TRADE_PAYABLES
            )
        )
        assert account is not None
        base = select(JournalLine).where(
            JournalLine.account_fk == account.id,
            JournalLine.party_fk == supplier.id,
            JournalLine.state == "posted",
        )
        opening = money(
            sum(
                (
                    line.base_credit - line.base_debit
                    for line in self._db.scalars(base.where(JournalLine.posting_date < start))
                ),
                ZERO,
            )
        )
        running = opening
        entries = []
        for line in self._db.scalars(
            base.where(
                JournalLine.posting_date >= start, JournalLine.posting_date <= ends_on
            ).order_by(JournalLine.posting_date, JournalLine.entry_fk, JournalLine.display_order)
        ):
            entry = self._db.get(JournalEntry, line.entry_fk)
            running = money(running + line.base_credit - line.base_debit)
            entries.append(
                StatementEntry(
                    posted_on=line.posting_date,
                    reference=entry.source_reference if entry else None,
                    narration=line.narration or (entry.narration if entry else ""),
                    debit=line.base_debit,
                    credit=line.base_credit,
                    balance=running,
                )
            )
        return SupplierStatement(
            supplier=supplier,
            starts_on=start,
            ends_on=ends_on,
            opening=opening,
            entries=entries,
            closing=running,
        )

    # -- batches ----------------------------------------------------------------------------

    def prepare_batch(
        self,
        actor: AnyActor,
        *,
        financial_account_id: uuid.UUID,
        items: list[BatchItem],
        pay_on: date | None = None,
    ) -> PaymentBatch:
        """Several payments, prepared together and approved once by someone else."""
        actor.require("payment.manage")
        business_id = _business_of(actor)
        if not items:
            raise ValidationError("A batch pays at least one supplier.", field="items")
        from nickland.application.services.treasury import TreasuryService

        account = TreasuryService(self._db, clock=self._clock).require(actor, financial_account_id)
        stored = []
        for item in items:
            supplier = self._db.get(Party, item.supplier_id)
            if supplier is None or supplier.business_id != business_id or not supplier.is_supplier:
                raise NotFoundError("Supplier not found.")
            if money(item.amount) <= ZERO:
                raise ValidationError("Each payment is above zero.", field="items")
            owed = money(
                sum((owed for _, owed in self._outstanding_for(business_id, supplier.id)), ZERO)
            )
            if money(item.amount) > owed:
                raise ConflictError(f"{supplier.name} is owed {owed}.")
            stored.append(
                {
                    "supplier_id": str(supplier.id),
                    "supplier": supplier.name,
                    "amount": str(money(item.amount)),
                    "invoice_ids": [str(value) for value in item.invoice_ids],
                    "reference": item.reference,
                }
            )
        batch = PaymentBatch(
            business_id=business_id,
            number=OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "payment_voucher", branch_id=actor.branch_id)
            .number,
            financial_account_fk=account.id,
            pay_on=pay_on or self._today(actor),
            items=stored,
            total=money(sum((money(item.amount) for item in items), ZERO)),
            prepared_by_id=actor.user_id,
        )
        self._db.add(batch)
        self._db.flush()
        return batch

    def approve_batch(self, actor: AnyActor, batch_id: uuid.UUID) -> PaymentBatch:
        """Approve and pay every payment in the batch."""
        actor.require("payment.approve")
        batch = self._batch(actor, batch_id)
        if batch.state != "awaiting_approval":
            raise ConflictError(f"Batch {batch.number} is {batch.state.replace('_', ' ')}.")
        if batch.prepared_by_id == actor.user_id and not actor.is_administrator:
            raise ConflictError("A batch is approved by someone other than whoever prepared it.")
        paid = []
        for item in batch.items:
            payment = self.pay(
                actor,
                PaymentDraft(
                    supplier_id=uuid.UUID(str(item["supplier_id"])),
                    amount=Decimal(str(item["amount"])),
                    method=PaymentMethod.BANK,
                    paid_on=batch.pay_on,
                    reference=str(item["reference"]) if item.get("reference") else batch.number,
                    invoice_ids=[uuid.UUID(str(value)) for value in item["invoice_ids"]],  # type: ignore[attr-defined]
                    financial_account_id=batch.financial_account_fk,
                ),
            )
            paid.append(str(payment.id))
        batch.state = "paid"
        batch.approved_by_id = actor.user_id
        batch.decided_at = self._clock.now()
        batch.payment_ids = paid
        self._db.flush()
        self._audit.record(
            actor,
            "payment_batch_approved",
            entity_type="payment_batch",
            entity_id=batch.id,
            entity_label=batch.number,
            after={"total": str(batch.total), "payments": len(paid)},
        )
        return batch

    def reject_batch(self, actor: AnyActor, batch_id: uuid.UUID, *, reason: str) -> PaymentBatch:
        actor.require("payment.approve")
        batch = self._batch(actor, batch_id)
        if batch.state != "awaiting_approval":
            raise ConflictError(f"Batch {batch.number} is {batch.state.replace('_', ' ')}.")
        if not reason.strip():
            raise ValidationError("A rejection records why.", field="reason")
        batch.state = "rejected"
        batch.rejection_reason = reason.strip()
        batch.approved_by_id = actor.user_id
        batch.decided_at = self._clock.now()
        self._db.flush()
        return batch

    def batches(self, actor: AnyActor) -> list[PaymentBatch]:
        actor.require("payable.view")
        return list(
            self._db.scalars(
                select(PaymentBatch)
                .where(PaymentBatch.business_id == actor.business_id)
                .order_by(PaymentBatch.created_at.desc())
            ).all()
        )

    def _batch(self, actor: AnyActor, batch_id: uuid.UUID) -> PaymentBatch:
        batch = self._db.get(PaymentBatch, batch_id)
        if batch is None or batch.business_id != actor.business_id:
            raise NotFoundError("Payment batch not found.")
        return batch

    def _withholding_authority(self, business_id: uuid.UUID) -> uuid.UUID:
        """The authority tax withheld from a supplier is owed to: the purchase-side
        withholding rule's. Refused where none is configured, rather than posting a
        liability to nobody."""
        from nickland.application.services.taxation import TaxService
        from nickland.domain.taxation import TaxCategory

        taxes = TaxService(self._db, clock=self._clock)
        rules = [
            rule
            for rule in taxes.rules(_actor_for(business_id))
            if rule.category == TaxCategory.WITHHOLDING.value and rule.authority_fk is not None
        ]
        preferred = [rule for rule in rules if rule.applies_to_purchases] or rules
        if not preferred:
            raise ValidationError(
                "Tax cannot be withheld until a withholding rule names the authority it is "
                "owed to. Configure one under Taxation, or record the payment without "
                "withholding.",
                field="withheld",
            )
        return preferred[0].authority_fk  # type: ignore[return-value]

    def _outstanding_for(
        self, business_id: uuid.UUID, supplier_id: uuid.UUID | None
    ) -> list[tuple[SupplierInvoice, Decimal]]:
        query = select(SupplierInvoice).where(
            SupplierInvoice.business_id == business_id,
            SupplierInvoice.state.in_([state.value for state in PAYABLE_STATES]),
        )
        if supplier_id is not None:
            query = query.where(SupplierInvoice.supplier_fk == supplier_id)
        found: list[tuple[SupplierInvoice, Decimal]] = []
        for invoice in self._db.scalars(
            query.order_by(SupplierInvoice.due_on, SupplierInvoice.invoiced_on)
        ):
            owed = money(invoice.base_total - invoice.amount_paid - invoice.amount_credited)
            if owed > ZERO:
                found.append((invoice, owed))
        return found

    def _today(self, actor: AnyActor) -> date:
        business = self._db.get(Business, actor.business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)


def _actor_for(business_id: uuid.UUID) -> AnyActor:
    """A read-only system actor for looking up the business's own configuration."""
    from nickland.application.context import SystemActor

    return SystemActor(business_id=business_id, reason="Withholding authority lookup")


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
