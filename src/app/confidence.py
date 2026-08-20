"""Per-quote confidence score and component signals (CP-2a: scoring only).

Computes and persists the trust-ramp confidence signals from
docs/research/amazon-engine-end-to-end-design.md §4. Nothing in this module
changes send behavior — the score is recorded so the CP-2b dashboard can show
it (and WHY) and CP-2c can gate on it.

Every signal is tri-state: "pass", "fail", or "unknown". Unknown is never
treated as pass — a signal that cannot be evaluated (no price history, no
ship-to on the quote) earns zero points, exactly like a failure, but is
labeled distinctly so the dashboard can tell "looks wrong" from "can't tell".

The tolerance and signal weights below become admin-configurable dials in
CP-2c — keep them here, in one place, and do not duplicate them elsewhere.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from allenedwards.ship_to import normalize_ship_to

from .extensions import db
from .models import (
    Customer,
    Quote,
    QuoteConfidence,
    QuoteLineItem,
    QuoteStatus,
    ShipToAddress,
)

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

# Composite = sum of the weights of passing signals (weights sum to 1.0).
SIGNAL_WEIGHTS: dict[str, Decimal] = {
    "decode_clean": Decimal("0.20"),
    "all_lines_priced": Decimal("0.25"),
    "customer_known": Decimal("0.15"),
    "ship_to_confirmed": Decimal("0.10"),
    "price_in_tolerance": Decimal("0.20"),
    "recipient_allowlisted": Decimal("0.10"),
}

# A line's unit price must be within this fraction of the median historical
# unit price for the same product_type + specs. Env-overridable until CP-2c
# gives it an admin dial.
DEFAULT_PRICE_TOLERANCE_PCT = 0.20

# Specs that define "the same product" for price-history comparison. Every
# key present on the line being scored must match the candidate exactly.
_COMPARABLE_SPEC_KEYS = ("diameter", "wall_thickness", "grade", "length_ft")

# A Customer row created within this window of the quote itself is the RFQ's
# own auto-created record (db_writer._create_customer_from_rfq), not a known
# customer — the trust ramp explicitly holds new-customer quotes.
_NEW_CUSTOMER_WINDOW = timedelta(minutes=5)

_SHIP_TO_MATCH_FIELDS = (
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "country",
)


# ---------------------------------------------------------------------------
# Shared quote/line state helpers. These are the source of truth for pricing
# completeness — routes._sync_quote_pricing_status delegates here.
# ---------------------------------------------------------------------------


def line_item_is_manual_no_charge(item: QuoteLineItem) -> bool:
    """Return whether an editor user deliberately made this non-shipping line free."""
    return item.product_type not in {"note", "shipping"} and bool(
        dict(item.specs_json or {}).get("manual_no_charge")
    )


def line_item_has_tbd_marker(item: QuoteLineItem) -> bool:
    values = (item.part_number, item.description)
    return any("tbd" in str(value or "").lower() for value in values)


def quote_has_unpriced_items(quote: Quote) -> bool:
    for item in quote.line_items:
        if item.product_type == "note" or line_item_is_manual_no_charge(item):
            continue
        if Decimal(str(item.unit_price)) <= 0 or Decimal(str(item.line_total)) <= 0:
            return True
    return False


def quote_has_tbd_items(quote: Quote) -> bool:
    """Return whether a material line still contains an explicit TBD marker."""
    for item in quote.line_items:
        if item.product_type == "note":
            continue
        if line_item_has_tbd_marker(item):
            return True
    return False


# ---------------------------------------------------------------------------
# Component signals. Each returns (status, detail).
# ---------------------------------------------------------------------------


def _material_line_items(quote: Quote) -> list[QuoteLineItem]:
    return [item for item in quote.line_items if item.product_type not in ("note", "shipping")]


def _signal_decode_clean(quote: Quote) -> tuple[str, dict]:
    """Did the pipeline extract every line cleanly?

    Derived from what the pipeline already records: TBD markers, price_stale
    flags, defaults-applied notes on line specs, and the needs-pricing state.
    """
    reasons: list[str] = []
    for item in _material_line_items(quote):
        label = item.part_number or item.description or item.product_type
        specs = dict(item.specs_json or {})
        if line_item_has_tbd_marker(item):
            reasons.append(f"{label}: TBD marker")
        if specs.get("price_stale"):
            reasons.append(f"{label}: price flagged stale")
        notes = str(specs.get("notes") or "")
        if "default" in notes.lower():
            reasons.append(f"{label}: defaults applied ({notes})")
    if quote.status == QuoteStatus.NEEDS_PRICING:
        reasons.append("quote status is needs_pricing")
    if reasons:
        return FAIL, {"reasons": reasons}
    return PASS, {}


def _signal_all_lines_priced(quote: Quote) -> tuple[str, dict]:
    """No $0/unpriced/TBD lines (manual_no_charge counts as priced-by-human)."""
    if not _material_line_items(quote):
        return UNKNOWN, {"reason": "quote has no material line items"}
    reasons: list[str] = []
    if quote_has_unpriced_items(quote):
        reasons.append("unpriced ($0) line items present")
    if quote_has_tbd_items(quote):
        reasons.append("TBD marker on a material line")
    if reasons:
        return FAIL, {"reasons": reasons}
    return PASS, {}


def _signal_customer_known(quote: Quote) -> tuple[str, dict]:
    """Linked to a customer that existed before this quote.

    A customer row auto-created from this same RFQ (or created moments before
    the quote) is a NEW customer — the ramp holds those, so the signal fails.
    """
    if quote.customer_id is None:
        return FAIL, {"reason": "no customer linked"}
    customer = db.session.get(Customer, quote.customer_id)
    if customer is None:
        return FAIL, {"reason": f"customer {quote.customer_id} not found"}
    quote_created = quote.created_at or datetime.utcnow()
    if customer.created_at is not None and (
        customer.created_at <= quote_created - _NEW_CUSTOMER_WINDOW
    ):
        return PASS, {"customer_id": customer.id}
    has_prior_quote = (
        db.session.query(Quote.id)
        .filter(
            Quote.customer_id == customer.id,
            Quote.id != quote.id,
            Quote.created_at < quote_created,
            Quote.deleted_at.is_(None),
        )
        .first()
        is not None
    )
    if has_prior_quote:
        return PASS, {"customer_id": customer.id, "basis": "customer has prior quotes"}
    return FAIL, {
        "reason": "customer record was created with this quote (new customer)",
        "customer_id": customer.id,
    }


def _stored_address_data(address: ShipToAddress) -> dict:
    return normalize_ship_to(
        {
            "address_line1": address.address_line1,
            "address_line2": address.address_line2 or "",
            "city": address.city,
            "state": address.state,
            "postal_code": address.postal_code,
            "country": address.country,
        }
    )


def _signal_ship_to_confirmed(quote: Quote) -> tuple[str, dict]:
    """The quote's ship-to matches a stored, human-confirmed customer address.

    Unknown when the quote carries no ship-to at all (product-only quote);
    fail when a ship-to exists but no confirmed stored address backs it.
    """
    incoming = normalize_ship_to(quote.ship_to_json)
    if incoming is None:
        return UNKNOWN, {"reason": "quote has no ship-to address"}
    if quote.customer_id is None:
        return FAIL, {"reason": "ship-to present but no linked customer to confirm against"}
    customer = db.session.get(Customer, quote.customer_id)
    if customer is None:
        return FAIL, {"reason": f"customer {quote.customer_id} not found"}
    matched_unconfirmed = None
    for address in customer.ship_to_addresses:
        stored = _stored_address_data(address)
        if all(incoming[field] == stored[field] for field in _SHIP_TO_MATCH_FIELDS):
            if address.human_confirmed:
                return PASS, {"address_id": address.id}
            matched_unconfirmed = address
    if matched_unconfirmed is not None:
        return FAIL, {
            "reason": "matching stored address is not human-confirmed",
            "address_id": matched_unconfirmed.id,
        }
    return FAIL, {"reason": "no stored customer address matches the quote ship-to"}


def _price_tolerance_pct() -> float:
    raw = os.getenv("CONFIDENCE_PRICE_TOLERANCE_PCT", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_PRICE_TOLERANCE_PCT


def _revision_chain_quote_ids(quote: Quote) -> set[int]:
    """IDs of every quote in this quote's revision chain (including itself)."""
    root = quote
    guard: set[int] = set()
    while root.replaces is not None and id(root) not in guard:
        guard.add(id(root))
        root = root.replaces
    ids: set[int] = set()
    node: Quote | None = root
    guard = set()
    while node is not None and id(node) not in guard:
        guard.add(id(node))
        if node.id is not None:
            ids.add(node.id)
        node = node.replaced_by
    if quote.id is not None:
        ids.add(quote.id)
    return ids


