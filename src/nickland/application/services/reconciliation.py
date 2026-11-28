"""Reconciliation: statements matched to the ledger, and every source of money checked
against its postings. PRD §20.4, AC-20.

A statement line is the bank's record of money moving; a ledger line on the account is
ours. They are the same money when they match, and an account is reconciled when every
line of a statement is matched and the statement's closing balance is the ledger's
balance on the same day. What is left over is the unmatched queue: a charge or interest
the business has not yet recorded is posted from it, never typed in a second time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.treasury import MovementDraft, TreasuryService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.finance import FinancialAccountKind, TreasuryMovementKind, parse_statement
from nickland.domain.money import ZERO, money
from nickland.persistence.finance_models import (
    BankStatement,
    CustomerReceipt,
    FinancialAccount,
    ReconciliationMatch,
    StatementLine,
)
from nickland.persistence.ledger_models import Account, JournalEntry, JournalLine
from nickland.persistence.models import Business
from nickland.persistence.order_models import SupplierPayment
from nickland.persistence.pos_models import PosShift
from nickland.persistence.tax_models import TaxPayment
from nickland.platform.clock import Clock, SystemClock, business_date

WINDOW = timedelta(days=3)
"""How far apart a statement line and a posting may be dated and still be suggested."""


@dataclass(slots=True)
class LedgerItem:
    line: JournalLine
    entry: JournalEntry
    amount: Decimal
    """Money in positive, money out negative, as on the statement."""


@dataclass(slots=True)
class Suggestion:
    item: LedgerItem
    score: int
    """Higher is closer: the same reference, then the same day."""


@dataclass(slots=True)
class StatementPosition:
    statement: BankStatement
    ledger_balance: Decimal
    """The ledger's balance on the statement's last day."""
    difference: Decimal
    unmatched: int
    reconciled: bool


@dataclass(slots=True)
class SourceRow:
    source: str
    label: str
    records: int
    recorded: Decimal
    posted: Decimal
    difference: Decimal
    unposted: int
    agrees: bool
    notes: list[str] = field(default_factory=list)


class ReconciliationService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._treasury = TreasuryService(session, clock=self._clock)

    # -- statements -----------------------------------------------------------------------

    def import_statement(
        self,
        actor: AnyActor,
        financial_account_id: uuid.UUID,
        *,
        content: str,
        closing_balance: Decimal,
        reference: str,
    ) -> BankStatement:
        """Import a CSV statement. A line already imported for this account is marked as a
        duplicate and never matched; the rest are suggested against the ledger."""
        actor.require("reconciliation.manage")
        account = self._treasury.require(actor, financial_account_id)
        parsed = parse_statement(content)
        name = reference.strip()
        if not name:
            raise ValidationError("Name the statement, as the bank numbers it.", field="reference")
        statement = BankStatement(
            business_id=account.business_id,
            financial_account_fk=account.id,
            reference=name,
            starts_on=min(item.line_date for item in parsed),
            ends_on=max(item.line_date for item in parsed),
            closing_balance=money(closing_balance),
            imported_at=self._clock.now(),
            imported_by_id=actor.user_id,
        )
        seen: dict[str, uuid.UUID] = dict(
            self._db.execute(
                select(StatementLine.fingerprint, StatementLine.id).where(
                    StatementLine.financial_account_fk == account.id,
                    StatementLine.state != "duplicate",
                )
            )
            .tuples()
            .all()
        )
        self._db.add(statement)
        self._db.flush()
        for position, item in enumerate(parsed, start=1):
            prior = seen.get(item.fingerprint)
            line = StatementLine(
                business_id=account.business_id,
                financial_account_fk=account.id,
                position=position,
                line_date=item.line_date,
                description=item.description,
                reference=item.reference,
                amount=item.amount,
                fingerprint=item.fingerprint,
                state="duplicate" if prior else "unmatched",
                duplicate_of_fk=prior,
            )
            statement.lines.append(line)
            self._db.flush()
            if prior is None:
                seen[item.fingerprint] = line.id
        self._audit.record(
            actor,
            "statement_imported",
            entity_type="bank_statement",
            entity_id=statement.id,
            entity_label=name,
            after={
                "account": account.name,
                "lines": len(parsed),
                "duplicates": sum(1 for line in statement.lines if line.state == "duplicate"),
            },
        )
        return statement

    def statements(
        self, actor: AnyActor, *, financial_account_id: uuid.UUID | None = None
    ) -> list[BankStatement]:
        actor.require_any("reconciliation.manage", "journal.view")
        query = select(BankStatement).where(BankStatement.business_id == actor.business_id)
        if financial_account_id:
            query = query.where(BankStatement.financial_account_fk == financial_account_id)
        return list(self._db.scalars(query.order_by(BankStatement.ends_on.desc())).all())

    def require(self, actor: AnyActor, statement_id: uuid.UUID) -> BankStatement:
        actor.require_any("reconciliation.manage", "journal.view")
        statement = self._db.get(BankStatement, statement_id)
        if statement is None or statement.business_id != actor.business_id:
            raise NotFoundError("Statement not found.")
        return statement

    def position(self, actor: AnyActor, statement_id: uuid.UUID) -> StatementPosition:
        statement = self.require(actor, statement_id)
        account = self._db.get(FinancialAccount, statement.financial_account_fk)
        assert account is not None
        ledger = LedgerReportService(self._db, clock=self._clock).account_balance(
            statement.business_id, account.account_fk, as_at=statement.ends_on
        )
        unmatched = sum(1 for line in statement.lines if line.state == "unmatched")
        difference = money(statement.closing_balance - ledger)
        return StatementPosition(
            statement=statement,
            ledger_balance=ledger,
            difference=difference,
            unmatched=unmatched,
            reconciled=unmatched == 0 and difference == ZERO,
        )

    # -- the ledger side ------------------------------------------------------------------

    def unreconciled(self, actor: AnyActor, financial_account_id: uuid.UUID) -> list[LedgerItem]:
        """Postings on the account no statement line has been matched to."""
        actor.require_any("reconciliation.manage", "journal.view")
        account = self._treasury.require(actor, financial_account_id)
        matched = select(ReconciliationMatch.journal_line_fk)
        rows = self._db.execute(
            select(JournalLine, JournalEntry)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_fk)
            .where(
                JournalLine.account_fk == account.account_fk,
                JournalLine.state == "posted",
                JournalLine.id.not_in(matched),
            )
            .order_by(JournalLine.posting_date, JournalEntry.number)
        ).all()
        return [
            LedgerItem(line=line, entry=entry, amount=money(line.base_debit - line.base_credit))
            for line, entry in rows
        ]

    def suggestions(self, actor: AnyActor, line_id: uuid.UUID) -> list[Suggestion]:
        """Postings of the same amount within three days, the same reference first."""
        line = self._line(actor, line_id)
        found = []
        for item in self.unreconciled(actor, line.financial_account_fk):
            if item.amount != line.amount:
                continue
            gap = abs((item.line.posting_date - line.line_date).days)
            if gap > WINDOW.days:
                continue
            score = 10 - gap
            reference = (line.reference or "").strip().lower()
            if (
                reference
                and reference
                in " ".join(
                    (
                        item.entry.source_reference or "",
                        item.entry.narration,
                        item.line.narration or "",
                    )
                ).lower()
            ):
                score += 100
            found.append(Suggestion(item=item, score=score))
        return sorted(found, key=lambda suggestion: -suggestion.score)

    # -- matching -------------------------------------------------------------------------

    def match(
        self,
        actor: AnyActor,
        line_id: uuid.UUID,
        journal_line_ids: list[uuid.UUID],
        *,
        method: str = "manual",
    ) -> StatementLine:
        """The statement line is the same money as these postings, which together equal it."""
        actor.require("reconciliation.manage")
        line = self._line(actor, line_id)
        if line.state != "unmatched":
            raise ConflictError(f"This line is {line.state}.")
        if not journal_line_ids:
            raise ValidationError("Choose the postings it matches.", field="journal_line_ids")
        account = self._db.get(FinancialAccount, line.financial_account_fk)
        assert account is not None
        total = ZERO
        chosen = []
        for journal_line_id in journal_line_ids:
            posting = self._db.get(JournalLine, journal_line_id)
            if posting is None or posting.account_fk != account.account_fk:
                raise ConflictError("Each posting matched is on this account.")
            if posting.state != "posted":
                raise ConflictError("Only a posted line is matched.")
            if self._db.scalar(
                select(ReconciliationMatch.id).where(
                    ReconciliationMatch.journal_line_fk == posting.id
                )
            ):
                raise ConflictError("A posting is matched to one statement line only.")
            signed = money(posting.base_debit - posting.base_credit)
            total = money(total + signed)
            chosen.append((posting, signed))
        if total != line.amount:
            raise ConflictError(f"The postings total {total}; the statement line is {line.amount}.")
        now = self._clock.now()
        for posting, signed in chosen:
            self._db.add(
                ReconciliationMatch(
                    business_id=line.business_id,
                    statement_line_fk=line.id,
                    journal_line_fk=posting.id,
                    amount=signed,
                    method=method,
                    matched_at=now,
                    matched_by_id=actor.user_id,
                )
            )
        line.state = "matched"
        self._db.flush()
        return line

    def auto_match(self, actor: AnyActor, statement_id: uuid.UUID) -> int:
        """Match every line with exactly one suggestion of the same reference, or exactly
        one of the same amount within the window. Returns how many were matched."""
        actor.require("reconciliation.manage")
        statement = self.require(actor, statement_id)
        matched = 0
        for line in statement.lines:
            if line.state != "unmatched":
                continue
            found = self.suggestions(actor, line.id)
            best = [item for item in found if item.score >= 100] or found
            if len(best) == 1:
                self.match(actor, line.id, [best[0].item.line.id], method="suggested")
                matched += 1
        return matched

    def unmatch(self, actor: AnyActor, line_id: uuid.UUID) -> StatementLine:
        actor.require("reconciliation.manage")
        line = self._line(actor, line_id)
        statement = self._db.get(BankStatement, line.statement_fk)
        if statement is not None and statement.state == "reconciled":
            raise ConflictError("A reconciled statement is not amended.")
        for item in self._db.scalars(
            select(ReconciliationMatch).where(ReconciliationMatch.statement_line_fk == line.id)
        ).all():
            self._db.delete(item)
        if line.state in ("matched", "excluded"):
            line.state = "unmatched"
        self._db.flush()
        return line

    def exclude(self, actor: AnyActor, line_id: uuid.UUID, *, reason: str) -> StatementLine:
        """Set a line aside — a bank's own reversal pair, say — with the reason."""
        actor.require("reconciliation.manage")
        line = self._line(actor, line_id)
        if line.state != "unmatched":
            raise ConflictError(f"This line is {line.state}.")
        if not reason.strip():
            raise ValidationError("An excluded line records why.", field="reason")
        line.state = "excluded"
        line.note = reason.strip()
        self._db.flush()
        return line

    def post_from_line(
        self, actor: AnyActor, line_id: uuid.UUID, *, kind: TreasuryMovementKind | str, reason: str
    ) -> StatementLine:
        """Record what the bank did that the business had not — a charge, interest — from
        the statement line itself, and match it."""
        actor.require("reconciliation.manage")
        line = self._line(actor, line_id)
        if line.state != "unmatched":
            raise ConflictError(f"This line is {line.state}.")
        chosen = TreasuryMovementKind(kind)
        outward = line.amount < ZERO
        if chosen is TreasuryMovementKind.TRANSFER:
            raise ValidationError("A transfer is recorded between accounts, then matched.")
        if outward != (
            chosen in (TreasuryMovementKind.BANK_CHARGE, TreasuryMovementKind.ADJUSTMENT_OUT)
        ):
            raise ValidationError(
                "Money out is a charge or an adjustment out; money in is interest or an "
                "adjustment in.",
                field="kind",
            )
        movement = self._treasury.record(
            actor,
            MovementDraft(
                kind=chosen,
                amount=abs(line.amount),
                reason=(reason or "").strip() or line.description,
                from_account_id=line.financial_account_fk if outward else None,
                to_account_id=None if outward else line.financial_account_fk,
                occurred_on=line.line_date,
                reference=line.reference,
            ),
        )
        account = self._db.get(FinancialAccount, line.financial_account_fk)
        assert account is not None
        posting = self._db.scalar(
            select(JournalLine).where(
                JournalLine.entry_fk == movement.journal_entry_fk,
                JournalLine.account_fk == account.account_fk,
            )
        )
        assert posting is not None
        return self.match(actor, line.id, [posting.id], method="posted")

    def complete(self, actor: AnyActor, statement_id: uuid.UUID) -> StatementPosition:
        """Close a statement: every line matched, set aside or a duplicate, and the closing
        balance equal to the ledger's."""
        actor.require("reconciliation.manage")
        position = self.position(actor, statement_id)
        if position.unmatched:
            raise ConflictError(f"{position.unmatched} line(s) are still unmatched.")
        if position.difference != ZERO:
            raise ConflictError(
                f"The statement closes at {position.statement.closing_balance}; the ledger "
                f"holds {position.ledger_balance} on {position.statement.ends_on}. "
                f"{position.difference} is unexplained."
            )
        position.statement.state = "reconciled"
        position.statement.reconciled_at = self._clock.now()
        position.statement.reconciled_by_id = actor.user_id
        position.reconciled = True
        self._db.flush()
        self._audit.record(
            actor,
            "statement_reconciled",
            entity_type="bank_statement",
            entity_id=position.statement.id,
            entity_label=position.statement.reference,
            after={"closing": str(position.statement.closing_balance)},
        )
        return position

    # -- every source against its postings --------------------------------------------------

    def sources(self, actor: AnyActor, *, start: date, end: date) -> list[SourceRow]:
        """POS shifts, each bank and wallet, customer receipts, supplier payments and tax
        payments, each against what the ledger holds for them."""
        actor.require_any("reconciliation.manage", "journal.view")
        business_id = _business_of(actor)
        rows = [self._shifts(business_id, start, end)]
        for position in self._treasury.accounts(actor):
            kind = FinancialAccountKind(position.account.kind)
            if kind in (FinancialAccountKind.BANK, FinancialAccountKind.MOBILE_MONEY):
                rows.append(self._statement_row(actor, position.account))
        rows.append(
            self._records_row(
                "customer_receipts",
                "Customer receipts",
                [
                    (receipt.amount, receipt.journal_entry_fk)
                    for receipt in self._db.scalars(
                        select(CustomerReceipt).where(
                            CustomerReceipt.business_id == business_id,
                            CustomerReceipt.received_on.between(start, end),
                        )
                    ).all()
                ],
                "11000",
                credit=True,
            )
        )
        rows.append(
            self._records_row(
                "supplier_payments",
                "Supplier payments",
                [
                    (money(payment.amount + payment.withheld), payment.journal_entry_fk)
                    for payment in self._db.scalars(
                        select(SupplierPayment).where(
                            SupplierPayment.business_id == business_id,
                            SupplierPayment.paid_on.between(start, end),
                        )
                    ).all()
                ],
                "20000",
                credit=False,
            )
        )
        rows.append(
            self._records_row(
                "tax_payments",
                "Tax payments",
                [
                    (payment.amount, payment.journal_entry_fk)
                    for payment in self._db.scalars(
                        select(TaxPayment).where(
                            TaxPayment.business_id == business_id,
                            TaxPayment.paid_on.between(start, end),
                        )
                    ).all()
                ],
                "11400",
                credit=False,
            )
        )
        return rows

    def _shifts(self, business_id: uuid.UUID, start: date, end: date) -> SourceRow:
        shifts = self._db.scalars(
            select(PosShift).where(
                PosShift.business_id == business_id,
                PosShift.state == "closed",
                PosShift.business_day.between(start, end),
            )
        ).all()
        recorded = money(sum((abs(shift.variance or ZERO) for shift in shifts), ZERO))
        posted = ZERO
        unposted = 0
        notes = []
        for shift in shifts:
            if (shift.variance or ZERO) == ZERO:
                continue
            if shift.variance_journal_fk is None:
                unposted += 1
                notes.append(f"{shift.number}: variance not posted")
                continue
            posted = money(posted + self._entry_amount(shift.variance_journal_fk, "60900"))
        return SourceRow(
            source="pos_shifts",
            label="Register shifts",
            records=len(shifts),
            recorded=recorded,
            posted=posted,
            difference=money(recorded - posted),
            unposted=unposted,
            agrees=recorded == posted and unposted == 0,
            notes=notes,
        )

    def _statement_row(self, actor: AnyActor, account: FinancialAccount) -> SourceRow:
        latest = self._db.scalar(
            select(BankStatement)
            .where(BankStatement.financial_account_fk == account.id)
            .order_by(BankStatement.ends_on.desc())
            .limit(1)
        )
        if latest is None:
            return SourceRow(
                source=f"account:{account.id}",
                label=account.name,
                records=0,
                recorded=ZERO,
                posted=ZERO,
                difference=ZERO,
                unposted=0,
                agrees=False,
                notes=["No statement imported"],
            )
        position = self.position(actor, latest.id)
        return SourceRow(
            source=f"account:{account.id}",
            label=account.name,
            records=len(latest.lines),
            recorded=latest.closing_balance,
            posted=position.ledger_balance,
            difference=position.difference,
            unposted=position.unmatched,
            agrees=position.reconciled,
            notes=[f"Statement {latest.reference} to {latest.ends_on.isoformat()}"],
        )

    def _records_row(
        self,
        source: str,
        label: str,
        records: list[tuple[Decimal, uuid.UUID | None]],
        account_code: str,
        *,
        credit: bool,
    ) -> SourceRow:
        recorded = money(sum((amount for amount, _ in records), ZERO))
        posted = ZERO
        unposted = 0
        for _, entry_id in records:
            if entry_id is None:
                unposted += 1
                continue
            posted = money(posted + self._entry_amount(entry_id, account_code, credit=credit))
        return SourceRow(
            source=source,
            label=label,
            records=len(records),
            recorded=recorded,
            posted=posted,
            difference=money(recorded - posted),
            unposted=unposted,
            agrees=recorded == posted and unposted == 0,
        )

    def _entry_amount(
        self, entry_id: uuid.UUID, account_code: str, *, credit: bool | None = None
    ) -> Decimal:
        """What an entry moved on one account: its credits, its debits, or either."""
        debit, credit_total = self._db.execute(
            select(
                func.coalesce(func.sum(JournalLine.base_debit), 0),
                func.coalesce(func.sum(JournalLine.base_credit), 0),
            )
            .join(Account, Account.id == JournalLine.account_fk)
            .where(JournalLine.entry_fk == entry_id, Account.code == account_code)
        ).one()
        debit, credit_total = Decimal(str(debit)), Decimal(str(credit_total))
        if credit is True:
            return money(credit_total)
        if credit is False:
            return money(debit)
        return money(max(debit, credit_total))

    # -- internals ------------------------------------------------------------------------

    def _line(self, actor: AnyActor, line_id: uuid.UUID) -> StatementLine:
        line = self._db.get(StatementLine, line_id)
        if line is None or line.business_id != actor.business_id:
            raise NotFoundError("Statement line not found.")
        return line

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
