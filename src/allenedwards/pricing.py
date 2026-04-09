"""Price calculation engine based on Allan Edwards pricing rules."""

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
import re
from time import monotonic
from typing import Any

from flask import has_app_context
from sqlalchemy import inspect

from .parser import ParsedItem, ParsedRFQ
from .pricing_catalog import (
    DEFAULT_BAG_PRICING,
    DEFAULT_GIRTH_WELD_PRICING,
    DEFAULT_OTHER_PRICING,
    DEFAULT_PRICE_PER_LB,
    DEFAULT_SERVICE_PRICES,
)

logger = logging.getLogger(__name__)

# Default values for missing item fields
DEFAULT_GRADE = 50
DEFAULT_LENGTH_SLEEVE = 40.0
DEFAULT_LENGTH_GIRTH_WELD = 6.0

# Price per pound lookup table
# wall_thickness -> (GR50 price, GR65 price)
PRICE_PER_LB: dict[str, tuple[Decimal, Decimal]] = DEFAULT_PRICE_PER_LB.copy()

# Girth weld pricing by diameter range
# (min_diameter, max_diameter, price_per_set)
GIRTH_WELD_PRICING = list(DEFAULT_GIRTH_WELD_PRICING)

# Bag pricing
# (pipe_size_min, pipe_size_max, part_number, pieces_per_pallet, price_per_bag)
BAG_PRICING = list(DEFAULT_BAG_PRICING)

# Other product pricing
OTHER_PRICING: dict[str, tuple[Decimal, str]] = DEFAULT_OTHER_PRICING.copy()

# Standard sleeve bundle sizing rule:
# up to 24" diameter and 10' length are sold as bundles of 5 pieces (50 LF total).
STANDARD_BUNDLE_MAX_DIAMETER = 24.0
STANDARD_BUNDLE_LENGTH_FT = 10.0
STANDARD_BUNDLE_PIECES = 5

# Service pricing
MILLING_PRICE = DEFAULT_SERVICE_PRICES["milling"]
PAINTING_PRICE = DEFAULT_SERVICE_PRICES["painting"]

# Standard sleeve diameter lookup from quoting-spreadsheet.xlsm ("Part Number Description" tab)
# Maps parsed decimal ID -> (part-number diameter code, display text for descriptions)
SLEEVE_DIAMETER_LOOKUP: dict[Decimal, tuple[str, str]] = {
    Decimal("4.5"): ("4.12", "4-1/2"),
    Decimal("5.25"): ("5.14", "5-1/4"),
    Decimal("6.625"): ("6.58", "6-5/8"),
    Decimal("7.375"): ("7.38", "7-3/8"),
    Decimal("8.25"): ("8.14", "8-1/4"),
    Decimal("8.625"): ("8.58", "8-5/8"),
    Decimal("9.375"): ("9.38", "9-3/8"),
    Decimal("10.25"): ("10.14", "10-1/4"),
    Decimal("10.75"): ("10.34", "10-3/4"),
    Decimal("11.5"): ("11.12", "11-1/2"),
    Decimal("12.25"): ("12.14", "12-1/4"),
    Decimal("12.75"): ("12.34", "12-3/4"),
    Decimal("14"): ("14", "14"),
    Decimal("16"): ("16", "16"),
    Decimal("18"): ("18", "18"),
    Decimal("20"): ("20", "20"),
    Decimal("22"): ("22", "22"),
    Decimal("24"): ("24", "24"),
    Decimal("26"): ("26", "26"),
    Decimal("28"): ("28", "28"),
    Decimal("30"): ("30", "30"),
    Decimal("32"): ("32", "32"),
    Decimal("36"): ("36", "36"),
    Decimal("38"): ("38", "38"),
    Decimal("40"): ("40", "40"),
    Decimal("42"): ("42", "42"),
    Decimal("44"): ("44", "44"),
    Decimal("46"): ("46", "46"),
    Decimal("48"): ("48", "48"),
}

DIAMETER_MATCH_TOLERANCE = Decimal("0.001")

# Nominal pipe size to actual OD mapping (NPS -> actual OD in inches).
# This is used when customers request nominal sizes like "8 inch".
NOMINAL_OD_MAP: dict[Decimal, Decimal] = {
    Decimal("2"): Decimal("2.375"),
    Decimal("2.5"): Decimal("2.875"),
    Decimal("3"): Decimal("3.5"),
    Decimal("3.5"): Decimal("4"),
    Decimal("4"): Decimal("4.5"),
    Decimal("5"): Decimal("5.563"),
    Decimal("6"): Decimal("6.625"),
    Decimal("8"): Decimal("8.625"),
    Decimal("10"): Decimal("10.75"),
    Decimal("12"): Decimal("12.75"),
    Decimal("14"): Decimal("14"),
    Decimal("16"): Decimal("16"),
    Decimal("18"): Decimal("18"),
    Decimal("20"): Decimal("20"),
    Decimal("22"): Decimal("22"),
    Decimal("24"): Decimal("24"),
    Decimal("26"): Decimal("26"),
    Decimal("28"): Decimal("28"),
    Decimal("30"): Decimal("30"),
    Decimal("32"): Decimal("32"),
    Decimal("34"): Decimal("34"),
    Decimal("36"): Decimal("36"),
    Decimal("38"): Decimal("38"),
    Decimal("40"): Decimal("40"),
    Decimal("42"): Decimal("42"),
    Decimal("44"): Decimal("44"),
    Decimal("46"): Decimal("46"),
    Decimal("48"): Decimal("48"),
}

