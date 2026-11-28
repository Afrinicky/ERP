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

    show_prices: bool | None = None
    """Three-valued: true, false, or absent to follow the kind's own default."""
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


# -- The designer (Phase 6) ------------------------------------------------------------


class PaletteFieldReply(Reply):
    key: str
    label: str
    group: str
    group_label: str
    kind: str
    per_line: bool
    summable: bool
    is_internal: bool


class FindingReply(Reply):
    where: str
    message: str
    is_blocking: bool


class ConditionContract(Contract):
    field: str
    operator: str
    value: str | None = None
    values: list[str] = Field(default_factory=list)


class VisibilityContract(Contract):
    mode: str = "always"
    join: str = "all"
    conditions: list[ConditionContract] = Field(default_factory=list)


class DraftReply(Reply):
    """A template being composed, and everything wrong with it."""

    template_id: uuid.UUID
    type_id: uuid.UUID
    version: int
    name: str
    is_published: bool
    definition: dict[str, Any]
    findings: list[FindingReply] = Field(default_factory=list)
    is_publishable: bool = True


class CreateDocumentTypeContract(Contract):
    code: str
    name: str
    kind: str = "pro_forma"
    classification: str = "customer_facing"
    data_source: str = "quotation"
    description: str | None = None
    prefix: str | None = None
    pattern: str = "{prefix}-{year}-{counter:4}"
    reset_cadence: str = "annually"
    fields: list[dict[str, Any]] = Field(default_factory=list)


class DocumentTypeFieldReply(Reply):
    id: uuid.UUID
    key: str
    label: str
    value_kind: str
    per_line: bool
    is_required: bool
    help_text: str | None
    display_order: int


class AddFieldContract(Contract):
    key: str
    label: str
    value_kind: str = "text"
    per_line: bool = False
    is_required: bool = False
    help_text: str | None = None


class ClassificationContract(Contract):
    classification: str


class SaveDefinitionContract(Contract):
    definition: dict[str, Any]


class MoveContract(Contract):
    offset: int = -1


class AddSectionContract(Contract):
    kind: str
    title: str = ""


class AddColumnContract(Contract):
    binding: str


class AmendColumnContract(Contract):
    changes: dict[str, Any]


class AddElementContract(Contract):
    element: dict[str, Any]


class AmendElementContract(Contract):
    changes: dict[str, Any]


class PageContract(Contract):
    page: dict[str, Any]


class DuplicateContract(Contract):
    name: str = ""


class ImportContract(Contract):
    payload: dict[str, Any]


class ImportReportReply(Reply):
    name: str
    kind: str
    classification: str
    unresolved: list[str]
    findings: list[FindingReply]
    is_acceptable: bool


class ExpressionCheckContract(Contract):
    expression: str
    classification: str = "customer_facing"


class ExpressionCheckReply(Reply):
    """What the designer tells an administrator about a formula as they type it."""

    is_valid: bool
    message: str | None = None
    references: list[str] = Field(default_factory=list)
    sample: str | None = None
    nodes: int = 0
    depth: int = 0


class SectionKindReply(Reply):
    code: str
    label: str


class OperatorReply(Reply):
    code: str
    label: str
    takes_value: bool
    takes_list: bool


class IssueContract(Contract):
    """Issue a document against a source transaction. PRD §6.6."""

    type_code: str = Field(min_length=1, max_length=40)
    source_type: str = Field(default="quotation", max_length=32)
    source_id: uuid.UUID
    page: str | None = Field(default=None, max_length=12)


class AcknowledgeContract(Contract):
    """Proof that a document was received. PRD §6.7."""

    recipient_name: str = Field(min_length=1, max_length=160)
    recipient_role: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=200)
    comments: str | None = None
    evidence_kind: str = Field(default="signature", max_length=16)
    evidence_base64: str | None = None
    """The signature or scan as base64. A signature drawn on screen arrives as a PNG."""
    media_type: str | None = Field(default=None, max_length=80)
    acknowledged_at: datetime | None = None
    """Only for a receipt collected on paper and entered afterwards."""


class AcknowledgementReply(Reply):
    id: uuid.UUID
    document_fk: uuid.UUID
    recipient_name: str
    recipient_role: str | None
    acknowledged_at: datetime
    location: str | None
    comments: str | None
    evidence_kind: str
    media_type: str | None
    evidence_hash: str | None
    recorded_by_id: uuid.UUID | None
