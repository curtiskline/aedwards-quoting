"""Web routes for the quoting app."""

from __future__ import annotations

import csv
import math
import os
import re
import tempfile
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, Response, abort, redirect, render_template, request
from flask_login import login_required
from sqlalchemy import inspect

from allenedwards.pdf_generator import generate_quote_pdf
from .extensions import db
from .models import (
    AuditLog,
    Contact,
    Customer,
    PricingTable,
    ProductType,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    QuoteVersion,
    ShippingConfig,
    ShipToAddress,
    User,
)
from allenedwards.pricing import (
    STANDARD_BUNDLE_PIECES,
    bundle_round,
    generate_girth_weld_part_number,
    generate_oversleeve_part_number,
    generate_part_number,
    generate_sleeve_part_number,
    normalize_nominal_od,
    pallet_round,
)
from allenedwards.pricing import Quote as PricingQuote
from allenedwards.pricing import QuoteLineItem as PricingLineItem

main_bp = Blueprint("main", __name__)

DEFAULT_PRODUCT_TYPES: list[tuple[str, str]] = [
    ("sleeve", "Sleeve"),
    ("bag", "Bag"),
    ("girth_weld", "Girth Weld"),
    ("compression", "Compression"),
    ("oversleeve", "Oversleeve"),
    ("accessory", "Accessory"),
    ("service", "Service"),
    ("shipping", "Shipping & Handling"),
]


@main_bp.get("/")
@login_required
def dashboard():
    return redirect("/quotes/")


_PRODUCT_TYPE_LABELS = {
    "shipping": "Shipping & Handling",
}

AUTO_SHIPPING_DESCRIPTION = "Auto-calculated shipping & handling"


def _format_product_label(product_type: str) -> str:
    return _PRODUCT_TYPE_LABELS.get(product_type, product_type.replace("_", " ").title())


def _describe_key_fields(product_type: str, key_fields: dict) -> str:
    if product_type == "sleeve":
        return f'{key_fields.get("wall_thickness")}" wall, GR{key_fields.get("grade")}'
    if product_type == "girth_weld":
        return f'{key_fields.get("min_diameter")}-{key_fields.get("max_diameter")}" diameter'
    if product_type == "bag":
        return (
            f'{key_fields.get("part_number")} ({key_fields.get("pipe_size_min")}-'
            f'{key_fields.get("pipe_size_max")}" pipe, {key_fields.get("pieces_per_pallet")} pcs/pallet)'
        )
    if "key" in key_fields:
        return str(key_fields.get("key")).replace("_", " ").title()
    return str(key_fields)


def _pricing_section_order(product_type: str) -> int:
    order = {
        "sleeve": 0,
        "girth_weld": 1,
        "bag": 2,
        "compression": 3,
        "omegawrap": 4,
        "accessory": 5,
        "service": 6,
        "flat": 7,
    }
    return order.get(product_type, 99)


def _group_pricing_rows() -> list[dict]:
    if not inspect(db.engine).has_table("pricing_table"):
        return []

    rows = db.session.query(PricingTable).order_by(PricingTable.product_type, PricingTable.id).all()
    grouped: dict[str, list[dict]] = {}

    for row in rows:
        grouped.setdefault(row.product_type, []).append(
            {
                "id": row.id,
                "price": Decimal(str(row.price)),
                "key_fields": dict(row.key_fields or {}),
                "label": _describe_key_fields(row.product_type, row.key_fields or {}),
            }
        )

    sections: list[dict] = []
    for product_type, entries in sorted(grouped.items(), key=lambda pair: _pricing_section_order(pair[0])):
        sections.append(
            {
                "product_type": product_type,
                "title": _format_product_label(product_type),
                "entries": entries,
            }
        )
    return sections


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _parse_decimal(value: str | None, default: Decimal = Decimal("0")) -> Decimal:
    raw = (value or "").strip()
    if not raw:
        return default
    try:
        return Decimal(raw)
    except InvalidOperation:
        return default


def _parse_float(value: str | None) -> float | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _ensure_product_types_seeded() -> None:
    if not inspect(db.engine).has_table("product_type"):
        return
    has_rows = db.session.query(ProductType.id).first()
    if has_rows is not None:
        return
    for idx, (name, label) in enumerate(DEFAULT_PRODUCT_TYPES, start=1):
        db.session.add(
            ProductType(
                name=name,
                display_label=label,
                sort_order=idx,
                is_active=True,
            )
        )
    db.session.flush()


def _all_product_types() -> list[ProductType]:
    if not inspect(db.engine).has_table("product_type"):
        return [
            ProductType(name=name, display_label=label, sort_order=idx, is_active=True)
            for idx, (name, label) in enumerate(DEFAULT_PRODUCT_TYPES, start=1)
        ]
    _ensure_product_types_seeded()
    return (
        db.session.query(ProductType)
        .order_by(ProductType.sort_order.asc(), ProductType.id.asc())
        .all()
    )


def _active_product_types() -> list[ProductType]:
    return [row for row in _all_product_types() if row.is_active]


def _product_type_choices() -> list[dict[str, str]]:
    return [{"name": row.name, "label": row.display_label} for row in _active_product_types()]


def _resolve_product_type(raw_product_type: str | None, fallback: str) -> str:
    selected = (raw_product_type or "").strip() or fallback
    valid_names = {row.name for row in _all_product_types()}
    if selected in valid_names:
        return selected
    if fallback in valid_names:
        return fallback
    active = _active_product_types()
    if active:
        return active[0].name
    return "sleeve"


def _current_user() -> User | None:
    user_id = request.headers.get("X-User-Id") or request.args.get("user_id")
    if user_id:
        user = db.session.get(User, int(user_id))
        if user is not None:
            return user
    return db.session.query(User).order_by(User.id.asc()).first()


def _sorted_line_items(quote: Quote) -> list[QuoteLineItem]:
    return sorted(quote.line_items, key=lambda li: (li.sort_order, li.id))


def _normalize_sort_orders(quote: Quote) -> None:
    for idx, item in enumerate(_sorted_line_items(quote), start=1):
        item.sort_order = idx


def _normalize_zip(raw_zip: str | None) -> str | None:
    digits = "".join(ch for ch in (raw_zip or "") if ch.isdigit())
    if len(digits) < 5:
        return None
    return digits[:5]


