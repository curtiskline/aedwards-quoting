"""add sku to quote_line_item

Revision ID: 20260512_0002
Revises: 20260511_0001
Create Date: 2026-05-12 03:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260512_0002"
down_revision = "20260511_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quote_line_item", sa.Column("sku", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_quote_line_item_sku"), "quote_line_item", ["sku"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_quote_line_item_sku"), table_name="quote_line_item")
    op.drop_column("quote_line_item", "sku")
