"""Fulfillment: pick lists + shop ping (CP-4, design Stage E).

The pick list is the shop's instruction for one order. Its lines are
materialized AT CREATION from the frozen ``QuoteVersion.line_items_snapshot``
— never from the live Quote or catalog (§12.8: drift between quoted lines and
picked goods is the primary fulfillment hazard). Pack-unit math (bundle /
pallet counts) is frozen into ``PickList.lines_snapshot`` at the same moment,
so a printed sheet can never change under later pricing-table edits.

One pick list per order, ever (``order_id`` UNIQUE — hazard §12.1). There is
no regenerate path: a botched order is handled at the ORDER level (revise the
quote, accept the revision — the new order gets its own pick list).

Generating the pick list is what advances the Order ACCEPTED -> ORDERED
(replacing CP-3's bare "Mark Ordered"); the SHIPPED transition advances it
ORDERED -> FULFILLED. Both happen in the same transaction as the pick-list
write. ``emit_pick_list_shipped`` is the single hook CP-5's stock decrement
attaches to.
"""

from __future__ import annotations

import math
from datetime import datetime

from flask import Blueprint, abort, render_template, request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from allenedwards.pricing import STANDARD_BUNDLE_PIECES
from allenedwards.ship_to import normalize_ship_to

from .extensions import db
from .models import (
    Order,
    OrderStatus,
    PickList,
    PickListAuditLog,
    PickListStatus,
    PricingTable,
    ShopPing,
    ShopPingChannel,
    User,
)
from .orders import advance_order

fulfillment_bp = Blueprint("fulfillment", __name__)

# Strictly ordered, one step at a time — no skips, no backwards. A one-tap
# shop UI WILL get mis-taps; anything but the single next step is a visible
# no-op, never an error cascade.
PICK_LIST_SEQUENCE = [
    PickListStatus.QUEUED,
    PickListStatus.PICKED,
    PickListStatus.LOADED,
    PickListStatus.SHIPPED,
]

_TRANSITION_STAMPS = {
    PickListStatus.PICKED: ("picked_at", "picked_by"),
    PickListStatus.LOADED: ("loaded_at", "loaded_by"),
    PickListStatus.SHIPPED: ("shipped_at", "shipped_by"),
}


class PickListError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Pick lines: frozen snapshot -> physical pack units
# ---------------------------------------------------------------------------


def _parse_float(raw: object) -> float | None:
    try:
        return float(str(raw))
    except (TypeError, ValueError):
        return None


def _parse_int(raw: object) -> int | None:
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return None


def _bag_pieces_per_pallet(diameter: float | None) -> int:
    """Pallet size for a bag line, looked up ONCE at pick-list creation.

    This is the only catalog read in fulfillment, and its result is frozen
    into lines_snapshot — quantities themselves always come from the version
    snapshot, and later pricing-table edits cannot alter an existing pick
    list. Returns 0 when no row matches (line stays pieces-only).
    """
    if diameter is None:
        return 0
    for row in db.session.query(PricingTable).filter_by(product_type="bag").all():
        key_fields = dict(row.key_fields or {})
        min_size = _parse_float(key_fields.get("pipe_size_min"))
        max_size = _parse_float(key_fields.get("pipe_size_max"))
        if min_size is None or max_size is None:
            continue
        if min_size <= diameter <= max_size:
            return _parse_int(key_fields.get("pieces_per_pallet")) or 0
    return 0


