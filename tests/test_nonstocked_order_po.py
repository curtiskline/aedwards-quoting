"""Never-stock (min/max 0) parts: order-triggered vendor PO (task 444).

Engine v2 §3 / I148.2 ("treat those exactly the same but min/max set at
zero. So if order is received it automatically sends build order with the
customer details"). The load-bearing properties:

- min = max = 0 is a VALID SEEDED state ("never stock"), distinct from
  NULL/NULL unseeded, and it NEVER min-triggers — not at seeding, and not
  when the shipment decrement later drives on_hand negative (the vendor PO
  already fired at pick-list generation; a second one would double-order);
- pick-list generation emits one customer-linked Reorder per never-stock
  line, customer details FROZEN on the record (I153: unpriced order
  instruction with the customer/job context), idempotent per (pick_list,
  line) via the OrderVendorPoClaim guard;
- order-triggered POs are per-customer-order: two customers ordering the
  same 0/0 part each get their own concurrently-open PO (the one-active-
  reorder-per-item partial index applies to MIN-TRIGGERED rows only), and
  a later reseed to real thresholds can still min-trigger while an
  order-linked PO is open;
- the printed sheet renders unpriced with the customer/job block, and the
  lifecycle (OPEN -> SENT -> RECEIVED) plus the ledger invariant hold.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.extensions import db as _db
from app.fulfillment import advance_pick_list, create_pick_list
from app.inventory import (
    emit_order_triggered_reorders,
    close_reorder,
    mark_reorder_sent,
    seed_stock_row,
    verify_stock_integrity,
)
from app.models import (
    AcceptanceSource,
    OrderVendorPoClaim,
    PickListStatus,
    ProductCatalog,
    Quote,
    QuoteStatus,
    QuoteVersion,
    Reorder,
    ReorderStatus,
    ShopPing,
    ShopPingChannel,
    StockItem,
    StockMovement,
    StockMovementType,
    User,
)
from app.orders import acceptable_version, create_order_from_acceptance

CUSTOM_SNAPSHOT = [
    {
        "id": 501,
        "product_type": "sleeve",
        "description": '36" custom sleeve, engineered',
        "quantity": 4.0,
        "unit_price": 900.0,
        "line_total": 3600.0,
        "part_number": "CUST-36",
        "specs_json": {
            "notes": "Charpy @ -22F; stencil PO#, heat#; lifting lugs shipped loose",
            "diameter": 36,
        },
        "sort_order": 1,
    },
    {
        "id": 502,
        "product_type": "sleeve",
        "description": '12" sleeve, 10 ft',
        "quantity": 10.0,
        "unit_price": 100.0,
        "line_total": 1000.0,
        "part_number": "SLV-12",
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
        _db.session.add(
            ProductCatalog(
                part_number="CUST-36",
                description='36" custom sleeve, engineered',
                product_type="sleeve",
                vendor="AE MFG",
            )
        )
        _db.session.add(
            ProductCatalog(
                part_number="SLV-12",
                description='12" sleeve, 10 ft',
                product_type="sleeve",
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


def _catalog(part_number: str) -> ProductCatalog:
    return (
        _db.session.query(ProductCatalog).filter_by(part_number=part_number).one()
    )


def _seed(catalog, on_hand, min_qty=None, max_qty=None, reorder_qty=None):
    item, movement, reorder = seed_stock_row(
        catalog,
        on_hand=on_hand,
        min_qty=min_qty,
        max_qty=max_qty,
        reorder_qty=reorder_qty,
        actor=_owner(),
    )
    _db.session.commit()
    return item, movement, reorder


def _make_order(number="126-600", snapshot=CUSTOM_SNAPSHOT, po_number="PO3063-001520"):
    quote = Quote(
        quote_number=number,
        status=QuoteStatus.SENT,
        customer_name_raw="Acme Pipeline",
        ship_to_json={
            "company": "Acme Pipeline",
            "street": "500 Jobsite Rd",
            "city": "Cushing",
            "state": "OK",
            "postal_code": "74023",
        },
    )
    _db.session.add(quote)
    _db.session.flush()
    _db.session.add(
        QuoteVersion(
            quote_id=quote.id,
            version_number=1,
            pdf_path=f"/tmp/{number}-v1.pdf",
            artifact_status="retained",
            line_items_snapshot=snapshot,
            sent_at=datetime(2026, 8, 26, 12, 0),
            sent_to="buyer@acme.com",
        )
    )
    _db.session.commit()
    order, _ = create_order_from_acceptance(
        acceptable_version(quote),
        source=AcceptanceSource.EXPLICIT_CLICK,
        actor=_owner(),
        po_number=po_number,
    )
    _db.session.commit()
    return order


def _order_reorders():
    return (
        _db.session.query(Reorder)
        .filter(Reorder.order_id.isnot(None))
        .order_by(Reorder.id.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Seeding: 0/0 is valid, distinct from unseeded, and never min-triggers
# ---------------------------------------------------------------------------


def test_seed_zero_zero_is_valid_never_stock_state(app):
    with app.app_context():
        item, movement, reorder = _seed(
            _catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0
        )
        assert item.is_seeded
        assert item.is_non_stocked
        # 0 on hand <= 0 min, but never-stock must NOT min-trigger.
        assert reorder is None
        assert not item.needs_reorder
        assert movement is not None  # the seed still lands in the ledger
        assert verify_stock_integrity() == []


def test_unseeded_item_is_not_non_stocked(app):
    with app.app_context():
        from app.inventory import get_or_create_stock_item

        item = get_or_create_stock_item(_catalog("CUST-36"))
        _db.session.commit()
        assert not item.is_seeded
        assert not item.is_non_stocked
        assert not item.needs_reorder


# ---------------------------------------------------------------------------
# Emission at pick-list generation
# ---------------------------------------------------------------------------


def test_pick_list_generation_emits_customer_linked_po(app):
    with app.app_context():
        _seed(_catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0)
        _seed(_catalog("SLV-12"), on_hand=40, min_qty=10, max_qty=60)
        order = _make_order()
        pick_list, created = create_pick_list(order, _owner())
        assert created

        reorders = _order_reorders()
        assert len(reorders) == 1  # ONLY the never-stock line, not SLV-12
        r = reorders[0]
        assert r.order_id == order.id
        assert r.status == ReorderStatus.OPEN
        assert r.qty == 4
        assert (r.min_qty_at_trigger, r.max_qty_at_trigger) == (0, 0)
        assert r.vendor_at_trigger == "AE MFG"
        ctx = r.customer_context
        assert ctx["customer_name"] == "Acme Pipeline"
        assert ctx["po_number"] == "PO3063-001520"
        assert ctx["quote_number"] == "126-600"
        assert ctx["ship_to"]["address_line1"] == "500 Jobsite Rd"
        assert ctx["line"]["part_number"] == "CUST-36"
        assert ctx["line"]["pieces"] == 4
        assert "Charpy" in ctx["line"]["notes"]
        # The emission is a zero-delta REORDER ledger row + a shop ping.
        assert r.reorder_movement.movement_type == StockMovementType.REORDER
        assert r.reorder_movement.qty_delta == 0
        ping = (
            _db.session.query(ShopPing).filter_by(reorder_id=r.id).one()
        )
        assert ping.channel == ShopPingChannel.MANUAL_PRINT
        assert verify_stock_integrity() == []


def test_emission_skips_stocked_and_unseeded_lines(app):
    with app.app_context():
        # CUST-36 left UNSEEDED (no thresholds) — must not emit.
        _seed(_catalog("SLV-12"), on_hand=40, min_qty=10, max_qty=60)
        order = _make_order()
        create_pick_list(order, _owner())
        assert _order_reorders() == []


def test_emission_is_idempotent_per_pick_list_line(app):
    with app.app_context():
        _seed(_catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0)
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        assert len(_order_reorders()) == 1

        # A replayed generate returns the existing pick list and emits nothing.
        again, created = create_pick_list(order, _owner())
        assert not created and again.id == pick_list.id
        assert len(_order_reorders()) == 1

        # Even a direct re-emission is absorbed by the per-line claim.
        emitted = emit_order_triggered_reorders(pick_list, order, _owner())
        _db.session.commit()
        assert emitted == []
        assert len(_order_reorders()) == 1
        assert (
            _db.session.query(OrderVendorPoClaim)
            .filter_by(pick_list_id=pick_list.id)
            .count()
            == 1
        )


def test_customer_details_are_frozen_on_the_record(app):
    with app.app_context():
        _seed(_catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0)
        order = _make_order()
        create_pick_list(order, _owner())
        # Later edits to the quote and catalog must not leak into the record.
        order.quote.customer_name_raw = "Renamed Corp"
        order.quote.ship_to_json = {"company": "Elsewhere"}
        _catalog("CUST-36").vendor = "Someone Else"
        _db.session.commit()

        r = _order_reorders()[0]
        assert r.customer_context["customer_name"] == "Acme Pipeline"
        assert r.customer_context["ship_to"]["company"] == "Acme Pipeline"
        assert r.vendor_at_trigger == "AE MFG"


# ---------------------------------------------------------------------------
# Never min-triggers: the ship-time negative decrement must not double-order
# ---------------------------------------------------------------------------


def test_zero_zero_item_never_min_triggers_through_shipment(app):
    with app.app_context():
        item, _, _ = _seed(_catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0)
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        advance_pick_list(pick_list, PickListStatus.PICKED, _owner())
        advance_pick_list(pick_list, PickListStatus.LOADED, _owner())
        advance_pick_list(pick_list, PickListStatus.SHIPPED, _owner())

        # The decrement ran honestly (shelf goes negative)...
        assert item.on_hand == -4
        # ...but the ONLY reorder is the order-triggered one from generation.
        all_reorders = _db.session.query(Reorder).all()
        assert len(all_reorders) == 1
        assert all_reorders[0].order_id == order.id
        assert verify_stock_integrity() == []

        # Receiving the vendor delivery brings the shelf back to 0 and does
        # not re-fire (design §3 ledger semantics).
        movement, new_reorder = close_reorder(all_reorders[0], 4, _owner())
        _db.session.commit()
        assert item.on_hand == 0
        assert new_reorder is None
        assert verify_stock_integrity() == []


# ---------------------------------------------------------------------------
# Per-customer-order POs: no collision across orders (PM ask 1)
# ---------------------------------------------------------------------------


def test_two_orders_for_same_part_each_get_their_own_open_po(app):
    with app.app_context():
        _seed(_catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0)
        order_a = _make_order(number="126-601", po_number="PO-A")
        order_b = _make_order(number="126-602", po_number="PO-B")
        create_pick_list(order_a, _owner())
        create_pick_list(order_b, _owner())

        reorders = _order_reorders()
        assert len(reorders) == 2
        assert {r.order_id for r in reorders} == {order_a.id, order_b.id}
        assert all(r.status == ReorderStatus.OPEN for r in reorders)
        assert {r.customer_context["po_number"] for r in reorders} == {
            "PO-A",
            "PO-B",
        }


# ---------------------------------------------------------------------------
# Reseed to real thresholds while an order-linked PO is open (PM ask 2)
# ---------------------------------------------------------------------------


def test_min_trigger_still_fires_while_order_linked_po_is_open(app):
    with app.app_context():
        item, _, _ = _seed(_catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0)
        order = _make_order()
        pick_list, _ = create_pick_list(order, _owner())
        advance_pick_list(pick_list, PickListStatus.PICKED, _owner())
        advance_pick_list(pick_list, PickListStatus.LOADED, _owner())
        advance_pick_list(pick_list, PickListStatus.SHIPPED, _owner())
        assert len(_order_reorders()) == 1  # order-linked PO still OPEN

        # Chip decides to start stocking the part: reseed with real
        # thresholds while the order-linked PO is still open. The open
        # order-linked row must NOT hold the min-trigger claim — the
        # partial index only guards min-triggered (order_id IS NULL) rows.
        item, _, fired = _seed(
            _catalog("CUST-36"), on_hand=2, min_qty=5, max_qty=20
        )
        assert fired is not None
        assert fired.order_id is None
        assert fired.customer_context is None
        open_rows = (
            _db.session.query(Reorder)
            .filter(Reorder.status == ReorderStatus.OPEN)
            .all()
        )
        assert len(open_rows) == 2  # one order-linked + one min-triggered

        # And the min-triggered guard still holds against ITSELF: another
        # at/below-min save cannot stack a second min-triggered row.
        _, _, again = _seed(_catalog("CUST-36"), on_hand=1, min_qty=5, max_qty=20)
        assert again is None


# ---------------------------------------------------------------------------
# The printed sheet: unpriced, with the customer/job block (I153)
# ---------------------------------------------------------------------------


def test_sheet_renders_unpriced_with_customer_block(app, client):
    with app.app_context():
        _seed(_catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0)
        order = _make_order()
        create_pick_list(order, _owner())
        reorder_id = _order_reorders()[0].id

    sheet = client.get(f"/stock/reorders/{reorder_id}/sheet")
    assert sheet.status_code == 200
    text = sheet.data.decode()
    # Unpriced order instruction: part/desc/qty, customer/job context, no $.
    assert "PURCHASE ORDER" in text
    assert "CUST-36" in text
    assert "Customer / job" in text
    assert "Acme Pipeline" in text
    assert "PO3063-001520" in text
    assert "126-600" in text
    assert "500 Jobsite Rd" in text
    assert "Charpy" in text
    assert "AE MFG" in text
    assert "Ship to: Allan Edwards, Inc." in text
    assert "$" not in text
    # The min-triggered "At trigger" table is meaningless at 0/0 — replaced
    # by the customer block.
    assert "Minimum to keep" not in text


def test_min_triggered_sheet_keeps_trigger_table_and_no_customer_block(
    app, client
):
    with app.app_context():
        _, _, fired = _seed(
            _catalog("SLV-12"), on_hand=2, min_qty=5, max_qty=20
        )
        reorder_id = fired.id
    sheet = client.get(f"/stock/reorders/{reorder_id}/sheet")
    text = sheet.data.decode()
    assert "Minimum to keep" in text
    assert "Customer / job" not in text


# ---------------------------------------------------------------------------
# Lifecycle: the same OPEN -> SENT -> RECEIVED states (T439)
# ---------------------------------------------------------------------------


def test_order_triggered_po_flows_open_sent_received(app):
    with app.app_context():
        item, _, _ = _seed(_catalog("CUST-36"), on_hand=0, min_qty=0, max_qty=0)
        order = _make_order()
        create_pick_list(order, _owner())
        r = _order_reorders()[0]

        mark_reorder_sent(r, _owner())
        _db.session.commit()
        assert r.status == ReorderStatus.SENT

        movement, new_reorder = close_reorder(r, 4, _owner())
        _db.session.commit()
        assert r.status == ReorderStatus.RECEIVED
        assert movement.qty_delta == 4
        assert item.on_hand == 4  # goods arrive at AEI first (I150)
        assert new_reorder is None
        assert verify_stock_integrity() == []
