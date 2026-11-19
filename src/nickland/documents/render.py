"""Compose a document as HTML, and lay it out as PDF or as thermal text.

Three outputs from one model, per PRD §6.6:

* **HTML**, which the interface previews in place without a round trip to a viewer;
* **PDF**, laid out by WeasyPrint, which is what is filed, posted and reprinted;
* **plain text**, for a thermal printer driven directly rather than through a driver.

Rendering is a pure function of the context. The same context produces the same bytes,
which is what makes a stored snapshot worth storing.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from nickland.documents.styles import page_stylesheet, roll_stylesheet
from nickland.domain.binding import (
    alignment_class,
    collections,
    document_values,
    format_value,
    holds,
    kind_of,
    line_values,
)
from nickland.domain.documents import (
    GOODS_DOCUMENTS,
    BankAccount,
    DocumentContext,
    DocumentKind,
    Line,
    PageFormat,
    Party,
    TemplateOptions,
)
from nickland.domain.errors import ValidationError
from nickland.domain.expressions import EvaluationContext, Value, evaluate, parse
from nickland.domain.money import ZERO, money
from nickland.domain.templates import (
    Column,
    Element,
    ElementKind,
    Section,
    SectionKind,
    TemplateDefinition,
    ValueFormat,
    ValueKind,
    Visibility,
)

if TYPE_CHECKING:  # pragma: no cover - import cost is paid only when rendering a PDF
    pass


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """What a render produced."""

    content: bytes
    media_type: str
    filename: str


# --------------------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------------------


def _e(value: object) -> str:
    """Escape for HTML. Everything that reaches the markup passes through here."""
    return html.escape("" if value is None else str(value), quote=True)


def _money(amount: Decimal | None, context: DocumentContext, *, symbol: bool = True) -> str:
    if amount is None:
        return ""
    quantum = Decimal(1).scaleb(-context.currency_precision)
    fixed = amount.quantize(quantum)
    negative = fixed < 0
    whole, _, fraction = str(abs(fixed)).partition(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    body = f"{grouped}.{fraction}" if fraction else grouped
    prefix = f"{context.currency_symbol}" if symbol else ""
    return f"({prefix}{body})" if negative else f"{prefix}{body}"


def _quantity(value: Decimal) -> str:
    """Trim a quantity to its significant places: 1 rather than 1.000000."""
    trimmed = value.normalize()
    text = format(trimmed, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _day(value: date | None) -> str:
    return value.strftime("%d %b %Y") if value else ""


def _lines(party: Party) -> str:
    rows = [f'<span class="party__name">{_e(party.name)}</span>']
    if party.attention:
        rows.append(f"<span>For the attention of {_e(party.attention)}</span>")
    if party.reference:
        rows.append(f"<span>{_e(party.reference)}</span>")
    rows.extend(f"<span>{_e(line)}</span>" for line in party.address_lines)
    if party.telephone:
        rows.append(f"<span>{_e(party.telephone)}</span>")
    if party.email:
        rows.append(f"<span>{_e(party.email)}</span>")
    if party.tax_identifier:
        rows.append(f"<span>TIN {_e(party.tax_identifier)}</span>")
    return "".join(rows)


# --------------------------------------------------------------------------------------
# The printed page
# --------------------------------------------------------------------------------------


def _masthead(context: DocumentContext) -> str:
    issuer = context.issuer
    address = "".join(f"<span>{_e(line)}</span>" for line in issuer.address_lines)
    contact: list[str] = []
    if issuer.telephone:
        contact.append(_e(issuer.telephone))
    if issuer.email:
        contact.append(_e(issuer.email))
    if issuer.website:
        contact.append(_e(issuer.website))
    identifiers: list[str] = []
    if issuer.tax_identifier:
        identifiers.append(f"TIN {_e(issuer.tax_identifier)}")
    if issuer.registration_number:
        identifiers.append(f"Reg. {_e(issuer.registration_number)}")

    mark = (
        f'<div class="masthead__mark"><img src="{_e(issuer.logo_data_uri)}" alt="" /></div>'
        if issuer.logo_data_uri
        else ""
    )
    strapline = (
        f'<p class="masthead__strapline">{_e(issuer.strapline)}</p>' if issuer.strapline else ""
    )
    return f"""
