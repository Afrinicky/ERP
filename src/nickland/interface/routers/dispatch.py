"""Dispatch endpoints: shipments, their documents, and proof of delivery."""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter

from nickland.application.services.dispatch import (
    DeliveryDraft,
    DispatchDraft,
    OutcomeDraft,
    PrepareDraft,
    ShipmentLineDraft,
)
from nickland.domain.dispatch import DispatchMode, ExceptionKind, ShipmentState
from nickland.domain.documents import DocumentKind
from nickland.domain.errors import ValidationError
from nickland.domain.money import ZERO
from nickland.interface.dependencies import (
    ActorDependency,
    AssemblyDependency,
    DispatchDependency,
    DocumentDependency,
    InventoryReportDependency,
)
from nickland.interface.dispatch_schemas import (
    CancelShipmentContract,
    CustomerReturnContract,
    DeliveryContract,
    DeliveryExceptionReply,
    DispatchContract,
    DocumentLinkReply,
    PackContract,
    PrepareContract,
    ProofReply,
    ShipmentLineReply,
    ShipmentReply,
    ShippableLineReply,
)
from nickland.interface.document_schemas import IssuedDocumentReply
from nickland.persistence.dispatch_models import Shipment

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


def _link(
    reports: InventoryReportDependency, document_id: uuid.UUID | None
) -> DocumentLinkReply | None:
    found = reports.issued_document(document_id)
    return DocumentLinkReply(id=found.id, number=found.number, kind=found.kind) if found else None


def _reply(reports: InventoryReportDependency, shipment: Shipment) -> ShipmentReply:
    proof = reports.acknowledgement(shipment.acknowledgement_fk)
    positions = {line.id: line.position for line in shipment.lines}
    return ShipmentReply(
        id=shipment.id,
        number=shipment.number,
        order_id=shipment.sales_order_fk,
        order_number=reports.order_number(shipment.sales_order_fk),
        customer=reports.party_name(shipment.customer_fk),
        warehouse=reports.warehouse_code(shipment.warehouse_fk),
        state=shipment.state,
        state_label=ShipmentState(shipment.state).label,
        mode=shipment.mode,
        mode_label=DispatchMode(shipment.mode).label,
        carrier=shipment.carrier,
        driver=shipment.driver,
        vehicle=shipment.vehicle,
        destination=shipment.destination,
        package_count=shipment.package_count,
        tracking_reference=shipment.tracking_reference,
        prepared_on=shipment.prepared_on,
        packed_on=shipment.packed_on,
        dispatched_on=shipment.dispatched_on,
        delivered_on=shipment.delivered_on,
        notes=shipment.notes,
        pick_list=_link(reports, shipment.pick_list_fk),
        packing_list=_link(reports, shipment.packing_list_fk),
        waybill=_link(reports, shipment.waybill_fk),
        delivery_note=_link(reports, shipment.delivery_note_fk),
        proof=(
            ProofReply(
                recipient_name=proof.recipient_name,
                recipient_role=proof.recipient_role,
                acknowledged_at=proof.acknowledged_at,
                location=proof.location,
                evidence_kind=proof.evidence_kind,
                has_evidence=proof.evidence is not None,
                evidence_hash=proof.evidence_hash,
            )
            if proof
            else None
        ),
        lines=[
            ShipmentLineReply(
                id=line.id,
                position=line.position,
                sales_order_line_id=line.sales_order_line_fk,
                sku=reports.product(line.product_fk).sku,
                description=reports.order_line_description(line.sales_order_line_fk),
                tracking=reports.product(line.product_fk).tracking_policy,
                quantity=line.quantity,
                delivered=line.quantity_delivered,
                returned=line.quantity_returned,
                damaged=line.quantity_damaged,
                lost=line.quantity_lost,
                customer_returned=line.quantity_customer_returned,
                serials=(line.serials or "").splitlines(),
            )
            for line in shipment.lines
        ],
        exceptions=[
            DeliveryExceptionReply(
                id=item.id,
                line_position=positions.get(item.shipment_line_fk, 0),
                kind=item.kind,
                kind_label=ExceptionKind(item.kind).label,
                quantity=item.quantity,
                reason=item.reason,
                replacement_owed=item.replacement_owed,
                occurred_on=item.occurred_on,
            )
            for item in shipment.exceptions
        ],
    )


@router.get("/shipments", response_model=list[ShipmentReply], summary="Shipments")
def shipments(
    actor: ActorDependency,
    dispatch: DispatchDependency,
    reports: InventoryReportDependency,
    order_id: uuid.UUID | None = None,
    state: str | None = None,
) -> list[ShipmentReply]:
    found = dispatch.shipments(actor, order_id=order_id, state=state)
    return [_reply(reports, item) for item in found]


@router.get("/shipments/{shipment_id}", response_model=ShipmentReply, summary="A shipment")
def shipment(
    shipment_id: uuid.UUID,
    actor: ActorDependency,
    dispatch: DispatchDependency,
    reports: InventoryReportDependency,
) -> ShipmentReply:
    return _reply(reports, dispatch.require(actor, shipment_id))


@router.get(
    "/orders/{order_id}/lines",
    response_model=list[ShippableLineReply],
    summary="What an order may ship",
)
def shippable(
    order_id: uuid.UUID,
    actor: ActorDependency,
    dispatch: DispatchDependency,
    assembly: AssemblyDependency,
) -> list[ShippableLineReply]:
    actor.require("dispatch.view")
    space = assembly.workspace(actor, order_id)
    lines = {line.id: line for line in space.order.lines}
    # Not a stock item: it travels on the supplier's delivery, so there is nothing to ship.
    return [
        ShippableLineReply(
            line_id=view.line_id,
            position=view.position,
            description=view.description,
            sku=view.sku,
            tracking=view.tracking,
            holds_stock=view.holds_stock,
            ordered=view.required,
            delivered=view.delivered,
            outstanding=view.owed,
            staged=view.staged,
            shippable=dispatch.shippable(lines[view.line_id]) if view.holds_stock else ZERO,
        )
        for view in space.lines
    ]


