"""Add quote_attachment table.

Revision ID: 20260719_0001
Revises: 20260708_0001
Create Date: 2026-07-19 08:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260719_0001"
down_revision = "20260708_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_attachment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quote.id"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("is_stored", sa.Boolean(), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_quote_attachment_quote_id"), "quote_attachment", ["quote_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_quote_attachment_quote_id"), table_name="quote_attachment")
    op.drop_table("quote_attachment")
