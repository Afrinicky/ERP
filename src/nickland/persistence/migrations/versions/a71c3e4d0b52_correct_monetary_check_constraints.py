"""Correct the commission rate constraint

Monetary columns are held on SQLite as integers scaled by the column's number of
decimal places. Under that representation a comparison against zero means the same
thing on both engines, because zero scales to zero; a comparison against any other
literal does not, because the literal would have to be scaled too.

Phase 2 wrote ``CHECK (rate >= 0 AND rate < 1)`` on the affiliate commission rate.
The lower bound is portable and stays. The upper bound is not: a rate of 0.5 is held
as 500000 on SQLite, which is not below 1, so the constraint would reject every valid
commission rate on a standalone installation. It is removed here and enforced in the
party service, which already refuses a rate outside ``0 <= rate < 1`` and can say why
in words the user can act on.

Revision ID: a71c3e4d0b52
Revises: 6b480ea47b83
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "a71c3e4d0b52"
down_revision = "6b480ea47b83"
branch_labels = None
depends_on = None

TABLE = "affiliate_profiles"


def upgrade() -> None:
    _replace("rate_fraction", "rate_non_negative", "rate >= 0")


def downgrade() -> None:
    _replace("rate_non_negative", "rate_fraction", "rate >= 0 AND rate < 1")


def _replace(drop: str, create: str, expression: str) -> None:
    """Swap one named check for another.

    SQLite cannot alter a constraint in place, so the table is rebuilt and its rows
    copied; PostgreSQL alters the constraint directly.
    """
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.drop_constraint(drop, type_="check")
            batch_op.create_check_constraint(create, expression)
    else:
        op.drop_constraint(drop, TABLE, type_="check")
        op.create_check_constraint(create, TABLE, expression)
