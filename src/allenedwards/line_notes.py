"""Audience split for line-item notes.

Pricing writes a per-line note whenever it assumed something: a spec that fell
through to a default, a metric length it converted, a quantity it rounded to a
billing unit. ``db_writer`` persists that text in ``specs_json['notes']`` and the
quote editor renders it verbatim, which is what Chip needs — he is the reviewer
and he wants the raw provenance, including the parts that ask him to go confirm
something.

The customer PDF is a different audience. "interim conversion, confirm with
Chip" is a working note about an assumption we have not validated yet; it must
never reach Azimuth. So the PDF gets a curated subset, produced here.

The mapping is an ALLOWLIST and it fails closed. A clause only prints to a
customer if a rule below recognizes it and states the customer wording; every
other clause is treated as internal and dropped. That way a note string added to
pricing later is invisible on the PDF until someone deliberately writes its
customer phrasing, rather than leaking internal text by default.

``test_line_notes.py`` pins every note string pricing currently produces against
these rules, so rewording a note in pricing.py fails a test instead of silently
dropping the customer line.
"""

from __future__ import annotations

import re
from collections.abc import Callable


def _trim_number(value: str) -> str:
    """Render '10.0' as '10' and leave '9.843' alone."""
    text = value.strip()
    if "." not in text:
        return text
    text = text.rstrip("0").rstrip(".")
    return text or "0"


# (pattern, builder). ``builder`` receives the regex match and returns the text a
# customer sees, or None to keep the clause internal. Patterns are matched
# case-insensitively against a single clause, already stripped.
_RULES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str | None]]] = [
    # allenedwards.pricing._apply_item_defaults
    (
        re.compile(r"^grade defaulted to GR(\d+)$", re.I),
        lambda m: f"Priced at Grade {m.group(1)}",
    ),
    (
        re.compile(r"^wall thickness defaulted to (.+?)(?: for part number)?$", re.I),
        lambda m: f"Priced at {m.group(1)} wall",
    ),
    (
        re.compile(r"^length defaulted to ([\d.]+)\s*ft$", re.I),
        lambda m: f"Priced at {_trim_number(m.group(1))} ft length",
    ),
    # allenedwards.units.convert_length_to_ft (metric RFQs, task 330)
    (
        re.compile(r"^length (.+?) converted to ([\d.]+) ft, verify$", re.I),
        lambda m: f"Length {m.group(1)} quoted as {_trim_number(m.group(2))} ft",
    ),
    (
        re.compile(
            r"^length (.+?) \([\d.]+ ft\) quoted as standard ([\d.]+) ft piece$", re.I
        ),
        lambda m: f"Length {m.group(1)} quoted as a standard {_trim_number(m.group(2))} ft piece",
    ),
    # allenedwards.pricing._normalize_sleeve_footage
    (
        re.compile(r"^Requested (.+?) ft → (.+?) pc\(s\) of (.+?) ft$", re.I),
        lambda m: f"Requested {m.group(1)} ft quoted as {m.group(2)} pc(s) of {m.group(3)} ft",
    ),
    # allenedwards.pricing bundle rounding. The "Priced as N bundles (M pcs /
    # K ft)" clause is an internal packaging/billing-unit breakdown for standard
    # grayscale sleeves sold in bundles of 5. Chip flagged it leaking onto the
    # customer PDF (quote 126-111, task 458): the customer buys against their
    # requested footage and should not see the bundle-of-5 packaging math. It
    # matches nothing here on purpose so it stays in the editor only; the count
    # and price still reach the customer through the normal quantity/price
    # columns, not through this note.
    # allenedwards.pricing pallet rounding for bags
    (
        re.compile(
            r"^(\d+) pcs rounded to (\d+ pallets?) \((\d+) pcs\)$", re.I
        ),
        lambda m: f"Quantity rounded to {m.group(2)}, {m.group(3)} pcs",
    ),
    # allenedwards.pricing._backing_strip_packs (task 343). Only the billing
    # unit prints. The "N lf = M strips at 5 ft per strip (interim conversion,
    # confirm with Chip)" basis clause matches nothing here on purpose — it is
    # an unconfirmed assumption and stays in the editor only.
    (
        re.compile(
            r"^billed as (\d+ packs? of \d+)"
            r"(?: \(partial pack rounded up to (\d+) strips\))?$",
            re.I,
        ),
        lambda m: (
            f"Billed as {m.group(1)}"
            + (
                f" (partial pack rounded up to {m.group(2)} strips)"
                if m.group(2)
                else ""
            )
        ),
    ),
    # allenedwards.pricing.price_item
    (
        re.compile(r"^Quantity not specified, defaulted to (\d+)$", re.I),
        lambda m: f"Priced for quantity {m.group(1)}",
    ),
    # allenedwards.pricing._tbd_line_item
    (
        re.compile(r"^Pricing TBD, contact sales$", re.I),
        lambda m: "Pricing TBD, contact sales",
    ),
]

# The clause _tbd_line_item writes on a $0 line the engine could not price. It
# describes the PRICE, not the specs, so it is stale the moment the line has a
# real price — matched here so both the price-set path (app.routes) and the
# render-time guard (customer_note(priced=True)) recognize the same text.
_TBD_CLAUSE = re.compile(r"^Pricing TBD, contact sales$", re.I)


def is_tbd_clause(clause: str) -> bool:
    """Return whether one stripped clause is the needs-pricing TBD note."""
    return bool(_TBD_CLAUSE.match(clause.strip()))


def strip_tbd_clauses(notes: str | None) -> str | None:
    """Drop the TBD clause from a stored note string, keeping everything else."""
    kept = [clause for clause in split_clauses(notes) if not is_tbd_clause(clause)]
    return "; ".join(kept) or None


# Backstop over the rules above. A clause may be recognized and still be worded
# for Chip; if the customer text a rule produced still carries one of these, the
# rule is wrong and the clause is dropped rather than printed.
_INTERNAL_MARKERS = ("chip", "interim", "verify", "confirm", "tentative", "check with")


def split_clauses(notes: str | None) -> list[str]:
    """Split a stored note string into its individual clauses.

    Pricing joins clauses with "; ", the same separator
    ``routes._strip_stale_default_notes`` splits on.
    """
    if not notes:
        return []
    return [clause.strip() for clause in notes.split(";") if clause.strip()]


def customer_clause(clause: str) -> str | None:
    """Return the customer wording for one clause, or None if it is internal."""
    text = clause.strip()
    if not text:
        return None
    for pattern, builder in _RULES:
        match = pattern.match(text)
        if match:
            rendered = builder(match)
            if not rendered:
                return None
            lowered = rendered.lower()
            if any(marker in lowered for marker in _INTERNAL_MARKERS):
                return None
            return rendered
    return None


def customer_note(notes: str | None, *, priced: bool = False) -> str | None:
    """Return the customer-facing rendering of a stored note, or None.

    Unrecognized clauses are dropped. If nothing survives, the line prints no
    note at all rather than a partial or internal one.

    ``priced=True`` additionally drops the "Pricing TBD, contact sales" clause:
    that note only ever means "this line has no price", so on a line that now
    has one it is stale by definition (Chip-reported bug, quote 126-107). The
    stored note is normally cleared when the price is set, but old rows predate
    that — this guard keeps the stale text off the PDF regardless.
    """
    kept = [
        rendered
        for clause in split_clauses(notes)
        if not (priced and is_tbd_clause(clause))
        and (rendered := customer_clause(clause))
    ]
    return "; ".join(kept) or None
