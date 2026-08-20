"""Persisted line-item specs must never bill freight to a ship-to we do not have.

Task 329 made RFQ-created line items persist the specs they were priced from
(diameter, wall_thickness, length_ft). ``_steel_weight_for_item`` in app/routes.py
reads exactly those keys, so from 329 onward an RFQ quote carries a real freight
weight for the first time and editing a line in the quote editor starts adding the
auto-calculated freight line. That is intended (D12) — the feature was accidentally
dead, not deliberately off — but it is only safe because task 331 made the freight
path ship-to aware. Before 331, quote 126-086's Malaysian postcode 40160 resolved to
US ZIP 40160 (Vine Grove, KY) and billed distance-derived Kentucky freight on a
Malaysian quote (I106).

Neither task could write this test alone. Without 329 the persisted specs are absent,
the weight comes out zero, and every case below produces no freight for the wrong
reason — the test would discriminate on nothing. So each case asserts a nonzero
steel weight as a precondition, and the domestic control at the same postcode proves
the difference is the ship-to, not freight being globally off.
"""

from __future__ import annotations

import os

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import Quote, QuoteLineItem, User
from app.routes import _steel_weight_for_item
from allenedwards.db_writer import write_quote_to_db
from allenedwards.outlook import OutlookMessage
from allenedwards.parser import ParsedItem, ParsedRFQ, ShipTo, _parse_ship_to
from allenedwards.pricing import generate_quote
from decimal import Decimal

# The 126-086 shape: a 36" half sole, 3/8" wall, A572 GR65, 20' long.
_SLEEVE = dict(diameter=36.0, wall_thickness=0.375, grade=65, length_ft=20.0)


def _make_app(db_url):
    os.environ["DATABASE_URL"] = db_url
    Config.SQLALCHEMY_DATABASE_URI = db_url
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["TESTING"] = True
    return app


def _message() -> OutlookMessage:
    return OutlookMessage(
        id="AAMk-freight-337",
        subject='RFQ - 36" half soles',
        sender_email="hasif@example.com",
        sender_name="Hasif",
        body_content="Please quote 4 half soles, ship to the address below.",
        body_preview="Please quote 4 half soles",
        received_datetime="2026-07-29T12:00:00Z",
        has_attachments=False,
        internet_message_id="<freight-337@example.com>",
        body_content_type="text",
    )


def _rfq(ship_to: ShipTo | None) -> ParsedRFQ:
    return ParsedRFQ(
        customer_name="Azimuth Energy",
        contact_name="Hasif",
        contact_email="hasif@example.com",
        contact_phone=None,
        ship_to=ship_to,
        po_number=None,
        quote_number=None,
        items=[
            ParsedItem(
                product_type="sleeve",
                quantity=4,
                description='36" half sole, 3/8" wall, A572 GR65, 20 ft',
                **_SLEEVE,
            )
        ],
        notes=None,
    )


def _seed_rfq_quote(app, quote_number: str, ship_to: ShipTo | None):
    """Build a quote the way the monitor does: price the RFQ, then write it to the DB.

    Deliberately not hand-seeded — the whole point is that the specs reaching
    specs_json are the ones the pricing layer actually priced from (task 329).
    """
    with app.app_context():
        db.create_all()
        user = User(email=f"{quote_number}@example.com", name="Reviewer", password_hash="x")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

        priced = generate_quote(_rfq(ship_to), quote_number)
        db_quote = write_quote_to_db(_message(), _rfq(ship_to), priced, quote_number)

        line = (
            QuoteLineItem.query.filter_by(quote_id=db_quote.id, product_type="sleeve")
            .order_by(QuoteLineItem.sort_order)
            .first()
        )
        assert line is not None, "precondition: the RFQ produced a sleeve line"
        # Precondition (task 329): the specs a reviewer would edit are on the row, so
        # the freight path can actually weigh this line. Without this the cases below
        # would pass for the trivial reason that nothing is weighable.
        specs = dict(line.specs_json or {})
        assert specs.get("diameter") and specs.get("wall_thickness"), specs
        assert _steel_weight_for_item(line, Decimal("10")) > 0, (
            "precondition: persisted specs must yield a real freight weight"
        )
        return db_quote.id, line.id, user_id


def _edit_wall_thickness(app, quote_id: int, item_id: int, user_id: int):
    """The autosave blur that task 329 made re-price — and that now triggers freight."""
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    response = client.post(
        f"/quotes/{quote_id}/line-items/{item_id}/update",
        data={
            "product_type": "sleeve",
            "description": '36" half sole',
            "quantity": "4",
            "unit_price": "0",
            "spec_diameter": "36",
            "spec_wall_thickness": "0.5",
            "spec_grade": "65",
            "spec_length_ft": "20",
        },
    )
    assert response.status_code == 200
    return response


def _shipping_items(app, quote_id: int):
    with app.app_context():
        return (
            db.session.query(QuoteLineItem)
            .filter_by(quote_id=quote_id, product_type="shipping")
            .all()
        )


def test_editing_a_line_adds_no_freight_when_the_rfq_designated_no_ship_to(db_url):
    """Chip's rule: "if people don't provide ship to we just price the product only"."""
    app = _make_app(db_url)
    quote_id, item_id, user_id = _seed_rfq_quote(app, "126-337A", None)

    with app.app_context():
        assert db.session.get(Quote, quote_id).ship_to_json is None

    _edit_wall_thickness(app, quote_id, item_id, user_id)

    assert _shipping_items(app, quote_id) == []


def test_editing_a_line_adds_no_freight_for_a_foreign_ship_to(db_url):
    """The 126-086 collision: Malaysian 40160 is also US ZIP 40160 (Vine Grove, KY)."""
    app = _make_app(db_url)
    quote_id, item_id, user_id = _seed_rfq_quote(
        app,
        "126-337B",
        ShipTo(
            company="Azimuth Energy",
            street="No 47-2, Level 2, Jalan Neutron U16/Q, Denai Alam",
            city="Shah Alam",
            state="Selangor",
            postal_code="40160",
            country="Malaysia",
        ),
    )

    _edit_wall_thickness(app, quote_id, item_id, user_id)

    assert _shipping_items(app, quote_id) == []


def test_editing_a_line_adds_no_freight_when_the_ship_to_scrubbed_to_nothing(db_url):
    """"FOB Tulsa" is a freight term, not a destination — 331 scrubs it, leaving nothing."""
    app = _make_app(db_url)

    scrubbed = _parse_ship_to(
        {"street": "FOB Tulsa", "city": "", "state": "", "postal_code": "", "country": None},
        body="Please ship to the address below.",
    )
    assert scrubbed is None, "precondition: the address was only a freight term"

    quote_id, item_id, user_id = _seed_rfq_quote(app, "126-337C", scrubbed)

    _edit_wall_thickness(app, quote_id, item_id, user_id)

    assert _shipping_items(app, quote_id) == []


def test_editing_a_line_does_add_freight_for_a_domestic_ship_to(db_url):
    """Control: same postcode as the Malaysian case, this time actually in Kentucky.

    Without this the three no-freight cases above would also pass with freight
    switched off entirely.
    """
    app = _make_app(db_url)
    quote_id, item_id, user_id = _seed_rfq_quote(
        app,
        "126-337D",
        ShipTo(
            company="Acme Pipeline Co",
            street="1 Main St",
            city="Vine Grove",
            state="KY",
            postal_code="40160",
            country="United States",
        ),
    )

    _edit_wall_thickness(app, quote_id, item_id, user_id)

    shipping = _shipping_items(app, quote_id)
    assert len(shipping) == 1
    assert float(shipping[0].line_total) > 0
