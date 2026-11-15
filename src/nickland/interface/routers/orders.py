"""Sales order endpoints: acceptance, conversion, position and cost variance."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nickland.application.services.orders import AcceptanceDraft, SalesOrderService
from nickland.domain.money import present
from nickland.interface.dependencies import (
    ActorDependency,
    PartyDependency,
    SalesOrderDependency,
)
from nickland.interface.order_schemas import (
    AcceptanceContract,
    AcceptanceReply,
    CancelLineContract,
    ConvertContract,
    LineVarianceReply,
    OrderVarianceReply,
    ReasonContract,
    SalesOrderLineReply,
    SalesOrderReply,
    SubstituteContract,
)
from nickland.persistence.order_models import SalesOrder

router = APIRouter(prefix="/orders", tags=["orders"])


def _reply(
    orders: SalesOrderService, actor: ActorDependency, parties: PartyDependency, order: SalesOrder
) -> SalesOrderReply:
    customer = parties.require(actor, order.customer_fk)
    linked = orders.purchase_orders_for(actor, order.id)
    precision = order.precision
    return SalesOrderReply(
        id=order.id,
        number=order.number,
        quotation_fk=order.quotation_fk,
        acceptance_fk=order.acceptance_fk,
        customer_fk=order.customer_fk,
        customer_name=customer.name,
        customer_reference=order.customer_reference,
        state=order.state,
        ordered_on=order.ordered_on,
        required_by=order.required_by,
        currency_code=order.currency_code,
        precision=precision,
        payment_plan=order.payment_plan,
        net_total=present(order.net_total, precision),
        tax_total=present(order.tax_total, precision),
        total_payable=present(order.total_payable, precision),
        notes=order.notes,
        cancelled_at=order.cancelled_at,
        cancellation_reason=order.cancellation_reason,
        lines=[
            SalesOrderLineReply(
                id=line.id,
                position=line.position,
                product_fk=line.product_fk,
                description=line.description,
                specification=line.specification,
                source_kind=line.source_kind,
                quantity_ordered=line.quantity_ordered,
                quantity_cancelled=line.quantity_cancelled,
                quantity_procured=line.quantity_procured,
                quantity_received=line.quantity_received,
                state=line.state,
                unit_price=present(line.unit_price, precision),
                line_total=present(line.line_total, precision),
                tax_amount=present(line.tax_amount, precision),
                substituted_from_product_fk=line.substituted_from_product_fk,
                substitution_reason=line.substitution_reason,
            )
            for line in order.lines
        ],
        purchase_order_numbers=[item.number for item in linked if item.number],
    )


@router.post(
    "/quotations/{quotation_id}/acceptance",
    response_model=AcceptanceReply,
    status_code=201,
    summary="Record the customer's acceptance",
)
def record_acceptance(
    quotation_id: uuid.UUID,
    payload: AcceptanceContract,
    actor: ActorDependency,
    orders: SalesOrderDependency,
) -> AcceptanceReply:
    """The customer's purchase order or confirmation, kept against the quotation."""
    acceptance = orders.record_acceptance(
        actor,
        quotation_id,
        AcceptanceDraft(
            method=payload.method,
            reference=payload.reference,
            accepted_on=payload.accepted_on,
            evidence_attachment_id=payload.evidence_attachment_id,
            notes=payload.notes,
        ),
    )
    return AcceptanceReply.model_validate(acceptance)


@router.get(
    "/quotations/{quotation_id}/acceptance",
    response_model=list[AcceptanceReply],
    summary="Acceptances recorded",
)
def acceptances(
    quotation_id: uuid.UUID, actor: ActorDependency, orders: SalesOrderDependency
) -> list[AcceptanceReply]:
    return [
        AcceptanceReply.model_validate(item) for item in orders.acceptances(actor, quotation_id)
    ]


@router.post(
    "/quotations/{quotation_id}/convert",
    response_model=SalesOrderReply,
    status_code=201,
    summary="Convert the accepted quotation",
)
def convert(
    quotation_id: uuid.UUID,
    payload: ConvertContract,
    actor: ActorDependency,
    orders: SalesOrderDependency,
    parties: PartyDependency,
) -> SalesOrderReply:
    """Every quoted figure carried across; nothing typed again."""
    order = orders.convert(
        actor, quotation_id, required_by=payload.required_by, notes=payload.notes
    )
    return _reply(orders, actor, parties, order)


@router.get("", response_model=list[SalesOrderReply], summary="List sales orders")
def list_orders(
    actor: ActorDependency,
    orders: SalesOrderDependency,
    parties: PartyDependency,
    state: str | None = None,
    limit: int = 50,
) -> list[SalesOrderReply]:
    return [
        _reply(orders, actor, parties, item)
        for item in orders.orders(actor, state=state, limit=limit)
    ]


@router.get("/{order_id}", response_model=SalesOrderReply, summary="Read a sales order")
def read_order(
    order_id: uuid.UUID,
    actor: ActorDependency,
    orders: SalesOrderDependency,
    parties: PartyDependency,
) -> SalesOrderReply:
    return _reply(orders, actor, parties, orders.require(actor, order_id))


@router.post(
    "/{order_id}/lines/{line_id}/cancel",
    response_model=SalesOrderReply,
    summary="Cancel part of a line",
)
def cancel_line(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: CancelLineContract,
    actor: ActorDependency,
    orders: SalesOrderDependency,
    parties: PartyDependency,
) -> SalesOrderReply:
    order = orders.cancel_line_quantity(
        actor, order_id, line_id, quantity=payload.quantity, reason=payload.reason
    )
    return _reply(orders, actor, parties, order)


@router.post("/{order_id}/cancel", response_model=SalesOrderReply, summary="Cancel the order")
def cancel_order(
    order_id: uuid.UUID,
    payload: ReasonContract,
    actor: ActorDependency,
    orders: SalesOrderDependency,
    parties: PartyDependency,
) -> SalesOrderReply:
    return _reply(orders, actor, parties, orders.cancel(actor, order_id, reason=payload.reason))


@router.post(
    "/{order_id}/lines/{line_id}/substitute",
    response_model=SalesOrderReply,
    summary="Substitute the product on a line",
)
def substitute(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: SubstituteContract,
    actor: ActorDependency,
    orders: SalesOrderDependency,
    parties: PartyDependency,
) -> SalesOrderReply:
    order = orders.substitute(
        actor, order_id, line_id, product_id=payload.product_id, reason=payload.reason
    )
    return _reply(orders, actor, parties, order)


@router.get(
    "/{order_id}/cost-variance",
    response_model=OrderVarianceReply,
    summary="Quoted against actual procurement cost",
)
def cost_variance(
    order_id: uuid.UUID, actor: ActorDependency, orders: SalesOrderDependency
) -> OrderVarianceReply:
    """Internal. Behind the costing permission, like the quotation's build-up."""
    report = orders.cost_variance(actor, order_id)
    return OrderVarianceReply(
        order_id=report.order_id,
        number=report.number,
        lines=[LineVarianceReply.model_validate(line) for line in report.lines],
        expected_total=report.expected_total,
        actual_total=report.actual_total,
        variance_total=report.variance_total,
        lines_known=report.lines_known,
    )


__all__ = ["router"]
