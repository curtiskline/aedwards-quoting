"""Add auto_send_claim + trust-ramp dial columns (CP-2c: Tier-2 auto-send).

auto_send_claim: one-per-quote idempotency claim committed atomically with
the QuoteVersion BEFORE the external send, so a crash/replay can never
double-send (hazard §12.1). trust_ramp_config gains the three admin dials
(auto_send_threshold, auto_send_dollar_ceiling, price_tolerance_pct); NULL
means "use the environment/default fallback" so existing rows keep today's
behavior unchanged.

Revision ID: 20260820_0003
Revises: 20260820_0002
Create Date: 2026-08-20 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260820_0003"
down_revision = "20260820_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auto_send_claim",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("quote_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"]),
        sa.ForeignKeyConstraint(["quote_version_id"], ["quote_version.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_auto_send_claim_quote_id"), "auto_send_claim", ["quote_id"], unique=True
    )

    op.add_column(
        "trust_ramp_config",
        sa.Column("auto_send_threshold", sa.Numeric(4, 3), nullable=True),
    )
    op.add_column(
        "trust_ramp_config",
        sa.Column("auto_send_dollar_ceiling", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "trust_ramp_config",
        sa.Column("price_tolerance_pct", sa.Numeric(5, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trust_ramp_config", "price_tolerance_pct")
    op.drop_column("trust_ramp_config", "auto_send_dollar_ceiling")
    op.drop_column("trust_ramp_config", "auto_send_threshold")
    op.drop_index(op.f("ix_auto_send_claim_quote_id"), table_name="auto_send_claim")
    op.drop_table("auto_send_claim")
