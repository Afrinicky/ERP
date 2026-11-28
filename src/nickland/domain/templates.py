"""The document template: what an administrator composes, and what the renderer reads.

PRD §6.6 designates the designer a core capability: an administrator redesigns a
document and creates a new document type without a developer. That requires the layout
to be data rather than code, and it requires the data to be checkable — a stored
template that the renderer cannot read, or that discloses a margin to a customer, is
worse than no designer at all.

Three ideas shape this module.

A section is a composed region, not a free-floating box. Sections are ordered, titled,
conditionally visible, and of a known kind the renderer knows how to lay out. Absolute
positioning was the alternative and is the wrong tool for a business document: a pro
forma whose totals block sits wherever it was dropped looks like it was dropped there,
and the layout breaks the first time a line wraps.

The palette is the whole vocabulary. An element binds to a palette key or it does not
bind at all. A binding that is not in the palette fails validation, which is what makes
the import of a template from another installation checkable.

Internal fields are segregated in the palette itself rather than filtered at render
time. A customer-facing template cannot be published carrying one, and an expression on
such a template cannot reference one. The render-time assertion in the test suite is the
third of three controls, not the only one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Final

from nickland.domain.documents import GOODS_DOCUMENTS, Classification, DocumentKind, PageFormat
from nickland.domain.errors import ValidationError
from nickland.domain.expressions import Expression, parse

TEMPLATE_FORMAT: Final = 1
"""The version of the exported file format, not of a template."""

MAXIMUM_SECTIONS: Final = 24
MAXIMUM_ELEMENTS: Final = 40
MAXIMUM_COLUMNS: Final = 14
MAXIMUM_CONDITIONS: Final = 3
"""Per the designer specification: at most three terms, no nesting. A condition a
reader cannot hold in their head is a condition nobody will dare change later."""


# --------------------------------------------------------------------------------------
# The palette
# --------------------------------------------------------------------------------------


class FieldGroup(StrEnum):
    """The groups PRD §6.6 names, in the order the palette presents them."""

    BUSINESS = "business"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    TRANSACTION = "transaction"
    PRODUCT = "product"
    PAYMENT = "payment"
    DELIVERY = "delivery"
    TAX = "tax"
    TOTALS = "totals"
    CUSTOM = "custom"
    INTERNAL = "internal"


GROUP_LABELS: Final[dict[FieldGroup, str]] = {
    FieldGroup.BUSINESS: "Business",
    FieldGroup.CUSTOMER: "Customer",
    FieldGroup.SUPPLIER: "Supplier",
    FieldGroup.TRANSACTION: "Transaction",
    FieldGroup.PRODUCT: "Product",
    FieldGroup.PAYMENT: "Payment",
    FieldGroup.DELIVERY: "Delivery",
    FieldGroup.TAX: "Tax",
    FieldGroup.TOTALS: "Totals",
    FieldGroup.CUSTOM: "Custom fields",
    FieldGroup.INTERNAL: "Internal",
}


class ValueKind(StrEnum):
    """What a field holds, which decides its default alignment and format."""

    TEXT = "text"
    MONEY = "money"
    QUANTITY = "quantity"
    COUNT = "count"
    """A whole number of things. Distinct from a quantity, which may be fractional:
    a line number printed as 1.00 is a line number nobody asked to be measured."""
    RATE = "rate"
    DATE = "date"
    BOOLEAN = "boolean"
    IMAGE = "image"
    LINES = "lines"
    """A block of text over several lines, such as an address."""


@dataclass(frozen=True, slots=True)
class PaletteField:
    """One bindable value."""

    key: str
    label: str
    group: FieldGroup
    kind: ValueKind = ValueKind.TEXT
    per_line: bool = False
    """Whether the field varies by line, and so belongs in a table column rather than
    in the body of the document."""
    summable: bool = False
    """Whether ``sum()`` may add the column up."""

    @property
    def is_internal(self) -> bool:
        return self.group is FieldGroup.INTERNAL

    @property
    def numeric(self) -> bool:
        return self.kind in (
            ValueKind.MONEY,
            ValueKind.QUANTITY,
            ValueKind.COUNT,
            ValueKind.RATE,
        )


def _f(
    key: str,
    label: str,
    group: FieldGroup,
    kind: ValueKind = ValueKind.TEXT,
    *,
    per_line: bool = False,
    summable: bool = False,
) -> PaletteField:
    return PaletteField(
        key=key, label=label, group=group, kind=kind, per_line=per_line, summable=summable
    )


_B, _C, _S = FieldGroup.BUSINESS, FieldGroup.CUSTOMER, FieldGroup.SUPPLIER
_T, _P, _Y = FieldGroup.TRANSACTION, FieldGroup.PRODUCT, FieldGroup.PAYMENT
_D, _X, _O = FieldGroup.DELIVERY, FieldGroup.TAX, FieldGroup.TOTALS
_I = FieldGroup.INTERNAL

PALETTE: Final[tuple[PaletteField, ...]] = (
    # Business
    _f("business.name", "Business name", _B),
    _f("business.legal_name", "Registered legal name", _B),
    _f("business.strapline", "Strapline", _B),
    _f("business.logo", "Logo", _B, ValueKind.IMAGE),
    _f("business.address", "Address", _B, ValueKind.LINES),
    _f("business.telephone", "Telephone", _B),
    _f("business.email", "Electronic mail", _B),
    _f("business.website", "Website", _B),
    _f("business.tax_identifier", "Tax identification number", _B),
    _f("business.registration_number", "Registration number", _B),
    _f("business.bank_details", "Bank details", _B, ValueKind.LINES),
    _f("business.mobile_money", "Mobile Money details", _B, ValueKind.LINES),
    _f("business.branch", "Branch", _B),
    # Customer
    _f("customer.name", "Customer name", _C),
    _f("customer.code", "Customer code", _C),
    _f("customer.bill_to", "Bill to", _C, ValueKind.LINES),
    _f("customer.ship_to", "Ship to", _C, ValueKind.LINES),
    _f("customer.contact", "Contact", _C),
    _f("customer.telephone", "Telephone", _C),
    _f("customer.email", "Electronic mail", _C),
    _f("customer.tax_identifier", "Tax identification number", _C),
    _f("customer.payment_terms", "Payment terms", _C),
    _f("customer.credit_status", "Credit standing", _C),
    # Supplier
    _f("supplier.name", "Supplier name", _S),
    _f("supplier.code", "Supplier code", _S),
    _f("supplier.address", "Address", _S, ValueKind.LINES),
    _f("supplier.contact", "Contact", _S),
    _f("supplier.tax_identifier", "Tax identification number", _S),
    _f("supplier.payment_terms", "Payment terms", _S),
    # Transaction
    _f("transaction.number", "Document number", _T),
    _f("transaction.type", "Document type", _T),
    _f("transaction.issue_date", "Issue date", _T, ValueKind.DATE),
    _f("transaction.valid_until", "Validity or expiry date", _T, ValueKind.DATE),
    _f("transaction.customer_reference", "Customer order reference", _T),
    _f("transaction.request_reference", "Request for quotation reference", _T),
    _f("transaction.our_reference", "Our reference", _T),
    _f("transaction.state", "State", _T),
    _f("transaction.prepared_by", "Prepared by", _T),
    _f("transaction.approved_by", "Approved by", _T),
    _f("transaction.notes", "Notes", _T, ValueKind.LINES),
    _f("transaction.currency", "Currency", _T),
    # Product, per line
    _f("line.number", "Line number", _P, ValueKind.COUNT, per_line=True),
    _f("line.sku", "SKU", _P, per_line=True),
    _f("line.description", "Description", _P, per_line=True),
    _f("line.specification", "Specification", _P, ValueKind.LINES, per_line=True),
    _f("line.category", "Category", _P, per_line=True),
    _f("line.manufacturer", "Manufacturer", _P, per_line=True),
    _f("line.model", "Model", _P, per_line=True),
    _f("line.batch", "Batch or lot", _P, per_line=True),
    _f("line.serial", "Serial number", _P, per_line=True),
    _f("line.expiry", "Expiry date", _P, ValueKind.DATE, per_line=True),
    _f("line.unit", "Unit of measure", _P, per_line=True),
    _f("line.quantity", "Quantity", _P, ValueKind.QUANTITY, per_line=True, summable=True),
    _f("line.unit_price", "Unit price", _P, ValueKind.MONEY, per_line=True),
    _f("line.discount", "Discount", _P, ValueKind.MONEY, per_line=True, summable=True),
    _f("line.tax", "Tax", _P, ValueKind.MONEY, per_line=True, summable=True),
    _f("line.total", "Line total", _P, ValueKind.MONEY, per_line=True, summable=True),
    _f("line.weight", "Weight", _P, ValueKind.QUANTITY, per_line=True, summable=True),
    _f("line.volume", "Volume", _P, ValueKind.QUANTITY, per_line=True, summable=True),
    # Payment
    _f("payment.plan", "Payment plan", _Y),
    _f("payment.deposit", "Deposit", _Y, ValueKind.MONEY),
    _f("payment.instalment_amount", "Instalment amount", _Y, ValueKind.MONEY),
    _f("payment.frequency", "Instalment frequency", _Y),
    _f("payment.duration", "Repayment duration", _Y),
    _f("payment.first_due_on", "First due date", _Y, ValueKind.DATE),
    _f("payment.final_due_on", "Final due date", _Y, ValueKind.DATE),
    _f("payment.finance_charge", "Finance charge", _Y, ValueKind.MONEY),
    _f("payment.total_payable", "Total payable", _Y, ValueKind.MONEY),
    _f("payment.terms", "Payment terms", _Y, ValueKind.LINES),
    # Delivery
    _f("delivery.mode", "Delivery mode", _D),
    _f("delivery.address", "Delivery address", _D, ValueKind.LINES),
    _f("delivery.carrier", "Carrier", _D),
    _f("delivery.driver", "Driver", _D),
    _f("delivery.vehicle", "Vehicle", _D),
    _f("delivery.waybill_number", "Waybill number", _D),
    _f("delivery.dispatch_date", "Dispatch date", _D, ValueKind.DATE),
    _f("delivery.package_count", "Package count", _D, ValueKind.COUNT),
    _f("delivery.tracking_reference", "Tracking reference", _D),
    _f("delivery.recipient", "Recipient", _D),
    _f("delivery.acknowledged_on", "Acknowledgement date", _D, ValueKind.DATE),
    # Tax
    _f("tax.summary", "Tax summary by component", _X, ValueKind.LINES),
    _f("tax.rule_name", "Tax name", _X),
    _f("tax.rate", "Tax rate", _X, ValueKind.RATE),
    _f("tax.basis", "Tax basis", _X, ValueKind.MONEY),
    _f("tax.amount", "Tax amount", _X, ValueKind.MONEY),
    _f("tax.treatment", "Tax treatment", _X),
    _f("tax.authority", "Tax authority", _X),
    _f("tax.identifier", "Tax identifier", _X),
    # Totals
    _f("totals.subtotal", "Subtotal", _O, ValueKind.MONEY),
    _f("totals.discount", "Total discount", _O, ValueKind.MONEY),
    _f("totals.transport", "Transport", _O, ValueKind.MONEY),
    _f("totals.other_charges", "Other charges", _O, ValueKind.MONEY),
    _f("totals.tax", "Tax total", _O, ValueKind.MONEY),
    _f("totals.grand_total", "Grand total", _O, ValueKind.MONEY),
    _f("totals.units", "Total units", _O, ValueKind.COUNT),
    _f("totals.amount_in_words", "Amount in words", _O, ValueKind.LINES),
    # Internal — never offered to a customer-facing template
    _f("internal.supplier_cost", "Supplier cost", _I, ValueKind.MONEY),
    _f("internal.allocated_cost", "Allocated costs", _I, ValueKind.MONEY),
    _f("internal.landed_cost", "Landed cost", _I, ValueKind.MONEY),
    _f("internal.margin", "Margin", _I, ValueKind.MONEY),
    _f("internal.margin_rate", "Margin rate", _I, ValueKind.RATE),
    _f("internal.commission", "Affiliate commission", _I, ValueKind.MONEY),
    _f("internal.contribution", "Contribution", _I, ValueKind.MONEY),
    _f("internal.line_cost", "Line cost", _I, ValueKind.MONEY, per_line=True, summable=True),
    _f("internal.line_margin", "Line margin", _I, ValueKind.MONEY, per_line=True, summable=True),
)

BY_KEY: Final[dict[str, PaletteField]] = {item.key: item for item in PALETTE}

INTERNAL_KEYS: Final[frozenset[str]] = frozenset(item.key for item in PALETTE if item.is_internal)


def palette_for(
    classification: Classification, *, custom: Iterable[PaletteField] = ()
) -> tuple[PaletteField, ...]:
    """The fields a template of this classification may bind.

    A customer-facing template is not shown the internal group at all. Hiding a
    control is never a control on its own, which is why publication validates the
    bindings too — but a palette that offers a margin to someone composing an invoice
    is an invitation, and the cheapest control is not extending it.
    """
    offered = [
        item
        for item in PALETTE
        if classification is Classification.INTERNAL or not item.is_internal
    ]
    offered.extend(custom)
    return tuple(offered)


def resolve(key: str, *, custom: Mapping[str, PaletteField] | None = None) -> PaletteField:
    """The palette entry a binding names, or a refusal that says so."""
    if key in BY_KEY:
        return BY_KEY[key]
    if custom and key in custom:
        return custom[key]
    raise ValidationError(f"[{key}] is not a field in the palette.", field="binding")


# --------------------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------------------


class Operator(StrEnum):
    """The operators the condition builder offers, per the designer specification."""

    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    IN = "in"
    NOT_IN = "not_in"

    @property
    def takes_value(self) -> bool:
        return self not in (Operator.IS_EMPTY, Operator.IS_NOT_EMPTY)

    @property
    def takes_list(self) -> bool:
        return self in (Operator.IN, Operator.NOT_IN)


OPERATOR_LABELS: Final[dict[Operator, str]] = {
    Operator.IS_EMPTY: "is empty",
    Operator.IS_NOT_EMPTY: "is not empty",
    Operator.EQUALS: "equals",
    Operator.NOT_EQUALS: "does not equal",
    Operator.GREATER_THAN: "is greater than",
    Operator.LESS_THAN: "is less than",
    Operator.IN: "is one of",
    Operator.NOT_IN: "is not one of",
}


class Join(StrEnum):
    ALL = "all"
    ANY = "any"


@dataclass(frozen=True, slots=True)
class Condition:
    """One term: a field, an operator, and a value where the operator takes one."""

    field_key: str
    operator: Operator
    value: str | None = None
    values: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field_key,
            "operator": self.operator.value,
            "value": self.value,
            "values": list(self.values),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Condition:
        raw = data.get("values")
        return cls(
            field_key=str(data.get("field", "")),
            operator=Operator(str(data.get("operator", "is_not_empty"))),
            value=None if data.get("value") is None else str(data["value"]),
            values=tuple(str(item) for item in raw) if isinstance(raw, list) else (),
        )

    def describe(self, label: str | None = None) -> str:
        name = label or self.field_key
        if not self.operator.takes_value:
            return f"{name} {OPERATOR_LABELS[self.operator]}"
        if self.operator.takes_list:
            return f"{name} {OPERATOR_LABELS[self.operator]} {', '.join(self.values)}"
        return f"{name} {OPERATOR_LABELS[self.operator]} {self.value}"


@dataclass(frozen=True, slots=True)
class Visibility:
    """When a section, element or column appears.

    ``NEVER`` is kept as a state rather than expressed by deleting the thing, because
    an administrator turning a column off for one season wants it back without
    rebuilding it, and because a disabled optional column is how the default pro forma
    ships its manufacturer and batch columns.
    """

    mode: str = "always"
    """``always``, ``never`` or ``conditional``."""
    join: Join = Join.ALL
    conditions: tuple[Condition, ...] = ()

    @property
    def is_conditional(self) -> bool:
        return self.mode == "conditional"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "join": self.join.value,
            "conditions": [item.as_dict() for item in self.conditions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Visibility:
        if not data:
            return cls()
        raw = data.get("conditions")
        return cls(
            mode=str(data.get("mode", "always")),
            join=Join(str(data.get("join", "all"))),
            conditions=(
                tuple(Condition.from_dict(item) for item in raw) if isinstance(raw, list) else ()
            ),
        )


ALWAYS: Final = Visibility()
NEVER: Final = Visibility(mode="never")


# --------------------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------------------


class Alignment(StrEnum):
    LEFT = "left"
    CENTRE = "centre"
    RIGHT = "right"


class TextCase(StrEnum):
    AS_ENTERED = "as_entered"
    UPPER = "upper"
    LOWER = "lower"
    TITLE = "title"


@dataclass(frozen=True, slots=True)
class ValueFormat:
    """How a bound value is written out."""

    precision: int | None = None
    currency_symbol: bool = False
    thousands_separator: bool = True
    date_format: str = "long"
    """``long`` (06 Nov 2026), ``short`` (06/11/2026) or ``iso``."""
    text_case: TextCase = TextCase.AS_ENTERED
    prefix: str = ""
    suffix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision,
            "currency_symbol": self.currency_symbol,
            "thousands_separator": self.thousands_separator,
            "date_format": self.date_format,
            "text_case": self.text_case.value,
            "prefix": self.prefix,
            "suffix": self.suffix,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ValueFormat:
        if not data:
            return cls()
        precision = data.get("precision")
        return cls(
            precision=int(precision)
            if isinstance(precision, int | str) and precision != ""
            else None,
            currency_symbol=bool(data.get("currency_symbol", False)),
            thousands_separator=bool(data.get("thousands_separator", True)),
            date_format=str(data.get("date_format", "long")),
            text_case=TextCase(str(data.get("text_case", "as_entered"))),
            prefix=str(data.get("prefix", "")),
            suffix=str(data.get("suffix", "")),
        )


def default_format(palette_field: PaletteField) -> ValueFormat:
    """The format a field takes when it is first placed."""
    if palette_field.kind is ValueKind.MONEY:
        return ValueFormat(precision=2, currency_symbol=True)
    if palette_field.kind is ValueKind.COUNT:
        return ValueFormat(precision=0)
    if palette_field.kind is ValueKind.QUANTITY:
        # No fixed precision: a quantity of two prints as 2, and one of 2.5 as 2.5.
        # Padding every count to two places makes a column of whole numbers look
        # like a column of measurements.
        return ValueFormat(precision=None)
    if palette_field.kind is ValueKind.RATE:
        return ValueFormat(precision=2, suffix="%")
    return ValueFormat()


def default_alignment(palette_field: PaletteField) -> Alignment:
    return Alignment.RIGHT if palette_field.numeric else Alignment.LEFT


# --------------------------------------------------------------------------------------
# The document tree
# --------------------------------------------------------------------------------------


class SectionKind(StrEnum):
    """What a section is for. The renderer knows how to lay each of these out."""

    MASTHEAD = "masthead"
    TITLE = "title"
    PARTIES = "parties"
    ITEMS = "items"
    TOTALS = "totals"
    TAX_SUMMARY = "tax_summary"
    PAYMENT_PLAN = "payment_plan"
    DELIVERY = "delivery"
    TERMS = "terms"
    BANKING = "banking"
    NOTES = "notes"
    SIGNATURES = "signatures"
    CUSTOM = "custom"
    """A region of freely placed elements, for anything the fixed kinds do not cover."""


SECTION_LABELS: Final[dict[SectionKind, str]] = {
    SectionKind.MASTHEAD: "Masthead",
    SectionKind.TITLE: "Title and references",
    SectionKind.PARTIES: "Parties",
    SectionKind.ITEMS: "Items",
    SectionKind.TOTALS: "Totals",
    SectionKind.TAX_SUMMARY: "Tax summary",
    SectionKind.PAYMENT_PLAN: "Payment plan",
    SectionKind.DELIVERY: "Delivery",
    SectionKind.TERMS: "Terms",
    SectionKind.BANKING: "Payment details",
    SectionKind.NOTES: "Notes",
    SectionKind.SIGNATURES: "Signatures",
    SectionKind.CUSTOM: "Custom block",
}


class ElementKind(StrEnum):
    FIELD = "field"
    TEXT = "text"
    CALCULATED = "calculated"
    IMAGE = "image"
    SIGNATURE = "signature"
    QR = "qr"
    SPACER = "spacer"


@dataclass(frozen=True, slots=True)
class Element:
    """One placed thing inside a section."""

    id: str
    kind: ElementKind
    label: str = ""
    binding: str | None = None
    text: str = ""
    expression: str = ""
    format: ValueFormat = field(default_factory=ValueFormat)
    alignment: Alignment = Alignment.LEFT
    width_percent: int = 100
    emphasis: bool = False
    visibility: Visibility = ALWAYS

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "binding": self.binding,
            "text": self.text,
            "expression": self.expression,
            "format": self.format.as_dict(),
            "alignment": self.alignment.value,
            "width_percent": self.width_percent,
            "emphasis": self.emphasis,
            "visibility": self.visibility.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Element:
        return cls(
            id=str(data.get("id", "")),
            kind=ElementKind(str(data.get("kind", "text"))),
            label=str(data.get("label", "")),
            binding=None if data.get("binding") is None else str(data["binding"]),
            text=str(data.get("text", "")),
            expression=str(data.get("expression", "")),
            format=ValueFormat.from_dict(data.get("format")),
            alignment=Alignment(str(data.get("alignment", "left"))),
            width_percent=int(data.get("width_percent", 100)),
            emphasis=bool(data.get("emphasis", False)),
            visibility=Visibility.from_dict(data.get("visibility")),
        )


@dataclass(frozen=True, slots=True)
class Column:
    """One column of the items table."""

    id: str
    label: str
    binding: str | None = None
    expression: str = ""
    width_percent: int = 10
    alignment: Alignment = Alignment.LEFT
    format: ValueFormat = field(default_factory=ValueFormat)
    visibility: Visibility = ALWAYS
    subtotal: bool = False
    """Whether the column is added up under the table."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "binding": self.binding,
            "expression": self.expression,
            "width_percent": self.width_percent,
            "alignment": self.alignment.value,
            "format": self.format.as_dict(),
            "visibility": self.visibility.as_dict(),
            "subtotal": self.subtotal,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Column:
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            binding=None if data.get("binding") is None else str(data["binding"]),
            expression=str(data.get("expression", "")),
            width_percent=int(data.get("width_percent", 10)),
            alignment=Alignment(str(data.get("alignment", "left"))),
            format=ValueFormat.from_dict(data.get("format")),
            visibility=Visibility.from_dict(data.get("visibility")),
            subtotal=bool(data.get("subtotal", False)),
        )


