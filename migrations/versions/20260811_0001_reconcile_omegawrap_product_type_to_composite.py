"""Reconcile product_type 'omegawrap' -> 'composite' in pricing_table and line items

Task 362 (parent 358). The terminology refactor (358/360) renamed the catalog
axis to Type and folded OmegaWrap into 'composite' (ProductCatalog/ProductType
already use 'composite'). The decode + pricing path was left on the legacy
'omegawrap' product_type string. Now that the decode prompt emits 'composite'
and pricing dispatches on 'composite', the stored product_type VALUES must move
too, or existing rows fall off the Type taxonomy and priced wraps split into a
stale group.

This migration rewrites only the product_type STRING 'omegawrap' -> 'composite'
in two tables:
- pricing_table: the seeded omegawrap_* variant rows (product_type was the group
  slug, the variant lives in key_fields.key and is untouched).
- quote_line_item: any historical wrap line items decoded/priced before this
  batch.

Internal variant keys (omegawrap_carbon, etc. in key_fields.key and OTHER_PRICING)
are deliberately NOT touched — they are sub-variant lookup keys, not the
product_type.

Downgrade reverses the same two updates (composite -> omegawrap). This is safe
because 'composite' as a product_type on these two tables originates solely from
this rename; ProductCatalog/ProductType are untouched by this migration.

Revision ID: 20260811_0001
Revises: 20260810_0003
Create Date: 2026-08-11 05:20:00
"""

from __future__ import annotations

from alembic import op

revision = "20260811_0001"
down_revision = "20260810_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE pricing_table SET product_type = 'composite' "
        "WHERE product_type = 'omegawrap'"
    )
    op.execute(
        "UPDATE quote_line_item SET product_type = 'composite' "
        "WHERE product_type = 'omegawrap'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE pricing_table SET product_type = 'omegawrap' "
        "WHERE product_type = 'composite'"
    )
    op.execute(
        "UPDATE quote_line_item SET product_type = 'omegawrap' "
        "WHERE product_type = 'composite'"
    )
