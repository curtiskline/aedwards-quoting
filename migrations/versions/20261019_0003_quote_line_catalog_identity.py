"""Link quote lines to Product Catalog identities and persist match state.

Existing lines are deliberately left nullable/unchecked.  New intake and
editor writes resolve exact catalog matches; a separate status distinguishes
active, inactive, unmatched, ambiguous, and type-mismatch outcomes.

Revision ID: 20261019_0003
Revises: 20261019_0002
Create Date: 2026-10-19 00:00:00
"""

from __future__ import annotations

import json
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "20261019_0003"
down_revision = "20261019_0002"
branch_labels = None
depends_on = None


def _pricing_table() -> sa.TableClause:
    return sa.table(
        "pricing_table",
        sa.column("id", sa.Integer()),
        sa.column("product_type", sa.String()),
        sa.column("key_fields", sa.JSON()),
        sa.column("price", sa.Numeric(12, 2)),
    )


def _rewrite_sheet_pricing(*, forward: bool) -> None:
    """Move existing sleeve/girth rows to/from the October 2026 source sheet."""
    table = _pricing_table()
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(table.c.id, table.c.product_type, table.c.key_fields, table.c.price).where(
            table.c.product_type.in_(["sleeve", "girth_weld"])
        )
    ).mappings()

    sleeve_new = {
        "0.25": Decimal("4.45"),
        "0.3125": Decimal("3.84"),
        "0.375": Decimal("3.31"),
        "0.5": Decimal("3.40"),
    }
    sleeve_old = {
        "0.25": Decimal("3.01"),
        "0.3125": Decimal("2.86"),
        "0.375": Decimal("2.65"),
        "0.5": Decimal("2.59"),
    }
    girth_new = {
        2: (2, 16, Decimal("325")),
        18: (18, 30, Decimal("550")),
        20: (18, 30, Decimal("550")),
        32: (32, 44, Decimal("875")),
    }
    girth_old = {
        2: (2, 19, Decimal("300")),
        18: (20, 31, Decimal("500")),
        32: (32, 44, Decimal("800")),
    }
    for row in rows:
        fields = row["key_fields"]
        if isinstance(fields, str):
            fields = json.loads(fields)
        fields = dict(fields or {})
        price: Decimal | None = None
        if row["product_type"] == "sleeve":
            wall = str(fields.get("wall_thickness", ""))
            grade = int(fields.get("grade") or 50)
            base = (sleeve_new if forward else sleeve_old).get(wall)
            if base is not None:
                price = base + (Decimal("0.17") if grade == 65 and forward else Decimal("0.10") if grade == 65 else Decimal("0"))
        elif row["product_type"] == "girth_weld":
            minimum = int(fields.get("min_diameter") or 0)
            values = (girth_new if forward else girth_old).get(minimum)
            if values is not None:
                fields["min_diameter"], fields["max_diameter"], price = values
        if price is not None:
            bind.execute(
                table.update().where(table.c.id == row["id"]).values(key_fields=fields, price=price)
            )


def upgrade() -> None:
    with op.batch_alter_table("product_catalog") as batch:
        batch.add_column(sa.Column("diameter_min", sa.Numeric(12, 4), nullable=True))
        batch.add_column(sa.Column("diameter_max", sa.Numeric(12, 4), nullable=True))
        batch.add_column(sa.Column("wall_thickness_min", sa.Numeric(12, 4), nullable=True))
        batch.add_column(sa.Column("wall_thickness_max", sa.Numeric(12, 4), nullable=True))

    with op.batch_alter_table("quote_line_item") as batch:
        batch.add_column(sa.Column("catalog_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "catalog_match_status",
                sa.String(),
                nullable=False,
                server_default="unchecked",
            )
        )
        batch.create_foreign_key(
            "fk_quote_line_item_catalog_id",
            "product_catalog",
            ["catalog_id"],
            ["id"],
        )
    op.create_index(
        op.f("ix_quote_line_item_catalog_id"),
        "quote_line_item",
        ["catalog_id"],
        unique=False,
    )
    _rewrite_sheet_pricing(forward=True)
    op.create_index(
        op.f("ix_quote_line_item_catalog_match_status"),
        "quote_line_item",
        ["catalog_match_status"],
        unique=False,
    )


def downgrade() -> None:
    _rewrite_sheet_pricing(forward=False)
    op.drop_index(
        op.f("ix_quote_line_item_catalog_match_status"),
        table_name="quote_line_item",
    )
    op.drop_index(op.f("ix_quote_line_item_catalog_id"), table_name="quote_line_item")
    with op.batch_alter_table("quote_line_item") as batch:
        batch.drop_constraint("fk_quote_line_item_catalog_id", type_="foreignkey")
        batch.drop_column("catalog_match_status")
        batch.drop_column("catalog_id")
    with op.batch_alter_table("product_catalog") as batch:
        batch.drop_column("wall_thickness_max")
        batch.drop_column("wall_thickness_min")
        batch.drop_column("diameter_max")
        batch.drop_column("diameter_min")
