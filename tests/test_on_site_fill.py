"""On-site fill is manually sourced per location; history must never price it."""

from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.extensions import db
from app.models import PricingTable, Quote, QuoteLineItem, QuoteStatus, User
from app.send_service import quote_line_items_snapshot
from tests.test_quote_editor import _login, _make_app


@pytest.fixture
def editor(db_url):
    app = _make_app(db_url)
    with app.app_context():
        db.create_all()
        user = User(email="fill@example.test", name="Fill Tester", password_hash="x")
        quote = Quote(
            quote_number="FILL-465",
            customer_name_raw="Current customer",
            status=QuoteStatus.IN_REVIEW,
        )
        db.session.add_all([user, quote])
        db.session.commit()
        uid, qid = user.id, quote.id
    client = app.test_client()
    _login(client, uid)
    return app, client, qid


def form(**overrides):
    return {
        "product_type": "on_site_fill",
        "quantity": "20",
        "unit_price": "349.50",
        "spec_diameter": "36",
        "spec_fill_weight_lb": "9000",
        "on_site_label": "Louisiana river crossing",
        "on_site_city": "Baton Rouge",
        "on_site_state": "LA",
        "on_site_price_source": "Local sand and labor phone estimate",
        **overrides,
    }


def add(editor, **overrides):
    _, client, qid = editor
    response = client.post(f"/quotes/{qid}/line-items/add", data=form(**overrides))
    assert response.status_code == 200
    return response


def pdf_text(client, qid):
    response = client.get(f"/quotes/{qid}/preview-pdf")
    assert response.status_code == 200
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.data)).pages)


def test_manual_fill_generation_edit_and_customer_pdf(editor):
    app, client, qid = editor
    add(editor)
    with app.app_context():
        line = db.session.query(QuoteLineItem).filter_by(quote_id=qid).one()
        lid = line.id
        assert line.part_number == "GTB-36-9000-ONSITE"
        assert line.description == "Geotextile Bag Weight 36in Pipe 9,000 lb Fill - On-site Filling"
        assert line.unit_price == Decimal("349.50")
        assert line.line_total == Decimal("6990.00")
        assert line.on_site_label == "Louisiana river crossing"
        assert line.on_site_city == "Baton Rouge" and line.on_site_state == "LA"
        assert line.on_site_price_source == "Local sand and labor phone estimate"
        assert line.on_site_priced_at is not None
        assert "on_site_city" not in line.specs_json
        before = quote_line_items_snapshot(line.quote)
        old_description, old_part = line.description, line.part_number
    response = client.post(
        f"/quotes/{qid}/line-items/{lid}/update",
        data=form(
            spec_diameter="42",
            spec_fill_weight_lb="12000",
            description=old_description,
            part_number=old_part,
            part_number_baseline=old_part,
            unit_price_baseline="349.50",
        ),
    )
    assert response.status_code == 200
    with app.app_context():
        line = db.session.get(QuoteLineItem, lid)
        assert line.part_number == "GTB-42-12000-ONSITE"
        assert "12,000 lb Fill" in line.description
        assert line.unit_price == Decimal("349.50")  # Spec change never recomputes the price.
        assert "auto_unit_price" not in line.specs_json
        assert before[0]["on_site_city"] == "Baton Rouge"
        assert before[0]["on_site_price_source"] == "Local sand and labor phone estimate"
    client.post(
        f"/quotes/{qid}/line-items/{lid}/update",
        data=form(
            spec_diameter="48",
            spec_fill_weight_lb="13000",
            description="Custom field fill",
            part_number="CUSTOM-465",
        ),
    )
    with app.app_context():
        line = db.session.get(QuoteLineItem, lid)
        assert line.description == "Custom field fill" and line.part_number == "CUSTOM-465"
    text = pdf_text(client, qid)
    assert "CUSTOM-465" in text and "Custom field fill" in text and "349.50" in text
    assert "Local sand and labor phone estimate" not in text
    assert "Prior on-site" not in text


