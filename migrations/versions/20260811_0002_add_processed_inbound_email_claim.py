"""Add durable inbox-message idempotency claims.

The monitor can make several quotes from one source email, so a unique
constraint on quote.source_email_id would reject valid multi-RFQ messages.
Instead, this table records one unique claim per source email before monitor
side effects occur. Existing quote source IDs are backfilled to prevent a
state-file loss from replaying historical messages.

Revision ID: 20260811_0002
Revises: 20260811_0001
Create Date: 2026-08-11 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260811_0002"
down_revision = "20260811_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processed_inbound_email",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_email_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_processed_inbound_email_source_email_id"),
        "processed_inbound_email",
        ["source_email_id"],
        unique=True,
    )
    op.execute(
        """
        INSERT INTO processed_inbound_email (source_email_id, created_at)
        SELECT source_email_id, MIN(created_at)
        FROM quote
        WHERE source_email_id IS NOT NULL
        GROUP BY source_email_id
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_processed_inbound_email_source_email_id"), table_name="processed_inbound_email")
    op.drop_table("processed_inbound_email")
