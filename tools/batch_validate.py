#!/usr/bin/env python3
"""Batch validation runner: parse every email in the test corpus and capture outputs.

Usage:
    python tools/batch_validate.py                    # Run all emails
    python tools/batch_validate.py --sample 10        # Run 10 random emails
    python tools/batch_validate.py --resume            # Skip already-processed emails
    python tools/batch_validate.py --sample 5 --resume # Sample 5 from unprocessed
"""

import argparse
import json
import os
import random
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from allenedwards.parser import parse_rfq_multi  # noqa: E402
from allenedwards.pricing import generate_quote  # noqa: E402
from allenedwards.pdf_generator import generate_quote_pdf  # noqa: E402
from allenedwards.cli import generate_quote_number  # noqa: E402


EMAILS_DIR = PROJECT_ROOT / "data" / "test-corpus" / "emails"
OUTPUT_DIR = PROJECT_ROOT / "data" / "test-corpus" / "our-output"

# Rate limiting: seconds between API calls
API_DELAY = 0.5


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def get_provider():
    """Get LLM provider (mirrors cli.py logic)."""
    from allenedwards.providers.claude import ClaudeProvider

    provider_name = os.environ.get("LLM_PROVIDER", "claude")

    if provider_name == "mock":
        from allenedwards.providers.mock import MockProvider
        return MockProvider(), "mock"

    if provider_name == "claude" or os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeProvider(), "claude"

    from allenedwards.providers.minimax import MiniMaxProvider
    return MiniMaxProvider(), "minimax"


def load_env():
    """Load environment from ~/.env if it exists."""
    env_path = Path.home() / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value


def email_id_from_path(eml_path: Path) -> str:
    """Derive a stable email ID from the .eml filename."""
    return eml_path.stem


