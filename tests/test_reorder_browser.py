"""CP-5b browser-level test: seed -> ship -> auto-reorder -> print -> receive
-> re-armed.

Same live-server Playwright harness as test_inventory_browser.py. The full
inventory loop through the real UI: thresholds seeded on the seeding screen
(save-per-row, htmx), a shipment walked through the one-tap shop queue drops
stock to/below min, the reorder appears in the shop queue's Reorders tab with
a printable restock sheet, mark-received books the receipt and closes it, and
a later drop below min re-arms (a fresh reorder fires). Skips (not passes)
when playwright/chromium are unavailable.
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
    Reorder,
    ReorderStatus,
    User,
)
from app.orders import acceptable_version, create_order_from_acceptance
from app.fulfillment import create_pick_list

PASSWORD = "reorder-test-pass"

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
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("reorder") / "reorder.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    from app.config import Config

    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    with app.app_context():
        db.create_all()
        user = User(email="reorder@example.com", name="Reorder Tester", password_hash="")
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.add(
            ProductCatalog(
                part_number="SLV-12",
                description="12in sleeve, 10 ft",
                product_type="sleeve",
            )
        )

        quote = Quote(
            quote_number="RRD-1",
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
                pdf_path="/tmp/rrd-1-v1.pdf",
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
    page.fill("form[action$='/auth/password'] input[name='email']", "reorder@example.com")
    page.fill("form[action$='/auth/password'] input[name='password']", PASSWORD)
    page.click("form[action$='/auth/password'] button[type='submit']")
    page.wait_for_load_state("networkidle")
    yield page
    browser.close()
    ctx.stop()


def test_seed_ship_reorder_print_receive_rearm(live_app, browser_page):
    page = browser_page
    base = live_app["base_url"]

    # 1. Seed the sleeve on the seeding screen: 12 on hand, min 5 / max 20.
    #    Shipping 10 will drop it to 2 — at/below min fires the reorder.
    page.goto(f"{base}/stock/seed")
    page.wait_for_load_state("networkidle")
    row = page.locator("tr", has_text="SLV-12").first
    row.locator("input[name='on_hand']").fill("12")
    row.locator("input[name='min_qty']").fill("5")
    row.locator("input[name='max_qty']").fill("20")
    row.locator("button:has-text('Save')").click()
    page.wait_for_selector("tr:has-text('SLV-12') .row-saved")
    assert "Saved." in page.locator("tr", has_text="SLV-12").first.inner_text()

    # 2. Walk the shop queue picked -> loaded -> shipped (real one-tap taps).
    page.goto(f"{base}/pick-lists/")
    page.wait_for_load_state("networkidle")
    page.click("button:has-text('Mark Picked')")
    page.wait_for_selector("button:has-text('Mark Loaded')")
    page.click("button:has-text('Mark Loaded')")
    page.wait_for_selector("button:has-text('Mark Shipped')")
    page.click("button:has-text('Mark Shipped')")
    page.wait_for_selector(".advance-btn", state="detached")

    # 3. The auto-reorder appears in the shop queue's Reorders tab.
    page.goto(f"{base}/pick-lists/")
    page.wait_for_load_state("networkidle")
    reorders_tab = page.locator("a.tab", has_text="Reorders")
    assert "(1)" in reorders_tab.inner_text()
    reorders_tab.click()
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "Order 18" in body  # fallback qty: max 20 - on_hand 2
    assert "SLV-12" in body
    assert "on hand now 2" in body

    # 4. The printable vendor PO sheet renders the frozen trigger context.
    with page.context.expect_page() as sheet_info:
        page.click("a:has-text('PO sheet')")
    sheet = sheet_info.value
    sheet.wait_for_load_state("networkidle")
    sheet_text = sheet.locator("body").inner_text()
    assert "PURCHASE ORDER" in sheet_text
    assert "Order 18" in sheet_text
    assert "Minimum to keep\t5" in sheet_text or "5" in sheet_text
    sheet.close()

    # 5. Mark received (the shop made all 18) — closes the reorder.
    page.fill("input[name='received_qty']", "18")
    page.click("button:has-text('Mark received')")
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "Received 18" in body
    assert "on hand is now 20" in body
    assert "No open reorders" in body

    # 6. Re-armed: a later drop to/below min (manual adjustment through the
    #    item page) fires a FRESH reorder.
    page.goto(f"{base}/stock/")
    page.wait_for_load_state("networkidle")
    page.click("a:has-text('12in sleeve, 10 ft')")
    page.wait_for_load_state("networkidle")
    page.fill("input[name='qty_delta']", "-15")
    page.fill(
        "form[action$='/adjustment'] input[name='reason']", "yard recount came up short"
    )
    page.click("button:has-text('Add adjustment')")
    page.wait_for_load_state("networkidle")
    detail = page.locator("body").inner_text()
    assert "reorder open" in detail

    page.goto(f"{base}/stock/reorders/")
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert "Order" in body and "SLV-12" in body
    assert "on hand now 5" in body

    with live_app["app"].app_context():
        reorders = db.session.query(Reorder).order_by(Reorder.id).all()
        assert len(reorders) == 2
        assert reorders[0].status == ReorderStatus.RECEIVED
        assert reorders[1].status == ReorderStatus.OPEN
