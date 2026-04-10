"""SQLAlchemy models for the quote management web app."""

from __future__ import annotations

import secrets
from datetime import datetime
from enum import Enum

from flask_login import UserMixin
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


class QuoteStatus(str, Enum):
    NEW = "new"
    IN_REVIEW = "in_review"
    NEEDS_PRICING = "needs_pricing"
    READY = "ready"
    SENT = "sent"
    ARCHIVED = "archived"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    magic_link_token: Mapped[str | None]

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def issue_magic_link_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.magic_link_token = token
        return token


class AuthToken(TimestampMixin, db.Model):
    """Magic-link authentication tokens for cross-device polling flow."""

    __tablename__ = "auth_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(foreign_keys=[user_id])

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > datetime.utcnow()

    def mark_used(self) -> None:
        self.used_at = datetime.utcnow()


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(nullable=False, index=True)
    discount_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    ship_to_addresses: Mapped[list["ShipToAddress"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    quotes: Mapped[list["Quote"]] = relationship(back_populates="customer")


class Contact(db.Model):
    __tablename__ = "contact"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(nullable=False)
    email: Mapped[str] = mapped_column(nullable=False)
    phone: Mapped[str | None]

    customer: Mapped[Customer] = relationship(back_populates="contacts")


class ShipToAddress(db.Model):
    __tablename__ = "ship_to_address"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)
    address_line1: Mapped[str] = mapped_column(nullable=False)
    address_line2: Mapped[str | None]
    city: Mapped[str] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(nullable=False)
    postal_code: Mapped[str] = mapped_column(nullable=False)
    country: Mapped[str] = mapped_column(default="US", nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="ship_to_addresses")


class Quote(db.Model):
    __tablename__ = "quote"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_number: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    status: Mapped[QuoteStatus] = mapped_column(
        SAEnum(QuoteStatus, name="quote_status"), default=QuoteStatus.NEW, nullable=False
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    review_started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    project_name: Mapped[str | None]
    notes_customer: Mapped[str | None] = mapped_column(Text)
    notes_internal: Mapped[str | None] = mapped_column(Text)
    source_email_id: Mapped[str | None]
    sender_email: Mapped[str | None]
    sender_name: Mapped[str | None]
    subject: Mapped[str | None]
    customer_name_raw: Mapped[str | None]
    contact_name: Mapped[str | None]
    contact_email: Mapped[str | None]
    contact_phone: Mapped[str | None]
    po_number: Mapped[str | None]
    ship_to_json: Mapped[dict | None] = mapped_column(db.JSON)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="quotes")
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])
    line_items: Mapped[list["QuoteLineItem"]] = relationship(
        back_populates="quote", cascade="all, delete-orphan"
    )
    versions: Mapped[list["QuoteVersion"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="quote", cascade="all, delete-orphan")


class QuoteLineItem(db.Model):
    __tablename__ = "quote_line_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id"), nullable=False, index=True)
    product_type: Mapped[str] = mapped_column(nullable=False, index=True)
    description: Mapped[str] = mapped_column(nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    line_total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    specs_json: Mapped[dict | None] = mapped_column(db.JSON)
    part_number: Mapped[str | None]
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False)

    quote: Mapped[Quote] = relationship(back_populates="line_items")


class QuoteVersion(db.Model):
    __tablename__ = "quote_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(nullable=False)
    pdf_path: Mapped[str] = mapped_column(nullable=False)
    sent_at: Mapped[datetime | None]
    sent_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    sent_to: Mapped[str | None]

    quote: Mapped[Quote] = relationship(back_populates="versions")


class PricingTable(db.Model):
    __tablename__ = "pricing_table"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_type: Mapped[str] = mapped_column(nullable=False, index=True)
    key_fields: Mapped[dict] = mapped_column(db.JSON, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ShippingConfig(db.Model):
    __tablename__ = "shipping_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    default_rate_per_lb_mile: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0.0006)
    default_length_ft: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=10.0)
    origin_zip_codes_json: Mapped[list[str]] = mapped_column(db.JSON, nullable=False, default=lambda: ["74103"])
    rate_overrides_json: Mapped[dict | None] = mapped_column(db.JSON)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AuditLog(TimestampMixin, db.Model):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    details: Mapped[dict | None] = mapped_column(db.JSON)

    quote: Mapped[Quote] = relationship(back_populates="audit_logs")
