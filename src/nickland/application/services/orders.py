"""Sales orders: acceptance, conversion from the quotation, position and cost variance.

PRD §6.8, AC-07. The order is converted from the quotation without anybody typing it
again, which is both the convenience the requirement asks for and the control: the
figures on the order are the figures that were offered, because they were copied.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.quotations import QuotationService
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.money import ZERO, divide, money, present
from nickland.domain.orders import (
    AcceptanceMethod,
    CostVariance,
    LinePosition,
    SalesOrderLineState,
    SalesOrderState,
    SourceKind,
    order_state,
)
from nickland.domain.pricing import QuotationState
from nickland.persistence.base import as_dict
from nickland.persistence.catalogue_models import Product
from nickland.persistence.models import Business
from nickland.persistence.order_models import (
    CustomerAcceptance,
    PurchaseOrder,
    SalesOrder,
    SalesOrderLine,
)
from nickland.persistence.pricing_models import QuotationLine
from nickland.platform.clock import Clock, SystemClock, business_date


@dataclass(frozen=True, slots=True)
class AcceptanceDraft:
    """The customer's confirmation, as it arrived."""

    method: AcceptanceMethod | str = AcceptanceMethod.PURCHASE_ORDER
    reference: str | None = None
    accepted_on: date | None = None
    evidence_attachment_id: uuid.UUID | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class LineVariance:
    """Expected against actual procurement cost on one order line."""

    line_id: uuid.UUID
    position: int
    description: str
    quantity: Decimal
    expected_unit_cost: Decimal
    actual_unit_cost: Decimal | None
    per_unit: Decimal | None
    total: Decimal | None


@dataclass(frozen=True, slots=True)
class OrderVariance:
    """The cost variance report for one order. The fourth exit criterion."""

    order_id: uuid.UUID
    number: str
    lines: list[LineVariance]
    expected_total: Decimal
    actual_total: Decimal | None
    variance_total: Decimal | None
    lines_known: int


