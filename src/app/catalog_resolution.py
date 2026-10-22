"""Deterministic Product Catalog resolution for quote line identities.

The LLM may extract a candidate part number, but it never decides whether that
candidate is a real product.  This module resolves exact catalog identities and
records the outcome on the quote line.  Under the default flag-only policy,
pricing stays untouched when no match exists; optional hard enforcement moves
the line onto the existing $0/TBD path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from flask import current_app, has_app_context
from sqlalchemy import func

from .extensions import db
from .models import ProductCatalog, QuoteLineItem

ACTIVE = "active"
INACTIVE = "inactive"
UNMATCHED = "unmatched"
AMBIGUOUS = "ambiguous"
TYPE_MISMATCH = "type_mismatch"
UNCHECKED = "unchecked"
NOT_APPLICABLE = "not_applicable"

MATCHED_STATUSES = frozenset({ACTIVE, INACTIVE})
# Historical lines are intentionally nullable/unchecked and are not newly
# blocked by this rollout. Any new intake/editor write receives a definitive
# status and therefore cannot hide behind UNCHECKED.
REVIEW_STATUSES = frozenset({UNMATCHED, AMBIGUOUS, TYPE_MISMATCH})


@dataclass(frozen=True)
class CatalogResolution:
    row: ProductCatalog | None
    status: str
    matched_by: str | None = None


def _normalized(value: str | None) -> str:
    return str(value or "").strip().casefold()


# A catalog range description names its priced span like "30-38in"; the rewrite
# keeps the catalog's own unit formatting by replacing only the numeric span and
# leaving the "in" (or " in") that follows untouched.
_SIZE_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)(?=\s*in\b)")


def echo_requested_size(catalog_desc: str, diameter: float) -> str:
    """Replace a catalog range token with the size the customer asked for.

    "Geotextile Bag Weight 30-38in Pipe MAX 9K Fill (30/pallet)" with a 36" ask
    becomes "... 36in Pipe ...". Only a range that actually contains the
    requested size is rewritten; anything else is left alone.
    """

    def _substitute(match: re.Match[str]) -> str:
        low, high = float(match.group(1)), float(match.group(2))
        if low <= diameter <= high:
            return f"{diameter:g}"
        return match.group(0)

    return _SIZE_RANGE_RE.sub(_substitute, catalog_desc)


def _decimal(value: object | None) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _resolve_candidates(
    rows: list[ProductCatalog], product_type: str | None, matched_by: str
) -> CatalogResolution:
    if not rows:
        return CatalogResolution(None, UNMATCHED)
    if len(rows) == 1:
        row = rows[0]
        # Never attach an identity priced under a different product type. A
        # reviewer must resolve that conflict rather than blessing a plausible
        # part number paired with the wrong pricing branch.
        if (
            _normalized(row.product_type)
            and _normalized(product_type)
            and _normalized(row.product_type) != _normalized(product_type)
        ):
            return CatalogResolution(None, TYPE_MISMATCH, matched_by)
        return CatalogResolution(row, ACTIVE if row.is_active else INACTIVE, matched_by)

    normalized_type = _normalized(product_type)
    compatible = [
        row
        for row in rows
        if not _normalized(row.product_type) or _normalized(row.product_type) == normalized_type
    ]
    if len(compatible) == 1:
        row = compatible[0]
        return CatalogResolution(row, ACTIVE if row.is_active else INACTIVE, matched_by)
    if len(compatible) > 1:
        return CatalogResolution(None, AMBIGUOUS, matched_by)
    return CatalogResolution(None, TYPE_MISMATCH if normalized_type else AMBIGUOUS, matched_by)


def resolve_catalog_product(
    part_number: str | None,
    description: str | None,
    product_type: str | None,
    specs: dict | None = None,
) -> CatalogResolution:
    """Resolve one unique catalog row, preferring exact active identity.

    Exact part number/description matches come first.  An active dimensional
    range is the fallback for legitimate product families that intentionally
    have no per-size SKU.  Inactive identities remain detectable for audit and
    human review, but never beat an active match at quote time.
    """
    inactive_results: list[CatalogResolution] = []

    def resolve_exact(rows: list[ProductCatalog], matched_by: str) -> CatalogResolution | None:
        active = _resolve_candidates([row for row in rows if row.is_active], product_type, matched_by)
        if active.status != UNMATCHED:
            return active
        inactive = _resolve_candidates(
            [row for row in rows if not row.is_active], product_type, matched_by
        )
        if inactive.status != UNMATCHED:
            inactive_results.append(inactive)
        return None

    normalized_part = _normalized(part_number)
    if normalized_part:
        with db.session.no_autoflush:
            rows = (
                db.session.query(ProductCatalog)
                .filter(func.lower(func.trim(ProductCatalog.part_number)) == normalized_part)
                .order_by(ProductCatalog.id.asc())
                .all()
            )
        result = resolve_exact(rows, "part_number")
        if result is not None:
            return result

    normalized_description = _normalized(description)
    if normalized_description:
        with db.session.no_autoflush:
            rows = (
                db.session.query(ProductCatalog)
                .filter(func.lower(func.trim(ProductCatalog.description)) == normalized_description)
                .order_by(ProductCatalog.id.asc())
                .all()
            )
        result = resolve_exact(rows, "description")
        if result is not None:
            return result

    diameter = _decimal((specs or {}).get("diameter"))
    wall_thickness = _decimal((specs or {}).get("wall_thickness"))
    normalized_type = _normalized(product_type)
    if diameter is not None and normalized_type:
        with db.session.no_autoflush:
            rows = (
                db.session.query(ProductCatalog)
                .filter(
                    func.lower(func.trim(ProductCatalog.product_type)) == normalized_type,
                    ProductCatalog.diameter_min.isnot(None),
                    ProductCatalog.diameter_max.isnot(None),
                    ProductCatalog.diameter_min <= diameter,
                    ProductCatalog.diameter_max >= diameter,
                )
                .order_by(ProductCatalog.id.asc())
                .all()
            )

        def dimensions_match(row: ProductCatalog) -> bool:
            thickness_min = _decimal(row.wall_thickness_min)
            thickness_max = _decimal(row.wall_thickness_max)
            if thickness_min is None and thickness_max is None:
                return True
            return (
                wall_thickness is not None
                and thickness_min is not None
                and thickness_max is not None
                and thickness_min <= wall_thickness <= thickness_max
            )

        rows = [row for row in rows if dimensions_match(row)]
        active_range = _resolve_candidates(
            [row for row in rows if row.is_active], product_type, "dimensional_range"
        )
        if active_range.status != UNMATCHED:
            return active_range
        inactive_range = _resolve_candidates(
            [row for row in rows if not row.is_active], product_type, "dimensional_range"
        )
        if inactive_range.status != UNMATCHED:
            inactive_results.append(inactive_range)

    if inactive_results:
        return inactive_results[0]

    return CatalogResolution(None, UNMATCHED)


def _frozen_description(item: QuoteLineItem, row: ProductCatalog) -> str:
    """Description to freeze onto a line matched to ``row``.

    Exact (non-range) rows freeze their canonical text verbatim. A range row
    covers many sizes, and Chip wants the customer-facing description to state
    the size the customer asked for, never the range — only the part number
    keeps the range. When the canonical text carries a range token ("30-38in")
    it is rewritten to the requested diameter; when it does not (minimal rows
    like "GTW 30-36") the line's existing size-stating description is kept
    rather than clobbered with range-shaped text.
    """
    canonical = row.description
    if row.diameter_min is None or row.diameter_max is None:
        return canonical
    diameter = _decimal((item.specs_json or {}).get("diameter"))
    if diameter is None:
        return canonical
    echoed = echo_requested_size(canonical or "", float(diameter))
    if echoed != canonical:
        return echoed
    existing = str(item.description or "").strip()
    return existing or canonical


def apply_catalog_identity(
    item: QuoteLineItem,
    *,
    candidate_part_number: str | None = None,
    candidate_description: str | None = None,
    candidate_product_type: str | None = None,
    enforce_policy: bool = True,
) -> CatalogResolution:
    """Resolve and persist catalog identity, freezing canonical display text."""
    if item.product_type in {"note", "shipping", "on_site_fill"}:
        item.catalog_id = None
        item.catalog_match_status = NOT_APPLICABLE
        return CatalogResolution(None, NOT_APPLICABLE)

    result = resolve_catalog_product(
        item.part_number if candidate_part_number is None else candidate_part_number,
        item.description if candidate_description is None else candidate_description,
        item.product_type if candidate_product_type is None else candidate_product_type,
        dict(item.specs_json or {}),
    )
    item.catalog_id = result.row.id if result.row is not None else None
    item.catalog_match_status = result.status
    if result.row is not None:
        # Quote rows remain point-in-time snapshots, but the catalog supplies
        # their identity and canonical text at the moment of resolution.
        item.part_number = result.row.part_number
        item.description = _frozen_description(item, result.row)
        if result.row.product_type:
            item.product_type = result.row.product_type
    elif (
        enforce_policy
        and result.status in REVIEW_STATUSES
        and has_app_context()
        and current_app.config.get("CATALOG_HARD_TBD_ENFORCEMENT", False)
    ):
        item.part_number = "TBD"
        item.unit_price = 0
        item.line_total = 0
        specs = dict(item.specs_json or {})
        note = str(specs.get("notes") or "").strip()
        catalog_note = "Pricing TBD — product is not in the active Product Catalog"
        if catalog_note not in note:
            specs["notes"] = f"{note}; {catalog_note}".strip("; ")
        item.specs_json = specs
    return result


def catalog_status_needs_review(status: str | None) -> bool:
    """Return whether current policy requires a human to review this status."""
    normalized = status or UNCHECKED
    if normalized in REVIEW_STATUSES:
        return True
    if normalized == INACTIVE:
        allow_inactive = False
        if has_app_context():
            allow_inactive = bool(current_app.config.get("CATALOG_ALLOW_INACTIVE_MATCHES", False))
        return not allow_inactive
    return False


def catalog_status_message(status: str | None) -> str | None:
    return {
        ACTIVE: "Matched to active Product Catalog item",
        INACTIVE: "Matched to inactive Product Catalog item — verify this product is still offered",
        UNMATCHED: "Not found in Product Catalog — verify this product before sending",
        AMBIGUOUS: "Multiple Product Catalog items match — select the correct product",
        TYPE_MISMATCH: "Part number exists in Product Catalog under a different product type",
        UNCHECKED: "Product Catalog match not checked (historical line)",
    }.get(status or UNCHECKED)
