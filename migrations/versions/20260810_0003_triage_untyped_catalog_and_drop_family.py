"""Triage the 66 type-unset catalog rows and retire product_family for good

Follow-up to 20260810_0002 (task 360, parent 358). Chip signed off on the
Part Number / Type / Composite naming (decision chip-approved-terminology-
naming-2026-08-10); Family is NOT part of that vision, so product_family must
not persist into the first production deploy of this batch. 0002 mapped the 6
clean families onto Types and kept product_family as reversible legacy, which
left 66 rows (old OTHER 63 + PIPE_JACK 3) with product_type unset.

This migration:

1. Assigns a real Type to those 66 rows by part_number pattern (case-
   insensitive), using the mappings Devin approved. Two non-product rows
   ('207-HB-EPOXY' training-only, 'Product List' a BOM) are left untyped on
   purpose. Only rows whose product_type is still NULL are touched, so already
   triaged rows are never disturbed.
2. Drops product_family from ProductCatalog entirely — the column and its
   index — so Family is fully retired.

Verified against a copy of prod (225 rows). Post-upgrade product_type counts:
sleeve 92, girth_weld 61, composite 24, accessory 22, bag 11, compression 13,
NULL 2 (the two untyped), total 225. The 66 delta: sleeve +28, accessory +21,
composite +14, bag +1, untyped 2.

Revision ID: 20260810_0003
Revises: 20260810_0002
Create Date: 2026-08-11 04:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260810_0003"
down_revision = "20260810_0002"
branch_labels = None
depends_on = None


# product_type slug -> old family enum value, for the best-effort downgrade
# repopulation of product_family. Mirrors 0002's _TYPE_TO_FAMILY; anything not
# listed (accessory, and the untyped rows) falls back to OTHER, the safe
# catch-all the pre-terminology model already tolerated. The restore is lossy
# (e.g. the old PIPE_JACK rows come back as OTHER) — acceptable for rollback.
_TYPE_TO_FAMILY = {
    "sleeve": "SLEEVE",
    "girth_weld": "GIRTH_WELD",
    "bag": "BAG",
    "compression": "COMPRESSION_SLEEVE",
    "composite": "OMEGAWRAP",
}


def upgrade() -> None:
    # --- 1. Assign Types to the 66 type-unset rows by part_number pattern ----
    # Order matters: 'accessory' runs first so 'SLV BACKINGSTRIP' is claimed as
    # an accessory before the 'SLV %' sleeve rule could see it. The sleeve rule
    # also excludes it explicitly, so the two are belt-and-suspenders safe.
    # Every statement is guarded by product_type IS NULL so already-triaged
    # rows (and re-runs) are never touched.
    op.execute(
        sa.text(
            "UPDATE product_catalog SET product_type = 'accessory' "
            "WHERE product_type IS NULL AND ("
            "  part_number LIKE 'Backing%'"
            "  OR part_number = 'Backing Bar'"
            "  OR part_number LIKE 'CK-%'"
            "  OR part_number LIKE 'PJ-%'"
            "  OR part_number LIKE 'Casing Spacers%'"
            "  OR part_number = 'End Seal'"
            "  OR part_number LIKE 'STUD%'"
            "  OR part_number = 'Sheet Metal'"
            "  OR part_number = 'Spacers'"
            "  OR part_number = 'SLV BACKINGSTRIP'"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE product_catalog SET product_type = 'composite' "
            "WHERE product_type IS NULL AND ("
            "  part_number LIKE 'OW-%'"
            "  OR part_number = 'Carbon Fiber'"
            "  OR part_number LIKE 'Porcupine Roller%'"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE product_catalog SET product_type = 'bag' "
            "WHERE product_type IS NULL AND part_number LIKE 'GTWeight%'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE product_catalog SET product_type = 'sleeve' "
            "WHERE product_type IS NULL AND ("
            "  (part_number LIKE 'SLV %' AND part_number <> 'SLV BACKINGSTRIP')"
            "  OR part_number LIKE 'LP-%'"
            "  OR part_number LIKE 'X-S-%'"
            "  OR part_number LIKE 'FWS%'"
            ")"
        )
    )
    # '207-HB-EPOXY' and 'Product List' are intentionally left type-unset.

    # --- 2. Drop product_family for good (column + index) -------------------
    with op.batch_alter_table("product_catalog", schema=None) as batch_op:
        batch_op.drop_index("ix_product_catalog_product_family")
        batch_op.drop_column("product_family")


def downgrade() -> None:
    # Re-add product_family (nullable) and best-effort repopulate it from
    # product_type so 0002's downgrade — which reads product_family — still
    # works. Lossy by design: the six clean types round-trip, everything else
    # (accessory, the untyped rows) restores as OTHER.
    with op.batch_alter_table("product_catalog", schema=None) as batch_op:
        batch_op.add_column(sa.Column("product_family", sa.String(length=18), nullable=True))
    op.create_index(
        "ix_product_catalog_product_family", "product_catalog", ["product_family"]
    )
    case_sql = " ".join(
        f"WHEN '{slug}' THEN '{family}'" for slug, family in _TYPE_TO_FAMILY.items()
    )
    op.execute(
        sa.text(
            "UPDATE product_catalog SET product_family = "
            f"CASE product_type {case_sql} ELSE 'OTHER' END"
        )
    )
