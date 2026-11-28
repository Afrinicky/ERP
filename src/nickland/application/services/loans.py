"""Loans and financing agreements. PRD §25.4, §25.5, §25.7, §25.8.

Money borrowed is a liability and money lent is an asset. A drawdown moves the balance and
the cash together, and a repayment of principal undoes it; neither touches profit. Interest
and fees are recognised when an instalment falls due (or when it is paid early, if sooner),
into the agreement's accrued account, and the repayment that follows only clears it.

Every posting names the agreement as the subledger party on the lender or borrower control
accounts, so each agreement's balance in the ledger can be read and checked against its own
schedule and transactions.

A schedule, once approved, is never recalculated in place. A reschedule, a payment holiday,
a change of rate or a prepayment produces a new version beside it; the original remains, and
every version can be read back exactly as it stood.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.treasury import TreasuryService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.financing import (
    ACCOUNTS,
    BAD_DEBT_EXPENSE,
    DEFAULT_ORDER,
    AgreementState,
    Component,
    CustomRow,
    InterestModel,
    LoanDirection,
    LoanTerms,
    LoanType,
    Outstanding,
    RepaymentFrequency,
    RevisionKind,
    ScheduledInstalment,
    ScheduleState,
    SecurityKind,
    SettlementQuote,
    accrued_since,
    allocate_repayment,
    build_schedule,
    cents,
    early_settlement_fee,
    parse_order,
)
from nickland.domain.ledger import EntryLine, JournalSource, Side
from nickland.domain.money import ZERO, money
from nickland.persistence.catalogue_models import Party
from nickland.persistence.finance_models import FinancialAccount
from nickland.persistence.financing_models import (
    LoanAgreement,
    LoanAllocation,
    LoanInstalment,
    LoanSchedule,
    LoanSecurity,
    LoanTransaction,
)
from nickland.persistence.models import Business, Setting, User
from nickland.platform.clock import Clock, SystemClock, business_date

THRESHOLD_KEY = "loans.approval_threshold"


class TransactionKind:
    DRAWDOWN = "drawdown"
    REPAYMENT = "repayment"
    SETTLEMENT = "settlement"
    PENALTY = "penalty"
    REFINANCE_IN = "refinance_in"
    REFINANCE_OUT = "refinance_out"
    WRITE_OFF = "write_off"

    LABELS: ClassVar[dict[str, str]] = {
        DRAWDOWN: "Drawdown",
        REPAYMENT: "Repayment",
        SETTLEMENT: "Early settlement",
        PENALTY: "Penalty charged",
        REFINANCE_IN: "Refinanced from an earlier agreement",
        REFINANCE_OUT: "Refinanced by a new agreement",
        WRITE_OFF: "Written off",
    }


@dataclass(slots=True)
class LoanDraft:
    direction: LoanDirection | str
    loan_type: LoanType | str
    counterparty_name: str
    principal: Decimal
    agreement_date: date
    model: InterestModel | str
    frequency: RepaymentFrequency | str
    instalments: int
    first_due_on: date
    annual_rate: Decimal = ZERO
    compounding: RepaymentFrequency | str | None = None
    grace_periods: int = 0
    interest_only_periods: int = 0
    balloon: Decimal = ZERO
    periodic_fee: Decimal = ZERO
    fixed_interest: Decimal = ZERO
    upfront_fee: Decimal = ZERO
    penalty_rate: Decimal = ZERO
    early_settlement_percent: Decimal = ZERO
    prepayment_allowed: bool = True
    allocation_order: Sequence[str] = field(
        default_factory=lambda: [c.value for c in DEFAULT_ORDER]
    )
    custom_rows: Sequence[CustomRow] = ()
    counterparty_party_id: uuid.UUID | None = None
    counterparty_employee_id: uuid.UUID | None = None
    agreement_reference: str | None = None
    purpose: str | None = None
    disbursement_date: date | None = None
    financial_account_id: uuid.UUID | None = None
    responsible_id: uuid.UUID | None = None
    agreement_attachment_id: uuid.UUID | None = None
    notes: str | None = None

    def terms(self) -> LoanTerms:
        return LoanTerms(
            principal=money(self.principal),
            model=InterestModel(self.model),
            frequency=RepaymentFrequency(self.frequency),
            instalments=self.instalments,
            first_due_on=self.first_due_on,
            annual_rate=money(self.annual_rate),
            compounding=RepaymentFrequency(self.compounding) if self.compounding else None,
            grace_periods=self.grace_periods,
            interest_only_periods=self.interest_only_periods,
            balloon=money(self.balloon),
            periodic_fee=money(self.periodic_fee),
            fixed_interest=money(self.fixed_interest),
            custom=tuple(self.custom_rows),
        )


@dataclass(slots=True)
class RevisionDraft:
    kind: RevisionKind | str
    reason: str
    effective_on: date
    first_due_on: date
    instalments: int
    annual_rate: Decimal | None = None
    model: InterestModel | str | None = None
    frequency: RepaymentFrequency | str | None = None
    grace_periods: int = 0
    interest_only_periods: int = 0
    balloon: Decimal = ZERO
    periodic_fee: Decimal | None = None
    evidence_attachment_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class LoanPosition:
    """Where an agreement stands, every figure separately, as PRD §25.4 asks."""

    principal_agreed: Decimal
    principal_drawn: Decimal
    undrawn: Decimal
    capitalised: Decimal
    principal_repaid: Decimal
    principal_outstanding: Decimal
    interest_due: Decimal
    fees_due: Decimal
    penalties_due: Decimal
    arrears: Decimal
    next_due_on: date | None
    next_due_amount: Decimal
    days_overdue: int
    settlement_today: Decimal
    ledger_principal: Decimal
    ledger_accrued: Decimal

    @property
    def total_outstanding(self) -> Decimal:
        return self.principal_outstanding + self.interest_due + self.fees_due + self.penalties_due


class LoanService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._treasury = TreasuryService(session, clock=self._clock)

    # -- threshold --------------------------------------------------------------------------

    def threshold(self, business_id: uuid.UUID) -> Decimal | None:
        stored = self._setting(business_id)
        return (
            Decimal(str(stored.value)) if stored is not None and stored.value is not None else None
        )

    def set_threshold(self, actor: AnyActor, amount: Decimal | None) -> Decimal | None:
        """Agreements and revisions at or above this wait for a second person's approval,
        with documented authorisation. None: an agreement is active once submitted."""
        actor.require("loan.approve")
        if amount is not None and money(amount) <= ZERO:
            raise ValidationError("A threshold above zero, or none.", field="amount")
        business_id = _business_of(actor)
        stored = self._setting(business_id)
        before = stored.value if stored else None
        value = str(money(amount)) if amount is not None else None
        if stored is None:
            stored = Setting(
                business_id=business_id,
                key=THRESHOLD_KEY,
                value=value,
                description="Borrowing, lending and revisions at or above this need approval.",
            )
            self._db.add(stored)
        else:
            stored.value = value
        self._db.flush()
        self._audit.record(
            actor,
            "loan_threshold_amended",
            entity_type="setting",
            entity_id=stored.id,
            entity_label=THRESHOLD_KEY,
            before={"value": before},
            after={"value": value},
        )
        return self.threshold(business_id)

    def _setting(self, business_id: uuid.UUID) -> Setting | None:
        return self._db.scalar(
            select(Setting).where(Setting.business_id == business_id, Setting.key == THRESHOLD_KEY)
        )

    # -- agreements -------------------------------------------------------------------------

    def preview(self, actor: AnyActor, draft: LoanDraft) -> list[ScheduledInstalment]:
        """The schedule the terms produce, persisting nothing."""
        actor.require_any("loan.view", "loan.manage")
        return build_schedule(draft.terms())

    def create(self, actor: AnyActor, draft: LoanDraft) -> LoanAgreement:
        actor.require("loan.manage")
        business_id = _business_of(actor)
        values = self._validated(actor, draft)
        rows = build_schedule(draft.terms())
        agreement = LoanAgreement(
            business_id=business_id,
            number=OrganisationService(self._db, clock=self._clock)
            .allocate_number(
                actor, "loan_agreement", branch_id=actor.branch_id, on=draft.agreement_date
            )
            .number,
            state=AgreementState.DRAFT.value,
            created_by_id=actor.user_id,
            branch_id=actor.branch_id,
            maturity_date=rows[-1].due_on,
            **values,
        )
        self._db.add(agreement)
        self._db.flush()
        self._new_version(
            agreement,
            rows,
            kind=RevisionKind.ORIGINAL,
            state=ScheduleState.PROPOSED,
            effective_on=draft.agreement_date,
            basis=agreement.principal,
            terms=_terms_record(draft.terms()),
            proposed_by=actor.user_id,
        )
        self._audit.record(
            actor,
            "loan_created",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            after=_summary(agreement),
        )
        return agreement

    def amend(self, actor: AnyActor, agreement_id: uuid.UUID, draft: LoanDraft) -> LoanAgreement:
        """Change a draft's terms. Once submitted, terms change only by a revision."""
        actor.require("loan.manage")
        agreement = self.require(actor, agreement_id)
        if agreement.state != AgreementState.DRAFT.value:
            raise ConflictError(
                f"{agreement.number} has been submitted. Its terms now change only by an "
                "approved revision."
            )
        before = _summary(agreement)
        values = self._validated(actor, draft)
        rows = build_schedule(draft.terms())
        for name, value in values.items():
            setattr(agreement, name, value)
        agreement.maturity_date = rows[-1].due_on
        original = self._version(agreement, 1)
        for row in list(original.instalments):
            self._db.delete(row)
        self._db.flush()
        original.terms = _terms_record(draft.terms())
        original.principal_basis = agreement.principal
        original.effective_on = draft.agreement_date
        self._db.flush()
        self._add_rows(agreement, original, rows)
        self._audit.record(
            actor,
            "loan_amended",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            before=before,
            after=_summary(agreement),
        )
        return agreement

    def submit(self, actor: AnyActor, agreement_id: uuid.UUID) -> LoanAgreement:
        """Below the threshold an agreement is active at once; at or above it, it waits."""
        actor.require("loan.manage")
        agreement = self.require(actor, agreement_id)
        if agreement.state != AgreementState.DRAFT.value:
            raise ConflictError(f"{agreement.number} is not a draft.")
        agreement.submitted_by_id = actor.user_id
        threshold = self.threshold(agreement.business_id)
        if threshold is None or agreement.principal < threshold:
            self._activate(actor, agreement, note=None)
        else:
            agreement.state = AgreementState.AWAITING_APPROVAL.value
            self._db.flush()
        self._audit.record(
            actor,
            "loan_submitted",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            after={"state": agreement.state},
        )
        return agreement

    def approve(
        self,
        actor: AnyActor,
        agreement_id: uuid.UUID,
        *,
        note: str | None = None,
        attachment_id: uuid.UUID | None = None,
    ) -> LoanAgreement:
        """Approve a material agreement. It rests on documented authorisation: the signed
        agreement, an approval document, or a note of who authorised it and how."""
        actor.require("loan.approve")
        agreement = self.require(actor, agreement_id)
        if agreement.state != AgreementState.AWAITING_APPROVAL.value:
            raise ConflictError(f"{agreement.number} is not awaiting approval.")
        if agreement.submitted_by_id == actor.user_id and not actor.is_administrator:
            raise ConflictError(
                "An agreement is approved by someone other than whoever submitted it."
            )
        text = (note or "").strip() or None
        if text is None and attachment_id is None and agreement.agreement_attachment_fk is None:
            raise ValidationError(
                "A material agreement rests on documented authorisation. Attach the signed "
                "agreement or approval, or record who authorised it and how.",
                field="note",
            )
        agreement.approval_attachment_fk = attachment_id
        self._activate(actor, agreement, note=text)
        self._audit.record(
            actor,
            "loan_approved",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            reason=text,
            after={"principal": str(agreement.principal), "state": agreement.state},
        )
        return agreement

    def reject(self, actor: AnyActor, agreement_id: uuid.UUID, *, reason: str) -> LoanAgreement:
        actor.require("loan.approve")
        agreement = self.require(actor, agreement_id)
        if agreement.state != AgreementState.AWAITING_APPROVAL.value:
            raise ConflictError(f"{agreement.number} is not awaiting approval.")
        if not reason.strip():
            raise ValidationError("A rejection records why.", field="reason")
        agreement.state = AgreementState.REJECTED.value
        agreement.rejection_reason = reason.strip()
        agreement.approved_by_id = actor.user_id
        agreement.decided_at = self._clock.now()
        self._version(agreement, 1).state = ScheduleState.DECLINED.value
        self._db.flush()
        self._audit.record(
            actor,
            "loan_rejected",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            reason=reason.strip(),
        )
        return agreement

    def _activate(self, actor: AnyActor, agreement: LoanAgreement, *, note: str | None) -> None:
        agreement.state = AgreementState.ACTIVE.value
        agreement.approved_by_id = actor.user_id
        agreement.approval_note = note
        agreement.decided_at = self._clock.now()
        original = self._version(agreement, 1)
        original.state = ScheduleState.CURRENT.value
        original.decided_by_id = actor.user_id
        original.decided_at = agreement.decided_at
        original.decision_note = note
        self._db.flush()

    # -- reading ----------------------------------------------------------------------------

    def require(self, actor: AnyActor, agreement_id: uuid.UUID) -> LoanAgreement:
        actor.require_any("loan.view", "loan.manage", "repayment.manage")
        row = self._db.get(LoanAgreement, agreement_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Agreement not found.")
        return row

    def agreements(
        self,
        actor: AnyActor,
        *,
        direction: str | None = None,
        state: str | None = None,
    ) -> list[LoanAgreement]:
        actor.require_any("loan.view", "loan.manage", "repayment.manage")
        query = select(LoanAgreement).where(LoanAgreement.business_id == actor.business_id)
        if direction:
            query = query.where(LoanAgreement.direction == LoanDirection(direction).value)
        if state:
            query = query.where(LoanAgreement.state == AgreementState(state).value)
        return list(
            self._db.scalars(
                query.order_by(LoanAgreement.agreement_date.desc(), LoanAgreement.number.desc())
            )
        )

    def versions(self, actor: AnyActor, agreement_id: uuid.UUID) -> list[LoanSchedule]:
        agreement = self.require(actor, agreement_id)
        return list(agreement.schedules)

    def current(self, agreement: LoanAgreement) -> LoanSchedule:
        for version in agreement.schedules:
            if version.state == ScheduleState.CURRENT.value:
                return version
        return agreement.schedules[0]

    def proposed(self, agreement: LoanAgreement) -> LoanSchedule | None:
        for version in agreement.schedules:
            if version.state == ScheduleState.PROPOSED.value and version.version > 1:
                return version
        return None

    def transactions(self, actor: AnyActor, agreement_id: uuid.UUID) -> list[LoanTransaction]:
        self.require(actor, agreement_id)
        return list(
            self._db.scalars(
                select(LoanTransaction)
                .where(LoanTransaction.agreement_fk == agreement_id)
                .order_by(LoanTransaction.occurred_on, LoanTransaction.created_at)
            )
        )

    def securities(self, actor: AnyActor, agreement_id: uuid.UUID) -> list[LoanSecurity]:
        self.require(actor, agreement_id)
        return list(
            self._db.scalars(
                select(LoanSecurity)
                .where(LoanSecurity.agreement_fk == agreement_id)
                .order_by(LoanSecurity.created_at)
            )
        )

    def open_instalments(self, agreement: LoanAgreement) -> list[LoanInstalment]:
        """Every instalment that may still be owed, across versions, oldest first."""
        return list(
            self._db.scalars(
                select(LoanInstalment)
                .join(LoanSchedule, LoanSchedule.id == LoanInstalment.schedule_fk)
                .where(
                    LoanInstalment.agreement_fk == agreement.id,
                    LoanInstalment.closed.is_(False),
                    LoanSchedule.state == ScheduleState.CURRENT.value,
                )
                .order_by(LoanInstalment.due_on, LoanInstalment.sequence)
            )
        )

    def position(self, agreement: LoanAgreement, on: date | None = None) -> LoanPosition:
        today = on or self._today(agreement.business_id)
        transactions = list(
            self._db.scalars(
                select(LoanTransaction).where(LoanTransaction.agreement_fk == agreement.id)
            )
        )
        drawn = sum(
            (
                row.amount
                for row in transactions
                if row.kind in {TransactionKind.DRAWDOWN, TransactionKind.REFINANCE_IN}
            ),
            ZERO,
        )
        repaid = self._paid(agreement.id, Component.PRINCIPAL)
        all_rows = list(
            self._db.scalars(
                select(LoanInstalment).where(LoanInstalment.agreement_fk == agreement.id)
            )
        )
        capitalised = sum((row.capitalised for row in all_rows if row.accrued), ZERO)
        closed_state = AgreementState(agreement.state).closed
        principal_out = ZERO if closed_state else max(ZERO, drawn + capitalised - repaid)
        open_rows = [] if closed_state else self.open_instalments(agreement)
        due = [row for row in open_rows if row.accrued]
        interest_due = sum((row.interest - row.paid_interest for row in due), ZERO)
        fees_due = sum((row.fees - row.paid_fees for row in due), ZERO)
        penalties_due = sum((row.penalty - row.paid_penalty for row in open_rows), ZERO)
        overdue = [row for row in open_rows if row.due_on < today and row.owed > row.paid]
        arrears = sum((row.owed - row.paid for row in overdue), ZERO)
        upcoming = next(
            (row for row in open_rows if row.due_on >= today and row.owed > row.paid), None
        )
        days = (today - overdue[0].due_on).days if overdue else 0
        ledger_principal, ledger_accrued = self._ledger_balances(agreement)
        settlement = (
            self._quote(agreement, today, principal_out).total if not closed_state else ZERO
        )
        return LoanPosition(
            principal_agreed=agreement.principal,
            principal_drawn=drawn,
            undrawn=max(ZERO, agreement.principal - drawn) if not closed_state else ZERO,
            capitalised=capitalised,
            principal_repaid=repaid,
            principal_outstanding=principal_out,
            interest_due=interest_due,
            fees_due=fees_due,
            penalties_due=penalties_due,
            arrears=arrears,
            next_due_on=upcoming.due_on if upcoming else None,
            next_due_amount=(upcoming.owed - upcoming.paid) if upcoming else ZERO,
            days_overdue=days,
            settlement_today=settlement,
            ledger_principal=ledger_principal,
            ledger_accrued=ledger_accrued,
        )

    def _paid(self, agreement_id: uuid.UUID, component: Component) -> Decimal:
        rows = self._db.scalars(
            select(LoanAllocation.amount)
            .join(LoanTransaction, LoanTransaction.id == LoanAllocation.transaction_fk)
            .where(
                LoanTransaction.agreement_fk == agreement_id,
                LoanAllocation.component == component.value,
            )
        )
        return sum(rows, ZERO)

    def _ledger_balances(self, agreement: LoanAgreement) -> tuple[Decimal, Decimal]:
        """The agreement's balance on its principal and accrued accounts, in the ledger,
        each as the business would describe it: owed by us, or owed to us."""
        from nickland.persistence.ledger_models import Account, JournalLine

        accounts = ACCOUNTS[LoanDirection(agreement.direction)]
        result: list[Decimal] = []
        for code in (accounts.principal, accounts.accrued):
            rows = self._db.execute(
                select(JournalLine.base_debit, JournalLine.base_credit)
                .join(Account, Account.id == JournalLine.account_fk)
                .where(
                    JournalLine.business_id == agreement.business_id,
                    JournalLine.party_fk == agreement.id,
                    JournalLine.state == "posted",
                    Account.code == code,
                )
            ).all()
            debit = sum((row[0] for row in rows), ZERO)
            credit = sum((row[1] for row in rows), ZERO)
            borrowing = agreement.direction == LoanDirection.BORROWING.value
            result.append(credit - debit if borrowing else debit - credit)
        return result[0], result[1]

    # -- money moving -----------------------------------------------------------------------

    def drawdown(
        self,
        actor: AnyActor,
        agreement_id: uuid.UUID,
        *,
        amount: Decimal,
        on: date | None = None,
        financial_account_id: uuid.UUID | None = None,
        fee: Decimal = ZERO,
        reference: str | None = None,
        evidence_attachment_id: uuid.UUID | None = None,
    ) -> LoanTransaction:
        """Money arriving from a lender, or leaving to a borrower.

        Borrowing: Dr the account, Cr loans and borrowings. Lending: Dr loans and advances
        issued, Cr the account. A fee deducted from the proceeds is a finance charge to the
        borrower and fee income to the lender, never part of the principal.
        """
        actor.require("repayment.manage")
        agreement = self._active(actor, agreement_id)
        amount = cents(money(amount))
        fee = cents(money(fee))
        if amount <= ZERO:
            raise ValidationError("A drawdown must be more than nothing.", field="amount")
        if fee < ZERO or fee >= amount:
            raise ValidationError("A fee is less than the amount drawn.", field="fee")
        position = self.position(agreement)
        if amount > position.undrawn:
            raise ConflictError(
                f"Only {position.undrawn} of {agreement.number} remains to be drawn."
            )
        account = self._account(actor, agreement, financial_account_id)
        when = on or self._today(agreement.business_id)
        accounts = ACCOUNTS[LoanDirection(agreement.direction)]
        cash = self._treasury.ledger_code(account)
        borrowing = agreement.direction == LoanDirection.BORROWING.value
        party = self._party(agreement)
        lines = (
            [
                EntryLine(cash, Side.DEBIT, amount - fee, reference or agreement.number),
                EntryLine(
                    accounts.principal, Side.CREDIT, amount, agreement.counterparty_name, **party
                ),
            ]
            if borrowing
            else [
                EntryLine(
                    accounts.principal, Side.DEBIT, amount, agreement.counterparty_name, **party
                ),
                EntryLine(cash, Side.CREDIT, amount - fee, reference or agreement.number),
            ]
        )
        if fee > ZERO:
            lines.append(
                EntryLine(
                    accounts.fees,
                    Side.DEBIT if borrowing else Side.CREDIT,
                    fee,
                    f"Arrangement fee, {agreement.number}",
                )
            )
        row = self._transaction(
            actor,
            agreement,
            TransactionKind.DRAWDOWN,
            when,
            amount,
            fee=fee,
            account=account.id,
            reference=reference,
            evidence=evidence_attachment_id,
        )
        row.journal_entry_fk = self._post(
            actor,
            agreement,
            lines,
            on=when,
            narration=f"Drawdown, {agreement.number}: {agreement.counterparty_name}",
            key=f"loan_drawdown:{row.id}",
            attachment=evidence_attachment_id,
        )
        if agreement.disbursement_date is None:
            agreement.disbursement_date = when
        self._db.flush()
        self._audit.record(
            actor,
            "loan_drawdown",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            after={"amount": str(amount), "fee": str(fee), "on": when.isoformat()},
        )
        return row

    def accrue(self, actor: AnyActor, *, as_of: date | None = None) -> int:
        """Recognise the interest and fees of every instalment that has fallen due."""
        actor.require("repayment.manage")
        business_id = _business_of(actor)
        when = as_of or self._today(business_id)
        rows = list(
            self._db.scalars(
                select(LoanInstalment)
                .join(LoanSchedule, LoanSchedule.id == LoanInstalment.schedule_fk)
                .join(LoanAgreement, LoanAgreement.id == LoanInstalment.agreement_fk)
                .where(
                    LoanInstalment.business_id == business_id,
                    LoanInstalment.accrued.is_(False),
                    LoanInstalment.closed.is_(False),
                    LoanInstalment.due_on <= when,
                    LoanSchedule.state == ScheduleState.CURRENT.value,
                    LoanAgreement.state == AgreementState.ACTIVE.value,
                )
                .order_by(LoanInstalment.due_on)
            )
        )
        for each in rows:
            agreement = self._db.get(LoanAgreement, each.agreement_fk)
            assert agreement is not None
            self._accrue(actor, agreement, each)
        return len(rows)

    def _accrue(self, actor: AnyActor, agreement: LoanAgreement, row: LoanInstalment) -> None:
        """Interest and fees into the accrued account; capitalised interest into principal."""
        if row.accrued:
            return
        accounts = ACCOUNTS[LoanDirection(agreement.direction)]
        borrowing = agreement.direction == LoanDirection.BORROWING.value
        party = self._party(agreement)
        label = f"{agreement.number} instalment {row.sequence}"
        lines: list[EntryLine] = []

        def pair(charge_account: str, carried_to: str, amount: Decimal, what: str) -> None:
            if amount <= ZERO:
                return
            if borrowing:
                lines.append(EntryLine(charge_account, Side.DEBIT, amount, f"{what}, {label}"))
                lines.append(EntryLine(carried_to, Side.CREDIT, amount, label, **party))
            else:
                lines.append(EntryLine(carried_to, Side.DEBIT, amount, label, **party))
                lines.append(EntryLine(charge_account, Side.CREDIT, amount, f"{what}, {label}"))

        pair(accounts.interest, accounts.accrued, row.interest, "Interest")
        pair(accounts.fees, accounts.accrued, row.fees, "Fees")
        pair(accounts.interest, accounts.principal, row.capitalised, "Interest capitalised")
        row.accrued = True
        if lines:
            row.accrual_journal_fk = self._post(
                actor,
                agreement,
                lines,
                on=row.due_on,
                narration=f"Interest and fees due, {label}",
                key=f"loan_accrual:{row.id}",
            )
        self._db.flush()

    def repay(
        self,
        actor: AnyActor,
        agreement_id: uuid.UUID,
        *,
        amount: Decimal,
        on: date | None = None,
        financial_account_id: uuid.UUID | None = None,
        reference: str | None = None,
        evidence_attachment_id: uuid.UUID | None = None,
    ) -> LoanTransaction:
        """A repayment, made by the business or received by it.

        It settles what has fallen due, oldest instalment first and each component in the
        agreement's order. Anything beyond that prepays principal where the agreement allows
        it, producing a recalculated schedule as a new version; otherwise it pays the next
        instalments ahead of their dates.
        """
        actor.require("repayment.manage")
        agreement = self._active(actor, agreement_id)
        amount = cents(money(amount))
        when = on or self._today(agreement.business_id)
        account = self._account(actor, agreement, financial_account_id)
        order = parse_order(agreement.allocation_order)

        rows = self.open_instalments(agreement)
        for each in rows:
            if each.due_on <= when:
                self._accrue(actor, agreement, each)
        principal_out = self.position(agreement, when).principal_outstanding
        due = [_outstanding(item) for item in rows if item.due_on <= when and item.owed > item.paid]
        allocations, remaining = allocate_repayment(amount, due, order)

        prepaid = ZERO
        if remaining > ZERO:
            ahead = [item for item in rows if item.due_on > when and item.owed > item.paid]
            if agreement.prepayment_allowed:
                taken = sum(
                    (item.amount for item in allocations if item.component is Component.PRINCIPAL),
                    ZERO,
                )
                available = principal_out - taken
                if remaining > available:
                    raise ConflictError(
                        f"That is {remaining - available} more than {agreement.number} owes. "
                        "To close it, take a settlement quotation and settle."
                    )
                prepaid = remaining
                remaining = ZERO
            else:
                for each in ahead:
                    self._accrue(actor, agreement, each)
                more, remaining = allocate_repayment(
                    remaining, [_outstanding(item) for item in ahead], order
                )
                allocations.extend(more)
                if remaining > ZERO:
                    raise ConflictError(
                        f"That is {remaining} more than {agreement.number} owes on its schedule."
                    )

        row = self._transaction(
            actor,
            agreement,
            TransactionKind.REPAYMENT,
            when,
            amount,
            account=account.id,
            reference=reference,
            evidence=evidence_attachment_id,
        )
        by_id: dict[object, LoanInstalment] = {item.id: item for item in rows}
        for item in allocations:
            self._apply(row, by_id[item.instalment_id], item.component, item.amount)
        if prepaid > ZERO:
            self._apply_prepayment(row, rows, when, prepaid)

        row.journal_entry_fk = self._post(
            actor,
            agreement,
            self._settlement_lines(agreement, account, row, reference),
            on=when,
            narration=f"Repayment, {agreement.number}: {agreement.counterparty_name}",
            key=f"loan_repayment:{row.id}",
            attachment=evidence_attachment_id,
        )
        self._db.flush()
        if prepaid > ZERO:
            self._recalculate_after_prepayment(actor, agreement, when, prepaid)
        self._close_if_repaid(agreement, when)
        self._audit.record(
            actor,
            "loan_repayment",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            after={
                "amount": str(amount),
                "on": when.isoformat(),
                "prepaid": str(prepaid),
                "allocation": {
                    component.value: str(
                        sum(
                            (
                                item.amount
                                for item in row.allocations
                                if item.component == component.value
                            ),
                            ZERO,
                        )
                    )
                    for component in Component
                },
            },
        )
        return row

    def _apply(
        self,
        transaction: LoanTransaction,
        instalment: LoanInstalment | None,
        component: Component,
        amount: Decimal,
    ) -> None:
        if instalment is not None:
            attribute = f"paid_{component.value}"
            setattr(instalment, attribute, getattr(instalment, attribute) + amount)
        allocation = LoanAllocation(
            business_id=transaction.business_id,
            transaction_fk=transaction.id,
            instalment_fk=instalment.id if instalment else None,
            component=component.value,
            amount=amount,
        )
        self._db.add(allocation)

    def _apply_prepayment(
        self,
        transaction: LoanTransaction,
        rows: Sequence[LoanInstalment],
        when: date,
        amount: Decimal,
    ) -> None:
        """Principal paid ahead, taken from the last instalments first: the schedule that
        replaces them is built on what is left."""
        remaining = amount
        for row in reversed([item for item in rows if item.due_on > when]):
            open_principal = row.principal - row.paid_principal
            if open_principal <= ZERO or remaining <= ZERO:
                continue
            taken = min(open_principal, remaining)
            self._apply(transaction, row, Component.PRINCIPAL, taken)
            remaining -= taken
        if remaining > ZERO:
            self._apply(transaction, None, Component.PRINCIPAL, remaining)

    def _recalculate_after_prepayment(
        self, actor: AnyActor, agreement: LoanAgreement, when: date, prepaid: Decimal
    ) -> None:
        """A new version for the instalments still to come, built on the principal left.
        The agreement permits it, so it is current at once; the version before it remains."""
        rows = self.open_instalments(agreement)
        future = [row for row in rows if row.due_on > when]
        principal_out = self.position(agreement, when).principal_outstanding
        if not future or principal_out <= ZERO:
            for row in future:
                row.closed = True
            self._db.flush()
            return
        current = self.current(agreement)
        terms = dict(current.terms)
        model = InterestModel(str(terms["model"]))
        if model is InterestModel.CUSTOM:
            return
        balloon = money(str(terms.get("balloon", "0")))
        rebuilt = LoanTerms(
            principal=principal_out,
            model=model,
            frequency=RepaymentFrequency(str(terms["frequency"])),
            instalments=len(future),
            first_due_on=future[0].due_on,
            annual_rate=money(str(terms["annual_rate"])),
            compounding=(
                RepaymentFrequency(str(terms["compounding"])) if terms.get("compounding") else None
            ),
            balloon=balloon if balloon < principal_out else ZERO,
            periodic_fee=money(str(terms.get("periodic_fee", "0"))),
            fixed_interest=_remaining_fixed(future) if model is InterestModel.FIXED else ZERO,
        )
        if rebuilt.model is InterestModel.BALLOON and rebuilt.balloon <= ZERO:
            rebuilt = _replace_model(rebuilt, InterestModel.REDUCING_BALANCE)
        if rebuilt.model is InterestModel.FIXED and rebuilt.fixed_interest <= ZERO:
            rebuilt = _replace_model(rebuilt, InterestModel.NONE)
        self._replace_schedule(
            actor,
            agreement,
            build_schedule(rebuilt),
            kind=RevisionKind.PREPAYMENT,
            reason=(
                f"Principal of {prepaid} prepaid on {when.isoformat()}, as the agreement allows."
            ),
            effective_on=when,
            basis=principal_out,
            terms=_terms_record(rebuilt),
            proposed_by=actor.user_id,
            decided_by=actor.user_id,
        )

    def charge_penalty(
        self,
        actor: AnyActor,
        agreement_id: uuid.UUID,
        *,
        reason: str,
        on: date | None = None,
        amount: Decimal | None = None,
    ) -> LoanTransaction:
        """A penalty on arrears, and only where the agreement provides for one."""
        actor.require("loan.manage")
        agreement = self._active(actor, agreement_id)
        if agreement.penalty_rate <= ZERO:
            raise ConflictError(
                f"{agreement.number} provides for no penalty, so none may be charged."
            )
        if not reason.strip():
            raise ValidationError("A penalty records why.", field="reason")
        when = on or self._today(agreement.business_id)
        for each in self.open_instalments(agreement):
            if each.due_on < when:
                self._accrue(actor, agreement, each)
        overdue = [
            row
            for row in self.open_instalments(agreement)
            if row.due_on < when and row.owed - row.penalty > row.paid - row.paid_penalty
        ]
        if not overdue:
            raise ConflictError(f"Nothing on {agreement.number} is overdue.")
        if amount is None:
            charge = sum(
                (
                    (row.owed - row.penalty - (row.paid - row.paid_penalty))
                    * agreement.penalty_rate
                    / Decimal(100)
                    * Decimal((when - row.due_on).days)
                    / Decimal(365)
                    for row in overdue
                ),
                ZERO,
            )
        else:
            charge = money(amount)
        charge = cents(charge)
        if charge <= ZERO:
            raise ValidationError("The penalty comes to nothing.", field="amount")
        target = overdue[0]
        target.penalty += charge
        accounts = ACCOUNTS[LoanDirection(agreement.direction)]
        borrowing = agreement.direction == LoanDirection.BORROWING.value
        party = self._party(agreement)
        label = f"Penalty, {agreement.number} instalment {target.sequence}"
        lines = (
            [
                EntryLine(accounts.fees, Side.DEBIT, charge, label),
                EntryLine(accounts.accrued, Side.CREDIT, charge, label, **party),
            ]
            if borrowing
            else [
                EntryLine(accounts.accrued, Side.DEBIT, charge, label, **party),
                EntryLine(accounts.fees, Side.CREDIT, charge, label),
            ]
        )
        row = self._transaction(
            actor, agreement, TransactionKind.PENALTY, when, charge, note=reason.strip()
        )
        row.journal_entry_fk = self._post(
            actor, agreement, lines, on=when, narration=label, key=f"loan_penalty:{row.id}"
        )
        self._db.flush()
        self._audit.record(
            actor,
            "loan_penalty_charged",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            reason=reason.strip(),
            after={"amount": str(charge)},
        )
        return row

    # -- settlement, refinancing, write-off ---------------------------------------------------

    def quote(
        self, actor: AnyActor, agreement_id: uuid.UUID, on: date | None = None
    ) -> SettlementQuote:
        agreement = self._active(actor, agreement_id)
        when = on or self._today(agreement.business_id)
        principal = self.position(agreement, when).principal_outstanding
        return self._quote(agreement, when, principal)

    def quote_for(self, agreement: LoanAgreement, on: date) -> SettlementQuote:
        """The settlement quotation on a date, for a reader that already holds the agreement."""
        return self._quote(agreement, on, self.position(agreement, on).principal_outstanding)

    def _quote(self, agreement: LoanAgreement, when: date, principal: Decimal) -> SettlementQuote:
        rows = self.open_instalments(agreement)
        due = [row for row in rows if row.due_on <= when]
        interest_due = sum((row.interest - row.paid_interest for row in due), ZERO)
        fees_due = sum((row.fees - row.paid_fees for row in due), ZERO)
        penalties = sum((row.penalty - row.paid_penalty for row in rows), ZERO)
        last_due = max(
            (row.due_on for row in due if not row.arrears),
            default=agreement.disbursement_date or agreement.agreement_date,
        )
        accrued = accrued_since(
            principal,
            agreement.annual_rate,
            InterestModel(agreement.model),
            last_due,
            when,
        )
        if InterestModel(agreement.model) in {InterestModel.FLAT, InterestModel.FIXED}:
            # A charge agreed in advance: settling early owes the part of the next instalment's
            # interest already earned, pro rata to the day, and releases the rest.
            upcoming = next((row for row in rows if row.due_on > when and not row.arrears), None)
            if upcoming is not None and upcoming.interest > upcoming.paid_interest:
                span = (upcoming.due_on - last_due).days or 1
                elapsed = max(0, (when - last_due).days)
                accrued = cents(
                    (upcoming.interest - upcoming.paid_interest)
                    * Decimal(min(elapsed, span))
                    / Decimal(span)
                )
        return SettlementQuote(
            on=when,
            principal=principal,
            interest_due=interest_due,
            interest_accrued=accrued,
            fees_due=fees_due,
            penalties_due=penalties,
            early_settlement_fee=early_settlement_fee(
                principal, agreement.early_settlement_percent
            ),
        )

    def settle(
        self,
        actor: AnyActor,
        agreement_id: uuid.UUID,
        *,
        on: date | None = None,
        financial_account_id: uuid.UUID | None = None,
        reference: str | None = None,
        evidence_attachment_id: uuid.UUID | None = None,
    ) -> LoanTransaction:
        """Close an agreement early, for the amount its quotation names.

        Interest to the day and any settlement fee are recognised first; the payment then
        clears principal and every charge, and the instalments still to come are released.
        """
        actor.require("repayment.manage")
        agreement = self._active(actor, agreement_id)
        when = on or self._today(agreement.business_id)
        account = self._account(actor, agreement, financial_account_id)
        rows = self.open_instalments(agreement)
        for each in rows:
            if each.due_on <= when:
                self._accrue(actor, agreement, each)
        principal = self.position(agreement, when).principal_outstanding
        quote = self._quote(agreement, when, principal)
        if quote.total <= ZERO:
            raise ConflictError(f"{agreement.number} owes nothing.")

        extra = quote.interest_accrued + quote.early_settlement_fee
        accounts = ACCOUNTS[LoanDirection(agreement.direction)]
        borrowing = agreement.direction == LoanDirection.BORROWING.value
        party = self._party(agreement)
        if extra > ZERO:
            charge_lines: list[EntryLine] = []
            for amount, income_or_cost, what in (
                (quote.interest_accrued, accounts.interest, "Interest to settlement"),
                (quote.early_settlement_fee, accounts.fees, "Early settlement fee"),
            ):
                if amount <= ZERO:
                    continue
                label = f"{what}, {agreement.number}"
                if borrowing:
                    charge_lines += [
                        EntryLine(income_or_cost, Side.DEBIT, amount, label),
                        EntryLine(accounts.accrued, Side.CREDIT, amount, label, **party),
                    ]
                else:
                    charge_lines += [
                        EntryLine(accounts.accrued, Side.DEBIT, amount, label, **party),
                        EntryLine(income_or_cost, Side.CREDIT, amount, label),
                    ]
            self._post(
                actor,
                agreement,
                charge_lines,
                on=when,
                narration=f"Settlement charges, {agreement.number}",
                key=f"loan_settlement_charges:{agreement.id}:{when.isoformat()}",
            )

        row = self._transaction(
            actor,
            agreement,
            TransactionKind.SETTLEMENT,
            when,
            quote.total,
            account=account.id,
            reference=reference,
            evidence=evidence_attachment_id,
        )
        order = parse_order(agreement.allocation_order)
        due = [_outstanding(item) for item in rows if item.accrued and item.owed > item.paid]
        due_total = sum((item.total for item in due), ZERO)
        allocations = allocate_repayment(due_total, due, order)[0] if due_total > ZERO else []
        by_id: dict[object, LoanInstalment] = {item.id: item for item in rows}
        settled_principal = ZERO
        for item in allocations:
            self._apply(row, by_id[item.instalment_id], item.component, item.amount)
            if item.component is Component.PRINCIPAL:
                settled_principal += item.amount
        future_principal = principal - settled_principal
        for instalment in rows:
            if future_principal <= ZERO:
                break
            if instalment.accrued:
                continue
            taken = min(instalment.principal - instalment.paid_principal, future_principal)
            if taken > ZERO:
                self._apply(row, instalment, Component.PRINCIPAL, taken)
                future_principal -= taken
        if future_principal > ZERO:
            self._apply(row, None, Component.PRINCIPAL, future_principal)
        if quote.interest_accrued > ZERO:
            self._apply(row, None, Component.INTEREST, quote.interest_accrued)
        if quote.early_settlement_fee > ZERO:
            self._apply(row, None, Component.FEES, quote.early_settlement_fee)

        row.journal_entry_fk = self._post(
            actor,
            agreement,
            self._settlement_lines(agreement, account, row, reference),
            on=when,
            narration=f"Early settlement, {agreement.number}: {agreement.counterparty_name}",
            key=f"loan_settlement:{row.id}",
            attachment=evidence_attachment_id,
        )
        for instalment in rows:
            instalment.closed = True
        agreement.state = AgreementState.SETTLED.value
        agreement.closed_on = when
        agreement.closing_note = f"Settled early for {quote.total}."
        self._db.flush()
        self._audit.record(
            actor,
            "loan_settled",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            after={
                "on": when.isoformat(),
                "principal": str(quote.principal),
                "interest": str(quote.interest_due + quote.interest_accrued),
                "fees": str(quote.fees_due + quote.early_settlement_fee),
                "penalties": str(quote.penalties_due),
                "total": str(quote.total),
            },
        )
        return row

    def refinance(
        self,
        actor: AnyActor,
        agreement_id: uuid.UUID,
        *,
        replacement_id: uuid.UUID,
        on: date | None = None,
    ) -> LoanTransaction:
        """Close an agreement by moving what it owes onto a new one with the same party.

        No cash moves: the old agreement's principal and accrued charges are transferred to
        the new agreement's principal. The old agreement and its full history remain.
        """
        actor.require("loan.restructure")
        old = self._active(actor, agreement_id)
        new = self._active(actor, replacement_id)
        if old.id == new.id or old.direction != new.direction:
            raise ConflictError(
                "A refinancing replaces an agreement with another in the same direction."
            )
        when = on or self._today(old.business_id)
        rows = self.open_instalments(old)
        for each in rows:
            if each.due_on <= when:
                self._accrue(actor, old, each)
        principal = self.position(old, when).principal_outstanding
        quote = self._quote(old, when, principal)
        moved = quote.total - quote.early_settlement_fee - quote.interest_accrued
        if moved <= ZERO:
            raise ConflictError(f"{old.number} owes nothing to refinance.")
        if moved > self.position(new, when).undrawn:
            raise ConflictError(
                f"{new.number} has {self.position(new, when).undrawn} left to draw; "
                f"refinancing {old.number} needs {moved}."
            )
        accounts = ACCOUNTS[LoanDirection(old.direction)]
        borrowing = old.direction == LoanDirection.BORROWING.value
        old_party = self._party(old)
        new_party = self._party(new)
        accrued_part = moved - principal
        lines: list[EntryLine] = []
        if borrowing:
            lines.append(
                EntryLine(accounts.principal, Side.DEBIT, principal, old.number, **old_party)
            )
            if accrued_part > ZERO:
                lines.append(
                    EntryLine(accounts.accrued, Side.DEBIT, accrued_part, old.number, **old_party)
                )
            lines.append(EntryLine(accounts.principal, Side.CREDIT, moved, new.number, **new_party))
        else:
            lines.append(EntryLine(accounts.principal, Side.DEBIT, moved, new.number, **new_party))
            lines.append(
                EntryLine(accounts.principal, Side.CREDIT, principal, old.number, **old_party)
            )
            if accrued_part > ZERO:
                lines.append(
                    EntryLine(accounts.accrued, Side.CREDIT, accrued_part, old.number, **old_party)
                )

        out = self._transaction(
            actor, old, TransactionKind.REFINANCE_OUT, when, moved, related=new.id
        )
        inward = self._transaction(
            actor, new, TransactionKind.REFINANCE_IN, when, moved, related=old.id
        )
        order = parse_order(old.allocation_order)
        due = [_outstanding(item) for item in rows if item.accrued and item.owed > item.paid]
        allocations, _ = allocate_repayment(moved, due, order) if due else ([], ZERO)
        by_id: dict[object, LoanInstalment] = {item.id: item for item in rows}
        for item in allocations:
            self._apply(out, by_id[item.instalment_id], item.component, item.amount)
        left = moved - sum((item.amount for item in allocations), ZERO)
        if left > ZERO:
            self._apply(out, None, Component.PRINCIPAL, left)
        entry = self._post(
            actor,
            old,
            lines,
            on=when,
            narration=f"{old.number} refinanced by {new.number}",
            key=f"loan_refinance:{old.id}:{new.id}",
        )
        out.journal_entry_fk = entry
        inward.journal_entry_fk = entry
        for instalment in rows:
            instalment.closed = True
        old.state = AgreementState.REFINANCED.value
        old.refinanced_by_fk = new.id
        old.closed_on = when
        old.closing_note = f"Refinanced by {new.number}."
        new.refinances_fk = old.id
        if new.disbursement_date is None:
            new.disbursement_date = when
        self._db.flush()
        self._audit.record(
            actor,
            "loan_refinanced",
            entity_type="loan_agreement",
            entity_id=old.id,
            entity_label=old.number,
            after={"replacement": new.number, "moved": str(moved)},
        )
        return out

    def write_off(
        self, actor: AnyActor, agreement_id: uuid.UUID, *, reason: str, on: date | None = None
    ) -> LoanTransaction:
        """Recognise that money lent will not come back. Only a loan issued can be written
        off; a debt the business owes is settled, refinanced or disputed, never wished away."""
        actor.require("debt.write_off")
        agreement = self._active(actor, agreement_id)
        if agreement.direction != LoanDirection.LENDING.value:
            raise ConflictError("Only a loan or advance issued by the business can be written off.")
        if not reason.strip():
            raise ValidationError("A write-off records why.", field="reason")
        when = on or self._today(agreement.business_id)
        rows = self.open_instalments(agreement)
        for each in rows:
            if each.due_on <= when:
                self._accrue(actor, agreement, each)
        position = self.position(agreement, when)
        accrued = position.interest_due + position.fees_due + position.penalties_due
        total = position.principal_outstanding + accrued
        if total <= ZERO:
            raise ConflictError(f"{agreement.number} owes nothing.")
        accounts = ACCOUNTS[LoanDirection.LENDING]
        party = self._party(agreement)
        label = f"Written off, {agreement.number}"
        lines = [EntryLine(BAD_DEBT_EXPENSE, Side.DEBIT, total, label)]
        if position.principal_outstanding > ZERO:
            lines.append(
                EntryLine(
                    accounts.principal, Side.CREDIT, position.principal_outstanding, label, **party
                )
            )
        if accrued > ZERO:
            lines.append(EntryLine(accounts.accrued, Side.CREDIT, accrued, label, **party))
        row = self._transaction(
            actor, agreement, TransactionKind.WRITE_OFF, when, total, note=reason.strip()
        )
        row.journal_entry_fk = self._post(
            actor, agreement, lines, on=when, narration=label, key=f"loan_write_off:{agreement.id}"
        )
        for instalment in rows:
            instalment.closed = True
        agreement.state = AgreementState.WRITTEN_OFF.value
        agreement.closed_on = when
        agreement.closing_note = reason.strip()
        self._db.flush()
        self._audit.record(
            actor,
            "loan_written_off",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            reason=reason.strip(),
            after={"principal": str(position.principal_outstanding), "charges": str(accrued)},
        )
        return row

    # -- revisions --------------------------------------------------------------------------

    def propose_revision(
        self, actor: AnyActor, agreement_id: uuid.UUID, draft: RevisionDraft
    ) -> LoanSchedule:
        """A revised schedule for what remains, proposed beside the current one. Nothing
        changes until it is approved."""
        actor.require("loan.restructure")
        agreement = self._active(actor, agreement_id)
        kind = RevisionKind(draft.kind)
        if kind in {RevisionKind.ORIGINAL, RevisionKind.PREPAYMENT}:
            raise ValidationError("Choose the kind of revision.", field="kind")
        reason = (draft.reason or "").strip()
        if not reason:
            raise ValidationError("A revision records why.", field="reason")
        if self.proposed(agreement) is not None:
            raise ConflictError(f"{agreement.number} already has a revision awaiting a decision.")
        if draft.effective_on > self._today(agreement.business_id):
            raise ValidationError(
                "A revision takes effect today or earlier; propose a future change when its "
                "day comes.",
                field="effective_on",
            )
        if draft.first_due_on <= draft.effective_on:
            raise ValidationError(
                "The first revised instalment falls after the revision takes effect.",
                field="first_due_on",
            )
        current = self.current(agreement)
        terms = current.terms
        principal = self.position(agreement, draft.effective_on).principal_outstanding
        if principal <= ZERO:
            raise ConflictError(f"No principal remains on {agreement.number} to reschedule.")
        model = InterestModel(draft.model or str(terms["model"]))
        if model is InterestModel.CUSTOM:
            raise ValidationError(
                "A revision is built from terms; a custom schedule cannot be revised this way.",
                field="model",
            )
        rate = (
            money(draft.annual_rate)
            if draft.annual_rate is not None
            else money(str(terms["annual_rate"]))
        )
        revised = LoanTerms(
            principal=principal,
            model=model,
            frequency=RepaymentFrequency(draft.frequency or str(terms["frequency"])),
            instalments=draft.instalments,
            first_due_on=draft.first_due_on,
            annual_rate=rate,
            compounding=(
                RepaymentFrequency(str(terms["compounding"])) if terms.get("compounding") else None
            ),
            grace_periods=draft.grace_periods,
            interest_only_periods=draft.interest_only_periods,
            balloon=money(draft.balloon),
            periodic_fee=(
                money(draft.periodic_fee)
                if draft.periodic_fee is not None
                else money(str(terms.get("periodic_fee", "0")))
            ),
            fixed_interest=(
                _remaining_fixed(
                    [
                        row
                        for row in self.open_instalments(agreement)
                        if row.due_on > draft.effective_on
                    ]
                )
                if model is InterestModel.FIXED
                else ZERO
            ),
        )
        rows = build_schedule(revised)
        version = self._new_version(
            agreement,
            rows,
            kind=kind,
            state=ScheduleState.PROPOSED,
            effective_on=draft.effective_on,
            basis=principal,
            terms=_terms_record(revised),
            proposed_by=actor.user_id,
            reason=reason,
            evidence=draft.evidence_attachment_id,
        )
        self._audit.record(
            actor,
            "loan_revision_proposed",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            reason=reason,
            after={
                "version": version.version,
                "kind": kind.value,
                "effective_on": draft.effective_on.isoformat(),
                "principal": str(principal),
                "rate": str(rate),
                "instalments": len(rows),
            },
        )
        return version

    def decide_revision(
        self,
        actor: AnyActor,
        agreement_id: uuid.UUID,
        *,
        approve: bool,
        note: str | None = None,
    ) -> LoanSchedule:
        """Approve or decline the proposed revision. Approval supersedes the current version,
        which remains readable exactly as it stood; arrears are carried into the new one."""
        actor.require("loan.restructure")
        agreement = self._active(actor, agreement_id)
        version = self.proposed(agreement)
        if version is None:
            raise ConflictError(f"{agreement.number} has no revision awaiting a decision.")
        text = (note or "").strip() or None
        threshold = self.threshold(agreement.business_id)
        material = threshold is not None and version.principal_basis >= threshold
        if material and version.proposed_by_id == actor.user_id and not actor.is_administrator:
            raise ConflictError("A revision is approved by someone other than whoever proposed it.")
        if not approve:
            if text is None:
                raise ValidationError("Declining a revision records why.", field="note")
            version.state = ScheduleState.DECLINED.value
            version.decided_by_id = actor.user_id
            version.decided_at = self._clock.now()
            version.decision_note = text
            self._db.flush()
            self._audit.record(
                actor,
                "loan_revision_declined",
                entity_type="loan_agreement",
                entity_id=agreement.id,
                entity_label=agreement.number,
                reason=text,
                after={"version": version.version},
            )
            return version
        if material and text is None and version.evidence_attachment_fk is None:
            raise ValidationError(
                "A material revision rests on documented authorisation. Attach it, or record "
                "who authorised it and how.",
                field="note",
            )
        principal = self.position(agreement, version.effective_on).principal_outstanding
        if cents(principal) != cents(version.principal_basis):
            raise ConflictError(
                f"{agreement.number} has moved since this revision was proposed: it was built "
                f"on {version.principal_basis} of principal and {principal} remains. Decline it "
                "and propose it again."
            )
        for each in self.open_instalments(agreement):
            if each.due_on <= version.effective_on:
                self._accrue(actor, agreement, each)
        self._supersede(agreement, version)
        version.state = ScheduleState.CURRENT.value
        version.decided_by_id = actor.user_id
        version.decided_at = self._clock.now()
        version.decision_note = text
        agreement.maturity_date = max(row.due_on for row in version.instalments)
        self._db.flush()
        self._audit.record(
            actor,
            "loan_revision_approved",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            reason=version.reason,
            after={
                "version": version.version,
                "kind": version.kind,
                "note": text,
                "maturity": agreement.maturity_date.isoformat()
                if agreement.maturity_date
                else None,
            },
        )
        return version

    def _replace_schedule(
        self,
        actor: AnyActor,
        agreement: LoanAgreement,
        rows: list[ScheduledInstalment],
        *,
        kind: RevisionKind,
        reason: str,
        effective_on: date,
        basis: Decimal,
        terms: dict[str, Any],
        proposed_by: uuid.UUID | None,
        decided_by: uuid.UUID | None,
    ) -> LoanSchedule:
        version = self._new_version(
            agreement,
            rows,
            kind=kind,
            state=ScheduleState.PROPOSED,
            effective_on=effective_on,
            basis=basis,
            terms=terms,
            proposed_by=proposed_by,
            reason=reason,
        )
        self._supersede(agreement, version)
        version.state = ScheduleState.CURRENT.value
        version.decided_by_id = decided_by
        version.decided_at = self._clock.now()
        agreement.maturity_date = max(row.due_on for row in version.instalments)
        self._db.flush()
        self._audit.record(
            actor,
            "loan_schedule_recalculated",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            reason=reason,
            after={"version": version.version, "kind": kind.value},
        )
        return version

    def _supersede(self, agreement: LoanAgreement, incoming: LoanSchedule) -> None:
        """Retire the current version. Charges already recognised and unpaid are carried into
        the incoming version as an arrears row, so nothing owed is lost and nothing is owed
        twice."""
        current = self.current(agreement)
        open_rows = self.open_instalments(agreement)
        interest = sum((row.interest - row.paid_interest for row in open_rows if row.accrued), ZERO)
        fees = sum((row.fees - row.paid_fees for row in open_rows if row.accrued), ZERO)
        penalty = sum((row.penalty - row.paid_penalty for row in open_rows), ZERO)
        for row in open_rows:
            row.closed = True
        current.state = ScheduleState.SUPERSEDED.value
        if interest + fees + penalty > ZERO:
            self._db.add(
                LoanInstalment(
                    business_id=agreement.business_id,
                    schedule_fk=incoming.id,
                    agreement_fk=agreement.id,
                    sequence=0,
                    due_on=incoming.effective_on,
                    opening=incoming.principal_basis,
                    interest=interest,
                    fees=fees,
                    principal=ZERO,
                    closing=incoming.principal_basis,
                    penalty=penalty,
                    arrears=True,
                    accrued=True,
                )
            )
        self._db.flush()
        self._db.refresh(incoming)

    # -- security and standing ----------------------------------------------------------------

    def add_security(
        self,
        actor: AnyActor,
        agreement_id: uuid.UUID,
        *,
        kind: SecurityKind | str,
        description: str,
        party_name: str | None = None,
        value: Decimal | None = None,
        review_on: date | None = None,
        attachment_id: uuid.UUID | None = None,
    ) -> LoanSecurity:
        actor.require("loan.manage")
        agreement = self.require(actor, agreement_id)
        text = (description or "").strip()
        if not text:
            raise ValidationError("Describe the security.", field="description")
        row = LoanSecurity(
            business_id=agreement.business_id,
            agreement_fk=agreement.id,
            kind=SecurityKind(kind).value,
            description=text,
            party_name=(party_name or "").strip() or None,
            value=money(value) if value is not None else None,
            review_on=review_on,
            attachment_fk=attachment_id,
        )
        self._db.add(row)
        self._db.flush()
        self._audit.record(
            actor,
            "loan_security_added",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            after={"kind": row.kind, "description": text},
        )
        return row

    def release_security(
        self, actor: AnyActor, security_id: uuid.UUID, *, on: date | None = None
    ) -> LoanSecurity:
        actor.require("loan.manage")
        row = self._db.get(LoanSecurity, security_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Security not found.")
        if row.released_on is not None:
            raise ConflictError("That security has already been released.")
        row.released_on = on or self._today(row.business_id)
        self._db.flush()
        self._audit.record(
            actor,
            "loan_security_released",
            entity_type="loan_agreement",
            entity_id=row.agreement_fk,
            after={"description": row.description},
        )
        return row

    def set_disputed(
        self, actor: AnyActor, agreement_id: uuid.UUID, *, disputed: bool, note: str
    ) -> LoanAgreement:
        actor.require("loan.manage")
        agreement = self.require(actor, agreement_id)
        if not note.strip():
            raise ValidationError("Record what the dispute is about.", field="note")
        agreement.disputed = disputed
        self._db.flush()
        self._audit.record(
            actor,
            "loan_dispute_raised" if disputed else "loan_dispute_resolved",
            entity_type="loan_agreement",
            entity_id=agreement.id,
            entity_label=agreement.number,
            reason=note.strip(),
        )
        return agreement

    # -- internals --------------------------------------------------------------------------

    def _validated(self, actor: AnyActor, draft: LoanDraft) -> dict[str, Any]:
        business_id = _business_of(actor)
        name = " ".join((draft.counterparty_name or "").split())
        if not name:
            raise ValidationError("Name the lender or borrower.", field="counterparty_name")
        direction = LoanDirection(draft.direction)
        loan_type = LoanType(draft.loan_type)
        if draft.counterparty_party_id is not None:
            party = self._db.get(Party, draft.counterparty_party_id)
            if party is None or party.business_id != business_id:
                raise NotFoundError("Counterparty not found.")
        if draft.counterparty_employee_id is not None:
            employee = self._db.get(User, draft.counterparty_employee_id)
            if employee is None or employee.business_id != business_id:
                raise NotFoundError("Employee not found.")
        if loan_type is LoanType.STAFF_ADVANCE and direction is not LoanDirection.LENDING:
            raise ValidationError("A staff advance is money the business lends.", field="loan_type")
        if draft.financial_account_id is not None:
            self._treasury.require(actor, draft.financial_account_id)
        if draft.first_due_on < draft.agreement_date:
            raise ValidationError(
                "The first instalment falls on or after the agreement date.", field="first_due_on"
            )
        for value, label in (
            (draft.upfront_fee, "upfront_fee"),
            (draft.penalty_rate, "penalty_rate"),
            (draft.early_settlement_percent, "early_settlement_percent"),
        ):
            if money(value) < ZERO:
                raise ValidationError("This cannot be negative.", field=label)
        order = parse_order(draft.allocation_order)
        terms = draft.terms()
        terms.validate()
        business = self._db.get(Business, business_id)
        return {
            "direction": direction.value,
            "loan_type": loan_type.value,
            "counterparty_name": name,
            "counterparty_party_fk": draft.counterparty_party_id,
            "counterparty_employee_id": draft.counterparty_employee_id,
            "agreement_reference": (draft.agreement_reference or "").strip() or None,
            "purpose": (draft.purpose or "").strip() or None,
            "currency_code": business.base_currency if business else "GHS",
            "principal": cents(terms.principal),
            "agreement_date": draft.agreement_date,
            "disbursement_date": draft.disbursement_date,
            "model": terms.model.value,
            "annual_rate": terms.annual_rate,
            "frequency": terms.frequency.value,
            "compounding": terms.compounding.value if terms.compounding else None,
            "instalments": len(terms.custom) if terms.custom else terms.instalments,
            "first_due_on": terms.custom[0].due_on if terms.custom else terms.first_due_on,
            "grace_periods": terms.grace_periods,
            "interest_only_periods": terms.interest_only_periods,
            "balloon": terms.balloon,
            "periodic_fee": terms.periodic_fee,
            "fixed_interest": terms.fixed_interest,
            "upfront_fee": money(draft.upfront_fee),
            "penalty_rate": money(draft.penalty_rate),
            "early_settlement_percent": money(draft.early_settlement_percent),
            "prepayment_allowed": bool(draft.prepayment_allowed),
            "allocation_order": [component.value for component in order],
            "custom_rows": [
                {
                    "due_on": row.due_on.isoformat(),
                    "principal": str(row.principal),
                    "interest": str(row.interest),
                    "fees": str(row.fees),
                }
                for row in terms.custom
            ]
            or None,
            "financial_account_fk": draft.financial_account_id,
            "responsible_id": draft.responsible_id,
            "agreement_attachment_fk": draft.agreement_attachment_id,
            "notes": (draft.notes or "").strip() or None,
        }

    def _new_version(
        self,
        agreement: LoanAgreement,
        rows: list[ScheduledInstalment],
        *,
        kind: RevisionKind,
        state: ScheduleState,
        effective_on: date,
        basis: Decimal,
        terms: dict[str, Any],
        proposed_by: uuid.UUID | None,
        reason: str | None = None,
        evidence: uuid.UUID | None = None,
    ) -> LoanSchedule:
        number = max((version.version for version in agreement.schedules), default=0) + 1
        version = LoanSchedule(
            business_id=agreement.business_id,
            agreement_fk=agreement.id,
            version=number,
            kind=kind.value,
            state=state.value,
            reason=reason,
            effective_on=effective_on,
            principal_basis=basis,
            terms=terms,
            proposed_by_id=proposed_by,
            evidence_attachment_fk=evidence,
        )
        self._db.add(version)
        self._db.flush()
        self._add_rows(agreement, version, rows)
        self._db.refresh(agreement)
        return version

    def _add_rows(
        self, agreement: LoanAgreement, version: LoanSchedule, rows: list[ScheduledInstalment]
    ) -> None:
        for each in rows:
            self._db.add(
                LoanInstalment(
                    business_id=agreement.business_id,
                    schedule_fk=version.id,
                    agreement_fk=agreement.id,
                    sequence=each.sequence,
                    due_on=each.due_on,
                    opening=each.opening,
                    interest=each.interest,
                    fees=each.fees,
                    principal=each.principal,
                    capitalised=each.capitalised,
                    closing=each.closing,
                )
            )
        self._db.flush()
        self._db.refresh(version)

    def _version(self, agreement: LoanAgreement, number: int) -> LoanSchedule:
        for version in agreement.schedules:
            if version.version == number:
                return version
        raise NotFoundError("Schedule version not found.")

    def _active(self, actor: AnyActor, agreement_id: uuid.UUID) -> LoanAgreement:
        agreement = self.require(actor, agreement_id)
        if agreement.state != AgreementState.ACTIVE.value:
            raise ConflictError(
                f"{agreement.number} is {AgreementState(agreement.state).label.lower()}, "
                "so nothing more can be recorded against it."
            )
        return agreement

    def _account(
        self, actor: AnyActor, agreement: LoanAgreement, account_id: uuid.UUID | None
    ) -> FinancialAccount:
        chosen = account_id or agreement.financial_account_fk
        if chosen is None:
            raise ValidationError(
                "Name the account the money moves through.", field="financial_account_id"
            )
        account = self._treasury.require(actor, chosen)
        if not account.is_active:
            raise ConflictError(f"{account.name} is closed.")
        return account

    def _party(self, agreement: LoanAgreement) -> dict[str, Any]:
        return {
            "party_id": agreement.id,
            "subledger": ACCOUNTS[LoanDirection(agreement.direction)].subledger,
        }

    def _settlement_lines(
        self,
        agreement: LoanAgreement,
        account: FinancialAccount,
        transaction: LoanTransaction,
        reference: str | None,
    ) -> list[EntryLine]:
        """The cash against principal and accrued charges, by what the allocation settled."""
        accounts = ACCOUNTS[LoanDirection(agreement.direction)]
        borrowing = agreement.direction == LoanDirection.BORROWING.value
        party = self._party(agreement)
        self._db.flush()
        principal = sum(
            self._db.scalars(
                select(LoanAllocation.amount).where(
                    LoanAllocation.transaction_fk == transaction.id,
                    LoanAllocation.component == Component.PRINCIPAL.value,
                )
            ),
            ZERO,
        )
        charges = transaction.amount - principal
        cash = self._treasury.ledger_code(account)
        label = reference or agreement.number
        loan_side = Side.DEBIT if borrowing else Side.CREDIT
        cash_side = Side.CREDIT if borrowing else Side.DEBIT
        lines = [EntryLine(cash, cash_side, transaction.amount, label)]
        if principal > ZERO:
            lines.append(
                EntryLine(
                    accounts.principal,
                    loan_side,
                    principal,
                    f"Principal, {agreement.number}",
                    **party,
                )
            )
        if charges > ZERO:
            lines.append(
                EntryLine(
                    accounts.accrued,
                    loan_side,
                    charges,
                    f"Interest and charges, {agreement.number}",
                    **party,
                )
            )
        return lines

    def _transaction(
        self,
        actor: AnyActor,
        agreement: LoanAgreement,
        kind: str,
        on: date,
        amount: Decimal,
        *,
        fee: Decimal = ZERO,
        account: uuid.UUID | None = None,
        reference: str | None = None,
        evidence: uuid.UUID | None = None,
        note: str | None = None,
        related: uuid.UUID | None = None,
    ) -> LoanTransaction:
        row = LoanTransaction(
            business_id=agreement.business_id,
            agreement_fk=agreement.id,
            kind=kind,
            occurred_on=on,
            amount=amount,
            fee=fee,
            financial_account_fk=account,
            reference=(reference or "").strip() or None,
            evidence_attachment_fk=evidence,
            note=note,
            recorded_by_id=actor.user_id,
            related_agreement_fk=related,
        )
        self._db.add(row)
        self._db.flush()
        return row

    def _post(
        self,
        actor: AnyActor,
        agreement: LoanAgreement,
        lines: list[EntryLine],
        *,
        on: date,
        narration: str,
        key: str,
        attachment: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return (
            JournalService(self._db, clock=self._clock)
            .post(
                actor,
                JournalDraft(
                    posting_date=on,
                    narration=narration[:240],
                    lines=lines,
                    source=JournalSource.FINANCING,
                    source_reference=agreement.number,
                    source_id=agreement.id,
                    idempotency_key=key,
                    currency_code=agreement.currency_code,
                    attachment_ids=[str(attachment)] if attachment else [],
                    branch_id=actor.branch_id,
                ),
            )
            .entry.id
        )

    def _close_if_repaid(self, agreement: LoanAgreement, on: date) -> None:
        position = self.position(agreement, on)
        if (
            position.undrawn <= ZERO
            and position.total_outstanding <= ZERO
            and all(row.owed <= row.paid for row in self.open_instalments(agreement))
        ):
            agreement.state = AgreementState.SETTLED.value
            agreement.closed_on = on
            agreement.closing_note = "Repaid in full."
            for each in self.open_instalments(agreement):
                each.closed = True
            self._db.flush()

    def number_of(self, agreement_id: uuid.UUID) -> str | None:
        row = self._db.get(LoanAgreement, agreement_id)
        return row.number if row else None

    def today(self, business_id: uuid.UUID) -> date:
        return self._today(business_id)

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


def _outstanding(row: LoanInstalment) -> Outstanding:
    return Outstanding(
        instalment_id=row.id,
        sequence=row.sequence,
        due_on=row.due_on,
        owed={
            Component.PENALTY: row.penalty - row.paid_penalty,
            Component.FEES: row.fees - row.paid_fees,
            Component.INTEREST: row.interest - row.paid_interest,
            Component.PRINCIPAL: row.principal - row.paid_principal,
        },
    )


def _remaining_fixed(rows: Sequence[LoanInstalment]) -> Decimal:
    return sum((row.interest - row.paid_interest for row in rows if not row.accrued), ZERO)


def _replace_model(terms: LoanTerms, model: InterestModel) -> LoanTerms:
    return LoanTerms(
        principal=terms.principal,
        model=model,
        frequency=terms.frequency,
        instalments=terms.instalments,
        first_due_on=terms.first_due_on,
        annual_rate=terms.annual_rate,
        compounding=terms.compounding,
        grace_periods=terms.grace_periods,
        interest_only_periods=terms.interest_only_periods,
        balloon=terms.balloon,
        periodic_fee=terms.periodic_fee,
        fixed_interest=terms.fixed_interest,
    )


def _terms_record(terms: LoanTerms) -> dict[str, Any]:
    return {
        "principal": str(terms.principal),
        "model": terms.model.value,
        "frequency": terms.frequency.value,
        "instalments": terms.instalments,
        "first_due_on": terms.first_due_on.isoformat(),
        "annual_rate": str(terms.annual_rate),
        "compounding": terms.compounding.value if terms.compounding else None,
        "grace_periods": terms.grace_periods,
        "interest_only_periods": terms.interest_only_periods,
        "balloon": str(terms.balloon),
        "periodic_fee": str(terms.periodic_fee),
        "fixed_interest": str(terms.fixed_interest),
        "custom": [
            {
                "due_on": row.due_on.isoformat(),
                "principal": str(row.principal),
                "interest": str(row.interest),
                "fees": str(row.fees),
            }
            for row in terms.custom
        ],
    }


def _summary(agreement: LoanAgreement) -> dict[str, Any]:
    return {
        "direction": agreement.direction,
        "type": agreement.loan_type,
        "counterparty": agreement.counterparty_name,
        "principal": str(agreement.principal),
        "model": agreement.model,
        "rate": str(agreement.annual_rate),
        "frequency": agreement.frequency,
        "instalments": agreement.instalments,
        "state": agreement.state,
    }


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
