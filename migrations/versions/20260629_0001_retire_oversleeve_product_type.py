"""Retire oversleeve from configurable product types.

Revision ID: 20260629_0001
Revises: 20260512_0002
Create Date: 2026-06-29 12:00:00
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "20260629_0001"
down_revision = "20260512_0002"
branch_labels = None
depends_on = None


product_type = sa.table(
    "product_type",
    sa.column("name", sa.String()),
    sa.column("display_label", sa.String()),
    sa.column("sort_order", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime()),
)


def upgrade() -> None:
    op.execute(sa.text("UPDATE product_type SET is_active = 0 WHERE name = 'oversleeve'"))
    op.execute(sa.text("DELETE FROM product_type WHERE name = 'oversleeve'"))


def downgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(sa.text("SELECT 1 FROM product_type WHERE name = 'oversleeve'")).scalar()
    if existing:
        return

    max_sort = bind.execute(sa.text("SELECT COALESCE(MAX(sort_order), 0) FROM product_type")).scalar() or 0
    op.bulk_insert(
        product_type,
        [
            {
                "name": "oversleeve",
                "display_label": "Oversleeve",
                "sort_order": int(max_sort) + 1,
                "is_active": True,
                "created_at": datetime.utcnow(),
            }
        ],
    )
