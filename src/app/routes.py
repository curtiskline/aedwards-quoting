"""Web routes for the quoting app."""

from __future__ import annotations

import io
import math
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP
from decimal import Decimal, InvalidOperation

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request
from flask_login import login_required
from sqlalchemy import inspect

from .extensions import db
from .models import AuditLog, PricingTable, Quote, QuoteLineItem, QuoteStatus, QuoteVersion, User
from allenedwards.pricing import (
    STANDARD_BUNDLE_PIECES,
    bundle_round,
    generate_sleeve_part_number,
    pallet_round,
)
from allenedwards.pricing import Quote as PricingQuote, QuoteLineItem as PricingLineItem
from allenedwards.pdf_generator import generate_quote_pdf

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
@login_required
def dashboard():
    return redirect("/quotes/")


def _format_product_label(product_type: str) -> str:
    return product_type.replace("_", " ").title()


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
    return {
        "quote": quote,
        "line_items": [_line_item_view(li) for li in line_items],
        "totals": _quote_totals(line_items),
        "review_user": db.session.get(User, quote.reviewed_by) if quote.reviewed_by else None,
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


def _render_line_items(quote: Quote):
    return render_template("quotes/_line_items.html", **_quote_context(quote))


@main_bp.get("/quotes/<int:quote_id>")
def quote_detail(quote_id: int):
    quote = db.get_or_404(Quote, quote_id)
    user = _current_user()
    if quote.reviewed_by is None and user is not None:
        quote.reviewed_by = user.id
        if quote.status in {QuoteStatus.NEW, QuoteStatus.NEEDS_PRICING}:
            quote.status = QuoteStatus.IN_REVIEW
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
    db.session.commit()
    return _render_customer_info(quote)


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
    product_type = (request.form.get("product_type") or "sleeve").strip() or "sleeve"
    line_item = QuoteLineItem(
        quote_id=quote.id,
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
    db.session.add(line_item)
    db.session.commit()
    return _render_line_items(quote)


@main_bp.post("/quotes/<int:quote_id>/line-items/<int:item_id>/update")
def quote_update_line_item(quote_id: int, item_id: int):
    quote = db.get_or_404(Quote, quote_id)
    item = db.get_or_404(QuoteLineItem, item_id)
    if item.quote_id != quote.id:
        abort(404)

    item.product_type = (request.form.get("product_type") or item.product_type).strip() or item.product_type
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
    elif item.product_type == "bag":
        diameter = _parse_float(str(specs.get("diameter", "")))
        bag_row = _bag_pricing_row_for_diameter(diameter)
        if bag_row is not None:
            item.part_number = bag_row[0]
    item.specs_json = specs or None

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
    db.session.commit()
    return _render_line_items(quote)


@main_bp.get("/admin/pricing")
@login_required
def pricing_admin():
    sections = _group_pricing_rows()
    return render_template("pricing_admin.html", sections=sections)


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


def _db_quote_to_pricing_quote(quote: Quote) -> PricingQuote:
    """Convert a DB Quote model to the pricing.Quote dataclass for PDF generation."""
    line_items = _sorted_line_items(quote)
    pricing_items = []
    for idx, li in enumerate(line_items, start=1):
        pricing_items.append(PricingLineItem(
            sort_order=idx,
            product_type=li.product_type,
            part_number=li.part_number or "",
            description=li.description,
            quantity=int(math.ceil(float(li.quantity))),
            unit_price=Decimal(str(li.unit_price)),
            total=Decimal(str(li.line_total)),
        ))
    totals = _quote_totals(line_items)
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
        subtotal=totals["subtotal"],
        shipping_amount=None,
        tax_amount=Decimal("0.00"),
        total=totals["total"],
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
    if not subject:
        subject = f"Quote {quote.quote_number} — Allan Edwards, Inc."

    # Generate PDF
    pdf_bytes, filename = _generate_pdf_bytes(quote)

    # Send via OutlookClient
    from allenedwards.outlook import OutlookClient, OutlookAuthError
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