@dataclass(frozen=True, slots=True)
class Section:
    """An ordered, titled, conditionally visible region."""

    id: str
    kind: SectionKind
    title: str = ""
    visibility: Visibility = ALWAYS
    elements: tuple[Element, ...] = ()
    columns: tuple[Column, ...] = ()
    bordered: bool = False
    keep_together: bool = False
    page_break_before: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "visibility": self.visibility.as_dict(),
            "elements": [item.as_dict() for item in self.elements],
            "columns": [item.as_dict() for item in self.columns],
            "bordered": self.bordered,
            "keep_together": self.keep_together,
            "page_break_before": self.page_break_before,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Section:
        elements = data.get("elements")
        columns = data.get("columns")
        return cls(
            id=str(data.get("id", "")),
            kind=SectionKind(str(data.get("kind", "custom"))),
            title=str(data.get("title", "")),
            visibility=Visibility.from_dict(data.get("visibility")),
            elements=(
                tuple(Element.from_dict(item) for item in elements)
                if isinstance(elements, list)
                else ()
            ),
            columns=(
                tuple(Column.from_dict(item) for item in columns)
                if isinstance(columns, list)
                else ()
            ),
            bordered=bool(data.get("bordered", False)),
            keep_together=bool(data.get("keep_together", False)),
            page_break_before=bool(data.get("page_break_before", False)),
        )


