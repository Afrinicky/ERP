"""The command centre against a real database. PRD §20.1, AC-19.

Exit criterion 4 held here: every dashboard measure drills down to its supporting
transactions and reconciles to the ledger.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.command_centre import CommandCentreService
from nickland.application.services.expenses import ExpenseDraft, ExpenseService
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.treasury import (
    FinancialAccountDraft,
    MovementDraft,
    TreasuryService,
)
from nickland.domain.errors import PermissionDeniedError, ValidationError
from nickland.domain.finance import MEASURES, FinancialAccountKind
from nickland.persistence.ledger_models import Account
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal
START = date(2026, 1, 1)
END = date(2026, 12, 31)


@pytest.fixture
def centre(db: Session, clock: FixedClock) -> CommandCentreService:
    return CommandCentreService(db, clock=clock)


@pytest.fixture
def activity(db: Session, clock: FixedClock, admin: Actor) -> None:
    """Interest into the main bank, a second bank, a transfer, and two expenses."""
    treasury = TreasuryService(db, clock=clock)
    assert admin.business_id is not None
    bank = treasury.default_for(admin.business_id, FinancialAccountKind.BANK)
    second = treasury.create_account(admin, FinancialAccountDraft(kind="bank", name="Ecobank"))
    treasury.record(
        admin,
        MovementDraft(kind="interest", amount=D("5000"), to_account_id=bank.id, reason="Interest"),
    )
    treasury.record(
        admin,
        MovementDraft(
            kind="transfer",
            amount=D("1200"),
            from_account_id=bank.id,
            to_account_id=second.id,
            reason="Fund Ecobank",
        ),
    )
    expenses = ExpenseService(db, clock=clock)
    expenses.record(
        admin,
        ExpenseDraft(
            payee_name="ECG",
            category_code="60100",
            description="Electricity, October",
            net=D("640"),
            settlement="paid",
            financial_account_id=bank.id,
        ),
    )
    expenses.record(
        admin,
        ExpenseDraft(
            payee_name="Osu Properties Ltd",
            category_code="60000",
            description="Shop rent",
            net=D("1500"),
            settlement="on_credit",
        ),
    )


def balance(db: Session, clock: FixedClock, admin: Actor, code: str) -> Decimal:
    account = db.scalar(
        select(Account).where(Account.business_id == admin.business_id, Account.code == code)
    )
    assert account is not None
    return LedgerReportService(db, clock=clock).account_balance(
        admin.business_id,  # type: ignore[arg-type]
        account.id,
        as_at=END,
    )


class TestMeasures:
    def test_every_measure_drills_to_lines_that_total_it(
        self, centre: CommandCentreService, admin: Actor, activity: None
    ) -> None:
        for definition in MEASURES:
            drill = centre.drill(admin, definition.code, start=START, end=END)
            assert drill.total == drill.measure.value, definition.code
            assert sum((line.contribution for line in drill.lines), D("0")) == drill.total

    def test_the_measures_agree_with_the_ledger_reports(
        self,
        db: Session,
        clock: FixedClock,
        centre: CommandCentreService,
        admin: Actor,
        activity: None,
    ) -> None:
        values = {
            item.definition.code: item.value
            for item in centre.measures(admin, start=START, end=END)
        }
        pnl = LedgerReportService(db, clock=clock).profit_and_loss(admin, start=START, end=END)
        assert values["net_profit"] == pnl.net_result
        assert values["operating_expenses"] == D("2140")
        assert values["cash_and_bank"] == D("4360")  # 5,000 less 640, wherever it now sits
        assert values["cash_and_bank"] == sum(
            (
                balance(db, clock, admin, code)
                for code in ("10100", "10200", "10201", "10300", "10400")
            ),
            D("0"),
        )
        assert values["payables"] == D("0")  # other payables are not trade payables
        assert values["recognised_revenue"] == D("5000")
        assert values["gross_sales"] == D("0")

    def test_a_drill_names_the_accounts_and_entries(
        self, centre: CommandCentreService, admin: Actor, activity: None
    ) -> None:
        drill = centre.drill(admin, "operating_expenses", start=START, end=END)
        assert sorted(line.account_code for line in drill.lines) == ["60000", "60100"]
        assert all(line.number for line in drill.lines)

    def test_the_period_bounds_a_flow_but_not_a_position(
        self, centre: CommandCentreService, admin: Actor, activity: None
    ) -> None:
        values = {
            item.definition.code: item.value
            for item in centre.measures(admin, start=date(2026, 12, 1), end=END)
        }
        assert values["operating_expenses"] == D("0")
        assert values["cash_and_bank"] == D("4360")

    def test_measures_need_report_access(self, centre: CommandCentreService, admin: Actor) -> None:
        clerk = make_actor(admin.business_id, permissions={"pos.operate"})
        with pytest.raises(PermissionDeniedError):
            centre.measures(clerk, start=START, end=END)


class TestLayouts:
    def test_a_person_arranges_resizes_and_saves_their_own(
        self, centre: CommandCentreService, admin: Actor
    ) -> None:
        assert centre.layout(admin).source == "preset"
        saved = centre.save_layout(
            admin,
            [
                {"measure": "cash_and_bank", "size": "large"},
                {"measure": "net_profit", "size": "small"},
            ],
            preset="finance_manager",
        )
        assert saved.source == "personal"
        again = centre.layout(admin)
        assert [item["measure"] for item in again.widgets] == ["cash_and_bank", "net_profit"]
        assert again.widgets[1]["size"] == "small"
        assert centre.reset_layout(admin).source == "preset"

    def test_a_role_layout_needs_its_permission_and_is_where_holders_start(
        self, centre: CommandCentreService, admin: Actor
    ) -> None:
        viewer = make_actor(admin.business_id, permissions={"report.view"})
        with pytest.raises(PermissionDeniedError):
            centre.save_layout(
                viewer, [{"measure": "net_sales", "size": "medium"}], role_code="sales_person"
            )
        centre.save_layout(
            admin, [{"measure": "net_sales", "size": "medium"}], role_code="sales_person"
        )
        seller = make_actor(admin.business_id, permissions={"report.view"})
        object.__setattr__(seller, "role_codes", frozenset({"sales_person"}))
        layout = centre.layout(seller)
        assert layout.source == "role" and layout.widgets == [
            {"measure": "net_sales", "size": "medium"}
        ]

    def test_a_layout_is_validated(self, centre: CommandCentreService, admin: Actor) -> None:
        with pytest.raises(ValidationError, match="twice"):
            centre.save_layout(
                admin,
                [
                    {"measure": "net_sales", "size": "large"},
                    {"measure": "net_sales", "size": "small"},
                ],
            )
        with pytest.raises(ValidationError, match="not a measure"):
            centre.save_layout(admin, [{"measure": "vanity", "size": "large"}])