<header class="masthead">
  <div class="masthead__identity">
    <h1 class="masthead__name">{_e(issuer.name)}</h1>
    {strapline}
    <p class="masthead__address">
      {address}
      {f"<span>{' · '.join(contact)}</span>" if contact else ""}
      {f"<span>{' · '.join(identifiers)}</span>" if identifiers else ""}
    </p>
  </div>
  {mark}
</header>
"""


def _references(context: DocumentContext, options: TemplateOptions) -> str:
    # "Document no." rather than the kind's own name: "Goods Received Note no."
    # wraps onto two lines in this column and the heading already says which kind
    # the reader is holding.
    rows: list[tuple[str, str]] = [
        ("Document no.", context.number),
        ("Issue date", _day(context.issued_on)),
    ]
    # A validity date belongs on an offer, not on a note that travels with goods, and
    # a tax number belongs where money is stated. Printing either elsewhere invites
    # the reader to think the document means something it does not.
    offers = context.kind in (DocumentKind.PRO_FORMA, DocumentKind.QUOTATION)
    if context.valid_until and offers:
        rows.append(("Valid until", _day(context.valid_until)))
    if context.due_on:
        rows.append(("Payment due", _day(context.due_on)))
    if context.order_reference:
        rows.append(("Order no.", context.order_reference))
    if context.customer_reference:
        rows.append(("Your reference", context.customer_reference))
    if context.our_reference:
        rows.append(("Our reference", context.our_reference))
    if context.issuer.tax_identifier and options.prices_on(context.kind):
        rows.append(("VAT number", context.issuer.tax_identifier))
    body = "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in rows)
    return f'<div class="references"><dl>{body}</dl></div>'


def _parties(context: DocumentContext, options: TemplateOptions) -> str:
    blocks: list[str] = []
    if context.bill_to and options.show_bill_to:
        label = "Supplier" if not context.kind.is_outbound else "Bill to"
        blocks.append(
            f'<div class="party"><p class="party__label">{label}</p><p>'
            f"{_lines(context.bill_to)}</p></div>"
        )
    if context.ship_to and options.show_ship_to:
        blocks.append(
            '<div class="party"><p class="party__label">Ship to</p><p>'
            f"{_lines(context.ship_to)}</p></div>"
        )
    delivery = context.delivery if options.show_delivery else None
    if delivery:
        rows = [
            ("Mode", delivery.mode),
            ("Carrier", delivery.carrier),
            ("Driver", delivery.driver),
            ("Vehicle", delivery.vehicle),
            ("Dispatched", _day(delivery.dispatched_on)),
            ("Packages", delivery.package_count),
            ("Tracking", delivery.tracking_reference),
        ]
        present = "".join(
            f"<span>{_e(label)}: {_e(value)}</span>" for label, value in rows if value
        )
        if present:
            blocks.append(
                f'<div class="party"><p class="party__label">Dispatch</p><p>{present}</p></div>'
            )
    return f'<section class="parties">{"".join(blocks)}</section>' if blocks else ""


def _items(context: DocumentContext, options: TemplateOptions) -> str:
    priced = options.prices_on(context.kind)
    # A discount column with nothing in it is a column of zeros: shown only when the
    # template allows it and at least one line actually carries one.
    discounted = (
        priced
        and options.show_discount_column
        and any(line.discount_rate for line in context.lines)
    )

    headings: list[str] = []
    if options.show_index_column:
        headings.append('<th class="idx">#</th>')
    headings.extend(["<th>SKU</th>", "<th>Description</th>", '<th class="num">Quantity</th>'])
    if priced:
        headings.append('<th class="num">Price</th>')
        if discounted:
            headings.append('<th class="num">Discount</th>')
        headings.append(f'<th class="num">Total ({_e(context.currency_code)})</th>')

    rows: list[str] = []
    for index, line in enumerate(context.lines, start=1):
        cells: list[str] = []
        if options.show_index_column:
            cells.append(f'<td class="idx">{index}</td>')
        unit = f" {_e(line.unit)}" if line.unit and options.show_unit_column else ""
        cells.extend(
            [
                f'<td class="sku">{_e(line.sku)}</td>',
                f"<td>{_e(line.name)}{_detail(line, options)}</td>",
                f'<td class="num">{_e(_quantity(line.quantity))}{unit}</td>',
            ]
        )
        if priced:
            cells.append(f'<td class="num">{_money(line.unit_price, context, symbol=False)}</td>')
            if discounted:
                percent = _quantity(line.discount_percent)
                cells.append(f'<td class="num">{_e(percent)}%</td>')
            cells.append(f'<td class="num">{_money(line.line_total, context, symbol=False)}</td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")

    return f"""