def build_pick_lines(snapshot: list[dict]) -> list[dict]:
    """Derive the shop's pick lines from a frozen line_items_snapshot.

    Input is ONLY the immutable send-time snapshot (plus the one bag-pallet
    catalog read documented above). Non-material lines (shipping) are
    excluded; goods sell in whole pieces on standard lengths (Chip onsite),
    so quantities render as integer pieces plus bundle/pallet counts where
    the standard pack rules apply.
    """
    lines: list[dict] = []
    for raw in snapshot or []:
        product_type = str(raw.get("product_type") or "")
        if product_type == "shipping":
            continue
        specs = dict(raw.get("specs_json") or {})
        pieces = int(math.ceil(_parse_float(raw.get("quantity")) or 0))
        line: dict = {
            "snapshot_line_id": raw.get("id"),
            "product_type": product_type,
            "part_number": raw.get("part_number"),
            "description": raw.get("description") or "",
            "pieces": pieces,
            "requested_qty": _parse_int(specs.get("original_qty")),
            "pack_unit": None,
            "pack_count": None,
            "pieces_per_pack": None,
        }
        diameter = _parse_float(specs.get("diameter"))
        length_ft = _parse_float(specs.get("length_ft"))
        if (
            product_type == "sleeve"
            and diameter is not None
            and diameter <= 24
            and length_ft == 10
            and pieces > 0
            and pieces % STANDARD_BUNDLE_PIECES == 0
        ):
            line.update(
                pack_unit="bundle",
                pack_count=pieces // STANDARD_BUNDLE_PIECES,
                pieces_per_pack=STANDARD_BUNDLE_PIECES,
            )
        elif product_type == "bag" and pieces > 0:
            per_pallet = _bag_pieces_per_pallet(diameter)
            if per_pallet > 0 and pieces % per_pallet == 0:
                line.update(
                    pack_unit="pallet",
                    pack_count=pieces // per_pallet,
                    pieces_per_pack=per_pallet,
                )
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Creation (the "Generate pick list" action — replaces bare Mark Ordered)
# ---------------------------------------------------------------------------


def create_pick_list(order: Order, actor: User | None) -> tuple[PickList, bool]:
    """Create the order's pick list and advance it ACCEPTED -> ORDERED,
    atomically. Returns (pick_list, created). Idempotent per order: a replay
    (double-click, concurrent request) lands on the unique order_id claim and
    returns the existing pick list with created=False (hazard §12.1).
    """
    if order.quote_version.line_items_snapshot is None:
        raise PickListError(
            "This order's version predates snapshot archiving — there is no "
            "frozen line record to pick from. Handle this order manually."
        )
    existing = _pick_list_for_order(order.id)
    if existing is not None:
        return existing, False
    if order.status not in (OrderStatus.ACCEPTED, OrderStatus.ORDERED):
        raise PickListError(
            f"Cannot generate a pick list for a {order.status.value} order."
        )

    pick_lines = build_pick_lines(order.quote_version.line_items_snapshot)
    if not pick_lines:
        raise PickListError(
            "The frozen snapshot contains no material lines to pick."
        )
    try:
        pick_list = PickList(
            order_id=order.id,
            status=PickListStatus.QUEUED,
            lines_snapshot=pick_lines,
            created_by=actor.id if actor else None,
        )
        db.session.add(pick_list)
        db.session.flush()
        db.session.add(
            PickListAuditLog(
                pick_list_id=pick_list.id,
                action="queued",
                user_id=actor.id if actor else None,
                details={"order_id": order.id, "line_count": len(pick_lines)},
            )
        )
        # CP-4's shop ping IS the printed sheet + the work-queue indicator.
        db.session.add(
            ShopPing(
                pick_list_id=pick_list.id,
                channel=ShopPingChannel.MANUAL_PRINT,
                details={"order_id": order.id},
            )
        )
        if order.status == OrderStatus.ACCEPTED:
            advance_order(order, OrderStatus.ORDERED, actor)
        db.session.commit()
    except IntegrityError:
        # A concurrent generate won the unique-per-order claim.
        db.session.rollback()
        existing = _pick_list_for_order(order.id)
        if existing is None:  # pragma: no cover - constraint violated by something else
            raise
        return existing, False
    return pick_list, True


def _pick_list_for_order(order_id: int) -> PickList | None:
    return (
        db.session.query(PickList).filter(PickList.order_id == order_id).one_or_none()
    )


def pick_list_for_order(order: Order) -> PickList | None:
    return _pick_list_for_order(order.id)


