#!/usr/bin/env python3
"""Build the demo-ready STAGING dataset from the real RFQ corpus (task 408).

Feeds curated REAL .eml messages (tools/demo_corpus_manifest.json) through the
REAL pipeline — classify -> parse -> price -> db_writer, exactly the monitor's
path but with no mailbox — and seeds the history that makes the CP-2
confidence signals meaningful: backdated customers, human-confirmed ship-to
addresses, and SENT quotes carrying REAL prices from the ground-truth corpus
(data/test-corpus/ground-truth, task K26) so price_in_tolerance has genuine
comparables.

Phases (run in this order; `all` runs them in sequence):
  reset         wipe quote-family + customer tables (users/pricing/trust ramp kept)
  seed-history  customers + confirmed addresses + backdated SENT quotes from
                ground-truth JSON (real prices)
  ingest        run every manifest .eml through classify/parse/price/db-write;
                then apply per-case directives (confirm_ship_to)
  rescore       recompute every quote's confidence row
  report        print the dataset shape: category / status / signal / recommendation mix

SAFETY: the reset phase deletes data, so this tool refuses to run anywhere but
the staging box (hostname aedwards-staging AND EMAIL_DELIVERY_ENABLED=false AND
an explicit --i-am-staging flag). Local development testing is possible only
against a sqlite:// DATABASE_URL with --dev-sandbox. There is deliberately no
override that would let it touch production (D67 prod freeze).

Mail isolation: nothing here sends, drafts, or touches a mailbox. The LLM
decode does need ANTHROPIC_API_KEY (already present on staging).

Idempotency: each ingested email takes the monitor's ProcessedInboundEmail
claim under source_email_id "demo-corpus:<filename>", so re-running `ingest`
skips already-loaded cases. A fresh demo reset is `all` (reset + reseed).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import socket
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

DEMO_SOURCE_PREFIX = "demo-corpus:"
SEED_SOURCE_PREFIX = "demo-seed:"
DEFAULT_ENV_FILE = Path("/opt/aedwards/.env")
STAGING_HOSTNAME = "aedwards-staging"

_FALSY = {"0", "false", "no"}


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines without overriding the existing process env."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def assert_safe_environment(args: argparse.Namespace) -> None:
    """Refuse to run anywhere but staging (or a local sqlite sandbox).

    The reset phase is destructive; a demo-reset tool that can point at prod
    is how the D67 freeze gets violated by accident. No flag combination may
    bypass BOTH the hostname check and the sqlite-only sandbox path.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if args.dev_sandbox:
        if db_url and not db_url.startswith("sqlite:"):
            sys.exit(
                "REFUSING: --dev-sandbox only runs against a sqlite:// DATABASE_URL "
                f"(got {db_url.split('@')[0]!r}...). Unset DATABASE_URL or point it at a scratch sqlite file."
            )
        return
    if not args.i_am_staging:
        sys.exit("REFUSING: this tool modifies/deletes data. Pass --i-am-staging on the staging box (or --dev-sandbox with a sqlite DB for local testing).")
    hostname = socket.gethostname()
    if hostname != STAGING_HOSTNAME:
        sys.exit(f"REFUSING: hostname is {hostname!r}, expected {STAGING_HOSTNAME!r}. This tool only runs on the staging box.")
    delivery = os.environ.get("EMAIL_DELIVERY_ENABLED", "").strip().lower()
    if delivery not in _FALSY:
        sys.exit(
            f"REFUSING: EMAIL_DELIVERY_ENABLED={delivery!r} — staging must have outbound mail disabled. "
            "If this is really staging, fix /opt/aedwards/.env first."
        )


# ---------------------------------------------------------------------------
# Phase: reset
# ---------------------------------------------------------------------------


