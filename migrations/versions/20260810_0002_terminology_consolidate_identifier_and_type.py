"""Terminology cleanup: consolidate identifier to part_number, fold Family into Type

Implements the naming Chip signed off on (task 358, parent 357):

1. QuoteLineItem: collapse the two identifier columns (`sku` + `part_number`)
   into a single `part_number`. Copy `sku` into `part_number` where the latter
   is blank, then drop `sku`.
2. ProductCatalog: rename `sku` -> `part_number` and relax it to NULLABLE with
   no UNIQUE constraint, so name-only products are first-class. Add a nullable
   `product_type` slug (mirrors QuoteLineItem.product_type / ProductType.name)
   and populate it from the retired `product_family` enum. `product_family` is
   kept as a nullable retained-legacy column (removed from the UI only) so the
   family->type mapping stays reversible and untriaged rows keep their label.
   PIPE_JACK and OTHER have no agreed type mapping yet -> left type-unset.
3. Drop the junk `test` product type (id 9).

Revision ID: 20260810_0002
Revises: 20260810_0001
Create Date: 2026-08-10 18:55:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260810_0002"
down_revision = "20260810_0001"
branch_labels = None
depends_on = None


# Old family enum name (stored uppercase) -> new product_type slug.
# PIPE_JACK and OTHER intentionally absent: no agreed mapping, left unset.
_FAMILY_TO_TYPE = {
    "SLEEVE": "sleeve",
    "GIRTH_WELD": "girth_weld",
    "BAG": "bag",
    "COMPRESSION_SLEEVE": "compression",
    "OMEGAWRAP": "composite",
    "BACKING_STRIP": "accessory",
}

# Reverse mapping for downgrade. `composite`/`accessory` are ambiguous targets
# (several families map onto them); we restore only the unambiguous ones and
# fall back to OTHER, which is the safe catch-all the app already tolerates.
_TYPE_TO_FAMILY = {
    "sleeve": "SLEEVE",
    "girth_weld": "GIRTH_WELD",
    "bag": "BAG",
    "compression": "COMPRESSION_SLEEVE",
    "composite": "OMEGAWRAP",
}


def _report_untriaged(bind) -> None:
    """Report untriaged rows so the operator sees what still needs a type."""
    untriaged = bind.execute(
        sa.text(
            "SELECT COALESCE(product_family, '(null)'), COUNT(*) FROM product_catalog "
            "WHERE product_type IS NULL GROUP BY product_family"
        )
    ).fetchall()
    for family, count in untriaged:
        print(f"[358 migration] {count} catalog rows left type-unset (family={family})")


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. QuoteLineItem: fold sku into part_number, then drop sku ---------
    op.execute(
        sa.text(
            "UPDATE quote_line_item SET part_number = sku "
            "WHERE (part_number IS NULL OR TRIM(part_number) = '') "
            "AND sku IS NOT NULL AND TRIM(sku) <> ''"
        )
    )
    with op.batch_alter_table("quote_line_item") as batch_op:
        batch_op.drop_index("ix_quote_line_item_sku")
        batch_op.drop_column("sku")

    # --- 3. Drop the junk `test` product type ------------------------------
    op.execute(sa.text("DELETE FROM product_type WHERE name = 'test'"))

    # --- 2. ProductCatalog: rename sku->part_number (nullable, non-unique),
    #        add product_type, retain product_family as nullable legacy -----
    case_sql = " ".join(
        f"WHEN '{family}' THEN '{slug}'" for family, slug in _FAMILY_TO_TYPE.items()
    )
    if bind.dialect.name == "postgresql":
        # PostgreSQL can do every step in place — no table rebuild needed.
        op.execute(sa.text("ALTER TABLE product_catalog RENAME COLUMN sku TO part_number"))
        op.execute(sa.text("ALTER TABLE product_catalog ALTER COLUMN part_number DROP NOT NULL"))
        op.execute(sa.text("ALTER TABLE product_catalog DROP CONSTRAINT product_catalog_sku_key"))
        # product_family becomes a plain retained-legacy VARCHAR; the native
        # enum type goes away with it.
        op.execute(
            sa.text(
                "ALTER TABLE product_catalog ALTER COLUMN product_family "
                "TYPE VARCHAR(18) USING product_family::text"
            )
        )
        op.execute(sa.text("ALTER TABLE product_catalog ALTER COLUMN product_family DROP NOT NULL"))
        op.execute(sa.text("DROP TYPE IF EXISTS product_family"))
        op.execute(sa.text("ALTER TABLE product_catalog ADD COLUMN product_type VARCHAR"))
        op.execute(
            sa.text(
                "UPDATE product_catalog SET product_type = "
                f"CASE UPPER(product_family) {case_sql} ELSE NULL END"
            )
        )
        op.execute(sa.text("ALTER INDEX ix_product_catalog_sku RENAME TO ix_product_catalog_part_number"))
        op.create_index("ix_product_catalog_product_type", "product_catalog", ["product_type"])
        _report_untriaged(bind)
        return

    # SQLite cannot drop an unnamed UNIQUE constraint or alter nullability in
    # place, so rebuild the table explicitly. This keeps full control over the
    # family->type population and is exactly what we test against a prod copy.
    op.execute(
        sa.text(
            """
            CREATE TABLE product_catalog_new (
                id INTEGER NOT NULL,
                part_number VARCHAR,
                description TEXT NOT NULL,
                product_type VARCHAR,
                product_family VARCHAR(18),
                is_active BOOLEAN DEFAULT 1 NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                PRIMARY KEY (id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO product_catalog_new "
            "(id, part_number, description, product_type, product_family, "
            " is_active, created_at, updated_at) "
            "SELECT id, sku, description, "
            f"CASE UPPER(product_family) {case_sql} ELSE NULL END, "
            "product_family, is_active, created_at, updated_at "
            "FROM product_catalog"
        )
    )
    op.execute(sa.text("DROP TABLE product_catalog"))
    op.execute(sa.text("ALTER TABLE product_catalog_new RENAME TO product_catalog"))
    op.create_index("ix_product_catalog_part_number", "product_catalog", ["part_number"])
    op.create_index("ix_product_catalog_product_type", "product_catalog", ["product_type"])
    op.create_index("ix_product_catalog_product_family", "product_catalog", ["product_family"])
    op.create_index("ix_product_catalog_is_active", "product_catalog", ["is_active"])

    _report_untriaged(bind)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # PostgreSQL adoption happened at head; nothing below head has ever
        # existed on a PostgreSQL database, so a relational downgrade of this
        # rebuild has no consumer. Refuse loudly rather than half-restore.
        raise NotImplementedError(
            "20260810_0002 downgrade is not supported on PostgreSQL; "
            "restore from a pg_dump backup instead"
        )

    # --- 2. ProductCatalog: restore sku (NOT NULL UNIQUE), drop product_type,
    #        restore product_family NOT NULL -------------------------------
    op.execute(
        sa.text(
            """
            CREATE TABLE product_catalog_old (
                id INTEGER NOT NULL,
                sku VARCHAR NOT NULL,
                description TEXT NOT NULL,
                product_family VARCHAR(18) NOT NULL,
                is_active BOOLEAN DEFAULT 1 NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                PRIMARY KEY (id),
                UNIQUE (sku)
            )
            """
        )
    )
    # Restore product_family from product_type where it was dropped/unset, and
    # synthesize a placeholder sku for any name-only rows added post-upgrade so
    # the NOT NULL UNIQUE constraint holds.
    case_sql = " ".join(
        f"WHEN '{slug}' THEN '{family}'" for slug, family in _TYPE_TO_FAMILY.items()
    )
    op.execute(
        sa.text(
            "INSERT INTO product_catalog_old "
            "(id, sku, description, product_family, is_active, created_at, updated_at) "
            "SELECT id, "
            "COALESCE(NULLIF(TRIM(part_number), ''), 'UNKNOWN-' || id), "
            "description, "
            "COALESCE(product_family, "
            f"CASE product_type {case_sql} ELSE 'OTHER' END), "
            "is_active, created_at, updated_at "
            "FROM product_catalog"
        )
    )
    op.execute(sa.text("DROP TABLE product_catalog"))
    op.execute(sa.text("ALTER TABLE product_catalog_old RENAME TO product_catalog"))
    op.create_index("ix_product_catalog_sku", "product_catalog", ["sku"])
    op.create_index("ix_product_catalog_product_family", "product_catalog", ["product_family"])
    op.create_index("ix_product_catalog_is_active", "product_catalog", ["is_active"])

    # --- 3. Re-seed the `test` product type --------------------------------
    existing = op.get_bind().execute(
        sa.text("SELECT 1 FROM product_type WHERE name = 'test'")
    ).scalar()
    if not existing:
        max_sort = op.get_bind().execute(
            sa.text("SELECT COALESCE(MAX(sort_order), 0) FROM product_type")
        ).scalar() or 0
        op.execute(
            sa.text(
                "INSERT INTO product_type (name, display_label, sort_order, is_active, created_at) "
                "VALUES ('test', 'Test', :sort, 1, CURRENT_TIMESTAMP)"
            ).bindparams(sort=int(max_sort) + 1)
        )

    # --- 1. QuoteLineItem: restore the (now empty) sku column --------------
    with op.batch_alter_table("quote_line_item") as batch_op:
        batch_op.add_column(sa.Column("sku", sa.String(length=100), nullable=True))
        batch_op.create_index("ix_quote_line_item_sku", ["sku"], unique=False)
