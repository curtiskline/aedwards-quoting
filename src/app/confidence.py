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
    SendHold,
    ShipToAddress,
    TrustRampConfig,
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


def _dial_value(column: str, env_var: str, default: float) -> float:
    """Resolve an admin dial: stored config value, else env, else default.

    The admin-set value (CP-2c) wins whenever the trust-ramp row carries one;
    NULL columns fall back to the environment override, then the code default,
    which keeps pre-dial deployments behaving exactly as before.
    """
    cfg = db.session.get(TrustRampConfig, 1)
    stored = getattr(cfg, column, None) if cfg is not None else None
    if stored is not None:
        return float(stored)
    raw = os.getenv(env_var, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


def _price_tolerance_pct() -> float:
    return _dial_value(
        "price_tolerance_pct", "CONFIDENCE_PRICE_TOLERANCE_PCT", DEFAULT_PRICE_TOLERANCE_PCT
    )


def price_tolerance_pct() -> float:
    """Public reader for the admin dashboard (CP-2c dial display)."""
    return _price_tolerance_pct()


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


# ---------------------------------------------------------------------------
# Tier-1 assisted-send recommendation (CP-2b: recommend-only).
#
# quote_recommendation below is THE recommend rule — the dashboard, the editor
# panel, the send form, and CP-2c's future auto-send gate must all call it, so
# display and enforcement can never drift apart. Do not re-derive any part of
# it (threshold, guardrails, holds, tier) elsewhere.
# ---------------------------------------------------------------------------

# Human-facing labels for the component signals, shared by all surfaces.
SIGNAL_LABELS: dict[str, str] = {
    "decode_clean": "Clean decode",
    "all_lines_priced": "All lines priced",
    "customer_known": "Customer known",
    "ship_to_confirmed": "Ship-to confirmed",
    "price_in_tolerance": "Price in tolerance",
    "recipient_allowlisted": "Recipient allowed",
}

# Minimum composite score to recommend. Env-overridable until CP-2c gives it
# an admin dial (same pattern as the price tolerance above). The default
# tolerates one small unknown (e.g. a product-only quote with no ship-to)
# but never a failing signal — see quote_recommendation.
DEFAULT_RECOMMEND_THRESHOLD = 0.90

# The tier at which the engine starts recommending. Tier 0 is the
# kill-switch: fully manual, recommendations suspended.
RECOMMEND_MIN_TIER = 1

# Statuses for which "recommended for send" is meaningful.
_SENDABLE_STATUSES = frozenset(
    {QuoteStatus.NEW, QuoteStatus.IN_REVIEW, QuoteStatus.NEEDS_PRICING, QuoteStatus.READY}
)


def recommend_threshold() -> float:
    raw = os.getenv("CONFIDENCE_RECOMMEND_THRESHOLD", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_RECOMMEND_THRESHOLD


def active_trust_tier() -> int:
    """Read-only tier lookup: the stored row, or the Tier-1 default.

    Read paths (queue, editor, send form) must use this — it never writes, so
    GET requests cannot open a lingering SQLite write transaction.
    """
    cfg = db.session.get(TrustRampConfig, 1)
    return cfg.active_tier if cfg is not None else 1


def get_trust_ramp_config() -> TrustRampConfig:
    """The single trust-ramp row (id=1), created at default Tier 1 if absent.

    Write-path only (the admin tier form): flushes an INSERT when the row is
    missing, and the caller must commit.
    """
    cfg = db.session.get(TrustRampConfig, 1)
    if cfg is None:
        cfg = TrustRampConfig(id=1, active_tier=1)
        db.session.add(cfg)
        db.session.flush()
    return cfg


def active_send_holds() -> list[SendHold]:
    return db.session.query(SendHold).order_by(SendHold.created_at, SendHold.id).all()


def _matched_holds(quote: Quote, holds: list[SendHold]) -> list[dict]:
    """Holds that apply to this quote, as display-ready dicts."""
    line_types = {item.product_type for item in _material_line_items(quote)}
    matched: list[dict] = []
    for hold in holds:
        if hold.customer_id is not None and hold.customer_id == quote.customer_id:
            label = hold.customer.company_name if hold.customer else f"customer {hold.customer_id}"
            matched.append({"kind": "customer", "label": label, "reason": hold.reason, "id": hold.id})
        elif hold.product_type is not None and hold.product_type in line_types:
            matched.append(
                {"kind": "product_type", "label": hold.product_type, "reason": hold.reason, "id": hold.id}
            )
    return matched


def quote_recommendation(
    quote: Quote,
    *,
    confidence: QuoteConfidence | None = None,
    holds: list[SendHold] | None = None,
    tier: int | None = None,
) -> dict:
    """Evaluate the Tier-1 recommend rule for one quote.

    Recommended iff ALL of:
      - the trust ramp is at Tier >= 1 (Tier 0 is the kill-switch),
      - the quote is in a sendable status and has a stored confidence row,
      - no component signal FAILS (unknowns are tolerated only through the
        threshold: they earn zero points),
      - the guardrail signals all_lines_priced and recipient_allowlisted PASS
        outright — unpriced work or a blocked recipient is never recommended,
      - the composite score >= the recommend threshold,
      - no admin hold matches the quote's customer or product types.

    holds/tier accept prefetched values so list views evaluate N quotes with
    one query; when omitted they are loaded here.
    """
    if confidence is None:
        confidence = quote.confidence
    if holds is None:
        holds = active_send_holds()
    if tier is None:
        tier = active_trust_tier()

    reasons: list[str] = []
    matched_holds = _matched_holds(quote, holds)
    score = float(confidence.score) if confidence is not None else None

    if tier < RECOMMEND_MIN_TIER:
        reasons.append("trust ramp is at Tier 0 (kill-switch): recommendations suspended")
    if quote.status not in _SENDABLE_STATUSES:
        reasons.append(f"quote is {quote.status.value.replace('_', ' ')}")
    if confidence is None:
        reasons.append("quote has not been scored yet")
    else:
        statuses = {name: getattr(confidence, name) for name in SIGNAL_WEIGHTS}
        for name in ("all_lines_priced", "recipient_allowlisted"):
            if statuses[name] != PASS:
                reasons.append(f"guardrail: {SIGNAL_LABELS[name]} must pass, is {statuses[name]}")
        for name, status in statuses.items():
            if status == FAIL and name not in ("all_lines_priced", "recipient_allowlisted"):
                reasons.append(f"{SIGNAL_LABELS[name]} failed")
        threshold = recommend_threshold()
        if not any(status == FAIL for status in statuses.values()) and score < threshold:
            unknowns = [SIGNAL_LABELS[n] for n, s in statuses.items() if s == UNKNOWN]
            detail = f" (unknown: {', '.join(unknowns)})" if unknowns else ""
            reasons.append(f"score {score:.2f} is below the {threshold:.2f} threshold{detail}")
    for hold in matched_holds:
        kind = "customer" if hold["kind"] == "customer" else "product type"
        suffix = f" — {hold['reason']}" if hold["reason"] else ""
        reasons.append(f"admin hold on {kind} {hold['label']}{suffix}")

    return {
        "recommended": not reasons,
        "score": score,
        "threshold": recommend_threshold(),
        "tier": tier,
        "scored": confidence is not None,
        "reasons": reasons,
        "holds": matched_holds,
    }


# ---------------------------------------------------------------------------
# Tier-2 auto-send gate (CP-2c).
#
# auto_send_evaluation is THE eligibility rule for auto-send. It builds on
# quote_recommendation (so display and enforcement share one recommend rule)
# and then applies the stricter Tier-2 requirements from design §4. The send
# machinery itself (send_service) re-enforces the delivery gate and
# allowlist — eligibility here is necessary, never sufficient.
# ---------------------------------------------------------------------------

# Conservative defaults, admin-editable on trust_ramp_config (NULL = fall
# back to env, then these values).
DEFAULT_AUTO_SEND_THRESHOLD = 0.95
DEFAULT_AUTO_SEND_DOLLAR_CEILING = 2500.0

AUTO_SEND_TIER = 2

# Signals that must PASS outright for auto-send — unknown is NOT eligible
# (design §4: unknown-history pricing, unconfirmed ship-to, or a new customer
# all fall to the human).
AUTO_SEND_REQUIRED_SIGNALS = (
    "all_lines_priced",
    "recipient_allowlisted",
    "customer_known",
    "ship_to_confirmed",
    "price_in_tolerance",
)


def auto_send_threshold() -> float:
    return _dial_value("auto_send_threshold", "AUTO_SEND_THRESHOLD", DEFAULT_AUTO_SEND_THRESHOLD)


def auto_send_dollar_ceiling() -> float:
    return _dial_value(
        "auto_send_dollar_ceiling", "AUTO_SEND_DOLLAR_CEILING", DEFAULT_AUTO_SEND_DOLLAR_CEILING
    )


def quote_grand_total(quote: Quote) -> Decimal:
    """Product + shipping + tax. Shipping is a line item, so summing every
    line_total and adding tax_amount matches the editor's grand total."""
    total = Decimal("0")
    for item in quote.line_items:
        if item.product_type == "note":
            continue
        total += Decimal(str(item.line_total or 0))
    total += Decimal(str(quote.tax_amount or 0))
    return total.quantize(Decimal("0.01"))


def auto_send_evaluation(quote: Quote) -> dict:
    """Evaluate every Tier-2 auto-send guardrail for one quote.

    Eligible iff ALL of:
      - the trust ramp is at Tier 2 (read live: dropping to 0/1 is the
        kill-switch and stops auto-sends immediately),
      - the Tier-1 recommend rule passes in full (no failing signal,
        guardrail signals pass, score >= recommend threshold, no holds,
        sendable status),
      - every AUTO_SEND_REQUIRED_SIGNAL is PASS — never unknown: no price
        history, no confirmed ship-to, or a new customer is NOT eligible,
      - the composite score >= the (stricter) auto-send threshold,
      - the quote grand total is within the dollar ceiling.

    Returns a dict with "eligible", "reasons", and the full basis snapshot
    (score, thresholds, ceiling, total, signal statuses) for the audit log.
    """
    tier = active_trust_tier()
    confidence = quote.confidence
    recommendation = quote_recommendation(quote, confidence=confidence, tier=tier)

    reasons: list[str] = []
    if tier != AUTO_SEND_TIER:
        reasons.append(f"trust ramp is at Tier {tier}: auto-send requires Tier {AUTO_SEND_TIER}")
    reasons.extend(recommendation["reasons"])

    statuses: dict[str, str] = {}
    if confidence is not None:
        statuses = {name: getattr(confidence, name) for name in SIGNAL_WEIGHTS}
        for name in AUTO_SEND_REQUIRED_SIGNALS:
            if statuses[name] != PASS:
                reasons.append(
                    f"auto-send requires {SIGNAL_LABELS[name]} to pass, is {statuses[name]}"
                )

    threshold = auto_send_threshold()
    score = float(confidence.score) if confidence is not None else None
    if score is not None and score < threshold:
        reasons.append(f"score {score:.2f} is below the {threshold:.2f} auto-send threshold")

    ceiling = auto_send_dollar_ceiling()
    total = float(quote_grand_total(quote))
    if total > ceiling:
        reasons.append(f"quote total ${total:,.2f} exceeds the ${ceiling:,.2f} auto-send ceiling")
    if total <= 0:
        reasons.append("quote total is $0 — never auto-sendable")

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "tier": tier,
        "score": score,
        "auto_send_threshold": threshold,
        "recommend_threshold": recommendation["threshold"],
        "dollar_ceiling": ceiling,
        "quote_total": total,
        "price_tolerance_pct": _price_tolerance_pct(),
        "signals": statuses,
        "holds": recommendation["holds"],
    }
