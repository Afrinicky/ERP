"""The operational reports of PRD §8, and the frame every report shares.

A report is run for a window (a day, a week, a month, a quarter, a year or any range),
optionally compared with the window before it or the same window a year earlier, and
narrowed by the filters its definition allows. The result is a ``Report`` (see
``domain/reporting.py``): what the screen draws, what the exports write and what a saved
run keeps.

Reports read; nothing here writes. A report that discloses cost or margin is open only to
those who may export such reports, and any cost column in an otherwise open report is
withheld from everyone else.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.inventory_reports import InventoryReportService
from nickland.application.services.profitability import Fact, ProfitabilityService
from nickland.domain.errors import ConflictError, PermissionDeniedError, ValidationError
from nickland.domain.inventory import MovementType
from nickland.domain.ledger import AccountingBasis
from nickland.domain.money import ZERO
from nickland.domain.reporting import (
    OPERATIONAL_BASIS,
    REPORTS,
    SENSITIVE_PERMISSION,
    Column,
    ColumnKind,
    Comparison,
    Figure,
    Granularity,
    Header,
    Report,
    ReportDefinition,
    ReportId,
    Row,
    Section,
    Table,
    cents,
    compare_figures,
    compare_table,
    comparison_window,
    definition_of,
    normalise,
    required_permissions,
    share,
    window,
    withhold,
)
from nickland.persistence.catalogue_models import Party, Product, ProductCategory
from nickland.persistence.dispatch_models import DeliveryException, Shipment
from nickland.persistence.models import Branch, Business, User, Warehouse
from nickland.persistence.order_models import (
    GoodsReceipt,
    PurchaseOrder,
    SalesOrder,
    SalesOrderLine,
    SupplierInvoice,
)
from nickland.persistence.pos_models import (
    PosCashMovement,
    PosRefund,
    PosSale,
    PosShift,
    PosTender,
)
from nickland.persistence.pricing_models import (
    CommissionAccrual,
    Quotation,
    QuotationInstalment,
    QuotationLine,
    QuotationRequest,
    SupplierQuotation,
    SupplierQuotationLine,
)
from nickland.platform.clock import Clock, SystemClock, business_date

ISSUED_STATES = ("issued", "accepted", "rejected", "expired")
SALE_STATES = ("completed", "refunded", "partially_refunded")
OPEN_ORDER_STATES = ("open", "partially_fulfilled")
NOT_PHYSICAL = frozenset({MovementType.RESERVATION.value, MovementType.RELEASE.value})

CHANNEL_LABELS = {"quotation": "Quotation-led sales", "register": "Point of sale"}
TENDER_LABELS = {
    "cash": "Cash",
    "mobile_money": "Mobile money",
    "card": "Card",
    "bank_transfer": "Bank transfer",
    "on_account": "On account",
}
MOVEMENT_LABELS = {
    "opening": "Opening stock",
    "receipt": "Receipt",
    "inspection_pass": "Passed inspection",
    "inspection_fail": "Failed inspection",
    "return_to_supplier": "Return to supplier",
    "relocation": "Relocation",
    "transfer_out": "Transfer out",
    "transfer_in": "Transfer in",
    "issue": "Issue",
    "customer_return": "Customer return",
    "damage": "Damage",
    "expiry": "Expiry",
    "count_gain": "Count gain",
    "count_loss": "Count loss",
    "adjustment_gain": "Adjustment gain",
    "adjustment_loss": "Adjustment loss",
    "dispatch": "Dispatch",
    "delivery": "Delivery",
    "delivery_return": "Delivery return",
    "transit_loss": "Loss in transit",
    "revaluation": "Revaluation",
    "reversal": "Reversal",
}
FILTER_LABELS = {
    "branch": "Branch",
    "customer": "Customer",
    "supplier": "Supplier",
    "product": "Product",
    "person": "Sales person",
    "warehouse": "Warehouse",
    "affiliate": "Affiliate",
    "register": "Register",
    "channel": "Channel",
    "basis": "Accounting basis",
}


# -- parameters ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportParameters:
    granularity: Granularity = Granularity.MONTH
    anchor: date | None = None
    start: date | None = None
    end: date | None = None
    comparison: Comparison = Comparison.NONE
    grouping: str | None = None
    filters: dict[str, str] = field(default_factory=dict)
    basis: AccountingBasis = AccountingBasis.ACCRUAL

    def as_record(self) -> dict[str, Any]:
        return {
            "granularity": self.granularity.value,
            "anchor": self.anchor.isoformat() if self.anchor else None,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "comparison": self.comparison.value,
            "grouping": self.grouping,
            "filters": dict(self.filters),
            "basis": self.basis.value,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> ReportParameters:
        def day(key: str) -> date | None:
            value = record.get(key)
            return date.fromisoformat(value) if value else None

        return cls(
            granularity=Granularity(record.get("granularity") or "month"),
            anchor=day("anchor"),
            start=day("start"),
            end=day("end"),
            comparison=Comparison(record.get("comparison") or "none"),
            grouping=record.get("grouping"),
            filters={str(k): str(v) for k, v in (record.get("filters") or {}).items() if v},
            basis=AccountingBasis(record.get("basis") or "accrual"),
        )


@dataclass(slots=True)
class Scope:
    """One run of one report: the window, the filters, and the means to name things."""

    business: Business
    today: date
    start: date
    end: date
    params: ReportParameters
    names: Names
    actor: AnyActor

    @property
    def business_id(self) -> uuid.UUID:
        return self.business.id

    def filter(self, key: str) -> str | None:
        return self.params.filters.get(key) or None

    def filter_id(self, key: str) -> uuid.UUID | None:
        value = self.filter(key)
        if value is None:
            return None
        try:
            return uuid.UUID(value)
        except ValueError:
            raise ValidationError(f"The {key} filter is not recognised.", field=key) from None

    def shifted(self, start: date, end: date) -> Scope:
        return replace(self, start=start, end=end)


class Names:
    """Names by identifier, read once per run."""

    __slots__ = ("_cache", "_db")

    def __init__(self, session: DbSession) -> None:
        self._db = session
        self._cache: dict[tuple[str, uuid.UUID], str] = {}

    def _get(self, kind: str, identifier: uuid.UUID | None, fallback: str) -> str:
        if identifier is None:
            return fallback
        key = (kind, identifier)
        if key not in self._cache:
            model, attribute = {
                "party": (Party, "name"),
                "product": (Product, "name"),
                "sku": (Product, "sku"),
                "category": (ProductCategory, "name"),
                "branch": (Branch, "name"),
                "user": (User, "full_name"),
                "warehouse": (Warehouse, "name"),
            }[kind]
            row = self._db.get(model, identifier)
            self._cache[key] = str(getattr(row, attribute)) if row is not None else fallback
        return self._cache[key]

    def party(self, identifier: uuid.UUID | None, fallback: str = "Not recorded") -> str:
        return self._get("party", identifier, fallback)

    def user(self, identifier: uuid.UUID | None, fallback: str = "Not recorded") -> str:
        return self._get("user", identifier, fallback)

    def product(self, identifier: uuid.UUID | None, fallback: str = "Not a catalogue item") -> str:
        return self._get("product", identifier, fallback)

    def sku(self, identifier: uuid.UUID | None) -> str:
        return self._get("sku", identifier, "")

    def category(self, identifier: uuid.UUID | None) -> str:
        return self._get("category", identifier, "Uncategorised")

    def branch(self, identifier: uuid.UUID | None) -> str:
        return self._get("branch", identifier, "Main business")

    def warehouse(self, identifier: uuid.UUID | None) -> str:
        return self._get("warehouse", identifier, "Not recorded")


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    definition: ReportDefinition
    available: bool
    exportable: bool


Builder = Callable[[Scope], list[Section]]


# -- the service ---------------------------------------------------------------------------


class ReportService:
    """Runs reports. Writes nothing."""

    __slots__ = ("_clock", "_db", "_inventory", "_profit")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._profit = ProfitabilityService(session)
        self._inventory = InventoryReportService(session, clock=self._clock)

    # -- catalogue ------------------------------------------------------------------------

    def catalogue(self, actor: AnyActor) -> list[CatalogueEntry]:
        actor.require("report.view")
        return [
            CatalogueEntry(
                definition=definition,
                available=all(
                    actor.has(code) for code in required_permissions(definition, export=False)
                ),
                exportable=all(
                    actor.has(code) for code in required_permissions(definition, export=True)
                ),
            )
            for definition in REPORTS.values()
        ]

    @staticmethod
    def authorise(actor: AnyActor, definition: ReportDefinition, *, export: bool) -> None:
        for code in required_permissions(definition, export=export):
            if not actor.has(code):
                raise PermissionDeniedError(code)

    # -- running --------------------------------------------------------------------------

    def scope(
        self, actor: AnyActor, definition: ReportDefinition, params: ReportParameters
    ) -> Scope:
        business = self._business(actor)
        today = business_date(self._clock.now(), business.timezone)
        anchor = params.anchor or today
        if definition.as_at:
            start, end = (params.end or today), (params.end or today)
        else:
            start, end = window(
                params.granularity,
                anchor,
                start=params.start,
                end=params.end,
                year_starts=business.financial_year_start_month,
            )
        if params.comparison not in (Comparison.NONE, *definition.comparisons):
            raise ValidationError(
                f"{definition.title} cannot be compared with the "
                f"{params.comparison.label.lower()}.",
                field="comparison",
            )
        if params.grouping and params.grouping not in {key for key, _ in definition.groupings}:
            raise ValidationError("That grouping is not offered here.", field="grouping")
        unknown = set(params.filters) - set(definition.filters)
        if unknown:
            raise ValidationError(
                f"{definition.title} cannot be filtered by {', '.join(sorted(unknown))}.",
                field="filters",
            )
        return Scope(business, today, start, end, params, Names(self._db), actor)

    def run(self, actor: AnyActor, report_id: str, params: ReportParameters) -> Report:
        definition = definition_of(report_id)
        if definition.id is ReportId.MANAGEMENT_PACK:
            raise ValidationError("The management pack is run from its own screen.")
        self.authorise(actor, definition, export=False)
        scope = self.scope(actor, definition, params)
        builder = self._builders()[definition.id]
        sections = builder(scope)
        header = self.header(scope, definition)
        prior = comparison_window(params.comparison, scope.start, scope.end, params.granularity)
        if prior is not None:
            before = builder(scope.shifted(*prior))
            sections = merge_sections(sections, before, params.comparison)
            header.comparison = params.comparison.label
            header.comparison_starts_on, header.comparison_ends_on = prior
        report = Report(
            id=definition.id.value,
            title=definition.title,
            header=header,
            sections=sections,
            sensitive=definition.sensitive,
        )
        if not actor.has(SENSITIVE_PERMISSION):
            withhold(report)
        return normalise(report)

    def header(self, scope: Scope, definition: ReportDefinition) -> Header:
        filters: list[tuple[str, str]] = []
        grouping = dict(definition.groupings).get(scope.params.grouping or "")
        if grouping:
            filters.append(("Grouped by", grouping))
        for key, value in sorted(scope.params.filters.items()):
            if key == "basis" or not value:
                continue
            filters.append((FILTER_LABELS.get(key, key.title()), self.describe(scope, key)))
        basis = (
            f"{scope.params.basis.value.capitalize()} basis"
            if definition.ledger
            else OPERATIONAL_BASIS
        )
        return Header(
            title=definition.title,
            starts_on=scope.start,
            ends_on=scope.end,
            currency=scope.business.base_currency,
            basis=basis,
            generated_at=self._clock.now(),
            generated_by=scope.actor.full_name,
            business=scope.business.trading_name or scope.business.name,
            filters=filters,
            as_at=definition.as_at,
        )

    def describe(self, scope: Scope, key: str) -> str:
        value = scope.filter(key) or ""
        if key in ("register",):
            return value
        if key == "channel":
            return CHANNEL_LABELS.get(value, value)
        identifier = scope.filter_id(key)
        return {
            "branch": scope.names.branch,
            "customer": scope.names.party,
            "supplier": scope.names.party,
            "affiliate": scope.names.party,
            "product": scope.names.product,
            "person": scope.names.user,
            "warehouse": scope.names.warehouse,
        }[key](identifier)

    def _builders(self) -> dict[ReportId, Builder]:
        from nickland.application.services.report_ledger import LedgerSections

        ledger = LedgerSections(self._db, clock=self._clock)
        return {
            ReportId.QUOTATIONS: self._quotations,
            ReportId.ORDER_PROFITABILITY: self._order_profitability,
            ReportId.SUPPLIER_COMPARISON: self._supplier_comparison,
            ReportId.PURCHASING: self._purchasing,
            ReportId.SALES: self._sales,
            ReportId.POS_SHIFTS: self._pos_shifts,
            ReportId.INSTALMENTS: self._instalments,
            ReportId.COMMISSION: self._commission,
            ReportId.INVENTORY: self._inventory_position,
            ReportId.STOCK_MOVEMENTS: self._stock_movements,
            ReportId.DELIVERY: self._delivery,
            ReportId.FINANCE: ledger.finance,
        }

    def _business(self, actor: AnyActor) -> Business:
        if actor.business_id is None:
            raise ConflictError("The business has not been provisioned.")
        business = self._db.get(Business, actor.business_id)
        if business is None:
            raise ConflictError("The business has not been provisioned.")
        return business

    # -- quotation performance ------------------------------------------------------------

    def _quotations(self, scope: Scope) -> list[Section]:
        grouping = scope.params.grouping or "person"
        statement = select(Quotation).where(
            Quotation.business_id == scope.business_id,
            Quotation.state.in_(ISSUED_STATES),
            Quotation.quoted_on >= scope.start,
            Quotation.quoted_on <= scope.end,
        )
        requests = select(QuotationRequest).where(
            QuotationRequest.business_id == scope.business_id,
            QuotationRequest.state != "draft",
            QuotationRequest.received_on >= scope.start,
            QuotationRequest.received_on <= scope.end,
        )
        if (branch := scope.filter_id("branch")) is not None:
            statement = statement.where(Quotation.branch_id == branch)
            requests = requests.where(QuotationRequest.branch_id == branch)
        if (customer := scope.filter_id("customer")) is not None:
            statement = statement.where(Quotation.customer_fk == customer)
            requests = requests.where(QuotationRequest.customer_fk == customer)
        if (person := scope.filter_id("person")) is not None:
            statement = statement.where(Quotation.issued_by_id == person)

        def key_of(quotation: Quotation | QuotationRequest) -> tuple[str, str]:
            if grouping == "customer":
                return str(quotation.customer_fk), scope.names.party(quotation.customer_fk)
            if grouping == "branch":
                return str(quotation.branch_id or "main"), scope.names.branch(quotation.branch_id)
            if grouping == "month":
                on = (
                    quotation.quoted_on
                    if isinstance(quotation, Quotation)
                    else quotation.received_on
                )
                return f"{on:%Y-%m}", f"{on:%B %Y}"
            if isinstance(quotation, QuotationRequest):
                return "none", "Not attributed"
            return str(quotation.issued_by_id or "none"), scope.names.user(quotation.issued_by_id)

        groups: dict[str, dict[str, Any]] = {}

        def group(key: str, label: str) -> dict[str, Any]:
            return groups.setdefault(
                key,
                {
                    "label": label,
                    "requests": 0,
                    "issued": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "expired": 0,
                    "open": 0,
                    "value": ZERO,
                    "won": ZERO,
                },
            )

        received = 0
        for request in self._db.scalars(requests).all():
            received += 1
            if grouping != "person" or scope.filter_id("person") is None:
                group(*key_of(request))["requests"] += 1
        for quotation in self._db.scalars(statement).all():
            bucket = group(*key_of(quotation))
            bucket["issued"] += 1
            bucket["value"] = cents(bucket["value"] + quotation.total_payable)
            state = quotation.state
            if state == "issued" and quotation.valid_until and quotation.valid_until < scope.today:
                state = "expired"
            if state == "issued":
                bucket["open"] += 1
            else:
                bucket[state] += 1
            if state == "accepted":
                bucket["won"] = cents(bucket["won"] + quotation.total_payable)

        columns = [
            Column("label", dict(REPORTS[ReportId.QUOTATIONS].groupings)[grouping]),
            Column("requests", "Requests", ColumnKind.COUNT),
            Column("issued", "Issued", ColumnKind.COUNT, compare=True),
            Column("accepted", "Accepted", ColumnKind.COUNT),
            Column("rejected", "Rejected", ColumnKind.COUNT),
            Column("expired", "Expired", ColumnKind.COUNT),
            Column("open", "Open", ColumnKind.COUNT),
            Column("value", "Value issued", ColumnKind.MONEY),
            Column("won", "Value accepted", ColumnKind.MONEY, compare=True),
            Column("conversion", "Conversion", ColumnKind.PERCENT),
        ]
        rows = [
            Row(
                key,
                {
                    **data,
                    "conversion": share(Decimal(data["accepted"]), Decimal(data["issued"])),
                },
                drill={"view": "quotations", "group": grouping, "key": key},
            )
            for key, data in sorted(groups.items(), key=lambda item: -item[1]["value"])
        ]
        totals: dict[str, Any] = {
            name: sum(data[name] for data in groups.values())
            for name in ("requests", "issued", "accepted", "rejected", "expired", "open")
        }
        totals["value"] = cents(sum((data["value"] for data in groups.values()), ZERO))
        totals["won"] = cents(sum((data["won"] for data in groups.values()), ZERO))
        totals["requests"] = received
        total = Row(
            "total",
            {
                "label": "Total",
                **totals,
                "conversion": share(Decimal(totals["accepted"]), Decimal(totals["issued"])),
            },
            emphasis="total",
        )
        figures = [
            Figure("requests", "Requests received", received, ColumnKind.COUNT),
            Figure("issued", "Quotations issued", totals["issued"], ColumnKind.COUNT),
            Figure("accepted", "Accepted", totals["accepted"], ColumnKind.COUNT),
            Figure(
                "conversion",
                "Conversion rate",
                share(Decimal(totals["accepted"]), Decimal(totals["issued"])),
                ColumnKind.PERCENT,
            ),
            Figure("won", "Value accepted", cents(Decimal(totals["won"]))),
        ]
        return [
            Section(
                "performance",
                "Quotation Performance",
                figures=figures,
                tables=[Table("by_group", "Performance", columns, rows, total)],
            )
        ]

    # -- sales ----------------------------------------------------------------------------

    def _facts(self, scope: Scope) -> list[Fact]:
        facts = self._profit.facts(scope.business_id, scope.start, scope.end)
        if (branch := scope.filter_id("branch")) is not None:
            facts = [fact for fact in facts if fact.branch_id == branch]
        if (customer := scope.filter_id("customer")) is not None:
            facts = [fact for fact in facts if fact.customer_id == customer]
        if (product := scope.filter_id("product")) is not None:
            facts = [fact for fact in facts if fact.product_id == product]
        if (person := scope.filter_id("person")) is not None:
            facts = [fact for fact in facts if fact.person_id == person]
        if (channel := scope.filter("channel")) is not None:
            facts = [fact for fact in facts if fact.channel == channel]
        return facts

    def _sales(self, scope: Scope) -> list[Section]:
        grouping = scope.params.grouping or "customer"
        categories: dict[uuid.UUID, uuid.UUID | None] = {}

        def key_of(fact: Fact) -> tuple[str, str]:
            if grouping == "product":
                return str(fact.product_id or "none"), scope.names.product(fact.product_id)
            if grouping == "category":
                if fact.product_id is None:
                    return "none", "Uncategorised"
                if fact.product_id not in categories:
                    product = self._db.get(Product, fact.product_id)
                    categories[fact.product_id] = product.category_fk if product else None
                category = categories[fact.product_id]
                return str(category or "none"), scope.names.category(category)
            if grouping == "month":
                return f"{fact.on:%Y-%m}", f"{fact.on:%B %Y}"
            if grouping == "channel":
                return fact.channel, CHANNEL_LABELS.get(fact.channel, fact.channel)
            if grouping == "person":
                return str(fact.person_id or "none"), scope.names.user(fact.person_id)
            if grouping == "branch":
                return str(fact.branch_id or "main"), scope.names.branch(fact.branch_id)
            if fact.customer_id is None:
                return "walk_in", "Walk-in customers"
            return str(fact.customer_id), scope.names.party(fact.customer_id)

        groups: dict[str, dict[str, Any]] = {}
        documents: dict[str, set[str]] = defaultdict(set)
        for fact in self._facts(scope):
            key, label = key_of(fact)
            bucket = groups.setdefault(
                key, {"label": label, "lines": 0, "quantity": ZERO, "revenue": ZERO}
            )
            bucket["lines"] += 1
            bucket["quantity"] += fact.quantity
            bucket["revenue"] = cents(bucket["revenue"] + fact.revenue)
            documents[key].add(fact.reference)
        revenue = cents(sum((data["revenue"] for data in groups.values()), ZERO))
        rows = [
            Row(
                key,
                {
                    "label": data["label"],
                    "documents": len(documents[key]),
                    "lines": data["lines"],
                    "quantity": data["quantity"],
                    "revenue": data["revenue"],
                    "share": share(data["revenue"], revenue),
                },
                drill={"view": "sales", "group": grouping, "key": key},
            )
            for key, data in sorted(groups.items(), key=lambda item: -item[1]["revenue"])
        ]
        if grouping == "month":
            rows.sort(key=lambda row: row.key)
        documents_total = len({ref for refs in documents.values() for ref in refs})
        total = Row(
            "total",
            {
                "label": "Total",
                "documents": documents_total,
                "lines": sum(row.cells["lines"] for row in rows),
                "quantity": sum((data["quantity"] for data in groups.values()), ZERO),
                "revenue": revenue,
                "share": Decimal("100.0") if revenue else None,
            },
            emphasis="total",
        )
        columns = [
            Column("label", dict(REPORTS[ReportId.SALES].groupings)[grouping]),
            Column("documents", "Invoices and sales", ColumnKind.COUNT),
            Column("lines", "Lines", ColumnKind.COUNT),
            Column("quantity", "Quantity", ColumnKind.QUANTITY),
            Column("revenue", "Net sales", ColumnKind.MONEY, compare=True),
            Column("share", "Share", ColumnKind.PERCENT),
        ]
        average = cents(revenue / documents_total) if documents_total else ZERO
        figures = [
            Figure("revenue", "Net sales", revenue),
            Figure("documents", "Invoices and register sales", documents_total, ColumnKind.COUNT),
            Figure("average", "Average sale", average),
        ]
        return [
            Section(
                "sales",
                "Sales",
                figures=figures,
                tables=[
                    Table(
                        "by_group",
                        "Sales",
                        columns,
                        rows,
                        total,
                        note="Net of tax and of credit notes and refunds.",
                    )
                ],
            )
        ]

    # -- order profitability --------------------------------------------------------------

    def _order_profitability(self, scope: Scope) -> list[Section]:
        facts = [fact for fact in self._facts(scope) if fact.channel == "quotation"]
        orders: dict[str, dict[str, Any]] = {}
        for fact in facts:
            key = str(fact.order_id or fact.source_id)
            bucket = orders.setdefault(
                key,
                {
                    "order": fact.order_label,
                    "customer": scope.names.party(fact.customer_id, "Walk-in customer"),
                    "revenue": ZERO,
                    "cost": ZERO,
                    "traced": ZERO,
                    "commission": ZERO,
                    "pending": 0,
                },
            )
            bucket["revenue"] = cents(bucket["revenue"] + fact.revenue)
            bucket["commission"] = cents(bucket["commission"] + fact.commission)
            if fact.cost is None:
                bucket["pending"] += 1
            else:
                bucket["cost"] = cents(bucket["cost"] + fact.cost)
                bucket["traced"] = cents(bucket["traced"] + fact.revenue)
        quoted = self._quoted_costs(scope, set(orders))
        rows: list[Row] = []
        sums: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
        for key, data in orders.items():
            estimate = quoted.get(key, (ZERO, ZERO))
            contribution = cents(data["traced"] - data["cost"] - data["commission"])
            cells = {
                "order": data["order"],
                "customer": data["customer"],
                "revenue": data["revenue"],
                "quoted_cost": estimate[0],
                "charges": estimate[1],
                "actual_cost": data["cost"],
                "variance": cents(estimate[0] - data["cost"]) if not data["pending"] else None,
                "commission": data["commission"],
                "contribution": contribution,
                "margin": share(contribution, data["traced"]),
                "pending": data["pending"],
            }
            for name in (
                "revenue",
                "quoted_cost",
                "charges",
                "actual_cost",
                "commission",
                "contribution",
            ):
                sums[name] = cents(sums[name] + cells[name])
            sums["traced"] = cents(sums["traced"] + data["traced"])
            rows.append(Row(key, cells, drill={"view": "order", "id": key}))
        rows.sort(key=lambda row: -row.cells["revenue"])
        pending = sum(int(data["pending"]) for data in orders.values())
        total = Row(
            "total",
            {
                "order": "Total",
                "customer": None,
                "revenue": sums["revenue"],
                "quoted_cost": sums["quoted_cost"],
                "charges": sums["charges"],
                "actual_cost": sums["actual_cost"],
                "variance": None,
                "commission": sums["commission"],
                "contribution": sums["contribution"],
                "margin": share(sums["contribution"], sums["traced"]),
                "pending": pending,
            },
            emphasis="total",
        )
        columns = [
            Column("order", "Order"),
            Column("customer", "Customer"),
            Column("revenue", "Revenue", ColumnKind.MONEY, compare=True),
            Column("quoted_cost", "Quoted cost", ColumnKind.MONEY, sensitive=True),
            Column("charges", "Charges and tax in quote", ColumnKind.MONEY, sensitive=True),
            Column("actual_cost", "Actual cost", ColumnKind.MONEY, sensitive=True),
            Column("variance", "Cost variance", ColumnKind.MONEY, sensitive=True),
            Column("commission", "Affiliate cost", ColumnKind.MONEY, sensitive=True),
            Column("contribution", "Contribution", ColumnKind.MONEY, compare=True, sensitive=True),
            Column("margin", "Margin", ColumnKind.PERCENT, sensitive=True),
            Column("pending", "Awaiting cost", ColumnKind.COUNT),
        ]
        figures = [
            Figure("revenue", "Revenue", sums["revenue"]),
            Figure("contribution", "Contribution", sums["contribution"], sensitive=True),
            Figure(
                "margin",
                "Margin",
                share(sums["contribution"], sums["traced"]),
                ColumnKind.PERCENT,
                sensitive=True,
            ),
            Figure("orders", "Orders invoiced", len(orders), ColumnKind.COUNT),
        ]
        return [
            Section(
                "orders",
                "Order Profitability",
                figures=figures,
                tables=[
                    Table(
                        "orders",
                        "Orders invoiced in the window",
                        columns,
                        rows,
                        total,
                        note="Revenue and cost on what was invoiced in the window. A line "
                        "without an actual cost counts in revenue and is left out of "
                        "contribution until its cost is known.",
                    )
                ],
            )
        ]

    def _quoted_costs(
        self, scope: Scope, order_keys: set[str]
    ) -> dict[str, tuple[Decimal, Decimal]]:
        """Per order, the quoted cost and the quoted charges and tax on what was invoiced."""
        from nickland.persistence.finance_models import SalesInvoice, SalesInvoiceLine

        result: dict[str, tuple[Decimal, Decimal]] = {}
        rows = self._db.execute(
            select(SalesInvoiceLine, SalesOrderLine, QuotationLine)
            .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_fk)
            .join(SalesOrderLine, SalesOrderLine.id == SalesInvoiceLine.sales_order_line_fk)
            .outerjoin(QuotationLine, QuotationLine.id == SalesOrderLine.quotation_line_fk)
            .where(
                SalesInvoice.business_id == scope.business_id,
                SalesInvoice.issued_on >= scope.start,
                SalesInvoice.issued_on <= scope.end,
            )
        ).all()
        for line, order_line, quote_line in rows:
            key = str(order_line.order_fk)
            if key not in order_keys:
                continue
            cost, charges = result.get(key, (ZERO, ZERO))
            if order_line.expected_unit_cost is not None:
                cost = cents(cost + order_line.expected_unit_cost * line.quantity)
            if quote_line is not None and quote_line.quantity:
                portion = line.quantity / quote_line.quantity
                charges = cents(
                    charges + (quote_line.allocated_cost + quote_line.non_recoverable_tax) * portion
                )
            result[key] = (cost, charges)
        return result

    # -- supplier comparison --------------------------------------------------------------

    def _supplier_comparison(self, scope: Scope) -> list[Section]:
        supplier = scope.filter_id("supplier")
        product = scope.filter_id("product")
        quotes = select(SupplierQuotation).where(
            SupplierQuotation.business_id == scope.business_id,
            SupplierQuotation.received_on >= scope.start,
            SupplierQuotation.received_on <= scope.end,
        )
        if supplier is not None:
            quotes = quotes.where(SupplierQuotation.supplier_fk == supplier)
        stats: dict[uuid.UUID, dict[str, Any]] = {}

        def bucket(party: uuid.UUID) -> dict[str, Any]:
            return stats.setdefault(
                party,
                {
                    "quotes": 0,
                    "lines": 0,
                    "available": 0,
                    "lead": [],
                    "orders": 0,
                    "ordered": ZERO,
                    "receipts": 0,
                    "on_time": 0,
                },
            )

        history: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[date, Decimal]]] = defaultdict(list)
        for quote in self._db.scalars(quotes).all():
            data = bucket(quote.supplier_fk)
            data["quotes"] += 1
            for line in self._db.scalars(
                select(SupplierQuotationLine).where(
                    SupplierQuotationLine.supplier_quotation_fk == quote.id
                )
            ).all():
                if product is not None and line.product_fk != product:
                    continue
                data["lines"] += 1
                if line.is_available:
                    data["available"] += 1
                lead = line.lead_time_days or quote.lead_time_days
                if lead is not None:
                    data["lead"].append(lead)
                if line.product_fk is not None:
                    net = line.unit_cost * (1 - (line.discount_rate or ZERO) / 100)
                    history[(line.product_fk, quote.supplier_fk)].append(
                        (quote.received_on, cents(net * quote.exchange_rate))
                    )
        orders = select(PurchaseOrder).where(
            PurchaseOrder.business_id == scope.business_id,
            PurchaseOrder.state.not_in(("draft", "cancelled")),
            PurchaseOrder.ordered_on >= scope.start,
            PurchaseOrder.ordered_on <= scope.end,
        )
        if supplier is not None:
            orders = orders.where(PurchaseOrder.supplier_fk == supplier)
        for order in self._db.scalars(orders).all():
            data = bucket(order.supplier_fk)
            data["orders"] += 1
            data["ordered"] = cents(data["ordered"] + order.total * order.exchange_rate)
            for receipt in self._db.scalars(
                select(GoodsReceipt).where(
                    GoodsReceipt.purchase_order_fk == order.id, GoodsReceipt.state == "posted"
                )
            ).all():
                data["receipts"] += 1
                if order.expected_on is None or receipt.received_on <= order.expected_on:
                    data["on_time"] += 1
        rows = []
        for party, data in sorted(stats.items(), key=lambda item: scope.names.party(item[0])):
            lead = data["lead"]
            rows.append(
                Row(
                    str(party),
                    {
                        "supplier": scope.names.party(party),
                        "quotes": data["quotes"],
                        "lines": data["lines"],
                        "availability": share(Decimal(data["available"]), Decimal(data["lines"])),
                        "lead": (
                            (Decimal(sum(lead)) / len(lead)).quantize(Decimal("0.1"))
                            if lead
                            else None
                        ),
                        "orders": data["orders"],
                        "ordered": data["ordered"],
                        "on_time": share(Decimal(data["on_time"]), Decimal(data["receipts"])),
                    },
                    drill={"view": "supplier", "id": str(party)},
                )
            )
        price_rows = []
        for (item, party), points in sorted(
            history.items(),
            key=lambda entry: (scope.names.product(entry[0][0]), scope.names.party(entry[0][1])),
        ):
            points.sort()
            costs = [cost for _, cost in points]
            price_rows.append(
                Row(
                    f"{item}:{party}",
                    {
                        "product": scope.names.product(item),
                        "supplier": scope.names.party(party),
                        "quotes": len(points),
                        "latest_on": points[-1][0],
                        "latest": points[-1][1],
                        "lowest": min(costs),
                        "highest": max(costs),
                        "movement": share(points[-1][1] - points[0][1], points[0][1])
                        if len(points) > 1
                        else None,
                    },
                )
            )
        # The cheapest latest price for each product is the one to beat.
        best: dict[str, Decimal] = {}
        for row in price_rows:
            name = str(row.cells["product"])
            latest = row.cells["latest"]
            assert isinstance(latest, Decimal)
            best[name] = min(best.get(name, latest), latest)
        for row in price_rows:
            latest = row.cells["latest"]
            assert isinstance(latest, Decimal)
            row.cells["against_best"] = share(
                latest - best[str(row.cells["product"])], best[str(row.cells["product"])]
            )
        return [
            Section(
                "suppliers",
                "Supplier Comparison",
                figures=[
                    Figure("suppliers", "Suppliers quoting", len(stats), ColumnKind.COUNT),
                    Figure(
                        "quotes",
                        "Quotations received",
                        sum(data["quotes"] for data in stats.values()),
                        ColumnKind.COUNT,
                    ),
                    Figure(
                        "ordered",
                        "Ordered",
                        cents(sum((data["ordered"] for data in stats.values()), ZERO)),
                        sensitive=True,
                    ),
                ],
                tables=[
                    Table(
                        "suppliers",
                        "Performance by supplier",
                        [
                            Column("supplier", "Supplier"),
                            Column("quotes", "Quotations", ColumnKind.COUNT),
                            Column("lines", "Lines quoted", ColumnKind.COUNT),
                            Column("availability", "Available", ColumnKind.PERCENT),
                            Column("lead", "Lead time", ColumnKind.DAYS),
                            Column("orders", "Orders", ColumnKind.COUNT),
                            Column("ordered", "Ordered", ColumnKind.MONEY, sensitive=True),
                            Column("on_time", "Received on time", ColumnKind.PERCENT),
                        ],
                        rows,
                    ),
                    Table(
                        "prices",
                        "Price history",
                        [
                            Column("product", "Product"),
                            Column("supplier", "Supplier"),
                            Column("quotes", "Quotes", ColumnKind.COUNT),
                            Column("latest_on", "Latest", ColumnKind.DATE),
                            Column("latest", "Latest unit cost", ColumnKind.MONEY, sensitive=True),
                            Column("lowest", "Lowest", ColumnKind.MONEY, sensitive=True),
                            Column("highest", "Highest", ColumnKind.MONEY, sensitive=True),
                            Column("movement", "Movement", ColumnKind.PERCENT),
                            Column("against_best", "Above best", ColumnKind.PERCENT),
                        ],
                        price_rows,
                        note="Unit costs net of the supplier's discount, in the base currency.",
                    ),
                ],
            )
        ]

    # -- purchasing -----------------------------------------------------------------------

    def _purchasing(self, scope: Scope) -> list[Section]:
        statement = select(PurchaseOrder).where(
            PurchaseOrder.business_id == scope.business_id,
            PurchaseOrder.state.not_in(("draft",)),
            PurchaseOrder.ordered_on >= scope.start,
            PurchaseOrder.ordered_on <= scope.end,
        )
        if (branch := scope.filter_id("branch")) is not None:
            statement = statement.where(PurchaseOrder.branch_id == branch)
        if (supplier := scope.filter_id("supplier")) is not None:
            statement = statement.where(PurchaseOrder.supplier_fk == supplier)
        order_rows: list[Row] = []
        suppliers: dict[uuid.UUID, dict[str, Any]] = {}
        sums: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
        for order in self._db.scalars(statement.order_by(PurchaseOrder.ordered_on)).all():
            cancelled = order.state == "cancelled"
            ordered = ZERO if cancelled else cents(order.total * order.exchange_rate)
            received = cents(
                Decimal(
                    self._db.scalar(
                        select(func.coalesce(func.sum(GoodsReceipt.accepted_value), 0)).where(
                            GoodsReceipt.purchase_order_fk == order.id,
                            GoodsReceipt.state == "posted",
                        )
                    )
                    or 0
                )
            )
            invoiced = paid = credited = ZERO
            for invoice in self._db.scalars(
                select(SupplierInvoice).where(
                    SupplierInvoice.purchase_order_fk == order.id,
                    SupplierInvoice.state.in_(("posted", "partially_paid", "paid")),
                )
            ).all():
                rate = invoice.exchange_rate
                invoiced = cents(invoiced + invoice.total * rate)
                paid = cents(paid + invoice.amount_paid * rate)
                credited = cents(credited + invoice.amount_credited * rate)
            outstanding = cents(invoiced - paid - credited)
            cells = {
                "number": order.number,
                "supplier": scope.names.party(order.supplier_fk),
                "ordered_on": order.ordered_on,
                "state": order.state.replace("_", " ").capitalize(),
                "ordered": ordered,
                "received": received,
                "invoiced": invoiced,
                "paid": paid,
                "outstanding": outstanding,
            }
            order_rows.append(
                Row(str(order.id), cells, drill={"view": "purchase_order", "id": str(order.id)})
            )
            data = suppliers.setdefault(
                order.supplier_fk,
                {
                    "orders": 0,
                    "ordered": ZERO,
                    "received": ZERO,
                    "invoiced": ZERO,
                    "paid": ZERO,
                    "outstanding": ZERO,
                },
            )
            if not cancelled:
                data["orders"] += 1
            for name in ("ordered", "received", "invoiced", "paid", "outstanding"):
                data[name] = cents(data[name] + cells[name])
                sums[name] = cents(sums[name] + cells[name])  # type: ignore[operator]
        money_columns = [
            Column("ordered", "Ordered", ColumnKind.MONEY, compare=True, sensitive=True),
            Column("received", "Received", ColumnKind.MONEY, sensitive=True),
            Column("invoiced", "Invoiced", ColumnKind.MONEY, sensitive=True),
            Column("paid", "Paid", ColumnKind.MONEY, sensitive=True),
            Column("outstanding", "Outstanding", ColumnKind.MONEY, sensitive=True),
        ]
        supplier_rows = [
            Row(
                str(party),
                {"supplier": scope.names.party(party), **data},
                drill={"view": "supplier", "id": str(party)},
            )
            for party, data in sorted(suppliers.items(), key=lambda item: -item[1]["ordered"])
        ]
        return [
            Section(
                "purchasing",
                "Purchasing",
                figures=[
                    Figure("ordered", "Ordered", sums["ordered"], sensitive=True),
                    Figure("received", "Goods received", sums["received"], sensitive=True),
                    Figure("invoiced", "Invoiced by suppliers", sums["invoiced"], sensitive=True),
                    Figure("outstanding", "Outstanding", sums["outstanding"], sensitive=True),
                ],
                tables=[
                    Table(
                        "by_supplier",
                        "By supplier",
                        [
                            Column("supplier", "Supplier"),
                            Column("orders", "Orders", ColumnKind.COUNT),
                            *money_columns,
                        ],
                        supplier_rows,
                        Row(
                            "total",
                            {
                                "supplier": "Total",
                                "orders": sum(int(d["orders"]) for d in suppliers.values()),
                                **{
                                    k: sums[k]
                                    for k in (
                                        "ordered",
                                        "received",
                                        "invoiced",
                                        "paid",
                                        "outstanding",
                                    )
                                },
                            },
                            emphasis="total",
                        ),
                    ),
                    Table(
                        "orders",
                        "Purchase orders",
                        [
                            Column("number", "Order"),
                            Column("supplier", "Supplier"),
                            Column("ordered_on", "Ordered", ColumnKind.DATE),
                            Column("state", "Status"),
                            *[replace(column, compare=False) for column in money_columns],
                        ],
                        order_rows,
                        note="Received is the accepted value of goods received; invoiced, paid "
                        "and outstanding are the posted supplier invoices on each order.",
                    ),
                ],
            )
        ]

    # -- point of sale shifts -------------------------------------------------------------

    def _pos_shifts(self, scope: Scope) -> list[Section]:
        statement = select(PosShift).where(
            PosShift.business_id == scope.business_id,
            PosShift.business_day >= scope.start,
            PosShift.business_day <= scope.end,
        )
        if (branch := scope.filter_id("branch")) is not None:
            statement = statement.where(PosShift.branch_id == branch)
        if (register := scope.filter("register")) is not None:
            statement = statement.where(PosShift.register == register)
        if (person := scope.filter_id("person")) is not None:
            statement = statement.where(PosShift.cashier_id == person)
        shifts = list(self._db.scalars(statement.order_by(PosShift.opened_at)).all())
        tenders: dict[str, dict[str, Any]] = {}
        rows: list[Row] = []
        movement_rows: list[Row] = []
        sums: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
        for shift in shifts:
            sales = self._db.execute(
                select(func.count(), func.coalesce(func.sum(PosSale.total), 0)).where(
                    PosSale.shift_fk == shift.id, PosSale.state.in_(SALE_STATES)
                )
            ).one()
            refunds = Decimal(
                self._db.scalar(
                    select(func.coalesce(func.sum(PosRefund.total), 0)).where(
                        PosRefund.shift_fk == shift.id
                    )
                )
                or 0
            )
            cash_in = cash_out = ZERO
            for movement in self._db.scalars(
                select(PosCashMovement).where(PosCashMovement.shift_fk == shift.id)
            ).all():
                if movement.kind == "cash_in":
                    cash_in = cents(cash_in + movement.amount)
                else:
                    cash_out = cents(cash_out + movement.amount)
                movement_rows.append(
                    Row(
                        str(movement.id),
                        {
                            "shift": shift.number,
                            "kind": "Cash in" if movement.kind == "cash_in" else "Cash out",
                            "amount": movement.amount,
                            "reason": movement.reason,
                            "by": scope.names.user(movement.recorded_by_id),
                        },
                    )
                )
            for method, amount, count in self._db.execute(
                select(PosTender.method, func.sum(PosTender.amount), func.count())
                .join(PosSale, PosSale.id == PosTender.sale_fk)
                .where(
                    PosSale.shift_fk == shift.id,
                    PosSale.state.in_(SALE_STATES),
                    PosTender.state == "confirmed",
                )
                .group_by(PosTender.method)
            ).all():
                data = tenders.setdefault(method, {"count": 0, "amount": ZERO})
                data["count"] += int(count)
                data["amount"] = cents(data["amount"] + Decimal(amount or 0))
            cells: dict[str, Any] = {
                "number": shift.number,
                "day": shift.business_day,
                "register": shift.register,
                "cashier": scope.names.user(shift.cashier_id),
                "state": shift.state.capitalize(),
                "float": shift.opening_float,
                "sales": int(sales[0]),
                "takings": cents(Decimal(sales[1] or 0)),
                "refunds": cents(refunds),
                "cash_in": cash_in,
                "cash_out": cash_out,
                "expected": shift.expected_cash,
                "counted": shift.counted_cash,
                "variance": shift.variance,
            }
            for name in ("float", "takings", "refunds", "cash_in", "cash_out"):
                sums[name] = cents(sums[name] + cells[name])
            if shift.variance is not None:
                sums["variance"] = cents(sums["variance"] + shift.variance)
            sums["sales"] += int(sales[0])
            rows.append(Row(str(shift.id), cells, drill={"view": "shift", "id": str(shift.id)}))
        tender_total = cents(sum((data["amount"] for data in tenders.values()), ZERO))
        tender_rows = [
            Row(
                method,
                {
                    "method": TENDER_LABELS.get(method, method),
                    "count": data["count"],
                    "amount": data["amount"],
                    "share": share(data["amount"], tender_total),
                },
            )
            for method, data in sorted(tenders.items(), key=lambda item: -item[1]["amount"])
        ]
        return [
            Section(
                "shifts",
                "Point of Sale Shifts",
                figures=[
                    Figure("shifts", "Shifts", len(shifts), ColumnKind.COUNT),
                    Figure("takings", "Takings", sums["takings"]),
                    Figure("refunds", "Refunds", sums["refunds"]),
                    Figure("variance", "Net cash variance", sums["variance"]),
                ],
                tables=[
                    Table(
                        "shifts",
                        "Shifts",
                        [
                            Column("number", "Shift"),
                            Column("day", "Business day", ColumnKind.DATE),
                            Column("register", "Register"),
                            Column("cashier", "Cashier"),
                            Column("state", "Status"),
                            Column("float", "Float", ColumnKind.MONEY),
                            Column("sales", "Sales", ColumnKind.COUNT),
                            Column("takings", "Takings", ColumnKind.MONEY),
                            Column("refunds", "Refunds", ColumnKind.MONEY),
                            Column("cash_in", "Cash in", ColumnKind.MONEY),
                            Column("cash_out", "Cash out", ColumnKind.MONEY),
                            Column("expected", "Expected cash", ColumnKind.MONEY),
                            Column("counted", "Counted", ColumnKind.MONEY),
                            Column("variance", "Variance", ColumnKind.MONEY),
                        ],
                        rows,
                        Row(
                            "total",
                            {
                                "number": "Total",
                                "float": sums["float"],
                                "sales": int(sums["sales"]),
                                "takings": sums["takings"],
                                "refunds": sums["refunds"],
                                "cash_in": sums["cash_in"],
                                "cash_out": sums["cash_out"],
                                "variance": sums["variance"],
                            },
                            emphasis="total",
                        ),
                    ),
                    Table(
                        "tenders",
                        "Tender split",
                        [
                            Column("method", "Tender"),
                            Column("count", "Payments", ColumnKind.COUNT),
                            Column("amount", "Amount", ColumnKind.MONEY),
                            Column("share", "Share", ColumnKind.PERCENT),
                        ],
                        tender_rows,
                        Row(
                            "total",
                            {
                                "method": "Total",
                                "count": sum(int(d["count"]) for d in tenders.values()),
                                "amount": tender_total,
                                "share": Decimal("100.0") if tender_total else None,
                            },
                            emphasis="total",
                        ),
                        note="Confirmed tenders, including change given back on cash.",
                    ),
                    Table(
                        "movements",
                        "Cash movements",
                        [
                            Column("shift", "Shift"),
                            Column("kind", "Movement"),
                            Column("amount", "Amount", ColumnKind.MONEY),
                            Column("reason", "Reason"),
                            Column("by", "Recorded by"),
                        ],
                        movement_rows,
                        empty="No cash was paid in or taken out.",
                    ),
                ],
            )
        ]

    # -- instalments ----------------------------------------------------------------------

    def _instalments(self, scope: Scope) -> list[Section]:
        statement = (
            select(QuotationInstalment, Quotation)
            .join(Quotation, Quotation.id == QuotationInstalment.quotation_fk)
            .where(
                Quotation.business_id == scope.business_id,
                Quotation.state == "accepted",
                QuotationInstalment.schedule_version == Quotation.schedule_version,
            )
        )
        if (branch := scope.filter_id("branch")) is not None:
            statement = statement.where(Quotation.branch_id == branch)
        if (customer := scope.filter_id("customer")) is not None:
            statement = statement.where(Quotation.customer_fk == customer)
        plans: dict[uuid.UUID, dict[str, Any]] = {}
        due_rows: list[Row] = []
        due = collected = collected_of_due = overdue = outstanding = ZERO
        for instalment, quotation in self._db.execute(
            statement.order_by(QuotationInstalment.due_on)
        ).all():
            paid = instalment.paid_amount or ZERO
            waived = instalment.state == "waived"
            remaining = ZERO if waived else cents(max(ZERO, instalment.amount - paid))
            late = remaining > ZERO and instalment.due_on < scope.today
            plan = plans.setdefault(
                quotation.id,
                {
                    "quotation": quotation.number,
                    "customer": scope.names.party(quotation.customer_fk),
                    "scheduled": ZERO,
                    "paid": ZERO,
                    "outstanding": ZERO,
                    "overdue": ZERO,
                    "next_due": None,
                    "instalments": 0,
                },
            )
            plan["instalments"] += 1
            plan["scheduled"] = cents(plan["scheduled"] + instalment.amount)
            plan["paid"] = cents(plan["paid"] + paid)
            plan["outstanding"] = cents(plan["outstanding"] + remaining)
            if late:
                plan["overdue"] = cents(plan["overdue"] + remaining)
                overdue = cents(overdue + remaining)
            elif remaining > ZERO and plan["next_due"] is None:
                plan["next_due"] = instalment.due_on
            outstanding = cents(outstanding + remaining)
            in_window = scope.start <= instalment.due_on <= scope.end
            if instalment.paid_on and scope.start <= instalment.paid_on <= scope.end:
                collected = cents(collected + paid)
            if in_window:
                due = cents(due + instalment.amount)
                if instalment.paid_on and instalment.paid_on <= scope.end:
                    collected_of_due = cents(collected_of_due + paid)
                due_rows.append(
                    Row(
                        str(instalment.id),
                        {
                            "quotation": quotation.number,
                            "customer": scope.names.party(quotation.customer_fk),
                            "sequence": "Deposit"
                            if instalment.is_deposit
                            else str(instalment.sequence),
                            "due_on": instalment.due_on,
                            "amount": instalment.amount,
                            "paid": paid,
                            "paid_on": instalment.paid_on,
                            "standing": "Waived"
                            if waived
                            else ("Paid" if remaining == ZERO else ("Overdue" if late else "Due")),
                        },
                        drill={"view": "quotation", "id": str(quotation.id)},
                    )
                )
        plan_rows = [
            Row(str(key), data, drill={"view": "quotation", "id": str(key)})
            for key, data in sorted(plans.items(), key=lambda item: -item[1]["outstanding"])
            if data["outstanding"] > ZERO or data["paid"] > ZERO
        ]
        return [
            Section(
                "instalments",
                "Instalment Collections",
                figures=[
                    Figure("due", "Fell due in the window", due),
                    Figure("collected", "Collected in the window", collected),
                    Figure(
                        "rate",
                        "Collected of what fell due",
                        share(collected_of_due, due),
                        ColumnKind.PERCENT,
                    ),
                    Figure("overdue", "Overdue today", overdue),
                    Figure("outstanding", "Outstanding on all plans", outstanding),
                ],
                tables=[
                    Table(
                        "due",
                        "Instalments due in the window",
                        [
                            Column("quotation", "Quotation"),
                            Column("customer", "Customer"),
                            Column("sequence", "Instalment"),
                            Column("due_on", "Due", ColumnKind.DATE),
                            Column("amount", "Amount", ColumnKind.MONEY),
                            Column("paid", "Paid", ColumnKind.MONEY),
                            Column("paid_on", "Paid on", ColumnKind.DATE),
                            Column("standing", "Standing"),
                        ],
                        due_rows,
                        empty="No instalment fell due in this window.",
                    ),
                    Table(
                        "plans",
                        "Payment plans",
                        [
                            Column("quotation", "Quotation"),
                            Column("customer", "Customer"),
                            Column("instalments", "Instalments", ColumnKind.COUNT),
                            Column("scheduled", "Scheduled", ColumnKind.MONEY),
                            Column("paid", "Paid", ColumnKind.MONEY),
                            Column("outstanding", "Outstanding", ColumnKind.MONEY),
                            Column("overdue", "Overdue", ColumnKind.MONEY),
                            Column("next_due", "Next due", ColumnKind.DATE),
                        ],
                        plan_rows,
                        empty="No accepted quotation is on a payment plan.",
                    ),
                ],
            )
        ]

    # -- affiliate commission -------------------------------------------------------------

    def _commission(self, scope: Scope) -> list[Section]:
        statement = select(CommissionAccrual).where(
            CommissionAccrual.business_id == scope.business_id,
            CommissionAccrual.accrued_on >= scope.start,
            CommissionAccrual.accrued_on <= scope.end,
        )
        if (affiliate := scope.filter_id("affiliate")) is not None:
            statement = statement.where(CommissionAccrual.affiliate_fk == affiliate)
        affiliates: dict[uuid.UUID, dict[str, Any]] = {}
        lines: list[Row] = []
        sums: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
        for accrual in self._db.scalars(statement.order_by(CommissionAccrual.accrued_on)).all():
            data = affiliates.setdefault(
                accrual.affiliate_fk,
                {
                    "accruals": 0,
                    "eligible": ZERO,
                    "accrued": ZERO,
                    "approved": ZERO,
                    "paid": ZERO,
                    "outstanding": ZERO,
                    "cancelled": 0,
                },
            )
            if accrual.state == "cancelled":
                data["cancelled"] += 1
                continue
            approved = accrual.state in ("approved", "payable", "paid")
            remaining = cents(max(ZERO, accrual.entitlement - accrual.settled_amount))
            values = {
                "eligible": accrual.basis_amount,
                "accrued": accrual.accrued_amount,
                "approved": accrual.accrued_amount if approved else ZERO,
                "paid": accrual.settled_amount,
                "outstanding": remaining,
            }
            data["accruals"] += 1
            for name, value in values.items():
                data[name] = cents(data[name] + value)
                sums[name] = cents(sums[name] + value)
            lines.append(
                Row(
                    str(accrual.id),
                    {
                        "accrued_on": accrual.accrued_on,
                        "affiliate": scope.names.party(accrual.affiliate_fk),
                        "reference": accrual.source_reference,
                        "eligible": accrual.basis_amount,
                        "rate": accrual.rate,
                        "accrued": accrual.accrued_amount,
                        "state": accrual.state.capitalize(),
                        "paid": accrual.settled_amount,
                    },
                    drill={"view": "quotation", "id": str(accrual.quotation_fk)},
                )
            )
        rows = [
            Row(
                str(party),
                {
                    "affiliate": scope.names.party(party),
                    **data,
                    "rate": share(data["accrued"], data["eligible"]),
                },
                drill={"view": "affiliate", "id": str(party)},
            )
            for party, data in sorted(affiliates.items(), key=lambda item: -item[1]["accrued"])
        ]
        money_columns = [
            Column("eligible", "Eligible sales", ColumnKind.MONEY),
            Column("rate", "Effective rate", ColumnKind.PERCENT),
            Column("accrued", "Accrued", ColumnKind.MONEY, compare=True),
            Column("approved", "Approved", ColumnKind.MONEY),
            Column("paid", "Paid", ColumnKind.MONEY),
            Column("outstanding", "Outstanding", ColumnKind.MONEY),
        ]
        return [
            Section(
                "commission",
                "Affiliate Commission",
                figures=[
                    Figure("eligible", "Eligible sales", sums["eligible"]),
                    Figure("accrued", "Accrued", sums["accrued"]),
                    Figure("paid", "Paid", sums["paid"]),
                    Figure("outstanding", "Outstanding", sums["outstanding"]),
                ],
                tables=[
                    Table(
                        "affiliates",
                        "By affiliate",
                        [
                            Column("affiliate", "Affiliate"),
                            Column("accruals", "Accruals", ColumnKind.COUNT),
                            *money_columns,
                        ],
                        rows,
                        Row(
                            "total",
                            {
                                "affiliate": "Total",
                                "accruals": sum(int(d["accruals"]) for d in affiliates.values()),
                                **{
                                    k: sums[k]
                                    for k in (
                                        "eligible",
                                        "accrued",
                                        "approved",
                                        "paid",
                                        "outstanding",
                                    )
                                },
                                "rate": share(sums["accrued"], sums["eligible"]),
                            },
                            emphasis="total",
                        ),
                    ),
                    Table(
                        "accruals",
                        "Accruals",
                        [
                            Column("accrued_on", "Accrued", ColumnKind.DATE),
                            Column("affiliate", "Affiliate"),
                            Column("reference", "Source"),
                            Column("eligible", "Eligible", ColumnKind.MONEY),
                            Column("rate", "Rate", ColumnKind.PERCENT),
                            Column("accrued", "Accrued", ColumnKind.MONEY),
                            Column("state", "Status"),
                            Column("paid", "Paid", ColumnKind.MONEY),
                        ],
                        lines,
                    ),
                ],
            )
        ]

    # -- inventory ------------------------------------------------------------------------

    def _inventory_position(self, scope: Scope) -> list[Section]:
        warehouse = scope.filter_id("warehouse")
        product = scope.filter_id("product")
        positions = [
            row
            for row in self._inventory.positions(scope.actor, warehouse_id=warehouse)
            if product is None or row.product_id == product
        ]
        rows = [
            Row(
                f"{row.product_id}:{row.warehouse_id}",
                {
                    "sku": row.sku,
                    "name": row.name,
                    "warehouse": row.warehouse,
                    "on_hand": row.usable,
                    "quarantine": row.quarantine,
                    "reserved": row.reserved,
                    "available": row.available,
                    "incoming": row.incoming,
                    "reorder": row.reorder_level,
                    "unit_cost": row.unit_cost,
                    "value": row.value,
                },
                drill={"view": "product", "id": str(row.product_id)},
            )
            for row in positions
        ]
        value = cents(sum((row.value for row in positions), ZERO))
        below = sum(
            1
            for row in positions
            if row.reorder_level is not None and row.available + row.incoming <= row.reorder_level
        )
        expiring = [
            row
            for row in self._inventory.expiry(scope.actor, within_days=90)
            if (product is None or row.product_id == product)
            and (warehouse is None or row.warehouse == scope.names.warehouse(warehouse))
        ]
        expiry_rows = [
            Row(
                str(row.batch_id),
                {
                    "batch": row.batch,
                    "sku": row.sku,
                    "name": row.name,
                    "warehouse": row.warehouse,
                    "expires_on": row.expires_on,
                    "days": row.days,
                    "quantity": row.quantity,
                },
                drill={"view": "product", "id": str(row.product_id)},
            )
            for row in expiring
        ]
        return [
            Section(
                "inventory",
                "Inventory Position",
                figures=[
                    Figure("lines", "Stock lines held", len(positions), ColumnKind.COUNT),
                    Figure("value", "Stock value", value, sensitive=True),
                    Figure("below", "At or below reorder level", below, ColumnKind.COUNT),
                    Figure(
                        "expiring",
                        "Batches expiring within 90 days",
                        len(expiring),
                        ColumnKind.COUNT,
                    ),
                ],
                tables=[
                    Table(
                        "positions",
                        "Stock by product and warehouse",
                        [
                            Column("sku", "SKU"),
                            Column("name", "Product"),
                            Column("warehouse", "Warehouse"),
                            Column("on_hand", "On hand", ColumnKind.QUANTITY),
                            Column("quarantine", "Quarantine", ColumnKind.QUANTITY),
                            Column("reserved", "Reserved", ColumnKind.QUANTITY),
                            Column("available", "Available", ColumnKind.QUANTITY),
                            Column("incoming", "Incoming", ColumnKind.QUANTITY),
                            Column("reorder", "Reorder level", ColumnKind.QUANTITY),
                            Column("unit_cost", "Unit cost", ColumnKind.MONEY, sensitive=True),
                            Column("value", "Value", ColumnKind.MONEY, sensitive=True),
                        ],
                        rows,
                        Row("total", {"sku": "Total", "value": value}, emphasis="total"),
                        note="Available excludes reserved and expired stock; incoming is on "
                        "issued purchase orders not yet received.",
                    ),
                    Table(
                        "expiry",
                        "Batches expiring within 90 days",
                        [
                            Column("batch", "Batch"),
                            Column("sku", "SKU"),
                            Column("name", "Product"),
                            Column("warehouse", "Warehouse"),
                            Column("expires_on", "Expires", ColumnKind.DATE),
                            Column("days", "Days left", ColumnKind.DAYS),
                            Column("quantity", "Quantity", ColumnKind.QUANTITY),
                        ],
                        expiry_rows,
                        empty="No batch expires within 90 days.",
                    ),
                ],
            )
        ]

    def _stock_movements(self, scope: Scope) -> list[Section]:
        from nickland.persistence.inventory_models import StockMovement

        statement = select(StockMovement).where(
            StockMovement.business_id == scope.business_id,
            StockMovement.occurred_on >= scope.start,
            StockMovement.occurred_on <= scope.end,
            StockMovement.movement_type.not_in(tuple(NOT_PHYSICAL)),
        )
        if (warehouse := scope.filter_id("warehouse")) is not None:
            statement = statement.where(StockMovement.warehouse_fk == warehouse)
        if (product := scope.filter_id("product")) is not None:
            statement = statement.where(StockMovement.product_fk == product)
        kinds: dict[str, dict[str, Any]] = {}
        detail: list[Row] = []
        for movement in self._db.scalars(
            statement.order_by(StockMovement.occurred_on, StockMovement.sequence)
        ).all():
            data = kinds.setdefault(
                movement.movement_type,
                {"operations": set(), "in": ZERO, "out": ZERO, "value": ZERO},
            )
            data["operations"].add(movement.operation_id)
            if movement.quantity >= ZERO:
                data["in"] += movement.quantity
            else:
                data["out"] -= movement.quantity
            data["value"] = cents(data["value"] + movement.value)
            if len(detail) < 1000:
                detail.append(
                    Row(
                        str(movement.id),
                        {
                            "on": movement.occurred_on,
                            "reference": movement.reference,
                            "type": MOVEMENT_LABELS.get(
                                movement.movement_type, movement.movement_type
                            ),
                            "sku": scope.names.sku(movement.product_fk),
                            "product": scope.names.product(movement.product_fk),
                            "warehouse": scope.names.warehouse(movement.warehouse_fk),
                            "state": movement.state.replace("_", " ").capitalize(),
                            "quantity": movement.quantity,
                            "value": movement.value,
                            "by": scope.names.user(movement.recorded_by_id),
                        },
                        drill={"view": "operation", "id": str(movement.operation_id)},
                    )
                )
        summary = [
            Row(
                kind,
                {
                    "type": MOVEMENT_LABELS.get(kind, kind),
                    "operations": len(data["operations"]),
                    "in": data["in"],
                    "out": data["out"],
                    "value": data["value"],
                },
            )
            for kind, data in sorted(
                kinds.items(), key=lambda item: MOVEMENT_LABELS.get(item[0], item[0])
            )
        ]
        operations = sum(len(data["operations"]) for data in kinds.values())
        return [
            Section(
                "movements",
                "Stock Movements",
                figures=[
                    Figure("operations", "Stock operations", operations, ColumnKind.COUNT),
                    Figure(
                        "received",
                        "Units received",
                        kinds.get("receipt", {}).get("in", ZERO),
                        ColumnKind.QUANTITY,
                    ),
                    Figure(
                        "delivered",
                        "Units delivered and sold",
                        sum(
                            (kinds.get(k, {}).get("out", ZERO) for k in ("issue", "delivery")), ZERO
                        ),
                        ColumnKind.QUANTITY,
                    ),
                ],
                tables=[
                    Table(
                        "summary",
                        "By kind of movement",
                        [
                            Column("type", "Movement"),
                            Column("operations", "Operations", ColumnKind.COUNT),
                            Column("in", "Units in", ColumnKind.QUANTITY),
                            Column("out", "Units out", ColumnKind.QUANTITY),
                            Column("value", "Net value", ColumnKind.MONEY, sensitive=True),
                        ],
                        summary,
                        note="A movement between states (into transit, out of quarantine) "
                        "counts out of one and into the other.",
                    ),
                    Table(
                        "detail",
                        "Movements",
                        [
                            Column("on", "Date", ColumnKind.DATE),
                            Column("reference", "Reference"),
                            Column("type", "Movement"),
                            Column("sku", "SKU"),
                            Column("product", "Product"),
                            Column("warehouse", "Warehouse"),
                            Column("state", "Stock state"),
                            Column("quantity", "Quantity", ColumnKind.QUANTITY),
                            Column("value", "Value", ColumnKind.MONEY, sensitive=True),
                            Column("by", "Recorded by"),
                        ],
                        detail,
                        note="The first 1,000 movements; narrow the window or filter to see "
                        "the rest."
                        if len(detail) >= 1000
                        else None,
                    ),
                ],
            )
        ]

    # -- delivery -------------------------------------------------------------------------

    def _delivery(self, scope: Scope) -> list[Section]:
        branch = scope.filter_id("branch")
        customer = scope.filter_id("customer")
        lines = (
            select(SalesOrderLine, SalesOrder)
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_fk)
            .where(
                SalesOrder.business_id == scope.business_id,
                SalesOrder.state.in_(OPEN_ORDER_STATES),
                SalesOrderLine.state.in_(("open", "backordered")),
            )
        )
        if branch is not None:
            lines = lines.where(SalesOrder.branch_id == branch)
        if customer is not None:
            lines = lines.where(SalesOrder.customer_fk == customer)
        assembly: list[Row] = []
        for line, order in self._db.execute(lines.order_by(SalesOrder.required_by)).all():
            wanted = line.quantity_ordered - line.quantity_cancelled
            awaiting = wanted - line.quantity_assembled
            if awaiting <= ZERO or line.quantity_dispatched >= wanted:
                continue
            assembly.append(
                Row(
                    str(line.id),
                    {
                        "order": order.number,
                        "customer": scope.names.party(order.customer_fk),
                        "required_by": order.required_by,
                        "item": line.description,
                        "awaiting": awaiting,
                        "received": line.quantity_received,
                        "late": "Late"
                        if order.required_by and order.required_by < scope.today
                        else None,
                    },
                    drill={"view": "sales_order", "id": str(order.id)},
                )
            )

        def shipments(*states: str) -> list[Shipment]:
            statement = select(Shipment).where(
                Shipment.business_id == scope.business_id, Shipment.state.in_(states)
            )
            if branch is not None:
                statement = statement.where(Shipment.branch_id == branch)
            if customer is not None:
                statement = statement.where(Shipment.customer_fk == customer)
            return list(self._db.scalars(statement.order_by(Shipment.number)).all())

        def shipment_row(shipment: Shipment, **extra: Any) -> Row:
            return Row(
                str(shipment.id),
                {
                    "number": shipment.number,
                    "customer": scope.names.party(shipment.customer_fk),
                    "state": shipment.state.replace("_", " ").capitalize(),
                    "prepared_on": shipment.prepared_on,
                    "destination": shipment.destination,
                    **extra,
                },
                drill={"view": "shipment", "id": str(shipment.id)},
            )

        waiting = [shipment_row(item) for item in shipments("prepared", "packed")]
        unsigned = [
            shipment_row(item, delivered_on=item.delivered_on)
            for item in shipments("delivered", "partially_delivered")
            if item.acknowledgement_fk is None
        ]
        exceptions = (
            select(DeliveryException, Shipment)
            .join(Shipment, Shipment.id == DeliveryException.shipment_fk)
            .where(
                Shipment.business_id == scope.business_id,
                DeliveryException.occurred_on >= scope.start,
                DeliveryException.occurred_on <= scope.end,
            )
        )
        if branch is not None:
            exceptions = exceptions.where(Shipment.branch_id == branch)
        if customer is not None:
            exceptions = exceptions.where(Shipment.customer_fk == customer)
        failed = [
            Row(
                str(exception.id),
                {
                    "number": shipment.number,
                    "customer": scope.names.party(shipment.customer_fk),
                    "occurred_on": exception.occurred_on,
                    "kind": exception.kind.replace("_", " ").capitalize(),
                    "quantity": exception.quantity,
                    "reason": exception.reason,
                    "replacement": "Owed" if exception.replacement_owed else None,
                },
                drill={"view": "shipment", "id": str(shipment.id)},
            )
            for exception, shipment in self._db.execute(
                exceptions.order_by(DeliveryException.occurred_on)
            ).all()
        ]
        failed_shipments = [
            shipment_row(item)
            for item in shipments("failed")
            if item.dispatched_on is None or scope.start <= item.dispatched_on <= scope.end
        ]
        shipment_columns = [
            Column("number", "Shipment"),
            Column("customer", "Customer"),
            Column("state", "Status"),
            Column("prepared_on", "Prepared", ColumnKind.DATE),
            Column("destination", "Destination"),
        ]
        return [
            Section(
                "delivery",
                "Delivery",
                figures=[
                    Figure("assembly", "Lines awaiting assembly", len(assembly), ColumnKind.COUNT),
                    Figure(
                        "dispatch", "Shipments awaiting dispatch", len(waiting), ColumnKind.COUNT
                    ),
                    Figure(
                        "unsigned", "Delivered, not acknowledged", len(unsigned), ColumnKind.COUNT
                    ),
                    Figure(
                        "failed", "Delivery exceptions in the window", len(failed), ColumnKind.COUNT
                    ),
                ],
                tables=[
                    Table(
                        "assembly",
                        "Awaiting assembly",
                        [
                            Column("order", "Order"),
                            Column("customer", "Customer"),
                            Column("required_by", "Required by", ColumnKind.DATE),
                            Column("item", "Item"),
                            Column("awaiting", "Awaiting", ColumnKind.QUANTITY),
                            Column("received", "Received", ColumnKind.QUANTITY),
                            Column("late", "Late"),
                        ],
                        assembly,
                        empty="Every open order line is assembled.",
                    ),
                    Table(
                        "dispatch",
                        "Awaiting dispatch",
                        shipment_columns,
                        waiting,
                        empty="No shipment is waiting to leave.",
                    ),
                    Table(
                        "unsigned",
                        "Awaiting acknowledgement",
                        [*shipment_columns, Column("delivered_on", "Delivered", ColumnKind.DATE)],
                        unsigned,
                        empty="Every delivery is signed for.",
                    ),
                    Table(
                        "failed",
                        "Failed deliveries and exceptions",
                        [
                            Column("number", "Shipment"),
                            Column("customer", "Customer"),
                            Column("occurred_on", "Date", ColumnKind.DATE),
                            Column("kind", "Exception"),
                            Column("quantity", "Quantity", ColumnKind.QUANTITY),
                            Column("reason", "Reason"),
                            Column("replacement", "Replacement"),
                        ],
                        failed,
                        empty="No delivery failed in this window.",
                    ),
                    *(
                        [
                            Table(
                                "failed_shipments",
                                "Failed shipments",
                                shipment_columns,
                                failed_shipments,
                            )
                        ]
                        if failed_shipments
                        else []
                    ),
                ],
                commentary=[
                    "Awaiting stages are as at today; exceptions are those recorded in the window."
                ],
            )
        ]


def merge_sections(
    current: list[Section], prior: list[Section], comparison: Comparison
) -> list[Section]:
    """Each section with its figures and tables set beside the prior window's."""
    before = {section.key: section for section in prior}
    for section in current:
        earlier = before.get(section.key)
        if earlier is None:
            continue
        compare_figures(section.figures, earlier.figures)
        tables = {table.key: table for table in earlier.tables}
        section.tables = [
            compare_table(table, tables[table.key], comparison) if table.key in tables else table
            for table in section.tables
        ]
    return current


__all__ = [
    "CatalogueEntry",
    "Names",
    "ReportParameters",
    "ReportService",
    "Scope",
    "merge_sections",
]
