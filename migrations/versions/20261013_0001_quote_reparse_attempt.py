"""Append-only record of operator re-parse attempts (T495).

Each row stores the exact parser input used (original or operator-corrected),
and the interpretation or error that resulted. The original quote_source_snapshot
row is never modified.

Revision ID: 20261013_0001
Revises: 20261012_0002
Create Date: 2026-10-13 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261013_0001"
down_revision = "20261012_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_reparse_attempt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("input_edited", sa.Boolean(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("parsed_rfq_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_quote_reparse_attempt_quote_id"),
        "quote_reparse_attempt",
        ["quote_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_quote_reparse_attempt_quote_id"),
        table_name="quote_reparse_attempt",
    )
    op.drop_table("quote_reparse_attempt")
