"""Affiliate commission endpoints: the register and its states."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nickland.interface.dependencies import (
    ActorDependency,
    CommissionDependency,
    PartyDependency,
    QuotationDependency,
)
from nickland.interface.pricing_schemas import (
    AccrualReply,
    AccrueContract,
    CancelCommissionContract,
    CollectionContract,
    CommissionTotalsReply,
    OverrideCommissionContract,
    PayCommissionContract,
)
from nickland.persistence.pricing_models import CommissionAccrual

router = APIRouter(prefix="/commissions", tags=["commissions"])


@router.get("", response_model=list[AccrualReply], summary="The commission register")
def register(
    actor: ActorDependency,
    commissions: CommissionDependency,
    parties: PartyDependency,
    quotations: QuotationDependency,
    state: str | None = None,
    affiliate_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[AccrualReply]:
    rows = commissions.register(actor, state=state, affiliate_id=affiliate_id, limit=limit)
    return [_accrual(actor, parties, quotations, row) for row in rows]


@router.get("/totals", response_model=CommissionTotalsReply, summary="Register totals")
def totals(actor: ActorDependency, commissions: CommissionDependency) -> CommissionTotalsReply:
    return CommissionTotalsReply.model_validate(commissions.totals(actor))


@router.post("/accrue", response_model=list[AccrualReply], summary="Accrue on an invoice")
def accrue(
    actor: ActorDependency,
    commissions: CommissionDependency,
    parties: PartyDependency,
    quotations: QuotationDependency,
    body: AccrueContract,
) -> list[AccrualReply]:
    rows = commissions.accrue_for_quotation(
        actor,
        body.quotation_id,
        source_reference=body.source_reference,
        fulfilled_fraction=body.fulfilled_fraction,
        on=body.on,
    )
    return [_accrual(actor, parties, quotations, row) for row in rows]


@router.post("/{accrual_id}/approve", response_model=AccrualReply, summary="Approve an entitlement")
def approve(
    actor: ActorDependency,
    commissions: CommissionDependency,
    parties: PartyDependency,
    quotations: QuotationDependency,
    accrual_id: uuid.UUID,
) -> AccrualReply:
    return _accrual(actor, parties, quotations, commissions.approve(actor, accrual_id))


@router.post(
    "/{accrual_id}/collection", response_model=AccrualReply, summary="Record customer collection"
)
def collection(
    actor: ActorDependency,
    commissions: CommissionDependency,
    parties: PartyDependency,
    quotations: QuotationDependency,
    accrual_id: uuid.UUID,
    body: CollectionContract,
) -> AccrualReply:
    row = commissions.record_payment_received(actor, accrual_id, paid_fraction=body.paid_fraction)
    return _accrual(actor, parties, quotations, row)


@router.post("/{accrual_id}/pay", response_model=AccrualReply, summary="Pay the referrer")
def pay(
    actor: ActorDependency,
    commissions: CommissionDependency,
    parties: PartyDependency,
    quotations: QuotationDependency,
    accrual_id: uuid.UUID,
    body: PayCommissionContract,
) -> AccrualReply:
    row = commissions.pay(actor, accrual_id, amount=body.amount, reference=body.reference)
    return _accrual(actor, parties, quotations, row)


@router.post("/{accrual_id}/cancel", response_model=AccrualReply, summary="Cancel an entitlement")
def cancel(
    actor: ActorDependency,
    commissions: CommissionDependency,
    parties: PartyDependency,
    quotations: QuotationDependency,
    accrual_id: uuid.UUID,
    body: CancelCommissionContract,
) -> AccrualReply:
    row = commissions.cancel(actor, accrual_id, reason=body.reason)
    return _accrual(actor, parties, quotations, row)


@router.post("/{accrual_id}/override", response_model=AccrualReply, summary="Override the rate")
def override(
    actor: ActorDependency,
    commissions: CommissionDependency,
    parties: PartyDependency,
    quotations: QuotationDependency,
    accrual_id: uuid.UUID,
    body: OverrideCommissionContract,
) -> AccrualReply:
    row = commissions.override_rate(actor, accrual_id, rate=body.rate, reason=body.reason)
    return _accrual(actor, parties, quotations, row)


def _accrual(
    actor: ActorDependency,
    parties: PartyDependency,
    quotations: QuotationDependency,
    row: CommissionAccrual,
) -> AccrualReply:
    reply = AccrualReply.model_validate(row)
    try:
        reply.affiliate_name = parties.require(actor, row.affiliate_fk).name
    except Exception:
        reply.affiliate_name = None
    if row.quotation_fk is not None:
        try:
            reply.quotation_number = quotations.require(actor, row.quotation_fk).number
        except Exception:
            reply.quotation_number = None
    return reply
