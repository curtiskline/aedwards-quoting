"""Add auth_token table for magic-link polling flow.

Revision ID: 20260408_0001
Revises: 20260406_0004
Create Date: 2026-04-08 14:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260408_0001"
down_revision = "20260406_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False, index=True),
        sa.Column("token", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("auth_token")
