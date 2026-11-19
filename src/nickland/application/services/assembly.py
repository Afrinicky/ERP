"""Assembling a customer order from stock and receipts. PRD §6.11, §13 Phase 6.

Per order line: what is required, what is available in the warehouse, what is reserved
for this line, what is incoming on purchase orders raised for it, what is assembled at
the warehouse's assembly location, and what has been dispatched. Reserving holds stock;
assembling moves the held stock to the assembly bay; dispatching issues it to the
customer at cost.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.inventory import InventoryService, open_quantity
from nickland.application.services.orders import SalesOrderService
from nickland.application.services.stock import holds_stock
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.inventory import (
    COST_OF_GOODS_SOLD,
    LocationKind,
    MovementType,
    ReservationState,
    StockState,
)
from nickland.domain.money import ZERO, money
from nickland.domain.orders import RECEIVABLE_STATES
from nickland.persistence.catalogue_models import Product
from nickland.persistence.inventory_models import Batch, StockBalance
from nickland.persistence.models import Warehouse
from nickland.persistence.order_models import (
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
)
from nickland.platform.clock import Clock, SystemClock


@dataclass(slots=True)
class AssemblyLine:
    line_id: uuid.UUID
    position: int
    description: str
    product_id: uuid.UUID | None
    sku: str | None
    tracking: str
    holds_stock: bool
    required: Decimal
    available: Decimal
    reserved: Decimal
    incoming: Decimal
    assembled: Decimal
    dispatched: Decimal

    @property
    def outstanding(self) -> Decimal:
        return money(max(ZERO, self.required - self.dispatched))

    @property
    def staged(self) -> Decimal:
        """At the assembly bay now: assembled and not yet dispatched."""
        return money(max(ZERO, self.assembled - self.dispatched))

    @property
    def to_reserve(self) -> Decimal:
        return money(max(ZERO, self.outstanding - self.reserved))


@dataclass(slots=True)
class Workspace:
    order: SalesOrder
    warehouse: Warehouse
    lines: list[AssemblyLine] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Every stock line staged in full: the order can be dispatched."""
        stock_lines = [line for line in self.lines if line.holds_stock and line.outstanding]
        return bool(stock_lines) and all(line.staged >= line.outstanding for line in stock_lines)


