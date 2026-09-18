"""Exercise add, reference recall, and autosave in the actual quote editor."""

import threading
from datetime import datetime

import pytest
from werkzeug.serving import make_server

from app.extensions import db
from app.models import Quote, QuoteLineItem
from tests.test_on_site_fill import editor  # noqa: F401


def test_browser_fill_recall_never_prefills_price(editor):  # noqa: F811
    playwright = pytest.importorskip("playwright.sync_api")
    app, client, qid = editor
    with app.app_context():
        prior = Quote(quote_number="HISTORY-BROWSER", customer_name_raw="Prior customer")
        db.session.add(prior)
        db.session.add(
            QuoteLineItem(
                quote=prior,
                product_type="on_site_fill",
                description="Historical fill job",
                quantity=10,
                unit_price=300,
                line_total=3000,
                on_site_label="Prior crossing",
                on_site_city="Baton Rouge",
                on_site_state="LA",
                on_site_priced_at=datetime(2026, 8, 1),
            )
        )
        db.session.commit()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context()
            context.add_cookies(
                [
                    {
                        "name": "session",
                        "value": client.get_cookie("session").value,
                        "domain": "127.0.0.1",
                        "path": "/",
                    }
                ]
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/quotes/{qid}")
            page.wait_for_function("!!window.htmx")
            add = page.locator("#add-line-item-form")
            add.locator("[name=unit_price]").fill("99")
            add.locator("[name=product_type]").select_option("on_site_fill")
            assert add.locator("[name=unit_price]").input_value() == ""
            for key, value in {
                "spec_diameter": "36",
                "spec_fill_weight_lb": "9000",
                "on_site_label": "New crossing",
                "on_site_city": "Baton Rouge",
                "on_site_state": "LA",
            }.items():
                add.locator(f"[name={key}]").fill(value)
            playwright.expect(add.locator(".js-fill-history")).to_contain_text(
                "Historical fill job"
            )
            assert add.locator("[name=unit_price]").input_value() == ""
            add.locator("[name=unit_price]").fill("349.50")
            add.get_by_role("button", name="Add Line Item").click()
            line = page.locator(".line-item-form").first
            playwright.expect(line.locator("[name=part_number]")).to_have_value(
                "GTB-36-9000-ONSITE"
            )
            assert line.locator("[name=unit_price]").input_value() == "349.50"
            line.locator("[name=spec_fill_weight_lb]").fill("12000")
            page.locator(".line-items-section h2").click()
            playwright.expect(line.locator("[name=description]")).to_have_value(
                "Geotextile Bag Weight 36in Pipe 12,000 lb Fill — On-site Filling"
            )
            assert line.locator("[name=unit_price]").input_value() == "349.50"
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
