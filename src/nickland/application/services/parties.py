"""Party services: customers, suppliers, affiliates, contacts and commercial terms."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.money import ZERO
from nickland.domain.parties import (
    CreditAssessment,
    PartyKind,
    PartyType,
    PaymentTermKind,
    PaymentTerms,
    TaxTreatment,
    assert_exemption_evidenced,
    assess_credit,
    derive_party_code,
    validate_email,
    validate_party_code,
    validate_telephone,
)
from nickland.persistence.base import as_dict
from nickland.persistence.catalogue_models import (
    AffiliateEligibility,
    AffiliateProfile,
    Contact,
    CustomerPrice,
    Party,
    PriceList,
    Product,
    SupplierProduct,
)
from nickland.persistence.models import Business
from nickland.platform.clock import Clock, SystemClock

COMMISSION_SCOPES = frozenset(
    {"selected_products", "selected_categories", "selected_lines", "all_eligible"}
)
"""Commission scopes, per PRD 6.4."""


@dataclass(slots=True)
class PartyDraft:
    """Everything the party form collects."""

    name: str
    code: str | None = None
    legal_name: str | None = None
    party_type: PartyType | str = PartyType.ORGANISATION
    is_customer: bool = False
    is_supplier: bool = False
    is_affiliate: bool = False
    email: str | None = None
    telephone: str | None = None
    website: str | None = None
    tax_identifier: str | None = None
    registration_number: str | None = None
    tax_treatment: TaxTreatment | str = TaxTreatment.STANDARD
    tax_exemption_reference: str | None = None
    tax_exemption_expires: date | None = None
    payment_term_kind: PaymentTermKind | str = PaymentTermKind.IMMEDIATE
    payment_term_days: int = 0
    credit_limit: Decimal | None = None
    price_list_id: uuid.UUID | None = None
    discount_percent: Decimal | None = None
    default_lead_time_days: int | None = None
    bank_name: str | None = None
    bank_account_name: str | None = None
    bank_account_number: str | None = None
    mobile_money_provider: str | None = None
    mobile_money_number: str | None = None
    notes: str | None = None
    contacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PartyPage:
    """A page of party results."""

    items: list[Party]
    total: int
    offset: int
    limit: int


class PartyService:
    """Customers, suppliers and affiliates."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- creation and amendment -------------------------------------------------------

    def create(self, actor: AnyActor, draft: PartyDraft) -> Party:
        """Create a party in one or more roles."""
        self._require_write(actor, draft)

        if not draft.name.strip():
            raise ValidationError("A party name is required.", field="name")
        if not (draft.is_customer or draft.is_supplier or draft.is_affiliate):
            raise ValidationError(
                "A party must be a customer, a supplier or an affiliate. Select at least one.",
                field="roles",
            )

        treatment = TaxTreatment(str(draft.tax_treatment))
        assert_exemption_evidenced(treatment, evidence_reference=draft.tax_exemption_reference)
        terms = PaymentTerms(PaymentTermKind(str(draft.payment_term_kind)), draft.payment_term_days)

        if draft.credit_limit is not None and draft.credit_limit < ZERO:
            raise ValidationError("A credit limit cannot be negative.", field="credit_limit")
        if draft.discount_percent is not None and not (
            ZERO <= draft.discount_percent < Decimal("1")
        ):
            raise ValidationError(
                "A standing discount is a fraction below one, such as 0.05 for five per cent.",
                field="discount_percent",
            )

        code = (
            validate_party_code(draft.code)
            if draft.code
            else derive_party_code(
                draft.name, self._primary_kind(draft), self._next_sequence(actor)
            )
        )
        if self.by_code(actor, code) is not None:
            raise ConflictError(f"A party with code {code!r} already exists.")

        if draft.price_list_id is not None:
            self._require_price_list(actor, draft.price_list_id)

        party = Party(
            business_id=actor.business_id,
            code=code,
            name=draft.name.strip(),
            legal_name=draft.legal_name or None,
            party_type=PartyType(str(draft.party_type)).value,
            is_customer=draft.is_customer,
            is_supplier=draft.is_supplier,
            is_affiliate=draft.is_affiliate,
            email=validate_email(draft.email),
            telephone=validate_telephone(draft.telephone),
            website=draft.website or None,
            tax_identifier=draft.tax_identifier or None,
            registration_number=draft.registration_number or None,
            tax_treatment=treatment.value,
            tax_exemption_reference=draft.tax_exemption_reference or None,
            tax_exemption_expires=draft.tax_exemption_expires,
            payment_term_kind=terms.kind.value,
            payment_term_days=terms.days,
            credit_limit=draft.credit_limit,
            price_list_fk=draft.price_list_id,
            discount_percent=draft.discount_percent,
            default_lead_time_days=draft.default_lead_time_days,
            bank_name=draft.bank_name or None,
            bank_account_name=draft.bank_account_name or None,
            bank_account_number=draft.bank_account_number or None,
            mobile_money_provider=draft.mobile_money_provider or None,
            mobile_money_number=validate_telephone(
                draft.mobile_money_number, field="mobile_money_number"
            ),
            notes=draft.notes or None,
        )
        self._db.add(party)
        self._db.flush()

        for contact in draft.contacts:
            self.add_contact(actor, party.id, **contact)
        self._refresh_search_text(party)

        self._audit.record(
            actor,
            "party_created",
            entity_type="party",
            entity_id=party.id,
            entity_label=f"{party.code} — {party.name}",
            after={
                "code": party.code,
                "name": party.name,
                "roles": self._role_summary(party),
            },
        )
        return party

    def amend(self, actor: AnyActor, party_id: uuid.UUID, changes: dict[str, Any]) -> Party:
        """Amend a party."""
        party = self.require(actor, party_id)
        self._require_write_for(actor, party)
        before = as_dict(party)

        permitted = {
            "name",
            "legal_name",
            "party_type",
            "email",
            "telephone",
            "website",
            "tax_identifier",
            "registration_number",
            "tax_treatment",
            "tax_exemption_reference",
            "tax_exemption_expires",
            "payment_term_kind",
            "payment_term_days",
            "credit_limit",
            "price_list_id",
            "discount_percent",
            "default_lead_time_days",
            "bank_name",
            "bank_account_name",
            "bank_account_number",
            "mobile_money_provider",
            "mobile_money_number",
            "notes",
            "is_active",
            "is_customer",
            "is_supplier",
            "is_affiliate",
            "responsible_user_id",
        }
        unknown = set(changes) - permitted
        if unknown:
            raise ValidationError(f"These properties cannot be amended: {sorted(unknown)}")

        for key, value in changes.items():
            if key == "email":
                party.email = validate_email(value)
            elif key == "telephone":
                party.telephone = validate_telephone(value)
            elif key == "mobile_money_number":
                party.mobile_money_number = validate_telephone(value, field="mobile_money_number")
            elif key == "price_list_id":
                if value is not None:
                    self._require_price_list(actor, value)
                party.price_list_fk = value
            else:
                setattr(party, key, value)

        if not (party.is_customer or party.is_supplier or party.is_affiliate):
            raise ValidationError("A party must retain at least one role.", field="roles")
        assert_exemption_evidenced(
            TaxTreatment(party.tax_treatment),
            evidence_reference=party.tax_exemption_reference,
        )
        PaymentTerms(PaymentTermKind(party.payment_term_kind), party.payment_term_days)
        self._refresh_search_text(party)

        self._audit.record_change(
            actor,
            "party_amended",
            entity_type="party",
            entity_id=party.id,
            entity_label=f"{party.code} — {party.name}",
            before=before,
            after=as_dict(party),
        )
        return party

    def set_credit_hold(
        self, actor: AnyActor, party_id: uuid.UUID, *, on_hold: bool, reason: str
    ) -> Party:
        """Place a customer on hold, or release them. A reason is mandatory."""
        actor.require("customer.manage")
        if not reason.strip():
            raise ValidationError("A reason is required.", field="reason")
        party = self.require(actor, party_id)
        party.credit_on_hold = on_hold
        party.credit_hold_reason = reason.strip() if on_hold else None

        self._audit.record(
            actor,
            "credit_hold_placed" if on_hold else "credit_hold_released",
            entity_type="party",
            entity_id=party.id,
            entity_label=f"{party.code} — {party.name}",
            reason=reason.strip(),
        )
        return party

    # -- contacts ---------------------------------------------------------------------

    def add_contact(
        self,
        actor: AnyActor,
        party_id: uuid.UUID,
        *,
        name: str,
        position: str | None = None,
        email: str | None = None,
        telephone: str | None = None,
        is_primary: bool = False,
        notes: str | None = None,
    ) -> Contact:
        party = self.require(actor, party_id)
        self._require_write_for(actor, party)
        if not name.strip():
            raise ValidationError("A contact name is required.", field="name")

        if is_primary:
            for existing in party.contacts:
                existing.is_primary = False

        contact = Contact(
            business_id=actor.business_id,
            party_fk=party.id,
            name=name.strip(),
            position=position or None,
            email=validate_email(email),
            telephone=validate_telephone(telephone),
            is_primary=is_primary or not party.contacts,
            notes=notes or None,
        )
        # Appended through the relationship, so the loaded collection stays correct;
        # a plain add() would leave party.contacts stale until the session expires.
        party.contacts.append(contact)
        self._db.flush()
        return contact

    def remove_contact(self, actor: AnyActor, contact_id: uuid.UUID) -> None:
        contact = self._db.scalar(
            select(Contact).where(
                Contact.business_id == actor.business_id, Contact.id == contact_id
            )
        )
        if contact is None:
            raise NotFoundError("Contact not found.")
        party = self.require(actor, contact.party_fk)
        self._require_write_for(actor, party)
        self._db.delete(contact)
        self._db.flush()

    # -- credit -----------------------------------------------------------------------

    def credit_standing(
        self,
        actor: AnyActor,
        party_id: uuid.UUID,
        *,
        outstanding: Decimal = ZERO,
        overdue: Decimal = ZERO,
    ) -> CreditAssessment:
        """Assess a customer's credit position.

        Outstanding and overdue are supplied by the caller. The receivables register of
        Phase 11 will supply them; until then they default to zero, which reports the
        configured limit without asserting a balance the system has not recorded.
        """
        actor.require("customer.view")
        party = self.require(actor, party_id)
        business = self._db.get(Business, actor.business_id)
        return assess_credit(
            limit=party.credit_limit,
            outstanding=outstanding,
            overdue=overdue,
            on_hold=party.credit_on_hold,
            currency_symbol=f"{business.currency_symbol} " if business else "",
        )

    # -- supplier catalogue -----------------------------------------------------------

    def link_supplier_product(
        self,
        actor: AnyActor,
        *,
        supplier_id: uuid.UUID,
        product_id: uuid.UUID,
        supplier_sku: str | None = None,
        supplier_name: str | None = None,
        unit_id: uuid.UUID | None = None,
        units_per_purchase_unit: Decimal = Decimal("1"),
        last_price: Decimal | None = None,
        currency_code: str | None = None,
        lead_time_days: int | None = None,
        minimum_order_quantity: Decimal | None = None,
        is_preferred: bool = False,
        notes: str | None = None,
    ) -> SupplierProduct:
        """Record what a supplier calls a product and on what terms they supply it."""
        actor.require("supplier.manage")
        supplier = self.require(actor, supplier_id)
        if not supplier.is_supplier:
            raise ValidationError(
                f"{supplier.name} is not recorded as a supplier.", field="supplier_id"
            )
        if units_per_purchase_unit <= ZERO:
            raise ValidationError(
                "Units per purchase unit must be greater than zero.",
                field="units_per_purchase_unit",
            )
        product = self._db.scalar(
            select(Product).where(
                Product.business_id == actor.business_id, Product.id == product_id
            )
        )
        if product is None:
            raise NotFoundError("Product not found.")

        link = self._db.scalar(
            select(SupplierProduct).where(
                SupplierProduct.supplier_fk == supplier_id,
                SupplierProduct.product_fk == product_id,
            )
        )
        if link is None:
            link = SupplierProduct(
                business_id=actor.business_id,
                supplier_fk=supplier_id,
                product_fk=product_id,
            )
            self._db.add(link)

        link.supplier_sku = supplier_sku or link.supplier_sku
        link.supplier_name = supplier_name or link.supplier_name
        link.unit_fk = unit_id or link.unit_fk
        link.units_per_purchase_unit = units_per_purchase_unit
        link.lead_time_days = lead_time_days
        link.minimum_order_quantity = minimum_order_quantity
        link.notes = notes or link.notes

        if last_price is not None:
            link.last_price = last_price
            link.currency_code = currency_code or link.currency_code
            # Recorded, so a stale price is visible as stale rather than trusted.
            link.price_recorded_on = self._clock.now().date()

        if is_preferred:
            for other in self._db.scalars(
                select(SupplierProduct).where(
                    SupplierProduct.business_id == actor.business_id,
                    SupplierProduct.product_fk == product_id,
                    SupplierProduct.id != link.id,
                )
            ).all():
                other.is_preferred = False
        link.is_preferred = is_preferred

        self._db.flush()
        return link

    def suppliers_of(self, actor: AnyActor, product_id: uuid.UUID) -> list[SupplierProduct]:
        actor.require("supplier.view")
        return list(
            self._db.scalars(
                select(SupplierProduct)
                .where(
                    SupplierProduct.business_id == actor.business_id,
                    SupplierProduct.product_fk == product_id,
                    SupplierProduct.is_active.is_(True),
                )
                .order_by(SupplierProduct.is_preferred.desc())
            )
        )

    # -- customer pricing -------------------------------------------------------------

    def set_customer_price(
        self,
        actor: AnyActor,
        *,
        customer_id: uuid.UUID,
        product_id: uuid.UUID,
        price: Decimal,
        unit_id: uuid.UUID | None = None,
        minimum_quantity: Decimal = ZERO,
        currency_code: str | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        agreement_reference: str | None = None,
    ) -> CustomerPrice:
        """Record a price agreed with one customer, per PRD 6.10."""
        actor.require("price_list.manage")
        customer = self.require(actor, customer_id)
        if not customer.is_customer:
            raise ValidationError(
                f"{customer.name} is not recorded as a customer.", field="customer_id"
            )
        if price < ZERO:
            raise ValidationError("A price cannot be negative.", field="price")
        if effective_from and effective_to and effective_to < effective_from:
            raise ValidationError(
                "The end of an agreement cannot precede its beginning.",
                field="effective_to",
            )

        row = self._db.scalar(
            select(CustomerPrice).where(
                CustomerPrice.customer_fk == customer_id,
                CustomerPrice.product_fk == product_id,
                CustomerPrice.minimum_quantity == minimum_quantity,
                CustomerPrice.effective_from == effective_from,
            )
        )
        if row is None:
            row = CustomerPrice(
                business_id=actor.business_id,
                customer_fk=customer_id,
                product_fk=product_id,
                minimum_quantity=minimum_quantity,
                effective_from=effective_from,
            )
            self._db.add(row)

        row.price = price
        row.unit_fk = unit_id
        row.currency_code = currency_code
        row.effective_to = effective_to
        row.agreement_reference = agreement_reference or None
        self._db.flush()

        self._audit.record(
            actor,
            "customer_price_set",
            entity_type="customer_price",
            entity_id=row.id,
            entity_label=f"{customer.code} / {product_id}",
            after={"price": str(price), "minimum_quantity": str(minimum_quantity)},
        )
        return row

    # -- affiliates -------------------------------------------------------------------

    def set_affiliate_profile(
        self,
        actor: AnyActor,
        *,
        party_id: uuid.UUID,
        scope: str,
        method: str,
        rate: Decimal = ZERO,
        fixed_amount: Decimal | None = None,
        currency_code: str | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        payout_trigger: str = "customer_payment",
        agreement_reference: str | None = None,
        eligible_product_ids: list[uuid.UUID] | None = None,
        eligible_category_ids: list[uuid.UUID] | None = None,
        notes: str | None = None,
    ) -> AffiliateProfile:
        """Record a referrer's commission arrangement, per PRD 6.4 and ADR-0008."""
        actor.require("affiliate.manage")
        party = self.require(actor, party_id)
        if not party.is_affiliate:
            raise ValidationError(
                f"{party.name} is not recorded as an affiliate.", field="party_id"
            )

        if scope not in COMMISSION_SCOPES:
            raise ValidationError(f"Unknown commission scope: {scope!r}", field="scope")
        if method not in {"percentage", "fixed"}:
            raise ValidationError(f"Unknown commission method: {method!r}", field="method")
        if method == "percentage" and not (ZERO <= rate < Decimal("1")):
            raise ValidationError(
                "A percentage commission is a fraction below one, such as 0.05 for five per cent.",
                field="rate",
            )
        if method == "fixed" and (fixed_amount is None or fixed_amount < ZERO):
            raise ValidationError(
                "A fixed commission requires an amount of zero or more.",
                field="fixed_amount",
            )
        if scope == "selected_products" and not eligible_product_ids:
            raise ValidationError(
                "Select at least one product for this scope.", field="eligible_product_ids"
            )
        if scope == "selected_categories" and not eligible_category_ids:
            raise ValidationError(
                "Select at least one category for this scope.",
                field="eligible_category_ids",
            )

        profile = self._db.scalar(
            select(AffiliateProfile).where(
                AffiliateProfile.business_id == actor.business_id,
                AffiliateProfile.party_fk == party_id,
                AffiliateProfile.effective_from == effective_from,
            )
        )
        if profile is None:
            profile = AffiliateProfile(
                business_id=actor.business_id,
                party_fk=party_id,
                effective_from=effective_from,
            )
            self._db.add(profile)

        before = as_dict(profile) if profile.id else {}
        profile.scope = scope
        profile.method = method
        profile.rate = rate if method == "percentage" else ZERO
        profile.fixed_amount = fixed_amount if method == "fixed" else None
        profile.currency_code = currency_code
        profile.effective_to = effective_to
        profile.payout_trigger = payout_trigger
        profile.agreement_reference = agreement_reference or None
        profile.notes = notes or None
        self._db.flush()

        for existing in list(profile.eligible_products):
            self._db.delete(existing)
        self._db.flush()
        for product_id in eligible_product_ids or []:
            profile.eligible_products.append(
                AffiliateEligibility(
                    business_id=actor.business_id,
                    profile_fk=profile.id,
                    product_fk=product_id,
                )
            )
        for category_id in eligible_category_ids or []:
            profile.eligible_products.append(
                AffiliateEligibility(
                    business_id=actor.business_id,
                    profile_fk=profile.id,
                    category_fk=category_id,
                )
            )
        self._db.flush()

        self._audit.record(
            actor,
            "affiliate_profile_set",
            entity_type="affiliate_profile",
            entity_id=profile.id,
            entity_label=f"{party.code} — {party.name}",
            before=before or None,
            after={"scope": scope, "method": method, "rate": str(rate)},
        )
        return profile

    def affiliate_profile(self, actor: AnyActor, party_id: uuid.UUID) -> AffiliateProfile | None:
        actor.require("affiliate.view")
        return self._db.scalar(
            select(AffiliateProfile)
            .where(
                AffiliateProfile.business_id == actor.business_id,
                AffiliateProfile.party_fk == party_id,
                AffiliateProfile.is_active.is_(True),
            )
            .order_by(AffiliateProfile.effective_from.desc().nullslast())
            .limit(1)
        )

    # -- retrieval --------------------------------------------------------------------

    def require(self, actor: AnyActor, party_id: uuid.UUID) -> Party:
        party = self._db.scalar(
            select(Party).where(Party.business_id == actor.business_id, Party.id == party_id)
        )
        if party is None:
            raise NotFoundError("Party not found.")
        return party

    def by_code(self, actor: AnyActor, code: str) -> Party | None:
        return self._db.scalar(
            select(Party).where(
                Party.business_id == actor.business_id, Party.code == code.strip().upper()
            )
        )

    def search(
        self,
        actor: AnyActor,
        *,
        term: str | None = None,
        kind: PartyKind | str | None = None,
        include_inactive: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> PartyPage:
        """Search parties by name, code, tax identifier, telephone or contact."""
        actor.require_any("customer.view", "supplier.view", "affiliate.view")
        statement: Select[tuple[Party]] = select(Party).where(
            Party.business_id == actor.business_id
        )
        if not include_inactive:
            statement = statement.where(Party.is_active.is_(True))
        if kind is not None:
            column = {
                PartyKind.CUSTOMER.value: Party.is_customer,
                PartyKind.SUPPLIER.value: Party.is_supplier,
                PartyKind.AFFILIATE.value: Party.is_affiliate,
            }[str(kind)]
            statement = statement.where(column.is_(True))
        if term and term.strip():
            statement = statement.where(Party.search_text.like(f"%{term.strip().lower()}%"))

        total = self._db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(self._db.scalars(statement.order_by(Party.name).offset(offset).limit(limit)))
        return PartyPage(items=rows, total=total, offset=offset, limit=limit)

    # -- internals --------------------------------------------------------------------

    def _primary_kind(self, draft: PartyDraft) -> PartyKind:
        if draft.is_customer:
            return PartyKind.CUSTOMER
        if draft.is_supplier:
            return PartyKind.SUPPLIER
        return PartyKind.AFFILIATE

    def _next_sequence(self, actor: AnyActor) -> int:
        count = self._db.scalar(
            select(func.count()).select_from(Party).where(Party.business_id == actor.business_id)
        )
        return int(count or 0) + 1

    def _require_write(self, actor: AnyActor, draft: PartyDraft) -> None:
        """A party is written by whoever may manage the role it is being given."""
        if draft.is_customer:
            actor.require("customer.manage")
        if draft.is_supplier:
            actor.require("supplier.manage")
        if draft.is_affiliate:
            actor.require("affiliate.manage")

    def _require_write_for(self, actor: AnyActor, party: Party) -> None:
        permissions = []
        if party.is_customer:
            permissions.append("customer.manage")
        if party.is_supplier:
            permissions.append("supplier.manage")
        if party.is_affiliate:
            permissions.append("affiliate.manage")
        actor.require_any(*permissions)

    def _require_price_list(self, actor: AnyActor, price_list_id: uuid.UUID) -> PriceList:
        price_list = self._db.scalar(
            select(PriceList).where(
                PriceList.business_id == actor.business_id, PriceList.id == price_list_id
            )
        )
        if price_list is None:
            raise NotFoundError("Price list not found.")
        return price_list

    def _role_summary(self, party: Party) -> str:
        roles = [
            label
            for label, held in (
                ("customer", party.is_customer),
                ("supplier", party.is_supplier),
                ("affiliate", party.is_affiliate),
            )
            if held
        ]
        return ", ".join(roles)

    def _refresh_search_text(self, party: Party) -> None:
        self._db.flush()
        contacts = self._db.scalars(select(Contact.name).where(Contact.party_fk == party.id)).all()
        parts = [
            party.code,
            party.name,
            party.legal_name or "",
            party.tax_identifier or "",
            party.telephone or "",
            party.email or "",
            *contacts,
        ]
        party.search_text = " ".join(part for part in parts if part).lower()[:600]
