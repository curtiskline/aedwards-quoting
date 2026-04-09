"""Update pricing_table to April 2026 rates.

Revision ID: 20260409_0001
Revises: 20260408_0001
Create Date: 2026-04-09 16:00:00
"""

from __future__ import annotations

from decimal import Decimal

from alembic import op
import sqlalchemy as sa


revision = "20260409_0001"
down_revision = "20260408_0001"
branch_labels = None
depends_on = None


pricing_table = sa.table(
    "pricing_table",
    sa.column("product_type", sa.String()),
    sa.column("key_fields", sa.JSON()),
    sa.column("price", sa.Numeric(12, 2)),
    sa.column("updated_at", sa.DateTime()),
)

# (product_type, key_fields match criteria, old_price, new_price)
UPDATES: list[tuple[str, dict, Decimal, Decimal]] = [
    # Sleeve price per lb updates
    ("sleeve", {"wall_thickness": "0.25", "grade": 50}, Decimal("2.82"), Decimal("3.01")),
    ("sleeve", {"wall_thickness": "0.25", "grade": 65}, Decimal("2.92"), Decimal("3.11")),
    ("sleeve", {"wall_thickness": "0.3125", "grade": 50}, Decimal("2.69"), Decimal("2.86")),
    ("sleeve", {"wall_thickness": "0.3125", "grade": 65}, Decimal("2.79"), Decimal("2.96")),
    ("sleeve", {"wall_thickness": "0.375", "grade": 50}, Decimal("2.57"), Decimal("2.65")),
    ("sleeve", {"wall_thickness": "0.375", "grade": 65}, Decimal("2.67"), Decimal("2.75")),
    ("sleeve", {"wall_thickness": "0.5", "grade": 50}, Decimal("2.52"), Decimal("2.59")),
    ("sleeve", {"wall_thickness": "0.5", "grade": 65}, Decimal("2.62"), Decimal("2.69")),
    # Other pricing updates
    ("omegawrap", {"key": "omegawrap_carbon", "unit": "per_roll"}, Decimal("650"), Decimal("680")),
    ("omegawrap", {"key": "omegawrap_eglass", "unit": "per_roll"}, Decimal("420"), Decimal("470")),
    ("accessory", {"key": "plastic_wrap_large", "unit": "per_roll"}, Decimal("101"), Decimal("110")),
    # Service price updates
    ("flat", {"key": "milling", "unit": "flat"}, Decimal("30"), Decimal("35")),
    ("flat", {"key": "painting", "unit": "flat"}, Decimal("40"), Decimal("45")),
]


def _update_price(conn, product_type: str, key_fields: dict, old_price: Decimal, new_price: Decimal) -> None:
    """Update a single pricing_table row matched by product_type and key_fields."""
    # Use JSON matching — SQLite and PostgreSQL both support this via cast
    key_fields_json = sa.type_coerce(key_fields, sa.JSON())
    conn.execute(
        pricing_table.update()
        .where(pricing_table.c.product_type == product_type)
        .where(pricing_table.c.key_fields == key_fields_json)
        .values(price=new_price, updated_at=sa.func.now())
    )


def upgrade() -> None:
    conn = op.get_bind()
    for product_type, key_fields, old_price, new_price in UPDATES:
        _update_price(conn, product_type, key_fields, old_price, new_price)

    # Add new concrete_coating row
    conn.execute(
        pricing_table.insert().values(
            product_type="accessory",
            key_fields={"key": "concrete_coating", "unit": "per_inch_od_per_foot"},
            price=Decimal("1"),
            updated_at=sa.func.now(),
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    for product_type, key_fields, old_price, new_price in UPDATES:
        _update_price(conn, product_type, key_fields, new_price, old_price)

    # Remove concrete_coating row
    key_fields_json = sa.type_coerce({"key": "concrete_coating", "unit": "per_inch_od_per_foot"}, sa.JSON())
    conn.execute(
        pricing_table.delete()
        .where(pricing_table.c.product_type == "accessory")
        .where(pricing_table.c.key_fields == key_fields_json)
    )
