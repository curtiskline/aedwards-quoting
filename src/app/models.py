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
    REPLACED = "replaced"


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
    # Stored ship-to addresses may be inferred from RFQ data. Only an explicit
    # human action may promote one to a trusted default.
    human_confirmed: Mapped[bool] = mapped_column(default=False, nullable=False)

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
    bill_to_json: Mapped[dict | None] = mapped_column(db.JSON)
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    replaces_quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote.id"), nullable=True, unique=True, index=True
    )
    revision_number: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="quotes")
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])
    line_items: Mapped[list["QuoteLineItem"]] = relationship(
        back_populates="quote", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["QuoteAttachment"]] = relationship(
        back_populates="quote", cascade="all, delete-orphan"
    )
    replaces: Mapped["Quote | None"] = relationship(
        foreign_keys=[replaces_quote_id], remote_side=[id], back_populates="replaced_by"
    )
    replaced_by: Mapped["Quote | None"] = relationship(
        foreign_keys=[replaces_quote_id], back_populates="replaces", uselist=False
    )
    versions: Mapped[list["QuoteVersion"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="quote", cascade="all, delete-orphan")
    confidence: Mapped["QuoteConfidence | None"] = relationship(
        back_populates="quote", cascade="all, delete-orphan", uselist=False
    )


class QuoteConfidence(db.Model):
    """Per-quote confidence score + component signals (CP-2a: scoring only).

    One row per quote, recomputed on creation and on relevant edits. Each
    signal column is tri-state ("pass"/"fail"/"unknown"); unknown is never
    treated as pass. components_json carries the per-signal detail (weight,
    points, reasons) so the CP-2b dashboard can show WHY, not just a number.
    Nothing reads this for send decisions yet — that is CP-2c.
    """

    __tablename__ = "quote_confidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id"), nullable=False, unique=True, index=True
    )
    score: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    decode_clean: Mapped[str] = mapped_column(nullable=False)
    all_lines_priced: Mapped[str] = mapped_column(nullable=False)
    customer_known: Mapped[str] = mapped_column(nullable=False)
    ship_to_confirmed: Mapped[str] = mapped_column(nullable=False)
    price_in_tolerance: Mapped[str] = mapped_column(nullable=False)
    recipient_allowlisted: Mapped[str] = mapped_column(nullable=False)
    components_json: Mapped[dict] = mapped_column(db.JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)

    quote: Mapped[Quote] = relationship(back_populates="confidence")


class SendHold(TimestampMixin, db.Model):
    """An admin-set hold that forces a quote NOT-recommended regardless of score.

    Exactly one of customer_id / product_type is set (enforced by a check
    constraint): a customer hold covers every quote linked to that customer, a
    product-type hold covers every quote with a material line of that type.
    CP-2b only displays the effect (recommend-only); CP-2c's auto-send must
    honor the same rows — this is the per-customer / per-product-type dial from
    design §4 Tier 2, built early so the data model is already trusted.
    """

    __tablename__ = "send_hold"
    __table_args__ = (
        db.CheckConstraint(
            "(customer_id IS NULL) != (product_type IS NULL)",
            name="ck_send_hold_exactly_one_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer.id"), nullable=True, unique=True, index=True
    )
    product_type: Mapped[str | None] = mapped_column(nullable=True, unique=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))

    customer: Mapped[Customer | None] = relationship()


class TrustRampConfig(db.Model):
    """Single-row global trust-ramp state (design §4).

    active_tier is the kill-switch dial: 0 = fully manual, 1 = assisted
    (recommend-only), 2 = auto-send the safe slice (CP-2c). Setting the tier
    back to 0/1 stops all auto-sends immediately — the auto-send gate reads
    this value live on every attempt. The three dial columns are the CP-2c
    admin-configurable knobs; NULL means "use the environment/default value"
    (confidence.py owns the fallback chain).
    """

    __tablename__ = "trust_ramp_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    active_tier: Mapped[int] = mapped_column(default=1, nullable=False)
    # Minimum composite confidence score for Tier-2 auto-send (0-1).
    auto_send_threshold: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    # Maximum quote grand total (product + shipping + tax) that may auto-send.
    auto_send_dollar_ceiling: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # price_in_tolerance signal tolerance as a fraction (0.20 = ±20%).
    price_tolerance_pct: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class AutoSendClaim(TimestampMixin, db.Model):
    """Durable idempotency claim for Tier-2 auto-send (hazard §12.1).

    Auto-send is an irreversible external action, so the claim row is
    committed atomically with the QuoteVersion BEFORE the external send: a
    crash or replay after that commit finds the claim and never sends twice.
    One claim per quote, ever — any auto-send attempt (blocked, failed, or
    sent) consumes the quote's single auto-send chance and every later
    outcome falls to the human path, which never reads this table.

    status: "claimed"  — committed pre-send; a row stuck here means the
                          process died between claim-commit and the send
                          (email did NOT go out; human resolves).
            "blocked"  — a send gate refused before any external attempt
                          (delivery disabled, allowlist, credentials).
            "failed"   — the external send itself raised.
            "sent"     — delivered; the quote is SENT with a QuoteVersion.
    """

    __tablename__ = "auto_send_claim"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(
        ForeignKey("quote.id"), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(nullable=False, default="claimed")
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    quote_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_version.id"), nullable=True
    )

    quote: Mapped[Quote] = relationship()
    quote_version: Mapped["QuoteVersion | None"] = relationship()


