"""Customer-facing pricing and privacy guards for explicit quote percentages."""

import io
import json
import re
from decimal import Decimal
from unittest.mock import patch

import pytest
from pypdf import PdfReader

from app.extensions import db
from app.fulfillment import build_pick_lines
from app.models import AuditLog, Quote, QuoteLineItem, QuoteVersion
from app.routes import _db_quote_to_pricing_quote, _quote_totals
from app.send_service import build_quote_email_body, quote_line_items_snapshot
from tests.test_pdf_preview_send import _login, _make_app, _seed_quote

REASON = "30% difficult customer handling review"


@pytest.fixture
def setup(db_url, tmp_path):
    app = _make_app(db_url, tmp_path)
    quote_id, user_id = _seed_quote(app)
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        quote.ship_to_json = None
        quote.notes_internal = REASON
        # Match 126-121: every product line is manually overridden.
        for line in quote.line_items:
            line.specs_json = {
                **line.specs_json,
                "price_override": True,
                "auto_unit_price": "123.45",
                "notes": REASON,
            }
        quote.tax_amount = 12
        db.session.add(
            QuoteLineItem(
                quote=quote,
                product_type="shipping",
                description="Freight",
                quantity=1,
                unit_price=20,
                line_total=20,
                specs_json={"manual_override": True},
                sort_order=3,
            )
        )
        db.session.commit()
    client = app.test_client()
    _login(client, user_id)
    return app, client, quote_id


def save(client, quote_id, pct):
    return client.post(
        f"/quotes/{quote_id}/totals",
        data={
            "price_adjustment_pct": pct,
            "shipping_amount": "20",
            "shipping_amount_baseline": "20",
            "tax_amount": "12",
        },
    )


def pdf_text(client, quote_id):
    response = client.get(f"/quotes/{quote_id}/preview-pdf")
    assert response.status_code == 200
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(response.data)).pages)


@pytest.mark.parametrize("pct", ["30", "-5"])
def test_adjustment_reason_never_reaches_pdf_or_email(setup, pct):
    app, client, quote_id = setup
    assert save(client, quote_id, pct).status_code == 200
    text = pdf_text(client, quote_id)
    assert REASON not in text
    assert "difficult" not in text.lower()
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        assert REASON not in build_quote_email_body(quote)
        assert REASON not in json.dumps(quote_line_items_snapshot(quote))
    assert REASON in client.get(f"/quotes/{quote_id}").get_data(as_text=True)


@pytest.mark.parametrize("pct, factor, subtotal", [
    ("30", "1.30", "$2,015.00"), ("-5", "0.95", "$1,472.50"),
])
def test_markup_pdf_indistinguishable_from_ordinary_final_prices(setup, pct, factor, subtotal):
    app, client, quote_id = setup
    save(client, quote_id, pct)
    adjusted = pdf_text(client, quote_id)
    assert subtotal in adjusted
    assert "$1,550.00" not in adjusted
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        quote.price_adjustment_pct = 0
        for item in quote.line_items:
            if item.product_type != "shipping":
                item.unit_price *= Decimal(factor)
                item.line_total *= Decimal(factor)
        db.session.commit()
    assert adjusted == pdf_text(client, quote_id)


def test_discount_pdf_and_downstream_totals(setup):
    app, client, quote_id = setup
    assert save(client, quote_id, "-5").status_code == 200
    text = pdf_text(client, quote_id)
    assert "discount" not in text.lower()
    assert "5%" not in text
    assert "$1,504.50" in text
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        dto = _db_quote_to_pricing_quote(quote)
        assert dto.total == _quote_totals(quote)["total"] == Decimal("1504.50")
        snapshot = quote_line_items_snapshot(quote)
        assert all(row["product_type"] != "discount" for row in snapshot)
        assert sum(Decimal(row["line_total"]) for row in snapshot) == Decimal("1492.50")
        assert len(build_pick_lines(snapshot)) == 2