<table class="items">
  <thead><tr>{"".join(headings)}</tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
"""


def _detail(line: Line, options: TemplateOptions) -> str:
    parts: list[str] = []
    if line.manufacturer and options.show_manufacturer:
        parts.append(_e(line.manufacturer))
    if line.batch and options.show_batch_and_expiry:
        parts.append(f"Batch {_e(line.batch)}")
    if line.expiry and options.show_batch_and_expiry:
        parts.append(f"Expires {_day(line.expiry)}")
    if line.note:
        parts.append(_e(line.note))
    return f'<span class="items__note">{" · ".join(parts)}</span>' if parts else ""


def _totals(context: DocumentContext, options: TemplateOptions) -> str:
    if not options.prices_on(context.kind) or not options.show_totals:
        return ""
    totals = context.totals
    rows: list[str] = []

    def row(label: str, value: Decimal | None, *, css: str = "") -> None:
        if value is None:
            return
        attribute = f' class="{css}"' if css else ""
        rows.append(f"<tr{attribute}><td>{_e(label)}</td><td>{_money(value, context)}</td></tr>")

    if totals.total_units:
        rows.append(f"<tr><td>Total units</td><td>{_e(_quantity(totals.total_units))}</td></tr>")
    row("Subtotal", totals.subtotal)
    if totals.discount:
        row("Discount", -abs(totals.discount))
    if totals.transport:
        row("Transport", totals.transport)
    if totals.other_charges:
        row("Other charges", totals.other_charges)
    for component in totals.components:
        percent = _quantity(component.rate * 100)
        rows.append(
            f"<tr><td>{_e(component.name)} at {_e(percent)}%</td>"
            f"<td>{_money(component.amount, context)}</td></tr>"
        )
    if totals.tax and not totals.components:
        row("Tax", totals.tax)
    rows.append(
        f'<tr class="grand"><td>Total</td><td>{_money(totals.grand_total, context)}</td></tr>'
    )
    if totals.paid is not None:
        row("Paid", totals.paid, css="rule")
    if totals.balance_due is not None:
        row("Balance due", totals.balance_due)

    words = (
        f'<p class="in-words">{_e(totals.amount_in_words)}</p>' if totals.amount_in_words else ""
    )
    return f'<table class="totals">{"".join(rows)}</table>{words}'


def _banking(context: DocumentContext, options: TemplateOptions) -> str:
    if not options.show_banking:
        return ""
    if not context.banking and not context.mobile_money:
        return ""
    rows: list[str] = []
    for account in context.banking:
        rows.append(f"<dt>{_e(_bank_label(account))}</dt><dd>{_e(_bank_value(account))}</dd>")
    for label, value in context.mobile_money:
        rows.append(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>")
    return (
        '<section class="block"><p class="block__heading">Banking</p>'
        f"<dl>{''.join(rows)}</dl></section>"
    )


def _bank_label(account: BankAccount) -> str:
    parts = [account.bank]
    if account.branch:
        parts.append(account.branch)
    if account.currency:
        parts.append(f"({account.currency})")
    return " ".join(parts)


def _bank_value(account: BankAccount) -> str:
    parts = [part for part in (account.account_name, account.account_number) if part]
    if account.swift:
        parts.append(f"SWIFT {account.swift}")
    return " · ".join(parts)


def _terms(context: DocumentContext, options: TemplateOptions) -> str:
    terms = context.terms or list(options.terms)
    if not options.show_terms or not terms:
        return ""
    rows = "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in terms)
    return f'<section class="block"><p class="block__heading">Terms</p><dl>{rows}</dl></section>'


def _notes(context: DocumentContext, options: TemplateOptions) -> str:
    notes = context.notes or list(options.notes)
    if not options.show_notes or not notes:
        return ""
    first, *rest = notes
    body = f'<p class="standout">{_e(first)}</p>'
    body += "".join(f"<p>{_e(note)}</p>" for note in rest)
    return f'<section class="block">{body}</section>'


def _signatures(context: DocumentContext, options: TemplateOptions) -> str:
    if not options.show_signatures:
        return ""
    if context.kind in (DocumentKind.PRO_FORMA, DocumentKind.QUOTATION, DocumentKind.STATEMENT):
        blocks = [("Prepared by", context.prepared_by), ("Approved by", context.approved_by)]
    elif context.kind is DocumentKind.PICK_LIST:
        blocks = [("Picked by", context.prepared_by), ("Checked by", None)]
    elif context.kind in GOODS_DOCUMENTS:
        blocks = [("Dispatched by", context.prepared_by), ("Received by", None)]
    else:
        blocks = [
            ("Authorised by", context.approved_by or context.prepared_by),
            ("Received by", None),
        ]
    body = "".join(
        f'<div class="signature"><div class="signature__rule"></div>'
        f'<p class="signature__label">{_e(label)}'
        + (f" — {_e(name)}" if name else "")
        + "</p></div>"
        for label, name in blocks
    )
    return f'<section class="signatures">{body}</section>'


def render_html(
    context: DocumentContext,
    *,
    page: PageFormat | None = None,
    options: TemplateOptions | None = None,
    definition: TemplateDefinition | None = None,
) -> str:
    """Compose the document as a standalone HTML page.

    ``page`` overrides the template's own choice, which is how one template serves
    both an A4 reprint and an 80mm counter receipt.

    Where a composed ``definition`` is given, its sections decide what appears and in
    what order. Where it is not — a template stored before the designer existed, or a
    receipt going to a roll, where the layout is dictated by 48 millimetres of paper
    rather than by anybody's preference — the fixed composition is used.
    """
    chosen = options or TemplateOptions()
    stock = page or (definition.page.page if definition else chosen.page)
    if stock.is_roll:
        return _render_roll_html(context, stock, chosen)
    if definition is not None and definition.sections:
        return _compose(context, definition, chosen, stock)

    watermark = f'<div class="watermark">{_e(context.watermark)}</div>' if context.watermark else ""
    note = context.footer_note or chosen.footer_note
    footer = f'<div class="runningfoot">{_e(note)}</div>' if note else ""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8" />
<title>{_e(context.kind.heading)} {_e(context.number)}</title>
<style>{page_stylesheet(stock)}</style>
</head><body>
{watermark}
{footer}
{_masthead(context)}
<div class="title-row">
  <h2 class="title">{_e(context.kind.heading)}</h2>
  {_references(context, chosen)}
</div>
{_parties(context, chosen)}
{_items(context, chosen)}
<div class="closing">
  <div class="closing__left">{_notes(context, chosen)}{_terms(context, chosen)}\
{_banking(context, chosen)}</div>
  <div class="closing__right">{_totals(context, chosen)}</div>
</div>
{_signatures(context, chosen)}
</body></html>
"""


