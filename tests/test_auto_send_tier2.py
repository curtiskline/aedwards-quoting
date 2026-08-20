"""CP-2c: Tier-2 auto-send — confidence-gated, hard guardrails, kill-switch.

Covers:
- the Tier-2 eligibility gate (every guardrail, each with a negative flip),
- the claim-in-transaction idempotency pattern (crash between claim-commit
  and send can never double-send; replay after success is a no-op),
- gate enforcement INSIDE the shared send machinery (delivery flag +
  allowlist block even when the stored confidence signals say pass),
- visible surfacing of interrupted/failed claims + clean human resend after
  an orphaned claim (no artifact collision),
- the admin dials (threshold / ceiling / tolerance) and Tier-2 admin form,
- the web POST trigger, the GET-never-sends rule, and the monitor hook.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import create_app
from app.confidence import auto_send_evaluation, sync_quote_confidence
from app.extensions import db as _db
from app.models import (
    AuditLog,
    AutoSendClaim,
    Customer,
    Quote,
    QuoteLineItem,
    QuoteStatus,
    QuoteVersion,
    SendHold,
    ShipToAddress,
    TrustRampConfig,
    User,
)
from app.send_service import SendBlocked, check_send_gates, maybe_auto_send

LONG_AGO = datetime(2026, 1, 1)

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

SPECS = {"diameter": "12", "wall_thickness": "0.375", "grade": "50"}


@pytest.fixture()
def app(db_url, tmp_path, monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", db_url)
    monkeypatch.setattr(Config, "TESTING", True, raising=False)
    monkeypatch.delenv("SEND_EMAIL_ALLOWLIST", raising=False)
    monkeypatch.delenv("CONFIDENCE_PRICE_TOLERANCE_PCT", raising=False)
    monkeypatch.delenv("CONFIDENCE_RECOMMEND_THRESHOLD", raising=False)
    monkeypatch.delenv("AUTO_SEND_THRESHOLD", raising=False)
    monkeypatch.delenv("AUTO_SEND_DOLLAR_CEILING", raising=False)
    monkeypatch.delenv("EMAIL_DELIVERY_ENABLED", raising=False)
    monkeypatch.setenv("O365_EMAIL", "AEResponder@allanedwards.com")
    monkeypatch.setenv("O365_PASSWORD", "test-password")
    monkeypatch.setenv("ENABLE_OUTLOOK_DRAFTS", "false")
    application = create_app()
    application.config["QUOTE_ARTIFACT_DIR"] = str(tmp_path / "quote_versions")
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


def _set_tier(tier: int, **dials):
    cfg = _db.session.get(TrustRampConfig, 1)
    if cfg is None:
        cfg = TrustRampConfig(id=1)
        _db.session.add(cfg)
    cfg.active_tier = tier
    for key, value in dials.items():
        setattr(cfg, key, value)
    _db.session.commit()


def _make_customer(created_at=LONG_AGO, confirmed=True, name="Acme Pipeline"):
    customer = Customer(company_name=name, discount_pct=0)
    customer.created_at = created_at
    _db.session.add(customer)
    _db.session.flush()
    _db.session.add(
        ShipToAddress(
            customer_id=customer.id,
            address_line1=SHIP_TO["address_line1"],
            city=SHIP_TO["city"],
            state=SHIP_TO["state"],
            postal_code=SHIP_TO["postal_code"],
            country=SHIP_TO["country"],
            is_default=True,
            human_confirmed=confirmed,
        )
    )
    _db.session.flush()
    return customer


def _add_line(quote, unit_price=100.0, quantity=2, product_type="sleeve", specs=None,
              description="12in sleeve", part_number="SLV-12"):
    item = QuoteLineItem(
        quote_id=quote.id,
        product_type=product_type,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        line_total=unit_price * quantity,
        specs_json=specs if specs is not None else dict(SPECS),
        part_number=part_number,
        sort_order=1,
    )
    _db.session.add(item)
    _db.session.flush()
    return item


def _make_history(customer, unit_price=100.0, number="126-100"):
    """A SENT quote with matching specs — the price-tolerance baseline."""
    sent = Quote(
        quote_number=number,
        status=QuoteStatus.SENT,
        customer_id=customer.id,
        contact_email="buyer@acme.com",
    )
    sent.created_at = LONG_AGO
    _db.session.add(sent)
    _db.session.flush()
    _add_line(sent, unit_price=unit_price)
    return sent


def _eligible_quote(number="126-500", contact_email="buyer@acme.com"):
    """Baseline quote that passes EVERY Tier-2 guardrail."""
    customer = _make_customer()
    _make_history(customer)
    quote = Quote(
        quote_number=number,
        status=QuoteStatus.IN_REVIEW,
        customer_id=customer.id,
        contact_email=contact_email,
        ship_to_json=dict(SHIP_TO),
        customer_name_raw=customer.company_name,
        source_email_id="AAMk-test-rfq-1",
    )
    _db.session.add(quote)
    _db.session.flush()
    _add_line(quote)
    sync_quote_confidence(quote)
    _db.session.commit()
    return quote


def _mock_client():
    client = MagicMock()
    client.send_mail.return_value = None
    return client


def _claim(quote):
    return _db.session.query(AutoSendClaim).filter_by(quote_id=quote.id).first()


def _audit(quote, action):
    return (
        _db.session.query(AuditLog).filter_by(quote_id=quote.id, action=action).all()
    )


# ---------------------------------------------------------------------------
# Happy path + audit
# ---------------------------------------------------------------------------


@patch("allenedwards.outlook.OutlookClient")
def test_tier2_eligible_quote_auto_sends_with_full_audit(mock_outlook, app):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result == {"attempted": True, "sent": True, "version_number": 1}
    mock_client.send_mail.assert_called_once()
    kwargs = mock_client.send_mail.call_args.kwargs
    assert kwargs["to_email"] == "buyer@acme.com"
    assert kwargs["attachments"][0][0].endswith(".pdf")

    assert quote.status == QuoteStatus.SENT
    claim = _claim(quote)
    assert claim is not None and claim.status == "sent"

    versions = _db.session.query(QuoteVersion).filter_by(quote_id=quote.id).all()
    assert len(versions) == 1
    version = versions[0]
    assert version.artifact_status == "retained"
    assert version.sent_to == "buyer@acme.com"
    assert version.sent_by is None
    assert version.line_items_snapshot[0]["line_total"] == "200.00"
    assert Path(version.pdf_path).is_file()
    assert claim.quote_version_id == version.id

    audits = _audit(quote, "auto_sent")
    assert len(audits) == 1
    details = audits[0].details
    assert details["tier"] == 2
    assert details["auto_send_threshold"] == pytest.approx(0.95)
    assert details["score"] == pytest.approx(1.0)
    assert details["dollar_ceiling"] == pytest.approx(2500.0)
    assert details["quote_total"] == pytest.approx(200.0)
    assert details["source_email_id"] == "AAMk-test-rfq-1"
    assert details["signals"]["price_in_tolerance"] == "pass"
    assert details["components"]["price_in_tolerance"]["status"] == "pass"
    assert details["version_number"] == 1


# ---------------------------------------------------------------------------
# Kill-switch: tiers 0/1 never auto-send, read live on every attempt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", [0, 1])
@patch("allenedwards.outlook.OutlookClient")
def test_tier_below_2_never_auto_sends(mock_outlook, app, tier):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    _set_tier(tier)

    result = maybe_auto_send(quote)

    assert result is None
    mock_client.send_mail.assert_not_called()
    assert quote.status == QuoteStatus.IN_REVIEW
    assert _claim(quote) is None


@patch("allenedwards.outlook.OutlookClient")
def test_dropping_tier_stops_auto_sends_immediately(mock_outlook, app):
    """The tier is read live: flipping 2 -> 1 between attempts is the kill-switch."""
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    _set_tier(2)
    assert auto_send_evaluation(quote)["eligible"] is True

    _set_tier(1)
    assert maybe_auto_send(quote) is None
    mock_client.send_mail.assert_not_called()


# ---------------------------------------------------------------------------
# Guardrails, each as its own test with the single flipped condition
# ---------------------------------------------------------------------------


@patch("allenedwards.outlook.OutlookClient")
def test_unpriced_line_never_auto_sends(mock_outlook, app):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    _add_line(quote, unit_price=0, description="mystery part", part_number="X-0")
    sync_quote_confidence(quote)
    _db.session.commit()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("All lines priced" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()
    assert _claim(quote) is None


@patch("allenedwards.outlook.OutlookClient")
def test_new_customer_not_eligible(mock_outlook, app):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    customer = _db.session.get(Customer, quote.customer_id)
    customer.created_at = quote.created_at  # created with this quote = new
    # remove the prior-history quote so the customer has no earlier quotes
    for old in _db.session.query(Quote).filter(Quote.id != quote.id).all():
        old.customer_id = None
    sync_quote_confidence(quote)
    _db.session.commit()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("Customer known" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_unconfirmed_ship_to_not_eligible(mock_outlook, app):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    address = (
        _db.session.query(ShipToAddress).filter_by(customer_id=quote.customer_id).one()
    )
    address.human_confirmed = False
    sync_quote_confidence(quote)
    _db.session.commit()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("Ship-to confirmed" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_unknown_price_history_not_eligible(mock_outlook, app, monkeypatch):
    """No comparable history = unknown = NOT eligible (never treated as pass)."""
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    monkeypatch.setenv("CONFIDENCE_RECOMMEND_THRESHOLD", "0.75")
    quote = _eligible_quote()
    history = _db.session.query(Quote).filter_by(status=QuoteStatus.SENT).one()
    for item in history.line_items:
        item.product_type = "bag"  # no longer comparable
    sync_quote_confidence(quote)
    _db.session.commit()
    # Threshold lowered below the unknown-degraded score (0.80) on purpose:
    # the REQUIRED-signal gate must block unknown on its own, not lean on
    # the threshold catching the missing points.
    _set_tier(2, auto_send_threshold=0.75)

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("requires Price in tolerance to pass, is unknown" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_out_of_tolerance_price_not_eligible(mock_outlook, app):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    quote.line_items[0].unit_price = 150.0  # +50% vs the 100.0 history median
    quote.line_items[0].line_total = 300.0
    sync_quote_confidence(quote)
    _db.session.commit()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("Price in tolerance" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_recipient_not_on_allowlist_not_eligible(mock_outlook, app, monkeypatch):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    monkeypatch.setenv("SEND_EMAIL_ALLOWLIST", "someoneelse@example.com")
    sync_quote_confidence(quote)
    _db.session.commit()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("Recipient allowed" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()


@pytest.mark.parametrize("hold_kind", ["customer", "product_type"])
@patch("allenedwards.outlook.OutlookClient")
def test_admin_holds_block_auto_send(mock_outlook, app, hold_kind):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    if hold_kind == "customer":
        _db.session.add(SendHold(customer_id=quote.customer_id, reason="pause"))
    else:
        _db.session.add(SendHold(product_type="sleeve", reason="pause"))
    _db.session.commit()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("hold" in r.lower() for r in result["reasons"])
    mock_client.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_score_below_auto_send_threshold_not_eligible(mock_outlook, app):
    """The auto-send threshold is stricter than the recommend threshold."""
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    # All signals pass but the stored composite sits between the recommend
    # threshold (0.90) and the auto-send threshold (0.95).
    quote.confidence.score = 0.90
    _db.session.commit()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("auto-send threshold" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_dollar_ceiling_blocks_and_admin_dial_raises_it(mock_outlook, app):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    _set_tier(2, auto_send_dollar_ceiling=100.0)  # quote total is 200.0

    result = maybe_auto_send(quote)
    assert result["attempted"] is False
    assert any("ceiling" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()

    _set_tier(2, auto_send_dollar_ceiling=500.0)
    result = maybe_auto_send(quote)
    assert result["sent"] is True
    mock_client.send_mail.assert_called_once()


@patch("allenedwards.outlook.OutlookClient")
def test_tolerance_dial_tightens_price_signal(mock_outlook, app):
    """The admin tolerance dial feeds the price_in_tolerance signal itself."""
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    quote.line_items[0].unit_price = 110.0  # 10% off the 100.0 median
    quote.line_items[0].line_total = 220.0
    _set_tier(2, price_tolerance_pct=0.05)
    sync_quote_confidence(quote)
    _db.session.commit()

    result = maybe_auto_send(quote)

    assert result["attempted"] is False
    assert any("Price in tolerance" in r for r in result["reasons"])
    mock_client.send_mail.assert_not_called()


# ---------------------------------------------------------------------------
# Gates enforced INSIDE the send machinery (not only via eligibility)
# ---------------------------------------------------------------------------


@patch("allenedwards.outlook.OutlookClient")
def test_delivery_disabled_blocks_at_send_gate_with_claim_and_audit(
    mock_outlook, app, monkeypatch
):
    """Staging story: eligibility passes, the delivery gate blocks the send,
    and a durable blocked claim + audit row prove the gate held."""
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    _set_tier(2)
    monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "false")

    result = maybe_auto_send(quote)

    assert result["blocked"] is True
    mock_client.send_mail.assert_not_called()
    assert quote.status == QuoteStatus.IN_REVIEW
    claim = _claim(quote)
    assert claim.status == "blocked"
    assert "disabled" in claim.error
    assert _db.session.query(QuoteVersion).filter_by(quote_id=quote.id).count() == 0
    audits = _audit(quote, "auto_send_blocked")
    assert len(audits) == 1 and "disabled" in audits[0].details["reason"]

    # The blocked claim is terminal: re-enabling delivery does not auto-retry.
    monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "true")
    result = maybe_auto_send(quote)
    assert result == {"attempted": False, "already_claimed": True, "claim_status": "blocked"}
    mock_client.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_allowlist_enforced_inside_send_machinery(mock_outlook, app, monkeypatch):
    """Even if the stored signals say pass (scored before the allowlist
    changed), the shared send gate still refuses the recipient."""
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()  # scored with no allowlist -> signal pass
    _set_tier(2)
    monkeypatch.setenv("SEND_EMAIL_ALLOWLIST", "someoneelse@example.com")

    result = maybe_auto_send(quote)

    assert result["blocked"] is True
    mock_client.send_mail.assert_not_called()
    assert _claim(quote).status == "blocked"
    assert "allowed send list" in _claim(quote).error


def test_check_send_gates_unit(app, monkeypatch):
    quote = _eligible_quote()
    check_send_gates(quote, "buyer@acme.com")  # passes

    with pytest.raises(SendBlocked, match="allowed send list"):
        monkeypatch.setenv("SEND_EMAIL_ALLOWLIST", "x@y.com")
        check_send_gates(quote, "buyer@acme.com")
    monkeypatch.delenv("SEND_EMAIL_ALLOWLIST")

    with pytest.raises(SendBlocked, match="delivery is disabled"):
        monkeypatch.setenv("EMAIL_DELIVERY_ENABLED", "false")
        check_send_gates(quote, "buyer@acme.com")
    monkeypatch.delenv("EMAIL_DELIVERY_ENABLED")

    with pytest.raises(SendBlocked, match="Recipient email is required"):
        check_send_gates(quote, "")

    quote.status = QuoteStatus.NEEDS_PRICING
    with pytest.raises(SendBlocked, match="needs pricing"):
        check_send_gates(quote, "buyer@acme.com")


# ---------------------------------------------------------------------------
# Idempotency: the claim-in-transaction pattern
# ---------------------------------------------------------------------------


@patch("allenedwards.outlook.OutlookClient")
def test_crash_between_claim_and_send_never_double_sends(mock_outlook, app):
    """THE negative test from the task: simulate a process crash after the
    claim+version commit but before the external send, then replay."""
    crash_client = MagicMock()
    crash_client.send_mail.side_effect = KeyboardInterrupt  # process death:
    # BaseException skips auto_send_quote's failure handling entirely, so
    # nothing after the claim commit runs — exactly like a kill.
    mock_outlook.return_value = crash_client
    quote = _eligible_quote()
    _set_tier(2)

    with pytest.raises(KeyboardInterrupt):
        maybe_auto_send(quote)
    # A real process death discards any open (uncommitted) transaction;
    # only what was COMMITTED before the send survives. Without this, a
    # flush-not-commit implementation would pass undetected.
    _db.session.rollback()

    # The claim and version committed before the send; the email never went.
    claim = _claim(quote)
    assert claim.status == "claimed"
    assert quote.status == QuoteStatus.IN_REVIEW  # never marked SENT
    assert _db.session.query(QuoteVersion).filter_by(quote_id=quote.id).count() == 1

    # Replay with a working mailer: the claim blocks any second attempt.
    working_client = _mock_client()
    mock_outlook.return_value = working_client
    result = maybe_auto_send(quote)
    assert result == {"attempted": False, "already_claimed": True, "claim_status": "claimed"}
    working_client.send_mail.assert_not_called()
    assert _db.session.query(AutoSendClaim).filter_by(quote_id=quote.id).count() == 1


@patch("allenedwards.outlook.OutlookClient")
def test_replay_after_successful_auto_send_is_noop(mock_outlook, app):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    _set_tier(2)
    assert maybe_auto_send(quote)["sent"] is True

    result = maybe_auto_send(quote)

    assert result == {"attempted": False, "already_claimed": True, "claim_status": "sent"}
    mock_client.send_mail.assert_called_once()  # still exactly one send
    assert _db.session.query(QuoteVersion).filter_by(quote_id=quote.id).count() == 1


@patch("allenedwards.outlook.OutlookClient")
def test_send_failure_releases_version_and_never_retries(mock_outlook, app):
    failing_client = MagicMock()
    failing_client.send_mail.side_effect = RuntimeError("Graph API 503")
    mock_outlook.return_value = failing_client
    quote = _eligible_quote()
    _set_tier(2)

    result = maybe_auto_send(quote)

    assert result == {"attempted": True, "sent": False, "error": "Graph API 503"}
    assert quote.status == QuoteStatus.IN_REVIEW
    claim = _claim(quote)
    assert claim.status == "failed" and "503" in claim.error
    assert _db.session.query(QuoteVersion).filter_by(quote_id=quote.id).count() == 0
    artifact_dir = Path(app.config["QUOTE_ARTIFACT_DIR"])
    assert not list(artifact_dir.glob("*.pdf"))  # archive released
    assert len(_audit(quote, "auto_send_failed")) == 1

    # No auto-retry, even with a now-working mailer.
    working_client = _mock_client()
    mock_outlook.return_value = working_client
    result = maybe_auto_send(quote)
    assert result["already_claimed"] is True
    working_client.send_mail.assert_not_called()


@patch("allenedwards.outlook.OutlookClient")
def test_human_send_still_works_after_orphaned_claim(mock_outlook, app, client):
    """PM requirement: the human path must handle a claimed-but-unsent quote
    cleanly — new version number, no artifact collision, claim untouched."""
    crash_client = MagicMock()
    crash_client.send_mail.side_effect = KeyboardInterrupt
    mock_outlook.return_value = crash_client
    quote = _eligible_quote()
    quote_id = quote.id
    _set_tier(2)
    with pytest.raises(KeyboardInterrupt):
        maybe_auto_send(quote)
    _db.session.rollback()  # simulate process death: open txn is lost
    assert _claim(quote).status == "claimed"

    mock_outlook.return_value = _mock_client()
    resp = client.post(
        f"/quotes/{quote_id}/send",
        data={"to_email": "buyer@acme.com", "subject": "Quote 126-500"},
    )

    assert resp.status_code == 200
    assert "Quote Sent" in resp.data.decode()
    versions = (
        _db.session.query(QuoteVersion)
        .filter_by(quote_id=quote_id)
        .order_by(QuoteVersion.version_number)
        .all()
    )
    assert [v.version_number for v in versions] == [1, 2]  # orphan + human send
    assert Path(versions[1].pdf_path).is_file()
    quote = _db.session.get(Quote, quote_id)
    assert quote.status == QuoteStatus.SENT
    assert _claim(quote).status == "claimed"  # human path never touches claims


# ---------------------------------------------------------------------------
# Visible surfacing of claims
# ---------------------------------------------------------------------------


@patch("allenedwards.outlook.OutlookClient")
def test_orphaned_claim_surfaced_in_editor_and_queue(mock_outlook, app, client):
    crash_client = MagicMock()
    crash_client.send_mail.side_effect = KeyboardInterrupt
    mock_outlook.return_value = crash_client
    quote = _eligible_quote()
    quote_id = quote.id
    _set_tier(2)
    with pytest.raises(KeyboardInterrupt):
        maybe_auto_send(quote)
    _db.session.rollback()  # simulate process death: open txn is lost
    _set_tier(1)  # surfacing must not depend on staying at Tier 2

    editor = client.get(f"/quotes/{quote_id}").data.decode()
    assert "Auto-send interrupted" in editor
    assert "NEEDS ATTENTION" in editor
    assert "did NOT go out" in editor

    queue = client.get("/quotes/").data.decode()
    assert "Auto-send needs attention" in queue

    admin = client.get("/admin/pricing?tab=trust").data.decode()
    assert "send never completed" in admin


@patch("allenedwards.outlook.OutlookClient")
def test_auto_sent_quote_labeled_in_queue(mock_outlook, app, client):
    mock_outlook.return_value = _mock_client()
    quote = _eligible_quote()
    _set_tier(2)
    assert maybe_auto_send(quote)["sent"] is True

    queue = client.get("/quotes/?status=sent").data.decode()
    assert "Auto-sent" in queue


# ---------------------------------------------------------------------------
# Web triggers: POST recomputes attempt auto-send; GET never sends
# ---------------------------------------------------------------------------


@patch("allenedwards.outlook.OutlookClient")
def test_post_recompute_route_triggers_auto_send(mock_outlook, app, client):
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    quote_id = quote.id
    _set_tier(2)

    resp = client.post(f"/quotes/{quote_id}/status", data={"status": "ready"})

    assert resp.status_code == 200
    mock_client.send_mail.assert_called_once()
    assert _db.session.get(Quote, quote_id).status == QuoteStatus.SENT


@patch("allenedwards.outlook.OutlookClient")
def test_get_detail_view_never_auto_sends(mock_outlook, app, client):
    """Viewing a quote recomputes confidence but must NEVER send it."""
    mock_client = _mock_client()
    mock_outlook.return_value = mock_client
    quote = _eligible_quote()
    quote_id = quote.id
    _set_tier(2)

    resp = client.get(f"/quotes/{quote_id}")

    assert resp.status_code == 200
    mock_client.send_mail.assert_not_called()
    assert _db.session.get(Quote, quote_id).status != QuoteStatus.SENT
    assert _claim(_db.session.get(Quote, quote_id)) is None


# ---------------------------------------------------------------------------
# Admin: tier 2 + dials
# ---------------------------------------------------------------------------


def test_admin_accepts_tier_2(app, client):
    client.post("/admin/trust-ramp/tier", data={"active_tier": "2"}, follow_redirects=True)
    assert _db.session.get(TrustRampConfig, 1).active_tier == 2


def test_admin_dials_save_and_feed_the_gate(app, client):
    from app.confidence import (
        auto_send_dollar_ceiling,
        auto_send_threshold,
        price_tolerance_pct,
    )

    resp = client.post(
        "/admin/trust-ramp/dials",
        data={
            "auto_send_threshold": "0.97",
            "auto_send_dollar_ceiling": "1000",
            "price_tolerance_pct": "0.10",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    cfg = _db.session.get(TrustRampConfig, 1)
    assert float(cfg.auto_send_threshold) == pytest.approx(0.97)
    assert float(cfg.auto_send_dollar_ceiling) == pytest.approx(1000.0)
    assert float(cfg.price_tolerance_pct) == pytest.approx(0.10)
    assert auto_send_threshold() == pytest.approx(0.97)
    assert auto_send_dollar_ceiling() == pytest.approx(1000.0)
    assert price_tolerance_pct() == pytest.approx(0.10)


def test_admin_dials_reject_bad_values(app, client):
    client.post(
        "/admin/trust-ramp/dials",
        data={
            "auto_send_threshold": "1.5",
            "auto_send_dollar_ceiling": "1000",
            "price_tolerance_pct": "0.10",
        },
        follow_redirects=True,
    )
    cfg = _db.session.get(TrustRampConfig, 1)
    assert cfg is None or cfg.auto_send_threshold is None


def test_dashboard_surfaces_dial_values_at_tier_2(app, client):
    _set_tier(2)
    html = client.get("/quotes/").data.decode()
    assert "auto-send" in html
    assert "threshold 95%" in html
    assert "$2500.00" in html
    admin = client.get("/admin/pricing?tab=trust").data.decode()
    assert "Auto-send dials" in admin
    assert "0.95" in admin and "2500.00" in admin and "0.20" in admin


# ---------------------------------------------------------------------------
# Monitor hook
# ---------------------------------------------------------------------------


def _monitor(app, tmp_path, message, provider, drafts=False):
    from allenedwards.monitor import InboxMonitor

    outlook = MagicMock()
    outlook.fetch_messages.return_value = [message]
    return InboxMonitor(
        outlook=outlook,
        provider=provider,
        poll_interval_seconds=60,
        state_path=tmp_path / "state.json",
        output_dir=tmp_path / "out",
        enable_db_writes=True,
        enable_outlook_drafts=drafts,
        flask_app=app,
    ), outlook


class _RFQProvider:
    def complete_json(self, prompt: str, system: str = "") -> dict:
        if "Classify" in system or "classifier" in system:
            return {"is_rfq": True, "confidence": 0.95, "reason": "pipe products"}
        return {
            "customer_name": "Acme Pipeline",
            "contact_name": "Buyer",
            "contact_email": "buyer@acme.com",
            "contact_phone": None,
            "quote_number": None,
            "quotes": [
                {
                    "project_line": None,
                    "ship_to": None,
                    "po_number": None,
                    "items": [
                        {
                            "product_type": "sleeve",
                            "quantity": 2,
                            "diameter": "12",
                            "wall_thickness": "0.375",
                            "grade": "50",
                            "milling": False,
                            "painting": False,
                            "description": "12in sleeve",
                        }
                    ],
                }
            ],
            "urgency": "normal",
            "notes": None,
            "confidence": 0.9,
        }


def _rfq_message():
    from allenedwards.outlook import OutlookMessage

    return OutlookMessage(
        id="AAMk-autosend-monitor-1",
        subject="RFQ - sleeves",
        sender_name="Buyer",
        sender_email="buyer@acme.com",
        body_preview="Please quote 2 sleeves",
        body_content="Please quote 2 pcs 12 x 0.375 GR50 sleeves",
        body_content_type="text",
        internet_message_id="<autosend@example.com>",
        received_datetime="2026-08-20T12:00:00Z",
        has_attachments=False,
    )


def test_monitor_write_attempts_auto_send_per_quote(app, tmp_path):
    monitor, _outlook = _monitor(app, tmp_path, _rfq_message(), _RFQProvider())
    seen: list[str] = []

    def _record(quote):
        seen.append(quote.source_email_id)
        return {"attempted": False, "eligible": False, "reasons": []}

    with patch("app.send_service.maybe_auto_send", side_effect=_record):
        assert monitor.run_once() == 1
    assert seen == ["AAMk-autosend-monitor-1"]


def test_monitor_skips_review_draft_for_auto_sent_quote(app, tmp_path):
    monitor, outlook = _monitor(app, tmp_path, _rfq_message(), _RFQProvider(), drafts=True)
    with patch("app.send_service.maybe_auto_send") as mock_attempt:
        mock_attempt.return_value = {"attempted": True, "sent": True, "version_number": 1}
        assert monitor.run_once() == 1
    outlook.create_draft.assert_not_called()


def test_monitor_still_creates_draft_when_not_auto_sent(app, tmp_path):
    monitor, outlook = _monitor(app, tmp_path, _rfq_message(), _RFQProvider(), drafts=True)
    with patch("app.send_service.maybe_auto_send") as mock_attempt:
        mock_attempt.return_value = {"attempted": False, "eligible": False, "reasons": []}
        assert monitor.run_once() == 1
    outlook.create_draft.assert_called_once()
