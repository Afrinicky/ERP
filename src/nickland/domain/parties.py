"""Party rules: customers, suppliers, affiliates, terms and credit.

PRD 6.1 requires customers, suppliers and custom fields upon them. PRD 29.3 requires
credit limits, payment terms and ageing; the credit assessment here is the one the
sales phases will consult before accepting an order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from nickland.domain.errors import ValidationError
from nickland.domain.money import ZERO, money


class PartyKind(StrEnum):
    """What role a party plays. One organisation may hold several."""

    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    AFFILIATE = "affiliate"


class PartyType(StrEnum):
    """Whether the party is a person or an organisation.

    The distinction governs which identifiers apply and how the party is addressed on
    a document.
    """

    INDIVIDUAL = "individual"
    ORGANISATION = "organisation"


class PaymentTermKind(StrEnum):
    """How a due date is derived from an invoice date."""

    IMMEDIATE = "immediate"
    NET_DAYS = "net_days"
    """A number of days from the invoice date."""
    END_OF_MONTH = "end_of_month"
    """A number of days after the end of the invoice month."""
    DAY_OF_MONTH = "day_of_month"
    """A nominated day of the following month."""


class TaxTreatment(StrEnum):
    """How tax applies to a party, per PRD 19.1.

    The taxation engine of Phase 4 reads this; it is captured here because it is a
    property of the party, not of the transaction.
    """

    STANDARD = "standard"
    EXEMPT = "exempt"
    """Relieved by law or by a certificate. Requires evidence."""
    ZERO_RATED = "zero_rated"
    WITHHOLDING_AGENT = "withholding_agent"
    """A customer who withholds tax from payments to this business."""


class CreditStanding(StrEnum):
    """The outcome of a credit assessment."""

    WITHIN_LIMIT = "within_limit"
    APPROACHING_LIMIT = "approaching_limit"
    OVER_LIMIT = "over_limit"
    OVERDUE = "overdue"
    ON_HOLD = "on_hold"
    """Placed on hold by an authorised decision, whatever the balance."""
    NO_CREDIT = "no_credit"
    """Cash only: no credit limit is configured."""

    @property
    def permits_credit_sale(self) -> bool:
        return self in (CreditStanding.WITHIN_LIMIT, CreditStanding.APPROACHING_LIMIT)


@dataclass(frozen=True, slots=True)
class PaymentTerms:
    """When an invoice falls due."""

    kind: PaymentTermKind
    days: int = 0

    def __post_init__(self) -> None:
        if self.days < 0:
            raise ValidationError("Payment terms cannot be negative.", field="days")
        if self.days > 365:
            raise ValidationError(
                "Payment terms beyond a year require a financing agreement rather "
                "than a credit term.",
                field="days",
            )
        if self.kind is PaymentTermKind.DAY_OF_MONTH and not 1 <= self.days <= 28:
            raise ValidationError(
                "A nominated day of the month must be between 1 and 28, so the term "
                "falls in every month.",
                field="days",
            )

    def due_date(self, invoice_date: date) -> date:
        """The date an invoice issued on ``invoice_date`` falls due."""
        if self.kind is PaymentTermKind.IMMEDIATE:
            return invoice_date
        if self.kind is PaymentTermKind.NET_DAYS:
            return invoice_date + timedelta(days=self.days)
        if self.kind is PaymentTermKind.END_OF_MONTH:
            return _end_of_month(invoice_date) + timedelta(days=self.days)
        # A nominated day of the following month.
        following = _end_of_month(invoice_date) + timedelta(days=1)
        return following.replace(day=self.days)

    def describe(self) -> str:
        if self.kind is PaymentTermKind.IMMEDIATE:
            return "Due on issue"
        if self.kind is PaymentTermKind.NET_DAYS:
            return f"Net {self.days} days"
        if self.kind is PaymentTermKind.END_OF_MONTH:
            return "End of month" if self.days == 0 else f"{self.days} days after end of month"
        return f"Day {self.days} of the following month"


def _end_of_month(on: date) -> date:
    """The last day of the month containing ``on``."""
    if on.month == 12:
        return on.replace(day=31)
    return on.replace(month=on.month + 1, day=1) - timedelta(days=1)


@dataclass(frozen=True, slots=True)
class CreditAssessment:
    """The result of assessing a customer's credit position."""

    standing: CreditStanding
    limit: Decimal
    outstanding: Decimal
    overdue: Decimal
    available: Decimal
    detail: str

    @property
    def permits_credit_sale(self) -> bool:
        return self.standing.permits_credit_sale


