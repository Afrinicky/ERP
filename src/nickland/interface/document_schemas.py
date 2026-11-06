"""Request and response contracts for document types, templates and issued documents."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import Field

from nickland.interface.schemas import Contract, Reply


class TemplateReply(Reply):
    id: uuid.UUID
    version: int
    name: str
    definition: dict[str, Any]
    is_published: bool
    published_at: datetime | None


class DocumentTypeReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    kind: str
    classification: str
    sequence_kind: str | None
    is_system: bool
    is_active: bool
    display_order: int
    page_format: str = "a4"
    """The stock the published template lays out for."""
    published_version: int | None = None


class PageFormatReply(Reply):
    code: str
    label: str
    width_mm: str
    height_mm: str | None
    is_roll: bool


class AmendTemplateContract(Contract):
    """A change to the published definition. Publishing creates the next version."""

    show_bill_to: bool | None = None
    show_ship_to: bool | None = None
    show_delivery: bool | None = None
    show_totals: bool | None = None
    show_terms: bool | None = None
    show_banking: bool | None = None
    show_notes: bool | None = None
    show_signatures: bool | None = None
    show_index_column: bool | None = None
    show_unit_column: bool | None = None
    show_discount_column: bool | None = None
    show_batch_and_expiry: bool | None = None
    show_manufacturer: bool | None = None
    page: str | None = Field(default=None, max_length=12)
    terms: list[tuple[str, str]] | None = None
    notes: list[str] | None = None
    footer_note: str | None = Field(default=None, max_length=300)

    def changes(self) -> dict[str, Any]:
        """Only what the form submitted, so an untouched field keeps its value."""
        submitted = self.model_dump(exclude_unset=True)
        if submitted.get("terms") is not None:
            submitted["terms"] = [list(pair) for pair in submitted["terms"]]
        return submitted


class IssuedDocumentReply(Reply):
    id: uuid.UUID
    number: str
    kind: str
    issued_on: date
    issued_at: datetime
    issued_by_id: uuid.UUID | None
    template_version: int | None
    page_format: str
    media_type: str
    content_hash: str
    byte_count: int
    cancelled_at: datetime | None


class ThermalPreviewReply(Reply):
    """The plain text a thermal printer is fed, and the width it was laid out for."""

    width: int
    text: str
