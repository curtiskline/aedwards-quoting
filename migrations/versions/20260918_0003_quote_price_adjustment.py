"""Explicit quote-level percentage pricing."""
import sqlalchemy as sa
from alembic import op

revision = "20260918_0003"
down_revision = "20260918_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quote", sa.Column("price_adjustment_pct", sa.Numeric(5, 2), server_default="0", nullable=False))


def downgrade():
    op.drop_column("quote", "price_adjustment_pct")
