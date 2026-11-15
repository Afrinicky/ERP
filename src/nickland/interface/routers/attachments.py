"""Attachment endpoints: store evidence against a record, list it, serve it."""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter, Response

from nickland.domain.errors import ValidationError
from nickland.interface.dependencies import ActorDependency, AttachmentDependency
from nickland.interface.order_schemas import AttachmentContract, AttachmentReply

router = APIRouter(prefix="/attachments", tags=["attachments"])


@router.post("", response_model=AttachmentReply, status_code=201, summary="Attach a file")
def store(
    payload: AttachmentContract, actor: ActorDependency, attachments: AttachmentDependency
) -> AttachmentReply:
    """Kept under its own identifier, hashed, and immutable where it is evidence."""
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValidationError("The file is not valid base64.", field="content_base64") from error
    record = attachments.store(
        actor,
        content=content,
        filename=payload.filename,
        media_type=payload.media_type,
        owner_type=payload.owner_type,
        owner_id=payload.owner_id,
        purpose=payload.purpose,
        immutable=payload.immutable,
    )
    return AttachmentReply.model_validate(record)


@router.get("", response_model=list[AttachmentReply], summary="Attachments on a record")
def for_owner(
    owner_type: str,
    owner_id: uuid.UUID,
    actor: ActorDependency,
    attachments: AttachmentDependency,
) -> list[AttachmentReply]:
    return [
        AttachmentReply.model_validate(item)
        for item in attachments.for_owner(actor, owner_type, owner_id)
    ]


@router.get(
    "/{attachment_id}/file",
    summary="The attached file",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def read_file(
    attachment_id: uuid.UUID, actor: ActorDependency, attachments: AttachmentDependency
) -> Response:
    """Verified against its hash before it is served."""
    stored = attachments.read(actor, attachment_id)
    return Response(
        content=stored.content,
        media_type=stored.attachment.media_type,
        headers={
            "Content-Disposition": f'inline; filename="{stored.attachment.original_filename}"'
        },
    )


@router.delete("/{attachment_id}", status_code=204, summary="Remove an attachment")
def remove(
    attachment_id: uuid.UUID, actor: ActorDependency, attachments: AttachmentDependency
) -> None:
    """Refused for evidence, which is immutable."""
    attachments.remove(actor, attachment_id)


__all__ = ["router"]
