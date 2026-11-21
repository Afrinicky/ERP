"""The management pack. PRD §20.6, §29.8.

For a chosen window (a month by default) and compared with the window before it: an
executive summary, the profit and loss, the position, the cash flow, the trial balance,
sales, procurement, expenses, tax, receivable and payable ageing, debt and loans, inventory,
budget variance and the risks that need attention.

Every statement is drawn from the general ledger, and the pack carries the checks that
prove it: the trial balance balances, the statements agree with it and with each other,
and each register (customers, suppliers, loans, stock) agrees with its control account.
A pack whose checks do not all pass says so on its face.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.debts import DebtService
from nickland.application.services.forecasts import ForecastService
from nickland.application.services.inventory_reports import InventoryReportService
from nickland.application.services.ledger_reports import LedgerReportService
from nickland.application.services.loans import LoanService
from nickland.application.services.profitability import ProfitabilityService
from nickland.application.services.report_ledger import LedgerSections
from nickland.application.services.reports import (
    ReportParameters,
    ReportService,
    Scope,
    merge_sections,
)
from nickland.application.services.taxation import TaxService
from nickland.domain.errors import DomainError, PermissionDeniedError
from nickland.domain.financing import DebtKind
from nickland.domain.money import ZERO
from nickland.domain.reporting import (
    MANAGEMENT_PACK,
    Check,
    Column,
    ColumnKind,
    Comparison,
    Figure,
    Report,
    Row,
    Section,
    Table,
    cents,
    change,
    compare_figures,
    compare_table,
    comparison_window,
    normalise,
    share,
)
from nickland.persistence.finance_models import SalesInvoice
from nickland.persistence.inventory_models import StockMovement
from nickland.persistence.pos_models import PosSale
from nickland.persistence.tax_models import TaxPayment
from nickland.platform.clock import Clock, SystemClock

SECTION_TITLES: dict[str, str] = {
    "summary": "Executive Summary",
    "profit_and_loss": "Profit and Loss",
    "balance_sheet": "Statement of Financial Position",
    "cash_flow": "Cash Flow",
    "trial_balance": "Trial Balance",
    "sales": "Sales Performance",
    "profitability": "Profitability",
    "procurement": "Purchasing and Suppliers",
    "expenses": "Expense Analysis",
    "tax": "Tax Position",
    "ageing": "Receivables and Payables",
    "loans": "Debt and Loan Schedule",
    "inventory": "Inventory",
    "budget": "Budget Variance",
    "risks": "Key Risks",
}
ADJUSTMENTS = (
    "damage",
    "expiry",
    "count_gain",
    "count_loss",
    "adjustment_gain",
    "adjustment_loss",
    "transit_loss",
)


class ManagementPackService:
    __slots__ = ("_clock", "_db", "_ledger", "_reports", "_sections")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._reports = ReportService(session, clock=self._clock)
        self._sections = LedgerSections(session, clock=self._clock)
        self._ledger = LedgerReportService(session, clock=self._clock)

    def build(self, actor: AnyActor, params: ReportParameters) -> Report:
        ReportService.authorise(actor, MANAGEMENT_PACK, export=False)
        scope = self._reports.scope(actor, MANAGEMENT_PACK, params)
        prior = comparison_window(params.comparison, scope.start, scope.end, params.granularity)
        before = scope.shifted(*prior) if prior else None
        header = self._reports.header(scope, MANAGEMENT_PACK)
        if prior:
            header.comparison = params.comparison.label
            header.comparison_starts_on, header.comparison_ends_on = prior

        builders: list[tuple[str, Callable[[], Section]]] = [
            ("summary", lambda: self._summary(scope, before)),
            ("profit_and_loss", lambda: self._profit_and_loss(scope, before)),
            ("balance_sheet", lambda: self._balance_sheet(scope, before)),
            ("cash_flow", lambda: self._cash_flow(scope, before)),
            ("trial_balance", lambda: self._trial_balance(scope)),
            ("sales", lambda: self._sales(scope, before, params.comparison)),
            ("profitability", lambda: self._profitability(scope)),
            ("procurement", lambda: self._procurement(scope, before, params.comparison)),
            ("expenses", lambda: self._expenses(scope, before)),
            ("tax", lambda: self._tax(scope)),
            ("ageing", lambda: self._ageing(scope)),
            ("loans", lambda: self._loans(scope)),
            ("inventory", lambda: self._inventory(scope)),
            ("budget", lambda: self._budget(scope)),
        ]
        sections: list[Section] = []
        for key, build in builders:
            try:
                sections.append(build())
            except PermissionDeniedError:
                sections.append(
                    Section(
                        key,
                        SECTION_TITLES[key],
                        commentary=[
                            "Not available: this section needs a permission you do not hold."
                        ],
                    )
                )
        sections.append(self._risks(scope, sections))
        return normalise(
            Report(
                id=MANAGEMENT_PACK.id.value,
                title=MANAGEMENT_PACK.title,
                header=header,
                sections=sections,
                sensitive=True,
            )
        )

    # -- statements -----------------------------------------------------------------------

    def _summary(self, scope: Scope, before: Scope | None) -> Section:
        basis = scope.params.basis
        statement = self._ledger.profit_and_loss(
            scope.actor, start=scope.start, end=scope.end, basis=basis
        )
        figures = [
            Figure("sales", "Sales, including tax", self._gross_sales(scope)),
            *self._sections.headline(scope.actor, statement, scope.start, scope.end, basis),
        ]
        commentary: list[str] = []
        if before is not None:
            earlier = self._ledger.profit_and_loss(
                scope.actor, start=before.start, end=before.end, basis=basis
            )
            prior = [
                Figure("sales", "", self._gross_sales(before)),
                *self._sections.headline(scope.actor, earlier, before.start, before.end, basis),
            ]
            compare_figures(figures, prior)
            commentary = self._commentary(figures, scope.business.currency_symbol)
        margin = share(statement.gross_profit, statement.total_revenue)
        if margin is not None:
            commentary.append(f"Gross margin was {margin}% of net revenue.")
        return Section("summary", SECTION_TITLES["summary"], figures=figures, commentary=commentary)

    @staticmethod
    def _commentary(figures: list[Figure], symbol: str) -> list[str]:
        sentences = []
        named = {figure.key: figure for figure in figures}
        for key, noun in (
            ("revenue", "Net revenue"),
            ("net", "Net profit"),
            ("expenses", "Operating expenses"),
            ("closing_cash", "Cash and bank"),
        ):
            figure = named.get(key)
            if figure is None or not isinstance(figure.value, Decimal):
                continue
            then = figure.prior if isinstance(figure.prior, Decimal) else ZERO
            now = figure.value
            if then == now:
                sentences.append(f"{noun} was unchanged at {_amount(symbol, now)}.")
                continue
            verb = "rose" if now > then else "fell"
            percent = change(now, then) if then > ZERO and now >= ZERO else None
            by = f" by {abs(percent)}%" if percent is not None else ""
            sentences.append(
                f"{noun} {verb}{by} to {_amount(symbol, now)}, against "
                f"{_amount(symbol, then)} in the prior period."
            )
        return sentences

    def _gross_sales(self, scope: Scope) -> Decimal:
        invoiced = self._db.scalar(
            select(func.coalesce(func.sum(SalesInvoice.total), 0)).where(
                SalesInvoice.business_id == scope.business_id,
                SalesInvoice.origin == "sales_order",
                SalesInvoice.issued_on >= scope.start,
                SalesInvoice.issued_on <= scope.end,
            )
        )
        rung, refunded = self._db.execute(
            select(
                func.coalesce(func.sum(PosSale.total), 0),
                func.coalesce(func.sum(PosSale.refunded_total), 0),
            ).where(
                PosSale.business_id == scope.business_id,
                PosSale.state.in_(("completed", "refunded", "partially_refunded")),
                PosSale.completed_on >= scope.start,
                PosSale.completed_on <= scope.end,
            )
        ).one()
        return cents(Decimal(invoiced or 0) + Decimal(rung or 0) - Decimal(refunded or 0))

    def _profit_and_loss(self, scope: Scope, before: Scope | None) -> Section:
        basis = scope.params.basis
        statement = self._ledger.profit_and_loss(
            scope.actor, start=scope.start, end=scope.end, basis=basis
        )
        sheet = self._ledger.balance_sheet(scope.actor, as_at=scope.end, basis=basis)
        table = self._sections.profit_and_loss_table(statement)
        if before is not None:
            earlier = self._ledger.profit_and_loss(
                scope.actor, start=before.start, end=before.end, basis=basis
            )
            table = compare_table(
                table, self._sections.profit_and_loss_table(earlier), scope.params.comparison
            )
        return Section(
            "profit_and_loss",
            SECTION_TITLES["profit_and_loss"],
            tables=[table],
            checks=self._sections.statement_checks(
                scope.actor, statement, sheet, scope.start, scope.end, basis
            ),
        )

    def _balance_sheet(self, scope: Scope, before: Scope | None) -> Section:
        basis = scope.params.basis
        sheet = self._ledger.balance_sheet(scope.actor, as_at=scope.end, basis=basis)
        table = self._sections.balance_sheet_table(sheet)
        if before is not None:
            earlier = self._ledger.balance_sheet(scope.actor, as_at=before.end, basis=basis)
            table = compare_table(
                table, self._sections.balance_sheet_table(earlier), scope.params.comparison
            )
        return Section("balance_sheet", SECTION_TITLES["balance_sheet"], tables=[table])

    def _cash_flow(self, scope: Scope, before: Scope | None) -> Section:
        table = self._sections.cash_flow_table(scope.actor, scope.start, scope.end)
        if before is not None:
            table = compare_table(
                table,
                self._sections.cash_flow_table(scope.actor, before.start, before.end),
                scope.params.comparison,
            )
        return Section("cash_flow", SECTION_TITLES["cash_flow"], tables=[table])

    def _trial_balance(self, scope: Scope) -> Section:
        return Section(
            "trial_balance",
            SECTION_TITLES["trial_balance"],
            tables=[self._sections.trial_balance_table(scope.actor, scope.end, scope.params.basis)],
        )

    # -- operations -----------------------------------------------------------------------

    def _operational(
        self,
        scope: Scope,
        before: Scope | None,
        comparison: Comparison,
        builder: Callable[[Scope], list[Section]],
        grouping: str | None,
    ) -> list[Section]:
        params = ReportParameters(
            granularity=scope.params.granularity,
            comparison=scope.params.comparison,
            grouping=grouping,
        )
        current = builder(
            Scope(
                scope.business,
                scope.today,
                scope.start,
                scope.end,
                params,
                scope.names,
                scope.actor,
            )
        )
        if before is None:
            return current
        prior = builder(
            Scope(
                scope.business,
                scope.today,
                before.start,
                before.end,
                params,
                scope.names,
                scope.actor,
            )
        )
        return merge_sections(current, prior, comparison)

    def _sales(self, scope: Scope, before: Scope | None, comparison: Comparison) -> Section:
        tables: list[Table] = []
        figures: list[Figure] = []
        for grouping, title, limit in (
            ("channel", "By channel", None),
            ("category", "By product category", None),
            ("product", "Leading products", 10),
            ("customer", "Leading customers", 10),
            ("person", "By sales person", None),
        ):
            sections = self._operational(scope, before, comparison, self._reports._sales, grouping)
            table = sections[0].tables[0]
            table.key = f"sales_{grouping}"
            table.title = title
            table.note = None
            table.columns = [column for column in table.columns if column.key not in ("lines",)]
            if limit is not None and len(table.rows) > limit:
                table.rows = table.rows[:limit]
                table.title = f"{title}, first {limit}"
                table.total = None
            tables.append(table)
            if not figures:
                figures = sections[0].figures
        instalments = self._reports._instalments(scope)[0]
        collection = [
            figure
            for figure in instalments.figures
            if figure.key in ("due", "collected", "rate", "overdue")
        ]
        for figure in collection:
            figure.key = f"instalment_{figure.key}"
            figure.label = f"Instalments: {figure.label[0].lower()}{figure.label[1:]}"
        return Section(
            "sales", SECTION_TITLES["sales"], figures=[*figures, *collection], tables=tables
        )

    def _profitability(self, scope: Scope) -> Section:
        service = ProfitabilityService(self._db)
        tables = []
        for dimension, title in (
            ("product", "Products"),
            ("order", "Orders"),
            ("channel", "Channels"),
        ):
            result = service.profitability(
                scope.actor, dimension=dimension, start=scope.start, end=scope.end
            )
            ranked = sorted(
                (row for row in result.rows if row.revenue != ZERO),
                key=lambda row: -row.after_commission,
            )
            chosen = ranked if len(ranked) <= 10 else [*ranked[:5], *ranked[-5:]]
            tables.append(
                Table(
                    f"profit_{dimension}",
                    title if len(ranked) <= 10 else f"{title}: highest and lowest five",
                    [
                        Column("label", title[:-1]),
                        Column("revenue", "Revenue", ColumnKind.MONEY),
                        Column("cost", "Cost of goods", ColumnKind.MONEY, sensitive=True),
                        Column("commission", "Commission", ColumnKind.MONEY),
                        Column("contribution", "Contribution", ColumnKind.MONEY, sensitive=True),
                        Column("margin", "Margin", ColumnKind.PERCENT, sensitive=True),
                    ],
                    [
                        Row(
                            row.key,
                            {
                                "label": row.label,
                                "revenue": row.revenue,
                                "cost": row.cost,
                                "commission": row.commission,
                                "contribution": row.after_commission,
                                "margin": row.margin,
                            },
                        )
                        for row in chosen
                    ],
                    note="Contribution after the actual cost of goods and commission. Lines "
                    "still awaiting their cost count in revenue only.",
                )
            )
        return Section("profitability", SECTION_TITLES["profitability"], tables=tables)

    def _procurement(self, scope: Scope, before: Scope | None, comparison: Comparison) -> Section:
        section = self._operational(scope, before, comparison, self._reports._purchasing, None)[0]
        tables = [table for table in section.tables if table.key == "by_supplier"]
        erosion = ProfitabilityService(self._db).erosion(
            scope.actor, start=scope.start, end=scope.end
        )
        suppliers: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: {"quoted": ZERO, "actual": ZERO}
        )
        for row in erosion.rows:
            if row.actual_cost is None:
                continue
            suppliers[row.supplier]["quoted"] += row.quoted_cost
            suppliers[row.supplier]["actual"] += row.actual_cost
        tables.append(
            Table(
                "cost_variance",
                "Quoted against actual cost",
                [
                    Column("supplier", "Supplier or source"),
                    Column("quoted", "Quoted cost", ColumnKind.MONEY, sensitive=True),
                    Column("actual", "Actual cost", ColumnKind.MONEY, sensitive=True),
                    Column("variance", "Variance", ColumnKind.MONEY, sensitive=True),
                    Column("percent", "%", ColumnKind.PERCENT),
                ],
                [
                    Row(
                        name,
                        {
                            "supplier": name,
                            "quoted": data["quoted"],
                            "actual": data["actual"],
                            "variance": data["actual"] - data["quoted"],
                            "percent": share(data["actual"] - data["quoted"], data["quoted"]),
                        },
                    )
                    for name, data in sorted(suppliers.items())
                ],
                note="On order lines invoiced in the window whose actual cost is known. A "
                "positive variance cost more than quoted.",
                empty="No quoted order line with a known cost was invoiced in this window.",
            )
        )
        return Section(
            "procurement", SECTION_TITLES["procurement"], figures=section.figures, tables=tables
        )

    def _expenses(self, scope: Scope, before: Scope | None) -> Section:
        basis = scope.params.basis
        statement = self._ledger.profit_and_loss(
            scope.actor, start=scope.start, end=scope.end, basis=basis
        )
        table = self._sections.expense_table(statement)
        commentary: list[str] = []
        if before is not None:
            earlier = self._sections.expense_table(
                self._ledger.profit_and_loss(
                    scope.actor, start=before.start, end=before.end, basis=basis
                )
            )
            commentary = self._sections.unusual(table, earlier)
            table = compare_table(table, earlier, scope.params.comparison)
        if not commentary:
            commentary = ["No expense rose unusually against the prior period."]
        top = table.rows[:3]
        if top:
            names = ", ".join(str(row.cells["label"]).split(" ", 1)[-1] for row in top)
            commentary.insert(0, f"The largest costs were {names}.")
        return Section(
            "expenses", SECTION_TITLES["expenses"], tables=[table], commentary=commentary
        )

    def _tax(self, scope: Scope) -> Section:
        service = TaxService(self._db, clock=self._clock)
        summary = service.summary(scope.actor, as_at=scope.today)
        obligations = [
            item
            for item in service.obligations(scope.actor)
            if (item.period_ends_on >= scope.start
            and item.period_starts_on <= scope.end)
            or scope.start <= item.due_on <= scope.end
        ]
        paid = self._db.scalar(
            select(func.coalesce(func.sum(TaxPayment.amount), 0)).where(
                TaxPayment.business_id == scope.business_id,
                TaxPayment.paid_on >= scope.start,
                TaxPayment.paid_on <= scope.end,
            )
        )
        accrued = cents(
            sum((item.assessed for item in obligations if item.period_ends_on <= scope.end), ZERO)
        )
        rows = [
            Row(
                str(item.id),
                {
                    "label": item.label,
                    "period": f"{item.period_starts_on:%d %b} to {item.period_ends_on:%d %b %Y}",
                    "due_on": item.due_on,
                    "assessed": item.assessed,
                    "recoverable": item.recoverable,
                    "withheld": item.withheld,
                    "paid": item.paid,
                    "outstanding": service.net_payable(item),
                    "standing": item.state.replace("_", " ").capitalize()
                    + (
                        " (overdue)"
                        if item.due_on < scope.today and service.net_payable(item) > ZERO
                        else ""
                    ),
                },
                drill={"view": "tax_obligation", "id": str(item.id)},
            )
            for item in sorted(obligations, key=lambda item: item.due_on)
        ]
        return Section(
            "tax",
            SECTION_TITLES["tax"],
            figures=[
                Figure("accrued", "Assessed for the window", accrued),
                Figure("paid", "Paid in the window", cents(Decimal(paid or 0))),
                Figure("outstanding", "Outstanding today", summary["outstanding"]),  # type: ignore[arg-type]
                Figure("overdue", "Overdue today", summary["overdue"]),  # type: ignore[arg-type]
                Figure("recoverable", "Recoverable", summary["recoverable"]),  # type: ignore[arg-type]
                Figure("withheld", "Withheld", summary["withheld"]),  # type: ignore[arg-type]
            ],
            tables=[
                Table(
                    "obligations",
                    "Obligations for the window",
                    [
                        Column("label", "Obligation"),
                        Column("period", "Period"),
                        Column("due_on", "Due", ColumnKind.DATE),
                        Column("assessed", "Assessed", ColumnKind.MONEY),
                        Column("recoverable", "Recoverable", ColumnKind.MONEY),
                        Column("withheld", "Withheld", ColumnKind.MONEY),
                        Column("paid", "Paid", ColumnKind.MONEY),
                        Column("outstanding", "Outstanding", ColumnKind.MONEY),
                        Column("standing", "Standing"),
                    ],
                    rows,
                    empty="No tax obligation falls in this window.",
                )
            ],
        )

    def _ageing(self, scope: Scope) -> Section:
        debts = DebtService(self._db, clock=self._clock)
        summary = debts.summary(scope.actor, on=scope.today)
        columns = [
            Column("label", "Age"),
            Column("count", "Items", ColumnKind.COUNT),
            Column("amount", "Outstanding", ColumnKind.MONEY),
            Column("share", "Share", ColumnKind.PERCENT),
        ]

        def table(key: str, title: str, splits: list, total: Decimal) -> Table:  # type: ignore[type-arg]
            return Table(
                key,
                title,
                columns,
                [
                    Row(
                        split.code,
                        {
                            "label": split.label,
                            "count": split.count,
                            "amount": split.amount,
                            "share": share(split.amount, total),
                        },
                        drill={"view": "debts", "side": key, "bucket": split.code},
                    )
                    for split in splits
                ],
                Row(
                    "total",
                    {
                        "label": "Total",
                        "count": sum(s.count for s in splits),
                        "amount": cents(sum((s.amount for s in splits), ZERO)),
                        "share": Decimal("100.0") if total else None,
                    },
                    emphasis="total",
                ),
            )

        receivables = debts.register(scope.actor, owed_to_us=True, on=scope.today)
        payables = debts.register(scope.actor, owed_to_us=False, on=scope.today)
        control = self._sections.control_balances(scope.actor, date.max, scope.params.basis)
        trade = cents(
            sum(
                (
                    line.outstanding
                    for line in receivables
                    if line.kind is DebtKind.TRADE_RECEIVABLE
                ),
                ZERO,
            )
        )
        supplier = cents(
            sum((line.outstanding for line in payables if line.kind is DebtKind.SUPPLIER), ZERO)
        )
        return Section(
            "ageing",
            SECTION_TITLES["ageing"],
            figures=[
                Figure("receivables", "Owed to the business", summary.receivables),
                Figure("receivable_overdue", "Of which overdue", summary.receivable_overdue),
                Figure("payables", "Owed by the business", summary.payables),
                Figure("payable_overdue", "Of which overdue", summary.payable_overdue),
            ],
            tables=[
                table(
                    "receivable",
                    "Owed to the business, by age",
                    summary.receivable_ageing,
                    summary.receivables,
                ),
                table(
                    "payable",
                    "Owed by the business, by age",
                    summary.payable_ageing,
                    summary.payables,
                ),
            ],
            checks=[
                Check(
                    "receivables",
                    "Customer invoices agree with the customer control account",
                    trade,
                    control["receivables"],
                ),
                Check(
                    "payables",
                    "Supplier invoices agree with the supplier control account",
                    supplier,
                    control["payables"],
                ),
            ],
            commentary=[f"Ageing is as at {scope.today:%d %B %Y}.".replace(" 0", " ")],
        )

    def _loans(self, scope: Scope) -> Section:
        loans = LoanService(self._db, clock=self._clock)
        rows: list[Row] = []
        sums: dict[str, dict[str, Decimal]] = {
            "borrowing": defaultdict(lambda: ZERO),
            "lending": defaultdict(lambda: ZERO),
        }
        for agreement in loans.agreements(scope.actor):
            if agreement.state not in ("active", "in_arrears", "restructured", "approved"):
                continue
            position = loans.position(agreement, on=scope.today)
            side = agreement.direction
            for name, value in (
                ("principal", position.principal_outstanding),
                ("charges", position.interest_due + position.fees_due + position.penalties_due),
                ("ledger_principal", position.ledger_principal),
            ):
                sums[side][name] += value
            rows.append(
                Row(
                    str(agreement.id),
                    {
                        "number": agreement.number,
                        "direction": "Borrowed" if side == "borrowing" else "Lent",
                        "counterparty": agreement.counterparty_name,
                        "rate": agreement.annual_rate,
                        "maturity": agreement.maturity_date,
                        "principal": position.principal_outstanding,
                        "charges": cents(
                            position.interest_due + position.fees_due + position.penalties_due
                        ),
                        "arrears": position.arrears,
                        "next_due_on": position.next_due_on,
                        "next_due": position.next_due_amount,
                    },
                    drill={"view": "loan", "id": str(agreement.id)},
                )
            )
        checks = [
            Check(
                f"loans_{side}",
                (
                    "Borrowings agree with the loan accounts"
                    if side == "borrowing"
                    else "Loans made agree with the loan accounts"
                ),
                cents(figures["principal"]),
                cents(figures["ledger_principal"]),
            )
            for side, figures in sums.items()
            if figures["principal"] or figures["ledger_principal"]
        ]
        return Section(
            "loans",
            SECTION_TITLES["loans"],
            figures=[
                Figure("borrowed", "Borrowings outstanding", cents(sums["borrowing"]["principal"])),
                Figure("lent", "Loans made outstanding", cents(sums["lending"]["principal"])),
                Figure(
                    "arrears",
                    "In arrears",
                    cents(sum((row.cells["arrears"] for row in rows), ZERO)),
                ),
            ],
            tables=[
                Table(
                    "agreements",
                    "Agreements",
                    [
                        Column("number", "Agreement"),
                        Column("direction", "Direction"),
                        Column("counterparty", "Counterparty"),
                        Column("rate", "Rate", ColumnKind.PERCENT),
                        Column("maturity", "Matures", ColumnKind.DATE),
                        Column("principal", "Principal", ColumnKind.MONEY),
                        Column("charges", "Interest and fees due", ColumnKind.MONEY),
                        Column("arrears", "Arrears", ColumnKind.MONEY),
                        Column("next_due_on", "Next due", ColumnKind.DATE),
                        Column("next_due", "Next instalment", ColumnKind.MONEY),
                    ],
                    rows,
                    empty="No loan agreement is in force.",
                )
            ],
            checks=checks,
        )

    def _inventory(self, scope: Scope) -> Section:
        service = InventoryReportService(self._db, clock=self._clock)
        valuation = service.valuation(scope.actor)
        by_product: dict[str, dict[str, object]] = {}
        for row in valuation.rows:
            item = by_product.setdefault(
                row.sku, {"sku": row.sku, "name": row.name, "quantity": ZERO, "value": ZERO}
            )
            item["quantity"] = item["quantity"] + row.quantity  # type: ignore[operator]
            item["value"] = item["value"] + row.value  # type: ignore[operator]
        ranked = sorted(by_product.values(), key=lambda item: -item["value"])  # type: ignore[operator]
        cost_of_sales = cents(
            sum(
                (
                    balance
                    for account, balance in self._ledger.balances(
                        scope.business_id, start=scope.start, end=scope.end
                    )
                    if account.code == "50000"
                ),
                ZERO,
            )
        )
        turnover = (
            (cost_of_sales / valuation.total * 365 / ((scope.end - scope.start).days + 1)).quantize(
                Decimal("0.1")
            )
            if valuation.total
            else None
        )
        adjustments = self._db.execute(
            select(StockMovement.movement_type, func.count(), func.sum(StockMovement.value))
            .where(
                StockMovement.business_id == scope.business_id,
                StockMovement.occurred_on >= scope.start,
                StockMovement.occurred_on <= scope.end,
                StockMovement.movement_type.in_(ADJUSTMENTS),
            )
            .group_by(StockMovement.movement_type)
        ).all()
        idle_since = scope.today - timedelta(days=90)
        moving = set(
            self._db.scalars(
                select(StockMovement.product_fk).where(
                    StockMovement.business_id == scope.business_id,
                    StockMovement.occurred_on >= idle_since,
                    StockMovement.movement_type.in_(("issue", "dispatch", "delivery")),
                )
            ).all()
        )
        slow = [row for row in valuation.rows if row.product_id not in moving and row.value]
        expiring = service.expiry(scope.actor, within_days=90)
        return Section(
            "inventory",
            SECTION_TITLES["inventory"],
            figures=[
                Figure("value", "Stock value", valuation.total),
                Figure(
                    "turnover",
                    "Annualised turnover",
                    turnover,
                    ColumnKind.QUANTITY,
                    note="Times a year: cost of sales over stock value.",
                ),
                Figure("slow", "Lines without a sale in 90 days", len(slow), ColumnKind.COUNT),
                Figure(
                    "expiring", "Batches expiring within 90 days", len(expiring), ColumnKind.COUNT
                ),
            ],
            tables=[
                Table(
                    "valuation",
                    "Valuation by product" + (", largest 15" if len(ranked) > 15 else ""),
                    [
                        Column("sku", "SKU"),
                        Column("name", "Product"),
                        Column("quantity", "Quantity", ColumnKind.QUANTITY),
                        Column("value", "Value", ColumnKind.MONEY),
                    ],
                    [Row(str(item["sku"]), dict(item)) for item in ranked[:15]],
                    Row("total", {"sku": "Total", "value": valuation.total}, emphasis="total"),
                ),
                Table(
                    "adjustments",
                    "Adjustments in the window",
                    [
                        Column("kind", "Adjustment"),
                        Column("count", "Movements", ColumnKind.COUNT),
                        Column("value", "Value", ColumnKind.MONEY),
                    ],
                    [
                        Row(
                            kind,
                            {
                                "kind": kind.replace("_", " ").capitalize(),
                                "count": int(count),
                                "value": Decimal(value or 0),
                            },
                        )
                        for kind, count, value in adjustments
                    ],
                    empty="No stock was written off, found or adjusted in this window.",
                ),
            ],
            checks=[
                Check(
                    f"stock_{account.account}",
                    f"Stock layers agree with {account.account} {account.name}",
                    account.layers,
                    account.ledger,
                )
                for account in valuation.accounts
            ],
            commentary=["Stock is valued as at today, from the cost layers."],
        )

    def _budget(self, scope: Scope) -> Section:
        table = self._sections.budget_variance_table(scope.business_id, scope.start, scope.end)
        adverse = [row for row in table.rows if row.cells.get("standing") == "Adverse"]
        commentary = (
            [f"{len(adverse)} budget line{'s' if len(adverse) != 1 else ''} ran adverse to plan."]
            if adverse
            else (["Every budget line is within plan."] if table.rows else [])
        )
        return Section("budget", SECTION_TITLES["budget"], tables=[table], commentary=commentary)

    def _risks(self, scope: Scope, sections: list[Section]) -> Section:
        rows: list[Row] = []
        try:
            for alert in ForecastService(self._db, clock=self._clock).alerts(scope.actor):
                rows.append(
                    Row(
                        alert.kind,
                        {
                            "severity": alert.severity.capitalize(),
                            "risk": alert.title,
                            "detail": alert.detail,
                            "amount": alert.amount,
                        },
                        drill={"view": alert.view},
                    )
                )
        except DomainError:
            pass
        for section in sections:
            for check in section.checks:
                if not check.passed:
                    rows.insert(
                        0,
                        Row(
                            f"check:{check.key}",
                            {
                                "severity": "Critical",
                                "risk": check.label,
                                "detail": f"Out by {abs(check.difference):,.2f}; reported "
                                f"{check.reported:,.2f}, ledger {check.ledger:,.2f}.",
                                "amount": abs(check.difference),
                            },
                        ),
                    )
        order = {"Critical": 0, "Warning": 1, "Notice": 2}
        rows.sort(key=lambda row: order.get(str(row.cells["severity"]), 3))
        return Section(
            "risks",
            SECTION_TITLES["risks"],
            tables=[
                Table(
                    "risks",
                    "What needs attention",
                    [
                        Column("severity", "Severity"),
                        Column("risk", "Risk"),
                        Column("detail", "Detail"),
                        Column("amount", "Amount", ColumnKind.MONEY),
                    ],
                    rows,
                    empty="Nothing needs attention.",
                )
            ],
        )


def _amount(symbol: str, value: Decimal) -> str:
    sign = "\u2212" if value < ZERO else ""
    return f"{sign}{symbol} {abs(value):,.2f}"


__all__ = ["SECTION_TITLES", "ManagementPackService"]
