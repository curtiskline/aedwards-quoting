"""Order-triggered vendor PO for never-stock (min/max 0) items (task 444).

Engine v2 §3 / I148.2: a part seeded min = max = 0 is "never stock this" —
its vendor PO fires per customer order, at pick-list generation, with the
customer's details riding along. Additive schema, three pieces:

- reorder.order_id + reorder.customer_context: the customer order the PO
  exists for, and the customer details (name, PO/AFE, ship-to, quote number,
  line notes/specs) frozen at emission. Both NULL on min-triggered rows.
- order_vendor_po_claim: UNIQUE per (pick_list, line_index) — the §12.1
  claim guard so a replayed generate cannot emit a duplicate PO.
- uq_reorder_open_per_item narrows to WHERE ... AND order_id IS NULL: the
  one-active-reorder guard is a MIN-TRIGGERED invariant. Order-triggered POs
  are per-customer-order — two customers ordering the same 0/0 part must
  EACH get one, so they are excluded; their idempotency is the claim table.

Revision ID: 20260827_0002
Revises: 20260827_0001
Create Date: 2026-08-27 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260827_0002"
down_revision = "20260827_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reorder") as batch:
        batch.add_column(sa.Column("order_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("customer_context", sa.JSON(), nullable=True))
        batch.create_foreign_key(
            "fk_reorder_order_id", "customer_order", ["order_id"], ["id"]
        )
    op.create_index("ix_reorder_order_id", "reorder", ["order_id"])

    op.create_table(
        "order_vendor_po_claim",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "pick_list_id",
            sa.Integer(),
            sa.ForeignKey("pick_list.id"),
            nullable=False,
        ),
        sa.Column("line_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "pick_list_id", "line_index", name="uq_order_vendor_po_claim_line"
        ),
    )
    op.create_index(
        "ix_order_vendor_po_claim_pick_list_id",
        "order_vendor_po_claim",
        ["pick_list_id"],
    )

    op.drop_index("uq_reorder_open_per_item", table_name="reorder")
    op.create_index(
        "uq_reorder_open_per_item",
        "reorder",
        ["stock_item_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN', 'SENT') AND order_id IS NULL"),
        sqlite_where=sa.text("status IN ('OPEN', 'SENT') AND order_id IS NULL"),
    )


def downgrade() -> None:
    # Refuse to downgrade once any order-triggered PO exists — re-widening
    # the unique index could collide, and dropping the columns loses the
    # frozen customer context of live POs.
    bind = op.get_bind()
    linked = bind.execute(
        sa.text("SELECT COUNT(*) FROM reorder WHERE order_id IS NOT NULL")
    ).scalar()
    if linked:
        raise RuntimeError(
            f"{linked} order-triggered reorder row(s) exist — resolve them "
            "before downgrading."
        )
    op.drop_index("uq_reorder_open_per_item", table_name="reorder")
    op.create_index(
        "uq_reorder_open_per_item",
        "reorder",
        ["stock_item_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN', 'SENT')"),
        sqlite_where=sa.text("status IN ('OPEN', 'SENT')"),
    )
    op.drop_index(
        "ix_order_vendor_po_claim_pick_list_id", table_name="order_vendor_po_claim"
    )
    op.drop_table("order_vendor_po_claim")
    op.drop_index("ix_reorder_order_id", table_name="reorder")
    with op.batch_alter_table("reorder") as batch:
        batch.drop_constraint("fk_reorder_order_id", type_="foreignkey")
        batch.drop_column("customer_context")
        batch.drop_column("order_id")
