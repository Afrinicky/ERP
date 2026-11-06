"""Financial reporting: trial balance, general ledger and the statements.

Every figure on every report here is derived from posted journal lines. There is no
second source of truth and no stored total that could drift from the ledger, per
PRD §29.2. A report states the basis it was prepared on, because an accrual figure
and a cash figure answer different questions and must never be read as one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError
from nickland.domain.ledger import (
    AccountingBasis,
    AccountKind,
    CashFlowCategory,
    JournalState,
    Side,
    Statement,
    TrialBalance,
    TrialBalanceRow,
    presentation_side,
    signed_movement,
)
from nickland.domain.money import ZERO, money
from nickland.persistence.ledger_models import (
    Account,
    AccountGroup,
    AccountingPeriod,
    JournalEntry,
    JournalLine,
    SubledgerBalance,
)
from nickland.persistence.models import Business
from nickland.platform.clock import Clock, SystemClock, business_date

REPORTABLE_STATES = (JournalState.POSTED.value, JournalState.REVERSED.value)
"""States whose lines appear on a report.

A reversed entry remains on every report, beside the entry that reversed it. Removing
it would make the two cancel invisibly and leave the correction untraceable.
"""


@dataclass(frozen=True, slots=True)
class LedgerMovement:
    """One line as it appears on a general ledger page."""

    entry_id: uuid.UUID
    number: str | None
    posting_date: date
    narration: str
    line_narration: str | None
    source: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    party_id: uuid.UUID | None
    state: str


@dataclass(frozen=True, slots=True)
class AccountLedger:
    """An account's activity over a window, with its brought-forward balance."""

    account: Account
    starts_on: date | None
    ends_on: date
    basis: AccountingBasis
    opening_balance: Decimal
    movements: list[LedgerMovement]
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    presented_side: Side


@dataclass(frozen=True, slots=True)
class StatementLine:
    """One line of a financial statement."""

    code: str
    name: str
    amount: Decimal
    group_code: str | None = None
    group_name: str | None = None


@dataclass(frozen=True, slots=True)
class StatementSection:
    """A heading and the accounts beneath it."""

    code: str
    name: str
    lines: list[StatementLine]
    total: Decimal


@dataclass(frozen=True, slots=True)
class ProfitAndLoss:
    """Trading result for a window."""

    starts_on: date
    ends_on: date
    basis: AccountingBasis
    revenue: list[StatementSection]
    expenses: list[StatementSection]
    total_revenue: Decimal
    total_cost_of_sales: Decimal
    gross_profit: Decimal
    total_expenses: Decimal
    net_result: Decimal


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    """Position at a date."""

    as_at: date
    basis: AccountingBasis
    assets: list[StatementSection]
    liabilities: list[StatementSection]
    equity: list[StatementSection]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    total_liabilities_and_equity: Decimal
    """What the assets are financed by. Stated here rather than left to the reader to
    add up, because a statement that makes its reader do arithmetic to check that it
    balances is not finished."""
    retained_result: Decimal
    """The trading result not yet closed to reserves, carried onto the statement."""
    difference: Decimal
    balanced: bool


@dataclass(frozen=True, slots=True)
class CashFlowStatement:
    """Movement in cash over a window, classified by what it was for."""

    starts_on: date
    ends_on: date
    opening_cash: Decimal
    operating: Decimal
    investing: Decimal
    financing: Decimal
    unclassified: Decimal
    net_movement: Decimal
    closing_cash: Decimal
    reconciles: bool


@dataclass(frozen=True, slots=True)
class SubledgerVariance:
    """A control account whose subledger does not agree with it."""

    account_code: str
    account_name: str
    subledger: str
    control_balance: Decimal
    subledger_balance: Decimal
    difference: Decimal

    @property
    def agrees(self) -> bool:
        return self.difference == ZERO


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    """The figures the finance dashboard leads with."""

    as_at: date
    basis: AccountingBasis
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    cash_on_hand: Decimal
    receivables: Decimal
    payables: Decimal
    revenue_year_to_date: Decimal
    expenses_year_to_date: Decimal
    net_result_year_to_date: Decimal
    trial_balance_difference: Decimal
    accounting_equation_difference: Decimal
    """Assets less liabilities and equity. Zero, or something is wrong."""
    pending_approvals: int
    open_period_label: str | None
    financial_year: int | None


