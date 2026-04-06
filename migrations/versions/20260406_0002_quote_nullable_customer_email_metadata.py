"""Make quote.customer_id nullable and add email metadata columns.

Revision ID: 20260406_0002
Revises: 20260406_0001
Create Date: 2026-04-06 18:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_0002"
down_revision = "20260406_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quote") as batch_op:
        batch_op.alter_column("customer_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("sender_email", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("sender_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("subject", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("customer_name_raw", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("contact_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("contact_email", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("contact_phone", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("po_number", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("ship_to_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quote") as batch_op:
        batch_op.drop_column("ship_to_json")
        batch_op.drop_column("po_number")
        batch_op.drop_column("contact_phone")
        batch_op.drop_column("contact_email")
        batch_op.drop_column("contact_name")
        batch_op.drop_column("customer_name_raw")
        batch_op.drop_column("subject")
        batch_op.drop_column("sender_name")
        batch_op.drop_column("sender_email")
        batch_op.alter_column("customer_id", existing_type=sa.Integer(), nullable=False)
