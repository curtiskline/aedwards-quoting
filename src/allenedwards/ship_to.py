"""Canonical ship-to address shape shared by the parser, DB writer, web app and PDF.

Two incompatible dict shapes were in circulation: the RFQ path stored the ShipTo
dataclass verbatim (``company``/``attention``/``street``) while the web editor and
the PDF path read ``address_line1``/``address_line2``. A quote written by one path
and read by the other silently lost its street line, and the web editor's autosave
then wrote the blank back. :func:`normalize_ship_to` collapses both shapes into one.
"""

from __future__ import annotations

from typing import Any

SHIP_TO_KEYS = (
    "company",
    "attention",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "country",
)

# Countries we can compute a domestic distance for. Everything else must not
# reach the ZIP-centroid lookup: foreign postcodes collide with real US ZIPs
# (Malaysian 40160 is also Vine Grove, KY) and would yield a garbage charge.
DOMESTIC_COUNTRY_NAMES = {
    "",
    "us",
    "usa",
    "u.s",
    "u.s.a",
    "united states",
    "united states of america",
}


def _text(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    # Values round-tripped through str() can arrive as the literal "None"/"null".
    if text.lower() in ("none", "null"):
        return ""
    return text


def normalize_ship_to(raw: Any) -> dict[str, str] | None:
    """Return ``raw`` as the canonical ship-to dict, or None when it holds nothing.

    Accepts either shape. The legacy ``street`` key maps to ``address_line1``.
    """
    if not isinstance(raw, dict):
        return None

    normalized = {
        "company": _text(raw.get("company")),
        "attention": _text(raw.get("attention")),
        "address_line1": _text(raw.get("address_line1")) or _text(raw.get("street")),
        "address_line2": _text(raw.get("address_line2")),
        "city": _text(raw.get("city")),
        "state": _text(raw.get("state")),
        "postal_code": _text(raw.get("postal_code")),
        "country": _text(raw.get("country")),
    }
    if not any(normalized.values()):
        return None
    return normalized


def is_domestic_ship_to(ship_to: Any) -> bool:
    """True when the address has no country or names the United States."""
    normalized = normalize_ship_to(ship_to)
    if normalized is None:
        return False
    return normalized["country"].lower().strip(" .") in DOMESTIC_COUNTRY_NAMES
