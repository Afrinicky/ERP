"""The register: shifts, the cart, tenders, completion, refunds. PRD §6.10, ADR-0009.

The interface scans and taps; everything that is a figure is decided here. A sale posts once,
when every tender on it is confirmed: revenue, tax, the tender and the cost of the goods in
one entry, the goods through the stock engine. A tender confirmed twice — a duplicated
provider callback, a double tap — is recognised by its idempotency key and posts nothing more.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor, SystemActor
from nickland.application.services.catalogue import CatalogueService
from nickland.application.services.document_sources import DocumentSourceService
from nickland.application.services.documents import DocumentRecordService
from nickland.application.services.inventory import InventoryService
from nickland.application.services.journals import JournalDraft, JournalService
from nickland.application.services.organisation import OrganisationService
from nickland.application.services.pricing_lists import PriceListService
from nickland.application.services.receivables import ReceivablesService
from nickland.application.services.stock import holds_stock
from nickland.application.services.tax_postings import tax_entry_lines
from nickland.application.services.taxation import TaxableLine, TaxContext, TaxService
from nickland.domain.catalogue import TrackingPolicy
from nickland.domain.documents import DocumentContext, DocumentKind, Line, TaxComponent, Totals
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.finance import invoice_state
from nickland.domain.inventory import COST_OF_GOODS_SOLD, MovementType, StockState
from nickland.domain.ledger import EntryLine, JournalSource, Side, SubledgerKind
from nickland.domain.money import ZERO, allocate, money, multiply, plain, present, suggested_tenders
from nickland.domain.pos import (
    BANK,
    CASH_ON_HAND,
    CASH_OVER_AND_SHORT,
    PETTY_CASH,
    POS_REVENUE,
    SALES_RETURNS,
    CashMovementKind,
    DrawerPosition,
    RefundKind,
    SaleState,
    ShiftState,
    TenderMethod,
    TenderState,
    TenderView,
    assert_completable,
    assert_tender_transition,
    cash_change,
    count_total,
    outstanding,
)
from nickland.persistence.catalogue_models import Product
from nickland.persistence.finance_models import SalesInvoice
from nickland.persistence.inventory_models import Batch, StockMovement
from nickland.persistence.ledger_models import JournalEntry
from nickland.persistence.models import Business, Party, User
from nickland.persistence.pos_models import (
    PosCashMovement,
    PosRefund,
    PosRefundLine,
    PosSale,
    PosSaleLine,
    PosShift,
    PosTender,
)
from nickland.platform.clock import Clock, SystemClock, business_date
from nickland.platform.ids import new_id

COUNTER_ACCOUNTS = {"bank": BANK, "petty_cash": PETTY_CASH}
"""Where cash paid into or out of the drawer comes from or goes to."""


# --------------------------------------------------------------------------------------
# The payment-provider adapter (ADR-0009): defined, implemented by no vendor
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """A provider's answer, already mapped: only pending, confirmed or failed reach here."""

    idempotency_key: str
    state: TenderState
    provider_reference: str | None = None
    reason: str | None = None


class PaymentAdapter(Protocol):
    """What a Mobile Money, card or bank integration must provide. A timeout maps to
    pending, never to failed, because the payment may yet settle."""

    def initiate(self, amount: Decimal, reference: str, instrument: str) -> ProviderStatus: ...

    def query(self, provider_reference: str) -> ProviderStatus: ...

    def handle_callback(self, payload: dict[str, object]) -> ProviderStatus: ...


# --------------------------------------------------------------------------------------
# Drafts
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RefundLineDraft:
    sale_line_id: uuid.UUID
    quantity: Decimal


@dataclass(slots=True)
class RefundDraft:
    sale_id: uuid.UUID
    lines: list[RefundLineDraft]
    reason: str
    method: TenderMethod | str | None = None
    """How the money goes back. Defaults to how it was paid."""
    restock: bool = True
    kind: RefundKind | str = RefundKind.REFUND


@dataclass(slots=True)
class ShiftSummary:
    shift: PosShift
    drawer: DrawerPosition
    sales: int
    pending_tenders: list[PosTender] = field(default_factory=list)
    by_method: dict[str, Decimal] = field(default_factory=dict)


