"""Organisation rules: numbering sequences and custom-field definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from nickland.domain.errors import ValidationError


class DocumentKind(StrEnum):
    """Document types requiring an independent numbering sequence, per PRD 6.7."""

    QUOTATION = "quotation"
    PRO_FORMA = "pro_forma"
    SALES_ORDER = "sales_order"
    SALES_INVOICE = "sales_invoice"
    RECEIPT = "receipt"
    CREDIT_NOTE = "credit_note"
    PURCHASE_ORDER = "purchase_order"
    GOODS_RECEIPT = "goods_receipt"
    SUPPLIER_INVOICE = "supplier_invoice"
    DEBIT_NOTE = "debit_note"
    WAYBILL = "waybill"
    DELIVERY_NOTE = "delivery_note"
    PACKING_LIST = "packing_list"
    PICK_LIST = "pick_list"
    PAYMENT_VOUCHER = "payment_voucher"
    STATEMENT = "statement"
    JOURNAL = "journal"
    POS_SALE = "pos_sale"
    STOCK_ADJUSTMENT = "stock_adjustment"
    STOCK_TRANSFER = "stock_transfer"
    LOAN_AGREEMENT = "loan_agreement"
    EXPENSE = "expense"


DOCUMENT_KIND_LABELS: dict[DocumentKind, tuple[str, str]] = {
    DocumentKind.QUOTATION: ("Quotation", "QTN"),
    DocumentKind.PRO_FORMA: ("Pro forma invoice", "PFI"),
    DocumentKind.SALES_ORDER: ("Sales order", "SO"),
    DocumentKind.SALES_INVOICE: ("Sales invoice", "INV"),
    DocumentKind.RECEIPT: ("Receipt", "RCT"),
    DocumentKind.CREDIT_NOTE: ("Credit note", "CRN"),
    DocumentKind.PURCHASE_ORDER: ("Purchase order", "PO"),
    DocumentKind.GOODS_RECEIPT: ("Goods received note", "GRN"),
    DocumentKind.SUPPLIER_INVOICE: ("Supplier invoice", "SIN"),
    DocumentKind.DEBIT_NOTE: ("Debit note", "DBN"),
    DocumentKind.WAYBILL: ("Waybill", "WB"),
    DocumentKind.DELIVERY_NOTE: ("Delivery note", "DN"),
    DocumentKind.PACKING_LIST: ("Packing list", "PL"),
    DocumentKind.PICK_LIST: ("Pick list", "PK"),
    DocumentKind.PAYMENT_VOUCHER: ("Payment voucher", "PV"),
    DocumentKind.STATEMENT: ("Statement", "STM"),
    DocumentKind.JOURNAL: ("Journal entry", "JNL"),
    DocumentKind.POS_SALE: ("Register sale", "POS"),
    DocumentKind.STOCK_ADJUSTMENT: ("Stock adjustment", "ADJ"),
    DocumentKind.STOCK_TRANSFER: ("Stock transfer", "TRF"),
    DocumentKind.LOAN_AGREEMENT: ("Loan agreement", "LN"),
    DocumentKind.EXPENSE: ("Expense", "EXP"),
}


class ResetCadence(StrEnum):
    """When a sequence counter returns to its starting value."""

    NEVER = "never"
    ANNUALLY = "annually"
    MONTHLY = "monthly"


_PLACEHOLDER = re.compile(r"\{(prefix|branch|year|year2|month|day|counter(?::\d+)?)\}")
_ALLOWED = frozenset({"prefix", "branch", "year", "year2", "month", "day", "counter"})


@dataclass(frozen=True, slots=True)
class NumberPattern:
    """A document-number pattern.

    Supported placeholders: ``{prefix}``, ``{branch}``, ``{year}``, ``{year2}``,
    ``{month}``, ``{day}`` and ``{counter}`` or ``{counter:N}`` for zero padding.
    """

    template: str

    def __post_init__(self) -> None:
        found = {match.group(1).split(":")[0] for match in _PLACEHOLDER.finditer(self.template)}
        unknown = set(re.findall(r"\{([a-z0-9_:]+)\}", self.template)) - {
            match.group(1) for match in _PLACEHOLDER.finditer(self.template)
        }
        if unknown:
            raise ValidationError(
                f"Unknown placeholder(s) in pattern: {sorted(unknown)}", field="pattern"
            )
        if "counter" not in found:
            raise ValidationError("A numbering pattern must include {counter}.", field="pattern")

    def render(
        self,
        *,
        counter: int,
        prefix: str = "",
        branch_code: str = "",
        on: date,
    ) -> str:
        """Produce a document number."""

        def substitute(match: re.Match[str]) -> str:
            token = match.group(1)
            if token.startswith("counter"):
                _, _, width = token.partition(":")
                return str(counter).zfill(int(width)) if width else str(counter)
            return {
                "prefix": prefix,
                "branch": branch_code,
                "year": f"{on.year:04d}",
                "year2": f"{on.year % 100:02d}",
                "month": f"{on.month:02d}",
                "day": f"{on.day:02d}",
            }[token]

        return _PLACEHOLDER.sub(substitute, self.template)


DEFAULT_PATTERN = NumberPattern("{prefix}-{year}-{counter:4}")


def period_key(cadence: ResetCadence, on: date) -> str:
    """The counter bucket for a given date under a reset cadence."""
    if cadence is ResetCadence.NEVER:
        return "all"
    if cadence is ResetCadence.ANNUALLY:
        return f"{on.year:04d}"
    return f"{on.year:04d}-{on.month:02d}"


class CustomFieldType(StrEnum):
    """Types available to administrator-defined fields, per PRD 6.1."""

    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    AMOUNT = "amount"
    DATE = "date"
    BOOLEAN = "boolean"
    CHOICE = "choice"
    MULTI_CHOICE = "multi_choice"


class CustomFieldTarget(StrEnum):
    """Records that accept custom fields, per PRD 6.1."""

    PRODUCT = "product"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    AFFILIATE = "affiliate"
    QUOTATION = "quotation"
    SALES_ORDER = "sales_order"
    PURCHASE_ORDER = "purchase_order"
    GOODS_RECEIPT = "goods_receipt"
    DISPATCH = "dispatch"
    EXPENSE = "expense"


_CODE = re.compile(r"^[a-z][a-z0-9_]{1,38}[a-z0-9]$")


def validate_code(code: str, *, field: str = "code") -> str:
    """Normalise and validate a machine code. Returns the normalised form."""
    candidate = code.strip().lower().replace("-", "_").replace(" ", "_")
    if not _CODE.match(candidate):
        raise ValidationError(
            "Code must be 3 to 40 characters, beginning with a letter and using "
            "lowercase letters, digits and underscores.",
            field=field,
        )
    return candidate
