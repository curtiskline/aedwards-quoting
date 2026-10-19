"""T514 slice 2: work-queue home, navigation shell, CSRF guard, catalog bulk."""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import (
    AcceptanceEvent,
    AcceptanceSource,
    Order,
    OrderStatus,
    PickList,
    PickListStatus,
    ProductCatalog,
    Quote,
    QuoteStatus,
    QuoteVersion,
    StockItem,
    User,
)


@pytest.fixture()
def app(db_url, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    application = create_app()
    with application.app_context():
        _db.create_all()
        owner = User(email="owner@example.com", name="Owner", password_hash="")
        owner.set_password("secret123")
        _db.session.add(owner)
        _db.session.commit()
        yield application
        _db.session.remove()


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post(
        "/auth/password",
        data={"email": "owner@example.com", "password": "secret123"},
        follow_redirects=True,
    )
    return c


def _make_quote(number: str, status: QuoteStatus) -> Quote:
    quote = Quote(
        quote_number=number,
        status=status,
        customer_name_raw="Prairie Bend Utilities",
        ship_to_json={"city": "Broken Arrow", "state": "OK"},
    )
    _db.session.add(quote)
    _db.session.commit()
    return quote


def _make_order(quote: Quote, status: OrderStatus = OrderStatus.ACCEPTED) -> Order:
    version = QuoteVersion(
        quote_id=quote.id,
        version_number=1,
        pdf_path="unused.pdf",
        artifact_status="retained",
        line_items_snapshot=[],
        sent_at=datetime(2026, 10, 18, 9, 0),
    )
    _db.session.add(version)
    _db.session.flush()
    event = AcceptanceEvent(
        quote_version_id=version.id,
        source=AcceptanceSource.EXPLICIT_CLICK,
    )
    _db.session.add(event)
    _db.session.flush()
    order = Order(
        quote_version_id=version.id,
        quote_id=quote.id,
        acceptance_event_id=event.id,
        status=status,
        accepted_at=datetime(2026, 10, 18, 10, 0),
    )
    _db.session.add(order)
    _db.session.commit()
    return order


def test_home_is_work_queue_not_redirect(client):
    response = client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Work queue" in page
    assert "Waiting on us" in page


def test_quote_statuses_become_rows_with_plain_actions(client):
    _make_quote("126-084", QuoteStatus.NEEDS_PRICING)
    _make_quote("126-085", QuoteStatus.READY)
    page = client.get("/").get_data(as_text=True)
    assert "126-084 · Prairie Bend Utilities" in page
    assert "Price the unpriced lines" in page
    assert "Review and send" in page
    assert "Broken Arrow, OK" in page


def test_sent_quote_waits_on_customer_until_ordered(client):
    sent = _make_quote("126-086", QuoteStatus.SENT)
    page = client.get("/?view=customer").get_data(as_text=True)
    assert "126-086" in page
    assert "Waiting for the customer&#39;s go-ahead" in page
    # Once an order exists, the quote stops waiting on the customer.
    _make_order(sent)
    page = client.get("/?view=customer").get_data(as_text=True)
    assert "126-086" not in page
    # The accepted order (no pick list yet) is now work for us.
    page = client.get("/").get_data(as_text=True)
    assert "Generate the pick list" in page


def test_pick_list_stages_name_shop_and_shipping(client):
    quote = _make_quote("126-087", QuoteStatus.SENT)
    order = _make_order(quote)
    pick = PickList(order_id=order.id, status=PickListStatus.QUEUED, lines_snapshot=[])
    _db.session.add(pick)
    _db.session.commit()

    page = client.get("/").get_data(as_text=True)
    assert "Pick the order" in page
    assert ">Shop</td>" in page

    pick.status = PickListStatus.LOADED
    pick.loaded_at = datetime(2026, 10, 19, 8, 0)
    _db.session.commit()
    page = client.get("/").get_data(as_text=True)
    assert "Record it as shipped" in page
    assert ">Shipping</td>" in page


def test_sidebar_shell_marks_active_entry(client):
    page = client.get("/quotes/").get_data(as_text=True)
    assert 'aria-current="page">Quotes' in page
    assert "Shop &amp; shipping" in page
    assert "Stock &amp; purchasing" in page
    # The seat line states the enforced sending mode, in plain words.
    assert "Auto-send off" in page


def test_csrf_blocks_posts_without_token(db_url, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    application = create_app()
    application.config["CSRF_ENFORCE_UNDER_PYTEST"] = True
    with application.app_context():
        _db.create_all()
        owner = User(email="owner@example.com", name="Owner", password_hash="")
        owner.set_password("secret123")
        _db.session.add(owner)
        _db.session.commit()

        c = application.test_client()
        # Even the login post needs the token once enforcement is on.
        bare = c.post(
            "/auth/password",
            data={"email": "owner@example.com", "password": "secret123"},
        )
        assert bare.status_code == 400

        login_page = c.get("/auth/login").get_data(as_text=True)
        token = login_page.split('name="csrf_token" value="')[1].split('"')[0]
        with_token = c.post(
            "/auth/password",
            data={
                "email": "owner@example.com",
                "password": "secret123",
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        assert with_token.status_code == 200

        # Header form works too (the htmx path).
        via_header = c.post(
            "/quotes/", headers={"X-CSRF-Token": token}, follow_redirects=True
        )
        assert via_header.status_code == 200

        # A wrong token is rejected.
        wrong = c.post(
            "/quotes/", data={"csrf_token": "not-the-token"}
        )
        assert wrong.status_code == 400
        _db.session.remove()


def test_inactive_catalog_rows_offer_permanent_delete(client):
    client.post(
        "/admin/catalog/add",
        data={"part_number": "GIR-1", "description": "Girth weld kit"},
    )
    item = _db.session.query(ProductCatalog).filter_by(part_number="GIR-1").one()

    active_page = client.get("/admin/pricing?tab=catalog").get_data(as_text=True)
    assert "Make inactive" in active_page
    assert "Delete permanently" not in active_page

    client.post(f"/admin/catalog/{item.id}/delete")
    inactive_page = client.get("/admin/pricing?tab=catalog").get_data(as_text=True)
    assert "Delete permanently" in inactive_page

    response = client.post(f"/admin/catalog/{item.id}/hard-delete")
    assert response.status_code == 200
    assert "was deleted permanently" in response.get_data(as_text=True)
    assert _db.session.get(ProductCatalog, item.id) is None


def test_hard_delete_keeps_products_with_stock_records(client):
    client.post(
        "/admin/catalog/add",
        data={"part_number": "STK-1", "description": "Stocked sleeve"},
    )
    item = _db.session.query(ProductCatalog).filter_by(part_number="STK-1").one()
    item.is_active = False
    _db.session.add(StockItem(catalog_id=item.id, on_hand=4))
    _db.session.commit()

    response = client.post(f"/admin/catalog/{item.id}/hard-delete")
    assert response.status_code == 200
    assert "stock records" in response.get_data(as_text=True)
    assert _db.session.get(ProductCatalog, item.id) is not None


def test_bulk_deactivate_and_delete(client):
    for n in range(1, 4):
        client.post(
            "/admin/catalog/add",
            data={"part_number": f"BLK-{n}", "description": f"Bulk item {n}"},
        )
    items = (
        _db.session.query(ProductCatalog)
        .filter(ProductCatalog.part_number.like("BLK-%"))
        .all()
    )
    ids = [item.id for item in items]

    response = client.post(
        "/admin/catalog/bulk",
        data={"bulk_action": "deactivate", "item_ids": [str(i) for i in ids]},
    )
    assert response.status_code == 200
    assert "Made 3 products inactive." in response.get_data(as_text=True)
    assert all(not item.is_active for item in items)

    response = client.post(
        "/admin/catalog/bulk",
        data={"bulk_action": "delete", "item_ids": [str(i) for i in ids]},
    )
    assert response.status_code == 200
    assert "Deleted 3 products permanently." in response.get_data(as_text=True)
    assert (
        _db.session.query(ProductCatalog)
        .filter(ProductCatalog.part_number.like("BLK-%"))
        .count()
        == 0
    )


def test_bulk_delete_skips_active_rows_and_says_so(client):
    client.post(
        "/admin/catalog/add",
        data={"part_number": "ACT-1", "description": "Still active"},
    )
    item = _db.session.query(ProductCatalog).filter_by(part_number="ACT-1").one()
    response = client.post(
        "/admin/catalog/bulk",
        data={"bulk_action": "delete", "item_ids": [str(item.id)]},
    )
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Deleted 0 products permanently." in text
    assert "make a product inactive first" in text
    assert _db.session.get(ProductCatalog, item.id) is not None


def test_orders_queue_shows_next_action_and_flow(client):
    quote = _make_quote("126-090", QuoteStatus.SENT)
    order = _make_order(quote)

    page = client.get("/orders/").get_data(as_text=True)
    assert "Customer go-ahead" in page
    assert 'aria-current="step">03 · Sales<b>Order</b>' in page
    assert "Next: Generate the pick list" in page
    assert "waiting on Sales" in page

    pick = PickList(order_id=order.id, status=PickListStatus.QUEUED, lines_snapshot=[])
    _db.session.add(pick)
    _db.session.commit()
    page = client.get("/orders/").get_data(as_text=True)
    assert "Next: Pick the order" in page
    assert "waiting on Shop" in page

    pick.status = PickListStatus.SHIPPED
    order.status = OrderStatus.FULFILLED
    _db.session.commit()
    page = client.get("/orders/").get_data(as_text=True)
    assert "Shipped — nothing left to do." in page


def test_shop_queue_heading_and_flow(client):
    page = client.get("/pick-lists/").get_data(as_text=True)
    assert "Shop &amp; shipping" in page
    assert "one action for each real event" in page
    assert 'aria-current="step">04 · Shop<b>Pick &amp; load</b>' in page
