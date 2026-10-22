"""Fix bag pallet counts and stale seed prices to the April 2026 sheet.

GTW 10-12 is 48 pieces per pallet on the April 2026 bag price sheet; the
20260406_0003 seed carried 110 from an older list, so freshly seeded
databases (staging's Postgres cutover, any rebuild) kept reverting to it
even after production was corrected by hand. This migration fixes the
pallet count everywhere and brings prices forward only where they still
hold the original seed values, so manually maintained prices (production,
2026-07-20) are left untouched.

Revision ID: 20261019_0002
Revises: 20261013_0002
Create Date: 2026-10-19 18:00:00
"""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "20261019_0002"
down_revision = "20261013_0002"
branch_labels = None
depends_on = None


pricing_table = sa.table(
    "pricing_table",
    sa.column("id", sa.Integer()),
    sa.column("product_type", sa.String()),
    sa.column("key_fields", sa.JSON()),
    sa.column("price", sa.Numeric(12, 2)),
    sa.column("updated_at", sa.DateTime()),
)

# part_number -> (pieces_per_pallet, seed price it may still hold, April 2026 per-bag price)
APRIL_2026_BAGS: dict[str, tuple[int, Decimal, Decimal]] = {
    "GTW 10-12": (48, Decimal("52.08"), Decimal("46.87")),
    "GTW 16": (52, Decimal("80.77"), Decimal("72.69")),
    "GTW 20-24": (34, Decimal("138.24"), Decimal("124.41")),
    "GTW 30-36": (30, Decimal("155.00"), Decimal("139.50")),
    "GTW 42-48": (21, Decimal("214.29"), Decimal("192.85")),
}

OLD_PALLET_COUNTS: dict[str, int] = {"GTW 10-12": 110}


def _bag_rows(conn):
    return conn.execute(
        sa.select(pricing_table.c.id, pricing_table.c.key_fields, pricing_table.c.price).where(
            pricing_table.c.product_type == "bag"
        )
    ).fetchall()


def upgrade() -> None:
    conn = op.get_bind()
    for row_id, key_fields, price in _bag_rows(conn):
        fields = dict(key_fields or {})
        entry = APRIL_2026_BAGS.get(str(fields.get("part_number", "")))
        if entry is None:
            continue
        pieces_per_pallet, seed_price, april_price = entry
        values: dict[str, object] = {}
        if fields.get("pieces_per_pallet") != pieces_per_pallet:
            fields["pieces_per_pallet"] = pieces_per_pallet
            values["key_fields"] = fields
        if Decimal(str(price)) == seed_price:
            values["price"] = april_price
        if values:
            values["updated_at"] = sa.func.now()
            conn.execute(
                pricing_table.update().where(pricing_table.c.id == row_id).values(**values)
            )


def downgrade() -> None:
    conn = op.get_bind()
    for row_id, key_fields, price in _bag_rows(conn):
        fields = dict(key_fields or {})
        part_number = str(fields.get("part_number", ""))
        entry = APRIL_2026_BAGS.get(part_number)
        if entry is None:
            continue
        _, seed_price, april_price = entry
        values: dict[str, object] = {}
        old_count = OLD_PALLET_COUNTS.get(part_number)
        if old_count is not None and fields.get("pieces_per_pallet") != old_count:
            fields["pieces_per_pallet"] = old_count
            values["key_fields"] = fields
        if Decimal(str(price)) == april_price:
            values["price"] = seed_price
        if values:
            values["updated_at"] = sa.func.now()
            conn.execute(
                pricing_table.update().where(pricing_table.c.id == row_id).values(**values)
            )