def test_all_overridden_markup_save_twice_uses_base_once(setup):
    app, client, quote_id = setup
    for _ in range(2):
        response = save(client, quote_id, "30")
        assert response.status_code == 200
        assert (
            "Customer price: $195.00 each; $1950.00 extended (base $150.00 each)"
            in response.get_data(as_text=True)
        )
        with app.app_context():
            quote = db.session.get(Quote, quote_id)
            dto = _db_quote_to_pricing_quote(quote)
            assert [row.unit_price for row in dto.line_items] == [Decimal("195"), Decimal("6.5")]
            assert dto.total == _quote_totals(quote)["total"] == Decimal("2047")
            assert [
                row.unit_price
                for row in sorted(quote.line_items, key=lambda row: row.sort_order)
                if row.product_type != "shipping"
            ] == [Decimal("150"), Decimal("5")]
    save(client, quote_id, "0")
    with app.app_context():
        assert _db_quote_to_pricing_quote(db.session.get(Quote, quote_id)).subtotal == Decimal(
            "1550"
        )


def test_duplicate_to_different_customer_resets_percentage_and_keeps_base(setup):
    app, client, quote_id = setup
    save(client, quote_id, "30")
    response = client.post(
        f"/quotes/{quote_id}/duplicate", data={"new_customer_name": "Different Customer"}
    )
    assert response.status_code == 302
    with app.app_context():
        new = db.session.get(Quote, int(response.location.rsplit("/", 1)[-1]))
        assert new.customer_name_raw == "Different Customer"
        assert new.price_adjustment_pct == 0
        assert _db_quote_to_pricing_quote(new).subtotal == Decimal("1550")


def test_revision_retains_explicit_percentage(setup):
    app, client, quote_id = setup
    save(client, quote_id, "30")
    response = client.post(f"/quotes/{quote_id}/revise")
    assert response.status_code == 302
    with app.app_context():
        new = db.session.get(Quote, int(response.location.rsplit("/", 1)[-1]))
        assert new.price_adjustment_pct == 30
        assert _db_quote_to_pricing_quote(new).subtotal == Decimal("2015")


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-Infinity", "101", "-100.01", "1.001", "oops"]
)
def test_invalid_percentage_rejected_without_changing_quote(setup, value):
    app, client, quote_id = setup
    save(client, quote_id, "30")
    assert save(client, quote_id, value).status_code == 400
    with app.app_context():
        assert db.session.get(Quote, quote_id).price_adjustment_pct == 30


@pytest.mark.parametrize("pct, units, delta", [("30", ["195", "6.5", "20"], "465"), ("-5", ["142.5", "4.75", "20"], "-77.50")])
@patch("allenedwards.outlook.OutlookClient")
def test_manual_send_freezes_adjusted_prices_and_safe_snapshot(mock_outlook, setup, monkeypatch, pct, units, delta):
    app, client, quote_id = setup
    monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("ENABLE_OUTLOOK_DRAFTS", "false")
    monkeypatch.setenv("O365_EMAIL", "sender@example.com")
    monkeypatch.setenv("O365_PASSWORD", "test")
    save(client, quote_id, pct)
    preview = pdf_text(client, quote_id)
    result = client.post(f"/quotes/{quote_id}/send", data={"to_email": "devin@918.software"})
    assert "Quote Sent" in result.get_data(as_text=True)
    sent = mock_outlook.return_value.send_mail.call_args.kwargs
    assert (
        "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(sent["attachments"][0][1])).pages)
        == preview
    )
    with app.app_context():
        version = db.session.query(QuoteVersion).filter_by(quote_id=quote_id).one()
        assert [
            Decimal(row["unit_price"])
            for row in sorted(version.line_items_snapshot, key=lambda row: row["sort_order"])
        ] == [Decimal(value) for value in units]
        audit = db.session.query(AuditLog).filter_by(quote_id=quote_id, action="sent").one()
        assert Decimal(audit.details["price_adjustment_pct"]) == Decimal(pct)
        assert Decimal(audit.details["price_adjustment_amount"]) == Decimal(delta)
        assert audit.details["version_number"] == version.version_number
        serialized = json.dumps(version.line_items_snapshot)
        assert "auto_unit_price" not in serialized
        assert "price_override" not in serialized
        assert REASON not in serialized


