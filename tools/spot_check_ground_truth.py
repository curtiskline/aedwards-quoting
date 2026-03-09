#!/usr/bin/env python3
"""Pick N random quote PDFs from the manifest and print a Claude Code prompt to extract them.

Usage:
    python tools/spot_check_ground_truth.py          # 10 random PDFs
    python tools/spot_check_ground_truth.py --count 5 # 5 random PDFs
    python tools/spot_check_ground_truth.py --seed 42 # reproducible selection
    python tools/spot_check_ground_truth.py --list     # just list selected files, no prompt
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = PROJECT_ROOT / "data" / "test-corpus" / "manifest.json"
ATTACHMENTS_DIR = PROJECT_ROOT / "data" / "curtis-emails" / "attachments-jamee-sent"
GROUND_TRUTH_DIR = PROJECT_ROOT / "data" / "test-corpus" / "ground-truth"

SCHEMA = """{
  "customer_name": "<company from Bill To block>",
  "contact_name": "<person from Requested By>",
  "contact_email": "<from Req By Email>",
  "contact_phone": "<from Req By Phone>",
  "ship_to": {
    "company": "<company from Ship To>",
    "attention": "<person name from Ship To if different from contact_name, else null>",
    "street": "<street from Ship To, null if absent>",
    "city": "<city from Ship To, null if TBD or absent>",
    "state": "<state, null if absent>",
    "postal_code": "<zip, null if absent>",
    "country": "United States"
  },
  "po_number": "<from PO # field, null if blank>",
  "quote_number": "<e.g. QUO-125-383>",
  "line_items": [
    {
      "part_number": "<value from Item column>",
      "description": "<value from Description column>",
      "quantity": "<integer from Quantity column>",
      "unit_price": "<number from Rate column>",
      "total": "<number from Amount column>"
    }
  ],
  "subtotal": "<number>",
  "shipping_amount": "<number from Shipping/Handling>",
  "tax_amount": "<number from Sales Tax>",
  "total": "<number>",
  "_meta": {
    "source_pdf": "<filename only>",
    "extraction_status": "complete",
    "extraction_method": "claude_vision",
    "parsed_at": "<current ISO timestamp>",
    "notes": null
  }
}"""


def load_pdf_list() -> list[str]:
    with MANIFEST.open() as f:
        data = json.load(f)
    return [
        m["attachment"]
        for m in data["matches"]
        if m["attachment"].lower().endswith(".pdf")
    ]


def generate_prompt(selected: list[str]) -> str:
    files_block = "\n".join(
        f"  {i+1}. {ATTACHMENTS_DIR / name}\n     -> {GROUND_TRUTH_DIR / (Path(name).stem + '.json')}"
        for i, name in enumerate(selected)
    )

    return f"""You are a PDF data extractor. For each PDF below, read it with the Read tool and write a JSON file with the extracted data.

FILES ({len(selected)} PDFs):
{files_block}

JSON SCHEMA (use EXACTLY these field names, no extras):
{SCHEMA}

RULES:
- Process each PDF one at a time: read PDF, write JSON, move to next.
- Use EXACTLY the field names shown. Do not add extra fields.
- Monetary values as plain numbers (622.30 not "$622.30"). Quantity as integer.
- If a field is blank/empty/TBD in the PDF, use null.
- Overwrite any existing JSON files.
- When all files are done, print a summary table: filename | quote_number | total | status
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Spot-check ground truth extraction")
    parser.add_argument("--count", "-n", type=int, default=10, help="Number of PDFs to select (default: 10)")
    parser.add_argument("--seed", "-s", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--list", "-l", action="store_true", help="Just list selected files, don't print prompt")
    args = parser.parse_args()

    pdfs = load_pdf_list()
    if not pdfs:
        print("ERROR: No PDFs found in manifest", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    count = min(args.count, len(pdfs))
    selected = rng.sample(pdfs, count)

    if args.list:
        for name in selected:
            print(name)
        return 0

    print(generate_prompt(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
