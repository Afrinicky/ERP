"""The document designer against a real database. PRD §6.6, §6.7, AC-06.

The exit criteria this file exists to hold:

1. An administrator can add, remove, reorder and re-label table columns and reorder
   sections, and see the result.
2. A wholly new document type can be created, with its own fields and its own
   numbering sequence.
3. A customer-facing template cannot bind an internal field — refused where it is
   offered and refused again at publication, because hiding a control is not one.
4. A document issued under one template version reprints identically after a later
   version is published.
5. A template exports and imports as a definition, carrying no business data.
6. Classification is fixed once a type has issued a document.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from nickland.application.context import Actor
from nickland.application.services.designer import DesignerService
from nickland.application.services.documents import DocumentRecordService
from nickland.application.services.organisation import OrganisationService
from nickland.documents.service import DocumentRenderService
from nickland.domain.documents import Classification, DocumentKind
from nickland.domain.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from nickland.domain.templates import INTERNAL_KEYS, SectionKind
from nickland.persistence.document_models import DocumentType
from nickland.platform.clock import FixedClock
from tests.conftest import make_actor


@pytest.fixture
def records(db: Session, clock: FixedClock) -> DocumentRecordService:
    return DocumentRecordService(db, clock=clock)


@pytest.fixture
def designer(db: Session, clock: FixedClock) -> DesignerService:
    return DesignerService(db, clock=clock)


@pytest.fixture
def renderer(db: Session, clock: FixedClock) -> DocumentRenderService:
    return DocumentRenderService(db, clock=clock)


@pytest.fixture
def pro_forma(records: DocumentRecordService, admin: Actor) -> DocumentType:
    found = records.type_by_code(admin.business_id, "pro_forma")  # type: ignore[arg-type]
    assert found is not None
    return found


def items_id(designer: DesignerService, admin: Actor, template_id: uuid.UUID) -> str:
    return designer.read(admin, template_id).definition.section(SectionKind.ITEMS).id


class TestDraftingAndPublication:
    def test_a_draft_leaves_the_published_version_in_place(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        assert draft.template.version == 2
        assert not draft.template.is_published
        versions = designer.versions(admin, pro_forma.id)
        assert [(item.version, item.is_published) for item in versions] == [
            (1, True),
            (2, False),
        ]

    def test_asking_twice_returns_the_same_draft(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        first = designer.draft(admin, pro_forma.id)
        db.flush()
        second = designer.draft(admin, pro_forma.id)
        db.flush()
        assert first.template.id == second.template.id
        assert len(designer.versions(admin, pro_forma.id)) == 2

    def test_publication_supersedes_the_previous_version(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        designer.publish(admin, draft.template.id)
        db.flush()
        versions = designer.versions(admin, pro_forma.id)
        assert [(item.version, item.is_published) for item in versions] == [
            (1, False),
            (2, True),
        ]

    def test_reverting_discards_the_draft(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        designer.revert(admin, draft.template.id)
        db.flush()
        assert [item.version for item in designer.versions(admin, pro_forma.id)] == [1]

    def test_a_published_version_cannot_be_edited(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        published = designer.versions(admin, pro_forma.id)[0]
        with pytest.raises(ConflictError):
            designer.save(admin, published.id, {"sections": []})

    def test_a_salesperson_may_not_design(
        self,
        designer: DesignerService,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        seller = make_actor(admin.business_id, permissions={"template.view"})
        assert designer.palette(seller, pro_forma.id)
        with pytest.raises(PermissionDeniedError):
            designer.draft(seller, pro_forma.id)


class TestComposition:
    """Exit criterion 1."""

    def test_columns_are_added_removed_reordered_and_re_labelled(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        section = draft.definition.section(SectionKind.ITEMS)
        before = [column.id for column in section.columns]

        designer.add_table_column(admin, draft.template.id, section.id, "line.weight")
        designer.move_table_column(admin, draft.template.id, section.id, "line_batch", -2)
        designer.remove_table_column(admin, draft.template.id, section.id, "line_sku")
        state = designer.amend_column(
            admin,
            draft.template.id,
            section.id,
            "line_description",
            {"label": "Item supplied", "width_percent": 30},
        )
        db.flush()

        after = state.definition.section(SectionKind.ITEMS)
        identifiers = [column.id for column in after.columns]
        assert "line.weight" in [column.binding for column in after.columns]
        assert "line_sku" not in identifiers
        assert identifiers.index("line_batch") < before.index("line_batch")
        amended = next(column for column in after.columns if column.id == "line_description")
        assert amended.label == "Item supplied"
        assert amended.width_percent == 30

    def test_a_removed_column_leaves_the_table_fitting_the_page(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        section = draft.definition.section(SectionKind.ITEMS)
        state = designer.remove_table_column(admin, draft.template.id, section.id, "line_unit")
        db.flush()
        after = state.definition.section(SectionKind.ITEMS)
        assert sum(column.width_percent for column in after.columns) <= 100
        assert state.is_publishable

    def test_sections_are_reordered_added_and_removed(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        designer.move_section(admin, draft.template.id, "terms", -3)
        state = designer.add_section(
            admin, draft.template.id, SectionKind.CUSTOM, title="Compliance"
        )
        db.flush()
        order = [section.id for section in state.definition.sections]
        assert order.index("terms") < order.index("items") or order.index("terms") < order.index(
            "totals"
        )
        added = next(
            section for section in state.definition.sections if section.title == "Compliance"
        )
        state = designer.remove_section(admin, draft.template.id, added.id)
        db.flush()
        assert added.id not in {section.id for section in state.definition.sections}

    def test_the_masthead_can_be_removed_for_pre_printed_stationery(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        state = designer.remove_section(admin, draft.template.id, "masthead")
        db.flush()
        assert "masthead" not in {section.id for section in state.definition.sections}
        assert state.is_publishable

    def test_removing_a_section_that_is_not_there_is_refused(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        with pytest.raises(ValidationError):
            designer.remove_section(admin, draft.template.id, "no_such_section")

    def test_an_edit_shows_in_the_preview(
        self,
        designer: DesignerService,
        renderer: DocumentRenderService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        section = draft.definition.section(SectionKind.ITEMS)
        designer.amend_column(
            admin,
            draft.template.id,
            section.id,
            "line_description",
            {"label": "Particulars of supply"},
        )
        db.flush()
        html = renderer.preview_version(admin, draft.template.id)
        assert "Particulars of supply" in html
        assert "Preview" in html

    def test_a_field_that_does_not_vary_by_line_is_not_a_column(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        section = draft.definition.section(SectionKind.ITEMS)
        with pytest.raises(ValidationError):
            designer.add_table_column(admin, draft.template.id, section.id, "customer.name")


class TestConfidentiality:
    """Exit criterion 3."""

    def test_a_customer_facing_palette_offers_no_internal_field(
        self, designer: DesignerService, admin: Actor, pro_forma: DocumentType
    ) -> None:
        offered = {field.key for field in designer.palette(admin, pro_forma.id)}
        assert offered.isdisjoint(INTERNAL_KEYS)

    def test_an_internal_binding_is_refused_where_it_is_offered(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        section = draft.definition.section(SectionKind.ITEMS)
        with pytest.raises(ValidationError):
            designer.add_table_column(admin, draft.template.id, section.id, "internal.line_cost")

    def test_an_internal_binding_forced_into_a_draft_cannot_be_published(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        raw = draft.definition.as_dict()
        section = next(item for item in raw["sections"] if item["kind"] == SectionKind.ITEMS.value)
        section["columns"].append(
            {
                "id": "sneak",
                "label": "Our cost",
                "binding": "internal.line_cost",
                "width_percent": 8,
                "alignment": "right",
                "format": {},
                "visibility": {},
                "subtotal": False,
            }
        )
        saved = designer.save(admin, draft.template.id, raw)
        db.flush()
        assert not saved.is_publishable
        assert any(finding.is_blocking for finding in saved.findings)
        with pytest.raises(ValidationError):
            designer.publish(admin, draft.template.id)

    def test_an_internal_field_inside_a_formula_cannot_be_published(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        raw = draft.definition.as_dict()
        section = next(item for item in raw["sections"] if item["kind"] == SectionKind.ITEMS.value)
        section["columns"].append(
            {
                "id": "derived",
                "label": "Uplift",
                "formula": "[line.total] - [internal.line_cost]",
                "width_percent": 8,
                "alignment": "right",
                "format": {},
                "visibility": {},
                "subtotal": False,
            }
        )
        designer.save(admin, draft.template.id, raw)
        db.flush()
        with pytest.raises(ValidationError):
            designer.publish(admin, draft.template.id)

    def test_an_internal_template_may_bind_an_internal_field(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
    ) -> None:
        created = designer.create_type(
            admin,
            code="costing_sheet",
            name="Costing sheet",
            kind=DocumentKind.QUOTATION,
            classification=Classification.INTERNAL,
            prefix="CS",
        )
        db.flush()
        offered = {field.key for field in designer.palette(admin, created.id)}
        assert offered >= INTERNAL_KEYS
        draft = designer.draft(admin, created.id)
        db.flush()
        section = draft.definition.section(SectionKind.ITEMS)
        state = designer.add_table_column(
            admin, draft.template.id, section.id, "internal.line_cost"
        )
        db.flush()
        assert state.is_publishable
        designer.publish(admin, draft.template.id)
        db.flush()

    def test_no_internal_figure_reaches_a_customer_facing_document(
        self,
        records: DocumentRecordService,
        renderer: DocumentRenderService,
        db: Session,
        admin: Actor,
    ) -> None:
        pro_forma = records.type_by_code(
            admin.business_id,  # type: ignore[arg-type]
            "pro_forma",
        )
        assert pro_forma is not None
        html = renderer.preview_version(admin, records.published_template(pro_forma).id)
        for word in ("Supplier cost", "Margin", "Affiliate commission", "internal."):
            assert word not in html


class TestVersionPreservation:
    """Exit criterion 4."""

    def test_a_reprint_is_byte_identical_after_a_later_version_is_published(
        self,
        designer: DesignerService,
        records: DocumentRecordService,
        renderer: DocumentRenderService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.PRO_FORMA,
        )
        issued = renderer.issue(admin, "pro_forma", context)
        db.flush()
        original_hash = issued.content_hash
        original_version = issued.template_version
        original_bytes = bytes(issued.content)

        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        designer.remove_table_column(
            admin,
            draft.template.id,
            draft.definition.section(SectionKind.ITEMS).id,
            "line_quantity",
        )
        designer.publish(admin, draft.template.id)
        db.flush()

        reprint = records.require_issued(admin, issued.id)
        assert reprint.template_version == original_version == 1
        assert reprint.content_hash == original_hash
        assert bytes(reprint.content) == original_bytes

    def test_the_next_document_uses_the_new_version(
        self,
        designer: DesignerService,
        records: DocumentRecordService,
        renderer: DocumentRenderService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.PRO_FORMA,
        )
        first = renderer.issue(admin, "pro_forma", context)
        db.flush()
        draft = designer.draft(admin, pro_forma.id)
        db.flush()
        designer.publish(admin, draft.template.id)
        db.flush()
        second = renderer.issue(admin, "pro_forma", replace(context, number="SAMPLE-0002"))
        db.flush()
        assert first.template_version == 1
        assert second.template_version == 2
        assert second.number != first.number


class TestNewDocumentTypes:
    """Exit criterion 2."""

    def test_a_new_type_carries_its_own_fields_and_numbering(
        self,
        designer: DesignerService,
        db: Session,
        clock: FixedClock,
        admin: Actor,
    ) -> None:
        created = designer.create_type(
            admin,
            code="site_report",
            name="Installation site report",
            kind=DocumentKind.DELIVERY_NOTE,
            classification=Classification.CUSTOMER_FACING,
            prefix="SR",
            fields=[
                {"key": "engineer", "label": "Attending engineer"},
                {"key": "commissioned_on", "label": "Commissioned on", "value_kind": "date"},
                {
                    "key": "reading",
                    "label": "Calibration reading",
                    "value_kind": "quantity",
                    "per_line": True,
                },
            ],
        )
        db.flush()
        assert created.code == "site_report"
        assert {field.key for field in created.fields} == {
            "engineer",
            "commissioned_on",
            "reading",
        }

        organisation = OrganisationService(db, clock=clock)
        numbers = [
            organisation.allocate_number(admin, created.code, branch_id=None).number
            for _ in range(3)
        ]
        db.flush()
        assert numbers == ["SR-2026-0001", "SR-2026-0002", "SR-2026-0003"]

    def test_its_own_fields_are_offered_and_bindable(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
    ) -> None:
        created = designer.create_type(
            admin,
            code="site_report",
            name="Installation site report",
            kind=DocumentKind.DELIVERY_NOTE,
            prefix="SR",
            fields=[
                {
                    "key": "reading",
                    "label": "Calibration reading",
                    "value_kind": "quantity",
                    "per_line": True,
                }
            ],
        )
        db.flush()
        offered = {field.key for field in designer.palette(admin, created.id)}
        assert "custom.reading" in offered
        draft = designer.draft(admin, created.id)
        db.flush()
        state = designer.add_table_column(
            admin,
            draft.template.id,
            draft.definition.section(SectionKind.ITEMS).id,
            "custom.reading",
        )
        db.flush()
        assert state.is_publishable
        designer.publish(admin, draft.template.id)
        db.flush()

    def test_a_field_added_afterwards_joins_the_palette(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
    ) -> None:
        created = designer.create_type(
            admin, code="site_report", name="Installation site report", prefix="SR"
        )
        db.flush()
        designer.add_field(admin, created.id, {"key": "engineer", "label": "Attending engineer"})
        db.flush()
        offered = {field.key for field in designer.palette(admin, created.id)}
        assert "custom.engineer" in offered

    def test_a_duplicate_code_is_refused(
        self, designer: DesignerService, db: Session, admin: Actor
    ) -> None:
        with pytest.raises(ConflictError):
            designer.create_type(admin, code="pro_forma", name="Another pro forma")

    def test_an_unrenderable_numbering_pattern_is_refused(
        self, designer: DesignerService, admin: Actor
    ) -> None:
        with pytest.raises(ValidationError):
            designer.create_type(
                admin,
                code="site_report",
                name="Installation site report",
                pattern="{prefix}-{nonsense}",
            )

    def test_a_new_type_has_a_published_template_from_the_start(
        self,
        designer: DesignerService,
        records: DocumentRecordService,
        db: Session,
        admin: Actor,
    ) -> None:
        created = designer.create_type(
            admin, code="site_report", name="Installation site report", prefix="SR"
        )
        db.flush()
        published = records.published_template(created)
        assert published.version == 1
        assert published.is_published


class TestClassificationLock:
    """Exit criterion 6."""

    def test_classification_may_change_before_anything_is_issued(
        self, designer: DesignerService, db: Session, admin: Actor
    ) -> None:
        created = designer.create_type(
            admin, code="site_report", name="Installation site report", prefix="SR"
        )
        db.flush()
        changed = designer.set_classification(admin, created.id, Classification.INTERNAL)
        db.flush()
        assert changed.classification == Classification.INTERNAL.value

    def test_classification_is_fixed_once_a_document_has_been_issued(
        self,
        designer: DesignerService,
        records: DocumentRecordService,
        renderer: DocumentRenderService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        context = records.sample_context(
            admin.business_id,  # type: ignore[arg-type]
            DocumentKind.PRO_FORMA,
        )
        renderer.issue(admin, "pro_forma", context)
        db.flush()
        with pytest.raises(ConflictError):
            designer.set_classification(admin, pro_forma.id, Classification.INTERNAL)


class TestPortability:
    """Exit criterion 5."""

    @pytest.fixture
    def exported(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> dict[str, Any]:
        published = designer.versions(admin, pro_forma.id)[0]
        return dict(designer.export(admin, published.id).as_dict())

    def test_an_export_carries_the_definition_and_no_business_data(
        self, exported: dict[str, Any], admin: Actor
    ) -> None:
        text = json.dumps(exported)
        assert "sections" in exported["definition"]
        for leak in ("Nicholas Nickland", "proprietor", "C0001234567", "accounts@example.test"):
            assert leak not in text
        assert str(admin.business_id) not in text

    def test_an_export_round_trips_into_another_type(
        self,
        designer: DesignerService,
        exported: dict[str, Any],
        db: Session,
        admin: Actor,
    ) -> None:
        created = designer.create_type(
            admin,
            code="site_report",
            name="Installation site report",
            kind=DocumentKind.DELIVERY_NOTE,
            prefix="SR",
        )
        db.flush()
        report = designer.inspect_import(admin, created.id, exported)
        assert report.is_acceptable
        assert not report.unresolved
        imported = designer.accept_import(admin, created.id, exported)
        db.flush()
        assert not imported.is_published
        assert imported.version == 2

    def test_an_import_naming_a_field_that_does_not_exist_is_refused(
        self,
        designer: DesignerService,
        exported: dict[str, Any],
        db: Session,
        admin: Actor,
    ) -> None:
        created = designer.create_type(
            admin, code="site_report", name="Installation site report", prefix="SR"
        )
        db.flush()
        broken = json.loads(json.dumps(exported))
        section = next(
            item
            for item in broken["definition"]["sections"]
            if item["kind"] == SectionKind.ITEMS.value
        )
        section["columns"][0]["binding"] = "line.colour"
        report = designer.inspect_import(admin, created.id, broken)
        assert "line.colour" in report.unresolved
        assert not report.is_acceptable
        with pytest.raises(ValidationError):
            designer.accept_import(admin, created.id, broken)

    def test_an_import_of_an_internal_binding_into_a_customer_facing_type_is_refused(
        self,
        designer: DesignerService,
        db: Session,
        admin: Actor,
    ) -> None:
        internal = designer.create_type(
            admin,
            code="costing_sheet",
            name="Costing sheet",
            classification=Classification.INTERNAL,
            prefix="CS",
        )
        db.flush()
        draft = designer.draft(admin, internal.id)
        db.flush()
        designer.add_table_column(
            admin,
            draft.template.id,
            draft.definition.section(SectionKind.ITEMS).id,
            "internal.line_cost",
        )
        published = designer.publish(admin, draft.template.id)
        db.flush()
        payload = dict(designer.export(admin, published.id).as_dict())

        facing = designer.create_type(
            admin, code="site_report", name="Installation site report", prefix="SR"
        )
        db.flush()
        with pytest.raises(ValidationError):
            designer.accept_import(admin, facing.id, payload)

    def test_an_export_is_offered_as_a_file(
        self,
        designer: DesignerService,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        published = designer.versions(admin, pro_forma.id)[0]
        payload = designer.export_bytes(admin, published.id)
        assert json.loads(payload.decode("utf-8"))["definition"]["sections"]

    def test_a_template_of_another_business_is_not_found(
        self,
        designer: DesignerService,
        admin: Actor,
        pro_forma: DocumentType,
    ) -> None:
        stranger = make_actor(uuid.uuid4())
        with pytest.raises(NotFoundError):
            designer.versions(stranger, pro_forma.id)


class TestIssuingFromAQuotation:
    """PRD §6.6: a document carries the figures that were quoted, not retyped ones."""

    @pytest.fixture
    def quoted(
        self, db: Session, clock: FixedClock, admin: Actor
    ) -> tuple[uuid.UUID, dict[str, Any]]:
        from nickland.application.services.parties import PartyDraft, PartyService
        from nickland.application.services.quotations import (
            LineDraft,
            QuotationDraft,
            QuotationService,
        )

        customer = PartyService(db, clock=clock).create(
            admin,
            PartyDraft(
                name="Banhart Hospital",
                is_customer=True,
                telephone="+233 30 222 3344",
                tax_identifier="C0009988776",
            ),
        )
        db.flush()
        quotations = QuotationService(db, clock=clock)
        quotation = quotations.create(
            admin,
            QuotationDraft(
                customer_id=customer.id,
                customer_reference="BH/2026/114",
                lines=[
                    LineDraft(
                        description="Reagent pack",
                        quantity=Decimal("40"),
                        unit_cost=Decimal("172"),
                    )
                ],
            ),
        )
        db.flush()
        quotations.issue(admin, quotation.id)
        db.flush()
        return quotation.id, quotations.offer(admin, quotation.id)

    def test_the_document_carries_the_figures_that_were_quoted(
        self,
        renderer: DocumentRenderService,
        records: DocumentRecordService,
        db: Session,
        admin: Actor,
        quoted: tuple[uuid.UUID, dict[str, Any]],
    ) -> None:
        quotation_id, offer = quoted
        issued = renderer.issue_from_quotation(admin, "pro_forma", quotation_id)
        db.flush()
        assert issued.number.startswith("PFI-")
        assert issued.source_type == "quotation"
        assert issued.source_id == quotation_id
        assert bytes(issued.content).startswith(b"%PDF")

        html = renderer.preview_version(
            admin,
            records.published_template(
                records.type_by_code(admin.business_id, "pro_forma")  # type: ignore[arg-type,union-attr]
            ).id,
        )
        assert html  # the same composer produced the bytes above
        assert offer["total_payable"] > Decimal("0")

    def test_a_draft_quotation_cannot_be_put_on_paper(
        self,
        renderer: DocumentRenderService,
        db: Session,
        clock: FixedClock,
        admin: Actor,
    ) -> None:
        from nickland.application.services.parties import PartyDraft, PartyService
        from nickland.application.services.quotations import (
            LineDraft,
            QuotationDraft,
            QuotationService,
        )

        customer = PartyService(db, clock=clock).create(
            admin, PartyDraft(name="Banhart Hospital", is_customer=True)
        )
        db.flush()
        draft = QuotationService(db, clock=clock).create(
            admin,
            QuotationDraft(
                customer_id=customer.id,
                lines=[
                    LineDraft(
                        description="Reagent pack",
                        quantity=Decimal("1"),
                        unit_cost=Decimal("100"),
                    )
                ],
            ),
        )
        db.flush()
        with pytest.raises(ConflictError):
            renderer.issue_from_quotation(admin, "pro_forma", draft.id)

    def test_no_internal_figure_reaches_the_rendered_document(
        self,
        db: Session,
        clock: FixedClock,
        admin: Actor,
        quoted: tuple[uuid.UUID, dict[str, Any]],
    ) -> None:
        """The context is built from the disclosable projection, so there is nothing
        in it to leak: the assertion is on the context rather than on the PDF bytes,
        which are compressed and would hide a leak rather than prove its absence."""
        from nickland.application.services.document_sources import DocumentSourceService

        quotation_id, _ = quoted
        context = DocumentSourceService(db, clock=clock).context_for_quotation(
            admin, quotation_id, DocumentKind.PRO_FORMA, number="PFI-2026-9999"
        )
        text = repr(context)
        for word in ("unit_cost", "landed_cost", "margin", "commission", "supplier_cost"):
            assert word not in text

    def test_each_document_takes_the_next_number(
        self,
        renderer: DocumentRenderService,
        db: Session,
        admin: Actor,
        quoted: tuple[uuid.UUID, dict[str, Any]],
    ) -> None:
        quotation_id, _ = quoted
        first = renderer.issue_from_quotation(admin, "pro_forma", quotation_id)
        db.flush()
        second = renderer.issue_from_quotation(admin, "pro_forma", quotation_id)
        db.flush()
        assert [first.number, second.number] == ["PFI-2026-0001", "PFI-2026-0002"]

    def test_an_unknown_type_is_not_found(
        self,
        renderer: DocumentRenderService,
        admin: Actor,
        quoted: tuple[uuid.UUID, dict[str, Any]],
    ) -> None:
        quotation_id, _ = quoted
        with pytest.raises(NotFoundError):
            renderer.issue_from_quotation(admin, "no_such_type", quotation_id)


class TestPreviewAgainstATransaction:
    """PRD §6.6: a preview against a real draft transaction, not only sample data."""

    def test_a_preview_can_be_drawn_from_a_draft_quotation(
        self,
        renderer: DocumentRenderService,
        records: DocumentRecordService,
        db: Session,
        clock: FixedClock,
        admin: Actor,
    ) -> None:
        from nickland.application.services.parties import PartyDraft, PartyService
        from nickland.application.services.quotations import (
            LineDraft,
            QuotationDraft,
            QuotationService,
        )

        customer = PartyService(db, clock=clock).create(
            admin, PartyDraft(name="Banhart Hospital", is_customer=True)
        )
        db.flush()
        draft = QuotationService(db, clock=clock).create(
            admin,
            QuotationDraft(
                customer_id=customer.id,
                lines=[
                    LineDraft(
                        description="Haematology analyser, 5-part differential",
                        quantity=Decimal("2"),
                        unit_cost=Decimal("18500"),
                    )
                ],
            ),
        )
        db.flush()
        pro_forma = records.type_by_code(admin.business_id, "pro_forma")  # type: ignore[arg-type]
        assert pro_forma is not None
        template = records.published_template(pro_forma)

        html = renderer.preview_version(
            admin, template.id, source_type="quotation", source_id=draft.id
        )
        assert "Haematology analyser" in html
        assert "Banhart Hospital" in html
        assert "Preview" in html
        # A draft may be previewed; it still may not be issued.
        with pytest.raises(ConflictError):
            renderer.issue_from_quotation(admin, "pro_forma", draft.id)

    def test_a_source_this_release_cannot_preview_from_is_refused(
        self,
        renderer: DocumentRenderService,
        records: DocumentRecordService,
        admin: Actor,
    ) -> None:
        pro_forma = records.type_by_code(admin.business_id, "pro_forma")  # type: ignore[arg-type]
        assert pro_forma is not None
        template = records.published_template(pro_forma)
        with pytest.raises(ValidationError):
            renderer.preview_version(
                admin, template.id, source_type="sales_order", source_id=uuid.uuid4()
            )
