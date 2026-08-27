"""CP-4 browser-level test: generate -> print -> progress.

Drives real Chromium via Playwright against a live Flask server (same harness
as test_orders_accept_browser.py): an ACCEPTED order is seeded; the test
clicks Generate pick list on the order detail (confirm dialog accepted, htmx
panel swap), opens the printable pick sheet in its popup tab, then walks the
shop queue's one-tap progression picked -> loaded -> shipped, including a
deliberate double-tap that must render the visible no-op notice. Skips (not
passes) when playwright/chromium are unavailable.
"""

from __future__ import annotations

import os
import socket
import threading
from datetime import datetime

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import (
    Order,
    OrderStatus,
    PickList,
    PickListStatus,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    QuoteVersion,
    User,
)
from app.orders import acceptable_version, create_order_from_acceptance
from app.models import AcceptanceSource

PASSWORD = "pick-test-pass"

SNAPSHOT = [
    {
        "product_type": "sleeve",
        "description": "12in sleeve, 10 ft",
        "quantity": 10.0,
        "unit_price": 100.0,
        "line_total": 1000.0,
        "part_number": "SLV-12",
        "specs_json": {"diameter": "12", "length_ft": "10.0"},
        "sort_order": 1,
    },
    {
        "product_type": "shipping",
        "description": "Freight",
        "quantity": 1.0,
        "unit_price": 55.0,
        "line_total": 55.0,
        "part_number": None,
        "specs_json": None,
        "sort_order": 2,
    },
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("pick") / "pick.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    with app.app_context():
        db.create_all()
        user = User(email="pick@example.com", name="Pick Tester", password_hash="")
        user.set_password(PASSWORD)
        db.session.add(user)

        quote = Quote(
            quote_number="PICK-1",
            status=QuoteStatus.SENT,
            customer_name_raw="Acme Pipeline",
            ship_to_json={
                "company": "Acme Pipeline",
                "address_line1": "1 Pipeline Rd",
                "city": "Tulsa",
                "state": "OK",
                "postal_code": "74103",
            },
        )
        db.session.add(quote)
        db.session.flush()
        db.session.add(
            QuoteLineItem(
                quote_id=quote.id,
                product_type="sleeve",
                description="12in sleeve, 10 ft",
                quantity=10,
                unit_price=100,
                line_total=1000,
                specs_json={"diameter": "12", "length_ft": "10.0"},
                sort_order=1,
            )
        )
        db.session.add(
            QuoteVersion(
                quote_id=quote.id,
                version_number=1,
                pdf_path="/tmp/pick-1-v1.pdf",
                artifact_status="retained",
                line_items_snapshot=SNAPSHOT,
                sent_at=datetime(2026, 8, 20, 12, 0),
                sent_to="buyer@acme.com",
            )
        )
        db.session.commit()
        order, _ = create_order_from_acceptance(
            acceptable_version(quote),
            source=AcceptanceSource.EXPLICIT_CLICK,
            actor=user,
            po_number="PO-PICK-1",
        )
        order_id = order.id

    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"app": app, "base_url": f"http://127.0.0.1:{port}", "order_id": order_id}
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser_page(live_app):
    try:
        ctx = playwright_sync.sync_playwright().start()
        browser = ctx.chromium.launch(headless=True)
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"chromium unavailable for playwright: {exc}")
    page = browser.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{live_app['base_url']}/auth/login")
    page.fill("form[action$='/auth/password'] input[name='email']", "pick@example.com")
    page.fill("form[action$='/auth/password'] input[name='password']", PASSWORD)
    page.click("form[action$='/auth/password'] button[type='submit']")
    page.wait_for_load_state("networkidle")
    yield page
    browser.close()
    ctx.stop()


def test_generate_print_progress_flow(live_app, browser_page):
    page = browser_page
    base = live_app["base_url"]
    order_id = live_app["order_id"]

    # 1. Generate the pick list from order detail (confirm auto-accepted).
    page.goto(f"{base}/orders/{order_id}")
    page.wait_for_load_state("networkidle")
    page.click("button:has-text('Generate pick list')")
    page.wait_for_selector("#order-status-panel:has-text('Pick list generated')")
    panel = page.locator("#order-status-panel").inner_text()
    assert "Ordered" in panel
    assert "QUEUED" in panel.upper()

    with live_app["app"].app_context():
        order = db.session.get(Order, order_id)
        assert order.status == OrderStatus.ORDERED
        pick_list = db.session.query(PickList).filter_by(order_id=order_id).one()
        assert pick_list.status == PickListStatus.QUEUED
        pick_list_id = pick_list.id

    # 2. Print pick sheet opens the printable page in a new tab.
    with page.expect_popup() as popup_info:
        page.click("a:has-text('Print pick sheet')")
    sheet = popup_info.value
    sheet.wait_for_load_state("networkidle")
    sheet_text = sheet.locator("body").inner_text()
    assert "PICK SHEET" in sheet_text
    assert "PICK-1" in sheet_text
    assert "1 Pipeline Rd" in sheet_text
    assert "2 bundles" in sheet_text
    assert "Driver signature" not in sheet_text  # unsigned pack manifest (I148.3)
    assert "Freight" not in sheet_text
    sheet.close()

    # 3. Shop queue: one-tap progression picked -> loaded.
    page.goto(f"{base}/pick-lists/")
    page.wait_for_load_state("networkidle")
    assert "PICK-1" in page.locator("#picklists-body").inner_text()
    page.click(f"#pick-list-{pick_list_id} button:has-text('Mark Picked')")
    page.wait_for_selector(f"#pick-list-{pick_list_id} button:has-text('Mark Loaded')")

    # 4. Mis-tap: re-posting the same step shows the visible no-op notice.
    row = page.locator(f"#pick-list-{pick_list_id}")
    page.evaluate(
        """(id) => fetch(`/pick-lists/${id}/status`, {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'status=picked',
        }).then(r => r.text()).then(html => {
            document.getElementById(`pick-list-${id}`).outerHTML = html;
        })""",
        pick_list_id,
    )
    page.wait_for_selector(f"#pick-list-{pick_list_id} .row-notice")
    assert "Already picked" in row.locator(".row-notice").inner_text()

    # 5. Load, then ship — the order flips to FULFILLED.
    page.click(f"#pick-list-{pick_list_id} button:has-text('Mark Loaded')")
    page.wait_for_selector(f"#pick-list-{pick_list_id} button:has-text('Mark Shipped')")
    page.click(f"#pick-list-{pick_list_id} button:has-text('Mark Shipped')")
    page.wait_for_selector(
        f"#pick-list-{pick_list_id} .status-badge:has-text('Shipped')"
    )
    assert page.locator(f"#pick-list-{pick_list_id} .advance-btn").count() == 0

    with live_app["app"].app_context():
        order = db.session.get(Order, order_id)
        assert order.status == OrderStatus.FULFILLED
        pick_list = db.session.get(PickList, pick_list_id)
        assert pick_list.status == PickListStatus.SHIPPED
        assert pick_list.shipped_at is not None

    # 6. Order detail reflects the fulfilled state.
    page.goto(f"{base}/orders/{order_id}")
    page.wait_for_load_state("networkidle")
    panel = page.locator("#order-status-panel").inner_text().upper()
    assert "FULFILLED" in panel
    assert "SHIPPED" in panel
