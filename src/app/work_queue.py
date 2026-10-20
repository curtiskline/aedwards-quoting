"""Work-queue home (T514 slice 2, from the T485 mockup).

Every row is derived from a state the backend actually enforces — a quote
status, an unresolved failed intake, an order without its pick list, a pick
list stage, an open or sent vendor reorder, an unresolved unmatched shipment.
Nothing here invents ownership: "waiting on" names the stage's team (sales,
shop, shipping, customer, vendor), never an unenforced per-person assignment.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import joinedload

from .extensions import db
from .models import (
    FailedIntake,
    Order,
    OrderStatus,
    PickList,
    PickListStatus,
    Quote,
    QuoteStatus,
    QuoteVersion,
    Reorder,
    ReorderStatus,
    StockMovement,
    StockMovementType,
)

# Tab groups. "us" is every action taken inside the app by our side —
# sales, shop, shipping and purchasing rows all land there, each labeled
# with its stage's team in the "waiting on" column.
GROUP_US = "us"
GROUP_CUSTOMER = "customer"
GROUP_VENDOR = "vendor"


def humanize_age(moment: datetime | None, now: datetime | None = None) -> str:
    """Plain age: '3 minutes', '5 hours', '2 days'. Never clever."""
    if moment is None:
        return ""
    now = now or datetime.utcnow()
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 3600:
        minutes = max(1, seconds // 60)
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'' if hours == 1 else 's'}"
    days = seconds // 86400
    return f"{days} day{'' if days == 1 else 's'}"


def _row(
    *,
    group: str,
    team: str,
    record: str,
    sub: str,
    action: str,
    url: str,
    at: datetime | None,
    badge: str | None = None,
    badge_kind: str = "",
) -> dict:
    return {
        "group": group,
        "team": team,
        "record": record,
        "sub": sub,
        "action": action,
        "url": url,
        "at": at,
        "age": humanize_age(at),
        "badge": badge,
        "badge_kind": badge_kind,
    }


def _quote_place(quote: Quote) -> str:
    ship_to = quote.ship_to_json or {}
    city = (ship_to.get("city") or "").strip()
    state = (ship_to.get("state") or "").strip()
    if city and state:
        return f"{city}, {state}"
    return city or state or ""


def _quote_record(quote: Quote) -> tuple[str, str]:
    name = quote.customer_name_raw or quote.sender_name or quote.contact_email or "Unknown customer"
    sub_bits = [bit for bit in (_quote_place(quote), quote.project_name) if bit]
    return f"{quote.quote_number} · {name}", " · ".join(sub_bits)


_QUOTE_ACTIONS = {
    QuoteStatus.NEW: ("Read the new request", "New request", ""),
    QuoteStatus.IN_REVIEW: ("Finish the review", None, ""),
    QuoteStatus.NEEDS_PRICING: ("Price the unpriced lines", "Has unpriced lines", "red"),
    QuoteStatus.READY: ("Review and send", "All lines priced", "green"),
}


def _quote_rows() -> list[dict]:
    quotes = (
        db.session.query(Quote)
        .filter(
            Quote.deleted_at.is_(None),
            Quote.status.in_(list(_QUOTE_ACTIONS)),
        )
        .order_by(Quote.updated_at.asc())
        .all()
    )
    rows = []
    for quote in quotes:
        action, badge, kind = _QUOTE_ACTIONS[quote.status]
        record, sub = _quote_record(quote)
        at = quote.created_at if quote.status == QuoteStatus.NEW else quote.updated_at
        rows.append(
            _row(
                group=GROUP_US,
                team="Sales",
                record=record,
                sub=sub,
                action=action,
                url=f"/quotes/{quote.id}",
                at=at,
                badge=badge,
                badge_kind=kind,
            )
        )
    return rows


def _sent_quote_rows() -> list[dict]:
    """SENT quotes with no order yet: the customer owes us a go-ahead."""
    ordered_quote_ids = db.session.query(Order.quote_id)
    quotes = (
        db.session.query(Quote)
        .filter(
            Quote.deleted_at.is_(None),
            Quote.status == QuoteStatus.SENT,
            ~Quote.id.in_(ordered_quote_ids),
        )
        .all()
    )
    rows = []
    for quote in quotes:
        last_sent = (
            db.session.query(QuoteVersion.sent_at)
            .filter(
                QuoteVersion.quote_id == quote.id,
                QuoteVersion.sent_at.isnot(None),
            )
            .order_by(QuoteVersion.sent_at.desc())
            .limit(1)
            .scalar()
        )
        record, sub = _quote_record(quote)
        rows.append(
            _row(
                group=GROUP_CUSTOMER,
                team="Customer",
                record=record,
                sub=sub,
                action="Waiting for the customer's go-ahead",
                url=f"/quotes/{quote.id}",
                at=last_sent or quote.updated_at,
            )
        )
    return rows


def _failed_intake_rows() -> list[dict]:
    intakes = (
        db.session.query(FailedIntake)
        .filter(FailedIntake.resolved_at.is_(None))
        .order_by(FailedIntake.received_at.asc())
        .all()
    )
    rows = []
    for intake in intakes:
        sender = intake.sender_name or intake.sender_email or "unknown sender"
        rows.append(
            _row(
                group=GROUP_US,
                team="Sales",
                record=f"Intake {intake.id} · {sender}",
                sub=intake.subject or "",
                action="Recover the request details",
                url=f"/admin/failed-intakes/{intake.id}",
                at=intake.received_at,
                badge="No quote was created",
                badge_kind="red",
            )
        )
    return rows


def _order_rows() -> list[dict]:
    """Accepted orders with no pick list: the next app action is generating it."""
    orders = (
        db.session.query(Order)
        .options(joinedload(Order.quote))
        .outerjoin(PickList, PickList.order_id == Order.id)
        .filter(
            Order.status == OrderStatus.ACCEPTED,
            PickList.id.is_(None),
        )
        .order_by(Order.accepted_at.asc())
        .all()
    )
    rows = []
    for order in orders:
        record, sub = _quote_record(order.quote)
        rows.append(
            _row(
                group=GROUP_US,
                team="Sales",
                record=record,
                sub=sub,
                action="Generate the pick list",
                url=f"/orders/{order.id}",
                at=order.accepted_at,
                badge="Order accepted",
                badge_kind="green",
            )
        )
    return rows


_PICK_ACTIONS = {
    PickListStatus.QUEUED: ("Pick the order", "Shop"),
    PickListStatus.PICKED: ("Load the order", "Shop"),
    PickListStatus.LOADED: ("Record it as shipped", "Shipping"),
}


def _pick_list_rows() -> list[dict]:
    pick_lists = (
        db.session.query(PickList)
        .options(joinedload(PickList.order).joinedload(Order.quote))
        .filter(PickList.status.in_(list(_PICK_ACTIONS)))
        .order_by(PickList.updated_at.asc())
        .all()
    )
    rows = []
    for pick_list in pick_lists:
        action, team = _PICK_ACTIONS[pick_list.status]
        record, sub = _quote_record(pick_list.order.quote)
        at = {
            PickListStatus.QUEUED: pick_list.created_at,
            PickListStatus.PICKED: pick_list.picked_at,
            PickListStatus.LOADED: pick_list.loaded_at,
        }[pick_list.status] or pick_list.updated_at
        rows.append(
            _row(
                group=GROUP_US,
                team=team,
                record=record,
                sub=sub,
                action=action,
                url=f"/orders/{pick_list.order_id}",
                at=at,
            )
        )
    return rows


def _reorder_label(reorder: Reorder) -> tuple[str, str]:
    catalog = reorder.stock_item.catalog if reorder.stock_item else None
    item = ""
    if catalog is not None:
        item = catalog.part_number or catalog.description or ""
    vendor = reorder.vendor_at_trigger or "vendor not set"
    return f"Vendor order · {reorder.qty} × {item or 'stock item'}", vendor


def _reorder_rows() -> list[dict]:
    reorders = (
        db.session.query(Reorder)
        .filter(Reorder.status.in_([ReorderStatus.OPEN, ReorderStatus.SENT]))
        .order_by(Reorder.created_at.asc())
        .all()
    )
    rows = []
    for reorder in reorders:
        record, vendor = _reorder_label(reorder)
        if reorder.status == ReorderStatus.OPEN:
            rows.append(
                _row(
                    group=GROUP_US,
                    team="Purchasing",
                    record=record,
                    sub=vendor,
                    action="Send the vendor order",
                    url="/stock/reorders/",
                    at=reorder.created_at,
                )
            )
        else:
            rows.append(
                _row(
                    group=GROUP_VENDOR,
                    team="Vendor",
                    record=record,
                    sub=vendor,
                    action=f"Waiting for {vendor} to deliver",
                    url="/stock/reorders/",
                    at=reorder.sent_at or reorder.created_at,
                )
            )
    return rows


def _unmatched_rows() -> list[dict]:
    movements = (
        db.session.query(StockMovement)
        .filter(
            StockMovement.movement_type == StockMovementType.UNMATCHED_SHIPMENT,
            StockMovement.resolved_at.is_(None),
        )
        .order_by(StockMovement.id.asc())
        .all()
    )
    rows = []
    for movement in movements:
        details = movement.details or {}
        line = details.get("line") or details
        description = (
            line.get("description") or line.get("part_number") or "shipped line"
        )
        rows.append(
            _row(
                group=GROUP_US,
                team="Stock",
                record=f"Unmatched shipment · {description}",
                sub="",
                action="Match the shipped line to a stock item",
                url="/stock/unmatched",
                at=movement.created_at,
                badge="Stock count not updated",
                badge_kind="red",
            )
        )
    return rows


def build_work_queue() -> dict:
    """All rows plus the counts the home page shows.

    Returns {"rows": [...newest first...], "counts": {...}}. Newest first so new
    work is visible without scrolling; stale items are already surfaced by the
    age column and the red badges.
    """
    rows = (
        _quote_rows()
        + _failed_intake_rows()
        + _order_rows()
        + _pick_list_rows()
        + _reorder_rows()
        + _unmatched_rows()
        + _sent_quote_rows()
    )
    rows.sort(key=lambda row: row["at"] or datetime.min, reverse=True)

    def count(predicate) -> int:
        return sum(1 for row in rows if predicate(row))

    counts = {
        "us": count(lambda r: r["group"] == GROUP_US),
        "customer": count(lambda r: r["group"] == GROUP_CUSTOMER),
        "vendor": count(lambda r: r["group"] == GROUP_VENDOR),
        "sales": count(lambda r: r["team"] in ("Sales", "Stock", "Purchasing")),
        "shop": count(lambda r: r["team"] == "Shop"),
        "shipping": count(lambda r: r["team"] == "Shipping"),
        "all": len(rows),
    }
    return {"rows": rows, "counts": counts}
