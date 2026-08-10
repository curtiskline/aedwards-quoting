"""Add a canonical bill-to address column to the quote.

Chip: the address in an email signature is a bill-to, not a ship-to (D37). Task
331 preserved that signature address as a labelled line in ``notes_internal`` as a
stopgap, because the quote had no billing-address column and Template A's Bill To
block rendered the customer name only. This gives the bill-to a real home so it can
be rendered on the quote PDF.

``bill_to_json`` holds the canonical 8-key shape produced by
``allenedwards.ship_to.normalize_ship_to`` (company, attention, address_line1,
address_line2, city, state, postal_code, country) — deliberately NOT the two
incompatible shapes ``ship_to_json`` circulated in prod (I106).

Existing rows keep ``bill_to_json`` NULL: their signature address, if any, lives in
``notes_internal`` under the "Bill-to (from email signature):" prefix. It is not
backfilled here — the pipe-delimited note text is a lossy render, not the source
dict, and parsing it back would reintroduce the shape ambiguity this column avoids.

Revision ID: 20260810_0001
Revises: 20260805_0002
Create Date: 2026-08-10 20:15:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260810_0001"
down_revision = "20260805_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quote") as batch_op:
        batch_op.add_column(sa.Column("bill_to_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quote") as batch_op:
        batch_op.drop_column("bill_to_json")