@pytest.mark.parametrize("pct, expected", [("30", "2047.00"), ("-5", "1504.50")])
@patch("allenedwards.outlook.OutlookClient")
def test_accept_freezes_the_exact_quoted_total(mock_outlook, setup, monkeypatch, pct, expected):
    from app.models import Order
    from app.orders import _detail_context, _enrich_orders

    app, client, quote_id = setup
    monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("ENABLE_OUTLOOK_DRAFTS", "false")
    monkeypatch.setenv("O365_EMAIL", "sender@example.com")
    monkeypatch.setenv("O365_PASSWORD", "test")
    save(client, quote_id, pct)
    with app.app_context():
        dto = _db_quote_to_pricing_quote(db.session.get(Quote, quote_id))
        assert dto.total == Decimal(expected)
    result = client.post(f"/quotes/{quote_id}/send", data={"to_email": "devin@918.software"})
    assert "Quote Sent" in result.get_data(as_text=True)
    # Editing the live quote after sending must not rewrite the accepted deal.
    save(client, quote_id, "0")
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        quote.tax_amount = 999
        db.session.commit()
    response = client.post(f"/quotes/{quote_id}/accept", data={"po_number": "TEST-468"})
    assert "Order Created" in response.get_data(as_text=True)
    with app.app_context():
        order = db.session.query(Order).filter_by(quote_id=quote_id).one()
        context = _detail_context(order)
        assert context["total"] == dto.total
        assert context["tax"] == Decimal("12.00")
        assert _enrich_orders([order])[0]["total"] == f"${dto.total:,.2f}"
        assert len(build_pick_lines(order.quote_version.line_items_snapshot)) == 2


@pytest.mark.parametrize("pct, expected_units", [("30", ["1.44", "2.77"]), ("-5", ["1.05", "2.02"])])
def test_rendered_adjustment_arithmetic_and_rounding(setup, pct, expected_units):
    app, client, quote_id = setup
    with app.app_context():
        quote = db.session.get(Quote, quote_id)
        products = [row for row in quote.line_items if row.product_type != "shipping"]
        for index, (row, price, quantity) in enumerate(zip(products, ["1.11", "2.13"], [3, 7])):
            row.description = f"Arithmetic item {index}"
            row.unit_price = Decimal(price)
            row.quantity = quantity
            row.line_total = Decimal(price) * quantity
        before_picks = build_pick_lines(quote_line_items_snapshot(quote))
        db.session.commit()
    for _ in range(2):
        save(client, quote_id, pct)
        text = " ".join(pdf_text(client, quote_id).split())
        rows = re.findall(r"Arithmetic item \d+\s+(\d+)\s+\$([\d,.]+)\s+\$([\d,.]+)", text)
        assert len(rows) == 2
        rendered = [(Decimal(q), Decimal(u.replace(",", "")), Decimal(t.replace(",", ""))) for q, u, t in rows]
        assert [unit for _, unit, _ in rendered] == [Decimal(value) for value in expected_units]
        for quantity, unit, total in rendered:
            assert quantity * unit == total
        def amount(label):
            match = re.search(label + r"\s+\$([\d,.]+)", text)
            assert match, text
            return Decimal(match[1].replace(",", ""))
        subtotal = sum(total for _, _, total in rendered)
        assert amount("Subtotal:") == subtotal
        assert amount("Shipping and Handling:") == Decimal("20")
        assert amount("Tax:") == Decimal("12")
        assert amount("TOTAL") == subtotal + amount("Shipping and Handling:") + amount("Tax:")
        with app.app_context():
            quote = db.session.get(Quote, quote_id)
            snapshot = quote_line_items_snapshot(quote)
            assert build_pick_lines(snapshot) == before_picks
            products = [row for row in quote.line_items if row.product_type != "shipping"]
            assert [row.unit_price for row in products] == [Decimal("1.11"), Decimal("2.13")]
