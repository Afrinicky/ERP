"""The document designer: the palette, the draft, and what may be done to it.

Every structural edit is its own endpoint rather than a wholesale save of the
definition. Two reasons. The server can then refuse one operation — a column bound to
an internal field, a section identifier that is not there — without rejecting the
whole document the administrator has been working on. And the editor's pointer and
its keyboard call the same endpoint, so the two input modes PRD §11 requires cannot
drift apart into two code paths with two sets of bugs.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Response

from nickland.application.services.designer import DraftState
from nickland.domain.binding import evaluation_context
from nickland.domain.documents import Classification, DocumentKind
from nickland.domain.errors import ValidationError
from nickland.domain.expressions import evaluate, parse
from nickland.domain.templates import (
    GROUP_LABELS,
    OPERATOR_LABELS,
    SECTION_LABELS,
    Operator,
    SectionKind,
    resolve,
)
from nickland.interface.dependencies import (
    ActorDependency,
    DesignerDependency,
    DocumentRecordDependency,
)
from nickland.interface.document_schemas import (
    AddColumnContract,
    AddElementContract,
    AddFieldContract,
    AddSectionContract,
    AmendColumnContract,
    AmendElementContract,
    ClassificationContract,
    CreateDocumentTypeContract,
    DocumentTypeFieldReply,
    DraftReply,
    DuplicateContract,
    ExpressionCheckContract,
    ExpressionCheckReply,
    FindingReply,
    ImportContract,
    ImportReportReply,
    MoveContract,
    OperatorReply,
    PageContract,
    PaletteFieldReply,
    SaveDefinitionContract,
    SectionKindReply,
)

router = APIRouter(prefix="/designer", tags=["designer"])


# -- vocabulary ------------------------------------------------------------------------


@router.get("/sections", response_model=list[SectionKindReply], summary="Section kinds")
def section_kinds() -> list[SectionKindReply]:
    return [SectionKindReply(code=kind.value, label=SECTION_LABELS[kind]) for kind in SectionKind]


@router.get("/operators", response_model=list[OperatorReply], summary="Condition operators")
def operators() -> list[OperatorReply]:
    return [
        OperatorReply(
            code=item.value,
            label=OPERATOR_LABELS[item],
            takes_value=item.takes_value,
            takes_list=item.takes_list,
        )
        for item in Operator
    ]


@router.get(
    "/types/{type_id}/palette",
    response_model=list[PaletteFieldReply],
    summary="The fields this template may bind",
)
def palette(
    actor: ActorDependency, designer: DesignerDependency, type_id: uuid.UUID
) -> list[PaletteFieldReply]:
    return [
        PaletteFieldReply(
            key=item.key,
            label=item.label,
            group=item.group.value,
            group_label=GROUP_LABELS[item.group],
            kind=item.kind.value,
            per_line=item.per_line,
            summable=item.summable,
            is_internal=item.is_internal,
        )
        for item in designer.palette(actor, type_id)
    ]


# -- document types ---------------------------------------------------------------------


@router.post("/types", response_model=DraftReply, summary="Create a document type")
def create_type(
    actor: ActorDependency, designer: DesignerDependency, body: CreateDocumentTypeContract
) -> DraftReply:
    document_type = designer.create_type(
        actor,
        code=body.code,
        name=body.name,
        kind=body.kind,
        classification=body.classification,
        data_source=body.data_source,
        description=body.description,
        prefix=body.prefix,
        pattern=body.pattern,
        reset_cadence=body.reset_cadence,
        fields=body.fields,
    )
    return _draft(designer.draft(actor, document_type.id))


@router.get(
    "/types/{type_id}/fields",
    response_model=list[DocumentTypeFieldReply],
    summary="A type's own fields",
)
def type_fields(
    actor: ActorDependency, designer: DesignerDependency, type_id: uuid.UUID
) -> list[DocumentTypeFieldReply]:
    return [
        DocumentTypeFieldReply.model_validate(row)
        for row in designer.require_type(actor, type_id).fields
    ]


@router.post(
    "/types/{type_id}/fields",
    response_model=list[DocumentTypeFieldReply],
    summary="Add a field to a type",
)
def add_field(
    actor: ActorDependency,
    designer: DesignerDependency,
    type_id: uuid.UUID,
    body: AddFieldContract,
) -> list[DocumentTypeFieldReply]:
    document_type = designer.add_field(actor, type_id, body.model_dump())
    return [DocumentTypeFieldReply.model_validate(row) for row in document_type.fields]


@router.put(
    "/types/{type_id}/classification",
    response_model=DraftReply,
    summary="Set a type's classification",
)
def set_classification(
    actor: ActorDependency,
    designer: DesignerDependency,
    type_id: uuid.UUID,
    body: ClassificationContract,
) -> DraftReply:
    designer.set_classification(actor, type_id, body.classification)
    return _draft(designer.draft(actor, type_id))


# -- the draft ---------------------------------------------------------------------------


@router.get("/types/{type_id}/draft", response_model=DraftReply, summary="Open the draft")
def open_draft(
    actor: ActorDependency, designer: DesignerDependency, type_id: uuid.UUID
) -> DraftReply:
    return _draft(designer.draft(actor, type_id))


@router.get("/templates/{template_id}", response_model=DraftReply, summary="Read a version")
def read_version(
    actor: ActorDependency, designer: DesignerDependency, template_id: uuid.UUID
) -> DraftReply:
    return _draft(designer.read(actor, template_id))


@router.put("/templates/{template_id}", response_model=DraftReply, summary="Save the draft")
def save(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    body: SaveDefinitionContract,
) -> DraftReply:
    return _draft(designer.save(actor, template_id, body.definition))


@router.post(
    "/templates/{template_id}/publish", response_model=DraftReply, summary="Publish the draft"
)
def publish(
    actor: ActorDependency, designer: DesignerDependency, template_id: uuid.UUID
) -> DraftReply:
    designer.publish(actor, template_id)
    return _draft(designer.read(actor, template_id))


@router.delete("/templates/{template_id}", status_code=204, summary="Discard the draft")
def revert(actor: ActorDependency, designer: DesignerDependency, template_id: uuid.UUID) -> None:
    designer.revert(actor, template_id)


@router.post(
    "/templates/{template_id}/duplicate", response_model=DraftReply, summary="Duplicate a version"
)
def duplicate(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    body: DuplicateContract,
) -> DraftReply:
    copy = designer.duplicate(actor, template_id, name=body.name)
    return _draft(designer.read(actor, copy.id))


# -- structure ----------------------------------------------------------------------------


@router.post(
    "/templates/{template_id}/sections", response_model=DraftReply, summary="Add a section"
)
def add_section(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    body: AddSectionContract,
) -> DraftReply:
    return _draft(designer.add_section(actor, template_id, body.kind, title=body.title))


@router.post(
    "/templates/{template_id}/sections/{section_id}/move",
    response_model=DraftReply,
    summary="Move a section",
)
def move_section(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
    body: MoveContract,
) -> DraftReply:
    return _draft(designer.move_section(actor, template_id, section_id, body.offset))


@router.delete(
    "/templates/{template_id}/sections/{section_id}",
    response_model=DraftReply,
    summary="Remove a section",
)
def remove_section(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
) -> DraftReply:
    return _draft(designer.remove_section(actor, template_id, section_id))


@router.post(
    "/templates/{template_id}/sections/{section_id}/columns",
    response_model=DraftReply,
    summary="Add a column",
)
def add_column(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
    body: AddColumnContract,
) -> DraftReply:
    return _draft(designer.add_table_column(actor, template_id, section_id, body.binding))


@router.post(
    "/templates/{template_id}/sections/{section_id}/columns/{column_id}/move",
    response_model=DraftReply,
    summary="Move a column",
)
def move_column(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
    column_id: str,
    body: MoveContract,
) -> DraftReply:
    return _draft(
        designer.move_table_column(actor, template_id, section_id, column_id, body.offset)
    )


@router.put(
    "/templates/{template_id}/sections/{section_id}/columns/{column_id}",
    response_model=DraftReply,
    summary="Amend a column",
)
def amend_column(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
    column_id: str,
    body: AmendColumnContract,
) -> DraftReply:
    return _draft(designer.amend_column(actor, template_id, section_id, column_id, body.changes))


@router.delete(
    "/templates/{template_id}/sections/{section_id}/columns/{column_id}",
    response_model=DraftReply,
    summary="Remove a column",
)
def remove_column(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
    column_id: str,
) -> DraftReply:
    return _draft(designer.remove_table_column(actor, template_id, section_id, column_id))


@router.post(
    "/templates/{template_id}/sections/{section_id}/elements",
    response_model=DraftReply,
    summary="Add an element",
)
def add_element(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
    body: AddElementContract,
) -> DraftReply:
    return _draft(designer.add_element(actor, template_id, section_id, body.element))


@router.put(
    "/templates/{template_id}/sections/{section_id}/elements/{element_id}",
    response_model=DraftReply,
    summary="Amend an element",
)
def amend_element(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
    element_id: str,
    body: AmendElementContract,
) -> DraftReply:
    return _draft(designer.amend_element(actor, template_id, section_id, element_id, body.changes))


@router.delete(
    "/templates/{template_id}/sections/{section_id}/elements/{element_id}",
    response_model=DraftReply,
    summary="Remove an element",
)
def remove_element(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    section_id: str,
    element_id: str,
) -> DraftReply:
    return _draft(designer.remove_element(actor, template_id, section_id, element_id))


@router.put("/templates/{template_id}/page", response_model=DraftReply, summary="Set the page")
def set_page(
    actor: ActorDependency,
    designer: DesignerDependency,
    template_id: uuid.UUID,
    body: PageContract,
) -> DraftReply:
    return _draft(designer.set_page(actor, template_id, body.page))


# -- formulas -------------------------------------------------------------------------------


@router.post("/expression", response_model=ExpressionCheckReply, summary="Check a formula")
def check_expression(
    actor: ActorDependency,
    records: DocumentRecordDependency,
    body: ExpressionCheckContract,
) -> ExpressionCheckReply:
    """Parse a formula, and evaluate it against the sample document.

    Checked as it is typed rather than at publication, because a formula is the one
    part of a template an administrator cannot eyeball: the parser's refusal is the
    feedback, and showing a worked sample beside it is how they know it does what
    they meant and not merely something legal.
    """
    actor.require("template.view")
    classification = Classification(body.classification)
    try:
        expression = parse(body.expression)
    except ValidationError as error:
        return ExpressionCheckReply(is_valid=False, message=error.message)

    for key in sorted(expression.references):
        try:
            entry = resolve(key)
        except ValidationError as error:
            return ExpressionCheckReply(
                is_valid=False, message=error.message, references=sorted(expression.references)
            )
        if entry.is_internal and classification is Classification.CUSTOMER_FACING:
            return ExpressionCheckReply(
                is_valid=False,
                message=(
                    f"{entry.label} is an internal figure and cannot appear on a "
                    "customer-facing document."
                ),
                references=sorted(expression.references),
            )

    sample = records.sample_context(actor.business_id, DocumentKind.PRO_FORMA)
    try:
        value = evaluate(expression, evaluation_context(sample))
        shown = _shown(value)
    except ValidationError as error:
        return ExpressionCheckReply(
            is_valid=True,
            message=f"Valid, but against the sample document: {error.message}",
            references=sorted(expression.references),
            nodes=expression.nodes,
            depth=expression.depth,
        )

    return ExpressionCheckReply(
        is_valid=True,
        references=sorted(expression.references),
        sample=shown,
        nodes=expression.nodes,
        depth=expression.depth,
    )


def _shown(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# -- portability -----------------------------------------------------------------------------


@router.get("/templates/{template_id}/export", summary="Export a template")
def export(
    actor: ActorDependency, designer: DesignerDependency, template_id: uuid.UUID
) -> Response:
    """The template as a file. Structure and formatting; no business data."""
    payload = designer.export_bytes(actor, template_id)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="template.json"'},
    )


@router.post(
    "/types/{type_id}/import/inspect",
    response_model=ImportReportReply,
    summary="What an import would do",
)
def inspect_import(
    actor: ActorDependency,
    designer: DesignerDependency,
    type_id: uuid.UUID,
    body: ImportContract,
) -> ImportReportReply:
    report = designer.inspect_import(actor, type_id, body.payload)
    return ImportReportReply(
        name=report.name,
        kind=report.kind,
        classification=report.classification.value,
        unresolved=list(report.unresolved),
        findings=[
            FindingReply(where=item.where, message=item.message, is_blocking=item.is_blocking)
            for item in report.findings
        ],
        is_acceptable=report.is_acceptable,
    )


@router.post("/types/{type_id}/import", response_model=DraftReply, summary="Import a template")
def accept_import(
    actor: ActorDependency,
    designer: DesignerDependency,
    type_id: uuid.UUID,
    body: ImportContract,
) -> DraftReply:
    draft = designer.accept_import(actor, type_id, body.payload)
    return _draft(designer.read(actor, draft.id))


# -- projection --------------------------------------------------------------------------------


def _draft(state: DraftState) -> DraftReply:
    return DraftReply(
        template_id=state.template.id,
        type_id=state.template.type_fk,
        version=state.template.version,
        name=state.template.name,
        is_published=state.template.is_published,
        definition=state.template.definition,
        findings=[
            FindingReply(where=item.where, message=item.message, is_blocking=item.is_blocking)
            for item in state.findings
        ],
        is_publishable=state.is_publishable,
    )