# --------------------------------------------------------------------------------------
# Composition from a stored definition
# --------------------------------------------------------------------------------------


def _visible(visibility: Visibility, values: dict[str, Value]) -> bool:
    return holds(visibility, values)


def _table_from_columns(
    context: DocumentContext, section: Section, values: dict[str, Value]
) -> str:
    """The items table as the template configures it.

    A column is dropped when its own rule says so, and a conditional column is kept
    when any line satisfies it: a manufacturer column that vanished because the first
    line lacked one would silently lose the three below it that have one.
    """
    rows_values = [
        line_values(line, index, context) for index, line in enumerate(context.lines, start=1)
    ]
    shown = [
        column
        for column in section.columns
        if column.visibility.mode != "never"
        and (
            not column.visibility.is_conditional
            or not rows_values
            or any(holds(column.visibility, {**values, **row}) for row in rows_values)
        )
    ]
    if not shown:
        return ""

    headings = "".join(
        f'<th class="{alignment_class(column.alignment)}" '
        f'style="width:{column.width_percent}%">{_e(column.label)}</th>'
        for column in shown
    )

    body: list[str] = []
    for row in rows_values:
        cells = "".join(
            f'<td class="{alignment_class(column.alignment)}">'
            f"{_e(_cell(context, column, {**values, **row}))}</td>"
            for column in shown
        )
        body.append(f"<tr>{cells}</tr>")

    foot = ""
    if any(column.subtotal for column in shown) and context.lines:
        totals_row = "".join(
            f'<td class="{alignment_class(column.alignment)}">'
            + (
                _e(
                    format_value(
                        money(
                            sum(
                                (
                                    value
                                    for row in rows_values
                                    if isinstance(value := row.get(column.binding), Decimal)
                                ),
                                ZERO,
                            )
                        ),
                        column.format,
                        kind=kind_of(column.binding or ""),
                        currency_symbol=context.currency_symbol,
                        precision=context.currency_precision,
                    )
                )
                if column.subtotal and column.binding
                else ""
            )
            + "</td>"
            for column in shown
        )
        foot = f"<tfoot><tr>{totals_row}</tr></tfoot>"

    return f"""
<table class="items">
  <thead><tr>{headings}</tr></thead>
  <tbody>{"".join(body)}</tbody>
  {foot}
</table>
"""


