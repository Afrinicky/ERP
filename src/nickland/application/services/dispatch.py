"""Shipments: prepare, pack, dispatch, confirm, and account for what went wrong. PRD §6.9.

Every quantity that leaves, arrives, comes back or is lost moves through the stock engine,
so the delivery's cost and every exception's consequence are posted as they happen. The
documents a shipment carries are issued documents, numbered, stored, and reprinted from
their bytes; the proof of delivery is the recipient's acknowledgement of the waybill.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.assembly import AssemblyService
from nickland.application.services.document_sources import DocumentSourceService
from nickland.application.services.documents import DocumentRecordService
from nickland.application.services.inventory import InventoryService, open_quantity
from nickland.application.services.orders import SalesOrderService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.stock import Operation, holds_stock
from nickland.domain.catalogue import TrackingPolicy
from nickland.domain.dispatch import (
    DispatchDetail,
    DispatchMode,
    ExceptionKind,
    LineOutcome,
    ShipmentState,
    assert_shipment_transition,
    state_after_delivery,
)
from nickland.domain.documents import (
    Delivery,
    DocumentContext,
    DocumentKind,
    Line,
    Totals,
)
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.inventory import (
    COST_OF_GOODS_SOLD,
    WRITE_OFF,
    LocationKind,
    MovementType,
    ReservationState,
    StockState,
)
from nickland.domain.money import ZERO, money, plain
from nickland.persistence.catalogue_models import Product, UnitOfMeasure
from nickland.persistence.dispatch_models import DeliveryException, Shipment, ShipmentLine
from nickland.persistence.document_models import DocumentAcknowledgement, IssuedDocument
from nickland.persistence.inventory_models import (
    Batch,
    CostLayer,
    StockBalance,
    StockMovement,
    StockSerial,
)
from nickland.persistence.models import Business
from nickland.persistence.order_models import SalesOrder, SalesOrderLine
from nickland.platform.clock import Clock, SystemClock

DOCUMENT_FIELD = {
    DocumentKind.PICK_LIST: "pick_list_fk",
    DocumentKind.PACKING_LIST: "packing_list_fk",
    DocumentKind.WAYBILL: "waybill_fk",
    DocumentKind.DELIVERY_NOTE: "delivery_note_fk",
}
"""The documents a shipment carries, and the column that remembers each."""

ISSUABLE_FROM = {
    DocumentKind.PICK_LIST: frozenset(
        {
            ShipmentState.PREPARED,
            ShipmentState.PACKED,
            ShipmentState.DISPATCHED,
            ShipmentState.DELIVERED,
            ShipmentState.PARTIALLY_DELIVERED,
            ShipmentState.FAILED,
        }
    ),
    DocumentKind.PACKING_LIST: frozenset(
        {
            ShipmentState.PACKED,
            ShipmentState.DISPATCHED,
            ShipmentState.DELIVERED,
            ShipmentState.PARTIALLY_DELIVERED,
            ShipmentState.FAILED,
        }
    ),
}
"""A waybill and a delivery note describe goods that have left, so they follow dispatch."""

OPEN_STATES = frozenset({ShipmentState.PREPARED.value, ShipmentState.PACKED.value})


@dataclass(slots=True)
class ShipmentLineDraft:
    sales_order_line_id: uuid.UUID
    quantity: Decimal
    serials: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PrepareDraft:
    sales_order_id: uuid.UUID
    lines: list[ShipmentLineDraft]
    destination: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class DispatchDraft:
    mode: DispatchMode | str
    package_count: int
    carrier: str | None = None
    driver: str | None = None
    vehicle: str | None = None
    destination: str | None = None
    tracking_reference: str | None = None
    override_reason: str | None = None


@dataclass(slots=True)
class OutcomeDraft:
    shipment_line_id: uuid.UUID
    delivered: Decimal
    returned: Decimal = ZERO
    damaged: Decimal = ZERO
    lost: Decimal = ZERO
    reason: str | None = None
    serials_delivered: list[str] = field(default_factory=list)
    remainder_as: ExceptionKind | str | None = None
    """Account for everything not delivered under one outcome, worked out here rather
    than by whoever is entering it: ``returned``, ``damaged`` or ``lost``."""


@dataclass(slots=True)
class DeliveryDraft:
    outcomes: list[OutcomeDraft]
    recipient_name: str | None = None
    recipient_role: str | None = None
    location: str | None = None
    comments: str | None = None
    evidence_kind: str = "signature"
    evidence: bytes | None = None
    media_type: str | None = None


class DispatchService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._inventory = InventoryService(session, clock=self._clock)
        self._assembly = AssemblyService(session, clock=self._clock)
        self._orders = SalesOrderService(session, clock=self._clock)
        self._records = DocumentRecordService(session, clock=self._clock)

    @property
    def session(self) -> DbSession:
        return self._db

    @property
    def clock(self) -> Clock:
        return self._clock

    # -- preparing ----------------------------------------------------------------------

    def prepare(self, actor: AnyActor, draft: PrepareDraft) -> Shipment:
        """Choose what goes on this consignment, from what is reserved for the order."""
        actor.require("dispatch.manage")
        order = self._orders.require(actor, draft.sales_order_id)
        if order.cancelled_at is not None:
            raise ConflictError(f"Order {order.number} is cancelled.")
        lines = [item for item in draft.lines if item.quantity > ZERO]
        if not lines:
            raise ValidationError("A shipment carries at least one line.", field="lines")
        warehouse = self._inventory.default_warehouse(order.business_id, order.branch_id)
        count = self._db.scalar(
            select(func.count(Shipment.id)).where(Shipment.sales_order_fk == order.id)
        )
        shipment = Shipment(
            business_id=order.business_id,
            branch_id=actor.branch_id,
            number=f"{order.number}/{(count or 0) + 1}",
            sales_order_fk=order.id,
            customer_fk=order.customer_fk,
            warehouse_fk=warehouse.id,
            state=ShipmentState.PREPARED.value,
            destination=(draft.destination or "").strip() or None,
            prepared_on=self._inventory.today(actor),
            notes=(draft.notes or "").strip() or None,
            prepared_by_id=actor.user_id,
        )
        for position, item in enumerate(lines, start=1):
            line = self._order_line(order, item.sales_order_line_id)
            product = self._db.get(Product, line.product_fk) if line.product_fk else None
            if not holds_stock(product):
                raise ValidationError(
                    f"Line {line.position} is not a stock item and is not shipped from the "
                    "warehouse."
                )
            assert product is not None
            free = self.shippable(line)
            quantity = money(item.quantity)
            if quantity > free:
                raise ConflictError(
                    f"Line {line.position}: {plain(free)} is reserved and not already on "
                    f"another shipment; {plain(quantity)} was asked for.",
                    shippable=str(free),
                )
            serials = [value.strip() for value in item.serials if value.strip()]
            if TrackingPolicy(product.tracking_policy) is TrackingPolicy.SERIAL and len(
                serials
            ) != int(quantity):
                raise ValidationError(
                    f"Line {line.position}: name the serial number of each unit shipped.",
                    field="serials",
                )
            shipment.lines.append(
                ShipmentLine(
                    business_id=order.business_id,
                    position=position,
                    sales_order_line_fk=line.id,
                    product_fk=product.id,
                    quantity=quantity,
                    serials="\n".join(serials) or None,
                )
            )
        self._db.add(shipment)
        self._db.flush()
        self._audit.record(
            actor,
            "shipment_prepared",
            entity_type="shipment",
            entity_id=shipment.id,
            entity_label=shipment.number,
            after={"lines": len(shipment.lines)},
        )
        return shipment

    def shippable(self, line: SalesOrderLine) -> Decimal:
        """Reserved for the line and not already on a shipment that has yet to leave."""
        held = self._inventory.reserved_for(line.id)
        pending = self._db.scalar(
            select(func.coalesce(func.sum(ShipmentLine.quantity), 0))
            .join(Shipment, Shipment.id == ShipmentLine.shipment_fk)
            .where(
                ShipmentLine.sales_order_line_fk == line.id,
                Shipment.state.in_(OPEN_STATES),
            )
        )
        return money(max(ZERO, held - money(Decimal(str(pending or 0)))))

    def pack(self, actor: AnyActor, shipment_id: uuid.UUID, *, package_count: int) -> Shipment:
        """Stage at the assembly bay whatever is not there yet, and count the packages."""
        actor.require("dispatch.manage")
        shipment = self.require(actor, shipment_id)
        assert_shipment_transition(ShipmentState(shipment.state), ShipmentState.PACKED)
        if package_count < 1:
            raise ValidationError("A consignment has at least one package.", field="package_count")
        space = self._assembly.workspace(actor, shipment.sales_order_fk)
        for line in shipment.lines:
            view = next(item for item in space.lines if item.line_id == line.sales_order_line_fk)
            serials = (line.serials or "").splitlines()
            if serials:
                # Stage exactly the units named for the truck that are not at the bay yet.
                bay = self._inventory.engine.location_of_kind(
                    shipment.warehouse_fk, LocationKind.ASSEMBLY
                )
                away = [
                    number
                    for number in serials
                    if (unit := self._serial(line.product_fk, number)) is None
                    or unit.location_fk != bay.id
                ]
                if away:
                    space = self._assembly.assemble(
                        actor,
                        shipment.sales_order_fk,
                        line.sales_order_line_fk,
                        Decimal(len(away)),
                        serials=away,
                    )
                continue
            short = money(line.quantity - view.staged)
            if short > ZERO:
                space = self._assembly.assemble(
                    actor, shipment.sales_order_fk, line.sales_order_line_fk, short
                )
        shipment.package_count = package_count
        shipment.packed_on = self._inventory.today(actor)
        shipment.state = ShipmentState.PACKED.value
        self._db.flush()
        return shipment

    def cancel(self, actor: AnyActor, shipment_id: uuid.UUID, *, reason: str) -> Shipment:
        """Withdraw a shipment that has not left. Staged stock stays staged for the order."""
        actor.require("dispatch.manage")
        shipment = self.require(actor, shipment_id)
        assert_shipment_transition(ShipmentState(shipment.state), ShipmentState.CANCELLED)
        if not reason.strip():
            raise ValidationError("A cancellation records why.", field="reason")
        shipment.state = ShipmentState.CANCELLED.value
        shipment.cancellation_reason = reason.strip()
        self._db.flush()
        self._audit.record(
            actor,
            "shipment_cancelled",
            entity_type="shipment",
            entity_id=shipment.id,
            entity_label=shipment.number,
            reason=reason.strip(),
        )
        return shipment

    # -- dispatching --------------------------------------------------------------------

    def dispatch(self, actor: AnyActor, shipment_id: uuid.UUID, draft: DispatchDraft) -> Shipment:
        """The goods leave: usable at the bay into transit, Dr 12200, Cr 12000."""
        actor.require("dispatch.manage")
        shipment = self.require(actor, shipment_id)
        assert_shipment_transition(ShipmentState(shipment.state), ShipmentState.DISPATCHED)
        detail = DispatchDetail(
            mode=DispatchMode(draft.mode),
            carrier=_clean(draft.carrier),
            driver=_clean(draft.driver),
            vehicle=_clean(draft.vehicle),
            destination=_clean(draft.destination) or shipment.destination,
            package_count=draft.package_count or shipment.package_count,
            tracking_reference=_clean(draft.tracking_reference),
        )
        detail.validate()
        override = _clean(draft.override_reason)
        if override:
            actor.require("expiry.override")

        engine = self._inventory.engine
        bay = engine.location_of_kind(shipment.warehouse_fk, LocationKind.ASSEMBLY)
        despatched = engine.location_of_kind(shipment.warehouse_fk, LocationKind.DISPATCH)
        operation = engine.begin(
            actor,
            MovementType.DISPATCH,
            occurred_on=self._inventory.today(actor),
            source_type="shipment",
            source_id=shipment.id,
            reference=shipment.number,
        )
        for line in shipment.lines:
            order_line = self._db.get(SalesOrderLine, line.sales_order_line_fk)
            product = self._db.get(Product, line.product_fk)
            assert order_line is not None and product is not None
            self._consume_reservation(operation, product, shipment.warehouse_fk, order_line, line)
            operation.move(
                product,
                line.quantity,
                from_warehouse=shipment.warehouse_fk,
                from_state=StockState.USABLE,
                from_location=bay.id,
                to_warehouse=shipment.warehouse_fk,
                to_state=StockState.IN_TRANSIT,
                to_location=despatched.id,
                serials=(line.serials or "").splitlines(),
                exclude_expired=True,
                override_reason=override,
                movement_type=MovementType.DISPATCH,
            )
            order_line.quantity_dispatched = money(order_line.quantity_dispatched + line.quantity)
        operation.finish(f"Dispatch {shipment.number}")

        shipment.mode = detail.mode.value
        shipment.carrier = detail.carrier
        shipment.driver = detail.driver
        shipment.vehicle = detail.vehicle
        shipment.destination = detail.destination
        shipment.package_count = detail.package_count
        shipment.tracking_reference = detail.tracking_reference
        shipment.dispatched_on = operation.occurred_on
        shipment.dispatched_by_id = actor.user_id
        shipment.dispatch_operation_id = operation.id
        shipment.state = ShipmentState.DISPATCHED.value
        self._db.flush()
        self._audit.record(
            actor,
            "shipment_dispatched",
            entity_type="shipment",
            entity_id=shipment.id,
            entity_label=shipment.number,
            reason=override,
            after={
                "mode": detail.mode.value,
                "carrier": detail.carrier,
                "packages": detail.package_count,
            },
        )
        return shipment

    def _consume_reservation(
        self,
        operation: Operation,
        product: Product,
        warehouse_id: uuid.UUID,
        order_line: SalesOrderLine,
        line: ShipmentLine,
    ) -> None:
        remaining = line.quantity
        for reservation in self._inventory.reservations_for(order_line.id):
            if remaining <= ZERO:
                break
            portion = min(open_quantity(reservation), remaining)
            operation.release(
                product,
                warehouse_id,
                portion,
                batch_id=reservation.batch_fk,
                movement_type=MovementType.DISPATCH,
            )
            reservation.quantity_consumed = money(reservation.quantity_consumed + portion)
            if open_quantity(reservation) <= ZERO:
                reservation.state = ReservationState.CONSUMED.value
            remaining = money(remaining - portion)
        if remaining > ZERO:
            raise ConflictError(
                f"{product.sku}: the order's reservation no longer covers this shipment."
            )

    # -- delivery -----------------------------------------------------------------------

    def confirm_delivery(
        self, actor: AnyActor, shipment_id: uuid.UUID, draft: DeliveryDraft
    ) -> Shipment:
        """Close the shipment on what was signed for, and account for every other unit.

        Delivered: Dr 50000, Cr 12200. Returned to the store: back to the bay and held for
        the line again. Returned damaged: to quarantine. Lost: Dr 51000. The recipient's
        acknowledgement of the waybill is the proof of delivery.
        """
        actor.require("proof_of_delivery.capture")
        shipment = self.require(actor, shipment_id)
        if ShipmentState(shipment.state) is not ShipmentState.DISPATCHED:
            raise ConflictError(
                f"Shipment {shipment.number} is {ShipmentState(shipment.state).label.lower()}; "
                "a delivery is confirmed for goods in transit."
            )
        by_line = {item.shipment_line_id: item for item in draft.outcomes}
        unknown = set(by_line) - {line.id for line in shipment.lines}
        if unknown:
            raise NotFoundError("An outcome names a line that is not on this shipment.")
        outcomes: list[tuple[ShipmentLine, OutcomeDraft, LineOutcome]] = []
        for line in shipment.lines:
            item = by_line.get(line.id)
            if item is None:
                raise ValidationError(
                    f"Line {line.position}: record what became of it.", field="outcomes"
                )
            outcome = _outcome_of(line, item)
            outcome.validate(position=line.position)
            if outcome.short > ZERO and not _clean(item.reason):
                raise ValidationError(
                    f"Line {line.position}: a short delivery records why.", field="reason"
                )
            outcomes.append((line, item, outcome))
        state = state_after_delivery([outcome for _, _, outcome in outcomes])
        recipient = _clean(draft.recipient_name)
        if state is not ShipmentState.FAILED and not recipient:
            raise ValidationError(
                "A delivery is confirmed with the name of the person who received it.",
                field="recipient_name",
            )

        if recipient and shipment.waybill_fk is None:
            raise ConflictError(
                "The waybill is issued before a delivery is confirmed against it: the "
                "recipient signs the copy that travelled with the goods."
            )

        engine = self._inventory.engine
        warehouse_id = shipment.warehouse_fk
        bay = engine.location_of_kind(warehouse_id, LocationKind.ASSEMBLY)
        despatched = engine.location_of_kind(warehouse_id, LocationKind.DISPATCH)
        quarantine = engine.location_of_kind(warehouse_id, LocationKind.QUARANTINE)
        own_layers = self._dispatch_layers(shipment)
        today = self._inventory.today(actor)
        operation = engine.begin(
            actor,
            MovementType.DELIVERY,
            occurred_on=today,
            source_type="shipment",
            source_id=shipment.id,
            reference=shipment.number,
        )
        for line, item, outcome in outcomes:
            product = self._db.get(Product, line.product_fk)
            order_line = self._db.get(SalesOrderLine, line.sales_order_line_fk)
            assert product is not None and order_line is not None
            delivered_serials, other_serials = self._split_serials(product, line, item, outcome)
            if outcome.delivered > ZERO:
                operation.take(
                    product,
                    warehouse_id,
                    StockState.IN_TRANSIT,
                    outcome.delivered,
                    location_id=despatched.id,
                    serials=delivered_serials,
                    counter=COST_OF_GOODS_SOLD,
                    movement_type=MovementType.DELIVERY,
                    layer_ids=own_layers,
                )
                line.quantity_delivered = outcome.delivered
                order_line.quantity_delivered = money(
                    order_line.quantity_delivered + outcome.delivered
                )
            reason = _clean(item.reason) or "Not received"
            if outcome.returned > ZERO:
                operation.move(
                    product,
                    outcome.returned,
                    from_warehouse=warehouse_id,
                    from_state=StockState.IN_TRANSIT,
                    from_location=despatched.id,
                    to_warehouse=warehouse_id,
                    to_state=StockState.USABLE,
                    to_location=bay.id,
                    serials=other_serials,
                    movement_type=MovementType.DELIVERY_RETURN,
                    layer_ids=own_layers,
                )
                # Back at the bay and still the customer's: held for the line again.
                self._inventory.hold(operation, product, warehouse_id, outcome.returned, order_line)
                line.quantity_returned = outcome.returned
                order_line.quantity_dispatched = money(
                    order_line.quantity_dispatched - outcome.returned
                )
                self._exception(
                    shipment, line, ExceptionKind.RETURNED, outcome.returned, reason, operation
                )
            if outcome.damaged > ZERO:
                operation.move(
                    product,
                    outcome.damaged,
                    from_warehouse=warehouse_id,
                    from_state=StockState.IN_TRANSIT,
                    from_location=despatched.id,
                    to_warehouse=warehouse_id,
                    to_state=StockState.QUARANTINE,
                    to_location=quarantine.id,
                    serials=other_serials,
                    movement_type=MovementType.DELIVERY_RETURN,
                    layer_ids=own_layers,
                )
                line.quantity_damaged = outcome.damaged
                self._leave_bay(order_line, outcome.damaged)
                self._exception(
                    shipment, line, ExceptionKind.DAMAGED, outcome.damaged, reason, operation
                )
            if outcome.lost > ZERO:
                operation.take(
                    product,
                    warehouse_id,
                    StockState.IN_TRANSIT,
                    outcome.lost,
                    location_id=despatched.id,
                    serials=other_serials,
                    counter=WRITE_OFF,
                    movement_type=MovementType.TRANSIT_LOSS,
                    layer_ids=own_layers,
                )
                line.quantity_lost = outcome.lost
                self._leave_bay(order_line, outcome.lost)
                self._exception(shipment, line, ExceptionKind.LOST, outcome.lost, reason, operation)
        operation.finish(f"Delivery of {shipment.number}")

        if recipient:
            if shipment.waybill_fk is None:
                raise ConflictError(
                    "The waybill is issued before a delivery is confirmed against it: the "
                    "recipient signs the copy that travelled with the goods."
                )
            acknowledgement = self._records.acknowledge(
                actor,
                shipment.waybill_fk,
                recipient_name=recipient,
                recipient_role=draft.recipient_role,
                location=draft.location or shipment.destination,
                comments=draft.comments,
                evidence_kind=draft.evidence_kind,
                evidence=draft.evidence,
                media_type=draft.media_type,
            )
            shipment.acknowledgement_fk = acknowledgement.id
        shipment.state = state.value
        shipment.delivered_on = today
        shipment.delivery_operation_id = operation.id
        self._db.flush()
        self._audit.record(
            actor,
            "shipment_delivered",
            entity_type="shipment",
            entity_id=shipment.id,
            entity_label=shipment.number,
            after={
                "state": state.value,
                "recipient": recipient,
                "exceptions": len(shipment.exceptions),
            },
        )
        return shipment

    def customer_return(
        self,
        actor: AnyActor,
        shipment_id: uuid.UUID,
        line_id: uuid.UUID,
        *,
        quantity: Decimal,
        reason: str,
        replacement_owed: bool = True,
        serials: list[str] | None = None,
    ) -> Shipment:
        """Goods the customer received and sent back: to quarantine at the cost they were
        delivered at, Dr 12100, Cr 50000. Either a replacement is owed, or the quantity is
        cancelled from the order."""
        actor.require("sales_return.manage")
        shipment = self.require(actor, shipment_id)
        line = next((item for item in shipment.lines if item.id == line_id), None)
        if line is None:
            raise NotFoundError("That line is not on this shipment.")
        quantity = money(quantity)
        returnable = money(line.quantity_delivered - line.quantity_customer_returned)
        if quantity <= ZERO or quantity > returnable:
            raise ConflictError(
                f"Line {line.position}: {plain(returnable)} delivered remains returnable."
            )
        cleaned = _clean(reason)
        if not cleaned:
            raise ValidationError("A return records why.", field="reason")
        product = self._db.get(Product, line.product_fk)
        order_line = self._db.get(SalesOrderLine, line.sales_order_line_fk)
        assert product is not None and order_line is not None
        value = self._delivered_value(shipment, line, quantity)
        delivered_batch = self._delivered_batch(shipment, line)
        engine = self._inventory.engine
        quarantine = engine.location_of_kind(shipment.warehouse_fk, LocationKind.QUARANTINE)
        today = self._inventory.today(actor)
        operation = engine.begin(
            actor,
            MovementType.CUSTOMER_RETURN,
            occurred_on=today,
            source_type="shipment",
            source_id=shipment.id,
            reference=shipment.number,
            reason=cleaned,
        )
        operation.add(
            product,
            shipment.warehouse_fk,
            StockState.QUARANTINE,
            quantity,
            value,
            location_id=quarantine.id,
            batch=delivered_batch,
            serials=serials or [],
            counter=COST_OF_GOODS_SOLD,
            movement_type=MovementType.CUSTOMER_RETURN,
        )
        operation.finish(f"Return against {shipment.number}: {cleaned}")
        line.quantity_customer_returned = money(line.quantity_customer_returned + quantity)
        order_line.quantity_delivered = money(order_line.quantity_delivered - quantity)
        self._leave_bay(order_line, quantity)
        if not replacement_owed:
            order_line.quantity_cancelled = money(order_line.quantity_cancelled + quantity)
        self._exception(
            shipment,
            line,
            ExceptionKind.CUSTOMER_RETURN,
            quantity,
            cleaned,
            operation,
            replacement_owed=replacement_owed,
        )
        self._db.flush()
        self._audit.record(
            actor,
            "shipment_returned_by_customer",
            entity_type="shipment",
            entity_id=shipment.id,
            entity_label=shipment.number,
            reason=cleaned,
            after={"quantity": str(quantity), "replacement_owed": replacement_owed},
        )
        return shipment

    # -- documents ----------------------------------------------------------------------

    def document_for(
        self, actor: AnyActor, shipment_id: uuid.UUID, kind: DocumentKind
    ) -> tuple[Shipment, IssuedDocument | None, DocumentContext | None]:
        """What the documents layer needs to issue one of the shipment's documents.

        Returns the copy already issued, if there is one, since each is issued once; or
        else the context to render, numbered from the type's own sequence.
        """
        actor.require("waybill.issue")
        shipment = self.require(actor, shipment_id)
        if kind not in DOCUMENT_FIELD:
            raise ValidationError(f"A shipment does not carry a {kind.heading.lower()}.")
        existing_id = getattr(shipment, DOCUMENT_FIELD[kind])
        if existing_id is not None:
            return shipment, self._db.get(IssuedDocument, existing_id), None
        state = ShipmentState(shipment.state)
        if state is ShipmentState.CANCELLED:
            raise ConflictError("A cancelled shipment carries no documents.")
        allowed = ISSUABLE_FROM.get(kind)
        if allowed is not None and state not in allowed:
            raise ConflictError(
                f"A {kind.heading.lower()} is issued once the shipment is "
                + ("prepared." if kind is DocumentKind.PICK_LIST else "packed.")
            )
        if allowed is None and shipment.dispatch_operation_id is None:
            raise ConflictError(f"A {kind.heading.lower()} is issued once the goods have left.")

        self._records.seed(shipment.business_id)
        document_type = self._records.type_by_code(shipment.business_id, kind.value)
        if document_type is None:
            raise NotFoundError(f"No {kind.heading.lower()} document type is set up.")
        number = (
            OrganisationService(self._db, clock=self._clock)
            .allocate_number(
                actor, document_type.sequence_kind or document_type.code, branch_id=actor.branch_id
            )
            .number
        )
        return shipment, None, self.context(actor, shipment, kind, number=number)

    def record_document(
        self, shipment: Shipment, kind: DocumentKind, issued: IssuedDocument
    ) -> None:
        setattr(shipment, DOCUMENT_FIELD[kind], issued.id)
        self._db.flush()

    def context(
        self, actor: AnyActor, shipment: Shipment, kind: DocumentKind, *, number: str
    ) -> DocumentContext:
        order = self._db.get(SalesOrder, shipment.sales_order_fk)
        business = self._db.get(Business, shipment.business_id)
        assert order is not None
        sources = DocumentSourceService(self._db, clock=self._clock)
        customer = sources.party_block(shipment.customer_fk)
        ship_to = customer
        if shipment.destination:
            ship_to = type(customer)(
                name=customer.name,
                reference=customer.reference,
                address_lines=tuple(
                    part.strip() for part in shipment.destination.splitlines() if part.strip()
                ),
                telephone=customer.telephone,
            )
        lines = self._document_lines(shipment, kind)
        acknowledgement = (
            self._db.get(DocumentAcknowledgement, shipment.acknowledgement_fk)
            if shipment.acknowledgement_fk
            else None
        )
        return DocumentContext(
            kind=kind,
            number=number,
            issued_on=shipment.dispatched_on or self._inventory.today(actor),
            issuer=self._records.issuer_for(shipment.business_id),
            currency_code=order.currency_code,
            currency_symbol=business.currency_symbol if business else "GH₵",
            currency_precision=business.currency_precision if business else 2,
            bill_to=customer,
            ship_to=ship_to,
            order_reference=order.number,
            customer_reference=order.customer_reference,
            our_reference=shipment.number,
            prepared_by=actor.full_name,
            lines=lines,
            totals=Totals(total_units=money(sum((line.quantity for line in lines), ZERO))),
            delivery=Delivery(
                mode=DispatchMode(shipment.mode).label if shipment.dispatched_on else None,
                carrier=shipment.carrier,
                driver=shipment.driver,
                vehicle=shipment.vehicle,
                dispatched_on=shipment.dispatched_on,
                package_count=shipment.package_count or None,
                tracking_reference=shipment.tracking_reference,
                recipient=acknowledgement.recipient_name if acknowledgement else None,
            ),
            notes=[part for part in (shipment.notes,) if part],
        )

    def _document_lines(self, shipment: Shipment, kind: DocumentKind) -> list[Line]:
        """Per batch: what left, from the dispatch; or, before it, where to pick from."""
        rows: list[Line] = []
        for line in shipment.lines:
            product = self._db.get(Product, line.product_fk)
            order_line = self._db.get(SalesOrderLine, line.sales_order_line_fk)
            assert product is not None and order_line is not None
            unit = self._db.get(UnitOfMeasure, product.stock_unit_fk)
            parts = (
                self._dispatched_parts(shipment, line)
                if shipment.dispatch_operation_id
                else self._pick_parts(shipment, line)
            )
            for batch_id, location, quantity in parts:
                batch = self._db.get(Batch, batch_id) if batch_id else None
                note = (
                    f"Pick from {location}" if kind is DocumentKind.PICK_LIST and location else None
                )
                if line.serials and kind is not DocumentKind.PICK_LIST:
                    note = "Serial " + ", ".join(line.serials.splitlines())
                rows.append(
                    Line(
                        sku=product.sku,
                        name=order_line.description,
                        quantity=quantity,
                        unit=unit.code if unit is not None else None,
                        unit_price=order_line.unit_price,
                        line_total=money(order_line.unit_price * quantity),
                        batch=batch.code if batch else None,
                        expiry=batch.expires_on if batch else None,
                        manufacturer=(batch.manufacturer if batch else None)
                        or product.manufacturer,
                        note=note,
                    )
                )
        return rows

    def _dispatched_parts(
        self, shipment: Shipment, line: ShipmentLine
    ) -> list[tuple[uuid.UUID | None, str | None, Decimal]]:
        totals: dict[uuid.UUID | None, Decimal] = {}
        for movement in self._db.scalars(
            select(StockMovement).where(
                StockMovement.operation_id == shipment.dispatch_operation_id,
                StockMovement.product_fk == line.product_fk,
                StockMovement.state == StockState.IN_TRANSIT.value,
                StockMovement.quantity > 0,
            )
        ).all():
            totals[movement.batch_fk] = money(
                totals.get(movement.batch_fk, ZERO) + movement.quantity
            )
        if sum(1 for item in shipment.lines if item.product_fk == line.product_fk) > 1:
            # Two lines of one product on one shipment: the split by batch belongs to
            # the shipment, so each line shows its own quantity unsplit.
            return [(None, None, line.quantity)]
        return [(batch, None, quantity) for batch, quantity in totals.items()] or [
            (None, None, line.quantity)
        ]

    def _pick_parts(
        self, shipment: Shipment, line: ShipmentLine
    ) -> list[tuple[uuid.UUID | None, str | None, Decimal]]:
        """Where the stock for the line is: the bay first, then storage, earliest expiry."""
        engine = self._inventory.engine
        bay = engine.location_of_kind(shipment.warehouse_fk, LocationKind.ASSEMBLY)
        balances = list(
            self._db.scalars(
                select(StockBalance)
                .outerjoin(Batch, Batch.id == StockBalance.batch_fk)
                .where(
                    StockBalance.product_fk == line.product_fk,
                    StockBalance.warehouse_fk == shipment.warehouse_fk,
                    StockBalance.state == StockState.USABLE.value,
                    StockBalance.quantity > 0,
                )
                .order_by(
                    StockBalance.location_fk != bay.id,
                    Batch.expires_on.is_(None),
                    Batch.expires_on,
                    StockBalance.id,
                )
            ).all()
        )
        parts: list[tuple[uuid.UUID | None, str | None, Decimal]] = []
        remaining = line.quantity
        places = {
            location.id: location.name
            for location in engine.locations(engine.warehouse(shipment.warehouse_fk)).values()
        }
        for balance in balances:
            if remaining <= ZERO:
                break
            portion = min(balance.quantity, remaining)
            place = places.get(balance.location_fk) if balance.location_fk else None
            parts.append((balance.batch_fk, place, portion))
            remaining = money(remaining - portion)
        if remaining > ZERO:
            parts.append((None, None, remaining))
        return parts

    # -- reading ------------------------------------------------------------------------

    def require(self, actor: AnyActor, shipment_id: uuid.UUID) -> Shipment:
        actor.require("dispatch.view")
        shipment = self._db.get(Shipment, shipment_id)
        if shipment is None or shipment.business_id != actor.business_id:
            raise NotFoundError("Shipment not found.")
        return shipment

    def shipments(
        self,
        actor: AnyActor,
        *,
        order_id: uuid.UUID | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[Shipment]:
        actor.require("dispatch.view")
        query = select(Shipment).where(Shipment.business_id == actor.business_id)
        if order_id is not None:
            query = query.where(Shipment.sales_order_fk == order_id)
        if state:
            query = query.where(Shipment.state == state)
        return list(
            self._db.scalars(
                query.order_by(
                    Shipment.created_at.desc(), Shipment.prepared_on.desc(), Shipment.number.desc()
                ).limit(max(min(limit, 200), 1))
            ).all()
        )

    # -- helpers ------------------------------------------------------------------------

    def _order_line(self, order: SalesOrder, line_id: uuid.UUID) -> SalesOrderLine:
        line = next((item for item in order.lines if item.id == line_id), None)
        if line is None:
            raise NotFoundError("That line is not on this order.")
        return line

    def _serial(self, product_id: uuid.UUID, number: str) -> StockSerial | None:
        return self._db.scalar(
            select(StockSerial).where(
                StockSerial.product_fk == product_id, StockSerial.serial_number == number
            )
        )

    def _dispatch_layers(self, shipment: Shipment) -> frozenset[uuid.UUID]:
        movement_ids = select(StockMovement.id).where(
            StockMovement.operation_id == shipment.dispatch_operation_id
        )
        return frozenset(
            self._db.scalars(
                select(CostLayer.id).where(CostLayer.movement_fk.in_(movement_ids))
            ).all()
        )

    def _split_serials(
        self, product: Product, line: ShipmentLine, item: OutcomeDraft, outcome: LineOutcome
    ) -> tuple[list[str], list[str]]:
        """For a serialised line: which units were signed for, and which were not."""
        if TrackingPolicy(product.tracking_policy) is not TrackingPolicy.SERIAL:
            return [], []
        shipped = (line.serials or "").splitlines()
        undelivered_kinds = sum(
            1 for value in (outcome.returned, outcome.damaged, outcome.lost) if value > ZERO
        )
        if undelivered_kinds > 1:
            raise ValidationError(
                f"Line {line.position}: record a serialised line's undelivered units under "
                "one outcome at a time."
            )
        if outcome.delivered == len(shipped):
            return shipped, []
        delivered = [value.strip() for value in item.serials_delivered if value.strip()]
        if outcome.delivered and len(delivered) != int(outcome.delivered):
            raise ValidationError(
                f"Line {line.position}: name the serial number of each unit delivered.",
                field="serials_delivered",
            )
        if set(delivered) - set(shipped):
            raise ValidationError(f"Line {line.position}: a serial named was not on the truck.")
        return delivered, [value for value in shipped if value not in delivered]

    def _leave_bay(self, order_line: SalesOrderLine, quantity: Decimal) -> None:
        """A unit that will not be staged again: neither dispatched nor assembled now."""
        order_line.quantity_dispatched = money(max(ZERO, order_line.quantity_dispatched - quantity))
        order_line.quantity_assembled = money(max(ZERO, order_line.quantity_assembled - quantity))

    def _exception(
        self,
        shipment: Shipment,
        line: ShipmentLine,
        kind: ExceptionKind,
        quantity: Decimal,
        reason: str,
        operation: Operation,
        *,
        replacement_owed: bool = True,
    ) -> DeliveryException:
        """Recorded complete, once: an exception is append-only."""
        exception = DeliveryException(
            business_id=shipment.business_id,
            shipment_fk=shipment.id,
            shipment_line_fk=line.id,
            kind=kind.value,
            quantity=quantity,
            reason=reason,
            replacement_owed=replacement_owed,
            occurred_on=operation.occurred_on,
            operation_id=operation.id,
            recorded_by_id=operation.actor.user_id,
        )
        shipment.exceptions.append(exception)
        self._db.add(exception)
        return exception

    def _delivery_movements(self, shipment: Shipment, line: ShipmentLine) -> list[StockMovement]:
        return list(
            self._db.scalars(
                select(StockMovement).where(
                    StockMovement.operation_id == shipment.delivery_operation_id,
                    StockMovement.product_fk == line.product_fk,
                    StockMovement.movement_type == MovementType.DELIVERY.value,
                    StockMovement.state == StockState.IN_TRANSIT.value,
                )
            ).all()
        )

    def _delivered_value(
        self, shipment: Shipment, line: ShipmentLine, quantity: Decimal
    ) -> Decimal:
        movements = self._delivery_movements(shipment, line)
        out_quantity = -sum((movement.quantity for movement in movements), ZERO)
        out_value = -sum((movement.value for movement in movements), ZERO)
        if out_quantity <= ZERO:
            return ZERO
        if quantity == out_quantity:
            return money(out_value)
        return money(out_value * quantity / out_quantity)

    def _delivered_batch(self, shipment: Shipment, line: ShipmentLine) -> Batch | None:
        movements = self._delivery_movements(shipment, line)
        batches = {movement.batch_fk for movement in movements} - {None}
        if len(batches) == 1:
            return self._db.get(Batch, next(iter(batches)))
        return None


def _outcome_of(line: ShipmentLine, item: OutcomeDraft) -> LineOutcome:
    delivered = money(item.delivered)
    if item.remainder_as is None:
        return LineOutcome(
            shipped=line.quantity,
            delivered=delivered,
            returned=money(item.returned),
            damaged=money(item.damaged),
            lost=money(item.lost),
        )
    kind = ExceptionKind(item.remainder_as)
    if kind is ExceptionKind.CUSTOMER_RETURN:
        raise ValidationError("A return after delivery is recorded once the goods come back.")
    if delivered > line.quantity:
        raise ValidationError(
            f"Line {line.position}: more cannot be delivered than was shipped.", field="delivered"
        )
    remainder = money(line.quantity - delivered)
    return LineOutcome(
        shipped=line.quantity,
        delivered=delivered,
        returned=remainder if kind is ExceptionKind.RETURNED else ZERO,
        damaged=remainder if kind is ExceptionKind.DAMAGED else ZERO,
        lost=remainder if kind is ExceptionKind.LOST else ZERO,
    )


def _clean(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None
