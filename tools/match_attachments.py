#!/usr/bin/env python3
"""Match attachment files to extracted emails using date + fuzzy subject matching.

Reads the email_index.json produced by split_pst_dump.py, parses attachment
filenames for date and subject fragments, and matches them to emails.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "curtis-emails"


def parse_attachment_filename(filename: str) -> dict | None:
    """Extract date and subject snippet from attachment filename.

    Pattern: YYYYMMDD_[FW_|RE_][subject snippet]_[internal code].ext
    """
    # Extract date prefix
    match = re.match(r"^(\d{8})_(.+)$", filename)
    if not match:
        return None

    date_str = match.group(1)
    rest = match.group(2)

    try:
        date = datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError:
        return None

    # Remove file extension
    name_no_ext = os.path.splitext(rest)[0]

    # The rest contains subject snippet and internal codes
    # Try to extract the subject part (before the internal quote/SO code)
    return {
        "date": date,
        "date_str": date_str,
        "rest": name_no_ext,
        "filename": filename,
    }


def normalize_subject(s: str) -> str:
    """Normalize a subject string for comparison."""
    # Remove RE:/FW: prefixes
    s = re.sub(r"^(RE|FW|Fwd)\s*:\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(RE|FW|Fwd)\s*:\s*", "", s, flags=re.IGNORECASE)  # nested
    # Normalize whitespace and punctuation
    s = re.sub(r"[_\s]+", " ", s).strip().lower()
    # Remove common noise
    s = re.sub(r"\[external( email)?\]", "", s, flags=re.IGNORECASE).strip()
    return s


def extract_subject_from_attachment_rest(rest: str) -> str:
    """Extract the subject-like portion from attachment filename remainder."""
    # Remove FW_ RE_ prefixes
    s = re.sub(r"^(FW|RE|Fwd)_\s*", "", rest, flags=re.IGNORECASE)
    s = re.sub(r"^(FW|RE|Fwd)_\s*", "", s, flags=re.IGNORECASE)

    # The filename typically has: subject_snippet_INTERNAL-CODE
    # Internal codes look like: QU HS 8 062425-Name, QUO-125-281, SO-125-0236
    # Try to split at common internal code patterns
    # But these can also appear in the subject, so use heuristics

    # Normalize underscores to spaces for matching
    s = s.replace("_", " ")
    return s.strip().lower()


def fuzzy_match_score(attachment_text: str, email_subject: str) -> float:
    """Compute fuzzy match score between attachment text and email subject."""
    a_norm = normalize_subject(attachment_text)
    e_norm = normalize_subject(email_subject)

    if not a_norm or not e_norm:
        return 0.0

    # Check if significant words from attachment appear in email subject
    a_words = set(re.findall(r"\w{3,}", a_norm))
    e_words = set(re.findall(r"\w{3,}", e_norm))

    if not a_words:
        return 0.0

    # Word overlap score
    common = a_words & e_words
    word_score = len(common) / max(len(a_words), 1)

    # Sequence matcher for substring matching
    seq_score = SequenceMatcher(None, a_norm, e_norm).ratio()

    # Check if attachment text is a substring of email subject or vice versa
    substr_score = 0.0
    if a_norm in e_norm or e_norm in a_norm:
        substr_score = 0.8

    # Weighted combination
    return max(word_score * 0.6 + seq_score * 0.4, substr_score)


def main():
    parser = argparse.ArgumentParser(description="Match attachments to emails")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing PST data",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help="Test corpus directory (default: data/test-corpus/)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.3,
        help="Minimum match score (0-1)",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    corpus_dir = args.corpus_dir or data_dir.parent / "test-corpus"

    # Load email index
    index_path = corpus_dir / "email_index.json"
    if not index_path.exists():
        print(f"ERROR: {index_path} not found. Run split_pst_dump.py first.")
        sys.exit(1)

    with open(index_path) as f:
        email_index = json.load(f)

    print(f"Loaded {len(email_index)} email records")

    # Parse email dates and build date index
    email_by_date = defaultdict(list)
    for rec in email_index:
        try:
            dt = parsedate_to_datetime(rec["date"])
            rec["_parsed_date"] = dt.date()
            email_by_date[dt.date()].append(rec)
        except Exception:
            rec["_parsed_date"] = None

    # Load attachment filenames
    attach_dir = data_dir / "attachments-jamee-sent"
    if not attach_dir.exists():
        print(f"ERROR: {attach_dir} not found")
        sys.exit(1)

    attachments = sorted(os.listdir(attach_dir))
    print(f"Found {len(attachments)} attachment files")

    # Parse attachment filenames
    parsed_attachments = []
    unparseable = []
    for fname in attachments:
        parsed = parse_attachment_filename(fname)
        if parsed:
            parsed_attachments.append(parsed)
        else:
            unparseable.append(fname)

    print(f"Parsed {len(parsed_attachments)} attachment filenames, {len(unparseable)} unparseable")

    # Match attachments to emails
    matches = []
    unmatched_attachments = []
    match_details = []

    for att in parsed_attachments:
        att_date = att["date"]
        att_subject = extract_subject_from_attachment_rest(att["rest"])

        # Look for emails within +/- 1 day of attachment date
        candidates = []
        for delta in range(0, 3):
            for d in [att_date - timedelta(days=delta), att_date + timedelta(days=delta)]:
                candidates.extend(email_by_date.get(d, []))

        # Remove duplicates (by index)
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c["index"] not in seen:
                seen.add(c["index"])
                unique_candidates.append(c)

        # Score candidates
        best_score = 0.0
        best_match = None
        scored = []

        for cand in unique_candidates:
            score = fuzzy_match_score(att_subject, cand["subject"])
            scored.append((score, cand))
            if score > best_score:
                best_score = score
                best_match = cand

        if best_match and best_score >= args.min_score:
            matches.append({
                "attachment": att["filename"],
                "email_index": best_match["index"],
                "email_filename": best_match["filename"],
                "email_subject": best_match["subject"],
                "email_date": best_match["date"],
                "score": round(best_score, 3),
            })
            match_details.append({
                "attachment": att["filename"],
                "matched_email": best_match["filename"],
                "score": round(best_score, 3),
                "attachment_date": att["date_str"],
                "email_date": best_match["date"],
            })
        else:
            unmatched_attachments.append({
                "attachment": att["filename"],
                "date": att["date_str"],
                "best_score": round(best_score, 3) if best_match else 0.0,
                "best_candidate": best_match["subject"] if best_match else None,
            })

    # Find emails with no matched attachment
    matched_email_indices = {m["email_index"] for m in matches}
    unmatched_emails = [
        {"index": rec["index"], "filename": rec["filename"], "subject": rec["subject"]}
        for rec in email_index
        if rec["index"] not in matched_email_indices
    ]

    print(f"\nMatching results:")
    print(f"  Matched pairs: {len(matches)}")
    print(f"  Unmatched attachments: {len(unmatched_attachments)}")
    print(f"  Emails with no attachment: {len(unmatched_emails)}")

    # Create ground-truth directory with symlinks
    gt_dir = corpus_dir / "ground-truth"
    gt_dir.mkdir(parents=True, exist_ok=True)

    for m in matches:
        src = attach_dir / m["attachment"]
        dst = gt_dir / m["attachment"]
        if not dst.exists():
            try:
                os.symlink(src.resolve(), dst)
            except OSError:
                # Fall back to relative path if absolute fails
                pass

    print(f"Created {len(matches)} symlinks in {gt_dir}")

    # Build manifest
    manifest = {
        "generated": datetime.now().isoformat(),
        "source_data": str(data_dir),
        "matches": matches,
        "unmatched_attachments": unmatched_attachments,
        "unparseable_filenames": unparseable,
        "summary": {
            "total_attachments": len(attachments),
            "parsed_attachments": len(parsed_attachments),
            "matched_pairs": len(matches),
            "unmatched_attachments": len(unmatched_attachments),
            "unmatched_emails": len(unmatched_emails),
            "total_emails": len(email_index),
        },
    }

    manifest_path = corpus_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest to {manifest_path}")

    # Update stats
    stats_path = corpus_dir / "stats.json"
    if stats_path.exists():
        with open(stats_path) as f:
            stats = json.load(f)
    else:
        stats = {}

    stats.update({
        "total_emails": len(email_index),
        "total_attachments": len(attachments),
        "matched_pairs": len(matches),
        "unmatched_attachments": len(unmatched_attachments),
        "unmatched_emails": len(unmatched_emails),
        "unparseable_filenames": len(unparseable),
        "match_score_distribution": {
            ">=0.8": len([m for m in matches if m["score"] >= 0.8]),
            "0.5-0.8": len([m for m in matches if 0.5 <= m["score"] < 0.8]),
            "0.3-0.5": len([m for m in matches if 0.3 <= m["score"] < 0.5]),
        },
    })

    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Updated stats at {stats_path}")


if __name__ == "__main__":
    main()
