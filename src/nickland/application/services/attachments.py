"""Attachments: evidence stored under its own identifier, hashed, and kept unaltered.

PRD §6.7 and §6.8 attach signed acknowledgements, supplier quotations, invoices,
receipts and payment evidence to the records they prove. The file is written under a
key derived from its identifier, never from the name it arrived with, and an attachment
marked immutable can be read and copied but neither rewritten nor removed through this
service: evidence that can be replaced is not evidence.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.persistence.models import Attachment
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.config import Settings, get_settings

MAXIMUM_BYTES = 25 * 1024 * 1024
"""Twenty-five megabytes. A scanned invoice is a few hundred kilobytes; a photograph a
few megabytes; anything larger is a video, which is not evidence of a delivery."""

PERMITTED_MEDIA = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "image/tiff",
        "text/plain",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.ms-excel",
    }
)
"""What an attachment may be. An executable is refused whatever it is called."""

_FILENAME = re.compile(r"[^A-Za-z0-9._ ()\-]+")


@dataclass(frozen=True, slots=True)
class StoredFile:
    """An attachment with its bytes, for serving."""

    attachment: Attachment
    content: bytes


class AttachmentService:
    """Stores, serves and lists the files attached to records."""

    def __init__(
        self,
        session: DbSession,
        *,
        settings: Settings | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._db = session
        self._settings = settings or get_settings()
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    def store(
        self,
        actor: AnyActor,
        *,
        content: bytes,
        filename: str,
        media_type: str,
        owner_type: str | None = None,
        owner_id: uuid.UUID | None = None,
        purpose: str | None = None,
        immutable: bool = False,
    ) -> Attachment:
        """Keep a file against a record."""
        actor.require("attachment.upload")
        business_id = _business_of(actor)
        if not content:
            raise ValidationError("The file is empty.", field="file")
        if len(content) > MAXIMUM_BYTES:
            raise ValidationError(
                f"The file is larger than {MAXIMUM_BYTES // (1024 * 1024)} MB.", field="file"
            )
        media = media_type.split(";")[0].strip().lower()
        if media not in PERMITTED_MEDIA:
            raise ValidationError(
                f"A file of type {media!r} cannot be attached. Attach a PDF, an image, a "
                "spreadsheet or a document.",
                field="media_type",
            )
        cleaned = _FILENAME.sub("_", filename.strip())[:255] or "attachment"

        record = Attachment(
            business_id=business_id,
            owner_type=owner_type,
            owner_id=owner_id,
            original_filename=cleaned,
            media_type=media,
            size_bytes=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            storage_key="",
            purpose=purpose,
            uploaded_by_id=actor.user_id,
            is_immutable=immutable,
        )
        self._db.add(record)
        self._db.flush()
        # The key is the identifier, so a name from outside never reaches the file
        # system; the first two characters spread the files across directories.
        record.storage_key = f"{record.id.hex[:2]}/{record.id.hex}"
        path = self._path_for(record.storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self._db.flush()

        self._audit.record(
            actor,
            "attachment_stored",
            entity_type=owner_type or "attachment",
            entity_id=owner_id or record.id,
            entity_label=cleaned,
            after={
                "attachment_id": str(record.id),
                "bytes": len(content),
                "hash": record.content_hash,
                "immutable": immutable,
            },
        )
        return record

    def require(self, actor: AnyActor, attachment_id: uuid.UUID) -> Attachment:
        record = self._db.get(Attachment, attachment_id)
        if record is None or record.business_id != _business_of(actor):
            raise NotFoundError("Attachment not found.")
        return record

    def read(self, actor: AnyActor, attachment_id: uuid.UUID) -> StoredFile:
        """The file, verified against its hash before it is served.

        A file that no longer matches the hash it was stored with has been altered on
        disk, and serving it as evidence would be serving a forgery.
        """
        record = self.require(actor, attachment_id)
        path = self._path_for(record.storage_key)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise NotFoundError("The attached file is missing from the store.") from error
        if hashlib.sha256(content).hexdigest() != record.content_hash:
            raise ConflictError(
                f"The file {record.original_filename} no longer matches the hash it was "
                "stored with. It has been altered outside the application and cannot be "
                "served as evidence.",
                attachment_id=str(record.id),
            )
        return StoredFile(attachment=record, content=content)

    def for_owner(self, actor: AnyActor, owner_type: str, owner_id: uuid.UUID) -> list[Attachment]:
        return list(
            self._db.scalars(
                select(Attachment)
                .where(
                    Attachment.business_id == _business_of(actor),
                    Attachment.owner_type == owner_type,
                    Attachment.owner_id == owner_id,
                )
                .order_by(Attachment.created_at)
            ).all()
        )

    def remove(self, actor: AnyActor, attachment_id: uuid.UUID) -> None:
        """Take a file off a record. Refused for evidence."""
        actor.require("attachment.upload")
        record = self.require(actor, attachment_id)
        if record.is_immutable:
            raise ConflictError(
                f"{record.original_filename} is evidence and cannot be removed.",
                attachment_id=str(record.id),
            )
        path = self._path_for(record.storage_key)
        self._db.delete(record)
        self._db.flush()
        with contextlib.suppress(OSError):
            path.unlink()
        self._audit.record(
            actor,
            "attachment_removed",
            entity_type=record.owner_type or "attachment",
            entity_id=record.owner_id or record.id,
            entity_label=record.original_filename,
            before={"attachment_id": str(record.id)},
        )

    def _path_for(self, storage_key: str) -> Path:
        root = self._settings.attachment_directory
        candidate = (root / storage_key).resolve()
        if root.resolve() not in candidate.parents:
            raise ConflictError("The attachment key does not resolve inside the store.")
        return candidate


def _business_of(actor: AnyActor) -> uuid.UUID:
    if actor.business_id is None:
        raise ConflictError("The business has not been provisioned.")
    return actor.business_id
