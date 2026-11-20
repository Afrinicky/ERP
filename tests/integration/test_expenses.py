"""Expenses against a real database: recognition, approval, duplicates, classification,
staff claims, petty cash and recurrence. PRD §20.3.

Exit criterion 5 held here: cash paid is never equated with expense. An expense is
recognised once, when incurred; paying it later moves only cash and the debt.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.expenses import ExpenseDraft, ExpenseService, RecurringDraft
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.treasury import MovementDraft, TreasuryService
from nickland.domain.errors import ConflictError, PermissionDeniedError, ValidationError
from nickland.domain.finance import ExpenseState, FinancialAccountKind
from nickland.persistence.ledger_models import Account
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal
LATER = date(2027, 12, 31)


@pytest.fixture
def expenses(db: Session, clock: FixedClock) -> ExpenseService:
    return ExpenseService(db, clock=clock)


@pytest.fixture
def accounts(db: Session, clock: FixedClock, admin: Actor) -> dict:
    """The main bank and petty cash, each holding 2,000."""
    treasury = TreasuryService(db, clock=clock)
    business = admin.business_id
    assert business is not None
    bank = treasury.default_for(business, FinancialAccountKind.BANK)
    petty = treasury.default_for(business, FinancialAccountKind.PETTY_CASH)
    for account in (bank, petty):
        treasury.record(
            admin,
            MovementDraft(
                kind="adjustment_in", amount=D("2000"), to_account_id=account.id, reason="Float"
            ),
        )
    return {"bank": bank, "petty": petty}


def balance(db: Session, clock: FixedClock, admin: Actor, code: str) -> Decimal:
    account = db.scalar(
        select(Account).where(Account.business_id == admin.business_id, Account.code == code)
    )
    assert account is not None
    return LedgerReportService(db, clock=clock).account_balance(
        admin.business_id,  # type: ignore[arg-type]
        account.id,
        as_at=LATER,
    )


def expense_total(db: Session, clock: FixedClock, admin: Actor) -> Decimal:
    return (
        LedgerReportService(db, clock=clock)
        .profit_and_loss(admin, start=date(2026, 1, 1), end=LATER)
        .total_expenses
    )


def rent(**changes: object) -> ExpenseDraft:
    values: dict[str, object] = {
        "payee_name": "Osu Properties Ltd",
        "category_code": "60000",
        "description": "Shop rent, November",
        "net": D("1500"),
        "settlement": "on_credit",
        "payee_reference": "OSU-INV-2211",
    }
    values.update(changes)
    return ExpenseDraft(**values)  # type: ignore[arg-type]


class TestRecognition:
    def test_a_cost_on_credit_is_recognised_once_and_paying_it_is_not_an_expense(
        self,
        db: Session,
        clock: FixedClock,
        expenses: ExpenseService,
        admin: Actor,
        accounts: dict,
    ) -> None:
        before = expense_total(db, clock, admin)
        expense = expenses.record(admin, rent())
        assert expense.state == ExpenseState.RECOGNISED
        assert expense_total(db, clock, admin) == before + D("1500")
        assert balance(db, clock, admin, "20100") == D("1500")

        expenses.settle(admin, expense.id, financial_account_id=accounts["bank"].id)
        assert expense.state == ExpenseState.SETTLED
        assert balance(db, clock, admin, "20100") == D("0")
        assert balance(db, clock, admin, "10200") == D("500")
        assert expense_total(db, clock, admin) == before + D("1500")  # paid, not expensed again

    def test_petty_cash_with_recoverable_tax(
        self,
        db: Session,
        clock: FixedClock,
        expenses: ExpenseService,
        admin: Actor,
        accounts: dict,
    ) -> None:
        expenses.record(
            admin,
            ExpenseDraft(
                payee_name="Shell Ring Road",
                category_code="60300",
                description="Delivery van fuel",
                net=D("200"),
                input_tax=D("30"),
                settlement="paid",
                financial_account_id=accounts["petty"].id,
            ),
        )
        assert balance(db, clock, admin, "10400") == D("1770")
        assert balance(db, clock, admin, "60300") == D("200")
        assert balance(db, clock, admin, "13000") == D("30")

    def test_a_staff_claim_is_owed_to_the_employee(
        self, db: Session, clock: FixedClock, expenses: ExpenseService, admin: Actor, accounts: dict
    ) -> None:
        claim = expenses.record(
            admin,
            ExpenseDraft(
                payee_name="Accra Mall pharmacy",
                category_code="60800",
                description="Printer paper bought on the way in",
                net=D("85"),
                settlement="staff_claim",
                employee_id=admin.user_id,
            ),
        )
        assert balance(db, clock, admin, "20400") == D("85")
        expenses.settle(admin, claim.id, financial_account_id=accounts["petty"].id)
        assert balance(db, clock, admin, "20400") == D("0")
        with pytest.raises(ValidationError):
            expenses.record(admin, rent(settlement="staff_claim", payee_reference=None))

    def test_a_capital_purchase_is_an_asset_never_an_expense(
        self, db: Session, clock: FixedClock, expenses: ExpenseService, admin: Actor, accounts: dict
    ) -> None:
        with pytest.raises(ValidationError, match="non-current asset"):
            expenses.record(admin, rent(classification="capital"))
        with pytest.raises(ValidationError, match="expense account"):
            expenses.record(admin, rent(category_code="15000", payee_reference="X1"))
        before = expense_total(db, clock, admin)
        expenses.record(
            admin,
            ExpenseDraft(
                payee_name="Melcom",
                category_code="15000",
                classification="capital",
                description="Counter refrigerator for reagents",
                net=D("1800"),
                settlement="paid",
                financial_account_id=accounts["bank"].id,
            ),
        )
        assert balance(db, clock, admin, "15000") == D("1800")
        assert expense_total(db, clock, admin) == before


class TestControls:
    def test_the_same_bill_twice_is_refused_unless_explained(
        self, expenses: ExpenseService, admin: Actor, accounts: dict
    ) -> None:
        first = expenses.record(admin, rent())
        with pytest.raises(ConflictError, match=first.number):
            expenses.record(admin, rent())
        with pytest.raises(ConflictError):
            expenses.record(admin, rent(payee_reference=None, payee_name="osu properties ltd"))
        second = expenses.record(
            admin, rent(duplicate_reason="Landlord reissued under the same number")
        )
        assert second.duplicate_of_fk == first.id

    def test_a_large_expense_waits_for_someone_else_to_approve_it(
        self,
        db: Session,
        clock: FixedClock,
        expenses: ExpenseService,
        admin: Actor,
        accounts: dict,
    ) -> None:
        expenses.set_threshold(admin, D("1000"))
        clerk = make_actor(admin.business_id, permissions={"expense.manage", "expense.approve"})
        before = expense_total(db, clock, admin)
        expense = expenses.record(clerk, rent())
        assert expense.state == ExpenseState.AWAITING_APPROVAL
        assert expense.journal_entry_fk is None
        assert expense_total(db, clock, admin) == before
        with pytest.raises(ConflictError, match="someone other"):
            expenses.approve(clerk, expense.id)
        expenses.approve(admin, expense.id)
        assert expense.state == ExpenseState.RECOGNISED
        assert expense_total(db, clock, admin) == before + D("1500")
        small = expenses.record(clerk, rent(net=D("200"), payee_reference="OSU-2"))
        assert small.state == ExpenseState.RECOGNISED

    def test_a_rejected_expense_posts_nothing(
        self, expenses: ExpenseService, admin: Actor, accounts: dict
    ) -> None:
        expenses.set_threshold(admin, D("100"))
        expense = expenses.record(admin, rent())
        expenses.reject(admin, expense.id, reason="Rent is paid by the landlord's agent")
        assert expense.state == ExpenseState.REJECTED and expense.journal_entry_fk is None
        again = expenses.record(admin, rent())  # a rejected bill is no duplicate
        assert again.duplicate_of_fk is None

    def test_recording_needs_its_permission(self, expenses: ExpenseService, admin: Actor) -> None:
        viewer = make_actor(admin.business_id, permissions={"journal.view"})
        with pytest.raises(PermissionDeniedError):
            expenses.record(viewer, rent())


class TestRecurring:
    def test_a_monthly_expense_is_raised_once_per_month(
        self, expenses: ExpenseService, admin: Actor, accounts: dict
    ) -> None:
        expenses.schedule(
            admin,
            RecurringDraft(
                name="Shop rent",
                payee_name="Osu Properties Ltd",
                category_code="60000",
                description="Shop rent",
                net=D("1500"),
                frequency="monthly",
                next_due=date(2026, 9, 30),
            ),
        )
        raised = expenses.raise_due(admin, as_at=date(2026, 11, 5))
        assert [item.incurred_on for item in raised] == [date(2026, 9, 30), date(2026, 10, 30)]
        assert expenses.raise_due(admin, as_at=date(2026, 11, 5)) == []
        assert expenses.recurring(admin)[0].next_due == date(2026, 11, 30)
