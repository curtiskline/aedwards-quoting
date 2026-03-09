#!/usr/bin/env python3
"""Parse PST text dumps into individual .eml files.

Reads the text dump format produced by the PST extraction tool,
splits on ====...==== delimiters, extracts metadata from the header
block, and writes each email as a proper RFC822 .eml file.
"""

import argparse
import json
import os
import re
import sys
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime, format_datetime
from pathlib import Path

SEPARATOR = "=" * 80
HEADER_BODY_SEP = "-" * 80

# Data lives in the main repo, not the worktree
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "curtis-emails"


def parse_dump_file(filepath: Path) -> list[dict]:
    """Parse a PST text dump file into a list of email records."""
    text = filepath.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")

    emails = []
    i = 0
    n = len(lines)

    while i < n:
        # Find next email block: starts with separator followed by SOURCE PST
        if lines[i].strip() == SEPARATOR:
            # Check if next line starts a header block
            if i + 1 < n and lines[i + 1].startswith("SOURCE PST"):
                record = _parse_email_block(lines, i + 1, n)
                if record:
                    emails.append(record)
                    i = record["_end_line"]
                    continue
        i += 1

    return emails


def _parse_email_block(lines: list[str], start: int, n: int) -> dict | None:
    """Parse a single email block starting after the ==== separator.

    Returns dict with keys: source_pst, folder, date, from_, subject, body, _end_line
    """
    headers = {}
    i = start

    # Parse header lines until we hit the ---- separator
    while i < n:
        line = lines[i]
        if line.strip() == HEADER_BODY_SEP:
            i += 1  # skip past the separator
            break

        # Parse "KEY     : value" format
        match = re.match(r"^(\w[\w\s]*?)\s*:\s*(.*)$", line)
        if match:
            key = match.group(1).strip().upper()
            value = match.group(2).strip()
            headers[key] = value
        i += 1
    else:
        # Never found the ---- separator
        return None

    # Collect body lines until next ==== separator
    body_lines = []
    while i < n:
        if lines[i].strip() == SEPARATOR:
            # Check if this starts a new email block
            if i + 1 < n and lines[i + 1].startswith("SOURCE PST"):
                break
            # Could also be the very end separator
            if i + 1 >= n or lines[i + 1].strip() == "":
                # Check a couple more lines
                found_header = False
                for j in range(i + 1, min(i + 3, n)):
                    if lines[j].startswith("SOURCE PST"):
                        found_header = True
                        break
                if found_header:
                    break
        body_lines.append(lines[i])
        i += 1

    # Strip trailing blank lines from body
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    source_pst = headers.get("SOURCE PST", "")
    folder = headers.get("FOLDER", "")
    date_str = headers.get("DATE", "")
    from_str = headers.get("FROM", "")
    subject = headers.get("SUBJECT", "")

    if not date_str and not subject:
        return None

    return {
        "source_pst": source_pst,
        "folder": folder,
        "date": date_str,
        "from": from_str,
        "subject": subject,
        "body": "\n".join(body_lines),
        "_end_line": i,
    }


def make_eml(record: dict) -> str:
    """Construct an RFC822 .eml string from a parsed email record."""
    msg = MIMEText(record["body"], "plain", "utf-8")
    msg["Subject"] = record["subject"]
    msg["From"] = record["from"]
    msg["Date"] = record["date"]

    # Add custom headers for metadata
    msg["X-Source-PST"] = record["source_pst"]
    msg["X-Folder"] = record["folder"]

    return msg.as_string()


def sanitize_filename(subject: str, max_len: int = 80) -> str:
    """Create a safe filename from an email subject."""
    # Remove/replace unsafe characters
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", subject)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len]
    return s or "no-subject"


def main():
    parser = argparse.ArgumentParser(description="Split PST text dumps into .eml files")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing PST text dump files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for .eml files (default: data/test-corpus/emails/)",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = data_dir.parent / "test-corpus" / "emails"

    output_dir.mkdir(parents=True, exist_ok=True)

    dump_files = [
        ("quoting_emails.txt", "backup"),
        ("quoting_emails_jhamilton.txt", "jhamilton"),
    ]

    all_records = []
    stats = {"by_file": {}, "total_emails": 0, "by_folder": {}, "by_source": {}}

    for filename, label in dump_files:
        filepath = data_dir / filename
        if not filepath.exists():
            print(f"WARNING: {filepath} not found, skipping")
            continue

        print(f"Parsing {filepath.name}...")
        records = parse_dump_file(filepath)
        print(f"  Found {len(records)} email records")

        stats["by_file"][filename] = len(records)

        for rec in records:
            rec["_source_file"] = filename
            rec["_label"] = label
            all_records.append(rec)

    print(f"\nTotal records: {len(all_records)}")
    stats["total_emails"] = len(all_records)

    # Write .eml files - use index + date + subject for unique naming
    written = 0
    index_map = []  # For manifest: maps index to filename + metadata

    for idx, record in enumerate(all_records):
        # Parse date for filename prefix
        date_prefix = ""
        try:
            dt = parsedate_to_datetime(record["date"])
            date_prefix = dt.strftime("%Y%m%d_%H%M%S")
        except Exception:
            date_prefix = f"nodate_{idx:05d}"

        subject_part = sanitize_filename(record["subject"], max_len=60)
        label = record["_label"]
        eml_filename = f"{date_prefix}_{label}_{subject_part}.eml"

        # Handle duplicate filenames
        eml_path = output_dir / eml_filename
        counter = 1
        while eml_path.exists():
            eml_filename = f"{date_prefix}_{label}_{subject_part}_{counter}.eml"
            eml_path = output_dir / eml_filename
            counter += 1

        eml_content = make_eml(record)
        eml_path.write_text(eml_content, encoding="utf-8")
        written += 1

        # Track folder/source stats
        folder = record["folder"]
        source = record["source_pst"]
        stats["by_folder"][folder] = stats["by_folder"].get(folder, 0) + 1
        stats["by_source"][source] = stats["by_source"].get(source, 0) + 1

        index_map.append({
            "index": idx,
            "filename": eml_filename,
            "source_pst": record["source_pst"],
            "folder": record["folder"],
            "date": record["date"],
            "from": record["from"],
            "subject": record["subject"],
            "source_file": record["_source_file"],
        })

    print(f"Wrote {written} .eml files to {output_dir}")

    # Write email index for use by match_attachments.py
    index_path = output_dir.parent / "email_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w") as f:
        json.dump(index_map, f, indent=2)
    print(f"Wrote email index to {index_path}")

    # Write stats
    stats_path = output_dir.parent / "stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Wrote stats to {stats_path}")
    print(f"\nStats: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    main()
