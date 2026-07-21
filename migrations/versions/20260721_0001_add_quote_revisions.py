"""Add linked quote revisions and a replaced terminal status.

Revision ID: 20260721_0001
Revises: 20260719_0002
Create Date: 2026-07-21 00:01:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260721_0001"
down_revision = "20260719_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE quote_status ADD VALUE IF NOT EXISTS 'replaced'")

    with op.batch_alter_table("quote") as batch_op:
        batch_op.add_column(sa.Column("replaces_quote_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("revision_number", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.create_foreign_key(
            "fk_quote_replaces_quote_id", "quote", ["replaces_quote_id"], ["id"]
        )
        batch_op.create_unique_constraint("uq_quote_replaces_quote_id", ["replaces_quote_id"])

    op.create_index("ix_quote_replaces_quote_id", "quote", ["replaces_quote_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_quote_replaces_quote_id", table_name="quote")
    with op.batch_alter_table("quote") as batch_op:
        batch_op.drop_constraint("uq_quote_replaces_quote_id", type_="unique")
        batch_op.drop_constraint("fk_quote_replaces_quote_id", type_="foreignkey")
        batch_op.drop_column("revision_number")
        batch_op.drop_column("replaces_quote_id")
    # PostgreSQL does not support removing an enum value safely; leave it in place.
