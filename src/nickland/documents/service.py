"""Rendering and issue: the documents layer's service.

It sits above the application layer in the dependency order, which is why the two are
split as they are. Records — the types, their templates, and what was issued — are
kept by ``DocumentRecordService`` in the application layer. Rendering is here, and
issuing is the composition of the two: render first, then record the bytes that were
produced.

Putting the renderer in the application layer would have been one fewer file and the
wrong shape: the application layer would then depend on WeasyPrint, Pango and Cairo
to record that a document exists.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.documents import DocumentRecordService, options_of
from nickland.documents.render import (
    RenderedDocument,
    render_html,
    render_pdf,
    render_thermal_text,
)
from nickland.domain.documents import DocumentContext, DocumentKind, PageFormat
from nickland.domain.errors import NotFoundError, ValidationError
from nickland.domain.templates import TemplateDefinition
from nickland.persistence.document_models import IssuedDocument
from nickland.platform.clock import Clock, SystemClock


class DocumentRenderService:
    """Previews a template, and issues a document from a transaction."""

    __slots__ = ("_clock", "_db", "_records")

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._records = DocumentRecordService(session, clock=self._clock)

    @property
    def records(self) -> DocumentRecordService:
        """The record service, so a caller needs only one of these."""
        return self._records

    # ------------------------------------------------------------------- previews

    def preview(
        self, actor: AnyActor, type_id: uuid.UUID, *, page: PageFormat | None = None
    ) -> RenderedDocument:
        """Render the type against representative data, watermarked.

        A preview goes through the same engine as final output, per PRD §6.6, so what
        the administrator approves is what a customer will receive. It is watermarked
        and is never issuable.
        """
        document_type = self._records.require_type(actor, type_id)
        options = self._records.options_for(document_type)
        context = self._records.sample_context(
            _business_of(actor), DocumentKind(document_type.kind), watermark="Preview"
        )
        return render_pdf(context, page=page or options.page, options=options)

    def preview_version(
        self,
        actor: AnyActor,
        template_id: uuid.UUID,
        *,
        page: PageFormat | None = None,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
    ) -> str:
        """One version — draft or published — rendered as HTML, watermarked.

        HTML rather than PDF because the editor shows it beside the outline and
        re-renders on every change; a PDF would need a viewer and a round trip
        through a layout engine for each keystroke. It goes through the same
        composer as the final document, so what is on screen is what will print.

        Against sample data by default, or against a real quotation when one is named,
        per PRD §6.6: a layout that looks right over eight tidy sample lines may not
        over a customer's forty-line order with a specification on every one.
        """
        from nickland.application.services.designer import DesignerService
        from nickland.application.services.document_sources import DocumentSourceService

        designer = DesignerService(self._db, clock=self._clock)
        state = designer.read(actor, template_id)
        document_type = designer.require_type(actor, state.template.type_fk)
        if source_id is not None:
            if source_type != "quotation":
                raise ValidationError(
                    f"A preview cannot be drawn from a {source_type!r}; quotations are the "
                    "source this release previews from.",
                    field="source_type",
                )
            context = replace(
                DocumentSourceService(self._db, clock=self._clock).context_for_quotation(
                    actor,
                    source_id,
                    DocumentKind(document_type.kind),
                    number="PREVIEW",
                    issuable_only=False,
                ),
                watermark="Preview",
            )
        else:
            context = self._records.sample_context(
                _business_of(actor), DocumentKind(document_type.kind), watermark="Preview"
            )
        options = options_of(state.template.definition)
        stock = page or (state.definition.page.page if state.definition.sections else options.page)
        return render_html(context, page=stock, options=options, definition=state.definition)

    def preview_thermal(self, actor: AnyActor, type_id: uuid.UUID, *, width: int = 42) -> str:
        """What a printer driven directly, with no layout engine, would receive."""
        document_type = self._records.require_type(actor, type_id)
        context = self._records.sample_context(
            _business_of(actor), DocumentKind(document_type.kind), watermark="Preview"
        )
        return render_thermal_text(context, width=width)

    # --------------------------------------------------------------------- issuing

    def issue_from_quotation(
        self,
        actor: AnyActor,
        type_code: str,
        quotation_id: uuid.UUID,
        *,
        page: PageFormat | None = None,
    ) -> IssuedDocument:
        """Put a quotation on paper: pro forma, quotation or invoice. PRD §6.6.

        The number comes from the type's own sequence inside this transaction, so a
        document that fails to render leaves no gap behind it, and the figures come
        from the quotation's customer-facing projection rather than from this method
        reading the line rows.
        """
        from nickland.application.services.document_sources import DocumentSourceService
        from nickland.application.services.organisation import OrganisationService

        business_id = _business_of(actor)
        document_type = self._records.type_by_code(business_id, type_code)
        if document_type is None:
            raise NotFoundError(f"No document type is registered under {type_code!r}.")

        allocated = OrganisationService(self._db, clock=self._clock).allocate_number(
            actor,
            document_type.sequence_kind or document_type.code,
            branch_id=actor.branch_id,
        )
        sources = DocumentSourceService(self._db, clock=self._clock)
        context = sources.context_for_quotation(
            actor,
            quotation_id,
            DocumentKind(document_type.kind),
            number=allocated.number,
        )
        quotation = sources.quotation_of(actor, quotation_id)
        return self.issue(
            actor,
            type_code,
            context,
            page=page,
            source_type="quotation",
            source_id=quotation_id,
            party_id=quotation.customer_fk,
        )

    def issue(
        self,
        actor: AnyActor,
        type_code: str,
        context: DocumentContext,
        *,
        page: PageFormat | None = None,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
        party_id: uuid.UUID | None = None,
    ) -> IssuedDocument:
        """Render a document and keep what was produced."""
        business_id = _business_of(actor)
        document_type = self._records.type_by_code(business_id, type_code)
        if document_type is None:
            raise NotFoundError(f"No document type is registered under {type_code!r}.")

        template = self._records.published_template(document_type)
        options = options_of(template.definition)
        definition = TemplateDefinition.from_dict(template.definition)
        stock = page or (definition.page.page if definition.sections else options.page)
        rendered = render_pdf(context, page=stock, options=options, definition=definition)

        return self._records.record_issued(
            actor,
            document_type,
            template,
            context,
            content=rendered.content,
            media_type=rendered.media_type,
            page=stock,
            source_type=source_type,
            source_id=source_id,
            party_id=party_id,
        )

    def issue_for_shipment(
        self, actor: AnyActor, shipment_id: uuid.UUID, kind: DocumentKind
    ) -> IssuedDocument:
        """A shipment's pick list, packing list, waybill or delivery note. PRD §6.9.

        Issued once: asking again returns the copy kept, so the waybill the recipient
        signed is the one that is reprinted.
        """
        from nickland.application.services.dispatch import DispatchService

        dispatch = DispatchService(self._db, clock=self._clock)
        shipment, existing, context = dispatch.document_for(actor, shipment_id, kind)
        if existing is not None:
            return existing
        assert context is not None
        issued = self.issue(
            actor,
            kind.value,
            context,
            source_type="shipment",
            source_id=shipment.id,
            party_id=shipment.customer_fk,
        )
        dispatch.record_document(shipment, kind, issued)
        return issued

    def issue_for_pos_sale(self, actor: AnyActor, sale_id: uuid.UUID) -> IssuedDocument:
        """A register sale's receipt, issued once. PRD §6.10.

        Asking again returns the copy kept, so a reprint is the receipt the customer was
        handed.
        """
        from nickland.application.services.pos import PosService

        pos = PosService(self._db, clock=self._clock)
        sale = pos.require_sale(actor, sale_id)
        if sale.receipt_fk is not None:
            return self._records.require_issued(actor, sale.receipt_fk)
        issued = self.issue(
            actor,
            DocumentKind.RECEIPT.value,
            pos.receipt_context(actor, sale),
            source_type="pos_sale",
            source_id=sale.id,
            party_id=sale.customer_fk,
        )
        pos.record_receipt(sale, issued.id)
        return issued

    def pos_receipt_text(self, actor: AnyActor, sale_id: uuid.UUID, *, width: int = 42) -> str:
        """The receipt as a counter printer driven directly receives it."""
        from nickland.application.services.pos import PosService

        pos = PosService(self._db, clock=self._clock)
        sale = pos.require_sale(actor, sale_id)
        return render_thermal_text(pos.receipt_context(actor, sale), width=width)

    def reprint(self, actor: AnyActor, document_id: uuid.UUID) -> RenderedDocument:
        """Serve the stored snapshot, so a reprint matches the copy already sent."""
        issued = self._records.require_issued(actor, document_id)
        safe = issued.number.replace("/", "-")
        return RenderedDocument(
            content=issued.content,
            media_type=issued.media_type,
            filename=f"{issued.kind}-{safe}.pdf",
        )


def _business_of(actor: AnyActor) -> uuid.UUID:
    from nickland.domain.errors import ConflictError

    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
