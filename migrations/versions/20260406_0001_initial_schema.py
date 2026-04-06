"""Initial schema for quote management scaffold.

Revision ID: 20260406_0001
Revises:
Create Date: 2026-04-06 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_0001"
down_revision = None
branch_labels = None
depends_on = None


quote_status = sa.Enum(
    "new",
    "in_review",
    "needs_pricing",
    "ready",
    "sent",
    "archived",
    name="quote_status",
)


def upgrade() -> None:
    bind = op.get_bind()
    quote_status.create(bind, checkfirst=True)

    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("magic_link_token", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_user_email", "user", ["email"])

    op.create_table(
        "customer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_name", sa.String(), nullable=False),
        sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_customer_company_name", "customer", ["company_name"])

    op.create_table(
        "contact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
    )
    op.create_index("ix_contact_customer_id", "contact", ["customer_id"])

    op.create_table(
        "ship_to_address",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("address_line1", sa.String(), nullable=False),
        sa.Column("address_line2", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("postal_code", sa.String(), nullable=False),
        sa.Column("country", sa.String(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_ship_to_address_customer_id", "ship_to_address", ["customer_id"])

    op.create_table(
        "quote",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quote_number", sa.String(), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("status", quote_status, nullable=False),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("project_name", sa.String(), nullable=True),
        sa.Column("notes_customer", sa.Text(), nullable=True),
        sa.Column("notes_internal", sa.Text(), nullable=True),
        sa.Column("source_email_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("quote_number"),
    )
    op.create_index("ix_quote_quote_number", "quote", ["quote_number"])
    op.create_index("ix_quote_customer_id", "quote", ["customer_id"])

    op.create_table(
        "quote_line_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quote.id"), nullable=False),
        sa.Column("product_type", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("specs_json", sa.JSON(), nullable=True),
        sa.Column("part_number", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
    )
    op.create_index("ix_quote_line_item_quote_id", "quote_line_item", ["quote_id"])
    op.create_index("ix_quote_line_item_product_type", "quote_line_item", ["product_type"])

    op.create_table(
        "quote_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quote.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("pdf_path", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("sent_by", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("sent_to", sa.String(), nullable=True),
    )
    op.create_index("ix_quote_version_quote_id", "quote_version", ["quote_id"])

    op.create_table(
        "pricing_table",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_type", sa.String(), nullable=False),
        sa.Column("key_fields", sa.JSON(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pricing_table_product_type", "pricing_table", ["product_type"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quote.id"), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_log_quote_id", "audit_log", ["quote_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_quote_id", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_pricing_table_product_type", table_name="pricing_table")
    op.drop_table("pricing_table")

    op.drop_index("ix_quote_version_quote_id", table_name="quote_version")
    op.drop_table("quote_version")

    op.drop_index("ix_quote_line_item_product_type", table_name="quote_line_item")
    op.drop_index("ix_quote_line_item_quote_id", table_name="quote_line_item")
    op.drop_table("quote_line_item")

    op.drop_index("ix_quote_customer_id", table_name="quote")
    op.drop_index("ix_quote_quote_number", table_name="quote")
    op.drop_table("quote")

    op.drop_index("ix_ship_to_address_customer_id", table_name="ship_to_address")
    op.drop_table("ship_to_address")

    op.drop_index("ix_contact_customer_id", table_name="contact")
    op.drop_table("contact")

    op.drop_index("ix_customer_company_name", table_name="customer")
    op.drop_table("customer")

    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")

    quote_status.drop(op.get_bind(), checkfirst=True)
