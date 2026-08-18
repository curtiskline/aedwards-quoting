"""Regression test for task 386: editing a line item and clicking a save
button in the same section must not revert the edit.

The quote editor autosaves line-item forms 200ms after focus leaves the card.
Every other button in #editor-line-items (Save Totals, Add Line Item, Up,
Down, Remove) posts immediately and swaps the whole section with HTML rendered
from the database. When the response arrives before the 200ms autosave timer
fires, the swap detaches the edited form, htmx drops the queued autosave for
the detached element, and the user's edit is silently reverted.

This is a browser-level race, so the test drives real Chromium via Playwright
against a live Flask server. It requires the playwright package and its
chromium build; it skips (not passes) when they are unavailable.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import Quote, QuoteLineItem, QuoteStatus, User

PASSWORD = "race-test-pass"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("race") / "race.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    with app.app_context():
        db.create_all()
        user = User(email="race@example.com", name="Race Tester", password_hash="")
        user.set_password(PASSWORD)
        db.session.add(user)
        quote = Quote(
            quote_number="RACE-386",
            status=QuoteStatus.NEW,
            customer_name_raw="Race Customer",
        )
        db.session.add(quote)
        db.session.flush()
        item = QuoteLineItem(
            quote_id=quote.id,
            product_type="sleeve",
            description='12" OD x .375 WT x GR 52 sleeve, 10 ft',
            quantity=10,
            unit_price=100.0,
            line_total=1000.0,
            specs_json={
                "diameter": "12",
                "wall_thickness": ".375",
                "grade": "52",
                "length_ft": "10",
            },
            sort_order=1,
        )
        db.session.add(item)
        db.session.commit()
        quote_id = quote.id
        item_id = item.id

    port = _free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "app": app,
            "base_url": f"http://127.0.0.1:{port}",
            "quote_id": quote_id,
            "item_id": item_id,
        }
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
    page.fill("form[action$='/auth/password'] input[name='email']", "race@example.com")
    page.fill("form[action$='/auth/password'] input[name='password']", PASSWORD)
    page.click("form[action$='/auth/password'] button[type='submit']")
    page.wait_for_load_state("networkidle")
    yield page
    browser.close()
    ctx.stop()


def _item_quantity(live_app) -> float:
    with live_app["app"].app_context():
        db.session.expire_all()
        item = db.session.get(QuoteLineItem, live_app["item_id"])
        return item.quantity


def _wait_for_quantity(live_app, expected: float, timeout: float = 4.0) -> float:
    deadline = time.monotonic() + timeout
    value = _item_quantity(live_app)
    while value != expected and time.monotonic() < deadline:
        time.sleep(0.2)
        value = _item_quantity(live_app)
    return value


def test_qty_edit_survives_save_totals_click(live_app, browser_page):
    """Chip's repro: change Qty, immediately click Save Totals -> edit kept."""
    page = browser_page
    page.goto(f"{live_app['base_url']}/quotes/{live_app['quote_id']}")
    page.wait_for_load_state("networkidle")

    qty = page.locator(f"#line-item-{live_app['item_id']}-form input[name='quantity']")
    qty.fill("15")
    # Click the save button in the same section without blurring first: the
    # totals response swaps #editor-line-items before the 200ms autosave fires.
    page.click("form[action$='/totals'] button[type='submit']")
    page.wait_for_load_state("networkidle")

    assert _wait_for_quantity(live_app, 15.0) == 15.0
    # The re-rendered editor must show the edited quantity, not the old one.
    page.wait_for_load_state("networkidle")
    shown = page.locator(
        f"#line-item-{live_app['item_id']}-form input[name='quantity']"
    ).input_value()
    assert float(shown) == 15.0


def test_qty_edit_survives_add_line_item_click(live_app, browser_page):
    """Same race via the Add Line Item button, which also swaps the section."""
    page = browser_page
    page.goto(f"{live_app['base_url']}/quotes/{live_app['quote_id']}")
    page.wait_for_load_state("networkidle")

    qty = page.locator(f"#line-item-{live_app['item_id']}-form input[name='quantity']")
    qty.fill("20")
    page.click("#add-line-item-form button[type='submit']")
    page.wait_for_load_state("networkidle")

    assert _wait_for_quantity(live_app, 20.0) == 20.0

    # The add itself must also have gone through (a fresh default line item).
    # It is replayed after the flushed autosave, so poll rather than assert
    # immediately.
    def _descriptions() -> list[str]:
        with live_app["app"].app_context():
            db.session.expire_all()
            quote = db.session.get(Quote, live_app["quote_id"])
            return [li.description for li in quote.line_items]

    deadline = time.monotonic() + 4.0
    while "New line item" not in _descriptions() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert "New line item" in _descriptions()
