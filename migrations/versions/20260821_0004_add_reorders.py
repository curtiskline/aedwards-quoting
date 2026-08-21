"""Add auto-reorders: reorder table + shop_ping reorder channel (CP-5b).

Stage F's completion (task 419). reorder is created when a ledger write drops
a fully seeded item's on_hand to/below min_qty. Idempotency is the claim
pattern (§12.1): a partial UNIQUE index on stock_item_id WHERE status='OPEN'
allows at most one open reorder per stock item — a second trigger while one
is open is a no-op. qty + the *_at_trigger columns are frozen at creation so
the printable sheet cannot drift.

shop_ping (CP-4's channel) gains a nullable reorder_id, pick_list_id becomes
nullable, and a CHECK enforces exactly one subject per ping — the reorder
ping reuses the real channel instead of inventing a parallel one.

Like quote_status (see 20260406_0001), SAEnum columns persist the enum
member NAMES ("OPEN"), so the native PostgreSQL enum type is defined with
the names (I139).

Revision ID: 20260821_0004
Revises: 20260821_0003
Create Date: 2026-08-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_0004"
down_revision = "20260821_0003"
branch_labels = None
depends_on = None

reorder_status = sa.Enum("OPEN", "RECEIVED", name="reorder_status")


def upgrade() -> None:
    # No explicit .create() call: on PostgreSQL, op.create_table auto-creates
    # the native enum type (an explicit create first would double-CREATE).
    op.create_table(
        "reorder",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stock_item_id", sa.Integer(), nullable=False),
        sa.Column("status", reorder_status, nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("on_hand_at_trigger", sa.Integer(), nullable=False),
        sa.Column("min_qty_at_trigger", sa.Integer(), nullable=False),
        sa.Column("max_qty_at_trigger", sa.Integer(), nullable=False),
        sa.Column("trigger_movement_id", sa.Integer(), nullable=True),
        sa.Column("reorder_movement_id", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("received_by", sa.Integer(), nullable=True),
        sa.Column("receipt_movement_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["stock_item_id"], ["stock_item.id"]),
        sa.ForeignKeyConstraint(["trigger_movement_id"], ["stock_movement.id"]),
        sa.ForeignKeyConstraint(["reorder_movement_id"], ["stock_movement.id"]),
        sa.ForeignKeyConstraint(["received_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["receipt_movement_id"], ["stock_movement.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_reorder_stock_item_id"), "reorder", ["stock_item_id"], unique=False
    )
    op.create_index(op.f("ix_reorder_status"), "reorder", ["status"], unique=False)
    # THE idempotency claim: at most one OPEN reorder per stock item.
    op.create_index(
        "uq_reorder_open_per_item",
        "reorder",
        ["stock_item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
        sqlite_where=sa.text("status = 'OPEN'"),
    )

    # shop_ping: a ping is now about a pick list OR a reorder, exactly one.
    with op.batch_alter_table("shop_ping") as batch:
        batch.alter_column("pick_list_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("reorder_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_shop_ping_reorder_id", "reorder", ["reorder_id"], ["id"]
        )
        batch.create_check_constraint(
            "ck_shop_ping_exactly_one_subject",
            "(pick_list_id IS NULL) <> (reorder_id IS NULL)",
        )
    op.create_index(
        op.f("ix_shop_ping_reorder_id"), "shop_ping", ["reorder_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_shop_ping_reorder_id"), table_name="shop_ping")
    with op.batch_alter_table("shop_ping") as batch:
        batch.drop_constraint("ck_shop_ping_exactly_one_subject", type_="check")
        batch.drop_constraint("fk_shop_ping_reorder_id", type_="foreignkey")
        batch.drop_column("reorder_id")
        batch.alter_column("pick_list_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("uq_reorder_open_per_item", table_name="reorder")
    op.drop_index(op.f("ix_reorder_status"), table_name="reorder")
    op.drop_index(op.f("ix_reorder_stock_item_id"), table_name="reorder")
    op.drop_table("reorder")
    reorder_status.drop(op.get_bind(), checkfirst=True)
