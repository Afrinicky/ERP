"""Expenses: costs incurred, approved where large, settled when paid. PRD §20.3.

A cost is recognised when it is incurred, and paying it afterwards recognises nothing:
an expense on credit or a staff claim posts the cost to its account and the debt to the
payee, and the later payment only clears the debt. A capital purchase is an asset, never
an expense. A second record of the same bill is refused unless someone says why it is
not one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.treasury import TreasuryService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.finance import (
    OTHER_PAYABLES,
    RECOVERABLE_INPUT_TAX,
    STAFF_CLAIMS_PAYABLE,
    ExpenseClass,
    ExpenseSettlement,
    ExpenseState,
    Frequency,
    advance,
    assert_classified,
)
from nickland.domain.ledger import EntryLine, JournalSource, Side, SubledgerKind
from nickland.domain.money import ZERO, money
from nickland.persistence.catalogue_models import Party
from nickland.persistence.finance_models import Expense, FinancialAccount, RecurringExpense
from nickland.persistence.ledger_models import Account, AccountGroup
from nickland.persistence.models import Business, Setting, User
from nickland.persistence.tax_models import TaxAuthority
from nickland.platform.clock import Clock, SystemClock, business_date

THRESHOLD_KEY = "expenses.approval_threshold"


@dataclass(slots=True)
class ExpenseDraft:
    payee_name: str
    category_code: str
    description: str
    net: Decimal
    settlement: ExpenseSettlement | str = ExpenseSettlement.PAID
    input_tax: Decimal = ZERO
    classification: ExpenseClass | str = ExpenseClass.OPERATING
    incurred_on: date | None = None
    payee_reference: str | None = None
    payee_party_id: uuid.UUID | None = None
    employee_id: uuid.UUID | None = None
    financial_account_id: uuid.UUID | None = None
    duplicate_reason: str | None = None
    evidence_attachment_id: uuid.UUID | None = None
    analysis_code_id: uuid.UUID | None = None


@dataclass(slots=True)
class RecurringDraft:
    name: str
    payee_name: str
    category_code: str
    description: str
    net: Decimal
    frequency: Frequency | str
    next_due: date
    settlement: ExpenseSettlement | str = ExpenseSettlement.ON_CREDIT
    input_tax: Decimal = ZERO
    payee_party_id: uuid.UUID | None = None
    financial_account_id: uuid.UUID | None = None
    ends_on: date | None = None


class ExpenseService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._treasury = TreasuryService(session, clock=self._clock)

    # -- the threshold ----------------------------------------------------------------------

    def threshold(self, business_id: uuid.UUID) -> Decimal | None:
        stored = self._db.scalar(
            select(Setting).where(Setting.business_id == business_id, Setting.key == THRESHOLD_KEY)
        )
        return (
            Decimal(str(stored.value)) if stored is not None and stored.value is not None else None
        )

    def set_threshold(self, actor: AnyActor, amount: Decimal | None) -> Decimal | None:
        """Expenses at or above this wait for approval. None: none do."""
        actor.require("expense.approve")
        if amount is not None and money(amount) <= ZERO:
            raise ValidationError("A threshold above zero, or none.", field="amount")
        stored = self._db.scalar(
            select(Setting).where(
                Setting.business_id == actor.business_id, Setting.key == THRESHOLD_KEY
            )
        )
        before = stored.value if stored else None
        value = str(money(amount)) if amount is not None else None
        if stored is None:
            stored = Setting(
                business_id=actor.business_id,
                key=THRESHOLD_KEY,
                value=value,
                description="Expenses at or above this amount wait for approval.",
            )
            self._db.add(stored)
        else:
            stored.value = value
        self._db.flush()
        self._audit.record(
            actor,
            "expense_threshold_amended",
            entity_type="setting",
            entity_id=stored.id,
            entity_label=THRESHOLD_KEY,
            before={"value": before},
            after={"value": value},
        )
        return self.threshold(_business_of(actor))

    # -- recording --------------------------------------------------------------------------

    def record(
        self, actor: AnyActor, draft: ExpenseDraft, *, recurring_id: uuid.UUID | None = None
    ) -> Expense:
        """Record a cost. Below the threshold it is recognised at once; at or above it,
        it waits for approval."""
        actor.require("expense.manage")
        business_id = _business_of(actor)
        settlement = ExpenseSettlement(draft.settlement)
        classification = ExpenseClass(draft.classification)
        net = money(draft.net)
        tax = money(draft.input_tax)
        if net <= ZERO:
            raise ValidationError("An amount above zero.", field="net")
        if tax < ZERO:
            raise ValidationError("Input tax is not negative.", field="input_tax")
        payee = " ".join((draft.payee_name or "").split())
        if not payee:
            raise ValidationError("Name who was paid.", field="payee_name")
        description = (draft.description or "").strip()
        if not description:
            raise ValidationError("Say what the expense was for.", field="description")
        category = self._category(business_id, draft.category_code)
        assert_classified(classification, self._group_code(category))
        party = None
        if draft.payee_party_id is not None:
            party = self._db.get(Party, draft.payee_party_id)
            if party is None or party.business_id != business_id:
                raise NotFoundError("Payee not found.")
        account: FinancialAccount | None = None
        if settlement is ExpenseSettlement.PAID:
            if draft.financial_account_id is None:
                raise ValidationError(
                    "Name the account it was paid from.", field="financial_account_id"
                )
            account = self._treasury.require(actor, draft.financial_account_id)
            if not account.is_active:
                raise ConflictError(f"{account.name} is closed.")
        if settlement is ExpenseSettlement.STAFF_CLAIM:
            employee = self._db.get(User, draft.employee_id) if draft.employee_id else None
            if employee is None or employee.business_id != business_id:
                raise ValidationError("A staff claim names who is owed.", field="employee_id")
        incurred_on = draft.incurred_on or self._today(business_id)
        reference = (draft.payee_reference or "").strip() or None

        duplicate = self._duplicate_of(
            business_id, payee, party, reference, money(net + tax), incurred_on
        )
        reason = (draft.duplicate_reason or "").strip() or None
        if duplicate is not None and reason is None:
            raise ConflictError(
                f"This looks like {duplicate.number}, already recorded for {duplicate.payee_name}"
                + (
                    f" under reference {duplicate.payee_reference}"
                    if duplicate.payee_reference
                    else ""
                )
                + ". If it is a different bill, record why.",
                duplicate=duplicate.number,
            )

        expense = Expense(
            business_id=business_id,
            branch_id=actor.branch_id,
            number=OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "expense", branch_id=actor.branch_id, on=incurred_on)
            .number,
            payee_name=payee,
            payee_party_fk=party.id if party else None,
            employee_id=draft.employee_id if settlement is ExpenseSettlement.STAFF_CLAIM else None,
            category_code=category.code,
            description=description,
            classification=classification.value,
            net=net,
            input_tax=tax,
            total=money(net + tax),
            incurred_on=incurred_on,
            payee_reference=reference,
            settlement=settlement.value,
            financial_account_fk=account.id if account else None,
            state=ExpenseState.AWAITING_APPROVAL.value,
            submitted_by_id=actor.user_id,
            duplicate_of_fk=duplicate.id if duplicate else None,
            duplicate_reason=reason if duplicate else None,
            recurring_fk=recurring_id,
            evidence_attachment_fk=draft.evidence_attachment_id,
            analysis_code_fk=draft.analysis_code_id,
        )
        self._db.add(expense)
        self._db.flush()
        threshold = self.threshold(business_id)
        holds: list[str] = []
        if threshold is not None and expense.total >= threshold:
            holds.append(f"At or above the approval threshold of {money(threshold):,.2f}.")
        from nickland.application.services.budgets import BudgetService

        budgets = BudgetService(self._db, clock=self._clock)
        over = budgets.check_spend(
            business_id,
            account_code=category.code,
            on=incurred_on,
            amount=net,
            branch_id=actor.branch_id,
            analysis_code_id=draft.analysis_code_id,
        )
        if over is not None:
            request = budgets.hold_for(actor, over, expense.id, net)
            holds.append(request.reason)
        if holds:
            expense.hold_reason = " ".join(holds)
            self._db.flush()
        else:
            self._recognise(actor, expense)
        self._audit.record(
            actor,
            "expense_recorded",
            entity_type="expense",
            entity_id=expense.id,
            entity_label=expense.number,
            reason=expense.duplicate_reason,
            after={
                "payee": payee,
                "category": category.code,
                "total": str(expense.total),
                "settlement": settlement.value,
                "state": expense.state,
            },
        )
        return expense

    def approve(
        self, actor: AnyActor, expense_id: uuid.UUID, *, budget_cleared: bool = False
    ) -> Expense:
        actor.require("expense.approve")
        expense = self.require(actor, expense_id)
        if expense.state != ExpenseState.AWAITING_APPROVAL.value:
            raise ConflictError(f"{expense.number} is not awaiting approval.")
        if expense.submitted_by_id == actor.user_id and not actor.is_administrator:
            raise ConflictError("An expense is approved by someone other than whoever recorded it.")
        if not budget_cleared:
            from nickland.persistence.planning_models import BudgetRequest

            pending = self._db.scalar(
                select(BudgetRequest).where(
                    BudgetRequest.expense_fk == expense.id, BudgetRequest.state == "pending"
                )
            )
            if pending is not None:
                # Spending beyond a budget is a budget decision as well as an expense one.
                actor.require("budget.approve")
                pending.state = "approved"
                pending.decided_by_id = actor.user_id
                pending.decided_at = self._clock.now()
                pending.decision_note = "Approved with the expense."
        expense.approved_by_id = actor.user_id
        expense.decided_at = self._clock.now()
        self._recognise(actor, expense)
        self._audit.record(
            actor,
            "expense_approved",
            entity_type="expense",
            entity_id=expense.id,
            entity_label=expense.number,
            after={"total": str(expense.total)},
        )
        return expense

    def reject(self, actor: AnyActor, expense_id: uuid.UUID, *, reason: str) -> Expense:
        actor.require("expense.approve")
        expense = self.require(actor, expense_id)
        if expense.state != ExpenseState.AWAITING_APPROVAL.value:
            raise ConflictError(f"{expense.number} is not awaiting approval.")
        if not reason.strip():
            raise ValidationError("A rejection records why.", field="reason")
        expense.state = ExpenseState.REJECTED.value
        expense.rejection_reason = reason.strip()
        expense.approved_by_id = actor.user_id
        expense.decided_at = self._clock.now()
        self._db.flush()
        self._audit.record(
            actor,
            "expense_rejected",
            entity_type="expense",
            entity_id=expense.id,
            entity_label=expense.number,
            reason=reason.strip(),
        )
        return expense

    def settle(
        self,
        actor: AnyActor,
        expense_id: uuid.UUID,
        *,
        financial_account_id: uuid.UUID,
        settled_on: date | None = None,
    ) -> Expense:
        """Pay what an expense on credit or a staff claim still owes. Recognises nothing:
        the cost was recognised when it was incurred."""
        actor.require("expense.manage")
        expense = self.require(actor, expense_id)
        if (
            expense.state != ExpenseState.RECOGNISED.value
            or expense.settlement == ExpenseSettlement.PAID.value
        ):
            raise ConflictError(f"{expense.number} owes nothing.")
        account = self._treasury.require(actor, financial_account_id)
        if not account.is_active:
            raise ConflictError(f"{account.name} is closed.")
        on = settled_on or self._today(expense.business_id)
        owed = self._liability(expense)
        expense.settlement_journal_fk = self._post(
            actor,
            expense,
            narration=f"{expense.number} paid to {expense.payee_name}",
            lines=[
                owed,
                EntryLine(
                    self._treasury.ledger_code(account),
                    Side.CREDIT,
                    expense.total,
                    expense.payee_reference or expense.number,
                ),
            ],
            on=on,
            key=f"expense_settled:{expense.id}",
            side_of_liability=Side.DEBIT,
        )
        expense.state = ExpenseState.SETTLED.value
        expense.settled_on = on
        expense.settled_from_fk = account.id
        self._db.flush()
        return expense

    def require(self, actor: AnyActor, expense_id: uuid.UUID) -> Expense:
        actor.require_any("expense.manage", "expense.approve", "journal.view")
        expense = self._db.get(Expense, expense_id)
        if expense is None or expense.business_id != actor.business_id:
            raise NotFoundError("Expense not found.")
        return expense

    def expenses(
        self, actor: AnyActor, *, state: str | None = None, limit: int = 200
    ) -> list[Expense]:
        actor.require_any("expense.manage", "expense.approve", "journal.view")
        query = select(Expense).where(Expense.business_id == actor.business_id)
        if state:
            query = query.where(Expense.state == state)
        return list(
            self._db.scalars(
                query.order_by(Expense.incurred_on.desc(), Expense.number.desc()).limit(
                    max(min(limit, 1000), 1)
                )
            ).all()
        )

    # -- recurring --------------------------------------------------------------------------

    def schedule(self, actor: AnyActor, draft: RecurringDraft) -> RecurringExpense:
        actor.require("expense.manage")
        business_id = _business_of(actor)
        name = " ".join(draft.name.split())
        if not name:
            raise ValidationError("Name the recurring expense.", field="name")
        if self._db.scalar(
            select(RecurringExpense.id).where(
                RecurringExpense.business_id == business_id, RecurringExpense.name == name
            )
        ):
            raise ConflictError(f"A recurring expense named {name!r} already exists.")
        category = self._category(business_id, draft.category_code)
        assert_classified(ExpenseClass.OPERATING, self._group_code(category))
        settlement = ExpenseSettlement(draft.settlement)
        if settlement is ExpenseSettlement.STAFF_CLAIM:
            raise ValidationError(
                "A recurring expense is paid, or owed to its payee.", field="settlement"
            )
        if settlement is ExpenseSettlement.PAID and draft.financial_account_id is None:
            raise ValidationError("Name the account it is paid from.", field="financial_account_id")
        if money(draft.net) <= ZERO:
            raise ValidationError("An amount above zero.", field="net")
        row = RecurringExpense(
            business_id=business_id,
            name=name,
            payee_name=" ".join(draft.payee_name.split()),
            payee_party_fk=draft.payee_party_id,
            category_code=category.code,
            description=draft.description.strip(),
            net=money(draft.net),
            input_tax=money(draft.input_tax),
            settlement=settlement.value,
            financial_account_fk=draft.financial_account_id,
            frequency=Frequency(draft.frequency).value,
            next_due=draft.next_due,
            ends_on=draft.ends_on,
        )
        self._db.add(row)
        self._db.flush()
        return row

    def recurring(self, actor: AnyActor) -> list[RecurringExpense]:
        actor.require_any("expense.manage", "expense.approve")
        return list(
            self._db.scalars(
                select(RecurringExpense)
                .where(RecurringExpense.business_id == actor.business_id)
                .order_by(RecurringExpense.next_due)
            ).all()
        )

    def raise_due(self, actor: AnyActor, *, as_at: date | None = None) -> list[Expense]:
        """Raise every recurring expense that has fallen due, once per period."""
        actor.require("expense.manage")
        business_id = _business_of(actor)
        today = as_at or self._today(business_id)
        raised: list[Expense] = []
        for row in self._db.scalars(
            select(RecurringExpense).where(
                RecurringExpense.business_id == business_id,
                RecurringExpense.is_active.is_(True),
                RecurringExpense.next_due <= today,
            )
        ).all():
            while row.is_active and row.next_due <= today:
                if row.ends_on is not None and row.next_due > row.ends_on:
                    row.is_active = False
                    break
                raised.append(
                    self.record(
                        actor,
                        ExpenseDraft(
                            payee_name=row.payee_name,
                            payee_party_id=row.payee_party_fk,
                            category_code=row.category_code,
                            description=f"{row.description} — {row.next_due:%B %Y}",
                            net=row.net,
                            input_tax=row.input_tax,
                            settlement=row.settlement,
                            financial_account_id=row.financial_account_fk,
                            incurred_on=row.next_due,
                            payee_reference=f"{row.name} {row.next_due.isoformat()}",
                        ),
                        recurring_id=row.id,
                    )
                )
                row.next_due = advance(row.next_due, Frequency(row.frequency))
        self._db.flush()
        return raised

    # -- internals ------------------------------------------------------------------------

    def _recognise(self, actor: AnyActor, expense: Expense) -> None:
        """Dr the cost (and recoverable tax), Cr the account paid from or the payee owed."""
        lines = [
            EntryLine(
                expense.category_code,
                Side.DEBIT,
                expense.net,
                expense.description[:200],
                analysis_code_id=expense.analysis_code_fk,
            )
        ]
        if expense.input_tax > ZERO:
            lines.append(
                EntryLine(
                    RECOVERABLE_INPUT_TAX,
                    Side.DEBIT,
                    expense.input_tax,
                    f"Input tax, {expense.number}",
                    party_id=self._authority(expense.business_id),
                    subledger=SubledgerKind.AUTHORITY,
                )
            )
        if expense.settlement == ExpenseSettlement.PAID.value:
            account = self._db.get(FinancialAccount, expense.financial_account_fk)
            assert account is not None
            lines.append(
                EntryLine(
                    self._treasury.ledger_code(account),
                    Side.CREDIT,
                    expense.total,
                    expense.payee_reference or expense.payee_name,
                )
            )
        else:
            lines.append(self._liability(expense))
        expense.journal_entry_fk = self._post(
            actor,
            expense,
            narration=f"{expense.number}: {expense.payee_name}, {expense.description[:120]}",
            lines=lines,
            on=expense.incurred_on,
            key=f"expense:{expense.id}",
            side_of_liability=Side.CREDIT,
        )
        expense.state = (
            ExpenseState.SETTLED.value
            if expense.settlement == ExpenseSettlement.PAID.value
            else ExpenseState.RECOGNISED.value
        )
        if expense.settlement == ExpenseSettlement.PAID.value:
            expense.settled_on = expense.incurred_on
            expense.settled_from_fk = expense.financial_account_fk
        self._db.flush()

    def _liability(self, expense: Expense) -> EntryLine:
        """The debt the expense leaves: to the payee on credit, to the employee on a claim.
        Credit side; the settlement flips it."""
        if expense.settlement == ExpenseSettlement.STAFF_CLAIM.value:
            return EntryLine(
                STAFF_CLAIMS_PAYABLE,
                Side.CREDIT,
                expense.total,
                f"Claim {expense.number}",
                party_id=expense.employee_id,
                subledger=SubledgerKind.EMPLOYEE,
            )
        return EntryLine(
            OTHER_PAYABLES, Side.CREDIT, expense.total, f"Owed to {expense.payee_name}"
        )

    def _post(
        self,
        actor: AnyActor,
        expense: Expense,
        *,
        narration: str,
        lines: list[EntryLine],
        on: date,
        key: str,
        side_of_liability: Side,
    ) -> uuid.UUID:
        if side_of_liability is Side.DEBIT:
            # Settling: the liability line moves to the debit side.
            first = lines[0]
            lines = [
                EntryLine(
                    first.account_code,
                    Side.DEBIT,
                    first.amount,
                    first.narration,
                    party_id=first.party_id,
                    subledger=first.subledger,
                ),
                *lines[1:],
            ]
        business = self._db.get(Business, expense.business_id)
        return (
            JournalService(self._db, clock=self._clock)
            .post(
                actor,
                JournalDraft(
                    posting_date=on,
                    narration=narration,
                    lines=lines,
                    source=JournalSource.EXPENSE,
                    source_reference=expense.number,
                    source_id=expense.id,
                    idempotency_key=key,
                    currency_code=business.base_currency if business else None,
                    attachment_ids=(
                        [str(expense.evidence_attachment_fk)]
                        if expense.evidence_attachment_fk
                        else []
                    ),
                    branch_id=actor.branch_id,
                ),
            )
            .entry.id
        )

    def _duplicate_of(
        self,
        business_id: uuid.UUID,
        payee: str,
        party: Party | None,
        reference: str | None,
        total: Decimal,
        incurred_on: date,
    ) -> Expense | None:
        same_payee = (
            Expense.payee_party_fk == party.id
            if party is not None
            else func.lower(Expense.payee_name) == payee.lower()
        )
        base = select(Expense).where(
            Expense.business_id == business_id,
            Expense.state != ExpenseState.REJECTED.value,
            same_payee,
        )
        if reference is not None:
            found = self._db.scalar(
                base.where(func.lower(Expense.payee_reference) == reference.lower()).limit(1)
            )
            if found is not None:
                return found
        return self._db.scalar(
            base.where(Expense.total == total, Expense.incurred_on == incurred_on).limit(1)
        )

    def _category(self, business_id: uuid.UUID, code: str) -> Account:
        account = self._db.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )
        if account is None or not account.is_active:
            raise NotFoundError(f"No account {code} is in use.")
        return account

    def _group_code(self, account: Account) -> str | None:
        group = self._db.get(AccountGroup, account.group_fk) if account.group_fk else None
        return group.code if group else None

    def _authority(self, business_id: uuid.UUID) -> uuid.UUID:
        authority = self._db.scalar(
            select(TaxAuthority)
            .where(TaxAuthority.business_id == business_id)
            .order_by(TaxAuthority.code)
            .limit(1)
        )
        if authority is None:
            raise ConflictError("No tax authority is set up to recover input tax from.")
        return authority.id

    def _today(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