# ---------------------------------------------------------------------------
# Progression (queued -> picked -> loaded -> shipped)
# ---------------------------------------------------------------------------


def advance_pick_list(
    pick_list: PickList, new_status: PickListStatus, actor: User | None
) -> bool:
    """Apply one strictly-ordered transition. Returns True if the state
    advanced, False for a replay no-op (the requested status is the current
    one or already behind — a double-tap must not error). Skipping ahead or
    moving backwards past a replay raises PickListError. Commits on success;
    the SHIPPED transition also advances the Order to FULFILLED and emits
    the CP-5 hook, in the same transaction.
    """
    current_idx = PICK_LIST_SEQUENCE.index(pick_list.status)
    new_idx = PICK_LIST_SEQUENCE.index(new_status)
    if new_idx <= current_idx:
        # Replay-safe: the state is already there (or past it). No-op.
        return False
    if new_idx != current_idx + 1:
        raise PickListError(
            f"Cannot move a pick list from {pick_list.status.value} straight to "
            f"{new_status.value} — steps go one at a time."
        )
    now = datetime.utcnow()
    pick_list.status = new_status
    stamp_at, stamp_by = _TRANSITION_STAMPS[new_status]
    setattr(pick_list, stamp_at, now)
    setattr(pick_list, stamp_by, actor.id if actor else None)
    db.session.add(
        PickListAuditLog(
            pick_list_id=pick_list.id,
            action=new_status.value,
            user_id=actor.id if actor else None,
            details=None,
        )
    )
    if new_status == PickListStatus.SHIPPED:
        if pick_list.order.status == OrderStatus.ORDERED:
            advance_order(pick_list.order, OrderStatus.FULFILLED, actor)
        emit_pick_list_shipped(pick_list, actor)
    db.session.commit()
    return True


def emit_pick_list_shipped(pick_list: PickList, actor: User | None) -> None:
    """THE stock-decrement hook (CP-5 attaches here — design Stage F).

    Runs inside the SHIPPED transaction, exactly once per pick list (the
    strictly-ordered state machine cannot re-enter SHIPPED). The audit row's
    details carry the frozen pick lines so a decrement consumer has the full
    piece counts without re-reading anything mutable.

    CP-5a's consume_shipped_event is that consumer: it decrements stock in
    this same transaction, guarded by its own UNIQUE-per-pick-list claim so
    even a double-fired event cannot double-decrement.
    """
    from .inventory import consume_shipped_event

    db.session.add(
        PickListAuditLog(
            pick_list_id=pick_list.id,
            action="shipped_event",
            user_id=actor.id if actor else None,
            details={
                "order_id": pick_list.order_id,
                "lines": pick_list.lines_snapshot,
            },
        )
    )
    consume_shipped_event(pick_list, actor)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _current_user() -> User | None:
    from flask_login import current_user

    if current_user.is_authenticated:
        return current_user
    return db.session.query(User).order_by(User.id.asc()).first()


@fulfillment_bp.post("/orders/<int:order_id>/pick-list")
def generate(order_id: int):
    """Generate the order's pick list (htmx: replaces the status panel)."""
    from .orders import _detail_context

    order = db.get_or_404(Order, order_id)
    user = _current_user()
    try:
        _, created = create_pick_list(order, user)
    except PickListError as exc:
        return render_template(
            "orders/_status_panel.html", error=exc.message, **_detail_context(order)
        )
    # _detail_context re-queries, so it already sees the fresh pick list.
    return render_template(
        "orders/_status_panel.html",
        error=None,
        pick_list_created=created,
        **_detail_context(order),
    )


