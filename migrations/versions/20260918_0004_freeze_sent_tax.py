"""Freeze sent tax so accepted order totals match the quoted total.

Historical versions remain NULL; do not infer tax from mutable live quotes.
"""
import sqlalchemy as sa
from alembic import op

revision = "20260918_0004"
down_revision = "20260918_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quote_version", sa.Column("tax_amount", sa.Numeric(12, 2), nullable=True))


def downgrade():
    op.drop_column("quote_version", "tax_amount")
