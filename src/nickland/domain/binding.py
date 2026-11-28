"""Resolving a palette key against a document, and deciding what is visible.

The palette says what an administrator may bind. This says what each binding is worth
for one particular document, so that the renderer never has to know which key means
which attribute and the condition builder and the expression language read the same
values the page does.

The resolution is a table rather than a chain of ``if`` statements, because the
palette and the table have to stay in step: a key added to one and forgotten in the
other would render blank on a customer's invoice, and nothing would say so. The test
suite walks both and asserts they agree.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Final

from nickland.domain.documents import DocumentContext, Line, Party
from nickland.domain.expressions import EvaluationContext, Value
from nickland.domain.money import ZERO, money, present
from nickland.domain.templates import (
    Alignment,
    Condition,
    Join,
    Operator,
    TextCase,
    ValueFormat,
    ValueKind,
    Visibility,
    resolve,
)


def _party(party: Party | None) -> dict[str, Value]:
    if party is None:
        return {}
    return {
        "name": party.name,
        "reference": party.reference,
        "lines": "\n".join(party.address_lines),
        "telephone": party.telephone,
        "email": party.email,
        "tax_identifier": party.tax_identifier,
        "attention": party.attention,
    }


def document_values(context: DocumentContext) -> dict[str, Value]:
    """Every scalar binding, for one document."""
    issuer = context.issuer
    totals = context.totals
    delivery = context.delivery
    bill = _party(context.bill_to)
    ship = _party(context.ship_to)
    first_tax = totals.components[0] if totals.components else None

    values: dict[str, Value] = {
        # Business
        "business.name": issuer.name,
        "business.legal_name": issuer.name,
        "business.strapline": issuer.strapline,
        "business.logo": issuer.logo_data_uri,
        "business.address": "\n".join(issuer.address_lines),
        "business.telephone": issuer.telephone,
        "business.email": issuer.email,
        "business.website": issuer.website,
        "business.tax_identifier": issuer.tax_identifier,
        "business.registration_number": issuer.registration_number,
        "business.bank_details": "\n".join(
            f"{item.bank} {item.account_number or ''}".strip() for item in context.banking
        ),
        "business.mobile_money": "\n".join(
            f"{label} {value}" for label, value in context.mobile_money
        ),
        "business.branch": "\n".join(issuer.branch_address_lines),
        # Customer
        "customer.name": bill.get("name"),
        "customer.code": bill.get("reference"),
        "customer.bill_to": bill.get("lines"),
        "customer.ship_to": ship.get("lines") or bill.get("lines"),
        "customer.contact": bill.get("attention"),
        "customer.telephone": bill.get("telephone"),
        "customer.email": bill.get("email"),
        "customer.tax_identifier": bill.get("tax_identifier"),
        "customer.payment_terms": _term(context, "Payment terms"),
        "customer.credit_status": _term(context, "Credit standing"),
        # Supplier — populated on an inbound document, where the party is the supplier
        "supplier.name": bill.get("name") if not context.kind.is_outbound else None,
        "supplier.code": bill.get("reference") if not context.kind.is_outbound else None,
        "supplier.address": bill.get("lines") if not context.kind.is_outbound else None,
        "supplier.contact": bill.get("attention") if not context.kind.is_outbound else None,
        "supplier.tax_identifier": (
            bill.get("tax_identifier") if not context.kind.is_outbound else None
        ),
        "supplier.payment_terms": _term(context, "Payment terms"),
        # Transaction
        "transaction.number": context.number,
        "transaction.type": context.kind.heading,
        "transaction.issue_date": context.issued_on,
        "transaction.valid_until": context.valid_until or context.due_on,
        "transaction.customer_reference": context.customer_reference or context.order_reference,
        "transaction.request_reference": context.order_reference,
        "transaction.our_reference": context.our_reference,
        "transaction.state": None,
        "transaction.prepared_by": context.prepared_by,
        "transaction.approved_by": context.approved_by,
        "transaction.notes": "\n".join(context.notes),
        "transaction.currency": context.currency_code,
        # Payment
        "payment.plan": _term(context, "Payment plan"),
        "payment.deposit": _term_amount(context, "Deposit"),
        "payment.instalment_amount": _term_amount(context, "Instalment"),
        "payment.frequency": _term(context, "Frequency"),
        "payment.duration": _term(context, "Duration"),
        "payment.first_due_on": None,
        "payment.final_due_on": None,
        "payment.finance_charge": _term_amount(context, "Finance charge"),
        "payment.total_payable": totals.grand_total,
        "payment.terms": "\n".join(f"{label}: {value}" for label, value in context.terms),
        # Delivery
        "delivery.mode": delivery.mode if delivery else None,
        "delivery.address": ship.get("lines"),
        "delivery.carrier": delivery.carrier if delivery else None,
        "delivery.driver": delivery.driver if delivery else None,
        "delivery.vehicle": delivery.vehicle if delivery else None,
        "delivery.waybill_number": context.number if context.kind.value == "waybill" else None,
        "delivery.dispatch_date": delivery.dispatched_on if delivery else None,
        "delivery.package_count": (
            money(delivery.package_count) if delivery and delivery.package_count else None
        ),
        "delivery.tracking_reference": delivery.tracking_reference if delivery else None,
        "delivery.recipient": delivery.recipient if delivery else None,
        "delivery.acknowledged_on": None,
        # Tax
        "tax.summary": "\n".join(
            f"{item.name} {item.rate}: {item.amount}" for item in totals.components
        ),
        "tax.rule_name": first_tax.name if first_tax else None,
        "tax.rate": first_tax.rate if first_tax else None,
        "tax.basis": first_tax.basis if first_tax else None,
        "tax.amount": first_tax.amount if first_tax else totals.tax,
        "tax.treatment": None,
        "tax.authority": None,
        "tax.identifier": issuer.tax_identifier,
        # Totals
        "totals.subtotal": totals.subtotal,
        "totals.discount": totals.discount,
        "totals.transport": totals.transport,
        "totals.other_charges": totals.other_charges,
        "totals.tax": totals.tax,
        "totals.grand_total": totals.grand_total,
        "totals.units": totals.total_units,
        "totals.amount_in_words": totals.amount_in_words,
    }
    return values


def _term(context: DocumentContext, label: str) -> str | None:
    for name, value in context.terms:
        if name.casefold().startswith(label.casefold()):
            return value
    return None


def _term_amount(context: DocumentContext, label: str) -> Decimal | None:
    raw = _term(context, label)
    if raw is None:
        return None
    digits = "".join(character for character in raw if character.isdigit() or character == ".")
    try:
        return money(digits) if digits else None
    except (ValueError, ArithmeticError):
        return None


def line_values(line: Line, index: int, context: DocumentContext) -> dict[str, Value]:
    """Every per-line binding, for one row."""
    extended = money(line.unit_price * line.quantity)
    return {
        "line.number": money(index),
        "line.sku": line.sku,
        "line.description": line.name,
        "line.specification": line.note,
        "line.category": None,
        "line.manufacturer": line.manufacturer,
        "line.model": None,
        "line.batch": line.batch,
        "line.serial": None,
        "line.expiry": line.expiry,
        "line.unit": line.unit,
        "line.quantity": line.quantity,
        "line.unit_price": line.unit_price,
        "line.discount": money(extended * line.discount_rate),
        "line.tax": ZERO,
        "line.total": line.line_total,
        "line.weight": None,
        "line.volume": None,
    }


SUMMABLE_LINE_KEYS: Final[tuple[str, ...]] = (
    "line.quantity",
    "line.unit_price",
    "line.discount",
    "line.tax",
    "line.total",
)


def collections(context: DocumentContext) -> dict[str, list[Decimal]]:
    """The columns ``sum()`` may add up."""
    gathered: dict[str, list[Decimal]] = {key: [] for key in SUMMABLE_LINE_KEYS}
    for index, line in enumerate(context.lines, start=1):
        values = line_values(line, index, context)
        for key in SUMMABLE_LINE_KEYS:
            value = values.get(key)
            if isinstance(value, Decimal):
                gathered[key].append(value)
    return gathered


def evaluation_context(
    context: DocumentContext,
    *,
    line: Line | None = None,
    index: int = 0,
    extra: Mapping[str, Value] | None = None,
) -> EvaluationContext:
    """What an expression on this document may read."""
    values = document_values(context)
    if line is not None:
        values.update(line_values(line, index, context))
    if extra:
        values.update(extra)
    return EvaluationContext(values=values, collections=collections(context))


# --------------------------------------------------------------------------------------
# Conditions
# --------------------------------------------------------------------------------------


def holds(visibility: Visibility, values: Mapping[str, Value]) -> bool:
    """Whether a section, element or column appears on this document."""
    if visibility.mode == "never":
        return False
    if not visibility.is_conditional:
        return True
    if not visibility.conditions:
        return True
    outcomes = [_condition_holds(item, values) for item in visibility.conditions]
    return all(outcomes) if visibility.join is Join.ALL else any(outcomes)


def _condition_holds(condition: Condition, values: Mapping[str, Value]) -> bool:
    present_value = values.get(condition.field_key)
    operator = condition.operator

    if operator is Operator.IS_EMPTY:
        return _empty(present_value)
    if operator is Operator.IS_NOT_EMPTY:
        return not _empty(present_value)

    if operator in (Operator.IN, Operator.NOT_IN):
        folded = {item.strip().casefold() for item in condition.values}
        inside = _as_text(present_value).casefold() in folded
        return inside if operator is Operator.IN else not inside

    target = condition.value or ""
    if operator is Operator.EQUALS:
        return _as_text(present_value).casefold() == target.strip().casefold()
    if operator is Operator.NOT_EQUALS:
        return _as_text(present_value).casefold() != target.strip().casefold()

    left, right = _comparable(present_value, target)
    if left is None or right is None:
        return False
    return left > right if operator is Operator.GREATER_THAN else left < right


def _empty(value: Value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Decimal):
        return value == ZERO
    return False


def _as_text(value: Value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _comparable(value: Value, target: str) -> tuple[Decimal | None, Decimal | None]:
    """Both sides as numbers, or nothing.

    A greater-than against text is not an error the administrator should have to
    puzzle over at render time: it simply does not hold, and the condition builder
    will have offered the operator because the field is numeric in all but the odd
    case where the value is missing.
    """
    try:
        left = money(value) if isinstance(value, Decimal | int | str) and value != "" else None
        right = money(target.strip()) if target.strip() else None
    except (ValueError, ArithmeticError):
        return None, None
    return left, right


# --------------------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------------------

NBSP: Final = "\u00a0"
"""The thousands separator, so a grouped figure never breaks across a line."""

_MONTHS: Final = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_value(
    value: Value,
    spec: ValueFormat,
    *,
    kind: ValueKind = ValueKind.TEXT,
    currency_symbol: str = "",
    precision: int = 2,
) -> str:
    """Write a bound value out as the template asks for it."""
    if value is None:
        return ""

    if isinstance(value, date):
        return _format_date(value, spec.date_format)

    if isinstance(value, bool):
        return _case("Yes" if value else "No", spec.text_case)

    if isinstance(value, Decimal):
        if spec.precision is not None:
            places = spec.precision
        elif kind is ValueKind.QUANTITY:
            places = _significant(value)
        elif kind is ValueKind.COUNT:
            places = 0
        else:
            places = precision
        figure = present(value * 100, places) if kind is ValueKind.RATE else present(value, places)
        text = f"{figure:.{places}f}"
        if spec.thousands_separator:
            # Grouped with a non-breaking space and the symbol written against the
            # figure, which is how the rest of the product writes money: the screen,
            # the fixed sections of a document and its table must agree, or one page
            # carries two conventions and the reader notices before anyone else does.
            text = f"{figure:,.{places}f}".replace(",", NBSP)
        if spec.currency_symbol and currency_symbol:
            text = f"{currency_symbol}{text}"
        return f"{spec.prefix}{text}{spec.suffix}"

    return f"{spec.prefix}{_case(str(value), spec.text_case)}{spec.suffix}"


def _significant(value: Decimal) -> int:
    """How many decimal places a quantity actually needs, up to three.

    Two boxes is two, not 2.00; two and a half is 2.5. Padding a column of whole
    numbers to a fixed precision makes counted goods look measured.
    """
    whole = value.to_integral_value()
    if value == whole:
        return 0
    for places in (1, 2):
        if present(value, places) == value:
            return places
    return 3


def _format_date(value: date, style: str) -> str:
    if style == "iso":
        return value.isoformat()
    if style == "short":
        return f"{value.day:02d}/{value.month:02d}/{value.year}"
    return f"{value.day:02d} {_MONTHS[value.month - 1]} {value.year}"


def _case(text: str, style: TextCase) -> str:
    if style is TextCase.UPPER:
        return text.upper()
    if style is TextCase.LOWER:
        return text.lower()
    if style is TextCase.TITLE:
        return text.title()
    return text


def alignment_class(alignment: Alignment) -> str:
    return {Alignment.LEFT: "", Alignment.CENTRE: "mid", Alignment.RIGHT: "num"}[alignment]


def kind_of(key: str) -> ValueKind:
    """The palette's idea of what a key holds, for formatting."""
    try:
        return resolve(key).kind
    except Exception:
        return ValueKind.TEXT
