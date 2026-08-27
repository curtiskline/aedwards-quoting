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


class OrderStatus(str, Enum):
    """Order lifecycle, picking up where QuoteStatus stops (design Stage D).

    ACCEPTED -> ORDERED -> FULFILLED. Pick/load/ship progress belongs to the
    future CP-4 PickList record (queued/picked/loaded/shipped), NOT here —
    FULFILLED is set from that flow when it exists; until then it is unused.
    """

    ACCEPTED = "accepted"
    ORDERED = "ordered"
    FULFILLED = "fulfilled"


class PickListStatus(str, Enum):
    """Pick/load/ship progress for one order's fulfillment (design Stage E).

    Lives HERE, not on Order (T409 agreement): the Order machine stays
    ACCEPTED -> ORDERED -> FULFILLED, and this record carries the shop-floor
    detail in between. QUEUED at creation; SHIPPED is what flips the Order
    to FULFILLED.
    """

    QUEUED = "queued"
    PICKED = "picked"
    LOADED = "loaded"
    SHIPPED = "shipped"


class ShopPingChannel(str, Enum):
    """How the shop was told a pick list exists. Only MANUAL_PRINT is wired
    in CP-4 — the ping IS the printed sheet plus the work-queue indicator.
    EMAIL/SMS/SCREEN are reserved seams (I136 pluggability); wiring them means
    an outbound delivery gate first, not just adding a member here."""

    MANUAL_PRINT = "manual_print"
    EMAIL = "email"
    SMS = "sms"
    SCREEN = "screen"


class ReorderStatus(str, Enum):
    """Lifecycle of a vendor purchase order (CP-5b + engine v2, D72: AEI is
    a distributor — replenishment is a PO to a vendor, never an in-house
    make). OPEN from the moment the trigger fires; SENT once the PO has gone
    to the vendor (Chip printing/marking it — the mark-sent action); RECEIVED
    when the delivery is booked as a RECEIPT movement. Receipt is allowed
    straight from OPEN — goods can arrive without mark-sent ever being
    clicked, so receipt implies sent. The received qty is what the vendor
    ACTUALLY delivered — it may differ from the ordered qty. No partial
    tracking: a short receipt closes the reorder and the still-below-min
    state re-triggers a fresh one naturally (PM agreement, task 419)."""

    OPEN = "open"
    SENT = "sent"
    RECEIVED = "received"


class AcceptanceSource(str, Enum):
    """How an acceptance was detected. Only EXPLICIT_CLICK is wired today;
    the other members are reserved seams (Chip call 2026-08-19: reply-reading
    later, behind its own confidence gate). Future signals create the same
    AcceptanceEvent through orders.create_order_from_acceptance()."""

    EXPLICIT_CLICK = "explicit_click"
    REPLY_PARSE = "reply_parse"
    PO_RECEIVED = "po_received"


class StockMovementType(str, Enum):
    """Why a stock item's on_hand changed — one ledger row per change (CP-5,
    design Stage F). UNMATCHED_SHIPMENT rows carry a shipped pick line that
    matched no stock identity; they change nothing and sit in triage until a
    human resolves them. REORDER is reserved for CP-5b's auto-reorder (gated
    on Chip's D68 answer) — no code path writes it yet; the member exists so
    CP-5b needs no PG enum migration."""

    SHIPMENT_DECREMENT = "shipment_decrement"
    RECEIPT = "receipt"
    ADJUSTMENT = "adjustment"
    UNMATCHED_SHIPMENT = "unmatched_shipment"
    REORDER = "reorder"


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


class AcceptanceEvent(TimestampMixin, db.Model):
    """First-class record of HOW a customer acceptance was detected.

    This is the pluggable acceptance seam (CP-3): every signal — the explicit
    human click built today, reply parsing or PO-email detection later —
    records one of these and creates the Order through the same
    orders.create_order_from_acceptance() path. quote_version_id is UNIQUE:
    acceptance binds to the exact immutable QuoteVersion the customer saw,
    and the constraint is the idempotency claim (hazard §12.1) — a
    double-click or replayed signal hits IntegrityError instead of
    double-creating.
    """

    __tablename__ = "acceptance_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_version_id: Mapped[int] = mapped_column(
        ForeignKey("quote_version.id"), nullable=False, unique=True, index=True
    )
    source: Mapped[AcceptanceSource] = mapped_column(
        SAEnum(AcceptanceSource, name="acceptance_source"), nullable=False
    )
    # The human who recorded the acceptance (explicit_click); NULL for future
    # automated signals, which describe themselves in actor_label instead.
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    actor_label: Mapped[str | None]
    note: Mapped[str | None] = mapped_column(Text)
    po_number: Mapped[str | None]

    quote_version: Mapped[QuoteVersion] = relationship()
    actor: Mapped[User | None] = relationship(foreign_keys=[actor_user_id])


