"""Budgets, the cash forecast and alerts against a real database. PRD §29.6.

A budget never posts, and every figure it is compared with is read from the ledger as it
stands; a forecast is labelled forecast and never mixed with what was posted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.budgets import BudgetDraft, BudgetService, LineDraft
from nickland.application.services.expenses import ExpenseDraft, ExpenseService
from nickland.application.services.forecasts import ForecastService
from nickland.application.services.treasury import MovementDraft, TreasuryService
from nickland.domain.errors import ConflictError
from nickland.domain.finance import FinancialAccountKind
from nickland.domain.planning import Assumptions, Basis, BudgetState, PlannedFlow
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal


@pytest.fixture
def budgets(db: Session, clock: FixedClock) -> BudgetService:
    return BudgetService(db, clock=clock)


@pytest.fixture
def bank(db: Session, clock: FixedClock, admin: Actor):  # type: ignore[no-untyped-def]
    business = admin.business_id
    assert business is not None
    treasury = TreasuryService(db, clock=clock)
    account = treasury.default_for(business, FinancialAccountKind.BANK)
    treasury.record(
        admin,
        MovementDraft(
            kind="adjustment_in", amount=D("20000"), to_account_id=account.id, reason="Float"
        ),
    )
    return account


def approved(budgets: BudgetService, admin: Actor, **lines: str):  # type: ignore[no-untyped-def]
    budget = budgets.create(
        admin,
        BudgetDraft(
            name="Operating budget 2026",
            period_kind="monthly",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 12, 31),
            assumptions="Rent flat; utilities rise with the second shop.",
        ),
    )
    budgets.set_lines(
        admin,
        budget.id,
        [LineDraft(account_code=code, total=D(total)) for code, total in lines.items()],
    )
    budgets.submit(admin, budget.id)
    budgets.approve(admin, budget.id, note="Approved by the proprietor")
    return budget


def rent(net: str, **changes: object) -> ExpenseDraft:
    values: dict[str, object] = {
        "payee_name": "Osu Properties Ltd",
        "category_code": "60000",
        "description": "Shop rent",
        "net": D(net),
        "settlement": "on_credit",
        "incurred_on": date(2026, 11, 3),
    }
    values.update(changes)
    return ExpenseDraft(**values)  # type: ignore[arg-type]


class TestBudgets:
    def test_lines_spread_across_the_periods_and_sum_exactly(
        self, admin: Actor, budgets: BudgetService
    ) -> None:
        budget = approved(budgets, admin, **{"60000": "18000", "60100": "1000"})
        rent_line = budget.lines[0]
        assert len(rent_line.amounts) == 12
        assert sum((D(value) for value in rent_line.amounts.values()), D(0)) == D("18000")
        assert budgets.total(budget) == D("19000")

    def test_comparison_reads_actuals_from_the_ledger(
        self, db: Session, clock: FixedClock, admin: Actor, budgets: BudgetService
    ) -> None:
        budget = approved(budgets, admin, **{"60000": "18000"})
        budgets.set_control(admin, False)
        ExpenseService(db, clock=clock).record(admin, rent("1400"))
        comparison = budgets.comparison(admin, budget.id)
        line = comparison.lines[0]
        november = next(cell for cell in line.cells if cell.period_key == "2026-11")
        december = next(cell for cell in line.cells if cell.period_key == "2026-12")
        assert november.budget == D("1500.00")
        assert november.actual == D("1400.00")
        assert november.variance.absolute == D("-100.00")
        assert november.variance.favourable is True
        assert december.actual == D("0")
        assert comparison.bases["actual"] == Basis.ACTUAL.label
        # Forecast outturn: actual for the months gone (nothing posted before November),
        # the greater of actual and budget for this one, the budget for December.
        assert line.forecast == D("3000.00")

    def test_an_approved_budget_changes_only_by_a_new_version(
        self, admin: Actor, budgets: BudgetService
    ) -> None:
        budget = approved(budgets, admin, **{"60000": "18000"})
        with pytest.raises(ConflictError):
            budgets.set_lines(admin, budget.id, [LineDraft(account_code="60000", total=D("1"))])
        revision = budgets.revise(admin, budget.id)
        assert revision.version == 2 and revision.state == BudgetState.DRAFT.value
        budgets.set_lines(admin, revision.id, [LineDraft(account_code="60000", total=D("24000"))])
        budgets.submit(admin, revision.id)
        budgets.approve(admin, revision.id)
        assert budget.state == BudgetState.SUPERSEDED.value
        assert budgets.total(budget) == D("18000")
        actions = [row.action for row in budgets.decisions(admin, revision.id)]
        assert actions[:3] == ["created", "submitted", "approved"]
        assert "superseded" in actions

    def test_approval_is_by_someone_other_than_the_submitter(
        self, admin: Actor, budgets: BudgetService
    ) -> None:
        planner = make_actor(
            admin.business_id,  # type: ignore[arg-type]
            permissions={"budget.manage", "budget.approve"},
        )
        budget = budgets.create(
            planner,
            BudgetDraft(
                name="Branch budget",
                period_kind="quarterly",
                starts_on=date(2026, 1, 1),
                ends_on=date(2026, 12, 31),
            ),
        )
        budgets.set_lines(planner, budget.id, [LineDraft(account_code="60100", total=D("4000"))])
        budgets.submit(planner, budget.id)
        with pytest.raises(ConflictError):
            budgets.approve(planner, budget.id)

    def test_an_approved_transfer_moves_budget_between_lines(
        self, admin: Actor, budgets: BudgetService
    ) -> None:
        budget = approved(budgets, admin, **{"60000": "18000", "60100": "12000"})
        rent_line, power = budget.lines
        request = budgets.request(
            admin,
            budget.id,
            kind="transfer",
            line_id=power.id,
            from_line_id=rent_line.id,
            period_key="2026-11",
            amount=D("300"),
            reason="Generator fuel during the outages",
        )
        budgets.decide(admin, request.id, approve=True)
        cells = {line.line_id: line for line in budgets.comparison(admin, budget.id).lines}
        assert cells[power.id].cells[10].budget == D("1300.00")
        assert cells[power.id].cells[10].original == D("1000.00")
        assert cells[rent_line.id].cells[10].budget == D("1200.00")


class TestOverspend:
    def test_an_expense_beyond_the_budget_waits_for_approval(
        self, db: Session, clock: FixedClock, admin: Actor, budgets: BudgetService
    ) -> None:
        approved(budgets, admin, **{"60000": "18000"})
        expenses = ExpenseService(db, clock=clock)
        expense = expenses.record(admin, rent("1800"))
        assert expense.state == "awaiting_approval"
        assert "planned for Nov 2026" in (expense.hold_reason or "")
        [request] = budgets.requests(admin)
        assert request.kind == "overspend" and request.expense_fk == expense.id
        budgets.decide(admin, request.id, approve=True, note="Rent review came early")
        assert expense.state == "recognised"

    def test_spending_within_the_budget_is_recognised_at_once(
        self, db: Session, clock: FixedClock, admin: Actor, budgets: BudgetService
    ) -> None:
        approved(budgets, admin, **{"60000": "18000"})
        expense = ExpenseService(db, clock=clock).record(admin, rent("1500"))
        assert expense.state == "recognised"


class TestForecast:
    def test_the_forecast_starts_from_cash_and_lays_out_what_is_due(
        self, db: Session, clock: FixedClock, admin: Actor, bank
    ) -> None:
        ExpenseService(db, clock=clock).record(admin, rent("1500"))
        forecasts = ForecastService(db, clock=clock)
        made = forecasts.forecast(admin, horizon_days=30)
        assert made.basis is Basis.FORECAST
        assert made.opening_cash == D("20000.00")
        assert made.totals["expenses"] == D("1500.00")
        assert made.closing_cash == D("18500.00")
        assert len(made.rows) == 30

    def test_scenarios_move_dates_and_add_what_ifs(
        self, db: Session, clock: FixedClock, admin: Actor, bank
    ) -> None:
        forecasts = ForecastService(db, clock=clock)
        names = {row.kind for row in forecasts.scenarios(admin)}
        assert names == {"best", "base", "downside"}
        what_if = Assumptions(
            extra_flows=(PlannedFlow(date(2026, 11, 20), D("25000"), False, "Second delivery van"),)
        )
        made = forecasts.forecast(admin, horizon_days=60, assumptions=what_if)
        assert made.totals["planned_out"] == D("25000.00")
        assert made.lowest < D("0")
        assert made.warnings and "run out" in made.warnings[0]
        assert made.bucket_days == 7

    def test_a_low_cash_threshold_raises_an_alert(
        self, db: Session, clock: FixedClock, admin: Actor, bank
    ) -> None:
        forecasts = ForecastService(db, clock=clock)
        forecasts.set_threshold(admin, D("25000"))
        alerts = forecasts.alerts(admin)
        assert alerts[0].kind == "low_cash"
        assert alerts[0].severity == "warning"
