"""Reprice backing strip from $10 each to $400 per pack of 10.

Chip Edwards, 2026-07-28: "The catalog was off. They are priced as we do on
buckey quote. 400$ for pack of 10."

The pricing_table row is keyed on ``{"key": ..., "unit": ...}``, so the unit
change is part of the key: a stale ``{"backing_strip", "each"}`` row cannot be
updated in place, it has to be replaced. Deployments that never persisted a
backing strip row simply gain the correct one here (the pricing snapshot falls
back to pricing_catalog.py, which now carries the same numbers).

Revision ID: 20260730_0001
Revises: 20260721_0001
Create Date: 2026-07-30 16:00:00
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from alembic import op
import sqlalchemy as sa


revision = "20260730_0001"
down_revision = "20260721_0001"
branch_labels = None
depends_on = None


pricing_table = sa.table(
    "pricing_table",
    sa.column("product_type", sa.String()),
    sa.column("key_fields", sa.JSON()),
    sa.column("price", sa.Numeric(12, 2)),
    sa.column("updated_at", sa.DateTime()),
)

_OLD_KEY_FIELDS = {"key": "backing_strip", "unit": "each"}
_NEW_KEY_FIELDS = {"key": "backing_strip", "unit": "per_pack_of_10"}


def _replace_row(old_key_fields: dict, new_key_fields: dict, price: Decimal) -> None:
    conn = op.get_bind()
    for key_fields in (old_key_fields, new_key_fields):
        conn.execute(
            pricing_table.delete()
            .where(pricing_table.c.product_type == "accessory")
            .where(pricing_table.c.key_fields == sa.type_coerce(key_fields, sa.JSON()))
        )
    op.bulk_insert(
        pricing_table,
        [
            {
                "product_type": "accessory",
                "key_fields": new_key_fields,
                "price": price,
                "updated_at": datetime.utcnow(),
            }
        ],
    )


def upgrade() -> None:
    _replace_row(_OLD_KEY_FIELDS, _NEW_KEY_FIELDS, Decimal("400"))


def downgrade() -> None:
    _replace_row(_NEW_KEY_FIELDS, _OLD_KEY_FIELDS, Decimal("10"))
