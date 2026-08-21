"""CP-5a browser-level test: ship -> stock decrement visible -> triage resolve.

Same live-server Playwright harness as test_fulfillment_browser.py. A shipped
pick list (walked through the real one-tap queue) must show up in /stock/ as
decremented on-hand counts with unseeded + negative badges, a low-confidence
marker on the pass-2 match in the item history, and the uncatalogued line in
the unmatched triage view — where resolving it applies the owed decrement.
Skips (not passes) when playwright/chromium are unavailable.
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
from app.extensions import db
from app.models import (
    AcceptanceSource,
    ProductCatalog,
    Quote,
    QuoteStatus,
    QuoteVersion,
    StockItem,
    User,
)
from app.orders import acceptable_version, create_order_from_acceptance
from app.fulfillment import create_pick_list

PASSWORD = "stock-test-pass"

SNAPSHOT = [
    {
        "product_type": "sleeve",
        "description": "12in sleeve, 10 ft",
        "quantity": 10.0,
        "unit_price": 100.0,
        "line_total": 1000.0,
        "part_number": "SLV-12",
        "specs_json": None,
        "sort_order": 1,
    },
    {
        "product_type": "composite",
        "description": "Wrap kit",
        "quantity": 3.0,
        "unit_price": 50.0,
        "line_total": 150.0,
        "part_number": None,
        "specs_json": None,
        "sort_order": 2,
    },
    {
        "product_type": "accessory",
        "description": "Mystery widget nobody catalogued",
        "quantity": 7.0,
        "unit_price": 5.0,
        "line_total": 35.0,
        "part_number": None,
        "specs_json": None,
        "sort_order": 3,
    },
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("stock") / "stock.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    from app.config import Config

    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    with app.app_context():
        db.create_all()
        user = User(email="stock@example.com", name="Stock Tester", password_hash="")
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.add(
            ProductCatalog(
                part_number="SLV-12",
                description="12in sleeve, 10 ft",
                product_type="sleeve",
            )
        )
        db.session.add(
            ProductCatalog(
                part_number=None, description="Wrap kit", product_type="composite"
            )
        )

        quote = Quote(
            quote_number="STK-1",
            status=QuoteStatus.SENT,
            customer_name_raw="Acme Pipeline",
            ship_to_json={"company": "Acme Pipeline"},
        )
        db.session.add(quote)
        db.session.flush()
        db.session.add(
            QuoteVersion(
                quote_id=quote.id,
                version_number=1,
                pdf_path="/tmp/stk-1-v1.pdf",
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
            po_number="PO-STK-1",
        )
        create_pick_list(order, user)

    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"app": app, "base_url": f"http://127.0.0.1:{port}"}
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
    page.fill("form[action$='/auth/password'] input[name='email']", "stock@example.com")
    page.fill("form[action$='/auth/password'] input[name='password']", PASSWORD)
    page.click("form[action$='/auth/password'] button[type='submit']")
    page.wait_for_load_state("networkidle")
    yield page
    browser.close()
    ctx.stop()


def test_ship_decrement_and_triage_flow(live_app, browser_page):
    page = browser_page
    base = live_app["base_url"]

    # 1. Walk the shop queue picked -> loaded -> shipped (real one-tap taps).
    page.goto(f"{base}/pick-lists/")
    page.wait_for_load_state("networkidle")
    page.click("button:has-text('Mark Picked')")
    page.wait_for_selector("button:has-text('Mark Loaded')")
    page.click("button:has-text('Mark Loaded')")
    page.wait_for_selector("button:has-text('Mark Shipped')")
    page.click("button:has-text('Mark Shipped')")
    page.wait_for_selector(".advance-btn", state="detached")

    # 2. Stock list shows the decremented counts with honest badges.
    page.goto(f"{base}/stock/")
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "SLV-12" in body
    assert "-10" in body  # sleeve decrement, unseeded count
    assert "-3" in body  # wrap kit decrement (pass-2 match)
    assert "unseeded" in body
    assert "1 unmatched shipment" in body

    # 3. Item history renders the low-confidence marker on the pass-2 match.
    page.click("a:has-text('Wrap kit')")
    page.wait_for_load_state("networkidle")
    detail = page.locator("body").inner_text()
    assert "SHIPMENT_DECREMENT" in detail
    assert "low-confidence match" in detail

    # 4. Triage: the uncatalogued line waits with its frozen facts; resolving
    #    it assigns a product and applies the owed decrement.
    page.goto(f"{base}/stock/unmatched")
    page.wait_for_load_state("networkidle")
    triage = page.locator("body").inner_text()
    assert "Mystery widget nobody catalogued" in triage
    assert "7 pieces" in triage

    page.select_option("select[name='catalog_id']", label="SLV-12 — 12in sleeve, 10 ft")
    page.click("button:has-text('Resolve')")
    page.wait_for_load_state("networkidle")
    assert "Nothing to triage" in page.locator("body").inner_text()

    with live_app["app"].app_context():
        from app.models import ProductCatalog as PC

        sleeve = (
            db.session.query(StockItem)
            .join(PC, StockItem.catalog_id == PC.id)
            .filter(PC.part_number == "SLV-12")
            .one()
        )
        assert sleeve.on_hand == -17  # -10 shipped + -7 resolved

    # 5. The stock list reflects the resolution and the banner is gone.
    page.goto(f"{base}/stock/")
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "-17" in body
    assert "unmatched shipment" not in body
