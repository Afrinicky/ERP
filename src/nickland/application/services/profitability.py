"""Profitability and margin erosion. PRD §29.8.

Contribution is measured on what was realised: revenue invoiced or rung up, less the
actual cost of the goods (from the stock layers consumed or the supplier's invoice) and
the commission actually earned. A line whose actual cost is not yet known is not given an
estimated one; it is counted apart as awaiting cost, so an estimate never passes for a
result.

Costs no line carries — freight and non-recoverable tax posted straight to cost of sales,
and operating overheads — may be allocated across the rows by share of revenue. The
allocation is labelled as such and shown in its own columns.

Margin erosion compares each order line with its quotation, on the quantity invoiced, and
attributes the whole difference to named causes: price, discount, supplier cost, freight,
non-recoverable tax, returns and commission. The causes sum exactly to the difference.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.stock import holds_stock
from nickland.domain.errors import ConflictError, ValidationError
from nickland.domain.finance import InvoiceOrigin
from nickland.domain.money import ZERO, money
from nickland.domain.planning import (
    Basis,
    ErosionCause,
    ErosionInputs,
    ProfitDimension,
    cents,
    erosion,
)
from nickland.domain.pricing import CommissionState
from nickland.persistence.catalogue_models import Party, Product, ProductCategory
from nickland.persistence.dispatch_models import Shipment, ShipmentLine
from nickland.persistence.finance_models import (
    CustomerCreditNote,
    SalesInvoice,
    SalesInvoiceLine,
)
from nickland.persistence.inventory_models import LandedCharge, StockMovement
from nickland.persistence.ledger_models import Account, AccountGroup, JournalLine
from nickland.persistence.models import Branch, User
from nickland.persistence.order_models import (
    GoodsReceiptLine,
    PurchaseOrderLine,
    SalesOrder,
    SalesOrderLine,
    SupplierInvoiceLine,
)
from nickland.persistence.pos_models import PosSale, PosSaleLine, PosShift
from nickland.persistence.pricing_models import (
    CommissionAccrual,
    Quotation,
    QuotationLine,
    QuotationLineCost,
    QuotationLineSource,
)

LANDED_CATEGORIES = frozenset(
    {"freight", "transport", "clearing", "handling", "import_duty", "insurance", "packaging"}
)
DIRECT_UNTRACED = frozenset({"50100", "50200"})
"""Non-recoverable tax on purchases and freight on sales, posted to cost of sales with no
line to carry them."""


@dataclass(slots=True)
class Fact:
    """One realised sale line."""

    source_type: str
    source_id: uuid.UUID
    reference: str
    on: date
    description: str
    channel: str
    product_id: uuid.UUID | None
    customer_id: uuid.UUID | None
    order_id: uuid.UUID | None
    order_label: str
    supplier: str
    branch_id: uuid.UUID | None
    person_id: uuid.UUID | None
    affiliate_id: uuid.UUID | None
    terminal: str | None
    quantity: Decimal
    revenue: Decimal
    cost: Decimal | None
    commission: Decimal


@dataclass(slots=True)
class ProfitRow:
    key: str
    label: str
    revenue: Decimal = ZERO
    cost: Decimal = ZERO
    commission: Decimal = ZERO
    traced_revenue: Decimal = ZERO
    """Revenue on lines whose actual cost is known."""
    pending_revenue: Decimal = ZERO
    pending_lines: int = 0
    lines: int = 0
    direct: Decimal = ZERO
    overheads: Decimal = ZERO

    @property
    def gross(self) -> Decimal:
        return cents(self.traced_revenue - self.cost)

    @property
    def after_commission(self) -> Decimal:
        return cents(self.gross - self.commission)

    @property
    def after_allocation(self) -> Decimal:
        return cents(self.after_commission - self.direct - self.overheads)

    @property
    def margin(self) -> Decimal | None:
        if self.traced_revenue <= ZERO:
            return None
        return (self.after_commission / self.traced_revenue * 100).quantize(Decimal("0.1"))


@dataclass(frozen=True, slots=True)
class Profitability:
    basis: Basis
    dimension: ProfitDimension
    starts_on: date
    ends_on: date
    rows: list[ProfitRow]
    total: ProfitRow
    allocated: bool
    direct_pool: Decimal
    overhead_pool: Decimal


@dataclass(frozen=True, slots=True)
class ErosionRow:
    order_id: uuid.UUID
    order_number: str
    order_line_id: uuid.UUID
    customer: str
    description: str
    supplier: str
    quantity_quoted: Decimal
    quantity_invoiced: Decimal
    quoted_margin: Decimal
    realised_margin: Decimal
    quoted_cost: Decimal
    actual_cost: Decimal | None
    causes: dict[ErosionCause, Decimal]
    complete: bool
    """False while a cost on the line is still only an estimate."""

    @property
    def difference(self) -> Decimal:
        return cents(self.realised_margin - self.quoted_margin)


@dataclass(frozen=True, slots=True)
class ErosionReport:
    rows: list[ErosionRow]
    totals: dict[ErosionCause, Decimal]
    quoted_margin: Decimal
    realised_margin: Decimal
    pending: int
    bases: dict[str, str] = field(default_factory=dict)


class ProfitabilityService:
    def __init__(self, session: DbSession) -> None:
        self._db = session
        self._names: dict[tuple[str, uuid.UUID], str] = {}

    # -- facts ------------------------------------------------------------------------------

    def facts(self, business_id: uuid.UUID, start: date, end: date) -> list[Fact]:
        return [
            *self._invoice_facts(business_id, start, end),
            *self._register_facts(business_id, start, end),
        ]

    def _invoice_facts(self, business_id: uuid.UUID, start: date, end: date) -> list[Fact]:
        rows = self._db.execute(
            select(SalesInvoiceLine, SalesInvoice)
            .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_fk)
            .where(
                SalesInvoice.business_id == business_id,
                SalesInvoice.origin == InvoiceOrigin.SALES_ORDER.value,
                SalesInvoice.issued_on >= start,
                SalesInvoice.issued_on <= end,
            )
        ).all()
        facts: list[Fact] = []
        costs: dict[uuid.UUID, Decimal | None] = {}
        for line, invoice in rows:
            order_line = (
                self._db.get(SalesOrderLine, line.sales_order_line_fk)
                if line.sales_order_line_fk
                else None
            )
            order = self._db.get(SalesOrder, order_line.order_fk) if order_line else None
            quote_line = (
                self._db.get(QuotationLine, order_line.quotation_line_fk)
                if order_line and order_line.quotation_line_fk
                else None
            )
            quotation = self._db.get(Quotation, quote_line.quotation_fk) if quote_line else None
            unit_cost: Decimal | None = None
            if order_line is not None:
                if order_line.id not in costs:
                    costs[order_line.id] = self.actual_unit_cost(order_line)
                unit_cost = costs[order_line.id]
            credited = sum(
                (
                    money(item.get("net", "0"))
                    for note in self._db.scalars(
                        select(CustomerCreditNote).where(
                            CustomerCreditNote.invoice_fk == invoice.id
                        )
                    )
                    for item in note.lines
                    if item.get("invoice_line_id") == str(line.id)
                ),
                ZERO,
            )
            commission = ZERO
            if quote_line is not None and order_line is not None:
                accrued = self._commission(quote_line.id)
                ordered = order_line.quantity_ordered or line.quantity
                commission = cents(accrued * line.quantity / ordered) if ordered else ZERO
            facts.append(
                Fact(
                    source_type="sales_invoice",
                    source_id=invoice.id,
                    reference=invoice.number,
                    on=invoice.issued_on,
                    description=line.description,
                    channel="quotation",
                    product_id=line.product_fk,
                    customer_id=invoice.customer_fk,
                    order_id=order.id if order else None,
                    order_label=order.number if order else invoice.number,
                    supplier=self._supplier(quote_line),
                    branch_id=invoice.branch_id,
                    person_id=(quotation.issued_by_id if quotation else None)
                    or invoice.issued_by_id,
                    affiliate_id=quotation.affiliate_fk if quotation else None,
                    terminal=None,
                    quantity=line.quantity,
                    revenue=cents(line.net - credited),
                    cost=cents(unit_cost * line.quantity) if unit_cost is not None else None,
                    commission=commission,
                )
            )
        return facts

    def _register_facts(self, business_id: uuid.UUID, start: date, end: date) -> list[Fact]:
        rows = self._db.execute(
            select(PosSaleLine, PosSale, PosShift)
            .join(PosSale, PosSale.id == PosSaleLine.sale_fk)
            .join(PosShift, PosShift.id == PosSale.shift_fk)
            .where(
                PosSale.business_id == business_id,
                PosSale.completed_on.is_not(None),
                PosSale.completed_on >= start,
                PosSale.completed_on <= end,
            )
        ).all()
        unit_costs: dict[tuple[uuid.UUID, uuid.UUID], Decimal | None] = {}
        facts: list[Fact] = []
        for line, sale, shift in rows:
            key = (sale.id, line.product_fk)
            if key not in unit_costs:
                unit_costs[key] = self._issued_unit_cost("pos_sale", [sale.id], line.product_fk)
            kept = line.quantity - line.quantity_refunded
            share = kept / line.quantity if line.quantity else ZERO
            unit = unit_costs[key]
            product = self._db.get(Product, line.product_fk)
            stocked = holds_stock(product)
            facts.append(
                Fact(
                    source_type="pos_sale",
                    source_id=sale.id,
                    reference=sale.number or "Register sale",
                    on=sale.completed_on or start,
                    description=line.description,
                    channel="register",
                    product_id=line.product_fk,
                    customer_id=sale.customer_fk,
                    order_id=sale.id,
                    order_label=sale.number or "Register sale",
                    supplier="From stock",
                    branch_id=sale.branch_id,
                    person_id=sale.cashier_id,
                    affiliate_id=None,
                    terminal=shift.register,
                    quantity=kept,
                    revenue=cents(line.net * share),
                    cost=(
                        cents(unit * kept) if unit is not None else (ZERO if not stocked else None)
                    ),
                    commission=ZERO,
                )
            )
        return facts

    # -- profitability ----------------------------------------------------------------------

    def profitability(
        self,
        actor: AnyActor,
        *,
        dimension: str,
        start: date,
        end: date,
        allocate: bool = False,
    ) -> Profitability:
        """Contribution by one dimension, before and after commission and, if asked, after
        costs allocated by share of revenue."""
        actor.require("report.view")
        business_id = _business_of(actor)
        if end < start:
            raise ValidationError("A window ends after it begins.", field="end")
        chosen = ProfitDimension(dimension)
        rows: dict[str, ProfitRow] = {}
        for fact in self.facts(business_id, start, end):
            key, label = self._key(chosen, fact)
            row = rows.setdefault(key, ProfitRow(key=key, label=label))
            self._add(row, fact)
        total = ProfitRow(key="total", label="All")
        for row in rows.values():
            self._merge(total, row)
        direct_pool = overhead_pool = ZERO
        if allocate and total.revenue > ZERO:
            direct_pool = self._posted(business_id, start, end, codes=DIRECT_UNTRACED)
            overhead_pool = self._posted(business_id, start, end, groups={"OPEX"})
            for row in rows.values():
                share = row.revenue / total.revenue
                row.direct = cents(direct_pool * share)
                row.overheads = cents(overhead_pool * share)
            total.direct = cents(sum((row.direct for row in rows.values()), ZERO))
            total.overheads = cents(sum((row.overheads for row in rows.values()), ZERO))
        ranked = sorted(rows.values(), key=lambda row: (-row.after_commission, row.label))
        return Profitability(
            basis=Basis.ACTUAL,
            dimension=chosen,
            starts_on=start,
            ends_on=end,
            rows=ranked,
            total=total,
            allocated=allocate,
            direct_pool=cents(direct_pool),
            overhead_pool=cents(overhead_pool),
        )

    def records(
        self, actor: AnyActor, *, dimension: str, key: str, start: date, end: date
    ) -> list[Fact]:
        """The sale lines behind one row: what a chart segment opens onto."""
        actor.require("report.view")
        business_id = _business_of(actor)
        chosen = ProfitDimension(dimension)
        return [
            fact
            for fact in self.facts(business_id, start, end)
            if self._key(chosen, fact)[0] == key
        ]

    # -- erosion ----------------------------------------------------------------------------

    def erosion(self, actor: AnyActor, *, start: date, end: date) -> ErosionReport:
        """Quoted margin against realised margin, order line by order line, for orders
        invoiced in the window."""
        actor.require("report.view")
        business_id = _business_of(actor)
        order_lines = self._db.scalars(
            select(SalesOrderLine)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_fk)
            .where(
                SalesOrder.business_id == business_id,
                SalesOrderLine.quotation_line_fk.is_not(None),
                SalesOrderLine.id.in_(
                    select(SalesInvoiceLine.sales_order_line_fk)
                    .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_fk)
                    .where(SalesInvoice.issued_on >= start, SalesInvoice.issued_on <= end)
                ),
            )
        ).all()
        rows: list[ErosionRow] = []
        totals: dict[ErosionCause, Decimal] = defaultdict(lambda: ZERO)
        quoted_total = realised_total = ZERO
        pending = 0
        for order_line in order_lines:
            row = self._erosion_row(order_line)
            if row is None:
                continue
            if not row.complete:
                pending += 1
            rows.append(row)
            for cause, amount in row.causes.items():
                totals[cause] += amount
            quoted_total += row.quoted_margin
            realised_total += row.realised_margin
        rows.sort(key=lambda row: row.difference)
        return ErosionReport(
            rows=rows,
            totals={cause: cents(totals[cause]) for cause in ErosionCause if cause in totals},
            quoted_margin=cents(quoted_total),
            realised_margin=cents(realised_total),
            pending=pending,
            bases={
                "quoted": Basis.ESTIMATE.label,
                "realised": Basis.ACTUAL.label,
            },
        )

    def _erosion_row(self, order_line: SalesOrderLine) -> ErosionRow | None:
        quote_line = self._db.get(QuotationLine, order_line.quotation_line_fk)
        order = self._db.get(SalesOrder, order_line.order_fk)
        if quote_line is None or order is None:
            return None
        invoice_lines = list(
            self._db.scalars(
                select(SalesInvoiceLine).where(
                    SalesInvoiceLine.sales_order_line_fk == order_line.id
                )
            )
        )
        q = sum((line.quantity for line in invoice_lines), ZERO)
        if q <= ZERO:
            return None
        revenue = sum((line.net for line in invoice_lines), ZERO)
        credited = ZERO
        for line in invoice_lines:
            for note in self._db.scalars(
                select(CustomerCreditNote).where(CustomerCreditNote.invoice_fk == line.invoice_fk)
            ):
                credited += sum(
                    (
                        money(item.get("net", "0"))
                        for item in note.lines
                        if item.get("invoice_line_id") == str(line.id)
                    ),
                    ZERO,
                )
        quantity = quote_line.quantity or q
        base_unit = (
            (quote_line.unit_cost * quantity - quote_line.supplier_discount_amount) / quantity
            if quantity
            else ZERO
        )
        quoted_freight = sum(
            (
                row.amount
                for row in self._db.scalars(
                    select(QuotationLineCost).where(
                        QuotationLineCost.quotation_line_fk == quote_line.id
                    )
                )
                if row.category in LANDED_CATEGORIES
            ),
            ZERO,
        )
        quoted_tax = quote_line.non_recoverable_tax
        purchased = self._purchased(order_line)
        if purchased is not None:
            unit_actual, freight_actual, tax_actual = purchased
            share = q / quantity
            actual_unit: Decimal | None = unit_actual
            actual_freight: Decimal | None = (
                cents(freight_actual * share) if freight_actual is not None else None
            )
            actual_tax: Decimal | None = cents(tax_actual * share)
            if actual_freight is None:
                actual_freight = ZERO
        else:
            # From stock: the layer cost carries freight and tax already, so the quoted
            # freight and tax join the quoted unit cost and the whole difference is the
            # cost of the goods.
            actual_unit = self.actual_unit_cost(order_line)
            base_unit = (
                base_unit + (quoted_freight + quoted_tax) / quantity if quantity else base_unit
            )
            quoted_freight = quoted_tax = ZERO
            actual_freight = actual_tax = ZERO if actual_unit is not None else None
        ordered_net = quote_line.unit_net_price * order_line.quantity_ordered
        further = max(ZERO, cents(ordered_net - order_line.line_total))
        further = (
            cents(further * q / order_line.quantity_ordered)
            if order_line.quantity_ordered
            else ZERO
        )
        commission_actual = self._commission(quote_line.id)
        inputs = ErosionInputs(
            quantity_quoted=quantity,
            quantity_invoiced=q,
            quoted_unit_net=quote_line.unit_net_price,
            realised_revenue=revenue,
            quoted_unit_cost=base_unit,
            actual_unit_cost=actual_unit,
            quoted_freight=quoted_freight,
            actual_freight=actual_freight,
            quoted_tax=quoted_tax,
            actual_tax=actual_tax,
            quoted_commission=quote_line.commission_amount,
            actual_commission=cents(commission_actual * q / order_line.quantity_ordered)
            if order_line.quantity_ordered
            else ZERO,
            credited=credited,
            further_discount=further,
        )
        causes = erosion(inputs)
        share = q / quantity
        quoted_margin = cents(
            quote_line.unit_net_price * q
            - base_unit * q
            - quoted_freight * share
            - quoted_tax * share
            - quote_line.commission_amount * share
        )
        complete = actual_unit is not None
        cost_actual = (actual_unit if actual_unit is not None else base_unit) * q
        realised_margin = cents(
            revenue
            - credited
            - cost_actual
            - (actual_freight or ZERO)
            - (actual_tax or ZERO)
            - inputs.actual_commission
        )
        residual = cents(realised_margin - quoted_margin - sum(causes.values(), ZERO))
        if residual != ZERO:
            # Rounding to the pesewa across causes; it belongs to the price line.
            causes[ErosionCause.PRICE] = cents(causes[ErosionCause.PRICE] + residual)
        return ErosionRow(
            order_id=order.id,
            order_number=order.number,
            order_line_id=order_line.id,
            customer=self._name("party", order.customer_fk),
            description=order_line.description,
            supplier=self._supplier(quote_line),
            quantity_quoted=quantity,
            quantity_invoiced=q,
            quoted_margin=quoted_margin,
            realised_margin=realised_margin,
            quoted_cost=cents(base_unit * q + quoted_freight * share + quoted_tax * share),
            actual_cost=(
                cents(cost_actual + (actual_freight or ZERO) + (actual_tax or ZERO))
                if complete
                else None
            ),
            causes=dict(causes),
            complete=complete,
        )

    # -- costs ------------------------------------------------------------------------------

    def actual_unit_cost(self, order_line: SalesOrderLine) -> Decimal | None:
        """What a unit actually cost: the stock layers consumed when it was delivered, else
        the supplier's invoiced cost recorded on the order line. None until either exists."""
        shipments = list(
            self._db.scalars(
                select(ShipmentLine.shipment_fk).where(
                    ShipmentLine.sales_order_line_fk == order_line.id
                )
            )
        )
        if shipments and order_line.product_fk is not None:
            unit = self._issued_unit_cost("shipment", shipments, order_line.product_fk)
            if unit is not None:
                return unit
        if order_line.actual_unit_cost is not None:
            return order_line.actual_unit_cost
        purchased = self._purchased(order_line)
        if purchased is not None:
            unit, freight, tax = purchased
            quantity = order_line.quantity_ordered or Decimal(1)
            return unit + ((freight or ZERO) + tax) / quantity
        return None

    def _purchased(
        self, order_line: SalesOrderLine
    ) -> tuple[Decimal, Decimal | None, Decimal] | None:
        """For a line bought in for the order: the supplier's invoiced unit cost, the
        landed charges on its receipts, and the non-recoverable tax invoiced."""
        po_lines = list(
            self._db.scalars(
                select(PurchaseOrderLine).where(
                    PurchaseOrderLine.sales_order_line_fk == order_line.id
                )
            )
        )
        if not po_lines:
            return None
        ids = [line.id for line in po_lines]
        invoiced = list(
            self._db.scalars(
                select(SupplierInvoiceLine).where(
                    SupplierInvoiceLine.purchase_order_line_fk.in_(ids)
                )
            )
        )
        quantity = sum((line.quantity for line in invoiced), ZERO)
        if quantity <= ZERO:
            return None
        unit = sum((line.line_total for line in invoiced), ZERO) / quantity
        tax = sum((line.non_recoverable_tax for line in invoiced), ZERO)
        freight: Decimal | None = None
        receipt_lines = list(
            self._db.scalars(
                select(GoodsReceiptLine).where(GoodsReceiptLine.purchase_order_line_fk.in_(ids))
            )
        )
        for receipt_line in receipt_lines:
            charges = list(
                self._db.scalars(
                    select(LandedCharge).where(LandedCharge.receipt_fk == receipt_line.receipt_fk)
                )
            )
            if not charges:
                continue
            all_lines = list(
                self._db.scalars(
                    select(GoodsReceiptLine).where(
                        GoodsReceiptLine.receipt_fk == receipt_line.receipt_fk
                    )
                )
            )
            value = sum((row.unit_cost * row.quantity_accepted for row in all_lines), ZERO)
            mine = receipt_line.unit_cost * receipt_line.quantity_accepted
            share = mine / value if value > ZERO else ZERO
            freight = (freight or ZERO) + sum((charge.amount for charge in charges), ZERO) * share
        return unit, freight, tax

    def _issued_unit_cost(
        self, source_type: str, source_ids: Iterable[uuid.UUID], product_id: uuid.UUID
    ) -> Decimal | None:
        row = self._db.execute(
            select(func.sum(StockMovement.quantity), func.sum(StockMovement.value)).where(
                StockMovement.source_type == source_type,
                StockMovement.source_id.in_(list(source_ids)),
                StockMovement.product_fk == product_id,
                StockMovement.quantity < 0,
                StockMovement.movement_type.in_(["delivery", "issue"]),
            )
        ).one()
        quantity, value = row
        if not quantity:
            return None
        return abs(Decimal(value or 0)) / abs(Decimal(quantity))

    def _commission(self, quotation_line_id: uuid.UUID) -> Decimal:
        rows = self._db.scalars(
            select(CommissionAccrual).where(
                CommissionAccrual.quotation_line_fk == quotation_line_id,
                CommissionAccrual.state != CommissionState.CANCELLED.value,
            )
        )
        return sum((row.entitlement or row.accrued_amount for row in rows), ZERO)

    def _posted(
        self,
        business_id: uuid.UUID,
        start: date,
        end: date,
        *,
        codes: frozenset[str] | None = None,
        groups: set[str] | None = None,
    ) -> Decimal:
        query = (
            select(func.sum(JournalLine.base_debit), func.sum(JournalLine.base_credit))
            .join(Account, Account.id == JournalLine.account_fk)
            .where(
                JournalLine.business_id == business_id,
                JournalLine.state.in_(["posted", "reversed"]),
                JournalLine.posting_date >= start,
                JournalLine.posting_date <= end,
            )
        )
        if codes is not None:
            query = query.where(Account.code.in_(sorted(codes)))
        if groups is not None:
            query = query.join(AccountGroup, AccountGroup.id == Account.group_fk).where(
                AccountGroup.code.in_(sorted(groups))
            )
        debit, credit = self._db.execute(query).one()
        return money(Decimal(debit or 0) - Decimal(credit or 0))

    # -- dimensions -------------------------------------------------------------------------

    def _key(self, dimension: ProfitDimension, fact: Fact) -> tuple[str, str]:
        if dimension is ProfitDimension.CHANNEL:
            return (
                ("quotation", "Quotation-led sales")
                if fact.channel == "quotation"
                else ("register", "Point of sale")
            )
        if dimension is ProfitDimension.PRODUCT:
            if fact.product_id is None:
                return "none", "Not a catalogue item"
            return str(fact.product_id), self._name("product", fact.product_id)
        if dimension is ProfitDimension.CATEGORY:
            product = self._db.get(Product, fact.product_id) if fact.product_id else None
            if product is None or product.category_fk is None:
                return "none", "Uncategorised"
            return str(product.category_fk), self._name("category", product.category_fk)
        if dimension is ProfitDimension.ORDER:
            return str(fact.order_id or fact.source_id), fact.order_label
        if dimension is ProfitDimension.CUSTOMER:
            if fact.customer_id is None:
                return "walk_in", "Walk-in customers"
            return str(fact.customer_id), self._name("party", fact.customer_id)
        if dimension is ProfitDimension.SUPPLIER:
            return fact.supplier, fact.supplier
        if dimension is ProfitDimension.BRANCH:
            if fact.branch_id is None:
                return "main", "Main business"
            return str(fact.branch_id), self._name("branch", fact.branch_id)
        if dimension is ProfitDimension.SALESPERSON:
            if fact.person_id is None:
                return "none", "Not recorded"
            return str(fact.person_id), self._name("user", fact.person_id)
        if dimension is ProfitDimension.AFFILIATE:
            if fact.affiliate_id is None:
                return "none", "No affiliate"
            return str(fact.affiliate_id), self._name("party", fact.affiliate_id)
        terminal = fact.terminal or "Not a register sale"
        return terminal, terminal

    def _supplier(self, quote_line: QuotationLine | None) -> str:
        if quote_line is None:
            return "From stock"
        source = self._db.scalar(
            select(QuotationLineSource)
            .where(QuotationLineSource.quotation_line_fk == quote_line.id)
            .order_by(QuotationLineSource.position)
            .limit(1)
        )
        if source is None or source.kind == "stock" or source.supplier_fk is None:
            return "From stock"
        return self._name("party", source.supplier_fk)

    def _name(self, kind: str, identifier: uuid.UUID) -> str:
        key = (kind, identifier)
        if key not in self._names:
            model, attribute = {
                "party": (Party, "name"),
                "product": (Product, "name"),
                "category": (ProductCategory, "name"),
                "branch": (Branch, "name"),
                "user": (User, "full_name"),
                "shipment": (Shipment, "number"),
            }[kind]
            row = self._db.get(model, identifier)
            self._names[key] = str(getattr(row, attribute)) if row is not None else "Not recorded"
        return self._names[key]

    @staticmethod
    def _add(row: ProfitRow, fact: Fact) -> None:
        row.lines += 1
        row.revenue = cents(row.revenue + fact.revenue)
        row.commission = cents(row.commission + fact.commission)
        if fact.cost is None:
            row.pending_lines += 1
            row.pending_revenue = cents(row.pending_revenue + fact.revenue)
        else:
            row.cost = cents(row.cost + fact.cost)
            row.traced_revenue = cents(row.traced_revenue + fact.revenue)

    @staticmethod
    def _merge(total: ProfitRow, row: ProfitRow) -> None:
        total.lines += row.lines
        total.revenue = cents(total.revenue + row.revenue)
        total.cost = cents(total.cost + row.cost)
        total.commission = cents(total.commission + row.commission)
        total.traced_revenue = cents(total.traced_revenue + row.traced_revenue)
        total.pending_revenue = cents(total.pending_revenue + row.pending_revenue)
        total.pending_lines += row.pending_lines


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