def _comparable_unit_prices(item: QuoteLineItem, exclude_quote_ids: set[int]) -> list[float]:
    """Historical unit prices for the same product_type + specs.

    Only SENT quotes count — those prices were human-reviewed, so they are the
    trusted baseline. Every comparable spec key present on the scored line
    must match the candidate exactly.
    """
    specs = dict(item.specs_json or {})
    required = {
        key: str(specs[key]) for key in _COMPARABLE_SPEC_KEYS if specs.get(key) is not None
    }
    rows = (
        db.session.query(QuoteLineItem)
        .join(Quote, QuoteLineItem.quote_id == Quote.id)
        .filter(
            Quote.status == QuoteStatus.SENT,
            Quote.deleted_at.is_(None),
            QuoteLineItem.product_type == item.product_type,
        )
        .all()
    )
    prices: list[float] = []
    for row in rows:
        if row.quote_id in exclude_quote_ids or row.id == item.id:
            continue
        if line_item_is_manual_no_charge(row):
            continue
        row_specs = dict(row.specs_json or {})
        if any(str(row_specs.get(key)) != value for key, value in required.items()):
            continue
        price = float(row.unit_price or 0)
        if price > 0:
            prices.append(price)
    return prices


def _signal_price_in_tolerance(quote: Quote) -> tuple[str, dict]:
    """Unit prices within tolerance of historical SENT quotes for the same product+specs.

    No history for a line = unknown, never pass. Any line out of tolerance
    fails the whole signal.
    """
    tolerance = _price_tolerance_pct()
    exclude_ids = _revision_chain_quote_ids(quote)
    lines: list[dict] = []
    any_fail = False
    any_unknown = False
    evaluated = 0
    for item in _material_line_items(quote):
        if line_item_is_manual_no_charge(item):
            continue
        price = float(item.unit_price or 0)
        if price <= 0:
            continue  # unpriced lines are all_lines_priced's problem
        evaluated += 1
        label = item.part_number or item.description or item.product_type
        prices = _comparable_unit_prices(item, exclude_ids)
        if not prices:
            any_unknown = True
            lines.append({"line": label, "status": UNKNOWN, "reason": "no comparable history"})
            continue
        baseline = median(prices)
        deviation = abs(price - baseline) / baseline
        in_tolerance = deviation <= tolerance
        any_fail = any_fail or not in_tolerance
        lines.append(
            {
                "line": label,
                "status": PASS if in_tolerance else FAIL,
                "unit_price": price,
                "median_historical": round(baseline, 2),
                "deviation_pct": round(deviation * 100, 1),
                "comparables": len(prices),
            }
        )
    detail = {"tolerance_pct": tolerance, "lines": lines}
    if evaluated == 0:
        return UNKNOWN, {"reason": "no priced material lines to compare", **detail}
    if any_fail:
        return FAIL, detail
    if any_unknown:
        return UNKNOWN, detail
    return PASS, detail


