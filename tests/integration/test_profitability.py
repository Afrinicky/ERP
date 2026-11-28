# ruff: noqa: F811 — the fixtures are imported from the modules that define them
"""Profitability and analytics against a real database. PRD §20.7, §29.8.

Contribution is measured on what was realised, with the actual cost of the goods; a chart
point opens the records it is made of.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.analytics import CHARTS, AnalyticsService
from nickland.application.services.pos import PosService
from nickland.application.services.profitability import ProfitabilityService
from nickland.domain.planning import Basis
from nickland.platform.clock import FixedClock
from tests.integration.test_pos import pos, ring_up, shift, world  # noqa: F401

D = Decimal
START, END = date(2026, 1, 1), date(2026, 12, 31)


def sold(pos: PosService, admin: Actor, world: dict):  # type: ignore[no-untyped-def]
    sale = ring_up(pos, admin, world)
    pos.tender(admin, sale.id, method="cash", cash_received=D("310"))
    pos.complete(admin, sale.id)
    return sale


class TestProfitability:
    def test_contribution_uses_the_actual_cost_of_the_goods(
        self,
        db: Session,
        admin: Actor,
        pos: PosService,
        world: dict,
        shift,
    ) -> None:
        sold(pos, admin, world)
        made = ProfitabilityService(db).profitability(
            admin, dimension="product", start=START, end=END
        )
        assert made.basis is Basis.ACTUAL
        rows = {row.label: row for row in made.rows}
        gloves = rows["Nitrile gloves, box of 100"]
        assert (gloves.revenue, gloves.cost, gloves.gross) == (D("200"), D("120"), D("80"))
        assert made.total.gross == D("110")
        assert made.total.pending_lines == 0

    def test_every_dimension_partitions_the_same_total(
        self,
        db: Session,
        admin: Actor,
        pos: PosService,
        world: dict,
        shift,
    ) -> None:
        sold(pos, admin, world)
        service = ProfitabilityService(db)
        for dimension in (
            "product",
            "category",
            "order",
            "customer",
            "supplier",
            "branch",
            "salesperson",
            "affiliate",
            "terminal",
            "channel",
        ):
            made = service.profitability(admin, dimension=dimension, start=START, end=END)
            assert sum((row.revenue for row in made.rows), D(0)) == D("250"), dimension
        terminal = service.profitability(admin, dimension="terminal", start=START, end=END)
        assert [row.label for row in terminal.rows] == ["Register 1"]

    def test_a_row_opens_onto_its_sale_lines(
        self,
        db: Session,
        admin: Actor,
        pos: PosService,
        world: dict,
        shift,
    ) -> None:
        sale = sold(pos, admin, world)
        service = ProfitabilityService(db)
        row = service.profitability(admin, dimension="channel", start=START, end=END).rows[0]
        records = service.records(admin, dimension="channel", key=row.key, start=START, end=END)
        assert {record.source_id for record in records} == {sale.id}
        assert sum((record.revenue for record in records), D(0)) == row.revenue

    def test_allocated_costs_are_shown_apart(
        self,
        db: Session,
        admin: Actor,
        pos: PosService,
        world: dict,
        shift,
    ) -> None:
        sold(pos, admin, world)
        made = ProfitabilityService(db).profitability(
            admin, dimension="product", start=START, end=END, allocate=True
        )
        assert made.allocated
        assert made.total.after_commission == D("110")
        assert made.total.overheads == made.overhead_pool


class TestCharts:
    def test_every_chart_builds_and_labels_its_basis(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        pos: PosService,
        world: dict,
        shift,
    ) -> None:
        sold(pos, admin, world)
        analytics = AnalyticsService(db, clock=clock)
        for chart_id, _ in CHARTS:
            chart = analytics.chart(admin, chart_id, start=START, end=END)
            assert chart.series, chart_id
            assert all(series.basis in set(Basis) for series in chart.series)

    def test_a_point_opens_onto_the_postings_it_is_made_of(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        pos: PosService,
        world: dict,
        shift,
    ) -> None:
        sold(pos, admin, world)
        analytics = AnalyticsService(db, clock=clock)
        chart = analytics.chart(admin, "revenue_profit", start=START, end=END, compare=False)
        november = next(point for point in chart.points if point.key == "2026-11-01")
        assert november.values["revenue"] == D("250.00")
        drill = november.drill
        lines = analytics.drill(
            admin,
            measure=drill["measure"],
            start=date.fromisoformat(drill["start"]),
            end=date.fromisoformat(drill["end"]),
        )
        assert sum((line.amount for line in lines), D(0)) == D("250.00")

    def test_a_cost_estimate_is_never_shown_as_actual(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        pos: PosService,
        world: dict,
        shift,
    ) -> None:
        sold(pos, admin, world)
        chart = AnalyticsService(db, clock=clock).chart(
            admin, "supplier_variance", start=START, end=END
        )
        bases = {series.key: series.basis for series in chart.series}
        assert bases == {"quoted": Basis.ESTIMATE, "actual": Basis.ACTUAL}