@lru_cache(maxsize=1)
def _zip_centroid_map() -> dict[str, tuple[float, float]]:
    zip_path = Path(__file__).resolve().parents[1] / "allenedwards" / "data" / "us_zip_lat_lon.csv"
    if not zip_path.exists():
        return {}

    mapping: dict[str, tuple[float, float]] = {}
    with zip_path.open("r", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            postal = _normalize_zip(row.get("zip"))
            if postal is None:
                continue
            try:
                lat = float(row.get("lat") or "")
                lon = float(row.get("lon") or "")
            except ValueError:
                continue
            mapping[postal] = (lat, lon)
    return mapping


def _haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 3958.7613 * (2 * math.atan2(math.sqrt(x), math.sqrt(1 - x)))


def _decimal_from_raw(value: object) -> Decimal | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _shipping_config() -> ShippingConfig:
    cfg = db.session.get(ShippingConfig, 1)
    if cfg is None:
        cfg = ShippingConfig(
            id=1,
            default_rate_per_lb_mile=0.0006,
            default_length_ft=10.0,
            origin_zip_codes_json=["74103"],
            rate_overrides_json={},
        )
        db.session.add(cfg)
        db.session.flush()
    return cfg


def _shipping_rates(cfg: ShippingConfig) -> tuple[Decimal, dict[str, Decimal]]:
    default_rate = _quantize_rate(_decimal_from_raw(cfg.default_rate_per_lb_mile) or Decimal("0.0006"))
    overrides: dict[str, Decimal] = {}
    for key, value in dict(cfg.rate_overrides_json or {}).items():
        rate = _decimal_from_raw(value)
        if rate is None or rate <= 0:
            continue
        overrides[str(key)] = _quantize_rate(rate)
    return default_rate, overrides


def _shipping_origin_zips(cfg: ShippingConfig) -> list[str]:
    origins: list[str] = []
    for raw in list(cfg.origin_zip_codes_json or []):
        normalized = _normalize_zip(str(raw))
        if normalized and normalized not in origins:
            origins.append(normalized)
    return origins or ["74103"]


def _shipping_line_item(quote: Quote) -> QuoteLineItem | None:
    for item in _sorted_line_items(quote):
        if item.product_type == "shipping":
            return item
    return None


def _is_manual_shipping_override(item: QuoteLineItem | None) -> bool:
    if item is None:
        return False
    return bool(dict(item.specs_json or {}).get("manual_override"))


def _steel_weight_for_item(item: QuoteLineItem, default_length_ft: Decimal) -> Decimal:
    if item.product_type == "shipping":
        return Decimal("0")
    specs = dict(item.specs_json or {})
    od = _decimal_from_raw(specs.get("diameter"))
    wall = _decimal_from_raw(specs.get("wall_thickness"))
    if od is None or wall is None or od <= 0 or wall <= 0 or od <= wall:
        return Decimal("0")
    length_ft = _decimal_from_raw(specs.get("length_ft")) or default_length_ft
    qty = _decimal_from_raw(item.quantity) or Decimal("0")
    if qty <= 0 or length_ft <= 0:
        return Decimal("0")
    weight_per_ft = Decimal("10.69") * (od - wall) * wall
    if weight_per_ft <= 0:
        return Decimal("0")
    return weight_per_ft * length_ft * qty


def _shipping_breakdown(quote: Quote) -> dict | None:
    ship_to_zip = _normalize_zip((quote.ship_to_json or {}).get("postal_code"))
    if ship_to_zip is None:
        return None

    centroids = _zip_centroid_map()
    destination = centroids.get(ship_to_zip)
    if destination is None:
        return None

    cfg = _shipping_config()
    origins = [zip_code for zip_code in _shipping_origin_zips(cfg) if zip_code in centroids]
    if not origins:
        return None

    closest_origin = min(origins, key=lambda z: _haversine_miles(centroids[z], destination))
    distance_miles = _haversine_miles(centroids[closest_origin], destination)
    distance = _quantize_money(Decimal(str(distance_miles)))
    if distance <= 0:
        return None

    default_rate, overrides = _shipping_rates(cfg)
    default_length_ft = _decimal_from_raw(cfg.default_length_ft) or Decimal("10")

    total_weight = Decimal("0")
    weighted_rate_total = Decimal("0")
    priced_item_count = 0
    for item in _sorted_line_items(quote):
        if item.product_type == "shipping":
            continue
        item_weight = _steel_weight_for_item(item, default_length_ft)
        if item_weight <= 0:
            continue
        rate = overrides.get(item.product_type, default_rate)
        total_weight += item_weight
        weighted_rate_total += item_weight * rate
        priced_item_count += 1

    if total_weight <= 0 or weighted_rate_total <= 0:
        return None

    total_weight = _quantize_money(total_weight)
    effective_rate = _quantize_rate(weighted_rate_total / total_weight)
    total_cost = _quantize_money(total_weight * distance * effective_rate)
    if total_cost <= 0:
        return None

    return {
        "origin_zip": closest_origin,
        "destination_zip": ship_to_zip,
        "distance_miles": distance,
        "total_weight_lb": total_weight,
        "rate_per_lb_mile": effective_rate,
        "total_cost": total_cost,
        "priced_item_count": priced_item_count,
    }


def _apply_auto_shipping_line_item(quote: Quote) -> dict | None:
    breakdown = _shipping_breakdown(quote)
    shipping_item = _shipping_line_item(quote)
    if _is_manual_shipping_override(shipping_item):
        return breakdown
    if breakdown is None:
        return None

    if shipping_item is None:
        _normalize_sort_orders(quote)
        shipping_item = QuoteLineItem(
            quote_id=quote.id,
            product_type="shipping",
            description=AUTO_SHIPPING_DESCRIPTION,
            quantity=1,
            unit_price=float(breakdown["total_cost"]),
            line_total=float(breakdown["total_cost"]),
            specs_json={},
            sort_order=len(quote.line_items) + 1,
        )
        db.session.add(shipping_item)
    else:
        current_specs = dict(shipping_item.specs_json or {})
        if current_specs.get("auto_calculated_shipping") or not (shipping_item.description or "").strip():
            shipping_item.description = AUTO_SHIPPING_DESCRIPTION
        shipping_item.quantity = 1
        shipping_item.unit_price = float(breakdown["total_cost"])
        shipping_item.line_total = float(breakdown["total_cost"])

    shipping_item.specs_json = {
        "auto_calculated_shipping": True,
        "manual_override": False,
        "origin_zip": breakdown["origin_zip"],
        "destination_zip": breakdown["destination_zip"],
        "distance_miles": str(breakdown["distance_miles"]),
        "total_weight_lb": str(breakdown["total_weight_lb"]),
        "rate_per_lb_mile": str(breakdown["rate_per_lb_mile"]),
    }
    return breakdown


def _shipping_config_form_data(cfg: ShippingConfig) -> dict:
    default_rate = _quantize_rate(_decimal_from_raw(cfg.default_rate_per_lb_mile) or Decimal("0.0006"))
    default_length = _quantize_money(_decimal_from_raw(cfg.default_length_ft) or Decimal("10"))
    origins = _shipping_origin_zips(cfg)
    overrides = dict(cfg.rate_overrides_json or {})
    override_lines = []
    for key in sorted(overrides.keys()):
        rate = _decimal_from_raw(overrides.get(key))
        if rate is None or rate <= 0:
            continue
        override_lines.append(f"{key}={_quantize_rate(rate)}")
    return {
        "default_rate_per_lb_mile": default_rate,
        "default_length_ft": default_length,
        "origin_zip_codes": ", ".join(origins),
        "rate_overrides_text": "\n".join(override_lines),
    }


def _parse_rate_overrides(raw_value: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for idx, raw_line in enumerate(raw_value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            abort(400, description=f"Invalid override format on line {idx}")
        key, value = [part.strip() for part in line.split("=", 1)]
        if not key:
            abort(400, description=f"Missing product type on line {idx}")
        rate = _decimal_from_raw(value)
        if rate is None or rate <= 0:
            abort(400, description=f"Invalid rate on line {idx}")
        overrides[key] = str(_quantize_rate(rate))
    return overrides


def _bag_pricing_row_for_diameter(diameter: float | None) -> tuple[str, int] | None:
    if diameter is None:
        return None
    bag_rows = db.session.query(PricingTable).filter_by(product_type="bag").all()
    for row in bag_rows:
        key_fields = dict(row.key_fields or {})
        min_size = _parse_float(str(key_fields.get("pipe_size_min", "")))
        max_size = _parse_float(str(key_fields.get("pipe_size_max", "")))
        if min_size is None or max_size is None:
            continue
        if min_size <= diameter <= max_size:
            part_number = str(key_fields.get("part_number", "")).strip() or "TBD"
            pieces_per_pallet = _parse_int(str(key_fields.get("pieces_per_pallet", ""))) or 0
            return part_number, pieces_per_pallet
    return None


def _line_item_pricing_source(item: QuoteLineItem, specs: dict) -> str:
    if item.product_type == "shipping":
        if specs.get("auto_calculated_shipping"):
            return "Auto shipping calculation"
        return "Manual shipping entry"
    if item.product_type in {"sleeve", "oversleeve"}:
        weight = specs.get("weight_per_ft")
        per_lb = specs.get("price_per_lb")
        if weight and per_lb:
            return f"Price per lb at ${per_lb}/lb, {weight} lbs/ft"
    if item.product_type == "bag":
        diameter = _parse_float(str(specs.get("diameter", "")))
        bag_row = _bag_pricing_row_for_diameter(diameter)
        if bag_row is not None:
            _, pieces_per_pallet = bag_row
            if pieces_per_pallet > 0:
                return f"Pallet pricing, {pieces_per_pallet} pcs per pallet"
    return "Manual / table price"


def _line_item_rounding(item: QuoteLineItem, specs: dict) -> str | None:
    """Compute rounding indicator text for a line item.

    Uses original_qty from specs if available (set when rounding was applied),
    otherwise computes from the current quantity.
    """
    original_qty = _parse_int(str(specs.get("original_qty", "")))
    quantity = int(math.ceil(float(item.quantity or 0)))
    if quantity <= 0:
        return None
    if item.product_type == "sleeve":
        diameter = _parse_float(str(specs.get("diameter", "")))
        length_ft = _parse_float(str(specs.get("length_ft", "")))
        if diameter is not None and length_ft == 10 and diameter <= 24:
            check_qty = original_qty if original_qty is not None else quantity
            rounded, bundles = bundle_round(check_qty, STANDARD_BUNDLE_PIECES)
            if rounded != check_qty:
                return f"{check_qty} pcs \u2192 {bundles} bundle{'s' if bundles != 1 else ''} = {rounded} pcs"
    if item.product_type == "bag":
        diameter = _parse_float(str(specs.get("diameter", "")))
        bag_row = _bag_pricing_row_for_diameter(diameter)
        if bag_row is not None:
            _, pcs_per_pallet = bag_row
            if pcs_per_pallet > 0:
                check_qty = original_qty if original_qty is not None else quantity
                rounded, pallets = pallet_round(check_qty, pcs_per_pallet)
                if rounded != check_qty:
                    return f"{check_qty} pcs \u2192 {pallets} pallet{'s' if pallets != 1 else ''} = {rounded} pcs"
    return None


def _line_item_spec_fields(product_type: str, specs: dict) -> list[dict]:
    if product_type == "sleeve":
        return [
            {"key": "diameter", "label": "Diameter", "type": "number", "step": "0.125", "value": specs.get("diameter", "")},
            {
                "key": "wall_thickness",
                "label": "Wall",
                "type": "number",
                "step": "0.0625",
                "value": specs.get("wall_thickness", ""),
            },
            {"key": "grade", "label": "Grade", "type": "number", "step": "1", "value": specs.get("grade", "")},
            {"key": "length_ft", "label": "Length (ft)", "type": "number", "step": "0.5", "value": specs.get("length_ft", "")},
            {"key": "milling", "label": "Milling", "type": "checkbox", "checked": bool(specs.get("milling"))},
            {"key": "painting", "label": "Painting", "type": "checkbox", "checked": bool(specs.get("painting"))},
        ]
    if product_type == "bag":
        return [
            {"key": "diameter", "label": "GTW Size", "type": "number", "step": "0.125", "value": specs.get("diameter", "")}
        ]
    return []


def _shipping_breakdown_for_item(item: QuoteLineItem) -> dict | None:
    if item.product_type != "shipping":
        return None
    specs = dict(item.specs_json or {})
    if not specs.get("auto_calculated_shipping"):
        return None
    try:
        distance = float(specs.get("distance_miles", "0"))
        weight = float(specs.get("total_weight_lb", "0"))
        rate = float(specs.get("rate_per_lb_mile", "0"))
    except (TypeError, ValueError):
        return None
    return {
        "origin_zip": str(specs.get("origin_zip") or ""),
        "destination_zip": str(specs.get("destination_zip") or ""),
        "distance_miles": distance,
        "total_weight_lb": weight,
        "rate_per_lb_mile": rate,
        "total_cost": float(item.line_total or 0),
    }


def _line_item_view(item: QuoteLineItem) -> dict:
    specs = dict(item.specs_json or {})
    quantity = Decimal(str(item.quantity))
    unit_price = Decimal(str(item.unit_price))
    line_total = Decimal(str(item.line_total))
    original_qty = specs.get("original_qty")
    display_qty = Decimal(str(original_qty)) if original_qty else quantity
    return {
        "id": item.id,
        "product_type": item.product_type,
        "description": item.description,
        "quantity": quantity,
        "display_qty": display_qty,
        "unit_price": unit_price,
        "line_total": line_total,
        "part_number": item.part_number,
        "specs": specs,
        "spec_fields": _line_item_spec_fields(item.product_type, specs),
        "pricing_source": _line_item_pricing_source(item, specs),
        "rounding_indicator": _line_item_rounding(item, specs),
        "needs_pricing": unit_price <= 0 or line_total <= 0,
        "note": specs.get("notes"),
        "shipping_breakdown": _shipping_breakdown_for_item(item),
    }


def _quote_totals(line_items: list[Quote] | list[QuoteLineItem]) -> dict:
    if not line_items:
        return {"subtotal": Decimal("0.00"), "total": Decimal("0.00")}
    if isinstance(line_items[0], Quote):  # dashboard cards
        subtotal = Decimal("0.00")
        for quote in line_items:  # type: ignore[assignment]
            for li in quote.line_items:
                subtotal += Decimal(str(li.line_total))
        return {"subtotal": _quantize_money(subtotal), "total": _quantize_money(subtotal)}
    subtotal = sum((Decimal(str(li.line_total)) for li in line_items), Decimal("0.00"))
    subtotal = _quantize_money(subtotal)
    return {"subtotal": subtotal, "total": subtotal}


def _quote_context(quote: Quote) -> dict:
    line_items = _sorted_line_items(quote)
    shipping_line = _shipping_line_item(quote)
    shipping_breakdown = _shipping_breakdown(quote)
    return {
        "quote": quote,
        "line_items": [_line_item_view(li) for li in line_items],
        "product_type_choices": _product_type_choices(),
        "totals": _quote_totals(line_items),
        "review_user": db.session.get(User, quote.reviewed_by) if quote.reviewed_by else None,
        "shipping_breakdown": shipping_breakdown,
        "shipping_manual_override": _is_manual_shipping_override(shipping_line),
    }


def _render_editor(quote: Quote):
    html = render_template("quotes/_editor.html", **_quote_context(quote))
    return f'<div id="quote-editor">{html}</div>'


def _render_status_bar(quote: Quote):
    return render_template("quotes/_status_bar.html", **_quote_context(quote))


def _render_quote_fields(quote: Quote):
    return render_template("quotes/_quote_fields.html", **_quote_context(quote))


def _render_customer_info(quote: Quote):
    return render_template("quotes/_customer_info.html", **_quote_context(quote))


def _default_customer_ship_to(customer: Customer) -> dict | None:
    address = next((a for a in customer.ship_to_addresses if a.is_default), None)
    if address is None and customer.ship_to_addresses:
        address = customer.ship_to_addresses[0]
    if address is None:
        return None
    return {
        "address_line1": address.address_line1,
        "address_line2": address.address_line2 or "",
        "city": address.city,
        "state": address.state,
        "postal_code": address.postal_code,
        "country": address.country,
    }


def _hydrate_quote_ship_to_from_customer(quote: Quote) -> bool:
    if quote.ship_to_json or quote.customer_id is None:
        return False
    customer = db.session.get(Customer, quote.customer_id)
    if customer is None:
        return False
    default_ship_to = _default_customer_ship_to(customer)
    if not default_ship_to:
        return False
    quote.ship_to_json = default_ship_to
    if not quote.customer_name_raw:
        quote.customer_name_raw = customer.company_name
    return True


def _sync_customer_contact_from_quote(customer: Customer, quote: Quote) -> None:
    if not (quote.contact_name or quote.contact_email or quote.contact_phone):
        return

    contact = None
    if quote.contact_email:
        normalized = quote.contact_email.strip().lower()
        contact = next((c for c in customer.contacts if (c.email or "").strip().lower() == normalized), None)
    if contact is None and customer.contacts:
        contact = customer.contacts[0]

    if contact is None:
        if not quote.contact_email:
            return
        contact = Contact(
            customer_id=customer.id,
            name=quote.contact_name or customer.company_name or "Primary Contact",
            email=quote.contact_email,
            phone=quote.contact_phone or None,
        )
        db.session.add(contact)
        return

    if quote.contact_name:
        contact.name = quote.contact_name
    if quote.contact_email:
        contact.email = quote.contact_email
    contact.phone = quote.contact_phone or None


def _sync_customer_ship_to_from_quote(customer: Customer, quote: Quote) -> None:
    if not quote.ship_to_json:
        return

    incoming = {
        "address_line1": (quote.ship_to_json.get("address_line1") or "").strip(),
        "address_line2": (quote.ship_to_json.get("address_line2") or "").strip(),
        "city": (quote.ship_to_json.get("city") or "").strip(),
        "state": (quote.ship_to_json.get("state") or "").strip(),
        "postal_code": (quote.ship_to_json.get("postal_code") or "").strip(),
        "country": (quote.ship_to_json.get("country") or "").strip(),
    }
    address = next((a for a in customer.ship_to_addresses if a.is_default), None)
    if address is None and customer.ship_to_addresses:
        address = customer.ship_to_addresses[0]
        address.is_default = True

    if address is None:
        if not (incoming["address_line1"] and incoming["city"] and incoming["state"] and incoming["postal_code"]):
            return
        address = ShipToAddress(
            customer_id=customer.id,
            address_line1=incoming["address_line1"],
            address_line2=incoming["address_line2"] or None,
            city=incoming["city"],
            state=incoming["state"],
            postal_code=incoming["postal_code"],
            country=incoming["country"] or "US",
            is_default=True,
        )
        db.session.add(address)
        return

    if incoming["address_line1"]:
        address.address_line1 = incoming["address_line1"]
    address.address_line2 = incoming["address_line2"] or None
    if incoming["city"]:
        address.city = incoming["city"]
    if incoming["state"]:
        address.state = incoming["state"]
    if incoming["postal_code"]:
        address.postal_code = incoming["postal_code"]
    if incoming["country"]:
        address.country = incoming["country"]


def _sync_linked_customer_from_quote(quote: Quote) -> None:
    if quote.customer_id is None:
        return
    customer = db.session.get(Customer, quote.customer_id)
    if customer is None:
        return
    if quote.customer_name_raw:
        customer.company_name = quote.customer_name_raw
    _sync_customer_contact_from_quote(customer, quote)
    _sync_customer_ship_to_from_quote(customer, quote)


def _render_line_items(quote: Quote):
    return render_template("quotes/_line_items.html", **_quote_context(quote))


def _product_types_admin_data(just_saved: bool = False) -> dict:
    rows = _all_product_types()
    return {"product_types": rows, "types_just_saved": just_saved}


@main_bp.get("/quotes/<int:quote_id>")
def quote_detail(quote_id: int):
    quote = db.get_or_404(Quote, quote_id)
    needs_commit = _hydrate_quote_ship_to_from_customer(quote)
    user = _current_user()
    if quote.reviewed_by is None and user is not None:
        quote.reviewed_by = user.id
        if quote.status in {QuoteStatus.NEW, QuoteStatus.NEEDS_PRICING}:
            quote.status = QuoteStatus.IN_REVIEW
        needs_commit = True
    if needs_commit:
        db.session.commit()
    if request.headers.get("HX-Request") == "true":
        return _render_editor(quote)
    return render_template("quotes/detail.html", **_quote_context(quote))


@main_bp.post("/quotes/<int:quote_id>/meta")
def quote_update_meta(quote_id: int):
    quote = db.get_or_404(Quote, quote_id)
    quote.quote_number = (request.form.get("quote_number") or quote.quote_number).strip() or quote.quote_number
    quote.project_name = (request.form.get("project_name") or "").strip() or None
    quote.notes_customer = (request.form.get("notes_customer") or "").strip() or None
    quote.notes_internal = (request.form.get("notes_internal") or "").strip() or None
    db.session.commit()
    return _render_quote_fields(quote)


@main_bp.post("/quotes/<int:quote_id>/customer")
def quote_update_customer(quote_id: int):
    quote = db.get_or_404(Quote, quote_id)
    quote.customer_name_raw = (request.form.get("customer_name_raw") or "").strip() or None
    quote.contact_name = (request.form.get("contact_name") or "").strip() or None
    quote.contact_email = (request.form.get("contact_email") or "").strip() or None
    quote.contact_phone = (request.form.get("contact_phone") or "").strip() or None
    ship_to = {
        "address_line1": (request.form.get("ship_to_address_line1") or "").strip(),
        "address_line2": (request.form.get("ship_to_address_line2") or "").strip(),
        "city": (request.form.get("ship_to_city") or "").strip(),
        "state": (request.form.get("ship_to_state") or "").strip(),
        "postal_code": (request.form.get("ship_to_postal_code") or "").strip(),
        "country": (request.form.get("ship_to_country") or "").strip(),
    }
    if any(ship_to.values()):
        quote.ship_to_json = ship_to
    else:
        quote.ship_to_json = None
    _sync_linked_customer_from_quote(quote)
    _apply_auto_shipping_line_item(quote)
    db.session.commit()
    return _render_editor(quote)


@main_bp.post("/quotes/<int:quote_id>/status")
def quote_update_status(quote_id: int):
    quote = db.get_or_404(Quote, quote_id)
    raw_status = (request.form.get("status") or "").strip().lower()
    user = _current_user()
    status_map = {
        "in_review": QuoteStatus.IN_REVIEW,
        "ready": QuoteStatus.READY,
        "archived": QuoteStatus.ARCHIVED,
    }
    if raw_status not in status_map:
        abort(400, description="Invalid status")
    quote.status = status_map[raw_status]
    if quote.status == QuoteStatus.IN_REVIEW and user is not None:
        quote.reviewed_by = user.id
        quote.review_started_at = datetime.utcnow()
    else:
        quote.reviewed_by = None
        quote.review_started_at = None
    db.session.commit()
    return _render_status_bar(quote)


@main_bp.post("/quotes/<int:quote_id>/line-items/add")
def quote_add_line_item(quote_id: int):
    quote = db.get_or_404(Quote, quote_id)
    _normalize_sort_orders(quote)
    product_type = _resolve_product_type(request.form.get("product_type"), "sleeve")
    auto_shipping_trigger = request.form.get("auto_shipping_trigger") == "1"
    line_item = QuoteLineItem(
        quote=quote,
        product_type=product_type,
        description=(request.form.get("description") or "New line item").strip() or "New line item",
        quantity=float(_parse_decimal(request.form.get("quantity"), Decimal("1"))),
        unit_price=float(_parse_decimal(request.form.get("unit_price"), Decimal("0"))),
        line_total=0,
        specs_json={},
        sort_order=len(quote.line_items) + 1,
    )
    line_item.line_total = float(
        _quantize_money(Decimal(str(line_item.quantity)) * Decimal(str(line_item.unit_price)))
    )
    if line_item.product_type == "shipping":
        line_item.specs_json = {"manual_override": not auto_shipping_trigger, "auto_calculated_shipping": False}
        if auto_shipping_trigger:
            line_item.description = AUTO_SHIPPING_DESCRIPTION
            line_item.quantity = 1
            line_item.unit_price = 0
            line_item.line_total = 0
    db.session.add(line_item)
    _apply_auto_shipping_line_item(quote)
    db.session.commit()
    return _render_line_items(quote)


@main_bp.post("/quotes/<int:quote_id>/line-items/<int:item_id>/calc-total")
def quote_calc_line_item_total(quote_id: int, item_id: int):
    quote = db.get_or_404(Quote, quote_id)
    item = db.get_or_404(QuoteLineItem, item_id)
    if item.quote_id != quote.id:
        abort(404)

    quantity = _parse_decimal(request.form.get("quantity"), Decimal(str(item.quantity)))
    unit_price = _quantize_money(_parse_decimal(request.form.get("unit_price"), Decimal(str(item.unit_price))))

    specs = dict(item.specs_json or {})
    requested_qty = int(math.ceil(float(quantity)))
    rounded_qty = requested_qty
    if item.product_type == "sleeve":
        diameter = _parse_float(str(specs.get("diameter", "")))
        length_ft = _parse_float(str(specs.get("length_ft", "")))
        if diameter is not None and length_ft == 10 and diameter <= 24:
            rounded_qty, _ = bundle_round(requested_qty, STANDARD_BUNDLE_PIECES)
    elif item.product_type == "bag":
        diameter = _parse_float(str(specs.get("diameter", "")))
        bag_row = _bag_pricing_row_for_diameter(diameter)
        if bag_row is not None:
            _, pcs_per_pallet = bag_row
            if pcs_per_pallet > 0:
                rounded_qty, _ = pallet_round(requested_qty, pcs_per_pallet)

    if rounded_qty != requested_qty:
        specs["original_qty"] = str(requested_qty)
    else:
        specs.pop("original_qty", None)

    line_total = _quantize_money(Decimal(str(rounded_qty)) * unit_price)
    preview_item = QuoteLineItem(
        product_type=item.product_type,
        quantity=float(rounded_qty),
        unit_price=float(unit_price),
        line_total=float(line_total),
        specs_json=specs,
    )

    return render_template(
        "quotes/_line_total.html",
        item_id=item.id,
        line_total=line_total,
        rounding_indicator=_line_item_rounding(preview_item, specs),
        quantity=rounded_qty,
        needs_pricing=unit_price <= 0,
    )


@main_bp.post("/quotes/<int:quote_id>/line-items/<int:item_id>/update")
def quote_update_line_item(quote_id: int, item_id: int):
    quote = db.get_or_404(Quote, quote_id)
    item = db.get_or_404(QuoteLineItem, item_id)
    if item.quote_id != quote.id:
        abort(404)

    item.product_type = _resolve_product_type(request.form.get("product_type"), item.product_type)
    auto_shipping_trigger = request.form.get("auto_shipping_trigger") == "1"
    item.description = (request.form.get("description") or item.description).strip() or item.description
    quantity = _parse_decimal(request.form.get("quantity"), Decimal(str(item.quantity)))
    unit_price = _parse_decimal(request.form.get("unit_price"), Decimal(str(item.unit_price)))
    item.unit_price = float(_quantize_money(unit_price))

    specs = dict(item.specs_json or {})
    for key in ("diameter", "wall_thickness", "grade", "length_ft"):
        raw = request.form.get(f"spec_{key}")
        if raw is None:
            continue
        raw = raw.strip()
        if raw == "":
            specs.pop(key, None)
        else:
            specs[key] = raw
    specs["milling"] = request.form.get("spec_milling") == "on"
    specs["painting"] = request.form.get("spec_painting") == "on"

    # Apply pallet/bundle rounding to quantity
    requested_qty = int(math.ceil(float(quantity)))
    rounded_qty = requested_qty
    item.part_number = None
    if item.product_type == "sleeve":
        diameter = _parse_float(str(specs.get("diameter", "")))
        length_ft = _parse_float(str(specs.get("length_ft", "")))
        if diameter is not None and length_ft == 10 and diameter <= 24:
            rounded_qty, _ = bundle_round(requested_qty, STANDARD_BUNDLE_PIECES)
    elif item.product_type == "bag":
        diameter = _parse_float(str(specs.get("diameter", "")))
        bag_row = _bag_pricing_row_for_diameter(diameter)
        if bag_row is not None:
            _, pcs_per_pallet = bag_row
            if pcs_per_pallet > 0:
                rounded_qty, _ = pallet_round(requested_qty, pcs_per_pallet)

    if rounded_qty != requested_qty:
        specs["original_qty"] = str(requested_qty)
    else:
        specs.pop("original_qty", None)

    item.quantity = float(rounded_qty)
    item.line_total = float(_quantize_money(Decimal(str(rounded_qty)) * Decimal(str(item.unit_price))))

    if item.product_type == "sleeve":
        diameter = _parse_float(str(specs.get("diameter", "")))
        wall_thickness = _parse_float(str(specs.get("wall_thickness", "")))
        grade = _parse_int(str(specs.get("grade", "")))
        length_ft = _parse_float(str(specs.get("length_ft", "")))
        if all(v is not None for v in (diameter, wall_thickness, grade, length_ft)):
            item.part_number = generate_sleeve_part_number(
                diameter=diameter,  # type: ignore[arg-type]
                wall_thickness=wall_thickness,  # type: ignore[arg-type]
                grade=grade,  # type: ignore[arg-type]
                length_ft=length_ft,  # type: ignore[arg-type]
                milling=bool(specs.get("milling")),
                painting=bool(specs.get("painting")),
            )
    elif item.product_type == "oversleeve":
        diameter = _parse_float(str(specs.get("diameter", "")))
        wall_thickness = _parse_float(str(specs.get("wall_thickness", "")))
        grade = _parse_int(str(specs.get("grade", "")))
        length_ft = _parse_float(str(specs.get("length_ft", "")))
        if all(v is not None for v in (diameter, wall_thickness, grade, length_ft)):
            item.part_number = generate_oversleeve_part_number(
                diameter=diameter,  # type: ignore[arg-type]
                wall_thickness=wall_thickness,  # type: ignore[arg-type]
                grade=grade,  # type: ignore[arg-type]
                length_ft=length_ft,  # type: ignore[arg-type]
                milling=bool(specs.get("milling")),
                painting=bool(specs.get("painting")),
            )
    elif item.product_type == "girth_weld":
        diameter = _parse_float(str(specs.get("diameter", "")))
        wall_thickness = _parse_float(str(specs.get("wall_thickness", "")))
        grade = _parse_int(str(specs.get("grade", "")))
        length_ft = _parse_float(str(specs.get("length_ft", "")))
        if all(v is not None for v in (diameter, wall_thickness, grade, length_ft)):
            item.part_number = generate_girth_weld_part_number(
                diameter=diameter,  # type: ignore[arg-type]
                wall_thickness=wall_thickness,  # type: ignore[arg-type]
                grade=grade,  # type: ignore[arg-type]
                length_ft=length_ft,  # type: ignore[arg-type]
            )
    elif item.product_type == "compression":
        diameter = _parse_float(str(specs.get("diameter", "")))
        wall_thickness = _parse_float(str(specs.get("wall_thickness", "")))
        grade = _parse_int(str(specs.get("grade", "")))
        if diameter is not None and wall_thickness is not None and grade is not None:
            item.part_number = generate_part_number(
                part_type="compression",
                diameter=normalize_nominal_od(diameter),
                wall_thickness=wall_thickness,
                grade=grade,
            )
    elif item.product_type == "bag":
        diameter = _parse_float(str(specs.get("diameter", "")))
        bag_row = _bag_pricing_row_for_diameter(diameter)
        if bag_row is not None:
            item.part_number = bag_row[0]
    if item.product_type == "shipping":
        if auto_shipping_trigger:
            specs["manual_override"] = False
            specs["auto_calculated_shipping"] = True
            item.description = AUTO_SHIPPING_DESCRIPTION
            item.quantity = 1
            item.unit_price = 0
            item.line_total = 0
        else:
            specs["manual_override"] = True
            specs["auto_calculated_shipping"] = False

    item.specs_json = specs or None
    _apply_auto_shipping_line_item(quote)

    db.session.commit()
    return _render_line_items(quote)


@main_bp.post("/quotes/<int:quote_id>/line-items/<int:item_id>/delete")
def quote_delete_line_item(quote_id: int, item_id: int):
    quote = db.get_or_404(Quote, quote_id)
    item = db.get_or_404(QuoteLineItem, item_id)
    if item.quote_id != quote.id:
        abort(404)
    db.session.delete(item)
    _normalize_sort_orders(quote)
    _apply_auto_shipping_line_item(quote)
    db.session.commit()
    return _render_line_items(quote)


@main_bp.post("/quotes/<int:quote_id>/line-items/<int:item_id>/move")
def quote_move_line_item(quote_id: int, item_id: int):
    quote = db.get_or_404(Quote, quote_id)
    direction = (request.form.get("direction") or "").strip().lower()
    _normalize_sort_orders(quote)
    items = _sorted_line_items(quote)
    idx = next((i for i, li in enumerate(items) if li.id == item_id), None)
    if idx is None:
        abort(404)
    if direction == "up" and idx > 0:
        items[idx].sort_order, items[idx - 1].sort_order = items[idx - 1].sort_order, items[idx].sort_order
    elif direction == "down" and idx < len(items) - 1:
        items[idx].sort_order, items[idx + 1].sort_order = items[idx + 1].sort_order, items[idx].sort_order
    _apply_auto_shipping_line_item(quote)
    db.session.commit()
    return _render_line_items(quote)


@main_bp.get("/admin/pricing")
@login_required
def pricing_admin():
    sections = _group_pricing_rows()
    active_tab = (request.args.get("tab") or "shipping").strip().lower()
    if active_tab not in {"shipping", "pricing", "types"}:
        active_tab = "shipping"
    return render_template(
        "pricing_admin.html",
        sections=sections,
        shipping_config=_shipping_config_form_data(_shipping_config()),
        active_tab=active_tab,
        **_product_types_admin_data(),
    )


@main_bp.post("/admin/pricing/<int:row_id>")
@login_required
def update_pricing_row(row_id: int):
    row = db.session.get(PricingTable, row_id)
    if row is None:
        abort(404)

    raw_value = (request.form.get("price") or "").strip()
    try:
        price = Decimal(raw_value)
    except InvalidOperation:
        abort(400, description="Invalid price value")

    row.price = price.quantize(Decimal("0.01"))
    db.session.commit()
    try:
        from allenedwards.pricing import _clear_pricing_cache

        _clear_pricing_cache()
    except Exception:
        # Pricing module is optional for this admin endpoint.
        pass

    return render_template(
        "partials/pricing_row.html",
        row={
            "id": row.id,
            "price": Decimal(str(row.price)),
            "key_fields": dict(row.key_fields or {}),
            "label": _describe_key_fields(row.product_type, row.key_fields or {}),
        },
    )


@main_bp.post("/admin/shipping-config")
@login_required
def update_shipping_config():
    cfg = _shipping_config()

    default_rate = _decimal_from_raw(request.form.get("default_rate_per_lb_mile"))
    default_length_ft = _decimal_from_raw(request.form.get("default_length_ft"))
    if default_rate is None or default_rate <= 0:
        abort(400, description="Invalid default shipping rate")
    if default_length_ft is None or default_length_ft <= 0:
        abort(400, description="Invalid default product length")

    raw_origins = request.form.get("origin_zip_codes") or ""
    parsed_origins = []
    for token in raw_origins.replace("\n", ",").split(","):
        normalized = _normalize_zip(token.strip())
        if normalized and normalized not in parsed_origins:
            parsed_origins.append(normalized)
    if not parsed_origins:
        abort(400, description="At least one origin ZIP is required")

    cfg.default_rate_per_lb_mile = float(_quantize_rate(default_rate))
    cfg.default_length_ft = float(_quantize_money(default_length_ft))
    cfg.origin_zip_codes_json = parsed_origins
    cfg.rate_overrides_json = _parse_rate_overrides(request.form.get("rate_overrides") or "")
    db.session.commit()

    form_data = _shipping_config_form_data(cfg)
    form_data["just_saved"] = True
    return render_template("partials/shipping_config_form.html", shipping_config=form_data)


@main_bp.post("/admin/product-types/add")
@login_required
def add_product_type():
    display_label = (request.form.get("display_label") or "").strip()
    if not display_label:
        abort(400, description="Display label is required")
    name = re.sub(r"[^a-z0-9]+", "_", display_label.lower()).strip("_")
    if not name:
        abort(400, description="Display label must contain at least one letter or number")
    if db.session.query(ProductType).filter_by(name=name).first() is not None:
        abort(400, description="Product type name already exists")

    max_sort = db.session.query(db.func.max(ProductType.sort_order)).scalar() or 0
    db.session.add(
        ProductType(
            name=name,
            display_label=display_label,
            sort_order=int(max_sort) + 1,
            is_active=True,
        )
    )
    db.session.commit()
    return render_template("partials/product_types_table.html", **_product_types_admin_data(just_saved=True))


@main_bp.post("/admin/product-types/<int:type_id>/update")
@login_required
def update_product_type(type_id: int):
    row = db.session.get(ProductType, type_id)
    if row is None:
        abort(404)

    display_label = (request.form.get("display_label") or "").strip()
    if not display_label:
        abort(400, description="Display label is required")
    row.display_label = display_label
    row.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    return render_template("partials/product_types_table.html", **_product_types_admin_data(just_saved=True))


@main_bp.post("/admin/product-types/<int:type_id>/move")
@login_required
def move_product_type(type_id: int):
    row = db.session.get(ProductType, type_id)
    if row is None:
        abort(404)

    direction = (request.form.get("direction") or "").strip().lower()
    rows = _all_product_types()
    idx = next((i for i, entry in enumerate(rows) if entry.id == type_id), None)
    if idx is None:
        abort(404)
    if direction == "up" and idx > 0:
        rows[idx].sort_order, rows[idx - 1].sort_order = rows[idx - 1].sort_order, rows[idx].sort_order
    elif direction == "down" and idx < len(rows) - 1:
        rows[idx].sort_order, rows[idx + 1].sort_order = rows[idx + 1].sort_order, rows[idx].sort_order
    db.session.commit()
    return render_template("partials/product_types_table.html", **_product_types_admin_data(just_saved=True))


def _db_quote_to_pricing_quote(quote: Quote) -> PricingQuote:
    """Convert a DB Quote model to the pricing.Quote dataclass for PDF generation."""
    line_items = _sorted_line_items(quote)
    shipping_amount = Decimal("0.00")
    pricing_items: list[PricingLineItem] = []
    for li in line_items:
        if li.product_type == "shipping":
            shipping_amount += Decimal(str(li.line_total))
            continue
        pricing_items.append(PricingLineItem(
            sort_order=len(pricing_items) + 1,
            product_type=li.product_type,
            part_number=li.part_number or "",
            description=li.description,
            quantity=int(math.ceil(float(li.quantity))),
            unit_price=Decimal(str(li.unit_price)),
            total=Decimal(str(li.line_total)),
        ))
    subtotal = _quantize_money(sum((item.total for item in pricing_items), Decimal("0.00")))
    shipping_value = _quantize_money(shipping_amount)
    shipping_total = shipping_value if shipping_value > 0 else None
    total = _quantize_money(subtotal + (shipping_total or Decimal("0.00")))
    ship_to = None
    if quote.ship_to_json:
        st = quote.ship_to_json
        ship_to = {
            "company": st.get("address_line1", ""),
            "attention": st.get("address_line2", ""),
            "city": st.get("city", ""),
            "state": st.get("state", ""),
            "postal_code": st.get("postal_code", ""),
            "country": st.get("country", ""),
        }
    return PricingQuote(
        quote_number=quote.quote_number,
        customer_name=quote.customer_name_raw,
        contact_name=quote.contact_name,
        contact_email=quote.contact_email,
        contact_phone=quote.contact_phone,
        ship_to=ship_to,
        line_items=pricing_items,
        subtotal=subtotal,
        shipping_amount=shipping_total,
        tax_amount=Decimal("0.00"),
        total=total,
        notes=quote.notes_customer,
        po_number=quote.po_number,
        project_line=quote.project_name,
    )


def _generate_pdf_bytes(quote: Quote) -> tuple[bytes, str]:
    """Generate PDF for a quote and return (bytes, filename)."""
    pricing_quote = _db_quote_to_pricing_quote(quote)
    filename = f"{quote.quote_number.replace(' ', '_')}.pdf"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        from pathlib import Path
        generate_quote_pdf(pricing_quote, Path(tmp_path))
        with open(tmp_path, "rb") as f:
            pdf_bytes = f.read()
    finally:
        os.unlink(tmp_path)
    return pdf_bytes, filename


@main_bp.get("/quotes/<int:quote_id>/preview-pdf")
def quote_preview_pdf(quote_id: int):
    """Generate and return the quote PDF for inline browser preview."""
    quote = db.get_or_404(Quote, quote_id)
    pdf_bytes, filename = _generate_pdf_bytes(quote)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


@main_bp.get("/quotes/<int:quote_id>/send-form")
def quote_send_form(quote_id: int):
    """Return the send confirmation form as an HTMX partial."""
    quote = db.get_or_404(Quote, quote_id)
    return render_template(
        "quotes/_send_form.html",
        quote=quote,
        default_to=quote.contact_email or "",
        default_subject=f"Quote {quote.quote_number} — Allan Edwards, Inc.",
        default_cc="",
    )


@main_bp.post("/quotes/<int:quote_id>/send")
def quote_send(quote_id: int):
    """Generate PDF, send email via Graph API, update quote status."""
    quote = db.get_or_404(Quote, quote_id)
    user = _current_user()

    to_email = (request.form.get("to_email") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    cc_email = (request.form.get("cc_email") or "").strip() or None

    if not to_email:
        abort(400, description="Recipient email is required")

    # Allowlist check: if SEND_EMAIL_ALLOWLIST is set, only permit listed recipients
    allowlist_raw = os.getenv("SEND_EMAIL_ALLOWLIST", "").strip()
    if allowlist_raw:
        allowed = {e.strip().lower() for e in allowlist_raw.split(",") if e.strip()}
        if to_email.lower() not in allowed:
            return render_template(
                "quotes/_send_result.html",
                success=False,
                error=f"Recipient '{to_email}' is not in the allowed send list. Allowed: {', '.join(sorted(allowed))}",
                quote=quote,
            )

    if not subject:
        subject = f"Quote {quote.quote_number} — Allan Edwards, Inc."

    # Generate PDF
    pdf_bytes, filename = _generate_pdf_bytes(quote)

    # Send via OutlookClient
    from allenedwards.outlook import OutlookAuthError, OutlookClient
    sender_email = os.getenv("O365_EMAIL")
    sender_password = os.getenv("O365_PASSWORD")
    scopes_raw = os.getenv("O365_SCOPES", "")

    if not sender_email or not sender_password:
        return render_template(
            "quotes/_send_result.html",
            success=False,
            error="O365 credentials are not configured. Set O365_EMAIL and O365_PASSWORD.",
            quote=quote,
        )

    scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()] or None
    client = OutlookClient(
        email_address=sender_email,
        password=sender_password,
        scopes=scopes,
    )

    body_text = (
        f"Please find attached quote {quote.quote_number} from Allan Edwards, Inc.\n\n"
        f"If you have any questions, please don't hesitate to contact us.\n\n"
        f"Thank you,\nAllan Edwards, Inc.\n(918) 583-7184\nwww.allanedwards.com"
    )

    enable_drafts = os.environ.get("ENABLE_OUTLOOK_DRAFTS", "true").lower() not in ("0", "false", "no")

    try:
        client.send_mail(
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            attachments=[(filename, pdf_bytes)],
            cc_email=cc_email,
        )
        if enable_drafts:
            client.create_draft(
                to_email=to_email,
                subject=subject,
                body_text=body_text,
                attachments=[(filename, pdf_bytes)],
                cc_email=cc_email,
            )
    except (OutlookAuthError, Exception) as exc:
        return render_template(
            "quotes/_send_result.html",
            success=False,
            error=str(exc),
            quote=quote,
        )

    # Update quote status
    now = datetime.utcnow()
    quote.status = QuoteStatus.SENT
    quote.updated_at = now

    # Create version record
    version_number = len(quote.versions) + 1
    version = QuoteVersion(
        quote_id=quote.id,
        version_number=version_number,
        pdf_path=filename,
        sent_at=now,
        sent_by=user.id if user else None,
        sent_to=to_email,
    )
    db.session.add(version)

    # Audit log
    audit = AuditLog(
        quote_id=quote.id,
        action="sent",
        user_id=user.id if user else None,
        details={"to": to_email, "cc": cc_email, "subject": subject},
    )
    db.session.add(audit)
    db.session.commit()

    return render_template(
        "quotes/_send_result.html",
        success=True,
        quote=quote,
        to_email=to_email,
        sent_at=now,
    )


@main_bp.get("/healthz")
def healthz():
    return {"status": "ok"}
