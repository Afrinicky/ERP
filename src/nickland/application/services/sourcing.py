"""Sourcing: the customer's request, the requests put to suppliers, and the answers.

PRD §6.2 asks for a quotation that can source its lines from several suppliers and
from stock, chosen on recorded terms. This service holds the record those terms live
in. Nothing here prices anything: the comparison states price, availability, lead
time, freight and tax as they were offered, and the choice is the user's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.money import ZERO, divide, money, multiply
from nickland.domain.organisation import DocumentKind
from nickland.domain.pricing import (
    REQUEST_TRANSITIONS,
    SUPPLIER_REQUEST_TRANSITIONS,
    RequestState,
    SupplierRequestState,
    assert_transition,
    validate_quantity,
    validate_rate,
)
from nickland.persistence.base import as_dict
from nickland.persistence.catalogue_models import Contact, Party, Product
from nickland.persistence.pricing_models import (
    QuotationRequest,
    QuotationRequestLine,
    SupplierQuotation,
    SupplierQuotationLine,
    SupplierRequest,
    SupplierRequestLine,
)
from nickland.platform.clock import Clock, SystemClock, business_date

ONE = Decimal("1")


# --------------------------------------------------------------------------------------
# Drafts
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class RequestLineDraft:
    description: str
    quantity: Decimal
    product_id: uuid.UUID | None = None
    specification: str | None = None
    unit_id: uuid.UUID | None = None
    required_by: date | None = None
    notes: str | None = None


@dataclass(slots=True)
class RequestDraft:
    """What the customer-request form collects, per PRD §6.2."""

    customer_id: uuid.UUID
    received_on: date | None = None
    contact_id: uuid.UUID | None = None
    customer_reference: str | None = None
    required_by: date | None = None
    delivery_address: str | None = None
    currency_code: str = "GHS"
    notes: str | None = None
    lines: list[RequestLineDraft] = field(default_factory=list)


@dataclass(slots=True)
class SupplierQuotationLineDraft:
    description: str
    quantity_offered: Decimal
    unit_cost: Decimal
    request_line_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    discount_rate: Decimal = ZERO
    recoverable_tax_rate: Decimal = ZERO
    non_recoverable_tax_rate: Decimal = ZERO
    lead_time_days: int | None = None
    minimum_order_quantity: Decimal | None = None
    is_available: bool = True
    availability_note: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class SupplierQuotationDraft:
    """A supplier's answer, recorded as they gave it."""

    supplier_id: uuid.UUID
    reference: str
    received_on: date | None = None
    supplier_request_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    valid_until: date | None = None
    currency_code: str = "GHS"
    exchange_rate: Decimal = ONE
    lead_time_days: int | None = None
    freight_amount: Decimal = ZERO
    other_charges: Decimal = ZERO
    payment_terms: str | None = None
    tax_treatment: str = "exclusive"
    notes: str | None = None
    lines: list[SupplierQuotationLineDraft] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Offer:
    """One supplier's answer on one requested line, in comparable terms."""

    supplier_quotation_id: uuid.UUID
    supplier_quotation_line_id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    reference: str
    currency_code: str
    exchange_rate: Decimal
    unit_cost: Decimal
    discount_rate: Decimal
    net_unit_cost: Decimal
    """After the supplier's own discount, in their currency."""
    base_unit_cost: Decimal
    """The same figure in the business's base currency, which is the only basis on
    which two suppliers quoting in different currencies can be compared."""
    charges_per_unit: Decimal
    """This line's share of the supplier's document-level freight and other charges,
    apportioned by value and divided by the quantity offered.

    Per unit, because every other figure in the row is per unit. A line total here
    would sit beside a unit price under a heading the reader adds up, and the sum
    would be wrong by the quantity."""
    comparable_unit_cost: Decimal
    """Net unit cost in base currency including the apportioned charges."""
    recoverable_tax_rate: Decimal
    non_recoverable_tax_rate: Decimal
    quantity_offered: Decimal
    lead_time_days: int | None
    is_available: bool
    availability_note: str | None
    valid_until: date | None
    payment_terms: str | None
    meets_quantity: bool
    minimum_order_quantity: Decimal | None