WALL_THICKNESS_CODE_MAP: dict[float, str] = {
    0.1875: "316",
    0.25: "14",
    0.3125: "516",
    0.375: "38",
    0.5: "12",
    0.625: "58",
    0.75: "34",
    0.875: "78",
    1.0: "1",
}


def _format_decimal_inches(value: float | Decimal) -> str:
    decimal_value = Decimal(str(value))
    return f"{decimal_value:.3f}".rstrip("0").rstrip(".")


def normalize_nominal_od(diameter: float) -> float:
    """Convert nominal diameter to actual OD when there is an exact nominal match."""
    diameter_decimal = Decimal(str(diameter))
    for nominal_size, actual_od in NOMINAL_OD_MAP.items():
        if abs(diameter_decimal - nominal_size) <= DIAMETER_MATCH_TOLERANCE:
            return float(actual_od)
    return diameter


def calculate_oversleeve_od(pipe_diameter: float, wall_thickness: float) -> float:
    """Compute oversleeve ID from pipe OD + 2x wall thickness."""
    actual_pipe_od = Decimal(str(normalize_nominal_od(pipe_diameter)))
    wt = Decimal(str(wall_thickness))
    oversleeve_od = actual_pipe_od + (wt * Decimal("2"))
    return float(oversleeve_od.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def _part_number_diameter_code(diameter: float) -> str:
    diameter_decimal = Decimal(str(diameter))
    for standard_dia, (code, _) in SLEEVE_DIAMETER_LOOKUP.items():
        if abs(diameter_decimal - standard_dia) <= DIAMETER_MATCH_TOLERANCE:
            return code
    return _format_decimal_inches(diameter_decimal)


def generate_part_number(
    part_type: str,
    diameter: float,
    wall_thickness: float,
    grade: int,
    milling: bool = False,
    painting: bool = False,
) -> str:
    """Generate sleeve-style part numbers per spec Section 3.2."""
    type_code_map = {
        "sleeve": "S",
        "oversleeve": "S",
        "girth_weld": "G",
        "compression": "CS",
    }
    type_code = type_code_map.get(part_type, part_type.upper())
    wt_code = WALL_THICKNESS_CODE_MAP.get(wall_thickness, str(wall_thickness).replace(".", ""))
    dia_code = _part_number_diameter_code(diameter)

    part_num = f"{type_code}-{dia_code}-{wt_code}-{grade}"
    if milling:
        part_num += "-M"
    if painting:
        part_num += "-P"
    return part_num


@dataclass
class QuoteLineItem:
    """A calculated quote line item."""

    sort_order: int
    product_type: str
    part_number: str
    description: str
    quantity: int
    unit_price: Decimal
    total: Decimal
    is_note: bool = False

    # Optional details
    weight_per_ft: Decimal | None = None
    price_per_lb: Decimal | None = None
    notes: str | None = None


@dataclass
class Quote:
    """A complete quote with all line items and totals."""

    quote_number: str
    customer_name: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    ship_to: dict[str, Any] | None
    line_items: list[QuoteLineItem]
    subtotal: Decimal
    shipping_amount: Decimal | None
    tax_amount: Decimal
    total: Decimal
    notes: str | None
    po_number: str | None = None

    # Project line reference (e.g., "XB403CL Line") for multi-quote emails
    project_line: str | None = None

    # Metadata
    sales_rep: str = "Jamee Hamilton"
    payment_terms: str = "Net 30"
    shipping_terms: str = "Prepay & Add"
    shipping_method: str = "Flatbed"

    # Requested by (from RFQ)
    requested_by_name: str | None = None
    requested_by_email: str | None = None
    requested_by_phone: str | None = None


@dataclass
class PricingSnapshot:
    price_per_lb: dict[str, tuple[Decimal, Decimal]]
    girth_weld_pricing: list[tuple[int, int, Decimal]]
    bag_pricing: list[tuple[int, int, str, int, Decimal]]
    other_pricing: dict[str, tuple[Decimal, str]]
    flat_pricing: dict[str, Decimal]


_PRICING_CACHE: PricingSnapshot | None = None
_PRICING_CACHE_EXPIRES_AT = 0.0
_PRICING_CACHE_TTL_SECONDS = 5.0


def _default_pricing_snapshot() -> PricingSnapshot:
    return PricingSnapshot(
        price_per_lb=PRICE_PER_LB.copy(),
        girth_weld_pricing=list(GIRTH_WELD_PRICING),
        bag_pricing=list(BAG_PRICING),
        other_pricing=OTHER_PRICING.copy(),
        flat_pricing={"milling": MILLING_PRICE, "painting": PAINTING_PRICE},
    )


def _load_pricing_rows_from_db() -> list[tuple[str, dict[str, Any], Decimal]] | None:
    if not has_app_context():
        return None

    try:
        from app.extensions import db
        from app.models import PricingTable

        if not inspect(db.engine).has_table("pricing_table"):
            return None

        rows = db.session.query(PricingTable).all()
        if not rows:
            return None

        return [
            (
                row.product_type,
                dict(row.key_fields or {}),
                Decimal(str(row.price)),
            )
            for row in rows
        ]
    except Exception:
        logger.exception("Pricing DB lookup failed; using fallback pricing constants")
        return None


def _build_pricing_snapshot() -> PricingSnapshot:
    snapshot = _default_pricing_snapshot()
    rows = _load_pricing_rows_from_db()
    if rows is None:
        return snapshot

    for product_type, key_fields, price in rows:
        if product_type == "sleeve":
            wt = str(key_fields.get("wall_thickness", ""))
            grade = int(key_fields.get("grade", 50))
            base = snapshot.price_per_lb.get(wt, (Decimal("0"), Decimal("0")))
            if grade == 65:
                snapshot.price_per_lb[wt] = (base[0], price)
            else:
                snapshot.price_per_lb[wt] = (price, base[1])

        elif product_type == "girth_weld":
            min_diameter = int(key_fields.get("min_diameter", 0))
            max_diameter = int(key_fields.get("max_diameter", 0))
            match_index = next(
                (
                    i
                    for i, (min_dia, max_dia, _) in enumerate(snapshot.girth_weld_pricing)
                    if min_dia == min_diameter and max_dia == max_diameter
                ),
                None,
            )
            if match_index is not None:
                snapshot.girth_weld_pricing[match_index] = (min_diameter, max_diameter, price)
            else:
                snapshot.girth_weld_pricing.append((min_diameter, max_diameter, price))

        elif product_type == "bag":
            min_size = int(key_fields.get("pipe_size_min", 0))
            max_size = int(key_fields.get("pipe_size_max", 0))
            part_number = str(key_fields.get("part_number", "GTW TBD"))
            pieces_per_pallet = int(key_fields.get("pieces_per_pallet", 0))
            match_index = next(
                (
                    i
                    for i, (min_dia, max_dia, _, _, _) in enumerate(snapshot.bag_pricing)
                    if min_dia == min_size and max_dia == max_size
                ),
                None,
            )
            updated = (min_size, max_size, part_number, pieces_per_pallet, price)
            if match_index is not None:
                snapshot.bag_pricing[match_index] = updated
            else:
                snapshot.bag_pricing.append(updated)

        elif product_type in {"compression", "omegawrap", "accessory", "service", "flat"}:
            key = str(key_fields.get("key", ""))
            if not key:
                continue
            unit = str(key_fields.get("unit", "flat"))
            if key in {"milling", "painting"}:
                snapshot.flat_pricing[key] = price
            else:
                snapshot.other_pricing[key] = (price, unit)

    snapshot.girth_weld_pricing.sort(key=lambda row: row[0])
    snapshot.bag_pricing.sort(key=lambda row: row[0])
    return snapshot


def _get_pricing_snapshot() -> PricingSnapshot:
    global _PRICING_CACHE, _PRICING_CACHE_EXPIRES_AT
    now = monotonic()
    if _PRICING_CACHE is None or now >= _PRICING_CACHE_EXPIRES_AT:
        _PRICING_CACHE = _build_pricing_snapshot()
        _PRICING_CACHE_EXPIRES_AT = now + _PRICING_CACHE_TTL_SECONDS
    return _PRICING_CACHE


def _clear_pricing_cache() -> None:
    global _PRICING_CACHE, _PRICING_CACHE_EXPIRES_AT
    _PRICING_CACHE = None
    _PRICING_CACHE_EXPIRES_AT = 0.0


def calculate_sleeve_weight_per_ft(diameter: float, wall_thickness: float) -> Decimal:
    """Calculate weight per foot for a sleeve.

    Formula: weight_per_ft = 10.69 * ((sleeve_id + wall_thickness) * wall_thickness) / 2
    """
    sleeve_id = Decimal(str(diameter))
    wt = Decimal(str(wall_thickness))
    weight = Decimal("10.69") * ((sleeve_id + wt) * wt) / Decimal("2")
    return weight.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_price_per_lb(wall_thickness: float, grade: int) -> Decimal:
    """Get price per pound for given wall thickness and grade."""
    price_per_lb = _get_pricing_snapshot().price_per_lb
    wt_key = str(wall_thickness)

    # Find the matching wall thickness tier
    if wt_key in price_per_lb:
        prices = price_per_lb[wt_key]
    elif wall_thickness >= 0.5:
        prices = price_per_lb["0.5"]
    elif wall_thickness >= 0.375:
        prices = price_per_lb["0.375"]
    elif wall_thickness >= 0.3125:
        prices = price_per_lb["0.3125"]
    else:
        prices = price_per_lb["0.25"]

    return prices[1] if grade == 65 else prices[0]


def calculate_sleeve_price(
    diameter: float,
    wall_thickness: float,
    grade: int,
    length_ft: float,
    milling: bool = False,
    painting: bool = False,
) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate unit price for a sleeve.

    Returns:
        Tuple of (unit_price, weight_per_ft, price_per_lb)
    """
    weight_per_ft = calculate_sleeve_weight_per_ft(diameter, wall_thickness)
    price_per_lb = get_price_per_lb(wall_thickness, grade)
    length = Decimal(str(length_ft))

    # Base price: price_per_lb * weight_per_ft * length
    unit_price = price_per_lb * weight_per_ft * length

    # Add services
    if milling:
        unit_price += _get_pricing_snapshot().flat_pricing["milling"]
    if painting:
        unit_price += _get_pricing_snapshot().flat_pricing["painting"]

    return (
        unit_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        weight_per_ft,
        price_per_lb,
    )


def get_girth_weld_price(diameter: float) -> Decimal | None:
    """Get girth weld sleeve price based on diameter tier.

    Girth weld sleeves are priced per SET, not per pound.

    Returns:
        Price per set, or None if diameter is outside supported ranges.
    """
    for min_dia, max_dia, price in _get_pricing_snapshot().girth_weld_pricing:
        if min_dia <= diameter <= max_dia:
            return price
    return None


def generate_girth_weld_part_number(
    diameter: float,
    wall_thickness: float,
    grade: int,
    length_ft: float,
) -> str:
    """Generate a part number for a girth weld sleeve.

    Format: G-{sleeve_id}-{wt_code}-{grade}[-M][-P]
    """
    del length_ft
    actual_od = normalize_nominal_od(diameter)
    return generate_part_number("girth_weld", actual_od, wall_thickness, grade)


def generate_girth_weld_description(
    diameter: float,
    wall_thickness: float,
    grade: int,
    length_ft: float,
) -> str:
    """Generate a description for a girth weld sleeve."""
    # Format wall thickness as fraction
    wt_fractions = {
        0.25: '1/4"',
        0.3125: '5/16"',
        0.375: '3/8"',
        0.5: '1/2"',
        0.625: '5/8"',
        0.75: '3/4"',
    }
    wt_str = wt_fractions.get(wall_thickness, f'{wall_thickness}"')

    actual_od = normalize_nominal_od(diameter)
    return f'Girth Weld Sleeve, {actual_od}" ID, {wt_str} w/t, A572 GR{grade}, {length_ft}\' long'


def generate_sleeve_part_number(
    diameter: float,
    wall_thickness: float,
    grade: int,
    length_ft: float,
    milling: bool = False,
    painting: bool = False,
) -> str:
    """Generate a part number for a sleeve.

    Format: S-{sleeve_id}-{wt_code}-{grade}[-M][-P]
    """
    del length_ft
    actual_od = normalize_nominal_od(diameter)
    return generate_part_number(
        "sleeve",
        actual_od,
        wall_thickness,
        grade,
        milling=milling,
        painting=painting,
    )


def generate_sleeve_description(
    diameter: float,
    wall_thickness: float,
    grade: int,
    length_ft: float,
    milling: bool = False,
    painting: bool = False,
) -> str:
    """Generate a description for a sleeve."""
    # Format wall thickness as fraction
    wt_fractions = {
        0.25: '1/4"',
        0.3125: '5/16"',
        0.375: '3/8"',
        0.5: '1/2"',
        0.625: '5/8"',
        0.75: '3/4"',
    }
    wt_str = wt_fractions.get(wall_thickness, f'{wall_thickness}"')

    actual_od = normalize_nominal_od(diameter)
    diameter_decimal = Decimal(str(actual_od))
    dia_str = ""
    for standard_dia, (_, display) in SLEEVE_DIAMETER_LOOKUP.items():
        if abs(diameter_decimal - standard_dia) <= DIAMETER_MATCH_TOLERANCE:
            dia_str = display
            break
    if not dia_str:
        if diameter == int(diameter):
            dia_str = str(int(diameter))
        else:
            dia_str = f"{diameter:.3f}".rstrip("0").rstrip(".")

    if length_ft == int(length_ft):
        len_str = str(int(length_ft))
    else:
        len_str = f"{length_ft:.1f}".rstrip("0").rstrip(".")

    desc = f'reg half sole, {dia_str}" ID, {wt_str} w/t, A572 GR{grade}, {len_str}\' long'

    services = []
    if milling:
        services.append("Milled")
    if painting:
        services.append("Painted")

    if services:
        desc += f" ({', '.join(services)})"

    return f"{desc}. Backing Strip Included."


def _is_standard_bundle_sleeve(item: ParsedItem) -> bool:
    """Return True when sleeve item uses the standard bundle sizing rule."""
    if item.product_type != "sleeve":
        return False
    if item.diameter is None or item.length_ft is None:
        return False
    return item.diameter <= STANDARD_BUNDLE_MAX_DIAMETER and item.length_ft == STANDARD_BUNDLE_LENGTH_FT


def _extract_bundle_count(text: str) -> int | None:
    """Extract explicit bundle count from item description text."""
    match = re.search(r"\b(\d+)\s*bundles?\b", text, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _quote_quantity_and_warning(item: ParsedItem) -> tuple[int, str | None]:
    """Resolve displayed quote quantity in pieces and optional warning text."""
    quote_quantity = item.quantity
    warning: str | None = None

    if not _is_standard_bundle_sleeve(item):
        return quote_quantity, warning

    bundle_count = _extract_bundle_count(item.description)
    if bundle_count is not None:
        expected_pieces = bundle_count * STANDARD_BUNDLE_PIECES
        if item.quantity == bundle_count:
            quote_quantity = expected_pieces
        elif item.quantity != expected_pieces:
            warning = (
                f"WARNING: Sleeve quantity {item.quantity} does not match "
                f"{bundle_count} bundle(s) ({expected_pieces} pcs expected)."
            )

    if quote_quantity % STANDARD_BUNDLE_PIECES != 0:
        size = int(item.diameter) if item.diameter == int(item.diameter) else item.diameter
        warning = (
            f'WARNING: Sleeve quantity {quote_quantity} for {size}" x 10\' must be a multiple of '
            f"{STANDARD_BUNDLE_PIECES} pcs (standard bundle size)."
        )

    return quote_quantity, warning


def generate_oversleeve_part_number(
    diameter: float,
    wall_thickness: float,
    grade: int,
    length_ft: float,
    milling: bool = False,
    painting: bool = False,
) -> str:
    """Generate a part number for an oversleeve.

    Oversleeves fit over the outside of carrier pipe + standard sleeve.
    Format: S-{oversleeve_id}-{wt_code}-{grade}[-M][-P]
    """
    del length_ft
    oversleeve_od = calculate_oversleeve_od(diameter, wall_thickness)
    return generate_part_number(
        "oversleeve",
        oversleeve_od,
        wall_thickness,
        grade,
        milling=milling,
        painting=painting,
    )


def generate_oversleeve_description(
    diameter: float,
    wall_thickness: float,
    grade: int,
    length_ft: float,
    milling: bool = False,
    painting: bool = False,
) -> str:
    """Generate a description for an oversleeve."""
    # Format wall thickness as fraction
    wt_fractions = {
        0.25: '1/4"',
        0.3125: '5/16"',
        0.375: '3/8"',
        0.5: '1/2"',
        0.625: '5/8"',
        0.75: '3/4"',
    }
    wt_str = wt_fractions.get(wall_thickness, f'{wall_thickness}"')

    oversleeve_od = calculate_oversleeve_od(diameter, wall_thickness)
    desc = f'Oversleeve, {oversleeve_od}" ID, {wt_str} w/t, A572 GR{grade}, {length_ft}\' long'

    services = []
    if milling:
        services.append("Milled")
    if painting:
        services.append("Painted")

    if services:
        desc += f" ({', '.join(services)})"

    return desc


def _apply_item_defaults(item: ParsedItem) -> tuple[ParsedItem, list[str]]:
    """Apply sensible defaults for missing grade/length_ft.

    Returns a (possibly modified) item and a list of notes about defaults applied.
    """
    notes: list[str] = []
    grade = item.grade
    length_ft = item.length_ft

    if grade is None:
        grade = DEFAULT_GRADE
        notes.append(f"grade defaulted to GR{DEFAULT_GRADE}")
        logger.warning(
            "Item '%s' missing grade — defaulting to GR%d",
            item.description or item.product_type,
            DEFAULT_GRADE,
        )

    if length_ft is None:
        if item.product_type == "girth_weld":
            length_ft = DEFAULT_LENGTH_GIRTH_WELD
        else:
            length_ft = DEFAULT_LENGTH_SLEEVE
        notes.append(f"length defaulted to {length_ft}ft")
        logger.warning(
            "Item '%s' missing length_ft — defaulting to %.0fft",
            item.description or item.product_type,
            length_ft,
        )

    if notes:
        # Return a copy with defaults applied
        from dataclasses import replace
        item = replace(item, grade=grade, length_ft=length_ft)

    return item, notes


def _lookup_bag_pricing(diameter: float) -> tuple[str, int, Decimal] | None:
    """Look up bag pricing by pipe diameter.

    Returns (part_number, pieces_per_pallet, price_per_bag) or None.
    """
    for min_dia, max_dia, part_number, pcs_per_pallet, price in _get_pricing_snapshot().bag_pricing:
        if min_dia <= diameter <= max_dia:
            return part_number, pcs_per_pallet, price
    return None


def _get_other_pricing(key: str) -> tuple[Decimal, str]:
    snapshot = _get_pricing_snapshot()
    return snapshot.other_pricing[key]


# Keyword maps for matching parsed descriptions to OTHER_PRICING keys
_OMEGAWRAP_KEYWORDS: dict[str, list[str]] = {
    "omegawrap_carbon": ["carbon"],
    "omegawrap_eglass": ["eglass", "e-glass", "e glass", "fiberglass"],
    "omegawrap_magnum": ["magnum"],
}

_ACCESSORY_KEYS: dict[str, list[str]] = {
    "bag_fill": ["bag fill", "fill"],
    "isolation_wrap": ["isolation", "iso wrap"],
    "resin": ["resin"],
    "putty": ["putty"],
    "porcupine_roller": ["porcupine", "roller"],
    "magnet_set": ["magnet"],
    "accessory_kit": ["accessory kit", "kit"],
    "plastic_wrap_large": ["plastic wrap", "shrink wrap"],
    "pipejacks": ["pipe jack", "pipejack"],
    "pipejacks_large": ["large pipe jack", "large pipejack"],
}

_SERVICE_KEYS: dict[str, list[str]] = {
    "supervisor": ["supervisor"],
    "trainer_torch": ["trainer torch", "torch training"],
    "kickoff_training": ["kickoff", "kick-off", "kick off"],
    "training_package": ["training"],
}


def _match_omegawrap_key(description: str) -> str | None:
    """Match an omegawrap description to the correct OTHER_PRICING key."""
    desc_lower = (description or "").lower()
    for key, keywords in _OMEGAWRAP_KEYWORDS.items():
        for kw in keywords:
            if kw in desc_lower:
                return key
    # Default to carbon if no variant specified
    return "omegawrap_carbon"


def _match_other_pricing_key(description: str, keyword_map: dict[str, list[str]]) -> str | None:
    """Match a description against a keyword map to find the right pricing key."""
    desc_lower = (description or "").lower()
    for key, keywords in keyword_map.items():
        for kw in keywords:
            if kw in desc_lower:
                return key
    return None


def _tbd_line_item(item: ParsedItem, sort_order: int) -> QuoteLineItem:
    """Create a $0.00 line item with TBD note for unpriceable items."""
    return QuoteLineItem(
        sort_order=sort_order,
        product_type=item.product_type,
        part_number="TBD",
        description=item.description or item.product_type,
        quantity=item.quantity,
        unit_price=Decimal("0.00"),
        total=Decimal("0.00"),
        notes="Pricing TBD — contact sales",
    )


def price_item(item: ParsedItem, sort_order: int) -> QuoteLineItem | None:
    """Calculate price for a single parsed item.

    Returns None if item cannot be priced (missing required dimensions).
    Applies sensible defaults for missing grade/length before rejecting.
    """
    quantity_note: str | None = None
    if item.quantity == 0:
        from dataclasses import replace
        item = replace(item, quantity=1)
        quantity_note = "Quantity not specified — defaulted to 1"
        logger.warning("Item '%s' has quantity 0 — defaulting to 1", item.description or item.product_type)

    result = _price_item_core(item, sort_order)

    if result is not None and quantity_note:
        if result.notes:
            result.notes = f"{result.notes}; {quantity_note}"
        else:
            result.notes = quantity_note

    return result


def _price_item_core(item: ParsedItem, sort_order: int) -> QuoteLineItem | None:
    """Core pricing logic for a single parsed item."""

    if item.product_type == "sleeve":
        if not all([item.diameter, item.wall_thickness]):
            logger.warning(
                "Dropping sleeve item — missing diameter or wall_thickness: %s",
                item.description,
            )
            return None

        item, default_notes = _apply_item_defaults(item)
        quote_quantity, _ = _quote_quantity_and_warning(item)
        actual_od = normalize_nominal_od(item.diameter)

        unit_price, weight_per_ft, price_per_lb = calculate_sleeve_price(
            actual_od,
            item.wall_thickness,
            item.grade,
            item.length_ft,
            item.milling,
            item.painting,
        )

        total = unit_price * Decimal(str(quote_quantity))

        notes = "; ".join(default_notes) if default_notes else None
        return QuoteLineItem(
            sort_order=sort_order,
            product_type="sleeve",
            part_number=generate_sleeve_part_number(
                actual_od,
                item.wall_thickness,
                item.grade,
                item.length_ft,
                item.milling,
                item.painting,
            ),
            description=generate_sleeve_description(
                actual_od,
                item.wall_thickness,
                item.grade,
                item.length_ft,
                item.milling,
                item.painting,
            ),
            quantity=quote_quantity,
            unit_price=unit_price,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            weight_per_ft=weight_per_ft,
            price_per_lb=price_per_lb,
            notes=notes,
        )

    if item.product_type == "oversleeve":
        if not all([item.diameter, item.wall_thickness]):
            logger.warning(
                "Dropping oversleeve item — missing diameter or wall_thickness: %s",
                item.description,
            )
            return None

        item, default_notes = _apply_item_defaults(item)
        oversleeve_od = calculate_oversleeve_od(item.diameter, item.wall_thickness)

        # Oversleeves use same weight-based pricing as regular sleeves
        unit_price, weight_per_ft, price_per_lb = calculate_sleeve_price(
            oversleeve_od,
            item.wall_thickness,
            item.grade,
            item.length_ft,
            item.milling,
            item.painting,
        )

        total = unit_price * Decimal(str(item.quantity))

        notes = "; ".join(default_notes) if default_notes else None
        return QuoteLineItem(
            sort_order=sort_order,
            product_type="oversleeve",
            part_number=generate_oversleeve_part_number(
                item.diameter,
                item.wall_thickness,
                item.grade,
                item.length_ft,
                item.milling,
                item.painting,
            ),
            description=generate_oversleeve_description(
                item.diameter,
                item.wall_thickness,
                item.grade,
                item.length_ft,
                item.milling,
                item.painting,
            ),
            quantity=item.quantity,
            unit_price=unit_price,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            weight_per_ft=weight_per_ft,
            price_per_lb=price_per_lb,
            notes=notes,
        )

    if item.product_type == "girth_weld":
        if not all([item.diameter, item.wall_thickness]):
            logger.warning(
                "Dropping girth_weld item — missing diameter or wall_thickness: %s",
                item.description,
            )
            return None

        item, default_notes = _apply_item_defaults(item)
        actual_od = normalize_nominal_od(item.diameter)

        unit_price = get_girth_weld_price(actual_od)
        if unit_price is None:
            return None

        total = unit_price * Decimal(str(item.quantity))

        notes = "; ".join(default_notes) if default_notes else None
        return QuoteLineItem(
            sort_order=sort_order,
            product_type="girth_weld",
            part_number=generate_girth_weld_part_number(
                actual_od,
                item.wall_thickness,
                item.grade,
                item.length_ft,
            ),
            description=generate_girth_weld_description(
                actual_od,
                item.wall_thickness,
                item.grade,
                item.length_ft,
            ),
            quantity=item.quantity,
            unit_price=unit_price,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            notes=notes,
        )

    if item.product_type == "bag":
        if item.diameter is None:
            logger.warning(
                "Bag item missing diameter — cannot determine pricing: %s",
                item.description,
            )
            return _tbd_line_item(item, sort_order)

        bag_entry = _lookup_bag_pricing(item.diameter)
        if bag_entry is None:
            logger.warning(
                "No bag pricing for diameter %.1f: %s",
                item.diameter, item.description,
            )
            return _tbd_line_item(item, sort_order)

        part_number, pieces_per_pallet, price_per_bag = bag_entry
        total = price_per_bag * Decimal(str(item.quantity))
        desc = f'Geotextile Bag, {part_number}, {int(item.diameter)}" pipe'
        return QuoteLineItem(
            sort_order=sort_order,
            product_type="bag",
            part_number=part_number,
            description=desc,
            quantity=item.quantity,
            unit_price=price_per_bag,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )

    if item.product_type == "compression":
        price, unit = _get_other_pricing("compression_sleeve")
        total = price * Decimal(str(item.quantity))
        dia_str = f', {int(item.diameter)}"' if item.diameter else ""
        diameter = normalize_nominal_od(item.diameter) if item.diameter is not None else 0.0
        wt = item.wall_thickness or 0.25
        grade = item.grade or DEFAULT_GRADE
        return QuoteLineItem(
            sort_order=sort_order,
            product_type="compression",
            part_number=generate_part_number("compression", diameter, wt, grade),
            description=f"Compression Sleeve{dia_str}",
            quantity=item.quantity,
            unit_price=price,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )

    if item.product_type == "omegawrap":
        key = _match_omegawrap_key(item.description)
        if key is None:
            logger.warning(
                "Cannot determine omegawrap variant from description: %s",
                item.description,
            )
            return _tbd_line_item(item, sort_order)

        price, unit = _get_other_pricing(key)
        label = key.replace("omegawrap_", "").replace("_", " ").title()
        total = price * Decimal(str(item.quantity))
        return QuoteLineItem(
            sort_order=sort_order,
            product_type="omegawrap",
            part_number=f"OW-{label.upper()}",
            description=f"OmegaWrap {label} ({unit.replace('_', ' ')})",
            quantity=item.quantity,
            unit_price=price,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )

    if item.product_type == "accessory":
        key = _match_other_pricing_key(item.description, _ACCESSORY_KEYS)
        if key is None:
            logger.warning(
                "Cannot match accessory from description: %s",
                item.description,
            )
            return _tbd_line_item(item, sort_order)

        price, unit = _get_other_pricing(key)
        label = key.replace("_", " ").title()
        total = price * Decimal(str(item.quantity))
        return QuoteLineItem(
            sort_order=sort_order,
            product_type="accessory",
            part_number=f"ACC-{key.upper()}",
            description=f"{label} ({unit.replace('_', ' ')})",
            quantity=item.quantity,
            unit_price=price,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )

    if item.product_type == "service":
        key = _match_other_pricing_key(item.description, _SERVICE_KEYS)
        if key is None:
            logger.warning(
                "Cannot match service from description: %s",
                item.description,
            )
            return _tbd_line_item(item, sort_order)

        price, unit = _get_other_pricing(key)
        label = key.replace("_", " ").title()
        total = price * Decimal(str(item.quantity))
        return QuoteLineItem(
            sort_order=sort_order,
            product_type="service",
            part_number=f"SVC-{key.upper()}",
            description=f"{label} ({unit.replace('_', ' ')})",
            quantity=item.quantity,
            unit_price=price,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )

    # Unknown product type — include as TBD rather than silently dropping
    logger.warning(
        "Unknown product type '%s' for item: %s",
        item.product_type, item.description,
    )
    return _tbd_line_item(item, sort_order)


def _build_shipping_note_text(rfq: ParsedRFQ) -> str:
    """Build shipping note row text from parsed RFQ data."""
    candidate_texts = [rfq.notes, rfq.raw_body]

    # Prefer explicit "Ship: <carrier>" instructions when present.
    ship_with_carrier = re.compile(r"(?im)^\s*ship\s*:\s*(.+?)\s*$")
    for text in candidate_texts:
        if not text:
            continue
        match = ship_with_carrier.search(text)
        if match:
            return f"Ship: {match.group(1).strip().rstrip('.')}"

    # Fall back to the default shipping instruction used in sample quotes.
    return "*Ship LTL Prepay & Add"


def _build_rfq_contact_text(rfq: ParsedRFQ) -> str | None:
    """Build RFQ contact row text."""
    segments: list[str] = []
    if rfq.contact_name:
        segments.append(rfq.contact_name.strip())
    if rfq.contact_phone:
        segments.append(rfq.contact_phone.strip())
    if rfq.contact_email:
        segments.append(rfq.contact_email.strip())

    if not segments:
        return None

    return f"RFQ: {' '.join(segments)}"


def generate_quote(rfq: ParsedRFQ, quote_number: str) -> Quote:
    """Generate a complete quote from a parsed RFQ."""
    line_items: list[QuoteLineItem] = []
    quantity_warnings: list[str] = []

    default_notes: list[str] = []
    for i, item in enumerate(rfq.items, start=1):
        _, warning = _quote_quantity_and_warning(item)
        if warning:
            quantity_warnings.append(warning)
        priced = price_item(item, i)
        if priced:
            line_items.append(priced)
            if priced.notes:
                default_notes.append(f"Item {i}: {priced.notes}")
        else:
            desc = item.description or item.product_type
            logger.warning(
                "Dropped unpriceable item %d: %s (dia=%s, wt=%s, grade=%s, len=%s)",
                i, desc, item.diameter, item.wall_thickness, item.grade, item.length_ft,
            )

    shipping_note = QuoteLineItem(
        sort_order=len(line_items) + 1,
        product_type="note",
        part_number="",
        description=_build_shipping_note_text(rfq),
        quantity=0,
        unit_price=Decimal("0.00"),
        total=Decimal("0.00"),
        is_note=True,
    )
    line_items.append(shipping_note)

    rfq_contact_text = _build_rfq_contact_text(rfq)
    if rfq_contact_text:
        line_items.append(
            QuoteLineItem(
                sort_order=len(line_items) + 1,
                product_type="note",
                part_number="",
                description=rfq_contact_text,
                quantity=0,
                unit_price=Decimal("0.00"),
                total=Decimal("0.00"),
                is_note=True,
            )
        )

    for warning in quantity_warnings:
        line_items.append(
            QuoteLineItem(
                sort_order=len(line_items) + 1,
                product_type="note",
                part_number="",
                description=warning,
                quantity=0,
                unit_price=Decimal("0.00"),
                total=Decimal("0.00"),
                is_note=True,
            )
        )

    if default_notes:
        line_items.append(
            QuoteLineItem(
                sort_order=len(line_items) + 1,
                product_type="note",
                part_number="",
                description="*Defaults applied: " + "; ".join(default_notes),
                quantity=0,
                unit_price=Decimal("0.00"),
                total=Decimal("0.00"),
                is_note=True,
            )
        )

    subtotal = sum((item.total for item in line_items if not item.is_note), Decimal("0"))

    ship_to_dict = None
    if rfq.ship_to:
        ship_to_dict = {
            "company": rfq.ship_to.company,
            "attention": rfq.ship_to.attention,
            "street": rfq.ship_to.street,
            "city": rfq.ship_to.city,
            "state": rfq.ship_to.state,
            "postal_code": rfq.ship_to.postal_code,
            "country": rfq.ship_to.country,
        }

    return Quote(
        quote_number=quote_number,
        customer_name=rfq.customer_name,
        contact_name=rfq.contact_name,
        contact_email=rfq.contact_email,
        contact_phone=rfq.contact_phone,
        ship_to=ship_to_dict,
        po_number=rfq.po_number,
        project_line=rfq.project_line,
        line_items=line_items,
        subtotal=subtotal,
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=subtotal,
        notes=rfq.notes,
    )
