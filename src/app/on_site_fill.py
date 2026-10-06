"""Manual, location-specific fill jobs; no catalog or historical price assignment."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from .extensions import db
from .models import Quote, QuoteLineItem, QuoteStatus

TYPE = "on_site_fill"
LOCATION_FIELDS = ("on_site_label", "on_site_city", "on_site_state", "on_site_price_source")


def normalized(value):
    return " ".join((value or "").split())


def generated_text(specs):
    diameter = Decimal(str(specs["diameter"]))
    weight = Decimal(str(specs["fill_weight_lb"]))
    weight_text = f"{weight:,.2f}".rstrip("0").rstrip(".")
    return (
        f"GTB-{diameter.normalize():f}-{weight.normalize():f}-ONSITE",
        f"Geotextile Bag Weight {diameter:g}in Pipe {weight_text} lb Fill - On-site Filling",
    )


def validate_form(form):
    for key, label in (
        ("on_site_label", "Job site"),
        ("on_site_city", "City"),
        ("on_site_state", "State"),
    ):
        if not normalized(form.get(key)):
            raise ValueError(f"{label} is required for on-site filling.")
    for key, label in (
        ("quantity", "Quantity"),
        ("spec_diameter", "Diameter"),
        ("spec_fill_weight_lb", "Fill weight"),
        ("unit_price", "Manual unit price"),
    ):
        try:
            value = Decimal(form.get(key, ""))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{label} must be a positive number.") from None
        minimum = Decimal("0.01") if key == "unit_price" else Decimal("0.000001")
        if not value.is_finite() or value < minimum or value > Decimal("9999999999.99"):
            raise ValueError(f"{label} must be a positive number within 9,999,999,999.99.")


def apply_form(item, form, prior_specs=None):
    specs = dict(item.specs_json or {})
    previous = None
    if prior_specs and all(prior_specs.get(k) for k in ("diameter", "fill_weight_lb")):
        previous = generated_text(prior_specs)
    specs.update({k: form[f"spec_{k}"].strip() for k in ("diameter", "fill_weight_lb")})
    part, description = generated_text(specs)
    if not item.part_number or (
        previous and item.part_number == previous[0] and not specs.get("part_number_override")
    ):
        item.part_number = part
    if item.description in ("", "New line item") or (previous and item.description == previous[1]):
        item.description = description
    changed = any(getattr(item, k) != normalized(form.get(k)) for k in LOCATION_FIELDS)
    for key in LOCATION_FIELDS:
        setattr(item, key, normalized(form.get(key)))
    # Location spelling remains human-readable; case/whitespace are normalized in recall.
    item.on_site_state = item.on_site_state.upper()
    if (
        changed
        or specs != prior_specs
        or item.on_site_priced_at is None
        or form.get("unit_price") != form.get("unit_price_baseline")
    ):
        item.on_site_priced_at = datetime.utcnow()
    for key in (
        "manual_no_charge",
        "auto_unit_price",
        "price_stale",
        "original_qty",
        "weight_per_ft",
        "price_per_lb",
    ):
        specs.pop(key, None)
    specs["price_override"] = True
    item.specs_json = specs


def history(city, state, exclude_quote_id):
    city, state = normalized(city), normalized(state)
    if not city or not state:
        return []
    return (
        db.session.query(QuoteLineItem)
        .join(Quote)
        .filter(
            QuoteLineItem.product_type == TYPE,
            db.func.lower(QuoteLineItem.on_site_city) == city.lower(),
            db.func.lower(QuoteLineItem.on_site_state) == state.lower(),
            QuoteLineItem.unit_price > 0,
            QuoteLineItem.on_site_priced_at.isnot(None),
            Quote.id != exclude_quote_id,
            Quote.deleted_at.is_(None),
            Quote.status != QuoteStatus.REPLACED,
        )
        .order_by(QuoteLineItem.on_site_priced_at.desc(), QuoteLineItem.id.desc())
        .limit(20)
        .all()
    )
