"""Customer database CRUD routes and auto-match."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import or_

from .extensions import db
from .models import Contact, Customer, Quote, ShipToAddress

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")


# ---------------------------------------------------------------------------
# Auto-match
# ---------------------------------------------------------------------------


def auto_match(
    company_name: str | None = None,
    contact_email: str | None = None,
    contact_name: str | None = None,
) -> dict | None:
    """Find the best-matching Customer record.

    Returns ``{"customer": Customer, "confidence": float, "match_type": str}``
    or *None* when no plausible match is found.
    """
    # 1. Exact email match (highest confidence)
    if contact_email:
        contact = (
            db.session.query(Contact)
            .filter(db.func.lower(Contact.email) == contact_email.strip().lower())
            .first()
        )
        if contact:
            return {
                "customer": contact.customer,
                "confidence": 1.0,
                "match_type": "email",
            }

    # 2. Company name — exact (case-insensitive)
    if company_name:
        name_lower = company_name.strip().lower()
        exact = (
            db.session.query(Customer)
            .filter(db.func.lower(Customer.company_name) == name_lower)
            .first()
        )
        if exact:
            return {
                "customer": exact,
                "confidence": 0.95,
                "match_type": "company_exact",
            }

        # 3. Company name — substring / LIKE
        like_match = (
            db.session.query(Customer)
            .filter(db.func.lower(Customer.company_name).contains(name_lower))
            .first()
        )
        if like_match:
            return {
                "customer": like_match,
                "confidence": 0.7,
                "match_type": "company_partial",
            }

    # 4. Contact name — exact (case-insensitive)
    if contact_name:
        name_lower = contact_name.strip().lower()
        contact = (
            db.session.query(Contact)
            .filter(db.func.lower(Contact.name) == name_lower)
            .first()
        )
        if contact:
            return {
                "customer": contact.customer,
                "confidence": 0.8,
                "match_type": "contact_name",
            }

    return None


# ---------------------------------------------------------------------------
# JSON auto-match endpoint (for monitor pipeline / quote editor)
# ---------------------------------------------------------------------------


@customers_bp.get("/api/match")
def api_match():
    """Return best-matching customer as JSON."""
    result = auto_match(
        company_name=request.args.get("company"),
        contact_email=request.args.get("email"),
        contact_name=request.args.get("contact"),
    )
    if not result:
        return jsonify({"match": None}), 200
    c = result["customer"]
    default_addr = next((a for a in c.ship_to_addresses if a.is_default), None)
    return jsonify(
        {
            "match": {
                "customer_id": c.id,
                "company_name": c.company_name,
                "discount_pct": float(c.discount_pct),
                "confidence": result["confidence"],
                "match_type": result["match_type"],
                "default_ship_to": _addr_dict(default_addr) if default_addr else None,
            }
        }
    ), 200


def _addr_dict(a: ShipToAddress) -> dict:
    return {
        "id": a.id,
        "address_line1": a.address_line1,
        "address_line2": a.address_line2,
        "city": a.city,
        "state": a.state,
        "postal_code": a.postal_code,
        "country": a.country,
    }


# ---------------------------------------------------------------------------
# Customer list
# ---------------------------------------------------------------------------


@customers_bp.get("/")
def customer_list():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "company_name")
    order = request.args.get("order", "asc")

    query = db.session.query(Customer)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Customer.company_name.ilike(like),
                Customer.notes.ilike(like),
                Customer.contacts.any(Contact.name.ilike(like)),
                Customer.contacts.any(Contact.email.ilike(like)),
            )
        )

    col = getattr(Customer, sort, Customer.company_name)
    query = query.order_by(col.asc() if order == "asc" else col.desc())
    customers = query.all()

    if request.headers.get("HX-Request"):
        return render_template("customers/_table.html", customers=customers, q=q, sort=sort, order=order)
    return render_template("customers/list.html", customers=customers, q=q, sort=sort, order=order)


# ---------------------------------------------------------------------------
# Customer detail
# ---------------------------------------------------------------------------


@customers_bp.get("/<int:customer_id>")
def customer_detail(customer_id: int):
    customer = db.get_or_404(Customer, customer_id)
    quotes = db.session.query(Quote).filter_by(customer_id=customer_id).order_by(Quote.created_at.desc()).all()
    return render_template("customers/detail.html", customer=customer, quotes=quotes)


# ---------------------------------------------------------------------------
# Create customer
# ---------------------------------------------------------------------------


@customers_bp.get("/new")
def customer_new():
    return render_template("customers/form.html", customer=None)


@customers_bp.post("/")
def customer_create():
    customer = Customer(
        company_name=request.form["company_name"],
        discount_pct=float(request.form.get("discount_pct") or 0),
        notes=request.form.get("notes") or None,
    )
    db.session.add(customer)
    db.session.flush()  # get id

    _save_contacts(customer)
    _save_addresses(customer)

    db.session.commit()

    if request.headers.get("HX-Request"):
        return "", 200, {"HX-Redirect": f"/customers/{customer.id}"}
    from flask import redirect
    return redirect(f"/customers/{customer.id}")


# ---------------------------------------------------------------------------
# Edit customer
# ---------------------------------------------------------------------------


@customers_bp.get("/<int:customer_id>/edit")
def customer_edit(customer_id: int):
    customer = db.get_or_404(Customer, customer_id)
    return render_template("customers/form.html", customer=customer)


@customers_bp.post("/<int:customer_id>")
def customer_update(customer_id: int):
    customer = db.get_or_404(Customer, customer_id)
    customer.company_name = request.form["company_name"]
    customer.discount_pct = float(request.form.get("discount_pct") or 0)
    customer.notes = request.form.get("notes") or None

    # Replace contacts and addresses
    for c in list(customer.contacts):
        db.session.delete(c)
    for a in list(customer.ship_to_addresses):
        db.session.delete(a)
    db.session.flush()

    _save_contacts(customer)
    _save_addresses(customer)

    db.session.commit()

    if request.headers.get("HX-Request"):
        return "", 200, {"HX-Redirect": f"/customers/{customer.id}"}
    from flask import redirect
    return redirect(f"/customers/{customer.id}")


# ---------------------------------------------------------------------------
# Delete customer
# ---------------------------------------------------------------------------


@customers_bp.delete("/<int:customer_id>")
def customer_delete(customer_id: int):
    customer = db.get_or_404(Customer, customer_id)
    db.session.delete(customer)
    db.session.commit()

    if request.headers.get("HX-Request"):
        return "", 200, {"HX-Redirect": "/customers/"}
    from flask import redirect
    return redirect("/customers/")


# ---------------------------------------------------------------------------
# HTMX partials — add contact / address row
# ---------------------------------------------------------------------------


@customers_bp.get("/partial/contact-row")
def partial_contact_row():
    idx = request.args.get("idx", 0, type=int)
    return render_template("customers/_contact_row.html", idx=idx, contact=None)


@customers_bp.get("/partial/address-row")
def partial_address_row():
    idx = request.args.get("idx", 0, type=int)
    return render_template("customers/_address_row.html", idx=idx, address=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _save_contacts(customer: Customer) -> None:
    """Save contacts from form multi-fields."""
    names = request.form.getlist("contact_name")
    emails = request.form.getlist("contact_email")
    phones = request.form.getlist("contact_phone")

    for i, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        email = emails[i].strip() if i < len(emails) else ""
        if not email:
            continue
        phone = phones[i].strip() if i < len(phones) else None
        contact = Contact(
            customer_id=customer.id,
            name=name,
            email=email,
            phone=phone or None,
        )
        db.session.add(contact)


def _save_addresses(customer: Customer) -> None:
    """Save ship-to addresses from form multi-fields."""
    lines1 = request.form.getlist("addr_line1")
    lines2 = request.form.getlist("addr_line2")
    cities = request.form.getlist("addr_city")
    states = request.form.getlist("addr_state")
    zips = request.form.getlist("addr_zip")
    defaults = request.form.getlist("addr_default")

    for i, line1 in enumerate(lines1):
        line1 = line1.strip()
        if not line1:
            continue
        city = cities[i].strip() if i < len(cities) else ""
        state = states[i].strip() if i < len(states) else ""
        postal = zips[i].strip() if i < len(zips) else ""
        if not (city and state and postal):
            continue
        addr = ShipToAddress(
            customer_id=customer.id,
            address_line1=line1,
            address_line2=(lines2[i].strip() if i < len(lines2) else None) or None,
            city=city,
            state=state,
            postal_code=postal,
            is_default=str(i) in defaults,
        )
        db.session.add(addr)
