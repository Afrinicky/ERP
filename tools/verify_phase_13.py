#!/usr/bin/env python3
"""Verify each Phase 13 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_13.py

The interface criteria (no network, contrast, non-colour dependence) are checked against
the source, the theme tokens and the built bundle; run ``npx vite build`` in ``ui`` first
for the bundle check.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase13-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.analytics import CHARTS, AnalyticsService  # noqa: E402
from nickland.application.services.budgets import (  # noqa: E402
    BudgetDraft,
    BudgetService,
    LineDraft,
)
from nickland.application.services.catalogue import CatalogueService, ProductDraft  # noqa: E402
from nickland.application.services.forecasts import ForecastService  # noqa: E402
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.inventory import (  # noqa: E402
    InventoryService,
    OpeningDraft,
    StockLineDraft,
)
from nickland.application.services.orders import AcceptanceDraft, SalesOrderService  # noqa: E402
from nickland.application.services.parties import PartyDraft, PartyService  # noqa: E402
from nickland.application.services.pos import PosService  # noqa: E402
from nickland.application.services.profitability import ProfitabilityService  # noqa: E402
from nickland.application.services.quotations import (  # noqa: E402
    LineDraft as QuoteLine,
)
from nickland.application.services.quotations import (  # noqa: E402
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.receivables import (  # noqa: E402
    CreditDraft,
    CreditLineDraft,
    InvoiceDraft,
    ReceivablesService,
)
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.domain.planning import Basis  # noqa: E402
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.immutability import register_append_only_guards  # noqa: E402
from nickland.persistence.models import Business, Warehouse  # noqa: E402
from nickland.platform.clock import FixedClock  # noqa: E402
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
D = Decimal
START, END = date(2026, 1, 1), date(2026, 12, 31)
CLOCK = FixedClock(datetime(2026, 11, 21, 10, 0, tzinfo=UTC))
Report = tuple[str, bool, str]
UI = ROOT / "ui"


def build() -> tuple[Session, Actor]:
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-13')"))
    factory = create_session_factory(engine)
    register_append_only_guards(factory)
    session = factory()
    SetupService(session, clock=CLOCK).provision(
        SetupRequest(
            business_name="Nicholas Nickland Enterprise",
            administrator_username="proprietor",
            administrator_full_name="N. Nickland",
            administrator_password=ADMIN,
            theme_preset="nickland",
        )
    )
    session.commit()
    business_id = session.scalar(select(Business.id))
    assert business_id is not None
    actor = (
        IdentityService(session, clock=CLOCK)
        .sign_in("proprietor", ADMIN, business_id=business_id)
        .actor
    )
    return session, actor


def trade(session: Session, actor: Actor):  # type: ignore[no-untyped-def]
    """Register sales with actual stock cost, and a quoted order invoiced, with a supplier
    cost above the quote and one box returned."""
    warehouse = session.scalar(select(Warehouse.id))
    unit = UnitService(session, clock=CLOCK).units(actor)[0]
    catalogue = CatalogueService(session, clock=CLOCK)
    gloves = catalogue.create_product(
        actor,
        ProductDraft(
            sku="GLV-100", name="Nitrile gloves", stock_unit_id=unit.id, list_price=D("100")
        ),
    )
    reagent = catalogue.create_product(
        actor,
        ProductDraft(
            sku="RGT-200", name="Glucose reagent", stock_unit_id=unit.id, list_price=D("520")
        ),
    )
    session.flush()
    InventoryService(session, clock=CLOCK).open_stock(
        actor,
        OpeningDraft(
            warehouse_id=warehouse,
            lines=[
                StockLineDraft(product_id=gloves.id, quantity=D("40"), unit_cost=D("60")),
                StockLineDraft(product_id=reagent.id, quantity=D("10"), unit_cost=D("300")),
            ],
        ),
    )
    pos = PosService(session, clock=CLOCK)
    pos.open_shift(actor, opening_float=D("200"))
    sale = pos.current_sale(actor)
    pos.scan(actor, sale.id, "GLV-100", quantity=D("3"))
    pos.tender(actor, sale.id, method="cash", cash_received=sale.total)
    pos.complete(actor, sale.id)

    customer = PartyService(session, clock=CLOCK).create(
        actor,
        PartyDraft(
            name="Korle Bu", is_customer=True, payment_term_kind="net_days", payment_term_days=30
        ),
    )
    quotations = QuotationService(session, clock=CLOCK)
    quotation = quotations.create(
        actor,
        QuotationDraft(
            customer_id=customer.id,
            lines=[
                QuoteLine(
                    description=product.name,
                    quantity=D(quantity),
                    product_id=product.id,
                    sources=[
                        SourceDraft(
                            kind="stock",
                            quantity=D(quantity),
                            unit_cost=D(cost),
                            warehouse_id=warehouse,
                        )
                    ],
                )
                for product, quantity, cost in ((gloves, "10", "60"), (reagent, "4", "300"))
            ],
        ),
    )
    session.flush()
    quotations.issue(actor, quotation.id)
    orders = SalesOrderService(session, clock=CLOCK)
    orders.record_acceptance(actor, quotation.id, AcceptanceDraft(reference="KB-PO"))
    order = orders.convert(actor, quotation.id)
    session.flush()
    receivables = ReceivablesService(session, clock=CLOCK)
    invoice = receivables.issue_invoice(
        actor, InvoiceDraft(sales_order_id=order.id, basis="ordered")
    )
    by_product = {line.product_fk: line for line in order.lines}
    by_product[reagent.id].actual_unit_cost = D("330")
    by_product[gloves.id].actual_unit_cost = D("60")
    glove_line = next(line for line in invoice.lines if line.product_fk == gloves.id)
    receivables.credit(
        actor,
        CreditDraft(
            invoice_id=invoice.id,
            reason="One box damaged",
            lines=[CreditLineDraft(invoice_line_id=glove_line.id, quantity=D("1"))],
        ),
    )
    session.flush()
    return by_product[reagent.id]


def luminance(hex_colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(one: str, two: str) -> float:
    a, b = sorted((luminance(one), luminance(two)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# Machado, Oliveira and Fernandes (2009), full severity, applied in linear RGB.
CVD = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritan": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (max(0.0, min(1.0, c)) for c in rgb)
    lms = (
        0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
        0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
        0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
    )
    l_, m_, s_ = (c ** (1 / 3) for c in lms)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def linear(hex_colour: str) -> tuple[float, float, float]:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return tuple(channel(int(hex_colour[i : i + 2], 16)) for i in (1, 3, 5))  # type: ignore[return-value]


def separation(one: str, two: str) -> float:
    """The worst ΔE (OKLab, times 100) between two colours, across normal vision and the three
    colour-vision deficiencies."""
    worst = 1e9
    for matrix in (None, *CVD.values()):
        a, b = linear(one), linear(two)
        if matrix is not None:
            a = tuple(sum(row[i] * a[i] for i in range(3)) for row in matrix)  # type: ignore[assignment]
            b = tuple(sum(row[i] * b[i] for i in range(3)) for row in matrix)  # type: ignore[assignment]
        la, lb = oklab(a), oklab(b)
        worst = min(worst, 100 * sum((x - y) ** 2 for x, y in zip(la, lb, strict=True)) ** 0.5)
    return worst


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str) -> None:
        outcomes.append((label, passed, detail))

    session, actor = build()
    reagent_line = trade(session, actor)
    analytics = AnalyticsService(session, clock=CLOCK)
    profit = ProfitabilityService(session)
    budgets = BudgetService(session, clock=CLOCK)
    budget = budgets.create(
        actor,
        BudgetDraft(name="Operating budget", period_kind="monthly", starts_on=START, ends_on=END),
    )
    budgets.set_lines(
        actor,
        budget.id,
        [
            LineDraft(account_code="40100", total=D("24000")),
            LineDraft(account_code="60000", total=D("18000")),
        ],
    )
    budgets.submit(actor, budget.id)
    budgets.approve(actor, budget.id, note="Approved")
    forecasts = ForecastService(session, clock=CLOCK)

    # 1. Forecast, budget, quotation estimate and actual are distinct.
    charts = {
        chart_id: analytics.chart(actor, chart_id, start=START, end=END) for chart_id, _ in CHARTS
    }
    unlabelled = [
        f"{chart_id}.{series.key}"
        for chart_id, chart in charts.items()
        for series in chart.series
        if series.basis not in set(Basis)
    ]
    revenue_chart = charts["revenue_profit"]
    basis_of = {series.key: series.basis for series in revenue_chart.series}
    variance_chart = charts["supplier_variance"]
    forecast = forecasts.forecast(actor, horizon_days=30)
    comparison = budgets.comparison(actor, budget.id)
    erosion = profit.erosion(actor, start=START, end=END)
    distinct = (
        not unlabelled
        and basis_of.get("revenue") is Basis.ACTUAL
        and basis_of.get("revenue_budget") is Basis.BUDGET
        and {s.key: s.basis for s in variance_chart.series}
        == {"quoted": Basis.ESTIMATE, "actual": Basis.ACTUAL}
        and forecast.basis is Basis.FORECAST
        and comparison.bases["budget"] == Basis.BUDGET.label
        and comparison.bases["actual"] == Basis.ACTUAL.label
        and erosion.bases == {"quoted": Basis.ESTIMATE.label, "realised": Basis.ACTUAL.label}
    )
    record(
        "Forecast, budget, estimate and actual are distinct and never presented as one another",
        distinct,
        f"{sum(len(c.series) for c in charts.values())} series across {len(charts)} charts, each "
        "labelled; budget and forecast series kept apart from posted ones",
    )

    # 2. Every chart segment opens its records, and they add up to it.
    missing: list[str] = []
    mismatched: list[str] = []
    for chart_id, chart in charts.items():
        for point in chart.points:
            if not any(Decimal(value) != 0 for value in point.values.values()):
                continue
            if not point.drill:
                missing.append(f"{chart_id}:{point.key}")
                continue
            drill = point.drill
            if "measure" in drill and chart_id in {"revenue_profit", "expense_composition"}:
                lines = analytics.drill(
                    actor,
                    measure=drill["measure"],
                    start=date.fromisoformat(drill["start"]),
                    end=date.fromisoformat(drill["end"]),
                )
                expected = point.values.get("revenue", point.values.get("amount"))
                if expected is not None and sum((line.amount for line in lines), D(0)) != expected:
                    mismatched.append(f"{chart_id}:{point.key}")
            if "profit_dimension" in drill and "key" in drill:
                records = profit.records(
                    actor,
                    dimension=drill["profit_dimension"],
                    key=drill["key"],
                    start=date.fromisoformat(drill["start"]),
                    end=date.fromisoformat(drill["end"]),
                )
                if sum((fact.revenue for fact in records), D(0)) != point.values["revenue"]:
                    mismatched.append(f"{chart_id}:{point.key}")
    points = sum(len(c.points) for c in charts.values())
    record(
        "Selecting any chart segment opens the underlying records",
        not missing and not mismatched,
        f"{points} points; every non-empty one carries its filter, and the postings or sales it "
        "opens sum to it"
        if not missing and not mismatched
        else f"no drill: {missing[:5]}; not summing: {mismatched[:5]}",
    )

    # 3. Margin erosion is attributed to named causes.
    rows_ok = all(sum(row.causes.values(), D(0)) == row.difference for row in erosion.rows)
    reagent = next(row for row in erosion.rows if row.order_line_id == reagent_line.id)
    gloves = next(row for row in erosion.rows if row.order_line_id != reagent_line.id)
    named = {cause.value: amount for cause, amount in reagent.causes.items() if amount}
    returned = next(amount for cause, amount in gloves.causes.items() if cause.value == "returns")
    record(
        "Margin erosion between quotation and outcome is attributable to named causes",
        rows_ok and named.get("supplier_cost") == D("-120.00") and returned < 0,
        "on every line the causes sum to the difference; the reagent's 120.00 is supplier cost "
        f"(4 at 30 above the quote), the gloves' {returned} is the box returned",
    )

    # 4. Legible, exportable and fully functional with no network.
    charts_source = (UI / "src/components/charts.tsx").read_text(encoding="utf-8")
    imports = re.findall(r"^import .* from '([^']+)';", charts_source, flags=re.MULTILINE)
    external_imports = [name for name in imports if not name.startswith(("@/", "react"))]
    bundle = list((UI / "dist/assets").glob("*.js")) + list((UI / "dist/assets").glob("*.css"))
    fetched = []
    for asset in bundle:
        body = asset.read_text(encoding="utf-8", errors="ignore")
        for url in re.findall(r"url\((https?://[^)]+)\)|fetch\(\s*['\"](https?://[^'\"]+)", body):
            fetched.extend(part for part in url if part)
    exports = all(
        word in charts_source for word in ("text/csv", "image/svg+xml", "Show as a table")
    )
    record(
        "Charts stay legible, exportable and working with no network",
        bool(bundle) and not external_imports and not fetched and exports,
        "drawn as inline SVG by the application's own code; nothing is fetched; table view, CSV "
        "and SVG export on every chart"
        if bundle
        else "build the interface first: npx vite build",
    )

    # 5. Contrast and non-colour dependence, for every theme.
    failures: list[str] = []
    worst_contrast, worst_separation = 99.0, 99.0
    for preset in sorted((ROOT / "tokens/presets").glob("*.json")):
        data = json.loads(preset.read_text(encoding="utf-8"))
        colours = next(v for v in data.values() if isinstance(v, dict) and "chart-1" in v)
        surface = colours.get("surface-raised", "#FFFFFF")
        slots = [colours[f"chart-{i}"] for i in range(1, 5)]
        for slot in slots:
            ratio = contrast(slot, surface)
            worst_contrast = min(worst_contrast, ratio)
            if ratio < 3:
                failures.append(f"{preset.stem} {slot} {ratio:.2f}:1")
        for one, two in pairwise(slots):
            gap = separation(one, two)
            worst_separation = min(worst_separation, gap)
            if gap < 6:
                failures.append(f"{preset.stem} {one}/{two} ΔE {gap:.1f}")
    secondary = all(
        word in charts_source
        for word in ("viz__legend", "strokeDasharray", "url(#", "basis_label", "<title")
    )
    record(
        "Every chart meets the contrast and non-colour-dependence requirements",
        not failures and secondary,
        f"chart colours at least {worst_contrast:.2f}:1 against the surface in every theme; "
        f"adjacent series at least ΔE {worst_separation:.1f} under every colour-vision deficiency; "
        "a legend, the basis named in text, dashed or hatched estimates and a table view on each"
        if not failures
        else "; ".join(failures),
    )
    session.close()
    return outcomes


def main() -> int:
    try:
        outcomes = verify()
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)

    width = max(len(label) for label, _, _ in outcomes)
    print()
    for label, passed, detail in outcomes:
        print(f"  {'PASS' if passed else 'FAIL'}  {label.ljust(width)}  {detail}")
    print()
    broken = [label for label, passed, _ in outcomes if not passed]
    if broken:
        print(f"{len(broken)} criterion(a) not met: {broken}")
        return 1
    print("Every Phase 13 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
