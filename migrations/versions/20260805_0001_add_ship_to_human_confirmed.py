"""Track whether a human has confirmed a stored ship-to address.

Existing rows intentionally begin unconfirmed: their origin cannot be reliably
reconstructed from the old schema, so the quote editor surfaces them for review.

Revision ID: 20260805_0001
Revises: 20260730_0001
Create Date: 2026-08-05 21:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260805_0001"
down_revision = "20260730_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ship_to_address") as batch_op:
        batch_op.add_column(
            sa.Column(
                "human_confirmed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("ship_to_address") as batch_op:
        batch_op.drop_column("human_confirmed")
