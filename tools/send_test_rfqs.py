#!/usr/bin/env python3
"""Send a curated batch of historical RFQs through Gmail for live pipeline testing."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path


SHARED_PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT_ENV_VAR = "AE_TEST_CORPUS_ROOT"
DEFAULT_TO = "AEResponder@allanedwards.com"


def candidate_corpus_roots(explicit: str | None) -> list[tuple[Path, str]]:
    """List corpus-root candidates in resolution order, each with its source.

    Deploy worktrees intentionally carry no data/test-corpus (task 509) — or
    carry a hollow one with a stray file or two — so the repo-relative path is
    only trustworthy from the canonical checkout, and a candidate is only used
    when it actually contains the curated sample files. An explicit choice
    (--corpus-root or AE_TEST_CORPUS_ROOT) is never silently overridden: it is
    the sole candidate, and missing files there are an error. Implicit order:
    <this repo>/data/test-corpus, then the canonical checkout's
    data/test-corpus located from a linked worktree via the shared git common
    dir.
    """
    if explicit:
        return [(Path(explicit).expanduser(), "--corpus-root")]
    env_value = os.environ.get(CORPUS_ROOT_ENV_VAR)
    if env_value:
        return [(Path(env_value).expanduser(), f"${CORPUS_ROOT_ENV_VAR}")]
    local = SHARED_PROJECT_ROOT / "data" / "test-corpus"
    candidates = [(local, "repo-local")]
    completed = subprocess.run(
        ["git", "-C", str(SHARED_PROJECT_ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        canonical_root = Path(completed.stdout.strip()).parent
        candidate = canonical_root / "data" / "test-corpus"
        if candidate != local:
            candidates.append((candidate, "canonical checkout (via git common dir)"))
    return candidates


def select_corpus_root(explicit: str | None, filenames: list[str]) -> tuple[Path, str] | None:
    """Pick the first candidate whose emails/ holds every requested sample."""
    candidates = candidate_corpus_roots(explicit)
    for root, source in candidates:
        emails_dir = root / "emails"
        missing = [name for name in filenames if not (emails_dir / name).is_file()]
        if not missing:
            return root, source
        print(
            f"Corpus candidate {root} (via {source}) is missing "
            f"{len(missing)}/{len(filenames)} sample emails (e.g. {missing[0]}).",
            file=sys.stderr,
        )
    return None
TEST_SUBJECT_PREFIX = "[TEST] "


@dataclass(frozen=True)
class SampleEmail:
    filename: str
    category: str


SAMPLE_EMAILS = [
    SampleEmail("20250206_150218_jhamilton_RFQ.eml", "sleeve"),
    SampleEmail("20250312_205138_jhamilton_FW_ Quote on NPS16 Allen Edwards Sleeves.eml", "forwarded_sleeve"),
    SampleEmail("20250211_150802_jhamilton_CNP IM - fabricated weld sleeve.eml", "girth_weld"),
    SampleEmail("20250206_171030_jhamilton_RE_ [EXTERNAL] RE_ Allan Edwards 36_ 3_4_ Gr 65 Sleeves.eml", "forwarded_external"),
    SampleEmail("20250411_181926_jhamilton_Fw_ LEG TC 30_ Interconnect bag weights.eml", "pipe_weights"),
    SampleEmail("20250212_132738_jhamilton_Repair wrap.eml", "repair_wrap"),
    SampleEmail("20250210_154021_jhamilton_FW_ Omega wrap training and installation support for Enbridg.eml", "omegawrap"),
    SampleEmail("20251209_180129_jhamilton_RE_ Need girth weld sleeves quote QUO-125-790.eml", "welding_ring"),
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


def load_email(emails_dir: Path, filename: str) -> tuple[str, str]:
    path = emails_dir / filename
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
    parser.add_argument(
        "--corpus-root",
        default=None,
        help=(
            f"Test-corpus root containing emails/ (default: ${CORPUS_ROOT_ENV_VAR}, "
            "then <repo>/data/test-corpus, then the canonical checkout's "
            "data/test-corpus when run from a worktree that has none)"
        ),
    )
    args = parser.parse_args()

    selected = SAMPLE_EMAILS[: max(args.limit, 0)]

    resolved = select_corpus_root(args.corpus_root, [sample.filename for sample in selected])
    if resolved is None:
        print("No corpus candidate contains all requested sample emails.", file=sys.stderr)
        print(f"Pass --corpus-root <dir> or set {CORPUS_ROOT_ENV_VAR}.", file=sys.stderr)
        return 1
    corpus_root, corpus_source = resolved
    emails_dir = corpus_root / "emails"
    print(f"Corpus root: {corpus_root} (via {corpus_source})", file=sys.stderr)

    results: list[dict[str, str]] = []

    for idx, sample in enumerate(selected, start=1):
        subject, body = load_email(emails_dir, sample.filename)
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