@dataclass(frozen=True, slots=True)
class PageConfiguration:
    """Stock, orientation, margins and type."""

    page: PageFormat = PageFormat.A4
    landscape: bool = False
    margin_top_mm: int = 14
    margin_right_mm: int = 14
    margin_bottom_mm: int = 14
    margin_left_mm: int = 14
    base_font_size_pt: int = 9
    heading_font_size_pt: int = 17
    accent_colour: str | None = None
    """A token name such as ``accent-primary``, or ``None`` for the theme's own."""
    show_page_numbers: bool = True
    repeat_table_header: bool = True
    carry_forward_totals: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page.value,
            "landscape": self.landscape,
            "margin_top_mm": self.margin_top_mm,
            "margin_right_mm": self.margin_right_mm,
            "margin_bottom_mm": self.margin_bottom_mm,
            "margin_left_mm": self.margin_left_mm,
            "base_font_size_pt": self.base_font_size_pt,
            "heading_font_size_pt": self.heading_font_size_pt,
            "accent_colour": self.accent_colour,
            "show_page_numbers": self.show_page_numbers,
            "repeat_table_header": self.repeat_table_header,
            "carry_forward_totals": self.carry_forward_totals,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> PageConfiguration:
        if not data:
            return cls()
        return cls(
            page=PageFormat(str(data.get("page", "a4"))),
            landscape=bool(data.get("landscape", False)),
            margin_top_mm=int(data.get("margin_top_mm", 14)),
            margin_right_mm=int(data.get("margin_right_mm", 14)),
            margin_bottom_mm=int(data.get("margin_bottom_mm", 14)),
            margin_left_mm=int(data.get("margin_left_mm", 14)),
            base_font_size_pt=int(data.get("base_font_size_pt", 9)),
            heading_font_size_pt=int(data.get("heading_font_size_pt", 17)),
            accent_colour=(
                None if data.get("accent_colour") is None else str(data["accent_colour"])
            ),
            show_page_numbers=bool(data.get("show_page_numbers", True)),
            repeat_table_header=bool(data.get("repeat_table_header", True)),
            carry_forward_totals=bool(data.get("carry_forward_totals", True)),
        )


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    """A whole template: its page, its sections, and the legacy option block.

    ``options`` carries the Phase 3.5 ``TemplateOptions`` dictionary. The renderer
    still reads it for the parts no editor touches yet, and keeping it means a
    template written before the designer existed still renders exactly as it did.
    """

    page: PageConfiguration = field(default_factory=PageConfiguration)
    sections: tuple[Section, ...] = ()
    footer_note: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    def section(self, kind: SectionKind) -> Section | None:
        for item in self.sections:
            if item.kind is kind:
                return item
        return None

    def bindings(self) -> set[str]:
        """Every palette key this template reads, from bindings and expressions alike."""
        keys: set[str] = set()
        for section in self.sections:
            keys.update(_condition_keys(section.visibility))
            for element in section.elements:
                if element.binding:
                    keys.add(element.binding)
                keys.update(_condition_keys(element.visibility))
                keys.update(_expression_keys(element.expression))
            for column in section.columns:
                if column.binding:
                    keys.add(column.binding)
                keys.update(_condition_keys(column.visibility))
                keys.update(_expression_keys(column.expression))
        return keys

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": TEMPLATE_FORMAT,
            "page": self.page.as_dict(),
            "sections": [item.as_dict() for item in self.sections],
            "footer_note": self.footer_note,
            "options": dict(self.options),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> TemplateDefinition:
        if not data:
            return cls()
        sections = data.get("sections")
        options = data.get("options")
        return cls(
            page=PageConfiguration.from_dict(data.get("page")),
            sections=(
                tuple(Section.from_dict(item) for item in sections)
                if isinstance(sections, list)
                else ()
            ),
            footer_note=str(data.get("footer_note", "")),
            options=dict(options) if isinstance(options, dict) else {},
        )


