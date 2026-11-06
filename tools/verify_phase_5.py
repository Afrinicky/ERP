#!/usr/bin/env python3
"""Verify each Phase 5 exit criterion from docs/00-build-plan.md.

Runs against a throwaway database, so it may be run at any time:

    .venv/bin/python tools/verify_phase_5.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="nickland-phase5-"))
os.environ["NICKLAND_DATA_DIRECTORY"] = str(WORKSPACE)
os.environ["NICKLAND_ENVIRONMENT"] = "test"
os.environ["NICKLAND_LOG_LEVEL"] = "ERROR"

from sqlalchemy import select, text  # noqa: E402

from nickland.application.context import Actor  # noqa: E402
from nickland.application.services.catalogue import (  # noqa: E402
    CatalogueService,
    ProductDraft,
)
from nickland.application.services.identity import IdentityService  # noqa: E402
from nickland.application.services.parties import PartyDraft, PartyService  # noqa: E402
from nickland.application.services.pricing_policy import (  # noqa: E402
    PricingPolicyService,
)
from nickland.application.services.quotations import (  # noqa: E402
    ExpenseDraft,
    LineDraft,
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.setup import SetupRequest, SetupService  # noqa: E402
from nickland.application.services.sourcing import (  # noqa: E402
    RequestDraft,
    RequestLineDraft,
    SourcingService,
    SupplierQuotationDraft,
    SupplierQuotationLineDraft,
)
from nickland.application.services.units import UnitService  # noqa: E402
from nickland.domain.errors import ApprovalRequiredError  # noqa: E402
from nickland.domain.money import ZERO, money, present  # noqa: E402
from nickland.domain.pricing import (  # noqa: E402
    CONFIDENTIAL_FIELDS,
    MarginMethod,
    PolicyScope,
)
from nickland.persistence.base import Base  # noqa: E402
from nickland.persistence.engine import (  # noqa: E402
    create_database_engine,
    create_session_factory,
)
from nickland.persistence.models import Business, Warehouse  # noqa: E402
from nickland.persistence.pricing_models import (  # noqa: E402
    QuotationLineCost,
    SupplierQuotationLine,
)
from nickland.platform.config import get_settings  # noqa: E402

ADMIN = "Provision9Admin"
D = Decimal
Report = tuple[str, bool, str]


def build() -> tuple[object, Actor, dict[str, uuid.UUID]]:
    """A provisioned business with a catalogue, three suppliers and a referrer."""
    settings = get_settings()
    settings.ensure_directories()
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase-5')"))

    session = create_session_factory(engine)()
    SetupService(session).provision(
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
    actor = IdentityService(session).sign_in("proprietor", ADMIN, business_id=business_id).actor

    parties = PartyService(session)
    world: dict[str, uuid.UUID] = {
        "customer": parties.create(actor, PartyDraft(name="Banhart Hospital", is_customer=True)).id,
        "alpha": parties.create(actor, PartyDraft(name="Alpha Diagnostics", is_supplier=True)).id,
        "beta": parties.create(actor, PartyDraft(name="Beta Medical", is_supplier=True)).id,
        "gamma": parties.create(actor, PartyDraft(name="Gamma Labs", is_supplier=True)).id,
        "referrer": parties.create(actor, PartyDraft(name="K. Mensah", is_affiliate=True)).id,
    }
    parties.set_affiliate_profile(
        actor,
        party_id=world["referrer"],
        scope="all_eligible",
        method="percentage",
        rate=D("0.05"),
    )

    unit = UnitService(session).units(actor)[0]
    catalogue = CatalogueService(session)
    for key, sku, name, weight, volume in (
        ("analyser", "ANL-001", "Analyser", "18", "0.21"),
        ("reagent", "RGT-002", "Reagent", "2", "0.01"),
        ("gloves", "GLV-003", "Gloves", "0.5", "0.004"),
    ):
        world[key] = catalogue.create_product(
            actor,
            ProductDraft(
                sku=sku,
                name=name,
                stock_unit_id=unit.id,
                weight_kg=D(weight),
                volume_m3=D(volume),
            ),
        ).id

    policy = PricingPolicyService(session)
    policy.set_margin_policy(
        actor, scope=PolicyScope.BUSINESS, method=MarginMethod.MARKUP_ON_COST, rate=D("0.30")
    )
    policy.set_margin_policy(
        actor,
        scope=PolicyScope.PRODUCT,
        method=MarginMethod.TARGET_GROSS_MARGIN,
        rate=D("0.25"),
        product_id=world["analyser"],
    )
    world["warehouse"] = session.scalar(select(Warehouse.id))  # type: ignore[assignment]
    session.commit()
    return session, actor, world


def verify() -> list[Report]:
    outcomes: list[Report] = []

    def record(label: str, passed: bool, detail: str = "") -> None:
        outcomes.append((label, passed, detail))

    session, actor, world = build()
    sourcing = SourcingService(session)  # type: ignore[arg-type]
    quotations = QuotationService(session)  # type: ignore[arg-type]
    policy = PricingPolicyService(session)  # type: ignore[arg-type]

    # The customer asks, and three suppliers answer.
    request = sourcing.create_request(
        actor,
        RequestDraft(
            customer_id=world["customer"],
            customer_reference="BH/2026/114",
            lines=[
                RequestLineDraft(
                    description="Analyser", quantity=D("2"), product_id=world["analyser"]
                ),
                RequestLineDraft(
                    description="Reagent", quantity=D("40"), product_id=world["reagent"]
                ),
                RequestLineDraft(
                    description="Gloves", quantity=D("100"), product_id=world["gloves"]
                ),
            ],
        ),
    )
    sourcing.set_request_state(actor, request.id, "open")
    for supplier, prices, lead in (
        ("alpha", {"Analyser": "9500", "Reagent": "180", "Gloves": "9"}, 21),
        ("beta", {"Analyser": "9300", "Reagent": "195", "Gloves": "8.5"}, 35),
        ("gamma", {"Analyser": "9800", "Reagent": "172", "Gloves": "7.9"}, 14),
    ):
        lines = {line.description: line for line in request.lines}
        sourcing.record_supplier_quotation(
            actor,
            SupplierQuotationDraft(
                supplier_id=world[supplier],
                reference=f"{supplier}-Q1",
                request_id=request.id,
                lead_time_days=lead,
                freight_amount=D("600"),
                lines=[
                    SupplierQuotationLineDraft(
                        description=name,
                        quantity_offered=lines[name].quantity,
                        unit_cost=D(price),
                        request_line_id=lines[name].id,
                        product_id=lines[name].product_fk,
                        lead_time_days=lead,
                    )
                    for name, price in prices.items()
                ],
            ),
        )
    session.commit()
    offers = {
        item.description: {
            offer.supplier_name: offer.supplier_quotation_line_id for offer in item.offers
        }
        for item in sourcing.compare(actor, request.id)
    }

    # --- Criterion 1 ------------------------------------------------------------------
    # One quotation sources lines from stock and from three suppliers, and each line
    # retains its source and cost internally while the customer document shows one
    # coherent offer.
    quotation = quotations.create(
        actor,
        QuotationDraft(
            customer_id=world["customer"],
            request_id=request.id,
            valid_until=date(2026, 12, 31),
            affiliate_id=world["referrer"],
            commission_scope="all_eligible",
            document_discount_value=D("0.02"),
            lines=[
                LineDraft(
                    description="Analyser",
                    quantity=D("2"),
                    product_id=world["analyser"],
                    sources=[
                        SourceDraft(
                            kind="supplier",
                            quantity=D("1"),
                            supplier_quotation_line_id=offers["Analyser"]["Beta Medical"],
                        ),
                        SourceDraft(
                            kind="stock",
                            quantity=D("1"),
                            unit_cost=D("8900"),
                            warehouse_id=world["warehouse"],
                        ),
                    ],
                ),
                LineDraft(
                    description="Reagent",
                    quantity=D("40"),
                    product_id=world["reagent"],
                    sources=[
                        SourceDraft(
                            kind="supplier",
                            quantity=D("40"),
                            supplier_quotation_line_id=offers["Reagent"]["Gamma Labs"],
                        )
                    ],
                ),
                LineDraft(
                    description="Gloves",
                    quantity=D("100"),
                    product_id=world["gloves"],
                    sources=[
                        SourceDraft(
                            kind="supplier",
                            quantity=D("100"),
                            supplier_quotation_line_id=offers["Gloves"]["Alpha Diagnostics"],
                        )
                    ],
                ),
            ],
            expenses=[
                ExpenseDraft(category="transport", amount=D("1200"), method="by_weight"),
                ExpenseDraft(category="clearing", amount=D("450"), method="by_value"),
                ExpenseDraft(category="insurance", amount=D("300"), method="equal_share"),
            ],
        ),
    )
    session.commit()

    suppliers = {
        source.supplier_fk
        for line in quotation.lines
        for source in line.sources
        if source.supplier_fk is not None
    }
    kinds = {source.kind for line in quotation.lines for source in line.sources}
    offer = quotations.offer(actor, quotation.id)
    record(
        "One quotation sources from stock and three suppliers",
        kinds == {"stock", "supplier"} and len(suppliers) == 3 and len(offer["lines"]) == 3,
        f"{len(suppliers)} suppliers and own stock across {len(offer['lines'])} customer lines",
    )

    # --- Criterion 2 ------------------------------------------------------------------
    # The cost build-up is reproducible from stored inputs and every component is
    # separately visible.
    first = quotations.costing(actor, quotation.id).build_up
    second = quotations.costing(actor, quotation.id).build_up
    components = {label for line in first.lines for _, label, _ in line.steps()}
    required = {
        "Supplier cost",
        "Allocated costs",
        "Landed cost",
        "Affiliate commission",
        "List price",
        "Discount",
        "Net price",
    }
    record(
        "The build-up is reproducible and every component is visible",
        first.lines == second.lines and required <= components,
        f"{len(components)} named components, identical across two evaluations",
    )

    # Every allocated share is recorded against its line and sums to the expense.
    shares = list(
        session.scalars(  # type: ignore[attr-defined]
            select(QuotationLineCost).where(QuotationLineCost.quotation_fk == quotation.id)
        )
    )
    by_category: dict[str, Decimal] = {}
    for row in shares:
        by_category[row.category] = money(by_category.get(row.category, ZERO) + row.amount)
    record(
        "Each allocated cost traces to its line and sums to the expense",
        by_category
        == {
            "transport": D("1200.000000"),
            "clearing": D("450.000000"),
            "insurance": D("300.000000"),
        },
        ", ".join(f"{name} {amount}" for name, amount in sorted(by_category.items())),
    )

    # --- Criterion 3 ------------------------------------------------------------------
    # Commission applies only within its configured scope.
    scoped = quotations.create(
        actor,
        QuotationDraft(
            customer_id=world["customer"],
            affiliate_id=world["referrer"],
            commission_scope="selected_lines",
            lines=[
                LineDraft(
                    description="Chosen",
                    quantity=D("1"),
                    unit_cost=D("1000"),
                    commission_selected=True,
                ),
                LineDraft(description="Not chosen", quantity=D("1"), unit_cost=D("1000")),
            ],
        ),
    )
    session.commit()
    chosen, excluded = scoped.lines
    record(
        "Commission applies only within its configured scope",
        chosen.commission_amount > ZERO and excluded.commission_amount == ZERO,
        f"in scope {chosen.commission_amount}; out of scope {excluded.commission_amount} "
        f"({excluded.commission_reason})",
    )

    # The gross-up preserves the business's margin exactly.
    inclusive = money(chosen.landed_cost + chosen.margin_amount)
    record(
        "The commission gross-up preserves the margin",
        money(chosen.list_price - chosen.commission_amount) == present(inclusive, 4),
        f"list {chosen.list_price} less commission {chosen.commission_amount} "
        f"is the margin-inclusive {present(inclusive, 4)}",
    )

    # --- Criterion 4 ------------------------------------------------------------------
    # Instalment pricing follows the selected duration and displays deposit,
    # instalment amount, frequency, first and final due dates and total payable.
    plan = policy.plans(actor)[0]
    instalment = quotations.create(
        actor,
        QuotationDraft(
            customer_id=world["customer"],
            payment_plan="instalment",
            instalment_plan_id=plan.id,
            instalment_months=6,
            first_instalment_on=date(2026, 12, 1),
            lines=[LineDraft(description="Reagent", quantity=D("40"), unit_cost=D("172"))],
        ),
    )
    session.commit()
    quotations.issue(actor, instalment.id)
    session.commit()
    schedule = quotations.schedule(actor, instalment.id)
    deposits = [row for row in schedule if row.is_deposit]
    payments = [row for row in schedule if not row.is_deposit]
    disclosed = (
        len(deposits) == 1
        and len(payments) == 6
        and payments[0].due_on == date(2026, 12, 1)
        and payments[-1].due_on == date(2027, 5, 1)
        and instalment.finance_charge_total > ZERO
        and money(sum((row.amount for row in schedule), ZERO)) == instalment.total_payable
    )
    record(
        "Instalment pricing follows the duration and discloses the schedule",
        disclosed,
        f"deposit {deposits[0].amount}, {len(payments)} payments of about "
        f"{payments[0].amount}, {payments[0].due_on} to {payments[-1].due_on}, "
        f"charge {instalment.finance_charge_total}",
    )

    # A different band charges a different amount.
    charges: dict[int, Decimal] = {}
    for months in (3, 12):
        other = quotations.create(
            actor,
            QuotationDraft(
                customer_id=world["customer"],
                payment_plan="instalment",
                instalment_plan_id=plan.id,
                instalment_months=months,
                first_instalment_on=date(2026, 12, 1),
                lines=[LineDraft(description="Reagent", quantity=D("1"), unit_cost=D("1000"))],
            ),
        )
        charges[months] = other.finance_charge_total
    session.commit()
    record(
        "A longer repayment duration costs more",
        charges[3] < charges[12],
        f"three months {charges[3]}; twelve months {charges[12]}",
    )

    # --- Criterion 5 ------------------------------------------------------------------
    # A supplier price change after issue does not alter the issued quotation, and the
    # resulting variance is reported before procurement.
    quotations.issue(actor, quotation.id)
    session.commit()
    issued_total = quotation.total_payable
    snapshot = quotations.snapshots(actor, quotation.id)[0]

    for row in session.scalars(select(SupplierQuotationLine)):  # type: ignore[attr-defined]
        row.unit_cost = row.unit_cost * D("1.4")
    session.commit()

    unchanged = quotations.require(
        actor, quotation.id
    ).total_payable == issued_total and snapshot.payload["totals"]["total_payable"] == str(
        issued_total
    )
    record(
        "A supplier price change after issue leaves the quotation unchanged",
        unchanged,
        f"issued {issued_total}; still {quotations.require(actor, quotation.id).total_payable} "
        f"after a 40% supplier increase",
    )

    line = quotation.lines[0]
    estimated = line.landed_cost
    quotations.record_actual_cost(actor, line.id, actual_unit_cost=D("13020"), reference="SIN-0001")
    session.commit()
    variance = quotations.costing(actor, quotation.id).variances[0]
    record(
        "The variance is reported before procurement proceeds",
        variance.is_known and variance.is_adverse and variance.estimated == estimated,
        f"estimated {variance.estimated}, actual {variance.actual}, "
        f"variance {variance.variance} ({variance.variance_rate})",
    )

    # A revision supersedes the original, which keeps its figures and its snapshot.
    revision = quotations.revise(actor, quotation.id, reason="The customer increased the order.")
    session.commit()
    record(
        "An amendment is a revision, and the original stays on the record",
        revision.revision == 2
        and revision.number == quotation.number
        and quotations.require(actor, quotation.id).state == "superseded"
        and quotations.require(actor, quotation.id).total_payable == issued_total,
        f"{revision.number} revision {revision.revision} supersedes revision 1",
    )

    # --- Criterion 6 ------------------------------------------------------------------
    # Pricing is exact to four decimal places with no binary floating-point anywhere.
    exact = all(
        isinstance(value, Decimal)
        for line in quotation.lines
        for value in (
            line.unit_cost,
            line.landed_cost,
            line.margin_amount,
            line.commission_amount,
            line.list_price,
            line.net_price,
            line.payable_before_tax,
            line.total_payable,
        )
    )
    reconciles = (
        money(sum((item.total_payable for item in quotation.lines), ZERO))
        == quotation.total_payable
    )
    record(
        "Every stored figure is an exact decimal and the totals reconcile",
        exact and reconciles,
        f"{len(quotation.lines)} lines summing exactly to {quotation.total_payable}",
    )

    # --- Confidentiality --------------------------------------------------------------
    # PRD 14: the customer-facing projection carries no cost, margin or commission.
    rendered = str(quotations.offer(actor, quotation.id))
    leaked = sorted(field for field in CONFIDENTIAL_FIELDS if field in rendered)
    record(
        "The customer-facing offer discloses no cost, margin or commission",
        not leaked,
        f"{len(CONFIDENTIAL_FIELDS)} confidential fields, none present in the offer",
    )

    # --- Approval ----------------------------------------------------------------------
    # A discount beyond the actor's limit places the quotation in pending approval and
    # it cannot be issued until approved.
    policy.set_discount_limit(actor, role_code="sales_person", maximum_rate=D("0.05"))
    session.commit()
    seller = Actor(
        user_id=uuid.uuid4(),
        username="seller",
        full_name="Sales Person",
        business_id=actor.business_id,
        branch_id=actor.branch_id,
        permissions=frozenset(
            {
                "quotation.manage",
                "quotation.view",
                "quotation.issue",
                "costing.view",
                "product.view",
                "customer.view",
                "tax.view",
                "business.view",
            }
        ),
        role_codes=frozenset({"sales_person"}),
    )
    discounted = quotations.create(
        seller,
        QuotationDraft(
            customer_id=world["customer"],
            lines=[
                LineDraft(
                    description="Reagent",
                    quantity=D("10"),
                    unit_cost=D("100"),
                    discount_value=D("0.2"),
                )
            ],
        ),
    )
    session.commit()
    blocked = False
    try:
        quotations.issue(seller, discounted.id)
    except ApprovalRequiredError:
        blocked = True
    quotations.submit_for_approval(seller, discounted.id)
    quotations.approve(actor, discounted.id)
    quotations.issue(seller, discounted.id)
    session.commit()
    record(
        "A discount beyond the limit cannot be issued until approved",
        blocked and quotations.require(actor, discounted.id).state == "issued",
        "refused at 20% against a 5% limit, then issued once approved",
    )

    session.close()  # type: ignore[attr-defined]
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
    print("Every Phase 5 exit criterion is met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