@dataclass(frozen=True, slots=True)
class LineComparison:
    """Every offer against one requested line, cheapest comparable cost first."""

    request_line_id: uuid.UUID
    description: str
    quantity: Decimal
    offers: list[Offer]

    @property
    def best(self) -> Offer | None:
        available = [offer for offer in self.offers if offer.is_available and offer.meets_quantity]
        if not available:
            return None
        return min(available, key=lambda offer: offer.comparable_unit_cost)

    @property
    def unanswered_by(self) -> list[str]:
        return [offer.supplier_name for offer in self.offers if not offer.is_available]


class SourcingService:
    """Customer requests, supplier requests and supplier quotations."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- the customer's request -------------------------------------------------------

    def create_request(self, actor: AnyActor, draft: RequestDraft) -> QuotationRequest:
        """Record a customer's enquiry, per PRD §6.2."""
        actor.require("rfq.manage")
        customer = self._require_party(actor, draft.customer_id, "customer")
        if not customer.is_customer:
            raise ValidationError(
                f"{customer.name} is not recorded as a customer.", field="customer_id"
            )
        if not draft.lines:
            raise ValidationError("A request needs at least one line.", field="lines")

        received = draft.received_on or self._today(actor)
        if draft.required_by is not None and draft.required_by < received:
            raise ValidationError(
                "A request cannot be required before it was received.", field="required_by"
            )
        if draft.contact_id is not None:
            self._require_contact(actor, draft.contact_id, draft.customer_id)

        number = self._allocate(actor, DocumentKind.QUOTATION, prefix="RFQ")
        request = QuotationRequest(
            business_id=actor.business_id,
            branch_id=actor.branch_id,
            number=number,
            customer_fk=draft.customer_id,
            contact_fk=draft.contact_id,
            customer_reference=draft.customer_reference or None,
            received_on=received,
            required_by=draft.required_by,
            delivery_address=draft.delivery_address or None,
            currency_code=draft.currency_code.upper(),
            state=RequestState.DRAFT.value,
            notes=draft.notes or None,
        )
        self._db.add(request)
        self._db.flush()

        for position, line in enumerate(draft.lines, start=1):
            self._db.add(self._request_line(actor, request.id, position, line))
        self._db.flush()

        self._audit.record(
            actor,
            "quotation_request_created",
            entity_type="quotation_request",
            entity_id=request.id,
            entity_label=request.number,
            before=None,
            after=as_dict(request),
        )
        return request

    def _request_line(
        self,
        actor: AnyActor,
        request_id: uuid.UUID,
        position: int,
        draft: RequestLineDraft,
    ) -> QuotationRequestLine:
        if not draft.description.strip():
            raise ValidationError("A requested line needs a description.", field="description")
        if draft.product_id is not None:
            self._require_product(actor, draft.product_id)
        return QuotationRequestLine(
            business_id=actor.business_id,
            request_fk=request_id,
            position=position,
            product_fk=draft.product_id,
            description=draft.description.strip(),
            specification=draft.specification or None,
            quantity=validate_quantity(draft.quantity),
            unit_fk=draft.unit_id,
            required_by=draft.required_by,
            notes=draft.notes or None,
        )

    def requests(
        self, actor: AnyActor, *, state: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[list[QuotationRequest], int]:
        actor.require("rfq.view")
        statement = select(QuotationRequest).where(
            QuotationRequest.business_id == actor.business_id
        )
        if state:
            statement = statement.where(QuotationRequest.state == state)
        total = self._db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self._db.scalars(
            statement.order_by(QuotationRequest.received_on.desc(), QuotationRequest.number.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    def require_request(self, actor: AnyActor, request_id: uuid.UUID) -> QuotationRequest:
        actor.require("rfq.view")
        request = self._db.get(QuotationRequest, request_id)
        if request is None or request.business_id != actor.business_id:
            raise NotFoundError("Request not found.")
        return request

    def set_request_state(
        self,
        actor: AnyActor,
        request_id: uuid.UUID,
        state: RequestState | str,
        *,
        reason: str | None = None,
    ) -> QuotationRequest:
        actor.require("rfq.manage")
        request = self.require_request(actor, request_id)
        current = RequestState(request.state)
        requested = RequestState(str(state))
        assert_transition("Request", REQUEST_TRANSITIONS, current, requested)

        if requested is RequestState.CANCELLED and not (reason or "").strip():
            raise ValidationError(
                "A cancelled request records why, so the enquiry is not simply lost.",
                field="reason",
            )
        before = as_dict(request)
        request.state = requested.value
        if requested is RequestState.CANCELLED:
            request.cancellation_reason = reason
        if requested is RequestState.CLOSED:
            request.closed_at = self._clock.now()
        self._db.flush()
        self._audit.record(
            actor,
            "quotation_request_state_changed",
            entity_type="quotation_request",
            entity_id=request.id,
            entity_label=f"{request.number}: {current.value} to {requested.value}",
            reason=reason,
            before=before,
            after=as_dict(request),
        )
        return request

    # -- requests to suppliers --------------------------------------------------------

    def create_supplier_request(
        self,
        actor: AnyActor,
        *,
        supplier_id: uuid.UUID,
        request_id: uuid.UUID | None = None,
        request_line_ids: list[uuid.UUID] | None = None,
        respond_by: date | None = None,
        currency_code: str = "GHS",
        notes: str | None = None,
    ) -> SupplierRequest:
        """Put the customer's lines, or a selection of them, to one supplier.

        Where a customer request is named and no lines are selected, every line goes:
        asking a supplier about half an enquiry is almost never what was meant.
        """
        actor.require("supplier_rfq.manage")
        supplier = self._require_party(actor, supplier_id, "supplier")
        if not supplier.is_supplier:
            raise ValidationError(
                f"{supplier.name} is not recorded as a supplier.", field="supplier_id"
            )

        lines: list[QuotationRequestLine] = []
        if request_id is not None:
            request = self.require_request(actor, request_id)
            selected = set(request_line_ids or [])
            lines = [line for line in request.lines if not selected or line.id in selected]
            if not lines:
                raise ValidationError(
                    "None of the selected lines belong to this request.", field="request_line_ids"
                )

        number = self._allocate(actor, DocumentKind.PURCHASE_ORDER, prefix="SRQ")
        supplier_request = SupplierRequest(
            business_id=actor.business_id,
            number=number,
            supplier_fk=supplier_id,
            request_fk=request_id,
            respond_by=respond_by,
            currency_code=currency_code.upper(),
            state=SupplierRequestState.DRAFT.value,
            notes=notes or None,
        )
        self._db.add(supplier_request)
        self._db.flush()

        for position, line in enumerate(lines, start=1):
            self._db.add(
                SupplierRequestLine(
                    business_id=actor.business_id,
                    supplier_request_fk=supplier_request.id,
                    position=position,
                    request_line_fk=line.id,
                    product_fk=line.product_fk,
                    description=line.description,
                    specification=line.specification,
                    quantity=line.quantity,
                    unit_fk=line.unit_fk,
                )
            )
        self._db.flush()

        self._audit.record(
            actor,
            "supplier_request_created",
            entity_type="supplier_request",
            entity_id=supplier_request.id,
            entity_label=f"{supplier_request.number} to {supplier.name}",
            before=None,
            after=as_dict(supplier_request),
        )
        return supplier_request

    def issue_supplier_request(
        self, actor: AnyActor, supplier_request_id: uuid.UUID, *, issued_on: date | None = None
    ) -> SupplierRequest:
        actor.require("supplier_rfq.manage")
        supplier_request = self.require_supplier_request(actor, supplier_request_id)
        assert_transition(
            "Supplier request",
            SUPPLIER_REQUEST_TRANSITIONS,
            SupplierRequestState(supplier_request.state),
            SupplierRequestState.SENT,
        )
        if not supplier_request.lines:
            raise ValidationError(
                "A request with no lines asks the supplier nothing.", field="lines"
            )
        before = as_dict(supplier_request)
        supplier_request.state = SupplierRequestState.SENT.value
        supplier_request.issued_on = issued_on or self._today(actor)
        self._db.flush()

        if supplier_request.request_fk is not None:
            request = self._db.get(QuotationRequest, supplier_request.request_fk)
            if request is not None and request.state == RequestState.OPEN.value:
                request.state = RequestState.SOURCING.value

        self._db.flush()
        self._audit.record(
            actor,
            "supplier_request_issued",
            entity_type="supplier_request",
            entity_id=supplier_request.id,
            entity_label=supplier_request.number,
            before=before,
            after=as_dict(supplier_request),
        )
        return supplier_request

    def require_supplier_request(
        self, actor: AnyActor, supplier_request_id: uuid.UUID
    ) -> SupplierRequest:
        actor.require("supplier_rfq.manage")
        row = self._db.get(SupplierRequest, supplier_request_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Supplier request not found.")
        return row

    def supplier_requests(
        self, actor: AnyActor, *, request_id: uuid.UUID | None = None
    ) -> list[SupplierRequest]:
        actor.require("supplier_rfq.manage")
        statement = select(SupplierRequest).where(SupplierRequest.business_id == actor.business_id)
        if request_id is not None:
            statement = statement.where(SupplierRequest.request_fk == request_id)
        return list(self._db.scalars(statement.order_by(SupplierRequest.number.desc())))

    # -- the supplier's answer --------------------------------------------------------

    def record_supplier_quotation(
        self, actor: AnyActor, draft: SupplierQuotationDraft
    ) -> SupplierQuotation:
        """Record what a supplier offered, per PRD §6.2."""
        actor.require("supplier_quotation.manage")
        supplier = self._require_party(actor, draft.supplier_id, "supplier")
        if not draft.reference.strip():
            raise ValidationError(
                "A supplier quotation needs the supplier's own reference, which is "
                "what any query to them will cite.",
                field="reference",
            )
        if not draft.lines:
            raise ValidationError("A supplier quotation needs at least one line.", field="lines")
        if draft.exchange_rate <= ZERO:
            raise ValidationError("An exchange rate is above zero.", field="exchange_rate")
        if draft.freight_amount < ZERO or draft.other_charges < ZERO:
            raise ValidationError("A charge cannot be negative.", field="freight_amount")

        existing = self._db.scalar(
            select(SupplierQuotation).where(
                SupplierQuotation.business_id == actor.business_id,
                SupplierQuotation.supplier_fk == draft.supplier_id,
                SupplierQuotation.reference == draft.reference.strip(),
            )
        )
        if existing is not None:
            raise ConflictError(
                f"{supplier.name} quotation {draft.reference!r} has already been recorded."
            )

        request_id = draft.request_id
        if draft.supplier_request_id is not None:
            supplier_request = self.require_supplier_request(actor, draft.supplier_request_id)
            request_id = request_id or supplier_request.request_fk

        quotation = SupplierQuotation(
            business_id=actor.business_id,
            reference=draft.reference.strip(),
            supplier_fk=draft.supplier_id,
            supplier_request_fk=draft.supplier_request_id,
            request_fk=request_id,
            received_on=draft.received_on or self._today(actor),
            valid_until=draft.valid_until,
            currency_code=draft.currency_code.upper(),
            exchange_rate=money(draft.exchange_rate),
            lead_time_days=draft.lead_time_days,
            freight_amount=money(draft.freight_amount),
            other_charges=money(draft.other_charges),
            payment_terms=draft.payment_terms or None,
            tax_treatment=draft.tax_treatment,
            state="received",
            notes=draft.notes or None,
        )
        self._db.add(quotation)
        self._db.flush()

        for position, line in enumerate(draft.lines, start=1):
            if line.product_id is not None:
                self._require_product(actor, line.product_id)
            if line.request_line_id is not None:
                self._require_request_line(actor, line.request_line_id)
            if not line.is_available and not (line.availability_note or "").strip():
                raise ValidationError(
                    "A line the supplier cannot fill records why. The comparison "
                    "shows the reason rather than omitting the supplier.",
                    field="availability_note",
                )
            self._db.add(
                SupplierQuotationLine(
                    business_id=actor.business_id,
                    supplier_quotation_fk=quotation.id,
                    position=position,
                    request_line_fk=line.request_line_id,
                    product_fk=line.product_id,
                    description=line.description.strip(),
                    quantity_offered=validate_quantity(line.quantity_offered),
                    unit_cost=money(line.unit_cost),
                    discount_rate=validate_rate(
                        line.discount_rate, field_name="discount_rate", ceiling=ONE
                    ),
                    recoverable_tax_rate=validate_rate(
                        line.recoverable_tax_rate, field_name="recoverable_tax_rate"
                    ),
                    non_recoverable_tax_rate=validate_rate(
                        line.non_recoverable_tax_rate, field_name="non_recoverable_tax_rate"
                    ),
                    lead_time_days=line.lead_time_days,
                    minimum_order_quantity=line.minimum_order_quantity,
                    is_available=line.is_available,
                    availability_note=line.availability_note or None,
                    notes=line.notes or None,
                )
            )
        self._db.flush()

        if draft.supplier_request_id is not None:
            supplier_request = self.require_supplier_request(actor, draft.supplier_request_id)
            if supplier_request.state == SupplierRequestState.SENT.value:
                supplier_request.state = SupplierRequestState.ANSWERED.value
                self._db.flush()

        self._audit.record(
            actor,
            "supplier_quotation_recorded",
            entity_type="supplier_quotation",
            entity_id=quotation.id,
            entity_label=f"{supplier.name} {quotation.reference}",
            before=None,
            after=as_dict(quotation),
        )
        return quotation

    def supplier_quotations(
        self, actor: AnyActor, *, request_id: uuid.UUID | None = None, limit: int = 100
    ) -> list[SupplierQuotation]:
        actor.require("supplier_quotation.manage")
        statement = select(SupplierQuotation).where(
            SupplierQuotation.business_id == actor.business_id
        )
        if request_id is not None:
            statement = statement.where(SupplierQuotation.request_fk == request_id)
        return list(
            self._db.scalars(statement.order_by(SupplierQuotation.received_on.desc()).limit(limit))
        )

    def require_supplier_quotation(
        self, actor: AnyActor, quotation_id: uuid.UUID
    ) -> SupplierQuotation:
        actor.require("supplier_quotation.manage")
        row = self._db.get(SupplierQuotation, quotation_id)
        if row is None or row.business_id != actor.business_id:
            raise NotFoundError("Supplier quotation not found.")
        return row

    # -- comparison -------------------------------------------------------------------

    def compare(self, actor: AnyActor, request_id: uuid.UUID) -> list[LineComparison]:
        """Every supplier's answer on every requested line, side by side.

        Offers are made comparable before they are compared. A supplier quoting in
        another currency is converted at their recorded rate, and their document-level
        freight is apportioned across their own lines by value: a cheap unit price
        under heavy freight is not a cheap offer, and a screen that sorted on unit
        price alone would recommend it.
        """
        actor.require("supplier_quotation.manage")
        request = self.require_request(actor, request_id)
        quotations = list(
            self._db.scalars(
                select(SupplierQuotation).where(
                    SupplierQuotation.business_id == actor.business_id,
                    SupplierQuotation.request_fk == request_id,
                )
            )
        )
        suppliers = {
            party.id: party.name
            for party in self._db.scalars(
                select(Party).where(
                    Party.business_id == actor.business_id,
                    Party.id.in_([item.supplier_fk for item in quotations] or [uuid.uuid4()]),
                )
            )
        }

        by_line: dict[uuid.UUID, list[Offer]] = {line.id: [] for line in request.lines}
        for quotation in quotations:
            charges = money(quotation.freight_amount + quotation.other_charges)
            values = [
                multiply(
                    money(line.unit_cost - multiply(line.unit_cost, line.discount_rate)),
                    line.quantity_offered,
                )
                for line in quotation.lines
            ]
            value_total = money(sum(values, ZERO))
            for line, value in zip(quotation.lines, values, strict=True):
                if line.request_line_fk is None or line.request_line_fk not in by_line:
                    continue
                share = (
                    multiply(charges, divide(value, value_total)) if value_total > ZERO else ZERO
                )
                net_unit = money(line.unit_cost - multiply(line.unit_cost, line.discount_rate))
                base_unit = multiply(net_unit, quotation.exchange_rate)
                share_per_unit = (
                    divide(multiply(share, quotation.exchange_rate), line.quantity_offered)
                    if line.quantity_offered > ZERO
                    else ZERO
                )
                requested_quantity = next(
                    (item.quantity for item in request.lines if item.id == line.request_line_fk),
                    ZERO,
                )
                minimum = line.minimum_order_quantity
                by_line[line.request_line_fk].append(
                    Offer(
                        supplier_quotation_id=quotation.id,
                        supplier_quotation_line_id=line.id,
                        supplier_id=quotation.supplier_fk,
                        supplier_name=suppliers.get(quotation.supplier_fk, "Unknown supplier"),
                        reference=quotation.reference,
                        currency_code=quotation.currency_code,
                        exchange_rate=quotation.exchange_rate,
                        unit_cost=line.unit_cost,
                        discount_rate=line.discount_rate,
                        net_unit_cost=net_unit,
                        base_unit_cost=base_unit,
                        charges_per_unit=share_per_unit,
                        comparable_unit_cost=money(base_unit + share_per_unit),
                        recoverable_tax_rate=line.recoverable_tax_rate,
                        non_recoverable_tax_rate=line.non_recoverable_tax_rate,
                        quantity_offered=line.quantity_offered,
                        lead_time_days=line.lead_time_days or quotation.lead_time_days,
                        is_available=line.is_available,
                        availability_note=line.availability_note,
                        valid_until=quotation.valid_until,
                        payment_terms=quotation.payment_terms,
                        meets_quantity=minimum is None or requested_quantity >= minimum,
                        minimum_order_quantity=minimum,
                    )
                )

        return [
            LineComparison(
                request_line_id=line.id,
                description=line.description,
                quantity=line.quantity,
                offers=sorted(
                    by_line[line.id],
                    key=lambda offer: (not offer.is_available, offer.comparable_unit_cost),
                ),
            )
            for line in request.lines
        ]

    # -- helpers ----------------------------------------------------------------------

    def _allocate(self, actor: AnyActor, kind: DocumentKind, *, prefix: str) -> str:
        """Allocate a number for a sourcing document.

        Sourcing documents share the quotation and purchase-order sequences, with their
        own prefix, rather than claiming two more of the twenty-two numbering sequences
        PRD §6.7 defines. A request is not a document the customer receives.
        """
        from nickland.application.services.organisation import OrganisationService

        allocated = OrganisationService(self._db, clock=self._clock).allocate_number(
            actor, kind, branch_id=actor.branch_id
        )
        tail = allocated.number.split("-", 1)[-1]
        return f"{prefix}-{tail}"

    def _today(self, actor: AnyActor) -> date:
        from nickland.persistence.models import Business

        business = self._db.get(Business, actor.business_id)
        timezone = business.timezone if business is not None else "UTC"
        return business_date(self._clock.now(), timezone)

    def _require_party(self, actor: AnyActor, party_id: uuid.UUID, label: str) -> Party:
        party = self._db.get(Party, party_id)
        if party is None or party.business_id != actor.business_id:
            raise NotFoundError(f"{label.capitalize()} not found.")
        return party

    def _require_product(self, actor: AnyActor, product_id: uuid.UUID) -> Product:
        product = self._db.get(Product, product_id)
        if product is None or product.business_id != actor.business_id:
            raise NotFoundError("Product not found.")
        return product

    def _require_contact(
        self, actor: AnyActor, contact_id: uuid.UUID, party_id: uuid.UUID
    ) -> Contact:
        contact = self._db.get(Contact, contact_id)
        if contact is None or contact.business_id != actor.business_id:
            raise NotFoundError("Contact not found.")
        if contact.party_fk != party_id:
            raise ValidationError("That contact belongs to another party.", field="contact_id")
        return contact

    def _require_request_line(
        self, actor: AnyActor, request_line_id: uuid.UUID
    ) -> QuotationRequestLine:
        line = self._db.get(QuotationRequestLine, request_line_id)
        if line is None or line.business_id != actor.business_id:
            raise NotFoundError("Requested line not found.")
        return line
