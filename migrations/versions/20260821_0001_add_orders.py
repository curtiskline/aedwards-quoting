"""Add orders: acceptance_event + customer_order + order_audit_log (CP-3).

Stage D of the engine (task 409): a customer acceptance creates an Order
FROM the immutable QuoteVersion. acceptance_event.quote_version_id and
customer_order.quote_version_id/acceptance_event_id are all UNIQUE — the
claim-in-transaction idempotency guard (hazard §12.1) so a double-click or
replayed acceptance signal can never double-create an order.

Like quote_status (see 20260406_0001), the app's SAEnum columns persist the
enum member NAMES ("ACCEPTED", "EXPLICIT_CLICK"), not the values, so the
native PostgreSQL enum types are defined with the names.

Revision ID: 20260821_0001
Revises: 20260820_0003
Create Date: 2026-08-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_0001"
down_revision = "20260820_0003"
branch_labels = None
depends_on = None

acceptance_source = sa.Enum(
    "EXPLICIT_CLICK",
    "REPLY_PARSE",
    "PO_RECEIVED",
    name="acceptance_source",
)

order_status = sa.Enum(
    "ACCEPTED",
    "ORDERED",
    "FULFILLED",
    name="order_status",
)


def upgrade() -> None:
    # No explicit .create() calls: on PostgreSQL, op.create_table auto-creates
    # the native enum types (an explicit create first would double-CREATE).
    op.create_table(
        "acceptance_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("quote_version_id", sa.Integer(), nullable=False),
        sa.Column("source", acceptance_source, nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("po_number", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["quote_version_id"], ["quote_version.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_acceptance_event_quote_version_id"),
        "acceptance_event",
        ["quote_version_id"],
        unique=True,
    )

    op.create_table(
        "customer_order",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("quote_version_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("acceptance_event_id", sa.Integer(), nullable=False),
        sa.Column("status", order_status, nullable=False),
        sa.Column("po_number", sa.String(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_by", sa.Integer(), nullable=True),
        sa.Column("ordered_at", sa.DateTime(), nullable=True),
        sa.Column("ordered_by", sa.Integer(), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(), nullable=True),
        sa.Column("fulfilled_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["quote_version_id"], ["quote_version.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quote.id"]),
        sa.ForeignKeyConstraint(["acceptance_event_id"], ["acceptance_event.id"]),
        sa.ForeignKeyConstraint(["accepted_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["ordered_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["fulfilled_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_customer_order_quote_version_id"),
        "customer_order",
        ["quote_version_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_customer_order_acceptance_event_id"),
        "customer_order",
        ["acceptance_event_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_customer_order_quote_id"), "customer_order", ["quote_id"], unique=False
    )

    op.create_table(
        "order_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["customer_order.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_order_audit_log_order_id"), "order_audit_log", ["order_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_order_audit_log_order_id"), table_name="order_audit_log")
    op.drop_table("order_audit_log")
    op.drop_index(op.f("ix_customer_order_quote_id"), table_name="customer_order")
    op.drop_index(
        op.f("ix_customer_order_acceptance_event_id"), table_name="customer_order"
    )
    op.drop_index(op.f("ix_customer_order_quote_version_id"), table_name="customer_order")
    op.drop_table("customer_order")
    op.drop_index(
        op.f("ix_acceptance_event_quote_version_id"), table_name="acceptance_event"
    )
    op.drop_table("acceptance_event")
    order_status.drop(op.get_bind(), checkfirst=True)
    acceptance_source.drop(op.get_bind(), checkfirst=True)
