"""Building a document context from a source transaction.

PRD §6.6 requires a document to be issued *from a transaction*, not typed afresh: the
pro forma a customer receives carries the figures that were quoted, and typing them
again would be the one place the two could disagree.

The figures come through the quotation service's own customer-facing projection rather
than off the line rows. That keeps the disclosure boundary in one place: a figure
becomes printable because PRD §14 says it is disclosable, not because this module
happened to read the column. A supplier cost cannot reach a page through here, because
it is never handed to here.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.documents import DocumentRecordService
from nickland.application.services.quotations import QuotationService
from nickland.domain.documents import (
    DocumentContext,
    DocumentKind,
    Line,
    Party,
    Totals,
    amount_in_words,
)
from nickland.domain.errors import ConflictError, NotFoundError
from nickland.domain.money import money
from nickland.domain.pricing import InstalmentFrequency, QuotationState
from nickland.persistence.catalogue_models import Product
from nickland.persistence.models import Address, Business, Contact
from nickland.persistence.models import Party as PartyRow
from nickland.persistence.pricing_models import Quotation
from nickland.platform.clock import Clock, SystemClock

ISSUABLE_STATES = frozenset(
    {
        QuotationState.ISSUED.value,
        QuotationState.ACCEPTED.value,
        QuotationState.EXPIRED.value,
        QuotationState.SUPERSEDED.value,
    }
)
"""The states from which a quotation may be put on paper.