class AssemblyService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._inventory = InventoryService(session, clock=self._clock)
        self._orders = SalesOrderService(session, clock=self._clock)

    def workspace(self, actor: AnyActor, order_id: uuid.UUID) -> Workspace:
        actor.require("stock.view")
        order = self._orders.require(actor, order_id)
        warehouse = self._inventory.default_warehouse(order.business_id, order.branch_id)
        today = self._inventory.today(actor)
        incoming = self._incoming(order)
        lines: list[AssemblyLine] = []
        for line in order.lines:
            product = self._db.get(Product, line.product_fk) if line.product_fk else None
            stocked = holds_stock(product)
            lines.append(
                AssemblyLine(
                    line_id=line.id,
                    position=line.position,
                    description=line.description,
                    product_id=product.id if product else None,
                    sku=product.sku if product else None,
                    tracking=product.tracking_policy if product else "none",
                    holds_stock=stocked,
                    required=money(line.quantity_ordered - line.quantity_cancelled),
                    available=(
                        self._inventory.engine.available(
                            order.business_id, product.id, warehouse.id, today
                        )
                        if stocked and product
                        else ZERO
                    ),
                    reserved=self._inventory.reserved_for(line.id),
                    incoming=incoming.get(line.id, ZERO),
                    assembled=line.quantity_assembled,
                    dispatched=line.quantity_dispatched,
                )
            )
        return Workspace(order=order, warehouse=warehouse, lines=lines)

    # -- actions ----------------------------------------------------------------------

    def reserve(
        self,
        actor: AnyActor,
        order_id: uuid.UUID,
        line_id: uuid.UUID,
        quantity: Decimal,
        *,
        batch_id: uuid.UUID | None = None,
    ) -> Workspace:
        actor.require("assembly.manage")
        space = self.workspace(actor, order_id)
        line, view, product = self._line(space, line_id)
        quantity = _quantity(quantity)
        if quantity > view.to_reserve:
            raise ConflictError(
                f"Line {view.position} needs {view.to_reserve.normalize()} more; "
                f"{quantity.normalize()} was asked for."
            )
        operation = self._inventory.engine.begin(
            actor,
            MovementType.RESERVATION,
            occurred_on=self._inventory.today(actor),
            source_type="sales_order",
            source_id=space.order.id,
            reference=space.order.number,
        )
        self._inventory.hold(
            operation, product, space.warehouse.id, quantity, line, batch_id=batch_id
        )
        operation.finish(f"Stock reserved for {space.order.number}")
        return self.workspace(actor, order_id)

    def release(
        self, actor: AnyActor, order_id: uuid.UUID, line_id: uuid.UUID, quantity: Decimal
    ) -> Workspace:
        actor.require("assembly.manage")
        space = self.workspace(actor, order_id)
        line, view, _ = self._line(space, line_id)
        quantity = _quantity(quantity)
        free = money(view.reserved - view.staged)
        if quantity > free:
            raise ConflictError(
                f"Line {view.position}: {max(free, ZERO).normalize()} can be released; what is "
                "staged for dispatch is returned to storage first."
            )
        remaining = quantity
        for reservation in reversed(self._inventory.reservations_for(line.id)):
            if remaining <= ZERO:
                break
            portion = min(open_quantity(reservation), remaining)
            self._inventory.release(actor, reservation.id, quantity=portion)
            remaining = money(remaining - portion)
        return self.workspace(actor, order_id)

    def assemble(
        self, actor: AnyActor, order_id: uuid.UUID, line_id: uuid.UUID, quantity: Decimal
    ) -> Workspace:
        """Move held stock to the assembly bay, ready to go out together."""
        actor.require("assembly.manage")
        space = self.workspace(actor, order_id)
        line, view, product = self._line(space, line_id)
        quantity = _quantity(quantity)
        stageable = money(view.reserved - view.staged)
        if quantity > stageable:
            raise ConflictError(
                f"Line {view.position}: {max(stageable, ZERO).normalize()} is reserved and not "
                "yet staged. Reserve it first."
            )
        bay = self._inventory.engine.location_of_kind(space.warehouse.id, LocationKind.ASSEMBLY)
        operation = self._inventory.engine.begin(
            actor,
            MovementType.RELOCATION,
            occurred_on=self._inventory.today(actor),
            source_type="assembly",
            source_id=line.id,
            reference=space.order.number,
        )
        remaining = quantity
        for balance in self._balances(product, space.warehouse.id, exclude=bay.id):
            if remaining <= ZERO:
                break
            portion = min(balance.quantity, remaining)
            operation.relocate(
                product,
                space.warehouse.id,
                portion,
                to_location=bay.id,
                from_location=balance.location_fk,
                from_bin=balance.bin_fk,
                batch_id=balance.batch_fk,
            )
            remaining = money(remaining - portion)
        if remaining > ZERO:
            raise ConflictError("The reserved stock is not on the shelf to be staged.")
        operation.finish(f"Assembly for {space.order.number}")
        line.quantity_assembled = money(line.quantity_assembled + quantity)
        self._db.flush()
        return self.workspace(actor, order_id)

    def dispatch(
        self,
        actor: AnyActor,
        order_id: uuid.UUID,
        line_id: uuid.UUID,
        quantity: Decimal,
        *,
        serials: list[str] | None = None,
        override_reason: str | None = None,
    ) -> Workspace:
        """Issue staged stock to the customer against the line's reservation: Dr 50000."""
        actor.require("assembly.manage")
        space = self.workspace(actor, order_id)
        line, view, product = self._line(space, line_id)
        quantity = _quantity(quantity)
        if quantity > view.staged:
            raise ConflictError(
                f"Line {view.position}: {view.staged.normalize()} is staged for dispatch."
            )
        override = (override_reason or "").strip() or None
        if override:
            actor.require("expiry.override")
        bay = self._inventory.engine.location_of_kind(space.warehouse.id, LocationKind.ASSEMBLY)
        operation = self._inventory.engine.begin(
            actor,
            MovementType.ISSUE,
            occurred_on=self._inventory.today(actor),
            source_type="sales_order",
            source_id=space.order.id,
            reference=space.order.number,
        )
        reservations = self._inventory.reservations_for(line.id)
        remaining = quantity
        for reservation in reservations:
            if remaining <= ZERO:
                break
            portion = min(open_quantity(reservation), remaining)
            operation.release(
                product,
                space.warehouse.id,
                portion,
                batch_id=reservation.batch_fk,
                movement_type=MovementType.ISSUE,
            )
            reservation.quantity_consumed = money(reservation.quantity_consumed + portion)
            if open_quantity(reservation) <= ZERO:
                reservation.state = ReservationState.CONSUMED.value
            remaining = money(remaining - portion)
        if remaining > ZERO:
            raise ConflictError("The line's reservation does not cover the dispatch.")
        if serials:
            operation.take(
                product,
                space.warehouse.id,
                StockState.USABLE,
                quantity,
                location_id=bay.id,
                serials=serials,
                exclude_expired=True,
                override_reason=override,
                counter=COST_OF_GOODS_SOLD,
                movement_type=MovementType.ISSUE,
            )
        else:
            remaining = quantity
            # Each part names the batch it leaves from, earliest expiry first.
            for balance in self._balances(product, space.warehouse.id, only=bay.id):
                if remaining <= ZERO:
                    break
                portion = min(balance.quantity, remaining)
                operation.take(
                    product,
                    space.warehouse.id,
                    StockState.USABLE,
                    portion,
                    location_id=bay.id,
                    bin_id=balance.bin_fk,
                    batch_id=balance.batch_fk,
                    exclude_expired=True,
                    override_reason=override,
                    counter=COST_OF_GOODS_SOLD,
                    movement_type=MovementType.ISSUE,
                )
                remaining = money(remaining - portion)
            if remaining > ZERO:
                raise ConflictError("Less is at the assembly bay than was to be dispatched.")
        operation.finish(f"Dispatch for {space.order.number}")
        line.quantity_dispatched = money(line.quantity_dispatched + quantity)
        self._db.flush()
        self._audit.record(
            actor,
            "order_line_dispatched",
            entity_type="sales_order",
            entity_id=space.order.id,
            entity_label=space.order.number,
            reason=override,
            after={"line": view.position, "quantity": str(quantity)},
        )
        return self.workspace(actor, order_id)

    # -- helpers ----------------------------------------------------------------------

    def _line(
        self, space: Workspace, line_id: uuid.UUID
    ) -> tuple[SalesOrderLine, AssemblyLine, Product]:
        view = next((item for item in space.lines if item.line_id == line_id), None)
        line = next((item for item in space.order.lines if item.id == line_id), None)
        if view is None or line is None:
            raise NotFoundError("That line is not on this order.")
        if not view.holds_stock or view.product_id is None:
            raise ValidationError(
                f"Line {view.position} is not a stock item; it is procured and delivered "
                "without assembly."
            )
        product = self._db.get(Product, view.product_id)
        assert product is not None
        return line, view, product

    def _balances(
        self,
        product: Product,
        warehouse_id: uuid.UUID,
        *,
        exclude: uuid.UUID | None = None,
        only: uuid.UUID | None = None,
    ) -> list[StockBalance]:
        statement = (
            select(StockBalance)
            .outerjoin(Batch, Batch.id == StockBalance.batch_fk)
            .where(
                StockBalance.product_fk == product.id,
                StockBalance.warehouse_fk == warehouse_id,
                StockBalance.state == StockState.USABLE.value,
                StockBalance.quantity > 0,
            )
        )
        if exclude is not None:
            statement = statement.where(
                (StockBalance.location_fk.is_(None)) | (StockBalance.location_fk != exclude)
            )
        if only is not None:
            statement = statement.where(StockBalance.location_fk == only)
        return list(
            self._db.scalars(
                statement.order_by(Batch.expires_on.is_(None), Batch.expires_on, StockBalance.id)
            ).all()
        )

    def _incoming(self, order: SalesOrder) -> dict[uuid.UUID, Decimal]:
        statement = (
            select(PurchaseOrderLine)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.order_fk)
            .where(
                PurchaseOrderLine.sales_order_line_fk.in_([line.id for line in order.lines]),
                PurchaseOrder.state.in_([state.value for state in RECEIVABLE_STATES]),
            )
        )
        totals: dict[uuid.UUID, Decimal] = {}
        for line in self._db.scalars(statement).all():
            if line.sales_order_line_fk is None:
                continue
            outstanding = money(max(ZERO, line.quantity_ordered - line.quantity_received))
            totals[line.sales_order_line_fk] = money(
                totals.get(line.sales_order_line_fk, ZERO) + outstanding
            )
        return totals


def _quantity(value: Decimal) -> Decimal:
    quantity = money(value)
    if quantity <= ZERO:
        raise ValidationError("A quantity is above zero.", field="quantity")
    return quantity
