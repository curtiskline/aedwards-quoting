#!/usr/bin/env python3
"""Run RFQ extraction repeatedly and report field-level variance.

This intentionally stops after parsing: pricing is deterministic and is not
part of the repeatability measurement.

Examples:
    python tools/repeatability_validate.py --runs 5
    python tools/repeatability_validate.py --runs 10 --filter '*jhamilton_RFQ.eml'
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from allenedwards.cli import get_provider, resolve_provider_name  # noqa: E402
from allenedwards.parser import parse_rfq_multi  # noqa: E402

DEFAULT_EMAILS_DIR = PROJECT_ROOT / "data" / "test-corpus" / "emails"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "test-corpus" / "repeatability-report.json"
MISSING = {"__missing__": True}


def extraction_dicts(rfqs) -> list[dict[str, Any]]:
    """Convert parser dataclasses to comparable JSON-shaped values."""
    results = []
    for rfq in rfqs:
        value = asdict(rfq)
        value.pop("raw_body", None)
        results.append(value)
    return results


def flatten_fields(value: Any, path: str = "") -> dict[str, Any]:
    """Flatten nested extraction output into stable field paths."""
    fields: dict[str, Any] = {}
    if isinstance(value, dict):
        if not value:
            fields[path] = {}
        for key in sorted(value):
            child = f"{path}.{key}" if path else key
            fields.update(flatten_fields(value[key], child))
    elif isinstance(value, list):
        fields[f"{path}.__count__"] = len(value)
        for index, item in enumerate(value):
            fields.update(flatten_fields(item, f"{path}[{index}]"))
    else:
        fields[path] = value
    return fields


def compare_runs(run_outputs: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """Return each field whose value differs between successful runs."""
    flattened = [flatten_fields({"quotes": output}) for output in run_outputs]
    paths = sorted({path for run in flattened for path in run})
    variable_fields = []

    for path in paths:
        grouped: dict[str, dict[str, Any]] = {}
        for run_index, run in enumerate(flattened, start=1):
            value = run.get(path, MISSING)
            key = json.dumps(value, sort_keys=True, default=str)
            entry = grouped.setdefault(key, {"value": value, "runs": []})
            entry["runs"].append(run_index)
        if len(grouped) > 1:
            variable_fields.append(
                {"field": path, "variants": list(grouped.values())}
            )

    return {
        "successful_runs": len(run_outputs),
        "stable": not variable_fields,
        "variant_field_count": len(variable_fields),
        "variant_fields": variable_fields,
    }


def run_email(eml_path: Path, provider, runs: int, delay: float) -> dict[str, Any]:
    outputs: list[list[dict[str, Any]]] = []
    errors = []
    durations = []

    for run_index in range(1, runs + 1):
        started = time.monotonic()
        try:
            outputs.append(extraction_dicts(parse_rfq_multi(eml_path, provider)))
        except Exception as error:  # report API/parser failures without hiding variance elsewhere
            errors.append(
                {
                    "run": run_index,
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )
        durations.append(round(time.monotonic() - started, 3))
        if run_index < runs and delay > 0:
            time.sleep(delay)

    comparison = compare_runs(outputs)
    comparison.update(
        {
            "filename": eml_path.name,
            "requested_runs": runs,
            "errors": errors,
            "durations_s": durations,
            "stable": comparison["stable"] and not errors,
        }
    )
    return comparison


def build_report(
    email_reports: list[dict[str, Any]], provider_name: str, runs: int
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name,
        "runs_per_email": runs,
        "email_count": len(email_reports),
        "stable_email_count": sum(report["stable"] for report in email_reports),
        "variant_email_count": sum(not report["stable"] for report in email_reports),
        "variant_field_count": sum(
            report["variant_field_count"] for report in email_reports
        ),
        "emails": email_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run RFQ extraction N times and report field-level variance."
    )
    parser.add_argument("--runs", type=int, default=5, help="Parses per email (default: 5)")
    parser.add_argument("--provider", help="LLM provider override (claude/minimax/mock)")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between API calls")
    parser.add_argument("--filter", help="Filename glob, for example '*jhamilton_RFQ.eml'")
    parser.add_argument("--emails-dir", type=Path, default=DEFAULT_EMAILS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.runs < 2:
        parser.error("--runs must be at least 2 to measure variance")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if not args.emails_dir.is_dir():
        parser.error(f"email directory not found: {args.emails_dir}")

    emails = sorted(args.emails_dir.glob("*.eml"))
    if args.filter:
        emails = [path for path in emails if fnmatch.fnmatch(path.name, args.filter)]
    if not emails:
        parser.error("no email files matched")

    provider = get_provider()
    provider_name = resolve_provider_name()
    reports = []
    for index, eml_path in enumerate(emails, start=1):
        print(f"[{index}/{len(emails)}] {eml_path.name}", flush=True)
        report = run_email(eml_path, provider, args.runs, args.delay)
        reports.append(report)
        state = "stable" if report["stable"] else "VARIANT"
        print(
            f"  {state}: {report['variant_field_count']} variable fields, "
            f"{len(report['errors'])} errors"
        )

    result = build_report(reports, provider_name, args.runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    print(
        f"Summary: {result['stable_email_count']} stable, "
        f"{result['variant_email_count']} variant; {result['variant_field_count']} "
        f"variable fields. Report: {args.output}"
    )
    return 1 if result["variant_email_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
