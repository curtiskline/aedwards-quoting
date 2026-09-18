"""Retain optional quote email prose and exact sent email content.

Revision ID: 20260918_0002
Revises: 20260918_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260918_0002"
down_revision = "20260918_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quote", sa.Column("email_message", sa.Text(), nullable=True))
    op.add_column("quote_version", sa.Column("email_body", sa.Text(), nullable=True))
    op.add_column("quote_version", sa.Column("email_subject", sa.Text(), nullable=True))
    op.add_column("quote_version", sa.Column("email_cc", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("quote_version", "email_cc")
    op.drop_column("quote_version", "email_subject")
    op.drop_column("quote_version", "email_body")
    op.drop_column("quote", "email_message")
