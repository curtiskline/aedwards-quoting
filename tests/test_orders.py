"""CP-3 orders: state machine, idempotent accept, version binding, rendering.

The acceptance seam (orders.create_order_from_acceptance) must be:
- atomic: AcceptanceEvent + Order + audit rows in one transaction;
- idempotent per QuoteVersion: a double-click, replay, or concurrent race
  never double-creates (unique constraint = the claim, hazard §12.1);
- version-bound: only the LATEST sent version of a still-SENT quote is
  acceptable — a REPLACED revision or a superseded version is refused;
- read-only over the immutable QuoteVersion: order views render the frozen
  line_items_snapshot, not the live Quote lines.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import (
    AcceptanceEvent,
    AcceptanceSource,
    AuditLog,
    Order,
    OrderAuditLog,
    OrderStatus,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    QuoteVersion,
    User,
)
from app.orders import (
    AcceptanceError,
    InvalidTransition,
    acceptable_version,
    advance_order,
    create_order_from_acceptance,
)

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


@pytest.fixture()
def app(db_url, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
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


def _make_sent_quote(number: str = "126-100", versions: int = 1) -> Quote:
    """A SENT quote with live line items and N immutable send records."""
    quote = Quote(
        quote_number=number,
        status=QuoteStatus.SENT,
        customer_name_raw="Acme Pipeline",
    )
    _db.session.add(quote)
    _db.session.flush()
    _db.session.add(
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
    for n in range(1, versions + 1):
        _db.session.add(
            QuoteVersion(
                quote_id=quote.id,
                version_number=n,
                pdf_path=f"/tmp/{number}-v{n}.pdf",
                artifact_status="retained",
                line_items_snapshot=SNAPSHOT,
                sent_at=datetime(2026, 8, 20, 12, n),
                sent_to="buyer@acme.com",
            )
        )
    _db.session.commit()
    return quote


def _owner() -> User:
    return _db.session.query(User).filter_by(email="owner@example.com").one()


# ---------------------------------------------------------------------------
# Acceptance: creation, atomicity, provenance
# ---------------------------------------------------------------------------


def test_accept_creates_event_order_and_audit_rows(app):
    with app.app_context():
        quote = _make_sent_quote()
        version = acceptable_version(quote)
        order, created = create_order_from_acceptance(
            version,
            source=AcceptanceSource.EXPLICIT_CLICK,
            actor=_owner(),
            note="Chip called, PO to follow",
            po_number="PO-7788",
        )
        assert created is True
        assert order.status == OrderStatus.ACCEPTED
        assert order.quote_version_id == version.id
        assert order.quote_id == quote.id
        assert order.po_number == "PO-7788"
        assert order.accepted_by == _owner().id
        assert order.accepted_at is not None

        event = _db.session.get(AcceptanceEvent, order.acceptance_event_id)
        assert event.source == AcceptanceSource.EXPLICIT_CLICK
        assert event.note == "Chip called, PO to follow"
        assert event.po_number == "PO-7788"
        assert event.quote_version_id == version.id

        order_audit = _db.session.query(OrderAuditLog).filter_by(order_id=order.id).all()
        assert [a.action for a in order_audit] == ["accepted"]
        assert order_audit[0].details["source"] == "explicit_click"

        quote_audit = (
            _db.session.query(AuditLog).filter_by(quote_id=quote.id, action="accepted").all()
        )
        assert len(quote_audit) == 1
        assert quote_audit[0].details["order_id"] == order.id


def test_accept_route_end_to_end(app, client):
    with app.app_context():
        quote = _make_sent_quote(number="126-101")
        quote_id = quote.id
        version_id = acceptable_version(quote).id

    form = client.get(f"/quotes/{quote_id}/accept-form")
    assert form.status_code == 200
    assert b"version 1" in form.data.lower()
    assert f'value="{version_id}"'.encode() in form.data

    resp = client.post(
        f"/quotes/{quote_id}/accept",
        data={"quote_version_id": version_id, "po_number": "PO-1", "note": ""},
    )
    assert resp.status_code == 200
    assert b"Order Created" in resp.data

    with app.app_context():
        order = _db.session.query(Order).one()
        assert order.quote_id == quote_id
        assert order.po_number == "PO-1"


# ---------------------------------------------------------------------------
# Idempotency: double-click, replay, concurrent race
# ---------------------------------------------------------------------------


def test_double_accept_is_idempotent(app, client):
    with app.app_context():
        quote = _make_sent_quote(number="126-102")
        quote_id = quote.id
        version_id = acceptable_version(quote).id

    first = client.post(
        f"/quotes/{quote_id}/accept", data={"quote_version_id": version_id}
    )
    second = client.post(
        f"/quotes/{quote_id}/accept", data={"quote_version_id": version_id}
    )
    assert first.status_code == second.status_code == 200
    assert b"Order Created" in first.data
    assert b"Already Accepted" in second.data
    assert b"no duplicate order" in second.data

    with app.app_context():
        assert _db.session.query(Order).count() == 1
        assert _db.session.query(AcceptanceEvent).count() == 1


def test_concurrent_accept_race_hits_unique_constraint(app, monkeypatch):
    """Two requests pass the pre-check before either commits: the second
    insert must land on the unique constraint and return the first order,
    never a duplicate. Simulated by blinding the pre-check lookup once so
    the code path proceeds to the INSERT exactly as a true race would."""
    import app.orders as orders_mod

    with app.app_context():
        quote = _make_sent_quote(number="126-103")
        version = acceptable_version(quote)

        winner, created = create_order_from_acceptance(
            version, source=AcceptanceSource.EXPLICIT_CLICK, actor=_owner()
        )
        assert created is True

        real_lookup = orders_mod._order_for_version
        calls = {"n": 0}

        def racing_lookup(quote_version_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # the pre-check ran before the winner committed
            return real_lookup(quote_version_id)

        monkeypatch.setattr(orders_mod, "_order_for_version", racing_lookup)
        loser, created = create_order_from_acceptance(
            version, source=AcceptanceSource.EXPLICIT_CLICK, actor=_owner()
        )
        assert created is False
        assert loser.id == winner.id
        monkeypatch.undo()
        assert _db.session.query(Order).count() == 1
        assert _db.session.query(AcceptanceEvent).count() == 1


# ---------------------------------------------------------------------------
# Version binding
# ---------------------------------------------------------------------------


def test_replaced_quote_is_not_acceptable(app, client):
    with app.app_context():
        quote = _make_sent_quote(number="126-104")
        quote_id = quote.id
        version_id = acceptable_version(quote).id

    # Revise: the SENT quote becomes REPLACED, the customer holds the revision.
    revise = client.post(f"/quotes/{quote_id}/revise")
    assert revise.status_code in (302, 303)

    form = client.get(f"/quotes/{quote_id}/accept-form")
    assert b"Cannot Accept" in form.data
    assert b"replaced" in form.data.lower()

    resp = client.post(
        f"/quotes/{quote_id}/accept", data={"quote_version_id": version_id}
    )
    assert b"Cannot Accept" in resp.data
    with app.app_context():
        assert _db.session.query(Order).count() == 0


def test_stale_version_is_not_acceptable(app):
    """A re-sent quote has two versions; accepting the superseded one fails."""
    with app.app_context():
        quote = _make_sent_quote(number="126-105", versions=2)
        v1, v2 = sorted(quote.versions, key=lambda v: v.version_number)
        with pytest.raises(AcceptanceError, match="stale"):
            create_order_from_acceptance(
                v1, source=AcceptanceSource.EXPLICIT_CLICK, actor=_owner()
            )
        order, created = create_order_from_acceptance(
            v2, source=AcceptanceSource.EXPLICIT_CLICK, actor=_owner()
        )
        assert created is True
        assert order.quote_version_id == v2.id


def test_accept_form_stale_after_resend_is_refused(app, client):
    """A form opened before a re-send posts the old version id: refused."""
    with app.app_context():
        quote = _make_sent_quote(number="126-106")
        quote_id = quote.id
        v1_id = acceptable_version(quote).id
        # Re-send happens while the modal sits open.
        _db.session.add(
            QuoteVersion(
                quote_id=quote.id,
                version_number=2,
                pdf_path="/tmp/126-106-v2.pdf",
                artifact_status="retained",
                line_items_snapshot=SNAPSHOT,
                sent_at=datetime(2026, 8, 20, 13, 0),
                sent_to="buyer@acme.com",
            )
        )
        _db.session.commit()

    resp = client.post(f"/quotes/{quote_id}/accept", data={"quote_version_id": v1_id})
    assert b"Cannot Accept" in resp.data
    assert b"newer version" in resp.data
    with app.app_context():
        assert _db.session.query(Order).count() == 0


def test_unsent_quote_is_not_acceptable(app):
    with app.app_context():
        quote = Quote(quote_number="126-107", status=QuoteStatus.READY)
        _db.session.add(quote)
        _db.session.commit()
        with pytest.raises(AcceptanceError, match="sent"):
            acceptable_version(quote)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def _make_order(app_ctx_quote_number: str = "126-110") -> Order:
    quote = _make_sent_quote(number=app_ctx_quote_number)
    order, _ = create_order_from_acceptance(
        acceptable_version(quote), source=AcceptanceSource.EXPLICIT_CLICK, actor=_owner()
    )
    return order


def test_state_machine_accepted_to_ordered(app):
    with app.app_context():
        order = _make_order()
        advance_order(order, OrderStatus.ORDERED, _owner())
        _db.session.commit()
        assert order.status == OrderStatus.ORDERED
        assert order.ordered_at is not None
        assert order.ordered_by == _owner().id
        actions = [a.action for a in order.audit_logs]
        assert actions == ["accepted", "ordered"]


def test_state_machine_rejects_skip_and_backward(app):
    with app.app_context():
        order = _make_order("126-111")
        with pytest.raises(InvalidTransition):
            advance_order(order, OrderStatus.FULFILLED, _owner())  # skip
        with pytest.raises(InvalidTransition):
            advance_order(order, OrderStatus.ACCEPTED, _owner())  # self/backward
        advance_order(order, OrderStatus.ORDERED, _owner())
        with pytest.raises(InvalidTransition):
            advance_order(order, OrderStatus.ORDERED, _owner())  # repeat


def test_transition_route_ordered_and_fulfilled_gate(app, client):
    with app.app_context():
        order = _make_order("126-112")
        order_id = order.id

    resp = client.post(f"/orders/{order_id}/status", data={"status": "ordered"})
    assert resp.status_code == 200
    with app.app_context():
        assert _db.session.get(Order, order_id).status == OrderStatus.ORDERED

    # FULFILLED is CP-4's transition — the manual route refuses it.
    resp = client.post(f"/orders/{order_id}/status", data={"status": "fulfilled"})
    assert b"pick/ship" in resp.data
    with app.app_context():
        assert _db.session.get(Order, order_id).status == OrderStatus.ORDERED


# ---------------------------------------------------------------------------
# Rendering from the frozen snapshot
# ---------------------------------------------------------------------------


def test_order_detail_renders_snapshot_not_live_quote(app, client):
    with app.app_context():
        order = _make_order("126-113")
        order_id = order.id
        # Mutate the live quote afterwards — the order must not follow it.
        line = _db.session.query(QuoteLineItem).filter_by(quote_id=order.quote_id).one()
        line.description = "EDITED AFTER ACCEPT"
        line.unit_price = 999
        line.line_total = 1998
        _db.session.commit()

    resp = client.get(f"/orders/{order_id}")
    assert resp.status_code == 200
    assert b"12in sleeve" in resp.data
    assert b"$200.00" in resp.data
    assert b"EDITED AFTER ACCEPT" not in resp.data
    assert b"$999" not in resp.data
    assert b"1998" not in resp.data
    # Provenance block.
    assert b"explicit click" in resp.data
    assert b"buyer@acme.com" in resp.data
    # Freight from the snapshot's shipping line, shown separately.
    assert b"$55.00" in resp.data
    assert b"$255.00" in resp.data


def test_orders_queue_lists_and_filters(app, client):
    with app.app_context():
        o1 = _make_order("126-114")
        o2 = _make_order("126-115")
        advance_order(o2, OrderStatus.ORDERED, _owner())
        _db.session.commit()

    all_page = client.get("/orders/")
    assert b"126-114" in all_page.data
    assert b"126-115" in all_page.data

    accepted_page = client.get("/orders/?status=accepted")
    assert b"126-114" in accepted_page.data
    assert b"126-115" not in accepted_page.data

    ordered_page = client.get("/orders/?status=ordered")
    assert b"126-115" in ordered_page.data
    assert b"126-114" not in ordered_page.data


def test_quote_status_bar_shows_accept_then_order_link(app, client):
    with app.app_context():
        quote = _make_sent_quote(number="126-116")
        quote_id = quote.id

    before = client.get(f"/quotes/{quote_id}")
    assert b"Mark Accepted" in before.data

    accept = client.post(f"/quotes/{quote_id}/accept", data={})
    assert b"Order Created" in accept.data

    after = client.get(f"/quotes/{quote_id}")
    assert b"Mark Accepted" not in after.data
    assert b"/orders/" in after.data


# ---------------------------------------------------------------------------
# Quote soft-delete is blocked once an order exists
# ---------------------------------------------------------------------------


def test_quote_with_order_cannot_be_deleted(app, client):
    with app.app_context():
        order = _make_order("126-117")
        quote_id = order.quote_id

    resp = client.post(f"/quotes/{quote_id}/delete")
    assert resp.status_code == 409

    with app.app_context():
        quote = _db.session.get(Quote, quote_id)
        assert quote.deleted_at is None


def test_quote_without_order_still_deletable(app, client):
    with app.app_context():
        quote = _make_sent_quote(number="126-118")
        quote_id = quote.id

    resp = client.post(f"/quotes/{quote_id}/delete")
    assert resp.status_code in (302, 303)
    with app.app_context():
        assert _db.session.get(Quote, quote_id).deleted_at is not None
