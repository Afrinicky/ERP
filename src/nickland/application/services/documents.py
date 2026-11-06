"""Document types, their templates, and the record of what was issued.

The twelve default types of PRD §6.6 are seeded at provisioning so that an
installation can print from its first day. Each carries one published template
version; the designer of Phase 6 edits the same stored definition.

This service keeps records. It does not render: the renderer lives in the
``documents`` layer, which sits above this one in the dependency order, so a service
here that called it would have the architecture upside down. The rendering service
there composes the two — it renders, then asks this one to record what it produced.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.documents import (
    BankAccount,
    Classification,
    DocumentContext,
    DocumentKind,
    Issuer,
    Line,
    PageFormat,
    Party,
    TaxComponent,
    TemplateOptions,
    Totals,
    amount_in_words,
    validate_document_number,
)
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.persistence.document_models import DocumentTemplate, DocumentType, IssuedDocument
from nickland.persistence.models import Address, Branch, Business
from nickland.platform.clock import Clock, SystemClock

MARK_PATH = Path(__file__).resolve().parents[4] / "ui" / "public" / "mark.svg"
"""The product mark, used on a document until the business uploads its own logo."""


@dataclass(frozen=True, slots=True)
class SeededType:
    """A document type that ships."""

    code: str
    name: str
    kind: DocumentKind
    sequence_kind: str | None
    options: TemplateOptions
    order: int


def _terms(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return pairs


TRADING_TERMS = _terms(
    ("Order confirmation", "Confirmed prepayment"),
    ("Quote validity", "30 days from issue"),
    ("Delivery", "Within 28 days of cleared payment"),
    ("Tax", "Exclusive unless stated"),
)

SEEDED_TYPES: tuple[SeededType, ...] = (
    SeededType(
        "pro_forma",
        "Pro forma invoice",
        DocumentKind.PRO_FORMA,
        "proforma",
        TemplateOptions(
            terms=TRADING_TERMS,
            notes=("Goods sold are not returnable.",),
            footer_note="This pro forma is not a tax invoice.",
        ),
        10,
    ),
    SeededType(
        "quotation",
        "Quotation",
        DocumentKind.QUOTATION,
        "quotation",
        TemplateOptions(
            show_ship_to=False,
            terms=TRADING_TERMS,
            footer_note="Prices are subject to confirmation at the time of order.",
        ),
        20,
    ),
    SeededType(
        "invoice",
        "Sales invoice",
        DocumentKind.INVOICE,
        "invoice",
        TemplateOptions(
            terms=_terms(("Payment terms", "Due on receipt"), ("Tax", "Exclusive unless stated")),
            footer_note="Please quote the invoice number with your payment.",
        ),
        30,
    ),
    SeededType(
        "receipt",
        "Receipt",
        DocumentKind.RECEIPT,
        "receipt",
        TemplateOptions(
            page=PageFormat.ROLL_80,
            show_ship_to=False,
            show_banking=False,
            show_terms=False,
            show_signatures=False,
            show_index_column=False,
            footer_note="Thank you for your custom.",
        ),
        40,
    ),
    SeededType(
        "waybill",
        "Waybill",
        DocumentKind.WAYBILL,
        "waybill",
        TemplateOptions(
            show_delivery=True,
            show_totals=False,
            show_banking=False,
            show_terms=False,
            show_batch_and_expiry=True,
            footer_note="Goods received in good order and condition.",
        ),
        50,
    ),
    SeededType(
        "delivery_note",
        "Delivery note",
        DocumentKind.DELIVERY_NOTE,
        "delivery_note",
        TemplateOptions(
            show_delivery=True,
            show_totals=False,
            show_banking=False,
            show_terms=False,
            show_batch_and_expiry=True,
        ),
        60,
    ),
    SeededType(
        "packing_list",
        "Packing list",
        DocumentKind.PACKING_LIST,
        "packing_list",
        TemplateOptions(
            show_delivery=True,
            show_totals=False,
            show_banking=False,
            show_terms=False,
            show_batch_and_expiry=True,
        ),
        70,
    ),
    SeededType(
        "purchase_order",
        "Purchase order",
        DocumentKind.PURCHASE_ORDER,
        "purchase_order",
        TemplateOptions(
            terms=_terms(
                ("Delivery address", "As shown above"), ("Payment terms", "On agreed terms")
            ),
            footer_note="Our purchase order number must appear on your invoice.",
        ),
        80,
    ),
    SeededType(
        "goods_received_note",
        "Goods received note",
        DocumentKind.GOODS_RECEIVED_NOTE,
        "grn",
        TemplateOptions(
            show_totals=False,
            show_banking=False,
            show_terms=False,
            show_batch_and_expiry=True,
        ),
        90,
    ),
    SeededType(
        "statement",
        "Statement of account",
        DocumentKind.STATEMENT,
        "statement",
        TemplateOptions(
            show_ship_to=False,
            show_terms=False,
            footer_note="Please advise of any discrepancy within seven days.",
        ),
        100,
    ),
    SeededType(
        "payment_voucher",
        "Payment voucher",
        DocumentKind.PAYMENT_VOUCHER,
        "payment_voucher",
        TemplateOptions(
            show_ship_to=False,
            show_banking=False,
            show_terms=False,
            classification=Classification.INTERNAL,
        ),
        110,
    ),
    SeededType(
        "credit_note",
        "Credit note",
        DocumentKind.CREDIT_NOTE,
        "credit_note",
        TemplateOptions(
            show_ship_to=False,
            terms=_terms(
                ("Treatment", "Applied against the referenced invoice"),
            ),
        ),
        120,
    ),
)


class DocumentRecordService:
    """Seeds the default types, holds their templates, and records what was issued."""

    __slots__ = ("_audit", "_clock", "_db")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # ------------------------------------------------------------------- seeding

    def seed(self, business_id: uuid.UUID) -> int:
        """Create the twelve default types, each with one published template."""
        existing = self._db.scalar(
            select(DocumentType.id).where(DocumentType.business_id == business_id).limit(1)
        )
        if existing is not None:
            return 0

        now = self._clock.now()
        for seeded in SEEDED_TYPES:
            document_type = DocumentType(
                business_id=business_id,
                code=seeded.code,
                name=seeded.name,
                kind=seeded.kind.value,
                classification=seeded.options.classification.value,
                sequence_kind=seeded.sequence_kind,
                is_system=True,
                display_order=seeded.order,
            )
            document_type.templates.append(
                DocumentTemplate(
                    business_id=business_id,
                    version=1,
                    name=f"{seeded.name} — standard",
                    definition=seeded.options.as_dict(),
                    is_published=True,
                    published_at=now,
                )
            )
            self._db.add(document_type)
        self._db.flush()
        return len(SEEDED_TYPES)

    # -------------------------------------------------------------------- reading

    def types(self, actor: AnyActor, *, include_inactive: bool = False) -> list[DocumentType]:
        actor.require("template.view")
        query = select(DocumentType).where(DocumentType.business_id == _business_of(actor))
        if not include_inactive:
            query = query.where(DocumentType.is_active.is_(True))
        return list(
            self._db.scalars(query.order_by(DocumentType.display_order, DocumentType.name))
            .unique()
            .all()
        )

    def require_type(self, actor: AnyActor, type_id: uuid.UUID) -> DocumentType:
        actor.require("template.view")
        found = self._db.get(DocumentType, type_id)
        if found is None or found.business_id != _business_of(actor):
            raise NotFoundError("Document type not found.")
        return found

    def type_by_code(self, business_id: uuid.UUID, code: str) -> DocumentType | None:
        return self._db.scalar(
            select(DocumentType).where(
                DocumentType.business_id == business_id, DocumentType.code == code
            )
        )

    def published_template(self, document_type: DocumentType) -> DocumentTemplate:
        """The version a document issued now would use."""
        published = [item for item in document_type.templates if item.is_published]
        if not published:
            raise ConflictError(
                f"{document_type.name} has no published template, so nothing can be "
                "issued from it. Publish a version first.",
                code=document_type.code,
            )
        return max(published, key=lambda item: item.version)

    def options_for(self, document_type: DocumentType) -> TemplateOptions:
        return TemplateOptions.from_dict(self.published_template(document_type).definition)

    # ------------------------------------------------------------------- amending

    def amend_template(
        self, actor: AnyActor, type_id: uuid.UUID, changes: dict[str, Any]
    ) -> DocumentTemplate:
        """Publish the next version of a type's template.

        A published version is never edited in place: an issued document names the
        version it used, and that version must still mean what it meant.
        """
        actor.require("template.manage")
        document_type = self.require_type(actor, type_id)
        current = self.published_template(document_type)
        definition = {**current.definition, **changes}

        # Parsing proves the definition is renderable before it is published.
        options = TemplateOptions.from_dict(definition)
        if (
            document_type.classification == Classification.CUSTOMER_FACING.value
            and options.classification is Classification.INTERNAL
        ):
            raise ValidationError(
                "A customer-facing document type cannot carry an internal template: "
                "internal templates may disclose cost and margin.",
                field="classification",
            )

        now = self._clock.now()
        nxt = DocumentTemplate(
            business_id=document_type.business_id,
            type_fk=document_type.id,
            version=max(item.version for item in document_type.templates) + 1,
            name=current.name,
            definition=options.as_dict(),
            is_published=True,
            published_at=now,
            published_by_id=actor.user_id,
        )
        current.is_published = False
        document_type.templates.append(nxt)
        self._db.flush()

        self._audit.record(
            actor,
            "document_template_published",
            entity_type="document_template",
            entity_id=nxt.id,
            entity_label=f"{document_type.name} v{nxt.version}",
            before={"version": current.version, "definition": current.definition},
            after={"version": nxt.version, "definition": nxt.definition},
        )
        return nxt

    # -------------------------------------------------------------------- issuing

    def record_issued(
        self,
        actor: AnyActor,
        document_type: DocumentType,
        template: DocumentTemplate,
        context: DocumentContext,
        *,
        content: bytes,
        media_type: str,
        page: PageFormat,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
        party_id: uuid.UUID | None = None,
    ) -> IssuedDocument:
        """Keep the bytes that were produced, with their hash.

        The snapshot is what a reprint serves. Re-rendering later could differ — a
        price list moved, a template was amended, an address was corrected — and a
        reprint that differs from the customer's copy is worse than no reprint.
        """
        actor.require("document.issue")
        business_id = _business_of(actor)
        number = validate_document_number(context.number)
        duplicate = self._db.scalar(
            select(IssuedDocument.id).where(
                IssuedDocument.business_id == business_id, IssuedDocument.number == number
            )
        )
        if duplicate is not None:
            raise ConflictError(
                f"Document {number} has already been issued. A number is never reused.",
                number=number,
            )

        issued = IssuedDocument(
            business_id=business_id,
            branch_id=actor.branch_id,
            type_fk=document_type.id,
            template_fk=template.id,
            template_version=template.version,
            number=number,
            kind=document_type.kind,
            issued_on=context.issued_on,
            issued_at=self._clock.now(),
            issued_by_id=actor.user_id,
            source_type=source_type,
            source_id=source_id,
            party_fk=party_id,
            page_format=page.value,
            media_type=media_type,
            content=content,
            content_hash=hashlib.sha256(content).hexdigest(),
            byte_count=len(content),
        )
        self._db.add(issued)
        self._db.flush()

        self._audit.record(
            actor,
            "document_issued",
            entity_type="issued_document",
            entity_id=issued.id,
            entity_label=f"{document_type.name} {number}",
            after={
                "number": number,
                "kind": document_type.kind,
                "template_version": template.version,
                "content_hash": issued.content_hash,
                "bytes": issued.byte_count,
            },
        )
        return issued

    def require_issued(self, actor: AnyActor, document_id: uuid.UUID) -> IssuedDocument:
        """The stored snapshot. A reprint serves this, never a fresh render."""
        actor.require("document.reprint")
        issued = self._db.get(IssuedDocument, document_id)
        if issued is None or issued.business_id != _business_of(actor):
            raise NotFoundError("Issued document not found.")
        return issued

    def issued(
        self, actor: AnyActor, *, kind: str | None = None, limit: int = 50
    ) -> list[IssuedDocument]:
        actor.require("document.view")
        query = select(IssuedDocument).where(IssuedDocument.business_id == _business_of(actor))
        if kind:
            query = query.where(IssuedDocument.kind == kind)
        return list(
            self._db.scalars(
                query.order_by(IssuedDocument.issued_at.desc()).limit(max(min(limit, 200), 1))
            ).all()
        )

    # --------------------------------------------------------------- sample data

    def sample_context(
        self, business_id: uuid.UUID, kind: DocumentKind, *, watermark: str | None = None
    ) -> DocumentContext:
        """A representative transaction, with enough lines to exercise pagination.

        PRD §6.6 requires a sample preview. It uses the installation's own identity,
        so what the administrator sees is their letterhead rather than a mock-up of
        somebody else's.
        """
        business = self._db.get(Business, business_id)
        issuer = self._issuer(business)
        customer = Party(
            name="Sample Customer Limited",
            reference="CUST-0001",
            address_lines=("12 Independence Avenue", "Accra", "Ghana"),
            telephone="+233 30 000 0000",
            email="accounts@example.test",
            tax_identifier="C0000000000",
        )

        catalogue = [
            ("GLV-NITRILE-M", "Nitrile examination glove, medium, box of 100", "4", "85.00"),
            ("SYR-5ML", "Disposable syringe 5ml, box of 100", "2", "120.50"),
            ("MSK-N95", "N95 respirator mask", "10", "12.00"),
            ("CT0107B", "Amikacin antimicrobial susceptibility disc 30mcg", "1", "553.28"),
            ("CT0223B", "Amoxycillin antimicrobial susceptibility disc 30mcg", "1", "549.92"),
            ("BPM-AUTO", "Automatic blood pressure monitor, upper arm", "3", "640.00"),
            ("THM-IR", "Infrared forehead thermometer", "5", "210.00"),
            ("GAU-10", "Sterile gauze swab 10cm, pack of 50", "8", "46.75"),
        ]
        lines: list[Line] = []
        for sku, name, quantity, price in catalogue:
            count = Decimal(quantity)
            unit_price = Decimal(price)
            lines.append(
                Line(
                    sku=sku,
                    name=name,
                    quantity=count,
                    unit="pack",
                    unit_price=unit_price,
                    line_total=(count * unit_price).quantize(Decimal("0.01")),
                    batch="B24-0417",
                    expiry=date(2028, 6, 30),
                    manufacturer="Sample Manufacturer",
                )
            )

        subtotal = sum((line.line_total for line in lines), Decimal("0"))
        tax = (subtotal * Decimal("0.15")).quantize(Decimal("0.01"))
        grand = subtotal + tax
        units = sum((line.quantity for line in lines), Decimal("0"))

        paid = grand if kind is DocumentKind.RECEIPT else None
        currency = business.currency_symbol if business else "GH₵"

        return DocumentContext(
            kind=kind,
            number="SAMPLE-0001",
            issued_on=self._today(business),
            issuer=issuer,
            currency_code=business.base_currency if business else "GHS",
            currency_symbol=currency,
            currency_precision=business.currency_precision if business else 2,
            bill_to=customer,
            ship_to=customer,
            valid_until=date(self._today(business).year, 12, 31),
            order_reference="PO-SAMPLE-0001",
            prepared_by="Sample Operator",
            lines=lines,
            totals=Totals(
                total_units=units,
                subtotal=subtotal,
                tax=tax,
                grand_total=grand,
                paid=paid,
                balance_due=Decimal("0") if paid is not None else None,
                amount_in_words=amount_in_words(grand),
                components=(TaxComponent("VAT", Decimal("0.15"), subtotal, tax),),
            ),
            banking=self._banking(business),
            watermark=watermark,
        )

    def _issuer(self, business: Business | None) -> Issuer:
        if business is None:
            return Issuer(name="Nickland ERP", logo_data_uri=_mark_data_uri())
        address = self._db.scalar(
            select(Address).where(Address.owner_type == "business", Address.owner_id == business.id)
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
        branch = self._db.scalar(
            select(Branch).where(Branch.business_fk == business.id, Branch.is_primary.is_(True))
        )
        return Issuer(
            name=business.name,
            strapline=business.legal_name if business.legal_name != business.name else None,
            address_lines=lines,
            branch_address_lines=(branch.name,) if branch else (),
            telephone=business.telephone,
            email=business.email,
            website=business.website,
            tax_identifier=business.tax_identifier,
            registration_number=business.registration_number,
            logo_data_uri=_mark_data_uri(),
        )

    def _banking(self, business: Business | None) -> list[BankAccount]:
        """Banking details are a Phase 11 record; until then the block stays empty."""
        del business
        return []

    def _today(self, business: Business | None) -> date:
        from nickland.platform.clock import business_date

        return business_date(self._clock.now(), business.timezone if business else "UTC")


def _mark_data_uri() -> str | None:
    """The product mark as an embedded data URI.

    Embedded rather than linked: a document must render with no network and no file
    system assumptions, and WeasyPrint resolves a relative path against a base URL a
    PDF has no business needing.
    """
    try:
        markup = MARK_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    encoded = base64.b64encode(markup.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