class PosService:
    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)
        self._inventory = InventoryService(session, clock=self._clock)
        self._prices = PriceListService(session, clock=self._clock)
        self._catalogue = CatalogueService(session, clock=self._clock)

    @property
    def session(self) -> DbSession:
        return self._db

    @property
    def clock(self) -> Clock:
        return self._clock

    # -- shifts ------------------------------------------------------------------------

    def current_shift(self, actor: AnyActor) -> PosShift | None:
        actor.require("pos.operate")
        return self._db.scalar(
            select(PosShift).where(
                PosShift.business_id == actor.business_id,
                PosShift.cashier_id == actor.user_id,
                PosShift.state == ShiftState.OPEN.value,
            )
        )

    def open_shift(
        self, actor: AnyActor, *, opening_float: Decimal, register: str = "Register 1"
    ) -> PosShift:
        actor.require("pos.shift_manage")
        if actor.user_id is None:
            raise ConflictError("A shift is opened by a signed-in cashier.")
        if self.current_shift(actor) is not None:
            raise ConflictError("A shift is already open for you. Close it before opening another.")
        float_ = money(opening_float)
        if float_ < ZERO:
            raise ValidationError("The opening float is not negative.", field="opening_float")
        warehouse = self._inventory.default_warehouse(_business_of(actor), actor.branch_id)
        today = self._today(actor)
        count = self._db.scalar(
            select(func.count(PosShift.id)).where(PosShift.business_id == actor.business_id)
        )
        shift = PosShift(
            business_id=_business_of(actor),
            branch_id=actor.branch_id,
            number=f"SH-{today.strftime('%Y%m%d')}-{(count or 0) + 1:03d}",
            cashier_id=actor.user_id,
            register=(register or "").strip() or "Register 1",
            warehouse_fk=warehouse.id,
            state=ShiftState.OPEN.value,
            business_day=today,
            opened_at=self._clock.now(),
            opening_float=float_,
        )
        self._db.add(shift)
        self._db.flush()
        self._audit.record(
            actor,
            "pos_shift_opened",
            entity_type="pos_shift",
            entity_id=shift.id,
            entity_label=shift.number,
            after={"float": str(float_), "register": shift.register},
        )
        return shift

    def require_shift(self, actor: AnyActor, shift_id: uuid.UUID) -> PosShift:
        actor.require("pos.operate")
        shift = self._db.get(PosShift, shift_id)
        if shift is None or shift.business_id != actor.business_id:
            raise NotFoundError("Shift not found.")
        return shift

    def cash_movement(
        self,
        actor: AnyActor,
        *,
        kind: CashMovementKind | str,
        amount: Decimal,
        reason: str,
        counter: str = "bank",
    ) -> PosCashMovement:
        """Cash paid into or taken out of the drawer: a float top-up, a banking."""
        actor.require("pos.shift_manage")
        shift = self._open_shift_of(actor)
        chosen = CashMovementKind(kind)
        value = present(money(amount), self._precision(actor))
        if value <= ZERO:
            raise ValidationError("An amount above zero.", field="amount")
        if not reason.strip():
            raise ValidationError("A cash movement records why.", field="reason")
        account = COUNTER_ACCOUNTS.get(counter)
        if account is None:
            raise ValidationError("Cash moves to or from the bank or petty cash.", field="counter")
        if chosen is CashMovementKind.CASH_OUT and value > self.drawer(shift).expected:
            raise ConflictError("More cannot be taken out than the drawer should hold.")
        movement = PosCashMovement(
            business_id=shift.business_id,
            shift_fk=shift.id,
            kind=chosen.value,
            amount=value,
            counter_account=account,
            reason=reason.strip(),
            recorded_by_id=actor.user_id,
        )
        self._db.add(movement)
        self._db.flush()
        drawer_side = Side.DEBIT if chosen is CashMovementKind.CASH_IN else Side.CREDIT
        other_side = Side.CREDIT if drawer_side is Side.DEBIT else Side.DEBIT
        entry = self._post(
            actor,
            narration=f"{'Cash in' if drawer_side is Side.DEBIT else 'Cash out'}, {shift.number}: "
            f"{reason.strip()}",
            lines=[
                EntryLine(account_code=CASH_ON_HAND, side=drawer_side, amount=value),
                EntryLine(account_code=account, side=other_side, amount=value),
            ],
            reference=shift.number,
            source_id=movement.id,
            key=f"pos_cash_movement:{movement.id}",
        )
        movement.journal_entry_fk = entry.id
        self._db.flush()
        return movement

    def drawer(self, shift: PosShift) -> DrawerPosition:
        """What the drawer should hold: the float, cash kept from sales, cash in and out,
        less cash refunded."""
        cash_sales = self._db.scalar(
            select(func.coalesce(func.sum(PosTender.amount), 0))
            .join(PosSale, PosSale.id == PosTender.sale_fk)
            .where(
                PosSale.shift_fk == shift.id,
                PosSale.completed_at.is_not(None),
                PosTender.method == TenderMethod.CASH.value,
                PosTender.state == TenderState.CONFIRMED.value,
            )
        )
        movements = self._db.scalars(
            select(PosCashMovement).where(PosCashMovement.shift_fk == shift.id)
        ).all()
        refunds = self._db.scalar(
            select(func.coalesce(func.sum(PosRefund.total), 0)).where(
                PosRefund.shift_fk == shift.id, PosRefund.method == TenderMethod.CASH.value
            )
        )
        return DrawerPosition(
            opening_float=shift.opening_float,
            cash_sales=money(Decimal(str(cash_sales or 0))),
            cash_in=money(
                sum((m.amount for m in movements if m.kind == CashMovementKind.CASH_IN), ZERO)
            ),
            cash_out=money(
                sum((m.amount for m in movements if m.kind == CashMovementKind.CASH_OUT), ZERO)
            ),
            cash_refunds=money(Decimal(str(refunds or 0))),
        )

    def summary(self, actor: AnyActor, shift_id: uuid.UUID) -> ShiftSummary:
        shift = self.require_shift(actor, shift_id)
        sales = list(
            self._db.scalars(
                select(PosSale).where(
                    PosSale.shift_fk == shift.id, PosSale.completed_at.is_not(None)
                )
            ).all()
        )
        by_method: dict[str, Decimal] = {}
        for sale in sales:
            for tender in sale.tenders:
                if tender.state == TenderState.CONFIRMED.value:
                    by_method[tender.method] = money(
                        by_method.get(tender.method, ZERO) + tender.amount
                    )
        return ShiftSummary(
            shift=shift,
            drawer=self.drawer(shift),
            sales=len(sales),
            pending_tenders=self._pending_tenders(shift),
            by_method=by_method,
        )

    def count_preview(
        self, actor: AnyActor, shift_id: uuid.UUID, denominations: dict[str, int]
    ) -> tuple[Decimal, Decimal, Decimal]:
        """``(counted, expected, variance)`` for a count in progress, before it is submitted."""
        shift = self.require_shift(actor, shift_id)
        counted = count_total({Decimal(k): int(v) for k, v in denominations.items()})
        drawer = self.drawer(shift)
        return counted, drawer.expected, drawer.variance(counted)

    def submit_count(
        self,
        actor: AnyActor,
        shift_id: uuid.UUID,
        *,
        denominations: dict[str, int],
        variance_reason: str | None = None,
    ) -> PosShift:
        """Count the drawer. A variance needs a reason; sign-off follows."""
        actor.require("pos.shift_manage")
        shift = self.require_shift(actor, shift_id)
        if shift.state != ShiftState.OPEN.value:
            raise ConflictError(f"Shift {shift.number} has already been counted.")
        if shift.cashier_id != actor.user_id and not actor.is_administrator:
            raise ConflictError("A shift is counted by its own cashier.")
        if self._db.scalar(
            select(PosSale.id).where(
                PosSale.shift_fk == shift.id, PosSale.state == SaleState.OPEN.value
            )
        ) and any(sale.lines for sale in self._open_sales(shift)):
            raise ConflictError(
                "A sale is still open on this register. Complete it, park it or void it first."
            )
        counted = count_total({Decimal(k): int(v) for k, v in denominations.items()})
        drawer = self.drawer(shift)
        variance = drawer.variance(counted)
        reason = (variance_reason or "").strip() or None
        if variance != ZERO and reason is None:
            raise ValidationError(
                f"The count differs from the expected {drawer.expected} by {variance}. Record "
                "why before submitting.",
                field="variance_reason",
            )
        unresolved = self._pending_tenders(shift)
        if unresolved and reason is None:
            raise ValidationError(
                f"{len(unresolved)} payment is still awaiting confirmation. Confirm or fail it, "
                "or record why it is being abandoned.",
                field="variance_reason",
            )
        for tender in unresolved:
            tender.state = TenderState.ABANDONED.value
            tender.abandoned_reason = reason
        shift.counted_denominations = {str(k): int(v) for k, v in denominations.items()}
        shift.counted_cash = counted
        shift.expected_cash = drawer.expected
        shift.variance = variance
        shift.variance_reason = reason
        shift.submitted_at = self._clock.now()
        shift.state = ShiftState.SUBMITTED.value
        self._db.flush()
        if variance == ZERO and not unresolved:
            # Nothing to sign off: a balanced drawer closes on the count.
            return self._close(actor, shift)
        return shift

    def approve_shift(self, actor: AnyActor, shift_id: uuid.UUID) -> PosShift:
        """A supervisor signs off a variance, or an abandoned payment, and the shift closes."""
        actor.require("pos.shift_approve")
        shift = self.require_shift(actor, shift_id)
        if shift.state != ShiftState.SUBMITTED.value:
            raise ConflictError(f"Shift {shift.number} is not awaiting sign-off.")
        if shift.cashier_id == actor.user_id and not actor.is_administrator:
            raise ConflictError(
                "A shift's variance is signed off by someone other than its cashier."
            )
        shift.approved_by_id = actor.user_id
        return self._close(actor, shift)

    def _close(self, actor: AnyActor, shift: PosShift) -> PosShift:
        variance = shift.variance or ZERO
        if variance != ZERO:
            amount = abs(variance)
            shortage = variance < ZERO
            entry = self._post(
                actor,
                narration=f"Drawer {'shortage' if shortage else 'overage'}, {shift.number}: "
                f"{shift.variance_reason}",
                lines=[
                    EntryLine(
                        account_code=CASH_OVER_AND_SHORT if shortage else CASH_ON_HAND,
                        side=Side.DEBIT,
                        amount=amount,
                    ),
                    EntryLine(
                        account_code=CASH_ON_HAND if shortage else CASH_OVER_AND_SHORT,
                        side=Side.CREDIT,
                        amount=amount,
                    ),
                ],
                reference=shift.number,
                source_id=shift.id,
                key=f"pos_shift_variance:{shift.id}",
            )
            shift.variance_journal_fk = entry.id
        shift.state = ShiftState.CLOSED.value
        shift.closed_at = self._clock.now()
        self._db.flush()
        self._audit.record(
            actor,
            "pos_shift_closed",
            entity_type="pos_shift",
            entity_id=shift.id,
            entity_label=shift.number,
            reason=shift.variance_reason,
            after={
                "expected": str(shift.expected_cash),
                "counted": str(shift.counted_cash),
                "variance": str(variance),
            },
        )
        return shift

    def shifts(self, actor: AnyActor, *, limit: int = 50) -> list[PosShift]:
        actor.require("pos.operate")
        return list(
            self._db.scalars(
                select(PosShift)
                .where(PosShift.business_id == actor.business_id)
                .order_by(PosShift.opened_at.desc())
                .limit(max(min(limit, 200), 1))
            ).all()
        )

    # -- the cart ----------------------------------------------------------------------

    def current_sale(self, actor: AnyActor) -> PosSale:
        """The open cart on this cashier's register, created when there is none."""
        shift = self._open_shift_of(actor)
        existing = self._open_sales(shift)
        if existing:
            return existing[0]
        business = self._db.get(Business, shift.business_id)
        sale = PosSale(
            business_id=shift.business_id,
            branch_id=shift.branch_id,
            shift_fk=shift.id,
            state=SaleState.OPEN.value,
            currency_code=business.base_currency if business else "GHS",
            cashier_id=actor.user_id,
        )
        self._db.add(sale)
        self._db.flush()
        return sale

    def require_sale(self, actor: AnyActor, sale_id: uuid.UUID) -> PosSale:
        actor.require("pos.operate")
        sale = self._db.get(PosSale, sale_id)
        if sale is None or sale.business_id != actor.business_id:
            raise NotFoundError("Sale not found.")
        return sale

    def scan(
        self, actor: AnyActor, sale_id: uuid.UUID, code: str, *, quantity: Decimal = Decimal("1")
    ) -> PosSale:
        """A barcode or a SKU, as a scanner or a keyboard sends it."""
        actor.require("pos.operate")
        value = (code or "").strip()
        if not value:
            raise ValidationError("Scan or type a barcode or code.", field="code")
        product = self._catalogue.by_barcode(actor, value) or self._catalogue.by_sku(actor, value)
        if product is None:
            raise NotFoundError(f"No item carries the code {value}.")
        return self.add(actor, sale_id, product.id, quantity=quantity)

    def add(
        self,
        actor: AnyActor,
        sale_id: uuid.UUID,
        product_id: uuid.UUID,
        *,
        quantity: Decimal = Decimal("1"),
        serials: list[str] | None = None,
    ) -> PosSale:
        actor.require("pos.operate")
        sale = self._editable(actor, sale_id)
        product = self._catalogue.require_product(actor, product_id)
        if not product.is_active or not product.is_sellable:
            raise ConflictError(f"{product.sku} is not offered for sale.")
        quantity = money(quantity)
        if quantity <= ZERO:
            raise ValidationError("A quantity above zero.", field="quantity")
        tracked_serial = TrackingPolicy(product.tracking_policy) is TrackingPolicy.SERIAL
        existing = next(
            (
                line
                for line in sale.lines
                if line.product_fk == product.id
                and line.override_reason is None
                and not tracked_serial
            ),
            None,
        )
        if existing is not None:
            existing.quantity = money(existing.quantity + quantity)
        else:
            price = self._prices.resolve(
                actor,
                product_id=product.id,
                customer_id=sale.customer_fk,
                quantity=quantity,
                on=self._today(actor),
            )
            if price is None:
                raise ConflictError(
                    f"{product.sku} has no price for this customer. Set a list price before "
                    "selling it at the register."
                )
            sale.lines.append(
                PosSaleLine(
                    business_id=sale.business_id,
                    position=(max((line.position for line in sale.lines), default=0) + 1),
                    product_fk=product.id,
                    description=product.name,
                    quantity=quantity,
                    list_price=price.price,
                    price_source=price.source_label,
                    unit_price=price.price,
                    serials="\n".join(serials or []) or None,
                )
            )
        self._reprice(actor, sale)
        return sale

    def set_quantity(
        self, actor: AnyActor, sale_id: uuid.UUID, line_id: uuid.UUID, quantity: Decimal
    ) -> PosSale:
        """A quantity of zero removes the line."""
        actor.require("pos.operate")
        sale = self._editable(actor, sale_id)
        line = self._line(sale, line_id)
        quantity = money(quantity)
        if quantity < ZERO:
            raise ValidationError("A quantity is not negative.", field="quantity")
        if quantity == ZERO:
            sale.lines.remove(line)
        else:
            line.quantity = quantity
        self._reprice(actor, sale)
        return sale

    def override_price(
        self,
        actor: AnyActor,
        sale_id: uuid.UUID,
        line_id: uuid.UUID,
        *,
        unit_price: Decimal,
        reason: str,
    ) -> PosSale:
        actor.require("pos.price_override")
        sale = self._editable(actor, sale_id)
        line = self._line(sale, line_id)
        price = money(unit_price)
        if price < ZERO:
            raise ValidationError("A price is not negative.", field="unit_price")
        if not reason.strip():
            raise ValidationError("An override records why.", field="reason")
        line.unit_price = price
        line.override_reason = reason.strip()
        line.override_by_id = actor.user_id
        self._reprice(actor, sale)
        self._audit.record(
            actor,
            "pos_price_overridden",
            entity_type="pos_sale",
            entity_id=sale.id,
            reason=reason.strip(),
            after={"line": line.position, "from": str(line.list_price), "to": str(price)},
        )
        return sale

    def discount(
        self, actor: AnyActor, sale_id: uuid.UUID, line_id: uuid.UUID, *, rate: Decimal
    ) -> PosSale:
        """A discount as a fraction of the line: 0.05 is five per cent."""
        actor.require("pos.discount")
        sale = self._editable(actor, sale_id)
        line = self._line(sale, line_id)
        value = money(rate)
        if value < ZERO or value >= Decimal("1"):
            raise ValidationError("A discount lies between nothing and the whole line.")
        line.discount_rate = value
        line.discount_by_id = actor.user_id if value > ZERO else None
        self._reprice(actor, sale)
        return sale

    def set_customer(
        self, actor: AnyActor, sale_id: uuid.UUID, customer_id: uuid.UUID | None
    ) -> PosSale:
        """Walk-in, or a registered customer: their agreed prices apply to every line not
        already overridden."""
        actor.require("pos.operate")
        sale = self._editable(actor, sale_id)
        if customer_id is not None:
            party = self._db.get(Party, customer_id)
            if party is None or party.business_id != sale.business_id or not party.is_customer:
                raise NotFoundError("Customer not found.")
        sale.customer_fk = customer_id
        for line in sale.lines:
            if line.override_reason is not None:
                continue
            price = self._prices.resolve(
                actor,
                product_id=line.product_fk,
                customer_id=customer_id,
                quantity=line.quantity,
                on=self._today(actor),
            )
            if price is not None:
                line.list_price = price.price
                line.unit_price = price.price
                line.price_source = price.source_label
        self._reprice(actor, sale)
        return sale

    def park(self, actor: AnyActor, sale_id: uuid.UUID, *, label: str | None = None) -> PosSale:
        actor.require("pos.operate")
        sale = self._editable(actor, sale_id)
        if not sale.lines:
            raise ConflictError("An empty cart has nothing to park.")
        if any(t.state == TenderState.PENDING.value for t in sale.tenders):
            raise ConflictError("A sale with a payment awaiting confirmation cannot be parked.")
        sale.state = SaleState.PARKED.value
        sale.parked_label = (label or "").strip() or None
        self._db.flush()
        return sale

    def resume(self, actor: AnyActor, sale_id: uuid.UUID) -> PosSale:
        actor.require("pos.operate")
        sale = self.require_sale(actor, sale_id)
        if sale.state != SaleState.PARKED.value:
            raise ConflictError("Only a parked sale is resumed.")
        shift = self._open_shift_of(actor)
        current = self._open_sales(shift)
        if any(open_sale.lines for open_sale in current):
            raise ConflictError("Park or complete the sale in progress before resuming another.")
        for empty in current:
            self._db.delete(empty)
        sale.shift_fk = shift.id
        sale.state = SaleState.OPEN.value
        self._db.flush()
        return sale

    def parked(self, actor: AnyActor) -> list[PosSale]:
        actor.require("pos.operate")
        return list(
            self._db.scalars(
                select(PosSale)
                .where(
                    PosSale.business_id == actor.business_id,
                    PosSale.state == SaleState.PARKED.value,
                )
                .order_by(PosSale.updated_at.desc())
            ).all()
        )

    def void_open(self, actor: AnyActor, sale_id: uuid.UUID, *, reason: str) -> PosSale:
        """Abandon a cart before completion. Nothing has been posted, so nothing is reversed."""
        actor.require("pos.operate")
        sale = self.require_sale(actor, sale_id)
        if sale.state not in (SaleState.OPEN.value, SaleState.PARKED.value):
            raise ConflictError(
                "A completed sale is reversed by a refund or a void, not abandoned."
            )
        if any(
            t.state == TenderState.CONFIRMED.value and t.method != TenderMethod.CASH.value
            for t in sale.tenders
        ):
            raise ConflictError(
                "A confirmed electronic payment is on this sale. Complete it, or refund the "
                "payment through the provider before abandoning it."
            )
        if not reason.strip():
            raise ValidationError("An abandoned sale records why.", field="reason")
        sale.state = SaleState.VOIDED.value
        sale.void_reason = reason.strip()
        self._db.flush()
        return sale

    # -- tenders -----------------------------------------------------------------------

    def tender(
        self,
        actor: AnyActor,
        sale_id: uuid.UUID,
        *,
        method: TenderMethod | str,
        amount: Decimal | None = None,
        cash_received: Decimal | None = None,
        reference: str | None = None,
    ) -> PosSale:
        """Take a payment. Cash and on-account are confirmed at once; Mobile Money, card and
        transfer wait for confirmation against the provider's receipt (ADR-0009)."""
        actor.require("pos.operate")
        sale = self._editable(actor, sale_id)
        if not sale.lines:
            raise ConflictError("The cart is empty.")
        chosen = TenderMethod(method)
        precision = self._precision(actor)
        due = outstanding(sale.total, self._views(sale))
        if due <= ZERO:
            raise ConflictError("The sale is already fully paid.")
        tender = PosTender(
            business_id=sale.business_id,
            sale_fk=sale.id,
            method=chosen.value,
            idempotency_key=f"tender:{new_id()}",
            provider_reference=(reference or "").strip() or None,
        )
        if chosen is TenderMethod.CASH:
            received = present(
                money(cash_received if cash_received is not None else amount or due), precision
            )
            applied, change = cash_change(due, received)
            tender.amount = applied
            tender.cash_received = received
            tender.change_given = change
        else:
            value = present(money(amount if amount is not None else due), precision)
            if value <= ZERO or value > due:
                raise ValidationError(
                    f"{chosen.label} tender lies between nothing and the {due} still due.",
                    field="amount",
                )
            tender.amount = value
            if chosen is TenderMethod.ON_ACCOUNT and sale.customer_fk is None:
                raise ConflictError("An account sale names a registered customer.")
        if chosen.confirmed_on_entry:
            tender.state = TenderState.CONFIRMED.value
            tender.confirmed_at = self._clock.now()
            tender.confirmed_by_id = actor.user_id
            tender.confirmation_source = "cashier"
        else:
            tender.state = TenderState.PENDING.value
        sale.tenders.append(tender)
        self._db.flush()
        return sale

    def confirm_tender(
        self,
        actor: AnyActor,
        tender_id: uuid.UUID,
        *,
        provider_reference: str,
    ) -> PosSale:
        """Confirm an electronic payment against the provider's own receipt."""
        actor.require("pos.operate")
        tender = self._tender(actor, tender_id)
        reference = (provider_reference or "").strip()
        if not reference:
            raise ValidationError(
                "Record the provider's receipt or transaction reference that confirms it.",
                field="provider_reference",
            )
        return self._settle(
            actor, tender, TenderState.CONFIRMED, reference=reference, source="cashier"
        )

    def fail_tender(self, actor: AnyActor, tender_id: uuid.UUID, *, reason: str) -> PosSale:
        actor.require("pos.operate")
        tender = self._tender(actor, tender_id)
        if not reason.strip():
            raise ValidationError("A failed payment records why.", field="reason")
        return self._settle(
            actor, tender, TenderState.FAILED, reason=reason.strip(), source="cashier"
        )

    def provider_update(self, actor: AnyActor, status: ProviderStatus) -> PosSale | None:
        """A provider's callback or query result, already mapped by its adapter.

        Idempotent by the tender's key: a duplicated callback finds the tender already in the
        state it reports, changes nothing, and posts nothing.
        """
        tender = self._db.scalar(
            select(PosTender).where(
                PosTender.business_id == actor.business_id,
                PosTender.idempotency_key == status.idempotency_key,
            )
        )
        if tender is None:
            raise NotFoundError("No payment carries that reference.")
        if TenderState(tender.state) is status.state or status.state is TenderState.PENDING:
            return tender.sale
        return self._settle(
            actor,
            tender,
            status.state,
            reference=status.provider_reference,
            reason=status.reason,
            source="callback",
        )

    def _settle(
        self,
        actor: AnyActor,
        tender: PosTender,
        target: TenderState,
        *,
        reference: str | None = None,
        reason: str | None = None,
        source: str,
    ) -> PosSale:
        current = TenderState(tender.state)
        if current is target:
            return tender.sale
        assert_tender_transition(current, target)
        tender.state = target.value
        if target is TenderState.CONFIRMED:
            tender.confirmed_at = self._clock.now()
            tender.confirmed_by_id = actor.user_id
            tender.confirmation_source = source
            tender.provider_reference = reference or tender.provider_reference
        else:
            tender.failure_reason = reason
        self._db.flush()
        self._audit.record(
            actor,
            f"pos_tender_{target.value}",
            entity_type="pos_tender",
            entity_id=tender.id,
            reason=reason,
            after={"method": tender.method, "amount": str(tender.amount), "source": source},
        )
        return tender.sale

    # -- completion ----------------------------------------------------------------------

    def complete(self, actor: AnyActor, sale_id: uuid.UUID) -> PosSale:
        """Post the sale: tender, revenue, tax and the cost of the goods, in one entry.

        Refused while any payment awaits confirmation: nothing is posted, no stock moves,
        no receipt is issued. Asking again for a completed sale returns it unchanged.
        """
        actor.require("pos.operate")
        sale = self.require_sale(actor, sale_id)
        if sale.state == SaleState.COMPLETED.value:
            return sale
        if sale.state != SaleState.OPEN.value:
            raise ConflictError(f"This sale is {sale.state.replace('_', ' ')}.")
        if not sale.lines:
            raise ConflictError("The cart is empty.")
        self._reprice(actor, sale)
        views = self._views(sale)
        assert_completable(sale.total, views)
        shift = self._db.get(PosShift, sale.shift_fk)
        assert shift is not None
        today = self._today(actor)
        sale.number = (
            OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "pos_sale", branch_id=actor.branch_id)
            .number
        )
        engine = self._inventory.engine
        operation = engine.begin(
            actor,
            MovementType.ISSUE,
            occurred_on=today,
            source_type="pos_sale",
            source_id=sale.id,
            reference=sale.number,
        )
        for line in sale.lines:
            product = self._db.get(Product, line.product_fk)
            if not holds_stock(product):
                continue
            assert product is not None
            operation.take(
                product,
                shift.warehouse_fk,
                StockState.USABLE,
                line.quantity,
                batch_id=line.batch_fk,
                serials=(line.serials or "").splitlines(),
                exclude_expired=True,
                counter=COST_OF_GOODS_SOLD,
                movement_type=MovementType.ISSUE,
            )
        for tender in sale.tenders:
            if tender.state != TenderState.CONFIRMED.value:
                continue
            method = TenderMethod(tender.method)
            operation.extra.append(
                EntryLine(
                    account_code=method.account,
                    side=Side.DEBIT,
                    amount=tender.amount,
                    narration=f"{method.label} {tender.provider_reference or ''}".strip(),
                    party_id=sale.customer_fk if method is TenderMethod.ON_ACCOUNT else None,
                    subledger=SubledgerKind.CUSTOMER if method is TenderMethod.ON_ACCOUNT else None,
                )
            )
        if sale.net_total > ZERO:
            operation.extra.append(
                EntryLine(
                    account_code=POS_REVENUE,
                    side=Side.CREDIT,
                    amount=sale.net_total,
                    narration="Register sales, net of discount",
                )
            )
        operation.extra.extend(self._tax_lines(sale, Side.CREDIT))
        entry = operation.finish(
            f"Register sale {sale.number}",
            source=JournalSource.POINT_OF_SALE,
            idempotency_key=f"pos_sale:{sale.id}",
        )
        self._record_tax(actor, sale, today)
        sale.journal_entry_fk = entry.id if entry is not None else None
        sale.stock_operation_id = operation.id
        sale.state = SaleState.COMPLETED.value
        sale.completed_at = self._clock.now()
        sale.completed_on = today
        sale.cashier_id = actor.user_id
        on_account = money(
            sum(
                (
                    t.amount
                    for t in sale.tenders
                    if t.method == TenderMethod.ON_ACCOUNT.value
                    and t.state == TenderState.CONFIRMED.value
                ),
                ZERO,
            )
        )
        if on_account > ZERO:
            self._db.flush()
            ReceivablesService(self._db, clock=self._clock).record_pos_sale(sale, on_account)
        self._db.flush()
        self._audit.record(
            actor,
            "pos_sale_completed",
            entity_type="pos_sale",
            entity_id=sale.id,
            entity_label=sale.number,
            after={"total": str(sale.total), "tenders": len(views)},
        )
        return sale

    def suggested_cash(self, actor: AnyActor, sale: PosSale) -> list[Decimal]:
        """One-press cash amounts: the exact figure, then the notes above it."""
        return suggested_tenders(self.due(sale), precision=self._precision(actor))

    def due(self, sale: PosSale) -> Decimal:
        return outstanding(sale.total, self._views(sale))

    # -- refunds -----------------------------------------------------------------------

    def refund(
        self, actor: AnyActor, draft: RefundDraft, *, authoriser: AnyActor | None = None
    ) -> PosRefund:
        """Return goods against the sale they were sold on, under authorisation.

        Reverses the revenue and the tax of what comes back, returns the goods at the cost
        they left at, and pays the money back the way it came unless told otherwise.
        """
        actor.require("pos.operate")
        approver = authoriser or actor
        approver.require("pos.refund")
        sale = self.require_sale(actor, draft.sale_id)
        if sale.state not in (SaleState.COMPLETED.value, SaleState.PARTIALLY_REFUNDED.value):
            raise ConflictError("A refund is made against a completed sale.")
        reason = draft.reason.strip()
        if not reason:
            raise ValidationError("A refund records why.", field="reason")
        kind = RefundKind(draft.kind)
        shift = self._open_shift_of(actor)
        if kind is RefundKind.VOID and sale.shift_fk != shift.id:
            raise ConflictError(
                "A sale is voided only in the shift that made it. Refund it instead."
            )
        lines = {line.id: line for line in sale.lines}
        chosen: list[tuple[PosSaleLine, Decimal]] = []
        for item in draft.lines:
            line = lines.get(item.sale_line_id)
            if line is None:
                raise NotFoundError("That line is not on this sale.")
            quantity = money(item.quantity)
            if quantity <= ZERO:
                continue
            returnable = money(line.quantity - line.quantity_refunded)
            if quantity > returnable:
                raise ConflictError(
                    f"{line.description}: {plain(returnable)} remains returnable on this sale."
                )
            chosen.append((line, quantity))
        if not chosen:
            raise ValidationError("A refund returns at least one item.", field="lines")
        method = TenderMethod(draft.method) if draft.method else self._primary_method(sale)
        if method is TenderMethod.ON_ACCOUNT and sale.customer_fk is None:
            raise ConflictError("An account refund names the customer's account.")
        precision = self._precision(actor)
        today = self._today(actor)
        refund = PosRefund(
            business_id=sale.business_id,
            number=OrganisationService(self._db, clock=self._clock)
            .allocate_number(actor, "credit_note", branch_id=actor.branch_id)
            .number,
            kind=kind.value,
            sale_fk=sale.id,
            shift_fk=shift.id,
            reason=reason,
            method=method.value,
            restock=draft.restock,
            requested_by_id=actor.user_id,
            authorised_by_id=approver.user_id or actor.user_id,
            refunded_on=today,
        )
        tax_by_rule: dict[str, Decimal] = {}
        for line, quantity in chosen:
            returned_net, returned_tax = self._returned(line)
            if quantity == money(line.quantity - line.quantity_refunded):
                # The last of the line: exactly what remains, so the line nets to nothing.
                net = money(line.net - returned_net)
                tax = money(line.tax - returned_tax)
            else:
                net = _share(line.net, quantity, line.quantity, precision)
                tax = _share(line.tax, quantity, line.quantity, precision)
            breakdown = sorted((line.tax_breakdown or {}).items())
            parts = allocate(tax, [Decimal(str(amount)) for _, amount in breakdown], precision)
            for (code, _), part in zip(breakdown, parts, strict=True):
                tax_by_rule[code] = money(tax_by_rule.get(code, ZERO) + part)
            refund.lines.append(
                PosRefundLine(
                    business_id=sale.business_id,
                    sale_line_fk=line.id,
                    quantity=quantity,
                    net=net,
                    tax=tax,
                )
            )
            line.quantity_refunded = money(line.quantity_refunded + quantity)
        refund.net_total = money(sum((item.net for item in refund.lines), ZERO))
        refund.tax_total = money(sum((item.tax for item in refund.lines), ZERO))
        refund.total = money(refund.net_total + refund.tax_total)
        if method is TenderMethod.CASH and refund.total > self.drawer(shift).expected:
            raise ConflictError("The drawer does not hold enough cash for this refund.")
        self._db.add(refund)
        self._db.flush()

        engine = self._inventory.engine
        operation = engine.begin(
            actor,
            MovementType.CUSTOMER_RETURN,
            occurred_on=today,
            source_type="pos_refund",
            source_id=refund.id,
            reference=refund.number,
            reason=reason,
        )
        sale_shift = self._db.get(PosShift, sale.shift_fk)
        assert sale_shift is not None
        for line, quantity in chosen:
            product = self._db.get(Product, line.product_fk)
            if not holds_stock(product):
                continue
            assert product is not None
            value = self._issued_cost(sale, line, quantity)
            operation.add(
                product,
                sale_shift.warehouse_fk,
                StockState.USABLE if draft.restock else StockState.QUARANTINE,
                quantity,
                value,
                batch=self._issued_batch(sale, line),
                serials=(line.serials or "").splitlines()[: int(quantity)] if line.serials else (),
                counter=COST_OF_GOODS_SOLD,
                movement_type=MovementType.CUSTOMER_RETURN,
            )
        if refund.net_total > ZERO:
            operation.extra.append(
                EntryLine(
                    account_code=SALES_RETURNS,
                    side=Side.DEBIT,
                    amount=refund.net_total,
                    narration=f"Returned against {sale.number}",
                )
            )
        operation.extra.extend(self._tax_lines_for(sale, tax_by_rule, Side.DEBIT))
        operation.extra.append(
            EntryLine(
                account_code=method.account,
                side=Side.CREDIT,
                amount=refund.total,
                narration=f"{method.label} refund {refund.number}",
                party_id=sale.customer_fk if method is TenderMethod.ON_ACCOUNT else None,
                subledger=SubledgerKind.CUSTOMER if method is TenderMethod.ON_ACCOUNT else None,
            )
        )
        entry = operation.finish(
            f"{kind.value.capitalize()} {refund.number} against {sale.number}: {reason}",
            source=JournalSource.POINT_OF_SALE,
            idempotency_key=f"pos_refund:{refund.id}",
        )
        refund.journal_entry_fk = entry.id if entry is not None else None
        refund.stock_operation_id = operation.id
        sale.refunded_total = money(sale.refunded_total + refund.total)
        if method is TenderMethod.ON_ACCOUNT:
            self._credit_account_sale(sale, refund.total)
        every = all(line.quantity_refunded >= line.quantity for line in sale.lines)
        if kind is RefundKind.VOID and every:
            sale.state = SaleState.VOIDED.value
            sale.void_reason = reason
        else:
            sale.state = SaleState.REFUNDED.value if every else SaleState.PARTIALLY_REFUNDED.value
        self._db.flush()
        self._audit.record(
            actor,
            f"pos_{kind.value}",
            entity_type="pos_sale",
            entity_id=sale.id,
            entity_label=sale.number,
            reason=reason,
            after={
                "refund": refund.number,
                "total": str(refund.total),
                "authorised_by": str(refund.authorised_by_id),
                "method": method.value,
            },
        )
        return refund

    def void(
        self,
        actor: AnyActor,
        sale_id: uuid.UUID,
        *,
        reason: str,
        authoriser: AnyActor | None = None,
    ) -> PosRefund:
        """Reverse a completed sale in full, in its own shift."""
        sale = self.require_sale(actor, sale_id)
        return self.refund(
            actor,
            RefundDraft(
                sale_id=sale.id,
                lines=[
                    RefundLineDraft(
                        sale_line_id=line.id, quantity=line.quantity - line.quantity_refunded
                    )
                    for line in sale.lines
                    if line.quantity > line.quantity_refunded
                ],
                reason=reason,
                kind=RefundKind.VOID,
            ),
            authoriser=authoriser,
        )

    def refunds_of(self, actor: AnyActor, sale_id: uuid.UUID) -> list[PosRefund]:
        actor.require("pos.operate")
        return list(
            self._db.scalars(
                select(PosRefund)
                .where(PosRefund.business_id == actor.business_id, PosRefund.sale_fk == sale_id)
                .order_by(PosRefund.created_at)
            ).all()
        )

    def sales(
        self, actor: AnyActor, *, shift_id: uuid.UUID | None = None, limit: int = 50
    ) -> list[PosSale]:
        actor.require("pos.operate")
        query = select(PosSale).where(
            PosSale.business_id == actor.business_id, PosSale.completed_at.is_not(None)
        )
        if shift_id is not None:
            query = query.where(PosSale.shift_fk == shift_id)
        return list(
            self._db.scalars(
                query.order_by(PosSale.completed_at.desc()).limit(max(min(limit, 200), 1))
            ).all()
        )

    def find_sale(self, actor: AnyActor, number: str) -> PosSale:
        actor.require("pos.operate")
        sale = self._db.scalar(
            select(PosSale).where(
                PosSale.business_id == actor.business_id, PosSale.number == number.strip()
            )
        )
        if sale is None:
            raise NotFoundError(f"No register sale is numbered {number.strip()}.")
        return sale

    # -- the receipt -------------------------------------------------------------------

    def receipt_context(self, actor: AnyActor, sale: PosSale) -> DocumentContext:
        """The receipt as the documents layer renders it: numbered with the sale itself, so
        the customer holds one number for the sale, the receipt and any later refund."""
        if sale.number is None or sale.completed_on is None:
            raise ConflictError("A receipt is issued once the sale is complete.")
        business = self._db.get(Business, sale.business_id)
        records = DocumentRecordService(self._db, clock=self._clock)
        records.seed(sale.business_id)
        lines = []
        for line in sale.lines:
            product = self._db.get(Product, line.product_fk)
            lines.append(
                Line(
                    sku=product.sku if product else "",
                    name=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    discount_rate=line.discount_rate,
                    line_total=line.net,
                )
            )
        confirmed = [t for t in sale.tenders if t.state == TenderState.CONFIRMED.value]
        received = money(
            sum(
                (t.cash_received if t.cash_received is not None else t.amount for t in confirmed),
                ZERO,
            )
        )
        change = money(sum((t.change_given or ZERO for t in confirmed), ZERO))
        terms = [
            (
                TenderMethod(t.method).label
                + (f" {t.provider_reference}" if t.provider_reference else ""),
                str(
                    present(
                        t.cash_received if t.cash_received is not None else t.amount,
                        self._precision(actor),
                    )
                ),
            )
            for t in confirmed
        ]
        cashier = self._db.get(User, sale.cashier_id) if sale.cashier_id else None
        return DocumentContext(
            kind=DocumentKind.RECEIPT,
            number=sale.number,
            issued_on=sale.completed_on,
            issuer=records.issuer_for(sale.business_id),
            currency_code=sale.currency_code,
            currency_symbol=business.currency_symbol if business else "GH₵",
            currency_precision=business.currency_precision if business else 2,
            bill_to=DocumentSourceService(self._db, clock=self._clock).party_block(sale.customer_fk)
            if sale.customer_fk
            else None,
            prepared_by=cashier.full_name if cashier else None,
            lines=lines,
            totals=Totals(
                total_units=money(sum((line.quantity for line in sale.lines), ZERO)),
                subtotal=sale.net_total,
                tax=sale.tax_total,
                grand_total=sale.total,
                paid=received,
                balance_due=change,
                components=tuple(self._tax_components(sale)),
            ),
            terms=terms,
            footer_note="Goods sold are returnable against this receipt.",
        )

    def record_receipt(self, sale: PosSale, issued_id: uuid.UUID) -> None:
        sale.receipt_fk = issued_id
        self._db.flush()

    def _tax_components(self, sale: PosSale) -> list[TaxComponent]:
        """Each charged tax with its rate, its base and the amount recorded on the lines."""
        evaluation = TaxService(self._db, clock=self._clock).evaluate(
            self._tax_actor(sale),
            [self._taxable(line) for line in sale.lines],
            self._tax_context(sale),
        )
        named: dict[str, tuple[str, Decimal]] = {}
        bases: dict[str, Decimal] = {}
        for assessed in evaluation.lines:
            for item in assessed.assessments:
                if item.is_charged:
                    named.setdefault(item.rule_code, (item.rule_name, item.rate))
                    bases[item.rule_code] = money(bases.get(item.rule_code, ZERO) + item.base)
        amounts: dict[str, Decimal] = {}
        for line in sale.lines:
            for code, amount in (line.tax_breakdown or {}).items():
                amounts[code] = money(amounts.get(code, ZERO) + Decimal(str(amount)))
        return [
            TaxComponent(
                name=named.get(code, (code, ZERO))[0],
                rate=named.get(code, (code, ZERO))[1],
                basis=bases.get(code, ZERO),
                amount=amount,
            )
            for code, amount in sorted(amounts.items())
        ]

    # -- labels for the interface ------------------------------------------------------

    def skus(self, sale: PosSale) -> dict[uuid.UUID, str]:
        ids = {line.product_fk for line in sale.lines}
        if not ids:
            return {}
        return {
            product.id: product.sku
            for product in self._db.scalars(select(Product).where(Product.id.in_(ids))).all()
        }

    def user_name(self, user_id: uuid.UUID | None) -> str | None:
        user = self._db.get(User, user_id) if user_id else None
        return user.full_name if user else None

    def change_given(self, sale: PosSale) -> Decimal:
        return money(
            sum(
                (
                    t.change_given or ZERO
                    for t in sale.tenders
                    if t.state == TenderState.CONFIRMED.value
                ),
                ZERO,
            )
        )

    # -- internals ---------------------------------------------------------------------

    def _reprice(self, actor: AnyActor, sale: PosSale) -> None:
        """Every figure on the sale, from the lines up: gross, discount, net, tax, total."""
        precision = self._precision(actor)
        for line in sale.lines:
            line.gross = present(multiply(line.unit_price, line.quantity), precision)
            line.discount = present(multiply(line.gross, line.discount_rate), precision)
            line.net = money(line.gross - line.discount)
        evaluation = TaxService(self._db, clock=self._clock).evaluate(
            self._tax_actor(sale),
            [self._taxable(line) for line in sale.lines],
            self._tax_context(sale),
        )
        by_line = {item.line_reference: item for item in evaluation.lines}
        for line in sale.lines:
            assessed = by_line.get(str(line.id))
            breakdown: dict[str, str] = {}
            tax = ZERO
            if assessed is not None:
                for item in assessed.assessments:
                    if item.is_charged:
                        amount = present(item.amount, precision)
                        breakdown[item.rule_code] = str(amount)
                        tax = money(tax + amount)
            line.tax = tax
            line.tax_breakdown = breakdown or None
            line.total = money(line.net + line.tax)
        sale.gross_total = money(sum((line.gross for line in sale.lines), ZERO))
        sale.discount_total = money(sum((line.discount for line in sale.lines), ZERO))
        sale.net_total = money(sum((line.net for line in sale.lines), ZERO))
        sale.tax_total = money(sum((line.tax for line in sale.lines), ZERO))
        sale.total = money(sale.net_total + sale.tax_total)
        self._db.flush()

    def _taxable(self, line: PosSaleLine) -> TaxableLine:
        if not line.id:
            line.id = new_id()
        product = self._db.get(Product, line.product_fk)
        return TaxableLine(
            reference=str(line.id),
            net=line.net,
            quantity=line.quantity,
            product_tax_code=product.tax_code if product else None,
            product_category=str(product.category_fk) if product and product.category_fk else None,
            item_class=product.item_class if product else None,
            product_id=line.product_fk,
        )

    def _tax_context(self, sale: PosSale) -> TaxContext:
        party = self._db.get(Party, sale.customer_fk) if sale.customer_fk else None
        return TaxContext(
            transaction_kind="pos_sale",
            on=self._today_for(sale.business_id),
            direction="sales",
            party_id=sale.customer_fk,
            party_tax_treatment=getattr(party, "tax_treatment", None) if party else None,
            party_is_withholding_agent=bool(getattr(party, "is_withholding_agent", False))
            if party
            else False,
            party_tax_identifier=party.tax_identifier if party else None,
            currency_code=sale.currency_code,
        )

    def _tax_actor(self, sale: PosSale) -> SystemActor:
        return SystemActor(business_id=sale.business_id, reason="Register tax evaluation")

    def _record_tax(self, actor: AnyActor, sale: PosSale, on: date) -> None:
        service = TaxService(self._db, clock=self._clock)
        evaluation = service.evaluate(
            self._tax_actor(sale),
            [self._taxable(line) for line in sale.lines],
            self._tax_context(sale),
        )
        service.record(self._tax_actor(sale), evaluation, source_type="pos_sale", source_id=sale.id)
        del actor, on

    def _tax_lines(self, sale: PosSale, side: Side) -> list[EntryLine]:
        totals: dict[str, Decimal] = {}
        for line in sale.lines:
            for code, amount in (line.tax_breakdown or {}).items():
                totals[code] = money(totals.get(code, ZERO) + Decimal(str(amount)))
        return self._tax_lines_for(sale, totals, side)

    def _credit_account_sale(self, sale: PosSale, amount: Decimal) -> None:
        """A refund to account reduces what the account sale still owes."""
        invoice = self._db.scalar(select(SalesInvoice).where(SalesInvoice.pos_sale_fk == sale.id))
        if invoice is None:
            return
        invoice.amount_credited = money(
            invoice.amount_credited + min(amount, money(invoice.outstanding))
        )
        settled = money(
            invoice.amount_received + invoice.amount_credited + invoice.amount_written_off
        )
        invoice.state = invoice_state(invoice.total, settled, invoice.amount_written_off).value

    def _tax_lines_for(
        self, sale: PosSale, totals: dict[str, Decimal], side: Side
    ) -> list[EntryLine]:
        return tax_entry_lines(self._db, sale.business_id, totals, side)

    def _returned(self, line: PosSaleLine) -> tuple[Decimal, Decimal]:
        """Net and tax already refunded against a sale line."""
        net, tax = self._db.execute(
            select(
                func.coalesce(func.sum(PosRefundLine.net), 0),
                func.coalesce(func.sum(PosRefundLine.tax), 0),
            ).where(PosRefundLine.sale_line_fk == line.id)
        ).one()
        return money(Decimal(str(net))), money(Decimal(str(tax)))

    def _issued_movements(self, sale: PosSale, line: PosSaleLine) -> list[StockMovement]:
        return list(
            self._db.scalars(
                select(StockMovement).where(
                    StockMovement.operation_id == sale.stock_operation_id,
                    StockMovement.product_fk == line.product_fk,
                    StockMovement.state == StockState.USABLE.value,
                    StockMovement.quantity < 0,
                )
            ).all()
        )

    def _issued_cost(self, sale: PosSale, line: PosSaleLine, quantity: Decimal) -> Decimal:
        """The cost the goods left at, for the share coming back."""
        movements = self._issued_movements(sale, line)
        out_quantity = -sum((m.quantity for m in movements), ZERO)
        out_value = -sum((m.value for m in movements), ZERO)
        if out_quantity <= ZERO:
            return ZERO
        if quantity == out_quantity:
            return money(out_value)
        return money(out_value * quantity / out_quantity)

    def _issued_batch(self, sale: PosSale, line: PosSaleLine) -> Batch | None:
        batches = {m.batch_fk for m in self._issued_movements(sale, line)} - {None}
        if len(batches) == 1:
            return self._db.get(Batch, next(iter(batches)))
        return None

    def _primary_method(self, sale: PosSale) -> TenderMethod:
        confirmed = [t for t in sale.tenders if t.state == TenderState.CONFIRMED.value]
        if not confirmed:
            return TenderMethod.CASH
        largest = max(confirmed, key=lambda tender: tender.amount)
        return TenderMethod(largest.method)

    def _views(self, sale: PosSale) -> list[TenderView]:
        return [
            TenderView(
                method=TenderMethod(tender.method),
                state=TenderState(tender.state),
                amount=tender.amount,
            )
            for tender in sale.tenders
        ]

    def _pending_tenders(self, shift: PosShift) -> list[PosTender]:
        return list(
            self._db.scalars(
                select(PosTender)
                .join(PosSale, PosSale.id == PosTender.sale_fk)
                .where(PosSale.shift_fk == shift.id, PosTender.state == TenderState.PENDING.value)
            ).all()
        )

    def _open_sales(self, shift: PosShift) -> list[PosSale]:
        return list(
            self._db.scalars(
                select(PosSale)
                .where(PosSale.shift_fk == shift.id, PosSale.state == SaleState.OPEN.value)
                .order_by(PosSale.created_at.desc())
            ).all()
        )

    def _open_shift_of(self, actor: AnyActor) -> PosShift:
        shift = self.current_shift(actor)
        if shift is None:
            raise ConflictError("Open a shift before using the register.")
        return shift

    def _editable(self, actor: AnyActor, sale_id: uuid.UUID) -> PosSale:
        sale = self.require_sale(actor, sale_id)
        if sale.state != SaleState.OPEN.value:
            raise ConflictError(
                f"This sale is {sale.state.replace('_', ' ')} and cannot be changed."
            )
        return sale

    def _line(self, sale: PosSale, line_id: uuid.UUID) -> PosSaleLine:
        line = next((item for item in sale.lines if item.id == line_id), None)
        if line is None:
            raise NotFoundError("That line is not on this sale.")
        return line

    def _tender(self, actor: AnyActor, tender_id: uuid.UUID) -> PosTender:
        tender = self._db.get(PosTender, tender_id)
        if tender is None or tender.business_id != actor.business_id:
            raise NotFoundError("Payment not found.")
        return tender

    def _post(
        self,
        actor: AnyActor,
        *,
        narration: str,
        lines: list[EntryLine],
        reference: str,
        source_id: uuid.UUID,
        key: str,
    ) -> JournalEntry:
        business = self._db.get(Business, actor.business_id)
        return (
            JournalService(self._db, clock=self._clock)
            .post(
                actor,
                JournalDraft(
                    posting_date=self._today(actor),
                    narration=narration,
                    lines=lines,
                    source=JournalSource.POINT_OF_SALE,
                    source_reference=reference,
                    source_id=source_id,
                    idempotency_key=key,
                    currency_code=business.base_currency if business else None,
                    branch_id=actor.branch_id,
                ),
            )
            .entry
        )

    def _precision(self, actor: AnyActor) -> int:
        business = self._db.get(Business, actor.business_id)
        return business.currency_precision if business else 2

    def _today(self, actor: AnyActor) -> date:
        return self._today_for(_business_of(actor))

    def _today_for(self, business_id: uuid.UUID) -> date:
        business = self._db.get(Business, business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)


def _share(value: Decimal, quantity: Decimal, of: Decimal, precision: int) -> Decimal:
    """The part of a line's figure that a returned quantity carries."""
    return present(money(multiply(value, quantity) / of), precision)


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