@pytest.mark.parametrize(
    "overrides",
    [
        {"on_site_label": " "},
        {"on_site_city": ""},
        {"on_site_state": ""},
        {"unit_price": ""},
        {"unit_price": "0"},
        {"unit_price": "NaN"},
        {"unit_price": "0.001"},
        {"spec_fill_weight_lb": ""},
        {"spec_diameter": "-1"},
        {"quantity": "NaN"},
    ],
)
def test_invalid_fill_rejected_without_mutating_quote(editor, overrides):
    app, client, qid = editor
    response = client.post(f"/quotes/{qid}/line-items/add", data=form(**overrides))
    assert response.status_code == 422
    with app.app_context():
        assert db.session.query(QuoteLineItem).filter_by(quote_id=qid).count() == 0
    add(editor)
    with app.app_context():
        line = db.session.query(QuoteLineItem).filter_by(quote_id=qid).one()
        lid = line.id
        before = quote_line_items_snapshot(line.quote)
    response = client.post(f"/quotes/{qid}/line-items/{lid}/update", data=form(**overrides))
    assert response.status_code == 422
    with app.app_context():
        assert quote_line_items_snapshot(db.session.get(Quote, qid)) == before


def test_recall_is_normalized_ordered_filtered_and_display_only(editor):
    app, client, qid = editor
    with app.app_context():
        for i, (city, state, status, deleted) in enumerate(
            [
                ("Baton Rouge", "LA", QuoteStatus.SENT, False),
                ("baton rouge", "la", QuoteStatus.IN_REVIEW, False),
                ("Baton Rouge", "TX", QuoteStatus.SENT, False),
                ("Lafayette", "LA", QuoteStatus.SENT, False),
                ("Baton Rouge", "LA", QuoteStatus.SENT, True),
                ("Baton Rouge", "LA", QuoteStatus.REPLACED, False),
            ]
        ):
            quote = Quote(
                quote_number=f"HIST-{i}",
                customer_name_raw=f"Customer-{i}",
                status=status,
                deleted_at=datetime.utcnow() if deleted else None,
            )
            db.session.add(quote)
            db.session.flush()
            db.session.add(
                QuoteLineItem(
                    quote=quote,
                    product_type="on_site_fill",
                    description=f"Product-{i}",
                    part_number=f"PART-{i}",
                    quantity=10,
                    unit_price=300 + i,
                    line_total=3000 + i * 10,
                    on_site_label=f"Site-{i}",
                    on_site_city=city,
                    on_site_state=state,
                    on_site_price_source=f"Source-{i}",
                    on_site_priced_at=datetime(2026, 8, 1) + timedelta(days=i),
                )
            )
        db.session.commit()
    response = client.get(
        f"/quotes/{qid}/on-site-fill-history?on_site_city=%20Baton%20%20Rouge%20&on_site_state=la"
    )
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert text.index("Customer-1") < text.index("Customer-0")
    assert "301.00" in text and "Site-1" in text and "Product-1" in text and "Source-1" in text
    for i in (2, 3, 4, 5):
        assert f"Customer-{i}" not in text
    assert "<input" not in text
    with app.app_context():
        assert db.session.query(QuoteLineItem).filter_by(quote_id=qid).count() == 0
    add(editor, unit_price="477.25")
    with app.app_context():
        line = db.session.query(QuoteLineItem).filter_by(quote_id=qid).one()
        assert line.unit_price == Decimal("477.25")
    assert (
        b"Current customer"
        not in client.get(
            f"/quotes/{qid}/on-site-fill-history?on_site_city=Baton+Rouge&on_site_state=LA"
        ).data
    )


def test_duplicate_requires_new_local_price_and_revision_preserves_context(editor):
    app, client, qid = editor
    add(editor)
    response = client.post(f"/quotes/{qid}/duplicate", data={"new_customer_name": "Next customer"})
    assert response.status_code == 302
    new_id = int(response.location.rsplit("/", 1)[1])
    with app.app_context():
        copied = db.session.query(QuoteLineItem).filter_by(quote_id=new_id).one()
        assert copied.unit_price == 0 and copied.line_total == 0
        assert copied.on_site_city is None and copied.on_site_priced_at is None
        assert copied.quote.status == QuoteStatus.NEEDS_PRICING
    response = client.post(f"/quotes/{qid}/revise")
    assert response.status_code == 302
    revision_id = int(response.location.rsplit("/", 1)[1])
    with app.app_context():
        revised = db.session.query(QuoteLineItem).filter_by(quote_id=revision_id).one()
        assert revised.unit_price == Decimal("349.50")
        assert revised.on_site_city == "Baton Rouge"
        assert revised.on_site_price_source == "Local sand and labor phone estimate"
        assert revised.on_site_priced_at is not None


