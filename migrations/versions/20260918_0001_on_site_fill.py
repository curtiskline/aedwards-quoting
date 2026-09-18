"""Location-specific, manually priced on-site geotextile filling (task 465)."""

import sqlalchemy as sa
from alembic import op

revision = "20260918_0001"
down_revision = "20260827_0002"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("on_site_label", "on_site_city", "on_site_state"):
        op.add_column("quote_line_item", sa.Column(name, sa.String(), nullable=True))
    op.add_column("quote_line_item", sa.Column("on_site_price_source", sa.Text(), nullable=True))
    op.add_column("quote_line_item", sa.Column("on_site_priced_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_quote_line_item_fill_location", "quote_line_item", ["on_site_city", "on_site_state"]
    )
    op.execute(
        sa.text("""
        INSERT INTO product_type (name, display_label, sort_order, is_active, created_at, updated_at)
        SELECT 'on_site_fill', 'Bag — On-site Fill', COALESCE(MAX(sort_order), 0) + 1,
               true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM product_type
        HAVING NOT EXISTS (SELECT 1 FROM product_type WHERE name = 'on_site_fill')
    """)
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM quote_line_item WHERE product_type = 'on_site_fill'")
        )
        .scalar()
    ):
        raise RuntimeError(
            "On-site fill lines exist; preserve their location history before downgrading."
        )
    op.execute(sa.text("DELETE FROM product_type WHERE name = 'on_site_fill'"))
    op.drop_index("ix_quote_line_item_fill_location", table_name="quote_line_item")
    for name in (
        "on_site_priced_at",
        "on_site_price_source",
        "on_site_state",
        "on_site_city",
        "on_site_label",
    ):
        op.drop_column("quote_line_item", name)
