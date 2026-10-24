"""Seed active girth-weld dimensional-range rows in the Product Catalog.

Girth welds are priced per set by diameter range (D105); the catalog had no
active rows shaped that way, so every girth-weld line resolved UNMATCHED and
carried a spurious "Not found in Product Catalog" flag (Chip, quote 126-174,
task 530).  One range row is created per girth-weld pricing-table row, so the
catalog ranges always mirror whatever ranges the deployment actually prices
from.  The rows deliberately carry NO part number: the catalog identity is the
per-set range, while the quote line keeps its generated per-size part number
(Chip rejected bracket-style part numbers on lines).  Wall bounds stay NULL —
wall and grade do not differentiate girth-weld pricing.

Revision ID: 20261024_0001
Revises: 20261019_0003
Create Date: 2026-10-24 00:00:00
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "20261024_0001"
down_revision = "20261019_0003"
branch_labels = None
depends_on = None


def _catalog_table() -> sa.TableClause:
    return sa.table(
        "product_catalog",
        sa.column("id", sa.Integer()),
        sa.column("part_number", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("product_type", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("diameter_min", sa.Numeric(12, 4)),
        sa.column("diameter_max", sa.Numeric(12, 4)),
    )


def _girth_weld_ranges(bind: sa.engine.Connection) -> list[tuple[int, int]]:
    rows = bind.execute(
        sa.text("SELECT key_fields FROM pricing_table WHERE product_type = 'girth_weld'")
    ).scalars()
    ranges: list[tuple[int, int]] = []
    for fields in rows:
        if isinstance(fields, str):
            fields = json.loads(fields)
        fields = dict(fields or {})
        minimum, maximum = fields.get("min_diameter"), fields.get("max_diameter")
        if minimum is not None and maximum is not None:
            ranges.append((int(minimum), int(maximum)))
    return sorted(set(ranges))


# The description intentionally avoids a "<low>-<high>in" token: that shape is
# rewritten to the requested size when a range row freezes its text onto a
# line (echo_requested_size), and girth-weld lines should keep their own
# generated per-size description instead.
def _description(minimum: int, maximum: int) -> str:
    return (
        f'Girth Weld Sleeve (per set), fits {minimum}"-{maximum}" pipe, any wall/grade'
    )


def upgrade() -> None:
    bind = op.get_bind()
    table = _catalog_table()
    for minimum, maximum in _girth_weld_ranges(bind):
        existing = bind.execute(
            sa.select(table.c.id).where(
                table.c.product_type == "girth_weld",
                table.c.diameter_min == minimum,
                table.c.diameter_max == maximum,
            )
        ).first()
        if existing is not None:
            continue
        bind.execute(
            table.insert().values(
                part_number=None,
                description=_description(minimum, maximum),
                product_type="girth_weld",
                is_active=True,
                diameter_min=minimum,
                diameter_max=maximum,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    table = _catalog_table()
    bind.execute(
        table.delete().where(
            table.c.product_type == "girth_weld",
            table.c.part_number.is_(None),
            table.c.diameter_min.isnot(None),
            table.c.diameter_max.isnot(None),
        )
    )
