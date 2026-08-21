"""Add inventory: stock_item + stock_movement + stock_decrement_claim (CP-5a).

Stage F's Chip-independent half (task 417). stock_item is keyed 1:1 to
product_catalog.id (UNIQUE catalog_id) — the surrogate rename-safe product
identity. min/max/reorder thresholds are NULLABLE with an explicit
"unseeded" semantic; values arrive with Chip's D68 answer (CP-5b).

stock_movement is the append-only ledger: every on_hand change is a row.
UNMATCHED_SHIPMENT rows carry a shipped pick line that matched no catalog
product (stock_item_id NULL — enforced by check constraint) and sit in
triage until resolved. REORDER is reserved for CP-5b — nothing writes it.

stock_decrement_claim.pick_list_id is UNIQUE: the claim-in-transaction
guard (§12.1) that makes a replayed shipped event apply nothing.

Like quote_status (see 20260406_0001), SAEnum columns persist the enum
member NAMES ("SHIPMENT_DECREMENT"), so the native PostgreSQL enum type is
defined with the names (I139).

Revision ID: 20260821_0003
Revises: 20260821_0002
Create Date: 2026-08-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_0003"
down_revision = "20260821_0002"
branch_labels = None
depends_on = None

stock_movement_type = sa.Enum(
    "SHIPMENT_DECREMENT",
    "RECEIPT",
    "ADJUSTMENT",
    "UNMATCHED_SHIPMENT",
    "REORDER",
    name="stock_movement_type",
)


def upgrade() -> None:
    # No explicit .create() calls: on PostgreSQL, op.create_table auto-creates
    # the native enum types (an explicit create first would double-CREATE).
    op.create_table(
        "stock_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("catalog_id", sa.Integer(), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("min_qty", sa.Integer(), nullable=True),
        sa.Column("max_qty", sa.Integer(), nullable=True),
        sa.Column("reorder_qty", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["catalog_id"], ["product_catalog.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_stock_item_catalog_id"), "stock_item", ["catalog_id"], unique=True
    )

    op.create_table(
        "stock_movement",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stock_item_id", sa.Integer(), nullable=True),
        sa.Column("movement_type", stock_movement_type, nullable=False),
        sa.Column("qty_delta", sa.Integer(), nullable=False),
        sa.Column("resulting_on_hand", sa.Integer(), nullable=True),
        sa.Column("pick_list_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("resolution_movement_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(movement_type = 'UNMATCHED_SHIPMENT') = (stock_item_id IS NULL)",
            name="ck_stock_movement_unmatched_has_no_item",
        ),
        sa.ForeignKeyConstraint(["stock_item_id"], ["stock_item.id"]),
        sa.ForeignKeyConstraint(["pick_list_id"], ["pick_list.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["resolved_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["resolution_movement_id"], ["stock_movement.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_stock_movement_stock_item_id"),
        "stock_movement",
        ["stock_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stock_movement_movement_type"),
        "stock_movement",
        ["movement_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stock_movement_pick_list_id"),
        "stock_movement",
        ["pick_list_id"],
        unique=False,
    )

    op.create_table(
        "stock_decrement_claim",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pick_list_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pick_list_id"], ["pick_list.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_stock_decrement_claim_pick_list_id"),
        "stock_decrement_claim",
        ["pick_list_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_stock_decrement_claim_pick_list_id"),
        table_name="stock_decrement_claim",
    )
    op.drop_table("stock_decrement_claim")
    op.drop_index(op.f("ix_stock_movement_pick_list_id"), table_name="stock_movement")
    op.drop_index(op.f("ix_stock_movement_movement_type"), table_name="stock_movement")
    op.drop_index(op.f("ix_stock_movement_stock_item_id"), table_name="stock_movement")
    op.drop_table("stock_movement")
    op.drop_index(op.f("ix_stock_item_catalog_id"), table_name="stock_item")
    op.drop_table("stock_item")
    stock_movement_type.drop(op.get_bind(), checkfirst=True)
