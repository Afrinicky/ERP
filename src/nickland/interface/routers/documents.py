"""Document endpoints: types, templates, previews and issued documents."""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter, Query, Response

from nickland.domain.documents import PageFormat
from nickland.domain.errors import ValidationError
from nickland.interface.dependencies import (
    ActorDependency,
    DocumentDependency,
    DocumentRecordDependency,
)
from nickland.interface.document_schemas import (
    AcknowledgeContract,
    AcknowledgementReply,
    AmendTemplateContract,
    DocumentTypeReply,
    IssueContract,
    IssuedDocumentReply,
    PageFormatReply,
    TemplateReply,
    ThermalPreviewReply,
)

router = APIRouter(prefix="/documents", tags=["documents"])


def _page_of(definition: dict[str, object]) -> str:
    """The stock a stored template lays out for, whichever shape it is in.

    A template written before the designer kept the page at the top level; one
    written since keeps it inside the page configuration. Reading the wrong one
    returns a dictionary where the screen expects a word.
    """
    page = definition.get("page")
    if isinstance(page, dict):
        return str(page.get("page", "a4"))
    return str(page or "a4")


@router.get("/formats", response_model=list[PageFormatReply], summary="Page formats")
def page_formats() -> list[PageFormatReply]:
    """The stock a document may be laid out for, including the thermal rolls."""
    return [
        PageFormatReply(
            code=page.value,
            label=page.geometry.label,
            width_mm=str(page.geometry.width_mm),
            height_mm=str(page.geometry.height_mm) if page.geometry.height_mm else None,
            is_roll=page.is_roll,
        )
        for page in PageFormat
    ]


@router.get("/types", response_model=list[DocumentTypeReply], summary="List document types")
def list_types(
    actor: ActorDependency, records: DocumentRecordDependency, include_inactive: bool = False
) -> list[DocumentTypeReply]:
    replies: list[DocumentTypeReply] = []
    for document_type in records.types(actor, include_inactive=include_inactive):
        reply = DocumentTypeReply.model_validate(document_type)
        published = [item for item in document_type.templates if item.is_published]
        if published:
            latest = max(published, key=lambda item: item.version)
            reply.published_version = latest.version
            reply.page_format = _page_of(latest.definition)
        replies.append(reply)
    return replies


@router.get(
    "/types/{type_id}/template",
    response_model=TemplateReply,
    summary="The published template",
)
def read_template(
    type_id: uuid.UUID, actor: ActorDependency, records: DocumentRecordDependency
) -> TemplateReply:
    document_type = records.require_type(actor, type_id)
    return TemplateReply.model_validate(records.published_template(document_type))


@router.patch(
    "/types/{type_id}/template",
    response_model=TemplateReply,
    summary="Publish the next template version",
)
def amend_template(
    type_id: uuid.UUID,
    contract: AmendTemplateContract,
    actor: ActorDependency,
    records: DocumentRecordDependency,
) -> TemplateReply:
    return TemplateReply.model_validate(records.amend_template(actor, type_id, contract.changes()))


@router.get(
    "/types/{type_id}/preview",
    summary="Preview the type against sample data",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def preview(
    type_id: uuid.UUID,
    actor: ActorDependency,
    documents: DocumentDependency,
    page: PageFormat | None = Query(default=None),
) -> Response:
    """A watermarked PDF, rendered through the same engine as final output."""
    rendered = documents.preview(actor, type_id, page=page)
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={"Content-Disposition": f'inline; filename="{rendered.filename}"'},
    )


@router.get(
    "/templates/{template_id}/preview",
    summary="Preview one version against sample data or a transaction",
    response_class=Response,
    responses={200: {"content": {"text/html": {}}}},
)
def preview_version(
    template_id: uuid.UUID,
    actor: ActorDependency,
    documents: DocumentDependency,
    page: PageFormat | None = Query(default=None),
    source_type: str | None = Query(default=None),
    source_id: uuid.UUID | None = Query(default=None),
) -> Response:
    """The draft as it will print, against sample data or a named quotation.

    Watermarked, and never issuable.
    """
    markup = documents.preview_version(
        actor, template_id, page=page, source_type=source_type, source_id=source_id
    )
    return Response(content=markup, media_type="text/html; charset=utf-8")


