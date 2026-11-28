"""Party services against a real database."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.catalogue import CatalogueService, ProductDraft
from nickland.application.services.parties import PartyDraft, PartyService
from nickland.application.services.pricing_lists import PriceListService
from nickland.application.services.units import UnitService
from nickland.domain.errors import (
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.parties import CreditStanding, PartyKind
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor


@pytest.fixture
def parties(db: Session, clock: FixedClock) -> PartyService:
    return PartyService(db, clock=clock)


@pytest.fixture
def pricing(db: Session, clock: FixedClock) -> PriceListService:
    return PriceListService(db, clock=clock)


@pytest.fixture
def product_id(db: Session, clock: FixedClock, admin: Actor) -> uuid.UUID:
    units = UnitService(db, clock=clock)
    each = next(unit for unit in units.units(admin) if unit.code == "ea").id
    catalogue = CatalogueService(db, clock=clock)
    return catalogue.create_product(
        admin, ProductDraft(sku="GLV-1", name="Glove", stock_unit_id=each)
    ).id


def _customer(**overrides: object) -> PartyDraft:
    values: dict[str, object] = {
        "name": "Banhart Specialist Hospital",
        "is_customer": True,
    }
    values.update(overrides)
    return PartyDraft(**values)  # type: ignore[arg-type]


class TestCreation:
    def test_creates_a_customer_and_derives_a_code(
        self, parties: PartyService, admin: Actor
    ) -> None:
        party = parties.create(admin, _customer())
        assert party.is_customer
        assert party.code.startswith("C")
        assert party.name == "Banhart Specialist Hospital"

    def test_one_organisation_may_hold_several_roles(
        self, parties: PartyService, admin: Actor
    ) -> None:
        party = parties.create(admin, _customer(is_supplier=True, is_affiliate=True))
        assert party.is_customer and party.is_supplier and party.is_affiliate

    def test_a_party_must_hold_a_role(self, parties: PartyService, admin: Actor) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            parties.create(admin, PartyDraft(name="Nobody"))

    def test_a_duplicate_code_is_refused(self, parties: PartyService, admin: Actor) -> None:
        parties.create(admin, _customer(code="BANHART"))
        with pytest.raises(ConflictError, match="already exists"):
            parties.create(admin, _customer(code="BANHART", name="Another"))

    def test_an_exemption_requires_evidence(self, parties: PartyService, admin: Actor) -> None:
        with pytest.raises(ValidationError, match="certificate"):
            parties.create(admin, _customer(tax_treatment="exempt"))
        party = parties.create(
            admin, _customer(tax_treatment="exempt", tax_exemption_reference="CERT-1")
        )
        assert party.tax_exemption_reference == "CERT-1"

    def test_an_invalid_address_is_refused(self, parties: PartyService, admin: Actor) -> None:
        with pytest.raises(ValidationError, match="not a valid"):
            parties.create(admin, _customer(email="notanemail"))

    def test_contacts_are_created_with_the_party(self, parties: PartyService, admin: Actor) -> None:
        party = parties.create(
            admin,
            _customer(
                contacts=[
                    {"name": "A. Mensah", "position": "Procurement", "is_primary": True},
                    {"name": "K. Owusu"},
                ]
            ),
        )
        assert len(party.contacts) == 2
        assert sum(1 for contact in party.contacts if contact.is_primary) == 1

    def test_the_role_governs_which_permission_is_required(
        self, parties: PartyService, admin: Actor
    ) -> None:
        """A supplier is written by whoever may manage suppliers, not customers."""
        sales = make_actor(admin.business_id, permissions={"customer.manage"})
        parties.create(sales, _customer())
        with pytest.raises(PermissionDeniedError, match=r"supplier\.manage"):
            parties.create(sales, PartyDraft(name="A Supplier", is_supplier=True))


class TestCredit:
    def test_a_configured_limit_is_reported(self, parties: PartyService, admin: Actor) -> None:
        party = parties.create(admin, _customer(credit_limit=Decimal("10000")))
        assessment = parties.credit_standing(admin, party.id)
        assert assessment.standing is CreditStanding.WITHIN_LIMIT
        assert assessment.limit == Decimal("10000.0000")

    def test_no_limit_means_cash_only(self, parties: PartyService, admin: Actor) -> None:
        party = parties.create(admin, _customer())
        assert parties.credit_standing(admin, party.id).standing is CreditStanding.NO_CREDIT

    def test_a_hold_requires_a_reason_and_withholds_credit(
        self, parties: PartyService, admin: Actor
    ) -> None:
        party = parties.create(admin, _customer(credit_limit=Decimal("10000")))
        with pytest.raises(ValidationError, match="reason is required"):
            parties.set_credit_hold(admin, party.id, on_hold=True, reason="  ")

        parties.set_credit_hold(admin, party.id, on_hold=True, reason="Disputed invoice 0184")
        assessment = parties.credit_standing(admin, party.id)
        assert assessment.standing is CreditStanding.ON_HOLD
        assert not assessment.permits_credit_sale

    def test_releasing_a_hold_restores_credit(self, parties: PartyService, admin: Actor) -> None:
        party = parties.create(admin, _customer(credit_limit=Decimal("10000")))
        parties.set_credit_hold(admin, party.id, on_hold=True, reason="Dispute")
        parties.set_credit_hold(admin, party.id, on_hold=False, reason="Dispute settled")
        assert parties.credit_standing(admin, party.id).permits_credit_sale


class TestSearch:
    @pytest.fixture(autouse=True)
    def _parties(self, parties: PartyService, admin: Actor) -> None:
        parties.create(
            admin,
            _customer(
                name="Banhart Specialist Hospital",
                tax_identifier="C0009876543",
                telephone="+233 30 111 2222",
                contacts=[{"name": "A. Mensah"}],
            ),
        )
        parties.create(
            admin,
            PartyDraft(name="DCL Medical Supplies", is_supplier=True, email="sales@dcl.test"),
        )
        parties.create(
            admin, PartyDraft(name="K. Asante", is_affiliate=True, party_type="individual")
        )

    @pytest.mark.parametrize(
        ("term", "expected"),
        [
            ("banhart", 1),
            ("BANHART", 1),
            ("medical", 1),
            ("C0009876543", 1),
            ("mensah", 1),
            ("111 2222", 1),
            ("asante", 1),
        ],
    )
    def test_one_term_searches_every_field(
        self, parties: PartyService, admin: Actor, term: str, expected: int
    ) -> None:
        assert parties.search(admin, term=term).total == expected

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [(PartyKind.CUSTOMER, 1), (PartyKind.SUPPLIER, 1), (PartyKind.AFFILIATE, 1)],
    )
    def test_filtering_by_role(
        self, parties: PartyService, admin: Actor, kind: PartyKind, expected: int
    ) -> None:
        assert parties.search(admin, kind=kind).total == expected

    def test_searching_requires_one_viewing_permission(
        self, parties: PartyService, admin: Actor
    ) -> None:
        weak = make_actor(admin.business_id, permissions=set())
        with pytest.raises(PermissionDeniedError):
            parties.search(weak)


class TestSupplierCatalogue:
    def test_records_what_a_supplier_calls_a_product(
        self, parties: PartyService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        supplier = parties.create(admin, PartyDraft(name="DCL", is_supplier=True))
        link = parties.link_supplier_product(
            admin,
            supplier_id=supplier.id,
            product_id=product_id,
            supplier_sku="DCL-GLV-100",
            last_price=Decimal("120"),
            currency_code="GHS",
            lead_time_days=7,
        )
        assert link.supplier_sku == "DCL-GLV-100"
        assert link.last_price == Decimal("120.0000")

    def test_the_date_a_price_was_recorded_is_preserved(
        self, parties: PartyService, admin: Actor, product_id: uuid.UUID, clock: FixedClock
    ) -> None:
        """PRD 14: a stale price must be visible as stale."""
        supplier = parties.create(admin, PartyDraft(name="DCL", is_supplier=True))
        link = parties.link_supplier_product(
            admin, supplier_id=supplier.id, product_id=product_id, last_price=Decimal("120")
        )
        assert link.price_recorded_on == clock.now().date()

    def test_only_one_supplier_is_preferred_per_product(
        self, parties: PartyService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        first = parties.create(admin, PartyDraft(name="DCL", is_supplier=True))
        second = parties.create(admin, PartyDraft(name="Medisafe", is_supplier=True))
        parties.link_supplier_product(
            admin, supplier_id=first.id, product_id=product_id, is_preferred=True
        )
        parties.link_supplier_product(
            admin, supplier_id=second.id, product_id=product_id, is_preferred=True
        )
        preferred = [link for link in parties.suppliers_of(admin, product_id) if link.is_preferred]
        assert len(preferred) == 1
        assert preferred[0].supplier_fk == second.id

    def test_a_customer_cannot_be_linked_as_a_supplier(
        self, parties: PartyService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        customer = parties.create(admin, _customer())
        with pytest.raises(ValidationError, match="not recorded as a supplier"):
            parties.link_supplier_product(admin, supplier_id=customer.id, product_id=product_id)


class TestPriceResolution:
    """The order of precedence is fixed, so two people reach the same figure."""

    def test_the_product_price_is_the_last_resort(
        self,
        parties: PartyService,
        pricing: PriceListService,
        admin: Actor,
        db: Session,
        clock: FixedClock,
    ) -> None:
        units = UnitService(db, clock=clock)
        each = next(unit for unit in units.units(admin) if unit.code == "ea").id
        product = CatalogueService(db, clock=clock).create_product(
            admin,
            ProductDraft(sku="GLV-2", name="Glove", stock_unit_id=each, list_price=Decimal("150")),
        )
        resolved = pricing.resolve(admin, product_id=product.id)
        assert resolved is not None
        assert resolved.source == "product"
        assert resolved.price == Decimal("150.0000")

    def test_the_default_list_outranks_the_product_price(
        self, pricing: PriceListService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        price_list = pricing.create(
            admin, code="retail", name="Retail", currency_code="GHS", is_default=True
        )
        pricing.set_price(
            admin, price_list_id=price_list.id, product_id=product_id, price=Decimal("140")
        )
        resolved = pricing.resolve(admin, product_id=product_id)
        assert resolved is not None
        assert resolved.source == "price_list"
        assert resolved.price == Decimal("140.0000")

    def test_the_customer_list_outranks_the_default(
        self, parties: PartyService, pricing: PriceListService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        default = pricing.create(
            admin, code="retail", name="Retail", currency_code="GHS", is_default=True
        )
        pricing.set_price(
            admin, price_list_id=default.id, product_id=product_id, price=Decimal("140")
        )
        trade = pricing.create(admin, code="trade", name="Trade", currency_code="GHS")
        pricing.set_price(
            admin, price_list_id=trade.id, product_id=product_id, price=Decimal("120")
        )
        customer = parties.create(admin, _customer(price_list_id=trade.id))

        resolved = pricing.resolve(admin, product_id=product_id, customer_id=customer.id)
        assert resolved is not None
        assert resolved.price == Decimal("120.0000")
        assert resolved.source_label == "Trade"

    def test_an_agreement_outranks_every_list(
        self, parties: PartyService, pricing: PriceListService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        default = pricing.create(
            admin, code="retail", name="Retail", currency_code="GHS", is_default=True
        )
        pricing.set_price(
            admin, price_list_id=default.id, product_id=product_id, price=Decimal("140")
        )
        customer = parties.create(admin, _customer())
        parties.set_customer_price(
            admin,
            customer_id=customer.id,
            product_id=product_id,
            price=Decimal("99"),
            agreement_reference="AGR-2026-1",
        )
        resolved = pricing.resolve(admin, product_id=product_id, customer_id=customer.id)
        assert resolved is not None
        assert resolved.source == "customer_agreement"
        assert resolved.price == Decimal("99.0000")

    def test_a_quantity_break_applies_at_the_quantity(
        self, pricing: PriceListService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        price_list = pricing.create(
            admin, code="retail", name="Retail", currency_code="GHS", is_default=True
        )
        pricing.set_price(
            admin, price_list_id=price_list.id, product_id=product_id, price=Decimal("140")
        )
        pricing.set_price(
            admin,
            price_list_id=price_list.id,
            product_id=product_id,
            price=Decimal("125"),
            minimum_quantity=Decimal("100"),
        )
        assert pricing.resolve(
            admin, product_id=product_id, quantity=Decimal("50")
        ).price == Decimal("140.0000")  # type: ignore[union-attr]
        assert pricing.resolve(
            admin, product_id=product_id, quantity=Decimal("100")
        ).price == Decimal("125.0000")  # type: ignore[union-attr]

    def test_an_expired_agreement_is_passed_over(
        self, parties: PartyService, pricing: PriceListService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        default = pricing.create(
            admin, code="retail", name="Retail", currency_code="GHS", is_default=True
        )
        pricing.set_price(
            admin, price_list_id=default.id, product_id=product_id, price=Decimal("140")
        )
        customer = parties.create(admin, _customer())
        parties.set_customer_price(
            admin,
            customer_id=customer.id,
            product_id=product_id,
            price=Decimal("99"),
            effective_from=date(2020, 1, 1),
            effective_to=date(2020, 12, 31),
        )
        resolved = pricing.resolve(admin, product_id=product_id, customer_id=customer.id)
        assert resolved is not None
        assert resolved.source == "price_list"

    def test_no_price_recorded_returns_nothing(
        self, pricing: PriceListService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        assert pricing.resolve(admin, product_id=product_id) is None


class TestAffiliates:
    def test_records_a_commission_arrangement(
        self, parties: PartyService, admin: Actor, product_id: uuid.UUID
    ) -> None:
        affiliate = parties.create(admin, PartyDraft(name="K. Asante", is_affiliate=True))
        profile = parties.set_affiliate_profile(
            admin,
            party_id=affiliate.id,
            scope="selected_products",
            method="percentage",
            rate=Decimal("0.05"),
            eligible_product_ids=[product_id],
        )
        assert profile.rate == Decimal("0.050000")
        assert profile.payout_trigger == "customer_payment"
        assert len(profile.eligible_products) == 1

    def test_the_payout_trigger_defaults_to_customer_payment(
        self, parties: PartyService, admin: Actor
    ) -> None:
        """ADR-0008: commission is payable once the customer has paid."""
        affiliate = parties.create(admin, PartyDraft(name="K. Asante", is_affiliate=True))
        profile = parties.set_affiliate_profile(
            admin,
            party_id=affiliate.id,
            scope="all_eligible",
            method="percentage",
            rate=Decimal("0.05"),
        )
        assert profile.payout_trigger == "customer_payment"

    @pytest.mark.parametrize("rate", [Decimal("1"), Decimal("1.5"), Decimal("-0.1")])
    def test_an_impossible_rate_is_refused(
        self, parties: PartyService, admin: Actor, rate: Decimal
    ) -> None:
        affiliate = parties.create(admin, PartyDraft(name="K. Asante", is_affiliate=True))
        with pytest.raises(ValidationError, match="fraction below one"):
            parties.set_affiliate_profile(
                admin,
                party_id=affiliate.id,
                scope="all_eligible",
                method="percentage",
                rate=rate,
            )

    def test_a_scoped_arrangement_requires_its_scope(
        self, parties: PartyService, admin: Actor
    ) -> None:
        affiliate = parties.create(admin, PartyDraft(name="K. Asante", is_affiliate=True))
        with pytest.raises(ValidationError, match="at least one product"):
            parties.set_affiliate_profile(
                admin,
                party_id=affiliate.id,
                scope="selected_products",
                method="percentage",
                rate=Decimal("0.05"),
            )

    def test_a_customer_cannot_hold_a_commission_arrangement(
        self, parties: PartyService, admin: Actor
    ) -> None:
        customer = parties.create(admin, _customer())
        with pytest.raises(ValidationError, match="not recorded as an affiliate"):
            parties.set_affiliate_profile(
                admin,
                party_id=customer.id,
                scope="all_eligible",
                method="percentage",
                rate=Decimal("0.05"),
            )