def _cell(context: DocumentContext, column: Column, values: dict[str, Value]) -> str:
    if column.expression.strip():
        return _calculated(context, column.expression, column.format, values)
    if not column.binding:
        return ""
    return format_value(
        values.get(column.binding),
        column.format,
        kind=kind_of(column.binding),
        currency_symbol=context.currency_symbol,
        precision=context.currency_precision,
    )


def _calculated(
    context: DocumentContext,
    source: str,
    spec: ValueFormat,
    values: dict[str, Value],
) -> str:
    """Evaluate a formula and write it out as the template asks.

    Formatting applies to a computed figure exactly as it does to a bound one.
    Without it a calculated total printed at the engine's six working decimal places
    — 142.000000 beside a column reading 142 — and a reader could not tell which of
    the two the document meant.
    """
    try:
        value = evaluate(
            parse(source), EvaluationContext(values=values, collections=collections(context))
        )
    except ValidationError:
        return "\u2014"
    if isinstance(value, Decimal):
        kind = ValueKind.MONEY if spec.currency_symbol else ValueKind.QUANTITY
    else:
        kind = ValueKind.TEXT
    return format_value(
        value,
        spec,
        kind=kind,
        currency_symbol=context.currency_symbol,
        precision=context.currency_precision,
    )


def _custom_section(context: DocumentContext, section: Section, values: dict[str, Value]) -> str:
    """A region of freely placed elements, for what the fixed kinds do not cover."""
    blocks: list[str] = []
    for element in section.elements:
        if not _visible(element.visibility, values):
            continue
        body = _element_body(context, element, values)
        if not body:
            continue
        if element.label:
            blocks.append(f"<dt>{_e(element.label)}</dt><dd>{body}</dd>")
        else:
            blocks.append(f"<dt></dt><dd>{body}</dd>")
    if not blocks:
        return ""
    title = f'<p class="block__heading">{_e(section.title)}</p>' if section.title else ""
    return f'<div class="block">{title}<dl>{"".join(blocks)}</dl></div>'