A draft may be previewed but not issued: a document carries a number from the
numbering sequence and the stored bytes become the customer's copy, and handing one
out before the offer has been approved would make the approval decorative. An expired
or superseded quotation may still be reprinted, because the customer may have asked
for the copy they already hold.
"""

FREQUENCY_LABELS = {
    InstalmentFrequency.WEEKLY: "Weekly",
    InstalmentFrequency.FORTNIGHTLY: "Fortnightly",
    InstalmentFrequency.MONTHLY: "Monthly",
    InstalmentFrequency.QUARTERLY: "Quarterly",
}


class DocumentSourceService:
    """Assembles the context a renderer needs from the record it is issued against."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._records = DocumentRecordService(session, clock=self._clock)
        self._quotations = QuotationService(session, clock=self._clock)

    def quotation_of(self, actor: AnyActor, quotation_id: uuid.UUID) -> Quotation:
        """The quotation itself, for a caller that needs its customer or its state."""
        return self._quotations.require(actor, quotation_id)

    def context_for_quotation(
        self,
        actor: AnyActor,
        quotation_id: uuid.UUID,
        kind: DocumentKind,
        *,
        number: str,
        issuable_only: bool = True,
    ) -> DocumentContext:
        """The quotation as a document, at the figures that were quoted.

        ``issuable_only`` is relaxed for a preview, which may be drawn from a draft:
        a preview takes no number and keeps no bytes, so the reason a draft cannot be
        issued does not apply to looking at it.
        """
        quotation = self._quotations.require(actor, quotation_id)
        if issuable_only and quotation.state not in ISSUABLE_STATES:
            raise ConflictError(
                f"This quotation is {quotation.state.replace('_', ' ')}. A document is "
                "issued once the offer has been issued to the customer; until then the "
                "preview shows what it will look like.",
                state=quotation.state,
            )

        offer = self._quotations.offer(actor, quotation_id)
        business = self._db.get(Business, quotation.business_id)
        customer = self._party(quotation.customer_fk, quotation.contact_fk)

        catalogue = self._skus(quotation)
        lines = [self._line(row, catalogue) for row in offer["lines"]]
        units = sum((line.quantity for line in lines), Decimal("0"))
        grand = money(offer["total_payable"])
        tax = money(offer["tax_total"])

        return DocumentContext(
            kind=kind,
            number=number,
            issued_on=quotation.quoted_on,
            issuer=self._records.issuer_for(quotation.business_id),
            currency_code=quotation.currency_code,
            currency_symbol=business.currency_symbol if business else "GH₵",
            currency_precision=business.currency_precision if business else 2,
            bill_to=customer,
            ship_to=customer,
            valid_until=quotation.valid_until,
            customer_reference=quotation.customer_reference,
            our_reference=self._our_reference(quotation.number, quotation.revision),
            prepared_by=actor.full_name,
            lines=lines,
            totals=Totals(
                total_units=units,
                subtotal=money(offer["net_total"] + offer["discount_total"]),
                discount=money(offer["discount_total"]),
                other_charges=money(offer["finance_charge_total"]),
                tax=tax,
                grand_total=grand,
                amount_in_words=amount_in_words(grand),
                # No tax summary. The quotation stores one tax total, not the
                # components behind it, and dividing that total by the basis to
                # recover a rate would print a rate nobody charged: a line taxed at
                # fifteen per cent beside one that is zero-rated averages to a figure
                # that appears on no rule. The breakdown arrives when the tax engine's
                # per-line components are kept on the quotation.
            ),
            terms=self._terms(quotation, offer),
            notes=[part for part in (quotation.notes,) if part],
        )

    # -- pieces -----------------------------------------------------------------------

    def _skus(self, quotation: Quotation) -> dict[int, str]:
        """The catalogue reference for each line that has one, by position.

        A line quoted against a product shows the customer the code they will order
        against. A line typed in free text shows nothing, rather than a row number
        dressed up as a reference.
        """
        product_ids = {
            line.position: line.product_fk
            for line in quotation.lines
            if line.product_fk is not None
        }
        if not product_ids:
            return {}
        rows = self._db.scalars(
            select(Product).where(Product.id.in_(set(product_ids.values())))
        ).all()
        by_id = {row.id: row.sku for row in rows}
        return {
            position: by_id[product_id]
            for position, product_id in product_ids.items()
            if product_id in by_id
        }

    def _line(self, row: dict[str, Any], catalogue: dict[int, str]) -> Line:
        """One table row, from the disclosed figures only.

        The unit price, not the list price: the page shows what is being charged, and
        a list price beside it would invite the question of which one the customer is
        paying. The amount is the line's net price rather than what it will cost with
        tax, so the column adds up to the subtotal printed beneath it.
        """
        position = int(row["position"])
        return Line(
            sku=catalogue.get(position, ""),
            name=str(row["description"]),
            quantity=money(row["quantity"]),
            unit_price=money(row["unit_price"]),
            line_total=money(row["net_price"]),
            note=row.get("specification"),
        )

    def _party(self, customer_id: uuid.UUID, contact_id: uuid.UUID | None) -> Party:
        party = self._db.get(PartyRow, customer_id)
        if party is None:
            raise NotFoundError("The customer on this quotation no longer exists.")
        address = self._db.scalar(
            select(Address).where(Address.owner_type == "party", Address.owner_id == party.id)
        )
        lines: tuple[str, ...] = ()
        if address is not None:
            lines = tuple(
                part
                for part in (
                    address.line_one,
                    address.line_two,
                    ", ".join(part for part in (address.city, address.region) if part),
                    address.country,
                )
                if part
            )
        contact = self._db.get(Contact, contact_id) if contact_id else None
        return Party(
            name=party.name,
            reference=party.code,
            address_lines=lines,
            telephone=party.telephone,
            email=party.email,
            tax_identifier=party.tax_identifier,
            attention=contact.name if contact else None,
        )

    def _our_reference(self, number: str, revision: int) -> str:
        return number if revision <= 1 else f"{number} rev {revision}"

    def _terms(self, quotation: Any, offer: dict[str, Any]) -> list[tuple[str, str]]:
        """The terms block: the quotation's own text, plus what the plan commits to."""
        terms: list[tuple[str, str]] = []
        if quotation.valid_until:
            terms.append(("Quote validity", _on(quotation.valid_until)))
        if quotation.payment_plan == "instalment":
            frequency = (
                FREQUENCY_LABELS.get(InstalmentFrequency(quotation.instalment_frequency), "Monthly")
                if quotation.instalment_frequency
                else "Monthly"
            )
            instalments = len(offer.get("schedule") or ())
            terms.append(("Payment", f"{frequency}, {instalments} instalments"))
            if quotation.first_instalment_on:
                terms.append(("First instalment", _on(quotation.first_instalment_on)))
        else:
            terms.append(("Payment", "Due on acceptance"))
        if quotation.terms:
            terms.append(("Terms", quotation.terms))
        return terms


def _on(when: date) -> str:
    """The short form the rest of the document uses, which also fits the terms column."""
    return when.strftime("%d %b %Y")
