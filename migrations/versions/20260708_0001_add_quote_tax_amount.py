"""Add tax_amount column to quotes.

Revision ID: 20260708_0001
Revises: 20260630_0001
Create Date: 2026-07-08 15:15:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260708_0001"
down_revision = "20260630_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quote",
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("quote", "tax_amount")
