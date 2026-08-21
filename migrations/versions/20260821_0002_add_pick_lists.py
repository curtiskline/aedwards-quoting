"""Add fulfillment: pick_list + pick_list_audit_log + shop_ping (CP-4).

Stage E of the engine (task 415): generating a pick list from an Order.
pick_list.order_id is UNIQUE — one pick list per order, ever (the
claim-in-transaction idempotency guard, hazard §12.1; there is no
regenerate path — a botched order is handled at the order level).
lines_snapshot holds the pick lines materialized at creation from the
frozen QuoteVersion.line_items_snapshot, pack-unit math included, so a
printed sheet can never drift under later quote/catalog edits.

Like quote_status (see 20260406_0001), the app's SAEnum columns persist the
enum member NAMES ("QUEUED", "MANUAL_PRINT"), not the values, so the native
PostgreSQL enum types are defined with the names (I139).

Revision ID: 20260821_0002
Revises: 20260821_0001
Create Date: 2026-08-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_0002"
down_revision = "20260821_0001"
branch_labels = None
depends_on = None

pick_list_status = sa.Enum(
    "QUEUED",
    "PICKED",
    "LOADED",
    "SHIPPED",
    name="pick_list_status",
)

shop_ping_channel = sa.Enum(
    "MANUAL_PRINT",
    "EMAIL",
    "SMS",
    "SCREEN",
    name="shop_ping_channel",
)


def upgrade() -> None:
    # No explicit .create() calls: on PostgreSQL, op.create_table auto-creates
    # the native enum types (an explicit create first would double-CREATE).
    op.create_table(
        "pick_list",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("status", pick_list_status, nullable=False),
        sa.Column("lines_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("picked_at", sa.DateTime(), nullable=True),
        sa.Column("picked_by", sa.Integer(), nullable=True),
        sa.Column("loaded_at", sa.DateTime(), nullable=True),
        sa.Column("loaded_by", sa.Integer(), nullable=True),
        sa.Column("shipped_at", sa.DateTime(), nullable=True),
        sa.Column("shipped_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["customer_order.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["picked_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["loaded_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["shipped_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pick_list_order_id"), "pick_list", ["order_id"], unique=True
    )

    op.create_table(
        "pick_list_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pick_list_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pick_list_id"], ["pick_list.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pick_list_audit_log_pick_list_id"),
        "pick_list_audit_log",
        ["pick_list_id"],
        unique=False,
    )

    op.create_table(
        "shop_ping",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pick_list_id", sa.Integer(), nullable=False),
        sa.Column("channel", shop_ping_channel, nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pick_list_id"], ["pick_list.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_shop_ping_pick_list_id"), "shop_ping", ["pick_list_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_shop_ping_pick_list_id"), table_name="shop_ping")
    op.drop_table("shop_ping")
    op.drop_index(
        op.f("ix_pick_list_audit_log_pick_list_id"), table_name="pick_list_audit_log"
    )
    op.drop_table("pick_list_audit_log")
    op.drop_index(op.f("ix_pick_list_order_id"), table_name="pick_list")
    op.drop_table("pick_list")
    shop_ping_channel.drop(op.get_bind(), checkfirst=True)
    pick_list_status.drop(op.get_bind(), checkfirst=True)
