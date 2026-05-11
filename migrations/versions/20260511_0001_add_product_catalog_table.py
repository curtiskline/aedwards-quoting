"""Add product_catalog table.

Revision ID: 20260511_0001
Revises: 20260410_0002
Create Date: 2026-05-11 15:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260511_0001"
down_revision = "20260410_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_catalog",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "product_family",
            sa.Enum(
                "sleeve",
                "girth_weld",
                "bag",
                "omegawrap",
                "pipe_jack",
                "backing_strip",
                "compression_sleeve",
                "other",
                name="product_family",
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sku"),
    )
    op.create_index("ix_product_catalog_sku", "product_catalog", ["sku"])
    op.create_index("ix_product_catalog_product_family", "product_catalog", ["product_family"])
    op.create_index("ix_product_catalog_is_active", "product_catalog", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_product_catalog_is_active", table_name="product_catalog")
    op.drop_index("ix_product_catalog_product_family", table_name="product_catalog")
    op.drop_index("ix_product_catalog_sku", table_name="product_catalog")
    op.drop_table("product_catalog")
    op.execute("DROP TYPE IF EXISTS product_family")
