"""The posting engine: the only route by which a figure enters the ledger.

Every financial consequence in the system — a sale, a receipt, a stock adjustment, a
tax charge — arrives here as a balanced journal entry, per PRD §29.2. Nothing else
writes to an account.

Four rules are enforced without exception:

* an entry posts only when debits equal credits;
* a posted entry is never amended, only reversed;
* a closed period refuses a posting;
* a repeated idempotency key returns the original entry and creates nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.accounts import AccountService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.ledger import (
    AccountKind,
    EntryLine,
    JournalSource,
    JournalState,
    PeriodState,
    PeriodWindow,
    Side,
    SubledgerKind,
    assert_balanced,
    assert_posting_permitted,
)
from nickland.domain.money import ZERO, money
from nickland.persistence.ledger_models import (
    Account,
    AccountingPeriod,
    ApprovalThreshold,
    JournalApproval,
    JournalEntry,
    JournalLine,
    SubledgerBalance,
)
from nickland.persistence.models import Business
from nickland.platform.clock import Clock, SystemClock

MANUAL_SOURCES = frozenset({JournalSource.MANUAL.value})
"""Sources a person writes by hand, and which a control account therefore refuses."""


@dataclass(slots=True)
class JournalDraft:
    """An entry awaiting validation.

    The lines carry account codes rather than identifiers, because every caller that
    raises a journal — a sale, a payment, a stock movement — knows the account it
    means by its code and should not have to resolve it first.
    """

    posting_date: date
    narration: str
    lines: list[EntryLine]
    source: JournalSource | str = JournalSource.MANUAL
    source_reference: str | None = None
    source_id: uuid.UUID | None = None
    idempotency_key: str | None = None
    currency_code: str | None = None
    attachment_ids: list[str] = field(default_factory=list)
    branch_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class PostingOutcome:
    """What became of a posting request."""

    entry: JournalEntry
    created: bool
    """``False`` where an idempotency key matched an entry that already existed."""
    awaiting_approval: bool
    """``True`` where the amount met a threshold and a checker must now agree."""


@dataclass(frozen=True, slots=True)
class EntryPage:
    """A page of the journal register."""

    entries: list[JournalEntry]
    total: int
    offset: int
    limit: int


class JournalService:
    """Posts, approves, rejects and reverses journal entries."""

    __slots__ = ("_accounts", "_audit", "_clock", "_db")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._accounts = AccountService(session, clock=self._clock)

    # ---------------------------------------------------------------- posting

    def post(self, actor: AnyActor, draft: JournalDraft) -> PostingOutcome:
        """Validate and post an entry.

        Where the amount meets the approval threshold the entry is recorded as
        pending instead, and affects no balance until a checker approves it.
        """
        actor.require("journal.post")
        business_id = _business_of(actor)

        existing = self._by_idempotency_key(business_id, draft.idempotency_key)
        if existing is not None:
            return PostingOutcome(
                entry=existing,
                created=False,
                awaiting_approval=existing.state == JournalState.PENDING_APPROVAL.value,
            )

        balance = assert_balanced(draft.lines)
        source = JournalSource(str(draft.source))
        accounts = self._resolve_accounts(business_id, draft.lines, source)
        period = self._permitted_period(business_id, draft.posting_date)
        currency = draft.currency_code or self._base_currency(business_id)

        threshold = self._threshold(business_id, source)
        requires_approval = threshold is not None and balance.debits >= threshold.amount
        state = (
            JournalState.PENDING_APPROVAL.value if requires_approval else JournalState.POSTED.value
        )
        now = self._clock.now()

        entry = JournalEntry(
            business_id=business_id,
            branch_id=draft.branch_id or actor.branch_id,
            posting_date=draft.posting_date,
            period_fk=period.id,
            state=state,
            source=source.value,
            source_reference=draft.source_reference,
            source_id=draft.source_id,
            idempotency_key=draft.idempotency_key,
            narration=draft.narration.strip(),
            currency_code=currency,
            total_debit=balance.debits,
            total_credit=balance.credits,
            created_at=now,
            created_by_id=actor.user_id,
            attachment_ids=list(draft.attachment_ids) or None,
        )
        if state == JournalState.POSTED.value:
            entry.posted_at = now
            entry.posted_by_id = actor.user_id
            entry.number = self._next_number(business_id, period)

        for order, spec in enumerate(draft.lines):
            account = accounts[spec.account_code]
            entry.lines.append(
                JournalLine(
                    business_id=business_id,
                    branch_id=_as_uuid(spec.branch_id) or draft.branch_id or actor.branch_id,
                    account_fk=account.id,
                    display_order=order,
                    debit=spec.debit,
                    credit=spec.credit,
                    currency_code=currency,
                    exchange_rate=Decimal("1"),
                    base_debit=spec.debit,
                    base_credit=spec.credit,
                    narration=spec.narration,
                    party_fk=_as_uuid(spec.party_id),
                    subledger=_subledger_of(spec, account),
                    posting_date=draft.posting_date,
                    state=state,
                )
            )

        self._db.add(entry)
        self._db.flush()

        if state == JournalState.POSTED.value:
            self._apply_subledger(entry, direction=1)

        self._audit.record(
            actor,
            "journal_pending" if requires_approval else "journal_posted",
            entity_type="journal_entry",
            entity_id=entry.id,
            entity_label=entry.number or entry.narration[:80],
            after={
                "posting_date": draft.posting_date.isoformat(),
                "total": str(balance.debits),
                "source": source.value,
                "state": state,
                "lines": len(draft.lines),
            },
            context=(
                {"threshold": str(threshold.amount)} if requires_approval and threshold else None
            ),
        )
        return PostingOutcome(entry=entry, created=True, awaiting_approval=requires_approval)

    # --------------------------------------------------------------- approval

    def approve(
        self, actor: AnyActor, entry_id: uuid.UUID, *, note: str | None = None
    ) -> JournalEntry:
        """Agree a pending entry as checker, which posts it."""
        actor.require("journal.approve")
        entry = self.require(actor, entry_id)
        self._assert_pending(entry)
        self._assert_distinct_checker(actor, entry)

        period = self._permitted_period(entry.business_id, entry.posting_date)
        now = self._clock.now()

        entry.period_fk = period.id
        entry.state = JournalState.POSTED.value
        entry.approved_at = now
        entry.approved_by_id = actor.user_id
        entry.posted_at = now
        entry.posted_by_id = actor.user_id
        entry.number = entry.number or self._next_number(entry.business_id, period)
        for line in entry.lines:
            line.state = JournalState.POSTED.value

        self._db.add(
            JournalApproval(
                business_id=entry.business_id,
                entry_fk=entry.id,
                decided_at=now,
                decided_by_id=_require_user(actor),
                decision="approved",
                reason=note,
                threshold_amount=entry.total_debit,
            )
        )
        self._db.flush()
        self._apply_subledger(entry, direction=1)

        self._audit.record(
            actor,
            "journal_approved",
            entity_type="journal_entry",
            entity_id=entry.id,
            entity_label=entry.number,
            reason=note,
            after={"state": entry.state, "total": str(entry.total_debit)},
        )
        return entry

    def reject(self, actor: AnyActor, entry_id: uuid.UUID, reason: str) -> JournalEntry:
        """Decline a pending entry. It affects no balance and remains on the record."""
        actor.require("journal.approve")
        if not reason.strip():
            raise ValidationError("Give a reason for declining the entry.", field="reason")
        entry = self.require(actor, entry_id)
        self._assert_pending(entry)

        now = self._clock.now()
        entry.state = JournalState.REJECTED.value
        entry.rejection_reason = reason.strip()
        for line in entry.lines:
            line.state = JournalState.REJECTED.value

        self._db.add(
            JournalApproval(
                business_id=entry.business_id,
                entry_fk=entry.id,
                decided_at=now,
                decided_by_id=_require_user(actor),
                decision="rejected",
                reason=reason.strip(),
                threshold_amount=entry.total_debit,
            )
        )
        self._audit.record(
            actor,
            "journal_rejected",
            entity_type="journal_entry",
            entity_id=entry.id,
            entity_label=entry.number or entry.narration[:80],
            reason=reason.strip(),
            after={"state": entry.state},
        )
        return entry

    # -------------------------------------------------------------- reversal

    def reverse(
        self,
        actor: AnyActor,
        entry_id: uuid.UUID,
        reason: str,
        *,
        on: date | None = None,
    ) -> JournalEntry:
        """Undo a posted entry with a mirrored entry.

        The original is untouched but for a note of what reversed it. Both appear on
        every report, which is what makes a correction auditable rather than invisible.
        """
        actor.require("journal.reverse")
        if not reason.strip():
            raise ValidationError("Give a reason for the reversal.", field="reason")

        original = self.require(actor, entry_id)
        if original.state == JournalState.REVERSED.value:
            raise ConflictError(
                f"Entry {original.number} was already reversed. Reverse the reversing "
                "entry if the correction itself was wrong.",
                entry_id=str(original.id),
            )
        if original.state != JournalState.POSTED.value:
            raise ConflictError(
                f"Only a posted entry can be reversed; this one is {original.state}.",
                entry_id=str(original.id),
                state=original.state,
            )

        posting_date = on or original.posting_date
        period = self._permitted_period(original.business_id, posting_date)
        now = self._clock.now()

        reversal = JournalEntry(
            business_id=original.business_id,
            branch_id=original.branch_id,
            posting_date=posting_date,
            period_fk=period.id,
            state=JournalState.POSTED.value,
            source=JournalSource.REVERSAL.value,
            source_reference=original.number,
            source_id=original.id,
            narration=f"Reversal of {original.number}: {reason.strip()}",
            currency_code=original.currency_code,
            total_debit=original.total_credit,
            total_credit=original.total_debit,
            created_at=now,
            created_by_id=actor.user_id,
            posted_at=now,
            posted_by_id=actor.user_id,
            reverses_fk=original.id,
            reversal_reason=reason.strip(),
        )
        reversal.number = self._next_number(original.business_id, period)

        for order, line in enumerate(original.lines):
            reversal.lines.append(
                JournalLine(
                    business_id=line.business_id,
                    branch_id=line.branch_id,
                    account_fk=line.account_fk,
                    display_order=order,
                    debit=line.credit,
                    credit=line.debit,
                    currency_code=line.currency_code,
                    exchange_rate=line.exchange_rate,
                    base_debit=line.base_credit,
                    base_credit=line.base_debit,
                    narration=line.narration,
                    party_fk=line.party_fk,
                    subledger=line.subledger,
                    posting_date=posting_date,
                    state=JournalState.POSTED.value,
                )
            )

        self._db.add(reversal)
        self._db.flush()

        original.state = JournalState.REVERSED.value
        original.reversed_by_fk = reversal.id
        original.reversal_reason = reason.strip()
        for line in original.lines:
            line.state = JournalState.REVERSED.value

        self._apply_subledger(reversal, direction=1)

        self._audit.record(
            actor,
            "journal_reversed",
            entity_type="journal_entry",
            entity_id=original.id,
            entity_label=original.number,
            reason=reason.strip(),
            before={"state": JournalState.POSTED.value},
            after={"state": original.state, "reversed_by": reversal.number},
        )
        return reversal

    # ---------------------------------------------------------------- reading

    def require(self, actor: AnyActor, entry_id: uuid.UUID) -> JournalEntry:
        actor.require("journal.view")
        entry = self._db.get(JournalEntry, entry_id)
        if entry is None or entry.business_id != _business_of(actor):
            raise NotFoundError("Journal entry not found.")
        return entry

    def entries(
        self,
        actor: AnyActor,
        *,
        term: str | None = None,
        state: str | None = None,
        source: str | None = None,
        account_id: uuid.UUID | None = None,
        party_id: uuid.UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> EntryPage:
        """The journal register, newest first."""
        actor.require("journal.view")
        statement: Select[Any] = select(JournalEntry).where(
            JournalEntry.business_id == _business_of(actor)
        )
        if state:
            statement = statement.where(JournalEntry.state == state)
        if source:
            statement = statement.where(JournalEntry.source == source)
        if start:
            statement = statement.where(JournalEntry.posting_date >= start)
        if end:
            statement = statement.where(JournalEntry.posting_date <= end)
        if term:
            pattern = f"%{term.strip().lower()}%"
            statement = statement.where(
                or_(
                    func.lower(JournalEntry.narration).like(pattern),
                    func.lower(func.coalesce(JournalEntry.number, "")).like(pattern),
                    func.lower(func.coalesce(JournalEntry.source_reference, "")).like(pattern),
                )
            )
        if account_id or party_id:
            lines = select(JournalLine.entry_fk)
            if account_id:
                lines = lines.where(JournalLine.account_fk == account_id)
            if party_id:
                lines = lines.where(JournalLine.party_fk == party_id)
            statement = statement.where(JournalEntry.id.in_(lines))

        total = self._db.scalar(select(func.count()).select_from(statement.subquery()))
        page = (
            self._db.scalars(
                statement.order_by(JournalEntry.posting_date.desc(), JournalEntry.created_at.desc())
                .offset(max(offset, 0))
                .limit(max(min(limit, 200), 1))
            )
            .unique()
            .all()
        )
        return EntryPage(entries=list(page), total=int(total or 0), offset=offset, limit=limit)

    def pending(self, actor: AnyActor) -> list[JournalEntry]:
        """Entries awaiting a checker, oldest first — a work queue, not a report."""
        actor.require("journal.view")
        return list(
            self._db.scalars(
                select(JournalEntry)
                .where(
                    JournalEntry.business_id == _business_of(actor),
                    JournalEntry.state == JournalState.PENDING_APPROVAL.value,
                )
                .order_by(JournalEntry.created_at)
            )
            .unique()
            .all()
        )

    def approvals(self, actor: AnyActor, entry_id: uuid.UUID) -> list[JournalApproval]:
        actor.require("journal.view")
        self.require(actor, entry_id)
        return list(
            self._db.scalars(
                select(JournalApproval)
                .where(JournalApproval.entry_fk == entry_id)
                .order_by(JournalApproval.decided_at)
            ).all()
        )

    # -------------------------------------------------------------- threshold

    def threshold(self, actor: AnyActor) -> ApprovalThreshold | None:
        actor.require("journal.view")
        return self._threshold(_business_of(actor), JournalSource.MANUAL)

    def set_threshold(
        self,
        actor: AnyActor,
        amount: Decimal | None,
        *,
        requires_distinct_checker: bool = True,
        scope: str = "manual_journal",
    ) -> ApprovalThreshold | None:
        """Set, raise, lower or withdraw the amount above which a checker is required."""
        actor.require("journal.approve")
        business_id = _business_of(actor)
        existing = self._db.scalar(
            select(ApprovalThreshold).where(
                ApprovalThreshold.business_id == business_id,
                ApprovalThreshold.scope == scope,
            )
        )
        if amount is None:
            if existing is not None:
                existing.is_active = False
                self._audit.record(
                    actor,
                    "approval_threshold_withdrawn",
                    entity_type="approval_threshold",
                    entity_id=existing.id,
                    entity_label=scope,
                )
            return None
        if amount < ZERO:
            raise ValidationError("A threshold cannot be negative.", field="amount")

        value = money(amount)
        if existing is None:
            existing = ApprovalThreshold(
                business_id=business_id,
                scope=scope,
                amount=value,
                requires_distinct_checker=requires_distinct_checker,
                is_active=True,
            )
            self._db.add(existing)
        else:
            existing.amount = value
            existing.requires_distinct_checker = requires_distinct_checker
            existing.is_active = True
        self._db.flush()
        self._audit.record(
            actor,
            "approval_threshold_set",
            entity_type="approval_threshold",
            entity_id=existing.id,
            entity_label=scope,
            after={"amount": str(value), "distinct_checker": requires_distinct_checker},
        )
        return existing

    # --------------------------------------------------------------- internals

    def _by_idempotency_key(self, business_id: uuid.UUID, key: str | None) -> JournalEntry | None:
        """The entry a key already produced, where there is one.

        This is what makes a retried request safe. A caller that loses the response to
        a posting and sends it again must not post twice, per PRD §29.9.
        """
        if not key:
            return None
        return self._db.scalar(
            select(JournalEntry).where(
                JournalEntry.business_id == business_id,
                JournalEntry.idempotency_key == key,
            )
        )

    def _resolve_accounts(
        self, business_id: uuid.UUID, lines: list[EntryLine], source: JournalSource
    ) -> dict[str, Account]:
        """Fetch every account a draft names, refusing any that cannot take the posting."""
        codes = sorted({line.account_code for line in lines})
        found = {
            account.code: account
            for account in self._db.scalars(
                select(Account).where(Account.business_id == business_id, Account.code.in_(codes))
            ).unique()
        }
        missing = [code for code in codes if code not in found]
        if missing:
            raise ValidationError(
                f"These accounts are not in the chart: {', '.join(missing)}.",
                field="lines",
                codes=missing,
            )

        for code, account in sorted(found.items()):
            if not account.is_active:
                raise ConflictError(
                    f"{code} — {account.name} has been withdrawn, so nothing may be "
                    "posted to it. Choose an account in use.",
                    code=code,
                )
            if source.value in MANUAL_SOURCES and not account.permits_manual_posting:
                raise ConflictError(
                    f"{code} — {account.name} is a control account. Its balance is "
                    "written by the subledger that owns it, so it accepts no manual "
                    "journal. Record the underlying transaction instead.",
                    code=code,
                    subledger=account.control_subledger,
                )
        return found

    def _permitted_period(self, business_id: uuid.UUID, on: date) -> AccountingPeriod:
        """The period a date falls in, having confirmed it will accept a posting."""
        period = self._accounts.require_period_for(business_id, on)
        assert_posting_permitted(
            PeriodState(period.state),
            PeriodWindow(starts_on=period.starts_on, ends_on=period.ends_on),
            on,
        )
        return period

    def _threshold(self, business_id: uuid.UUID, source: JournalSource) -> ApprovalThreshold | None:
        """The approval threshold for a source, where one applies.

        Only an entry a person writes is held for a checker. An entry raised by a sale
        or a receipt was already authorised by the transaction that caused it.
        """
        if source.value not in MANUAL_SOURCES:
            return None
        return self._db.scalar(
            select(ApprovalThreshold).where(
                ApprovalThreshold.business_id == business_id,
                ApprovalThreshold.scope == "manual_journal",
                ApprovalThreshold.is_active.is_(True),
            )
        )

    def _assert_pending(self, entry: JournalEntry) -> None:
        if entry.state != JournalState.PENDING_APPROVAL.value:
            raise ConflictError(
                f"Entry {entry.number or entry.id} is {entry.state}, so there is "
                "nothing to decide.",
                entry_id=str(entry.id),
                state=entry.state,
            )

    def _assert_distinct_checker(self, actor: AnyActor, entry: JournalEntry) -> None:
        """Refuse an approval by the person who raised the entry.

        Maker-checker separation is the whole point of the threshold; a self-approval
        would satisfy the workflow while defeating the control, per PRD §29.9.
        """
        threshold = self._threshold(entry.business_id, JournalSource(entry.source))
        if threshold is None or not threshold.requires_distinct_checker:
            return
        if entry.created_by_id is not None and entry.created_by_id == actor.user_id:
            raise ConflictError(
                "An entry cannot be approved by the person who raised it. Ask a "
                "second authorised user to review it.",
                entry_id=str(entry.id),
            )

    def _next_number(self, business_id: uuid.UUID, period: AccountingPeriod) -> str:
        """The next journal number within the financial year.

        Numbering restarts each financial year, so a number reads as a position in a
        year rather than an ever-growing count. The uniqueness constraint on
        ``(business_id, number)`` is the guarantee; this query only proposes.
        """
        prefix = f"JV-{period.financial_year}-"
        highest = self._db.scalar(
            select(func.max(JournalEntry.number)).where(
                JournalEntry.business_id == business_id,
                JournalEntry.number.like(f"{prefix}%"),
            )
        )
        sequence = 1
        if highest:
            tail = str(highest).rsplit("-", 1)[-1]
            if tail.isdigit():
                sequence = int(tail) + 1
        return f"{prefix}{sequence:05d}"

    def _apply_subledger(self, entry: JournalEntry, *, direction: int) -> None:
        """Carry a posted entry's party lines into the subledger balances.

        The control account and its subledger are written in the same transaction, so
        the two cannot drift; the period close reconciles them as a further guard, per
        PRD §29.2.
        """
        now = self._clock.now()
        for line in entry.lines:
            if line.party_fk is None or line.subledger is None:
                continue
            row = self._db.scalar(
                select(SubledgerBalance).where(
                    SubledgerBalance.business_id == entry.business_id,
                    SubledgerBalance.subledger == line.subledger,
                    SubledgerBalance.party_fk == line.party_fk,
                    SubledgerBalance.account_fk == line.account_fk,
                )
            )
            if row is None:
                row = SubledgerBalance(
                    business_id=entry.business_id,
                    subledger=line.subledger,
                    party_fk=line.party_fk,
                    account_fk=line.account_fk,
                    debit=ZERO,
                    credit=ZERO,
                    updated_at=now,
                )
                self._db.add(row)
            row.debit = money(row.debit + line.base_debit * direction)
            row.credit = money(row.credit + line.base_credit * direction)
            row.updated_at = now

    def _base_currency(self, business_id: uuid.UUID) -> str:
        business = self._db.get(Business, business_id)
        return business.base_currency if business else "GHS"


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id


def _require_user(actor: AnyActor) -> uuid.UUID:
    if actor.user_id is None:
        raise ConflictError("An approval must be attributed to a user.")
    return actor.user_id


def _as_uuid(value: object | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _subledger_of(spec: EntryLine, account: Account) -> str | None:
    """The subledger a line belongs to.

    Only a control account has one. A party may be named on any line — an expense
    attributable to a supplier, say — but the subledger designation is what causes a
    materialised balance to be kept, and a balance against an account that no
    reconciliation examines is a figure nothing would ever correct. So the account
    decides, and a line that names a different subledger from the account it posts to
    is refused rather than quietly filed under the account's own.
    """
    declared = account.control_subledger
    if declared is None:
        return None
    if spec.subledger is not None:
        asked = SubledgerKind(str(spec.subledger)).value
        if asked != declared:
            raise ValidationError(
                f"{account.code} — {account.name} is the control account for the "
                f"{declared} subledger, so a line on it cannot be filed under "
                f"{asked}.",
                field="lines",
                code=account.code,
            )
    if spec.party_id is None:
        raise ValidationError(
            f"{account.code} — {account.name} is a control account, so every line "
            "on it must name the party whose balance it moves.",
            field="lines",
            code=account.code,
        )
    return declared


def balance_direction(account: Account) -> Side:
    """The side on which an account normally stands."""
    return AccountKind(account.kind).normal_balance
