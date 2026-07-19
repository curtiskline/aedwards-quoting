"""Add deleted_at column to quote for soft delete.

Revision ID: 20260719_0002
Revises: 20260719_0001
Create Date: 2026-07-19 21:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260719_0002"
down_revision = "20260719_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quote", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index(op.f("ix_quote_deleted_at"), "quote", ["deleted_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_quote_deleted_at"), table_name="quote")
    op.drop_column("quote", "deleted_at")
