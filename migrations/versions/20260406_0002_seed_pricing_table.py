"""Seed pricing_table with baseline pricing values.

Revision ID: 20260406_0002
Revises: 20260406_0001
Create Date: 2026-04-06 13:00:00
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from alembic import op
import sqlalchemy as sa


revision = "20260406_0002"
down_revision = "20260406_0001"
branch_labels = None
depends_on = None


pricing_table = sa.table(
    "pricing_table",
    sa.column("product_type", sa.String()),
    sa.column("key_fields", sa.JSON()),
    sa.column("price", sa.Numeric(12, 2)),
    sa.column("updated_at", sa.DateTime()),
)


def _default_pricing_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    sleeve = {
        "0.25": (Decimal("2.82"), Decimal("2.92")),
        "0.3125": (Decimal("2.69"), Decimal("2.79")),
        "0.375": (Decimal("2.57"), Decimal("2.67")),
        "0.5": (Decimal("2.52"), Decimal("2.62")),
        "0.625": (Decimal("2.52"), Decimal("2.62")),
        "0.75": (Decimal("2.52"), Decimal("2.62")),
    }
    for wall_thickness, (gr50, gr65) in sleeve.items():
        rows.append({"product_type": "sleeve", "key_fields": {"wall_thickness": wall_thickness, "grade": 50}, "price": gr50})
        rows.append({"product_type": "sleeve", "key_fields": {"wall_thickness": wall_thickness, "grade": 65}, "price": gr65})

    girth_weld = [(2, 19, Decimal("300")), (20, 31, Decimal("500")), (32, 44, Decimal("800"))]
    for min_diameter, max_diameter, price in girth_weld:
        rows.append({"product_type": "girth_weld", "key_fields": {"min_diameter": min_diameter, "max_diameter": max_diameter}, "price": price})

    bag = [
        (10, 13, "GTW 10-12", 110, Decimal("52.08")),
        (14, 19, "GTW 16", 52, Decimal("80.77")),
        (20, 27, "GTW 20-24", 34, Decimal("138.24")),
        (28, 39, "GTW 30-36", 30, Decimal("155.00")),
        (40, 48, "GTW 42-48", 21, Decimal("214.29")),
    ]
    for pipe_size_min, pipe_size_max, part_number, pieces_per_pallet, price in bag:
        rows.append(
            {
                "product_type": "bag",
                "key_fields": {
                    "pipe_size_min": pipe_size_min,
                    "pipe_size_max": pipe_size_max,
                    "part_number": part_number,
                    "pieces_per_pallet": pieces_per_pallet,
                },
                "price": price,
            }
        )

    other = {
        "bag_fill": (Decimal("0.02"), "per_lb"),
        "omegawrap_carbon": (Decimal("650"), "per_roll"),
        "omegawrap_eglass": (Decimal("420"), "per_roll"),
        "omegawrap_magnum": (Decimal("390"), "per_roll"),
        "isolation_wrap": (Decimal("200"), "per_roll"),
        "resin": (Decimal("125"), "per_quart"),
        "putty": (Decimal("130"), "per_pint"),
        "compression_sleeve": (Decimal("5000"), "per_set"),
        "porcupine_roller": (Decimal("210"), "each"),
        "magnet_set": (Decimal("80"), "per_set"),
        "accessory_kit": (Decimal("122"), "per_kit"),
        "plastic_wrap_large": (Decimal("101"), "per_roll"),
        "pipejacks": (Decimal("1800"), "each"),
        "pipejacks_large": (Decimal("2200"), "each"),
        "supervisor": (Decimal("1950"), "per_day"),
        "trainer_torch": (Decimal("6000"), "per_package"),
        "kickoff_training": (Decimal("6000"), "per_package"),
        "training_package": (Decimal("400"), "per_package"),
    }
    service_keys = {"supervisor", "trainer_torch", "kickoff_training", "training_package"}
    for key, (price, unit) in other.items():
        if key == "compression_sleeve":
            product_type = "compression"
        elif key.startswith("omegawrap_"):
            product_type = "omegawrap"
        elif key in service_keys:
            product_type = "service"
        else:
            product_type = "accessory"
        rows.append({"product_type": product_type, "key_fields": {"key": key, "unit": unit}, "price": price})

    rows.append({"product_type": "flat", "key_fields": {"key": "milling", "unit": "flat"}, "price": Decimal("30")})
    rows.append({"product_type": "flat", "key_fields": {"key": "painting", "unit": "flat"}, "price": Decimal("40")})
    return rows


def upgrade() -> None:
    now = datetime.utcnow()
    rows = [
        {
            "product_type": row["product_type"],
            "key_fields": row["key_fields"],
            "price": row["price"],
            "updated_at": now,
        }
        for row in _default_pricing_rows()
    ]
    op.bulk_insert(pricing_table, rows)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM pricing_table"))
