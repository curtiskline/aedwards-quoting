"""Add shipping_config table for automated quote shipping calculations.

Revision ID: 20260410_0001
Revises: 20260409_0001
Create Date: 2026-04-10 15:30:00
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "20260410_0001"
down_revision = "20260409_0001"
branch_labels = None
depends_on = None


shipping_config = sa.table(
    "shipping_config",
    sa.column("id", sa.Integer()),
    sa.column("default_rate_per_lb_mile", sa.Numeric(12, 6)),
    sa.column("default_length_ft", sa.Numeric(8, 2)),
    sa.column("origin_zip_codes_json", sa.JSON()),
    sa.column("rate_overrides_json", sa.JSON()),
    sa.column("updated_at", sa.DateTime()),
)


def upgrade() -> None:
    op.create_table(
        "shipping_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("default_rate_per_lb_mile", sa.Numeric(12, 6), nullable=False),
        sa.Column("default_length_ft", sa.Numeric(8, 2), nullable=False),
        sa.Column("origin_zip_codes_json", sa.JSON(), nullable=False),
        sa.Column("rate_overrides_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.bulk_insert(
        shipping_config,
        [
            {
                "id": 1,
                "default_rate_per_lb_mile": 0.0006,
                "default_length_ft": 10.0,
                "origin_zip_codes_json": ["74103"],
                "rate_overrides_json": {},
                "updated_at": datetime.utcnow(),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("shipping_config")