def _element_body(context: DocumentContext, element: Element, values: dict[str, Value]) -> str:
    if element.kind is ElementKind.SPACER:
        return '<div class="spacer"></div>'
    if element.kind is ElementKind.TEXT:
        return f"<p>{_e(element.text)}</p>"
    if element.kind is ElementKind.CALCULATED:
        return f"<p>{_e(_calculated(context, element.expression, element.format, values))}</p>"
    if element.kind is ElementKind.IMAGE:
        source = values.get(element.binding or "business.logo")
        return f'<img class="mark" src="{_e(str(source))}" alt="" />' if source else ""
    if element.kind is ElementKind.SIGNATURE:
        return f'<div class="rule"></div><span class="caption">{_e(element.label)}</span>'
    if element.kind is ElementKind.QR:
        # A QR code is drawn by the interface, which has a canvas; the document keeps
        # the value so the two cannot disagree about what was encoded.
        encoded = _e(str(values.get(element.binding or "", "")))
        return f'<span class="qr" data-value="{encoded}"></span>'
    if not element.binding:
        return ""
    written = format_value(
        values.get(element.binding),
        element.format,
        kind=kind_of(element.binding),
        currency_symbol=context.currency_symbol,
        precision=context.currency_precision,
    )
    return f"<p>{_e(written)}</p>"


_FIXED_SECTIONS: dict[SectionKind, object] = {}


def _compose(
    context: DocumentContext,
    definition: TemplateDefinition,
    options: TemplateOptions,
    stock: PageFormat,
) -> str:
    """Walk the sections in the order the template puts them in."""
    values = document_values(context)
    parts: list[str] = []
    closing_left: list[str] = []
    closing_right: list[str] = []
    signatures = ""
    """Held aside rather than appended in place: the signature blocks belong beneath
    the closing columns whatever order the sections are listed in, because a page
    whose signatures sit above its totals is not a page anybody signs."""

    for section in definition.sections:
        if not _visible(section.visibility, values):
            continue
        kind = section.kind
        if kind is SectionKind.MASTHEAD:
            parts.append(_masthead(context))
        elif kind is SectionKind.TITLE:
            parts.append(
                f'<div class="title-row"><h2 class="title">'
                f"{_e(section.title or context.kind.heading)}</h2>"
                f"{_references(context, options)}</div>"
            )
        elif kind is SectionKind.PARTIES:
            parts.append(_parties(context, options))
        elif kind is SectionKind.ITEMS:
            parts.append(_table_from_columns(context, section, values))
        elif kind is SectionKind.TOTALS:
            closing_right.append(_totals(context, options))
        elif kind is SectionKind.TERMS:
            closing_left.append(_terms(context, options))
        elif kind is SectionKind.BANKING:
            closing_left.append(_banking(context, options))
        elif kind is SectionKind.NOTES:
            closing_left.append(_notes(context, options))
        elif kind is SectionKind.SIGNATURES:
            signatures = _signatures(context, options)
        elif kind is SectionKind.DELIVERY:
            parts.append(_delivery_block(context))
        elif kind is SectionKind.PAYMENT_PLAN:
            closing_left.append(_plan_block(context, values))
        elif kind is SectionKind.TAX_SUMMARY:
            pass  # Printed as part of the totals block, where a reader expects it.
        else:
            parts.append(_custom_section(context, section, values))

    closing = ""
    if any(closing_left) or any(closing_right):
        closing = (
            f'<div class="closing"><div class="closing__left">{"".join(closing_left)}</div>'
            f'<div class="closing__right">{"".join(closing_right)}</div></div>'
        )

    watermark = f'<div class="watermark">{_e(context.watermark)}</div>' if context.watermark else ""
    note = context.footer_note or definition.footer_note or options.footer_note
    footer = f'<div class="runningfoot">{_e(note)}</div>' if note else ""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8" />
