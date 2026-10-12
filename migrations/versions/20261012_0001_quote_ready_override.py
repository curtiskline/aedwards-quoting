"""Persist explicit human approval for unusual quote readiness.

The fingerprint binds the approval to the exact customer-facing quote
content so later edits cannot silently reuse it.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261012_0001"
down_revision = "20260918_0004"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("quote") as batch_op:
        batch_op.add_column(sa.Column("ready_override_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("ready_override_by", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("ready_override_fingerprint", sa.String(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_quote_ready_override_by_user",
            "user",
            ["ready_override_by"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("quote") as batch_op:
        batch_op.drop_constraint("fk_quote_ready_override_by_user", type_="foreignkey")
        batch_op.drop_column("ready_override_fingerprint")
        batch_op.drop_column("ready_override_by")
        batch_op.drop_column("ready_override_at")
