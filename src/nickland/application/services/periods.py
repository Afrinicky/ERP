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
from dataclasses import dataclass
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
from nickland.persistence.ledger_models import (
    AccountingPeriod,
    JournalEntry,
    PeriodCloseCheck,
)
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
        """Reopen a closed period under authority, recording why."""
        actor.require("period.reopen")
        if len(reason.strip()) < 10:
            raise ValidationError(
                "Give a substantive reason for reopening a closed period. It is read "
                "by whoever reviews the ledger afterwards.",
                field="reason",
            )
        period = self._require_period(actor, period_id)
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
        period.reopened_by_id = actor.user_id
        period.reopen_reason = reason.strip()
        period.reopen_approved_by_id = approved_by_id

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
                "approved_by": str(approved_by_id) if approved_by_id else None,
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
        ]

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
    "CURRENT_YEAR_RESULT",
    "CloseCheck",
    "CloseReadiness",
    "PeriodService",
    "YearEndOutcome",
]
