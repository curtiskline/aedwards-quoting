"""Retain immutable send-time quote artifacts and line-item snapshots.

Revision ID: 20260805_0002
Revises: 20260805_0001
Create Date: 2026-08-05 22:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260805_0002"
down_revision = "20260805_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quote_version") as batch_op:
        # Existing rows predate durable artifacts.  Make that absence explicit
        # instead of pretending their current line items are historical data.
        batch_op.add_column(
            sa.Column("artifact_status", sa.String(), nullable=False, server_default="missing")
        )
        batch_op.add_column(sa.Column("line_items_snapshot", sa.JSON(), nullable=True))

    op.execute(
        "UPDATE quote_version "
        "SET pdf_path = 'artifact-missing://legacy-unretained' "
        "WHERE artifact_status = 'missing'"
    )


def downgrade() -> None:
    with op.batch_alter_table("quote_version") as batch_op:
        batch_op.drop_column("line_items_snapshot")
        batch_op.drop_column("artifact_status")
