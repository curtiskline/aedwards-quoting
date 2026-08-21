"""CP-5b: stock seeding + auto-reorder trigger + reorder lifecycle.

The load-bearing properties (design Stage F, task 419):
- seeding lands EVERY change in the ledger (ADJUSTMENT, reason auto-noted
  initial-seed / threshold-change) and a save at/below min fires immediately;
- the trigger fires only on FULLY SEEDED items (NULL thresholds never fire —
  CP-5a regression) and the qty rule is reorder_qty, else max(max-on_hand,1);
- idempotency is the claim pattern: at most ONE open reorder per stock item
  (partial unique index) — a second trigger while one is open is a no-op,
  and a REPLAYED shipped event (the design's named hazard: phantom decrement
  -> phantom reorder) cannot create a second reorder end-to-end;
- mark-received books the ACTUAL received qty, closes the reorder, and the
  still-below-min state re-triggers a fresh one (re-arm semantics);
- the ledger invariant survives all of it (verify_stock_integrity).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.extensions import db as _db
from app.fulfillment import advance_pick_list, create_pick_list
from app.inventory import (
    REASON_INITIAL_SEED,
    REASON_THRESHOLD_CHANGE,
    StockError,
    close_reorder,
    compute_reorder_qty,
    consume_shipped_event,
    parse_seed_csv,
    record_adjustment,
    record_receipt,
    seed_stock_row,
    verify_stock_integrity,
)
from app.models import (
    AcceptanceSource,
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

SNAPSHOT = [
    {
        "product_type": "sleeve",
        "description": '12" sleeve, 10 ft',
        "quantity": 10.0,
        "unit_price": 100.0,
        "line_total": 1000.0,
        "part_number": "SLV-12",
        "specs_json": None,
        "sort_order": 1,
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
                part_number="SLV-12",
                description='12" sleeve, 10 ft',
                product_type="sleeve",
            )
        )
        _db.session.add(
            ProductCatalog(
                part_number="BAG-6",
                description="6in bag",
                product_type="bag",
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


def _sleeve() -> ProductCatalog:
    return _db.session.query(ProductCatalog).filter_by(part_number="SLV-12").one()


def _bag() -> ProductCatalog:
    return _db.session.query(ProductCatalog).filter_by(part_number="BAG-6").one()


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


def _reorders(item=None):
    q = _db.session.query(Reorder)
    if item is not None:
        q = q.filter(Reorder.stock_item_id == item.id)
    return q.order_by(Reorder.id.asc()).all()


def _ship_pick_list(number="126-500", snapshot=SNAPSHOT):
    quote = Quote(
        quote_number=number,
        status=QuoteStatus.SENT,
        customer_name_raw="Acme Pipeline",
        ship_to_json={"company": "Acme Pipeline"},
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
            sent_at=datetime(2026, 8, 20, 12, 0),
            sent_to="buyer@acme.com",
        )
    )
    _db.session.commit()
    order, _ = create_order_from_acceptance(
        acceptable_version(quote), source=AcceptanceSource.EXPLICIT_CLICK, actor=_owner()
    )
    pick_list, _ = create_pick_list(order, _owner())
    advance_pick_list(pick_list, PickListStatus.PICKED, _owner())
    advance_pick_list(pick_list, PickListStatus.LOADED, _owner())
    advance_pick_list(pick_list, PickListStatus.SHIPPED, _owner())
    return pick_list


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seed_creates_item_with_initial_seed_ledger_row(app):
    with app.app_context():
        item, movement, reorder = _seed(
            _sleeve(), on_hand=40, min_qty=10, max_qty=60, reorder_qty=50
        )
        assert item.on_hand == 40
        assert (item.min_qty, item.max_qty, item.reorder_qty) == (10, 60, 50)
        assert item.is_seeded
        assert reorder is None
        assert movement.movement_type == StockMovementType.ADJUSTMENT
        assert movement.qty_delta == 40
        assert movement.resulting_on_hand == 40
        assert movement.reason == REASON_INITIAL_SEED
        assert movement.details["seeding"]["old"]["on_hand"] == 0
        assert verify_stock_integrity() == []


def test_threshold_only_edit_writes_zero_delta_threshold_change_row(app):
    with app.app_context():
        _seed(_sleeve(), on_hand=40, min_qty=10, max_qty=60)
        item, movement, _ = _seed(_sleeve(), on_hand=40, min_qty=15, max_qty=60)
        assert movement is not None
        assert movement.qty_delta == 0
        assert movement.reason == REASON_THRESHOLD_CHANGE
        assert movement.details["seeding"]["old"]["min_qty"] == 10
        assert movement.details["seeding"]["new"]["min_qty"] == 15
        assert verify_stock_integrity() == []


def test_seed_noop_save_writes_nothing(app):
    with app.app_context():
        _seed(_sleeve(), on_hand=40, min_qty=10, max_qty=60)
        before = _db.session.query(StockMovement).count()
        _, movement, reorder = _seed(_sleeve(), on_hand=40, min_qty=10, max_qty=60)
        assert movement is None and reorder is None
        assert _db.session.query(StockMovement).count() == before


def test_seed_validation_rules(app):
    with app.app_context():
        with pytest.raises(StockError, match="come together"):
            _seed(_sleeve(), on_hand=5, min_qty=3)
        with pytest.raises(StockError, match="greater than max"):
            _seed(_sleeve(), on_hand=5, min_qty=9, max_qty=3)
        with pytest.raises(StockError, match="at least 1"):
            _seed(_sleeve(), on_hand=5, min_qty=1, max_qty=3, reorder_qty=0)


def test_seed_at_or_below_min_fires_immediately(app):
    with app.app_context():
        item, movement, reorder = _seed(
            _sleeve(), on_hand=2, min_qty=5, max_qty=20, reorder_qty=12
        )
        assert reorder is not None
        assert reorder.status == ReorderStatus.OPEN
        assert reorder.qty == 12
        assert reorder.trigger_movement_id == movement.id
        # The REORDER ledger row + the CP-4 shop ping exist.
        reorder_moves = [
            m
            for m in _db.session.query(StockMovement).all()
            if m.movement_type == StockMovementType.REORDER
        ]
        assert len(reorder_moves) == 1
        assert reorder_moves[0].qty_delta == 0
        assert reorder.reorder_movement_id == reorder_moves[0].id
        ping = _db.session.query(ShopPing).filter_by(reorder_id=reorder.id).one()
        assert ping.channel == ShopPingChannel.MANUAL_PRINT
        assert ping.pick_list_id is None
        assert verify_stock_integrity() == []


def test_seed_row_route_saves_and_reports(app, client):
    with app.app_context():
        catalog_id = _sleeve().id
    resp = client.post(
        f"/stock/seed/rows/{catalog_id}",
        data={"on_hand": "40", "min_qty": "10", "max_qty": "60", "reorder_qty": "50"},
    )
    assert resp.status_code == 200
    assert b"Saved." in resp.data
    with app.app_context():
        item = _db.session.query(StockItem).filter_by(catalog_id=catalog_id).one()
        assert (item.on_hand, item.min_qty, item.max_qty, item.reorder_qty) == (
            40,
            10,
            60,
            50,
        )
    # A bad save reports the error inline and changes nothing.
    resp = client.post(
        f"/stock/seed/rows/{catalog_id}",
        data={"on_hand": "40", "min_qty": "70", "max_qty": "60"},
    )
    assert b"Min cannot be greater than max." in resp.data
    with app.app_context():
        item = _db.session.query(StockItem).filter_by(catalog_id=catalog_id).one()
        assert item.min_qty == 10


# ---------------------------------------------------------------------------
# The trigger
# ---------------------------------------------------------------------------


def test_ship_dropping_to_min_triggers_reorder_with_fallback_qty(app):
    with app.app_context():
        # No reorder_qty: fallback tops back up to max (18 = 20 - 2).
        item, _, _ = _seed(_sleeve(), on_hand=12, min_qty=5, max_qty=20)
        _ship_pick_list()  # ships 10 sleeves -> on_hand 2
        _db.session.refresh(item)
        assert item.on_hand == 2
        (reorder,) = _reorders(item)
        assert reorder.status == ReorderStatus.OPEN
        assert reorder.qty == 18
        assert reorder.on_hand_at_trigger == 2
        assert reorder.min_qty_at_trigger == 5
        assert reorder.max_qty_at_trigger == 20
        trigger = _db.session.get(StockMovement, reorder.trigger_movement_id)
        assert trigger.movement_type == StockMovementType.SHIPMENT_DECREMENT
        assert verify_stock_integrity() == []


def test_reorder_qty_used_when_seeded(app):
    with app.app_context():
        item, _, _ = _seed(_sleeve(), on_hand=12, min_qty=5, max_qty=20, reorder_qty=7)
        _ship_pick_list()
        (reorder,) = _reorders(item)
        assert reorder.qty == 7


def test_compute_reorder_qty_floors_at_one(app):
    with app.app_context():
        item, _, reorder = _seed(_sleeve(), on_hand=20, min_qty=20, max_qty=20)
        # Degenerate on_hand == min == max: fallback (max - on_hand = 0)
        # still asks for something.
        assert reorder is not None
        assert compute_reorder_qty(item) == 1
        assert reorder.qty == 1


def test_null_thresholds_never_fire(app):
    """CP-5a regression: an UNSEEDED item can never open a reorder, no matter
    how far negative shipments drive it."""
    with app.app_context():
        _ship_pick_list()  # auto-creates the sleeve stock item at -10, unseeded
        item = (
            _db.session.query(StockItem)
            .filter_by(catalog_id=_sleeve().id)
            .one()
        )
        assert item.on_hand == -10
        assert not item.is_seeded
        assert _reorders() == []
        assert (
            _db.session.query(StockMovement)
            .filter_by(movement_type=StockMovementType.REORDER)
            .count()
            == 0
        )


def test_second_trigger_while_open_is_noop(app):
    with app.app_context():
        item, _, _ = _seed(_sleeve(), on_hand=12, min_qty=5, max_qty=20)
        _ship_pick_list("126-500")  # -> 2, fires
        assert len(_reorders(item)) == 1
        # Another drop while the reorder is open: claim absorbs it.
        record_adjustment(item, -1, _owner(), "damaged piece")
        _db.session.commit()
        assert len(_reorders(item)) == 1
        # And a receipt that still leaves it below min is a no-op too.
        record_receipt(item, 1, _owner(), "found one on the rack")
        _db.session.commit()
        assert len(_reorders(item)) == 1
        assert verify_stock_integrity() == []


def test_replayed_shipped_event_cannot_create_second_reorder(app):
    """The composed end-to-end negative test the task names: phantom
    decrement -> phantom reorder. CP-5a's decrement claim blocks the
    double-decrement; the reorder claim is the second gate."""
    with app.app_context():
        item, _, _ = _seed(_sleeve(), on_hand=12, min_qty=5, max_qty=20)
        pick_list = _ship_pick_list()
        assert item.on_hand == 2
        assert len(_reorders(item)) == 1
        movements_before = _db.session.query(StockMovement).count()

        # Replay 1: the state machine refuses to re-enter SHIPPED.
        assert advance_pick_list(pick_list, PickListStatus.SHIPPED, _owner()) is False

        # Replay 2: even a direct double-fire of the event applies nothing.
        assert consume_shipped_event(pick_list, _owner()) is False
        _db.session.commit()

        _db.session.refresh(item)
        assert item.on_hand == 2  # no double decrement
        assert len(_reorders(item)) == 1  # no second reorder
        assert _db.session.query(StockMovement).count() == movements_before
        assert verify_stock_integrity() == []


# ---------------------------------------------------------------------------
# Lifecycle: receive closes, re-arms, and short receipts re-fire
# ---------------------------------------------------------------------------


def test_receive_closes_reorder_and_rearms(app):
    with app.app_context():
        item, _, _ = _seed(_sleeve(), on_hand=12, min_qty=5, max_qty=20)
        _ship_pick_list("126-500")  # -> 2, fires reorder for 18
        (reorder,) = _reorders(item)
        movement, new_reorder = close_reorder(reorder, 18, _owner())
        _db.session.commit()
        assert reorder.status == ReorderStatus.RECEIVED
        assert reorder.received_at is not None
        assert reorder.receipt_movement_id == movement.id
        assert movement.movement_type == StockMovementType.RECEIPT
        assert movement.qty_delta == 18
        assert movement.details["reorder_id"] == reorder.id
        assert item.on_hand == 20
        assert new_reorder is None  # back above min: no re-fire

        # Re-armed: the NEXT drop to/below min opens a FRESH reorder.
        record_adjustment(item, -15, _owner(), "yard recount")
        _db.session.commit()
        reorders = _reorders(item)
        assert len(reorders) == 2
        assert reorders[1].status == ReorderStatus.OPEN
        assert reorders[1].id != reorder.id
        assert verify_stock_integrity() == []


def test_short_receipt_closes_and_refires_fresh_reorder(app):
    """Shop makes 8 of 18: book the actual 8, close, and the still-below-min
    state opens a fresh reorder in the same transaction (PM agreement)."""
    with app.app_context():
        item, _, _ = _seed(_sleeve(), on_hand=12, min_qty=5, max_qty=20)
        _ship_pick_list("126-500")  # -> 2
        (reorder,) = _reorders(item)
        movement, new_reorder = close_reorder(reorder, 3, _owner())  # -> 5 == min
        _db.session.commit()
        assert reorder.status == ReorderStatus.RECEIVED
        assert movement.qty_delta == 3
        assert new_reorder is not None
        assert new_reorder.id != reorder.id
        assert new_reorder.status == ReorderStatus.OPEN
        assert new_reorder.on_hand_at_trigger == 5
        assert verify_stock_integrity() == []


def test_receive_zero_closes_without_receipt(app):
    with app.app_context():
        item, _, reorder = _seed(_sleeve(), on_hand=2, min_qty=5, max_qty=20)
        movement, new_reorder = close_reorder(reorder, 0, _owner())
        _db.session.commit()
        assert movement is None
        assert reorder.status == ReorderStatus.RECEIVED
        assert reorder.receipt_movement_id is None
        # Still below min, so honesty demands a fresh reorder immediately.
        assert new_reorder is not None


def test_receive_twice_is_refused(app):
    with app.app_context():
        item, _, reorder = _seed(_sleeve(), on_hand=2, min_qty=5, max_qty=20)
        close_reorder(reorder, 18, _owner())
        _db.session.commit()
        with pytest.raises(StockError, match="already received"):
            close_reorder(reorder, 18, _owner())


def test_receive_route_and_reorders_page(app, client):
    with app.app_context():
        item, _, reorder = _seed(
            _sleeve(), on_hand=2, min_qty=5, max_qty=20, reorder_qty=12
        )
        reorder_id = reorder.id
    resp = client.get("/stock/reorders/")
    assert resp.status_code == 200
    assert b"Make 12" in resp.data
    assert b"SLV-12" in resp.data

    sheet = client.get(f"/stock/reorders/{reorder_id}/sheet")
    assert sheet.status_code == 200
    assert b"RESTOCK SHEET" in sheet.data
    assert b"Make 12" in sheet.data

    resp = client.post(
        f"/stock/reorders/{reorder_id}/receive",
        data={"received_qty": "12"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        closed = _db.session.get(Reorder, reorder_id)
        assert closed.status == ReorderStatus.RECEIVED
        assert closed.stock_item.on_hand == 14


def test_shop_queue_shows_reorders_tab(app, client):
    with app.app_context():
        _seed(_sleeve(), on_hand=2, min_qty=5, max_qty=20)
    resp = client.get("/pick-lists/")
    assert resp.status_code == 200
    assert b"Reorders" in resp.data
    assert b"(1)" in resp.data


# ---------------------------------------------------------------------------
# CSV import (dry-run preview + all-or-nothing apply)
# ---------------------------------------------------------------------------

CSV_OK = "part_number,on_hand,min,max,reorder_qty\nSLV-12,40,10,60,50\nBAG-6,7,,,\n"
CSV_BAD = "SLV-12,40,10,60,50\nNOPE-99,5,,,\nBAG-6,oops,,,\n"


def test_parse_seed_csv_reports_per_row(app):
    with app.app_context():
        results = parse_seed_csv(CSV_BAD)
        assert len(results) == 3
        assert results[0]["error"] is None
        assert "No active catalog product" in results[1]["error"]
        assert "whole number" in results[2]["error"]


def test_csv_dry_run_does_not_write(app, client):
    resp = client.post(
        "/stock/seed/import", data={"csv_text": CSV_OK, "mode": "preview"}
    )
    assert resp.status_code == 200
    assert resp.data.count(b"OK") >= 2
    with app.app_context():
        assert _db.session.query(StockItem).count() == 0
        assert _db.session.query(StockMovement).count() == 0


def test_csv_apply_with_errors_is_blocked(app, client):
    resp = client.post(
        "/stock/seed/import", data={"csv_text": CSV_BAD, "mode": "apply"}
    )
    assert resp.status_code == 200
    assert b"Not applied" in resp.data
    with app.app_context():
        assert _db.session.query(StockItem).count() == 0


def test_csv_apply_seeds_rows(app, client):
    resp = client.post(
        "/stock/seed/import",
        data={"csv_text": CSV_OK, "mode": "apply"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        sleeve = _db.session.query(StockItem).filter_by(catalog_id=_sleeve().id).one()
        assert (sleeve.on_hand, sleeve.min_qty, sleeve.max_qty, sleeve.reorder_qty) == (
            40,
            10,
            60,
            50,
        )
        bag = _db.session.query(StockItem).filter_by(catalog_id=_bag().id).one()
        assert bag.on_hand == 7 and not bag.is_seeded
        # Every applied row is a ledger ADJUSTMENT.
        seeds = (
            _db.session.query(StockMovement)
            .filter_by(movement_type=StockMovementType.ADJUSTMENT)
            .all()
        )
        assert {m.reason for m in seeds} == {REASON_INITIAL_SEED}
        assert verify_stock_integrity() == []
