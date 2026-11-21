"""Phase 14: management reporting and period close.

Saved report runs and their notes, report schedules and deliveries to each recipient's
inbox; the period-close checklist a person confirms, and requests to reopen a closed period
decided by a second person.

Revision: 3d034cf54ce5
Previous: eac027ba1050
Created:  2026-11-21 15:56:55.822135+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

import nickland.persistence.types


revision: str = "3d034cf54ce5"
down_revision: str | None = "eac027ba1050"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "report_schedules",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("report_id", sa.String(length=40), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("frequency", sa.String(length=12), nullable=False),
        sa.Column("run_day", sa.Integer(), nullable=False),
        sa.Column("run_hour", sa.Integer(), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("formats", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("last_run_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by_id", nickland.persistence.types.Uuid(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_schedules")),
    )
    with op.batch_alter_table("report_schedules", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_report_schedules_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_report_schedules_next_run_at"), ["next_run_at"], unique=False
        )

    op.create_table(
        "period_checklist_items",
        sa.Column("period_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("done_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("done_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
            ["period_fk"],
            ["accounting_periods.id"],
            name=op.f("fk_period_checklist_items_period_fk_accounting_periods"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_period_checklist_items")),
        sa.UniqueConstraint(
            "period_fk", "key", name=op.f("uq_period_checklist_items_period_fk_key")
        ),
    )
    with op.batch_alter_table("period_checklist_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_period_checklist_items_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_period_checklist_items_period_fk"), ["period_fk"], unique=False
        )

    op.create_table(
        "period_reopen_requests",
        sa.Column("period_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("requested_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("requested_at", nickland.persistence.types.UtcDateTime(), nullable=False),
        sa.Column("decided_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("decided_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("self_approved", sa.Boolean(), nullable=False),
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
            ["period_fk"],
            ["accounting_periods.id"],
            name=op.f("fk_period_reopen_requests_period_fk_accounting_periods"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_period_reopen_requests")),
    )
    with op.batch_alter_table("period_reopen_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_period_reopen_requests_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_period_reopen_requests_period_fk"), ["period_fk"], unique=False
        )

    op.create_table(
        "report_runs",
        sa.Column("report_id", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("period_label", sa.String(length=80), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("reconciled", sa.Boolean(), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False),
        sa.Column("generated_at", nickland.persistence.types.UtcDateTime(), nullable=False),
        sa.Column("generated_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("schedule_fk", nickland.persistence.types.Uuid(), nullable=True),
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
            ["schedule_fk"],
            ["report_schedules.id"],
            name=op.f("fk_report_runs_schedule_fk_report_schedules"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_runs")),
    )
    with op.batch_alter_table("report_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_report_runs_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_report_runs_listing", ["business_id", "report_id", "generated_at"], unique=False
        )

    op.create_table(
        "report_deliveries",
        sa.Column("run_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("recipient_id", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("delivered_at", nickland.persistence.types.UtcDateTime(), nullable=False),
        sa.Column("read_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("id", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("business_id", nickland.persistence.types.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_fk"],
            ["report_runs.id"],
            name=op.f("fk_report_deliveries_run_fk_report_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_deliveries")),
        sa.UniqueConstraint(
            "run_fk", "recipient_id", name=op.f("uq_report_deliveries_run_fk_recipient_id")
        ),
    )
    with op.batch_alter_table("report_deliveries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_report_deliveries_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_report_deliveries_recipient_id"), ["recipient_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_report_deliveries_run_fk"), ["run_fk"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("report_deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_report_deliveries_run_fk"))
        batch_op.drop_index(batch_op.f("ix_report_deliveries_recipient_id"))
        batch_op.drop_index(batch_op.f("ix_report_deliveries_business_id"))

    op.drop_table("report_deliveries")
    with op.batch_alter_table("report_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_report_runs_listing")
        batch_op.drop_index(batch_op.f("ix_report_runs_business_id"))

    op.drop_table("report_runs")
    with op.batch_alter_table("period_reopen_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_period_reopen_requests_period_fk"))
        batch_op.drop_index(batch_op.f("ix_period_reopen_requests_business_id"))

    op.drop_table("period_reopen_requests")
    with op.batch_alter_table("period_checklist_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_period_checklist_items_period_fk"))
        batch_op.drop_index(batch_op.f("ix_period_checklist_items_business_id"))

    op.drop_table("period_checklist_items")
    with op.batch_alter_table("report_schedules", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_report_schedules_next_run_at"))
        batch_op.drop_index(batch_op.f("ix_report_schedules_business_id"))

    op.drop_table("report_schedules")