class Order(db.Model):
    """A customer order created from an accepted, immutable QuoteVersion.

    The order READS QuoteVersion.line_items_snapshot — it never re-derives
    lines from the mutable Quote and never mutates the version (§12.8).
    quote_version_id and acceptance_event_id are both UNIQUE so no replay
    path can ever double-create an order for the same sent record.

    `id` is an INTERNAL identifier only. A customer-facing order-number
    scheme is a deferred Chip decision — never surface "Order #<id>" in
    anything customer-shaped (PDFs, emails); reference the quote number.
    """

    # "order" is an SQL reserved word; customer_order keeps raw SQL sane.
    __tablename__ = "customer_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_version_id: Mapped[int] = mapped_column(
        ForeignKey("quote_version.id"), nullable=False, unique=True, index=True
    )
    # Denormalized for queue joins; the authoritative payload is the version.
    quote_id: Mapped[int] = mapped_column(ForeignKey("quote.id"), nullable=False, index=True)
    acceptance_event_id: Mapped[int] = mapped_column(
        ForeignKey("acceptance_event.id"), nullable=False, unique=True, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status"),
        default=OrderStatus.ACCEPTED,
        nullable=False,
    )
    po_number: Mapped[str | None]
    accepted_at: Mapped[datetime] = mapped_column(nullable=False)
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    ordered_at: Mapped[datetime | None]
    ordered_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    fulfilled_at: Mapped[datetime | None]
    fulfilled_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    quote_version: Mapped[QuoteVersion] = relationship()
    quote: Mapped[Quote] = relationship()
    acceptance_event: Mapped[AcceptanceEvent] = relationship()
    audit_logs: Mapped[list["OrderAuditLog"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderAuditLog(TimestampMixin, db.Model):
    __tablename__ = "order_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("customer_order.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    details: Mapped[dict | None] = mapped_column(db.JSON)

    order: Mapped[Order] = relationship(back_populates="audit_logs")


class PickList(db.Model):
    """The shop's pick/load/ship record for one order (CP-4, design Stage E).

    order_id is UNIQUE: one pick list per order, ever — creation is the
    claim-in-transaction idempotency guard (hazard §12.1), and there is NO
    regenerate path. If an order was botched, the escape hatch is at the
    ORDER level (revise the quote, accept the revision — a new order gets a
    new pick list); a pick list is never edited or replaced.

    lines_snapshot is materialized AT CREATION from
    QuoteVersion.line_items_snapshot — never from the live Quote or catalog
    (§12.8: drift between quoted lines and picked goods is the primary
    fulfillment hazard). Pack-unit math (bundle/pallet counts) is frozen in
    here too, so the printed sheet cannot change under later pricing-table
    edits.
    """

    __tablename__ = "pick_list"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("customer_order.id"), nullable=False, unique=True, index=True
    )
    status: Mapped[PickListStatus] = mapped_column(
        SAEnum(PickListStatus, name="pick_list_status"),
        default=PickListStatus.QUEUED,
        nullable=False,
    )
    lines_snapshot: Mapped[list[dict]] = mapped_column(db.JSON, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    picked_at: Mapped[datetime | None]
    picked_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    loaded_at: Mapped[datetime | None]
    loaded_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    shipped_at: Mapped[datetime | None]
    shipped_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    order: Mapped[Order] = relationship()
    audit_logs: Mapped[list["PickListAuditLog"]] = relationship(
        back_populates="pick_list", cascade="all, delete-orphan"
    )
    pings: Mapped[list["ShopPing"]] = relationship(
        back_populates="pick_list", cascade="all, delete-orphan"
    )


class PickListAuditLog(TimestampMixin, db.Model):
    __tablename__ = "pick_list_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_list_id: Mapped[int] = mapped_column(
        ForeignKey("pick_list.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    details: Mapped[dict | None] = mapped_column(db.JSON)

    pick_list: Mapped[PickList] = relationship(back_populates="audit_logs")


class ShopPing(TimestampMixin, db.Model):
    """A record that the shop was notified about a work item, per channel.

    CP-4 creates exactly one MANUAL_PRINT row with each pick list; CP-5b
    creates one with each auto-reorder. The "ping" is the printed sheet plus
    the shop work queue. Exactly ONE of pick_list_id / reorder_id is set
    (CHECK constraint) — a ping is about one work item. No outbound delivery
    of any kind happens here — future EMAIL/SMS/SCREEN channels write their
    own rows through their own delivery gates.
    """

    __tablename__ = "shop_ping"
    __table_args__ = (
        db.CheckConstraint(
            "(pick_list_id IS NULL) <> (reorder_id IS NULL)",
            name="ck_shop_ping_exactly_one_subject",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_list_id: Mapped[int | None] = mapped_column(
        ForeignKey("pick_list.id"), nullable=True, index=True
    )
    reorder_id: Mapped[int | None] = mapped_column(
        ForeignKey("reorder.id"), nullable=True, index=True
    )
    channel: Mapped[ShopPingChannel] = mapped_column(
        SAEnum(ShopPingChannel, name="shop_ping_channel"), nullable=False
    )
    details: Mapped[dict | None] = mapped_column(db.JSON)

    pick_list: Mapped[PickList | None] = relationship(back_populates="pings")
    reorder: Mapped["Reorder | None"] = relationship(back_populates="pings")


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
    # Who AEI buys this product from (engine v2, D72: AEI resells — e.g.
    # "AE MFG"). Plain text at <50 SKUs; a Vendor table only if/when POs go
    # out by email. NULL = Chip hasn't filled it in yet.
    vendor: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class StockItem(TimestampMixin, db.Model):
    """Per-product stock record (CP-5a, design Stage F).

    Identity is ProductCatalog.id — the surrogate, rename-safe key (the
    product-identity research: part numbers and descriptions are mutable
    text; the catalog row is the stable thing). catalog_id is UNIQUE: one
    stock record per catalog product, ever.

    on_hand starts at 0 and is ALWAYS derivable from the movement ledger
    (sum of qty_delta) — verify_stock_integrity() enforces this. It may go
    negative before CP-5b's initial import seeds real counts; that is honest
    ledger state, badged in the UI, never hidden.

    min_qty/max_qty/reorder_qty NULL means UNSEEDED: the values arrive with
    Chip's answer to D68 (CP-5b). No reorder logic may fire while any of
    them is NULL — needs_reorder is the single seam CP-5b plugs into and it
    hard-returns False on NULLs.
    """

    __tablename__ = "stock_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    catalog_id: Mapped[int] = mapped_column(
        ForeignKey("product_catalog.id"), nullable=False, unique=True, index=True
    )
    on_hand: Mapped[int] = mapped_column(default=0, nullable=False)
    min_qty: Mapped[int | None]
    max_qty: Mapped[int | None]
    reorder_qty: Mapped[int | None]
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    catalog: Mapped[ProductCatalog] = relationship()
    movements: Mapped[list["StockMovement"]] = relationship(
        back_populates="stock_item",
        foreign_keys="StockMovement.stock_item_id",
        order_by="StockMovement.id",
    )

    @property
    def is_seeded(self) -> bool:
        """True only when Chip's min/max answer has been applied (CP-5b)."""
        return self.min_qty is not None and self.max_qty is not None

    @property
    def is_non_stocked(self) -> bool:
        """min = max = 0 is a VALID SEEDED state meaning "never stock this"
        (engine v2 §3, I148.2) — distinct from NULL/NULL unseeded. These
        items replenish per customer order (the order-triggered vendor PO at
        pick-list generation), never by the min threshold."""
        return self.min_qty == 0 and self.max_qty == 0

    @property
    def needs_reorder(self) -> bool:
        """THE CP-5b reorder seam. Unseeded thresholds can never fire, and
        neither can never-stock (0/0) items — their vendor PO fired at
        pick-list generation, and the shipment decrement that later drives
        on_hand negative must not stack a second, min-triggered one."""
        if not self.is_seeded or self.is_non_stocked:
            return False
        return self.on_hand <= self.min_qty


class StockMovement(db.Model):
    """Append-only ledger: every on_hand change is a row (CP-5a).

    Rows are never edited or deleted. qty_delta is signed; resulting_on_hand
    is the item's on_hand immediately after applying it, so the ledger is
    self-auditing (verify_stock_integrity checks both the running chain and
    the final sum).

    UNMATCHED_SHIPMENT rows are the triage queue: stock_item_id is NULL,
    qty_delta is 0, and details carries the frozen pick line verbatim.
    Resolution assigns a stock item and writes the real SHIPMENT_DECREMENT
    row (resolution_movement_id points at it); the unmatched row itself is
    never mutated into a decrement.

    SHIPMENT_DECREMENT details record WHICH matcher pass produced the match
    ("matched_by": part_number | type_description) — pass-2 matches are the
    weaker guess and render with a low-confidence marker so a human can
    audit them.
    """

    __tablename__ = "stock_movement"
    __table_args__ = (
        db.CheckConstraint(
            "(movement_type = 'UNMATCHED_SHIPMENT') = (stock_item_id IS NULL)",
            name="ck_stock_movement_unmatched_has_no_item",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_item.id"), nullable=True, index=True
    )
    movement_type: Mapped[StockMovementType] = mapped_column(
        SAEnum(StockMovementType, name="stock_movement_type"),
        nullable=False,
        index=True,
    )
    qty_delta: Mapped[int] = mapped_column(nullable=False)
    resulting_on_hand: Mapped[int | None]
    pick_list_id: Mapped[int | None] = mapped_column(
        ForeignKey("pick_list.id"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    reason: Mapped[str | None]
    details: Mapped[dict | None] = mapped_column(db.JSON)
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    resolution_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_movement.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, nullable=False
    )

    stock_item: Mapped[StockItem | None] = relationship(
        back_populates="movements", foreign_keys=[stock_item_id]
    )
    pick_list: Mapped[PickList | None] = relationship()


class StockDecrementClaim(TimestampMixin, db.Model):
    """Claim-in-transaction guard for the shipped-event decrement (§12.1).

    pick_list_id UNIQUE: the first consumer of a pick list's shipped event
    inserts the claim inside the same transaction as its movements; a
    replayed/double-fired shipped event hits the constraint and applies
    NOTHING. Phantom decrement -> phantom reorder is THE Stage-F hazard.
    """

    __tablename__ = "stock_decrement_claim"

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_list_id: Mapped[int] = mapped_column(
        ForeignKey("pick_list.id"), nullable=False, unique=True, index=True
    )


class OrderVendorPoClaim(TimestampMixin, db.Model):
    """Claim-in-transaction guard for the order-triggered vendor PO (§12.1,
    task 444): one PO per pick-list line, ever.

    line_index is the line's position in PickList.lines_snapshot (not
    snapshot_line_id, which legacy snapshots may lack) — always present,
    and stable because a pick list is never edited or regenerated. The
    emitter inserts the claim inside the pick-list-creation transaction; a
    replayed generate hits the UNIQUE and emits nothing.
    """

    __tablename__ = "order_vendor_po_claim"
    __table_args__ = (
        db.UniqueConstraint(
            "pick_list_id", "line_index", name="uq_order_vendor_po_claim_line"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_list_id: Mapped[int] = mapped_column(
        ForeignKey("pick_list.id"), nullable=False, index=True
    )
    line_index: Mapped[int] = mapped_column(nullable=False)


class Reorder(TimestampMixin, db.Model):
    """An auto-reorder: AEI's purchase order to a vendor for more of a stock
    item (CP-5b + engine v2, D72: AEI is a distributor — replenishment is a
    vendor PO, never an in-house make).

    Created when a ledger write drops a FULLY SEEDED item's on_hand to/below
    min_qty (StockItem.needs_reorder — NULL thresholds can never fire).
    Idempotency is the claim pattern the design mandates: a partial UNIQUE
    index on stock_item_id WHERE status IN ('OPEN','SENT') AND order_id IS
    NULL means at most one un-received MIN-TRIGGERED reorder per item, ever —
    a second trigger while one is open OR in flight to the vendor hits the
    constraint and is a no-op (a PO already sent tops the item back up; a
    stock drop mid-transit must not double-order). Phantom decrement ->
    phantom reorder is THE Stage-F hazard; this is the second gate behind
    CP-5a's decrement claim.

    ORDER-TRIGGERED rows (engine v2 §3, task 444) carry order_id +
    customer_context: the vendor PO for a never-stock (min/max 0) item,
    emitted at pick-list generation with the customer's details frozen on
    it. These are per-customer-order — two customers ordering the same 0/0
    part must EACH get a PO, so the partial index deliberately excludes
    them; their idempotency guard is OrderVendorPoClaim, UNIQUE per
    (pick_list, line).

    qty, the *_at_trigger columns, and vendor_at_trigger are FROZEN at
    creation so the printable sheet cannot change under later threshold or
    catalog edits. qty rule (documented for the shop): reorder_qty if set,
    else max(max_qty - on_hand, 1) — the fallback tops the item back up to
    max. vendor_at_trigger may be NULL — the sheet renders "Order {qty}"
    with the vendor blank until Chip fills catalog vendors in.

    Mark-sent stamps sent_* and moves OPEN -> SENT (Chip printed/sent the PO).
    Closing = mark-received (allowed from OPEN or SENT — receipt implies
    sent): books a RECEIPT movement for the qty ACTUALLY delivered (may
    differ from qty ordered; no partial tracking) and stamps received_*. If
    the receipt leaves the item still at/below min, the normal trigger fires
    a FRESH reorder — that is the re-arm semantics.
    """

    __tablename__ = "reorder"
    __table_args__ = (
        db.Index(
            "uq_reorder_open_per_item",
            "stock_item_id",
            unique=True,
            postgresql_where=db.text(
                "status IN ('OPEN', 'SENT') AND order_id IS NULL"
            ),
            sqlite_where=db.text("status IN ('OPEN', 'SENT') AND order_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_item_id: Mapped[int] = mapped_column(
        ForeignKey("stock_item.id"), nullable=False, index=True
    )
    status: Mapped[ReorderStatus] = mapped_column(
        SAEnum(ReorderStatus, name="reorder_status"),
        default=ReorderStatus.OPEN,
        nullable=False,
        index=True,
    )
    qty: Mapped[int] = mapped_column(nullable=False)
    on_hand_at_trigger: Mapped[int] = mapped_column(nullable=False)
    min_qty_at_trigger: Mapped[int] = mapped_column(nullable=False)
    max_qty_at_trigger: Mapped[int] = mapped_column(nullable=False)
    vendor_at_trigger: Mapped[str | None]
    # Order-triggered vendor POs only (task 444): the customer order this PO
    # exists for, plus the customer details frozen at emission (customer
    # name, PO/AFE, ship-to, quote number, the line's notes/specs) — the
    # sheet renders ONLY this, so it cannot change under later quote edits.
    # Both NULL on min-triggered reorders.
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_order.id"), nullable=True, index=True
    )
    customer_context: Mapped[dict | None] = mapped_column(db.JSON)
    sent_at: Mapped[datetime | None]
    sent_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    trigger_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_movement.id"), nullable=True
    )
    reorder_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_movement.id"), nullable=True
    )
    received_at: Mapped[datetime | None]
    received_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    receipt_movement_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_movement.id"), nullable=True
    )

    stock_item: Mapped[StockItem] = relationship()
    order: Mapped["Order | None"] = relationship()
    trigger_movement: Mapped["StockMovement | None"] = relationship(
        foreign_keys=[trigger_movement_id]
    )
    reorder_movement: Mapped["StockMovement | None"] = relationship(
        foreign_keys=[reorder_movement_id]
    )
    receipt_movement: Mapped["StockMovement | None"] = relationship(
        foreign_keys=[receipt_movement_id]
    )
    pings: Mapped[list["ShopPing"]] = relationship(back_populates="reorder")


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
