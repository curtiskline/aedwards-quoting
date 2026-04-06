"""Quote queue dashboard routes."""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import func, or_

from .extensions import db
from .models import Quote, QuoteLineItem, QuoteStatus, User

quotes_bp = Blueprint("quotes", __name__, url_prefix="/quotes")

# Lock timeout: if a reviewer hasn't interacted in this many minutes, release the lock.
REVIEW_LOCK_TIMEOUT_MINUTES = 15


def _new_quote_count() -> int:
    """Count quotes with status NEW — used for nav badge."""
    return db.session.query(func.count(Quote.id)).filter(
        Quote.status == QuoteStatus.NEW,
    ).scalar() or 0


def _quote_query(status_filter: str | None, search: str | None):
    """Build the filtered/searched quote query."""
    q = (
        db.session.query(Quote)
        .outerjoin(Quote.line_items)
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
    results = []
    for q in quotes:
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
        })
    return results


@quotes_bp.get("/")
def queue():
    status_filter = request.args.get("status", "all")
    search = request.args.get("q", "").strip()

    quotes = _quote_query(status_filter, search or None).all()
    items = _enrich_quotes(quotes)
    new_count = _new_quote_count()

    # Status counts for tabs
    status_counts = dict(
        db.session.query(Quote.status, func.count(Quote.id)).group_by(Quote.status).all()
    )
    total_count = sum(status_counts.values())

    tabs = [
        {"key": "all", "label": "All", "count": total_count},
    ] + [
        {"key": s.value, "label": s.value.replace("_", " ").title(),
         "count": status_counts.get(s, 0)}
        for s in QuoteStatus
    ]

    if request.headers.get("HX-Request"):
        return render_template(
            "quotes/_queue_body.html",
            quotes=items,
            tabs=tabs,
            active_status=status_filter,
            search=search,
            new_count=new_count,
        )

    return render_template(
        "quotes/queue.html",
        quotes=items,
        tabs=tabs,
        active_status=status_filter,
        search=search,
        new_count=new_count,
    )


@quotes_bp.get("/badge")
def badge():
    """Return just the badge count HTML for nav polling."""
    count = _new_quote_count()
    if count > 0:
        return f'<span class="badge">{count}</span>'
    return ""


@quotes_bp.post("/<int:quote_id>/claim")
def claim(quote_id: int):
    """Mark a quote as in_review by current user (team awareness lock)."""
    quote = db.get_or_404(Quote, quote_id)
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
    quote.reviewed_by = None
    quote.review_started_at = None
    db.session.commit()
    return jsonify({"ok": True})
