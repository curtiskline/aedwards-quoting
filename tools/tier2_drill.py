"""Staging Tier-2 drill (task 403 verification plan, adapted: no monitor on staging).

Proves, with zero real mail:
  A. Tier 2 + fully eligible quote -> auto-send attempt runs and is BLOCKED by
     EMAIL_DELIVERY_ENABLED=false inside the send machinery: terminal 'blocked'
     claim naming the gate, auto_send_blocked audit row, quote NOT sent, no
     QuoteVersion.
  B. Kill-switch: tier back to 1, second eligible quote -> no attempt, no claim.
  C. Ineligible (new customer, no history) at tier 2 -> attempted=False, no claim.
Then deletes every drill record child-first and restores tier 1.
"""
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    AuditLog, AutoSendClaim, Customer, Quote, QuoteConfidence, QuoteLineItem,
    QuoteStatus, QuoteVersion, ShipToAddress, TrustRampConfig,
)
from app.confidence import sync_quote_confidence
from app import send_service

app = create_app()
SPECS = {"diameter": "12", "wall": "0.375", "grade": "GR50", "length": "10"}
SHIP = {
    "company": "Drill Test Co", "attention": "", "address_line1": "100 Drill Ave",
    "address_line2": "", "city": "Tulsa", "state": "OK", "postal_code": "74103",
    "country": "US",
}
PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))


def line(q, price):
    return QuoteLineItem(
        quote_id=q.id, product_type="sleeve", description="12in x 10ft sleeve GR50",
        quantity=2, unit_price=price, line_total=2 * price, specs_json=SPECS,
        part_number="DRL-SLV-12", sort_order=1,
    )


def set_tier(n):
    cfg = db.session.get(TrustRampConfig, 1) or TrustRampConfig(id=1)
    cfg.active_tier = n
    db.session.add(cfg)
    db.session.commit()


def eligible_quote(number, cust):
    q = Quote(
        quote_number=number, customer_id=cust.id, status=QuoteStatus.NEW,
        contact_email="drill@example.com", ship_to_json=dict(SHIP),
        created_at=datetime.utcnow(),
    )
    db.session.add(q)
    db.session.flush()
    db.session.add(line(q, 1000.00))
    db.session.flush()
    sync_quote_confidence(q)
    db.session.commit()
    return q


with app.app_context():
    old = datetime.utcnow() - timedelta(days=30)

    # ---- seed: established customer, confirmed address, SENT history ----
    cust = Customer(company_name="Drill Test Co", created_at=old)
    db.session.add(cust)
    db.session.flush()
    addr = ShipToAddress(
        customer_id=cust.id, address_line1=SHIP["address_line1"], address_line2="",
        city=SHIP["city"], state=SHIP["state"], postal_code=SHIP["postal_code"],
        country="US", human_confirmed=True,
    )
    db.session.add(addr)
    hist = []
    for i in range(3):
        h = Quote(
            quote_number=f"DRILL-H{i}", customer_id=cust.id, status=QuoteStatus.SENT,
            contact_email="drill@example.com", created_at=old + timedelta(days=i),
        )
        db.session.add(h)
        db.session.flush()
        db.session.add(line(h, 1000.00))
        hist.append(h)
    db.session.commit()

    # ---- Phase A: tier 2, eligible -> blocked by delivery gate ----
    print("== Phase A: tier 2, eligible quote ==")
    set_tier(2)
    qa = eligible_quote("DRILL-A", cust)
    conf = qa.confidence
    print(f"  score={conf.score} signals=" + ",".join(
        f"{n}:{getattr(conf, n)}" for n in (
            "decode_clean", "all_lines_priced", "customer_known",
            "ship_to_confirmed", "price_in_tolerance", "recipient_allowlisted")))
    res = send_service.maybe_auto_send(qa)
    print(f"  result={res}")
    db.session.refresh(qa)
    claim = db.session.query(AutoSendClaim).filter_by(quote_id=qa.id).first()
    check("A: claim exists and is 'blocked'", claim is not None and claim.status == "blocked",
          claim.status if claim else "no claim")
    check("A: block reason names delivery gate",
          claim is not None and "delivery" in (claim.error or "").lower(),
          (claim.error or "")[:80] if claim else "")
    audit = db.session.query(AuditLog).filter_by(quote_id=qa.id, action="auto_send_blocked").first()
    check("A: auto_send_blocked audit row with basis",
          audit is not None and audit.details and "score" in str(audit.details))
    check("A: quote NOT sent", qa.status != QuoteStatus.SENT, str(qa.status))
    check("A: no QuoteVersion", db.session.query(QuoteVersion).filter_by(quote_id=qa.id).count() == 0)

    # ---- Phase B: kill-switch (tier 1) ----
    print("== Phase B: tier back to 1 (kill-switch) ==")
    set_tier(1)
    qb = eligible_quote("DRILL-B", cust)
    res_b = send_service.maybe_auto_send(qb)
    check("B: no attempt at tier 1", res_b is None, str(res_b))
    check("B: no claim row", db.session.query(AutoSendClaim).filter_by(quote_id=qb.id).count() == 0)

    # ---- Phase C: tier 2 but ineligible (new customer, no history) ----
    print("== Phase C: tier 2, ineligible (new customer/no history) ==")
    set_tier(2)
    newcust = Customer(company_name="Drill Newco", created_at=datetime.utcnow())
    db.session.add(newcust)
    db.session.flush()
    qc = Quote(
        quote_number="DRILL-C", customer_id=newcust.id, status=QuoteStatus.NEW,
        contact_email="drill@example.com", created_at=datetime.utcnow(),
    )
    db.session.add(qc)
    db.session.flush()
    db.session.add(QuoteLineItem(
        quote_id=qc.id, product_type="girth_weld", description="drill gw set",
        quantity=1, unit_price=300.00, line_total=300.00, specs_json={"diameter": "24"},
        part_number="DRL-GW", sort_order=1))
    db.session.flush()
    sync_quote_confidence(qc)
    db.session.commit()
    res_c = send_service.maybe_auto_send(qc)
    check("C: attempted is False", res_c is not None and res_c.get("attempted") is False, str(res_c))
    check("C: marked ineligible with reasons",
          res_c is not None and res_c.get("eligible") is False and res_c.get("reasons"))
    check("C: no claim row", db.session.query(AutoSendClaim).filter_by(quote_id=qc.id).count() == 0)

    # ---- cleanup: child-first, then restore tier 1 ----
    print("== Cleanup ==")
    drill_ids = [q.id for q in [qa, qb, qc] + hist]
    for model in (AuditLog, AutoSendClaim, QuoteConfidence, QuoteLineItem, QuoteVersion):
        db.session.query(model).filter(model.quote_id.in_(drill_ids)).delete(synchronize_session=False)
    db.session.query(Quote).filter(Quote.id.in_(drill_ids)).delete(synchronize_session=False)
    db.session.query(ShipToAddress).filter_by(customer_id=cust.id).delete(synchronize_session=False)
    db.session.query(Customer).filter(Customer.id.in_([cust.id, newcust.id])).delete(synchronize_session=False)
    set_tier(1)
    db.session.commit()
    leftover = db.session.query(Quote).filter(Quote.quote_number.like("DRILL%")).count()
    check("cleanup: no drill quotes remain", leftover == 0, str(leftover))
    cfg = db.session.get(TrustRampConfig, 1)
    check("cleanup: tier restored to 1", cfg.active_tier == 1)

    print(f"\nDRILL {'PASSED' if not FAILED else 'FAILED'}: {len(PASSED)} ok, {len(FAILED)} failed")
    raise SystemExit(0 if not FAILED else 1)
