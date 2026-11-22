"""Treasury: the business's cash, bank and Mobile Money accounts, and money moved between
them. PRD §20.4.

Each financial account posts to its own ledger account, so it has its own balance, its own
cashbook and its own reconciliation. A transfer moves a balance from one to another and
recognises nothing: the income statement does not move when money changes pocket.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.ledger_reports import AccountLedger, LedgerReportService
from nickland.application.services.organisation import OrganisationService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.finance import (
    BANK_CHARGES,
    EXCHANGE_DIFFERENCES,
    INTEREST_INCOME,
    SEEDED_FINANCIAL_ACCOUNTS,
    FinancialAccountKind,
    TreasuryMovementKind,
    assert_no_income_or_expense,
    next_account_code,
)
from nickland.domain.ledger import AccountKind, CashFlowCategory, EntryLine, JournalSource, Side
from nickland.domain.money import ZERO, money
from nickland.persistence.finance_models import FinancialAccount, TreasuryMovement
from nickland.persistence.ledger_models import Account, AccountGroup
from nickland.persistence.locking import advisory_lock
from nickland.persistence.models import Business, User
from nickland.platform.clock import Clock, SystemClock, business_date


@dataclass(slots=True)
class FinancialAccountDraft:
    kind: FinancialAccountKind | str
    name: str
    institution: str | None = None
    number: str | None = None
    branch_name: str | None = None
    custodian_id: uuid.UUID | None = None
    notes: str | None = None


@dataclass(slots=True)
class MovementDraft:
    kind: TreasuryMovementKind | str
    amount: Decimal
    reason: str
    from_account_id: uuid.UUID | None = None
    to_account_id: uuid.UUID | None = None
    charge: Decimal = ZERO
    occurred_on: date | None = None
    reference: str | None = None


@dataclass(slots=True)
class AccountPosition:
    account: FinancialAccount
    ledger: Account
    balance: Decimal
    custodian: str | None


class TreasuryService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- accounts ---------------------------------------------------------------------------

    def ensure_seeded(self, business_id: uuid.UUID) -> None:
        """One financial account per base cash account, the first time it is asked for."""
        # Two desks asking at once for the first time would both create them.
        advisory_lock(self._db, "treasury-seed", business_id)
        existing = {
            row.account_fk
            for row in self._db.scalars(
                select(FinancialAccount).where(FinancialAccount.business_id == business_id)
            ).all()
        }
        business = self._db.get(Business, business_id)
        for kind, name in SEEDED_FINANCIAL_ACCOUNTS:
            ledger = self._ledger_by_code(business_id, kind.base_account)
            if ledger is None or ledger.id in existing:
                continue
            self._db.add(
                FinancialAccount(
                    business_id=business_id,
                    kind=kind.value,
                    name=name,
                    account_fk=ledger.id,
                    currency_code=business.base_currency if business else "GHS",
                )
            )
        self._db.flush()

    def accounts(self, actor: AnyActor, *, include_inactive: bool = False) -> list[AccountPosition]:
        """Every financial account with its balance today."""
        actor.require_any("financial_account.manage", "journal.view", "receipt.manage")
        business_id = _business_of(actor)
        self.ensure_seeded(business_id)
        query = select(FinancialAccount).where(FinancialAccount.business_id == business_id)
        if not include_inactive:
            query = query.where(FinancialAccount.is_active.is_(True))
        reports = LedgerReportService(self._db, clock=self._clock)
        positions = []
        for row in self._db.scalars(query).all():
            ledger = self._db.get(Account, row.account_fk)
            assert ledger is not None
            custodian = self._db.get(User, row.custodian_id) if row.custodian_id else None
            positions.append(
                AccountPosition(
                    account=row,
                    ledger=ledger,
                    balance=reports.account_balance(business_id, ledger.id),
                    custodian=custodian.full_name if custodian else None,
                )
            )
        return sorted(positions, key=lambda item: item.ledger.code)

    def require(self, actor: AnyActor, account_id: uuid.UUID) -> FinancialAccount:
        row = self._db.get(FinancialAccount, account_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Financial account not found.")
        return row

    def default_for(self, business_id: uuid.UUID, kind: FinancialAccountKind) -> FinancialAccount:
        """The first account of a kind: where a payment goes when no account is named."""
        self.ensure_seeded(business_id)
        ledger = self._ledger_by_code(business_id, kind.base_account)
        row = (
            self._db.scalar(
                select(FinancialAccount).where(
                    FinancialAccount.business_id == business_id,
                    FinancialAccount.account_fk == ledger.id,
                )
            )
            if ledger
            else None
        )
        if row is None:
            raise NotFoundError(f"No {kind.label.lower()} is set up.")
        return row

    def ledger_code(self, account: FinancialAccount) -> str:
        ledger = self._db.get(Account, account.account_fk)
        assert ledger is not None
        return ledger.code

    def create_account(self, actor: AnyActor, draft: FinancialAccountDraft) -> FinancialAccount:
        """A further bank, wallet, drawer or petty cash box, with its own ledger account."""
        actor.require("financial_account.manage")
        business_id = _business_of(actor)
        self.ensure_seeded(business_id)
        kind = FinancialAccountKind(draft.kind)
        name = " ".join(draft.name.split())
        if not 2 <= len(name) <= 120:
            raise ValidationError("An account is named in 2 to 120 characters.", field="name")
        if self._db.scalar(
            select(FinancialAccount.id).where(
                FinancialAccount.business_id == business_id, FinancialAccount.name == name
            )
        ):
            raise ConflictError(f"A financial account named {name!r} already exists.")
        base = self._ledger_by_code(business_id, kind.base_account)
        if base is None:
            raise NotFoundError(f"The chart has no account {kind.base_account} to extend.")
        taken = self._db.scalars(select(Account.code).where(Account.business_id == business_id))
        code = next_account_code(kind.base_account, taken)
        business = self._db.get(Business, business_id)
        ledger = Account(
            business_id=business_id,
            code=code,
            name=name,
            kind=AccountKind.ASSET.value,
            statement=AccountKind.ASSET.statement.value,
            group_fk=base.group_fk,
            cash_flow_category=CashFlowCategory.CASH.value,
            currency_code=business.base_currency if business else None,
            permits_manual_posting=True,
            search_text=f"{code} {name}".lower(),
        )
        self._db.add(ledger)
        self._db.flush()
        row = FinancialAccount(
            business_id=business_id,
            kind=kind.value,
            name=name,
            account_fk=ledger.id,
            institution=_clean(draft.institution),
            number=_clean(draft.number),
            branch_name=_clean(draft.branch_name),
            custodian_id=draft.custodian_id,
            currency_code=business.base_currency if business else "GHS",
            notes=_clean(draft.notes),
        )
        self._db.add(row)
        self._db.flush()
        self._audit.record(
            actor,
            "financial_account_created",
            entity_type="financial_account",
            entity_id=row.id,
            entity_label=name,
            after={"kind": kind.value, "ledger_account": code, "institution": row.institution},
        )
        return row

    def amend_account(
        self,
        actor: AnyActor,
        account_id: uuid.UUID,
        *,
        name: str | None = None,
        institution: str | None = None,
        number: str | None = None,
        branch_name: str | None = None,
        custodian_id: uuid.UUID | None = None,
        is_active: bool | None = None,
    ) -> FinancialAccount:
        actor.require("financial_account.manage")
        row = self.require(actor, account_id)
        before = {"name": row.name, "custodian": str(row.custodian_id), "active": row.is_active}
        if name is not None:
            row.name = " ".join(name.split())
            ledger = self._db.get(Account, row.account_fk)
            if ledger is not None and ledger.code not in {
                kind.base_account for kind in FinancialAccountKind
            }:
                ledger.name = row.name
        row.institution = _clean(institution) if institution is not None else row.institution
        row.number = _clean(number) if number is not None else row.number
        row.branch_name = _clean(branch_name) if branch_name is not None else row.branch_name
        row.custodian_id = custodian_id if custodian_id is not None else row.custodian_id
        if is_active is False:
            balance = LedgerReportService(self._db, clock=self._clock).account_balance(
                row.business_id, row.account_fk
            )
            if balance != ZERO:
                raise ConflictError(
                    f"{row.name} still holds {balance}. Transfer it out before closing the account."
                )
        if is_active is not None:
            row.is_active = is_active
        self._db.flush()
        self._audit.record(
            actor,
            "financial_account_amended",
            entity_type="financial_account",
            entity_id=row.id,
            entity_label=row.name,
            before=before,
            after={"name": row.name, "custodian": str(row.custodian_id), "active": row.is_active},
        )
        return row

    # -- movements ------------------------------------------------------------------------

    def record(self, actor: AnyActor, draft: MovementDraft) -> TreasuryMovement:
        """A transfer, charge, interest or adjustment, posted at once."""
        actor.require("transfer.manage")
        business_id = _business_of(actor)
        kind = TreasuryMovementKind(draft.kind)
        amount = money(draft.amount)
        charge = money(draft.charge)
        if amount <= ZERO:
            raise ValidationError("An amount above zero.", field="amount")
        if charge < ZERO:
            raise ValidationError("A charge is not negative.", field="charge")
        reason = (draft.reason or "").strip()
        if not reason:
            raise ValidationError("A movement records why.", field="reason")
        source = self.require(actor, draft.from_account_id) if draft.from_account_id else None
        target = self.require(actor, draft.to_account_id) if draft.to_account_id else None
        lines: list[EntryLine] = []
        if kind is TreasuryMovementKind.TRANSFER:
            if source is None or target is None:
                raise ValidationError("A transfer names both accounts.", field="to_account_id")
            if source.id == target.id:
                raise ValidationError("A transfer is between two different accounts.")
            for item in (source, target):
                if not item.is_active:
                    raise ConflictError(f"{item.name} is closed.")
            lines = [
                EntryLine(self.ledger_code(target), Side.DEBIT, amount, f"From {source.name}"),
                EntryLine(self.ledger_code(source), Side.CREDIT, amount, f"To {target.name}"),
            ]
            assert_no_income_or_expense(
                (self._group_code(item.account_fk) for item in (source, target)), what="A transfer"
            )
            if charge > ZERO:
                lines += [
                    EntryLine(
                        BANK_CHARGES, Side.DEBIT, charge, f"Charge on transfer to {target.name}"
                    ),
                    EntryLine(self.ledger_code(source), Side.CREDIT, charge, "Transfer charge"),
                ]
        else:
            account = source if kind in _OUTWARD else target
            if account is None:
                raise ValidationError(
                    "Name the account it leaves."
                    if kind in _OUTWARD
                    else "Name the account it enters.",
                    field="from_account_id" if kind in _OUTWARD else "to_account_id",
                )
            if charge > ZERO:
                raise ValidationError("A charge belongs to a transfer.", field="charge")
            counter = {
                TreasuryMovementKind.BANK_CHARGE: BANK_CHARGES,
                TreasuryMovementKind.INTEREST: INTEREST_INCOME,
                TreasuryMovementKind.ADJUSTMENT_IN: EXCHANGE_DIFFERENCES,
                TreasuryMovementKind.ADJUSTMENT_OUT: EXCHANGE_DIFFERENCES,
            }[kind]
            own = self.ledger_code(account)
            if kind in _OUTWARD:
                lines = [
                    EntryLine(counter, Side.DEBIT, amount, reason),
                    EntryLine(own, Side.CREDIT, amount, kind.label),
                ]
            else:
                lines = [
                    EntryLine(own, Side.DEBIT, amount, kind.label),
                    EntryLine(counter, Side.CREDIT, amount, reason),
                ]

        today = draft.occurred_on or self._today(business_id)
        movement = TreasuryMovement(
            business_id=business_id,
            branch_id=actor.branch_id,
            number=OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "treasury_movement", branch_id=actor.branch_id)
            .number,
            kind=kind.value,
            occurred_on=today,
            from_account_fk=source.id if source else None,
            to_account_fk=target.id if target else None,
            amount=amount,
            charge=charge,
            reference=_clean(draft.reference),
            reason=reason,
            recorded_by_id=actor.user_id,
            recorded_at=self._clock.now(),
        )
        self._db.add(movement)
        self._db.flush()
        business = self._db.get(Business, business_id)
        entry = (
            JournalService(self._db, clock=self._clock)
            .post(
                actor,
                JournalDraft(
                    posting_date=today,
                    narration=f"{kind.label}: {reason}",
                    lines=lines,
                    source=JournalSource.TREASURY,
                    source_reference=movement.reference or movement.number,
                    source_id=movement.id,
                    idempotency_key=f"treasury:{movement.id}",
                    currency_code=business.base_currency if business else None,
                    branch_id=actor.branch_id,
                ),
            )
            .entry
        )
        movement.journal_entry_fk = entry.id
        self._db.flush()
        self._audit.record(
            actor,
            "treasury_movement_recorded",
            entity_type="treasury_movement",
            entity_id=movement.id,
            entity_label=movement.number,
            reason=reason,
            after={"kind": kind.value, "amount": str(amount), "charge": str(charge)},
        )
        return movement

    def movements(self, actor: AnyActor, *, limit: int = 100) -> list[TreasuryMovement]:
        actor.require_any("financial_account.manage", "transfer.manage", "journal.view")
        return list(
            self._db.scalars(
                select(TreasuryMovement)
                .where(TreasuryMovement.business_id == actor.business_id)
                .order_by(TreasuryMovement.occurred_on.desc(), TreasuryMovement.recorded_at.desc())
                .limit(max(min(limit, 500), 1))
            ).all()
        )

    def cashbook(
        self,
        actor: AnyActor,
        account_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> tuple[FinancialAccount, AccountLedger]:
        """The daily cashbook or bank book: every movement on the account, with a running
        balance from the balance brought forward."""
        row = self.require(actor, account_id)
        ledger = LedgerReportService(self._db, clock=self._clock).account_ledger(
            actor, row.account_fk, start=start, end=end, limit=2000
        )
        return row, ledger

    # -- internals ------------------------------------------------------------------------

    def _ledger_by_code(self, business_id: uuid.UUID, code: str) -> Account | None:
        return self._db.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )

    def _group_code(self, account_id: uuid.UUID) -> str | None:
        ledger = self._db.get(Account, account_id)
        group = self._db.get(AccountGroup, ledger.group_fk) if ledger and ledger.group_fk else None
        return group.code if group else None

    def today(self, business_id: uuid.UUID) -> date:
        return self._today(business_id)

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


_OUTWARD = frozenset({TreasuryMovementKind.BANK_CHARGE, TreasuryMovementKind.ADJUSTMENT_OUT})


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
