"""Orders: quote→order transition and order views (CP-3, design Stage D).

The acceptance seam lives here. Every acceptance signal — today's explicit
human click, future reply-parsing or PO-email detection — funnels through
``create_order_from_acceptance``: one AcceptanceEvent + one Order committed
in a single transaction, idempotent per QuoteVersion. Orders are built FROM
the immutable ``QuoteVersion.line_items_snapshot`` (§12.8/§13); nothing here
reads pricing off the mutable Quote or writes to the version row.
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, render_template, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from .extensions import db
from .models import (
    AcceptanceEvent,
    AcceptanceSource,
    AuditLog,
    Order,
    OrderAuditLog,
    OrderStatus,
    PickList,
    Quote,
    QuoteStatus,
    QuoteVersion,
    User,
)

orders_bp = Blueprint("orders", __name__)

# The order state machine. ORDERED -> FULFILLED is defined here so the
# machine is complete, but the web route refuses it until CP-4's pick/ship
# flow exists to drive it.
ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.ACCEPTED: {OrderStatus.ORDERED},
    OrderStatus.ORDERED: {OrderStatus.FULFILLED},
    OrderStatus.FULFILLED: set(),
}

# Per-transition provenance columns on Order (timestamps + who authorized).
_TRANSITION_STAMPS = {
    OrderStatus.ORDERED: ("ordered_at", "ordered_by"),
    OrderStatus.FULFILLED: ("fulfilled_at", "fulfilled_by"),
}


class AcceptanceError(Exception):
    """An acceptance was refused (stale version, wrong status, ...)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidTransition(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def acceptable_version(quote: Quote) -> QuoteVersion:
    """Return the one QuoteVersion of this quote a customer can accept.

    Accept binds to the exact immutable record the customer saw, so only the
    LATEST sent version of a still-SENT quote qualifies:
    - a REPLACED (revised) quote is never acceptable — the newer revision is
      what the customer holds now;
    - if the same quote was re-sent, only the highest version_number counts.
    Raises AcceptanceError with a human-readable reason otherwise.
    """
    if quote.deleted_at is not None:
        raise AcceptanceError("This quote was deleted.")
    if quote.status == QuoteStatus.REPLACED:
        successor = quote.replaced_by
        raise AcceptanceError(
            "This quote was replaced by revision "
            f"{successor.quote_number if successor else '(unknown)'} — "
            "accept the current revision instead."
        )
    if quote.status != QuoteStatus.SENT:
        raise AcceptanceError(
            f"Only a sent quote can be accepted (status is {quote.status.value})."
        )
    versions = sorted(quote.versions, key=lambda v: v.version_number)
    if not versions:
        raise AcceptanceError("This quote has no send record to accept.")
    return versions[-1]


def create_order_from_acceptance(
    version: QuoteVersion,
    *,
    source: AcceptanceSource,
    actor: User | None = None,
    actor_label: str | None = None,
    note: str | None = None,
    po_number: str | None = None,
) -> tuple[Order, bool]:
    """Record an acceptance and create its Order atomically. Returns
    (order, created). Idempotent per QuoteVersion: a duplicate signal —
    double-click, replayed webhook, concurrent request — lands on the unique
    constraint, rolls back, and returns the existing order with created=False
    (claim-in-transaction, hazard §12.1).

    This is the pluggable seam: future signals (reply_parse, po_received)
    call this same function with their own source/actor_label.
    """
    quote = version.quote
    latest = acceptable_version(quote)
    if latest.id != version.id:
        raise AcceptanceError(
            f"Version {version.version_number} is stale — version "
            f"{latest.version_number} was sent after it. Accept the latest version."
        )

    existing = _order_for_version(version.id)
    if existing is not None:
        return existing, False

    now = datetime.utcnow()
    event = AcceptanceEvent(
        quote_version_id=version.id,
        source=source,
        actor_user_id=actor.id if actor else None,
        actor_label=actor_label,
        note=note or None,
        po_number=po_number or None,
    )
    try:
        db.session.add(event)
        db.session.flush()
        order = Order(
            quote_version_id=version.id,
            quote_id=quote.id,
            acceptance_event_id=event.id,
            status=OrderStatus.ACCEPTED,
            po_number=po_number or None,
            accepted_at=now,
            accepted_by=actor.id if actor else None,
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(
            OrderAuditLog(
                order_id=order.id,
                action="accepted",
                user_id=actor.id if actor else None,
                details={
                    "source": source.value,
                    "quote_version": version.version_number,
                    "po_number": po_number or None,
                },
            )
        )
        db.session.add(
            AuditLog(
                quote_id=quote.id,
                action="accepted",
                user_id=actor.id if actor else None,
                details={"order_id": order.id, "quote_version": version.version_number},
            )
        )
        db.session.commit()
    except IntegrityError:
        # A concurrent acceptance won the unique-per-version claim.
        db.session.rollback()
        existing = _order_for_version(version.id)
        if existing is None:  # pragma: no cover - constraint violated by something else
            raise
        return existing, False
    return order, True


def advance_order(order: Order, new_status: OrderStatus, actor: User | None) -> None:
    """Apply one state-machine transition with provenance + audit row.

    Does not commit — the caller owns the transaction.
    """
    if new_status not in ORDER_TRANSITIONS.get(order.status, set()):
        raise InvalidTransition(
            f"Cannot move an order from {order.status.value} to {new_status.value}."
        )
    now = datetime.utcnow()
    order.status = new_status
    stamp_at, stamp_by = _TRANSITION_STAMPS[new_status]
    setattr(order, stamp_at, now)
    setattr(order, stamp_by, actor.id if actor else None)
    db.session.add(
        OrderAuditLog(
            order_id=order.id,
            action=new_status.value,
            user_id=actor.id if actor else None,
            details=None,
        )
    )


def _order_for_version(quote_version_id: int) -> Order | None:
    return (
        db.session.query(Order)
        .filter(Order.quote_version_id == quote_version_id)
        .one_or_none()
    )


def order_for_quote(quote: Quote) -> Order | None:
    """The order hanging off any version of this quote, if one exists."""
    return (
        db.session.query(Order)
        .join(QuoteVersion, Order.quote_version_id == QuoteVersion.id)
        .filter(QuoteVersion.quote_id == quote.id)
        .order_by(Order.id.desc())
        .first()
    )


def _current_user() -> User | None:
    from flask_login import current_user

    if current_user.is_authenticated:
        return current_user
    return db.session.query(User).order_by(User.id.asc()).first()


# ---------------------------------------------------------------------------
# Accept action (lives on the quote, creates the order)
# ---------------------------------------------------------------------------


def _get_quote_or_404(quote_id: int) -> Quote:
    quote = db.get_or_404(Quote, quote_id)
    if quote.deleted_at is not None:
        abort(404)
    return quote


@orders_bp.get("/quotes/<int:quote_id>/accept-form")
def quote_accept_form(quote_id: int):
    """HTMX modal: confirm acceptance, showing exactly which version binds."""
    quote = _get_quote_or_404(quote_id)
    try:
        version = acceptable_version(quote)
    except AcceptanceError as exc:
        return render_template(
            "orders/_accept_result.html", success=False, error=exc.message, quote=quote
        )
    existing = _order_for_version(version.id)
    return render_template(
        "orders/_accept_form.html",
        quote=quote,
        version=version,
        existing_order=existing,
    )


@orders_bp.post("/quotes/<int:quote_id>/accept")
def quote_accept(quote_id: int):
    """Explicit human acceptance: AcceptanceEvent + Order, atomically."""
    quote = _get_quote_or_404(quote_id)
    user = _current_user()
    po_number = (request.form.get("po_number") or "").strip()
    note = (request.form.get("note") or "").strip()
    # The form posts the version id it displayed, so a form that sat open
    # across a re-send cannot silently accept a different record.
    form_version_id = request.form.get("quote_version_id", type=int)

    try:
        version = acceptable_version(quote)
        if form_version_id is not None and form_version_id != version.id:
            raise AcceptanceError(
                "A newer version of this quote was sent after this form was "
                "opened. Re-open Accept to see the current version."
            )
        order, created = create_order_from_acceptance(
            version,
            source=AcceptanceSource.EXPLICIT_CLICK,
            actor=user,
            note=note,
            po_number=po_number,
        )
    except AcceptanceError as exc:
        return render_template(
            "orders/_accept_result.html", success=False, error=exc.message, quote=quote
        )

    return render_template(
        "orders/_accept_result.html",
        success=True,
        quote=quote,
        order=order,
        created=created,
        version=version,
    )


# ---------------------------------------------------------------------------
# Order views
# ---------------------------------------------------------------------------


def _enrich_orders(orders: list[Order]) -> list[dict]:
    results = []
    for o in orders:
        snapshot = o.quote_version.line_items_snapshot or []
        total = sum(float(line.get("line_total") or 0) for line in snapshot)
        results.append(
            {
                "id": o.id,
                "quote_number": o.quote.quote_number,
                "version_number": o.quote_version.version_number,
                "customer_name": o.quote.customer_name_raw or o.quote.sender_name or "Unknown",
                "status": o.status.value,
                "status_label": o.status.value.title(),
                "po_number": o.po_number,
                "accepted_at": o.accepted_at,
                "item_count": len(snapshot),
                "total": f"${total:,.2f}",
                "snapshot_missing": o.quote_version.line_items_snapshot is None,
            }
        )
    return results


@orders_bp.get("/orders/")
def queue():
    status_filter = request.args.get("status", "all")

    q = (
        db.session.query(Order)
        .options(
            joinedload(Order.quote),
            joinedload(Order.quote_version),
        )
        .order_by(Order.accepted_at.desc())
    )
    if status_filter != "all":
        try:
            q = q.filter(Order.status == OrderStatus(status_filter))
        except ValueError:
            status_filter = "all"

    orders = q.all()

    status_counts = dict(
        db.session.query(Order.status, func.count(Order.id)).group_by(Order.status).all()
    )
    tabs = [{"key": "all", "label": "All", "count": sum(status_counts.values())}] + [
        {"key": s.value, "label": s.value.title(), "count": status_counts.get(s, 0)}
        for s in OrderStatus
    ]

    template = (
        "orders/_queue_body.html" if request.headers.get("HX-Request") else "orders/queue.html"
    )
    return render_template(
        template,
        orders=_enrich_orders(orders),
        tabs=tabs,
        active_status=status_filter,
    )


@orders_bp.get("/orders/<int:order_id>")
def detail(order_id: int):
    order = db.get_or_404(Order, order_id)
    return render_template("orders/detail.html", **_detail_context(order))


def _detail_context(order: Order) -> dict:
    snapshot = order.quote_version.line_items_snapshot
    lines = snapshot or []
    subtotal = sum(
        float(line.get("line_total") or 0)
        for line in lines
        if (line.get("product_type") or "") != "shipping"
    )
    shipping = sum(
        float(line.get("line_total") or 0)
        for line in lines
        if (line.get("product_type") or "") == "shipping"
    )
    users = {u.id: u.name for u in db.session.query(User).all()}
    next_statuses = sorted(
        (s for s in ORDER_TRANSITIONS.get(order.status, set())),
        key=lambda s: list(OrderStatus).index(s),
    )
    pick_list = (
        db.session.query(PickList).filter(PickList.order_id == order.id).one_or_none()
    )
    return {
        "pick_list": pick_list,
        "order": order,
        "quote": order.quote,
        "version": order.quote_version,
        "event": order.acceptance_event,
        "lines": lines,
        "snapshot_missing": snapshot is None,
        "subtotal": subtotal,
        "shipping": shipping,
        "total": subtotal + shipping,
        "user_names": users,
        # CP-4: generating the pick list IS the ACCEPTED->ORDERED trigger
        # (it replaced CP-3's bare Mark Ordered); SHIPPED drives FULFILLED.
        "can_generate_pick_list": pick_list is None
        and snapshot is not None
        and order.status in (OrderStatus.ACCEPTED, OrderStatus.ORDERED),
        "next_statuses": next_statuses,
        "audit_rows": sorted(order.audit_logs, key=lambda a: (a.created_at, a.id)),
    }


@orders_bp.post("/orders/<int:order_id>/status")
def transition(order_id: int):
    order = db.get_or_404(Order, order_id)
    user = _current_user()
    raw = (request.form.get("status") or "").strip()
    try:
        new_status = OrderStatus(raw)
    except ValueError:
        abort(400, description=f"Unknown order status: {raw!r}")
    if new_status == OrderStatus.FULFILLED:
        # Defined in the machine, but only CP-4's pick/ship flow may drive it.
        return render_template(
            "orders/_status_panel.html",
            error="Fulfillment is driven by the pick/ship flow (CP-4) — not settable by hand yet.",
            **_detail_context(order),
        )
    try:
        advance_order(order, new_status, user)
    except InvalidTransition as exc:
        return render_template(
            "orders/_status_panel.html", error=exc.message, **_detail_context(order)
        )
    db.session.commit()
    return render_template("orders/_status_panel.html", error=None, **_detail_context(order))
