"""add failed_intake table

Records RFQs the monitor could not turn into a quote (parse/truncation/DB/
quote-number/quarantine failures) so they are visible in the app instead of
being silently dropped.

Revision ID: 20260813_0001
Revises: 20260811_0002
Create Date: 2026-08-13 00:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260813_0001"
down_revision = "20260811_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failed_intake",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("source_email_id", sa.String(), nullable=True),
        sa.Column("sender_name", sa.String(), nullable=True),
        sa.Column("sender_email", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("failure_stage", sa.String(), nullable=False),
        sa.Column("error_type", sa.String(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("acknowledged", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_failed_intake_received_at"), "failed_intake", ["received_at"], unique=False)
    op.create_index(op.f("ix_failed_intake_source_email_id"), "failed_intake", ["source_email_id"], unique=False)
    op.create_index(op.f("ix_failed_intake_resolved_at"), "failed_intake", ["resolved_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_failed_intake_resolved_at"), table_name="failed_intake")
    op.drop_index(op.f("ix_failed_intake_source_email_id"), table_name="failed_intake")
    op.drop_index(op.f("ix_failed_intake_received_at"), table_name="failed_intake")
    op.drop_table("failed_intake")