def _enrich(pick_lists: list[PickList]) -> list[dict]:
    results = []
    for pl in pick_lists:
        total_pieces = sum(int(line.get("pieces") or 0) for line in pl.lines_snapshot)
        results.append(
            {
                "id": pl.id,
                "status": pl.status,
                "quote_number": pl.order.quote.quote_number,
                "customer_name": pl.order.quote.customer_name_raw
                or pl.order.quote.sender_name
                or "Unknown",
                "po_number": pl.order.po_number,
                "created_at": pl.created_at,
                "line_count": len(pl.lines_snapshot),
                "total_pieces": total_pieces,
                "next_status": next_pick_list_status(pl),
            }
        )
    return results


def next_pick_list_status(pick_list: PickList) -> PickListStatus | None:
    idx = PICK_LIST_SEQUENCE.index(pick_list.status)
    if idx + 1 < len(PICK_LIST_SEQUENCE):
        return PICK_LIST_SEQUENCE[idx + 1]
    return None


@fulfillment_bp.get("/pick-lists/")
def queue():
    """The shop work queue: pick lists by status, coarse and touch-friendly."""
    status_filter = request.args.get("status", "open")

    q = (
        db.session.query(PickList)
        .options(joinedload(PickList.order).joinedload(Order.quote))
        .order_by(PickList.created_at.asc())
    )
    if status_filter == "open":
        q = q.filter(PickList.status != PickListStatus.SHIPPED)
    elif status_filter != "all":
        try:
            q = q.filter(PickList.status == PickListStatus(status_filter))
        except ValueError:
            status_filter = "open"
            q = q.filter(PickList.status != PickListStatus.SHIPPED)

    status_counts = dict(
        db.session.query(PickList.status, func.count(PickList.id))
        .group_by(PickList.status)
        .all()
    )
    open_count = sum(
        c for s, c in status_counts.items() if s != PickListStatus.SHIPPED
    )
    tabs = (
        [{"key": "open", "label": "Open", "count": open_count}]
        + [
            {"key": s.value, "label": s.value.title(), "count": status_counts.get(s, 0)}
            for s in PICK_LIST_SEQUENCE
        ]
        + [{"key": "all", "label": "All", "count": sum(status_counts.values())}]
    )

    # CP-5b: open auto-reorders surface in the shop queue as their own tab
    # (a link to the reorders screen — reorders are not pick lists).
    from .inventory import open_reorders_query

    open_reorder_count = open_reorders_query().count()

    template = (
        "fulfillment/_queue_body.html"
        if request.headers.get("HX-Request")
        else "fulfillment/queue.html"
    )
    return render_template(
        template,
        pick_lists=_enrich(q.all()),
        tabs=tabs,
        active_status=status_filter,
        open_reorder_count=open_reorder_count,
    )


@fulfillment_bp.post("/pick-lists/<int:pick_list_id>/status")
def transition(pick_list_id: int):
    """One-tap progression from the shop queue (htmx: replaces the row)."""
    pick_list = db.get_or_404(PickList, pick_list_id)
    user = _current_user()
    raw = (request.form.get("status") or "").strip()
    try:
        new_status = PickListStatus(raw)
    except ValueError:
        abort(400, description=f"Unknown pick list status: {raw!r}")
    notice = None
    try:
        advanced = advance_pick_list(pick_list, new_status, user)
        if not advanced:
            notice = (
                f"Already {pick_list.status.value} — nothing changed."
            )
    except PickListError as exc:
        notice = exc.message
    return render_template(
        "fulfillment/_queue_row.html",
        row=_enrich([pick_list])[0],
        notice=notice,
    )


@fulfillment_bp.get("/pick-lists/<int:pick_list_id>/sheet")
def sheet(pick_list_id: int):
    """The printable pick sheet — the paper copy the truck drivers need."""
    pick_list = db.get_or_404(PickList, pick_list_id)
    order = pick_list.order
    quote = order.quote
    return render_template(
        "fulfillment/sheet.html",
        pick_list=pick_list,
        order=order,
        quote=quote,
        ship_to=normalize_ship_to(quote.ship_to_json),
        lines=pick_list.lines_snapshot,
        total_pieces=sum(
            int(line.get("pieces") or 0) for line in pick_list.lines_snapshot
        ),
    )
