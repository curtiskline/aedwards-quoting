from __future__ import annotations

import os

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import PricingTable, Quote, QuoteLineItem, QuoteStatus, User


def _make_app(tmp_path):
    db_path = tmp_path / "quote-editor.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["TESTING"] = True
    return app


def _login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_quote_detail_loads_and_locks_by_user(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        reviewer = User(email="reviewer@example.com", name="Reviewer One", password_hash="x")
        db.session.add(reviewer)
        quote = Quote(quote_number="126-200", status=QuoteStatus.NEW, customer_name_raw="Acme Co")
        db.session.add(quote)
        db.session.flush()
        db.session.add(
            QuoteLineItem(
                quote_id=quote.id,
                product_type="sleeve",
                description="Sleeve",
                quantity=5,
                unit_price=10,
                line_total=50,
                specs_json={"diameter": "24", "length_ft": "10", "weight_per_ft": "5.5", "price_per_lb": "2.1"},
                sort_order=1,
            )
        )
        db.session.commit()
        quote_id = quote.id
        reviewer_id = reviewer.id

    client = app.test_client()
    _login(client, reviewer_id)
    response = client.get(f"/quotes/{quote_id}")
    assert response.status_code == 200
    assert b"Quote 126-200" in response.data
    assert b"In Review" in response.data
    assert b"by Reviewer One" in response.data

    with app.app_context():
        updated = db.session.get(Quote, quote_id)
        assert updated.status == QuoteStatus.IN_REVIEW
        assert updated.reviewed_by is not None


def test_line_item_update_recalculates_and_generates_sleeve_part(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        user = User(email="editor@example.com", name="Editor", password_hash="x")
        db.session.add(user)
        quote = Quote(quote_number="126-201", status=QuoteStatus.IN_REVIEW)
        db.session.add(quote)
        db.session.flush()
        li = QuoteLineItem(
            quote_id=quote.id,
            product_type="sleeve",
            description="Editable Sleeve",
            quantity=1,
            unit_price=1,
            line_total=1,
            specs_json={},
            sort_order=1,
        )
        db.session.add(li)
        db.session.commit()
        quote_id = quote.id
        item_id = li.id
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)
    response = client.post(
        f"/quotes/{quote_id}/line-items/{item_id}/update",
        data={
            "product_type": "sleeve",
            "description": "Updated Sleeve",
            "quantity": "7",
            "unit_price": "10.25",
            "spec_diameter": "24",
            "spec_wall_thickness": "0.5",
            "spec_grade": "50",
            "spec_length_ft": "10",
        },
    )
    assert response.status_code == 200
    assert b"102.50" in response.data

    with app.app_context():
        updated = db.session.get(QuoteLineItem, item_id)
        assert float(updated.line_total) == 102.50
        assert (updated.part_number or "").startswith("S-")


def test_line_item_calc_total_returns_partial_without_db_write(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        user = User(email="calc@example.com", name="Calc User", password_hash="x")
        db.session.add(user)
        quote = Quote(quote_number="126-220", status=QuoteStatus.IN_REVIEW)
        db.session.add(quote)
        db.session.flush()
        li = QuoteLineItem(
            quote_id=quote.id,
            product_type="sleeve",
            description="Reactive sleeve",
            quantity=5,
            unit_price=3,
            line_total=15,
            specs_json={"diameter": "24", "length_ft": "10"},
            sort_order=1,
        )
        db.session.add(li)
        db.session.commit()
        quote_id = quote.id
        item_id = li.id
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)
    response = client.post(
        f"/quotes/{quote_id}/line-items/{item_id}/calc-total",
        data={"quantity": "7", "unit_price": "10.25"},
    )
    assert response.status_code == 200
    assert b'id="line-total-' in response.data
    assert b"$102.50" in response.data
    assert b"editor-line-items" not in response.data

    with app.app_context():
        unchanged = db.session.get(QuoteLineItem, item_id)
        assert unchanged is not None
        assert float(unchanged.quantity) == 5.0
        assert float(unchanged.unit_price) == 3.0
        assert float(unchanged.line_total) == 15.0


def test_add_remove_move_and_status_transitions(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        user = User(email="queue@example.com", name="Queue User", password_hash="x")
        db.session.add(user)
        quote = Quote(quote_number="126-202", status=QuoteStatus.NEW)
        db.session.add(quote)
        db.session.flush()
        first = QuoteLineItem(
            quote_id=quote.id,
            product_type="sleeve",
            description="First",
            quantity=1,
            unit_price=10,
            line_total=10,
            sort_order=1,
        )
        second = QuoteLineItem(
            quote_id=quote.id,
            product_type="bag",
            description="Second",
            quantity=2,
            unit_price=5,
            line_total=10,
            sort_order=2,
            specs_json={"diameter": "24"},
        )
        db.session.add(first)
        db.session.add(second)
        db.session.add(
            PricingTable(
                product_type="bag",
                key_fields={
                    "pipe_size_min": 20,
                    "pipe_size_max": 30,
                    "part_number": "GTW-24",
                    "pieces_per_pallet": 21,
                },
                price=2.0,
            )
        )
        db.session.commit()
        quote_id = quote.id
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)

    add_resp = client.post(
        f"/quotes/{quote_id}/line-items/add",
        data={"product_type": "service", "description": "New service", "quantity": "1", "unit_price": "0"},
    )
    assert add_resp.status_code == 200
    assert b"Needs Pricing" in add_resp.data

    with app.app_context():
        line_items = db.session.query(QuoteLineItem).filter_by(quote_id=quote_id).order_by(QuoteLineItem.sort_order.asc()).all()
        added_id = line_items[-1].id

    move_resp = client.post(f"/quotes/{quote_id}/line-items/{added_id}/move", data={"direction": "up"})
    assert move_resp.status_code == 200

    delete_resp = client.post(f"/quotes/{quote_id}/line-items/{added_id}/delete")
    assert delete_resp.status_code == 200

    ready_resp = client.post(f"/quotes/{quote_id}/status", data={"status": "ready"})
    assert ready_resp.status_code == 200
    assert b"Ready" in ready_resp.data

    archive_resp = client.post(f"/quotes/{quote_id}/status", data={"status": "archived"})
    assert archive_resp.status_code == 200
    assert b"Archived" in archive_resp.data

    detail_resp = client.get(f"/quotes/{quote_id}")
    assert detail_resp.status_code == 200
    assert b"500 pcs" not in detail_resp.data


def test_add_custom_type_line_item_persists_and_renders(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        user = User(email="custom@example.com", name="Custom User", password_hash="x")
        db.session.add(user)
        quote = Quote(quote_number="126-203", status=QuoteStatus.NEW)
        db.session.add(quote)
        db.session.commit()
        quote_id = quote.id
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)
    response = client.post(
        f"/quotes/{quote_id}/line-items/add",
        data={
            "product_type": "custom",
            "custom_product_type": "Field Service Special",
            "description": "Site-specific work",
            "quantity": "2",
            "unit_price": "0",
        },
    )
    assert response.status_code == 200
    assert b"Needs Pricing" in response.data
    assert b"Field Service Special" in response.data

    with app.app_context():
        line_item = db.session.query(QuoteLineItem).filter_by(quote_id=quote_id).first()
        assert line_item is not None
        assert line_item.product_type == "Field Service Special"
        assert float(line_item.unit_price) == 0.0
        assert float(line_item.line_total) == 0.0


def test_add_shipping_line_item(tmp_path):
    app = _make_app(tmp_path)
    with app.app_context():
        db.create_all()
        user = User(email="ship@example.com", name="Ship User", password_hash="x")
        db.session.add(user)
        quote = Quote(quote_number="126-204", status=QuoteStatus.NEW)
        db.session.add(quote)
        db.session.commit()
        quote_id = quote.id
        user_id = user.id

    client = app.test_client()
    _login(client, user_id)
    response = client.post(
        f"/quotes/{quote_id}/line-items/add",
        data={
            "product_type": "shipping",
            "description": "Freight to job site",
            "quantity": "1",
            "unit_price": "1250.00",
        },
    )
    assert response.status_code == 200
    assert b"Shipping &amp; Handling" in response.data
    assert b"Freight to job site" in response.data

    with app.app_context():
        line_item = db.session.query(QuoteLineItem).filter_by(quote_id=quote_id).first()
        assert line_item is not None
        assert line_item.product_type == "shipping"
        assert float(line_item.unit_price) == 1250.0
        assert float(line_item.line_total) == 1250.0