def phase_reset(db) -> None:
    """Wipe quote-family and customer tables, child rows first.

    Kept: users/auth, pricing tables, product types/catalog, shipping config,
    trust-ramp config. Staging's DB is disposable (deploy/README.md), and the
    demo dataset is rebuilt from scratch by seed-history + ingest.
    """
    from app.models import (
        AuditLog,
        AutoSendClaim,
        Contact,
        Customer,
        FailedIntake,
        ProcessedInboundEmail,
        Quote,
        QuoteAttachment,
        QuoteConfidence,
        QuoteLineItem,
        QuoteVersion,
        RejectedEmail,
        SendHold,
        ShipToAddress,
    )

    ordered = [
        AutoSendClaim,
        QuoteVersion,
        AuditLog,
        QuoteConfidence,
        QuoteLineItem,
        QuoteAttachment,
        Quote,
        ProcessedInboundEmail,
        RejectedEmail,
        FailedIntake,
        SendHold,
        Contact,
        ShipToAddress,
        Customer,
    ]
    for model in ordered:
        count = db.session.query(model).delete(synchronize_session=False)
        print(f"  reset: deleted {count:4d} {model.__tablename__}")
    db.session.commit()


# ---------------------------------------------------------------------------
# Phase: seed-history (ground-truth -> customers + backdated SENT quotes)
# ---------------------------------------------------------------------------

