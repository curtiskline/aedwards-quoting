"""CP-5a inventory: stock identity, movement ledger, idempotent decrement.

The load-bearing properties (design Stage F):
- the matcher is deterministic and NEVER guesses: part-number pass first,
  type+description fallback, zero/ambiguous -> UNMATCHED_SHIPMENT triage
  row, never a silent skip;
- the decrement is idempotent per pick list (claim-in-transaction §12.1):
  a replayed/double-fired shipped event applies NOTHING (negative-tested —
  phantom decrement -> phantom reorder is THE Stage-F hazard);
- on_hand is always derivable from the ledger (integrity checker);
- NULL min/max means UNSEEDED and no reorder logic can fire on it.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import create_app
from app.extensions import db as _db
from app.fulfillment import advance_pick_list, create_pick_list
from app.inventory import (
    MATCHED_BY_MANUAL_RESOLUTION,
    MATCHED_BY_PART_NUMBER,
    MATCHED_BY_TYPE_DESCRIPTION,
    StockError,
    consume_shipped_event,
    match_catalog_row,
    record_adjustment,
    record_receipt,
    resolve_unmatched,
    verify_stock_integrity,
)
from app.models import (
    AcceptanceSource,
    PickListStatus,
    ProductCatalog,
    Quote,
    QuoteStatus,
    QuoteVersion,
    StockDecrementClaim,
    StockItem,
    StockMovement,
    StockMovementType,
    User,
)
from app.orders import acceptable_version, create_order_from_acceptance

# sleeve matches by part number (pass 1), composite by type+description
# (pass 2, low-confidence), the mystery line matches nothing (triage).
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
    {
        "product_type": "composite",
        "description": "Wrap kit",
        "quantity": 3.0,
        "unit_price": 50.0,
        "line_total": 150.0,
        "part_number": None,
        "specs_json": None,
        "sort_order": 2,
    },
    {
        "product_type": "accessory",
        "description": "Mystery widget nobody catalogued",
        "quantity": 7.0,
        "unit_price": 5.0,
        "line_total": 35.0,
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
        _db.session.add(
            ProductCatalog(
                part_number="SLV-12",
                description='12" sleeve, 10 ft',
                product_type="sleeve",
            )
        )
        _db.session.add(
            ProductCatalog(
                part_number=None,
                description="Wrap kit",
                product_type="composite",
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


def _catalog(part_number=None, description=None) -> ProductCatalog:
    q = _db.session.query(ProductCatalog)
    if part_number is not None:
        q = q.filter_by(part_number=part_number)
    if description is not None:
        q = q.filter_by(description=description)
    return q.one()


def _make_shipped_pick_list(number: str = "126-300", snapshot=SNAPSHOT):
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
# Matcher: deterministic, never guesses
# ---------------------------------------------------------------------------


def test_match_by_part_number_normalized(app):
    with app.app_context():
        row, matched_by = match_catalog_row(
            {"part_number": "  slv-12 ", "product_type": "x", "description": "y"}
        )
        assert matched_by == MATCHED_BY_PART_NUMBER
        assert row.part_number == "SLV-12"


def test_ambiguous_part_number_goes_to_triage_not_fallback(app):
    with app.app_context():
        # A second active row with the same part number: pass 1 is ambiguous
        # and must NOT fall through to a description guess.
        _db.session.add(
            ProductCatalog(
                part_number="slv-12",
                description='12" sleeve, 10 ft',
                product_type="sleeve",
            )
        )
        _db.session.commit()
        row, matched_by = match_catalog_row(
            {
                "part_number": "SLV-12",
                "product_type": "sleeve",
                "description": '12" sleeve, 10 ft',
            }
        )
        assert row is None and matched_by is None


def test_fallback_matches_type_and_description(app):
    with app.app_context():
        row, matched_by = match_catalog_row(
            {"part_number": None, "product_type": "Composite", "description": " wrap  KIT "}
        )
        assert matched_by == MATCHED_BY_TYPE_DESCRIPTION
        assert row.description == "Wrap kit"


def test_fallback_requires_type_and_description_to_agree(app):
    with app.app_context():
        row, matched_by = match_catalog_row(
            {"part_number": None, "product_type": "sleeve", "description": "Wrap kit"}
        )
        assert row is None and matched_by is None


def test_inactive_rows_never_match(app):
    with app.app_context():
        _catalog(part_number="SLV-12").is_active = False
        _db.session.commit()
        row, matched_by = match_catalog_row(
            {"part_number": "SLV-12", "product_type": "", "description": ""}
        )
        assert row is None and matched_by is None


# ---------------------------------------------------------------------------
# Decrement on shipped: matched, low-confidence, unmatched — and idempotent
# ---------------------------------------------------------------------------


def test_shipped_decrements_and_records_match_provenance(app):
    with app.app_context():
        pick_list = _make_shipped_pick_list()

        sleeve_item = (
            _db.session.query(StockItem)
            .filter_by(catalog_id=_catalog(part_number="SLV-12").id)
            .one()
        )
        assert sleeve_item.on_hand == -10  # unseeded count, honest ledger
        wrap_item = (
            _db.session.query(StockItem)
            .filter_by(catalog_id=_catalog(description="Wrap kit").id)
            .one()
        )
        assert wrap_item.on_hand == -3

        movements = _db.session.query(StockMovement).order_by(StockMovement.id).all()
        by_type = {}
        for m in movements:
            by_type.setdefault(m.movement_type, []).append(m)
        decrements = by_type[StockMovementType.SHIPMENT_DECREMENT]
        assert {m.details["matched_by"] for m in decrements} == {
            MATCHED_BY_PART_NUMBER,
            MATCHED_BY_TYPE_DESCRIPTION,
        }
        assert all(m.pick_list_id == pick_list.id for m in movements)

        unmatched = by_type[StockMovementType.UNMATCHED_SHIPMENT]
        assert len(unmatched) == 1
        assert unmatched[0].stock_item_id is None
        assert unmatched[0].qty_delta == 0
        assert unmatched[0].details["pieces"] == 7
        assert (
            unmatched[0].details["line"]["description"]
            == "Mystery widget nobody catalogued"
        )
        # Shipping was excluded at pick-line build; nothing else leaked in.
        assert len(movements) == 3
        assert verify_stock_integrity() == []


def test_replayed_shipped_event_applies_nothing(app):
    """THE Stage-F hazard: a double-fired shipped event must not
    double-decrement (phantom decrement -> phantom reorder)."""
    with app.app_context():
        pick_list = _make_shipped_pick_list()
        before_movements = _db.session.query(StockMovement).count()
        sleeve_item = (
            _db.session.query(StockItem)
            .filter_by(catalog_id=_catalog(part_number="SLV-12").id)
            .one()
        )
        assert sleeve_item.on_hand == -10

        # Fire the consumer again directly — as a double-fired event would.
        assert consume_shipped_event(pick_list, _owner()) is False
        _db.session.commit()

        assert _db.session.query(StockMovement).count() == before_movements
        assert sleeve_item.on_hand == -10
        assert (
            _db.session.query(StockDecrementClaim)
            .filter_by(pick_list_id=pick_list.id)
            .count()
            == 1
        )
        # The state machine also refuses to re-enter SHIPPED at all.
        assert advance_pick_list(pick_list, PickListStatus.SHIPPED, _owner()) is False
        assert _db.session.query(StockMovement).count() == before_movements


# ---------------------------------------------------------------------------
# Triage: resolve applies the owed decrement, exactly once
# ---------------------------------------------------------------------------


def test_resolve_unmatched_applies_decrement_and_stamps(app):
    with app.app_context():
        _make_shipped_pick_list()
        unmatched = (
            _db.session.query(StockMovement)
            .filter_by(movement_type=StockMovementType.UNMATCHED_SHIPMENT)
            .one()
        )
        target = _catalog(description="Wrap kit")
        movement = resolve_unmatched(unmatched, target, _owner())
        _db.session.commit()

        assert movement.movement_type == StockMovementType.SHIPMENT_DECREMENT
        assert movement.qty_delta == -7
        assert movement.details["matched_by"] == MATCHED_BY_MANUAL_RESOLUTION
        assert unmatched.resolved_at is not None
        assert unmatched.resolution_movement_id == movement.id
        item = _db.session.query(StockItem).filter_by(catalog_id=target.id).one()
        assert item.on_hand == -10  # -3 from shipment, -7 from resolution
        assert verify_stock_integrity() == []

        # Resolving twice would double-decrement — refused.
        with pytest.raises(StockError):
            resolve_unmatched(unmatched, target, _owner())


# ---------------------------------------------------------------------------
# Manual movements: receipt + adjustment (reason required)
# ---------------------------------------------------------------------------


def test_receipt_and_adjustment_update_ledger(app):
    with app.app_context():
        pick_list = _make_shipped_pick_list()
        item = (
            _db.session.query(StockItem)
            .filter_by(catalog_id=_catalog(part_number="SLV-12").id)
            .one()
        )
        record_receipt(item, 25, _owner(), "PO 4711 from shop")
        record_adjustment(item, -2, _owner(), "two damaged in yard")
        _db.session.commit()
        assert item.on_hand == -10 + 25 - 2
        assert verify_stock_integrity() == []

        with pytest.raises(StockError):
            record_receipt(item, 0, _owner(), "zero pieces")
        with pytest.raises(StockError):
            record_receipt(item, 5, _owner(), "   ")
        with pytest.raises(StockError):
            record_adjustment(item, 0, _owner(), "no-op")
        with pytest.raises(StockError):
            record_adjustment(item, 3, _owner(), "")


# ---------------------------------------------------------------------------
# Ledger integrity: on_hand always derivable
# ---------------------------------------------------------------------------


def test_integrity_checker_catches_drift(app):
    with app.app_context():
        _make_shipped_pick_list()
        assert verify_stock_integrity() == []
        item = (
            _db.session.query(StockItem)
            .filter_by(catalog_id=_catalog(part_number="SLV-12").id)
            .one()
        )
        item.on_hand = 999  # corrupt outside the ledger
        _db.session.commit()
        problems = verify_stock_integrity()
        assert len(problems) == 1
        assert "on_hand=999" in problems[0]


# ---------------------------------------------------------------------------
# Unseeded semantics: NULL min/max can never trigger anything
# ---------------------------------------------------------------------------


def test_null_thresholds_never_trigger_reorder(app):
    with app.app_context():
        _make_shipped_pick_list()
        for item in _db.session.query(StockItem).all():
            assert item.is_seeded is False
            # Deeply negative on_hand, still unseeded: nothing may fire.
            assert item.needs_reorder is False
        # Partially seeded is still unseeded.
        item = _db.session.query(StockItem).first()
        item.min_qty = 5
        assert item.is_seeded is False
        assert item.needs_reorder is False
        # Fully seeded: the CP-5b seam activates.
        item.max_qty = 50
        assert item.is_seeded is True
        assert item.needs_reorder is True  # on_hand is negative, min is 5


# ---------------------------------------------------------------------------
# Routes: list badges, detail history + manual entry, triage resolve
# ---------------------------------------------------------------------------


def test_stock_list_shows_unseeded_negative_and_triage_banner(app, client):
    with app.app_context():
        _make_shipped_pick_list()
    resp = client.get("/stock/")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "SLV-12" in html
    assert "unseeded" in html
    assert "negative" in html
    assert "1 unmatched shipment" in html


def test_item_detail_shows_low_confidence_marker_and_forms(app, client):
    with app.app_context():
        _make_shipped_pick_list()
        wrap_item_id = (
            _db.session.query(StockItem)
            .filter_by(catalog_id=_catalog(description="Wrap kit").id)
            .one()
            .id
        )
    resp = client.get(f"/stock/items/{wrap_item_id}")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "low-confidence match" in html
    assert "SHIPMENT_DECREMENT" in html

    resp = client.post(
        f"/stock/items/{wrap_item_id}/receipt",
        data={"qty": "10", "reason": "shop run"},
        follow_redirects=True,
    )
    assert "on hand is now 7" in resp.get_data(as_text=True)

    # Reason is required: the movement must not be written without one.
    resp = client.post(
        f"/stock/items/{wrap_item_id}/adjustment",
        data={"qty_delta": "-1", "reason": "  "},
        follow_redirects=True,
    )
    assert "reason is required" in resp.get_data(as_text=True).lower()
    with app.app_context():
        assert _db.session.get(StockItem, wrap_item_id).on_hand == 7


def test_unmatched_triage_view_and_resolve_route(app, client):
    with app.app_context():
        _make_shipped_pick_list()
        unmatched_id = (
            _db.session.query(StockMovement)
            .filter_by(movement_type=StockMovementType.UNMATCHED_SHIPMENT)
            .one()
            .id
        )
        target_id = _catalog(part_number="SLV-12").id

    resp = client.get("/stock/unmatched")
    html = resp.get_data(as_text=True)
    assert "Mystery widget nobody catalogued" in html
    assert "7 pieces" in html

    resp = client.post(
        f"/stock/unmatched/{unmatched_id}/resolve",
        data={"catalog_id": str(target_id)},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert (
            _db.session.query(StockItem)
            .filter_by(catalog_id=target_id)
            .one()
            .on_hand
            == -17
        )
        assert verify_stock_integrity() == []
    # Triage queue is now empty.
    assert "Mystery widget" not in client.get("/stock/unmatched").get_data(as_text=True)