def _signal_recipient_allowlisted(quote: Quote) -> tuple[str, dict]:
    """Contact email present and permitted by SEND_EMAIL_ALLOWLIST when configured.

    Mirrors the send-gate semantics in routes.quote_send: an unset allowlist
    allows all recipients.
    """
    email = (quote.contact_email or "").strip()
    if not email:
        return FAIL, {"reason": "no contact email on quote"}
    allowlist_raw = os.getenv("SEND_EMAIL_ALLOWLIST", "").strip()
    if not allowlist_raw:
        return PASS, {"allowlist_configured": False}
    allowed = {e.strip().lower() for e in allowlist_raw.split(",") if e.strip()}
    if email.lower() in allowed:
        return PASS, {"allowlist_configured": True}
    return FAIL, {
        "reason": f"{email!r} is not in SEND_EMAIL_ALLOWLIST",
        "allowlist_configured": True,
    }


# ---------------------------------------------------------------------------
# Composite + persistence
# ---------------------------------------------------------------------------

_SIGNAL_FUNCTIONS = {
    "decode_clean": _signal_decode_clean,
    "all_lines_priced": _signal_all_lines_priced,
    "customer_known": _signal_customer_known,
    "ship_to_confirmed": _signal_ship_to_confirmed,
    "price_in_tolerance": _signal_price_in_tolerance,
    "recipient_allowlisted": _signal_recipient_allowlisted,
}


def compute_quote_confidence(quote: Quote) -> tuple[Decimal, dict]:
    """Return (composite score 0-1, per-signal component breakdown)."""
    components: dict[str, dict] = {}
    score = Decimal("0")
    for name, signal in _SIGNAL_FUNCTIONS.items():
        status, detail = signal(quote)
        weight = SIGNAL_WEIGHTS[name]
        points = weight if status == PASS else Decimal("0")
        score += points
        component = {"status": status, "weight": float(weight), "points": float(points)}
        if detail:
            component["detail"] = detail
        components[name] = component
    return score.quantize(Decimal("0.001")), components


def sync_quote_confidence(quote: Quote) -> bool:
    """Compute and upsert the quote's confidence row.

    Flushes (so a freshly added quote has an id) but never commits — the
    caller owns the transaction. Returns True when the stored row was created
    or its values changed, so read-only paths can decide whether to commit.
    """
    if quote.id is None:
        db.session.flush()
    score, components = compute_quote_confidence(quote)
    row = quote.confidence
    created = row is None
    if created:
        row = QuoteConfidence(quote_id=quote.id)
        quote.confidence = row
        db.session.add(row)
    statuses = {name: components[name]["status"] for name in SIGNAL_WEIGHTS}
    changed = (
        created
        or float(row.score) != float(score)
        or row.components_json != components
        or any(getattr(row, name) != status for name, status in statuses.items())
    )
    if changed:
        row.score = float(score)
        for name, status in statuses.items():
            setattr(row, name, status)
        row.components_json = components
        row.computed_at = datetime.utcnow()
    return changed
