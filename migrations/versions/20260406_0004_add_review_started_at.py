"""Add review_started_at column to quote table for team awareness locking.

Revision ID: 20260406_0004
Revises: 20260406_0003
Create Date: 2026-04-06 18:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_0004"
down_revision = "20260406_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quote", sa.Column("review_started_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("quote", "review_started_at")