@router.get(
    "/types/{type_id}/preview/thermal",
    response_model=ThermalPreviewReply,
    summary="Preview as thermal printer text",
)
def preview_thermal(
    type_id: uuid.UUID,
    actor: ActorDependency,
    documents: DocumentDependency,
    width: int = Query(default=42, ge=24, le=64),
) -> ThermalPreviewReply:
    """What a printer driven directly, without a layout engine, would receive."""
    return ThermalPreviewReply(
        width=width, text=documents.preview_thermal(actor, type_id, width=width)
    )


@router.get("/issued", response_model=list[IssuedDocumentReply], summary="Issued documents")
def issued(
    actor: ActorDependency,
    records: DocumentRecordDependency,
    kind: str | None = None,
    limit: int = 50,
) -> list[IssuedDocumentReply]:
    return [
        IssuedDocumentReply.model_validate(item)
        for item in records.issued(actor, kind=kind, limit=limit)
    ]


@router.post(
    "/issue",
    response_model=IssuedDocumentReply,
    status_code=201,
    summary="Issue a document from a transaction",
)
def issue(
    payload: IssueContract,
    actor: ActorDependency,
    documents: DocumentDependency,
) -> IssuedDocumentReply:
    """Render the document and keep the bytes that were produced.

    The number is allocated inside this request, so a render that fails leaves no gap
    in the sequence. A reprint afterwards serves these bytes and never re-renders.
    """
    if payload.source_type != "quotation":
        raise ValidationError(
            f"A document cannot yet be issued against a {payload.source_type}. "
            "Quotations are the source this release issues from.",
            field="source_type",
        )
    issued = documents.issue_from_quotation(
        actor,
        payload.type_code,
        payload.source_id,
        page=PageFormat(payload.page) if payload.page else None,
    )
    return IssuedDocumentReply.model_validate(issued)


@router.get(
    "/issued/{document_id}/file",
    summary="Reprint an issued document",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def reprint(
    document_id: uuid.UUID, actor: ActorDependency, documents: DocumentDependency
) -> Response:
    """Serves the stored snapshot, so a reprint matches the copy already sent."""
    rendered = documents.reprint(actor, document_id)
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={"Content-Disposition": f'inline; filename="{rendered.filename}"'},
    )


@router.get(
    "/issued/{document_id}/acknowledgements",
    response_model=list[AcknowledgementReply],
    summary="Proof of receipt",
)
def acknowledgements(
    document_id: uuid.UUID,
    actor: ActorDependency,
    records: DocumentRecordDependency,
) -> list[AcknowledgementReply]:
    """Every receipt recorded against a document. A part delivery has several."""
    return [
        AcknowledgementReply.model_validate(item)
        for item in records.acknowledgements(actor, document_id)
    ]


@router.post(
    "/issued/{document_id}/acknowledgements",
    response_model=AcknowledgementReply,
    status_code=201,
    summary="Record proof of receipt",
)
def acknowledge(
    document_id: uuid.UUID,
    payload: AcknowledgeContract,
    actor: ActorDependency,
    records: DocumentRecordDependency,
) -> AcknowledgementReply:
    """Capture a recipient's signature or a scanned acknowledgement. PRD §6.7."""
    evidence: bytes | None = None
    if payload.evidence_base64:
        try:
            evidence = base64.b64decode(payload.evidence_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValidationError(
                "The evidence is not valid base64.", field="evidence_base64"
            ) from error
    record = records.acknowledge(
        actor,
        document_id,
        recipient_name=payload.recipient_name,
        recipient_role=payload.recipient_role,
        location=payload.location,
        comments=payload.comments,
        evidence_kind=payload.evidence_kind,
        evidence=evidence,
        media_type=payload.media_type,
        acknowledged_at=payload.acknowledged_at,
    )
    return AcknowledgementReply.model_validate(record)


__all__ = ["router"]
