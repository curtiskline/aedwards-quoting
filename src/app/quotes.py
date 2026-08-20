"""Quote queue dashboard routes."""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from .confidence import (
    SIGNAL_LABELS,
    active_send_holds,
    active_trust_tier,
    auto_send_dollar_ceiling,
    auto_send_threshold,
    price_tolerance_pct,
    quote_recommendation,
    sync_quote_confidence,
)
from .extensions import db
from .models import AuditLog, AutoSendClaim, Quote, QuoteStatus, User
from .quote_numbers import generate_quote_number

quotes_bp = Blueprint("quotes", __name__, url_prefix="/quotes")

# Lock timeout: if a reviewer hasn't interacted in this many minutes, release the lock.
REVIEW_LOCK_TIMEOUT_MINUTES = 15


def _new_quote_count() -> int:
    """Count quotes with status NEW — used for nav badge."""
    return db.session.query(func.count(Quote.id)).filter(
        Quote.status == QuoteStatus.NEW,
        Quote.deleted_at.is_(None),
    ).scalar() or 0


def _quote_query(status_filter: str | None, search: str | None):
    """Build the filtered/searched quote query."""
    q = (
        db.session.query(Quote)
        .outerjoin(Quote.line_items)
        .filter(Quote.deleted_at.is_(None))
        .filter(Quote.status != QuoteStatus.REPLACED)
        .group_by(Quote.id)
    )

    if status_filter and status_filter != "all":
        try:
            status_enum = QuoteStatus(status_filter)
            q = q.filter(Quote.status == status_enum)
        except ValueError:
            pass

    if search:
        like = f"%{search}%"
        q = q.filter(
            or_(
                Quote.quote_number.ilike(like),
                Quote.customer_name_raw.ilike(like),
                Quote.sender_name.ilike(like),
                Quote.contact_name.ilike(like),
            )
        )

    return q.order_by(Quote.created_at.desc())


def _enrich_quotes(quotes: list[Quote]) -> list[dict]:
    """Build template-friendly dicts from Quote objects."""
    now = datetime.utcnow()
    holds = active_send_holds()
    tier = active_trust_tier()
    claims_by_quote = {
        c.quote_id: c
        for c in db.session.query(AutoSendClaim)
        .filter(AutoSendClaim.quote_id.in_([q.id for q in quotes]))
        .all()
    } if quotes else {}
    results = []
    for q in quotes:
        claim = claims_by_quote.get(q.id)
        recommendation = quote_recommendation(q, holds=holds, tier=tier)
        confidence = q.confidence
        signals = [
            {"name": name, "label": label, "status": getattr(confidence, name)}
            for name, label in SIGNAL_LABELS.items()
        ] if confidence is not None else []
        item_count = len(q.line_items)
        total = sum(float(li.line_total) for li in q.line_items)
        needs_pricing = any(float(li.unit_price) == 0 for li in q.line_items)

        # Time since received
        delta = now - q.created_at
        if delta.days > 0:
            time_ago = f"{delta.days}d ago"
        elif delta.seconds >= 3600:
            time_ago = f"{delta.seconds // 3600}h ago"
        elif delta.seconds >= 60:
            time_ago = f"{delta.seconds // 60}m ago"
        else:
            time_ago = "just now"

        # Reviewer lock status
        reviewer_name = None
        lock_active = False
        if q.reviewed_by and q.review_started_at:
            timeout = now - timedelta(minutes=REVIEW_LOCK_TIMEOUT_MINUTES)
            if q.review_started_at > timeout:
                lock_active = True
                if q.reviewer:
                    reviewer_name = q.reviewer.name

        results.append({
            "id": q.id,
            "quote_number": q.quote_number,
            "customer_name": q.customer_name_raw or q.sender_name or "Unknown",
            "item_count": item_count,
            "total": f"${total:,.2f}" if not needs_pricing else "Needs Pricing",
            "needs_pricing": needs_pricing,
            "status": q.status.value,
            "status_label": q.status.value.replace("_", " ").title(),
            "reviewer_name": reviewer_name,
            "lock_active": lock_active,
            "time_ago": time_ago,
            "created_at": q.created_at,
            "recommended": recommendation["recommended"],
            "recommendation_reasons": recommendation["reasons"],
            "confidence_pct": (
                round(recommendation["score"] * 100) if recommendation["score"] is not None else None
            ),
            "signals": signals,
            # CP-2c: an auto-send claim whose send never completed (or
            # failed) must be visibly surfaced, never silently stuck.
            "auto_send_status": claim.status if claim else None,
            "auto_send_needs_attention": claim.status in ("claimed", "failed") if claim else False,
        })
    return results