def test_existing_empty_bag_price_and_pdf_unchanged_after_fill_added(editor, monkeypatch):
    from reportlab import rl_config

    # Freeze ReportLab's random document ID / creation timestamp, so compare
    # actual PDF bytes rather than masking a customer-facing layout change.
    monkeypatch.setattr(rl_config, "invariant", 1)
    app, client, qid = editor
    with app.app_context():
        empty = Quote(
            quote_number="EMPTY-465",
            customer_name_raw="Empty bag customer",
            status=QuoteStatus.IN_REVIEW,
        )
        db.session.add(empty)
        db.session.add(
            PricingTable(
                product_type="bag",
                key_fields={
                    "pipe_size_min": 14,
                    "pipe_size_max": 18,
                    "part_number": "BAG-16",
                    "pieces_per_pallet": 25,
                },
                price=80,
            )
        )
        db.session.flush()
        line = QuoteLineItem(
            quote=empty,
            product_type="bag",
            part_number="BAG-16",
            description='Geotextile Bag, BAG-16, 16" pipe (Empty)',
            quantity=25,
            unit_price=80,
            line_total=2000,
            specs_json={"diameter": "16"},
        )
        db.session.add(line)
        db.session.commit()
        empty_id, lid = empty.id, line.id
        snapshot = quote_line_items_snapshot(empty)
    before_bytes = client.get(f"/quotes/{empty_id}/preview-pdf").data
    before = pdf_text(client, empty_id)
    assert "(Empty)" in before and "80.00" in before
    add(editor)
    assert client.get(f"/quotes/{empty_id}/preview-pdf").data == before_bytes
    assert pdf_text(client, empty_id) == before
    with app.app_context():
        assert quote_line_items_snapshot(db.session.get(Quote, empty_id)) == snapshot
    # Existing empty-bag edits still round to pallets and use the diameter lookup.
    response = client.post(
        f"/quotes/{empty_id}/line-items/{lid}/update",
        data={
            "product_type": "bag",
            "quantity": "20",
            "unit_price": "80.00",
            "spec_diameter": "16",
        },
    )
    assert response.status_code == 200
    with app.app_context():
        line = db.session.get(QuoteLineItem, lid)
        assert line.quantity == 25 and line.unit_price == 80 and line.line_total == 2000
        assert line.part_number == "BAG-16"
    assert "On-site Filling" not in pdf_text(client, empty_id)


def test_converting_empty_bag_generates_fill_text(editor):
    app, client, qid = editor
    with app.app_context():
        line = QuoteLineItem(
            quote_id=qid,
            product_type="bag",
            description="Old empty bag",
            part_number="EMPTY",
            quantity=25,
            unit_price=80,
            line_total=2000,
            specs_json={"diameter": "16", "wall_thickness": "0.5", "length_ft": "10"},
        )
        db.session.add(line)
        db.session.commit()
        lid = line.id
    response = client.post(
        f"/quotes/{qid}/line-items/{lid}/update",
        data=form(
            description="",
            part_number="",
            part_number_baseline="EMPTY",
            unit_price_baseline="80.00",
        ),
    )
    assert response.status_code == 200
    with app.app_context():
        line = db.session.get(QuoteLineItem, lid)
        assert line.description == "Geotextile Bag Weight 36in Pipe 9,000 lb Fill - On-site Filling"
        assert line.part_number == "GTB-36-9000-ONSITE"
        assert line.unit_price == Decimal("349.50")

        # Pipe specs left from a prior type must not turn a local fill job into
        # a steel-freight calculation. Filled bags are not shipped to the site.
        from app.routes import _steel_weight_for_item

        assert _steel_weight_for_item(line, Decimal("10")) == 0
