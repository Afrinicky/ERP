"""Phase 6: document type fields, acknowledgements, and the type's data source

An administrator-defined document type carries its own fields, which enter the palette
for templates of that type; ``document_type_fields`` holds them. ``document_acknowledgements``
holds the signed or scanned proof that a document was received, with the evidence kept
as the bytes that were handed over.

``document_types`` gains the transaction a template binds against and a description,
and the three seeded types whose ``sequence_kind`` named no existing numbering
sequence are corrected.

Revision: 8dd41cdab3b8
Previous: 82cb71d98d76
Created:  2026-11-06 20:36:01.458822+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

import nickland.persistence.types


revision: str = "8dd41cdab3b8"
down_revision: str | None = "82cb71d98d76"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "document_type_fields",
        sa.Column("type_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("value_kind", sa.String(length=16), nullable=False),
        sa.Column("per_line", sa.Boolean(), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("help_text", sa.String(length=200), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("id", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            nickland.persistence.types.UtcDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            nickland.persistence.types.UtcDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("business_id", nickland.persistence.types.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["type_fk"],
            ["document_types.id"],
            name=op.f("fk_document_type_fields_type_fk_document_types"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_type_fields")),
        sa.UniqueConstraint("type_fk", "key", name=op.f("uq_document_type_fields_type_fk_key")),
    )
    with op.batch_alter_table("document_type_fields", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_document_type_fields_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "document_acknowledgements",
        sa.Column("document_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("recipient_name", sa.String(length=160), nullable=False),
        sa.Column("recipient_role", sa.String(length=80), nullable=True),
        sa.Column("acknowledged_at", nickland.persistence.types.UtcDateTime(), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("evidence_kind", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=80), nullable=True),
        sa.Column("evidence", sa.LargeBinary(), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("recorded_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("id", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            nickland.persistence.types.UtcDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            nickland.persistence.types.UtcDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("business_id", nickland.persistence.types.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_fk"],
            ["issued_documents.id"],
            name=op.f("fk_document_acknowledgements_document_fk_issued_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_acknowledgements")),
    )
    with op.batch_alter_table("document_acknowledgements", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_document_acknowledgements_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_document_acknowledgements_document", ["business_id", "document_fk"], unique=False
        )

    with op.batch_alter_table("document_types", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        # A server default, so the twelve types a Phase 3.5 installation already
        # holds are backfilled rather than refused. The model's own default covers
        # rows written from here on; this one exists for the rows already there.
        batch_op.add_column(
            sa.Column(
                "data_source",
                sa.String(length=32),
                nullable=False,
                server_default="quotation",
            )
        )

    # Three of the twelve seeded types named a numbering sequence that does not
    # exist: "proforma", "invoice" and "grn", against an enumeration that spells
    # them "pro_forma", "sales_invoice" and "goods_receipt". A type whose sequence
    # cannot be found cannot be numbered, and so cannot be issued at all. The seed
    # is corrected in code; these are the rows already written.
    for wrong, right in (
        ("proforma", "pro_forma"),
        ("invoice", "sales_invoice"),
        ("grn", "goods_receipt"),
    ):
        op.execute(
            sa.text(
                "UPDATE document_types SET sequence_kind = :right WHERE sequence_kind = :wrong"
            ).bindparams(right=right, wrong=wrong)
        )


def downgrade() -> None:
    for right, wrong in (
        ("pro_forma", "proforma"),
        ("sales_invoice", "invoice"),
        ("goods_receipt", "grn"),
    ):
        op.execute(
            sa.text(
                "UPDATE document_types SET sequence_kind = :wrong WHERE sequence_kind = :right"
            ).bindparams(right=right, wrong=wrong)
        )

    with op.batch_alter_table("document_types", schema=None) as batch_op:
        batch_op.drop_column("data_source")
        batch_op.drop_column("description")

    with op.batch_alter_table("document_acknowledgements", schema=None) as batch_op:
        batch_op.drop_index("ix_document_acknowledgements_document")
        batch_op.drop_index(batch_op.f("ix_document_acknowledgements_business_id"))

    op.drop_table("document_acknowledgements")
    with op.batch_alter_table("document_type_fields", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_document_type_fields_business_id"))

    op.drop_table("document_type_fields")