@dataclass(slots=True)
class _Bucket:
    """A group of accounts being accumulated into a statement section."""

    code: str
    name: str
    order: int
    lines: list[StatementLine] = field(default_factory=list)
    total: Decimal = ZERO


class LedgerReportService:
    """Reads the ledger. It writes nothing."""

    __slots__ = ("_clock", "_db")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()

    # ---------------------------------------------------------- trial balance

    def trial_balance(
        self,
        actor: AnyActor,
        *,
        as_at: date | None = None,
        start: date | None = None,
        basis: AccountingBasis | str = AccountingBasis.ACCRUAL,
    ) -> TrialBalance:
        """Every account carrying a balance, in code order.

        With ``start`` given the figures are the movement over the window; without it
        they are cumulative from the first posting, which is the usual reading.
        """
        actor.require("account.view")
        business_id = _business_of(actor)
        chosen = AccountingBasis(str(basis))
        ends_on = as_at or self._today(business_id)

        rows = self._aggregate(business_id, start, ends_on, chosen)
        balance = TrialBalance(as_at=ends_on, basis=chosen, starts_on=start, rows=[])
        for account, debit, credit in rows:
            if debit == ZERO and credit == ZERO:
                continue
            balance.rows.append(
                TrialBalanceRow(
                    code=account.code,
                    name=account.name,
                    kind=AccountKind(account.kind),
                    debit=debit,
                    credit=credit,
                )
            )
        return balance

    def account_balance(
        self,
        business_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        as_at: date | None = None,
        start: date | None = None,
        basis: AccountingBasis = AccountingBasis.ACCRUAL,
    ) -> Decimal:
        """One account's balance in its own direction."""
        account = self._db.get(Account, account_id)
        if account is None or account.business_id != business_id:
            raise NotFoundError("Account not found.")
        debit, credit = self._totals(
            business_id, start, as_at or self._today(business_id), basis, account_id=account_id
        )
        return signed_movement(AccountKind(account.kind), debit, credit)

    # --------------------------------------------------------- general ledger

    def account_ledger(
        self,
        actor: AnyActor,
        account_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        basis: AccountingBasis | str = AccountingBasis.ACCRUAL,
        limit: int = 500,
    ) -> AccountLedger:
        """An account's activity, opening balance first and a running balance beside."""
        actor.require("journal.view")
        business_id = _business_of(actor)
        chosen = AccountingBasis(str(basis))
        ends_on = end or self._today(business_id)

        account = self._db.get(Account, account_id)
        if account is None or account.business_id != business_id:
            raise NotFoundError("Account not found.")
        kind = AccountKind(account.kind)

        opening = ZERO
        if start is not None:
            debit, credit = self._totals(
                business_id, None, _day_before(start), chosen, account_id=account_id
            )
            opening = signed_movement(kind, debit, credit)

        statement: Select[Any] = (
            select(JournalLine, JournalEntry)
            .join(JournalEntry, JournalLine.entry_fk == JournalEntry.id)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.account_fk == account_id,
                JournalLine.state.in_(REPORTABLE_STATES),
                JournalLine.posting_date <= ends_on,
            )
            .order_by(JournalLine.posting_date, JournalEntry.created_at, JournalLine.display_order)
            .limit(max(min(limit, 2000), 1))
        )
        if start is not None:
            statement = statement.where(JournalLine.posting_date >= start)
        if chosen is AccountingBasis.CASH:
            statement = statement.where(JournalEntry.id.in_(self._cash_entries(business_id)))

        movements: list[LedgerMovement] = []
        running = opening
        total_debit = ZERO
        total_credit = ZERO
        for line, entry in self._db.execute(statement).unique().all():
            running = money(running + signed_movement(kind, line.base_debit, line.base_credit))
            total_debit = money(total_debit + line.base_debit)
            total_credit = money(total_credit + line.base_credit)
            movements.append(
                LedgerMovement(
                    entry_id=entry.id,
                    number=entry.number,
                    posting_date=line.posting_date,
                    narration=entry.narration,
                    line_narration=line.narration,
                    source=entry.source,
                    debit=line.base_debit,
                    credit=line.base_credit,
                    running_balance=running,
                    party_id=line.party_fk,
                    state=line.state,
                )
            )

        return AccountLedger(
            account=account,
            starts_on=start,
            ends_on=ends_on,
            basis=chosen,
            opening_balance=opening,
            movements=movements,
            total_debit=total_debit,
            total_credit=total_credit,
            closing_balance=running,
            presented_side=presentation_side(kind, running),
        )

    # ------------------------------------------------------------- statements

    def profit_and_loss(
        self,
        actor: AnyActor,
        *,
        start: date,
        end: date,
        basis: AccountingBasis | str = AccountingBasis.ACCRUAL,
    ) -> ProfitAndLoss:
        """Trading result for a window, grouped as the chart groups the accounts."""
        actor.require("report.view")
        business_id = _business_of(actor)
        chosen = AccountingBasis(str(basis))
        if end < start:
            raise ConflictError("A reporting window cannot end before it begins.")

        revenue: dict[str, _Bucket] = {}
        expenses: dict[str, _Bucket] = {}
        cost_of_sales = ZERO

        for account, debit, credit in self._aggregate(business_id, start, end, chosen):
            kind = AccountKind(account.kind)
            if kind.statement is not Statement.PROFIT_AND_LOSS:
                continue
            amount = signed_movement(kind, debit, credit)
            if amount == ZERO:
                continue
            target = revenue if kind is AccountKind.REVENUE else expenses
            bucket = _bucket_for(target, account)
            bucket.lines.append(
                StatementLine(
                    code=account.code,
                    name=account.name,
                    amount=amount,
                    group_code=bucket.code,
                    group_name=bucket.name,
                )
            )
            bucket.total = money(bucket.total + amount)
            if bucket.code == "COS":
                cost_of_sales = money(cost_of_sales + amount)

        revenue_sections = _sections(revenue)
        expense_sections = _sections(expenses)
        total_revenue = money(sum((section.total for section in revenue_sections), ZERO))
        total_expenses = money(sum((section.total for section in expense_sections), ZERO))

        return ProfitAndLoss(
            starts_on=start,
            ends_on=end,
            basis=chosen,
            revenue=revenue_sections,
            expenses=expense_sections,
            total_revenue=total_revenue,
            total_cost_of_sales=cost_of_sales,
            gross_profit=money(total_revenue - cost_of_sales),
            total_expenses=total_expenses,
            net_result=money(total_revenue - total_expenses),
        )

    def balance_sheet(
        self,
        actor: AnyActor,
        *,
        as_at: date | None = None,
        basis: AccountingBasis | str = AccountingBasis.ACCRUAL,
    ) -> BalanceSheet:
        """Position at a date.

        The trading result for the year to date is carried into equity, because it is
        earned but not yet closed to reserves. Without it the statement would not
        balance between year ends, and an unbalanced balance sheet is not one.
        """
        actor.require("report.view")
        business_id = _business_of(actor)
        chosen = AccountingBasis(str(basis))
        ends_on = as_at or self._today(business_id)

        assets: dict[str, _Bucket] = {}
        liabilities: dict[str, _Bucket] = {}
        equity: dict[str, _Bucket] = {}
        result = ZERO

        for account, debit, credit in self._aggregate(business_id, None, ends_on, chosen):
            kind = AccountKind(account.kind)
            amount = signed_movement(kind, debit, credit)
            if kind.statement is Statement.PROFIT_AND_LOSS:
                sign = 1 if kind is AccountKind.REVENUE else -1
                result = money(result + amount * sign)
                continue
            if amount == ZERO:
                continue
            target = {
                AccountKind.ASSET: assets,
                AccountKind.LIABILITY: liabilities,
                AccountKind.EQUITY: equity,
            }[kind]
            bucket = _bucket_for(target, account)
            bucket.lines.append(
                StatementLine(
                    code=account.code,
                    name=account.name,
                    amount=amount,
                    group_code=bucket.code,
                    group_name=bucket.name,
                )
            )
            bucket.total = money(bucket.total + amount)

        asset_sections = _sections(assets)
        liability_sections = _sections(liabilities)
        equity_sections = _sections(equity)

        # The result earned but not yet closed to reserves belongs on the face of the
        # statement, not only in the totals. Without it the equity section adds up to
        # one figure while the statement balances to another, and a reader would be
        # right to distrust both.
        if result != ZERO:
            equity_sections = [
                *equity_sections,
                StatementSection(
                    code="RESULT",
                    name="Result for the period, not yet closed",
                    lines=[
                        StatementLine(
                            code="",
                            name="Profit for the period"
                            if result > ZERO
                            else "Loss for the period",
                            amount=result,
                        )
                    ],
                    total=result,
                ),
            ]

        total_assets = money(sum((section.total for section in asset_sections), ZERO))
        total_liabilities = money(sum((section.total for section in liability_sections), ZERO))
        total_equity = money(sum((section.total for section in equity_sections), ZERO))
        difference = money(total_assets - total_liabilities - total_equity)

        return BalanceSheet(
            as_at=ends_on,
            basis=chosen,
            assets=asset_sections,
            liabilities=liability_sections,
            equity=equity_sections,
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            total_equity=total_equity,
            total_liabilities_and_equity=money(total_liabilities + total_equity),
            retained_result=result,
            difference=difference,
            balanced=difference == ZERO,
        )

    def cash_flow(
        self,
        actor: AnyActor,
        *,
        start: date,
        end: date,
    ) -> CashFlowStatement:
        """Movement in cash, classified by the account on the other side of the entry.

        Each cash line is attributed to the categories of the non-cash lines it sits
        with, apportioned by their amounts. A payment is operating, investing or
        financing according to what it bought, which is a property of that account and
        not of the bank.
        """
        actor.require("report.view")
        business_id = _business_of(actor)
        if end < start:
            raise ConflictError("A reporting window cannot end before it begins.")

        cash_accounts = {
            account.id
            for account in self._db.scalars(
                select(Account).where(
                    Account.business_id == business_id,
                    Account.cash_flow_category == CashFlowCategory.CASH.value,
                )
            ).unique()
        }
        if not cash_accounts:
            zero = CashFlowStatement(
                starts_on=start,
                ends_on=end,
                opening_cash=ZERO,
                operating=ZERO,
                investing=ZERO,
                financing=ZERO,
                unclassified=ZERO,
                net_movement=ZERO,
                closing_cash=ZERO,
                reconciles=True,
            )
            return zero

        opening = self._cash_position(business_id, cash_accounts, _day_before(start))
        closing = self._cash_position(business_id, cash_accounts, end)

        totals = {
            CashFlowCategory.OPERATING: ZERO,
            CashFlowCategory.INVESTING: ZERO,
            CashFlowCategory.FINANCING: ZERO,
            CashFlowCategory.NONE: ZERO,
        }

        rows = self._db.execute(
            select(JournalLine, Account.cash_flow_category, JournalLine.entry_fk)
            .join(Account, JournalLine.account_fk == Account.id)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state.in_(REPORTABLE_STATES),
                JournalLine.posting_date >= start,
                JournalLine.posting_date <= end,
            )
            .order_by(JournalLine.entry_fk)
        ).all()

        grouped: dict[uuid.UUID, list[tuple[JournalLine, str]]] = {}
        for line, category, entry_fk in rows:
            grouped.setdefault(entry_fk, []).append((line, category))

        for lines in grouped.values():
            cash_movement = money(
                sum(
                    (
                        line.base_debit - line.base_credit
                        for line, _ in lines
                        if line.account_fk in cash_accounts
                    ),
                    ZERO,
                )
            )
            if cash_movement == ZERO:
                continue
            others = [
                (line, category) for line, category in lines if line.account_fk not in cash_accounts
            ]
            weights = money(
                sum((abs(line.base_debit - line.base_credit) for line, _ in others), ZERO)
            )
            if not others or weights == ZERO:
                totals[CashFlowCategory.NONE] = money(totals[CashFlowCategory.NONE] + cash_movement)
                continue
            allocated = ZERO
            for index, (line, category) in enumerate(others):
                weight = abs(money(line.base_debit - line.base_credit))
                share = (
                    money(cash_movement - allocated)
                    if index == len(others) - 1
                    else money(cash_movement * weight / weights)
                )
                allocated = money(allocated + share)
                bucket = _cash_bucket(category)
                totals[bucket] = money(totals[bucket] + share)

        net = money(
            totals[CashFlowCategory.OPERATING]
            + totals[CashFlowCategory.INVESTING]
            + totals[CashFlowCategory.FINANCING]
            + totals[CashFlowCategory.NONE]
        )
        return CashFlowStatement(
            starts_on=start,
            ends_on=end,
            opening_cash=opening,
            operating=totals[CashFlowCategory.OPERATING],
            investing=totals[CashFlowCategory.INVESTING],
            financing=totals[CashFlowCategory.FINANCING],
            unclassified=totals[CashFlowCategory.NONE],
            net_movement=net,
            closing_cash=closing,
            reconciles=money(opening + net) == closing,
        )

    # -------------------------------------------------------- reconciliations

    def subledger_variances(self, actor: AnyActor) -> list[SubledgerVariance]:
        """Each control account beside the sum of its subledger.

        This is a drift check, and it is deliberately not dated. The control balance
        comes from the journal, which is the record; the subledger side comes from the
        materialised party balances, which are maintained incrementally as entries
        post and are therefore the side that can drift. Asking the question at a past
        date would compare the journal as it stood then against party balances as they
        stand now, which is not a comparison of like with like — it reports every
        entry posted since as a discrepancy. So both sides are read in full, and a
        difference means the two genuinely disagree.
        """
        actor.require("account.view")
        business_id = _business_of(actor)

        variances: list[SubledgerVariance] = []
        controls = self._db.scalars(
            select(Account).where(
                Account.business_id == business_id,
                Account.control_subledger.is_not(None),
            )
        ).unique()

        for account in controls:
            debit, credit = self._totals(
                business_id,
                None,
                date.max,
                AccountingBasis.ACCRUAL,
                account_id=account.id,
            )
            control = signed_movement(AccountKind(account.kind), debit, credit)

            sums = self._db.execute(
                select(
                    func.coalesce(func.sum(SubledgerBalance.debit), 0),
                    func.coalesce(func.sum(SubledgerBalance.credit), 0),
                ).where(
                    SubledgerBalance.business_id == business_id,
                    SubledgerBalance.account_fk == account.id,
                )
            ).one()
            sub = signed_movement(
                AccountKind(account.kind), money(Decimal(sums[0])), money(Decimal(sums[1]))
            )
            variances.append(
                SubledgerVariance(
                    account_code=account.code,
                    account_name=account.name,
                    subledger=account.control_subledger or "",
                    control_balance=control,
                    subledger_balance=sub,
                    difference=money(control - sub),
                )
            )
        return variances

    # ---------------------------------------------------------------- summary

    def summary(
        self,
        actor: AnyActor,
        *,
        as_at: date | None = None,
        basis: AccountingBasis | str = AccountingBasis.ACCRUAL,
    ) -> LedgerSummary:
        """The figures the finance overview leads with, in one pass."""
        actor.require("account.view")
        business_id = _business_of(actor)
        chosen = AccountingBasis(str(basis))
        ends_on = as_at or self._today(business_id)

        period = self._db.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.business_id == business_id,
                AccountingPeriod.starts_on <= ends_on,
                AccountingPeriod.ends_on >= ends_on,
            )
        )
        year_start = None
        if period is not None:
            year_start = self._db.scalar(
                select(func.min(AccountingPeriod.starts_on)).where(
                    AccountingPeriod.business_id == business_id,
                    AccountingPeriod.financial_year == period.financial_year,
                )
            )

        assets = liabilities = equity = cash = receivable = payable = ZERO
        revenue = expense = ZERO

        for account, debit, credit in self._aggregate(business_id, None, ends_on, chosen):
            kind = AccountKind(account.kind)
            amount = signed_movement(kind, debit, credit)
            if kind is AccountKind.ASSET:
                assets = money(assets + amount)
                if account.cash_flow_category == CashFlowCategory.CASH.value:
                    cash = money(cash + amount)
                if account.control_subledger == "customer":
                    receivable = money(receivable + amount)
            elif kind is AccountKind.LIABILITY:
                liabilities = money(liabilities + amount)
                if account.control_subledger == "supplier":
                    payable = money(payable + amount)
            elif kind is AccountKind.EQUITY:
                equity = money(equity + amount)

        if year_start is not None:
            for account, debit, credit in self._aggregate(business_id, year_start, ends_on, chosen):
                kind = AccountKind(account.kind)
                if kind is AccountKind.REVENUE:
                    revenue = money(revenue + signed_movement(kind, debit, credit))
                elif kind is AccountKind.EXPENSE:
                    expense = money(expense + signed_movement(kind, debit, credit))

        pending = self._db.scalar(
            select(func.count())
            .select_from(JournalEntry)
            .where(
                JournalEntry.business_id == business_id,
                JournalEntry.state == JournalState.PENDING_APPROVAL.value,
            )
        )
        trial = self.trial_balance(actor, as_at=ends_on, basis=chosen)

        return LedgerSummary(
            as_at=ends_on,
            basis=chosen,
            total_assets=assets,
            total_liabilities=liabilities,
            total_equity=money(equity + revenue - expense),
            cash_on_hand=cash,
            receivables=receivable,
            payables=payable,
            revenue_year_to_date=revenue,
            expenses_year_to_date=expense,
            net_result_year_to_date=money(revenue - expense),
            trial_balance_difference=trial.difference,
            accounting_equation_difference=money(
                assets - liabilities - (equity + revenue - expense)
            ),
            pending_approvals=int(pending or 0),
            open_period_label=period.label if period else None,
            financial_year=period.financial_year if period else None,
        )

    # -------------------------------------------------------------- internals

    def balances(
        self,
        business_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        basis: AccountingBasis = AccountingBasis.ACCRUAL,
    ) -> list[tuple[Account, Decimal]]:
        """Every account's balance in its own direction, in code order.

        The year-end close works from this: it needs each account's balance and the
        direction that would return it to zero, not a presentation of it.
        """
        return [
            (account, signed_movement(AccountKind(account.kind), debit, credit))
            for account, debit, credit in self._aggregate(
                business_id, start, end or self._today(business_id), basis
            )
        ]

    def _today(self, business_id: uuid.UUID) -> date:
        """Today in the business's own timezone.

        A report run late in the evening in Accra must not be dated tomorrow because
        the server keeps UTC.
        """
        business = self._db.get(Business, business_id)
        return business_date(self._clock.now(), business.timezone if business else "UTC")

    def _aggregate(
        self,
        business_id: uuid.UUID,
        start: date | None,
        end: date,
        basis: AccountingBasis,
    ) -> list[tuple[Account, Decimal, Decimal]]:
        """Debit and credit totals per account over a window, in code order."""
        statement: Select[Any] = (
            select(
                Account,
                func.coalesce(func.sum(JournalLine.base_debit), 0),
                func.coalesce(func.sum(JournalLine.base_credit), 0),
            )
            .join(JournalLine, JournalLine.account_fk == Account.id)
            .where(
                Account.business_id == business_id,
                JournalLine.state.in_(REPORTABLE_STATES),
                JournalLine.posting_date <= end,
            )
            .group_by(Account.id)
            .order_by(Account.code)
        )
        if start is not None:
            statement = statement.where(JournalLine.posting_date >= start)
        if basis is AccountingBasis.CASH:
            statement = statement.where(JournalLine.entry_fk.in_(self._cash_entries(business_id)))
        return [
            (account, money(Decimal(debit)), money(Decimal(credit)))
            for account, debit, credit in self._db.execute(statement).unique().all()
        ]

    def _totals(
        self,
        business_id: uuid.UUID,
        start: date | None,
        end: date,
        basis: AccountingBasis,
        *,
        account_id: uuid.UUID,
    ) -> tuple[Decimal, Decimal]:
        statement: Select[Any] = select(
            func.coalesce(func.sum(JournalLine.base_debit), 0),
            func.coalesce(func.sum(JournalLine.base_credit), 0),
        ).where(
            JournalLine.business_id == business_id,
            JournalLine.account_fk == account_id,
            JournalLine.state.in_(REPORTABLE_STATES),
            JournalLine.posting_date <= end,
        )
        if start is not None:
            statement = statement.where(JournalLine.posting_date >= start)
        if basis is AccountingBasis.CASH:
            statement = statement.where(JournalLine.entry_fk.in_(self._cash_entries(business_id)))
        debit, credit = self._db.execute(statement).one()
        return money(Decimal(debit)), money(Decimal(credit))

    def _cash_entries(self, business_id: uuid.UUID) -> Select[Any]:
        """Entries that moved cash.

        The cash basis recognises a revenue or a cost when the money moves, so only an
        entry with a cash or bank line counts. An invoice raised and unpaid appears on
        the accrual statements and not on these.
        """
        return (
            select(JournalLine.entry_fk)
            .join(Account, JournalLine.account_fk == Account.id)
            .where(
                JournalLine.business_id == business_id,
                Account.cash_flow_category == CashFlowCategory.CASH.value,
            )
        )

    def _cash_position(
        self, business_id: uuid.UUID, cash_accounts: set[uuid.UUID], on: date
    ) -> Decimal:
        debit, credit = self._db.execute(
            select(
                func.coalesce(func.sum(JournalLine.base_debit), 0),
                func.coalesce(func.sum(JournalLine.base_credit), 0),
            ).where(
                JournalLine.business_id == business_id,
                JournalLine.account_fk.in_(cash_accounts),
                JournalLine.state.in_(REPORTABLE_STATES),
                JournalLine.posting_date <= on,
            )
        ).one()
        return money(Decimal(debit) - Decimal(credit))


def _bucket_for(buckets: dict[str, _Bucket], account: Account) -> _Bucket:
    """The section an account belongs to, created on first use."""
    group: AccountGroup | None = account.group
    code = group.code if group else "OTH"
    name = group.name if group else "Other"
    order = group.display_order if group else 999
    if code not in buckets:
        buckets[code] = _Bucket(code=code, name=name, order=order)
    return buckets[code]


def _sections(buckets: dict[str, _Bucket]) -> list[StatementSection]:
    return [
        StatementSection(
            code=bucket.code,
            name=bucket.name,
            lines=bucket.lines,
            total=bucket.total,
        )
        for bucket in sorted(buckets.values(), key=lambda item: (item.order, item.code))
    ]


def _cash_bucket(category: str) -> CashFlowCategory:
    try:
        resolved = CashFlowCategory(category)
    except ValueError:
        return CashFlowCategory.NONE
    if resolved in (CashFlowCategory.CASH, CashFlowCategory.NONE):
        return CashFlowCategory.NONE
    return resolved


def _day_before(on: date) -> date:
    from datetime import timedelta

    return on - timedelta(days=1)


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
