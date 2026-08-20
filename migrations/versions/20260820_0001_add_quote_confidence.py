"""Add quote_confidence: per-quote confidence score + component signals (CP-2a).

One row per quote. Six tri-state signal columns ("pass"/"fail"/"unknown"),
the weighted composite score, and a components_json breakdown recording the
per-signal weight/points/reasons. Scoring only — nothing reads this for send
decisions yet (CP-2b shows it; CP-2c gates on it).

Revision ID: 20260820_0001
Revises: 20260813_0001
Create Date: 2026-08-20 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260820_0001"
down_revision = "20260813_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_confidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(4, 3), nullable=False),
        sa.Column("decode_clean", sa.String(), nullable=False),
        sa.Column("all_lines_priced", sa.String(), nullable=False),
        sa.Column("customer_known", sa.String(), nullable=False),
        sa.Column("ship_to_confirmed", sa.String(), nullable=False),
        sa.Column("price_in_tolerance", sa.String(), nullable=False),
        sa.Column("recipient_allowlisted", sa.String(), nullable=False),
        sa.Column("components_json", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_quote_confidence_quote_id"), "quote_confidence", ["quote_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_quote_confidence_quote_id"), table_name="quote_confidence")
    op.drop_table("quote_confidence")
