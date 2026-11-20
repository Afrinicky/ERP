"""Treasury against a real database: financial accounts, transfers, charges, the cashbook."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.treasury import (
    FinancialAccountDraft,
    MovementDraft,
    TreasuryService,
)
from nickland.domain.errors import ConflictError, PermissionDeniedError, ValidationError
from nickland.domain.finance import FinancialAccountKind
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


@pytest.fixture
def treasury(db: Session, clock: FixedClock) -> TreasuryService:
    return TreasuryService(db, clock=clock)


def balances(treasury: TreasuryService, admin: Actor) -> dict[str, Decimal]:
    return {item.account.name: item.balance for item in treasury.accounts(admin)}


def result(db: Session, clock: FixedClock, admin: Actor) -> tuple[Decimal, Decimal]:
    pnl = LedgerReportService(db, clock=clock).profit_and_loss(
        admin, start=YEAR_START, end=YEAR_END
    )
    return pnl.total_revenue, pnl.total_expenses


@pytest.fixture
def funded(treasury: TreasuryService, admin: Actor) -> dict:
    """The main bank holding 1,000, received as interest so there is something to move."""
    bank = treasury.default_for(admin.business_id, FinancialAccountKind.BANK)  # type: ignore[arg-type]
    treasury.record(
        admin,
        MovementDraft(
            kind="interest", amount=D("1000"), to_account_id=bank.id, reason="Q3 interest"
        ),
    )
    second = treasury.create_account(
        admin,
        FinancialAccountDraft(
            kind="bank", name="GCB Ring Road", institution="GCB Bank", number="1021 0045 7781"
        ),
    )
    return {"bank": bank, "second": second}


class TestAccounts:
    def test_each_base_cash_account_is_a_financial_account(
        self, treasury: TreasuryService, admin: Actor
    ) -> None:
        listed = treasury.accounts(admin)
        assert [item.ledger.code for item in listed] == ["10100", "10200", "10300", "10400"]
        assert all(item.balance == D("0") for item in listed)

    def test_a_further_bank_gets_its_own_ledger_account(
        self, treasury: TreasuryService, admin: Actor, funded: dict
    ) -> None:
        assert treasury.ledger_code(funded["second"]) == "10201"
        third = treasury.create_account(admin, FinancialAccountDraft(kind="bank", name="Ecobank"))
        assert treasury.ledger_code(third) == "10202"
        with pytest.raises(ConflictError):
            treasury.create_account(admin, FinancialAccountDraft(kind="bank", name="Ecobank"))

    def test_an_account_holding_money_is_not_closed(
        self, treasury: TreasuryService, admin: Actor, funded: dict
    ) -> None:
        with pytest.raises(ConflictError, match="Transfer it out"):
            treasury.amend_account(admin, funded["bank"].id, is_active=False)
        treasury.amend_account(admin, funded["second"].id, is_active=False)
        assert "GCB Ring Road" not in balances(treasury, admin)

    def test_accounts_need_their_permission(self, treasury: TreasuryService, admin: Actor) -> None:
        clerk = make_actor(admin.business_id, permissions={"receipt.manage"})
        with pytest.raises(PermissionDeniedError):
            treasury.create_account(clerk, FinancialAccountDraft(kind="bank", name="Stanbic"))


class TestMovements:
    def test_a_transfer_moves_balances_and_touches_no_income_or_expense(
        self,
        db: Session,
        clock: FixedClock,
        treasury: TreasuryService,
        admin: Actor,
        funded: dict,
    ) -> None:
        before = result(db, clock, admin)
        treasury.record(
            admin,
            MovementDraft(
                kind="transfer",
                amount=D("400"),
                from_account_id=funded["bank"].id,
                to_account_id=funded["second"].id,
                reason="Fund the supplier account",
            ),
        )
        held = balances(treasury, admin)
        assert held["Main bank account"] == D("600") and held["GCB Ring Road"] == D("400")
        assert result(db, clock, admin) == before

    def test_a_transfer_charge_is_the_only_expense(
        self,
        db: Session,
        clock: FixedClock,
        treasury: TreasuryService,
        admin: Actor,
        funded: dict,
    ) -> None:
        revenue, expenses = result(db, clock, admin)
        treasury.record(
            admin,
            MovementDraft(
                kind="transfer",
                amount=D("400"),
                charge=D("5"),
                from_account_id=funded["bank"].id,
                to_account_id=funded["second"].id,
                reason="Same-day transfer",
            ),
        )
        held = balances(treasury, admin)
        assert held["Main bank account"] == D("595")
        assert result(db, clock, admin) == (revenue, expenses + D("5"))

    def test_a_transfer_names_two_different_accounts(
        self, treasury: TreasuryService, admin: Actor, funded: dict
    ) -> None:
        with pytest.raises(ValidationError):
            treasury.record(
                admin,
                MovementDraft(
                    kind="transfer",
                    amount=D("1"),
                    from_account_id=funded["bank"].id,
                    to_account_id=funded["bank"].id,
                    reason="x",
                ),
            )
        with pytest.raises(ValidationError):
            treasury.record(
                admin,
                MovementDraft(
                    kind="transfer", amount=D("1"), from_account_id=funded["bank"].id, reason="x"
                ),
            )

    def test_a_bank_charge_leaves_the_account(
        self, treasury: TreasuryService, admin: Actor, funded: dict
    ) -> None:
        treasury.record(
            admin,
            MovementDraft(
                kind="bank_charge",
                amount=D("12.50"),
                from_account_id=funded["bank"].id,
                reason="Monthly maintenance fee",
            ),
        )
        assert balances(treasury, admin)["Main bank account"] == D("987.50")

    def test_the_cashbook_runs_from_the_balance_brought_forward(
        self, treasury: TreasuryService, admin: Actor, funded: dict
    ) -> None:
        treasury.record(
            admin,
            MovementDraft(
                kind="transfer",
                amount=D("250"),
                from_account_id=funded["bank"].id,
                to_account_id=funded["second"].id,
                reason="Top up",
            ),
        )
        account, ledger = treasury.cashbook(admin, funded["bank"].id)
        assert account.id == funded["bank"].id
        assert [item.running_balance for item in ledger.movements] == [D("1000"), D("750")]
        assert ledger.closing_balance == D("750")

    def test_movements_need_their_permission(
        self, treasury: TreasuryService, admin: Actor, funded: dict
    ) -> None:
        clerk = make_actor(admin.business_id, permissions={"financial_account.manage"})
        with pytest.raises(PermissionDeniedError):
            treasury.record(
                clerk,
                MovementDraft(
                    kind="bank_charge",
                    amount=D("1"),
                    from_account_id=funded["bank"].id,
                    reason="x",
                ),
            )
