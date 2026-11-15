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

        candidates = self._outstanding_for(business_id, supplier.id)
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
        lines.append(
            EntryLine(
                account_code=PAYMENT_ACCOUNTS[method],
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
