# ruff: noqa: F811 — the fixtures are imported from the modules that define them
"""Margin erosion between quotation and outcome, attributed to named causes. PRD §29.8."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.profitability import ProfitabilityService
from nickland.application.services.receivables import (
    CreditDraft,
    CreditLineDraft,
    ReceivablesService,
)
from nickland.domain.planning import ErosionCause
from nickland.platform.clock import FixedClock
from tests.integration.test_receivables import bill_ordered, world  # noqa: F401

D = Decimal
START, END = date(2026, 1, 1), date(2026, 12, 31)


class TestErosion:
    def test_the_causes_sum_exactly_to_the_difference(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        world: dict,
    ) -> None:
        receivables = ReceivablesService(db, clock=clock)
        invoice = bill_ordered(receivables, admin, world)
        # The reagent cost more than quoted; one glove box came back.
        world["reagent"].actual_unit_cost = D("320")
        world["gloves"].actual_unit_cost = D("20")
        glove_line = next(
            line for line in invoice.lines if line.product_fk == world["gloves"].product_fk
        )
        receivables.credit(
            admin,
            CreditDraft(
                invoice_id=invoice.id,
                reason="One box damaged",
                lines=[CreditLineDraft(invoice_line_id=glove_line.id, quantity=D("1"))],
            ),
        )
        db.flush()
        report = ProfitabilityService(db).erosion(admin, start=START, end=END)
        assert len(report.rows) == 2 and report.pending == 0
        for row in report.rows:
            assert sum(row.causes.values(), D(0)) == row.difference
        reagent = next(row for row in report.rows if row.order_line_id == world["reagent"].id)
        assert reagent.causes[ErosionCause.SUPPLIER_COST] == D("-120.00")  # 6 x (300 - 320)
        gloves = next(row for row in report.rows if row.order_line_id == world["gloves"].id)
        assert gloves.causes[ErosionCause.RETURNS] < D("0")
        assert report.bases["quoted"].startswith("Quotation estimate")

    def test_a_line_without_its_actual_cost_is_marked_incomplete(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        world: dict,
    ) -> None:
        bill_ordered(ReceivablesService(db, clock=clock), admin, world)
        report = ProfitabilityService(db).erosion(admin, start=START, end=END)
        assert report.pending == 2
        assert all(not row.complete for row in report.rows)
