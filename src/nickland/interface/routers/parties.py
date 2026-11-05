"""Party endpoints: customers, suppliers, affiliates, contacts and terms."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from nickland.application.services.parties import PartyDraft
from nickland.domain.parties import PaymentTermKind, PaymentTerms
from nickland.interface.catalogue_schemas import (
    AffiliateProfileReply,
    AmendPartyContract,
    ContactContract,
    ContactReply,
    CreatePartyContract,
    CreditHoldContract,
    CreditReply,
    CustomerPriceContract,
    LinkSupplierProductContract,
    PartyPageReply,
    PartyReply,
    SetAffiliateProfileContract,
    SupplierProductReply,
)
from nickland.interface.dependencies import ActorDependency, PartyDependency

router = APIRouter(tags=["parties"])


def _party_reply(party: object) -> PartyReply:
    reply = PartyReply.model_validate(party)
    reply.payment_terms_label = PaymentTerms(
        PaymentTermKind(reply.payment_term_kind), reply.payment_term_days
    ).describe()
    return reply


@router.get("/parties", response_model=PartyPageReply, summary="Search parties")
def search_parties(
    actor: ActorDependency,
    parties: PartyDependency,
    term: str | None = None,
    kind: str | None = None,
    include_inactive: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PartyPageReply:
    """Search by name, code, tax identifier, telephone or contact name."""
    page = parties.search(
        actor,
        term=term,
        kind=kind,
        include_inactive=include_inactive,
        offset=offset,
        limit=limit,
    )
    return PartyPageReply(
        items=[_party_reply(party) for party in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.post("/parties", response_model=PartyReply, status_code=201, summary="Add a party")
def create_party(
    contract: CreatePartyContract, actor: ActorDependency, parties: PartyDependency
) -> PartyReply:
    payload = contract.model_dump()
    contacts = payload.pop("contacts", [])
    draft = PartyDraft(**payload, contacts=contacts)
    return _party_reply(parties.create(actor, draft))


@router.get("/parties/{party_id}", response_model=PartyReply, summary="Read a party")
def read_party(party_id: uuid.UUID, actor: ActorDependency, parties: PartyDependency) -> PartyReply:
    actor.require_any("customer.view", "supplier.view", "affiliate.view")
    return _party_reply(parties.require(actor, party_id))


@router.patch("/parties/{party_id}", response_model=PartyReply, summary="Amend a party")
def amend_party(
    party_id: uuid.UUID,
    contract: AmendPartyContract,
    actor: ActorDependency,
    parties: PartyDependency,
) -> PartyReply:
    return _party_reply(parties.amend(actor, party_id, contract.model_dump(exclude_unset=True)))


@router.post(
    "/parties/{party_id}/credit-hold",
    response_model=PartyReply,
    summary="Place or release a credit hold",
)
def set_credit_hold(
    party_id: uuid.UUID,
    contract: CreditHoldContract,
    actor: ActorDependency,
    parties: PartyDependency,
) -> PartyReply:
    """A hold withholds credit whatever the balance. A reason is mandatory."""
    return _party_reply(
        parties.set_credit_hold(actor, party_id, on_hold=contract.on_hold, reason=contract.reason)
    )


@router.get(
    "/parties/{party_id}/credit", response_model=CreditReply, summary="Assess credit standing"
)
def credit_standing(
    party_id: uuid.UUID,
    actor: ActorDependency,
    parties: PartyDependency,
    outstanding: Decimal = Decimal("0"),
    overdue: Decimal = Decimal("0"),
) -> CreditReply:
    """Assess a customer's credit position.

    Outstanding and overdue are supplied by the caller until the receivables register
    of Phase 11 exists; they default to zero rather than asserting a balance the
    system has not recorded.
    """
    assessment = parties.credit_standing(actor, party_id, outstanding=outstanding, overdue=overdue)
    return CreditReply(
        standing=assessment.standing.value,
        limit=assessment.limit,
        outstanding=assessment.outstanding,
        overdue=assessment.overdue,
        available=assessment.available,
        detail=assessment.detail,
        permits_credit_sale=assessment.permits_credit_sale,
    )


@router.post(
    "/parties/{party_id}/contacts",
    response_model=ContactReply,
    status_code=201,
    summary="Add a contact",
)
def add_contact(
    party_id: uuid.UUID,
    contract: ContactContract,
    actor: ActorDependency,
    parties: PartyDependency,
) -> ContactReply:
    return ContactReply.model_validate(
        parties.add_contact(actor, party_id, **contract.model_dump())
    )


@router.delete(
    "/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a contact",
)
def remove_contact(
    contact_id: uuid.UUID, actor: ActorDependency, parties: PartyDependency
) -> Response:
    parties.remove_contact(actor, contact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/products/{product_id}/suppliers",
    response_model=list[SupplierProductReply],
    summary="List a product's suppliers",
)
def suppliers_of(
    product_id: uuid.UUID, actor: ActorDependency, parties: PartyDependency
) -> list[SupplierProductReply]:
    return [
        SupplierProductReply.model_validate(link)
        for link in parties.suppliers_of(actor, product_id)
    ]


@router.put(
    "/supplier-products",
    response_model=SupplierProductReply,
    summary="Link a supplier to a product",
)
def link_supplier_product(
    contract: LinkSupplierProductContract,
    actor: ActorDependency,
    parties: PartyDependency,
) -> SupplierProductReply:
    """Record what a supplier calls a product and on what terms they supply it."""
    return SupplierProductReply.model_validate(
        parties.link_supplier_product(actor, **contract.model_dump())
    )


@router.put("/customer-prices", response_model=dict[str, str], summary="Agree a customer price")
def set_customer_price(
    contract: CustomerPriceContract, actor: ActorDependency, parties: PartyDependency
) -> dict[str, str]:
    row = parties.set_customer_price(actor, **contract.model_dump())
    return {"id": str(row.id), "price": str(row.price)}


@router.get(
    "/parties/{party_id}/affiliate",
    response_model=AffiliateProfileReply,
    summary="Read an affiliate's commission arrangement",
)
def affiliate_profile(
    party_id: uuid.UUID, actor: ActorDependency, parties: PartyDependency
) -> AffiliateProfileReply:
    from nickland.domain.errors import NotFoundError

    profile = parties.affiliate_profile(actor, party_id)
    if profile is None:
        raise NotFoundError("No commission arrangement is recorded for this affiliate.")
    reply = AffiliateProfileReply.model_validate(profile)
    reply.eligible_product_ids = [
        row.product_fk for row in profile.eligible_products if row.product_fk
    ]
    reply.eligible_category_ids = [
        row.category_fk for row in profile.eligible_products if row.category_fk
    ]
    return reply


@router.put(
    "/affiliate-profiles",
    response_model=AffiliateProfileReply,
    summary="Set an affiliate's commission arrangement",
)
def set_affiliate_profile(
    contract: SetAffiliateProfileContract,
    actor: ActorDependency,
    parties: PartyDependency,
) -> AffiliateProfileReply:
    """Record scope, method, rate and payout trigger, per PRD 6.4 and ADR-0008."""
    profile = parties.set_affiliate_profile(actor, **contract.model_dump())
    reply = AffiliateProfileReply.model_validate(profile)
    reply.eligible_product_ids = [
        row.product_fk for row in profile.eligible_products if row.product_fk
    ]
    reply.eligible_category_ids = [
        row.category_fk for row in profile.eligible_products if row.category_fk
    ]
    return reply
