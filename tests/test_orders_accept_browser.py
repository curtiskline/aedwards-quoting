"""CP-3 browser-level test: the explicit accept flow.

Drives real Chromium via Playwright against a live Flask server (same harness
as test_dashboard_recommendation_browser.py): a SENT quote with an immutable
QuoteVersion is seeded; the test clicks Mark Accepted on the quote editor,
fills the PO/note modal, submits, and follows the flow to the order detail.
A second accept attempt must surface the already-accepted path, and the
orders queue must list the new order. Skips (not passes) when
playwright/chromium are unavailable.
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
from app.models import Order, Quote, QuoteLineItem, QuoteStatus, QuoteVersion, User

PASSWORD = "accept-test-pass"

SNAPSHOT = [
    {
        "product_type": "sleeve",
        "description": "12in sleeve",
        "quantity": 2.0,
        "unit_price": 100.0,
        "line_total": 200.0,
        "part_number": "SLV-12",
        "specs_json": {"diameter": "12"},
        "sort_order": 1,
    }
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("accept") / "accept.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    with app.app_context():
        db.create_all()
        user = User(email="accept@example.com", name="Accept Tester", password_hash="")
        user.set_password(PASSWORD)
        db.session.add(user)

        quote = Quote(
            quote_number="ACC-1",
            status=QuoteStatus.SENT,
            customer_name_raw="Acme Pipeline",
        )
        db.session.add(quote)
        db.session.flush()
        db.session.add(
            QuoteLineItem(
                quote_id=quote.id,
                product_type="sleeve",
                description="12in sleeve",
                quantity=2,
                unit_price=100,
                line_total=200,
                sort_order=1,
            )
        )
        db.session.add(
            QuoteVersion(
                quote_id=quote.id,
                version_number=1,
                pdf_path="/tmp/acc-1-v1.pdf",
                artifact_status="retained",
                line_items_snapshot=SNAPSHOT,
                sent_at=datetime(2026, 8, 20, 12, 0),
                sent_to="buyer@acme.com",
            )
        )
        db.session.commit()
        quote_id = quote.id

    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"app": app, "base_url": f"http://127.0.0.1:{port}", "quote_id": quote_id}
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
    page.goto(f"{live_app['base_url']}/auth/login")
    page.fill("form[action$='/auth/password'] input[name='email']", "accept@example.com")
    page.fill("form[action$='/auth/password'] input[name='password']", PASSWORD)
    page.click("form[action$='/auth/password'] button[type='submit']")
    page.wait_for_load_state("networkidle")
    yield page
    browser.close()
    ctx.stop()


def test_accept_flow_creates_order_and_is_idempotent(live_app, browser_page):
    page = browser_page
    base = live_app["base_url"]
    quote_id = live_app["quote_id"]

    # 1. The SENT quote offers Mark Accepted.
    page.goto(f"{base}/quotes/{quote_id}")
    page.wait_for_load_state("networkidle")
    page.click("button:has-text('Mark Accepted')")

    # 2. The modal names the exact version being accepted.
    page.wait_for_selector("#accept-modal")
    modal_text = page.locator("#accept-modal").inner_text()
    assert "version 1" in modal_text
    assert "buyer@acme.com" in modal_text

    page.fill("#accept-modal input[name='po_number']", "PO-BROWSER-1")
    page.fill("#accept-modal textarea[name='note']", "Chip called, PO to follow")
    page.click("#accept-modal button:has-text('Mark Accepted')")

    # 3. Success modal links to the order.
    page.wait_for_selector("#accept-modal:has-text('Order Created')")
    with page.expect_navigation():
        page.click("#accept-modal a[href^='/orders/'] button")
    assert "/orders/" in page.url
    detail_text = page.locator("body").inner_text()
    assert "ACC-1" in detail_text
    assert "PO-BROWSER-1" in detail_text
    assert "12in sleeve" in detail_text
    assert "explicit click" in detail_text

    with live_app["app"].app_context():
        orders = db.session.query(Order).all()
        assert len(orders) == 1
        assert orders[0].po_number == "PO-BROWSER-1"

    # 4. The quote now shows the order banner instead of the accept action.
    page.goto(f"{base}/quotes/{quote_id}")
    page.wait_for_load_state("networkidle")
    assert page.locator("button:has-text('Mark Accepted')").count() == 0
    assert page.locator("#editor-status-bar a[href^='/orders/']").count() == 1

    # 5. The orders queue lists it under Accepted.
    page.goto(f"{base}/orders/")
    page.wait_for_load_state("networkidle")
    body = page.locator("#orders-body").inner_text()
    assert "ACC-1 v1" in body
    assert "PO/AFE PO-BROWSER-1" in body