def _condition_keys(visibility: Visibility) -> set[str]:
    if not visibility.is_conditional:
        return set()
    return {item.field_key for item in visibility.conditions if item.field_key}


def _expression_keys(source: str) -> set[str]:
    if not source.strip():
        return set()
    try:
        return set(parse(source).references)
    except ValidationError:
        return set()


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing wrong with a template, and where."""

    where: str
    message: str
    is_blocking: bool = True


def validate(
    definition: TemplateDefinition,
    *,
    classification: Classification,
    custom: Mapping[str, PaletteField] | None = None,
) -> list[Finding]:
    """Everything wrong with a template, rather than the first thing wrong with it.

    A list, because an administrator fixing a template wants to see the work, not to
    discover one more fault each time they press publish.
    """
    findings: list[Finding] = []
    custom = custom or {}

    if not definition.sections:
        findings.append(Finding("template", "A template needs at least one section."))
    if len(definition.sections) > MAXIMUM_SECTIONS:
        findings.append(
            Finding("template", f"A template holds at most {MAXIMUM_SECTIONS} sections.")
        )

    seen: set[str] = set()
    for section in definition.sections:
        where = f"section {section.title or SECTION_LABELS[section.kind]}"
        if not section.id:
            findings.append(Finding(where, "Every section needs an identifier."))
        elif section.id in seen:
            findings.append(Finding(where, f"Two sections share the identifier {section.id!r}."))
        seen.add(section.id)

        findings.extend(_check_visibility(section.visibility, where, classification, custom))

        if len(section.elements) > MAXIMUM_ELEMENTS:
            findings.append(Finding(where, f"A section holds at most {MAXIMUM_ELEMENTS} elements."))
        for element in section.elements:
            findings.extend(_check_element(element, where, classification, custom))

        if section.kind is SectionKind.ITEMS:
            findings.extend(_check_columns(section, where, classification, custom))
        elif section.columns:
            findings.append(
                Finding(where, "Only the items section carries columns.", is_blocking=False)
            )

    return findings


def _check_element(
    element: Element,
    where: str,
    classification: Classification,
    custom: Mapping[str, PaletteField],
) -> list[Finding]:
    findings: list[Finding] = []
    place = f"{where}, element {element.label or element.id}"

    if element.kind is ElementKind.FIELD:
        if not element.binding:
            findings.append(Finding(place, "A field element binds to a palette field."))
        else:
            findings.extend(_check_binding(element.binding, place, classification, custom))
    if element.kind is ElementKind.CALCULATED:
        findings.extend(_check_expression(element.expression, place, classification, custom))
    if not 1 <= element.width_percent <= 100:
        findings.append(Finding(place, "A width is between 1 and 100 per cent."))

    findings.extend(_check_visibility(element.visibility, place, classification, custom))
    return findings


def _check_columns(
    section: Section,
    where: str,
    classification: Classification,
    custom: Mapping[str, PaletteField],
) -> list[Finding]:
    findings: list[Finding] = []
    if not section.columns:
        findings.append(Finding(where, "The items table needs at least one column."))
    if len(section.columns) > MAXIMUM_COLUMNS:
        findings.append(Finding(where, f"A table holds at most {MAXIMUM_COLUMNS} columns."))

    identifiers: set[str] = set()
    total = 0
    for column in section.columns:
        place = f"{where}, column {column.label or column.id}"
        if not column.label.strip():
            findings.append(Finding(place, "A column needs a heading."))
        if column.id in identifiers:
            findings.append(Finding(place, f"Two columns share the identifier {column.id!r}."))
        identifiers.add(column.id)

        if column.binding:
            findings.extend(_check_binding(column.binding, place, classification, custom))
            try:
                entry = resolve(column.binding, custom=custom)
                if not entry.per_line:
                    findings.append(
                        Finding(
                            place,
                            f"{entry.label} is the same on every line, so it belongs in the "
                            "body of the document rather than in a column.",
                        )
                    )
            except ValidationError:
                pass
        elif column.expression:
            findings.extend(_check_expression(column.expression, place, classification, custom))
        else:
            findings.append(Finding(place, "A column binds to a field or carries a formula."))

        if column.visibility.mode != "never":
            total += column.width_percent
        findings.extend(_check_visibility(column.visibility, place, classification, custom))

    if total > 100:
        findings.append(
            Finding(
                where,
                f"The visible columns total {total} per cent of the printable width, so "
                "the table will not fit. Reduce a column or hide one.",
                is_blocking=False,
            )
        )
    return findings


def _check_binding(
    key: str,
    where: str,
    classification: Classification,
    custom: Mapping[str, PaletteField],
) -> list[Finding]:
    try:
        entry = resolve(key, custom=custom)
    except ValidationError as error:
        return [Finding(where, error.message)]
    if entry.is_internal and classification is Classification.CUSTOMER_FACING:
        return [
            Finding(
                where,
                f"{entry.label} is an internal figure and cannot appear on a "
                "customer-facing document.",
            )
        ]
    return []


def _check_expression(
    source: str,
    where: str,
    classification: Classification,
    custom: Mapping[str, PaletteField],
) -> list[Finding]:
    if not source.strip():
        return [Finding(where, "A calculated field needs a formula.")]
    try:
        expression = parse(source)
    except ValidationError as error:
        return [Finding(where, error.message)]

    findings: list[Finding] = []
    for key in sorted(expression.references):
        findings.extend(_check_binding(key, where, classification, custom))
    return findings


def _check_visibility(
    visibility: Visibility,
    where: str,
    classification: Classification,
    custom: Mapping[str, PaletteField],
) -> list[Finding]:
    if not visibility.is_conditional:
        return []
    findings: list[Finding] = []
    if not visibility.conditions:
        findings.append(Finding(where, "A conditional rule needs at least one condition."))
    if len(visibility.conditions) > MAXIMUM_CONDITIONS:
        findings.append(Finding(where, f"A rule holds at most {MAXIMUM_CONDITIONS} conditions."))
    for condition in visibility.conditions:
        findings.extend(_check_binding(condition.field_key, where, classification, custom))
        if condition.operator.takes_list and not condition.values:
            findings.append(
                Finding(where, f"{OPERATOR_LABELS[condition.operator]} needs a list of values.")
            )
        elif (
            condition.operator.takes_value
            and not condition.operator.takes_list
            and condition.value is None
        ):
            findings.append(Finding(where, f"{OPERATOR_LABELS[condition.operator]} needs a value."))
    return findings


def assert_valid(
    definition: TemplateDefinition,
    *,
    classification: Classification,
    custom: Mapping[str, PaletteField] | None = None,
) -> None:
    """Refuse a template that cannot be published, naming everything wrong with it."""
    blocking = [
        item
        for item in validate(definition, classification=classification, custom=custom)
        if item.is_blocking
    ]
    if not blocking:
        return
    detail = "; ".join(f"{item.where}: {item.message}" for item in blocking[:6])
    more = "" if len(blocking) <= 6 else f" and {len(blocking) - 6} more"
    raise ValidationError(
        f"This template cannot be published. {detail}{more}.",
        field="definition",
        findings=[{"where": item.where, "message": item.message} for item in blocking],
    )


# --------------------------------------------------------------------------------------
# Expressions carried by a template
# --------------------------------------------------------------------------------------


def compile_expressions(definition: TemplateDefinition) -> dict[str, Expression]:
    """Parse every formula once, keyed by the element or column that carries it.

    Parsed at publication rather than at render time: a formula that cannot be parsed
    is a template fault, and a template fault belongs to the person composing it, not
    to the clerk printing an invoice at the counter.
    """
    compiled: dict[str, Expression] = {}
    for section in definition.sections:
        for element in section.elements:
            if element.kind is ElementKind.CALCULATED and element.expression.strip():
                compiled[element.id] = parse(element.expression)
        for column in section.columns:
            if column.expression.strip():
                compiled[column.id] = parse(column.expression)
    return compiled


# --------------------------------------------------------------------------------------
# Portability
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Portable:
    """An exported template. Structure and formatting; never business data."""

    format: int
    kind: str
    classification: str
    name: str
    definition: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "kind": self.kind,
            "classification": self.classification,
            "name": self.name,
            "definition": self.definition,
        }


def export_template(
    definition: TemplateDefinition,
    *,
    kind: DocumentKind | str,
    classification: Classification,
    name: str,
) -> Portable:
    """The portable form, carrying no business data.

    What makes this safe to hand to another installation is that a template holds
    bindings and not values: ``[customer.name]`` travels, the customer does not.
    """
    return Portable(
        format=TEMPLATE_FORMAT,
        kind=str(kind),
        classification=classification.value,
        name=name,
        definition=definition.as_dict(),
    )


@dataclass(frozen=True, slots=True)
class ImportReport:
    """What an import would do, and what it cannot resolve."""

    definition: TemplateDefinition
    kind: str
    classification: Classification
    name: str
    unresolved: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def is_acceptable(self) -> bool:
        return not self.unresolved and not any(item.is_blocking for item in self.findings)


def import_template(
    payload: Mapping[str, Any],
    *,
    custom: Mapping[str, PaletteField] | None = None,
    into: Classification | None = None,
) -> ImportReport:
    """Read an exported template and report every binding it cannot resolve.

    The specification requires import to validate every binding against the target
    installation's palette and to report what is unresolved *before* the template is
    accepted. A template that imports with a silently broken binding renders a blank
    where a figure should be, and nobody notices until a customer does.

    ``into`` is the classification of the document type the template is entering.
    Validation uses it in preference to the classification the file declares, because
    a file declares its own classification and a customer-facing document must not
    acquire a supplier cost merely because the file that carried it said "internal".
    """
    if not isinstance(payload, Mapping):
        raise ValidationError("An exported template is a single object.", field="file")
    declared = payload.get("format")
    if declared != TEMPLATE_FORMAT:
        raise ValidationError(
            f"This file is in template format {declared!r}; this installation reads "
            f"format {TEMPLATE_FORMAT}.",
            field="format",
        )
    raw = payload.get("definition")
    if not isinstance(raw, Mapping):
        raise ValidationError("The file carries no template definition.", field="definition")

    classification = Classification(str(payload.get("classification", "customer_facing")))
    definition = TemplateDefinition.from_dict(raw)
    known = set(BY_KEY) | set(custom or {})
    unresolved = tuple(sorted(key for key in definition.bindings() if key not in known))
    findings = tuple(validate(definition, classification=into or classification, custom=custom))

    return ImportReport(
        definition=definition,
        kind=str(payload.get("kind", "")),
        classification=classification,
        name=str(payload.get("name", "Imported template")),
        unresolved=unresolved,
        findings=findings,
    )


# --------------------------------------------------------------------------------------
# Editing operations
# --------------------------------------------------------------------------------------


def reorder(items: Sequence[Any], identifier: str, offset: int) -> tuple[Any, ...]:
    """Move one item within an ordered collection.

    Used for both sections and columns, and for both the drag and the keyboard path,
    so the two input modes cannot drift apart: the editor's arrow keys and its mouse
    call the same function.
    """
    current = list(items)
    index = next((position for position, item in enumerate(current) if item.id == identifier), None)
    if index is None:
        raise ValidationError(f"{identifier!r} is not in this template.", field="id")
    target = index + offset
    if not 0 <= target < len(current):
        return tuple(current)
    current.insert(target, current.pop(index))
    return tuple(current)


def add_column(section: Section, palette_field: PaletteField, *, identifier: str) -> Section:
    """Append a column bound to a palette field, with a derived label and format."""
    if not palette_field.per_line:
        raise ValidationError(
            f"{palette_field.label} is the same on every line, so it cannot be a column.",
            field="binding",
        )
    column = Column(
        id=identifier,
        label=palette_field.label,
        binding=palette_field.key,
        width_percent=_suggest_width(section.columns),
        alignment=default_alignment(palette_field),
        format=default_format(palette_field),
        subtotal=palette_field.summable,
    )
    return replace(section, columns=(*section.columns, column))


def remove_column(section: Section, identifier: str) -> Section:
    """Withdraw a column; the rest take the freed width in proportion.

    The total is held at whatever it was before, so a table deliberately laid out to
    80 per cent of the printable width stays that way — except that it is capped at
    100. Removing a column is usually how somebody fixes a table that had grown too
    wide, and redistributing an overflow faithfully would leave them exactly where
    they started.
    """
    remaining = [item for item in section.columns if item.id != identifier]
    if len(remaining) == len(section.columns):
        raise ValidationError(f"{identifier!r} is not a column here.", field="id")
    if not remaining:
        return replace(section, columns=())

    target = min(sum(item.width_percent for item in section.columns), 100)
    return replace(section, columns=tuple(_spread(remaining, target)))


def _spread(columns: Sequence[Column], target: int) -> list[Column]:
    """Share ``target`` per cent across columns in proportion to their present widths.

    Integer arithmetic with the remainder going to the widest columns, so the parts
    add up to the target exactly rather than to one or two per cent either side of it.
    """
    present = [max(item.width_percent, 1) for item in columns]
    whole = sum(present)
    shares = [width * target // whole for width in present]
    remainder = target - sum(shares)
    for index in sorted(range(len(shares)), key=lambda i: -present[i])[:remainder]:
        shares[index] += 1
    return [
        replace(item, width_percent=max(share, 1))
        for item, share in zip(columns, shares, strict=True)
    ]


def _suggest_width(existing: Sequence[Column]) -> int:
    """How wide a newly added column starts.

    Where there is room, the new column takes it, up to a fifth of the table. Where
    there is not, it starts at the minimum and the editor's width advisory fires: the
    alternative is silently reshaping columns the administrator set deliberately, and
    a warning they can act on beats a layout they did not ask for.
    """
    used = sum(item.width_percent for item in existing)
    return max(6, min(20, 100 - used)) if used < 100 else 6


# --------------------------------------------------------------------------------------
# The shipped composition
# --------------------------------------------------------------------------------------


def _column(
    identifier: str,
    label: str,
    binding: str,
    width: int,
    *,
    subtotal: bool = False,
    visibility: Visibility = ALWAYS,
) -> Column:
    entry = BY_KEY[binding]
    return Column(
        id=identifier,
        label=label,
        binding=binding,
        width_percent=width,
        alignment=default_alignment(entry),
        format=default_format(entry),
        visibility=visibility,
        subtotal=subtotal,
    )


_PRESENT: Final = Visibility(
    mode="conditional",
    conditions=(Condition(field_key="line.manufacturer", operator=Operator.IS_NOT_EMPTY),),
)
_HAS_BATCH: Final = Visibility(
    mode="conditional",
    conditions=(Condition(field_key="line.batch", operator=Operator.IS_NOT_EMPTY),),
)
_INSTALMENT: Final = Visibility(
    mode="conditional",
    conditions=(Condition(field_key="payment.plan", operator=Operator.EQUALS, value="instalment"),),
)
"""The two conditions the requirements name by example: manufacturer and expiry appear
only where populated, and instalment details appear only for an instalment sale."""


def default_columns(*, priced: bool) -> tuple[Column, ...]:
    """The items table as it ships.

    Manufacturer, batch and expiry ship as conditional columns rather than as absent
    ones, so an administrator who needs them turns them on instead of building them,
    and a line that happens to carry a batch shows it without anyone configuring
    anything. The widths add to the full printable width with every conditional column
    showing, so the table fits in the worst case rather than only in the common one.
    """
    columns = [
        _column("line_number", "#", "line.number", 4),
        _column("line_sku", "SKU", "line.sku", 12),
        _column("line_description", "Description", "line.description", 28 if priced else 52),
        _column("line_manufacturer", "Manufacturer", "line.manufacturer", 12, visibility=_PRESENT),
        _column("line_batch", "Batch", "line.batch", 8, visibility=_HAS_BATCH),
        _column("line_quantity", "Qty", "line.quantity", 6, subtotal=True),
        _column("line_unit", "Unit", "line.unit", 6),
    ]
    if priced:
        columns.extend(
            [
                _column("line_unit_price", "Unit price", "line.unit_price", 12),
                _column("line_total", "Amount", "line.total", 12, subtotal=True),
            ]
        )
    return tuple(columns)


def default_sections(kind: DocumentKind, *, priced: bool) -> tuple[Section, ...]:
    """The section order the reference layout uses, per the designer specification."""
    sections: list[Section] = [
        Section(id="masthead", kind=SectionKind.MASTHEAD),
        Section(id="title", kind=SectionKind.TITLE, title=kind.heading),
        Section(id="parties", kind=SectionKind.PARTIES, title="Parties"),
    ]
    if kind in GOODS_DOCUMENTS:
        sections.append(Section(id="delivery", kind=SectionKind.DELIVERY, title="Delivery"))
    sections.append(
        Section(
            id="items",
            kind=SectionKind.ITEMS,
            title="Items",
            columns=default_columns(priced=priced),
            keep_together=False,
        )
    )
    if priced:
        sections.append(Section(id="totals", kind=SectionKind.TOTALS, title="Totals"))
        sections.append(
            Section(id="tax_summary", kind=SectionKind.TAX_SUMMARY, title="Tax summary")
        )
        sections.append(
            Section(
                id="payment_plan",
                kind=SectionKind.PAYMENT_PLAN,
                title="Payment plan",
                visibility=_INSTALMENT,
            )
        )
    sections.append(Section(id="terms", kind=SectionKind.TERMS, title="Terms"))
    if priced:
        sections.append(Section(id="banking", kind=SectionKind.BANKING, title="Payment details"))
    sections.append(Section(id="notes", kind=SectionKind.NOTES, title="Notes"))
    sections.append(Section(id="signatures", kind=SectionKind.SIGNATURES, title="Signatures"))
    return tuple(sections)


def default_definition(
    kind: DocumentKind,
    *,
    priced: bool,
    page: PageFormat = PageFormat.A4,
    options: Mapping[str, Any] | None = None,
) -> TemplateDefinition:
    """A composed, editable default for a kind of document."""
    return TemplateDefinition(
        page=PageConfiguration(page=page),
        sections=default_sections(kind, priced=priced),
        options=dict(options or {}),
    )
