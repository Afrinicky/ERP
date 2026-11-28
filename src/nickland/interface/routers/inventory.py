"""Inventory endpoints: stock, movements, operations, counts, valuation and assembly."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nickland.application.services.assembly import Workspace
from nickland.application.services.inventory import (
    AdjustmentDraft,
    InspectionDraft,
    IssueDraft,
    LandedChargeDraft,
    OpeningDraft,
    StockLineDraft,
    TransferDraft,
    TransferLineDraft,
)
from nickland.application.services.inventory_reports import ExpiryRow, PositionRow
from nickland.application.services.stock import Operation
from nickland.interface.dependencies import (
    ActorDependency,
    AssemblyDependency,
    InventoryDependency,
    InventoryReportDependency,
)
from nickland.interface.inventory_schemas import (
    AccountAgreementReply,
    AdjustmentContract,
    AssemblyLineReply,
    AssemblyQuantityContract,
    AssemblyReply,
    BatchReply,
    BinContract,
    BinReply,
    CountLineReply,
    CountOpenContract,
    CountRecordContract,
    CountReply,
    CustomerReturnContract,
    DiscrepancyReply,
    InspectionContract,
    InspectionReply,
    InventorySettingsContract,
    InventorySettingsReply,
    IssueContract,
    LandedChargeContract,
    LandedChargeReply,
    LocationContract,
    LocationReply,
    MovementReply,
    OpeningContract,
    OperationReply,
    PositionReply,
    ReconciliationReply,
    ReleaseContract,
    RelocationContract,
    ReservationContract,
    ReservationReply,
    ReverseContract,
    StockLineContract,
    SupplierReturnContract,
    TransferContract,
    TransferLineReply,
    TransferReply,
    ValuationReply,
    ValuationRowReply,
    WarehouseStockReply,
)
from nickland.persistence.inventory_models import (
    Inspection,
    StockCount,
    StockMovement,
    StockReservation,
    StockTransfer,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])


# -- replies ---------------------------------------------------------------------------


def _operation(operation: Operation) -> OperationReply:
    return OperationReply(
        operation_id=operation.id,
        movements=len(operation.movements),
        journal_entry_id=operation.journal.id if operation.journal else None,
    )


def _position(row: PositionRow, *, costs: bool) -> PositionReply:
    reply = PositionReply.model_validate(row, from_attributes=True)
    if not costs:
        reply.value = None
        reply.unit_cost = None
    return reply


def _batch(row: ExpiryRow) -> BatchReply:
    return BatchReply(
        batch_id=row.batch_id,
        batch=row.batch,
        product_id=row.product_id,
        sku=row.sku,
        name=row.name,
        warehouse=row.warehouse,
        expires_on=row.expires_on,
        quantity=row.quantity,
        status=row.status.value,
        days=row.days,
    )


def _movement(
    reports: InventoryReportDependency, movement: StockMovement, *, costs: bool
) -> MovementReply:
    product = reports.product(movement.product_fk)
    return MovementReply(
        id=movement.id,
        operation_id=movement.operation_id,
        movement_type=movement.movement_type,
        state=movement.state,
        product_id=product.id,
        sku=product.sku,
        warehouse=reports.warehouse_code(movement.warehouse_fk),
        location=reports.location_code(movement.location_fk),
        batch=reports.batch_code(movement.batch_fk),
        serial=reports.serial_number(movement.serial_fk),
        quantity=movement.quantity,
        value=movement.value if costs else None,
        reference=movement.reference,
        reason=movement.reason,
        override_reason=movement.override_reason,
        occurred_on=movement.occurred_on,
        created_at=movement.created_at,
        reversible=reports.reversible(movement),
    )


def _transfer(reports: InventoryReportDependency, transfer: StockTransfer) -> TransferReply:
    lines = []
    for line in transfer.lines:
        product = reports.product(line.product_fk)
        lines.append(
            TransferLineReply(
                id=line.id,
                position=line.position,
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                batch=reports.batch_code(line.batch_fk),
                quantity=line.quantity,
            )
        )
    return TransferReply(
        id=transfer.id,
        number=transfer.number,
        from_warehouse=reports.warehouse_code(transfer.from_warehouse_fk),
        to_warehouse=reports.warehouse_code(transfer.to_warehouse_fk),
        state=transfer.state,
        requested_on=transfer.requested_on,
        dispatched_on=transfer.dispatched_on,
        received_on=transfer.received_on,
        notes=transfer.notes,
        lines=lines,
    )


def _inspection(reports: InventoryReportDependency, inspection: Inspection) -> InspectionReply:
    product = reports.product(inspection.product_fk)
    return InspectionReply(
        id=inspection.id,
        product_id=product.id,
        sku=product.sku,
        name=product.name,
        warehouse=reports.warehouse_code(inspection.warehouse_fk),
        batch=reports.batch_code(inspection.batch_fk),
        quantity_passed=inspection.quantity_passed,
        quantity_failed=inspection.quantity_failed,
        criteria=inspection.criteria,
        findings=inspection.findings,
        inspected_on=inspection.inspected_on,
        inspector=reports.person(inspection.inspector_id),
    )


def _reservation(
    reports: InventoryReportDependency, reservation: StockReservation
) -> ReservationReply:
    product = reports.product(reservation.product_fk)
    return ReservationReply(
        id=reservation.id,
        product_id=product.id,
        sku=product.sku,
        name=product.name,
        warehouse=reports.warehouse_code(reservation.warehouse_fk),
        batch=reports.batch_code(reservation.batch_fk),
        sales_order_line_id=reservation.sales_order_line_fk,
        order_number=reports.order_number_of_line(reservation.sales_order_line_fk),
        quantity=reservation.quantity,
        quantity_consumed=reservation.quantity_consumed,
        quantity_released=reservation.quantity_released,
        state=reservation.state,
        created_at=reservation.created_at,
    )


def _count(reports: InventoryReportDependency, count: StockCount) -> CountReply:
    lines = []
    for line in count.lines:
        product = reports.product(line.product_fk)
        lines.append(
            CountLineReply(
                id=line.id,
                position=line.position,
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                location=reports.location_code(line.location_fk),
                batch=reports.batch_code(line.batch_fk),
                system_quantity=line.system_quantity,
                counted_quantity=line.counted_quantity,
                difference=(
                    line.counted_quantity - line.system_quantity
                    if line.counted_quantity is not None
                    else None
                ),
                reason=line.reason,
            )
        )
    return CountReply(
        id=count.id,
        number=count.number,
        warehouse=reports.warehouse_code(count.warehouse_fk),
        location=reports.location_code(count.location_fk),
        state=count.state,
        counted_on=count.counted_on,
        notes=count.notes,
        lines=lines,
    )


def _workspace(reports: InventoryReportDependency, space: Workspace) -> AssemblyReply:
    return AssemblyReply(
        order_id=space.order.id,
        order_number=space.order.number,
        customer=reports.party_name(space.order.customer_fk),
        warehouse=space.warehouse.name,
        ready=space.ready,
        lines=[
            AssemblyLineReply(
                line_id=line.line_id,
                position=line.position,
                description=line.description,
                product_id=line.product_id,
                sku=line.sku,
                tracking=line.tracking,
                holds_stock=line.holds_stock,
                required=line.required,
                available=line.available,
                reserved=line.reserved,
                incoming=line.incoming,
                assembled=line.assembled,
                staged=line.staged,
                dispatched=line.dispatched,
                delivered=line.delivered,
                outstanding=line.outstanding,
                to_reserve=line.to_reserve,
                stageable=line.stageable,
            )
            for line in space.lines
        ],
    )


def _lines(lines: list[StockLineContract]) -> list[StockLineDraft]:
    return [
        StockLineDraft(
            product_id=line.product_id,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            batch=line.batch,
            batch_id=line.batch_id,
            expiry=line.expiry,
            manufacturer=line.manufacturer,
            serials=line.serials,
            location_id=line.location_id,
            bin_id=line.bin_id,
        )
        for line in lines
    ]


# -- configuration and places ----------------------------------------------------------


@router.get("/settings", response_model=InventorySettingsReply, summary="Inventory settings")
def settings(actor: ActorDependency, inventory: InventoryDependency) -> InventorySettingsReply:
    return InventorySettingsReply.model_validate(inventory.settings(actor))


@router.put("/settings", response_model=InventorySettingsReply, summary="Change settings")
def set_settings(
    payload: InventorySettingsContract, actor: ActorDependency, inventory: InventoryDependency
) -> InventorySettingsReply:
    found = inventory.set_settings(
        actor,
        valuation_policy=payload.valuation_policy,
        expiry_warning_days=payload.expiry_warning_days,
    )
    return InventorySettingsReply.model_validate(found)


@router.get(
    "/warehouses", response_model=list[WarehouseStockReply], summary="Warehouses and places"
)
def warehouses(actor: ActorDependency, inventory: InventoryDependency) -> list[WarehouseStockReply]:
    return [
        WarehouseStockReply(
            id=warehouse.id,
            code=warehouse.code,
            name=warehouse.name,
            is_default=warehouse.is_default,
            locations=[
                LocationReply.model_validate(location)
                for location in inventory.locations(actor, warehouse.id)
            ],
        )
        for warehouse in inventory.warehouses(actor)
    ]


@router.post(
    "/warehouses/{warehouse_id}/locations",
    response_model=LocationReply,
    status_code=201,
    summary="Add a location",
)
def add_location(
    warehouse_id: uuid.UUID,
    payload: LocationContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
) -> LocationReply:
    location = inventory.add_location(actor, warehouse_id, code=payload.code, name=payload.name)
    return LocationReply.model_validate(location)


@router.post(
    "/locations/{location_id}/bins", response_model=BinReply, status_code=201, summary="Add a bin"
)
def add_bin(
    location_id: uuid.UUID,
    payload: BinContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
) -> BinReply:
    return BinReply.model_validate(
        inventory.add_bin(actor, location_id, code=payload.code, name=payload.name)
    )


# -- reports ---------------------------------------------------------------------------


@router.get("/positions", response_model=list[PositionReply], summary="Stock on hand")
def positions(
    actor: ActorDependency,
    reports: InventoryReportDependency,
    warehouse_id: uuid.UUID | None = None,
    q: str | None = None,
) -> list[PositionReply]:
    costs = actor.has("costing.view")
    found = reports.positions(actor, warehouse_id=warehouse_id, query=q)
    return [_position(row, costs=costs) for row in found]


@router.get("/low-stock", response_model=list[PositionReply], summary="At or below reorder")
def low_stock(actor: ActorDependency, reports: InventoryReportDependency) -> list[PositionReply]:
    costs = actor.has("costing.view")
    return [_position(row, costs=costs) for row in reports.low_stock(actor)]


@router.get("/expiry", response_model=list[BatchReply], summary="Batches near expiry")
def expiry(
    actor: ActorDependency, reports: InventoryReportDependency, within_days: int | None = None
) -> list[BatchReply]:
    return [_batch(row) for row in reports.expiry(actor, within_days=within_days)]


@router.get(
    "/products/{product_id}/batches", response_model=list[BatchReply], summary="Batches held"
)
def batches(
    product_id: uuid.UUID, actor: ActorDependency, reports: InventoryReportDependency
) -> list[BatchReply]:
    return [_batch(row) for row in reports.batches(actor, product_id)]


@router.get("/movements", response_model=list[MovementReply], summary="The stock ledger")
def movements(
    actor: ActorDependency,
    reports: InventoryReportDependency,
    product_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    operation_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[MovementReply]:
    found = reports.movements(
        actor,
        product_id=product_id,
        warehouse_id=warehouse_id,
        operation_id=operation_id,
        limit=limit,
    )
    costs = actor.has("costing.view")
    return [_movement(reports, movement, costs=costs) for movement in found]


@router.get("/valuation", response_model=ValuationReply, summary="Valuation and agreement")
def valuation(actor: ActorDependency, reports: InventoryReportDependency) -> ValuationReply:
    result = reports.valuation(actor)
    return ValuationReply(
        rows=[ValuationRowReply.model_validate(row, from_attributes=True) for row in result.rows],
        accounts=[
            AccountAgreementReply(
                account=account.account,
                name=account.name,
                layers=account.layers,
                ledger=account.ledger,
                difference=account.difference,
                agrees=account.difference == 0,
            )
            for account in result.accounts
        ],
        total=result.total,
        agrees=result.agrees,
    )


@router.get(
    "/reconciliation", response_model=ReconciliationReply, summary="The ledger, summed again"
)
def reconciliation(
    actor: ActorDependency, reports: InventoryReportDependency
) -> ReconciliationReply:
    result = reports.reconcile(actor)
    return ReconciliationReply(
        movements=result.movements,
        agrees=result.agrees,
        discrepancies=[
            DiscrepancyReply.model_validate(item, from_attributes=True)
            for item in result.discrepancies
        ],
    )


# -- operations ------------------------------------------------------------------------


@router.post("/opening", response_model=OperationReply, status_code=201, summary="Opening stock")
def opening(
    payload: OpeningContract, actor: ActorDependency, inventory: InventoryDependency
) -> OperationReply:
    return _operation(
        inventory.open_stock(
            actor,
            OpeningDraft(
                warehouse_id=payload.warehouse_id,
                lines=_lines(payload.lines),
                occurred_on=payload.occurred_on,
                reference=payload.reference,
            ),
        )
    )


@router.post("/adjustments", response_model=OperationReply, status_code=201, summary="Adjust stock")
def adjust(
    payload: AdjustmentContract, actor: ActorDependency, inventory: InventoryDependency
) -> OperationReply:
    return _operation(
        inventory.adjust(
            actor,
            AdjustmentDraft(
                warehouse_id=payload.warehouse_id,
                kind=payload.kind,
                reason=payload.reason,
                lines=_lines(payload.lines),
                occurred_on=payload.occurred_on,
            ),
        )
    )


@router.post(
    "/relocations", response_model=OperationReply, status_code=201, summary="Relocate stock"
)
def relocate(
    payload: RelocationContract, actor: ActorDependency, inventory: InventoryDependency
) -> OperationReply:
    return _operation(
        inventory.relocate(
            actor,
            product_id=payload.product_id,
            warehouse_id=payload.warehouse_id,
            quantity=payload.quantity,
            to_location_id=payload.to_location_id,
            to_bin_id=payload.to_bin_id,
            from_location_id=payload.from_location_id,
            from_bin_id=payload.from_bin_id,
            batch_id=payload.batch_id,
            serials=payload.serials,
        )
    )


@router.post(
    "/operations/{operation_id}/reverse",
    response_model=OperationReply,
    summary="Reverse an operation",
)
def reverse(
    operation_id: uuid.UUID,
    payload: ReverseContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
) -> OperationReply:
    return _operation(inventory.reverse(actor, operation_id, reason=payload.reason))


@router.post("/issues", response_model=OperationReply, status_code=201, summary="Issue stock")
def issue(
    payload: IssueContract, actor: ActorDependency, inventory: InventoryDependency
) -> OperationReply:
    return _operation(
        inventory.issue(
            actor,
            IssueDraft(
                product_id=payload.product_id,
                warehouse_id=payload.warehouse_id,
                quantity=payload.quantity,
                batch_id=payload.batch_id,
                serials=payload.serials,
                reservation_id=payload.reservation_id,
                location_id=payload.location_id,
                override_reason=payload.override_reason,
                reference=payload.reference,
            ),
        )
    )


# -- transfers -------------------------------------------------------------------------


@router.get("/transfers", response_model=list[TransferReply], summary="Transfers")
def transfers(
    actor: ActorDependency, inventory: InventoryDependency, reports: InventoryReportDependency
) -> list[TransferReply]:
    return [_transfer(reports, transfer) for transfer in inventory.transfers(actor)]


@router.post("/transfers", response_model=TransferReply, status_code=201, summary="Raise")
def raise_transfer(
    payload: TransferContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> TransferReply:
    transfer = inventory.raise_transfer(
        actor,
        TransferDraft(
            from_warehouse_id=payload.from_warehouse_id,
            to_warehouse_id=payload.to_warehouse_id,
            lines=[
                TransferLineDraft(
                    product_id=line.product_id,
                    quantity=line.quantity,
                    batch_id=line.batch_id,
                    serials=line.serials,
                )
                for line in payload.lines
            ],
            notes=payload.notes,
        ),
    )
    return _transfer(reports, transfer)


@router.post("/transfers/{transfer_id}/dispatch", response_model=TransferReply, summary="Dispatch")
def dispatch_transfer(
    transfer_id: uuid.UUID,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> TransferReply:
    return _transfer(reports, inventory.dispatch_transfer(actor, transfer_id))


@router.post("/transfers/{transfer_id}/receive", response_model=TransferReply, summary="Receive")
def receive_transfer(
    transfer_id: uuid.UUID,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> TransferReply:
    return _transfer(reports, inventory.receive_transfer(actor, transfer_id))


@router.post("/transfers/{transfer_id}/cancel", response_model=TransferReply, summary="Cancel")
def cancel_transfer(
    transfer_id: uuid.UUID,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> TransferReply:
    return _transfer(reports, inventory.cancel_transfer(actor, transfer_id))


# -- inspection and returns ------------------------------------------------------------


@router.get("/inspections", response_model=list[InspectionReply], summary="Inspections")
def inspections(
    actor: ActorDependency, inventory: InventoryDependency, reports: InventoryReportDependency
) -> list[InspectionReply]:
    return [_inspection(reports, item) for item in inventory.inspections(actor)]


@router.post(
    "/inspections", response_model=InspectionReply, status_code=201, summary="Record inspection"
)
def inspect(
    payload: InspectionContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> InspectionReply:
    inspection = inventory.inspect(
        actor,
        InspectionDraft(
            product_id=payload.product_id,
            warehouse_id=payload.warehouse_id,
            passed=payload.passed,
            failed=payload.failed,
            batch_id=payload.batch_id,
            receipt_line_id=payload.receipt_line_id,
            serials_passed=payload.serials_passed,
            serials_failed=payload.serials_failed,
            criteria=payload.criteria,
            findings=payload.findings,
            evidence_attachment_id=payload.evidence_attachment_id,
            inspected_on=payload.inspected_on,
        ),
    )
    return _inspection(reports, inspection)


@router.post(
    "/returns/supplier", response_model=OperationReply, status_code=201, summary="Return goods"
)
def return_to_supplier(
    payload: SupplierReturnContract, actor: ActorDependency, inventory: InventoryDependency
) -> OperationReply:
    return _operation(
        inventory.return_to_supplier(
            actor,
            product_id=payload.product_id,
            warehouse_id=payload.warehouse_id,
            quantity=payload.quantity,
            reason=payload.reason,
            batch_id=payload.batch_id,
            serials=payload.serials,
            receipt_line_id=payload.receipt_line_id,
        )
    )


@router.post(
    "/returns/customer", response_model=OperationReply, status_code=201, summary="Accept a return"
)
def customer_return(
    payload: CustomerReturnContract, actor: ActorDependency, inventory: InventoryDependency
) -> OperationReply:
    return _operation(
        inventory.customer_return(
            actor,
            issue_operation_id=payload.issue_operation_id,
            product_id=payload.product_id,
            quantity=payload.quantity,
            reason=payload.reason,
            serials=payload.serials,
        )
    )


# -- reservations ----------------------------------------------------------------------


@router.get("/reservations", response_model=list[ReservationReply], summary="Reservations")
def reservations(
    actor: ActorDependency, inventory: InventoryDependency, reports: InventoryReportDependency
) -> list[ReservationReply]:
    return [_reservation(reports, item) for item in inventory.reservations(actor)]


@router.post(
    "/reservations", response_model=ReservationReply, status_code=201, summary="Reserve stock"
)
def reserve(
    payload: ReservationContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> ReservationReply:
    reservation = inventory.reserve(
        actor,
        product_id=payload.product_id,
        warehouse_id=payload.warehouse_id,
        quantity=payload.quantity,
        sales_order_line_id=payload.sales_order_line_id,
        batch_id=payload.batch_id,
        notes=payload.notes,
    )
    return _reservation(reports, reservation)


@router.post(
    "/reservations/{reservation_id}/release",
    response_model=ReservationReply,
    summary="Release a reservation",
)
def release(
    reservation_id: uuid.UUID,
    payload: ReleaseContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> ReservationReply:
    return _reservation(
        reports, inventory.release(actor, reservation_id, quantity=payload.quantity)
    )


# -- counts ----------------------------------------------------------------------------


@router.get("/counts", response_model=list[CountReply], summary="Stock counts")
def counts(
    actor: ActorDependency, inventory: InventoryDependency, reports: InventoryReportDependency
) -> list[CountReply]:
    return [_count(reports, count) for count in inventory.counts(actor)]


@router.post("/counts", response_model=CountReply, status_code=201, summary="Open a count")
def open_count(
    payload: CountOpenContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> CountReply:
    count = inventory.open_count(
        actor,
        warehouse_id=payload.warehouse_id,
        location_id=payload.location_id,
        product_ids=payload.product_ids or None,
        notes=payload.notes,
    )
    return _count(reports, count)


@router.get("/counts/{count_id}", response_model=CountReply, summary="A count")
def count_detail(
    count_id: uuid.UUID,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> CountReply:
    return _count(reports, inventory.require_count(actor, count_id))


@router.put("/counts/{count_id}/lines", response_model=CountReply, summary="Record counts")
def record_count(
    count_id: uuid.UUID,
    payload: CountRecordContract,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> CountReply:
    count = inventory.record_count(
        actor,
        count_id,
        {line.line_id: (line.counted_quantity, line.reason) for line in payload.lines},
    )
    return _count(reports, count)


@router.post("/counts/{count_id}/submit", response_model=CountReply, summary="Submit a count")
def submit_count(
    count_id: uuid.UUID,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> CountReply:
    return _count(reports, inventory.submit_count(actor, count_id))


@router.post("/counts/{count_id}/approve", response_model=CountReply, summary="Approve a count")
def approve_count(
    count_id: uuid.UUID,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> CountReply:
    return _count(reports, inventory.approve_count(actor, count_id))


@router.post("/counts/{count_id}/cancel", response_model=CountReply, summary="Cancel a count")
def cancel_count(
    count_id: uuid.UUID,
    actor: ActorDependency,
    inventory: InventoryDependency,
    reports: InventoryReportDependency,
) -> CountReply:
    return _count(reports, inventory.cancel_count(actor, count_id))


# -- landed charges --------------------------------------------------------------------


@router.post(
    "/landed-charges",
    response_model=LandedChargeReply,
    status_code=201,
    summary="Record a landed charge",
)
def landed_charge(
    payload: LandedChargeContract, actor: ActorDependency, inventory: InventoryDependency
) -> LandedChargeReply:
    charge = inventory.landed_charge(
        actor,
        LandedChargeDraft(
            receipt_id=payload.receipt_id,
            category=payload.category,
            amount=payload.amount,
            method=payload.method,
            reference=payload.reference,
            payee_id=payload.payee_id,
            occurred_on=payload.occurred_on,
        ),
    )
    return LandedChargeReply.model_validate(charge)


@router.get(
    "/receipts/{receipt_id}/landed-charges",
    response_model=list[LandedChargeReply],
    summary="Landed charges on a receipt",
)
def landed_charges(
    receipt_id: uuid.UUID, actor: ActorDependency, inventory: InventoryDependency
) -> list[LandedChargeReply]:
    return [
        LandedChargeReply.model_validate(charge)
        for charge in inventory.landed_charges(actor, receipt_id)
    ]


# -- assembly --------------------------------------------------------------------------


@router.get("/assembly/{order_id}", response_model=AssemblyReply, summary="Assembly workspace")
def assembly(
    order_id: uuid.UUID,
    actor: ActorDependency,
    assembly_service: AssemblyDependency,
    reports: InventoryReportDependency,
) -> AssemblyReply:
    return _workspace(reports, assembly_service.workspace(actor, order_id))


@router.post(
    "/assembly/{order_id}/lines/{line_id}/reserve",
    response_model=AssemblyReply,
    summary="Reserve for a line",
)
def assembly_reserve(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: AssemblyQuantityContract,
    actor: ActorDependency,
    assembly_service: AssemblyDependency,
    reports: InventoryReportDependency,
) -> AssemblyReply:
    space = assembly_service.reserve(
        actor, order_id, line_id, payload.quantity, batch_id=payload.batch_id
    )
    return _workspace(reports, space)


@router.post(
    "/assembly/{order_id}/lines/{line_id}/release",
    response_model=AssemblyReply,
    summary="Release from a line",
)
def assembly_release(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: AssemblyQuantityContract,
    actor: ActorDependency,
    assembly_service: AssemblyDependency,
    reports: InventoryReportDependency,
) -> AssemblyReply:
    return _workspace(reports, assembly_service.release(actor, order_id, line_id, payload.quantity))


@router.post(
    "/assembly/{order_id}/lines/{line_id}/assemble",
    response_model=AssemblyReply,
    summary="Stage for dispatch",
)
def assembly_assemble(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: AssemblyQuantityContract,
    actor: ActorDependency,
    assembly_service: AssemblyDependency,
    reports: InventoryReportDependency,
) -> AssemblyReply:
    return _workspace(
        reports, assembly_service.assemble(actor, order_id, line_id, payload.quantity)
    )
