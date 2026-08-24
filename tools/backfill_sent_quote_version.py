#!/usr/bin/env python3
"""Backfill a QuoteVersion send record onto a seeded SENT history quote (task 422).

The staging demo dataset (task 408) seeds 79 backdated SENT quotes as price
history, but the seeder writes no QuoteVersion rows — versions are minted only
inside the real send flow, which staging correctly refuses to run
(EMAIL_DELIVERY_ENABLED=false). CP-3 acceptance binds to a QuoteVersion, so
without one none of the seeded history can be walked through
accept -> order -> fulfillment -> inventory for the demo.

This tool creates the one missing artifact for a named SENT quote:
a version_number=1 QuoteVersion with the line_items_snapshot built exactly the
way send_service builds it at send time, artifact_status="missing" — the
model's explicit, honest marker for "sent before PDF archiving existed", which
is precisely what backdated history is. Nothing is sent, no PDF is fabricated,
no existing row is modified beyond attaching the version to its quote.

Refuses non-SENT quotes, quotes that already have versions, and (mirroring
seed_staging_demo.py) refuses to run anywhere but the staging box or a local
sqlite sandbox. There is deliberately no path that reaches prod (D67).

Usage (on the staging box):
  sudo -u aedwards /opt/aedwards/venv/bin/python backfill_sent_quote_version.py \
      H126-032-01-1 --i-am-staging
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

DEFAULT_ENV_FILE = Path("/opt/aedwards/.env")
STAGING_HOSTNAME = "aedwards-staging"
_FALSY = {"0", "false", "no"}


def _load_env_file(path: Path) -> None:
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
    db_url = os.environ.get("DATABASE_URL", "")
    if args.dev_sandbox:
        if db_url and not db_url.startswith("sqlite:"):
            sys.exit(
                "REFUSING: --dev-sandbox only runs against a sqlite:// DATABASE_URL."
            )
        return
    if not args.i_am_staging:
        sys.exit(
            "REFUSING: this tool writes send records. Pass --i-am-staging on the "
            "staging box (or --dev-sandbox with a sqlite DB for local testing)."
        )
    hostname = socket.gethostname()
    if hostname != STAGING_HOSTNAME:
        sys.exit(
            f"REFUSING: hostname is {hostname!r}, expected {STAGING_HOSTNAME!r}."
        )
    delivery = os.environ.get("EMAIL_DELIVERY_ENABLED", "").strip().lower()
    if delivery not in _FALSY:
        sys.exit(
            f"REFUSING: EMAIL_DELIVERY_ENABLED={delivery!r} — staging must have "
            "outbound mail disabled."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quote_number", help="quote_number of a SENT quote")
    parser.add_argument("--i-am-staging", action="store_true")
    parser.add_argument("--dev-sandbox", action="store_true")
    parser.add_argument(
        "--env-file", type=Path, default=DEFAULT_ENV_FILE, help="env file to load"
    )
    args = parser.parse_args()

    _load_env_file(args.env_file)
    assert_safe_environment(args)

    from app import create_app
    from app.extensions import db
    from app.models import Quote, QuoteStatus, QuoteVersion
    from app.send_service import quote_line_items_snapshot

    app = create_app()
    with app.app_context():
        quote = (
            db.session.query(Quote)
            .filter(Quote.quote_number == args.quote_number)
            .one_or_none()
        )
        if quote is None:
            sys.exit(f"REFUSING: no quote with quote_number {args.quote_number!r}.")
        if quote.deleted_at is not None:
            sys.exit(f"REFUSING: quote {args.quote_number} is deleted.")
        if quote.status != QuoteStatus.SENT:
            sys.exit(
                f"REFUSING: quote {args.quote_number} is {quote.status.value}, "
                "only SENT history may be backfilled."
            )
        if quote.versions:
            sys.exit(
                f"REFUSING: quote {args.quote_number} already has "
                f"{len(quote.versions)} version(s) — nothing to backfill."
            )
        snapshot = quote_line_items_snapshot(quote)
        if not snapshot:
            sys.exit(f"REFUSING: quote {args.quote_number} has no line items.")
        version = QuoteVersion(
            quote_id=quote.id,
            version_number=1,
            # No send-time PDF exists for backdated history; "missing" is the
            # model's explicit marker for exactly this case. The path records
            # where the record came from, not a real file.
            pdf_path=f"backfill:task-422:{quote.quote_number}",
            artifact_status="missing",
            line_items_snapshot=snapshot,
            sent_at=quote.updated_at or quote.created_at,
            sent_by=None,
            sent_to=quote.contact_email,
        )
        db.session.add(version)
        db.session.commit()
        print(
            f"backfilled: quote {quote.quote_number} (id {quote.id}) -> "
            f"QuoteVersion id {version.id}, v{version.version_number}, "
            f"{len(snapshot)} line(s), sent_at {version.sent_at}, "
            f"artifact_status={version.artifact_status}"
        )


if __name__ == "__main__":
    main()