# 'reg half sole, 20" ID, 3/8" w/t, A572 GR50, 1' long.'  /  '20-3/4" ID'
_DESC_SLEEVE = re.compile(
    r"""(?P<kind>reg|ovsz)?\s*half\s*sole.*?
        (?P<dia>[\d][\d\-/. ]*?)"\s*ID.*?
        (?P<wall>[\d][\d/. ]*?)"\s*w/t.*?
        GR\s*(?P<grade>\d+).*?
        (?P<len>[\d.]+)'\s*long""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)
_GIRTH_PART = re.compile(r"^G-(?P<dia>[\d.]+)-(?P<wall>\d+)-(?P<grade>\d+)-(?P<len>[\d.]+)$")


def _mixed_number(text: str) -> float | None:
    """Parse '20', '20.75', '20-3/4', '8 5/8', '3/8' into a float."""
    text = text.strip().rstrip(".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)?[\s-]*(?:(\d+)\s*/\s*(\d+))?", text)
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    whole = float(m.group(1)) if m.group(1) else 0.0
    if m.group(2):
        whole += float(m.group(2)) / float(m.group(3))
    return whole


def _specs_from_ground_truth_line(part_number: str | None, description: str) -> tuple[str, dict]:
    """Best-effort (product_type, specs_json) from a ground-truth quote line.

    Spec strings are formatted EXACTLY like db_writer.write_quote_to_db does
    (str() of float/int), so price_in_tolerance's exact string match can pair
    these history lines with freshly ingested pipeline lines. Lines we cannot
    parse get no spec keys — spec-less lines never pollute the comparable
    median for a spec'd line (str(None) never equals a real spec string).
    """
    text = f"{part_number or ''} {description}".lower()
    m = _DESC_SLEEVE.search(description or "")
    if m:
        specs: dict = {}
        dia = _mixed_number(m.group("dia"))
        wall = _mixed_number(m.group("wall"))
        length = _mixed_number(m.group("len"))
        if dia is not None:
            specs["diameter"] = str(float(dia))
        if wall is not None:
            specs["wall_thickness"] = str(float(wall))
        specs["grade"] = str(int(m.group("grade")))
        if length is not None:
            specs["length_ft"] = str(float(length))
        specs["milling"] = "mill" in text
        specs["painting"] = "paint" in text
        return "sleeve", specs
    gm = _GIRTH_PART.match((part_number or "").strip())
    if gm or "girth weld" in text:
        specs = {}
        if gm:
            from allenedwards.pricing import WALL_THICKNESS_CODE_MAP

            wall_by_code = {code: value for value, code in WALL_THICKNESS_CODE_MAP.items()}
            specs["diameter"] = str(float(gm.group("dia")))
            wall = wall_by_code.get(gm.group("wall"))
            if wall is not None:
                specs["wall_thickness"] = str(float(wall))
            specs["grade"] = str(int(gm.group("grade")))
            specs["length_ft"] = str(float(gm.group("len")))
        return "girth_weld", specs
    if "backing strip" in text:
        return "accessory", {}
    if "bag" in text or "weight" in text:
        return "bag", {}
    if "wrap" in text or "omega" in text or "composite" in text:
        return "composite", {}
    return "accessory", {}


def _date_from_gt_filename(name: str) -> datetime | None:
    m = re.match(r"(\d{8})_", name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").replace(hour=15)
    except ValueError:
        return None


def phase_seed_history(db, history_dir: Path) -> None:
    """Create customers, confirmed ship-to addresses, and backdated SENT quotes
    from the complete ground-truth JSONs (REAL quotes Jamee sent, real prices)."""
    from app.models import (
        Contact,
        Customer,
        Quote,
        QuoteLineItem,
        QuoteStatus,
        ShipToAddress,
    )

    files = sorted(history_dir.glob("*.json"))
    seeded = skipped = 0
    customers_by_name: dict[str, Customer] = {}
    for row in db.session.query(Customer).all():
        customers_by_name[row.company_name.strip().lower()] = row

    for path in files:
        data = json.loads(path.read_text())
        meta = data.get("_meta") or {}
        if meta.get("extraction_status") != "complete":
            continue
        lines = data.get("line_items") or []
        if not lines or not data.get("customer_name"):
            skipped += 1
            continue
        source_id = f"{SEED_SOURCE_PREFIX}{path.name}"
        if db.session.query(Quote.id).filter_by(source_email_id=source_id).first():
            skipped += 1
            continue

        when = _date_from_gt_filename(path.name) or (datetime.utcnow() - timedelta(days=120))
        name = data["customer_name"].strip()
        customer = customers_by_name.get(name.lower())
        if customer is None:
            customer = Customer(company_name=name, discount_pct=0, created_at=when)
            db.session.add(customer)
            db.session.flush()
            customers_by_name[name.lower()] = customer
        else:
            customer.created_at = min(customer.created_at, when)
        if data.get("contact_email"):
            exists = (
                db.session.query(Contact.id)
                .filter(Contact.customer_id == customer.id, Contact.email == data["contact_email"])
                .first()
            )
            if not exists:
                db.session.add(
                    Contact(
                        customer_id=customer.id,
                        name=data.get("contact_name") or data["contact_email"],
                        email=data["contact_email"],
                        phone=data.get("contact_phone"),
                    )
                )
        ship = data.get("ship_to") or {}
        ship_json = None
        if ship.get("city") and ship.get("state"):
            db.session.add(
                ShipToAddress(
                    customer_id=customer.id,
                    address_line1=ship.get("street") or "",
                    city=ship["city"],
                    state=ship["state"],
                    postal_code=ship.get("postal_code") or "",
                    human_confirmed=True,
                )
            )
            ship_json = {
                "company": ship.get("company") or "",
                "attention": ship.get("attention") or "",
                "address_line1": ship.get("street") or "",
                "address_line2": "",
                "city": ship["city"],
                "state": ship["state"],
                "postal_code": ship.get("postal_code") or "",
                "country": "US",
            }

        quote_number = data.get("quote_number") or f"HIST-{path.name[:8]}-{seeded}"
        if db.session.query(Quote.id).filter_by(quote_number=quote_number).first():
            quote_number = f"{quote_number}-H{seeded}"
        quote = Quote(
            quote_number=quote_number,
            customer_id=customer.id,
            status=QuoteStatus.SENT,
            contact_name=data.get("contact_name"),
            contact_email=data.get("contact_email"),
            contact_phone=data.get("contact_phone"),
            po_number=data.get("po_number"),
            ship_to_json=ship_json,
            source_email_id=source_id,
            created_at=when,
            updated_at=when,
            tax_amount=data.get("tax_amount") or 0,
        )
        db.session.add(quote)
        db.session.flush()
        for order, line in enumerate(lines, start=1):
            unit_price = float(line.get("unit_price") or 0)
            total = float(line.get("total") or 0)
            product_type, specs = _specs_from_ground_truth_line(
                line.get("part_number"), line.get("description") or ""
            )
            db.session.add(
                QuoteLineItem(
                    quote_id=quote.id,
                    product_type=product_type,
                    description=line.get("description") or (line.get("part_number") or "item"),
                    quantity=float(line.get("quantity") or 1),
                    unit_price=unit_price,
                    line_total=total or unit_price * float(line.get("quantity") or 1),
                    specs_json=specs or None,
                    part_number=line.get("part_number"),
                    sort_order=order,
                )
            )
        seeded += 1
    db.session.commit()
    print(f"  seed-history: {seeded} SENT quotes seeded, {skipped} skipped, {len(customers_by_name)} customers")


# ---------------------------------------------------------------------------
# Phase: ingest (curated .eml -> real pipeline -> DB)
# ---------------------------------------------------------------------------


def _load_eml(path: Path):
    return BytesParser(policy=policy.default).parse(path.open("rb"))


def _strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _eml_body_text(msg) -> str:
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_subtype() == "plain":
            plain.append(str(content))
        elif part.get_content_subtype() == "html":
            html.append(_strip_html(str(content)))
    return ("\n\n".join(plain) or "\n\n".join(html)).strip()


def _sender_of(msg) -> tuple[str | None, str | None]:
    raw = str(msg.get("From", "") or "")
    m = re.match(r'\s*"?(?P<name>[^"<]*)"?\s*<(?P<email>[^>]+)>', raw)
    if m:
        name = m.group("name").strip() or None
        addr = m.group("email").strip() or None
        return name, addr
    return None, (raw.strip() or None)


def phase_ingest(db, app, manifest: dict, corpus_dir: Path, attachments_dir: Path) -> None:
    from allenedwards.cli import get_provider
    from allenedwards.db_writer import InboundEmailAlreadyProcessed, write_quote_to_db
    from allenedwards.email_provider import EmailMessage
    from allenedwards.monitor import _parse_message_to_rfqs
    from allenedwards.outlook import OutlookAttachment
    from allenedwards.parser import classify_rfq
    from allenedwards.pricing import generate_quote
    from app.models import ProcessedInboundEmail, RejectedEmail
    from app.quote_numbers import generate_quote_number

    provider = get_provider()
    print(f"  ingest: LLM provider = {type(provider).__name__}")
    results = []
    for case in manifest["cases"]:
        name = case["file"]
        source_id = f"{DEMO_SOURCE_PREFIX}{name}"
        if db.session.query(ProcessedInboundEmail.id).filter_by(source_email_id=source_id).first():
            results.append((name, case["category"], "skipped (already ingested)"))
            continue
        path = corpus_dir / name
        if not path.exists():
            results.append((name, case["category"], "MISSING FILE"))
            continue

        eml = _load_eml(path)
        body = _eml_body_text(eml)
        subject = str(eml.get("Subject", "") or "").strip()
        sender_name, sender_email = _sender_of(eml)
        received = None
        m = re.match(r"(\d{8})_(\d{6})_", name)
        if m:
            received = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").isoformat() + "Z"

        attachments: list[OutlookAttachment] = []
        for att_name in case.get("attachments", []):
            att_path = attachments_dir / att_name
            if not att_path.exists():
                print(f"    WARNING: sidecar attachment missing: {att_name}")
                continue
            ctype = mimetypes.guess_type(att_name)[0] or "application/octet-stream"
            attachments.append(
                OutlookAttachment(
                    filename=att_name.split("_")[-1],
                    content_bytes=att_path.read_bytes(),
                    content_type=ctype,
                )
            )

        msg = EmailMessage(
            id=source_id,
            subject=subject,
            sender_name=sender_name,
            sender_email=sender_email,
            body_preview=body[:255],
            body_content=body,
            body_content_type="text",
            internet_message_id=str(eml.get("Message-ID", "") or "") or None,
            received_datetime=received,
            has_attachments=bool(attachments),
        )

        try:
            received_dt = (
                datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else datetime.utcnow()
            )
            is_rfq, reason = classify_rfq(subject, body, provider)
            if not is_rfq:
                # RejectedEmail has no source_email_id column (matches the
                # monitor's write); the ProcessedInboundEmail claim carries the
                # re-run idempotency for rejected cases too.
                db.session.add(
                    RejectedEmail(
                        received_at=received_dt,
                        sender_name=sender_name,
                        sender_email=sender_email,
                        subject=subject,
                        classifier_reason=reason,
                    )
                )
                db.session.add(ProcessedInboundEmail(source_email_id=source_id))
                db.session.commit()
                results.append((name, case["category"], "rejected (non-RFQ)"))
                continue

            rfqs = _parse_message_to_rfqs(msg, body, provider, attachments)
            rfqs = [rfq for rfq in rfqs if rfq.items]
            if not rfqs:
                db.session.add(ProcessedInboundEmail(source_email_id=source_id))
                db.session.commit()
                results.append((name, case["category"], "parsed: no line items"))
                continue

            base_number = generate_quote_number()
            numbers = []
            for idx, rfq in enumerate(rfqs):
                number = base_number if len(rfqs) == 1 else f"{base_number}-{idx + 1:02d}"
                priced = generate_quote(rfq, number)
                write_quote_to_db(
                    msg,
                    rfq,
                    priced,
                    number,
                    attachments=attachments,
                    claim_source_email=idx == 0,
                    commit=False,
                )
                numbers.append(number)
            db.session.commit()
            results.append((name, case["category"], f"quoted: {', '.join(numbers)}"))
        except InboundEmailAlreadyProcessed:
            db.session.rollback()
            results.append((name, case["category"], "skipped (claim exists)"))
        except Exception as error:  # never let one case kill the batch
            db.session.rollback()
            results.append((name, case["category"], f"ERROR: {type(error).__name__}: {error}"))

    for name, category, outcome in results:
        print(f"  [{category:16s}] {outcome:40s} {name[:60]}")

    _apply_case_directives(db, manifest)


def _apply_case_directives(db, manifest: dict) -> None:
    """Post-ingest per-case tuning: confirm_ship_to marks the parsed ship-to as
    a stored human-confirmed address (a destination verified on an earlier order)."""
    from allenedwards.ship_to import normalize_ship_to
    from app.models import Quote, ShipToAddress

    for case in manifest["cases"]:
        if not case.get("confirm_ship_to"):
            continue
        source_id = f"{DEMO_SOURCE_PREFIX}{case['file']}"
        quotes = db.session.query(Quote).filter_by(source_email_id=source_id).all()
        for quote in quotes:
            incoming = normalize_ship_to(quote.ship_to_json)
            if incoming is None or quote.customer_id is None:
                continue
            matched = None
            for addr in db.session.query(ShipToAddress).filter_by(customer_id=quote.customer_id):
                stored = normalize_ship_to(
                    {
                        "address_line1": addr.address_line1,
                        "address_line2": addr.address_line2 or "",
                        "city": addr.city,
                        "state": addr.state,
                        "postal_code": addr.postal_code,
                        "country": addr.country,
                    }
                )
                if all(
                    incoming[f] == stored[f]
                    for f in ("address_line1", "address_line2", "city", "state", "postal_code", "country")
                ):
                    matched = addr
                    break
            if matched is None:
                matched = ShipToAddress(
                    customer_id=quote.customer_id,
                    address_line1=incoming["address_line1"],
                    address_line2=incoming["address_line2"] or None,
                    city=incoming["city"],
                    state=incoming["state"],
                    postal_code=incoming["postal_code"],
                    country=incoming["country"] or "US",
                )
                db.session.add(matched)
            matched.human_confirmed = True
            print(f"  directive: confirmed ship-to for {quote.quote_number} ({incoming['city']}, {incoming['state']})")
    db.session.commit()


# ---------------------------------------------------------------------------
# Phase: rescore + report
# ---------------------------------------------------------------------------


def phase_rescore(db) -> None:
    from app.confidence import sync_quote_confidence
    from app.models import Quote, QuoteStatus

    changed = total = 0
    for quote in db.session.query(Quote).filter(Quote.deleted_at.is_(None)):
        if quote.status == QuoteStatus.SENT:
            continue  # history rows are the baseline, not scored demo rows
        total += 1
        if sync_quote_confidence(quote):
            changed += 1
    db.session.commit()
    print(f"  rescore: {total} quotes scored, {changed} changed")


def phase_report(db, manifest: dict) -> None:
    from app.confidence import active_send_holds, active_trust_tier, quote_recommendation
    from app.models import Customer, Quote, QuoteStatus, RejectedEmail

    category_by_source = {
        f"{DEMO_SOURCE_PREFIX}{case['file']}": case["category"] for case in manifest["cases"]
    }
    holds = active_send_holds()
    tier = active_trust_tier()
    demo = (
        db.session.query(Quote)
        .filter(Quote.deleted_at.is_(None), Quote.source_email_id.like(f"{DEMO_SOURCE_PREFIX}%"))
        .order_by(Quote.id)
        .all()
    )
    history = (
        db.session.query(Quote)
        .filter(Quote.source_email_id.like(f"{SEED_SOURCE_PREFIX}%"))
        .count()
    )
    rejected = db.session.query(RejectedEmail).count()
    customers = db.session.query(Customer).count()

    print(f"\n=== STAGING DEMO DATASET (tier {tier}) ===")
    print(f"history: {history} seeded SENT quotes | customers: {customers} | rejected emails: {rejected}")
    print(f"demo quotes from corpus: {len(demo)}\n")
    by_category: Counter = Counter()
    by_status: Counter = Counter()
    state_mix: Counter = Counter()
    held_reasons: defaultdict = defaultdict(list)
    for quote in demo:
        category = category_by_source.get(quote.source_email_id, "?")
        by_category[category] += 1
        by_status[quote.status.value] += 1
        rec = quote_recommendation(quote, holds=holds, tier=tier)
        conf = quote.confidence
        signals = (
            " ".join(
                f"{name}={getattr(conf, name)[0]}"
                for name in (
                    "decode_clean",
                    "all_lines_priced",
                    "customer_known",
                    "ship_to_confirmed",
                    "price_in_tolerance",
                    "recipient_allowlisted",
                )
            )
            if conf
            else "unscored"
        )
        label = "RECOMMENDED" if rec["recommended"] else "held"
        state_mix[label] += 1
        if not rec["recommended"]:
            for reason in rec["reasons"]:
                held_reasons[reason.split(" — ")[0][:70]].append(quote.quote_number)
        score = f"{rec['score']:.2f}" if rec["score"] is not None else " n/a"
        print(f"  {quote.quote_number:12s} {quote.status.value:14s} score={score} {label:11s} [{category}] {signals}")
    print("\nper category:", dict(by_category))
    print("per status:  ", dict(by_status))
    print("recommendation mix:", dict(state_mix))
    print("\nheld-back reasons:")
    for reason, numbers in sorted(held_reasons.items()):
        print(f"  {len(numbers):2d}x {reason}  ({', '.join(numbers[:6])}{'...' if len(numbers) > 6 else ''})")


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phases", nargs="+", choices=["reset", "seed-history", "ingest", "rescore", "report", "all"])
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "tools" / "demo_corpus_manifest.json")
    parser.add_argument("--corpus-dir", type=Path, default=Path("/opt/aedwards/demo-corpus/emails"))
    parser.add_argument("--attachments-dir", type=Path, default=Path("/opt/aedwards/demo-corpus/attachments"))
    parser.add_argument("--history-dir", type=Path, default=Path("/opt/aedwards/demo-corpus/ground-truth"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--i-am-staging", action="store_true", help="Required on the staging box; asserts you know reset wipes its DB.")
    parser.add_argument("--dev-sandbox", action="store_true", help="Local plumbing test against a sqlite DATABASE_URL only.")
    args = parser.parse_args()

    _load_env_file(args.env_file)
    assert_safe_environment(args)

    phases = args.phases
    if "all" in phases:
        phases = ["reset", "seed-history", "ingest", "rescore", "report"]

    manifest = json.loads(args.manifest.read_text())

    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        for phase in phases:
            print(f"== phase: {phase} ==")
            if phase == "reset":
                phase_reset(db)
            elif phase == "seed-history":
                phase_seed_history(db, args.history_dir)
            elif phase == "ingest":
                phase_ingest(db, app, manifest, args.corpus_dir, args.attachments_dir)
            elif phase == "rescore":
                phase_rescore(db)
            elif phase == "report":
                phase_report(db, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
