"""Sourcing endpoints: customer requests, supplier requests and the comparison."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nickland.application.services.sourcing import (
    RequestDraft,
    RequestLineDraft,
    SupplierQuotationDraft,
    SupplierQuotationLineDraft,
)
from nickland.interface.dependencies import ActorDependency, PartyDependency, SourcingDependency
from nickland.interface.pricing_schemas import (
    ComparisonReply,
    CreateRequestContract,
    CreateSupplierRequestContract,
    OfferReply,
    RecordSupplierQuotationContract,
    RequestPageReply,
    RequestReply,
    RequestStateContract,
    SupplierQuotationReply,
    SupplierRequestReply,
)

router = APIRouter(prefix="/sourcing", tags=["sourcing"])


@router.get("/requests", response_model=RequestPageReply, summary="List customer requests")
def list_requests(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    state: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> RequestPageReply:
    items, total = sourcing.requests(actor, state=state, limit=limit, offset=offset)
    return RequestPageReply(
        items=[_request(actor, parties, item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/requests", response_model=RequestReply, summary="Record a customer request")
def create_request(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    body: CreateRequestContract,
) -> RequestReply:
    request = sourcing.create_request(
        actor,
        RequestDraft(
            customer_id=body.customer_id,
            received_on=body.received_on,
            contact_id=body.contact_id,
            customer_reference=body.customer_reference,
            required_by=body.required_by,
            delivery_address=body.delivery_address,
            currency_code=body.currency_code,
            notes=body.notes,
            lines=[
                RequestLineDraft(
                    description=line.description,
                    quantity=line.quantity,
                    product_id=line.product_id,
                    specification=line.specification,
                    unit_id=line.unit_id,
                    required_by=line.required_by,
                    notes=line.notes,
                )
                for line in body.lines
            ],
        ),
    )
    return _request(actor, parties, request)


@router.get("/requests/{request_id}", response_model=RequestReply, summary="Read a request")
def read_request(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    request_id: uuid.UUID,
) -> RequestReply:
    return _request(actor, parties, sourcing.require_request(actor, request_id))


@router.post(
    "/requests/{request_id}/state", response_model=RequestReply, summary="Move a request on"
)
def set_request_state(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    request_id: uuid.UUID,
    body: RequestStateContract,
) -> RequestReply:
    request = sourcing.set_request_state(actor, request_id, body.state, reason=body.reason)
    return _request(actor, parties, request)


@router.get(
    "/requests/{request_id}/comparison",
    response_model=list[ComparisonReply],
    summary="Compare supplier offers",
)
def comparison(
    actor: ActorDependency, sourcing: SourcingDependency, request_id: uuid.UUID
) -> list[ComparisonReply]:
    return [
        ComparisonReply(
            request_line_id=item.request_line_id,
            description=item.description,
            quantity=item.quantity,
            offers=[OfferReply.model_validate(offer) for offer in item.offers],
            best_offer_id=item.best.supplier_quotation_line_id if item.best else None,
        )
        for item in sourcing.compare(actor, request_id)
    ]


@router.get(
    "/supplier-requests",
    response_model=list[SupplierRequestReply],
    summary="List supplier requests",
)
def list_supplier_requests(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    request_id: uuid.UUID | None = None,
) -> list[SupplierRequestReply]:
    return [
        _supplier_request(actor, parties, item)
        for item in sourcing.supplier_requests(actor, request_id=request_id)
    ]


@router.post(
    "/supplier-requests",
    response_model=SupplierRequestReply,
    summary="Put a request to a supplier",
)
def create_supplier_request(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    body: CreateSupplierRequestContract,
) -> SupplierRequestReply:
    created = sourcing.create_supplier_request(
        actor,
        supplier_id=body.supplier_id,
        request_id=body.request_id,
        request_line_ids=body.request_line_ids,
        respond_by=body.respond_by,
        currency_code=body.currency_code,
        notes=body.notes,
    )
    return _supplier_request(actor, parties, created)


@router.post(
    "/supplier-requests/{supplier_request_id}/issue",
    response_model=SupplierRequestReply,
    summary="Issue a supplier request",
)
def issue_supplier_request(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    supplier_request_id: uuid.UUID,
) -> SupplierRequestReply:
    issued = sourcing.issue_supplier_request(actor, supplier_request_id)
    return _supplier_request(actor, parties, issued)


@router.get(
    "/supplier-quotations",
    response_model=list[SupplierQuotationReply],
    summary="List supplier quotations",
)
def list_supplier_quotations(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    request_id: uuid.UUID | None = None,
) -> list[SupplierQuotationReply]:
    return [
        _supplier_quotation(actor, parties, item)
        for item in sourcing.supplier_quotations(actor, request_id=request_id)
    ]


@router.post(
    "/supplier-quotations",
    response_model=SupplierQuotationReply,
    summary="Record a supplier quotation",
)
def record_supplier_quotation(
    actor: ActorDependency,
    sourcing: SourcingDependency,
    parties: PartyDependency,
    body: RecordSupplierQuotationContract,
) -> SupplierQuotationReply:
    recorded = sourcing.record_supplier_quotation(
        actor,
        SupplierQuotationDraft(
            supplier_id=body.supplier_id,
            reference=body.reference,
            received_on=body.received_on,
            supplier_request_id=body.supplier_request_id,
            request_id=body.request_id,
            valid_until=body.valid_until,
            currency_code=body.currency_code,
            exchange_rate=body.exchange_rate,
            lead_time_days=body.lead_time_days,
            freight_amount=body.freight_amount,
            other_charges=body.other_charges,
            payment_terms=body.payment_terms,
            tax_treatment=body.tax_treatment,
            notes=body.notes,
            lines=[
                SupplierQuotationLineDraft(
                    description=line.description,
                    quantity_offered=line.quantity_offered,
                    unit_cost=line.unit_cost,
                    request_line_id=line.request_line_id,
                    product_id=line.product_id,
                    discount_rate=line.discount_rate,
                    recoverable_tax_rate=line.recoverable_tax_rate,
                    non_recoverable_tax_rate=line.non_recoverable_tax_rate,
                    lead_time_days=line.lead_time_days,
                    minimum_order_quantity=line.minimum_order_quantity,
                    is_available=line.is_available,
                    availability_note=line.availability_note,
                    notes=line.notes,
                )
                for line in body.lines
            ],
        ),
    )
    return _supplier_quotation(actor, parties, recorded)


# -- projection ------------------------------------------------------------------------


def _name(
    actor: ActorDependency, parties: PartyDependency, party_id: uuid.UUID | None
) -> str | None:
    if party_id is None:
        return None
    try:
        return parties.require(actor, party_id).name
    except Exception:
        return None


def _request(actor: ActorDependency, parties: PartyDependency, row: object) -> RequestReply:
    reply = RequestReply.model_validate(row)
    reply.customer_name = _name(actor, parties, reply.customer_fk)
    return reply


def _supplier_request(
    actor: ActorDependency, parties: PartyDependency, row: object
) -> SupplierRequestReply:
    reply = SupplierRequestReply.model_validate(row)
    reply.supplier_name = _name(actor, parties, reply.supplier_fk)
    return reply


def _supplier_quotation(
    actor: ActorDependency, parties: PartyDependency, row: object
) -> SupplierQuotationReply:
    reply = SupplierQuotationReply.model_validate(row)
    reply.supplier_name = _name(actor, parties, reply.supplier_fk)
    return reply