class ProcessedInboundEmail(TimestampMixin, db.Model):
    """Durable, message-level idempotency claim for the inbox monitor.

    A single inbound email can legitimately create several Quote rows, so this
    guard cannot live on Quote.source_email_id itself.
    """

    __tablename__ = "processed_inbound_email"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_email_id: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)


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


class QuoteAttachment(TimestampMixin, db.Model):
    __tablename__ = "quote_attachment"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(nullable=False)
    content_type: Mapped[str] = mapped_column(nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(nullable=False, default=0)
    is_stored: Mapped[bool] = mapped_column(nullable=False, default=True)
    content_bytes: Mapped[bytes] = mapped_column(db.LargeBinary, nullable=False)

    quote: Mapped[Quote] = relationship(back_populates="attachments")


class QuoteVersion(db.Model):
    __tablename__ = "quote_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(nullable=False)
    pdf_path: Mapped[str] = mapped_column(nullable=False)
    # "retained" means pdf_path and line_items_snapshot are the immutable
    # send-time record.  Pre-archive versions are explicitly "missing" rather
    # than being mistaken for a record that was never sent.
    artifact_status: Mapped[str] = mapped_column(nullable=False, default="missing")
    line_items_snapshot: Mapped[list[dict] | None] = mapped_column(db.JSON, nullable=True)
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


class ProductType(TimestampMixin, db.Model):
    __tablename__ = "product_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    display_label: Mapped[str] = mapped_column(nullable=False)
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)


class ProductCatalog(db.Model):
    __tablename__ = "product_catalog"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Business identifier — optional, not a referential backbone. Name-only
    # products are first-class; the surrogate `id` is the rename-safe key.
    part_number: Mapped[str | None] = mapped_column(nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Category slug, mirroring QuoteLineItem.product_type (matches an editable
    # ProductType.name). Nullable: untriaged legacy rows carry no type yet.
    product_type: Mapped[str | None] = mapped_column(nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ShippingConfig(db.Model):
    __tablename__ = "shipping_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    default_rate_per_lb_mile: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0.0006)
    default_length_ft: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=10.0)
    origin_zip_codes_json: Mapped[list[str]] = mapped_column(db.JSON, nullable=False, default=lambda: ["74103"])
    rate_overrides_json: Mapped[dict | None] = mapped_column(db.JSON)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class RejectedEmail(db.Model):
    __tablename__ = "rejected_email"

    id: Mapped[int] = mapped_column(primary_key=True)
    received_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    sender_name: Mapped[str | None]
    sender_email: Mapped[str | None]
    subject: Mapped[str | None]
    classifier_reason: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)


class FailedIntake(db.Model):
    """A visible record of an RFQ the monitor could not turn into a quote.

    When the inbox monitor cannot produce a quote (parse failure, LLM response
    truncation, DB/quote-number error, or any other exception in the processing
    path) it quarantines the message so it is not retried forever.  Without a
    durable record that quarantine is silent — the sender hears nothing and no
    one on staff can see the RFQ ever arrived (Chip's 2026-08-13 report, I130).
    This table makes every such failure visible in the app so it can be handled
    manually.
    """

    __tablename__ = "failed_intake"

    id: Mapped[int] = mapped_column(primary_key=True)
    received_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    source_email_id: Mapped[str | None] = mapped_column(index=True)
    sender_name: Mapped[str | None]
    sender_email: Mapped[str | None]
    subject: Mapped[str | None]
    # A coarse label for where processing failed (e.g. "parse_truncated",
    # "db_write", "processing"), derived from the exception type.
    failure_stage: Mapped[str] = mapped_column(nullable=False, default="processing")
    error_type: Mapped[str | None]
    error_detail: Mapped[str | None] = mapped_column(Text)
    # Whether an acknowledgment was sent back to the sender (opt-in feature).
    acknowledged: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Set when a human marks the intake as handled, so it drops off the
    # outstanding list without deleting the audit trail.
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)


class AuditLog(TimestampMixin, db.Model):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    details: Mapped[dict | None] = mapped_column(db.JSON)

    quote: Mapped[Quote] = relationship(back_populates="audit_logs")