@router.post("/shipments", response_model=ShipmentReply, status_code=201, summary="Prepare")
def prepare(
    payload: PrepareContract,
    actor: ActorDependency,
    dispatch: DispatchDependency,
    reports: InventoryReportDependency,
) -> ShipmentReply:
    shipment = dispatch.prepare(
        actor,
        PrepareDraft(
            sales_order_id=payload.sales_order_id,
            lines=[
                ShipmentLineDraft(
                    sales_order_line_id=line.sales_order_line_id,
                    quantity=line.quantity,
                    serials=line.serials,
                )
                for line in payload.lines
            ],
            destination=payload.destination,
            notes=payload.notes,
        ),
    )
    return _reply(reports, shipment)


@router.post("/shipments/{shipment_id}/pack", response_model=ShipmentReply, summary="Pack")
def pack(
    shipment_id: uuid.UUID,
    payload: PackContract,
    actor: ActorDependency,
    dispatch: DispatchDependency,
    reports: InventoryReportDependency,
) -> ShipmentReply:
    shipment = dispatch.pack(actor, shipment_id, package_count=payload.package_count)
    return _reply(reports, shipment)


@router.post("/shipments/{shipment_id}/dispatch", response_model=ShipmentReply, summary="Dispatch")
def dispatch_shipment(
    shipment_id: uuid.UUID,
    payload: DispatchContract,
    actor: ActorDependency,
    dispatch: DispatchDependency,
    reports: InventoryReportDependency,
) -> ShipmentReply:
    shipment = dispatch.dispatch(
        actor,
        shipment_id,
        DispatchDraft(
            mode=payload.mode,
            package_count=payload.package_count,
            carrier=payload.carrier,
            driver=payload.driver,
            vehicle=payload.vehicle,
            destination=payload.destination,
            tracking_reference=payload.tracking_reference,
            override_reason=payload.override_reason,
        ),
    )
    return _reply(reports, shipment)


@router.post(
    "/shipments/{shipment_id}/deliver", response_model=ShipmentReply, summary="Confirm delivery"
)
def deliver(
    shipment_id: uuid.UUID,
    payload: DeliveryContract,
    actor: ActorDependency,
    dispatch: DispatchDependency,
    documents: DocumentDependency,
    reports: InventoryReportDependency,
) -> ShipmentReply:
    evidence: bytes | None = None
    if payload.evidence_base64:
        try:
            evidence = base64.b64decode(payload.evidence_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValidationError(
                "The evidence is not valid base64.", field="evidence_base64"
            ) from error
    if payload.recipient_name:
        # The recipient signs the waybill; issue it now if the driver left without a copy
        # being recorded, so the proof has a document to attach to.
        documents.issue_for_shipment(actor, shipment_id, DocumentKind.WAYBILL)
    shipment = dispatch.confirm_delivery(
        actor,
        shipment_id,
        DeliveryDraft(
            outcomes=[
                OutcomeDraft(
                    shipment_line_id=item.shipment_line_id,
                    delivered=item.delivered,
                    returned=item.returned,
                    damaged=item.damaged,
                    lost=item.lost,
                    reason=item.reason,
                    serials_delivered=item.serials_delivered,
                    remainder_as=item.remainder_as,
                )
                for item in payload.outcomes
            ],
            recipient_name=payload.recipient_name,
            recipient_role=payload.recipient_role,
            location=payload.location,
            comments=payload.comments,
            evidence_kind=payload.evidence_kind,
            evidence=evidence,
            media_type=payload.media_type,
        ),
    )
    return _reply(reports, shipment)


@router.post("/shipments/{shipment_id}/cancel", response_model=ShipmentReply, summary="Cancel")
def cancel(
    shipment_id: uuid.UUID,
    payload: CancelShipmentContract,
    actor: ActorDependency,
    dispatch: DispatchDependency,
    reports: InventoryReportDependency,
) -> ShipmentReply:
    shipment = dispatch.cancel(actor, shipment_id, reason=payload.reason)
    return _reply(reports, shipment)


@router.post(
    "/shipments/{shipment_id}/lines/{line_id}/return",
    response_model=ShipmentReply,
    summary="Accept a return after delivery",
)
def customer_return(
    shipment_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: CustomerReturnContract,
    actor: ActorDependency,
    dispatch: DispatchDependency,
    reports: InventoryReportDependency,
) -> ShipmentReply:
    shipment = dispatch.customer_return(
        actor,
        shipment_id,
        line_id,
        quantity=payload.quantity,
        reason=payload.reason,
        replacement_owed=payload.replacement_owed,
        serials=payload.serials,
    )
    return _reply(reports, shipment)


@router.post(
    "/shipments/{shipment_id}/documents/{kind}",
    response_model=IssuedDocumentReply,
    summary="Issue a shipment document",
)
def issue_document(
    shipment_id: uuid.UUID,
    kind: str,
    actor: ActorDependency,
    documents: DocumentDependency,
) -> IssuedDocumentReply:
    """Issued once; asking again returns the copy already issued."""
    try:
        chosen = DocumentKind(kind)
    except ValueError as error:
        raise ValidationError(f"{kind!r} is not a shipment document.", field="kind") from error
    return IssuedDocumentReply.model_validate(
        documents.issue_for_shipment(actor, shipment_id, chosen)
    )
