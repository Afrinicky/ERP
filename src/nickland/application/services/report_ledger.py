"""The ledger as report tables: profit and loss, position, cash flow, trial balance, the
expense analysis and the budget. Shared by the financial summary and the management pack,
so the two can never disagree.

Each table is drawn from ``LedgerReportService`` and nothing else, so every figure on it is
a figure of the general ledger.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.services.budgets import BudgetService
from nickland.application.services.ledger_reports import (
    BalanceSheet,
    LedgerReportService,
    ProfitAndLoss,
    StatementSection,
)
from nickland.domain.ledger import AccountingBasis, AccountKind, CashFlowCategory
from nickland.domain.money import ZERO
from nickland.domain.reporting import (
    Check,
    Column,
    ColumnKind,
    Comparison,
    Figure,
    Row,
    Section,
    Table,
    cents,
    compare_figures,
    compare_table,
    comparison_window,
    share,
)
from nickland.persistence.planning_models import Budget
from nickland.platform.clock import Clock, SystemClock

MONEY_COLUMNS = (
    Column("label", "Account"),
    Column("amount", "Amount", ColumnKind.MONEY, compare=True),
)


class LedgerSections:
    __slots__ = ("_budgets", "_clock", "_db", "_ledger")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._ledger = LedgerReportService(session, clock=self._clock)
        self._budgets = BudgetService(session, clock=self._clock)

    # -- the financial summary report -----------------------------------------------------

    def finance(self, scope: object) -> list[Section]:
        """The financial summary report of PRD §8. ``scope`` is a ``reports.Scope``."""
        from nickland.application.services.reports import Scope

        assert isinstance(scope, Scope)
        actor = scope.actor
        basis = scope.params.basis
        statement = self._ledger.profit_and_loss(
            actor, start=scope.start, end=scope.end, basis=basis
        )
        pnl = self.profit_and_loss_table(statement)
        cash = self.cash_flow_table(actor, scope.start, scope.end)
        expenses = self.expense_table(statement)
        figures = self.headline(actor, statement, scope.start, scope.end, basis)
        if scope.params.comparison is Comparison.BUDGET:
            budget = self.budget_table(scope.business_id, scope.start, scope.end, statement)
            pnl = compare_table(pnl, budget, Comparison.BUDGET)
            budgeted = {row.key: row for row in budget.rows}
            for figure in figures:
                source = {
                    "revenue": "total:revenue",
                    "net": "total:net",
                    "expenses": "total:expenses",
                }.get(figure.key)
                if source and source in budgeted:
                    figure.prior = budgeted[source].cells["amount"]
            compare_figures(
                figures,
                [Figure(f.key, f.label, f.prior, f.kind) for f in figures if f.prior is not None],
            )
        return [
            Section("summary", "Financial Summary", figures=figures),
            Section("profit_and_loss", "Profit and Loss", tables=[pnl]),
            Section("cash_flow", "Cash Flow", tables=[cash]),
            Section("expenses", "Operating Expenses", tables=[expenses]),
        ]

    # -- headline -------------------------------------------------------------------------

    def headline(
        self,
        actor: object,
        statement: ProfitAndLoss,
        start: date,
        end: date,
        basis: AccountingBasis,
    ) -> list[Figure]:
        flow = self._ledger.cash_flow(actor, start=start, end=end)  # type: ignore[arg-type]
        position = self.control_balances(actor, end, basis)
        return [
            Figure("revenue", "Net revenue", statement.total_revenue),
            Figure("gross", "Gross profit", statement.gross_profit),
            Figure(
                "expenses",
                "Operating expenses",
                cents(statement.total_expenses - statement.total_cost_of_sales),
            ),
            Figure("net", "Net profit", statement.net_result),
            Figure("cash_movement", "Cash movement", flow.net_movement),
            Figure("closing_cash", "Closing cash and bank", flow.closing_cash),
            Figure("receivables", "Owed by customers", position["receivables"]),
            Figure("payables", "Owed to suppliers", position["payables"]),
        ]

    def control_balances(
        self, actor: object, as_at: date, basis: AccountingBasis
    ) -> dict[str, Decimal]:
        business_id = actor.business_id  # type: ignore[attr-defined]
        found = {"receivables": ZERO, "payables": ZERO, "cash": ZERO}
        for account, balance in self._ledger.balances(business_id, end=as_at, basis=basis):
            kind = AccountKind(account.kind)
            if account.control_subledger == "customer" and kind is AccountKind.ASSET:
                found["receivables"] = cents(found["receivables"] + balance)
            elif account.control_subledger == "supplier" and kind is AccountKind.LIABILITY:
                found["payables"] = cents(found["payables"] + balance)
            if (
                AccountKind(account.kind) is AccountKind.ASSET
                and account.cash_flow_category == CashFlowCategory.CASH.value
            ):
                found["cash"] = cents(found["cash"] + balance)
        return found

    # -- profit and loss ------------------------------------------------------------------

    def profit_and_loss_table(self, statement: ProfitAndLoss) -> Table:
        rows: list[Row] = [Row("head:revenue", {"label": "Revenue"}, emphasis="heading")]
        rows.extend(self._section_rows(statement.revenue))
        rows.append(
            Row(
                "total:revenue",
                {"label": "Total revenue", "amount": statement.total_revenue},
                emphasis="subtotal",
            )
        )
        cost = [section for section in statement.expenses if section.code == "COS"]
        other = [section for section in statement.expenses if section.code != "COS"]
        if cost:
            rows.extend(self._section_rows(cost, always=True))
        rows.append(
            Row(
                "total:gross",
                {"label": "Gross profit", "amount": statement.gross_profit},
                emphasis="subtotal",
            )
        )
        rows.append(Row("head:expenses", {"label": "Operating expenses"}, emphasis="heading"))
        rows.extend(self._section_rows(other))
        rows.append(
            Row(
                "total:expenses",
                {
                    "label": "Total operating expenses",
                    "amount": cents(statement.total_expenses - statement.total_cost_of_sales),
                },
                emphasis="subtotal",
            )
        )
        return Table(
            "profit_and_loss",
            "Profit and loss",
            list(MONEY_COLUMNS),
            rows,
            Row(
                "total:net",
                {
                    "label": "Net profit" if statement.net_result >= ZERO else "Net loss",
                    "amount": statement.net_result,
                },
                emphasis="total",
            ),
        )

    @staticmethod
    def _section_rows(sections: list[StatementSection], *, always: bool = False) -> list[Row]:
        """Each account, and a subtotal per group where there is more than one group."""
        rows: list[Row] = []
        subtotal = always or len(sections) > 1
        for section in sections:
            for line in section.lines:
                rows.append(
                    Row(
                        f"acc:{line.code}",
                        {"label": f"{line.code} {line.name}", "amount": line.amount},
                        drill={"view": "account", "code": line.code},
                        level=1,
                    )
                )
            if not subtotal:
                continue
            rows.append(
                Row(
                    f"grp:{section.code}",
                    {"label": section.name, "amount": section.total},
                    emphasis="subtotal",
                    level=1,
                )
            )
        return rows

    # -- position -------------------------------------------------------------------------

    def balance_sheet_table(self, sheet: BalanceSheet) -> Table:
        rows: list[Row] = []
        for heading, sections, total_key, total_label, total in (
            ("Assets", sheet.assets, "assets", "Total assets", sheet.total_assets),
            (
                "Liabilities",
                sheet.liabilities,
                "liabilities",
                "Total liabilities",
                sheet.total_liabilities,
            ),
            ("Equity", sheet.equity, "equity", "Total equity", sheet.total_equity),
        ):
            rows.append(Row(f"head:{total_key}", {"label": heading}, emphasis="heading"))
            for section in sections:
                for line in section.lines:
                    key = f"acc:{line.code}" if line.code else f"line:{section.code}"
                    rows.append(
                        Row(
                            key,
                            {"label": f"{line.code} {line.name}".strip(), "amount": line.amount},
                            drill={"view": "account", "code": line.code} if line.code else None,
                            level=1,
                        )
                    )
            rows.append(
                Row(
                    f"total:{total_key}",
                    {"label": total_label, "amount": total},
                    emphasis="subtotal",
                )
            )
        return Table(
            "balance_sheet",
            f"Position at {sheet.as_at:%d %B %Y}".replace(" 0", " "),
            [
                Column("label", "Account"),
                Column("amount", "Amount", ColumnKind.MONEY, compare=True),
            ],
            rows,
            Row(
                "total:financing",
                {
                    "label": "Total liabilities and equity",
                    "amount": sheet.total_liabilities_and_equity,
                },
                emphasis="total",
            ),
        )

    # -- cash flow ------------------------------------------------------------------------

    def cash_flow_table(self, actor: object, start: date, end: date) -> Table:
        flow = self._ledger.cash_flow(actor, start=start, end=end)  # type: ignore[arg-type]
        rows = [
            Row(
                "opening",
                {"label": "Cash and bank at the start", "amount": flow.opening_cash},
                emphasis="subtotal",
            ),
            Row(
                "operating",
                {"label": "Operating activities", "amount": flow.operating},
                level=1,
                drill={"view": "cash", "category": "operating"},
            ),
            Row(
                "investing",
                {"label": "Investing activities", "amount": flow.investing},
                level=1,
                drill={"view": "cash", "category": "investing"},
            ),
            Row(
                "financing",
                {"label": "Financing activities", "amount": flow.financing},
                level=1,
                drill={"view": "cash", "category": "financing"},
            ),
        ]
        if flow.unclassified != ZERO:
            rows.append(
                Row(
                    "unclassified",
                    {"label": "Not classified", "amount": flow.unclassified},
                    level=1,
                )
            )
        rows.append(
            Row(
                "movement",
                {"label": "Net movement in cash", "amount": flow.net_movement},
                emphasis="subtotal",
            )
        )
        return Table(
            "cash_flow",
            "Cash flow",
            [
                Column("label", "Activity"),
                Column("amount", "Amount", ColumnKind.MONEY, compare=True),
            ],
            rows,
            Row(
                "closing",
                {"label": "Cash and bank at the end", "amount": flow.closing_cash},
                emphasis="total",
            ),
            note="Each payment is classified by the account it was paid to or from.",
        )

    # -- trial balance --------------------------------------------------------------------

    def trial_balance_table(self, actor: object, as_at: date, basis: AccountingBasis) -> Table:
        trial = self._ledger.trial_balance(actor, as_at=as_at, basis=basis)  # type: ignore[arg-type]
        rows = [
            Row(
                f"acc:{row.code}",
                {
                    "code": row.code,
                    "name": row.name,
                    "debit": row.presented_debit,
                    "credit": row.presented_credit,
                },
                drill={"view": "account", "code": row.code},
            )
            for row in trial.rows
            if row.presented_debit != ZERO or row.presented_credit != ZERO
        ]
        return Table(
            "trial_balance",
            f"Trial balance at {as_at:%d %B %Y}".replace(" 0", " "),
            [
                Column("code", "Code"),
                Column("name", "Account"),
                Column("debit", "Debit", ColumnKind.MONEY),
                Column("credit", "Credit", ColumnKind.MONEY),
            ],
            rows,
            Row(
                "total",
                {"code": "Total", "debit": trial.total_debit, "credit": trial.total_credit},
                emphasis="total",
            ),
        )

    # -- expenses -------------------------------------------------------------------------

    def expense_table(self, statement: ProfitAndLoss) -> Table:
        lines = [
            line
            for section in statement.expenses
            if section.code != "COS"
            for line in section.lines
        ]
        total = cents(sum((line.amount for line in lines), ZERO))
        rows = [
            Row(
                f"acc:{line.code}",
                {
                    "label": f"{line.code} {line.name}",
                    "category": line.group_name,
                    "amount": line.amount,
                    "share": share(line.amount, total),
                },
                drill={"view": "account", "code": line.code},
            )
            for line in sorted(lines, key=lambda item: -item.amount)
        ]
        return Table(
            "expenses",
            "Operating expenses, largest first",
            [
                Column("label", "Account"),
                Column("category", "Category"),
                Column("amount", "Amount", ColumnKind.MONEY, compare=True),
                Column("share", "Share", ColumnKind.PERCENT),
            ],
            rows,
            Row(
                "total",
                {"label": "Total", "amount": total, "share": Decimal("100.0") if total else None},
                emphasis="total",
            ),
        )

    def unusual(
        self,
        current: Table,
        prior: Table,
        *,
        ratio: Decimal = Decimal(25),
        floor: Decimal = Decimal(500),
    ) -> list[str]:
        """Expense lines that rose by more than a quarter and by a material amount."""
        before = {row.key: row.cells.get("amount") for row in prior.rows}
        notes = []
        for row in current.rows:
            now = row.cells.get("amount")
            then = before.get(row.key)
            if not isinstance(now, Decimal):
                continue
            then_value = then if isinstance(then, Decimal) else ZERO
            rise = now - then_value
            if rise >= floor and (then_value == ZERO or rise * 100 / then_value >= ratio):
                percent = (
                    f"{(rise * 100 / then_value).quantize(Decimal('1'))}%"
                    if then_value
                    else "from nothing"
                )
                notes.append(f"{row.cells['label']} rose by {rise:,.2f} ({percent}).")
        return notes

    # -- budget ---------------------------------------------------------------------------

    def approved_budgets(self, business_id: uuid.UUID, start: date, end: date) -> list[Budget]:
        return list(
            self._db.scalars(
                select(Budget).where(
                    Budget.business_id == business_id,
                    Budget.state == "approved",
                    Budget.starts_on <= end,
                    Budget.ends_on >= start,
                )
            ).all()
        )

    def budget_table(
        self, business_id: uuid.UUID, start: date, end: date, statement: ProfitAndLoss
    ) -> Table:
        """The profit and loss table as budgeted, keyed like the actual one, so the two can
        be set side by side."""
        accounts: dict[str, Decimal] = {}
        groups: dict[str, Decimal] = {}
        revenue = expenses = cost_of_sales = ZERO
        for budget in self.approved_budgets(business_id, start, end):
            for line in budget.lines:
                planned, _ = self._budgets.window_figures(budget, line, start, end)
                if planned == ZERO:
                    continue
                if line.account_code:
                    account = self._ledger_account(business_id, line.account_code)
                    if account is None:
                        continue
                    accounts[line.account_code] = cents(
                        accounts.get(line.account_code, ZERO) + planned
                    )
                    group_code = account.group.code if account.group else "OTH"
                    kind = account.kind
                else:
                    group_code = line.group_code or "OTH"
                    group = self._budgets._group(business_id, group_code)
                    kind = group.kind
                groups[group_code] = cents(groups.get(group_code, ZERO) + planned)
                if kind == "revenue":
                    revenue = cents(revenue + planned)
                else:
                    expenses = cents(expenses + planned)
                    if group_code == "COS":
                        cost_of_sales = cents(cost_of_sales + planned)
        rows = [Row(f"acc:{code}", {"amount": amount}) for code, amount in accounts.items()]
        rows.extend(
            Row(f"grp:{code}", {"amount": amount}, emphasis="subtotal")
            for code, amount in groups.items()
        )
        rows.extend(
            [
                Row("total:revenue", {"amount": revenue}, emphasis="subtotal"),
                Row("total:gross", {"amount": cents(revenue - cost_of_sales)}, emphasis="subtotal"),
                Row(
                    "total:expenses",
                    {"amount": cents(expenses - cost_of_sales)},
                    emphasis="subtotal",
                ),
            ]
        )
        return Table(
            "profit_and_loss",
            "Budget",
            list(MONEY_COLUMNS),
            rows,
            Row("total:net", {"amount": cents(revenue - expenses)}, emphasis="total"),
        )

    def budget_variance_table(self, business_id: uuid.UUID, start: date, end: date) -> Table:
        rows: list[Row] = []
        for budget in self.approved_budgets(business_id, start, end):
            for line in budget.lines:
                planned, actual = self._budgets.window_figures(budget, line, start, end)
                if planned == ZERO and actual == ZERO:
                    continue
                income = self._budgets.nature_of(business_id, line).value == "income"
                variance = cents(actual - planned)
                favourable = variance >= ZERO if income else variance <= ZERO
                rows.append(
                    Row(
                        str(line.id),
                        {
                            "budget": budget.name,
                            "label": self._budgets.label_of(business_id, line),
                            "nature": "Income" if income else "Cost",
                            "planned": planned,
                            "actual": actual,
                            "variance": variance,
                            "percent": share(variance, planned),
                            "standing": "Favourable" if favourable else "Adverse",
                        },
                        drill={"view": "budget", "id": str(budget.id)},
                    )
                )
        return Table(
            "budget_variance",
            "Budget against actual",
            [
                Column("budget", "Budget"),
                Column("label", "Line"),
                Column("nature", "Nature"),
                Column("planned", "Budget", ColumnKind.MONEY),
                Column("actual", "Actual", ColumnKind.MONEY),
                Column("variance", "Variance", ColumnKind.MONEY),
                Column("percent", "%", ColumnKind.PERCENT),
                Column("standing", "Standing"),
            ],
            rows,
            note="Budgets for periods partly in the window are taken in proportion to the days "
            "that fall within it, including approved transfers.",
            empty="No approved budget covers this window.",
        )

    def _ledger_account(self, business_id: uuid.UUID, code: str):  # type: ignore[no-untyped-def]
        from nickland.persistence.ledger_models import Account

        return self._db.scalar(
            select(Account).where(Account.business_id == business_id, Account.code == code)
        )

    # -- reconciliation -------------------------------------------------------------------

    def statement_checks(
        self,
        actor: object,
        statement: ProfitAndLoss,
        sheet: BalanceSheet,
        start: date,
        end: date,
        basis: AccountingBasis,
    ) -> list[Check]:
        """The statements agree with the trial balance and with each other."""
        business_id = actor.business_id  # type: ignore[attr-defined]
        movement = self._ledger.balances(business_id, start=start, end=end, basis=basis)
        income = cents(sum((b for a, b in movement if a.kind == "revenue"), ZERO))
        spend = cents(sum((b for a, b in movement if a.kind == "expense"), ZERO))
        trial = self._ledger.trial_balance(actor, as_at=end, basis=basis)  # type: ignore[arg-type]
        flow = self._ledger.cash_flow(actor, start=start, end=end)  # type: ignore[arg-type]
        cash_end = self.control_balances(actor, end, basis)["cash"]
        cash_start = self.control_balances(actor, start - timedelta(days=1), basis)["cash"]
        return [
            Check(
                "trial_balance",
                "Trial balance debits equal credits",
                trial.total_debit,
                trial.total_credit,
            ),
            Check(
                "net_result",
                "Net profit agrees with the ledger's income less expenses",
                statement.net_result,
                cents(income - spend),
            ),
            Check(
                "balance_sheet",
                "Assets equal liabilities and equity",
                sheet.total_assets,
                sheet.total_liabilities_and_equity,
            ),
            Check(
                "cash_opening",
                "Opening cash agrees with the cash and bank accounts",
                flow.opening_cash,
                cash_start,
            ),
            Check(
                "cash_closing",
                "Closing cash agrees with the cash and bank accounts",
                flow.closing_cash,
                cash_end,
            ),
        ]

    def compare_window(
        self, comparison: Comparison, start: date, end: date, granularity: object
    ) -> tuple[date, date] | None:
        return comparison_window(comparison, start, end, granularity)  # type: ignore[arg-type]


__all__ = ["LedgerSections"]
