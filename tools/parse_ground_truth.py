#!/usr/bin/env python3
"""Scaffold for parsing ground-truth quote PDFs into structured JSON.

This script intentionally DOES NOT implement OCR or traditional PDF parsing.
It provides a runnable pipeline scaffold that:

1. Traverses manifest matches from Task 28.
2. Writes one placeholder JSON record per PDF in ground-truth.
3. Emits aggregate stats for progress tracking.

Future work: replace `extract_pdf_structured_placeholder()` with AI vision-based
extraction (for example Claude image/PDF input).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "curtis-emails"


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load manifest.json from Task 28."""
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Manifest not found at {manifest_path}. Run tools/match_attachments.py first."
        )

    with manifest_path.open() as f:
        data = json.load(f)

    if "matches" not in data or not isinstance(data["matches"], list):
        raise ValueError(f"Unexpected manifest format in {manifest_path}: missing list 'matches'.")

    return data


def output_json_path(ground_truth_dir: Path, filename: str) -> Path:
    """Return output path with same basename and .json extension."""
    src = Path(filename)
    return ground_truth_dir / f"{src.stem}.json"


def extract_pdf_structured_placeholder(pdf_path: Path) -> dict[str, Any]:
    """Placeholder for future AI vision extraction.

    Replace this function with an AI-based extractor that reads the PDF and
    returns structured fields.
    """
    now = datetime.now().isoformat()

    return {
        "customer_name": None,
        "contact_name": None,
        "contact_email": None,
        "contact_phone": None,
        "ship_to": {
            "company": None,
            "city": None,
            "state": None,
            "postal_code": None,
        },
        "po_number": None,
        "quote_number": None,
        "line_items": [],
        "subtotal": None,
        "total": None,
        "_meta": {
            "source_pdf": str(pdf_path),
            "extraction_status": "placeholder",
            "extraction_method": "ai_vision_tbd",
            "parsed_at": now,
            "notes": "AI vision extraction not implemented yet in this scaffold.",
        },
    }


def compute_field_rates(records: list[dict[str, Any]]) -> dict[str, float]:
    """Compute non-empty field rates across structured records."""
    if not records:
        return {}

    def present(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return len(value) > 0
        return True

    n = len(records)
    rates: dict[str, float] = {}

    direct_fields = [
        "customer_name",
        "contact_name",
        "contact_email",
        "contact_phone",
        "po_number",
        "quote_number",
        "line_items",
        "subtotal",
        "total",
    ]

    for field in direct_fields:
        count = sum(1 for r in records if present(r.get(field)))
        rates[field] = round((count / n) * 100, 2)

    ship_fields = ["company", "city", "state", "postal_code"]
    for field in ship_fields:
        count = sum(
            1
            for r in records
            if isinstance(r.get("ship_to"), dict) and present(r["ship_to"].get(field))
        )
        rates[f"ship_to.{field}"] = round((count / n) * 100, 2)

    return rates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold: parse matched ground-truth quote PDFs into placeholder JSON"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing PST data (default: data/curtis-emails)",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help="Test corpus directory (default: data/test-corpus)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Override manifest path (default: <corpus-dir>/manifest.json)",
    )
    parser.add_argument(
        "--ground-truth-dir",
        type=Path,
        default=None,
        help="Override ground-truth directory (default: <corpus-dir>/ground-truth)",
    )
    parser.add_argument(
        "--stats-path",
        type=Path,
        default=None,
        help="Override stats output path (default: <corpus-dir>/ground-truth-stats.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N manifest matches",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing JSON outputs",
    )
    args = parser.parse_args()

    corpus_dir = args.corpus_dir or args.data_dir.parent / "test-corpus"
    manifest_path = args.manifest or corpus_dir / "manifest.json"
    ground_truth_dir = args.ground_truth_dir or corpus_dir / "ground-truth"
    stats_path = args.stats_path or corpus_dir / "ground-truth-stats.json"

    try:
        manifest = load_manifest(manifest_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    matches = manifest["matches"]
    if args.limit is not None:
        matches = matches[: args.limit]

    if not ground_truth_dir.exists():
        print(f"ERROR: Ground-truth directory does not exist: {ground_truth_dir}")
        return 1

    structured_records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped_non_pdf: list[str] = []

    total_seen = 0
    pdf_seen = 0
    json_written = 0
    json_skipped_existing = 0

    for match in matches:
        total_seen += 1
        filename = match.get("attachment")
        if not isinstance(filename, str):
            failures.append({"match": match, "reason": "missing attachment filename"})
            continue

        src_path = ground_truth_dir / filename
        if not src_path.exists():
            fallback = args.data_dir / "attachments-jamee-sent" / filename
            if fallback.exists():
                src_path = fallback
            else:
                failures.append({"file": filename, "reason": "source file not found"})
                continue

        if src_path.suffix.lower() != ".pdf":
            skipped_non_pdf.append(filename)
            continue

        pdf_seen += 1
        out_path = output_json_path(ground_truth_dir, filename)

        if out_path.exists() and not args.overwrite:
            json_skipped_existing += 1
            continue

        try:
            record = extract_pdf_structured_placeholder(src_path)
            with out_path.open("w") as f:
                json.dump(record, f, indent=2)
            structured_records.append(record)
            json_written += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"file": filename, "reason": str(exc)})

    rates = compute_field_rates(structured_records)

    stats = {
        "generated": datetime.now().isoformat(),
        "pipeline": "ground_truth_scaffold",
        "extraction_mode": "placeholder",
        "todo": "Replace placeholder extraction with AI vision-based PDF-to-JSON parser",
        "inputs": {
            "manifest": str(manifest_path),
            "ground_truth_dir": str(ground_truth_dir),
        },
        "summary": {
            "total_manifest_matches_considered": len(matches),
            "total_files_seen": total_seen,
            "pdf_files_seen": pdf_seen,
            "json_written": json_written,
            "json_skipped_existing": json_skipped_existing,
            "skipped_non_pdf": len(skipped_non_pdf),
            "failures": len(failures),
            "parse_success_rate_pct": round((json_written / pdf_seen) * 100, 2) if pdf_seen else 0.0,
        },
        "field_extraction_rates_pct": rates,
        "skipped_non_pdf_files": skipped_non_pdf,
        "failures": failures,
        "sample_parsed_output": structured_records[:3],
    }

    with stats_path.open("w") as f:
        json.dump(stats, f, indent=2)

    print(f"Processed matches: {len(matches)}")
    print(f"PDFs seen: {pdf_seen}")
    print(f"JSON written: {json_written}")
    print(f"JSON skipped (already exists): {json_skipped_existing}")
    print(f"Skipped non-PDF: {len(skipped_non_pdf)}")
    print(f"Failures: {len(failures)}")
    print(f"Wrote stats: {stats_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
