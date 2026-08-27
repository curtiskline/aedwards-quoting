"""CP-4 fulfillment: pick lists, pack units, shop ping, printable sheet.

The pick list must be:
- idempotent per order: generating twice (double-click, concurrent race)
  never double-creates (unique order_id = the claim, hazard §12.1);
- snapshot-only: pick lines are frozen at creation from
  QuoteVersion.line_items_snapshot — mutating the live quote afterwards must
  not change the pick sheet (negative-tested below);
- strictly ordered: queued -> picked -> loaded -> shipped, one step at a
  time; a replayed transition is a no-op, a skip is refused;
- wired to the Order: creation drives ACCEPTED->ORDERED, shipped drives
  ORDERED->FULFILLED, and shipped emits the single CP-5 decrement hook.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.extensions import db as _db
from app.fulfillment import (
    PickListError,
    advance_pick_list,
    build_pick_lines,
    create_pick_list,
    pick_list_for_order,
)
from app.models import (
    AcceptanceSource,
    OrderStatus,
    PickList,
    PickListAuditLog,
    PickListStatus,
    PricingTable,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    QuoteVersion,
    ShopPing,
    ShopPingChannel,
    User,
)
from app.orders import acceptable_version, create_order_from_acceptance

SNAPSHOT = [
    {
        "product_type": "sleeve",
        "description": '12" sleeve, 10 ft, standard bundles',
        "quantity": 10.0,
        "unit_price": 100.0,
        "line_total": 1000.0,
        "part_number": "SLV-12",
        "specs_json": {"diameter": "12", "length_ft": "10.0", "original_qty": "8"},
        "sort_order": 1,
    },
    {
        "product_type": "bag",
        "description": "Anode bag",
        "quantity": 100.0,
        "unit_price": 8.0,
        "line_total": 800.0,
        "part_number": "BAG-8",
        "specs_json": {"diameter": "8"},
        "sort_order": 2,
    },
    {
        "product_type": "composite",
        "description": "Wrap kit",
        "quantity": 3.0,
        "unit_price": 50.0,
        "line_total": 150.0,
        "part_number": None,
        "specs_json": None,
        "sort_order": 3,
    },
    {
        "product_type": "shipping",
        "description": "Freight",
        "quantity": 1.0,
        "unit_price": 55.0,
        "line_total": 55.0,
        "part_number": None,
        "specs_json": None,
        "sort_order": 4,
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
        # The one catalog row fulfillment reads (bag pallet size), frozen into
        # the pick lines at creation.
        _db.session.add(
            PricingTable(
                product_type="bag",
                key_fields={
                    "pipe_size_min": "6",
                    "pipe_size_max": "10",
                    "part_number": "BAG-8",
                    "pieces_per_pallet": "50",
                },
                price=8.0,
            )
        )
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


def _owner() -> User:
    return _db.session.query(User).filter_by(email="owner@example.com").one()


def _make_order(number: str = "126-200", snapshot=SNAPSHOT):
    quote = Quote(
        quote_number=number,
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
    _db.session.add(quote)
    _db.session.flush()
    _db.session.add(
        QuoteLineItem(
            quote_id=quote.id,
            product_type="sleeve",
            description='12" sleeve, 10 ft, standard bundles',
            quantity=10,
            unit_price=100,
            line_total=1000,
            specs_json={"diameter": "12", "length_ft": "10.0"},
            sort_order=1,
        )
    )
    _db.session.add(
        QuoteVersion(
            quote_id=quote.id,
            version_number=1,
            pdf_path=f"/tmp/{number}-v1.pdf",
            artifact_status="retained",
            line_items_snapshot=snapshot,
            sent_at=datetime(2026, 8, 20, 12, 0),
            sent_to="buyer@acme.com",
        )
    )
    _db.session.commit()
    order, _ = create_order_from_acceptance(
        acceptable_version(quote), source=AcceptanceSource.EXPLICIT_CLICK, actor=_owner()
    )
    return order


# ---------------------------------------------------------------------------
# Pick lines: snapshot-only derivation + pack units
# ---------------------------------------------------------------------------


def test_pick_lines_pack_units_and_material_filter(app):
    with app.app_context():
        lines = build_pick_lines(SNAPSHOT)
        # Shipping is excluded; the three material lines remain.
        assert [ln["product_type"] for ln in lines] == ["sleeve", "bag", "composite"]

        sleeve, bag, composite = lines
        # Standard sleeve: 10 pcs at 10 ft / <=24in = 2 bundles of 5.
        assert sleeve["pieces"] == 10
        assert sleeve["pack_unit"] == "bundle"
        assert sleeve["pack_count"] == 2
        assert sleeve["pieces_per_pack"] == 5
        assert sleeve["requested_qty"] == 8  # original_qty before rounding

        # Bag: 100 pcs at 50/pallet = 2 pallets (from the seeded pricing row).
        assert bag["pieces"] == 100
        assert bag["pack_unit"] == "pallet"
        assert bag["pack_count"] == 2
        assert bag["pieces_per_pack"] == 50

        # No pack rule applies: plain pieces.
        assert composite["pieces"] == 3
        assert composite["pack_unit"] is None


def test_pick_lines_no_pack_unit_when_not_divisible(app):
    with app.app_context():
        snapshot = [dict(SNAPSHOT[0], quantity=7.0)]  # not whole bundles
        (line,) = build_pick_lines(snapshot)
        assert line["pieces"] == 7
        assert line["pack_unit"] is None


# ---------------------------------------------------------------------------
# Creation: idempotency + order wiring + shop ping
# ---------------------------------------------------------------------------


def test_create_pick_list_advances_order_and_pings(app):
    with app.app_context():
        order = _make_order()
        assert order.status == OrderStatus.ACCEPTED
        pick_list, created = create_pick_list(order, _owner())
        assert created is True
        assert pick_list.status == PickListStatus.QUEUED
        assert order.status == OrderStatus.ORDERED
        assert order.ordered_at is not None
        assert len(pick_list.lines_snapshot) == 3
        assert pick_list.created_by == _owner().id

        pings = _db.session.query(ShopPing).filter_by(pick_list_id=pick_list.id).all()
        assert [p.channel for p in pings] == [ShopPingChannel.MANUAL_PRINT]

        actions = [a.action for a in pick_list.audit_logs]
        assert actions == ["queued"]


def test_create_pick_list_is_idempotent(app):
    with app.app_context():
        order = _make_order()
        first, created_first = create_pick_list(order, _owner())
        second, created_second = create_pick_list(order, _owner())
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert _db.session.query(PickList).count() == 1
        assert _db.session.query(ShopPing).count() == 1
        # The order advanced exactly once.
        assert order.status == OrderStatus.ORDERED


def test_create_pick_list_refused_without_snapshot(app):
    with app.app_context():
        order = _make_order()
        order.quote_version.line_items_snapshot = None
        _db.session.commit()
        with pytest.raises(PickListError):
            create_pick_list(order, _owner())


def test_create_pick_list_refused_when_only_shipping(app):
    with app.app_context():
        order = _make_order(snapshot=[SNAPSHOT[3]])
        with pytest.raises(PickListError):
            create_pick_list(order, _owner())


# ---------------------------------------------------------------------------
# The snapshot-only rule (negative test): mutate the live quote AND the
# pricing table after generation — the pick lines must not move.
# ---------------------------------------------------------------------------


def test_pick_lines_immune_to_live_quote_and_catalog_mutation(app, client):
    with app.app_context():
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        pick_list_id = pick_list.id
        before = [dict(line) for line in pick_list.lines_snapshot]

        # Sabotage everything mutable the lines could have been derived from.
        live_quote = order.quote
        for item in live_quote.line_items:
            item.quantity = 999
            item.description = "MUTATED LIVE LINE"
            item.specs_json = {"diameter": "99"}
        bag_row = _db.session.query(PricingTable).filter_by(product_type="bag").one()
        bag_row.key_fields = dict(bag_row.key_fields, pieces_per_pallet="7")
        _db.session.commit()

        _db.session.expire_all()
        after = _db.session.get(PickList, pick_list_id).lines_snapshot
        assert after == before

        # And the rendered sheet reflects the frozen lines, not the mutation.
        page = client.get(f"/pick-lists/{pick_list_id}/sheet")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "MUTATED LIVE LINE" not in html
        assert '12&#34; sleeve, 10 ft, standard bundles' in html
        assert "2 bundles" in html
        assert "2 pallets" in html


# ---------------------------------------------------------------------------
# Progression: strict order, replay no-ops, order wiring, CP-5 hook
# ---------------------------------------------------------------------------


def test_progression_happy_path_stamps_and_audits(app):
    with app.app_context():
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        for status in (PickListStatus.PICKED, PickListStatus.LOADED, PickListStatus.SHIPPED):
            assert advance_pick_list(pick_list, status, _owner()) is True
        assert pick_list.picked_at is not None
        assert pick_list.loaded_at is not None
        assert pick_list.shipped_at is not None
        assert pick_list.shipped_by == _owner().id
        assert order.status == OrderStatus.FULFILLED
        assert order.fulfilled_at is not None

        actions = [a.action for a in pick_list.audit_logs]
        assert actions == ["queued", "picked", "loaded", "shipped", "shipped_event"]


def test_replayed_transition_is_noop_not_error(app):
    with app.app_context():
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        assert advance_pick_list(pick_list, PickListStatus.PICKED, _owner()) is True
        picked_at = pick_list.picked_at
        # Double-tap: same transition again — no-op, nothing re-stamped.
        assert advance_pick_list(pick_list, PickListStatus.PICKED, _owner()) is False
        assert pick_list.status == PickListStatus.PICKED
        assert pick_list.picked_at == picked_at
        # Backwards is also a no-op, never an un-advance.
        assert advance_pick_list(pick_list, PickListStatus.QUEUED, _owner()) is False
        assert pick_list.status == PickListStatus.PICKED
        assert [a.action for a in pick_list.audit_logs] == ["queued", "picked"]


def test_skipping_a_step_is_refused(app):
    with app.app_context():
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        with pytest.raises(PickListError):
            advance_pick_list(pick_list, PickListStatus.SHIPPED, _owner())
        assert pick_list.status == PickListStatus.QUEUED
        assert order.status == OrderStatus.ORDERED


def test_shipped_emits_decrement_hook_exactly_once(app):
    with app.app_context():
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        advance_pick_list(pick_list, PickListStatus.PICKED, _owner())
        advance_pick_list(pick_list, PickListStatus.LOADED, _owner())
        advance_pick_list(pick_list, PickListStatus.SHIPPED, _owner())
        # Replay the final tap: no second event.
        assert advance_pick_list(pick_list, PickListStatus.SHIPPED, _owner()) is False

        events = (
            _db.session.query(PickListAuditLog)
            .filter_by(pick_list_id=pick_list.id, action="shipped_event")
            .all()
        )
        assert len(events) == 1
        # The event carries the frozen lines so CP-5 never re-reads anything mutable.
        assert events[0].details["lines"] == pick_list.lines_snapshot
        assert events[0].details["order_id"] == order.id


# ---------------------------------------------------------------------------
# Routes: generate, queue, one-tap progression, sheet
# ---------------------------------------------------------------------------


def test_generate_route_creates_and_replays_cleanly(app, client):
    with app.app_context():
        order = _make_order()
        order_id = order.id
    first = client.post(f"/orders/{order_id}/pick-list")
    assert first.status_code == 200
    assert "Pick list generated" in first.get_data(as_text=True)
    second = client.post(f"/orders/{order_id}/pick-list")
    assert second.status_code == 200
    html = second.get_data(as_text=True)
    assert "Pick list generated" not in html  # replay: no created banner
    assert "Print pick sheet" in html
    with app.app_context():
        assert _db.session.query(PickList).count() == 1


def test_queue_view_tabs_and_progression_route(app, client):
    with app.app_context():
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        pick_list_id = pick_list.id

    page = client.get("/pick-lists/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "126-200" in html
    assert "Mark Picked" in html

    tap = client.post(f"/pick-lists/{pick_list_id}/status", data={"status": "picked"})
    assert tap.status_code == 200
    assert "Mark Loaded" in tap.get_data(as_text=True)

    # Mis-tap replay: visible no-op, not an error.
    replay = client.post(f"/pick-lists/{pick_list_id}/status", data={"status": "picked"})
    assert replay.status_code == 200
    assert "Already picked" in replay.get_data(as_text=True)

    # Mis-tap skip: refused with the state unchanged.
    skip = client.post(f"/pick-lists/{pick_list_id}/status", data={"status": "shipped"})
    assert skip.status_code == 200
    assert "one at a time" in skip.get_data(as_text=True)
    with app.app_context():
        assert _db.session.get(PickList, pick_list_id).status == PickListStatus.PICKED


def test_sheet_renders_pack_units_checkboxes_no_signature(app, client):
    with app.app_context():
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        pick_list_id = pick_list.id
    page = client.get(f"/pick-lists/{pick_list_id}/sheet")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "PICK SHEET" in html
    assert "126-200" in html
    assert "Acme Pipeline" in html
    assert "1 Pipeline Rd" in html
    assert "2 bundles" in html and "5 pcs / bundle" in html
    assert "2 pallets" in html and "50 pcs / pallet" in html
    # Pick sheets are NOT signed (I148.3, engine v2): pure pack manifest.
    assert "Driver signature" not in html
    assert "Picked by" not in html
    assert html.count('class="checkbox"') == 3  # one per material line
    assert "Freight" not in html  # non-material excluded
    assert "customer asked for 8" in html  # original_qty surfaced


def test_order_detail_shows_generate_then_pick_list_state(app, client):
    with app.app_context():
        order = _make_order()
        order_id = order.id
    before = client.get(f"/orders/{order_id}").get_data(as_text=True)
    assert "Generate pick list" in before
    assert "Mark Ordered" not in before
    client.post(f"/orders/{order_id}/pick-list")
    after = client.get(f"/orders/{order_id}").get_data(as_text=True)
    assert "Generate pick list" not in after
    assert "Print pick sheet" in after
