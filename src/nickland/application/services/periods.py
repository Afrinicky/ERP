"""Period close, reopening and the year end.

A period is closed only when the ledger within it has been shown to be sound. The
gates are run before anything is locked and recorded with their measurements, so that
a close is evidence of a reconciliation and not merely a flag, per PRD §29.2.

Reopening a closed period is permitted, because a late correction is a fact of
business life, but it is never silent: it requires authority, a reason, and leaves an
audit entry naming the person and what they gave as the reason.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.accounts import (
    CURRENT_YEAR_RESULT,
    RETAINED_EARNINGS,
    AccountService,
)
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.ledger import (
    AccountingBasis,
    AccountKind,
    EntryLine,
    JournalSource,
    JournalState,
    PeriodState,
    Side,
)
from nickland.domain.money import ZERO, money
from nickland.persistence.finance_models import BankStatement, Expense
from nickland.persistence.ledger_models import (
    AccountingPeriod,
    JournalEntry,
    PeriodCloseCheck,
)
from nickland.persistence.pos_models import PosShift
from nickland.persistence.reporting_models import PeriodChecklistItem, PeriodReopenRequest
from nickland.platform.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class CloseCheck:
    """One reconciliation gate and what it found."""

    name: str
    passed: bool
    detail: str
    measurements: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CloseReadiness:
    """Whether a period may be closed, and what stands in the way."""

    period: AccountingPeriod
    checks: list[CloseCheck]

    @property
    def ready(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def obstacles(self) -> list[CloseCheck]:
        return [check for check in self.checks if not check.passed]


CHECKLIST: tuple[tuple[str, str], ...] = (
    ("accruals", "Accruals, prepayments and recurring charges reviewed"),
    ("stock", "Stock counted or confirmed against the ledger"),
    ("tax", "Tax obligations for the period reviewed"),
    ("pack", "Management report for the period reviewed"),
)
"""The steps a person confirms. The system measures everything else."""


@dataclass(frozen=True, slots=True)
class ChecklistStep:
    key: str
    label: str
    done: bool
    done_by_id: uuid.UUID | None
    done_at: datetime | None
    note: str | None


@dataclass(frozen=True, slots=True)
class Checklist:
    period: AccountingPeriod
    checks: list[CloseCheck]
    steps: list[ChecklistStep]

    @property
    def ready(self) -> bool:
        return all(check.passed for check in self.checks) and all(step.done for step in self.steps)


@dataclass(frozen=True, slots=True)
class YearEndOutcome:
    """What the year-end close produced."""

    period: AccountingPeriod
    entry: JournalEntry | None
    accounts_closed: int
    net_result: Decimal


class PeriodService:
    """Closes, reopens and finalises accounting periods."""

    __slots__ = ("_accounts", "_audit", "_clock", "_db", "_journals", "_reports")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._accounts = AccountService(session, clock=self._clock)
        self._journals = JournalService(session, clock=self._clock)
        self._reports = LedgerReportService(session, clock=self._clock)

    # --------------------------------------------------------------- readiness

    def readiness(self, actor: AnyActor, period_id: uuid.UUID) -> CloseReadiness:
        """Run the gates without closing anything.

        The finance screen shows this before the button is offered, so that a close is
        never attempted blind.
        """
        actor.require("account.view")
        period = self._require_period(actor, period_id)
        return CloseReadiness(period=period, checks=self._run_checks(actor, period))

    # ------------------------------------------------------------------- close

    def close(
        self, actor: AnyActor, period_id: uuid.UUID, *, note: str | None = None
    ) -> CloseReadiness:
        """Close a period, provided every gate passes."""
        actor.require("period.close")
        period = self._require_period(actor, period_id)

        if period.state == PeriodState.CLOSED.value:
            raise ConflictError(f"{period.label} is already closed.", period=period.label)

        readiness = CloseReadiness(period=period, checks=self._run_checks(actor, period))
        now = self._clock.now()
        for check in readiness.checks:
            self._db.add(
                PeriodCloseCheck(
                    business_id=period.business_id,
                    period_fk=period.id,
                    name=check.name,
                    passed=check.passed,
                    detail=check.detail,
                    measurements=check.measurements,
                    checked_at=now,
                )
            )

        if not readiness.ready:
            self._audit.record(
                actor,
                "period_close_refused",
                entity_type="accounting_period",
                entity_id=period.id,
                entity_label=period.label,
                after={"obstacles": [check.name for check in readiness.obstacles]},
            )
            raise ConflictError(
                f"{period.label} cannot be closed yet. "
                + " ".join(check.detail for check in readiness.obstacles),
                period=period.label,
                obstacles=[check.name for check in readiness.obstacles],
            )

        period.state = PeriodState.CLOSED.value
        period.closed_at = now
        period.closed_by_id = actor.user_id

        self._audit.record(
            actor,
            "period_closed",
            entity_type="accounting_period",
            entity_id=period.id,
            entity_label=period.label,
            reason=note,
            before={"state": PeriodState.OPEN.value},
            after={"state": period.state, "checks": len(readiness.checks)},
        )
        return readiness

    def reopen(
        self,
        actor: AnyActor,
        period_id: uuid.UUID,
        reason: str,
        *,
        approved_by_id: uuid.UUID | None = None,
    ) -> AccountingPeriod:
        """Reopen a closed period at once. Only the administrator may: anyone else asks,
        and a second person decides (``request_reopen`` and ``decide_reopen``)."""
        actor.require("period.reopen")
        if not actor.is_administrator:
            raise ConflictError(
                "Ask for the period to be reopened; a second person approves the request."
            )
        period = self._require_period(actor, period_id)
        return self._reopen(
            actor,
            period,
            reason,
            approved_by_id=approved_by_id,
            requested_by_id=actor.user_id,
            self_approved=approved_by_id in (None, actor.user_id),
        )

    def _reopen(
        self,
        actor: AnyActor,
        period: AccountingPeriod,
        reason: str,
        *,
        approved_by_id: uuid.UUID | None,
        requested_by_id: uuid.UUID | None,
        self_approved: bool,
    ) -> AccountingPeriod:
        if len(reason.strip()) < 10:
            raise ValidationError(
                "Give a substantive reason for reopening a closed period. It is read "
                "by whoever reviews the ledger afterwards.",
                field="reason",
            )
        if period.state == PeriodState.OPEN.value:
            raise ConflictError(f"{period.label} is already open.", period=period.label)
        if period.is_year_end and period.year_end_posted_at is not None:
            raise ConflictError(
                f"{period.label} carries the year-end close. Reverse the year-end "
                "entry before reopening the period, so the reserves stay correct.",
                period=period.label,
            )
        later = self._db.scalar(
            select(func.count())
            .select_from(AccountingPeriod)
            .where(
                AccountingPeriod.business_id == period.business_id,
                AccountingPeriod.starts_on > period.starts_on,
                AccountingPeriod.state == PeriodState.CLOSED.value,
            )
        )
        if later:
            raise ConflictError(
                f"{int(later)} later period(s) are closed. Reopen them in reverse "
                "order, so no period is ever open beneath a closed one.",
                period=period.label,
            )

        now = self._clock.now()
        period.state = PeriodState.REOPENED.value
        period.reopened_at = now
        period.reopened_by_id = requested_by_id
        period.reopen_reason = reason.strip()
        period.reopen_approved_by_id = approved_by_id or actor.user_id

        self._audit.record(
            actor,
            "period_reopened",
            entity_type="accounting_period",
            entity_id=period.id,
            entity_label=period.label,
            reason=reason.strip(),
            before={"state": PeriodState.CLOSED.value},
            after={
                "state": period.state,
                "requested_by": str(requested_by_id) if requested_by_id else None,
                "approved_by": str(period.reopen_approved_by_id)
                if period.reopen_approved_by_id
                else None,
                "self_approved": self_approved,
            },
        )
        return period

    # ---------------------------------------------------------------- year end

    def close_year(
        self, actor: AnyActor, financial_year: int, *, note: str | None = None
    ) -> YearEndOutcome:
        """Carry the year's result to reserves and close the final period.

        Revenue and expense accounts measure one year and begin the next at nothing.
        The closing entry moves each balance to zero against retained earnings, so the
        new year opens with the position carried forward and the result banked.
        """
        actor.require("period.close")
        business_id = _business_of(actor)

        periods = list(
            self._db.scalars(
                select(AccountingPeriod)
                .where(
                    AccountingPeriod.business_id == business_id,
                    AccountingPeriod.financial_year == financial_year,
                )
                .order_by(AccountingPeriod.sequence)
            ).all()
        )
        if not periods:
            raise NotFoundError(f"Financial year {financial_year} has not been opened.")

        final = periods[-1]
        if final.year_end_posted_at is not None:
            raise ConflictError(
                f"Financial year {financial_year} has already been closed.",
                period=final.label,
            )
        still_open = [
            period.label for period in periods[:-1] if period.state != PeriodState.CLOSED.value
        ]
        if still_open:
            raise ConflictError(
                "Close every period of the year before the year end: "
                f"{', '.join(still_open)} remain open.",
                periods=still_open,
            )

        readiness = CloseReadiness(period=final, checks=self._run_checks(actor, final))
        if not readiness.ready:
            raise ConflictError(
                f"{final.label} cannot be closed yet. "
                + " ".join(check.detail for check in readiness.obstacles),
                period=final.label,
                obstacles=[check.name for check in readiness.obstacles],
            )

        lines: list[EntryLine] = []
        closed = 0
        net = ZERO
        for account, balance in self._reports.balances(
            business_id,
            start=periods[0].starts_on,
            end=final.ends_on,
            basis=AccountingBasis.ACCRUAL,
        ):
            kind = AccountKind(account.kind)
            if not kind.closes_at_year_end or balance == ZERO:
                continue
            closed += 1
            net = money(net + (balance if kind is AccountKind.REVENUE else -balance))
            lines.append(
                EntryLine(
                    account_code=account.code,
                    side=(kind.normal_balance.opposite if balance > ZERO else kind.normal_balance),
                    amount=abs(balance),
                    narration=f"Year-end close of {account.name}",
                )
            )

        entry = None
        if lines:
            reserves = self._accounts.require_by_code(business_id, RETAINED_EARNINGS)
            lines.append(
                EntryLine(
                    account_code=reserves.code,
                    side=Side.CREDIT if net > ZERO else Side.DEBIT,
                    amount=abs(net),
                    narration=f"Result for financial year {financial_year}",
                )
            )
            outcome = self._journals.post(
                actor,
                JournalDraft(
                    posting_date=final.ends_on,
                    narration=(
                        f"Year-end close for financial year {financial_year}. "
                        f"{'Profit' if net > ZERO else 'Loss'} of {abs(net)} "
                        "carried to retained earnings."
                    ),
                    lines=lines,
                    source=JournalSource.YEAR_END,
                    source_reference=str(financial_year),
                    idempotency_key=f"year-end:{business_id}:{financial_year}",
                ),
            )
            entry = outcome.entry

        now = self._clock.now()
        final.is_year_end = True
        final.year_end_posted_at = now
        if final.state != PeriodState.CLOSED.value:
            final.state = PeriodState.CLOSED.value
            final.closed_at = now
            final.closed_by_id = actor.user_id

        self._audit.record(
            actor,
            "financial_year_closed",
            entity_type="accounting_period",
            entity_id=final.id,
            entity_label=f"Financial year {financial_year}",
            reason=note,
            after={
                "net_result": str(net),
                "accounts_closed": closed,
                "entry": entry.number if entry else None,
            },
        )
        return YearEndOutcome(period=final, entry=entry, accounts_closed=closed, net_result=net)

    # -------------------------------------------------------------- the gates

    def _run_checks(self, actor: AnyActor, period: AccountingPeriod) -> list[CloseCheck]:
        """Every reconciliation a close depends upon."""
        return [
            self._check_earlier_periods(period),
            self._check_nothing_outstanding(period),
            self._check_entries_balance(period),
            self._check_trial_balance(actor, period),
            self._check_subledgers(actor, period),
            *self._check_operations(period),
            self._check_confirmations(period),
        ]

    def _check_operations(self, period: AccountingPeriod) -> list[CloseCheck]:
        """What the operations owe the ledger before the month can be shut: every till
        counted, every expense decided, every bank statement reconciled."""
        shifts = list(
            self._db.scalars(
                select(PosShift.number).where(
                    PosShift.business_id == period.business_id,
                    PosShift.business_day <= period.ends_on,
                    PosShift.state.in_(("open", "submitted")),
                )
            ).all()
        )
        expenses = list(
            self._db.scalars(
                select(Expense.number).where(
                    Expense.business_id == period.business_id,
                    Expense.incurred_on <= period.ends_on,
                    Expense.state == "awaiting_approval",
                )
            ).all()
        )
        statements = list(
            self._db.scalars(
                select(BankStatement.reference).where(
                    BankStatement.business_id == period.business_id,
                    BankStatement.starts_on <= period.ends_on,
                    BankStatement.state != "reconciled",
                )
            ).all()
        )

        def listed(items: Sequence[str | None]) -> str:
            names = [str(item) for item in items if item]
            shown = ", ".join(names[:5])
            return shown + (f" and {len(names) - 5} more" if len(names) > 5 else "")

        return [
            CloseCheck(
                name="shifts_closed",
                passed=not shifts,
                detail="Every point of sale shift in the period is counted and closed."
                if not shifts
                else f"Close the till shifts still open: {listed(shifts)}.",
                measurements={"open": [str(item) for item in shifts]},
            ),
            CloseCheck(
                name="expenses_decided",
                passed=not expenses,
                detail="No expense in the period awaits approval."
                if not expenses
                else f"Approve or reject the expenses awaiting a decision: {listed(expenses)}.",
                measurements={"awaiting": [str(item) for item in expenses]},
            ),
            CloseCheck(
                name="statements_reconciled",
                passed=not statements,
                detail="Every bank and mobile money statement to the period end is reconciled."
                if not statements
                else f"Reconcile the statements still open: {listed(statements)}.",
                measurements={"open": [str(item) for item in statements]},
            ),
        ]

    def _check_confirmations(self, period: AccountingPeriod) -> CloseCheck:
        done = self._confirmed(period)
        missing = [label for key, label in CHECKLIST if key not in done]
        return CloseCheck(
            name="checklist_confirmed",
            passed=not missing,
            detail="Every step of the close checklist is confirmed."
            if not missing
            else "Confirm the remaining steps of the checklist: " + "; ".join(missing) + ".",
            measurements={"missing": missing},
        )

    def _confirmed(self, period: AccountingPeriod) -> set[str]:
        return set(
            self._db.scalars(
                select(PeriodChecklistItem.key).where(
                    PeriodChecklistItem.period_fk == period.id,
                    PeriodChecklistItem.done.is_(True),
                )
            ).all()
        )

    # ------------------------------------------------------------- checklist

    def checklist(self, actor: AnyActor, period_id: uuid.UUID) -> Checklist:
        """The close as a list: each gate the system measures and each step a person
        confirms, with who confirmed it and when."""
        actor.require("account.view")
        period = self._require_period(actor, period_id)
        checks = [
            check
            for check in self._run_checks(actor, period)
            if check.name != "checklist_confirmed"
        ]
        rows = {
            row.key: row
            for row in self._db.scalars(
                select(PeriodChecklistItem).where(PeriodChecklistItem.period_fk == period.id)
            ).all()
        }
        steps = [
            ChecklistStep(
                key=key,
                label=label,
                done=bool(rows.get(key) and rows[key].done),
                done_by_id=rows[key].done_by_id if key in rows else None,
                done_at=rows[key].done_at if key in rows else None,
                note=rows[key].note if key in rows else None,
            )
            for key, label in CHECKLIST
        ]
        return Checklist(period=period, checks=checks, steps=steps)

    def confirm_step(
        self,
        actor: AnyActor,
        period_id: uuid.UUID,
        key: str,
        *,
        done: bool,
        note: str | None = None,
    ) -> Checklist:
        actor.require("period.close")
        period = self._require_period(actor, period_id)
        labels = dict(CHECKLIST)
        if key not in labels:
            raise ValidationError("That is not a step of the close checklist.", field="key")
        if period.state == PeriodState.CLOSED.value:
            raise ConflictError(f"{period.label} is closed; its checklist is kept as it was.")
        row = self._db.scalar(
            select(PeriodChecklistItem).where(
                PeriodChecklistItem.period_fk == period.id, PeriodChecklistItem.key == key
            )
        )
        if row is None:
            row = PeriodChecklistItem(business_id=period.business_id, period_fk=period.id, key=key)
            self._db.add(row)
        row.done = done
        row.done_by_id = actor.user_id if done else None
        row.done_at = self._clock.now() if done else None
        row.note = (note or "").strip() or None
        self._db.flush()
        self._audit.record(
            actor,
            "period_step_confirmed" if done else "period_step_withdrawn",
            entity_type="accounting_period",
            entity_id=period.id,
            entity_label=period.label,
            reason=row.note,
            after={"step": labels[key], "done": done},
        )
        return self.checklist(actor, period_id)

    # -------------------------------------------------------- reopen requests

    def request_reopen(
        self, actor: AnyActor, period_id: uuid.UUID, reason: str
    ) -> PeriodReopenRequest:
        """Ask for a closed period to be reopened. A second person decides."""
        actor.require_any("period.close", "period.reopen")
        cleaned = reason.strip()
        if len(cleaned) < 10:
            raise ValidationError(
                "Give a substantive reason for reopening a closed period. It is read "
                "by whoever reviews the ledger afterwards.",
                field="reason",
            )
        period = self._require_period(actor, period_id)
        if period.state != PeriodState.CLOSED.value:
            raise ConflictError(f"{period.label} is not closed.", period=period.label)
        pending = self._db.scalar(
            select(PeriodReopenRequest).where(
                PeriodReopenRequest.period_fk == period.id,
                PeriodReopenRequest.state == "pending",
            )
        )
        if pending is not None:
            raise ConflictError(f"A request to reopen {period.label} is already waiting.")
        request = PeriodReopenRequest(
            business_id=period.business_id,
            period_fk=period.id,
            reason=cleaned,
            state="pending",
            requested_by_id=actor.user_id,
            requested_at=self._clock.now(),
        )
        self._db.add(request)
        self._db.flush()
        self._audit.record(
            actor,
            "period_reopen_requested",
            entity_type="accounting_period",
            entity_id=period.id,
            entity_label=period.label,
            reason=cleaned,
        )
        return request

    def reopen_requests(
        self, actor: AnyActor, *, period_id: uuid.UUID | None = None
    ) -> list[PeriodReopenRequest]:
        actor.require("account.view")
        query = select(PeriodReopenRequest).where(
            PeriodReopenRequest.business_id == _business_of(actor)
        )
        if period_id is not None:
            query = query.where(PeriodReopenRequest.period_fk == period_id)
        return list(self._db.scalars(query.order_by(PeriodReopenRequest.requested_at.desc())).all())

    def decide_reopen(
        self, actor: AnyActor, request_id: uuid.UUID, *, approve: bool, note: str | None = None
    ) -> PeriodReopenRequest:
        """Approve or decline a request. The requester may not decide their own request,
        except the administrator, whose self-approval is permitted and recorded as such."""
        actor.require("period.reopen")
        request = self._db.get(PeriodReopenRequest, request_id)
        if request is None or request.business_id != _business_of(actor):
            raise NotFoundError("Reopening request not found.")
        if request.state != "pending":
            raise ConflictError("That request has already been decided.")
        own = request.requested_by_id is not None and request.requested_by_id == actor.user_id
        if own and not actor.is_administrator:
            raise ConflictError(
                "A request to reopen a period is decided by someone other than the person "
                "who made it."
            )
        period = self._require_period(actor, request.period_fk)
        now = self._clock.now()
        request.decided_by_id = actor.user_id
        request.decided_at = now
        request.decision_note = (note or "").strip() or None
        request.self_approved = own and approve
        if not approve:
            request.state = "declined"
            self._audit.record(
                actor,
                "period_reopen_declined",
                entity_type="accounting_period",
                entity_id=period.id,
                entity_label=period.label,
                reason=request.decision_note,
            )
            return request
        self._reopen(
            actor,
            period,
            request.reason,
            approved_by_id=actor.user_id,
            requested_by_id=request.requested_by_id,
            self_approved=own,
        )
        request.state = "approved"
        return request

    def _check_earlier_periods(self, period: AccountingPeriod) -> CloseCheck:
        """No earlier period may still be open.

        Closing out of order would leave a gap through which a late posting could land
        beneath a period already reported.
        """
        open_earlier = list(
            self._db.scalars(
                select(AccountingPeriod.label)
                .where(
                    AccountingPeriod.business_id == period.business_id,
                    AccountingPeriod.starts_on < period.starts_on,
                    AccountingPeriod.state != PeriodState.CLOSED.value,
                )
                .order_by(AccountingPeriod.starts_on)
            ).all()
        )
        passed = not open_earlier
        return CloseCheck(
            name="earlier_periods_closed",
            passed=passed,
            detail=(
                "Every earlier period is closed."
                if passed
                else f"Close {', '.join(open_earlier)} first; periods close in order."
            ),
            measurements={"open_earlier": open_earlier},
        )

    def _check_nothing_outstanding(self, period: AccountingPeriod) -> CloseCheck:
        """No draft and no pending entry may remain in the period."""
        counts = self._db.execute(
            select(JournalEntry.state, func.count())
            .where(
                JournalEntry.business_id == period.business_id,
                JournalEntry.posting_date >= period.starts_on,
                JournalEntry.posting_date <= period.ends_on,
                JournalEntry.state.in_(
                    (JournalState.DRAFT.value, JournalState.PENDING_APPROVAL.value)
                ),
            )
            .group_by(JournalEntry.state)
        ).all()
        found = {state: int(count) for state, count in counts}
        passed = not found
        parts = [f"{count} {state.replace('_', ' ')}" for state, count in sorted(found.items())]
        return CloseCheck(
            name="no_outstanding_entries",
            passed=passed,
            detail=(
                "No draft or pending entries remain in the period."
                if passed
                else f"{' and '.join(parts)} entries remain. Post, approve or decline them."
            ),
            measurements=found,
        )

    def _check_entries_balance(self, period: AccountingPeriod) -> CloseCheck:
        """Every posted entry in the period is internally balanced.

        The posting engine guarantees this, so a failure here means the ledger was
        written by something that went round it, which is worth knowing loudly.
        """
        unbalanced = list(
            self._db.execute(
                select(JournalEntry.number, JournalEntry.total_debit, JournalEntry.total_credit)
                .where(
                    JournalEntry.business_id == period.business_id,
                    JournalEntry.posting_date >= period.starts_on,
                    JournalEntry.posting_date <= period.ends_on,
                    JournalEntry.state.in_(
                        (JournalState.POSTED.value, JournalState.REVERSED.value)
                    ),
                    JournalEntry.total_debit != JournalEntry.total_credit,
                )
                .limit(20)
            ).all()
        )
        passed = not unbalanced
        return CloseCheck(
            name="entries_balanced",
            passed=passed,
            detail=(
                "Every posted entry in the period balances."
                if passed
                else f"{len(unbalanced)} posted entries do not balance: "
                + ", ".join(str(row[0]) for row in unbalanced)
            ),
            measurements={"unbalanced": [str(row[0]) for row in unbalanced]},
        )

    def _check_trial_balance(self, actor: AnyActor, period: AccountingPeriod) -> CloseCheck:
        """The trial balance at the period end sums to zero."""
        trial = self._reports.trial_balance(actor, as_at=period.ends_on)
        difference = trial.difference
        passed = difference == ZERO
        return CloseCheck(
            name="trial_balance_balanced",
            passed=passed,
            detail=(
                f"The trial balance at {period.ends_on:%d %B %Y} sums to zero."
                if passed
                else f"The trial balance is out by {abs(difference)}. "
                "Find the difference before closing."
            ),
            measurements={
                "debits": str(trial.total_debit),
                "credits": str(trial.total_credit),
                "difference": str(difference),
                "accounts": len(trial.rows),
            },
        )

    def _check_subledgers(self, actor: AnyActor, period: AccountingPeriod) -> CloseCheck:
        """Every control account agrees with the sum of its subledger.

        Read over the whole ledger rather than to the period end: the party balances
        are a running total with no history, so the only sound comparison is the
        present one. A close is refused while they disagree, whenever the disagreement
        arose, because a statement of account drawn from them would be wrong.
        """
        variances = self._reports.subledger_variances(actor)
        failing = [variance for variance in variances if not variance.agrees]
        passed = not failing
        return CloseCheck(
            name="subledgers_reconciled",
            passed=passed,
            detail=(
                f"All {len(variances)} control accounts agree with their subledgers."
                if passed
                else "These control accounts disagree with their party balances: "
                + "; ".join(
                    f"{variance.account_code} out by {abs(variance.difference)}"
                    for variance in failing
                )
            ),
            measurements={
                "checked": len(variances),
                "failing": [
                    {
                        "code": variance.account_code,
                        "control": str(variance.control_balance),
                        "subledger": str(variance.subledger_balance),
                        "difference": str(variance.difference),
                    }
                    for variance in failing
                ],
            },
        )

    # -------------------------------------------------------------- internals

    def checks_recorded(self, actor: AnyActor, period_id: uuid.UUID) -> list[PeriodCloseCheck]:
        """The gates as they stood when the period was last closed."""
        actor.require("account.view")
        self._require_period(actor, period_id)
        return list(
            self._db.scalars(
                select(PeriodCloseCheck)
                .where(PeriodCloseCheck.period_fk == period_id)
                .order_by(PeriodCloseCheck.checked_at.desc(), PeriodCloseCheck.name)
            ).all()
        )

    def _require_period(self, actor: AnyActor, period_id: uuid.UUID) -> AccountingPeriod:
        period = self._db.get(AccountingPeriod, period_id)
        if period is None or period.business_id != _business_of(actor):
            raise NotFoundError("Accounting period not found.")
        return period


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id


__all__ = [
    "CHECKLIST",
    "CURRENT_YEAR_RESULT",
    "Checklist",
    "ChecklistStep",
    "CloseCheck",
    "CloseReadiness",
    "PeriodService",
    "YearEndOutcome",
]
