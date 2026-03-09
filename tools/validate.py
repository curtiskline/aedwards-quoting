#!/usr/bin/env python3
"""Phase 4: Validate parser output against ground truth.

Compares our parser output (from batch_validate.py) against ground truth
(from parse_ground_truth.py) and produces accuracy reports.

Usage:
    python tools/validate.py                         # Run full comparison
    python tools/validate.py --html-only             # Regenerate HTML from existing JSON report
    python tools/validate.py --manifest path/to/manifest.json  # Custom manifest
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = PROJECT_ROOT / "data" / "test-corpus"
GROUND_TRUTH_DIR = CORPUS_DIR / "ground-truth"
OUR_OUTPUT_DIR = CORPUS_DIR / "our-output"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
REPORT_JSON_PATH = CORPUS_DIR / "validation-report.json"
REPORT_HTML_PATH = CORPUS_DIR / "validation-report.html"


# ---------------------------------------------------------------------------
# Fuzzy / comparison helpers
# ---------------------------------------------------------------------------

def normalize_str(s: str | None) -> str:
    """Lowercase, strip, collapse whitespace."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip().lower())


# Legal suffixes to strip when comparing company names (order: longest first)
_LEGAL_SUFFIXES = [
    "mobile services",
    "international",
    "operating",
    "pipeline",
    "services",
    "company",
    "l.p.",
    "l p",
    "corp.",
    "corp",
    "inc.",
    "inc",
    "llc",
    "ltd.",
    "ltd",
    "lp",
    "co.",
    "co",
]


def normalize_company_name(s: str | None) -> str:
    """Normalize a company name for comparison.

    Strips legal suffixes (Inc, LLC, L.P., etc.), normalizes punctuation
    (& → and), and collapses whitespace.  Designed so cosmetic differences
    like "Buckeye Partners L.P." vs "Buckeye Partners, L.P." compare equal.
    """
    if not s:
        return ""
    n = s.strip().lower()
    # Normalize & → and
    n = n.replace("&", "and")
    # Remove commas, periods, parentheses, slashes
    n = re.sub(r"[,./()]+", " ", n)
    # Collapse whitespace
    n = re.sub(r"\s+", " ", n).strip()
    # Strip known legal suffixes (may appear at end, possibly repeated)
    changed = True
    while changed:
        changed = False
        for suffix in _LEGAL_SUFFIXES:
            if n.endswith(" " + suffix):
                n = n[: -(len(suffix) + 1)].rstrip()
                changed = True
            elif n == suffix:
                break
    return n.strip()


def fuzzy_match(a: str | None, b: str | None, threshold: float = 0.8,
                company: bool = False) -> str:
    """Compare two strings. Returns 'exact_match', 'close_match', 'mismatch', or 'missing'.

    If company=True, applies company-name normalization (strips legal suffixes,
    normalizes punctuation) before comparing.
    """
    if company:
        na, nb = normalize_company_name(a), normalize_company_name(b)
    else:
        na, nb = normalize_str(a), normalize_str(b)
    if not na and not nb:
        return "exact_match"  # both empty
    if not na or not nb:
        return "missing"
    if na == nb:
        return "exact_match"
    # Simple token overlap ratio
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return "mismatch"
    overlap = len(ta & tb) / max(len(ta), len(tb))
    if overlap >= threshold:
        return "close_match"
    # Check containment (one is substring of other)
    if na in nb or nb in na:
        return "close_match"
    return "mismatch"


def compare_numeric(a: float | int | None, b: float | int | None,
                    tolerances: tuple[float, ...] = (0.01, 0.05, 0.10)) -> str:
    """Compare two numeric values. Returns accuracy tier or 'missing'."""
    if a is None and b is None:
        return "exact_match"
    if a is None or b is None:
        return "missing"
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return "mismatch"
    if fa == fb:
        return "exact_match"
    if fb == 0:
        return "mismatch" if fa != 0 else "exact_match"
    pct_diff = abs(fa - fb) / abs(fb)
    for tol in tolerances:
        if pct_diff <= tol:
            return f"within_{int(tol*100)}pct"
    return "mismatch"


# ---------------------------------------------------------------------------
# Matching: map ground truth JSON to our parser output JSON
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> list[dict]:
    with path.open() as f:
        data = json.load(f)
    return data["matches"]


