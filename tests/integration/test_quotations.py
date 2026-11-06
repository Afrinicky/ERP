"""Quotation, costing and pricing against a real database. PRD 6.2, 6.3, 6.4, 6.5."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.commissions import CommissionService
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.application.services.pricing_policy import PricingPolicyService
from nickland.application.services.quotations import (
    ExpenseDraft,
    LineDraft,
    QuotationDraft,
    QuotationService,
    SourceDraft,
)
from nickland.application.services.sourcing import (
    RequestDraft,
    RequestLineDraft,
    SourcingService,
    SupplierQuotationDraft,
    SupplierQuotationLineDraft,
)
from nickland.application.services.units import UnitService
from nickland.domain.errors import (
    ApprovalRequiredError,
    ConflictError,
    PermissionDeniedError,
    StateTransitionError,
    ValidationError,
)
from nickland.domain.money import ZERO
from nickland.domain.pricing import MarginMethod, PolicyScope
from nickland.persistence.pricing_models import (
    CommissionAccrual,
    QuotationLineCost,
    QuotationSnapshot,
)
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor

D = Decimal


# --------------------------------------------------------------------------------------
# A business with a catalogue, three suppliers and a referrer
# --------------------------------------------------------------------------------------


@pytest.fixture
def world(db: Session, clock: FixedClock, admin: Actor) -> dict[str, uuid.UUID]:
    """Everything a quotation needs behind it."""
    parties = PartyService(db, clock=clock)
    customer = parties.create(admin, PartyDraft(name="Banhart Hospital", is_customer=True))
    alpha = parties.create(admin, PartyDraft(name="Alpha Diagnostics", is_supplier=True))
    beta = parties.create(admin, PartyDraft(name="Beta Medical", is_supplier=True))
    gamma = parties.create(admin, PartyDraft(name="Gamma Labs", is_supplier=True))
    referrer = parties.create(admin, PartyDraft(name="K. Mensah", is_affiliate=True))
    parties.set_affiliate_profile(
        admin, party_id=referrer.id, scope="all_eligible", method="percentage", rate=D("0.05")
    )

    unit = UnitService(db, clock=clock).units(admin)[0]
    catalogue = CatalogueService(db, clock=clock)
    analyser = catalogue.create_product(
        admin,
        ProductDraft(
            sku="ANL-001",
            name="Analyser",
            stock_unit_id=unit.id,
            weight_kg=D("18"),
            volume_m3=D("0.21"),
        ),
    )
    reagent = catalogue.create_product(
        admin,
        ProductDraft(
            sku="RGT-002",
            name="Reagent",
            stock_unit_id=unit.id,
            weight_kg=D("2"),
            volume_m3=D("0.01"),
        ),
    )
    gloves = catalogue.create_product(
        admin,
        ProductDraft(
            sku="GLV-003",
            name="Gloves",
            stock_unit_id=unit.id,
            weight_kg=D("0.5"),
            volume_m3=D("0.004"),
        ),
    )
    db.flush()

    policy = PricingPolicyService(db, clock=clock)
    policy.set_margin_policy(
        admin, scope=PolicyScope.BUSINESS, method=MarginMethod.MARKUP_ON_COST, rate=D("0.30")
    )
    db.flush()

    return {
        "customer": customer.id,
        "alpha": alpha.id,
        "beta": beta.id,
        "gamma": gamma.id,
        "referrer": referrer.id,
        "analyser": analyser.id,
        "reagent": reagent.id,
        "gloves": gloves.id,
        "unit": unit.id,
    }


@pytest.fixture
def sourcing(db: Session, clock: FixedClock) -> SourcingService:
    return SourcingService(db, clock=clock)


@pytest.fixture
def quotations(db: Session, clock: FixedClock) -> QuotationService:
    return QuotationService(db, clock=clock)


@pytest.fixture
def policy(db: Session, clock: FixedClock) -> PricingPolicyService:
    return PricingPolicyService(db, clock=clock)


@pytest.fixture
def commissions(db: Session, clock: FixedClock) -> CommissionService:
    return CommissionService(db, clock=clock)


def _request(sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]) -> uuid.UUID:
    request = sourcing.create_request(
        admin,
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
    sourcing.set_request_state(admin, request.id, "open")
    return request.id


def _offers(
    sourcing: SourcingService,
    admin: Actor,
    world: dict[str, uuid.UUID],
    request_id: uuid.UUID,
) -> dict[str, dict[str, uuid.UUID]]:
    """Three suppliers answer every line. Returns line description to supplier to offer."""
    request = sourcing.require_request(admin, request_id)
    lines = {line.description: line for line in request.lines}
    for supplier, prices, lead in (
        ("alpha", {"Analyser": "9500", "Reagent": "180", "Gloves": "9"}, 21),
        ("beta", {"Analyser": "9300", "Reagent": "195", "Gloves": "8.5"}, 35),
        ("gamma", {"Analyser": "9800", "Reagent": "172", "Gloves": "7.9"}, 14),
    ):
        sourcing.record_supplier_quotation(
            admin,
            SupplierQuotationDraft(
                supplier_id=world[supplier],
                reference=f"{supplier}-Q1",
                request_id=request_id,
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
    return {
        item.description: {
            offer.supplier_name: offer.supplier_quotation_line_id for offer in item.offers
        }
        for item in sourcing.compare(admin, request_id)
    }


# --------------------------------------------------------------------------------------


class TestTheCustomerRequest:
    def test_a_request_is_numbered_and_carries_its_lines(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        request = sourcing.require_request(admin, _request(sourcing, admin, world))
        assert request.number.startswith("RFQ-")
        assert len(request.lines) == 3
        assert request.customer_reference == "BH/2026/114"

    def test_a_request_needs_a_line(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        with pytest.raises(ValidationError, match="at least one line"):
            sourcing.create_request(admin, RequestDraft(customer_id=world["customer"]))

    def test_a_cancellation_records_why(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        request_id = _request(sourcing, admin, world)
        with pytest.raises(ValidationError, match="records why"):
            sourcing.set_request_state(admin, request_id, "cancelled")

    def test_the_lifecycle_refuses_a_leap(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        request_id = _request(sourcing, admin, world)
        with pytest.raises(StateTransitionError):
            sourcing.set_request_state(admin, request_id, "closed")

    def test_viewing_requires_a_permission(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        request_id = _request(sourcing, admin, world)
        nobody = make_actor(admin.business_id, permissions=set())
        with pytest.raises(PermissionDeniedError):
            sourcing.require_request(nobody, request_id)


class TestTheComparison:
    def test_every_supplier_answers_every_line(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        request_id = _request(sourcing, admin, world)
        _offers(sourcing, admin, world, request_id)
        comparison = sourcing.compare(admin, request_id)
        assert len(comparison) == 3
        assert all(len(line.offers) == 3 for line in comparison)

    def test_the_cheapest_landed_cost_wins_not_the_cheapest_unit_price(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        """Freight is apportioned over the supplier's own lines before comparing."""
        request_id = _request(sourcing, admin, world)
        _offers(sourcing, admin, world, request_id)
        reagent = next(
            line for line in sourcing.compare(admin, request_id) if line.description == "Reagent"
        )
        best = reagent.best
        assert best is not None
        assert best.supplier_name == "Gamma Labs"
        assert best.comparable_unit_cost > best.net_unit_cost
        assert best.charges_per_unit > ZERO

    def test_a_line_a_supplier_cannot_fill_states_the_reason(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        request_id = _request(sourcing, admin, world)
        request = sourcing.require_request(admin, request_id)
        line = request.lines[0]
        sourcing.record_supplier_quotation(
            admin,
            SupplierQuotationDraft(
                supplier_id=world["alpha"],
                reference="alpha-Q2",
                request_id=request_id,
                lines=[
                    SupplierQuotationLineDraft(
                        description="Analyser",
                        quantity_offered=line.quantity,
                        unit_cost=ZERO,
                        request_line_id=line.id,
                        is_available=False,
                        availability_note="Discontinued by the manufacturer",
                    )
                ],
            ),
        )
        comparison = sourcing.compare(admin, request_id)
        analyser = next(item for item in comparison if item.description == "Analyser")
        assert analyser.unanswered_by == ["Alpha Diagnostics"]
        assert analyser.best is None

    def test_an_unavailable_line_must_say_why(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        with pytest.raises(ValidationError, match="records why"):
            sourcing.record_supplier_quotation(
                admin,
                SupplierQuotationDraft(
                    supplier_id=world["alpha"],
                    reference="alpha-Q3",
                    lines=[
                        SupplierQuotationLineDraft(
                            description="Analyser",
                            quantity_offered=D("1"),
                            unit_cost=ZERO,
                            is_available=False,
                        )
                    ],
                ),
            )

    def test_the_same_supplier_reference_is_not_recorded_twice(
        self, sourcing: SourcingService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        request_id = _request(sourcing, admin, world)
        _offers(sourcing, admin, world, request_id)
        with pytest.raises(ConflictError, match="already been recorded"):
            sourcing.record_supplier_quotation(
                admin,
                SupplierQuotationDraft(
                    supplier_id=world["alpha"],
                    reference="alpha-Q1",
                    lines=[
                        SupplierQuotationLineDraft(
                            description="Analyser", quantity_offered=D("1"), unit_cost=D("1")
                        )
                    ],
                ),
            )


class TestSourcingAQuotation:
    def test_one_quotation_sources_from_stock_and_three_suppliers(
        self,
        db: Session,
        sourcing: SourcingService,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
        provisioned: dict[str, uuid.UUID],
    ) -> None:
        """The first Phase 5 exit criterion."""
        request_id = _request(sourcing, admin, world)
        offers = _offers(sourcing, admin, world, request_id)
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                request_id=request_id,
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
                                warehouse_id=provisioned["warehouse_id"],
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
            ),
        )
        db.flush()

        suppliers = {
            source.supplier_fk
            for line in quotation.lines
            for source in line.sources
            if source.supplier_fk is not None
        }
        kinds = {source.kind for line in quotation.lines for source in line.sources}
        assert kinds == {"stock", "supplier"}
        assert suppliers == {world["beta"], world["gamma"], world["alpha"]}
        # Each line retains its own cost internally.
        analyser = next(line for line in quotation.lines if line.description == "Analyser")
        assert analyser.unit_cost == D("9100.000000")
        # The customer document shows one coherent offer.
        offer = quotations.offer(admin, quotation.id)
        assert len(offer["lines"]) == 3
        assert offer["total_payable"] > ZERO

    def test_the_sources_must_add_up_to_the_line(
        self,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
        provisioned: dict[str, uuid.UUID],
    ) -> None:
        with pytest.raises(ValidationError, match="supply"):
            quotations.create(
                admin,
                QuotationDraft(
                    customer_id=world["customer"],
                    lines=[
                        LineDraft(
                            description="Analyser",
                            quantity=D("2"),
                            sources=[
                                SourceDraft(
                                    kind="stock",
                                    quantity=D("1"),
                                    unit_cost=D("100"),
                                    warehouse_id=provisioned["warehouse_id"],
                                )
                            ],
                        )
                    ],
                ),
            )

    def test_a_stock_source_names_its_warehouse(
        self, quotations: QuotationService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        with pytest.raises(ValidationError, match="warehouse"):
            quotations.create(
                admin,
                QuotationDraft(
                    customer_id=world["customer"],
                    lines=[
                        LineDraft(
                            description="Analyser",
                            quantity=D("1"),
                            sources=[
                                SourceDraft(kind="stock", quantity=D("1"), unit_cost=D("100"))
                            ],
                        )
                    ],
                ),
            )


class TestTheCostBuildUp:
    @pytest.fixture
    def quotation_id(
        self,
        db: Session,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
    ) -> uuid.UUID:
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                affiliate_id=world["referrer"],
                commission_scope="all_eligible",
                lines=[
                    LineDraft(
                        description="Analyser",
                        quantity=D("2"),
                        product_id=world["analyser"],
                        unit_cost=D("9300"),
                    ),
                    LineDraft(
                        description="Reagent",
                        quantity=D("40"),
                        product_id=world["reagent"],
                        unit_cost=D("172"),
                    ),
                ],
                expenses=[
                    ExpenseDraft(category="transport", amount=D("1200"), method="by_weight"),
                    ExpenseDraft(category="clearing", amount=D("450"), method="by_value"),
                ],
            ),
        )
        db.flush()
        return quotation.id

    def test_the_build_up_is_reproducible_from_stored_inputs(
        self, quotations: QuotationService, admin: Actor, quotation_id: uuid.UUID
    ) -> None:
        """The second exit criterion."""
        first = quotations.costing(admin, quotation_id).build_up
        second = quotations.costing(admin, quotation_id).build_up
        assert first.lines == second.lines
        assert first.payable_before_tax == second.payable_before_tax

    def test_every_component_is_separately_visible(
        self, quotations: QuotationService, admin: Actor, quotation_id: uuid.UUID
    ) -> None:
        view = quotations.costing(admin, quotation_id)
        labels = {label for line in view.build_up.lines for _, label, _ in line.steps()}
        assert {"Supplier cost", "Allocated costs", "Landed cost", "List price"} <= labels

    def test_each_expense_share_is_recorded_against_its_line(
        self, db: Session, quotations: QuotationService, admin: Actor, quotation_id: uuid.UUID
    ) -> None:
        rows = list(
            db.scalars(
                select(QuotationLineCost).where(QuotationLineCost.quotation_fk == quotation_id)
            )
        )
        assert {row.category for row in rows} == {"transport", "clearing"}
        assert sum(row.amount for row in rows) == D("1650.0000")

    def test_allocation_by_weight_follows_the_catalogue(
        self, quotations: QuotationService, admin: Actor, quotation_id: uuid.UUID
    ) -> None:
        """A line's weight comes from the product unless the draft overrides it."""
        view = quotations.costing(admin, quotation_id)
        transport = [row for row in view.allocations if row.category == "transport"]
        assert len(transport) == 2
        # 2 x 18kg against 40 x 2kg: 36 to 80, so the reagent line bears the larger share.
        by_amount = sorted(transport, key=lambda row: row.amount)
        assert by_amount[0].amount < by_amount[1].amount
        assert sum(row.amount for row in transport) == D("1200.0000")

    def test_the_margin_source_is_recorded(
        self, quotations: QuotationService, admin: Actor, quotation_id: uuid.UUID
    ) -> None:
        view = quotations.costing(admin, quotation_id)
        assert set(view.margin_sources.values()) == {"business"}

    def test_a_product_policy_displaces_the_house_rate(
        self,
        db: Session,
        policy: PricingPolicyService,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
        quotation_id: uuid.UUID,
    ) -> None:
        policy.set_margin_policy(
            admin,
            scope=PolicyScope.PRODUCT,
            method=MarginMethod.TARGET_GROSS_MARGIN,
            rate=D("0.25"),
            product_id=world["analyser"],
        )
        db.flush()
        quotations.recost(admin, quotation_id)
        view = quotations.costing(admin, quotation_id)
        assert view.margin_sources["1"] == "product"
        assert view.margin_sources["2"] == "business"

    def test_reading_the_costing_amends_nothing(
        self, db: Session, quotations: QuotationService, admin: Actor, quotation_id: uuid.UUID
    ) -> None:
        """``costing`` explains a quotation; it does not quietly re-price it."""
        quotations.costing(admin, quotation_id)
        assert not db.dirty

    def test_a_margin_set_on_the_line_survives_a_recost(
        self,
        db: Session,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
    ) -> None:
        """An override to zero is a decision, not an absence."""
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                lines=[
                    LineDraft(
                        description="At cost",
                        quantity=D("1"),
                        unit_cost=D("1000"),
                        margin_method="markup_on_cost",
                        margin_rate=ZERO,
                    )
                ],
            ),
        )
        db.flush()
        quotations.recost(admin, quotation.id)
        db.flush()
        line = quotation.lines[0]
        assert line.margin_source == "line"
        assert line.margin_amount == ZERO
        assert line.list_price == D("1000.0000")

    def test_costing_requires_its_own_permission(
        self, quotations: QuotationService, admin: Actor, quotation_id: uuid.UUID
    ) -> None:
        """PRD 14: supplier cost, margin and commission are internal."""
        seller = make_actor(admin.business_id, permissions={"quotation.view", "tax.view"})
        with pytest.raises(PermissionDeniedError):
            quotations.costing(seller, quotation_id)

    def test_the_customer_offer_needs_no_costing_permission(
        self, quotations: QuotationService, admin: Actor, quotation_id: uuid.UUID
    ) -> None:
        seller = make_actor(admin.business_id, permissions={"quotation.view"})
        offer = quotations.offer(seller, quotation_id)
        rendered = str(offer)
        for forbidden in ("landed", "margin", "commission", "supplier"):
            assert forbidden not in rendered

    def test_commission_applies_only_within_its_scope(
        self,
        db: Session,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The third exit criterion."""
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                affiliate_id=world["referrer"],
                commission_scope="selected_lines",
                lines=[
                    LineDraft(
                        description="In scope",
                        quantity=D("1"),
                        unit_cost=D("1000"),
                        commission_selected=True,
                    ),
                    LineDraft(description="Out of scope", quantity=D("1"), unit_cost=D("1000")),
                ],
            ),
        )
        db.flush()
        chosen, other = quotation.lines
        assert chosen.commission_amount > ZERO
        assert other.commission_amount == ZERO
        assert other.commission_reason == "This line was not selected for commission."

    def test_no_referrer_means_no_commission_and_a_reason(
        self, db: Session, quotations: QuotationService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                lines=[LineDraft(description="Line", quantity=D("1"), unit_cost=D("1000"))],
            ),
        )
        db.flush()
        assert quotation.lines[0].commission_amount == ZERO
        assert "No referrer" in (quotation.lines[0].commission_reason or "")


class TestIssueAndPreservation:
    @pytest.fixture
    def issued(
        self,
        db: Session,
        sourcing: SourcingService,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
    ) -> uuid.UUID:
        request_id = _request(sourcing, admin, world)
        offers = _offers(sourcing, admin, world, request_id)
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                request_id=request_id,
                valid_until=date(2026, 12, 31),
                affiliate_id=world["referrer"],
                commission_scope="all_eligible",
                lines=[
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
                    )
                ],
            ),
        )
        db.flush()
        quotations.issue(admin, quotation.id)
        db.flush()
        return quotation.id

    def test_issuing_takes_a_snapshot(
        self, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        snapshots = quotations.snapshots(admin, issued)
        assert len(snapshots) == 1
        assert snapshots[0].content_hash
        assert snapshots[0].payload["lines"]

    def test_a_supplier_price_change_after_issue_does_not_move_the_quotation(
        self, db: Session, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        """The fifth exit criterion."""
        from nickland.persistence.pricing_models import SupplierQuotationLine

        before = quotations.require(admin, issued).total_payable
        for row in db.scalars(select(SupplierQuotationLine)):
            row.unit_cost = row.unit_cost * D("1.4")
        db.flush()
        assert quotations.require(admin, issued).total_payable == before
        assert quotations.snapshots(admin, issued)[0].payload["totals"]["total_payable"] == str(
            before
        )

    def test_an_issued_quotation_cannot_be_recosted(
        self, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        with pytest.raises(StateTransitionError):
            quotations.recost(admin, issued)

    def test_the_snapshot_cannot_be_altered(
        self, db: Session, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        snapshot = quotations.snapshots(admin, issued)[0]
        snapshot.content_hash = "tampered"
        with pytest.raises(ConflictError, match="cannot be altered"):
            db.flush()
        db.rollback()

    def test_the_snapshot_cannot_be_deleted(
        self, db: Session, admin: Actor, issued: uuid.UUID
    ) -> None:
        snapshot = db.scalar(
            select(QuotationSnapshot).where(QuotationSnapshot.quotation_fk == issued)
        )
        assert snapshot is not None
        db.delete(snapshot)
        with pytest.raises(ConflictError, match="cannot be deleted"):
            db.flush()
        db.rollback()

    def test_the_variance_is_reported_once_the_actual_cost_is_known(
        self, db: Session, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        quotation = quotations.require(admin, issued)
        line = quotation.lines[0]
        estimated = line.landed_cost
        quotations.record_actual_cost(
            admin, line.id, actual_unit_cost=D("200"), reference="SIN-0001"
        )
        db.flush()
        variance = quotations.costing(admin, issued).variances[0]
        assert variance.estimated == estimated
        assert variance.is_known
        assert variance.is_adverse

    def test_a_revision_supersedes_and_keeps_the_original(
        self, db: Session, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        original = quotations.require(admin, issued)
        number = original.number
        revision = quotations.revise(admin, issued, reason="The customer asked for 60 packs.")
        db.flush()
        assert revision.number == number
        assert revision.revision == 2
        assert revision.state == "draft"
        assert revision.supersedes_fk == issued
        assert quotations.require(admin, issued).state == "superseded"
        assert len(revision.lines) == len(original.lines)

    def test_a_revision_records_why(
        self, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        with pytest.raises(ValidationError, match="records why"):
            quotations.revise(admin, issued, reason="  ")

    def test_a_rejection_records_why(
        self, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        with pytest.raises(ValidationError, match="records why"):
            quotations.decide(admin, issued, state="rejected")

    def test_acceptance_is_recorded(
        self, db: Session, quotations: QuotationService, admin: Actor, issued: uuid.UUID
    ) -> None:
        quotations.decide(admin, issued, state="accepted", on=date(2026, 11, 20))
        db.flush()
        quotation = quotations.require(admin, issued)
        assert quotation.state == "accepted"
        assert quotation.accepted_on == date(2026, 11, 20)

    def test_an_unsourced_line_cannot_be_issued(
        self, db: Session, quotations: QuotationService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                lines=[LineDraft(description="Nothing behind it", quantity=D("1"))],
            ),
        )
        db.flush()
        with pytest.raises(ValidationError, match="no price behind"):
            quotations.issue(admin, quotation.id)


class TestApproval:
    def test_a_discount_beyond_the_limit_blocks_issue_until_approved(
        self,
        db: Session,
        policy: PricingPolicyService,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
    ) -> None:
        """The sixth test obligation, and the control PRD 6.3 requires."""
        policy.set_discount_limit(admin, role_code="sales_person", maximum_rate=D("0.05"))
        db.flush()
        seller = make_actor(
            admin.business_id,
            permissions={
                "quotation.manage",
                "quotation.view",
                "quotation.issue",
                "costing.view",
                "product.view",
                "customer.view",
                "tax.view",
                "business.view",
            },
        )
        object.__setattr__(seller, "role_codes", frozenset({"sales_person"}))

        quotation = quotations.create(
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
        db.flush()
        assert quotation.requires_approval
        with pytest.raises(ApprovalRequiredError):
            quotations.issue(seller, quotation.id)

        quotations.submit_for_approval(seller, quotation.id)
        db.flush()
        quotations.approve(admin, quotation.id)
        db.flush()
        quotations.issue(seller, quotation.id)
        db.flush()
        assert quotations.require(admin, quotation.id).state == "issued"

    def test_an_administrator_needs_no_approval(
        self, db: Session, quotations: QuotationService, admin: Actor, world: dict[str, uuid.UUID]
    ) -> None:
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                lines=[
                    LineDraft(
                        description="Reagent",
                        quantity=D("10"),
                        unit_cost=D("100"),
                        discount_value=D("0.4"),
                    )
                ],
            ),
        )
        db.flush()
        assert not quotation.requires_approval


class TestInstalments:
    @pytest.fixture
    def plan_id(self, policy: PricingPolicyService, admin: Actor) -> uuid.UUID:
        return policy.plans(admin)[0].id

    def test_the_schedule_discloses_everything_the_requirement_asks_for(
        self,
        db: Session,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
        plan_id: uuid.UUID,
    ) -> None:
        """The fourth exit criterion."""
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                payment_plan="instalment",
                instalment_plan_id=plan_id,
                instalment_months=6,
                first_instalment_on=date(2026, 12, 1),
                lines=[LineDraft(description="Reagent", quantity=D("40"), unit_cost=D("172"))],
            ),
        )
        db.flush()
        quotations.issue(admin, quotation.id)
        db.flush()

        rows = quotations.schedule(admin, quotation.id)
        deposit = [row for row in rows if row.is_deposit]
        payments = [row for row in rows if not row.is_deposit]
        assert len(deposit) == 1
        assert len(payments) == 6
        assert payments[0].due_on == date(2026, 12, 1)
        assert payments[-1].due_on == date(2027, 5, 1)
        assert quotation.finance_charge_total > ZERO
        assert sum(row.amount for row in rows) == quotation.total_payable

    def test_the_duration_band_sets_the_finance_charge(
        self,
        db: Session,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
        plan_id: uuid.UUID,
    ) -> None:
        charges = {}
        for months in (3, 6, 12):
            quotation = quotations.create(
                admin,
                QuotationDraft(
                    customer_id=world["customer"],
                    payment_plan="instalment",
                    instalment_plan_id=plan_id,
                    instalment_months=months,
                    first_instalment_on=date(2026, 12, 1),
                    lines=[LineDraft(description="Reagent", quantity=D("1"), unit_cost=D("1000"))],
                ),
            )
            db.flush()
            charges[months] = quotation.finance_charge_total
        assert charges[3] < charges[6] < charges[12]

    def test_a_duration_outside_every_band_is_refused(
        self,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
        plan_id: uuid.UUID,
    ) -> None:
        with pytest.raises(ValidationError, match="No duration band covers 24"):
            quotations.create(
                admin,
                QuotationDraft(
                    customer_id=world["customer"],
                    payment_plan="instalment",
                    instalment_plan_id=plan_id,
                    instalment_months=24,
                    first_instalment_on=date(2026, 12, 1),
                    lines=[LineDraft(description="Reagent", quantity=D("1"), unit_cost=D("100"))],
                ),
            )

    def test_rescheduling_keeps_the_original_as_a_version(
        self,
        db: Session,
        quotations: QuotationService,
        admin: Actor,
        world: dict[str, uuid.UUID],
        plan_id: uuid.UUID,
    ) -> None:
        """PRD 25.5: the original schedule is never overwritten."""
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                payment_plan="instalment",
                instalment_plan_id=plan_id,
                instalment_months=3,
                first_instalment_on=date(2026, 12, 1),
                lines=[LineDraft(description="Reagent", quantity=D("1"), unit_cost=D("1000"))],
            ),
        )
        db.flush()
        quotations.issue(admin, quotation.id)
        db.flush()
        original = [row.id for row in quotations.schedule(admin, quotation.id)]

        quotations.reschedule(
            admin, quotation.id, months=6, reason="The customer asked for longer terms."
        )
        db.flush()
        current = quotations.schedule(admin, quotation.id)
        assert quotation.schedule_version == 2
        assert {row.id for row in current}.isdisjoint(original)

        from nickland.persistence.pricing_models import QuotationInstalment

        superseded = db.scalars(
            select(QuotationInstalment).where(QuotationInstalment.id.in_(original))
        ).all()
        assert {row.state for row in superseded} == {"rescheduled"}


class TestCommissionRegister:
    @pytest.fixture
    def accrued(
        self,
        db: Session,
        quotations: QuotationService,
        commissions: CommissionService,
        admin: Actor,
        world: dict[str, uuid.UUID],
    ) -> CommissionAccrual:
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=world["customer"],
                affiliate_id=world["referrer"],
                commission_scope="all_eligible",
                lines=[LineDraft(description="Reagent", quantity=D("40"), unit_cost=D("172"))],
            ),
        )
        db.flush()
        quotations.issue(admin, quotation.id)
        db.flush()
        rows = commissions.accrue_for_quotation(admin, quotation.id, source_reference="INV-0001")
        db.flush()
        return rows[0]

    def test_accrual_records_the_basis_and_the_rate(self, accrued: CommissionAccrual) -> None:
        assert accrued.state == "accrued"
        assert accrued.rate == D("0.050000")
        assert accrued.basis_amount > ZERO
        assert accrued.accrued_amount > ZERO
        assert accrued.entitlement == ZERO

    def test_an_entitlement_becomes_payable_on_collection(
        self,
        db: Session,
        commissions: CommissionService,
        admin: Actor,
        accrued: CommissionAccrual,
    ) -> None:
        commissions.approve(admin, accrued.id)
        db.flush()
        assert accrued.state == "approved"
        commissions.record_payment_received(admin, accrued.id, paid_fraction=D("0.5"))
        db.flush()
        assert accrued.state == "payable"
        assert accrued.entitlement * 2 == accrued.accrued_amount

    def test_paying_the_current_entitlement_does_not_close_a_part_collected_accrual(
        self,
        db: Session,
        commissions: CommissionService,
        admin: Actor,
        accrued: CommissionAccrual,
    ) -> None:
        """The referrer earns the rest when the customer pays the rest."""
        commissions.approve(admin, accrued.id)
        commissions.record_payment_received(admin, accrued.id, paid_fraction=D("0.5"))
        db.flush()
        commissions.pay(admin, accrued.id, amount=accrued.entitlement)
        db.flush()
        assert accrued.state == "payable"

        commissions.record_payment_received(admin, accrued.id, paid_fraction=D("1"))
        db.flush()
        commissions.pay(admin, accrued.id, amount=accrued.entitlement - accrued.settled_amount)
        db.flush()
        assert accrued.state == "paid"
        assert accrued.settled_amount == accrued.accrued_amount

    def test_paying_more_than_the_entitlement_is_refused(
        self,
        db: Session,
        commissions: CommissionService,
        admin: Actor,
        accrued: CommissionAccrual,
    ) -> None:
        commissions.approve(admin, accrued.id)
        commissions.record_payment_received(admin, accrued.id, paid_fraction=D("1"))
        db.flush()
        with pytest.raises(ValidationError, match="exceeds"):
            commissions.pay(admin, accrued.id, amount=accrued.entitlement + D("1"))

    def test_an_override_keeps_the_prior_rate(
        self,
        db: Session,
        commissions: CommissionService,
        admin: Actor,
        accrued: CommissionAccrual,
    ) -> None:
        commissions.override_rate(
            admin, accrued.id, rate=D("0.03"), reason="Renegotiated for this account."
        )
        db.flush()
        assert accrued.prior_rate == D("0.050000")
        assert accrued.rate == D("0.030000")
        assert accrued.override_reason

    def test_a_cancellation_records_why(
        self, commissions: CommissionService, admin: Actor, accrued: CommissionAccrual
    ) -> None:
        with pytest.raises(ValidationError, match="records why"):
            commissions.cancel(admin, accrued.id, reason="")

    def test_the_register_reports_money_paid_not_value_settled(
        self,
        db: Session,
        commissions: CommissionService,
        admin: Actor,
        accrued: CommissionAccrual,
    ) -> None:
        commissions.approve(admin, accrued.id)
        commissions.record_payment_received(admin, accrued.id, paid_fraction=D("0.5"))
        db.flush()
        commissions.pay(admin, accrued.id, amount=accrued.entitlement)
        db.flush()
        totals = commissions.totals(admin)
        assert totals.settled == accrued.settled_amount
        assert totals.settled < accrued.accrued_amount
        assert totals.outstanding == ZERO

    def test_a_line_cannot_accrue_twice(
        self,
        commissions: CommissionService,
        admin: Actor,
        accrued: CommissionAccrual,
    ) -> None:
        assert accrued.quotation_fk is not None
        with pytest.raises(ConflictError, match="already accrued"):
            commissions.accrue_for_quotation(
                admin, accrued.quotation_fk, source_reference="INV-0002"
            )


class TestPolicy:
    def test_provisioning_seeds_a_policy_a_limit_and_a_plan(
        self, policy: PricingPolicyService, admin: Actor
    ) -> None:
        assert any(item.scope == "business" for item in policy.margin_policies(admin))
        assert [item.role_code for item in policy.discount_limits(admin)] == ["sales_person"]
        plans = policy.plans(admin)
        assert len(plans) == 1
        assert len(plans[0].bands) == 3

    def test_the_business_policy_cannot_be_withdrawn(
        self, policy: PricingPolicyService, admin: Actor
    ) -> None:
        house = next(item for item in policy.margin_policies(admin) if item.scope == "business")
        with pytest.raises(ConflictError, match="cannot be withdrawn"):
            policy.withdraw_margin_policy(admin, house.id)

    def test_overlapping_bands_are_refused(
        self, policy: PricingPolicyService, admin: Actor
    ) -> None:
        plan = policy.plans(admin)[0]
        with pytest.raises(ValidationError, match="overlap"):
            policy.set_bands(
                admin,
                plan.id,
                [
                    {"minimum_months": 1, "maximum_months": 6, "rate": "0.03"},
                    {"minimum_months": 4, "maximum_months": 12, "rate": "0.08"},
                ],
            )

    def test_a_target_margin_of_one_is_refused(
        self, policy: PricingPolicyService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError, match="not permitted"):
            policy.set_margin_policy(
                admin,
                scope=PolicyScope.BUSINESS,
                method=MarginMethod.TARGET_GROSS_MARGIN,
                rate=D("1"),
            )