class SalesOrderService:
    """Acceptance, conversion, the fulfilment position, substitution and variance."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._quotations = QuotationService(session, clock=self._clock)

    # -- acceptance -------------------------------------------------------------------

    def record_acceptance(
        self, actor: AnyActor, quotation_id: uuid.UUID, draft: AcceptanceDraft
    ) -> CustomerAcceptance:
        """Keep the customer's confirmation against the quotation, and mark it accepted.

        A verbal acceptance is admitted and recorded as such, because the alternative
        is a clerk inventing a purchase order number. It is still evidence of a kind: who
        took the call, when, and what was said.
        """
        actor.require("sales_order.manage")
        quotation = self._quotations.require(actor, quotation_id)
        method = AcceptanceMethod(str(draft.method))
        reference = (draft.reference or "").strip() or None
        if (
            method is not AcceptanceMethod.VERBAL
            and not reference
            and not draft.evidence_attachment_id
        ):
            raise ValidationError(
                "An acceptance by purchase order, signed quotation or email carries the "
                "customer's reference or the document itself. Record it as verbal if "
                "neither exists.",
                field="reference",
            )
        if method is AcceptanceMethod.VERBAL and not (draft.notes or "").strip():
            raise ValidationError(
                "A verbal acceptance records who confirmed it and what was said.",
                field="notes",
            )
        if quotation.state == QuotationState.ISSUED.value:
            self._quotations.decide(
                actor, quotation_id, state=QuotationState.ACCEPTED, on=draft.accepted_on
            )
        elif quotation.state != QuotationState.ACCEPTED.value:
            raise ConflictError(
                f"Quotation {quotation.number} is {quotation.state.replace('_', ' ')}; only "
                "an issued quotation can be accepted.",
                state=quotation.state,
            )

        acceptance = CustomerAcceptance(
            business_id=quotation.business_id,
            quotation_fk=quotation.id,
            method=method.value,
            reference=reference,
            accepted_on=draft.accepted_on or self._today(actor),
            evidence_attachment_fk=draft.evidence_attachment_id,
            notes=(draft.notes or "").strip() or None,
            recorded_by_id=actor.user_id,
        )
        self._db.add(acceptance)
        self._db.flush()
        self._audit.record(
            actor,
            "quotation_acceptance_recorded",
            entity_type="quotation",
            entity_id=quotation.id,
            entity_label=quotation.number,
            after={
                "method": method.value,
                "reference": reference,
                "evidence": str(draft.evidence_attachment_id)
                if draft.evidence_attachment_id
                else None,
            },
        )
        return acceptance

    def acceptances(self, actor: AnyActor, quotation_id: uuid.UUID) -> list[CustomerAcceptance]:
        actor.require("sales_order.view")
        return list(
            self._db.scalars(
                select(CustomerAcceptance)
                .where(
                    CustomerAcceptance.business_id == _business_of(actor),
                    CustomerAcceptance.quotation_fk == quotation_id,
                )
                .order_by(CustomerAcceptance.accepted_on)
            ).all()
        )

    # -- conversion -------------------------------------------------------------------

    def convert(
        self,
        actor: AnyActor,
        quotation_id: uuid.UUID,
        *,
        required_by: date | None = None,
        notes: str | None = None,
    ) -> SalesOrder:
        """The accepted quotation as an order, every figure carried across.

        Refused without an acceptance on record: an order with no evidence that the
        customer agreed to it is a quotation somebody promoted, and the evidence is the
        point of AC-07.
        """
        actor.require("sales_order.manage")
        quotation = self._quotations.require(actor, quotation_id)
        if quotation.state != QuotationState.ACCEPTED.value:
            raise ConflictError(
                f"Quotation {quotation.number} has not been accepted. Record the "
                "customer's acceptance first.",
                state=quotation.state,
            )
        acceptance = self._db.scalar(
            select(CustomerAcceptance)
            .where(CustomerAcceptance.quotation_fk == quotation.id)
            .order_by(CustomerAcceptance.accepted_on.desc())
        )
        if acceptance is None:
            raise ConflictError(
                "No acceptance is on record for this quotation. Record the customer's "
                "purchase order or confirmation before converting it."
            )
        existing = self._db.scalar(
            select(SalesOrder).where(
                SalesOrder.quotation_fk == quotation.id,
                SalesOrder.state != SalesOrderState.CANCELLED.value,
            )
        )
        if existing is not None:
            raise ConflictError(
                f"Quotation {quotation.number} has already been converted to order "
                f"{existing.number}.",
                order_id=str(existing.id),
            )

        number = OrganisationService(self._db, clock=self._clock).allocate_number(
            actor, "sales_order", branch_id=actor.branch_id
        )
        order = SalesOrder(
            business_id=quotation.business_id,
            branch_id=actor.branch_id,
            number=number.number,
            quotation_fk=quotation.id,
            acceptance_fk=acceptance.id,
            customer_fk=quotation.customer_fk,
            customer_reference=acceptance.reference or quotation.customer_reference,
            state=SalesOrderState.OPEN.value,
            ordered_on=acceptance.accepted_on,
            required_by=required_by,
            currency_code=quotation.currency_code,
            precision=quotation.precision,
            payment_plan=quotation.payment_plan,
            net_total=money(quotation.net_total),
            tax_total=money(quotation.tax_total),
            total_payable=money(quotation.total_payable),
            expected_cost_total=money(quotation.landed_cost_total),
            notes=(notes or "").strip() or quotation.notes,
            converted_by_id=actor.user_id,
        )
        for line in quotation.lines:
            order.lines.append(self._line_from(line))
        self._db.add(order)
        self._db.flush()
        self._refresh_position(order)
        self._audit.record(
            actor,
            "sales_order_converted",
            entity_type="sales_order",
            entity_id=order.id,
            entity_label=order.number,
            after={
                "quotation": quotation.number,
                "lines": len(order.lines),
                "total_payable": str(order.total_payable),
            },
        )
        return order

    def _line_from(self, line: QuotationLine) -> SalesOrderLine:
        # The part of a line sourced from stock is procured, and received, the moment
        # it is ordered: Phase 8 reserves the stock; until then the order says what it
        # draws on. A line split between stock and a supplier carries only its
        # supplier part forward to procurement.
        from_stock = money(
            sum(
                (
                    source.quantity
                    for source in line.sources
                    if source.kind == SourceKind.STOCK.value
                ),
                ZERO,
            )
        )
        from_stock = min(from_stock, line.quantity)
        source_kind = SourceKind.STOCK if from_stock >= line.quantity else SourceKind.SUPPLIER
        expected_unit_cost = (
            divide(line.landed_cost, line.quantity) if line.quantity > ZERO else ZERO
        )
        return SalesOrderLine(
            business_id=line.business_id,
            position=line.position,
            quotation_line_fk=line.id,
            product_fk=line.product_fk,
            description=line.description,
            specification=line.specification,
            unit_fk=line.unit_fk,
            source_kind=source_kind.value,
            quantity_ordered=line.quantity,
            quantity_procured=from_stock,
            quantity_received=from_stock,
            unit_price=line.unit_net_price,
            line_total=money(line.net_price),
            tax_amount=money(line.tax_amount),
            expected_unit_cost=expected_unit_cost,
        )

    # -- reading ----------------------------------------------------------------------

    def require(self, actor: AnyActor, order_id: uuid.UUID) -> SalesOrder:
        actor.require("sales_order.view")
        order = self._db.get(SalesOrder, order_id)
        if order is None or order.business_id != _business_of(actor):
            raise NotFoundError("Sales order not found.")
        return order

    def orders(
        self, actor: AnyActor, *, state: str | None = None, limit: int = 50
    ) -> list[SalesOrder]:
        actor.require("sales_order.view")
        query = select(SalesOrder).where(SalesOrder.business_id == _business_of(actor))
        if state:
            query = query.where(SalesOrder.state == state)
        return list(
            self._db.scalars(
                query.order_by(SalesOrder.ordered_on.desc(), SalesOrder.number.desc()).limit(
                    max(min(limit, 200), 1)
                )
            ).all()
        )

    def purchase_orders_for(self, actor: AnyActor, order_id: uuid.UUID) -> list[PurchaseOrder]:
        """Every purchase order serving this order. Several may, per PRD §6.8."""
        order = self.require(actor, order_id)
        return list(
            self._db.scalars(
                select(PurchaseOrder)
                .where(PurchaseOrder.sales_order_fk == order.id)
                .order_by(PurchaseOrder.ordered_on, PurchaseOrder.created_at)
            ).all()
        )

    def position(self, order: SalesOrder) -> list[LinePosition]:
        return [
            LinePosition(
                ordered=line.quantity_ordered,
                cancelled=line.quantity_cancelled,
                procured=line.quantity_procured,
                received=line.quantity_received,
            )
            for line in order.lines
        ]

    # -- changes ----------------------------------------------------------------------

    def cancel_line_quantity(
        self,
        actor: AnyActor,
        order_id: uuid.UUID,
        line_id: uuid.UUID,
        *,
        quantity: Decimal,
        reason: str,
    ) -> SalesOrder:
        """Take part or all of a line off the order, never below what has arrived."""
        actor.require("sales_order.manage")
        order = self.require(actor, order_id)
        line = self._line_of(order, line_id)
        if quantity <= ZERO:
            raise ValidationError("The cancelled quantity is above zero.", field="quantity")
        if not reason.strip():
            raise ValidationError("A cancellation records why.", field="reason")
        remaining = line.quantity_ordered - line.quantity_cancelled - line.quantity_received
        if quantity > remaining:
            raise ConflictError(
                f"Only {remaining} of line {line.position} is still outstanding; what has "
                "been received cannot be cancelled.",
                outstanding=str(remaining),
            )
        before = as_dict(line)
        line.quantity_cancelled = line.quantity_cancelled + quantity
        self._refresh_position(order)
        self._audit.record(
            actor,
            "sales_order_line_cancelled",
            entity_type="sales_order",
            entity_id=order.id,
            entity_label=f"{order.number} line {line.position}",
            reason=reason,
            before=before,
            after=as_dict(line),
        )
        return order

    def cancel(self, actor: AnyActor, order_id: uuid.UUID, *, reason: str) -> SalesOrder:
        """Cancel what is outstanding on every line."""
        actor.require("sales_order.manage")
        order = self.require(actor, order_id)
        if not reason.strip():
            raise ValidationError("A cancellation records why.", field="reason")
        if any(line.quantity_received > ZERO for line in order.lines):
            raise ConflictError(
                f"Order {order.number} has received goods and cannot be cancelled outright. "
                "Cancel the outstanding quantity line by line.",
            )
        for line in order.lines:
            line.quantity_cancelled = line.quantity_ordered
        order.cancelled_at = self._clock.now()
        order.cancellation_reason = reason.strip()
        self._refresh_position(order)
        self._audit.record(
            actor,
            "sales_order_cancelled",
            entity_type="sales_order",
            entity_id=order.id,
            entity_label=order.number,
            reason=reason,
        )
        return order

    def substitute(
        self,
        actor: AnyActor,
        order_id: uuid.UUID,
        line_id: uuid.UUID,
        *,
        product_id: uuid.UUID,
        reason: str,
    ) -> SalesOrder:
        """Replace the product on a line with an approved alternative. PRD §6.8.

        The original product is kept on the line, so the order still says what the
        customer asked for and the substitution is visible beside it.
        """
        actor.require("sales_order.manage")
        order = self.require(actor, order_id)
        line = self._line_of(order, line_id)
        if not reason.strip():
            raise ValidationError("A substitution records why.", field="reason")
        if line.quantity_received > ZERO:
            raise ConflictError(
                f"Line {line.position} has received goods; what arrived cannot be "
                "substituted after the fact."
            )
        product = self._db.get(Product, product_id)
        if product is None or product.business_id != order.business_id:
            raise NotFoundError("Product not found.")
        if product.id == line.product_fk:
            raise ValidationError("That is already the product on the line.", field="product_id")
        before = as_dict(line)
        line.substituted_from_product_fk = line.substituted_from_product_fk or line.product_fk
        line.product_fk = product.id
        line.description = product.name
        line.substitution_reason = reason.strip()
        line.substitution_approved_by_id = actor.user_id
        line.substitution_approved_at = self._clock.now()
        self._db.flush()
        self._audit.record(
            actor,
            "sales_order_line_substituted",
            entity_type="sales_order",
            entity_id=order.id,
            entity_label=f"{order.number} line {line.position}",
            reason=reason,
            before=before,
            after=as_dict(line),
        )
        return order

    # -- position maintenance, called by procurement ----------------------------------

    def record_procured(self, line: SalesOrderLine, quantity: Decimal) -> None:
        """A purchase order line was issued against this line."""
        line.quantity_procured = line.quantity_procured + quantity
        self._refresh_position(line.order)

    def record_received(self, line: SalesOrderLine, quantity: Decimal) -> None:
        """Goods for this line arrived and were accepted."""
        line.quantity_received = line.quantity_received + quantity
        self._refresh_position(line.order)

    def record_actual_cost(self, line: SalesOrderLine, unit_cost: Decimal) -> None:
        """The supplier's invoice is posted, so the cost is now known."""
        line.actual_unit_cost = unit_cost

    def _refresh_position(self, order: SalesOrder) -> None:
        positions = self.position(order)
        for line, position in zip(order.lines, positions, strict=True):
            line.state = position.state.value
        if order.cancelled_at is None or any(
            line.state != SalesOrderLineState.CANCELLED.value for line in order.lines
        ):
            order.state = order_state(positions).value
        else:
            order.state = SalesOrderState.CANCELLED.value
        self._db.flush()

    # -- cost variance ----------------------------------------------------------------

    def cost_variance(self, actor: AnyActor, order_id: uuid.UUID) -> OrderVariance:
        """Quoted landed cost against what procurement actually cost. PRD §6.8."""
        actor.require("costing.view")
        order = self.require(actor, order_id)
        lines: list[LineVariance] = []
        expected_total = ZERO
        actual_total = ZERO
        known = 0
        for line in order.lines:
            live = line.quantity_ordered - line.quantity_cancelled
            variance = CostVariance(
                expected_unit_cost=line.expected_unit_cost,
                actual_unit_cost=line.actual_unit_cost,
                quantity=live,
            )
            lines.append(
                LineVariance(
                    line_id=line.id,
                    position=line.position,
                    description=line.description,
                    quantity=live,
                    expected_unit_cost=line.expected_unit_cost,
                    actual_unit_cost=line.actual_unit_cost,
                    per_unit=variance.per_unit,
                    total=variance.total,
                )
            )
            expected_total = money(expected_total + line.expected_unit_cost * live)
            if line.actual_unit_cost is not None:
                known += 1
                actual_total = money(actual_total + line.actual_unit_cost * live)
        complete = known == len(order.lines) and bool(order.lines)
        # Presented at the money precision the report is read at. Each line's unit cost
        # is a quotient held at working precision, and multiplying it back leaves a
        # residue in the sixth place that is arithmetic, not information.
        return OrderVariance(
            order_id=order.id,
            number=order.number,
            lines=[
                LineVariance(
                    line_id=line.line_id,
                    position=line.position,
                    description=line.description,
                    quantity=line.quantity,
                    expected_unit_cost=present(line.expected_unit_cost, 2),
                    actual_unit_cost=(
                        present(line.actual_unit_cost, 2)
                        if line.actual_unit_cost is not None
                        else None
                    ),
                    per_unit=present(line.per_unit, 2) if line.per_unit is not None else None,
                    total=present(line.total, 2) if line.total is not None else None,
                )
                for line in lines
            ],
            expected_total=present(expected_total, 2),
            actual_total=present(actual_total, 2) if complete else None,
            variance_total=present(money(actual_total - expected_total), 2) if complete else None,
            lines_known=known,
        )

    # -- helpers ----------------------------------------------------------------------

    def _line_of(self, order: SalesOrder, line_id: uuid.UUID) -> SalesOrderLine:
        for line in order.lines:
            if line.id == line_id:
                return line
        raise NotFoundError("Order line not found.")

    def _today(self, actor: AnyActor) -> date:
        business = self._db.get(Business, actor.business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)


def summary_of(order: SalesOrder, purchase_orders: list[PurchaseOrder]) -> dict[str, Any]:
    """The figures a list screen shows, computed once here."""
    ordered = sum((line.quantity_ordered - line.quantity_cancelled for line in order.lines), ZERO)
    received = sum((line.quantity_received for line in order.lines), ZERO)
    return {
        "units_ordered": ordered,
        "units_received": received,
        "purchase_orders": len(purchase_orders),
        "purchase_order_numbers": [item.number for item in purchase_orders if item.number],
    }


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
