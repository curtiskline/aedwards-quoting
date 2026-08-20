"""Add send_hold + trust_ramp_config (CP-2b: Tier-1 assisted send).

send_hold: admin-set per-customer / per-product-type holds that force a quote
NOT-recommended regardless of confidence score (exactly one target per row).
trust_ramp_config: single-row global tier setting, default Tier 1 (assisted,
recommend-only). CP-2b displays both; CP-2c's auto-send gates on them.

Revision ID: 20260820_0002
Revises: 20260820_0001
Create Date: 2026-08-20 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260820_0002"
down_revision = "20260820_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "send_hold",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("product_type", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(customer_id IS NULL) != (product_type IS NULL)",
            name="ck_send_hold_exactly_one_target",
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_send_hold_customer_id"), "send_hold", ["customer_id"], unique=True
    )
    op.create_index(
        op.f("ix_send_hold_product_type"), "send_hold", ["product_type"], unique=True
    )

    op.create_table(
        "trust_ramp_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("active_tier", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("trust_ramp_config")
    op.drop_index(op.f("ix_send_hold_product_type"), table_name="send_hold")
    op.drop_index(op.f("ix_send_hold_customer_id"), table_name="send_hold")
    op.drop_table("send_hold")
