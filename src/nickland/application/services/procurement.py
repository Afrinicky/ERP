"""Purchase orders and goods receipts. PRD §6.8.

A purchase order is raised against the lines of a sales order, from the supplier the
quotation chose, or for the shelf. Several may serve one customer order. A goods
receipt records what arrived and posts the inventory accrual; the supplier's invoice,
in supplier_billing, later replaces the accrual with the supplier's own figure.
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
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.orders import SalesOrderService
from nickland.application.services.organisation import OrganisationService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.ledger import EntryLine, JournalSource, Side
from nickland.domain.money import ZERO, money, multiply
from nickland.domain.orders import (
    GOODS_IN_QUARANTINE,
    GOODS_RECEIVED_NOT_INVOICED,
    INVENTORY,
    RECEIVABLE_STATES,
    GoodsReceiptState,
    PurchaseOrderState,
    SourceKind,
    assert_purchase_order_transition,
    purchase_order_state_after_receipt,
)
from nickland.persistence.catalogue_models import Product
from nickland.persistence.models import Business, Party, Warehouse
from nickland.persistence.order_models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
)
from nickland.persistence.pricing_models import QuotationLineSource, SupplierQuotationLine
from nickland.platform.clock import Clock, SystemClock, business_date


@dataclass(slots=True)
class PurchaseLineDraft:
    """One line to buy, typed or taken from a sales order line."""

    description: str
    quantity: Decimal
    unit_cost: Decimal
    product_id: uuid.UUID | None = None
    sales_order_line_id: uuid.UUID | None = None
    supplier_quotation_line_id: uuid.UUID | None = None
    discount_rate: Decimal = ZERO
    unit_id: uuid.UUID | None = None
    expected_on: date | None = None
    notes: str | None = None


@dataclass(slots=True)
class PurchaseOrderDraft:
    supplier_id: uuid.UUID
    lines: list[PurchaseLineDraft] = field(default_factory=list)
    sales_order_id: uuid.UUID | None = None
    supplier_quotation_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    purpose: str = "order"
    currency_code: str | None = None
    exchange_rate: Decimal = Decimal("1")
    expected_on: date | None = None
    supplier_reference: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class ReceiptLineDraft:
    purchase_order_line_id: uuid.UUID
    quantity_received: Decimal
    quantity_rejected: Decimal = ZERO
    rejection_reason: str | None = None
    batch: str | None = None
    expiry: date | None = None
    serials: str | None = None
    manufacturer: str | None = None


@dataclass(slots=True)
class ReceiptDraft:
    purchase_order_id: uuid.UUID
    lines: list[ReceiptLineDraft] = field(default_factory=list)
    received_on: date | None = None
    warehouse_id: uuid.UUID | None = None
    delivery_reference: str | None = None
    inspection_pending: bool = False
    notes: str | None = None


class ProcurementService:
    """Purchase orders and goods receipts."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._orders = SalesOrderService(session, clock=self._clock)

    # -- raising ----------------------------------------------------------------------

    def suggest_from_order(
        self, actor: AnyActor, order_id: uuid.UUID
    ) -> dict[uuid.UUID, PurchaseOrderDraft]:
        """A draft per supplier, from the sources the quotation chose, for what is not
        yet procured. The buyer reviews and raises each; nothing is raised here."""
        actor.require("purchase_order.manage")
        order = self._orders.require(actor, order_id)
        drafts: dict[uuid.UUID, PurchaseOrderDraft] = {}
        for line in order.lines:
            unprocured = line.quantity_ordered - line.quantity_cancelled - line.quantity_procured
            if unprocured <= ZERO or line.source_kind == SourceKind.STOCK.value:
                continue
            # Only the supplier sources share the unprocured quantity: the stock part
            # of a split line was drawn on the day the order was converted.
            sources = [
                source
                for source in self._sources_of(line)
                if source.kind == SourceKind.SUPPLIER.value and source.supplier_fk is not None
            ]
            if not sources:
                continue
            total_sourced = sum((source.quantity for source in sources), ZERO) or Decimal("1")
            for source in sources:
                supplier_id = source.supplier_fk
                if supplier_id is None:
                    continue
                share = money(unprocured * source.quantity / total_sourced)
                if share <= ZERO:
                    continue
                draft = drafts.get(supplier_id)
                if draft is None:
                    draft = PurchaseOrderDraft(
                        supplier_id=supplier_id,
                        sales_order_id=order.id,
                        currency_code=source.currency_code,
                        exchange_rate=source.exchange_rate,
                        expected_on=order.required_by,
                    )
                    drafts[supplier_id] = draft
                draft.lines.append(
                    PurchaseLineDraft(
                        description=line.description,
                        quantity=share,
                        unit_cost=source.unit_cost,
                        product_id=line.product_fk,
                        sales_order_line_id=line.id,
                        supplier_quotation_line_id=source.supplier_quotation_line_fk,
                        unit_id=line.unit_fk,
                    )
                )
                if source.supplier_quotation_line_fk and draft.supplier_quotation_id is None:
                    offered = self._db.get(SupplierQuotationLine, source.supplier_quotation_line_fk)
                    if offered is not None:
                        draft.supplier_quotation_id = offered.supplier_quotation_fk
        return drafts

    def raise_order(self, actor: AnyActor, draft: PurchaseOrderDraft) -> PurchaseOrder:
        """A purchase order in draft. Issue allocates its number."""
        actor.require("purchase_order.manage")
        business_id = _business_of(actor)
        supplier = self._db.get(Party, draft.supplier_id)
        if supplier is None or supplier.business_id != business_id or not supplier.is_supplier:
            raise NotFoundError("Supplier not found.")
        if not draft.lines:
            raise ValidationError("A purchase order carries at least one line.", field="lines")
        if draft.purpose not in ("order", "replenishment"):
            raise ValidationError(
                "A purchase order is for an order or for replenishment.", field="purpose"
            )
        if draft.exchange_rate <= ZERO:
            raise ValidationError("The exchange rate is above zero.", field="exchange_rate")
        sales_order: SalesOrder | None = None
        if draft.sales_order_id is not None:
            sales_order = self._orders.require(actor, draft.sales_order_id)
        if draft.warehouse_id is not None:
            warehouse = self._db.get(Warehouse, draft.warehouse_id)
            if warehouse is None or warehouse.business_id != business_id:
                raise NotFoundError("Warehouse not found.")

        business = self._db.get(Business, business_id)
        order = PurchaseOrder(
            business_id=business_id,
            branch_id=actor.branch_id,
            supplier_fk=supplier.id,
            sales_order_fk=sales_order.id if sales_order else None,
            supplier_quotation_fk=draft.supplier_quotation_id,
            warehouse_fk=draft.warehouse_id,
            purpose=draft.purpose,
            state=PurchaseOrderState.DRAFT.value,
            ordered_on=self._today(actor),
            expected_on=draft.expected_on,
            supplier_reference=(draft.supplier_reference or "").strip() or None,
            currency_code=draft.currency_code
            or supplier.currency_code
            or (business.base_currency if business else "GHS"),
            exchange_rate=draft.exchange_rate,
            tax_total=ZERO,
            notes=(draft.notes or "").strip() or None,
        )
        for position, item in enumerate(draft.lines, start=1):
            order.lines.append(self._line_from(actor, item, position, sales_order))
        self._total(order)
        self._db.add(order)
        self._db.flush()
        self._audit.record(
            actor,
            "purchase_order_raised",
            entity_type="purchase_order",
            entity_id=order.id,
            entity_label=f"draft for {supplier.name}",
            after={"lines": len(order.lines), "total": str(order.total)},
        )
        return order

    def _line_from(
        self,
        actor: AnyActor,
        item: PurchaseLineDraft,
        position: int,
        sales_order: SalesOrder | None,
    ) -> PurchaseOrderLine:
        if item.quantity <= ZERO:
            raise ValidationError(f"Line {position}: the quantity is above zero.", field="quantity")
        if item.unit_cost < ZERO:
            raise ValidationError(
                f"Line {position}: the unit cost is not negative.", field="unit_cost"
            )
        if not (item.description or "").strip():
            raise ValidationError(
                f"Line {position}: a description is required.", field="description"
            )
        if not ZERO <= item.discount_rate < Decimal("1"):
            raise ValidationError("A discount rate is between 0 and 1.", field="discount_rate")
        sales_line: SalesOrderLine | None = None
        if item.sales_order_line_id is not None:
            if sales_order is None:
                raise ValidationError(
                    "A line placed against a sales order line needs the sales order on the "
                    "purchase order.",
                    field="sales_order_id",
                )
            sales_line = next(
                (line for line in sales_order.lines if line.id == item.sales_order_line_id), None
            )
            if sales_line is None:
                raise NotFoundError("Sales order line not found on that order.")
        if item.product_id is not None:
            product = self._db.get(Product, item.product_id)
            if product is None or product.business_id != _business_of(actor):
                raise NotFoundError("Product not found.")
        net_unit = money(item.unit_cost * (Decimal("1") - item.discount_rate))
        return PurchaseOrderLine(
            business_id=_business_of(actor),
            position=position,
            sales_order_line_fk=sales_line.id if sales_line else None,
            supplier_quotation_line_fk=item.supplier_quotation_line_id,
            product_fk=item.product_id,
            description=item.description.strip(),
            unit_fk=item.unit_id,
            quantity_ordered=item.quantity,
            unit_cost=item.unit_cost,
            discount_rate=item.discount_rate,
            net_unit_cost=net_unit,
            line_total=money(net_unit * item.quantity),
            expected_on=item.expected_on,
            notes=(item.notes or "").strip() or None,
        )

    def _total(self, order: PurchaseOrder) -> None:
        order.net_total = money(sum((line.line_total for line in order.lines), ZERO))
        order.total = money(order.net_total + order.tax_total)

    # -- lifecycle --------------------------------------------------------------------

    def approve(self, actor: AnyActor, order_id: uuid.UUID) -> PurchaseOrder:
        actor.require("purchase_order.approve")
        order = self.require(actor, order_id)
        assert_purchase_order_transition(
            PurchaseOrderState(order.state), PurchaseOrderState.APPROVED
        )
        order.state = PurchaseOrderState.APPROVED.value
        order.approved_by_id = actor.user_id
        order.approved_at = self._clock.now()
        self._db.flush()
        self._audit.record(
            actor,
            "purchase_order_approved",
            entity_type="purchase_order",
            entity_id=order.id,
            entity_label=order.number or "draft",
        )
        return order

    def issue(self, actor: AnyActor, order_id: uuid.UUID) -> PurchaseOrder:
        """Number the order, freeze its lines and record what it procures."""
        actor.require("purchase_order.approve")
        order = self.require(actor, order_id)
        assert_purchase_order_transition(PurchaseOrderState(order.state), PurchaseOrderState.ISSUED)
        allocated = OrganisationService(self._db, clock=self._clock).allocate_number(
            actor, "purchase_order", branch_id=actor.branch_id
        )
        order.number = allocated.number
        order.state = PurchaseOrderState.ISSUED.value
        order.issued_by_id = actor.user_id
        order.issued_at = self._clock.now()
        for line in order.lines:
            if line.sales_order_line_fk is not None:
                sales_line = self._db.get(SalesOrderLine, line.sales_order_line_fk)
                if sales_line is not None:
                    self._orders.record_procured(sales_line, line.quantity_ordered)
        self._db.flush()
        self._audit.record(
            actor,
            "purchase_order_issued",
            entity_type="purchase_order",
            entity_id=order.id,
            entity_label=order.number,
            after={"total": str(order.total), "lines": len(order.lines)},
        )
        return order

    def cancel(self, actor: AnyActor, order_id: uuid.UUID, *, reason: str) -> PurchaseOrder:
        actor.require("purchase_order.manage")
        order = self.require(actor, order_id)
        if not reason.strip():
            raise ValidationError("A cancellation records why.", field="reason")
        if any(line.quantity_received > ZERO for line in order.lines):
            raise ConflictError(
                f"Purchase order {order.number} has received goods and cannot be cancelled."
            )
        was_issued = order.state == PurchaseOrderState.ISSUED.value
        assert_purchase_order_transition(
            PurchaseOrderState(order.state), PurchaseOrderState.CANCELLED
        )
        order.state = PurchaseOrderState.CANCELLED.value
        order.cancelled_at = self._clock.now()
        order.cancellation_reason = reason.strip()
        if was_issued:
            for line in order.lines:
                if line.sales_order_line_fk is not None:
                    sales_line = self._db.get(SalesOrderLine, line.sales_order_line_fk)
                    if sales_line is not None:
                        self._orders.record_procured(sales_line, -line.quantity_ordered)
        self._db.flush()
        self._audit.record(
            actor,
            "purchase_order_cancelled",
            entity_type="purchase_order",
            entity_id=order.id,
            entity_label=order.number or "draft",
            reason=reason,
        )
        return order

    def close(self, actor: AnyActor, order_id: uuid.UUID) -> PurchaseOrder:
        """Close a short-delivered order: what did not arrive will not."""
        actor.require("purchase_order.manage")
        order = self.require(actor, order_id)
        assert_purchase_order_transition(PurchaseOrderState(order.state), PurchaseOrderState.CLOSED)
        for line in order.lines:
            short = line.quantity_ordered - line.quantity_received
            if short > ZERO and line.sales_order_line_fk is not None:
                sales_line = self._db.get(SalesOrderLine, line.sales_order_line_fk)
                if sales_line is not None:
                    self._orders.record_procured(sales_line, -short)
        order.state = PurchaseOrderState.CLOSED.value
        self._db.flush()
        self._audit.record(
            actor,
            "purchase_order_closed",
            entity_type="purchase_order",
            entity_id=order.id,
            entity_label=order.number or "draft",
        )
        return order

    # -- reading ----------------------------------------------------------------------

    def require(self, actor: AnyActor, order_id: uuid.UUID) -> PurchaseOrder:
        actor.require("purchase_order.view")
        order = self._db.get(PurchaseOrder, order_id)
        if order is None or order.business_id != _business_of(actor):
            raise NotFoundError("Purchase order not found.")
        return order

    def orders(
        self,
        actor: AnyActor,
        *,
        state: str | None = None,
        supplier_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[PurchaseOrder]:
        actor.require("purchase_order.view")
        query = select(PurchaseOrder).where(PurchaseOrder.business_id == _business_of(actor))
        if state:
            query = query.where(PurchaseOrder.state == state)
        if supplier_id:
            query = query.where(PurchaseOrder.supplier_fk == supplier_id)
        return list(
            self._db.scalars(
                query.order_by(PurchaseOrder.created_at.desc()).limit(max(min(limit, 200), 1))
            ).all()
        )

    # -- receiving --------------------------------------------------------------------

    def receive(self, actor: AnyActor, draft: ReceiptDraft) -> GoodsReceipt:
        """Record what arrived and post the inventory accrual, in one step.

        A receipt is posted as it is recorded: the goods are on the floor, and a draft
        receipt would be stock that exists in the warehouse and not in the ledger.
        """
        actor.require("goods_receipt.manage")
        order = self.require(actor, draft.purchase_order_id)
        if PurchaseOrderState(order.state) not in RECEIVABLE_STATES:
            raise ConflictError(
                f"Purchase order {order.number or 'in draft'} is "
                f"{order.state.replace('_', ' ')}; goods are received against an issued order.",
                state=order.state,
            )
        if not draft.lines:
            raise ValidationError("A receipt carries at least one line.", field="lines")

        business = self._db.get(Business, order.business_id)
        receipt = GoodsReceipt(
            business_id=order.business_id,
            branch_id=actor.branch_id,
            purchase_order_fk=order.id,
            supplier_fk=order.supplier_fk,
            warehouse_fk=draft.warehouse_id or order.warehouse_fk,
            state=GoodsReceiptState.DRAFT.value,
            received_on=draft.received_on or self._today(actor),
            delivery_reference=(draft.delivery_reference or "").strip() or None,
            inspection_pending=draft.inspection_pending,
            notes=(draft.notes or "").strip() or None,
        )
        accepted_value = ZERO
        for position, item in enumerate(draft.lines, start=1):
            line = next(
                (
                    candidate
                    for candidate in order.lines
                    if candidate.id == item.purchase_order_line_id
                ),
                None,
            )
            if line is None:
                raise NotFoundError(f"Receipt line {position}: that line is not on this order.")
            if item.quantity_received < ZERO or item.quantity_rejected < ZERO:
                raise ValidationError("Quantities are not negative.", field="quantity_received")
            if item.quantity_rejected > item.quantity_received:
                raise ValidationError(
                    "More cannot be rejected than was received.", field="quantity_rejected"
                )
            if item.quantity_rejected > ZERO and not (item.rejection_reason or "").strip():
                raise ValidationError("A rejection records why.", field="rejection_reason")
            outstanding = line.quantity_ordered - line.quantity_received
            if item.quantity_received > outstanding:
                raise ConflictError(
                    f"Line {line.position}: only {outstanding} is outstanding on the order; "
                    f"{item.quantity_received} was offered. Over-delivery is refused rather "
                    "than quietly accepted.",
                    outstanding=str(outstanding),
                )
            accepted = item.quantity_received - item.quantity_rejected
            base_unit_cost = money(multiply(line.net_unit_cost, order.exchange_rate))
            receipt.lines.append(
                GoodsReceiptLine(
                    business_id=order.business_id,
                    position=position,
                    purchase_order_line_fk=line.id,
                    quantity_received=item.quantity_received,
                    quantity_accepted=accepted,
                    quantity_rejected=item.quantity_rejected,
                    rejection_reason=(item.rejection_reason or "").strip() or None,
                    unit_cost=base_unit_cost,
                    batch=(item.batch or "").strip() or None,
                    expiry=item.expiry,
                    serials=(item.serials or "").strip() or None,
                    manufacturer=(item.manufacturer or "").strip() or None,
                )
            )
            line.quantity_received = line.quantity_received + item.quantity_received
            line.quantity_accepted = line.quantity_accepted + accepted
            line.quantity_rejected = line.quantity_rejected + item.quantity_rejected
            accepted_value = money(accepted_value + base_unit_cost * accepted)
            if line.sales_order_line_fk is not None and accepted > ZERO:
                sales_line = self._db.get(SalesOrderLine, line.sales_order_line_fk)
                if sales_line is not None:
                    self._orders.record_received(sales_line, accepted)

        receipt.accepted_value = accepted_value
        self._db.add(receipt)
        self._db.flush()

        allocated = OrganisationService(self._db, clock=self._clock).allocate_number(
            actor, "goods_receipt", branch_id=actor.branch_id
        )
        receipt.number = allocated.number

        if accepted_value > ZERO:
            inventory_account = GOODS_IN_QUARANTINE if receipt.inspection_pending else INVENTORY
            outcome = JournalService(self._db, clock=self._clock).post(
                actor,
                JournalDraft(
                    posting_date=receipt.received_on,
                    narration=f"Goods received {receipt.number} against {order.number}",
                    lines=[
                        EntryLine(
                            account_code=inventory_account,
                            side=Side.DEBIT,
                            amount=accepted_value,
                            narration=f"{order.number} received",
                        ),
                        EntryLine(
                            account_code=GOODS_RECEIVED_NOT_INVOICED,
                            side=Side.CREDIT,
                            amount=accepted_value,
                            narration=f"{order.number} awaiting invoice",
                        ),
                    ],
                    source=JournalSource.PURCHASES,
                    source_reference=receipt.number,
                    source_id=receipt.id,
                    idempotency_key=f"goods_receipt:{receipt.id}",
                    currency_code=business.base_currency if business else None,
                    branch_id=actor.branch_id,
                ),
            )
            receipt.journal_entry_fk = outcome.entry.id

        receipt.state = GoodsReceiptState.POSTED.value
        receipt.posted_by_id = actor.user_id
        receipt.posted_at = self._clock.now()
        ordered = sum((line.quantity_ordered for line in order.lines), ZERO)
        received = sum((line.quantity_received for line in order.lines), ZERO)
        order.state = purchase_order_state_after_receipt(
            ordered, received, PurchaseOrderState(order.state)
        ).value
        self._db.flush()
        self._audit.record(
            actor,
            "goods_receipt_posted",
            entity_type="goods_receipt",
            entity_id=receipt.id,
            entity_label=receipt.number,
            after={
                "purchase_order": order.number,
                "accepted_value": str(accepted_value),
                "lines": len(receipt.lines),
            },
        )
        return receipt

    def receipts(
        self, actor: AnyActor, *, order_id: uuid.UUID | None = None, limit: int = 50
    ) -> list[GoodsReceipt]:
        actor.require("purchase_order.view")
        query = select(GoodsReceipt).where(GoodsReceipt.business_id == _business_of(actor))
        if order_id:
            query = query.where(GoodsReceipt.purchase_order_fk == order_id)
        return list(
            self._db.scalars(
                query.order_by(
                    GoodsReceipt.received_on.desc(), GoodsReceipt.created_at.desc()
                ).limit(max(min(limit, 200), 1))
            ).all()
        )

    def require_receipt(self, actor: AnyActor, receipt_id: uuid.UUID) -> GoodsReceipt:
        actor.require("purchase_order.view")
        receipt = self._db.get(GoodsReceipt, receipt_id)
        if receipt is None or receipt.business_id != _business_of(actor):
            raise NotFoundError("Goods receipt not found.")
        return receipt

    # -- helpers ----------------------------------------------------------------------

    def _sources_of(self, line: SalesOrderLine) -> list[QuotationLineSource]:
        if line.quotation_line_fk is None:
            return []
        return list(
            self._db.scalars(
                select(QuotationLineSource)
                .where(QuotationLineSource.quotation_line_fk == line.quotation_line_fk)
                .order_by(QuotationLineSource.position)
            ).all()
        )

    def _today(self, actor: AnyActor) -> date:
        business = self._db.get(Business, actor.business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
