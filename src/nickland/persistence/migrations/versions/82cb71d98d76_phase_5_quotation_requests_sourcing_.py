"""Phase 5: quotation requests, supplier sourcing, quotations and pricing

Eighteen tables. The configuration three — margin policies, discount limits and
instalment plans with their duration bands — carry the parameters the engine reads.
The sourcing five record what was asked of whom and what came back. The quotation ten
hold the offer: its lines, where each line is sourced from, the allocable expenses and
each expense's share of each line, the costing snapshot taken at issue, the instalment
schedule, and the affiliate accruals.

``quotation_snapshots`` has no ``updated_at``, which is deliberate: the session guard
refuses any change to a row in it, so a column recording the last amendment would
describe something that cannot happen.

Revision: 82cb71d98d76
Previous: e422a983cfc3
Created:  2026-11-06 09:12:58.645362+00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

import nickland.persistence.types


revision: str = "82cb71d98d76"
down_revision: str | None = "e422a983cfc3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "discount_limits",
        sa.Column("role_code", sa.String(length=40), nullable=False),
        sa.Column("maximum_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("maximum_amount", nickland.persistence.types.Money(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "maximum_rate >= 0", name=op.f("ck_discount_limits_maximum_rate_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discount_limits")),
        sa.UniqueConstraint(
            "business_id", "role_code", name=op.f("uq_discount_limits_business_id_role_code")
        ),
    )
    with op.batch_alter_table("discount_limits", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_discount_limits_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "instalment_plans",
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("applies_to", sa.String(length=12), nullable=False),
        sa.Column("category_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("product_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("customer_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("deposit_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint(
            "deposit_rate >= 0", name=op.f("ck_instalment_plans_deposit_rate_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["category_fk"],
            ["product_categories.id"],
            name=op.f("fk_instalment_plans_category_fk_product_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_fk"],
            ["parties.id"],
            name=op.f("fk_instalment_plans_customer_fk_parties"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_fk"],
            ["products.id"],
            name=op.f("fk_instalment_plans_product_fk_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instalment_plans")),
        sa.UniqueConstraint(
            "business_id", "code", name=op.f("uq_instalment_plans_business_id_code")
        ),
    )
    with op.batch_alter_table("instalment_plans", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_instalment_plans_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "margin_policies",
        sa.Column("scope", sa.String(length=12), nullable=False),
        sa.Column("category_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("product_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("method", sa.String(length=24), nullable=False),
        sa.Column("rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "(scope = 'business' AND category_fk IS NULL AND product_fk IS NULL) OR (scope = 'category' AND category_fk IS NOT NULL AND product_fk IS NULL) OR (scope = 'product' AND product_fk IS NOT NULL AND category_fk IS NULL)",
            name=op.f("ck_margin_policies_scope_matches_target"),
        ),
        sa.CheckConstraint("rate >= 0", name=op.f("ck_margin_policies_rate_non_negative")),
        sa.ForeignKeyConstraint(
            ["category_fk"],
            ["product_categories.id"],
            name=op.f("fk_margin_policies_category_fk_product_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_fk"],
            ["products.id"],
            name=op.f("fk_margin_policies_product_fk_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_margin_policies")),
        sa.UniqueConstraint(
            "business_id",
            "scope",
            "category_fk",
            "product_fk",
            name=op.f("uq_margin_policies_business_id_scope_category_fk_product_fk"),
        ),
    )
    with op.batch_alter_table("margin_policies", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_margin_policies_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "instalment_bands",
        sa.Column("plan_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("minimum_months", sa.Integer(), nullable=False),
        sa.Column("maximum_months", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("fixed_per_unit", nickland.persistence.types.Money(), nullable=True),
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
        sa.CheckConstraint(
            "maximum_months >= minimum_months", name=op.f("ck_instalment_bands_band_is_ordered")
        ),
        sa.CheckConstraint(
            "minimum_months >= 1", name=op.f("ck_instalment_bands_minimum_months_positive")
        ),
        sa.CheckConstraint("rate >= 0", name=op.f("ck_instalment_bands_rate_non_negative")),
        sa.ForeignKeyConstraint(
            ["plan_fk"],
            ["instalment_plans.id"],
            name=op.f("fk_instalment_bands_plan_fk_instalment_plans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instalment_bands")),
        sa.UniqueConstraint(
            "plan_fk", "minimum_months", name=op.f("uq_instalment_bands_plan_fk_minimum_months")
        ),
    )
    with op.batch_alter_table("instalment_bands", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_instalment_bands_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "quotation_requests",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("customer_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("contact_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("customer_reference", sa.String(length=120), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("required_by", sa.Date(), nullable=True),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("closed_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.Column("branch_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["contact_fk"],
            ["contacts.id"],
            name=op.f("fk_quotation_requests_contact_fk_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_fk"],
            ["parties.id"],
            name=op.f("fk_quotation_requests_customer_fk_parties"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_requests")),
        sa.UniqueConstraint(
            "business_id", "number", name=op.f("uq_quotation_requests_business_id_number")
        ),
    )
    with op.batch_alter_table("quotation_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quotation_requests_branch_id"), ["branch_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_quotation_requests_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_quotation_requests_customer", ["business_id", "customer_fk"], unique=False
        )
        batch_op.create_index("ix_quotation_requests_state", ["business_id", "state"], unique=False)

    op.create_table(
        "quotation_request_lines",
        sa.Column("request_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("product_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("specification", sa.Text(), nullable=True),
        sa.Column("quantity", nickland.persistence.types.Quantity(), nullable=False),
        sa.Column("unit_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("required_by", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_quotation_request_lines_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["product_fk"],
            ["products.id"],
            name=op.f("fk_quotation_request_lines_product_fk_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_fk"],
            ["quotation_requests.id"],
            name=op.f("fk_quotation_request_lines_request_fk_quotation_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unit_fk"],
            ["units_of_measure.id"],
            name=op.f("fk_quotation_request_lines_unit_fk_units_of_measure"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_request_lines")),
        sa.UniqueConstraint(
            "request_fk", "position", name=op.f("uq_quotation_request_lines_request_fk_position")
        ),
    )
    with op.batch_alter_table("quotation_request_lines", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quotation_request_lines_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "quotations",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("request_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("customer_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("contact_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("customer_reference", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("quoted_on", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("precision", sa.Integer(), nullable=False),
        sa.Column("document_discount_method", sa.String(length=12), nullable=False),
        sa.Column("document_discount_value", nickland.persistence.types.Money(), nullable=False),
        sa.Column("affiliate_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("commission_scope", sa.String(length=24), nullable=True),
        sa.Column("payment_plan", sa.String(length=12), nullable=False),
        sa.Column("instalment_plan_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("instalment_months", sa.Integer(), nullable=True),
        sa.Column("instalment_frequency", sa.String(length=16), nullable=True),
        sa.Column("deposit_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("first_instalment_on", sa.Date(), nullable=True),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("landed_cost_total", nickland.persistence.types.Money(), nullable=False),
        sa.Column("margin_total", nickland.persistence.types.Money(), nullable=False),
        sa.Column("commission_total", nickland.persistence.types.Money(), nullable=False),
        sa.Column("list_total", nickland.persistence.types.Money(), nullable=False),
        sa.Column("discount_total", nickland.persistence.types.Money(), nullable=False),
        sa.Column("net_total", nickland.persistence.types.Money(), nullable=False),
        sa.Column("finance_charge_total", nickland.persistence.types.Money(), nullable=False),
        sa.Column("payable_before_tax", nickland.persistence.types.Money(), nullable=False),
        sa.Column("tax_total", nickland.persistence.types.Money(), nullable=False),
        sa.Column("total_payable", nickland.persistence.types.Money(), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("approved_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("approved_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("issued_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("issued_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("accepted_on", sa.Date(), nullable=True),
        sa.Column("decided_on", sa.Date(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
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
        sa.Column("branch_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.CheckConstraint("exchange_rate > 0", name=op.f("ck_quotations_exchange_rate_positive")),
        sa.CheckConstraint(
            "precision >= 0 AND precision <= 6", name=op.f("ck_quotations_precision_in_range")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_quotations_revision_positive")),
        sa.ForeignKeyConstraint(
            ["affiliate_fk"],
            ["parties.id"],
            name=op.f("fk_quotations_affiliate_fk_parties"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["contact_fk"],
            ["contacts.id"],
            name=op.f("fk_quotations_contact_fk_contacts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_fk"],
            ["parties.id"],
            name=op.f("fk_quotations_customer_fk_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instalment_plan_fk"],
            ["instalment_plans.id"],
            name=op.f("fk_quotations_instalment_plan_fk_instalment_plans"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["request_fk"],
            ["quotation_requests.id"],
            name=op.f("fk_quotations_request_fk_quotation_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_fk"],
            ["quotations.id"],
            name=op.f("fk_quotations_supersedes_fk_quotations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotations")),
        sa.UniqueConstraint(
            "business_id",
            "number",
            "revision",
            name=op.f("uq_quotations_business_id_number_revision"),
        ),
    )
    with op.batch_alter_table("quotations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_quotations_branch_id"), ["branch_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_quotations_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_quotations_customer", ["business_id", "customer_fk"], unique=False
        )
        batch_op.create_index("ix_quotations_request", ["business_id", "request_fk"], unique=False)
        batch_op.create_index("ix_quotations_state", ["business_id", "state"], unique=False)

    op.create_table(
        "supplier_requests",
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("supplier_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("request_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("respond_by", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
            ["request_fk"],
            ["quotation_requests.id"],
            name=op.f("fk_supplier_requests_request_fk_quotation_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_fk"],
            ["parties.id"],
            name=op.f("fk_supplier_requests_supplier_fk_parties"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_requests")),
        sa.UniqueConstraint(
            "business_id", "number", name=op.f("uq_supplier_requests_business_id_number")
        ),
    )
    with op.batch_alter_table("supplier_requests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_supplier_requests_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_supplier_requests_request", ["business_id", "request_fk"], unique=False
        )
        batch_op.create_index(
            "ix_supplier_requests_supplier", ["business_id", "supplier_fk"], unique=False
        )

    op.create_table(
        "quotation_instalments",
        sa.Column("quotation_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("paid_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("waiver_reason", sa.Text(), nullable=True),
        sa.Column("is_deposit", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint(
            "amount >= 0", name=op.f("ck_quotation_instalments_amount_non_negative")
        ),
        sa.CheckConstraint(
            "sequence >= 0", name=op.f("ck_quotation_instalments_sequence_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["quotation_fk"],
            ["quotations.id"],
            name=op.f("fk_quotation_instalments_quotation_fk_quotations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_instalments")),
        sa.UniqueConstraint(
            "quotation_fk",
            "schedule_version",
            "sequence",
            name=op.f("uq_quotation_instalments_quotation_fk_schedule_version_sequence"),
        ),
    )
    with op.batch_alter_table("quotation_instalments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quotation_instalments_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_quotation_instalments_due", ["business_id", "due_on"], unique=False
        )

    op.create_table(
        "quotation_lines",
        sa.Column("quotation_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("request_line_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("product_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("specification", sa.Text(), nullable=True),
        sa.Column("quantity", nickland.persistence.types.Quantity(), nullable=False),
        sa.Column("unit_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("source_kind", sa.String(length=12), nullable=False),
        sa.Column("unit_cost", nickland.persistence.types.UnitPrice(), nullable=False),
        sa.Column("supplier_discount_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("supplier_discount_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("non_recoverable_tax", nickland.persistence.types.Money(), nullable=False),
        sa.Column("recoverable_tax", nickland.persistence.types.Money(), nullable=False),
        sa.Column("line_weight", nickland.persistence.types.Quantity(), nullable=False),
        sa.Column("line_volume", nickland.persistence.types.Quantity(), nullable=False),
        sa.Column("margin_method", sa.String(length=24), nullable=False),
        sa.Column("margin_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("margin_source", sa.String(length=12), nullable=True),
        sa.Column("commission_method", sa.String(length=16), nullable=True),
        sa.Column("commission_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("commission_fixed_per_unit", nickland.persistence.types.Money(), nullable=True),
        sa.Column("commission_selected", sa.Boolean(), nullable=False),
        sa.Column("commission_in_scope", sa.Boolean(), nullable=False),
        sa.Column("commission_reason", sa.String(length=200), nullable=True),
        sa.Column("discount_method", sa.String(length=12), nullable=False),
        sa.Column("discount_value", nickland.persistence.types.Money(), nullable=False),
        sa.Column("uplift_method", sa.String(length=16), nullable=False),
        sa.Column("uplift_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("uplift_fixed_per_unit", nickland.persistence.types.Money(), nullable=True),
        sa.Column("allocated_cost", nickland.persistence.types.Money(), nullable=False),
        sa.Column("landed_cost", nickland.persistence.types.Money(), nullable=False),
        sa.Column("margin_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("commission_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("list_price", nickland.persistence.types.Money(), nullable=False),
        sa.Column("discount_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("net_price", nickland.persistence.types.Money(), nullable=False),
        sa.Column("finance_charge", nickland.persistence.types.Money(), nullable=False),
        sa.Column("payable_before_tax", nickland.persistence.types.Money(), nullable=False),
        sa.Column("tax_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("total_payable", nickland.persistence.types.Money(), nullable=False),
        sa.Column("unit_list_price", nickland.persistence.types.UnitPrice(), nullable=False),
        sa.Column("unit_net_price", nickland.persistence.types.UnitPrice(), nullable=False),
        sa.Column("actual_unit_cost", nickland.persistence.types.UnitPrice(), nullable=True),
        sa.Column("actual_cost_recorded_on", sa.Date(), nullable=True),
        sa.Column("actual_cost_reference", sa.String(length=120), nullable=True),
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
        sa.CheckConstraint(
            "discount_amount >= 0", name=op.f("ck_quotation_lines_discount_non_negative")
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_quotation_lines_quantity_positive")),
        sa.CheckConstraint(
            "unit_cost >= 0", name=op.f("ck_quotation_lines_unit_cost_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["product_fk"],
            ["products.id"],
            name=op.f("fk_quotation_lines_product_fk_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quotation_fk"],
            ["quotations.id"],
            name=op.f("fk_quotation_lines_quotation_fk_quotations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_line_fk"],
            ["quotation_request_lines.id"],
            name=op.f("fk_quotation_lines_request_line_fk_quotation_request_lines"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["unit_fk"],
            ["units_of_measure.id"],
            name=op.f("fk_quotation_lines_unit_fk_units_of_measure"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_lines")),
        sa.UniqueConstraint(
            "quotation_fk", "position", name=op.f("uq_quotation_lines_quotation_fk_position")
        ),
    )
    with op.batch_alter_table("quotation_lines", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quotation_lines_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_quotation_lines_product", ["business_id", "product_fk"], unique=False
        )

    op.create_table(
        "quotation_snapshots",
        sa.Column("quotation_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("taken_at", nickland.persistence.types.UtcDateTime(), nullable=False),
        sa.Column("taken_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("id", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("business_id", nickland.persistence.types.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["quotation_fk"],
            ["quotations.id"],
            name=op.f("fk_quotation_snapshots_quotation_fk_quotations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_snapshots")),
        sa.UniqueConstraint(
            "quotation_fk", "revision", name=op.f("uq_quotation_snapshots_quotation_fk_revision")
        ),
    )
    with op.batch_alter_table("quotation_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            "ix_quotation_snapshots_business", ["business_id", "quotation_fk"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_quotation_snapshots_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "supplier_quotations",
        sa.Column("reference", sa.String(length=60), nullable=False),
        sa.Column("supplier_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("supplier_request_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("request_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("freight_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("other_charges", nickland.persistence.types.Money(), nullable=False),
        sa.Column("payment_terms", sa.String(length=200), nullable=True),
        sa.Column("tax_treatment", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "exchange_rate > 0", name=op.f("ck_supplier_quotations_exchange_rate_positive")
        ),
        sa.CheckConstraint(
            "freight_amount >= 0", name=op.f("ck_supplier_quotations_freight_non_negative")
        ),
        sa.CheckConstraint(
            "other_charges >= 0", name=op.f("ck_supplier_quotations_other_charges_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["request_fk"],
            ["quotation_requests.id"],
            name=op.f("fk_supplier_quotations_request_fk_quotation_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_fk"],
            ["parties.id"],
            name=op.f("fk_supplier_quotations_supplier_fk_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_request_fk"],
            ["supplier_requests.id"],
            name=op.f("fk_supplier_quotations_supplier_request_fk_supplier_requests"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_quotations")),
        sa.UniqueConstraint(
            "business_id",
            "supplier_fk",
            "reference",
            name=op.f("uq_supplier_quotations_business_id_supplier_fk_reference"),
        ),
    )
    with op.batch_alter_table("supplier_quotations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_supplier_quotations_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_supplier_quotations_request", ["business_id", "request_fk"], unique=False
        )

    op.create_table(
        "supplier_request_lines",
        sa.Column("supplier_request_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("request_line_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("product_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("specification", sa.Text(), nullable=True),
        sa.Column("quantity", nickland.persistence.types.Quantity(), nullable=False),
        sa.Column("unit_fk", nickland.persistence.types.Uuid(), nullable=True),
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
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_supplier_request_lines_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["product_fk"],
            ["products.id"],
            name=op.f("fk_supplier_request_lines_product_fk_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_line_fk"],
            ["quotation_request_lines.id"],
            name=op.f("fk_supplier_request_lines_request_line_fk_quotation_request_lines"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_request_fk"],
            ["supplier_requests.id"],
            name=op.f("fk_supplier_request_lines_supplier_request_fk_supplier_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unit_fk"],
            ["units_of_measure.id"],
            name=op.f("fk_supplier_request_lines_unit_fk_units_of_measure"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_request_lines")),
        sa.UniqueConstraint(
            "supplier_request_fk",
            "position",
            name=op.f("uq_supplier_request_lines_supplier_request_fk_position"),
        ),
    )
    with op.batch_alter_table("supplier_request_lines", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_supplier_request_lines_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "commission_accruals",
        sa.Column("affiliate_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("affiliate_profile_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("quotation_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("quotation_line_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("source_reference", sa.String(length=60), nullable=True),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("accrued_on", sa.Date(), nullable=False),
        sa.Column("basis_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("accrued_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("fulfilled_fraction", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("paid_fraction", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("entitlement", nickland.persistence.types.Money(), nullable=False),
        sa.Column("settled_amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("approved_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("approved_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("paid_at", nickland.persistence.types.UtcDateTime(), nullable=True),
        sa.Column("payment_reference", sa.String(length=120), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("prior_rate", nickland.persistence.types.Rate(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("overridden_by_id", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("overridden_at", nickland.persistence.types.UtcDateTime(), nullable=True),
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
        sa.CheckConstraint(
            "accrued_amount >= 0", name=op.f("ck_commission_accruals_accrued_non_negative")
        ),
        sa.CheckConstraint(
            "entitlement >= 0", name=op.f("ck_commission_accruals_entitlement_non_negative")
        ),
        sa.CheckConstraint(
            "settled_amount >= 0", name=op.f("ck_commission_accruals_settled_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["affiliate_fk"],
            ["parties.id"],
            name=op.f("fk_commission_accruals_affiliate_fk_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["affiliate_profile_fk"],
            ["affiliate_profiles.id"],
            name=op.f("fk_commission_accruals_affiliate_profile_fk_affiliate_profiles"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["quotation_fk"],
            ["quotations.id"],
            name=op.f("fk_commission_accruals_quotation_fk_quotations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["quotation_line_fk"],
            ["quotation_lines.id"],
            name=op.f("fk_commission_accruals_quotation_line_fk_quotation_lines"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commission_accruals")),
        sa.UniqueConstraint(
            "quotation_line_fk",
            "affiliate_fk",
            name=op.f("uq_commission_accruals_quotation_line_fk_affiliate_fk"),
        ),
    )
    with op.batch_alter_table("commission_accruals", schema=None) as batch_op:
        batch_op.create_index(
            "ix_commission_accruals_affiliate",
            ["business_id", "affiliate_fk", "state"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_commission_accruals_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_commission_accruals_state", ["business_id", "state"], unique=False
        )

    op.create_table(
        "quotation_expenses",
        sa.Column("quotation_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("reference", sa.String(length=24), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("nominated_line_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("excluded_line_ids", sa.JSON(), nullable=False),
        sa.Column("manual_amounts", sa.JSON(), nullable=False),
        sa.Column("supplier_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("is_disclosed", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("amount >= 0", name=op.f("ck_quotation_expenses_amount_non_negative")),
        sa.ForeignKeyConstraint(
            ["nominated_line_fk"],
            ["quotation_lines.id"],
            name=op.f("fk_quotation_expenses_nominated_line_fk_quotation_lines"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["quotation_fk"],
            ["quotations.id"],
            name=op.f("fk_quotation_expenses_quotation_fk_quotations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_fk"],
            ["parties.id"],
            name=op.f("fk_quotation_expenses_supplier_fk_parties"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_expenses")),
        sa.UniqueConstraint(
            "quotation_fk", "reference", name=op.f("uq_quotation_expenses_quotation_fk_reference")
        ),
    )
    with op.batch_alter_table("quotation_expenses", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quotation_expenses_business_id"), ["business_id"], unique=False
        )

    op.create_table(
        "supplier_quotation_lines",
        sa.Column("supplier_quotation_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("request_line_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("product_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("quantity_offered", nickland.persistence.types.Quantity(), nullable=False),
        sa.Column("unit_cost", nickland.persistence.types.UnitPrice(), nullable=False),
        sa.Column("discount_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("recoverable_tax_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("non_recoverable_tax_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("minimum_order_quantity", nickland.persistence.types.Quantity(), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("availability_note", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "discount_rate >= 0",
            name=op.f("ck_supplier_quotation_lines_discount_rate_non_negative"),
        ),
        sa.CheckConstraint(
            "quantity_offered > 0", name=op.f("ck_supplier_quotation_lines_quantity_positive")
        ),
        sa.CheckConstraint(
            "unit_cost >= 0", name=op.f("ck_supplier_quotation_lines_unit_cost_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["product_fk"],
            ["products.id"],
            name=op.f("fk_supplier_quotation_lines_product_fk_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_line_fk"],
            ["quotation_request_lines.id"],
            name=op.f("fk_supplier_quotation_lines_request_line_fk_quotation_request_lines"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_quotation_fk"],
            ["supplier_quotations.id"],
            name=op.f("fk_supplier_quotation_lines_supplier_quotation_fk_supplier_quotations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_quotation_lines")),
        sa.UniqueConstraint(
            "supplier_quotation_fk",
            "position",
            name=op.f("uq_supplier_quotation_lines_supplier_quotation_fk_position"),
        ),
    )
    with op.batch_alter_table("supplier_quotation_lines", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_supplier_quotation_lines_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_supplier_quotation_lines_request_line",
            ["business_id", "request_line_fk"],
            unique=False,
        )

    op.create_table(
        "quotation_line_costs",
        sa.Column("quotation_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("quotation_line_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("expense_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("amount", nickland.persistence.types.Money(), nullable=False),
        sa.Column("id", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("business_id", nickland.persistence.types.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["expense_fk"],
            ["quotation_expenses.id"],
            name=op.f("fk_quotation_line_costs_expense_fk_quotation_expenses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quotation_fk"],
            ["quotations.id"],
            name=op.f("fk_quotation_line_costs_quotation_fk_quotations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quotation_line_fk"],
            ["quotation_lines.id"],
            name=op.f("fk_quotation_line_costs_quotation_line_fk_quotation_lines"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_line_costs")),
        sa.UniqueConstraint(
            "quotation_line_fk",
            "expense_fk",
            name=op.f("uq_quotation_line_costs_quotation_line_fk_expense_fk"),
        ),
    )
    with op.batch_alter_table("quotation_line_costs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quotation_line_costs_business_id"), ["business_id"], unique=False
        )
        batch_op.create_index(
            "ix_quotation_line_costs_quotation", ["business_id", "quotation_fk"], unique=False
        )

    op.create_table(
        "quotation_line_sources",
        sa.Column("quotation_line_fk", nickland.persistence.types.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("supplier_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("supplier_quotation_line_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("warehouse_fk", nickland.persistence.types.Uuid(), nullable=True),
        sa.Column("quantity", nickland.persistence.types.Quantity(), nullable=False),
        sa.Column("unit_cost", nickland.persistence.types.UnitPrice(), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", nickland.persistence.types.Rate(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("selection_reason", sa.String(length=200), nullable=True),
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
        sa.CheckConstraint(
            "(kind = 'stock' AND supplier_fk IS NULL) OR (kind = 'supplier' AND supplier_fk IS NOT NULL)",
            name=op.f("ck_quotation_line_sources_source_names_its_origin"),
        ),
        sa.CheckConstraint(
            "exchange_rate > 0", name=op.f("ck_quotation_line_sources_exchange_rate_positive")
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_quotation_line_sources_quantity_positive")
        ),
        sa.CheckConstraint(
            "unit_cost >= 0", name=op.f("ck_quotation_line_sources_unit_cost_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["quotation_line_fk"],
            ["quotation_lines.id"],
            name=op.f("fk_quotation_line_sources_quotation_line_fk_quotation_lines"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_fk"],
            ["parties.id"],
            name=op.f("fk_quotation_line_sources_supplier_fk_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_quotation_line_fk"],
            ["supplier_quotation_lines.id"],
            name=op.f(
                "fk_quotation_line_sources_supplier_quotation_line_fk_supplier_quotation_lines"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_fk"],
            ["warehouses.id"],
            name=op.f("fk_quotation_line_sources_warehouse_fk_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_line_sources")),
        sa.UniqueConstraint(
            "quotation_line_fk",
            "position",
            name=op.f("uq_quotation_line_sources_quotation_line_fk_position"),
        ),
    )
    with op.batch_alter_table("quotation_line_sources", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quotation_line_sources_business_id"), ["business_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("quotation_line_sources", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quotation_line_sources_business_id"))

    op.drop_table("quotation_line_sources")
    with op.batch_alter_table("quotation_line_costs", schema=None) as batch_op:
        batch_op.drop_index("ix_quotation_line_costs_quotation")
        batch_op.drop_index(batch_op.f("ix_quotation_line_costs_business_id"))

    op.drop_table("quotation_line_costs")
    with op.batch_alter_table("supplier_quotation_lines", schema=None) as batch_op:
        batch_op.drop_index("ix_supplier_quotation_lines_request_line")
        batch_op.drop_index(batch_op.f("ix_supplier_quotation_lines_business_id"))

    op.drop_table("supplier_quotation_lines")
    with op.batch_alter_table("quotation_expenses", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quotation_expenses_business_id"))

    op.drop_table("quotation_expenses")
    with op.batch_alter_table("commission_accruals", schema=None) as batch_op:
        batch_op.drop_index("ix_commission_accruals_state")
        batch_op.drop_index(batch_op.f("ix_commission_accruals_business_id"))
        batch_op.drop_index("ix_commission_accruals_affiliate")

    op.drop_table("commission_accruals")
    with op.batch_alter_table("supplier_request_lines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_supplier_request_lines_business_id"))

    op.drop_table("supplier_request_lines")
    with op.batch_alter_table("supplier_quotations", schema=None) as batch_op:
        batch_op.drop_index("ix_supplier_quotations_request")
        batch_op.drop_index(batch_op.f("ix_supplier_quotations_business_id"))

    op.drop_table("supplier_quotations")
    with op.batch_alter_table("quotation_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quotation_snapshots_business_id"))
        batch_op.drop_index("ix_quotation_snapshots_business")

    op.drop_table("quotation_snapshots")
    with op.batch_alter_table("quotation_lines", schema=None) as batch_op:
        batch_op.drop_index("ix_quotation_lines_product")
        batch_op.drop_index(batch_op.f("ix_quotation_lines_business_id"))

    op.drop_table("quotation_lines")
    with op.batch_alter_table("quotation_instalments", schema=None) as batch_op:
        batch_op.drop_index("ix_quotation_instalments_due")
        batch_op.drop_index(batch_op.f("ix_quotation_instalments_business_id"))

    op.drop_table("quotation_instalments")
    with op.batch_alter_table("supplier_requests", schema=None) as batch_op:
        batch_op.drop_index("ix_supplier_requests_supplier")
        batch_op.drop_index("ix_supplier_requests_request")
        batch_op.drop_index(batch_op.f("ix_supplier_requests_business_id"))

    op.drop_table("supplier_requests")
    with op.batch_alter_table("quotations", schema=None) as batch_op:
        batch_op.drop_index("ix_quotations_state")
        batch_op.drop_index("ix_quotations_request")
        batch_op.drop_index("ix_quotations_customer")
        batch_op.drop_index(batch_op.f("ix_quotations_business_id"))
        batch_op.drop_index(batch_op.f("ix_quotations_branch_id"))

    op.drop_table("quotations")
    with op.batch_alter_table("quotation_request_lines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quotation_request_lines_business_id"))

    op.drop_table("quotation_request_lines")
    with op.batch_alter_table("quotation_requests", schema=None) as batch_op:
        batch_op.drop_index("ix_quotation_requests_state")
        batch_op.drop_index("ix_quotation_requests_customer")
        batch_op.drop_index(batch_op.f("ix_quotation_requests_business_id"))
        batch_op.drop_index(batch_op.f("ix_quotation_requests_branch_id"))

    op.drop_table("quotation_requests")
    with op.batch_alter_table("instalment_bands", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_instalment_bands_business_id"))

    op.drop_table("instalment_bands")
    with op.batch_alter_table("margin_policies", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_margin_policies_business_id"))

    op.drop_table("margin_policies")
    with op.batch_alter_table("instalment_plans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_instalment_plans_business_id"))

    op.drop_table("instalment_plans")
    with op.batch_alter_table("discount_limits", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_discount_limits_business_id"))

    op.drop_table("discount_limits")
