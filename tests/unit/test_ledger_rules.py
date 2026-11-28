"""The accounting rules in isolation. PRD 29.2."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.ledger import (
    AccountKind,
    EntryLine,
    PeriodState,
    PeriodWindow,
    Side,
    Statement,
    TrialBalance,
    TrialBalanceRow,
    assert_balanced,
    assert_code_matches_kind,
    assert_posting_permitted,
    check_balance,
    kind_for_code,
    months_in_year,
    presentation_side,
    signed_movement,
    validate_account_code,
)


class TestAccountClassification:
    @pytest.mark.parametrize(
        ("kind", "side"),
        [
            (AccountKind.ASSET, Side.DEBIT),
            (AccountKind.EXPENSE, Side.DEBIT),
            (AccountKind.LIABILITY, Side.CREDIT),
            (AccountKind.EQUITY, Side.CREDIT),
            (AccountKind.REVENUE, Side.CREDIT),
        ],
    )
    def test_each_class_has_its_normal_balance(self, kind: AccountKind, side: Side) -> None:
        assert kind.normal_balance is side

    def test_only_trading_accounts_close_at_the_year_end(self) -> None:
        closing = {kind for kind in AccountKind if kind.closes_at_year_end}
        assert closing == {AccountKind.REVENUE, AccountKind.EXPENSE}

    def test_the_statement_follows_the_class(self) -> None:
        assert AccountKind.ASSET.statement is Statement.BALANCE_SHEET
        assert AccountKind.REVENUE.statement is Statement.PROFIT_AND_LOSS

    @pytest.mark.parametrize(
        ("code", "kind"),
        [
            ("10100", AccountKind.ASSET),
            ("20000", AccountKind.LIABILITY),
            ("30100", AccountKind.EQUITY),
            ("40100", AccountKind.REVENUE),
            ("50000", AccountKind.EXPENSE),
            ("80000", AccountKind.EXPENSE),
        ],
    )
    def test_the_leading_digit_carries_the_class(self, code: str, kind: AccountKind) -> None:
        assert kind_for_code(code) is kind

    def test_a_code_contradicting_its_class_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="asset range"):
            assert_code_matches_kind("10100", AccountKind.LIABILITY)

    @pytest.mark.parametrize("code", ["", "123", "abcd", "1234567890", "10 100", "-1000"])
    def test_a_code_that_is_not_four_to_eight_digits_is_refused(self, code: str) -> None:
        with pytest.raises(ValidationError):
            validate_account_code(code)


class TestBalance:
    def test_an_entry_balances_when_the_two_sides_agree(self) -> None:
        outcome = assert_balanced(
            [
                EntryLine("10100", Side.DEBIT, Decimal("1150.00")),
                EntryLine("40100", Side.CREDIT, Decimal("1000.00")),
                EntryLine("21000", Side.CREDIT, Decimal("150.00")),
            ]
        )
        assert outcome.balanced
        assert outcome.difference == Decimal("0")

    def test_the_difference_names_the_heavier_side(self) -> None:
        outcome = check_balance(
            [
                EntryLine("10100", Side.DEBIT, Decimal("100.00")),
                EntryLine("40100", Side.CREDIT, Decimal("90.00")),
            ]
        )
        assert not outcome.balanced
        assert "Debits exceed" in outcome.detail
        assert "10" in outcome.detail

    def test_an_entry_of_one_line_is_not_an_entry(self) -> None:
        outcome = check_balance([EntryLine("10100", Side.DEBIT, Decimal("100.00"))])
        assert not outcome.balanced
        assert "at least two lines" in outcome.detail

    def test_an_entry_of_no_lines_is_not_an_entry(self) -> None:
        assert not check_balance([]).balanced

    def test_a_line_of_zero_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="above zero"):
            EntryLine("10100", Side.DEBIT, Decimal("0"))

    def test_a_negative_line_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="other side"):
            EntryLine("10100", Side.DEBIT, Decimal("-5.00"))

    def test_a_reversed_line_mirrors_the_original(self) -> None:
        line = EntryLine("10100", Side.DEBIT, Decimal("40.00"), narration="Receipt")
        mirrored = line.reversed()
        assert mirrored.side is Side.CREDIT
        assert mirrored.amount == line.amount
        assert mirrored.narration == line.narration
        assert mirrored.reversed() == line


class TestMovement:
    def test_an_asset_rises_on_a_debit(self) -> None:
        assert signed_movement(AccountKind.ASSET, Decimal("100"), Decimal("30")) == Decimal("70")

    def test_a_liability_rises_on_a_credit(self) -> None:
        movement = signed_movement(AccountKind.LIABILITY, Decimal("30"), Decimal("100"))
        assert movement == Decimal("70")

    def test_a_balance_in_its_own_direction_presents_on_its_normal_side(self) -> None:
        assert presentation_side(AccountKind.ASSET, Decimal("70")) is Side.DEBIT
        assert presentation_side(AccountKind.REVENUE, Decimal("70")) is Side.CREDIT

    def test_a_balance_against_its_direction_presents_on_the_other_side(self) -> None:
        assert presentation_side(AccountKind.ASSET, Decimal("-70")) is Side.CREDIT


class TestPeriods:
    def test_an_open_period_accepts_a_posting(self) -> None:
        window = PeriodWindow(date(2026, 11, 1), date(2026, 11, 30))
        assert_posting_permitted(PeriodState.OPEN, window, date(2026, 11, 6))

    def test_a_reopened_period_accepts_a_posting(self) -> None:
        window = PeriodWindow(date(2026, 11, 1), date(2026, 11, 30))
        assert_posting_permitted(PeriodState.REOPENED, window, date(2026, 11, 6))

    def test_a_closed_period_refuses_one(self) -> None:
        window = PeriodWindow(date(2026, 11, 1), date(2026, 11, 30))
        with pytest.raises(ConflictError, match="closed"):
            assert_posting_permitted(PeriodState.CLOSED, window, date(2026, 11, 6))

    def test_a_period_cannot_end_before_it_begins(self) -> None:
        with pytest.raises(ValidationError):
            PeriodWindow(date(2026, 11, 30), date(2026, 11, 1))

    def test_a_whole_month_is_labelled_by_its_month(self) -> None:
        assert PeriodWindow(date(2026, 11, 1), date(2026, 11, 30)).label == "November 2026"

    def test_a_calendar_year_begins_in_january(self) -> None:
        months = months_in_year(1)
        assert months[0] == (1, 0)
        assert months[-1] == (12, 0)

    def test_a_year_beginning_in_july_runs_into_the_next_calendar_year(self) -> None:
        months = months_in_year(7)
        assert months[0] == (7, 0)
        assert months[5] == (12, 0)
        assert months[6] == (1, 1)
        assert months[-1] == (6, 1)

    @pytest.mark.parametrize("start", [0, 13, -1])
    def test_a_year_cannot_begin_outside_the_twelve_months(self, start: int) -> None:
        with pytest.raises(ValidationError):
            months_in_year(start)


class TestTrialBalance:
    def test_it_balances_when_the_columns_agree(self) -> None:
        balance = TrialBalance(as_at=date(2026, 11, 6))
        balance.rows.append(
            TrialBalanceRow("10100", "Cash", AccountKind.ASSET, Decimal("100"), Decimal("0"))
        )
        balance.rows.append(
            TrialBalanceRow("40100", "Sales", AccountKind.REVENUE, Decimal("0"), Decimal("100"))
        )
        assert balance.balanced
        assert balance.difference == Decimal("0")

    def test_a_row_presents_only_its_net_side(self) -> None:
        row = TrialBalanceRow("10100", "Cash", AccountKind.ASSET, Decimal("500"), Decimal("200"))
        assert row.presented_debit == Decimal("300")
        assert row.presented_credit == Decimal("0")

    def test_an_account_run_against_its_direction_presents_on_the_other_side(self) -> None:
        row = TrialBalanceRow("10100", "Cash", AccountKind.ASSET, Decimal("200"), Decimal("500"))
        assert row.presented_debit == Decimal("0")
        assert row.presented_credit == Decimal("300")
        assert row.balance == Decimal("-300")
