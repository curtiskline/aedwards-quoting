"""Inventory: stock table + movement ledger + idempotent decrement (CP-5a).

The Chip-independent half of design Stage F. What lives here:

- **Identity bridge**: a shipped pick line maps to a StockItem through
  ProductCatalog.id (the surrogate rename-safe key). The matcher is
  DETERMINISTIC — pass 1 matches the line's normalized part_number against
  exactly one active catalog row; pass 2 falls back to normalized
  product_type + description. Zero matches or an ambiguous (>1) match
  produces an UNMATCHED_SHIPMENT ledger row for human triage — NEVER a
  guessed match, NEVER a silent skip. Every decrement records which pass
  matched it; pass-2 matches render with a low-confidence marker.

- **Idempotent decrement**: consume_shipped_event runs inside CP-4's
  SHIPPED transaction (emit_pick_list_shipped is the single hook). A
  stock_decrement_claim row UNIQUE per pick_list (claim-in-transaction,
  hazard §12.1) means a replayed/double-fired shipped event applies
  NOTHING — phantom decrement -> phantom reorder is THE Stage-F hazard.

- **Ledger invariant**: on_hand is always derivable from the movement
  ledger; verify_stock_integrity() checks the running chain and final sum.

What does NOT live here (CP-5b, gated on Chip's D68 answer): min/max
seeding, initial on_hand import, and the auto-reorder trigger. The seam is
StockItem.needs_reorder — it hard-returns False while thresholds are NULL.
See docs/reference/inventory-stock.md.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from .extensions import db
from .models import (
    PickList,
    ProductCatalog,
    Reorder,
    ReorderStatus,
    ShopPing,
    ShopPingChannel,
    StockDecrementClaim,
    StockItem,
    StockMovement,
    StockMovementType,
    User,
)

stock_bp = Blueprint("stock", __name__, url_prefix="/stock")

# details["matched_by"] values on SHIPMENT_DECREMENT rows.
MATCHED_BY_PART_NUMBER = "part_number"
MATCHED_BY_TYPE_DESCRIPTION = "type_description"  # pass-2: low-confidence
MATCHED_BY_MANUAL_RESOLUTION = "manual_resolution"

# Auto-noted reasons on seeding-screen ADJUSTMENT rows (CP-5b). The first
# save that seeds an item is the initial seed; every later edit (count or
# thresholds) is a threshold-change — details carry the old/new values.
REASON_INITIAL_SEED = "initial-seed"
REASON_THRESHOLD_CHANGE = "threshold-change"


class StockError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Identity: frozen pick line -> catalog row (deterministic, never guesses)
# ---------------------------------------------------------------------------


def _norm(raw: object) -> str:
    """Case/whitespace normalization — the ONLY transformation the matcher
    applies. Anything fuzzier than this is a guess, and guesses go to triage."""
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def match_catalog_row(line: dict) -> tuple[ProductCatalog | None, str | None]:
    """Map a frozen pick line to exactly one active catalog row.

    Returns (row, matched_by) or (None, None). Pass 1: normalized
    part_number equals exactly one active row's part_number. An ambiguous
    part number (>1 rows) is a data problem a human must see — straight to
    unmatched, no fallback. Zero part-number hits fall through to pass 2:
    normalized product_type + description equal exactly one active row.
    """
    rows = (
        db.session.query(ProductCatalog)
        .filter(ProductCatalog.is_active.is_(True))
        .all()
    )
    part = _norm(line.get("part_number"))
    if part:
        hits = [r for r in rows if _norm(r.part_number) == part]
        if len(hits) == 1:
            return hits[0], MATCHED_BY_PART_NUMBER
        if len(hits) > 1:
            return None, None
    ptype = _norm(line.get("product_type"))
    desc = _norm(line.get("description"))
    if ptype and desc:
        hits = [
            r
            for r in rows
            if _norm(r.product_type) == ptype and _norm(r.description) == desc
        ]
        if len(hits) == 1:
            return hits[0], MATCHED_BY_TYPE_DESCRIPTION
    return None, None


def get_or_create_stock_item(catalog_row: ProductCatalog) -> StockItem:
    """The stock record for a catalog product, created unseeded (on_hand 0,
    NULL thresholds) on first touch. Identity is the catalog surrogate id."""
    item = (
        db.session.query(StockItem)
        .filter(StockItem.catalog_id == catalog_row.id)
        .one_or_none()
    )
    if item is None:
        item = StockItem(catalog_id=catalog_row.id, on_hand=0)
        db.session.add(item)
        db.session.flush()
    return item


# ---------------------------------------------------------------------------
# The decrement (consumes CP-4's shipped event; idempotent per pick list)
# ---------------------------------------------------------------------------


def consume_shipped_event(pick_list: PickList, actor: User | None) -> bool:
    """Apply the stock decrement for a shipped pick list, exactly once.

    Called from emit_pick_list_shipped INSIDE the SHIPPED transaction — the
    caller commits. First claims pick_list_id in stock_decrement_claim
    (UNIQUE); a replay hits the constraint inside a savepoint and applies
    nothing, returning False. The lines consumed are the frozen
    lines_snapshot — the same payload the shipped_event audit row carries.
    """
    try:
        with db.session.begin_nested():
            db.session.add(StockDecrementClaim(pick_list_id=pick_list.id))
    except IntegrityError:
        return False

    for line in pick_list.lines_snapshot or []:
        pieces = int(line.get("pieces") or 0)
        row, matched_by = match_catalog_row(line)
        if row is None:
            db.session.add(
                StockMovement(
                    stock_item_id=None,
                    movement_type=StockMovementType.UNMATCHED_SHIPMENT,
                    qty_delta=0,
                    resulting_on_hand=None,
                    pick_list_id=pick_list.id,
                    user_id=actor.id if actor else None,
                    details={"line": line, "pieces": pieces},
                )
            )
            continue
        item = get_or_create_stock_item(row)
        item.on_hand -= pieces
        movement = StockMovement(
            stock_item_id=item.id,
            movement_type=StockMovementType.SHIPMENT_DECREMENT,
            qty_delta=-pieces,
            resulting_on_hand=item.on_hand,
            pick_list_id=pick_list.id,
            user_id=actor.id if actor else None,
            details={"matched_by": matched_by, "line": line},
        )
        db.session.add(movement)
        db.session.flush()
        maybe_trigger_reorder(item, movement, actor)
    return True


# ---------------------------------------------------------------------------
# Auto-reorder (CP-5b): the needs_reorder seam fires here, exactly once
# ---------------------------------------------------------------------------


def compute_reorder_qty(item: StockItem) -> int:
    """The documented qty rule: reorder_qty when seeded, else top the item
    back up to max (max_qty - on_hand), floored at 1 so a degenerate
    on_hand == min == max trigger still asks the shop for something."""
    if item.reorder_qty is not None:
        return item.reorder_qty
    return max(item.max_qty - item.on_hand, 1)


def maybe_trigger_reorder(
    item: StockItem, trigger_movement: StockMovement | None, actor: User | None
) -> Reorder | None:
    """Open a reorder if this ledger write left the item at/below min.

    Called after EVERY ledger write (decrement, adjustment, receipt, triage
    resolution, seeding save) — needs_reorder hard-returns False on NULL
    thresholds, so unseeded items can never fire (CP-5a regression).

    Idempotent by the claim pattern: the partial UNIQUE index (one
    un-received — OPEN or SENT — reorder per stock item) is taken inside a
    savepoint; a second trigger while one is open or in flight hits the
    constraint and is a no-op returning None. This is the second gate behind
    CP-5a's decrement claim — a replayed shipped event decrements nothing,
    and even a real second decrement cannot stack a second reorder.
    """
    if not item.needs_reorder:
        return None
    qty = compute_reorder_qty(item)
    reorder = Reorder(
        stock_item_id=item.id,
        status=ReorderStatus.OPEN,
        qty=qty,
        on_hand_at_trigger=item.on_hand,
        min_qty_at_trigger=item.min_qty,
        max_qty_at_trigger=item.max_qty,
        # Frozen like min/max: the sheet must not change under later catalog
        # edits. NULL vendor is fine — the sheet renders "Order {qty}" with
        # the vendor line blank (D72; data arrives as Chip fills it in).
        vendor_at_trigger=item.catalog.vendor or None,
        trigger_movement_id=trigger_movement.id if trigger_movement else None,
    )
    try:
        with db.session.begin_nested():
            db.session.add(reorder)
    except IntegrityError:
        return None

    movement = StockMovement(
        stock_item_id=item.id,
        movement_type=StockMovementType.REORDER,
        qty_delta=0,
        resulting_on_hand=item.on_hand,
        user_id=actor.id if actor else None,
        reason=f"auto-reorder: on hand {item.on_hand} at/below min {item.min_qty}",
        details={
            "reorder_id": reorder.id,
            "qty": qty,
            "on_hand": item.on_hand,
            "min_qty": item.min_qty,
            "max_qty": item.max_qty,
            "vendor": reorder.vendor_at_trigger,
        },
    )
    db.session.add(movement)
    db.session.flush()
    reorder.reorder_movement_id = movement.id
    # The shop ping through CP-4's real channel: MANUAL_PRINT — the printed
    # restock sheet plus the shop-queue indicator. NO outbound anything.
    db.session.add(
        ShopPing(
            reorder_id=reorder.id,
            channel=ShopPingChannel.MANUAL_PRINT,
            details={"stock_item_id": item.id, "qty": qty},
        )
    )
    return reorder


def mark_reorder_sent(reorder: Reorder, actor: User | None) -> None:
    """OPEN -> SENT: the PO went to the vendor (Chip printed/sent it).

    Stamps sent_at/sent_by. Only an OPEN reorder can be marked sent — a
    RECEIVED one is done, and a second mark-sent is refused rather than
    silently re-stamping the date the vendor actually got the order.
    Does not commit — the caller owns the transaction.
    """
    if reorder.status != ReorderStatus.OPEN:
        raise StockError(
            f"Only an open reorder can be marked sent (this one is "
            f"{reorder.status.value})."
        )
    reorder.status = ReorderStatus.SENT
    reorder.sent_at = datetime.utcnow()
    reorder.sent_by = actor.id if actor else None


def close_reorder(
    reorder: Reorder, received_qty: int, actor: User | None
) -> tuple[StockMovement | None, Reorder | None]:
    """Mark-received: book what the vendor ACTUALLY delivered and close the
    reorder.

    Allowed from OPEN or SENT — receipt implies sent (goods can arrive
    without mark-sent ever being clicked; PM agreement, task 439).
    received_qty may differ from the ordered qty — no partial tracking (PM
    agreement, task 419). A qty of 0 closes the reorder without a receipt
    (the false-fire escape hatch). If the receipt leaves the item still
    at/below min, the normal trigger fires a FRESH reorder in the same
    transaction — that is the re-arm semantics.

    Returns (receipt_movement, new_reorder) — either may be None.
    """
    if reorder.status == ReorderStatus.RECEIVED:
        raise StockError("This reorder is already received.")
    if received_qty < 0:
        raise StockError("Received quantity cannot be negative.")
    reorder.status = ReorderStatus.RECEIVED
    reorder.received_at = datetime.utcnow()
    reorder.received_by = actor.id if actor else None
    item = reorder.stock_item
    movement = None
    if received_qty > 0:
        item.on_hand += received_qty
        movement = StockMovement(
            stock_item_id=item.id,
            movement_type=StockMovementType.RECEIPT,
            qty_delta=received_qty,
            resulting_on_hand=item.on_hand,
            user_id=actor.id if actor else None,
            reason=f"reorder #{reorder.id} received",
            details={
                "reorder_id": reorder.id,
                "qty_ordered": reorder.qty,
                "qty_received": received_qty,
            },
        )
        db.session.add(movement)
    # Flush the status flip BEFORE the re-trigger claim: the partial unique
    # index must see this reorder closed, or the fresh OPEN row would collide.
    db.session.flush()
    if movement is not None:
        reorder.receipt_movement_id = movement.id
    new_reorder = maybe_trigger_reorder(item, movement, actor)
    return movement, new_reorder


def open_reorders_query():
    """Un-received reorders (OPEN or SENT) — everything still holding the
    one-active-reorder-per-item claim. SENT rows are in flight to the vendor
    and still need mark-received, so every 'open reorder' surface (badges,
    reorders screen, shop-queue tab) treats them as active."""
    return (
        db.session.query(Reorder)
        .filter(Reorder.status.in_((ReorderStatus.OPEN, ReorderStatus.SENT)))
        .order_by(Reorder.id.asc())
    )


# ---------------------------------------------------------------------------
# Seeding (CP-5b): initial counts + min/max thresholds, sized for <50 SKUs
# ---------------------------------------------------------------------------


def _parse_opt_int(raw: object, label: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise StockError(f"{label} must be a whole number (got {text!r}).")


def seed_stock_row(
    catalog_row: ProductCatalog,
    *,
    on_hand: int,
    min_qty: int | None,
    max_qty: int | None,
    reorder_qty: int | None,
    actor: User | None,
) -> tuple[StockItem, StockMovement | None, Reorder | None]:
    """Save one row of the seeding screen (hand count / spreadsheet entry).

    Creates the stock item on first touch. Every change lands in the ledger:
    the save that first seeds an item writes an ADJUSTMENT with reason
    "initial-seed"; later edits write "threshold-change" (a threshold-only
    edit is a zero-delta ADJUSTMENT — the ledger invariant holds either way,
    and details carry old/new values). A save that leaves the row at/below
    min fires the reorder trigger immediately (spreadsheet says 2 on hand
    with min 5 — the shop really should make more).
    """
    if min_qty is not None and min_qty < 0:
        raise StockError("Min cannot be negative.")
    if max_qty is not None and max_qty < 0:
        raise StockError("Max cannot be negative.")
    if (min_qty is None) != (max_qty is None):
        raise StockError("Min and max come together — set both or neither.")
    if min_qty is not None and max_qty is not None and min_qty > max_qty:
        raise StockError("Min cannot be greater than max.")
    if reorder_qty is not None and reorder_qty < 1:
        raise StockError("Reorder quantity must be at least 1 (or blank).")

    item = get_or_create_stock_item(catalog_row)
    was_seeded = item.is_seeded
    old = {
        "on_hand": item.on_hand,
        "min_qty": item.min_qty,
        "max_qty": item.max_qty,
        "reorder_qty": item.reorder_qty,
    }
    delta = on_hand - item.on_hand
    thresholds_changed = (
        item.min_qty != min_qty
        or item.max_qty != max_qty
        or item.reorder_qty != reorder_qty
    )
    if delta == 0 and not thresholds_changed:
        return item, None, None

    item.on_hand = on_hand
    item.min_qty = min_qty
    item.max_qty = max_qty
    item.reorder_qty = reorder_qty
    movement = StockMovement(
        stock_item_id=item.id,
        movement_type=StockMovementType.ADJUSTMENT,
        qty_delta=delta,
        resulting_on_hand=item.on_hand,
        user_id=actor.id if actor else None,
        reason=REASON_INITIAL_SEED if not was_seeded else REASON_THRESHOLD_CHANGE,
        details={
            "seeding": {
                "old": old,
                "new": {
                    "on_hand": on_hand,
                    "min_qty": min_qty,
                    "max_qty": max_qty,
                    "reorder_qty": reorder_qty,
                },
            }
        },
    )
    db.session.add(movement)
    db.session.flush()
    reorder = maybe_trigger_reorder(item, movement, actor)
    return item, movement, reorder


# ---------------------------------------------------------------------------
# Manual movements (RECEIPT / ADJUSTMENT) + triage resolution
# ---------------------------------------------------------------------------


def record_receipt(
    item: StockItem, qty: int, actor: User | None, reason: str
) -> StockMovement:
    if qty <= 0:
        raise StockError("Receipt quantity must be a positive number of pieces.")
    if not reason.strip():
        raise StockError("A reason is required (e.g. PO number, shop run).")
    item.on_hand += qty
    movement = StockMovement(
        stock_item_id=item.id,
        movement_type=StockMovementType.RECEIPT,
        qty_delta=qty,
        resulting_on_hand=item.on_hand,
        user_id=actor.id if actor else None,
        reason=reason.strip(),
    )
    db.session.add(movement)
    db.session.flush()
    # A receipt that still leaves the item at/below min fires the trigger
    # (no-op while a reorder is already open — the claim absorbs it).
    maybe_trigger_reorder(item, movement, actor)
    return movement


def record_adjustment(
    item: StockItem, qty_delta: int, actor: User | None, reason: str
) -> StockMovement:
    if qty_delta == 0:
        raise StockError("An adjustment must change on-hand (delta cannot be 0).")
    if not reason.strip():
        raise StockError("A reason is required for every adjustment.")
    item.on_hand += qty_delta
    movement = StockMovement(
        stock_item_id=item.id,
        movement_type=StockMovementType.ADJUSTMENT,
        qty_delta=qty_delta,
        resulting_on_hand=item.on_hand,
        user_id=actor.id if actor else None,
        reason=reason.strip(),
    )
    db.session.add(movement)
    db.session.flush()
    maybe_trigger_reorder(item, movement, actor)
    return movement


def resolve_unmatched(
    unmatched: StockMovement, catalog_row: ProductCatalog, actor: User | None
) -> StockMovement:
    """Human triage action: assign an unmatched shipped line to a product and
    apply the decrement it was owed. The unmatched row is never mutated into
    a decrement — it gets resolved_* stamps pointing at the new movement."""
    if unmatched.movement_type != StockMovementType.UNMATCHED_SHIPMENT:
        raise StockError("Only unmatched-shipment rows can be resolved.")
    if unmatched.resolved_at is not None:
        raise StockError("This unmatched shipment is already resolved.")
    details = dict(unmatched.details or {})
    pieces = int(details.get("pieces") or 0)
    item = get_or_create_stock_item(catalog_row)
    item.on_hand -= pieces
    movement = StockMovement(
        stock_item_id=item.id,
        movement_type=StockMovementType.SHIPMENT_DECREMENT,
        qty_delta=-pieces,
        resulting_on_hand=item.on_hand,
        pick_list_id=unmatched.pick_list_id,
        user_id=actor.id if actor else None,
        details={
            "matched_by": MATCHED_BY_MANUAL_RESOLUTION,
            "line": details.get("line"),
            "unmatched_movement_id": unmatched.id,
        },
    )
    db.session.add(movement)
    db.session.flush()
    unmatched.resolved_at = datetime.utcnow()
    unmatched.resolved_by = actor.id if actor else None
    unmatched.resolution_movement_id = movement.id
    maybe_trigger_reorder(item, movement, actor)
    return movement


# ---------------------------------------------------------------------------
# Ledger integrity: on_hand is always derivable from the movements
# ---------------------------------------------------------------------------


def verify_stock_integrity() -> list[str]:
    """Every stock item's on_hand must equal the sum of its movement deltas,
    and every movement's resulting_on_hand must equal the running sum at that
    point. Returns a list of human-readable problems (empty = healthy)."""
    problems: list[str] = []
    items = (
        db.session.query(StockItem)
        .options(joinedload(StockItem.movements))
        .all()
    )
    for item in items:
        running = 0
        for movement in item.movements:
            running += movement.qty_delta
            if movement.resulting_on_hand != running:
                problems.append(
                    f"stock_item {item.id}: movement {movement.id} records "
                    f"resulting_on_hand={movement.resulting_on_hand}, "
                    f"ledger says {running}"
                )
        if item.on_hand != running:
            problems.append(
                f"stock_item {item.id}: on_hand={item.on_hand} but ledger "
                f"sums to {running}"
            )
    return problems


# ---------------------------------------------------------------------------
# Routes (/stock/) — admin list, item detail, manual entry, triage
# ---------------------------------------------------------------------------


def _current_user() -> User | None:
    from flask_login import current_user

    if current_user.is_authenticated:
        return current_user
    return db.session.query(User).order_by(User.id.asc()).first()


def _open_unmatched_query():
    return (
        db.session.query(StockMovement)
        .filter(
            StockMovement.movement_type == StockMovementType.UNMATCHED_SHIPMENT,
            StockMovement.resolved_at.is_(None),
        )
        .order_by(StockMovement.id.asc())
    )


def _catalog_order(query):
    return query.order_by(
        ProductCatalog.part_number.is_(None),
        ProductCatalog.part_number,
        ProductCatalog.description,
    )


@stock_bp.get("/")
def stock_list():
    items = _catalog_order(
        db.session.query(StockItem)
        .options(joinedload(StockItem.catalog))
        .join(ProductCatalog, StockItem.catalog_id == ProductCatalog.id)
    ).all()
    unmatched_count = (
        db.session.query(func.count(StockMovement.id))
        .filter(
            StockMovement.movement_type == StockMovementType.UNMATCHED_SHIPMENT,
            StockMovement.resolved_at.is_(None),
        )
        .scalar()
    )
    open_reorders = {r.stock_item_id: r for r in open_reorders_query().all()}
    return render_template(
        "stock/list.html",
        items=items,
        unmatched_count=unmatched_count,
        open_reorders=open_reorders,
    )


@stock_bp.get("/items/<int:item_id>")
def item_detail(item_id: int):
    item = db.get_or_404(StockItem, item_id)
    movements = (
        db.session.query(StockMovement)
        .filter(StockMovement.stock_item_id == item.id)
        .order_by(StockMovement.id.desc())
        .all()
    )
    open_reorder = (
        open_reorders_query().filter(Reorder.stock_item_id == item.id).one_or_none()
    )
    return render_template(
        "stock/detail.html", item=item, movements=movements, open_reorder=open_reorder
    )


@stock_bp.post("/items/<int:item_id>/receipt")
def add_receipt(item_id: int):
    item = db.get_or_404(StockItem, item_id)
    try:
        qty = int(request.form.get("qty") or 0)
        record_receipt(item, qty, _current_user(), request.form.get("reason") or "")
        db.session.commit()
        flash(f"Received {qty} pieces — on hand is now {item.on_hand}.", "success")
    except StockError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("stock.item_detail", item_id=item.id))


@stock_bp.post("/items/<int:item_id>/adjustment")
def add_adjustment(item_id: int):
    item = db.get_or_404(StockItem, item_id)
    try:
        delta = int(request.form.get("qty_delta") or 0)
        record_adjustment(
            item, delta, _current_user(), request.form.get("reason") or ""
        )
        db.session.commit()
        flash(
            f"Adjusted by {delta:+d} pieces — on hand is now {item.on_hand}.",
            "success",
        )
    except StockError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("stock.item_detail", item_id=item.id))


@stock_bp.get("/unmatched")
def unmatched_list():
    rows = _open_unmatched_query().options(joinedload(StockMovement.pick_list)).all()
    catalog_choices = (
        db.session.query(ProductCatalog)
        .filter(ProductCatalog.is_active.is_(True))
        .order_by(ProductCatalog.part_number.is_(None), ProductCatalog.part_number,
                  ProductCatalog.description)
        .all()
    )
    return render_template(
        "stock/unmatched.html", rows=rows, catalog_choices=catalog_choices
    )


@stock_bp.post("/unmatched/<int:movement_id>/resolve")
def resolve(movement_id: int):
    unmatched = db.get_or_404(StockMovement, movement_id)
    raw = (request.form.get("catalog_id") or "").strip()
    catalog_row = db.session.get(ProductCatalog, int(raw)) if raw.isdigit() else None
    if catalog_row is None:
        abort(400, description="Pick the product this line should decrement.")
    try:
        movement = resolve_unmatched(unmatched, catalog_row, _current_user())
        db.session.commit()
        flash(
            f"Resolved: decremented {abs(movement.qty_delta)} pieces from "
            f"{catalog_row.part_number or catalog_row.description}.",
            "success",
        )
    except StockError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("stock.unmatched_list"))


# ---------------------------------------------------------------------------
# Seeding routes (/stock/seed) — inline table + optional CSV import
# ---------------------------------------------------------------------------


def _seed_rows():
    """One row per catalog product: its stock item when one exists, else the
    bare catalog row (saving creates the item). Sized for <50 SKUs — the
    whole corpus renders on one screen, no paging."""
    catalog_rows = _catalog_order(
        db.session.query(ProductCatalog).filter(ProductCatalog.is_active.is_(True))
    ).all()
    items_by_catalog = {
        item.catalog_id: item for item in db.session.query(StockItem).all()
    }
    open_by_item = {r.stock_item_id: r for r in open_reorders_query().all()}
    rows = []
    for row in catalog_rows:
        item = items_by_catalog.get(row.id)
        rows.append(
            {
                "catalog": row,
                "item": item,
                "open_reorder": open_by_item.get(item.id) if item else None,
            }
        )
    return rows


@stock_bp.get("/seed")
def seed_screen():
    return render_template("stock/seed.html", rows=_seed_rows())


@stock_bp.post("/seed/rows/<int:catalog_id>")
def seed_row_save(catalog_id: int):
    """Save-per-row from the seeding table (htmx: replaces the row)."""
    catalog_row = db.get_or_404(ProductCatalog, catalog_id)
    error = None
    saved = False
    reorder = None
    try:
        on_hand = _parse_opt_int(request.form.get("on_hand"), "On hand")
        if on_hand is None:
            raise StockError("On hand is required — enter the counted quantity.")
        _, movement, reorder = seed_stock_row(
            catalog_row,
            on_hand=on_hand,
            min_qty=_parse_opt_int(request.form.get("min_qty"), "Min"),
            max_qty=_parse_opt_int(request.form.get("max_qty"), "Max"),
            reorder_qty=_parse_opt_int(request.form.get("reorder_qty"), "Reorder qty"),
            actor=_current_user(),
        )
        db.session.commit()
        saved = True
        if movement is None:
            error = None  # nothing changed; still render the fresh row
    except StockError as exc:
        db.session.rollback()
        error = exc.message
    item = (
        db.session.query(StockItem)
        .filter(StockItem.catalog_id == catalog_row.id)
        .one_or_none()
    )
    open_reorder = None
    if item is not None:
        open_reorder = (
            open_reorders_query()
            .filter(Reorder.stock_item_id == item.id)
            .one_or_none()
        )
    return render_template(
        "stock/_seed_row.html",
        row={"catalog": catalog_row, "item": item, "open_reorder": open_reorder},
        error=error,
        saved=saved and error is None,
        fired_reorder=reorder,
    )


# CSV columns for the optional import (part_number, on_hand, min, max,
# reorder_qty). Matching is by normalized part number ONLY — the same
# normalization as the shipment matcher, and the same rule: ambiguity or a
# miss is a per-row error for a human, never a guess.
CSV_HEADER = ["part_number", "on_hand", "min", "max", "reorder_qty"]


def parse_seed_csv(text: str) -> list[dict]:
    """Parse + validate seeding CSV into per-row results (no writes).

    Each result: {line, part_number, values, catalog (or None), error (or
    None)}. The header row is optional — recognized and skipped when present.
    """
    results: list[dict] = []
    active = (
        db.session.query(ProductCatalog)
        .filter(ProductCatalog.is_active.is_(True))
        .all()
    )
    by_part: dict[str, list[ProductCatalog]] = {}
    for row in active:
        key = _norm(row.part_number)
        if key:
            by_part.setdefault(key, []).append(row)

    reader = csv.reader(io.StringIO(text))
    for line_no, fields in enumerate(reader, start=1):
        if not fields or not any(f.strip() for f in fields):
            continue
        if line_no == 1 and _norm(fields[0]) == "part_number":
            continue
        part_raw = fields[0].strip() if fields else ""
        result = {
            "line": line_no,
            "part_number": part_raw,
            "values": {},
            "catalog": None,
            "error": None,
        }
        results.append(result)
        if len(fields) < 2:
            result["error"] = "Need at least part_number and on_hand."
            continue
        padded = list(fields) + [""] * (len(CSV_HEADER) - len(fields))
        try:
            values = {
                "on_hand": _parse_opt_int(padded[1], "on_hand"),
                "min_qty": _parse_opt_int(padded[2], "min"),
                "max_qty": _parse_opt_int(padded[3], "max"),
                "reorder_qty": _parse_opt_int(padded[4], "reorder_qty"),
            }
        except StockError as exc:
            result["error"] = exc.message
            continue
        result["values"] = values
        if values["on_hand"] is None:
            result["error"] = "on_hand is required."
            continue
        hits = by_part.get(_norm(part_raw), [])
        if not part_raw:
            result["error"] = "part_number is empty."
        elif not hits:
            result["error"] = "No active catalog product has this part number."
        elif len(hits) > 1:
            result["error"] = (
                f"Ambiguous: {len(hits)} catalog products share this part number."
            )
        else:
            result["catalog"] = hits[0]
    return results


@stock_bp.route("/seed/import", methods=["GET", "POST"])
def seed_import():
    """Optional CSV import with dry-run preview. Apply is all-or-nothing:
    any per-row error blocks the whole import (fix the CSV, re-preview) —
    a partial import of a hand count is worse than no import."""
    if request.method == "GET":
        return render_template(
            "stock/seed_import.html", csv_text="", results=None, applied=None
        )
    csv_text = request.form.get("csv_text") or ""
    upload = request.files.get("csv_file")
    if upload is not None and upload.filename:
        csv_text = upload.read().decode("utf-8", errors="replace")
    results = parse_seed_csv(csv_text)
    errors = [r for r in results if r["error"]]
    if request.form.get("mode") != "apply":
        return render_template(
            "stock/seed_import.html", csv_text=csv_text, results=results, applied=None
        )
    if not results:
        flash("Nothing to import — the CSV has no data rows.", "error")
        return render_template(
            "stock/seed_import.html", csv_text=csv_text, results=results, applied=None
        )
    if errors:
        flash(
            f"Not applied: {len(errors)} row{'s' if len(errors) != 1 else ''} "
            "have errors. Fix the CSV and preview again.",
            "error",
        )
        return render_template(
            "stock/seed_import.html", csv_text=csv_text, results=results, applied=None
        )
    actor = _current_user()
    fired = 0
    try:
        for r in results:
            values = r["values"]
            _, _, reorder = seed_stock_row(
                r["catalog"],
                on_hand=values["on_hand"],
                min_qty=values["min_qty"],
                max_qty=values["max_qty"],
                reorder_qty=values["reorder_qty"],
                actor=actor,
            )
            if reorder is not None:
                fired += 1
        db.session.commit()
    except StockError as exc:
        db.session.rollback()
        flash(exc.message, "error")
        return render_template(
            "stock/seed_import.html", csv_text=csv_text, results=results, applied=None
        )
    message = f"Imported {len(results)} row{'s' if len(results) != 1 else ''}."
    if fired:
        message += f" {fired} reorder{'s' if fired != 1 else ''} fired (at/below min)."
    flash(message, "success")
    return redirect(url_for("stock.seed_screen"))


# ---------------------------------------------------------------------------
# Reorder routes — shop-facing list, printable sheet, mark-received
# ---------------------------------------------------------------------------


@stock_bp.get("/reorders/")
def reorders_list():
    open_rows = (
        open_reorders_query()
        .options(joinedload(Reorder.stock_item).joinedload(StockItem.catalog))
        .all()
    )
    received_rows = (
        db.session.query(Reorder)
        .filter(Reorder.status == ReorderStatus.RECEIVED)
        .options(joinedload(Reorder.stock_item).joinedload(StockItem.catalog))
        .order_by(Reorder.received_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "stock/reorders.html", open_rows=open_rows, received_rows=received_rows
    )


@stock_bp.get("/reorders/<int:reorder_id>/sheet")
def reorder_sheet(reorder_id: int):
    """The printable vendor purchase order — CP-4's MANUAL_PRINT channel for
    reorders: order [qty] from [vendor], with the frozen trigger context."""
    reorder = db.get_or_404(Reorder, reorder_id)
    return render_template(
        "stock/reorder_sheet.html", reorder=reorder, item=reorder.stock_item
    )


@stock_bp.post("/reorders/<int:reorder_id>/mark-sent")
def reorder_mark_sent(reorder_id: int):
    """OPEN -> SENT: Chip printed/sent the PO to the vendor."""
    reorder = db.get_or_404(Reorder, reorder_id)
    try:
        mark_reorder_sent(reorder, _current_user())
        db.session.commit()
        item_name = (
            reorder.stock_item.catalog.part_number
            or reorder.stock_item.catalog.description
        )
        vendor = reorder.vendor_at_trigger
        flash(
            f"PO marked sent{f' to {vendor}' if vendor else ''} ({item_name}). "
            "Mark it received when the delivery arrives.",
            "success",
        )
    except StockError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("stock.reorders_list"))


@stock_bp.post("/reorders/<int:reorder_id>/receive")
def reorder_receive(reorder_id: int):
    reorder = db.get_or_404(Reorder, reorder_id)
    try:
        received_qty = _parse_opt_int(request.form.get("received_qty"), "Received qty")
        if received_qty is None:
            raise StockError("Enter the quantity actually received (0 if none).")
        movement, new_reorder = close_reorder(reorder, received_qty, _current_user())
        db.session.commit()
        item_name = (
            reorder.stock_item.catalog.part_number
            or reorder.stock_item.catalog.description
        )
        if movement is not None:
            message = (
                f"Received {received_qty} — {item_name} on hand is now "
                f"{reorder.stock_item.on_hand}."
            )
        else:
            message = f"Reorder closed with nothing received ({item_name})."
        if new_reorder is not None:
            message += " Still at/below min: a fresh reorder was opened."
        flash(message, "success")
    except StockError as exc:
        db.session.rollback()
        flash(exc.message, "error")
    return redirect(url_for("stock.reorders_list"))