<title>{_e(context.kind.heading)} {_e(context.number)}</title>
<style>{page_stylesheet(stock)}</style>
</head><body>
{watermark}
{footer}
{"".join(parts)}
{closing}
{signatures}
</body></html>
"""


def _delivery_block(context: DocumentContext) -> str:
    delivery = context.delivery
    if delivery is None:
        return ""
    rows = [
        ("Mode", delivery.mode),
        ("Carrier", delivery.carrier),
        ("Driver", delivery.driver),
        ("Vehicle", delivery.vehicle),
        ("Dispatched", _day(delivery.dispatched_on) if delivery.dispatched_on else None),
        ("Packages", str(delivery.package_count) if delivery.package_count else None),
        ("Tracking", delivery.tracking_reference),
    ]
    cells = "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in rows if value)
    return f'<section class="parties"><dl class="terms">{cells}</dl></section>' if cells else ""


def _plan_block(context: DocumentContext, values: dict[str, Value]) -> str:
    """The instalment disclosure, shown only where the sale is on a plan."""
    rows = [(label, value) for label, value in context.terms if "instal" in label.casefold()]
    if not rows:
        return ""
    cells = "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in rows)
    return f'<dl class="terms"><dt class="terms__head">Payment plan</dt><dd></dd>{cells}</dl>'


# --------------------------------------------------------------------------------------
# The thermal roll
# --------------------------------------------------------------------------------------


def _render_roll_html(context: DocumentContext, page: PageFormat, options: TemplateOptions) -> str:
    issuer = context.issuer
    contact = " · ".join(part for part in (issuer.telephone, issuer.email) if part)
    narrow = page is PageFormat.ROLL_58

    header = [f'<p class="receipt__name">{_e(issuer.name)}</p>']
    if issuer.strapline and not narrow:
        header.append(f'<p class="receipt__strapline">{_e(issuer.strapline)}</p>')
    for line in issuer.address_lines[: 1 if narrow else 2]:
        header.append(f'<p class="receipt__contact">{_e(line)}</p>')
    if contact:
        header.append(f'<p class="receipt__contact">{_e(contact)}</p>')
    if issuer.tax_identifier:
        header.append(f'<p class="receipt__contact">TIN {_e(issuer.tax_identifier)}</p>')

    meta = [
        ("No.", context.number),
        (
            "Date",
            context.issued_on.strftime("%d/%m/%y %H:%M") if False else _day(context.issued_on),
        ),
    ]
    if context.bill_to:
        meta.append(("Customer", context.bill_to.name))
    if context.prepared_by:
        meta.append(("Served by", context.prepared_by))
    meta_rows = "".join(
        f'<div class="pair"><span>{_e(label)}</span><span>{_e(value)}</span></div>'
        for label, value in meta
    )

    items: list[str] = []
    for item in context.lines:
        quantity = _quantity(item.quantity)
        price = _money(item.unit_price, context, symbol=False)
        total = _money(item.line_total, context, symbol=False)
        items.append(
            f'<div class="line"><span class="line__name">{_e(item.name)}</span>'
            f'<div class="line__detail"><span>{_e(quantity)} x {_e(price)}</span>'
            f"<span>{_e(total)}</span></div></div>"
        )

    totals = context.totals
    closing: list[str] = []

    def pair(label: str, value: Decimal | None, *, css: str = "") -> None:
        if value is None:
            return
        attribute = f' class="pair {css}"' if css else ' class="pair"'
        closing.append(
            f"<div{attribute}><span>{_e(label)}</span><span>{_money(value, context)}</span></div>"
        )

    pair("Subtotal", totals.subtotal)
    if totals.discount:
        pair("Discount", -abs(totals.discount))
    for component in totals.components:
        pair(component.name, component.amount)
    if totals.tax and not totals.components:
        pair("Tax", totals.tax)
    pair("TOTAL", totals.grand_total, css="total")
    pair("Paid", totals.paid)
    if totals.balance_due is not None:
        owed = totals.balance_due < 0
        pair("Balance due" if owed else "Change", abs(totals.balance_due))

    words = (
        f'<p class="receipt__words">{_e(totals.amount_in_words)}</p>'
        if totals.amount_in_words and not narrow
        else ""
    )
    note = context.footer_note or options.footer_note
    footer = f'<p class="receipt__footer">{_e(note)}</p>' if note else ""
    watermark = (
        f'<p class="receipt__title">{_e(context.watermark)}</p>' if context.watermark else ""
    )

    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8" />
<title>{_e(context.kind.heading)} {_e(context.number)}</title>
<style>{roll_stylesheet(page)}</style>
</head><body><div class="receipt">
{"".join(header)}
<p class="receipt__title">{_e(context.kind.heading)}</p>
{watermark}
<hr class="rule" />
{meta_rows}
<hr class="rule" />
{"".join(items)}
<hr class="rule" />
{"".join(closing)}
{words}
<hr class="rule" />
{footer}
<div class="receipt__feed"></div>
</div></body></html>
"""