def process_email(eml_path: Path, provider, provider_name: str, output_dir: Path) -> dict:
    """Process a single email and save outputs. Returns metadata dict."""
    email_id = email_id_from_path(eml_path)
    result = {
        "email_id": email_id,
        "filename": eml_path.name,
        "provider": provider_name,
        "status": "success",
        "num_quotes": 0,
        "processing_time_s": 0.0,
        "error": None,
        "error_category": None,
        "quotes": [],
    }

    start = time.monotonic()
    try:
        rfqs = parse_rfq_multi(eml_path, provider)
        result["num_quotes"] = len(rfqs)

        # Build parsed JSON (list of all RFQs from this email)
        rfq_data = []
        for rfq in rfqs:
            d = asdict(rfq)
            # Remove raw_body to keep output manageable
            d.pop("raw_body", None)
            rfq_data.append(d)

        # Generate quote PDFs and enrich parsed JSON with pricing data
        for i, rfq in enumerate(rfqs):
            if not rfq.items:
                continue

            try:
                quote_number = generate_quote_number()
                if len(rfqs) > 1:
                    quote_number = f"{quote_number}-{i + 1:02d}"

                quote = generate_quote(rfq, quote_number)

                suffix = f"-{i + 1}" if len(rfqs) > 1 else ""
                pdf_path = output_dir / f"{email_id}{suffix}.pdf"
                generate_quote_pdf(quote, pdf_path)

                # Enrich parsed JSON with pricing fields for validation.
                # Keep parser-extracted quote_number untouched so validation can
                # distinguish extraction misses from true mismatches.
                rfq_data[i]["generated_quote_number"] = quote.quote_number
                rfq_data[i]["subtotal"] = float(quote.subtotal)
                rfq_data[i]["total"] = float(quote.total)

                # Add per-item pricing from quote line items
                priced_items = [li for li in quote.line_items if not li.is_note]
                if len(priced_items) == len(rfq_data[i].get("items", [])):
                    for j, li in enumerate(priced_items):
                        rfq_data[i]["items"][j]["unit_price"] = float(li.unit_price)
                        rfq_data[i]["items"][j]["total"] = float(li.total)

                result["quotes"].append({
                    "quote_number": quote.quote_number,
                    "total": float(quote.total),
                    "line_items": len(quote.line_items),
                    "project_line": rfq.project_line,
                    "confidence": rfq.confidence,
                })
            except Exception as e:
                result["quotes"].append({
                    "quote_number": None,
                    "error": str(e),
                    "project_line": rfq.project_line,
                })

        # Save enriched JSON (parsed data + pricing)
        json_path = output_dir / f"{email_id}.json"
        json_path.write_text(json.dumps(rfq_data, indent=2, default=decimal_default))

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["error_category"] = categorize_error(e)

        # Save error details
        error_path = output_dir / f"{email_id}.error"
        error_path.write_text(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")

    result["processing_time_s"] = round(time.monotonic() - start, 3)
    return result


def categorize_error(e: Exception) -> str:
    """Categorize an error for summary reporting."""
    name = type(e).__name__
    msg = str(e).lower()

    if "rate" in msg or "429" in msg:
        return "rate_limit"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "api" in msg or "anthropic" in msg:
        return "api_error"
    if "json" in msg or "decode" in msg:
        return "json_parse"
    if "email" in msg or "mime" in msg or "charset" in msg:
        return "email_parse"
    if "key" in name.lower() or "key" in msg:
        return "key_error"
    return f"other:{name}"


def build_summary(results: list[dict], total_time: float, provider_name: str) -> dict:
    """Build aggregate summary from individual results."""
    successes = [r for r in results if r["status"] == "success"]
    failures = [r for r in results if r["status"] == "error"]

    quote_counts = [r["num_quotes"] for r in successes]
    zero_quotes = sum(1 for q in quote_counts if q == 0)
    one_quote = sum(1 for q in quote_counts if q == 1)
    multi_quotes = sum(1 for q in quote_counts if q > 1)

    processing_times = [r["processing_time_s"] for r in results]
    avg_time = sum(processing_times) / len(processing_times) if processing_times else 0

    # Error categories
    error_categories = {}
    for f in failures:
        cat = f.get("error_category", "unknown")
        error_categories[cat] = error_categories.get(cat, 0) + 1

    # Rough cost estimate: ~$0.003 per email (sonnet input+output for typical email)
    estimated_cost = len(successes) * 0.003

    return {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name,
        "total_emails_processed": len(results),
        "success_count": len(successes),
        "failure_count": len(failures),
        "emails_with_0_quotes": zero_quotes,
        "emails_with_1_quote": one_quote,
        "emails_with_multiple_quotes": multi_quotes,
        "total_quotes_generated": sum(quote_counts),
        "average_processing_time_s": round(avg_time, 3),
        "total_run_time_s": round(total_time, 1),
        "estimated_api_cost_usd": round(estimated_cost, 2),
        "error_categories": error_categories,
        "failures": [
            {"email_id": f["email_id"], "error": f["error"], "category": f["error_category"]}
            for f in failures
        ],
    }


def get_processed_ids(output_dir: Path) -> set[str]:
    """Get set of email IDs already processed (have .json output)."""
    if not output_dir.exists():
        return set()
    return {p.stem for p in output_dir.glob("*.json") if p.name != "batch-run-summary.json"}


def main():
    parser = argparse.ArgumentParser(description="Batch validate RFQ parser against email corpus")
    parser.add_argument("--sample", type=int, default=0, help="Process N random emails (0=all)")
    parser.add_argument("--resume", action="store_true", help="Skip already-processed emails")
    parser.add_argument("--provider", default=None, help="LLM provider override (claude/minimax/mock)")
    parser.add_argument("--delay", type=float, default=API_DELAY, help="Seconds between API calls")
    parser.add_argument("--emails-dir", type=Path, default=EMAILS_DIR, help="Directory with .eml files")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Output directory")
    args = parser.parse_args()

    # Load env vars
    load_env()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider

    # Gather email files
    if not args.emails_dir.exists():
        print(f"Error: emails directory not found: {args.emails_dir}")
        sys.exit(1)

    eml_files = sorted(args.emails_dir.glob("*.eml"))
    print(f"Found {len(eml_files)} .eml files in {args.emails_dir}")

    # Filter already-processed if resuming
    if args.resume:
        processed = get_processed_ids(args.output_dir)
        eml_files = [f for f in eml_files if email_id_from_path(f) not in processed]
        print(f"  {len(processed)} already processed, {len(eml_files)} remaining")

    # Sample if requested
    if args.sample and args.sample < len(eml_files):
        eml_files = random.sample(eml_files, args.sample)
        print(f"  Sampled {args.sample} emails")

    if not eml_files:
        print("No emails to process.")
        sys.exit(0)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize provider
    provider, provider_name = get_provider()
    print(f"Using provider: {provider_name}")
    print(f"Output: {args.output_dir}")
    print()

    # Process emails
    results = []
    total_start = time.monotonic()

    for idx, eml_path in enumerate(eml_files, 1):
        email_id = email_id_from_path(eml_path)
        print(f"[{idx}/{len(eml_files)}] {eml_path.name[:80]}...", end=" ", flush=True)

        result = process_email(eml_path, provider, provider_name, args.output_dir)
        results.append(result)

        status = result["status"]
        nq = result["num_quotes"]
        t = result["processing_time_s"]
        if status == "success":
            print(f"OK ({nq} quotes, {t:.1f}s)")
        else:
            print(f"ERROR: {result['error'][:60]}")

        # Rate limiting between API calls
        if idx < len(eml_files) and args.delay > 0:
            time.sleep(args.delay)

    total_time = time.monotonic() - total_start

    # Build and save summary
    summary = build_summary(results, total_time, provider_name)
    summary_path = args.output_dir / "batch-run-summary.json"

    # If resuming, merge with existing summary
    if args.resume and summary_path.exists():
        try:
            existing = json.loads(summary_path.read_text())
            print(f"\nMerging with existing summary ({existing.get('total_emails_processed', 0)} prior emails)")
        except (json.JSONDecodeError, KeyError):
            pass

    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n{'='*60}")
    print(f"BATCH RUN COMPLETE")
    print(f"{'='*60}")
    print(f"  Processed:  {summary['total_emails_processed']} emails")
    print(f"  Successes:  {summary['success_count']}")
    print(f"  Failures:   {summary['failure_count']}")
    print(f"  0 quotes:   {summary['emails_with_0_quotes']}")
    print(f"  1 quote:    {summary['emails_with_1_quote']}")
    print(f"  Multi:      {summary['emails_with_multiple_quotes']}")
    print(f"  Total time: {summary['total_run_time_s']:.1f}s")
    print(f"  Est. cost:  ${summary['estimated_api_cost_usd']:.2f}")
    if summary["error_categories"]:
        print(f"  Error types: {summary['error_categories']}")
    print(f"\nSummary: {summary_path}")


if __name__ == "__main__":
    main()
