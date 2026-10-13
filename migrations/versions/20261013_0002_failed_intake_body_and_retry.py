"""Persist inbound body on failed_intake and allow retry attempts (T505).

failed_intake gains the QuoteSourceSnapshot-shaped body columns (provider
original body + content type, normalized parser input) so a hard-crash parse
can be corrected and retried from the app. Forward-only: existing rows keep
NULL bodies and stay non-retryable.

quote_reparse_attempt rows can now belong to a failed_intake instead of a
quote (quote_id becomes nullable, failed_intake_id is added); a CHECK keeps at
least one parent set.

Revision ID: 20261013_0002
Revises: 20261013_0001
Create Date: 2026-10-13 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261013_0002"
down_revision = "20261013_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("failed_intake") as batch:
        batch.add_column(sa.Column("original_body", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("original_body_content_type", sa.String(), nullable=True)
        )
        batch.add_column(sa.Column("normalized_body_text", sa.Text(), nullable=True))

    with op.batch_alter_table("quote_reparse_attempt") as batch:
        batch.alter_column("quote_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("failed_intake_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_quote_reparse_attempt_failed_intake_id",
            "failed_intake",
            ["failed_intake_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_reparse_attempt_has_parent",
            "quote_id IS NOT NULL OR failed_intake_id IS NOT NULL",
        )
    op.create_index(
        op.f("ix_quote_reparse_attempt_failed_intake_id"),
        "quote_reparse_attempt",
        ["failed_intake_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_quote_reparse_attempt_failed_intake_id"),
        table_name="quote_reparse_attempt",
    )
    with op.batch_alter_table("quote_reparse_attempt") as batch:
        batch.drop_constraint("ck_reparse_attempt_has_parent", type_="check")
        batch.drop_constraint(
            "fk_quote_reparse_attempt_failed_intake_id", type_="foreignkey"
        )
        batch.drop_column("failed_intake_id")
        batch.alter_column("quote_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("failed_intake") as batch:
        batch.drop_column("normalized_body_text")
        batch.drop_column("original_body_content_type")
        batch.drop_column("original_body")
