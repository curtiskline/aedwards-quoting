"""Add product_type table and seed default line-item types.

Revision ID: 20260410_0002
Revises: 20260410_0001
Create Date: 2026-04-10 17:10:00
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "20260410_0002"
down_revision = "20260410_0001"
branch_labels = None
depends_on = None


product_type = sa.table(
    "product_type",
    sa.column("id", sa.Integer()),
    sa.column("name", sa.String()),
    sa.column("display_label", sa.String()),
    sa.column("sort_order", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime()),
)


def upgrade() -> None:
    op.create_table(
        "product_type",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("display_label", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_product_type_name", "product_type", ["name"])
    op.create_index("ix_product_type_sort_order", "product_type", ["sort_order"])
    op.create_index("ix_product_type_is_active", "product_type", ["is_active"])

    defaults = [
        ("sleeve", "Sleeve"),
        ("bag", "Bag"),
        ("girth_weld", "Girth Weld"),
        ("compression", "Compression"),
        ("oversleeve", "Oversleeve"),
        ("accessory", "Accessory"),
        ("service", "Service"),
        ("shipping", "Shipping & Handling"),
    ]
    op.bulk_insert(
        product_type,
        [
            {
                "id": idx,
                "name": name,
                "display_label": label,
                "sort_order": idx,
                "is_active": True,
                "created_at": datetime.utcnow(),
            }
            for idx, (name, label) in enumerate(defaults, start=1)
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_product_type_is_active", table_name="product_type")
    op.drop_index("ix_product_type_sort_order", table_name="product_type")
    op.drop_index("ix_product_type_name", table_name="product_type")
    op.drop_table("product_type")
