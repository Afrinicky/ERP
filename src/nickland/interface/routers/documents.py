"""Document endpoints: types, templates, previews and issued documents."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response

from nickland.domain.documents import PageFormat
from nickland.interface.dependencies import (
    ActorDependency,
    DocumentDependency,
    DocumentRecordDependency,
)
from nickland.interface.document_schemas import (
    AmendTemplateContract,
    DocumentTypeReply,
    IssuedDocumentReply,
    PageFormatReply,
    TemplateReply,
    ThermalPreviewReply,
)

router = APIRouter(prefix="/documents", tags=["documents"])


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
            reply.page_format = str(latest.definition.get("page", "a4"))
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


__all__ = ["router"]
