"""Document types, their templates, and the documents actually issued.

A template version is immutable once published, and an issued document is kept as the
bytes that were sent, with a content hash, per PRD §6.6 and §14. Reprinting serves the
stored snapshot rather than re-rendering, so a customer's copy and the business's copy
can never diverge because a template changed in between.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON as SA_JSON,
)
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nickland.persistence.base import (
    Base,
    BranchScopedMixin,
    BusinessScopedMixin,
    IdentifiedMixin,
    TimestampedMixin,
)
from nickland.persistence.types import UtcDateTime, Uuid


class DocumentType(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """A kind of document the business issues, with its numbering and classification."""

    __tablename__ = "document_types"

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    classification: Mapped[str] = mapped_column(
        String(20), nullable=False, default="customer_facing"
    )
    """Internal templates may bind cost and margin; customer-facing ones may not."""

    sequence_kind: Mapped[str | None] = mapped_column(String(32))
    """The numbering sequence this type draws its numbers from."""

    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    templates: Mapped[list[DocumentTemplate]] = relationship(
        back_populates="document_type",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DocumentTemplate.version",
    )

    __table_args__ = (UniqueConstraint("business_id", "code", name=None),)


class DocumentTemplate(Base, IdentifiedMixin, TimestampedMixin, BusinessScopedMixin):
    """One version of a document type's layout.

    ``definition`` is the stored template. The renderer reads it; the designer of
    PRD §6.6 will write it. A published version is never edited: editing one creates
    the next draft.
    """

    __tablename__ = "document_templates"

    type_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_types.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(SA_JSON, nullable=False)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    document_type: Mapped[DocumentType] = relationship(back_populates="templates")

    __table_args__ = (
        UniqueConstraint("type_fk", "version", name=None),
        CheckConstraint("version >= 1", name="version_positive"),
    )


class IssuedDocument(Base, IdentifiedMixin, BusinessScopedMixin, BranchScopedMixin):
    """A document that was issued, kept as the bytes that were sent.

    Append-only in practice: a document is superseded or cancelled, never rewritten,
    because the copy the customer holds cannot be recalled and amended.
    """

    __tablename__ = "issued_documents"

    type_fk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_types.id", ondelete="RESTRICT"), nullable=False
    )
    template_fk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("document_templates.id", ondelete="RESTRICT")
    )
    template_version: Mapped[int | None] = mapped_column(Integer)

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    issued_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    source_type: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    party_fk: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)

    page_format: Mapped[str] = mapped_column(String(12), nullable=False, default="a4")
    media_type: Mapped[str] = mapped_column(String(80), nullable=False, default="application/pdf")
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancelled_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("business_id", "number", name=None),
        Index("ix_issued_documents_kind", "business_id", "kind", "issued_on"),
        Index("ix_issued_documents_source", "business_id", "source_type", "source_id"),
        CheckConstraint("byte_count >= 0", name="byte_count_non_negative"),
    )
