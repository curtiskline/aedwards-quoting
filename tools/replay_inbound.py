#!/usr/bin/env python3
"""Re-run a specific inbound email through the intake pipeline.

For emails the responder mis-handled (rejected as non-RFQ, or skipped with no
line items) and that now carry a ProcessedInboundEmail claim: this clears the
claim and the state-file entry for the given Graph message id, fetches the
message directly (any folder — the monitor may have moved it to Processed),
and processes it exactly like the polling loop would.

Usage (run on the host with the monitor's .env loaded):
    python tools/replay_inbound.py --dry-run <message-id>   # classify+parse, print, no writes
    python tools/replay_inbound.py <message-id> [...]       # full pipeline, DB writes per env

Dry-run performs no side effects at all: no DB writes, no drafts, no folder
moves, no state changes. The full run honors ENABLE_DB_WRITES /
ENABLE_OUTLOOK_DRAFTS from the environment just like the monitor service.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _build_outlook_client():
    from allenedwards.outlook import OutlookClient

    email_addr = os.environ.get("O365_EMAIL")
    if not email_addr:
        raise SystemExit("Missing O365_EMAIL — run with the monitor's environment loaded.")
    scopes = os.environ.get("O365_SCOPES")
    return OutlookClient(
        email_address=email_addr,
        password=os.environ.get("O365_PASSWORD"),
        client_id=os.environ.get("O365_CLIENT_ID"),
        scopes=[s.strip() for s in scopes.split(",")] if scopes else None,
        client_secret=os.environ.get("O365_CLIENT_SECRET"),
        tenant_id=os.environ.get("O365_TENANT_ID"),
    )


def _dry_run(client, provider, message_id: str) -> None:
    from allenedwards.monitor import _normalize_body, _parse_message_to_rfqs
    from allenedwards.parser import classify_rfq

    msg = client.fetch_message(message_id)
    body_text = _normalize_body(msg.body_content, msg.body_preview, msg.body_content_type)
    print(f"Subject: {msg.subject}")
    print(f"From:    {msg.sender_name} <{msg.sender_email}>")
    print(f"Body text ({len(body_text)} chars, first 800):\n{body_text[:800]}\n{'-' * 60}")

    is_rfq, reason = classify_rfq(msg.subject, body_text, provider)
    print(f"classify_rfq -> is_rfq={is_rfq}" + (f" reason={reason}" if reason else ""))
    if not is_rfq:
        return

    attachments = []
    if msg.has_attachments:
        attachments = client.get_attachments(message_id)
        print(f"Fetched {len(attachments)} attachment(s)")
    rfqs = _parse_message_to_rfqs(msg, body_text, provider, attachments)
    for i, rfq in enumerate(rfqs):
        print(
            f"RFQ[{i}]: customer={rfq.customer_name!r} contact={rfq.contact_email!r} "
            f"items={len(rfq.items)} confidence={rfq.confidence}"
        )
        for item in rfq.items:
            print(f"    - {item.product_type} qty={item.quantity} {item.description!r}")


def _clear_claim_and_state(message_id: str, state_path: Path) -> None:
    """Delete the idempotency claim and state-file entry so replay can write."""
    import json

    from app import create_app
    from app.extensions import db
    from app.models import ProcessedInboundEmail

    app = create_app()
    with app.app_context():
        deleted = (
            db.session.query(ProcessedInboundEmail)
            .filter_by(source_email_id=message_id)
            .delete()
        )
        db.session.commit()
        print(f"Cleared {deleted} ProcessedInboundEmail claim(s) for {message_id}")

    if state_path.exists():
        data = json.loads(state_path.read_text())
        ids = data.get("processed_ids") or []
        if message_id in ids:
            ids.remove(message_id)
            data["processed_ids"] = ids
            state_path.write_text(json.dumps(data, indent=2))
            print(f"Removed {message_id} from state file {state_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message_ids", nargs="+", help="Graph message id(s) to replay")
    parser.add_argument("--dry-run", action="store_true", help="classify+parse only; no side effects")
    parser.add_argument(
        "--state-file",
        default=os.environ.get("MONITOR_STATE_FILE", ".monitor_state.json"),
        help="monitor state file path (prod: /opt/aedwards/.monitor_state.json)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("MONITOR_OUTPUT_DIR", "monitor_output"),
        help="directory for generated quote PDFs (prod: /opt/aedwards/monitor_output)",
    )
    args = parser.parse_args()

    from allenedwards.cli import get_provider, load_environment

    load_environment()
    client = _build_outlook_client()
    provider = get_provider()

    if args.dry_run:
        for message_id in args.message_ids:
            _dry_run(client, provider, message_id)
        return

    from allenedwards.monitor import InboxMonitor

    enable_db = os.environ.get("ENABLE_DB_WRITES", "").lower() in ("1", "true", "yes")
    enable_drafts = os.environ.get("ENABLE_OUTLOOK_DRAFTS", "true").lower() not in ("0", "false", "no")
    flask_app = None
    if enable_db:
        from app import create_app

        flask_app = create_app()

    state_path = Path(args.state_file)
    service = InboxMonitor(
        email_client=client,
        provider=provider,
        poll_interval_seconds=300,
        state_path=state_path,
        output_dir=Path(args.output_dir),
        quote_email_cc=os.environ.get("QUOTE_EMAIL_CC"),
        processed_folder_name=os.environ.get("PROCESSED_FOLDER_NAME") or None,
        enable_db_writes=enable_db,
        enable_outlook_drafts=enable_drafts,
        enable_failure_ack=False,
        mailbox_address=os.environ.get("O365_EMAIL"),
        flask_app=flask_app,
    )

    for message_id in args.message_ids:
        if enable_db:
            _clear_claim_and_state(message_id, state_path)
        msg = client.fetch_message(message_id)
        print(f"Replaying {message_id}: {msg.subject!r} from {msg.sender_email}")
        handled = service._process_message(msg)
        service.state.add(message_id)
        print(f"  handled={handled}")


if __name__ == "__main__":
    main()
