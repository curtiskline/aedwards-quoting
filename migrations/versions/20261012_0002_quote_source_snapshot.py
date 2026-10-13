"""Persist immutable inbound RFQ source and parser interpretation.

Forward-only by design: legacy and manually-created quotes are not backfilled.

Revision ID: 20261012_0002
Revises: 20261012_0001
Create Date: 2026-10-12 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261012_0002"
down_revision = "20261012_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_source_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("original_body", sa.Text(), nullable=False),
        sa.Column("original_body_content_type", sa.String(), nullable=False),
        sa.Column("normalized_body_text", sa.Text(), nullable=False),
        sa.Column("parsed_rfq_json", sa.JSON(), nullable=False),
        sa.Column("parser_schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_quote_source_snapshot_quote_id"),
        "quote_source_snapshot",
        ["quote_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_quote_source_snapshot_quote_id"),
        table_name="quote_source_snapshot",
    )
    op.drop_table("quote_source_snapshot")
