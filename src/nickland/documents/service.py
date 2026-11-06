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

from sqlalchemy.orm import Session as DbSession

from nickland.application.context import AnyActor
from nickland.application.services.documents import DocumentRecordService
from nickland.documents.render import RenderedDocument, render_pdf, render_thermal_text
from nickland.domain.documents import DocumentContext, DocumentKind, PageFormat, TemplateOptions
from nickland.domain.errors import NotFoundError
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

    def preview_thermal(self, actor: AnyActor, type_id: uuid.UUID, *, width: int = 42) -> str:
        """What a printer driven directly, with no layout engine, would receive."""
        document_type = self._records.require_type(actor, type_id)
        context = self._records.sample_context(
            _business_of(actor), DocumentKind(document_type.kind), watermark="Preview"
        )
        return render_thermal_text(context, width=width)

    # --------------------------------------------------------------------- issuing

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
        options = TemplateOptions.from_dict(template.definition)
        stock = page or options.page
        rendered = render_pdf(context, page=stock, options=options)

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
