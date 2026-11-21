# ruff: noqa: DTZ001  (a schedule falls due on the business's local wall clock)
"""Report windows, comparison, rounding, withholding, schedules and the export writers.
PRD §8, §20.6, §29.8."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from nickland.documents.report_export import present, to_csv, to_html, to_xlsx
from nickland.domain.errors import ValidationError
from nickland.domain.reporting import (
    MANAGEMENT_PACK,
    REPORTS,
    Column,
    ColumnKind,
    Comparison,
    Figure,
    Frequency,
    Granularity,
    Header,
    Report,
    ReportId,
    Row,
    Section,
    Table,
    change,
    compare_table,
    comparison_window,
    definition_of,
    describe_window,
    from_record,
    next_run,
    normalise,
    reported_window,
    required_permissions,
    to_record,
    window,
    withhold,
)


class TestCatalogue:
    def test_the_twelve_reports_of_the_requirements(self) -> None:
        assert len(REPORTS) == 12
        assert ReportId.MANAGEMENT_PACK not in REPORTS
        assert definition_of("management_pack") is MANAGEMENT_PACK

    def test_cost_and_margin_need_the_sensitive_permission(self) -> None:
        sensitive = {key.value for key, item in REPORTS.items() if item.sensitive}
        assert sensitive == {"order_profitability", "supplier_comparison", "purchasing", "finance"}
        assert "report.export_sensitive" in required_permissions(MANAGEMENT_PACK, export=False)
        assert required_permissions(REPORTS[ReportId.SALES], export=False) == ("report.view",)
        assert "report.export" in required_permissions(REPORTS[ReportId.SALES], export=True)

    def test_an_unknown_report_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            definition_of("everything")


class TestWindows:
    @pytest.mark.parametrize(
        ("granularity", "expected"),
        [
            (Granularity.DAY, (date(2026, 11, 18), date(2026, 11, 18))),
            (Granularity.WEEK, (date(2026, 11, 16), date(2026, 11, 22))),
            (Granularity.MONTH, (date(2026, 11, 1), date(2026, 11, 30))),
            (Granularity.QUARTER, (date(2026, 10, 1), date(2026, 12, 31))),
            (Granularity.YEAR, (date(2026, 1, 1), date(2026, 12, 31))),
        ],
    )
    def test_the_window_containing_a_day(
        self, granularity: Granularity, expected: tuple[date, date]
    ) -> None:
        assert window(granularity, date(2026, 11, 18)) == expected

    def test_a_year_follows_the_financial_year(self) -> None:
        assert window(Granularity.YEAR, date(2026, 3, 1), year_starts=7) == (
            date(2025, 7, 1),
            date(2026, 6, 30),
        )

    def test_a_custom_range_needs_both_ends_in_order(self) -> None:
        with pytest.raises(ValidationError):
            window(Granularity.CUSTOM, date(2026, 1, 1))
        with pytest.raises(ValidationError):
            window(
                Granularity.CUSTOM, date(2026, 1, 1), start=date(2026, 2, 1), end=date(2026, 1, 1)
            )

    def test_the_prior_month_is_the_calendar_month_before(self) -> None:
        assert comparison_window(
            Comparison.PRIOR, date(2026, 3, 1), date(2026, 3, 31), Granularity.MONTH
        ) == (date(2026, 2, 1), date(2026, 2, 28))

    def test_the_same_period_last_year(self) -> None:
        assert comparison_window(
            Comparison.YEAR, date(2028, 2, 1), date(2028, 2, 29), Granularity.MONTH
        ) == (date(2027, 2, 1), date(2027, 2, 28))

    def test_a_custom_range_is_compared_with_the_same_length_before(self) -> None:
        assert comparison_window(
            Comparison.PRIOR, date(2026, 1, 11), date(2026, 1, 20), Granularity.CUSTOM
        ) == (date(2026, 1, 1), date(2026, 1, 10))

    def test_a_budget_is_not_a_window(self) -> None:
        assert (
            comparison_window(
                Comparison.BUDGET, date(2026, 1, 1), date(2026, 1, 31), Granularity.MONTH
            )
            is None
        )

    def test_windows_are_named_as_a_reader_says_them(self) -> None:
        assert describe_window(date(2026, 11, 1), date(2026, 11, 30)) == "November 2026"
        assert describe_window(date(2026, 1, 1), date(2026, 3, 31)) == "January to March 2026"


def _table(rows: dict[str, str], total: str) -> Table:
    return Table(
        "t",
        "T",
        [Column("label", "Line"), Column("amount", "Amount", ColumnKind.MONEY, compare=True)],
        [Row(key, {"label": key, "amount": Decimal(value)}) for key, value in rows.items()],
        Row("total", {"label": "Total", "amount": Decimal(total)}, emphasis="total"),
    )


class TestComparison:
    def test_prior_change_and_percentage_sit_beside_the_figure(self) -> None:
        compared = compare_table(
            _table({"a": "150"}, "150"), _table({"a": "100"}, "100"), Comparison.PRIOR
        )
        assert [column.key for column in compared.columns] == [
            "label",
            "amount",
            "amount__prior",
            "amount__change",
            "amount__pct",
        ]
        cells = compared.rows[0].cells
        assert cells["amount__prior"] == Decimal("100.00")
        assert cells["amount__change"] == Decimal("50.00")
        assert cells["amount__pct"] == Decimal("50.0")
        assert compared.total is not None and compared.total.cells["amount__change"] == Decimal(
            "50.00"
        )

    def test_a_line_found_only_before_is_kept_at_nothing(self) -> None:
        compared = compare_table(
            _table({"a": "10"}, "10"), _table({"a": "5", "b": "7"}, "12"), Comparison.PRIOR
        )
        gone = next(row for row in compared.rows if row.key == "b")
        assert gone.cells["amount"] == Decimal("0.00")
        assert gone.cells["amount__prior"] == Decimal("7.00")

    def test_no_percentage_across_a_change_of_sign_or_from_nothing(self) -> None:
        assert change(Decimal(5), Decimal(0)) is None
        assert change(Decimal(5), Decimal(-5)) is None
        assert change(Decimal(-10), Decimal(-5)) == Decimal("-100.0")


def _report() -> Report:
    return Report(
        id="sales",
        title="Sales",
        header=Header(
            title="Sales",
            starts_on=date(2026, 11, 1),
            ends_on=date(2026, 11, 30),
            currency="GHS",
            basis="Operational records",
            generated_at=datetime(2026, 11, 21, 9, 0, tzinfo=UTC),
            generated_by="N. Nickland",
            business="Nicholas Nickland Enterprise",
            filters=[("Grouped by", "Customer")],
        ),
        sections=[
            Section(
                "sales",
                "Sales",
                figures=[Figure("revenue", "Net sales", Decimal("1234567.005"))],
                tables=[
                    Table(
                        "by_group",
                        "Sales",
                        [
                            Column("label", "Customer"),
                            Column("quantity", "Quantity", ColumnKind.QUANTITY),
                            Column("revenue", "Net sales", ColumnKind.MONEY),
                            Column("cost", "Cost", ColumnKind.MONEY, sensitive=True),
                            Column("share", "Share", ColumnKind.PERCENT),
                            Column("on", "Last sale", ColumnKind.DATE),
                        ],
                        [
                            Row(
                                "a",
                                {
                                    "label": "Korle Bu",
                                    "quantity": Decimal("12.500000"),
                                    "revenue": Decimal("-1500.5"),
                                    "cost": Decimal("900"),
                                    "share": Decimal("62.5"),
                                    "on": date(2026, 11, 5),
                                },
                            ),
                        ],
                    )
                ],
            )
        ],
    )


class TestPresentation:
    def test_every_figure_is_rounded_once(self) -> None:
        report = normalise(_report())
        row = report.sections[0].tables[0].rows[0]
        assert row.cells["revenue"] == Decimal("-1500.50")
        assert row.cells["quantity"] == Decimal("12.5")
        assert report.sections[0].figures[0].value == Decimal("1234567.01")

    def test_figures_are_written_as_the_screen_writes_them(self) -> None:
        assert present(Decimal("-1500.50"), ColumnKind.MONEY) == "(1 500.50)"
        assert present(Decimal("1234567.01"), ColumnKind.MONEY) == "1 234 567.01"
        assert present(Decimal("12.500"), ColumnKind.QUANTITY) == "12.5"
        assert present(Decimal("62.5"), ColumnKind.PERCENT) == "62.5%"
        assert present(1200, ColumnKind.COUNT) == "1 200"
        assert present(date(2026, 11, 5), ColumnKind.DATE) == "05 Nov 2026"

    def test_cost_is_withheld_from_those_who_may_not_see_it(self) -> None:
        report = withhold(_report())
        table = report.sections[0].tables[0]
        assert "cost" not in {column.key for column in table.columns}
        assert "cost" not in table.rows[0].cells
        assert report.withheld

    def test_the_record_survives_a_round_trip(self) -> None:
        record = to_record(normalise(_report()))
        assert to_record(from_record(record)) == record


class TestExports:
    def test_csv_carries_the_exact_figures(self) -> None:
        report = normalise(_report())
        rows = list(csv.reader(io.StringIO(to_csv(report).decode("utf-8-sig"))))
        assert ["Currency", "GHS"] in rows
        assert ["Korle Bu", "12.5", "-1500.50", "900.00", "62.5", "2026-11-05"] in rows

    def test_excel_carries_the_exact_figures_as_numbers(self) -> None:
        report = normalise(_report())
        with zipfile.ZipFile(io.BytesIO(to_xlsx(report))) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
            assert "xl/styles.xml" in book.namelist()
        values = set(re.findall(r"<v>([^<]+)</v>", sheet))
        assert {"-1500.50", "12.5", "900.00", "62.5"} <= values

    def test_the_pdf_writes_what_the_screen_shows(self) -> None:
        markup = to_html(normalise(_report()))
        for text in (
            "(1 500.50)",
            "12.5",
            "62.5%",
            "05 Nov 2026",
            "Operational records",
            "November 2026",
            "Grouped by",
        ):
            assert text in markup


class TestSchedules:
    def test_a_monthly_schedule_runs_on_its_day(self) -> None:
        after = datetime(2026, 11, 21, 10, 0)
        assert next_run(Frequency.MONTHLY, 1, 7, after) == datetime(2026, 12, 1, 7, 0)
        assert next_run(Frequency.MONTHLY, 25, 7, after) == datetime(2026, 11, 25, 7, 0)

    def test_weekly_and_daily(self) -> None:
        saturday = datetime(2026, 11, 21, 10, 0)
        assert next_run(Frequency.WEEKLY, 1, 6, saturday) == datetime(2026, 11, 23, 6, 0)
        assert next_run(Frequency.DAILY, 1, 6, saturday) == datetime(2026, 11, 22, 6, 0)
        assert next_run(Frequency.DAILY, 1, 23, saturday) == datetime(2026, 11, 21, 23, 0)

    def test_a_run_reports_the_period_just_ended(self) -> None:
        assert reported_window(Frequency.MONTHLY, date(2026, 12, 1)) == date(2026, 11, 30)
        assert reported_window(Frequency.WEEKLY, date(2026, 11, 23)) == date(2026, 11, 22)
        assert reported_window(Frequency.DAILY, date(2026, 11, 23)) == date(2026, 11, 22)

    def test_impossible_days_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            next_run(Frequency.MONTHLY, 31, 7, datetime(2026, 1, 1))
        with pytest.raises(ValidationError):
            next_run(Frequency.WEEKLY, 8, 7, datetime(2026, 1, 1))