@quotes_bp.get("/")
def queue():
    status_filter = request.args.get("status", "all")
    search = request.args.get("q", "").strip()
    rec_filter = request.args.get("rec", "all")
    if rec_filter not in ("all", "recommended", "not_recommended"):
        rec_filter = "all"
    sort = request.args.get("sort", "newest")
    if sort not in ("newest", "confidence"):
        sort = "newest"

    quotes = _quote_query(status_filter, search or None).all()
    items = _enrich_quotes(quotes)
    # Recommendation is render-time state (holds/tier apply instantly), so the
    # filter/sort operate on the enriched rows, not in SQL.
    if rec_filter == "recommended":
        items = [item for item in items if item["recommended"]]
    elif rec_filter == "not_recommended":
        items = [item for item in items if not item["recommended"]]
    if sort == "confidence":
        items.sort(
            key=lambda item: (
                item["recommended"],
                item["confidence_pct"] if item["confidence_pct"] is not None else -1,
            ),
            reverse=True,
        )
    new_count = _new_quote_count()

    # Status counts for tabs
    status_counts = dict(
        db.session.query(Quote.status, func.count(Quote.id))
        .filter(Quote.deleted_at.is_(None))
        .filter(Quote.status != QuoteStatus.REPLACED)
        .group_by(Quote.status)
        .all()
    )
    total_count = sum(status_counts.values())

    tabs = [
        {"key": "all", "label": "All", "count": total_count},
    ] + [
        {"key": s.value, "label": s.value.replace("_", " ").title(),
         "count": status_counts.get(s, 0)}
        for s in QuoteStatus
        if s != QuoteStatus.REPLACED
    ]

    template = (
        "quotes/_queue_body.html" if request.headers.get("HX-Request") else "quotes/queue.html"
    )
    return render_template(
        template,
        quotes=items,
        tabs=tabs,
        active_status=status_filter,
        search=search,
        new_count=new_count,
        rec_filter=rec_filter,
        sort=sort,
        active_tier=active_trust_tier(),
        # CP-2c: the dashboard surfaces the live dial values (design §4).
        auto_send_dials={
            "threshold": auto_send_threshold(),
            "dollar_ceiling": auto_send_dollar_ceiling(),
            "tolerance_pct": price_tolerance_pct(),
        },
    )


@quotes_bp.get("/badge")
def badge():
    """Return just the badge count HTML for nav polling."""
    count = _new_quote_count()
    if count > 0:
        return f'<span class="badge">{count}</span>'
    return ""


@quotes_bp.post("/")
@login_required
def create():
    """Create a blank quote and redirect to its editor."""
    for _ in range(2):
        try:
            quote_number = generate_quote_number()
            quote = Quote(quote_number=quote_number, status=QuoteStatus.NEW)
            db.session.add(quote)
            db.session.flush()
            db.session.add(AuditLog(quote_id=quote.id, action="created_manually"))
            sync_quote_confidence(quote)
            db.session.commit()
            return redirect(url_for("main.quote_detail", quote_id=quote.id))
        except IntegrityError:
            db.session.rollback()
    return "Failed to generate unique quote number", 500


@quotes_bp.post("/<int:quote_id>/claim")
def claim(quote_id: int):
    """Mark a quote as in_review by current user (team awareness lock)."""
    quote = db.get_or_404(Quote, quote_id)
    if quote.deleted_at is not None:
        abort(404)
    # For now, use user_id=1 as placeholder until auth is wired up
    user_id = request.form.get("user_id", 1, type=int)
    user = db.session.get(User, user_id)

    quote.reviewed_by = user_id
    quote.review_started_at = datetime.utcnow()
    if quote.status == QuoteStatus.NEW:
        quote.status = QuoteStatus.IN_REVIEW
    db.session.commit()

    return jsonify({"ok": True, "reviewer": user.name if user else "Unknown"})


@quotes_bp.post("/<int:quote_id>/release")
def release(quote_id: int):
    """Release the review lock on a quote."""
    quote = db.get_or_404(Quote, quote_id)
    if quote.deleted_at is not None:
        abort(404)
    quote.reviewed_by = None
    quote.review_started_at = None
    db.session.commit()
    return jsonify({"ok": True})
