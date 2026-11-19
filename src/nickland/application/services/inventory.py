"""Inventory operations: what a storekeeper, an inspector and an assembler do. PRD §6.11.

Every operation here is a document or a decision that produces stock movements through
the engine. None writes a balance, a layer or a journal line itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.stock import Operation, StockEngine
from nickland.domain.catalogue import ValuationPolicy
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.inventory import (
    COST_OF_GOODS_SOLD,
    GOODS_RECEIVED_NOT_INVOICED,
    OTHER_PAYABLES,
    OWNERS_CAPITAL,
    WRITE_OFF,
    CountState,
    CountVariance,
    LocationKind,
    MovementType,
    ReservationState,
    StockState,
    TransferState,
    unit_cost,
)
from nickland.domain.ledger import JournalSource
from nickland.domain.money import WORKING_PRECISION, ZERO, allocate, money
from nickland.persistence.catalogue_models import Product
from nickland.persistence.inventory_models import (
    Batch,
    CostLayer,
    Inspection,
    InventorySettings,
    LandedCharge,
    StockBalance,
    StockBin,
    StockCount,
    StockCountLine,
    StockLocation,
    StockMovement,
    StockReservation,
    StockTransfer,
    StockTransferLine,
)
from nickland.persistence.models import Business, Warehouse
from nickland.persistence.order_models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrderLine,
    SalesOrderLine,
)
from nickland.platform.clock import Clock, SystemClock, business_date

LOSS_KINDS = {
    "damage": MovementType.DAMAGE,
    "expiry": MovementType.EXPIRY,
    "loss": MovementType.ADJUSTMENT_LOSS,
}
GAIN_KINDS = {"gain": MovementType.ADJUSTMENT_GAIN}

LANDED_CATEGORIES = ("freight", "duty", "clearing", "port", "handling", "insurance", "other")


@dataclass(slots=True)
class StockLineDraft:
    product_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal | None = None
    batch: str | None = None
    batch_id: uuid.UUID | None = None
    expiry: date | None = None
    manufacturer: str | None = None
    serials: list[str] = field(default_factory=list)
    location_id: uuid.UUID | None = None
    bin_id: uuid.UUID | None = None


@dataclass(slots=True)
class OpeningDraft:
    warehouse_id: uuid.UUID
    lines: list[StockLineDraft]
    occurred_on: date | None = None
    reference: str | None = None


@dataclass(slots=True)
class AdjustmentDraft:
    warehouse_id: uuid.UUID
    kind: str
    """``gain``, ``loss``, ``damage`` or ``expiry``."""
    reason: str
    lines: list[StockLineDraft]
    occurred_on: date | None = None


@dataclass(slots=True)
class TransferLineDraft:
    product_id: uuid.UUID
    quantity: Decimal
    batch_id: uuid.UUID | None = None
    serials: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransferDraft:
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    lines: list[TransferLineDraft]
    notes: str | None = None


@dataclass(slots=True)
class InspectionDraft:
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    passed: Decimal
    failed: Decimal
    batch_id: uuid.UUID | None = None
    receipt_line_id: uuid.UUID | None = None
    serials_passed: list[str] = field(default_factory=list)
    serials_failed: list[str] = field(default_factory=list)
    criteria: str | None = None
    findings: str | None = None
    evidence_attachment_id: uuid.UUID | None = None
    inspected_on: date | None = None


@dataclass(slots=True)
class IssueDraft:
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal
    batch_id: uuid.UUID | None = None
    serials: list[str] = field(default_factory=list)
    reservation_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    override_reason: str | None = None
    reference: str | None = None
    occurred_on: date | None = None


@dataclass(slots=True)
class LandedChargeDraft:
    receipt_id: uuid.UUID
    category: str
    amount: Decimal
    method: str = "by_value"
    reference: str | None = None
    payee_id: uuid.UUID | None = None
    occurred_on: date | None = None


class InventoryService:
    """The operations on stock a person performs."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self.engine = StockEngine(session, clock=self._clock)

    # -- configuration ----------------------------------------------------------------

    def settings(self, actor: AnyActor) -> InventorySettings:
        actor.require("stock.view")
        return self.engine.settings(_business_of(actor))

    def set_settings(
        self, actor: AnyActor, *, valuation_policy: str, expiry_warning_days: int
    ) -> InventorySettings:
        actor.require("stock.adjust")
        policy = ValuationPolicy(valuation_policy)
        if policy is ValuationPolicy.BUSINESS_DEFAULT:
            raise ValidationError("Choose moving average or FIFO.", field="valuation_policy")
        if not 0 <= expiry_warning_days <= 730:
            raise ValidationError(
                "The expiry warning lies between 0 and 730 days.", field="expiry_warning_days"
            )
        found = self.engine.settings(_business_of(actor))
        before = {"valuation_policy": found.valuation_policy, "days": found.expiry_warning_days}
        found.valuation_policy = policy.value
        found.expiry_warning_days = expiry_warning_days
        self._db.flush()
        self._audit.record(
            actor,
            "inventory_settings_changed",
            entity_type="inventory_settings",
            entity_id=found.id,
            before=before,
            after={"valuation_policy": policy.value, "days": expiry_warning_days},
        )
        return found

    def warehouses(self, actor: AnyActor) -> list[Warehouse]:
        actor.require("stock.view")
        found = list(
            self._db.scalars(
                select(Warehouse)
                .where(Warehouse.business_id == _business_of(actor), Warehouse.is_active)
                .order_by(Warehouse.is_default.desc(), Warehouse.code)
            ).all()
        )
        for warehouse in found:
            self.engine.locations(warehouse)
        return found

    def locations(self, actor: AnyActor, warehouse_id: uuid.UUID) -> list[StockLocation]:
        actor.require("stock.view")
        warehouse = self._warehouse(actor, warehouse_id)
        self.engine.locations(warehouse)
        return list(
            self._db.scalars(
                select(StockLocation)
                .where(StockLocation.warehouse_fk == warehouse.id)
                .order_by(StockLocation.code)
            ).all()
        )

    def add_location(
        self, actor: AnyActor, warehouse_id: uuid.UUID, *, code: str, name: str
    ) -> StockLocation:
        actor.require("location.manage")
        warehouse = self._warehouse(actor, warehouse_id)
        cleaned = _code(code)
        if self._db.scalar(
            select(StockLocation.id).where(
                StockLocation.warehouse_fk == warehouse.id, StockLocation.code == cleaned
            )
        ):
            raise ConflictError(f"Location {cleaned} already exists in {warehouse.code}.")
        location = StockLocation(
            business_id=warehouse.business_id,
            warehouse_fk=warehouse.id,
            code=cleaned,
            name=_required(name, "name"),
            kind=LocationKind.STORAGE.value,
        )
        self._db.add(location)
        self._db.flush()
        self._audit.record(
            actor,
            "stock_location_added",
            entity_type="stock_location",
            entity_id=location.id,
            entity_label=f"{warehouse.code}/{cleaned}",
        )
        return location

    def add_bin(
        self, actor: AnyActor, location_id: uuid.UUID, *, code: str, name: str | None = None
    ) -> StockBin:
        actor.require("location.manage")
        location = self._db.get(StockLocation, location_id)
        if location is None or location.business_id != _business_of(actor):
            raise NotFoundError("Location not found.")
        cleaned = _code(code, limit=24)
        if any(existing.code == cleaned for existing in location.bins):
            raise ConflictError(f"Bin {cleaned} already exists in {location.code}.")
        stock_bin = StockBin(
            business_id=location.business_id,
            location_fk=location.id,
            code=cleaned,
            name=(name or "").strip() or None,
        )
        self._db.add(stock_bin)
        self._db.flush()
        self._db.refresh(location)
        return stock_bin

    # -- opening stock and adjustments ------------------------------------------------

    def open_stock(self, actor: AnyActor, draft: OpeningDraft) -> Operation:
        """Stock on hand when the system starts, at what it cost. Dr 12000, Cr 30000."""
        actor.require("stock.adjust")
        warehouse = self._warehouse(actor, draft.warehouse_id)
        if not draft.lines:
            raise ValidationError("Opening stock carries at least one line.", field="lines")
        on = draft.occurred_on or self.today(actor)
        operation = self.engine.begin(
            actor,
            MovementType.OPENING,
            occurred_on=on,
            source_type="opening_stock",
            reference=(draft.reference or "").strip() or "Opening stock",
        )
        for item in draft.lines:
            product = self._product(actor, item.product_id)
            if item.unit_cost is None or item.unit_cost < ZERO:
                raise ValidationError(
                    f"{product.sku}: opening stock records what it cost.", field="unit_cost"
                )
            batch = self.engine.batch_for(
                product, item.batch, expires_on=item.expiry, manufacturer=item.manufacturer
            )
            operation.add(
                product,
                warehouse.id,
                StockState.USABLE,
                item.quantity,
                money(item.quantity * item.unit_cost),
                location_id=item.location_id,
                bin_id=item.bin_id,
                batch=batch,
                serials=item.serials,
                counter=OWNERS_CAPITAL,
            )
        operation.finish(f"Opening stock at {warehouse.code}", source=JournalSource.OPENING_BALANCE)
        self._audit.record(
            actor,
            "opening_stock_recorded",
            entity_type="stock_operation",
            entity_id=operation.id,
            entity_label=warehouse.code,
            after={"lines": len(draft.lines)},
        )
        return operation

    def adjust(self, actor: AnyActor, draft: AdjustmentDraft) -> Operation:
        """A gain or a loss found outside a count: damage, expiry, a correction."""
        actor.require("stock.adjust")
        warehouse = self._warehouse(actor, draft.warehouse_id)
        reason = _required(draft.reason, "reason")
        kind = draft.kind.strip().lower()
        if kind not in LOSS_KINDS and kind not in GAIN_KINDS:
            raise ValidationError(
                "An adjustment is a gain, a loss, damage or expiry.", field="kind"
            )
        if not draft.lines:
            raise ValidationError("An adjustment carries at least one line.", field="lines")
        movement_type = LOSS_KINDS.get(kind) or GAIN_KINDS[kind]
        operation = self.engine.begin(
            actor,
            movement_type,
            occurred_on=draft.occurred_on or self.today(actor),
            source_type="adjustment",
            reference=self._number(actor, "stock_adjustment"),
            reason=reason,
        )
        for item in draft.lines:
            product = self._product(actor, item.product_id)
            if kind in LOSS_KINDS:
                operation.take(
                    product,
                    warehouse.id,
                    StockState.USABLE,
                    item.quantity,
                    location_id=item.location_id,
                    bin_id=item.bin_id,
                    batch_id=item.batch_id,
                    serials=item.serials,
                    counter=WRITE_OFF,
                )
            else:
                batch = (
                    self._db.get(Batch, item.batch_id)
                    if item.batch_id
                    else self.engine.batch_for(product, item.batch, expires_on=item.expiry)
                )
                cost = (
                    item.unit_cost
                    if item.unit_cost is not None
                    else self.unit_cost(product, warehouse.id)
                )
                operation.add(
                    product,
                    warehouse.id,
                    StockState.USABLE,
                    item.quantity,
                    money(item.quantity * cost),
                    location_id=item.location_id,
                    bin_id=item.bin_id,
                    batch=batch,
                    serials=item.serials,
                    counter=WRITE_OFF,
                )
        operation.finish(f"Stock {kind} {operation.reference}: {reason}")
        self._audit.record(
            actor,
            "stock_adjusted",
            entity_type="stock_operation",
            entity_id=operation.id,
            entity_label=operation.reference,
            reason=reason,
            after={"kind": kind, "lines": len(draft.lines)},
        )
        return operation

    def unit_cost(self, product: Product, warehouse_id: uuid.UUID) -> Decimal:
        """The pool's current cost per unit: a gain is valued at what the stock is
        carried at, so it neither inflates nor deflates the average."""
        layers = list(
            self._db.scalars(
                select(CostLayer).where(
                    CostLayer.product_fk == product.id,
                    CostLayer.warehouse_fk == warehouse_id,
                    CostLayer.state == StockState.USABLE.value,
                    CostLayer.quantity_remaining > 0,
                )
            ).all()
        )
        quantity = sum((layer.quantity_remaining for layer in layers), ZERO)
        value = sum((layer.value_remaining for layer in layers), ZERO)
        if quantity > ZERO:
            return unit_cost(quantity, value)
        last = self._db.scalar(
            select(CostLayer)
            .where(CostLayer.product_fk == product.id, CostLayer.quantity_received > 0)
            .order_by(CostLayer.aged_on.desc(), CostLayer.id.desc())
        )
        if last is None:
            raise ConflictError(
                f"{product.sku} has no cost on record; give the unit cost of the gain."
            )
        return unit_cost(last.quantity_received, last.value_received)

    def reverse(self, actor: AnyActor, operation_id: uuid.UUID, *, reason: str) -> Operation:
        actor.require("stock.adjust")
        operation = self.engine.reverse(actor, operation_id, reason=reason, on=self.today(actor))
        self._audit.record(
            actor,
            "stock_operation_reversed",
            entity_type="stock_operation",
            entity_id=operation_id,
            reason=reason,
            after={"reversal": str(operation.id)},
        )
        return operation

    # -- moving stock -----------------------------------------------------------------

    def relocate(
        self,
        actor: AnyActor,
        *,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        to_location_id: uuid.UUID,
        to_bin_id: uuid.UUID | None = None,
        from_location_id: uuid.UUID | None = None,
        from_bin_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        serials: list[str] | None = None,
    ) -> Operation:
        actor.require("stock.transfer")
        warehouse = self._warehouse(actor, warehouse_id)
        product = self._product(actor, product_id)
        target = self._db.get(StockLocation, to_location_id)
        if target is None or target.warehouse_fk != warehouse.id:
            raise ValidationError("Relocate within the same warehouse.", field="to_location_id")
        operation = self.engine.begin(
            actor, MovementType.RELOCATION, occurred_on=self.today(actor), source_type="relocation"
        )
        operation.relocate(
            product,
            warehouse.id,
            quantity,
            to_location=target.id,
            to_bin=to_bin_id,
            from_location=from_location_id,
            from_bin=from_bin_id,
            batch_id=batch_id,
            serials=serials or [],
        )
        operation.finish(f"Relocation of {product.sku}")
        return operation

    def raise_transfer(self, actor: AnyActor, draft: TransferDraft) -> StockTransfer:
        actor.require("stock.transfer")
        source = self._warehouse(actor, draft.from_warehouse_id)
        destination = self._warehouse(actor, draft.to_warehouse_id)
        if source.id == destination.id:
            raise ValidationError("A transfer goes to a different warehouse.")
        if not draft.lines:
            raise ValidationError("A transfer carries at least one line.", field="lines")
        transfer = StockTransfer(
            business_id=source.business_id,
            number=self._number(actor, "stock_transfer"),
            from_warehouse_fk=source.id,
            to_warehouse_fk=destination.id,
            state=TransferState.DRAFT.value,
            requested_on=self.today(actor),
            notes=(draft.notes or "").strip() or None,
            created_by_id=actor.user_id,
        )
        for position, item in enumerate(draft.lines, start=1):
            product = self._product(actor, item.product_id)
            transfer.lines.append(
                StockTransferLine(
                    business_id=source.business_id,
                    position=position,
                    product_fk=product.id,
                    batch_fk=item.batch_id,
                    serials="\n".join(item.serials) or None,
                    quantity=_quantity(item.quantity),
                )
            )
        self._db.add(transfer)
        self._db.flush()
        self._audit.record(
            actor,
            "stock_transfer_raised",
            entity_type="stock_transfer",
            entity_id=transfer.id,
            entity_label=transfer.number,
        )
        return transfer

    def dispatch_transfer(self, actor: AnyActor, transfer_id: uuid.UUID) -> StockTransfer:
        """Usable stock leaves the source and is in transit to the destination."""
        actor.require("stock.transfer")
        transfer = self.require_transfer(actor, transfer_id)
        if transfer.state != TransferState.DRAFT.value:
            raise ConflictError(f"Transfer {transfer.number} has already been dispatched.")
        operation = self.engine.begin(
            actor,
            MovementType.TRANSFER_OUT,
            occurred_on=self.today(actor),
            source_type="stock_transfer",
            source_id=transfer.id,
            reference=transfer.number,
        )
        for line in transfer.lines:
            product = self._product(actor, line.product_fk)
            operation.move(
                product,
                line.quantity,
                from_warehouse=transfer.from_warehouse_fk,
                from_state=StockState.USABLE,
                to_warehouse=transfer.to_warehouse_fk,
                to_state=StockState.IN_TRANSIT,
                batch_id=line.batch_fk,
                serials=(line.serials or "").splitlines(),
            )
        operation.finish(f"Transfer {transfer.number} dispatched")
        transfer.state = TransferState.DISPATCHED.value
        transfer.dispatched_on = operation.occurred_on
        transfer.dispatch_operation_id = operation.id
        self._db.flush()
        self._audit.record(
            actor,
            "stock_transfer_dispatched",
            entity_type="stock_transfer",
            entity_id=transfer.id,
            entity_label=transfer.number,
        )
        return transfer

    def receive_transfer(self, actor: AnyActor, transfer_id: uuid.UUID) -> StockTransfer:
        actor.require("stock.transfer")
        transfer = self.require_transfer(actor, transfer_id)
        if transfer.state != TransferState.DISPATCHED.value:
            raise ConflictError(
                f"Transfer {transfer.number} is {transfer.state}; only a dispatched "
                "transfer is received."
            )
        operation = self.engine.begin(
            actor,
            MovementType.TRANSFER_IN,
            occurred_on=self.today(actor),
            source_type="stock_transfer",
            source_id=transfer.id,
            reference=transfer.number,
        )
        for line in transfer.lines:
            product = self._product(actor, line.product_fk)
            operation.move(
                product,
                line.quantity,
                from_warehouse=transfer.to_warehouse_fk,
                from_state=StockState.IN_TRANSIT,
                to_warehouse=transfer.to_warehouse_fk,
                to_state=StockState.USABLE,
                batch_id=line.batch_fk,
                serials=(line.serials or "").splitlines(),
            )
        operation.finish(f"Transfer {transfer.number} received")
        transfer.state = TransferState.RECEIVED.value
        transfer.received_on = operation.occurred_on
        transfer.receipt_operation_id = operation.id
        self._db.flush()
        self._audit.record(
            actor,
            "stock_transfer_received",
            entity_type="stock_transfer",
            entity_id=transfer.id,
            entity_label=transfer.number,
        )
        return transfer

    def cancel_transfer(self, actor: AnyActor, transfer_id: uuid.UUID) -> StockTransfer:
        actor.require("stock.transfer")
        transfer = self.require_transfer(actor, transfer_id)
        if transfer.state != TransferState.DRAFT.value:
            raise ConflictError(
                "A dispatched transfer is received at its destination, not cancelled."
            )
        transfer.state = TransferState.CANCELLED.value
        self._db.flush()
        return transfer

    def transfers(self, actor: AnyActor, *, limit: int = 100) -> list[StockTransfer]:
        actor.require("stock.view")
        return list(
            self._db.scalars(
                select(StockTransfer)
                .where(StockTransfer.business_id == _business_of(actor))
                .order_by(StockTransfer.created_at.desc())
                .limit(max(min(limit, 200), 1))
            ).all()
        )

    def require_transfer(self, actor: AnyActor, transfer_id: uuid.UUID) -> StockTransfer:
        actor.require("stock.view")
        transfer = self._db.get(StockTransfer, transfer_id)
        if transfer is None or transfer.business_id != _business_of(actor):
            raise NotFoundError("Transfer not found.")
        return transfer

    # -- inspection and returns -------------------------------------------------------

    def inspect(self, actor: AnyActor, draft: InspectionDraft) -> Inspection:
        """Quarantined stock passes to usable, or fails to rejected. PRD §6.8."""
        actor.require("inspection.manage")
        product = self._product(actor, draft.product_id)
        warehouse = self._warehouse(actor, draft.warehouse_id)
        passed = money(draft.passed)
        failed = money(draft.failed)
        if passed < ZERO or failed < ZERO or passed + failed <= ZERO:
            raise ValidationError("An inspection passes or fails something.", field="passed")
        if failed > ZERO and not (draft.findings or "").strip():
            raise ValidationError("A failure records the findings.", field="findings")
        on = draft.inspected_on or self.today(actor)
        receipt_line = (
            self._db.get(GoodsReceiptLine, draft.receipt_line_id)
            if draft.receipt_line_id
            else self._quarantined_receipt_line(product.id, warehouse.id, draft.batch_id)
        )
        operation = self.engine.begin(
            actor,
            MovementType.INSPECTION_PASS if passed > ZERO else MovementType.INSPECTION_FAIL,
            occurred_on=on,
            source_type="inspection",
            reference=(draft.criteria or "").strip()[:80] or None,
        )
        if passed > ZERO:
            operation.move(
                product,
                passed,
                from_warehouse=warehouse.id,
                from_state=StockState.QUARANTINE,
                to_warehouse=warehouse.id,
                to_state=StockState.USABLE,
                batch_id=draft.batch_id,
                serials=draft.serials_passed,
                movement_type=MovementType.INSPECTION_PASS,
            )
        if failed > ZERO:
            operation.move(
                product,
                failed,
                from_warehouse=warehouse.id,
                from_state=StockState.QUARANTINE,
                to_warehouse=warehouse.id,
                to_state=StockState.REJECTED,
                batch_id=draft.batch_id,
                serials=draft.serials_failed,
                movement_type=MovementType.INSPECTION_FAIL,
            )
        # Goods bought for an order line are held for it the moment they are usable.
        sales_line = self._sales_line_of(receipt_line)
        if sales_line is not None and passed > ZERO:
            self.reserve_for_line(operation, product, warehouse.id, sales_line, passed)
        operation.finish(f"Inspection of {product.sku}")
        inspection = Inspection(
            business_id=warehouse.business_id,
            product_fk=product.id,
            warehouse_fk=warehouse.id,
            batch_fk=draft.batch_id,
            receipt_line_fk=receipt_line.id if receipt_line else None,
            quantity_passed=passed,
            quantity_failed=failed,
            criteria=(draft.criteria or "").strip() or None,
            findings=(draft.findings or "").strip() or None,
            inspected_on=on,
            inspector_id=actor.user_id,
            evidence_attachment_fk=draft.evidence_attachment_id,
            operation_id=operation.id,
        )
        self._db.add(inspection)
        self._db.flush()
        self._audit.record(
            actor,
            "inspection_recorded",
            entity_type="inspection",
            entity_id=inspection.id,
            entity_label=product.sku,
            after={"passed": str(passed), "failed": str(failed)},
        )
        return inspection

    def inspections(self, actor: AnyActor, *, limit: int = 100) -> list[Inspection]:
        actor.require("stock.view")
        return list(
            self._db.scalars(
                select(Inspection)
                .where(Inspection.business_id == _business_of(actor))
                .order_by(Inspection.created_at.desc())
                .limit(max(min(limit, 200), 1))
            ).all()
        )

    def return_to_supplier(
        self,
        actor: AnyActor,
        *,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        reason: str,
        batch_id: uuid.UUID | None = None,
        serials: list[str] | None = None,
        receipt_line_id: uuid.UUID | None = None,
    ) -> Operation:
        """Rejected goods go back. The accrual for them is reversed: Dr 20150, Cr 12100."""
        actor.require("inspection.manage")
        product = self._product(actor, product_id)
        warehouse = self._warehouse(actor, warehouse_id)
        operation = self.engine.begin(
            actor,
            MovementType.RETURN_TO_SUPPLIER,
            occurred_on=self.today(actor),
            source_type="supplier_return",
            source_id=receipt_line_id,
            reason=_required(reason, "reason"),
        )
        operation.take(
            product,
            warehouse.id,
            StockState.REJECTED,
            quantity,
            batch_id=batch_id,
            serials=serials or [],
            counter=GOODS_RECEIVED_NOT_INVOICED,
        )
        receipt_line = self._db.get(GoodsReceiptLine, receipt_line_id) if receipt_line_id else None
        if receipt_line is not None:
            ordered = self._db.get(PurchaseOrderLine, receipt_line.purchase_order_line_fk)
            if ordered is not None:
                ordered.quantity_accepted = money(ordered.quantity_accepted - quantity)
                ordered.quantity_rejected = money(ordered.quantity_rejected + quantity)
        operation.finish(f"Return to supplier: {product.sku}")
        self._audit.record(
            actor,
            "stock_returned_to_supplier",
            entity_type="stock_operation",
            entity_id=operation.id,
            entity_label=product.sku,
            reason=reason,
            after={"quantity": str(quantity)},
        )
        return operation

    def customer_return(
        self,
        actor: AnyActor,
        *,
        issue_operation_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: Decimal,
        reason: str,
        serials: list[str] | None = None,
    ) -> Operation:
        """Goods come back into quarantine, at the cost they left at: Dr 12100, Cr 50000."""
        actor.require("sales_return.manage")
        product = self._product(actor, product_id)
        issued = [
            movement
            for movement in self.engine.movements_of(issue_operation_id)
            if movement.product_fk == product.id
            and movement.state == StockState.USABLE.value
            and movement.quantity < ZERO
            and movement.business_id == _business_of(actor)
        ]
        if not issued or MovementType(issued[0].movement_type) is not MovementType.ISSUE:
            raise NotFoundError("That issue of this product was not found.")
        out_quantity = -sum((movement.quantity for movement in issued), ZERO)
        out_value = -sum((movement.value for movement in issued), ZERO)
        returned_before = sum(
            (
                movement.quantity
                for movement in self._db.scalars(
                    select(StockMovement).where(
                        StockMovement.movement_type == MovementType.CUSTOMER_RETURN.value,
                        StockMovement.source_id == issue_operation_id,
                        StockMovement.product_fk == product.id,
                    )
                ).all()
            ),
            ZERO,
        )
        quantity = _quantity(quantity)
        if quantity + returned_before > out_quantity:
            raise ConflictError(
                f"Only {money(out_quantity - returned_before).normalize()} of that issue "
                "remains to be returned."
            )
        value = (
            money(out_value)
            if quantity + returned_before == out_quantity and returned_before == ZERO
            else money(out_value * quantity / out_quantity)
        )
        operation = self.engine.begin(
            actor,
            MovementType.CUSTOMER_RETURN,
            occurred_on=self.today(actor),
            source_type="customer_return",
            source_id=issue_operation_id,
            reference=issued[0].reference,
            reason=_required(reason, "reason"),
        )
        batch = self._db.get(Batch, issued[0].batch_fk) if issued[0].batch_fk else None
        operation.add(
            product,
            issued[0].warehouse_fk,
            StockState.QUARANTINE,
            quantity,
            value,
            batch=batch,
            serials=serials or [],
            counter=COST_OF_GOODS_SOLD,
        )
        operation.finish(f"Customer return of {product.sku}")
        self._audit.record(
            actor,
            "customer_return_received",
            entity_type="stock_operation",
            entity_id=operation.id,
            entity_label=product.sku,
            reason=reason,
        )
        return operation

    # -- reservations and issue -------------------------------------------------------

    def reserve(
        self,
        actor: AnyActor,
        *,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        sales_order_line_id: uuid.UUID | None = None,
        batch_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> StockReservation:
        actor.require("assembly.manage")
        product = self._product(actor, product_id)
        warehouse = self._warehouse(actor, warehouse_id)
        operation = self.engine.begin(
            actor,
            MovementType.RESERVATION,
            occurred_on=self.today(actor),
            source_type="reservation",
            source_id=sales_order_line_id,
        )
        sales_line = (
            self._db.get(SalesOrderLine, sales_order_line_id) if sales_order_line_id else None
        )
        reservation = self.hold(
            operation, product, warehouse.id, quantity, sales_line, batch_id=batch_id
        )
        reservation.notes = (notes or "").strip() or None
        operation.finish(f"Reservation of {product.sku}")
        return reservation

    def hold(
        self,
        operation: Operation,
        product: Product,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        sales_line: SalesOrderLine | None,
        *,
        batch_id: uuid.UUID | None = None,
    ) -> StockReservation:
        quantity = _quantity(quantity)
        operation.reserve(product, warehouse_id, quantity, batch_id=batch_id)
        reservation = StockReservation(
            business_id=operation.business_id,
            product_fk=product.id,
            warehouse_fk=warehouse_id,
            batch_fk=batch_id,
            sales_order_line_fk=sales_line.id if sales_line else None,
            quantity=quantity,
            state=ReservationState.ACTIVE.value,
            reserved_by_id=operation.actor.user_id,
        )
        self._db.add(reservation)
        self._db.flush()
        return reservation

    def reserve_for_line(
        self,
        operation: Operation,
        product: Product,
        warehouse_id: uuid.UUID,
        sales_line: SalesOrderLine,
        quantity: Decimal,
    ) -> StockReservation | None:
        """Hold goods for the order line they were bought for, up to what it still needs."""
        held = self.reserved_for(sales_line.id)
        needed = money(
            sales_line.quantity_ordered
            - sales_line.quantity_cancelled
            - sales_line.quantity_dispatched
            - held
        )
        portion = min(quantity, needed)
        if portion <= ZERO:
            return None
        return self.hold(operation, product, warehouse_id, portion, sales_line)

    def reserved_for(self, sales_line_id: uuid.UUID) -> Decimal:
        return money(
            sum(
                (
                    reservation.quantity
                    - reservation.quantity_consumed
                    - reservation.quantity_released
                    for reservation in self.reservations_for(sales_line_id)
                ),
                ZERO,
            )
        )

    def reservations_for(self, sales_line_id: uuid.UUID) -> list[StockReservation]:
        return list(
            self._db.scalars(
                select(StockReservation)
                .where(
                    StockReservation.sales_order_line_fk == sales_line_id,
                    StockReservation.state == ReservationState.ACTIVE.value,
                )
                .order_by(StockReservation.created_at, StockReservation.id)
            ).all()
        )

    def release(
        self,
        actor: AnyActor,
        reservation_id: uuid.UUID,
        *,
        quantity: Decimal | None = None,
    ) -> StockReservation:
        actor.require("assembly.manage")
        reservation = self.require_reservation(actor, reservation_id)
        holding = open_quantity(reservation)
        quantity = holding if quantity is None else _quantity(quantity)
        if quantity > holding:
            raise ConflictError("More cannot be released than the reservation holds.")
        product = self._product(actor, reservation.product_fk)
        operation = self.engine.begin(
            actor,
            MovementType.RELEASE,
            occurred_on=self.today(actor),
            source_type="reservation",
            source_id=reservation.id,
        )
        operation.release(
            product, reservation.warehouse_fk, quantity, batch_id=reservation.batch_fk
        )
        reservation.quantity_released = money(reservation.quantity_released + quantity)
        _settle(reservation)
        operation.finish(f"Release of {product.sku}")
        return reservation

    def require_reservation(self, actor: AnyActor, reservation_id: uuid.UUID) -> StockReservation:
        actor.require("stock.view")
        reservation = self._db.get(StockReservation, reservation_id)
        if reservation is None or reservation.business_id != _business_of(actor):
            raise NotFoundError("Reservation not found.")
        return reservation

    def reservations(self, actor: AnyActor, *, limit: int = 100) -> list[StockReservation]:
        actor.require("stock.view")
        return list(
            self._db.scalars(
                select(StockReservation)
                .where(
                    StockReservation.business_id == _business_of(actor),
                    StockReservation.state == ReservationState.ACTIVE.value,
                )
                .order_by(StockReservation.created_at.desc())
                .limit(max(min(limit, 200), 1))
            ).all()
        )

    def issue(self, actor: AnyActor, draft: IssueDraft) -> Operation:
        """Stock leaves to a customer at cost: Dr 50000, Cr 12000.

        A batch-tracked product is issued from a named batch. An expired batch is
        refused unless an override is recorded by someone permitted to give one.
        """
        actor.require("assembly.manage")
        product = self._product(actor, draft.product_id)
        warehouse = self._warehouse(actor, draft.warehouse_id)
        if product.tracking_policy == "batch" and draft.batch_id is None:
            raise ValidationError(
                f"{product.sku} is batch-tracked; name the batch it is issued from.",
                field="batch_id",
            )
        override = (draft.override_reason or "").strip() or None
        if override is not None:
            actor.require("expiry.override")
        operation = self.engine.begin(
            actor,
            MovementType.ISSUE,
            occurred_on=draft.occurred_on or self.today(actor),
            source_type="issue",
            source_id=draft.reservation_id,
            reference=(draft.reference or "").strip() or None,
        )
        self._issue(
            operation,
            product,
            warehouse.id,
            draft.quantity,
            batch_id=draft.batch_id,
            serials=draft.serials,
            reservation=(
                self.require_reservation(actor, draft.reservation_id)
                if draft.reservation_id
                else None
            ),
            location_id=draft.location_id,
            override=override,
        )
        operation.finish(f"Issue of {product.sku}")
        self._audit.record(
            actor,
            "stock_issued",
            entity_type="stock_operation",
            entity_id=operation.id,
            entity_label=product.sku,
            reason=override,
            after={"quantity": str(draft.quantity), "expiry_override": bool(override)},
        )
        return operation

    def _issue(
        self,
        operation: Operation,
        product: Product,
        warehouse_id: uuid.UUID,
        quantity: Decimal,
        *,
        batch_id: uuid.UUID | None,
        serials: list[str],
        reservation: StockReservation | None,
        location_id: uuid.UUID | None,
        override: str | None,
    ) -> Decimal:
        quantity = _quantity(quantity)
        if reservation is not None:
            if reservation.product_fk != product.id or reservation.warehouse_fk != warehouse_id:
                raise ValidationError("That reservation is for other stock.")
            if quantity > open_quantity(reservation):
                raise ConflictError(
                    f"The reservation holds {open_quantity(reservation).normalize()}; "
                    f"{quantity.normalize()} was to be issued."
                )
            operation.release(
                product,
                warehouse_id,
                quantity,
                batch_id=reservation.batch_fk,
                movement_type=MovementType.ISSUE,
            )
            reservation.quantity_consumed = money(reservation.quantity_consumed + quantity)
            _settle(reservation)
        drawn = operation.take(
            product,
            warehouse_id,
            StockState.USABLE,
            quantity,
            location_id=location_id,
            batch_id=batch_id or (reservation.batch_fk if reservation else None),
            serials=serials,
            exclude_expired=True,
            override_reason=override,
            counter=COST_OF_GOODS_SOLD,
            movement_type=MovementType.ISSUE,
        )
        return money(sum((part.value for part in drawn), ZERO))

    # -- counts -----------------------------------------------------------------------

    def open_count(
        self,
        actor: AnyActor,
        *,
        warehouse_id: uuid.UUID,
        location_id: uuid.UUID | None = None,
        product_ids: list[uuid.UUID] | None = None,
        notes: str | None = None,
    ) -> StockCount:
        """Freeze what the ledger says, so the variance is against a fixed figure."""
        actor.require("stock_count.manage")
        warehouse = self._warehouse(actor, warehouse_id)
        count = StockCount(
            business_id=warehouse.business_id,
            number=self._number(actor, "stock_adjustment"),
            warehouse_fk=warehouse.id,
            location_fk=location_id,
            state=CountState.OPEN.value,
            counted_on=self.today(actor),
            notes=(notes or "").strip() or None,
            opened_by_id=actor.user_id,
        )
        query = select(StockBalance).where(
            StockBalance.business_id == warehouse.business_id,
            StockBalance.warehouse_fk == warehouse.id,
            StockBalance.state == StockState.USABLE.value,
        )
        if location_id is not None:
            query = query.where(StockBalance.location_fk == location_id)
        if product_ids:
            query = query.where(StockBalance.product_fk.in_(product_ids))
        balances = [
            balance
            for balance in self._db.scalars(query).all()
            if balance.quantity > ZERO or product_ids
        ]
        if not balances:
            raise ConflictError("Nothing is held there to count.")
        products = {
            product.id: product
            for product in self._db.scalars(
                select(Product).where(Product.id.in_({b.product_fk for b in balances}))
            ).all()
        }
        balances.sort(
            key=lambda b: (products[b.product_fk].sku, str(b.location_fk), str(b.batch_fk))
        )
        for position, balance in enumerate(balances, start=1):
            count.lines.append(
                StockCountLine(
                    business_id=warehouse.business_id,
                    position=position,
                    product_fk=balance.product_fk,
                    location_fk=balance.location_fk,
                    bin_fk=balance.bin_fk,
                    batch_fk=balance.batch_fk,
                    system_quantity=balance.quantity,
                )
            )
        self._db.add(count)
        self._db.flush()
        self._audit.record(
            actor,
            "stock_count_opened",
            entity_type="stock_count",
            entity_id=count.id,
            entity_label=count.number,
            after={"lines": len(count.lines)},
        )
        return count

    def record_count(
        self,
        actor: AnyActor,
        count_id: uuid.UUID,
        counted: dict[uuid.UUID, tuple[Decimal | None, str | None]],
    ) -> StockCount:
        actor.require("stock_count.manage")
        count = self.require_count(actor, count_id)
        if count.state != CountState.OPEN.value:
            raise ConflictError(f"Count {count.number} is {count.state} and cannot be changed.")
        lines = {line.id: line for line in count.lines}
        for line_id, (quantity, reason) in counted.items():
            line = lines.get(line_id)
            if line is None:
                raise NotFoundError("That line is not on this count.")
            if quantity is not None and quantity < ZERO:
                raise ValidationError("A counted quantity is not negative.")
            line.counted_quantity = money(quantity) if quantity is not None else None
            line.reason = (reason or "").strip() or None
        self._db.flush()
        return count

    def submit_count(self, actor: AnyActor, count_id: uuid.UUID) -> StockCount:
        actor.require("stock_count.manage")
        count = self.require_count(actor, count_id)
        if count.state != CountState.OPEN.value:
            raise ConflictError(f"Count {count.number} has already been submitted.")
        missing = [line.position for line in count.lines if line.counted_quantity is None]
        if missing:
            raise ConflictError(
                f"Lines {', '.join(str(p) for p in missing[:8])} have not been counted."
            )
        for line in count.lines:
            assert line.counted_quantity is not None
            if line.counted_quantity != line.system_quantity and not line.reason:
                raise ValidationError(
                    f"Line {line.position}: a variance records its reason.", field="reason"
                )
        count.state = CountState.SUBMITTED.value
        count.submitted_by_id = actor.user_id
        self._db.flush()
        return count

    def approve_count(self, actor: AnyActor, count_id: uuid.UUID) -> StockCount:
        """Post the variances. Gains at the pool's current cost; losses at what they
        were carried at. Dr/Cr 51000 against the inventory account."""
        actor.require("stock_count.approve")
        count = self.require_count(actor, count_id)
        if count.state != CountState.SUBMITTED.value:
            raise ConflictError(f"Count {count.number} is not awaiting approval.")
        if (
            count.submitted_by_id is not None
            and count.submitted_by_id == actor.user_id
            and not actor.is_administrator
        ):
            raise ConflictError("A count is approved by someone other than its counter.")
        operation = self.engine.begin(
            actor,
            MovementType.COUNT_GAIN,
            occurred_on=count.counted_on,
            source_type="stock_count",
            source_id=count.id,
            reference=count.number,
        )
        for line in count.lines:
            assert line.counted_quantity is not None
            variance = CountVariance(line.system_quantity, line.counted_quantity)
            kind = variance.movement
            if kind is None:
                continue
            product = self._product(actor, line.product_fk)
            if kind is MovementType.COUNT_LOSS:
                operation.take(
                    product,
                    count.warehouse_fk,
                    StockState.USABLE,
                    -variance.difference,
                    location_id=line.location_fk,
                    bin_id=line.bin_fk,
                    batch_id=line.batch_fk,
                    counter=WRITE_OFF,
                    movement_type=kind,
                )
            else:
                cost = self.unit_cost(product, count.warehouse_fk)
                operation.add(
                    product,
                    count.warehouse_fk,
                    StockState.USABLE,
                    variance.difference,
                    money(variance.difference * cost),
                    location_id=line.location_fk,
                    bin_id=line.bin_fk,
                    batch=self._db.get(Batch, line.batch_fk) if line.batch_fk else None,
                    counter=WRITE_OFF,
                    movement_type=kind,
                )
        operation.finish(f"Stock count {count.number}")
        count.state = CountState.POSTED.value
        count.approved_by_id = actor.user_id
        count.posted_operation_id = operation.id
        self._db.flush()
        self._audit.record(
            actor,
            "stock_count_posted",
            entity_type="stock_count",
            entity_id=count.id,
            entity_label=count.number,
        )
        return count

    def cancel_count(self, actor: AnyActor, count_id: uuid.UUID) -> StockCount:
        actor.require("stock_count.manage")
        count = self.require_count(actor, count_id)
        if count.state == CountState.POSTED.value:
            raise ConflictError("A posted count is corrected by a further count.")
        count.state = CountState.CANCELLED.value
        self._db.flush()
        return count

    def counts(self, actor: AnyActor, *, limit: int = 100) -> list[StockCount]:
        actor.require("stock.view")
        return list(
            self._db.scalars(
                select(StockCount)
                .where(StockCount.business_id == _business_of(actor))
                .order_by(StockCount.created_at.desc())
                .limit(max(min(limit, 200), 1))
            ).all()
        )

    def require_count(self, actor: AnyActor, count_id: uuid.UUID) -> StockCount:
        actor.require("stock.view")
        count = self._db.get(StockCount, count_id)
        if count is None or count.business_id != _business_of(actor):
            raise NotFoundError("Count not found.")
        return count

    # -- landed charges ---------------------------------------------------------------

    def landed_charge(self, actor: AnyActor, draft: LandedChargeDraft) -> LandedCharge:
        """Carry a charge into the stock a receipt brought in: Dr 12000/12100 or 50000,
        Cr 20100. Apportioned across the receipt's lines by value or by quantity."""
        actor.require("goods_receipt.manage")
        receipt = self._db.get(GoodsReceipt, draft.receipt_id)
        if receipt is None or receipt.business_id != _business_of(actor):
            raise NotFoundError("Goods receipt not found.")
        category = draft.category.strip().lower()
        if category not in LANDED_CATEGORIES:
            raise ValidationError(
                f"A landed charge is one of: {', '.join(LANDED_CATEGORIES)}.", field="category"
            )
        if draft.method not in ("by_value", "by_quantity"):
            raise ValidationError("Apportion by value or by quantity.", field="method")
        amount = money(draft.amount)
        if amount <= ZERO:
            raise ValidationError("A landed charge is above zero.", field="amount")
        lines = [line for line in receipt.lines if line.quantity_accepted > ZERO]
        if not lines:
            raise ConflictError("Nothing was accepted on that receipt to carry the charge.")
        weights = [
            money(line.unit_cost * line.quantity_accepted)
            if draft.method == "by_value"
            else line.quantity_accepted
            for line in lines
        ]
        if sum(weights, ZERO) <= ZERO:
            weights = [line.quantity_accepted for line in lines]
        shares = allocate(amount, weights, WORKING_PRECISION)
        operation = self.engine.begin(
            actor,
            MovementType.REVALUATION,
            occurred_on=draft.occurred_on or self.today(actor),
            source_type="landed_charge",
            source_id=receipt.id,
            reference=receipt.number,
        )
        to_inventory = ZERO
        to_cost = ZERO
        for line, share in zip(lines, shares, strict=True):
            layered, issued = operation.revalue([line.id], share)
            to_inventory = money(to_inventory + layered)
            to_cost = money(to_cost + issued)
        operation.credit(OTHER_PAYABLES, amount)
        operation.finish(
            f"{category.capitalize()} on {receipt.number}"
            + (f" ({draft.reference.strip()})" if draft.reference else ""),
            source=JournalSource.PURCHASES,
        )
        charge = LandedCharge(
            business_id=receipt.business_id,
            receipt_fk=receipt.id,
            category=category,
            method=draft.method,
            reference=(draft.reference or "").strip() or None,
            payee_fk=draft.payee_id,
            amount=amount,
            to_inventory=to_inventory,
            to_cost_of_sales=to_cost,
            operation_id=operation.id,
            journal_entry_fk=operation.journal.id if operation.journal else None,
            recorded_by_id=actor.user_id,
        )
        self._db.add(charge)
        self._db.flush()
        self._audit.record(
            actor,
            "landed_charge_recorded",
            entity_type="landed_charge",
            entity_id=charge.id,
            entity_label=receipt.number,
            after={"amount": str(amount), "to_inventory": str(to_inventory)},
        )
        return charge

    def landed_charges(self, actor: AnyActor, receipt_id: uuid.UUID) -> list[LandedCharge]:
        actor.require("stock.view")
        return list(
            self._db.scalars(
                select(LandedCharge)
                .where(
                    LandedCharge.business_id == _business_of(actor),
                    LandedCharge.receipt_fk == receipt_id,
                )
                .order_by(LandedCharge.created_at)
            ).all()
        )

    # -- helpers ----------------------------------------------------------------------

    def default_warehouse(self, business_id: uuid.UUID, branch_id: uuid.UUID | None) -> Warehouse:
        candidates = list(
            self._db.scalars(
                select(Warehouse)
                .where(Warehouse.business_id == business_id, Warehouse.is_active)
                .order_by(Warehouse.is_default.desc(), Warehouse.code)
            ).all()
        )
        for warehouse in candidates:
            if branch_id is not None and warehouse.branch_fk == branch_id:
                return warehouse
        if not candidates:
            raise ConflictError("No warehouse is set up.")
        return candidates[0]

    def _quarantined_receipt_line(
        self, product_id: uuid.UUID, warehouse_id: uuid.UUID, batch_id: uuid.UUID | None
    ) -> GoodsReceiptLine | None:
        """The receipt the oldest quarantined stock came in on, where none is named."""
        query = select(CostLayer).where(
            CostLayer.product_fk == product_id,
            CostLayer.warehouse_fk == warehouse_id,
            CostLayer.state == StockState.QUARANTINE.value,
            CostLayer.quantity_remaining > 0,
            CostLayer.source_type == "goods_receipt",
        )
        if batch_id is not None:
            query = query.where(CostLayer.batch_fk == batch_id)
        layer = self._db.scalar(query.order_by(CostLayer.aged_on, CostLayer.id))
        if layer is None or layer.source_line_id is None:
            return None
        return self._db.get(GoodsReceiptLine, layer.source_line_id)

    def _sales_line_of(self, receipt_line: GoodsReceiptLine | None) -> SalesOrderLine | None:
        if receipt_line is None:
            return None
        ordered = self._db.get(PurchaseOrderLine, receipt_line.purchase_order_line_fk)
        if ordered is None or ordered.sales_order_line_fk is None:
            return None
        return self._db.get(SalesOrderLine, ordered.sales_order_line_fk)

    def _product(self, actor: AnyActor, product_id: uuid.UUID) -> Product:
        product = self._db.get(Product, product_id)
        if product is None or product.business_id != _business_of(actor):
            raise NotFoundError("Product not found.")
        return product

    def _warehouse(self, actor: AnyActor, warehouse_id: uuid.UUID) -> Warehouse:
        warehouse = self._db.get(Warehouse, warehouse_id)
        if warehouse is None or warehouse.business_id != _business_of(actor):
            raise NotFoundError("Warehouse not found.")
        return warehouse

    def _number(self, actor: AnyActor, kind: str) -> str:
        return (
            OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, kind, branch_id=actor.branch_id)
            .number
        )

    def today(self, actor: AnyActor) -> date:
        business = self._db.get(Business, actor.business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)


def open_quantity(reservation: StockReservation) -> Decimal:
    return money(
        reservation.quantity - reservation.quantity_consumed - reservation.quantity_released
    )


def _settle(reservation: StockReservation) -> None:
    if open_quantity(reservation) > ZERO:
        return
    reservation.state = (
        ReservationState.CONSUMED.value
        if reservation.quantity_consumed > ZERO
        else ReservationState.RELEASED.value
    )


def _quantity(value: Decimal) -> Decimal:
    quantity = money(value)
    if quantity <= ZERO:
        raise ValidationError("A quantity is above zero.", field="quantity")
    return quantity


def _required(value: str | None, field_name: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError(f"The {field_name.replace('_', ' ')} is required.", field=field_name)
    return cleaned


def _code(value: str, *, limit: int = 16) -> str:
    cleaned = value.strip().upper()
    if not cleaned or len(cleaned) > limit or not cleaned.replace("-", "").isalnum():
        raise ValidationError(
            f"A code is letters, digits and hyphens, up to {limit} characters.", field="code"
        )
    return cleaned


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
