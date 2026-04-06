"""Web routes for the quoting app."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, render_template, request
from flask_login import login_required
from sqlalchemy import inspect

from .extensions import db
from .models import PricingTable

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


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


@main_bp.get("/healthz")
def healthz():
    return {"status": "ok"}
