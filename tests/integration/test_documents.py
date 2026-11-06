"""Document types, rendering and issue against a real database. PRD 6.6, 6.7."""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.documents import SEEDED_TYPES, DocumentRecordService
from nickland.documents.render import render_html, render_pdf, render_thermal_text
from nickland.documents.service import DocumentRenderService
from nickland.domain.documents import (
    DocumentKind,
    Line,
    PageFormat,
    TemplateOptions,
    Totals,
)
from nickland.domain.errors import ConflictError, NotFoundError, PermissionDeniedError
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor


@pytest.fixture
def records(db: Session, clock: FixedClock) -> DocumentRecordService:
    return DocumentRecordService(db, clock=clock)


@pytest.fixture
def documents(db: Session, clock: FixedClock) -> DocumentRenderService:
    return DocumentRenderService(db, clock=clock)


class TestSeeding:
    def test_the_twelve_defaults_are_seeded_at_provisioning(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        types = records.types(admin)
        assert len(types) == len(SEEDED_TYPES) == 12
        assert {item.code for item in types} == {item.code for item in SEEDED_TYPES}

    def test_each_type_has_one_published_template(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        for document_type in records.types(admin):
            published = [item for item in document_type.templates if item.is_published]
            assert len(published) == 1
            assert published[0].version == 1

    def test_seeding_twice_creates_nothing_further(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        before = len(records.types(admin))
        assert records.seed(admin.business_id) == 0  # type: ignore[arg-type]
        assert len(records.types(admin)) == before

    def test_the_receipt_is_laid_out_for_a_till_roll(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        receipt = records.type_by_code(admin.business_id, "receipt")  # type: ignore[arg-type]
        assert receipt is not None
        assert records.options_for(receipt).page is PageFormat.ROLL_80

    def test_the_payment_voucher_is_internal(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        voucher = records.type_by_code(admin.business_id, "payment_voucher")  # type: ignore[arg-type]
        assert voucher is not None
        assert voucher.classification == "internal"


class TestRendering:
    def test_a_pro_forma_renders_to_a_pdf(
        self, documents: DocumentRenderService, records: DocumentRecordService, admin: Actor
    ) -> None:
        pro_forma = records.type_by_code(admin.business_id, "pro_forma")  # type: ignore[arg-type]
        assert pro_forma is not None
        rendered = documents.preview(admin, pro_forma.id)
        assert rendered.media_type == "application/pdf"
        assert rendered.content.startswith(b"%PDF")
        assert len(rendered.content) > 5000

    def test_the_same_context_renders_the_same_markup(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        """A stored snapshot is only worth storing if rendering is deterministic."""
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
        )
        assert render_html(context) == render_html(context)

    def test_a_receipt_renders_on_both_roll_widths(
        self, documents: DocumentRenderService, records: DocumentRecordService, admin: Actor
    ) -> None:
        receipt = records.type_by_code(admin.business_id, "receipt")  # type: ignore[arg-type]
        assert receipt is not None
        for page in (PageFormat.ROLL_80, PageFormat.ROLL_58):
            rendered = documents.preview(admin, receipt.id, page=page)
            assert rendered.content.startswith(b"%PDF")

    def test_a_preview_is_watermarked(
        self, documents: DocumentRenderService, records: DocumentRecordService, admin: Actor
    ) -> None:
        """PRD 6.6: a preview is never issuable, and must be unmistakable on paper."""
        invoice = records.type_by_code(admin.business_id, "invoice")  # type: ignore[arg-type]
        assert invoice is not None
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
            watermark="Preview",
        )
        assert "watermark" in render_html(context)
        assert "Preview" in render_html(context)

    def test_a_waybill_discloses_no_prices(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        """A driver and a storekeeper handle it; commercial terms are not theirs."""
        waybill = records.type_by_code(admin.business_id, "waybill")  # type: ignore[arg-type]
        assert waybill is not None
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.WAYBILL,
        )
        markup = render_html(context, options=records.options_for(waybill))
        text = re.sub(r"<[^>]+>", " ", markup)
        for amount in ("340.00", "120.50", "553.28"):
            assert amount not in text
        assert "Total" not in text.split("Dispatch")[-1]

    def test_a_validity_date_appears_only_on_an_offer(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        """A waybill that says 'valid until' invites the reader to misread it."""
        for kind, expected in (
            (DocumentKind.PRO_FORMA, True),
            (DocumentKind.QUOTATION, True),
            (DocumentKind.WAYBILL, False),
            (DocumentKind.INVOICE, False),
        ):
            context = records.sample_context(
                admin.business_id,  # type: ignore[arg-type]
                kind,
            )
            assert ("Valid until" in render_html(context)) is expected, kind

    def test_a_tax_number_appears_only_where_money_is_stated(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        for kind, expected in (
            (DocumentKind.INVOICE, True),
            (DocumentKind.PACKING_LIST, False),
        ):
            context = records.sample_context(
                admin.business_id,  # type: ignore[arg-type]
                kind,
            )
            assert ("VAT number" in render_html(context)) is expected, kind

    def test_an_invoice_does_disclose_its_prices(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        invoice = records.type_by_code(admin.business_id, "invoice")  # type: ignore[arg-type]
        assert invoice is not None
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
        )
        markup = render_html(context, options=records.options_for(invoice))
        assert "340.00" in markup

    def test_the_options_govern_which_blocks_appear(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
        )
        with_terms = render_html(context, options=TemplateOptions(terms=(("Validity", "30 days"),)))
        without = render_html(
            context, options=TemplateOptions(show_terms=False, terms=(("Validity", "30 days"),))
        )
        assert "Validity" in with_terms
        assert "Validity" not in without

    def test_thermal_text_fits_the_column_width(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.RECEIPT,
        )
        for width in (42, 32):
            text = render_thermal_text(context, width=width)
            assert text
            assert all(len(line) <= width for line in text.splitlines())

    def test_thermal_text_states_the_total(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.RECEIPT,
        )
        text = render_thermal_text(context, width=42)
        assert "TOTAL" in text
        assert "RECEIPT" in text

    def test_markup_is_escaped(self, records: DocumentRecordService, admin: Actor) -> None:
        """A product name is not markup, whatever it contains."""
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
        )
        context.lines = [
            Line(
                sku="X<1>",
                name='Glove <script>alert("x")</script>',
                quantity=Decimal("1"),
                unit_price=Decimal("1.00"),
                line_total=Decimal("1.00"),
            )
        ]
        markup = render_html(context)
        assert "<script>" not in markup
        assert "&lt;script&gt;" in markup

    def test_a_long_document_paginates(self, records: DocumentRecordService, admin: Actor) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
        )
        one = context.lines[0]
        context.lines = [one] * 90
        context.totals = Totals(subtotal=Decimal("1"), grand_total=Decimal("1"))
        rendered = render_pdf(context)
        assert rendered.content.count(b"/Type /Page\n") >= 2 or len(rendered.content) > 20000


class TestTemplateVersions:
    def test_amending_publishes_the_next_version(
        self, db: Session, records: DocumentRecordService, admin: Actor
    ) -> None:
        invoice = records.type_by_code(admin.business_id, "invoice")  # type: ignore[arg-type]
        assert invoice is not None
        first = records.published_template(invoice)
        nxt = records.amend_template(admin, invoice.id, {"show_banking": False})
        db.flush()

        assert nxt.version == first.version + 1
        assert nxt.is_published
        assert not first.is_published
        assert nxt.definition["show_banking"] is False

    def test_the_earlier_version_is_left_intact(
        self, db: Session, records: DocumentRecordService, admin: Actor
    ) -> None:
        """An issued document names its version, and that version must still mean it."""
        invoice = records.type_by_code(admin.business_id, "invoice")  # type: ignore[arg-type]
        assert invoice is not None
        first = records.published_template(invoice)
        original = dict(first.definition)
        records.amend_template(admin, invoice.id, {"show_banking": False})
        db.flush()
        assert first.definition == original

    def test_an_unmentioned_field_keeps_its_value(
        self, db: Session, records: DocumentRecordService, admin: Actor
    ) -> None:
        invoice = records.type_by_code(admin.business_id, "invoice")  # type: ignore[arg-type]
        assert invoice is not None
        before = records.published_template(invoice).definition["footer_note"]
        nxt = records.amend_template(admin, invoice.id, {"show_banking": False})
        db.flush()
        assert nxt.definition["footer_note"] == before

    def test_designing_requires_the_permission(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        invoice = records.type_by_code(admin.business_id, "invoice")  # type: ignore[arg-type]
        assert invoice is not None
        reader = make_actor(admin.business_id, permissions={"template.view"})  # type: ignore[arg-type]
        with pytest.raises(PermissionDeniedError):
            records.amend_template(reader, invoice.id, {"show_banking": False})


class TestIssue:
    def _context(self, records: DocumentRecordService, admin: Actor, number: str):
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
        )
        context.number = number
        return context

    def test_issuing_keeps_the_bytes_and_their_hash(
        self,
        db: Session,
        documents: DocumentRenderService,
        records: DocumentRecordService,
        admin: Actor,
    ) -> None:
        issued = documents.issue(admin, "invoice", self._context(records, admin, "INV-0001"))
        db.flush()
        assert issued.number == "INV-0001"
        assert issued.content.startswith(b"%PDF")
        assert issued.byte_count == len(issued.content)
        assert len(issued.content_hash) == 64
        assert issued.template_version == 1

    def test_a_reprint_serves_the_stored_copy(
        self,
        db: Session,
        documents: DocumentRenderService,
        records: DocumentRecordService,
        admin: Actor,
    ) -> None:
        """Re-rendering could differ; the customer's copy cannot be recalled."""
        issued = documents.issue(admin, "invoice", self._context(records, admin, "INV-0002"))
        db.flush()
        records.amend_template(admin, issued.type_fk, {"show_banking": False})
        db.flush()
        assert documents.reprint(admin, issued.id).content == issued.content

    def test_a_number_is_never_reused(
        self,
        db: Session,
        documents: DocumentRenderService,
        records: DocumentRecordService,
        admin: Actor,
    ) -> None:
        documents.issue(admin, "invoice", self._context(records, admin, "INV-0003"))
        db.flush()
        with pytest.raises(ConflictError, match="already been issued"):
            documents.issue(admin, "invoice", self._context(records, admin, "INV-0003"))

    def test_an_unregistered_type_is_not_found(
        self, documents: DocumentRenderService, records: DocumentRecordService, admin: Actor
    ) -> None:
        with pytest.raises(NotFoundError):
            documents.issue(admin, "not_a_type", self._context(records, admin, "INV-0004"))

    def test_issuing_requires_the_permission(
        self, documents: DocumentRenderService, records: DocumentRecordService, admin: Actor
    ) -> None:
        reader = make_actor(
            admin.business_id,  # type: ignore[arg-type]
            permissions={"template.view", "document.view"},
        )
        with pytest.raises(PermissionDeniedError):
            documents.issue(reader, "invoice", self._context(records, admin, "INV-0005"))

    def test_an_issued_document_appears_in_the_register(
        self,
        db: Session,
        documents: DocumentRenderService,
        records: DocumentRecordService,
        admin: Actor,
    ) -> None:
        documents.issue(admin, "invoice", self._context(records, admin, "INV-0006"))
        db.flush()
        register = records.issued(admin)
        assert [item.number for item in register] == ["INV-0006"]

    def test_a_document_from_another_business_is_not_found(
        self,
        db: Session,
        documents: DocumentRenderService,
        records: DocumentRecordService,
        admin: Actor,
    ) -> None:
        issued = documents.issue(admin, "invoice", self._context(records, admin, "INV-0007"))
        db.flush()
        stranger = make_actor(uuid.uuid4())
        with pytest.raises(NotFoundError):
            documents.reprint(stranger, issued.id)


class TestSampleData:
    def test_the_sample_uses_the_installation_s_own_identity(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.PRO_FORMA,
        )
        assert context.issuer.name == "Nicholas Nickland Enterprise"
        assert context.issuer.tax_identifier == "C0001234567"

    def test_the_sample_carries_the_mark(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        """Embedded, not linked: a document renders with no network."""
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.PRO_FORMA,
        )
        assert context.issuer.logo_data_uri is not None
        assert context.issuer.logo_data_uri.startswith("data:image/svg+xml;base64,")

    def test_the_sample_totals_add_up(self, records: DocumentRecordService, admin: Actor) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
        )
        lines = sum((line.line_total for line in context.lines), Decimal("0"))
        assert context.totals.subtotal == lines
        assert context.totals.grand_total == context.totals.subtotal + context.totals.tax

    def test_a_receipt_sample_is_paid_in_full(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.RECEIPT,
        )
        assert context.totals.paid == context.totals.grand_total
        assert context.totals.balance_due == Decimal("0")

    def test_the_sample_is_dated_in_the_business_s_own_timezone(
        self, records: DocumentRecordService, admin: Actor
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.INVOICE,
        )
        assert context.issued_on == date(2026, 11, 5)
