#!/usr/bin/env python3
"""Send a curated batch of historical RFQs through Gmail for live pipeline testing."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path


SHARED_PROJECT_ROOT = Path(__file__).resolve().parents[3]
EMAILS_DIR = SHARED_PROJECT_ROOT / "data" / "test-corpus" / "emails"
DEFAULT_TO = "AEResponder@allenedwards.com"
TEST_SUBJECT_PREFIX = "[TEST] "


@dataclass(frozen=True)
class SampleEmail:
    filename: str
    category: str


SAMPLE_EMAILS = [
    SampleEmail("20250206_150218_jhamilton_RFQ.eml", "sleeve"),
    SampleEmail("20250312_205138_jhamilton_FW_ Quote on NPS16 Allen Edwards Sleeves.eml", "forwarded_sleeve"),
    SampleEmail("20250627_130247_jhamilton_18” girth weld sleeves 3_8 g50.eml", "girth_weld"),
    SampleEmail("20250630_123531_jhamilton_FW_ [EXTERNAL] 18” girth weld sleeves 3_8 g50.eml", "forwarded_external"),
    SampleEmail("20250411_181926_jhamilton_Fw_ LEG TC 30_ Interconnect bag weights.eml", "pipe_weights"),
    SampleEmail("20250212_132738_jhamilton_Repair wrap.eml", "repair_wrap"),
    SampleEmail("20250210_154021_jhamilton_FW_ Omega wrap training and installation support for Enbridg.eml", "omegawrap"),
    SampleEmail("20250310_185743_jhamilton_FW_ 8” welding ring mtrs and quote needed.eml", "welding_ring"),
    SampleEmail("20251114_085108_jhamilton_Request for mtrs on weld wraps for Etp.eml", "weld_wraps"),
    SampleEmail("20260131_071352_backup_RE_ ⚠ External - Re_ B-sleeve repairs quote for NPS.eml", "b_sleeve"),
    SampleEmail("20260203_221845_backup_Fw_ Enbridge Gas Inc - NPS 30 AE Sleeves Quote Request - NPS.eml", "nps30_sleeve"),
]


def _strip_html(text: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_part(part) -> str:
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        return _strip_html(content)
    return str(content)


def load_email(filename: str) -> tuple[str, str]:
    path = EMAILS_DIR / filename
    with path.open("rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)

    body_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() not in {"text/plain", "text/html"}:
                continue
            disposition = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in disposition:
                continue
            text = _decode_part(part).strip()
            if text:
                body_parts.append(text)
    else:
        text = _decode_part(message).strip()
        if text:
            body_parts.append(text)

    subject = str(message.get("Subject", "") or "").strip()
    body = "\n\n".join(body_parts).strip()
    if not subject:
        raise ValueError(f"{filename} is missing a Subject header")
    if not body:
        raise ValueError(f"{filename} does not contain a usable message body")
    return subject, body


def build_send_command(*, to: str, subject: str, account: str | None, client: str | None) -> list[str]:
    command = [
        "gog",
        "gmail",
        "send",
        "--to",
        to,
        "--subject",
        subject,
        "--body-file=-",
        "--no-input",
    ]
    if account:
        command.extend(["--account", account])
    if client:
        command.extend(["--client", client])
    return command


def print_results(results: list[dict[str, str]]) -> None:
    headers = ("idx", "category", "subject", "timestamp", "status")
    rows = [
        (
            str(result["idx"]),
            result["category"],
            result["subject"],
            result["timestamp"],
            result["status"],
        )
        for result in results
    ]
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))

    def format_row(row: tuple[str, str, str, str, str]) -> str:
        return " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(row))

    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(format_row(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the sends without calling gog gmail send")
    parser.add_argument("--to", default=DEFAULT_TO, help=f"Destination address (default: {DEFAULT_TO})")
    parser.add_argument("--limit", type=int, default=len(SAMPLE_EMAILS), help="Number of curated samples to send")
    parser.add_argument("--account", default=None, help="Optional gog gmail account override")
    parser.add_argument("--client", default=None, help="Optional gog gmail OAuth client override")
    args = parser.parse_args()

    if not EMAILS_DIR.exists():
        print(f"Historical corpus directory not found: {EMAILS_DIR}", file=sys.stderr)
        return 1

    selected = SAMPLE_EMAILS[: max(args.limit, 0)]
    results: list[dict[str, str]] = []

    for idx, sample in enumerate(selected, start=1):
        subject, body = load_email(sample.filename)
        send_subject = subject if subject.startswith(TEST_SUBJECT_PREFIX) else f"{TEST_SUBJECT_PREFIX}{subject}"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if args.dry_run:
            status = "dry-run"
        else:
            command = build_send_command(
                to=args.to,
                subject=send_subject,
                account=args.account,
                client=args.client,
            )
            completed = subprocess.run(
                command,
                input=body,
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown gog gmail send failure"
                raise RuntimeError(f"Failed sending {sample.filename}: {stderr}")
            status = "sent"

        results.append(
            {
                "idx": str(idx),
                "category": sample.category,
                "subject": send_subject,
                "timestamp": timestamp,
                "status": status,
            }
        )

    print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
