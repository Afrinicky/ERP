"""Document composition rules: kinds, page geometry and the render model.

Satisfies PRD §6.6, §6.7. A document is data rendered through a template; this module
defines what may be rendered, onto what, and in what shape the transaction must arrive.

Nothing here draws anything. The rules live apart from the renderer so that the same
model can be laid out on A4 for a customer and on a 58mm roll at the counter, and so
that what a document is allowed to disclose is decided once rather than per template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from nickland.domain.errors import ValidationError
from nickland.domain.money import ZERO, money


class DocumentKind(StrEnum):
    """The twelve documents that ship, per PRD §6.6 and §6.7."""

    PRO_FORMA = "pro_forma"
    QUOTATION = "quotation"
    INVOICE = "invoice"
    RECEIPT = "receipt"
    WAYBILL = "waybill"
    DELIVERY_NOTE = "delivery_note"
    PACKING_LIST = "packing_list"
    PURCHASE_ORDER = "purchase_order"
    GOODS_RECEIVED_NOTE = "goods_received_note"
    STATEMENT = "statement"
    PAYMENT_VOUCHER = "payment_voucher"
    CREDIT_NOTE = "credit_note"

    @property
    def heading(self) -> str:
        """The heading printed on the document.

        Named ``heading`` rather than ``title`` because ``str.title`` already exists
        on a ``StrEnum`` and shadowing it would make ``kind.title()`` mean two things.
        """
        return _HEADINGS[self]

    @property
    def prices_by_default(self) -> bool:
        """Whether money appears on this kind unless the template says otherwise.

        A waybill, delivery note or packing list accompanies goods and is read by a
        driver and a storekeeper, so prices start off: printing commercial terms on
        the carton's paperwork discloses them to everyone who handles it. Some
        customers require a priced delivery note for goods-inwards checking, so the
        template may turn them on. The default is the safe way round; the decision is
        the administrator's.
        """
        return self not in (
            DocumentKind.WAYBILL,
            DocumentKind.DELIVERY_NOTE,
            DocumentKind.PACKING_LIST,
        )

    @property
    def is_outbound(self) -> bool:
        """Whether the document is issued to a customer rather than to a supplier."""
        return self not in (
            DocumentKind.PURCHASE_ORDER,
            DocumentKind.GOODS_RECEIVED_NOTE,
            DocumentKind.PAYMENT_VOUCHER,
        )


_HEADINGS: dict[DocumentKind, str] = {
    DocumentKind.PRO_FORMA: "Pro Forma Invoice",
    DocumentKind.QUOTATION: "Quotation",
    DocumentKind.INVOICE: "Invoice",
    DocumentKind.RECEIPT: "Receipt",
    DocumentKind.WAYBILL: "Waybill",
    DocumentKind.DELIVERY_NOTE: "Delivery Note",
    DocumentKind.PACKING_LIST: "Packing List",
    DocumentKind.PURCHASE_ORDER: "Purchase Order",
    DocumentKind.GOODS_RECEIVED_NOTE: "Goods Received Note",
    DocumentKind.STATEMENT: "Statement of Account",
    DocumentKind.PAYMENT_VOUCHER: "Payment Voucher",
    DocumentKind.CREDIT_NOTE: "Credit Note",
}


class Classification(StrEnum):
    """Whether a template may bind fields that disclose cost or margin, per PRD §6.6."""

    CUSTOMER_FACING = "customer_facing"
    INTERNAL = "internal"


class PageFormat(StrEnum):
    """The stock a document is laid out for."""

    A4 = "a4"
    A5 = "a5"
    LETTER = "letter"
    LEGAL = "legal"
    ROLL_80 = "roll_80"
    """An 80mm thermal roll: the usual counter printer."""
    ROLL_58 = "roll_58"
    """A 58mm thermal roll: a small handheld."""

    @property
    def geometry(self) -> PageGeometry:
        return _GEOMETRY[self]

    @property
    def is_roll(self) -> bool:
        """A roll has no fixed height and no page numbering: it prints until it stops."""
        return self in (PageFormat.ROLL_80, PageFormat.ROLL_58)


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """A page's dimensions and margins, in millimetres.

    A roll's height is open: the paper advances until the content ends, so the height
    is given as ``None`` rather than as a number the printer would ignore.
    """

    label: str
    width_mm: Decimal
    height_mm: Decimal | None
    margin_top_mm: Decimal
    margin_right_mm: Decimal
    margin_bottom_mm: Decimal
    margin_left_mm: Decimal

    @property
    def printable_width_mm(self) -> Decimal:
        return self.width_mm - self.margin_left_mm - self.margin_right_mm

    @property
    def css_size(self) -> str:
        """The CSS ``@page size`` declaration for this stock."""
        if self.height_mm is None:
            # A very tall page with no footer: the renderer trims to the content, and
            # a roll printer feeds and cuts rather than paginating.
            return f"{self.width_mm}mm 3000mm"
        return f"{self.width_mm}mm {self.height_mm}mm"


_GEOMETRY: dict[PageFormat, PageGeometry] = {
    PageFormat.A4: PageGeometry(
        "A4",
        Decimal("210"),
        Decimal("297"),
        Decimal("14"),
        Decimal("14"),
        Decimal("16"),
        Decimal("14"),
    ),
    PageFormat.A5: PageGeometry(
        "A5",
        Decimal("148"),
        Decimal("210"),
        Decimal("10"),
        Decimal("10"),
        Decimal("12"),
        Decimal("10"),
    ),
    PageFormat.LETTER: PageGeometry(
        "Letter",
        Decimal("216"),
        Decimal("279"),
        Decimal("14"),
        Decimal("14"),
        Decimal("16"),
        Decimal("14"),
    ),
    PageFormat.LEGAL: PageGeometry(
        "Legal",
        Decimal("216"),
        Decimal("356"),
        Decimal("14"),
        Decimal("14"),
        Decimal("16"),
        Decimal("14"),
    ),
    # A thermal roll's printable width is narrower than the paper: 72mm of an 80mm
    # roll and 48mm of a 58mm one, which is what the print heads actually cover.
    PageFormat.ROLL_80: PageGeometry(
        "80mm roll",
        Decimal("80"),
        None,
        Decimal("3"),
        Decimal("4"),
        Decimal("6"),
        Decimal("4"),
    ),
    PageFormat.ROLL_58: PageGeometry(
        "58mm roll",
        Decimal("58"),
        None,
        Decimal("3"),
        Decimal("5"),
        Decimal("6"),
        Decimal("5"),
    ),
}


# --------------------------------------------------------------------------------------
# The render model
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Issuer:
    """The business issuing the document."""

    name: str
    strapline: str | None = None
    address_lines: tuple[str, ...] = ()
    branch_address_lines: tuple[str, ...] = ()
    telephone: str | None = None
    email: str | None = None
    website: str | None = None
    tax_identifier: str | None = None
    registration_number: str | None = None
    logo_data_uri: str | None = None
    """Embedded, never fetched: a document must render with no network, per PRD §9.4."""


@dataclass(frozen=True, slots=True)
class Party:
    """A bill-to or ship-to block."""

    name: str
    reference: str | None = None
    address_lines: tuple[str, ...] = ()
    telephone: str | None = None
    email: str | None = None
    tax_identifier: str | None = None
    attention: str | None = None


@dataclass(frozen=True, slots=True)
class Line:
    """One row of the item table."""

    sku: str
    name: str
    quantity: Decimal
    unit: str | None = None
    unit_price: Decimal = ZERO
    discount_rate: Decimal = ZERO
    """A fraction, not a percentage: 0.05 is five per cent."""
    line_total: Decimal = ZERO
    batch: str | None = None
    expiry: date | None = None
    manufacturer: str | None = None
    note: str | None = None

    @property
    def discount_percent(self) -> Decimal:
        return money(self.discount_rate * 100)


@dataclass(frozen=True, slots=True)
class TaxComponent:
    """One line of the tax summary."""

    name: str
    rate: Decimal
    basis: Decimal
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Totals:
    """The figures beneath the table.

    Every figure is supplied, never derived here: the pricing and tax engines compute
    them, and a document that recomputed them could disagree with the ledger.
    """

    total_units: Decimal = ZERO
    subtotal: Decimal = ZERO
    discount: Decimal = ZERO
    transport: Decimal = ZERO
    other_charges: Decimal = ZERO
    tax: Decimal = ZERO
    grand_total: Decimal = ZERO
    paid: Decimal | None = None
    balance_due: Decimal | None = None
    amount_in_words: str | None = None
    components: tuple[TaxComponent, ...] = ()


@dataclass(frozen=True, slots=True)
class Delivery:
    """Dispatch detail, for a waybill or delivery note."""

    mode: str | None = None
    carrier: str | None = None
    driver: str | None = None
    vehicle: str | None = None
    dispatched_on: date | None = None
    package_count: int | None = None
    tracking_reference: str | None = None
    recipient: str | None = None


@dataclass(frozen=True, slots=True)
class BankAccount:
    """One payment destination printed in the banking block."""

    bank: str
    branch: str | None = None
    account_name: str | None = None
    account_number: str | None = None
    currency: str | None = None
    swift: str | None = None


@dataclass(slots=True)
class DocumentContext:
    """Everything a template may bind against.

    One structure for every kind: the template decides what it shows, so a renderer
    never has to ask which kind it is holding in order to find a field.
    """

    kind: DocumentKind
    number: str
    issued_on: date
    issuer: Issuer
    currency_code: str = "GHS"
    currency_symbol: str = "GH₵"
    currency_precision: int = 2

    bill_to: Party | None = None
    ship_to: Party | None = None

    valid_until: date | None = None
    due_on: date | None = None
    order_reference: str | None = None
    customer_reference: str | None = None
    our_reference: str | None = None
    prepared_by: str | None = None
    approved_by: str | None = None

    lines: list[Line] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)
    delivery: Delivery | None = None

    terms: list[tuple[str, str]] = field(default_factory=list)
    """Label and value, printed as a definition list beneath the totals."""
    notes: list[str] = field(default_factory=list)
    banking: list[BankAccount] = field(default_factory=list)
    mobile_money: list[tuple[str, str]] = field(default_factory=list)

    watermark: str | None = None
    """Set on a preview, which is never issuable, per PRD §6.6."""
    footer_note: str | None = None

    def __post_init__(self) -> None:
        if not self.number.strip():
            raise ValidationError("A document carries a number.", field="number")


_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/\-._]{0,39}$")


def validate_document_number(number: str) -> str:
    """Normalise and validate a document number.

    A number appears on paper, in a filename and in a customer's own records, so it is
    restricted to characters that survive all three.
    """
    candidate = number.strip()
    if not _NUMBER.match(candidate):
        raise ValidationError(
            "A document number is 1 to 40 characters: letters, digits, and the "
            "separators / - . and _",
            field="number",
        )
    return candidate


@dataclass(frozen=True, slots=True)
class TemplateOptions:
    """What a template chooses to show.

    The full designer of PRD §6.6 edits a richer structure than this; what is here is
    the part the renderer actually reads, so a stored template is a real instruction
    rather than a decorative record. The editor, when it arrives, writes these same
    fields and adds to them.
    """

    page: PageFormat = PageFormat.A4
    classification: Classification = Classification.CUSTOMER_FACING

    show_prices: bool | None = None
    """Whether the price columns and the totals appear.

    ``None`` follows the kind's own default, which is the usual case. Setting it
    explicitly is how an administrator puts prices on a delivery note, or takes them
    off an internal copy of an invoice.
    """

    show_bill_to: bool = True
    show_ship_to: bool = True
    show_delivery: bool = False
    show_totals: bool = True
    show_terms: bool = True
    show_banking: bool = True
    show_notes: bool = True
    show_signatures: bool = True

    show_index_column: bool = True
    show_unit_column: bool = True
    show_discount_column: bool = True
    """Shown only when a line actually carries a discount, however this is set."""
    show_batch_and_expiry: bool = False
    show_manufacturer: bool = False

    terms: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    footer_note: str | None = None

    def prices_on(self, kind: DocumentKind) -> bool:
        """Whether this template shows money for the kind it belongs to."""
        return kind.prices_by_default if self.show_prices is None else self.show_prices

    def as_dict(self) -> dict[str, object]:
        """The stored form. Tuples become lists so the JSON round-trips."""
        return {
            "page": self.page.value,
            "classification": self.classification.value,
            "show_prices": self.show_prices,
            "show_bill_to": self.show_bill_to,
            "show_ship_to": self.show_ship_to,
            "show_delivery": self.show_delivery,
            "show_totals": self.show_totals,
            "show_terms": self.show_terms,
            "show_banking": self.show_banking,
            "show_notes": self.show_notes,
            "show_signatures": self.show_signatures,
            "show_index_column": self.show_index_column,
            "show_unit_column": self.show_unit_column,
            "show_discount_column": self.show_discount_column,
            "show_batch_and_expiry": self.show_batch_and_expiry,
            "show_manufacturer": self.show_manufacturer,
            "terms": [list(pair) for pair in self.terms],
            "notes": list(self.notes),
            "footer_note": self.footer_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TemplateOptions:
        """Read a stored definition, tolerating a field the writer did not know."""

        def flag(name: str, fallback: bool) -> bool:
            value = data.get(name, fallback)
            return bool(value)

        raw_terms = data.get("terms")
        raw_notes = data.get("notes")
        terms: tuple[tuple[str, str], ...] = ()
        if isinstance(raw_terms, list):
            terms = tuple(
                (str(pair[0]), str(pair[1]))
                for pair in raw_terms
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            )
        notes: tuple[str, ...] = ()
        if isinstance(raw_notes, list):
            notes = tuple(str(note) for note in raw_notes)
        return cls(
            page=PageFormat(str(data.get("page", PageFormat.A4.value))),
            show_prices=(None if data.get("show_prices") is None else bool(data["show_prices"])),
            classification=Classification(
                str(data.get("classification", Classification.CUSTOMER_FACING.value))
            ),
            show_bill_to=flag("show_bill_to", True),
            show_ship_to=flag("show_ship_to", True),
            show_delivery=flag("show_delivery", False),
            show_totals=flag("show_totals", True),
            show_terms=flag("show_terms", True),
            show_banking=flag("show_banking", True),
            show_notes=flag("show_notes", True),
            show_signatures=flag("show_signatures", True),
            show_index_column=flag("show_index_column", True),
            show_unit_column=flag("show_unit_column", True),
            show_discount_column=flag("show_discount_column", True),
            show_batch_and_expiry=flag("show_batch_and_expiry", False),
            show_manufacturer=flag("show_manufacturer", False),
            terms=terms,
            notes=notes,
            footer_note=(str(data["footer_note"]) if data.get("footer_note") is not None else None),
        )


# --------------------------------------------------------------------------------------
# Amount in words
# --------------------------------------------------------------------------------------

_UNITS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)
_SCALES = ((10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand"))


def _below_hundred(value: int) -> str:
    if value < 20:
        return _UNITS[value]
    tens, units = divmod(value, 10)
    return _TENS[tens] + (f"-{_UNITS[units]}" if units else "")


def _below_thousand(value: int) -> str:
    hundreds, rest = divmod(value, 100)
    parts = []
    if hundreds:
        parts.append(f"{_UNITS[hundreds]} hundred")
    if rest:
        parts.append(_below_hundred(rest))
    # Zero has no hundreds and no remainder, so the join would produce nothing; a
    # cheque for nothing still has to say so.
    return " and ".join(parts) if parts else _UNITS[0]


def spell_number(value: int) -> str:
    """Spell a non-negative integer in words."""
    if value < 0:
        raise ValidationError("A negative amount cannot be spelled.", field="amount")
    if value < 1000:
        return _below_thousand(value)
    parts: list[str] = []
    remainder = value
    for size, name in _SCALES:
        count, remainder = divmod(remainder, size)
        if count:
            parts.append(f"{_below_thousand(count)} {name}")
    if remainder:
        # "one thousand and one", but "ten thousand, nine hundred and thirty-two":
        # the conjunction joins a bare remainder, a comma joins a further group.
        joiner = " and " if remainder < 100 else ", "
        return (", ".join(parts) + joiner + _below_thousand(remainder)).strip()
    return ", ".join(parts)


def amount_in_words(amount: Decimal, *, unit: str = "cedis", subunit: str = "pesewas") -> str:
    """Spell a monetary amount, as a receipt and a cheque both require.

    The subunit is always stated, including when it is zero, because "forty cedis" and
    "forty cedis and no pesewas" are not equally safe to sign.
    """
    rounded = money(amount).quantize(Decimal("0.01"))
    negative = rounded < ZERO
    rounded = abs(rounded)
    whole = int(rounded)
    fraction = int((rounded - whole) * 100)
    words = f"{spell_number(whole)} {unit} and {spell_number(fraction)} {subunit}"
    if negative:
        words = f"minus {words}"
    return words[0].upper() + words[1:]
