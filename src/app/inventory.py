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
        db.session.add(
            StockMovement(
                stock_item_id=item.id,
                movement_type=StockMovementType.SHIPMENT_DECREMENT,
                qty_delta=-pieces,
                resulting_on_hand=item.on_hand,
                pick_list_id=pick_list.id,
                user_id=actor.id if actor else None,
                details={"matched_by": matched_by, "line": line},
            )
        )
    return True


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


@stock_bp.get("/")
def stock_list():
    items = (
        db.session.query(StockItem)
        .options(joinedload(StockItem.catalog))
        .join(ProductCatalog, StockItem.catalog_id == ProductCatalog.id)
        .order_by(ProductCatalog.part_number.is_(None), ProductCatalog.part_number,
                  ProductCatalog.description)
        .all()
    )
    unmatched_count = (
        db.session.query(func.count(StockMovement.id))
        .filter(
            StockMovement.movement_type == StockMovementType.UNMATCHED_SHIPMENT,
            StockMovement.resolved_at.is_(None),
        )
        .scalar()
    )
    return render_template(
        "stock/list.html", items=items, unmatched_count=unmatched_count
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
    return render_template("stock/detail.html", item=item, movements=movements)


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