def assess_credit(
    *,
    limit: Decimal | None,
    outstanding: Decimal,
    overdue: Decimal = ZERO,
    on_hold: bool = False,
    warning_threshold: Decimal = Decimal("0.90"),
    currency_symbol: str = "",
) -> CreditAssessment:
    """Assess a customer's credit position.

    Outstanding and overdue are supplied by the receivables register; this function
    decides only what they mean. An overdue balance withholds further credit whatever
    the limit, because a limit measures exposure, not conduct.
    """
    outstanding = money(outstanding)
    overdue = money(overdue)
    ceiling = money(limit) if limit is not None else ZERO
    available = money(ceiling - outstanding)

    def symbol(amount: Decimal) -> str:
        return f"{currency_symbol}{amount:,.2f}"

    if on_hold:
        return CreditAssessment(
            CreditStanding.ON_HOLD,
            ceiling,
            outstanding,
            overdue,
            available,
            "This account is on hold. Credit sales require authorisation.",
        )
    if overdue > ZERO:
        return CreditAssessment(
            CreditStanding.OVERDUE,
            ceiling,
            outstanding,
            overdue,
            available,
            f"{symbol(overdue)} is overdue. Settle or authorise before extending credit.",
        )
    if limit is None or ceiling <= ZERO:
        return CreditAssessment(
            CreditStanding.NO_CREDIT,
            ceiling,
            outstanding,
            overdue,
            ZERO,
            "No credit limit is configured. This account is cash only.",
        )
    if outstanding > ceiling:
        return CreditAssessment(
            CreditStanding.OVER_LIMIT,
            ceiling,
            outstanding,
            overdue,
            available,
            f"{symbol(outstanding)} outstanding against a limit of {symbol(ceiling)}.",
        )
    if outstanding >= money(ceiling * warning_threshold):
        return CreditAssessment(
            CreditStanding.APPROACHING_LIMIT,
            ceiling,
            outstanding,
            overdue,
            available,
            f"{symbol(available)} of credit remains against a limit of {symbol(ceiling)}.",
        )
    return CreditAssessment(
        CreditStanding.WITHIN_LIMIT,
        ceiling,
        outstanding,
        overdue,
        available,
        f"{symbol(available)} of credit available.",
    )


_PARTY_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,22}[A-Z0-9]$")


def validate_party_code(code: str, *, field: str = "code") -> str:
    """Normalise and validate a party code. Returns the normalised form."""
    candidate = code.strip().upper().replace(" ", "-").replace("_", "-")
    if not _PARTY_CODE.match(candidate):
        raise ValidationError(
            "A party code is 3 to 24 characters, beginning and ending with a letter or "
            "digit, using letters, digits and hyphens.",
            field=field,
        )
    return candidate


def derive_party_code(name: str, kind: PartyKind, sequence: int) -> str:
    """Derive a party code from a name, as a default the user may amend."""
    initials = "".join(word[0] for word in re.findall(r"[A-Za-z]+", name))[:4].upper()
    if len(initials) < 2:
        initials = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:4] or "PTY"
    prefix = {"customer": "C", "supplier": "S", "affiliate": "A"}[kind.value]
    return validate_party_code(f"{prefix}{initials}-{sequence:04d}")


_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def validate_email(value: str | None, *, field: str = "email") -> str | None:
    """Normalise and validate an electronic mail address, where one is supplied."""
    if value is None or not value.strip():
        return None
    candidate = value.strip().lower()
    if len(candidate) > 200 or not _EMAIL.match(candidate):
        raise ValidationError("This is not a valid electronic mail address.", field=field)
    return candidate


def validate_telephone(value: str | None, *, field: str = "telephone") -> str | None:
    """Normalise a telephone number, retaining the form the user entered.

    No national format is imposed, because the product is not bound to one
    jurisdiction; only obviously impossible values are rejected.
    """
    if value is None or not value.strip():
        return None
    candidate = " ".join(value.split())
    digits = re.sub(r"\D", "", candidate)
    if len(digits) < 6 or len(digits) > 18:
        raise ValidationError("A telephone number contains between 6 and 18 digits.", field=field)
    return candidate


def assert_exemption_evidenced(treatment: TaxTreatment, *, evidence_reference: str | None) -> None:
    """Raise where an exemption is claimed without evidence, per PRD 19.1."""
    if treatment is TaxTreatment.EXEMPT and not (evidence_reference or "").strip():
        raise ValidationError(
            "An exemption requires a certificate or other supporting reference.",
            field="tax_exemption_reference",
        )
