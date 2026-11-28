"""The document designer: composing templates and creating document types.

PRD §6.6 requires an administrator to do both without a developer. What that means in
practice is that every operation here writes stored data and none of them require a
release: a column added, a section reordered, a condition set, a new type created with
its own numbering sequence.

Draft and published versions are kept apart. A published version is never edited,
because an issued document names the version it was rendered under and that version
has to go on meaning what it meant. Editing one opens a draft; publishing the draft
supersedes the published one and leaves it on the record.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from nickland.application.audit import AuditRecorder
from nickland.application.context import AnyActor
from nickland.application.services.documents import options_of
from nickland.domain.documents import Classification, DocumentKind, TemplateOptions
from nickland.domain.errors import ConflictError, NotFoundError, ValidationError
from nickland.domain.organisation import NumberPattern, ResetCadence, validate_code
from nickland.domain.templates import (
    BY_KEY,
    Column,
    Element,
    Finding,
    PaletteField,
    Portable,
    Section,
    SectionKind,
    TemplateDefinition,
    ValueKind,
    add_column,
    assert_valid,
    compile_expressions,
    default_definition,
    export_template,
    import_template,
    palette_for,
    remove_column,
    reorder,
    validate,
)
from nickland.persistence.base import as_dict
from nickland.persistence.document_models import (
    DocumentTemplate,
    DocumentType,
    DocumentTypeField,
    IssuedDocument,
)
from nickland.persistence.models import Business, NumberSequence
from nickland.platform.clock import Clock, SystemClock
from nickland.platform.ids import new_id


@dataclass(frozen=True, slots=True)
class DraftState:
    """A template version and what is wrong with it."""

    template: DocumentTemplate
    definition: TemplateDefinition
    findings: tuple[Finding, ...]

    @property
    def is_publishable(self) -> bool:
        return not any(item.is_blocking for item in self.findings)


class DesignerService:
    """Document types, their fields, and their template versions."""

    def __init__(self, session: DbSession, *, clock: Clock | None = None) -> None:
        self._db = session
        self._clock = clock or SystemClock()
        self._audit = AuditRecorder(session, self._clock)

    # -- the palette ------------------------------------------------------------------

    def palette(self, actor: AnyActor, type_id: uuid.UUID) -> tuple[PaletteField, ...]:
        """What a template of this type may bind, including the type's own fields."""
        actor.require("template.view")
        document_type = self.require_type(actor, type_id)
        return palette_for(
            Classification(document_type.classification),
            custom=self._custom_fields(document_type).values(),
        )

    def _custom_fields(self, document_type: DocumentType) -> dict[str, PaletteField]:
        from nickland.domain.templates import FieldGroup

        return {
            f"custom.{row.key}": PaletteField(
                key=f"custom.{row.key}",
                label=row.label,
                group=FieldGroup.CUSTOM,
                kind=ValueKind(row.value_kind),
                per_line=row.per_line,
            )
            for row in document_type.fields
        }

    # -- document types ---------------------------------------------------------------

    def require_type(self, actor: AnyActor, type_id: uuid.UUID) -> DocumentType:
        actor.require("template.view")
        found = self._db.get(DocumentType, type_id)
        if found is None or found.business_id != _business_of(actor):
            raise NotFoundError("Document type not found.")
        return found

    def create_type(
        self,
        actor: AnyActor,
        *,
        code: str,
        name: str,
        kind: DocumentKind | str = DocumentKind.PRO_FORMA,
        classification: Classification | str = Classification.CUSTOMER_FACING,
        data_source: str = "quotation",
        description: str | None = None,
        prefix: str | None = None,
        pattern: str = "{prefix}-{year}-{counter:4}",
        reset_cadence: ResetCadence | str = ResetCadence.ANNUALLY,
        fields: list[dict[str, Any]] | None = None,
    ) -> DocumentType:
        """Create a document type the system did not ship, with its own numbering.

        AC-06. The numbering sequence is created here rather than left to be
        configured afterwards: a document type that cannot be numbered cannot be
        issued, and discovering that at the counter is not the moment.
        """
        actor.require("template.manage")
        business_id = _business_of(actor)
        code = validate_code(code)
        if not name.strip():
            raise ValidationError("A document type needs a name.", field="name")
        if self._db.scalar(
            select(DocumentType).where(
                DocumentType.business_id == business_id, DocumentType.code == code
            )
        ):
            raise ConflictError(f"A document type with code {code!r} already exists.")

        resolved_kind = DocumentKind(str(kind))
        resolved_class = Classification(str(classification))
        prefix = (prefix or code[:6]).upper()
        NumberPattern(pattern)  # Refuses an unrenderable pattern before anything is written.

        document_type = DocumentType(
            business_id=business_id,
            code=code,
            name=name.strip(),
            kind=resolved_kind.value,
            classification=resolved_class.value,
            sequence_kind=code,
            data_source=data_source,
            description=description or None,
            is_system=False,
            display_order=900,
        )
        self._db.add(document_type)
        self._db.flush()

        for position, entry in enumerate(fields or [], start=1):
            document_type.fields.append(self._build_field(business_id, entry, position))

        self._db.add(
            NumberSequence(
                business_id=business_id,
                document_kind=code,
                prefix=prefix,
                pattern=pattern,
                reset_cadence=ResetCadence(str(reset_cadence)).value,
                next_value=1,
            )
        )

        definition = default_definition(
            resolved_kind,
            priced=resolved_kind.prices_by_default,
            options=TemplateOptions(classification=resolved_class).as_dict(),
        )
        document_type.templates.append(
            DocumentTemplate(
                business_id=business_id,
                version=1,
                name=f"{name.strip()} — standard",
                definition=definition.as_dict(),
                is_published=True,
                published_at=self._clock.now(),
                published_by_id=getattr(actor, "user_id", None),
            )
        )
        self._db.flush()

        self._audit.record(
            actor,
            "document_type_created",
            entity_type="document_type",
            entity_id=document_type.id,
            entity_label=f"{code}: {name}",
            before=None,
            after=as_dict(document_type),
        )
        return document_type

    def _build_field(
        self, business_id: uuid.UUID, entry: dict[str, Any], position: int
    ) -> DocumentTypeField:
        key = str(entry.get("key", "")).strip().lower()
        if not key or not key.replace("_", "").isalnum():
            raise ValidationError("A field key is letters, digits and underscores.", field="key")
        label = str(entry.get("label", "")).strip()
        if not label:
            raise ValidationError("A field needs a label.", field="label")
        return DocumentTypeField(
            business_id=business_id,
            key=key,
            label=label,
            value_kind=ValueKind(str(entry.get("value_kind", "text"))).value,
            per_line=bool(entry.get("per_line", False)),
            is_required=bool(entry.get("is_required", False)),
            help_text=str(entry["help_text"]) if entry.get("help_text") else None,
            display_order=position,
        )

    def add_field(self, actor: AnyActor, type_id: uuid.UUID, entry: dict[str, Any]) -> DocumentType:
        """Add a field to an administrator-defined type."""
        actor.require("template.manage")
        document_type = self.require_type(actor, type_id)
        if document_type.is_system:
            raise ConflictError(
                "A shipped document type carries the standard palette. Create your own "
                "type to add fields to it."
            )
        key = str(entry.get("key", "")).strip().lower()
        if any(row.key == key for row in document_type.fields):
            raise ConflictError(f"{key!r} is already a field on this type.")
        document_type.fields.append(
            self._build_field(document_type.business_id, entry, len(document_type.fields) + 1)
        )
        self._db.flush()
        self._audit.record(
            actor,
            "document_type_field_added",
            entity_type="document_type",
            entity_id=document_type.id,
            entity_label=f"{document_type.code}.{key}",
            before=None,
            after={"key": key},
        )
        return document_type

    def set_classification(
        self, actor: AnyActor, type_id: uuid.UUID, classification: Classification | str
    ) -> DocumentType:
        """Change a type's classification, unless documents have been issued under it.

        The lock is the point. A type reclassified from customer-facing to internal
        after the fact would retroactively permit cost bindings on templates whose
        output is already in customers' hands.
        """
        actor.require("template.manage")
        document_type = self.require_type(actor, type_id)
        resolved = Classification(str(classification))
        if resolved.value == document_type.classification:
            return document_type

        issued = self._db.scalar(
            select(IssuedDocument.id).where(IssuedDocument.type_fk == document_type.id).limit(1)
        )
        if issued is not None:
            raise ConflictError(
                f"{document_type.name} has issued documents, so its classification is "
                "fixed. Create a separate type for the other classification."
            )

        before = as_dict(document_type)
        document_type.classification = resolved.value
        self._db.flush()
        self._audit.record(
            actor,
            "document_type_reclassified",
            entity_type="document_type",
            entity_id=document_type.id,
            entity_label=f"{document_type.code}: {resolved.value}",
            before=before,
            after=as_dict(document_type),
        )
        return document_type

    # -- versions ---------------------------------------------------------------------

    def versions(self, actor: AnyActor, type_id: uuid.UUID) -> list[DocumentTemplate]:
        actor.require("template.view")
        return list(self.require_type(actor, type_id).templates)

    def draft(self, actor: AnyActor, type_id: uuid.UUID) -> DraftState:
        """The draft being worked on, opening one from the published version if needed."""
        actor.require("template.manage")
        document_type = self.require_type(actor, type_id)
        existing = [item for item in document_type.templates if not item.is_published]
        if existing:
            template = max(existing, key=lambda item: item.version)
        else:
            published = self._published(document_type)
            template = DocumentTemplate(
                business_id=document_type.business_id,
                type_fk=document_type.id,
                version=max(item.version for item in document_type.templates) + 1,
                name=published.name,
                definition=dict(published.definition),
                is_published=False,
            )
            document_type.templates.append(template)
            self._db.flush()
        return self._state(document_type, template)

    def _state(self, document_type: DocumentType, template: DocumentTemplate) -> DraftState:
        definition = TemplateDefinition.from_dict(template.definition)
        findings = validate(
            definition,
            classification=Classification(document_type.classification),
            custom=self._custom_fields(document_type),
        )
        return DraftState(template=template, definition=definition, findings=tuple(findings))

    def read(self, actor: AnyActor, template_id: uuid.UUID) -> DraftState:
        actor.require("template.view")
        template = self._require_template(actor, template_id)
        return self._state(self._owner(template), template)

    def save(
        self, actor: AnyActor, template_id: uuid.UUID, definition: dict[str, Any]
    ) -> DraftState:
        """Write the draft. Saving never validates away the work in progress.

        A half-composed template is the normal state of one being composed, so saving
        accepts it and reports what is wrong. Publishing is where the refusal lives.
        """
        actor.require("template.manage")
        template = self._require_template(actor, template_id)
        if template.is_published:
            raise ConflictError(
                "A published version cannot be edited. Open a draft from it instead: "
                "an issued document names the version it used, and that version has to "
                "go on meaning what it meant."
            )
        parsed = TemplateDefinition.from_dict(definition)
        template.definition = parsed.as_dict()
        self._db.flush()
        return self._state(self._owner(template), template)

    def publish(self, actor: AnyActor, template_id: uuid.UUID) -> DocumentTemplate:
        """Publish a draft, superseding the version in use."""
        actor.require("template.manage")
        template = self._require_template(actor, template_id)
        document_type = self._owner(template)
        if template.is_published:
            raise ConflictError("That version is already published.")

        definition = TemplateDefinition.from_dict(template.definition)
        classification = Classification(document_type.classification)
        assert_valid(
            definition,
            classification=classification,
            custom=self._custom_fields(document_type),
        )
        compile_expressions(definition)

        now = self._clock.now()
        for other in document_type.templates:
            if other.is_published:
                other.is_published = False
        template.is_published = True
        template.published_at = now
        template.published_by_id = getattr(actor, "user_id", None)
        self._db.flush()

        self._audit.record(
            actor,
            "document_template_published",
            entity_type="document_template",
            entity_id=template.id,
            entity_label=f"{document_type.name} v{template.version}",
            before=None,
            after={"version": template.version, "definition": template.definition},
        )
        return template

    def revert(self, actor: AnyActor, template_id: uuid.UUID) -> None:
        """Discard a draft and go back to what is published."""
        actor.require("template.manage")
        template = self._require_template(actor, template_id)
        if template.is_published:
            raise ConflictError("A published version cannot be discarded.")
        document_type = self._owner(template)
        version = template.version
        # Taken out of the loaded collection as well as deleted, so that a caller
        # reading the versions back in the same unit of work sees what the database
        # holds rather than the row that has just gone.
        document_type.templates.remove(template)
        self._db.delete(template)
        self._db.flush()
        self._audit.record(
            actor,
            "document_template_reverted",
            entity_type="document_type",
            entity_id=document_type.id,
            entity_label=f"{document_type.name} draft v{version}",
            before={"version": version},
            after=None,
        )

    def duplicate(self, actor: AnyActor, template_id: uuid.UUID, *, name: str) -> DocumentTemplate:
        """Copy a version into a new draft under the same type."""
        actor.require("template.manage")
        source = self._require_template(actor, template_id)
        document_type = self._owner(source)
        copy = DocumentTemplate(
            business_id=document_type.business_id,
            type_fk=document_type.id,
            version=max(item.version for item in document_type.templates) + 1,
            name=name.strip() or f"{source.name} (copy)",
            definition=dict(source.definition),
            is_published=False,
        )
        document_type.templates.append(copy)
        self._db.flush()
        return copy

    # -- structural editing -----------------------------------------------------------

    def move_section(
        self, actor: AnyActor, template_id: uuid.UUID, section_id: str, offset: int
    ) -> DraftState:
        """Move a section up or down. The drag and the arrow key call this."""
        return self._amend(
            actor,
            template_id,
            lambda definition: replace(
                definition, sections=reorder(definition.sections, section_id, offset)
            ),
        )

    def add_section(
        self, actor: AnyActor, template_id: uuid.UUID, kind: SectionKind | str, *, title: str = ""
    ) -> DraftState:
        resolved = SectionKind(str(kind))

        def amend(definition: TemplateDefinition) -> TemplateDefinition:
            section = Section(id=f"s{new_id().hex[:8]}", kind=resolved, title=title)
            return replace(definition, sections=(*definition.sections, section))

        return self._amend(actor, template_id, amend)

    def remove_section(
        self, actor: AnyActor, template_id: uuid.UUID, section_id: str
    ) -> DraftState:
        def amend(definition: TemplateDefinition) -> TemplateDefinition:
            remaining = tuple(item for item in definition.sections if item.id != section_id)
            if len(remaining) == len(definition.sections):
                raise ValidationError(f"{section_id!r} is not a section here.", field="id")
            return replace(definition, sections=remaining)

        return self._amend(actor, template_id, amend)

    def add_table_column(
        self, actor: AnyActor, template_id: uuid.UUID, section_id: str, binding: str
    ) -> DraftState:
        template = self._require_template(actor, template_id)
        document_type = self._owner(template)
        custom = self._custom_fields(document_type)
        entry = BY_KEY.get(binding) or custom.get(binding)
        if entry is None:
            raise ValidationError(f"[{binding}] is not a field in the palette.", field="binding")
        if (
            entry.is_internal
            and Classification(document_type.classification) is Classification.CUSTOMER_FACING
        ):
            # Refused here as well as at publication. The palette does not offer an
            # internal field to a customer-facing template, but a palette is a
            # convenience and this endpoint is reachable without it; a control that
            # only guards the screen is not a control.
            raise ValidationError(
                f"{entry.label} is an internal figure and cannot appear on a "
                "customer-facing document.",
                field="binding",
            )

        def amend(definition: TemplateDefinition) -> TemplateDefinition:
            return _map_section(
                definition,
                section_id,
                lambda section: add_column(section, entry, identifier=f"c{new_id().hex[:8]}"),
            )

        return self._amend(actor, template_id, amend)

    def remove_table_column(
        self, actor: AnyActor, template_id: uuid.UUID, section_id: str, column_id: str
    ) -> DraftState:
        return self._amend(
            actor,
            template_id,
            lambda definition: _map_section(
                definition, section_id, lambda section: remove_column(section, column_id)
            ),
        )

    def move_table_column(
        self,
        actor: AnyActor,
        template_id: uuid.UUID,
        section_id: str,
        column_id: str,
        offset: int,
    ) -> DraftState:
        return self._amend(
            actor,
            template_id,
            lambda definition: _map_section(
                definition,
                section_id,
                lambda section: replace(
                    section, columns=reorder(section.columns, column_id, offset)
                ),
            ),
        )

    def amend_column(
        self,
        actor: AnyActor,
        template_id: uuid.UUID,
        section_id: str,
        column_id: str,
        changes: dict[str, Any],
    ) -> DraftState:
        """Rename, resize, realign, reformat or re-condition one column."""

        def amend(definition: TemplateDefinition) -> TemplateDefinition:
            def on_section(section: Section) -> Section:
                found = False
                columns: list[Column] = []
                for column in section.columns:
                    if column.id == column_id:
                        found = True
                        columns.append(Column.from_dict({**column.as_dict(), **changes}))
                    else:
                        columns.append(column)
                if not found:
                    raise ValidationError(f"{column_id!r} is not a column here.", field="column_id")
                return replace(section, columns=tuple(columns))

            return _map_section(definition, section_id, on_section)

        return self._amend(actor, template_id, amend)

    def amend_element(
        self,
        actor: AnyActor,
        template_id: uuid.UUID,
        section_id: str,
        element_id: str,
        changes: dict[str, Any],
    ) -> DraftState:
        def amend(definition: TemplateDefinition) -> TemplateDefinition:
            def on_section(section: Section) -> Section:
                elements = [
                    Element.from_dict({**item.as_dict(), **changes})
                    if item.id == element_id
                    else item
                    for item in section.elements
                ]
                return replace(section, elements=tuple(elements))

            return _map_section(definition, section_id, on_section)

        return self._amend(actor, template_id, amend)

    def add_element(
        self, actor: AnyActor, template_id: uuid.UUID, section_id: str, element: dict[str, Any]
    ) -> DraftState:
        def amend(definition: TemplateDefinition) -> TemplateDefinition:
            built = Element.from_dict({**element, "id": f"e{new_id().hex[:8]}"})
            return _map_section(
                definition,
                section_id,
                lambda section: replace(section, elements=(*section.elements, built)),
            )

        return self._amend(actor, template_id, amend)

    def remove_element(
        self, actor: AnyActor, template_id: uuid.UUID, section_id: str, element_id: str
    ) -> DraftState:
        return self._amend(
            actor,
            template_id,
            lambda definition: _map_section(
                definition,
                section_id,
                lambda section: replace(
                    section,
                    elements=tuple(item for item in section.elements if item.id != element_id),
                ),
            ),
        )

    def set_page(self, actor: AnyActor, template_id: uuid.UUID, page: dict[str, Any]) -> DraftState:
        from nickland.domain.templates import PageConfiguration

        return self._amend(
            actor,
            template_id,
            lambda definition: replace(
                definition,
                page=PageConfiguration.from_dict({**definition.page.as_dict(), **page}),
            ),
        )

    def _amend(
        self,
        actor: AnyActor,
        template_id: uuid.UUID,
        change: Any,
    ) -> DraftState:
        actor.require("template.manage")
        template = self._require_template(actor, template_id)
        if template.is_published:
            raise ConflictError(
                "A published version cannot be edited. Open a draft from it instead."
            )
        definition = change(TemplateDefinition.from_dict(template.definition))
        template.definition = definition.as_dict()
        self._db.flush()
        return self._state(self._owner(template), template)

    # -- portability ------------------------------------------------------------------

    def export(self, actor: AnyActor, template_id: uuid.UUID) -> Portable:
        actor.require("template.manage")
        template = self._require_template(actor, template_id)
        document_type = self._owner(template)
        return export_template(
            TemplateDefinition.from_dict(template.definition),
            kind=document_type.kind,
            classification=Classification(document_type.classification),
            name=template.name,
        )

    def inspect_import(self, actor: AnyActor, type_id: uuid.UUID, payload: dict[str, Any]) -> Any:
        """What an import would do, without doing it."""
        actor.require("template.manage")
        document_type = self.require_type(actor, type_id)
        return import_template(
            payload,
            custom=self._custom_fields(document_type),
            into=Classification(document_type.classification),
        )

    def accept_import(
        self, actor: AnyActor, type_id: uuid.UUID, payload: dict[str, Any]
    ) -> DocumentTemplate:
        """Bring an exported template in as a draft, refusing unresolved bindings."""
        actor.require("template.manage")
        document_type = self.require_type(actor, type_id)
        report = import_template(
            payload,
            custom=self._custom_fields(document_type),
            into=Classification(document_type.classification),
        )
        if report.unresolved:
            raise ValidationError(
                "This template reads fields this installation does not have: "
                + ", ".join(report.unresolved)
                + ". Add them to the document type, or amend the template before "
                "importing it.",
                field="bindings",
                unresolved=list(report.unresolved),
            )
        blocking = [item for item in report.findings if item.is_blocking]
        if blocking:
            raise ValidationError(
                "This template cannot be used here: "
                + "; ".join(f"{item.where}: {item.message}" for item in blocking[:4]),
                field="definition",
            )

        draft = DocumentTemplate(
            business_id=document_type.business_id,
            type_fk=document_type.id,
            version=max(item.version for item in document_type.templates) + 1,
            name=report.name,
            definition=report.definition.as_dict(),
            is_published=False,
        )
        document_type.templates.append(draft)
        self._db.flush()
        self._audit.record(
            actor,
            "document_template_imported",
            entity_type="document_template",
            entity_id=draft.id,
            entity_label=f"{document_type.name}: {report.name}",
            before=None,
            after={"version": draft.version, "name": report.name},
        )
        return draft

    def export_bytes(self, actor: AnyActor, template_id: uuid.UUID) -> bytes:
        """The exported template as a file, for download."""
        return json.dumps(self.export(actor, template_id).as_dict(), indent=2).encode("utf-8")

    # -- helpers ----------------------------------------------------------------------

    def _require_template(self, actor: AnyActor, template_id: uuid.UUID) -> DocumentTemplate:
        template = self._db.get(DocumentTemplate, template_id)
        if template is None or template.business_id != _business_of(actor):
            raise NotFoundError("Template not found.")
        return template

    def _owner(self, template: DocumentTemplate) -> DocumentType:
        document_type = self._db.get(DocumentType, template.type_fk)
        if document_type is None:  # pragma: no cover — the foreign key forbids it
            raise NotFoundError("Document type not found.")
        return document_type

    def _published(self, document_type: DocumentType) -> DocumentTemplate:
        published = [item for item in document_type.templates if item.is_published]
        if not published:
            raise ConflictError(f"{document_type.name} has no published template.")
        return max(published, key=lambda item: item.version)

    def options_for(self, template: DocumentTemplate) -> TemplateOptions:
        return options_of(template.definition)


def _map_section(
    definition: TemplateDefinition, section_id: str, change: Any
) -> TemplateDefinition:
    found = False
    sections: list[Section] = []
    for section in definition.sections:
        if section.id == section_id:
            found = True
            sections.append(change(section))
        else:
            sections.append(section)
    if not found:
        raise ValidationError(f"{section_id!r} is not a section here.", field="section_id")
    return replace(definition, sections=tuple(sections))


def _business_of(actor: AnyActor) -> uuid.UUID:
    return actor.business_id


def business_timezone(session: DbSession, business_id: uuid.UUID) -> str:
    business = session.get(Business, business_id)
    return business.timezone if business is not None else "UTC"