def render_thermal_text(context: DocumentContext, *, width: int = 42) -> str:
    """Render for a thermal printer driven directly, with no layout engine.

    Some counter printers are fed plain text over a serial or USB connection and do
    their own line breaking. Laying the receipt out here, to a fixed character width,
    produces the same receipt as the PDF without requiring the printer to understand
    anything but characters. 42 columns is the usual 80mm default and 32 the 58mm one.
    """
    issuer = context.issuer
    out: list[str] = []

    def centre(text: str) -> None:
        for chunk in _wrap(text, width):
            out.append(chunk.center(width))

    def pair(left: str, right: str) -> None:
        room = width - len(right)
        out.append(f"{left[: max(room - 1, 0)]:<{max(room, 0)}}{right}")

    def rule(character: str = "-") -> None:
        out.append(character * width)

    centre(issuer.name.upper())
    if issuer.strapline:
        centre(issuer.strapline)
    for line in issuer.address_lines:
        centre(line)
    if issuer.telephone:
        centre(issuer.telephone)
    if issuer.tax_identifier:
        centre(f"TIN {issuer.tax_identifier}")
    out.append("")
    centre(context.kind.heading.upper())
    if context.watermark:
        centre(f"*** {context.watermark.upper()} ***")
    rule("=")

    pair("No.", context.number)
    pair("Date", _day(context.issued_on))
    if context.bill_to:
        pair("Customer", context.bill_to.name[: width - 10])
    if context.prepared_by:
        pair("Served by", context.prepared_by[: width - 11])
    rule()

    for item in context.lines:
        out.extend(_wrap(item.name, width))
        quantity = _quantity(item.quantity)
        price = _money(item.unit_price, context, symbol=False)
        pair(f"  {quantity} x {price}", _money(item.line_total, context, symbol=False))
    rule()

    totals = context.totals
    pair("Subtotal", _money(totals.subtotal, context))
    if totals.discount:
        pair("Discount", _money(-abs(totals.discount), context))
    for component in totals.components:
        pair(component.name, _money(component.amount, context))
    if totals.tax and not totals.components:
        pair("Tax", _money(totals.tax, context))
    rule("=")
    pair("TOTAL", _money(totals.grand_total, context))
    if totals.paid is not None:
        pair("Paid", _money(totals.paid, context))
    if totals.balance_due is not None:
        owed = totals.balance_due < 0
        pair("Balance due" if owed else "Change", _money(abs(totals.balance_due), context))
    rule("=")

    if totals.amount_in_words:
        for chunk in _wrap(totals.amount_in_words, width):
            out.append(chunk)
    if context.footer_note:
        out.append("")
        centre(context.footer_note)
    out.append("")
    return "\n".join(out) + "\n"


def _wrap(text: str, width: int) -> list[str]:
    """Break text to a column width, preferring word boundaries."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(word) > width:
            lines.append(word[:width])
            word = word[width:]
        current = word
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------------------


def render_pdf(
    context: DocumentContext,
    *,
    page: PageFormat | None = None,
    options: TemplateOptions | None = None,
    definition: TemplateDefinition | None = None,
) -> RenderedDocument:
    """Lay the document out as a PDF.

    WeasyPrint is imported here rather than at module scope: it pulls in Pango and
    Cairo, which cost a noticeable fraction of a second, and the interface imports
    this module on every request whether or not a PDF is wanted.
    """
    from weasyprint import HTML

    markup = render_html(context, page=page, options=options, definition=definition)
    content = HTML(string=markup, base_url=None).write_pdf()
    assert content is not None
    safe = context.number.replace("/", "-")
    return RenderedDocument(
        content=content,
        media_type="application/pdf",
        filename=f"{context.kind.value}-{safe}.pdf",
    )