def load_json_safe(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        with path.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def build_match_pairs(manifest: list[dict]) -> list[dict]:
    """Build pairs of (ground_truth_json, our_output_json) from manifest.

    Each manifest entry maps an attachment PDF to an email.
    Ground truth: ground-truth/<attachment_stem>.json
    Our output: our-output/<email_stem>.json (list of parsed RFQs)
    """
    pairs = []
    seen = set()

    for entry in manifest:
        attachment = entry.get("attachment", "")
        email_filename = entry.get("email_filename", "")

        if not attachment or not email_filename:
            continue

        gt_stem = Path(attachment).stem
        # Skip non-PDF attachments (no ground truth JSON for them)
        if not attachment.lower().endswith(".pdf"):
            continue

        gt_path = GROUND_TRUTH_DIR / f"{gt_stem}.json"
        email_stem = Path(email_filename).stem
        our_path = OUR_OUTPUT_DIR / f"{email_stem}.json"

        pair_key = (gt_stem, email_stem)
        if pair_key in seen:
            continue
        seen.add(pair_key)

        pairs.append({
            "attachment": attachment,
            "email_filename": email_filename,
            "email_subject": entry.get("email_subject", ""),
            "email_date": entry.get("email_date", ""),
            "gt_path": str(gt_path),
            "our_path": str(our_path),
            "gt_stem": gt_stem,
            "email_stem": email_stem,
        })

    return pairs


# ---------------------------------------------------------------------------
# Field-level comparison
# ---------------------------------------------------------------------------

def compare_ship_to(gt_ship: dict | None, our_ship: dict | None) -> dict[str, str]:
    """Compare ship_to sub-fields."""
    gt = gt_ship or {}
    ou = our_ship or {}
    results = {}
    results["ship_to.company"] = fuzzy_match(gt.get("company"), ou.get("company"), company=True)
    for field in ("city", "state", "postal_code"):
        results[f"ship_to.{field}"] = fuzzy_match(gt.get(field), ou.get(field))
    return results


def match_line_items(gt_items: list[dict], our_items: list[dict]) -> dict:
    """Compare line items between ground truth and our output.

    Returns detailed comparison of matched, missing, and extra items.
    """
    # Normalize GT items to extract structured fields from part_number/description
    gt_normalized = [_normalize_gt_item(item) for item in gt_items]

    result = {
        "gt_count": len(gt_items),
        "our_count": len(our_items),
        "count_match": len(gt_items) == len(our_items),
        "matched_items": [],
        "missing_items": [],  # in GT but not in ours
        "extra_items": [],    # in ours but not in GT
    }

    if not gt_items and not our_items:
        return result

    # Simple positional matching (since items are ordered)
    # For a more robust approach, we could do bipartite matching on product similarity
    used_our = set()
    for gi, gt_item in enumerate(gt_normalized):
        best_match_idx = None
        best_score = -1

        for oi, our_item in enumerate(our_items):
            if oi in used_our:
                continue
            score = _item_similarity(gt_item, our_item)
            if score > best_score:
                best_score = score
                best_match_idx = oi

        if best_match_idx is not None and best_score > 0.3:
            used_our.add(best_match_idx)
            comparison = _compare_single_item(gt_item, our_items[best_match_idx])
            comparison["gt_index"] = gi
            comparison["our_index"] = best_match_idx
            comparison["similarity_score"] = round(best_score, 3)
            result["matched_items"].append(comparison)
        else:
            result["missing_items"].append({
                "gt_index": gi,
                "item": _item_summary(gt_item),
            })

    for oi, our_item in enumerate(our_items):
        if oi not in used_our:
            result["extra_items"].append({
                "our_index": oi,
                "item": _item_summary(our_item),
            })

    return result


def _normalize_gt_item(gt: dict) -> dict:
    """Extract structured fields from ground-truth part_number and description.

    GT items have part_number (e.g. 'S-12.34-38-50-10') and description
    (e.g. 'reg half sole, 12-3/4" ID, 3/8" w/t, A572 GR50, 10\' long.')
    but lack the structured fields our parser outputs. This bridges the gap.
    """
    item = dict(gt)  # shallow copy

    part = gt.get("part_number", "") or ""
    desc = (gt.get("description", "") or "").lower()

    # --- Infer product_type ---
    if not item.get("product_type"):
        # Check girth_weld BEFORE sleeve (since "girth weld sleeve" contains "sleeve")
        if part.startswith("G-") or "girth weld" in desc:
            item["product_type"] = "girth_weld"
        elif part.startswith("S-") or "half sole" in desc or "sleeve" in desc:
            if "ovsz" in desc or "oversleeve" in desc or "over sleeve" in desc:
                item["product_type"] = "oversleeve"
            elif "compression" in desc or part.startswith("Compression"):
                item["product_type"] = "compression"
            else:
                item["product_type"] = "sleeve"
        elif part.upper().startswith("GTW") or "bag" in desc or "geotextile" in desc:
            item["product_type"] = "bag"
        elif part.upper().startswith("OW-") or "omegawrap" in desc or "omega wrap" in desc:
            item["product_type"] = "omegawrap"
        elif "backing strip" in desc.lower() or part.lower().startswith("backing"):
            item["product_type"] = "accessory"
        elif "concrete" in desc or "coating" in desc:
            item["product_type"] = "service"

    # --- Parse part_number for dimensions ---
    # Format: S-<diam>-<wt_code>-<grade>-<length>[-M][-P]
    # or G-<diam>-<wt_code>-<grade>-<length>[-M][-P]
    pn_match = re.match(
        r"^[SG]-"
        r"([\d.]+)-"           # diameter
        r"(\d+)-"              # wall thickness code (e.g. 38 = 3/8)
        r"(\d+)-"              # grade
        r"([\d.]+)"            # length
        r"(?:-([MP]+))?",      # optional milling/painting suffix
        part,
    )
    if pn_match:
        if not item.get("diameter"):
            item["diameter"] = float(pn_match.group(1))
        wt_code = pn_match.group(2)
        if not item.get("wall_thickness"):
            wt_map = {"14": 0.25, "516": 0.3125, "38": 0.375,
                       "12": 0.5, "58": 0.625, "34": 0.75}
            item["wall_thickness"] = wt_map.get(wt_code)
        if not item.get("grade"):
            item["grade"] = int(pn_match.group(3))
        if not item.get("length_ft"):
            item["length_ft"] = float(pn_match.group(4))
        suffix = pn_match.group(5) or ""
        if "M" in suffix:
            item["milling"] = True
        if "P" in suffix:
            item["painting"] = True

    # --- Fallback: parse description for dimensions ---
    if not item.get("diameter"):
        # Match patterns like '12-3/4" ID', '6-5/8"', '16" ID'
        d_match = re.search(r"(\d+)(?:-(\d+)/(\d+))?[\"″]\s*(?:ID|OD)?", gt.get("description", "") or "")
        if d_match:
            d_val = float(d_match.group(1))
            if d_match.group(2) and d_match.group(3):
                d_val += float(d_match.group(2)) / float(d_match.group(3))
            item["diameter"] = d_val

    if not item.get("wall_thickness"):
        wt_match = re.search(r"(\d+)/(\d+)[\"″]\s*w/?t", gt.get("description", "") or "", re.I)
        if wt_match:
            item["wall_thickness"] = float(wt_match.group(1)) / float(wt_match.group(2))

    if not item.get("grade"):
        gr_match = re.search(r"GR\.?\s*(\d+)", gt.get("description", "") or "", re.I)
        if gr_match:
            item["grade"] = int(gr_match.group(1))

    if not item.get("length_ft"):
        len_match = re.search(r"(\d+)['\u2032]\s*long", gt.get("description", "") or "", re.I)
        if len_match:
            item["length_ft"] = float(len_match.group(1))
        else:
            # Also try "X" long pattern like "12" long" (inches -> check if description says long)
            len_match2 = re.search(r"(\d+)[\"″]\s*long", gt.get("description", "") or "", re.I)
            if len_match2:
                # This is inches, convert to feet for length_ft? No, keep raw.
                # Actually girth welds use inches for length (e.g. "12" long")
                item["length_ft"] = float(len_match2.group(1)) / 12.0

    return item


def _item_similarity(gt: dict, our: dict) -> float:
    """Score similarity between two line items (0-1)."""
    score = 0.0
    total = 0.0

    # Product type match (high weight)
    total += 3.0
    if fuzzy_match(gt.get("product_type"), our.get("product_type")) in ("exact_match", "close_match"):
        score += 3.0

    # Quantity match
    total += 2.0
    if gt.get("quantity") == our.get("quantity"):
        score += 2.0

    # Diameter match (with tolerance for fractional inch rounding)
    total += 2.0
    gt_d = gt.get("diameter")
    our_d = our.get("diameter")
    if gt_d is not None and our_d is not None:
        try:
            if abs(float(gt_d) - float(our_d)) < 0.5:
                score += 2.0
        except (TypeError, ValueError):
            pass
    elif gt_d is None and our_d is None:
        score += 1.0

    # Description similarity (helps match items with different naming conventions)
    total += 1.0
    desc_result = fuzzy_match(gt.get("description"), our.get("description"))
    if desc_result in ("exact_match", "close_match"):
        score += 1.0

    return score / total if total > 0 else 0.0


def _compare_single_item(gt: dict, our: dict) -> dict:
    """Detailed comparison of a matched pair of line items."""
    fields = {}
    fields["product_type"] = fuzzy_match(gt.get("product_type"), our.get("product_type"))
    fields["quantity"] = "exact_match" if gt.get("quantity") == our.get("quantity") else (
        "missing" if gt.get("quantity") is None or our.get("quantity") is None else "mismatch"
    )
    fields["description"] = fuzzy_match(gt.get("description"), our.get("description"))

    for nf in ("diameter", "wall_thickness", "length_ft"):
        fields[nf] = compare_numeric(gt.get(nf), our.get(nf))

    fields["grade"] = "exact_match" if gt.get("grade") == our.get("grade") else (
        "missing" if gt.get("grade") is None or our.get("grade") is None else "mismatch"
    )

    # Pricing fields (may be in QuoteLineItem format)
    fields["unit_price"] = compare_numeric(gt.get("unit_price"), our.get("unit_price"))
    fields["total"] = compare_numeric(gt.get("total"), our.get("total"))

    return {"fields": fields}


def _item_summary(item: dict) -> dict:
    """Brief summary of a line item for reporting."""
    return {
        "product_type": item.get("product_type"),
        "quantity": item.get("quantity"),
        "diameter": item.get("diameter"),
        "description": str(item.get("description", ""))[:80],
    }


def compare_pair(gt_data: dict, our_data: dict) -> dict:
    """Compare a single ground-truth record against our parser output.

    our_data is the best-matching RFQ from our output list for this ground truth.
    """
    field_results = {}

    # Top-level string fields
    # Use company-name normalization for customer_name
    field_results["customer_name"] = fuzzy_match(
        gt_data.get("customer_name"), our_data.get("customer_name"), company=True
    )
    for field in ("contact_name", "contact_email", "contact_phone", "po_number"):
        field_results[field] = fuzzy_match(gt_data.get(field), our_data.get(field))

    # Quote number
    field_results["quote_number"] = fuzzy_match(
        gt_data.get("quote_number"), our_data.get("quote_number")
    )

    # Ship-to
    gt_ship = gt_data.get("ship_to") or {}
    our_ship = our_data.get("ship_to") or {}
    field_results.update(compare_ship_to(gt_ship, our_ship))

    # Pricing totals
    field_results["subtotal"] = compare_numeric(gt_data.get("subtotal"), our_data.get("subtotal"))
    field_results["total"] = compare_numeric(gt_data.get("total"), our_data.get("total"))

    # Line items
    gt_items = gt_data.get("line_items", [])
    our_items = our_data.get("items", our_data.get("line_items", []))
    line_item_comparison = match_line_items(gt_items, our_items)

    return {
        "field_results": field_results,
        "line_items": line_item_comparison,
    }


def pick_best_rfq(gt_data: dict, our_rfqs: list[dict]) -> dict | None:
    """From a list of our parsed RFQs, pick the one best matching ground truth."""
    if not our_rfqs:
        return None
    if len(our_rfqs) == 1:
        return our_rfqs[0]

    # Score each RFQ by customer_name + item count similarity
    best = None
    best_score = -1
    for rfq in our_rfqs:
        score = 0
        if fuzzy_match(gt_data.get("customer_name"), rfq.get("customer_name"), company=True) in ("exact_match", "close_match"):
            score += 2
        gt_n = len(gt_data.get("line_items", []))
        our_n = len(rfq.get("items", rfq.get("line_items", [])))
        if gt_n > 0 and our_n > 0:
            score += 1.0 - abs(gt_n - our_n) / max(gt_n, our_n)
        if score > best_score:
            best_score = score
            best = rfq
    return best


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------

def categorize_case(comparison: dict | None, gt_data: dict | None, our_data: dict | None) -> str:
    """Assign a category to this test case."""
    if gt_data is None or _is_placeholder(gt_data):
        return "NO_GROUND_TRUTH"
    if our_data is None:
        return "PARSE_FAIL"

    if comparison is None:
        return "PARSE_FAIL"

    fr = comparison["field_results"]
    li = comparison["line_items"]

    # Check if all fields match
    all_exact = all(v in ("exact_match", "close_match") for v in fr.values())
    no_missing = not li["missing_items"]
    no_extra = not li["extra_items"]

    if all_exact and no_missing and no_extra and li["count_match"]:
        # Check pricing
        pricing_ok = fr.get("subtotal", "missing") in ("exact_match", "within_1pct") and \
                     fr.get("total", "missing") in ("exact_match", "within_1pct")
        if pricing_ok:
            return "PERFECT"
        return "PRICING_DIFF"

    if li["missing_items"]:
        return "MISSING_ITEMS"
    if li["extra_items"]:
        return "EXTRA_ITEMS"

    # Check for product type mismatches
    for matched in li.get("matched_items", []):
        if matched.get("fields", {}).get("product_type") == "mismatch":
            return "WRONG_PRODUCT"

    return "STRUCTURAL_DIFF"


def _is_placeholder(data: dict) -> bool:
    """Check if ground truth is still a placeholder (all nulls)."""
    meta = data.get("_meta", {})
    if meta.get("extraction_status") == "placeholder":
        return True
    # Check if all main fields are null
    check_fields = ["customer_name", "contact_name", "po_number", "quote_number"]
    if all(data.get(f) is None for f in check_fields) and not data.get("line_items"):
        return True
    return False


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def compute_field_accuracy(cases: list[dict]) -> dict[str, dict[str, int]]:
    """Aggregate field-level accuracy across all compared cases."""
    field_counts: dict[str, Counter] = {}

    for case in cases:
        comp = case.get("comparison")
        if not comp:
            continue
        for field, result in comp["field_results"].items():
            if field not in field_counts:
                field_counts[field] = Counter()
            field_counts[field][result] += 1

    accuracy = {}
    for field, counts in sorted(field_counts.items()):
        total = sum(counts.values())
        accuracy[field] = {
            "total": total,
            "exact_match": counts.get("exact_match", 0),
            "close_match": counts.get("close_match", 0),
            "mismatch": counts.get("mismatch", 0),
            "missing": counts.get("missing", 0),
        }
        # Add percentage
        correct = counts.get("exact_match", 0) + counts.get("close_match", 0)
        accuracy[field]["accuracy_pct"] = round(100 * correct / total, 1) if total > 0 else 0.0

        # Pricing tiers
        for tier in ("within_1pct", "within_5pct", "within_10pct"):
            if counts.get(tier, 0) > 0:
                accuracy[field][tier] = counts[tier]

    return accuracy


def compute_line_item_accuracy(cases: list[dict]) -> dict:
    """Aggregate line item comparison stats."""
    total_gt = 0
    total_our = 0
    total_matched = 0
    total_missing = 0
    total_extra = 0
    count_matches = 0

    item_field_counts: dict[str, Counter] = {}

    for case in cases:
        comp = case.get("comparison")
        if not comp:
            continue
        li = comp["line_items"]
        total_gt += li["gt_count"]
        total_our += li["our_count"]
        total_matched += len(li["matched_items"])
        total_missing += len(li["missing_items"])
        total_extra += len(li["extra_items"])
        if li["count_match"]:
            count_matches += 1

        for matched in li["matched_items"]:
            for field, result in matched.get("fields", {}).items():
                if field not in item_field_counts:
                    item_field_counts[field] = Counter()
                item_field_counts[field][result] += 1

    item_accuracy = {}
    for field, counts in sorted(item_field_counts.items()):
        total = sum(counts.values())
        correct = counts.get("exact_match", 0) + counts.get("close_match", 0)
        item_accuracy[field] = {
            "total": total,
            "exact_match": counts.get("exact_match", 0),
            "close_match": counts.get("close_match", 0),
            "mismatch": counts.get("mismatch", 0),
            "missing": counts.get("missing", 0),
            "accuracy_pct": round(100 * correct / total, 1) if total > 0 else 0.0,
        }

    compared_cases = sum(1 for c in cases if c.get("comparison"))
    return {
        "total_gt_items": total_gt,
        "total_our_items": total_our,
        "total_matched": total_matched,
        "total_missing_in_ours": total_missing,
        "total_extra_in_ours": total_extra,
        "count_match_rate_pct": round(100 * count_matches / compared_cases, 1) if compared_cases else 0.0,
        "item_field_accuracy": item_accuracy,
    }


def generate_json_report(cases: list[dict]) -> dict:
    """Generate the full JSON validation report."""
    compared = [c for c in cases if c.get("comparison")]
    categories = Counter(c["category"] for c in cases)

    return {
        "generated": datetime.now().isoformat(),
        "summary": {
            "total_test_cases": len(cases),
            "compared": len(compared),
            "categories": dict(categories.most_common()),
        },
        "field_accuracy": compute_field_accuracy(cases),
        "line_item_accuracy": compute_line_item_accuracy(cases),
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _esc(s: Any) -> str:
    return html.escape(str(s)) if s is not None else ""


def _badge(result: str) -> str:
    """Return colored badge HTML for a comparison result."""
    colors = {
        "exact_match": "#22c55e",
        "close_match": "#84cc16",
        "within_1pct": "#84cc16",
        "within_5pct": "#eab308",
        "within_10pct": "#f97316",
        "mismatch": "#ef4444",
        "missing": "#94a3b8",
    }
    color = colors.get(result, "#6b7280")
    label = result.replace("_", " ")
    return f'<span style="background:{color};color:#fff;padding:2px 6px;border-radius:4px;font-size:12px">{label}</span>'


def _cat_color(cat: str) -> str:
    colors = {
        "PERFECT": "#22c55e",
        "PRICING_DIFF": "#eab308",
        "MISSING_ITEMS": "#f97316",
        "EXTRA_ITEMS": "#f97316",
        "WRONG_PRODUCT": "#ef4444",
        "PARSE_FAIL": "#ef4444",
        "NO_GROUND_TRUTH": "#94a3b8",
        "STRUCTURAL_DIFF": "#f97316",
        "NON_RFQ": "#6b7280",
    }
    return colors.get(cat, "#6b7280")


def generate_html_report(report: dict) -> str:
    """Generate human-readable HTML validation report."""
    summary = report["summary"]
    field_acc = report["field_accuracy"]
    li_acc = report["line_item_accuracy"]
    cases = report["cases"]

    # Sort cases: worst first (PARSE_FAIL, WRONG_PRODUCT, MISSING, EXTRA, PRICING, PERFECT, NO_GT)
    cat_order = {
        "PARSE_FAIL": 0, "WRONG_PRODUCT": 1, "MISSING_ITEMS": 2,
        "EXTRA_ITEMS": 3, "STRUCTURAL_DIFF": 4, "PRICING_DIFF": 5,
        "PERFECT": 6, "NO_GROUND_TRUTH": 7, "NON_RFQ": 8,
    }
    sorted_cases = sorted(cases, key=lambda c: cat_order.get(c.get("category", ""), 99))

    # Compute overall accuracy
    compared = summary.get("compared", 0)
    cats = summary.get("categories", {})
    perfect = cats.get("PERFECT", 0)
    overall_pct = round(100 * perfect / compared, 1) if compared > 0 else 0.0

    parts = [f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Validation Report - Allen Edwards RFQ Parser</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 20px; background: #f8fafc; color: #1e293b; }}
  h1 {{ color: #0f172a; }}
  .dashboard {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 16px; margin: 20px 0; }}
  .card {{ background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .card .value {{ font-size: 2em; font-weight: 700; }}
  .card .label {{ color: #64748b; font-size: 0.9em; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff;
           border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
  th {{ background: #f1f5f9; font-weight: 600; }}
  tr:hover {{ background: #f8fafc; }}
  .bar {{ height: 8px; border-radius: 4px; background: #e2e8f0; }}
  .bar-fill {{ height: 100%; border-radius: 4px; }}
  details {{ margin: 4px 0; }}
  summary {{ cursor: pointer; padding: 8px; border-radius: 4px; }}
  summary:hover {{ background: #f1f5f9; }}
  .side-by-side {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .side-by-side > div {{ background: #f8fafc; padding: 12px; border-radius: 4px; font-size: 0.9em; }}
  .cat-badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px;
                color: #fff; font-weight: 600; font-size: 0.85em; }}
  pre {{ background: #f1f5f9; padding: 8px; border-radius: 4px; overflow-x: auto; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>Validation Report</h1>
<p>Generated: {_esc(report['generated'])}</p>

<h2>Summary Dashboard</h2>
<div class="dashboard">
  <div class="card">
    <div class="value">{summary['total_test_cases']}</div>
    <div class="label">Total Test Cases</div>
  </div>
  <div class="card">
    <div class="value">{compared}</div>
    <div class="label">Compared (both sides present)</div>
  </div>
  <div class="card">
    <div class="value" style="color:{'#22c55e' if overall_pct > 80 else '#eab308' if overall_pct > 50 else '#ef4444'}">{overall_pct}%</div>
    <div class="label">Perfect Match Rate</div>
  </div>
  <div class="card">
    <div class="value">{perfect}</div>
    <div class="label">Perfect Matches</div>
  </div>
</div>

<h3>Categories</h3>
<table>
<tr><th>Category</th><th>Count</th><th>%</th><th></th></tr>
"""]

    total_cases = summary["total_test_cases"] or 1
    for cat, count in sorted(cats.items(), key=lambda x: cat_order.get(x[0], 99)):
        pct = round(100 * count / total_cases, 1)
        color = _cat_color(cat)
        parts.append(f"""<tr>
  <td><span class="cat-badge" style="background:{color}">{_esc(cat)}</span></td>
  <td>{count}</td>
  <td>{pct}%</td>
  <td><div class="bar" style="width:200px"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div></td>
</tr>
""")

    parts.append("</table>")

    # Field accuracy table
    if field_acc:
        parts.append("""
<h2>Field-Level Accuracy</h2>
<table>
<tr><th>Field</th><th>Accuracy</th><th>Exact</th><th>Close</th><th>Mismatch</th><th>Missing</th><th>Total</th></tr>
""")
        for field, stats in sorted(field_acc.items(), key=lambda x: x[1].get("accuracy_pct", 0)):
            acc = stats["accuracy_pct"]
            color = "#22c55e" if acc > 80 else "#eab308" if acc > 50 else "#ef4444"
            parts.append(f"""<tr>
  <td><code>{_esc(field)}</code></td>
  <td style="color:{color};font-weight:600">{acc}%</td>
  <td>{stats['exact_match']}</td>
  <td>{stats['close_match']}</td>
  <td>{stats['mismatch']}</td>
  <td>{stats['missing']}</td>
  <td>{stats['total']}</td>
</tr>
""")
        parts.append("</table>")

    # Line item accuracy
    if li_acc.get("item_field_accuracy"):
        parts.append(f"""
<h2>Line Item Accuracy</h2>
<div class="dashboard">
  <div class="card"><div class="value">{li_acc['total_gt_items']}</div><div class="label">Ground Truth Items</div></div>
  <div class="card"><div class="value">{li_acc['total_our_items']}</div><div class="label">Our Items</div></div>
  <div class="card"><div class="value">{li_acc['total_matched']}</div><div class="label">Matched</div></div>
  <div class="card"><div class="value">{li_acc['total_missing_in_ours']}</div><div class="label">Missing (in GT, not ours)</div></div>
  <div class="card"><div class="value">{li_acc['total_extra_in_ours']}</div><div class="label">Extra (in ours, not GT)</div></div>
</div>
<table>
<tr><th>Item Field</th><th>Accuracy</th><th>Exact</th><th>Close</th><th>Mismatch</th><th>Missing</th></tr>
""")
        for field, stats in sorted(li_acc["item_field_accuracy"].items(),
                                   key=lambda x: x[1].get("accuracy_pct", 0)):
            acc = stats["accuracy_pct"]
            color = "#22c55e" if acc > 80 else "#eab308" if acc > 50 else "#ef4444"
            parts.append(f"""<tr>
  <td><code>{_esc(field)}</code></td>
  <td style="color:{color};font-weight:600">{acc}%</td>
  <td>{stats['exact_match']}</td>
  <td>{stats['close_match']}</td>
  <td>{stats['mismatch']}</td>
  <td>{stats['missing']}</td>
</tr>
""")
        parts.append("</table>")

    # Individual cases (drill-down)
    parts.append("""
<h2>Test Cases</h2>
<p>Sorted by worst-performing first. Click to expand side-by-side comparison.</p>
""")

    for i, case in enumerate(sorted_cases):
        cat = case.get("category", "UNKNOWN")
        color = _cat_color(cat)
        attachment = case.get("attachment", "unknown")
        email_subj = case.get("email_subject", "")

        parts.append(f"""<details id="case-{i}">
<summary>
  <span class="cat-badge" style="background:{color}">{_esc(cat)}</span>
  <strong>{_esc(attachment[:80])}</strong>
  <span style="color:#64748b;font-size:0.85em">— {_esc(email_subj[:60])}</span>
</summary>
""")

        comp = case.get("comparison")
        if comp:
            # Field comparison
            parts.append('<h4>Field Comparison</h4><table><tr><th>Field</th><th>Result</th></tr>')
            for field, result in comp["field_results"].items():
                parts.append(f"<tr><td><code>{_esc(field)}</code></td><td>{_badge(result)}</td></tr>")
            parts.append("</table>")

            # Side-by-side data
            gt_data = case.get("gt_data_summary", {})
            our_data = case.get("our_data_summary", {})
            parts.append("""<h4>Side-by-Side</h4><div class="side-by-side">
<div><strong>Ground Truth</strong><pre>""")
            parts.append(_esc(json.dumps(gt_data, indent=2, default=str)[:2000]))
            parts.append('</pre></div><div><strong>Our Output</strong><pre>')
            parts.append(_esc(json.dumps(our_data, indent=2, default=str)[:2000]))
            parts.append("</pre></div></div>")

            # Line items
            li = comp["line_items"]
            if li["missing_items"]:
                parts.append(f"<p style='color:#f97316'>Missing items: {len(li['missing_items'])}</p>")
            if li["extra_items"]:
                parts.append(f"<p style='color:#f97316'>Extra items: {len(li['extra_items'])}</p>")
        else:
            if cat == "NO_GROUND_TRUTH":
                parts.append("<p>Ground truth data not yet extracted (placeholder).</p>")
            elif cat == "PARSE_FAIL":
                parts.append("<p>Our parser did not produce output for this email.</p>")

        parts.append("</details>")

    parts.append("""
</body>
</html>""")

    return "".join(parts)


def _summarize_data(data: dict | None) -> dict:
    """Create a brief summary of a data record for the HTML report."""
    if not data:
        return {}
    summary = {}
    for field in ("customer_name", "contact_name", "contact_email", "po_number", "quote_number"):
        if data.get(field):
            summary[field] = data[field]
    ship = data.get("ship_to")
    if ship and any(ship.get(k) for k in ("company", "city", "state")):
        summary["ship_to"] = {k: v for k, v in ship.items() if v}
    items = data.get("items", data.get("line_items", []))
    if items:
        summary["line_items_count"] = len(items)
        summary["line_items_sample"] = [_item_summary(it) for it in items[:3]]
    for pf in ("subtotal", "total"):
        if data.get(pf) is not None:
            summary[pf] = data[pf]
    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_validation() -> dict:
    """Run the full validation pipeline and return the report dict."""
    manifest = load_manifest(MANIFEST_PATH)
    pairs = build_match_pairs(manifest)
    print(f"Manifest entries: {len(manifest)}")
    print(f"Comparison pairs: {len(pairs)}")

    cases = []
    for pair in pairs:
        gt_data = load_json_safe(Path(pair["gt_path"]))
        our_raw = load_json_safe(Path(pair["our_path"]))

        # our_raw is a list of RFQ dicts (from batch_validate.py)
        our_rfqs = our_raw if isinstance(our_raw, list) else ([our_raw] if our_raw else [])

        comparison = None
        our_matched = None
        if gt_data and not _is_placeholder(gt_data) and our_rfqs:
            our_matched = pick_best_rfq(gt_data, our_rfqs)
            if our_matched:
                comparison = compare_pair(gt_data, our_matched)

        category = categorize_case(comparison, gt_data, our_matched)

        case = {
            "attachment": pair["attachment"],
            "email_filename": pair["email_filename"],
            "email_subject": pair["email_subject"],
            "email_date": pair["email_date"],
            "gt_path": pair["gt_path"],
            "our_path": pair["our_path"],
            "gt_exists": gt_data is not None,
            "our_exists": our_raw is not None,
            "category": category,
            "comparison": comparison,
            "gt_data_summary": _summarize_data(gt_data),
            "our_data_summary": _summarize_data(our_matched),
        }
        cases.append(case)

    return generate_json_report(cases)


def main():
    global MANIFEST_PATH, GROUND_TRUTH_DIR, OUR_OUTPUT_DIR, REPORT_JSON_PATH, REPORT_HTML_PATH

    parser = argparse.ArgumentParser(description="Validate parser output against ground truth")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--gt-dir", type=Path, default=GROUND_TRUTH_DIR)
    parser.add_argument("--our-dir", type=Path, default=OUR_OUTPUT_DIR)
    parser.add_argument("--output-json", type=Path, default=REPORT_JSON_PATH)
    parser.add_argument("--output-html", type=Path, default=REPORT_HTML_PATH)
    parser.add_argument("--html-only", action="store_true",
                        help="Regenerate HTML from existing JSON report")
    args = parser.parse_args()

    MANIFEST_PATH = args.manifest
    GROUND_TRUTH_DIR = args.gt_dir
    OUR_OUTPUT_DIR = args.our_dir
    REPORT_JSON_PATH = args.output_json
    REPORT_HTML_PATH = args.output_html

    if args.html_only:
        if not REPORT_JSON_PATH.exists():
            print(f"Error: JSON report not found: {REPORT_JSON_PATH}")
            sys.exit(1)
        with REPORT_JSON_PATH.open() as f:
            report = json.load(f)
        print(f"Loaded existing report: {REPORT_JSON_PATH}")
    else:
        report = run_validation()
        REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with REPORT_JSON_PATH.open("w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nJSON report: {REPORT_JSON_PATH}")

    # Generate HTML
    html_content = generate_html_report(report)
    with REPORT_HTML_PATH.open("w") as f:
        f.write(html_content)
    print(f"HTML report: {REPORT_HTML_PATH}")

    # Print summary
    summary = report["summary"]
    cats = summary.get("categories", {})
    print(f"\n{'='*50}")
    print(f"VALIDATION SUMMARY")
    print(f"{'='*50}")
    print(f"  Total cases:     {summary['total_test_cases']}")
    print(f"  Compared:        {summary['compared']}")
    for cat, count in sorted(cats.items()):
        print(f"  {cat:20s} {count}")


if __name__ == "__main__":
    main()
