"""CP-2b browser-level test: the queue dashboard's recommendation display.

Drives real Chromium via Playwright against a live Flask server (same harness
as test_line_item_save_race.py): a recommended and a not-recommended quote are
seeded, and the test asserts the dashboard renders the verdict pills, the
confidence score, the signal dots, and that the recommendation filter tabs
(htmx-swapped) narrow the list. Skips (not passes) when playwright/chromium
are unavailable.
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
from app.confidence import sync_quote_confidence
from app.config import Config
from app.extensions import db
from app.models import Customer, Quote, QuoteLineItem, QuoteStatus, ShipToAddress, User

PASSWORD = "rec-test-pass"
LONG_AGO = datetime(2026, 1, 1)
SPECS = {"diameter": "12", "wall_thickness": "0.375", "grade": "50"}
SHIP_TO = {
    "company": "",
    "attention": "",
    "address_line1": "100 Main St",
    "address_line2": "",
    "city": "Tulsa",
    "state": "OK",
    "postal_code": "74103",
    "country": "US",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _add_line(quote_id: int, unit_price: float, sort_order: int = 1) -> None:
    db.session.add(
        QuoteLineItem(
            quote_id=quote_id,
            product_type="sleeve",
            description="12in sleeve",
            quantity=2,
            unit_price=unit_price,
            line_total=unit_price * 2,
            specs_json=SPECS,
            part_number="SLV-12",
            sort_order=sort_order,
        )
    )
    db.session.flush()


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("rec") / "rec.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.pop("SEND_EMAIL_ALLOWLIST", None)
    os.environ.pop("CONFIDENCE_RECOMMEND_THRESHOLD", None)
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    with app.app_context():
        db.create_all()
        user = User(email="rec@example.com", name="Rec Tester", password_hash="")
        user.set_password(PASSWORD)
        db.session.add(user)

        customer = Customer(company_name="Acme Pipeline", discount_pct=0)
        customer.created_at = LONG_AGO
        db.session.add(customer)
        db.session.flush()
        db.session.add(
            ShipToAddress(
                customer_id=customer.id,
                address_line1=SHIP_TO["address_line1"],
                city=SHIP_TO["city"],
                state=SHIP_TO["state"],
                postal_code=SHIP_TO["postal_code"],
                country=SHIP_TO["country"],
                is_default=True,
                human_confirmed=True,
            )
        )

        # Price history: two SENT quotes for the same product+specs.
        for i, price in enumerate((100.0, 105.0), start=1):
            hist = Quote(
                quote_number=f"REC-HIST-{i}",
                status=QuoteStatus.SENT,
                customer_id=customer.id,
            )
            db.session.add(hist)
            db.session.flush()
            _add_line(hist.id, price)

        # Every signal passes -> recommended.
        good = Quote(
            quote_number="REC-GOOD",
            status=QuoteStatus.READY,
            customer_id=customer.id,
            customer_name_raw="Acme Pipeline",
            contact_email="buyer@acme.com",
            ship_to_json=dict(SHIP_TO),
        )
        db.session.add(good)
        db.session.flush()
        _add_line(good.id, 100.0)
        sync_quote_confidence(good)

        # Unpriced line -> guardrail fail -> not recommended.
        bad = Quote(
            quote_number="REC-BAD",
            status=QuoteStatus.NEW,
            customer_name_raw="Mystery Co",
            contact_email="who@mystery.com",
        )
        db.session.add(bad)
        db.session.flush()
        _add_line(bad.id, 0.0)
        sync_quote_confidence(bad)

        db.session.commit()

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
    page.goto(f"{live_app['base_url']}/auth/login")
    page.fill("form[action$='/auth/password'] input[name='email']", "rec@example.com")
    page.fill("form[action$='/auth/password'] input[name='password']", PASSWORD)
    page.click("form[action$='/auth/password'] button[type='submit']")
    page.wait_for_load_state("networkidle")
    yield page
    browser.close()
    ctx.stop()


def _card_locator(page, quote_number):
    return page.locator(".quote-card", has=page.locator(".quote-number", has_text=quote_number))


def test_dashboard_shows_recommendation_verdicts(live_app, browser_page):
    page = browser_page
    page.goto(f"{live_app['base_url']}/quotes/")
    page.wait_for_load_state("networkidle")

    good = _card_locator(page, "REC-GOOD").first
    assert good.locator(".rec-pill.rec-yes").inner_text().strip().endswith("Recommended")
    assert "100% confidence" in good.locator(".conf-score").inner_text()
    assert good.locator(".signal-dot.signal-pass").count() == 6

    bad = _card_locator(page, "REC-BAD").first
    assert bad.locator(".rec-pill.rec-no").inner_text().strip() == "Not recommended"
    assert bad.locator(".signal-dot.signal-fail").count() >= 1
    # The tooltip carries the WHY.
    title = bad.locator(".rec-pill.rec-no").get_attribute("title")
    assert "All lines priced" in title


def test_dashboard_recommendation_filter_tabs(live_app, browser_page):
    page = browser_page
    page.goto(f"{live_app['base_url']}/quotes/")
    page.wait_for_load_state("networkidle")

    # htmx swap: click the Recommended filter tab.
    page.click("#rec-toolbar a.tab:has-text('Recommended')")
    page.wait_for_selector(".quote-number:has-text('REC-GOOD')")
    assert page.locator(".quote-number", has_text="REC-BAD").count() == 0

    page.click("#rec-toolbar a.tab:has-text('Not recommended')")
    page.wait_for_selector(".quote-number:has-text('REC-BAD')")
    assert page.locator(".quote-number", has_text="REC-GOOD").count() == 0


def test_dashboard_shows_tier_note(live_app, browser_page):
    page = browser_page
    page.goto(f"{live_app['base_url']}/quotes/")
    page.wait_for_load_state("networkidle")
    note = page.locator(".tier-note").inner_text()
    assert "Tier 1" in note
    assert "human" in note
