"""Register endpoints: shifts, the cart, tenders, completion, receipts and refunds."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from nickland.application.context import Actor
from nickland.application.services.pos import (
    PosService,
    RefundDraft,
    RefundLineDraft,
)
from nickland.domain.errors import PermissionDeniedError
from nickland.domain.money import ZERO, money
from nickland.domain.pos import DENOMINATIONS, TenderMethod, TenderState
from nickland.interface.dependencies import (
    ActorDependency,
    DocumentDependency,
    IdentityDependency,
    InventoryReportDependency,
    PosDependency,
)
from nickland.interface.pos_schemas import (
    AddLineContract,
    CashMovementContract,
    CashMovementReply,
    ConfirmTenderContract,
    CountContract,
    CountPreviewReply,
    CustomerContract,
    DiscountContract,
    DrawerReply,
    MethodTotalReply,
    OpenShiftContract,
    OverrideContract,
    ParkContract,
    QuantityContract,
    ReasonContract,
    RefundContract,
    RefundLineReply,
    RefundReply,
    SaleLineReply,
    SaleReply,
    SaleSummaryReply,
    ScanContract,
    ShiftReply,
    ShiftSummaryReply,
    SuggestedAmountReply,
    Supervisor,
    TenderContract,
    TenderReply,
    VoidContract,
)
from nickland.persistence.pos_models import PosRefund, PosSale, PosShift

router = APIRouter(prefix="/pos", tags=["point of sale"])


# -- replies ------------------------------------------------------------------------------


def _sale(
    pos: PosService, reports: InventoryReportDependency, actor: Actor, sale: PosSale
) -> SaleReply:
    skus = pos.skus(sale)
    open_sale = sale.completed_at is None
    return SaleReply(
        id=sale.id,
        number=sale.number,
        state=sale.state,
        customer_id=sale.customer_fk,
        customer=reports.party_name(sale.customer_fk),
        parked_label=sale.parked_label,
        lines=[
            SaleLineReply(
                id=line.id,
                position=line.position,
                product_id=line.product_fk,
                sku=skus.get(line.product_fk, ""),
                description=line.description,
                quantity=line.quantity,
                list_price=line.list_price,
                price_source=line.price_source,
                unit_price=line.unit_price,
                override_reason=line.override_reason,
                discount_rate=line.discount_rate,
                discount=line.discount,
                net=line.net,
                tax=line.tax,
                total=line.total,
                returnable=money(line.quantity - line.quantity_refunded),
            )
            for line in sorted(sale.lines, key=lambda item: item.position)
        ],
        tenders=[
            TenderReply(
                id=tender.id,
                method=tender.method,
                method_label=TenderMethod(tender.method).label,
                state=tender.state,
                state_label=TenderState(tender.state).label,
                amount=tender.amount,
                cash_received=tender.cash_received,
                change_given=tender.change_given,
                provider_reference=tender.provider_reference,
                failure_reason=tender.failure_reason,
            )
            for tender in sale.tenders
        ],
        gross_total=sale.gross_total,
        discount_total=sale.discount_total,
        net_total=sale.net_total,
        tax_total=sale.tax_total,
        total=sale.total,
        due=pos.due(sale) if open_sale else ZERO,
        change=pos.change_given(sale),
        suggested_cash=[
            SuggestedAmountReply(amount=amount, exact=index == 0)
            for index, amount in enumerate(
                pos.suggested_cash(actor, sale) if open_sale and sale.lines else []
            )
        ],
        refunded_total=sale.refunded_total,
        awaiting_confirmation=any(t.state == TenderState.PENDING.value for t in sale.tenders),
        completed_at=sale.completed_at,
        cashier=pos.user_name(sale.cashier_id),
        receipt_id=sale.receipt_fk,
        void_reason=sale.void_reason,
    )


def _summary(reports: InventoryReportDependency, sale: PosSale) -> SaleSummaryReply:
    return SaleSummaryReply(
        id=sale.id,
        number=sale.number,
        state=sale.state,
        customer=reports.party_name(sale.customer_fk),
        parked_label=sale.parked_label,
        total=sale.total,
        refunded_total=sale.refunded_total,
        items=len(sale.lines),
        completed_at=sale.completed_at,
        methods=sorted(
            {
                TenderMethod(t.method).label
                for t in sale.tenders
                if t.state == TenderState.CONFIRMED.value
            }
        ),
    )


def _shift(pos: PosService, shift: PosShift) -> ShiftReply:
    return ShiftReply(
        id=shift.id,
        number=shift.number,
        register_name=shift.register,
        cashier=pos.user_name(shift.cashier_id),
        state=shift.state,
        business_day=shift.business_day,
        opened_at=shift.opened_at,
        opening_float=shift.opening_float,
        counted_cash=shift.counted_cash,
        expected_cash=shift.expected_cash,
        variance=shift.variance,
        variance_reason=shift.variance_reason,
        submitted_at=shift.submitted_at,
        closed_at=shift.closed_at,
        approved_by=pos.user_name(shift.approved_by_id),
        counted_denominations=shift.counted_denominations,
    )


def _refund(pos: PosService, refund: PosRefund) -> RefundReply:
    return RefundReply(
        id=refund.id,
        number=refund.number,
        kind=refund.kind,
        sale_id=refund.sale_fk,
        reason=refund.reason,
        method=refund.method,
        restock=refund.restock,
        net_total=refund.net_total,
        tax_total=refund.tax_total,
        total=refund.total,
        requested_by=pos.user_name(refund.requested_by_id),
        authorised_by=pos.user_name(refund.authorised_by_id),
        refunded_on=refund.refunded_on,
        lines=[
            RefundLineReply(
                sale_line_id=line.sale_line_fk, quantity=line.quantity, net=line.net, tax=line.tax
            )
            for line in refund.lines
        ],
    )


# -- shifts -------------------------------------------------------------------------------


@router.get("/shift", response_model=ShiftSummaryReply | None, summary="My open shift")
def current_shift(actor: ActorDependency, pos: PosDependency) -> ShiftSummaryReply | None:
    shift = pos.current_shift(actor)
    return _shift_summary(pos, actor, shift.id) if shift else None


def _shift_summary(pos: PosService, actor: Actor, shift_id: uuid.UUID) -> ShiftSummaryReply:
    summary = pos.summary(actor, shift_id)
    drawer = summary.drawer
    return ShiftSummaryReply(
        shift=_shift(pos, summary.shift),
        drawer=DrawerReply(
            opening_float=drawer.opening_float,
            cash_sales=drawer.cash_sales,
            cash_in=drawer.cash_in,
            cash_out=drawer.cash_out,
            cash_refunds=drawer.cash_refunds,
            expected=drawer.expected,
        ),
        sales=summary.sales,
        pending_tenders=len(summary.pending_tenders),
        by_method=[
            MethodTotalReply(method=method, label=TenderMethod(method).label, amount=amount)
            for method, amount in sorted(summary.by_method.items())
        ],
        denominations=list(DENOMINATIONS),
    )


@router.post("/shifts", response_model=ShiftSummaryReply, status_code=201, summary="Open a shift")
def open_shift(
    body: OpenShiftContract, actor: ActorDependency, pos: PosDependency
) -> ShiftSummaryReply:
    shift = pos.open_shift(actor, opening_float=body.opening_float, register=body.register_name)
    return _shift_summary(pos, actor, shift.id)


@router.get("/shifts", response_model=list[ShiftReply], summary="Shifts")
def shifts(actor: ActorDependency, pos: PosDependency) -> list[ShiftReply]:
    return [_shift(pos, shift) for shift in pos.shifts(actor)]


@router.get("/shifts/{shift_id}", response_model=ShiftSummaryReply, summary="A shift")
def shift(shift_id: uuid.UUID, actor: ActorDependency, pos: PosDependency) -> ShiftSummaryReply:
    return _shift_summary(pos, actor, shift_id)


@router.post(
    "/shifts/{shift_id}/count/preview", response_model=CountPreviewReply, summary="Total a count"
)
def count_preview(
    shift_id: uuid.UUID, body: CountContract, actor: ActorDependency, pos: PosDependency
) -> CountPreviewReply:
    counted, expected, variance = pos.count_preview(actor, shift_id, body.denominations)
    return CountPreviewReply(counted=counted, expected=expected, variance=variance)


@router.post("/shifts/{shift_id}/count", response_model=ShiftSummaryReply, summary="Count")
def count(
    shift_id: uuid.UUID, body: CountContract, actor: ActorDependency, pos: PosDependency
) -> ShiftSummaryReply:
    pos.submit_count(
        actor, shift_id, denominations=body.denominations, variance_reason=body.variance_reason
    )
    return _shift_summary(pos, actor, shift_id)


@router.post("/shifts/{shift_id}/approve", response_model=ShiftSummaryReply, summary="Sign off")
def approve(shift_id: uuid.UUID, actor: ActorDependency, pos: PosDependency) -> ShiftSummaryReply:
    pos.approve_shift(actor, shift_id)
    return _shift_summary(pos, actor, shift_id)


@router.post(
    "/cash-movements", response_model=CashMovementReply, status_code=201, summary="Cash in or out"
)
def cash_movement(
    body: CashMovementContract, actor: ActorDependency, pos: PosDependency
) -> CashMovementReply:
    movement = pos.cash_movement(
        actor, kind=body.kind, amount=body.amount, reason=body.reason, counter=body.counter
    )
    return CashMovementReply.model_validate(movement)


# -- the cart -----------------------------------------------------------------------------


@router.get("/sale", response_model=SaleReply, summary="The sale in progress")
def current_sale(
    actor: ActorDependency, pos: PosDependency, reports: InventoryReportDependency
) -> SaleReply:
    return _sale(pos, reports, actor, pos.current_sale(actor))


@router.get("/sales", response_model=list[SaleSummaryReply], summary="Completed sales")
def sales(
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
    shift_id: uuid.UUID | None = None,
) -> list[SaleSummaryReply]:
    return [_summary(reports, sale) for sale in pos.sales(actor, shift_id=shift_id)]


@router.get("/parked", response_model=list[SaleSummaryReply], summary="Parked sales")
def parked(
    actor: ActorDependency, pos: PosDependency, reports: InventoryReportDependency
) -> list[SaleSummaryReply]:
    return [_summary(reports, sale) for sale in pos.parked(actor)]


@router.get("/sales/by-number/{number}", response_model=SaleReply, summary="Find a sale")
def find_sale(
    number: str, actor: ActorDependency, pos: PosDependency, reports: InventoryReportDependency
) -> SaleReply:
    return _sale(pos, reports, actor, pos.find_sale(actor, number))


@router.get("/sales/{sale_id}", response_model=SaleReply, summary="A sale")
def sale(
    sale_id: uuid.UUID,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.require_sale(actor, sale_id))


@router.post("/sales/{sale_id}/scan", response_model=SaleReply, summary="Scan an item")
def scan(
    sale_id: uuid.UUID,
    body: ScanContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.scan(actor, sale_id, body.code, quantity=body.quantity))


@router.post("/sales/{sale_id}/lines", response_model=SaleReply, summary="Add an item")
def add_line(
    sale_id: uuid.UUID,
    body: AddLineContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    sale = pos.add(actor, sale_id, body.product_id, quantity=body.quantity, serials=body.serials)
    return _sale(pos, reports, actor, sale)


@router.put("/sales/{sale_id}/lines/{line_id}", response_model=SaleReply, summary="Set quantity")
def set_quantity(
    sale_id: uuid.UUID,
    line_id: uuid.UUID,
    body: QuantityContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.set_quantity(actor, sale_id, line_id, body.quantity))


@router.post(
    "/sales/{sale_id}/lines/{line_id}/override", response_model=SaleReply, summary="Override"
)
def override_price(
    sale_id: uuid.UUID,
    line_id: uuid.UUID,
    body: OverrideContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    sale = pos.override_price(
        actor, sale_id, line_id, unit_price=body.unit_price, reason=body.reason
    )
    return _sale(pos, reports, actor, sale)


@router.post(
    "/sales/{sale_id}/lines/{line_id}/discount", response_model=SaleReply, summary="Discount"
)
def discount(
    sale_id: uuid.UUID,
    line_id: uuid.UUID,
    body: DiscountContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.discount(actor, sale_id, line_id, rate=body.rate))


@router.put("/sales/{sale_id}/customer", response_model=SaleReply, summary="Set the customer")
def set_customer(
    sale_id: uuid.UUID,
    body: CustomerContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.set_customer(actor, sale_id, body.customer_id))


@router.post("/sales/{sale_id}/park", response_model=SaleReply, summary="Park")
def park(
    sale_id: uuid.UUID,
    body: ParkContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.park(actor, sale_id, label=body.label))


@router.post("/sales/{sale_id}/resume", response_model=SaleReply, summary="Resume")
def resume(
    sale_id: uuid.UUID,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.resume(actor, sale_id))


@router.post("/sales/{sale_id}/abandon", response_model=SaleReply, summary="Abandon the cart")
def abandon(
    sale_id: uuid.UUID,
    body: ReasonContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.void_open(actor, sale_id, reason=body.reason))


# -- tenders and completion ---------------------------------------------------------------


@router.post("/sales/{sale_id}/tenders", response_model=SaleReply, summary="Take a payment")
def tender(
    sale_id: uuid.UUID,
    body: TenderContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    sale = pos.tender(
        actor,
        sale_id,
        method=body.method,
        amount=body.amount,
        cash_received=body.cash_received,
        reference=body.reference,
    )
    return _sale(pos, reports, actor, sale)


@router.post("/tenders/{tender_id}/confirm", response_model=SaleReply, summary="Confirm")
def confirm_tender(
    tender_id: uuid.UUID,
    body: ConfirmTenderContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    sale = pos.confirm_tender(actor, tender_id, provider_reference=body.provider_reference)
    return _sale(pos, reports, actor, sale)


@router.post("/tenders/{tender_id}/fail", response_model=SaleReply, summary="Record a failure")
def fail_tender(
    tender_id: uuid.UUID,
    body: ReasonContract,
    actor: ActorDependency,
    pos: PosDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    return _sale(pos, reports, actor, pos.fail_tender(actor, tender_id, reason=body.reason))


@router.post("/sales/{sale_id}/complete", response_model=SaleReply, summary="Complete the sale")
def complete(
    sale_id: uuid.UUID,
    actor: ActorDependency,
    pos: PosDependency,
    documents: DocumentDependency,
    reports: InventoryReportDependency,
) -> SaleReply:
    """Posts the sale and issues its receipt, in one transaction."""
    sale = pos.complete(actor, sale_id)
    documents.issue_for_pos_sale(actor, sale.id)
    return _sale(pos, reports, actor, sale)


@router.get(
    "/sales/{sale_id}/receipt.txt",
    response_class=PlainTextResponse,
    summary="The receipt for a counter printer",
)
def receipt_text(
    sale_id: uuid.UUID, actor: ActorDependency, documents: DocumentDependency, width: int = 42
) -> PlainTextResponse:
    text = documents.pos_receipt_text(actor, sale_id, width=max(min(width, 64), 24))
    return PlainTextResponse(text)


# -- refunds ------------------------------------------------------------------------------


def _authoriser(
    identity: IdentityDependency, actor: Actor, supervisor: Supervisor | None
) -> Actor | None:
    """A supervisor authorising at this register. Their password is checked as at sign-in,
    and the session it opens is closed at once: they authorise, they do not take over."""
    if supervisor is None:
        return None
    assert actor.business_id is not None
    signed = identity.sign_in(
        supervisor.username, supervisor.password, business_id=actor.business_id, device="register"
    ).actor
    identity.sign_out(signed)
    if not signed.has("pos.refund"):
        raise PermissionDeniedError(f"{signed.full_name} may not authorise refunds.")
    return signed


@router.get("/sales/{sale_id}/refunds", response_model=list[RefundReply], summary="Refunds")
def refunds(sale_id: uuid.UUID, actor: ActorDependency, pos: PosDependency) -> list[RefundReply]:
    return [_refund(pos, item) for item in pos.refunds_of(actor, sale_id)]


@router.post(
    "/sales/{sale_id}/refunds", response_model=RefundReply, status_code=201, summary="Refund"
)
def refund(
    sale_id: uuid.UUID,
    body: RefundContract,
    actor: ActorDependency,
    pos: PosDependency,
    identity: IdentityDependency,
) -> RefundReply:
    made = pos.refund(
        actor,
        RefundDraft(
            sale_id=sale_id,
            lines=[
                RefundLineDraft(sale_line_id=line.sale_line_id, quantity=line.quantity)
                for line in body.lines
            ],
            reason=body.reason,
            method=body.method,
            restock=body.restock,
        ),
        authoriser=_authoriser(identity, actor, body.supervisor),
    )
    return _refund(pos, made)


@router.post("/sales/{sale_id}/void", response_model=RefundReply, status_code=201, summary="Void")
def void(
    sale_id: uuid.UUID,
    body: VoidContract,
    actor: ActorDependency,
    pos: PosDependency,
    identity: IdentityDependency,
) -> RefundReply:
    made = pos.void(
        actor,
        sale_id,
        reason=body.reason,
        authoriser=_authoriser(identity, actor, body.supervisor),
    )
    return _refund(pos, made)


__all__ = ["router"]
