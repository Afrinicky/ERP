"""Reports as data. PRD §8, §20.6, §29.8.

A report is a header, a few headline figures and one or more tables. The same structure is
drawn on screen, written to PDF, Excel and CSV, and frozen into a saved run, so a figure
can never read one way on screen and another on paper.

Every header states the reporting window, the currency, the accounting basis, when the
report was generated and by whom, and every filter that narrowed it.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Final

from nickland.domain.errors import ValidationError

Value = str | Decimal | int | date | None


class ColumnKind(StrEnum):
    TEXT = "text"
    MONEY = "money"
    QUANTITY = "quantity"
    COUNT = "count"
    PERCENT = "percent"
    DATE = "date"
    DAYS = "days"

    @property
    def numeric(self) -> bool:
        return self not in (ColumnKind.TEXT, ColumnKind.DATE)


class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    CUSTOM = "custom"

    @property
    def label(self) -> str:
        return {
            Granularity.DAY: "Day",
            Granularity.WEEK: "Week",
            Granularity.MONTH: "Month",
            Granularity.QUARTER: "Quarter",
            Granularity.YEAR: "Year",
            Granularity.CUSTOM: "Custom range",
        }[self]


class Comparison(StrEnum):
    NONE = "none"
    PRIOR = "prior"
    YEAR = "year"
    BUDGET = "budget"

    @property
    def label(self) -> str:
        return {
            Comparison.NONE: "No comparison",
            Comparison.PRIOR: "Prior period",
            Comparison.YEAR: "Same period last year",
            Comparison.BUDGET: "Budget",
        }[self]

    @property
    def short(self) -> str:
        return {
            Comparison.NONE: "",
            Comparison.PRIOR: "Prior",
            Comparison.YEAR: "Last year",
            Comparison.BUDGET: "Budget",
        }[self]


class ReportGroup(StrEnum):
    SALES = "sales"
    PROCUREMENT = "procurement"
    OPERATIONS = "operations"
    FINANCE = "finance"

    @property
    def label(self) -> str:
        return {
            ReportGroup.SALES: "Sales",
            ReportGroup.PROCUREMENT: "Procurement",
            ReportGroup.OPERATIONS: "Operations",
            ReportGroup.FINANCE: "Finance",
        }[self]


class ReportId(StrEnum):
    QUOTATIONS = "quotation_performance"
    ORDER_PROFITABILITY = "order_profitability"
    SUPPLIER_COMPARISON = "supplier_comparison"
    PURCHASING = "purchasing"
    SALES = "sales"
    POS_SHIFTS = "pos_shifts"
    INSTALMENTS = "instalments"
    COMMISSION = "affiliate_commission"
    INVENTORY = "inventory"
    STOCK_MOVEMENTS = "stock_movements"
    DELIVERY = "delivery"
    FINANCE = "finance"
    MANAGEMENT_PACK = "management_pack"


OPERATIONAL_BASIS: Final = "Operational records"


@dataclass(frozen=True, slots=True)
class ReportDefinition:
    id: ReportId
    title: str
    group: ReportGroup
    summary: str
    sensitive: bool
    """Discloses cost or margin, so it is open only to those who may export such reports."""
    groupings: tuple[tuple[str, str], ...] = ()
    filters: tuple[str, ...] = ()
    comparisons: tuple[Comparison, ...] = (Comparison.PRIOR, Comparison.YEAR)
    as_at: bool = False
    """A position at a moment rather than activity over a window."""
    ledger: bool = False
    """Read from the general ledger, so its basis is an accounting basis."""


REPORTS: Final[dict[ReportId, ReportDefinition]] = {
    definition.id: definition
    for definition in (
        ReportDefinition(
            ReportId.QUOTATIONS,
            "Quotation Performance",
            ReportGroup.SALES,
            "Requests received, quotations issued, accepted, rejected and expired, and the rate "
            "at which they convert.",
            sensitive=False,
            groupings=(
                ("person", "Sales person"),
                ("customer", "Customer"),
                ("branch", "Branch"),
                ("month", "Month"),
            ),
            filters=("branch", "customer", "person"),
        ),
        ReportDefinition(
            ReportId.ORDER_PROFITABILITY,
            "Order Profitability",
            ReportGroup.SALES,
            "Quoted against actual cost, direct charges, tax, affiliate cost and the "
            "contribution of each order.",
            sensitive=True,
            filters=("branch", "customer"),
        ),
        ReportDefinition(
            ReportId.SUPPLIER_COMPARISON,
            "Supplier Comparison",
            ReportGroup.PROCUREMENT,
            "Price, availability, lead time and delivery performance by supplier, with the "
            "price history of each product.",
            sensitive=True,
            filters=("supplier", "product"),
        ),
        ReportDefinition(
            ReportId.PURCHASING,
            "Purchasing",
            ReportGroup.PROCUREMENT,
            "Purchase orders, goods received, supplier invoices, amounts paid and outstanding.",
            sensitive=True,
            filters=("branch", "supplier"),
        ),
        ReportDefinition(
            ReportId.SALES,
            "Sales",
            ReportGroup.SALES,
            "Realised sales by customer, product, category, period, channel and staff.",
            sensitive=False,
            groupings=(
                ("customer", "Customer"),
                ("product", "Product"),
                ("category", "Category"),
                ("month", "Month"),
                ("channel", "Channel"),
                ("person", "Sales person"),
                ("branch", "Branch"),
            ),
            filters=("branch", "customer", "product", "person", "channel"),
            comparisons=(Comparison.PRIOR, Comparison.YEAR),
        ),
        ReportDefinition(
            ReportId.POS_SHIFTS,
            "Point of Sale Shifts",
            ReportGroup.OPERATIONS,
            "Each shift's cashier, float, tender split, refunds, cash movements and variance.",
            sensitive=False,
            filters=("branch", "register", "person"),
            comparisons=(),
        ),
        ReportDefinition(
            ReportId.INSTALMENTS,
            "Instalment Collections",
            ReportGroup.SALES,
            "Payment schedules: what fell due, what was paid, what is overdue and outstanding.",
            sensitive=False,
            filters=("branch", "customer"),
            comparisons=(),
        ),
        ReportDefinition(
            ReportId.COMMISSION,
            "Affiliate Commission",
            ReportGroup.SALES,
            "Eligible sales, rate, accrual, approval and payout for each affiliate.",
            sensitive=False,
            filters=("affiliate",),
        ),
        ReportDefinition(
            ReportId.INVENTORY,
            "Inventory Position",
            ReportGroup.OPERATIONS,
            "On hand, reserved, available and incoming stock, its value, and batches nearing "
            "expiry.",
            sensitive=False,
            filters=("warehouse", "product"),
            comparisons=(),
            as_at=True,
        ),
        ReportDefinition(
            ReportId.STOCK_MOVEMENTS,
            "Stock Movements",
            ReportGroup.OPERATIONS,
            "Every receipt, issue, transfer, adjustment and return in the window.",
            sensitive=False,
            filters=("warehouse", "product"),
            comparisons=(),
        ),
        ReportDefinition(
            ReportId.DELIVERY,
            "Delivery",
            ReportGroup.OPERATIONS,
            "Orders awaiting assembly, dispatch and acknowledgement, and failed deliveries.",
            sensitive=False,
            filters=("branch", "customer"),
            comparisons=(),
        ),
        ReportDefinition(
            ReportId.FINANCE,
            "Financial Summary",
            ReportGroup.FINANCE,
            "Profit and loss, cash flow, receivables, payables and expenses for the window.",
            sensitive=True,
            filters=("basis",),
            comparisons=(Comparison.PRIOR, Comparison.YEAR, Comparison.BUDGET),
            ledger=True,
        ),
    )
}

MANAGEMENT_PACK: Final = ReportDefinition(
    ReportId.MANAGEMENT_PACK,
    "Management Report",
    ReportGroup.FINANCE,
    "The monthly management pack: every statement and analysis, reconciled to the ledger.",
    sensitive=True,
    filters=("basis",),
    comparisons=(Comparison.PRIOR, Comparison.YEAR),
    ledger=True,
)

SENSITIVE_PERMISSION: Final = "report.export_sensitive"


def definition_of(report_id: str) -> ReportDefinition:
    try:
        key = ReportId(report_id)
    except ValueError:
        raise ValidationError(f"There is no report called {report_id}.", field="report") from None
    if key is ReportId.MANAGEMENT_PACK:
        return MANAGEMENT_PACK
    return REPORTS[key]


def required_permissions(definition: ReportDefinition, *, export: bool) -> tuple[str, ...]:
    """What a person must hold to open, or to export, a report."""
    needed = ["report.view"]
    if definition.sensitive:
        needed.append(SENSITIVE_PERMISSION)
    if export:
        needed.append("report.export")
    return tuple(needed)


# -- windows -------------------------------------------------------------------------------


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def window(
    granularity: Granularity,
    anchor: date,
    *,
    start: date | None = None,
    end: date | None = None,
    year_starts: int = 1,
) -> tuple[date, date]:
    """The reporting window containing ``anchor``. A year follows the financial year."""
    if granularity is Granularity.CUSTOM:
        if start is None or end is None:
            raise ValidationError("Choose the first and last day of the range.", field="start")
        if end < start:
            raise ValidationError("The range ends before it begins.", field="end")
        if (end - start).days > 3660:
            raise ValidationError("Choose a range of ten years or less.", field="end")
        return start, end
    if granularity is Granularity.DAY:
        return anchor, anchor
    if granularity is Granularity.WEEK:
        first = anchor - timedelta(days=anchor.weekday())
        return first, first + timedelta(days=6)
    if granularity is Granularity.MONTH:
        return anchor.replace(day=1), _month_end(anchor.year, anchor.month)
    if granularity is Granularity.QUARTER:
        month = (anchor.month - 1) // 3 * 3 + 1
        return date(anchor.year, month, 1), _month_end(anchor.year, month + 2)
    year = anchor.year if anchor.month >= year_starts else anchor.year - 1
    first = date(year, year_starts, 1)
    return first, _add_months(first, 12) - timedelta(days=1)


def comparison_window(
    comparison: Comparison, start: date, end: date, granularity: Granularity
) -> tuple[date, date] | None:
    """The window a report is compared with: the one before it, or a year before."""
    if comparison in (Comparison.NONE, Comparison.BUDGET):
        return None
    if comparison is Comparison.YEAR:
        return _add_months(start, -12), _year_back_end(end)
    if granularity is Granularity.MONTH and start.day == 1:
        first = _add_months(start, -1)
        return first, _month_end(first.year, first.month)
    if granularity is Granularity.QUARTER and start.day == 1:
        first = _add_months(start, -3)
        return first, _add_months(first, 3) - timedelta(days=1)
    if granularity is Granularity.YEAR and start.day == 1:
        first = _add_months(start, -12)
        return first, start - timedelta(days=1)
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


def _year_back_end(day: date) -> date:
    if day == _month_end(day.year, day.month):
        return _month_end(day.year - 1, day.month)
    return _add_months(day, -12)


def describe_window(start: date, end: date) -> str:
    if start == end:
        return f"{start:%d %B %Y}".lstrip("0")
    if start.day == 1 and end == _month_end(end.year, end.month):
        if start.year == end.year and start.month == end.month:
            return f"{start:%B %Y}"
        if start.year == end.year:
            return f"{start:%B} to {end:%B %Y}"
        return f"{start:%B %Y} to {end:%B %Y}"
    return f"{start:%d %B %Y} to {end:%d %B %Y}".replace(" 0", " ").lstrip("0")


# -- arithmetic ----------------------------------------------------------------------------

CENT: Final = Decimal("0.01")
TENTH: Final = Decimal("0.1")


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def share(part: Decimal, whole: Decimal) -> Decimal | None:
    """``part`` as a percentage of ``whole``, to one decimal place."""
    if whole == 0:
        return None
    return (part * 100 / whole).quantize(TENTH, rounding=ROUND_HALF_UP)


def change(current: Decimal, prior: Decimal) -> Decimal | None:
    """Percentage change from ``prior`` to ``current``, against the size of ``prior``."""
    if prior == 0 or (current < 0) != (prior < 0):
        return None
    return ((current - prior) * 100 / abs(prior)).quantize(TENTH, rounding=ROUND_HALF_UP)


def difference(current: Decimal, prior: Decimal) -> Decimal:
    return current - prior


# -- the payload ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    label: str
    kind: ColumnKind = ColumnKind.TEXT
    compare: bool = False
    """Given a prior column, a change and a percentage beside it when compared."""
    sensitive: bool = False
    """Cost or value: withheld from those who may not see cost."""


@dataclass(slots=True)
class Row:
    key: str
    cells: dict[str, Any]
    drill: dict[str, str] | None = None
    emphasis: str | None = None
    """``heading``, ``subtotal`` or ``total``."""
    level: int = 0


@dataclass(slots=True)
class Table:
    key: str
    title: str
    columns: list[Column]
    rows: list[Row]
    total: Row | None = None
    note: str | None = None
    empty: str = "Nothing in this window."


@dataclass(slots=True)
class Figure:
    key: str
    label: str
    value: Value
    kind: ColumnKind = ColumnKind.MONEY
    prior: Value = None
    change: Decimal | None = None
    sensitive: bool = False
    note: str | None = None


@dataclass(slots=True)
class Check:
    """A reconciliation of a reported figure to the ledger."""

    key: str
    label: str
    reported: Decimal
    ledger: Decimal
    detail: str = ""

    @property
    def difference(self) -> Decimal:
        return self.reported - self.ledger

    @property
    def passed(self) -> bool:
        return self.difference == 0


@dataclass(slots=True)
class Header:
    title: str
    starts_on: date
    ends_on: date
    currency: str
    basis: str
    generated_at: datetime
    generated_by: str | None
    business: str
    filters: list[tuple[str, str]] = field(default_factory=list)
    comparison: str | None = None
    comparison_starts_on: date | None = None
    comparison_ends_on: date | None = None
    as_at: bool = False

    @property
    def period(self) -> str:
        if self.as_at:
            return f"As at {self.ends_on:%d %B %Y}".replace(" 0", " ")
        return describe_window(self.starts_on, self.ends_on)


@dataclass(slots=True)
class Section:
    """A part of a report: headline figures, tables and the notes beneath them."""

    key: str
    title: str
    figures: list[Figure] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    commentary: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Report:
    id: str
    title: str
    header: Header
    sections: list[Section]
    sensitive: bool = False
    withheld: bool = False
    """Columns were left out because the reader may not see cost."""

    @property
    def checks(self) -> list[Check]:
        return [check for section in self.sections for check in section.checks]

    @property
    def reconciled(self) -> bool:
        return all(check.passed for check in self.checks)


def compare_table(table: Table, prior: Table, comparison: Comparison) -> Table:
    """The table with, beside each comparable column, its prior value, the change and the
    percentage change. Rows found only in the prior window are kept, at nothing now."""
    if comparison is Comparison.NONE:
        return table
    previous = {row.key: row for row in prior.rows}
    columns: list[Column] = []
    for column in table.columns:
        columns.append(column)
        if column.compare and column.kind.numeric:
            columns.extend(
                [
                    Column(
                        f"{column.key}__prior",
                        comparison.short,
                        column.kind,
                        False,
                        column.sensitive,
                    ),
                    Column(f"{column.key}__change", "Change", column.kind, False, column.sensitive),
                    Column(f"{column.key}__pct", "%", ColumnKind.PERCENT, False, column.sensitive),
                ]
            )
    compared = [column for column in table.columns if column.compare and column.kind.numeric]

    def merge(row: Row, before: Row | None) -> Row:
        if row.emphasis == "heading":
            return row
        cells = dict(row.cells)
        for column in compared:
            now = _number(row.cells.get(column.key))
            then = _number(before.cells.get(column.key)) if before else Decimal(0)
            cells[f"{column.key}__prior"] = _like(then, column.kind)
            cells[f"{column.key}__change"] = _like(now - then, column.kind)
            cells[f"{column.key}__pct"] = change(now, then)
        return Row(row.key, cells, row.drill, row.emphasis, row.level)

    rows = [merge(row, previous.get(row.key)) for row in table.rows]
    seen = {row.key for row in table.rows}
    anchor: str | None = None
    for row in prior.rows:
        if row.key in seen:
            anchor = row.key
            continue
        if row.emphasis is not None:
            continue
        blank = {
            column.key: (
                _like(Decimal(0), column.kind) if column.kind.numeric else row.cells.get(column.key)
            )
            for column in table.columns
        }
        added = merge(Row(row.key, blank, None, None, row.level), row)
        # Placed after the row it followed in the prior window, so a line that has fallen
        # to nothing stays among its neighbours.
        position = next(
            (index + 1 for index, item in enumerate(rows) if item.key == anchor), len(rows)
        )
        if anchor is None:
            position = next(
                (index for index, item in enumerate(rows) if item.emphasis != "heading"), 0
            )
        rows.insert(position, added)
        anchor = row.key
    total = merge(table.total, prior.total) if table.total is not None else None
    return Table(table.key, table.title, columns, rows, total, table.note, table.empty)


def compare_figures(figures: list[Figure], prior: list[Figure]) -> list[Figure]:
    previous = {figure.key: figure for figure in prior}
    for figure in figures:
        before = previous.get(figure.key)
        if before is None or not figure.kind.numeric:
            continue
        figure.prior = before.value
        if figure.kind is not ColumnKind.PERCENT:
            figure.change = change(_number(figure.value), _number(before.value))
    return figures


def withhold(report: Report) -> Report:
    """Remove every sensitive column and figure."""
    removed = False
    for section in report.sections:
        kept = [figure for figure in section.figures if not figure.sensitive]
        removed = removed or len(kept) != len(section.figures)
        section.figures = kept
        for table in section.tables:
            hidden = {column.key for column in table.columns if column.sensitive}
            if not hidden:
                continue
            removed = True
            table.columns = [column for column in table.columns if column.key not in hidden]
            for row in [*table.rows, *([table.total] if table.total else [])]:
                for key in hidden:
                    row.cells.pop(key, None)
    report.withheld = removed
    return report


QUANTUM: Final = Decimal("0.001")


def tidy(value: Value, kind: ColumnKind) -> Value:
    """A figure as it is shown: money to the cent, quantities to three places at most."""
    if isinstance(value, bool) or value is None:
        return value
    if kind is ColumnKind.MONEY and isinstance(value, (Decimal, int)):
        return cents(Decimal(value))
    if kind is ColumnKind.QUANTITY and isinstance(value, (Decimal, int)):
        quantity = Decimal(value).quantize(QUANTUM, rounding=ROUND_HALF_UP)
        return (
            quantity.to_integral_value()
            if quantity == quantity.to_integral()
            else quantity.normalize()
        )
    return value


def normalise(report: Report) -> Report:
    """Every figure rounded once, here, so the screen and every export show the same."""
    for section in report.sections:
        for figure in section.figures:
            figure.value = tidy(figure.value, figure.kind)
            figure.prior = tidy(figure.prior, figure.kind)
        for table in section.tables:
            kinds = {column.key: column.kind for column in table.columns}
            for row in [*table.rows, *([table.total] if table.total else [])]:
                for key, value in list(row.cells.items()):
                    if key in kinds:
                        row.cells[key] = tidy(value, kinds[key])
                    else:
                        row.cells.pop(key)
        for check in section.checks:
            check.reported = cents(check.reported)
            check.ledger = cents(check.ledger)
    return report


def _number(value: Value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except ArithmeticError:
            return Decimal(0)
    return Decimal(0)


def _like(value: Decimal, kind: ColumnKind) -> Value:
    if kind is ColumnKind.COUNT:
        return int(value)
    if kind is ColumnKind.MONEY:
        return cents(value)
    return value


def number(value: Value) -> Decimal:
    return _number(value)


# -- the record: what is stored, sent and exported ----------------------------------------


def encode(value: Value) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def decode(value: Any, kind: ColumnKind) -> Value:
    if value is None:
        return None
    if kind is ColumnKind.DATE:
        return date.fromisoformat(value) if isinstance(value, str) else value
    if kind is ColumnKind.COUNT:
        return int(value)
    if kind.numeric:
        return Decimal(str(value))
    return str(value)


def _row_record(row: Row, kinds: dict[str, ColumnKind]) -> dict[str, Any]:
    return {
        "key": row.key,
        "cells": {key: encode(value) for key, value in row.cells.items() if key in kinds},
        "drill": row.drill,
        "emphasis": row.emphasis,
        "level": row.level,
    }


def _row_from(record: dict[str, Any], kinds: dict[str, ColumnKind]) -> Row:
    return Row(
        key=record["key"],
        cells={
            key: decode(value, kinds.get(key, ColumnKind.TEXT))
            for key, value in record["cells"].items()
        },
        drill=record.get("drill"),
        emphasis=record.get("emphasis"),
        level=int(record.get("level") or 0),
    )


def to_record(report: Report) -> dict[str, Any]:
    header = report.header
    return {
        "id": report.id,
        "title": report.title,
        "sensitive": report.sensitive,
        "withheld": report.withheld,
        "reconciled": report.reconciled,
        "header": {
            "title": header.title,
            "business": header.business,
            "period": header.period,
            "starts_on": header.starts_on.isoformat(),
            "ends_on": header.ends_on.isoformat(),
            "currency": header.currency,
            "basis": header.basis,
            "generated_at": header.generated_at.isoformat(),
            "generated_by": header.generated_by,
            "filters": [[label, value] for label, value in header.filters],
            "comparison": header.comparison,
            "comparison_starts_on": encode(header.comparison_starts_on),
            "comparison_ends_on": encode(header.comparison_ends_on),
            "as_at": header.as_at,
        },
        "sections": [
            {
                "key": section.key,
                "title": section.title,
                "commentary": list(section.commentary),
                "figures": [
                    {
                        "key": figure.key,
                        "label": figure.label,
                        "kind": figure.kind.value,
                        "value": encode(figure.value),
                        "prior": encode(figure.prior),
                        "change": encode(figure.change),
                        "sensitive": figure.sensitive,
                        "note": figure.note,
                    }
                    for figure in section.figures
                ],
                "tables": [
                    {
                        "key": table.key,
                        "title": table.title,
                        "note": table.note,
                        "empty": table.empty,
                        "columns": [
                            {
                                "key": column.key,
                                "label": column.label,
                                "kind": column.kind.value,
                                "compare": column.compare,
                                "sensitive": column.sensitive,
                            }
                            for column in table.columns
                        ],
                        "rows": [
                            _row_record(row, {c.key: c.kind for c in table.columns})
                            for row in table.rows
                        ],
                        "total": _row_record(table.total, {c.key: c.kind for c in table.columns})
                        if table.total
                        else None,
                    }
                    for table in section.tables
                ],
                "checks": [
                    {
                        "key": check.key,
                        "label": check.label,
                        "reported": encode(check.reported),
                        "ledger": encode(check.ledger),
                        "difference": encode(check.difference),
                        "passed": check.passed,
                        "detail": check.detail,
                    }
                    for check in section.checks
                ],
            }
            for section in report.sections
        ],
    }


def from_record(record: dict[str, Any]) -> Report:
    head = record["header"]

    def day(value: Any) -> date | None:
        return date.fromisoformat(value) if value else None

    header = Header(
        title=head["title"],
        starts_on=date.fromisoformat(head["starts_on"]),
        ends_on=date.fromisoformat(head["ends_on"]),
        currency=head["currency"],
        basis=head["basis"],
        generated_at=datetime.fromisoformat(head["generated_at"]),
        generated_by=head.get("generated_by"),
        business=head.get("business", ""),
        filters=[(label, value) for label, value in head.get("filters", [])],
        comparison=head.get("comparison"),
        comparison_starts_on=day(head.get("comparison_starts_on")),
        comparison_ends_on=day(head.get("comparison_ends_on")),
        as_at=bool(head.get("as_at")),
    )
    sections = []
    for item in record["sections"]:
        tables = []
        for table in item["tables"]:
            columns = [
                Column(
                    column["key"],
                    column["label"],
                    ColumnKind(column["kind"]),
                    bool(column.get("compare")),
                    bool(column.get("sensitive")),
                )
                for column in table["columns"]
            ]
            kinds = {column.key: column.kind for column in columns}
            tables.append(
                Table(
                    key=table["key"],
                    title=table["title"],
                    columns=columns,
                    rows=[_row_from(row, kinds) for row in table["rows"]],
                    total=_row_from(table["total"], kinds) if table.get("total") else None,
                    note=table.get("note"),
                    empty=table.get("empty") or "Nothing in this window.",
                )
            )
        sections.append(
            Section(
                key=item["key"],
                title=item["title"],
                commentary=list(item.get("commentary") or []),
                figures=[
                    Figure(
                        key=figure["key"],
                        label=figure["label"],
                        kind=ColumnKind(figure["kind"]),
                        value=decode(figure["value"], ColumnKind(figure["kind"])),
                        prior=decode(figure.get("prior"), ColumnKind(figure["kind"])),
                        change=Decimal(figure["change"]) if figure.get("change") else None,
                        sensitive=bool(figure.get("sensitive")),
                        note=figure.get("note"),
                    )
                    for figure in item["figures"]
                ],
                tables=tables,
                checks=[
                    Check(
                        key=check["key"],
                        label=check["label"],
                        reported=Decimal(check["reported"]),
                        ledger=Decimal(check["ledger"]),
                        detail=check.get("detail") or "",
                    )
                    for check in item.get("checks") or []
                ],
            )
        )
    return Report(
        id=record["id"],
        title=record["title"],
        header=header,
        sections=sections,
        sensitive=bool(record.get("sensitive")),
        withheld=bool(record.get("withheld")),
    )


# -- schedules -----------------------------------------------------------------------------


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

    @property
    def label(self) -> str:
        return self.value.capitalize()

    @property
    def granularity(self) -> Granularity:
        return {
            Frequency.DAILY: Granularity.DAY,
            Frequency.WEEKLY: Granularity.WEEK,
            Frequency.MONTHLY: Granularity.MONTH,
        }[self]


EXPORT_FORMATS: Final = ("pdf", "xlsx", "csv")


def next_run(frequency: Frequency, run_day: int, run_hour: int, after: datetime) -> datetime:
    """The first moment after ``after`` (local, naive) that a schedule falls due."""
    if not 0 <= run_hour <= 23:
        raise ValidationError("Choose an hour from 0 to 23.", field="run_hour")
    if frequency is Frequency.DAILY:
        candidate = after.replace(hour=run_hour, minute=0, second=0, microsecond=0)
        return candidate if candidate > after else candidate + timedelta(days=1)
    if frequency is Frequency.WEEKLY:
        if not 1 <= run_day <= 7:
            raise ValidationError("Choose a weekday from 1 (Monday) to 7.", field="run_day")
        candidate = after.replace(hour=run_hour, minute=0, second=0, microsecond=0)
        candidate += timedelta(days=(run_day - 1 - candidate.weekday()) % 7)
        return candidate if candidate > after else candidate + timedelta(days=7)
    if not 1 <= run_day <= 28:
        raise ValidationError("Choose a day of the month from 1 to 28.", field="run_day")
    candidate = after.replace(day=run_day, hour=run_hour, minute=0, second=0, microsecond=0)
    if candidate > after:
        return candidate
    month = _add_months(candidate.date().replace(day=1), 1)
    return candidate.replace(year=month.year, month=month.month)


def reported_window(frequency: Frequency, on: date) -> date:
    """The anchor of the window a run on ``on`` reports: the day, week or month just ended."""
    if frequency is Frequency.DAILY:
        return on - timedelta(days=1)
    if frequency is Frequency.WEEKLY:
        return on - timedelta(days=on.weekday() + 1)
    return on.replace(day=1) - timedelta(days=1)


__all__ = [
    "EXPORT_FORMATS",
    "MANAGEMENT_PACK",
    "OPERATIONAL_BASIS",
    "REPORTS",
    "SENSITIVE_PERMISSION",
    "Check",
    "Column",
    "ColumnKind",
    "Comparison",
    "Figure",
    "Frequency",
    "Granularity",
    "Header",
    "Report",
    "ReportDefinition",
    "ReportGroup",
    "ReportId",
    "Row",
    "Section",
    "Table",
    "Value",
    "cents",
    "change",
    "compare_figures",
    "compare_table",
    "comparison_window",
    "decode",
    "definition_of",
    "describe_window",
    "encode",
    "from_record",
    "next_run",
    "normalise",
    "number",
    "reported_window",
    "required_permissions",
    "share",
    "to_record",
    "window",
    "withhold",
]
