"""Engine v2 refinements: catalog vendor + reorder SENT state (task 439).

D72: AEI is a distributor — replenishment is a purchase order to a vendor,
never an in-house make. Three schema changes, all additive:

- product_catalog.vendor: who AEI buys the product from (plain text at
  <50 SKUs, e.g. "AE MFG"; NULL until Chip fills it in).
- reorder.vendor_at_trigger + sent_at/sent_by: the vendor is FROZEN on each
  reorder like min/max, and the OPEN -> SENT transition (Chip printed/sent
  the PO) is stamped.
- reorder_status gains SENT, and the one-active-reorder-per-item partial
  unique index widens from status='OPEN' to status IN ('OPEN','SENT') — a
  PO in flight to the vendor must keep holding the claim, or a stock drop
  mid-transit would double-order (PM agreement, task 439).

Like quote_status (see 20260406_0001), SAEnum columns persist the enum
member NAMES ("SENT"), so the native PostgreSQL enum value is the name
(I139). ALTER TYPE ... ADD VALUE cannot run inside the migration's
transaction, so it uses Alembic's autocommit_block (PostgreSQL only; the
SQLite test schema comes from create_all and stores plain VARCHAR).

Revision ID: 20260827_0001
Revises: 20260821_0004
Create Date: 2026-08-27 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260827_0001"
down_revision = "20260821_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE reorder_status ADD VALUE IF NOT EXISTS 'SENT' "
                "BEFORE 'RECEIVED'"
            )

    op.add_column(
        "product_catalog", sa.Column("vendor", sa.String(), nullable=True)
    )
    with op.batch_alter_table("reorder") as batch:
        batch.add_column(sa.Column("vendor_at_trigger", sa.String(), nullable=True))
        batch.add_column(sa.Column("sent_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("sent_by", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_reorder_sent_by", "user", ["sent_by"], ["id"])

    # Widen the active-reorder claim: at most one un-received (OPEN or SENT)
    # reorder per stock item.
    op.drop_index("uq_reorder_open_per_item", table_name="reorder")
    op.create_index(
        "uq_reorder_open_per_item",
        "reorder",
        ["stock_item_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN', 'SENT')"),
        sqlite_where=sa.text("status IN ('OPEN', 'SENT')"),
    )


def downgrade() -> None:
    # Refuse to downgrade once any reorder is SENT — narrowing the index and
    # dropping the enum value would corrupt live rows.
    bind = op.get_bind()
    sent = bind.execute(
        sa.text("SELECT COUNT(*) FROM reorder WHERE status = 'SENT'")
    ).scalar()
    if sent:
        raise RuntimeError(
            f"{sent} reorder row(s) are SENT — resolve them before downgrading."
        )
    op.drop_index("uq_reorder_open_per_item", table_name="reorder")
    op.create_index(
        "uq_reorder_open_per_item",
        "reorder",
        ["stock_item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
        sqlite_where=sa.text("status = 'OPEN'"),
    )
    with op.batch_alter_table("reorder") as batch:
        batch.drop_constraint("fk_reorder_sent_by", type_="foreignkey")
        batch.drop_column("sent_by")
        batch.drop_column("sent_at")
        batch.drop_column("vendor_at_trigger")
    op.drop_column("product_catalog", "vendor")
    # The PostgreSQL enum value 'SENT' is left in place: removing an enum
    # value requires a type rebuild and no row can reference it here.
