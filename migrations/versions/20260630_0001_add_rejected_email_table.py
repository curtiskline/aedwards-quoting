"""add rejected_email table

Revision ID: 20260630_0001
Revises: 20260629_0001
Create Date: 2026-06-30 22:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260630_0001"
down_revision = "20260629_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rejected_email",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("sender_name", sa.String(), nullable=True),
        sa.Column("sender_email", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("classifier_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_rejected_email_received_at"), "rejected_email", ["received_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_rejected_email_received_at"), table_name="rejected_email")
    op.drop_table("rejected_email")
