"""Price calculation engine based on Allan Edwards pricing rules."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
import re
from typing import Any

from .parser import ParsedItem, ParsedRFQ

# Price per pound lookup table
# wall_thickness -> (GR50 price, GR65 price)
PRICE_PER_LB: dict[str, tuple[Decimal, Decimal]] = {
    "0.25": (Decimal("2.82"), Decimal("2.92")),
    "0.3125": (Decimal("2.69"), Decimal("2.79")),
    "0.375": (Decimal("2.57"), Decimal("2.67")),
    "0.5": (Decimal("2.52"), Decimal("2.62")),
    "0.625": (Decimal("2.52"), Decimal("2.62")),
    "0.75": (Decimal("2.52"), Decimal("2.62")),
}

# Girth weld pricing by diameter range
# (min_diameter, max_diameter, price_per_set)
GIRTH_WELD_PRICING = [
    (2, 18, Decimal("300")),
    (20, 30, Decimal("500")),
    (32, 44, Decimal("800")),
]

# Bag pricing
# (pipe_size_min, pipe_size_max, part_number, pieces_per_pallet, price_per_bag)
BAG_PRICING = [
    (10, 12, "GTW 10-12", 110, Decimal("52.08")),
    (14, 18, "GTW 16", 52, Decimal("80.77")),
    (20, 26, "GTW 20-24", 34, Decimal("138.24")),
    (28, 38, "GTW 30-36", 30, Decimal("155.00")),
    (40, 48, "GTW 42-48", 21, Decimal("214.29")),
]

# Other product pricing
OTHER_PRICING: dict[str, tuple[Decimal, str]] = {
    "bag_fill": (Decimal("0.02"), "per_lb"),
    "omegawrap_carbon": (Decimal("650"), "per_roll"),
    "omegawrap_eglass": (Decimal("420"), "per_roll"),
    "omegawrap_magnum": (Decimal("390"), "per_roll"),
    "isolation_wrap": (Decimal("200"), "per_roll"),
    "resin": (Decimal("125"), "per_quart"),
    "putty": (Decimal("130"), "per_pint"),
    "compression_sleeve": (Decimal("5000"), "per_set"),
    "porcupine_roller": (Decimal("210"), "each"),
    "magnet_set": (Decimal("80"), "per_set"),
    "accessory_kit": (Decimal("122"), "per_kit"),
    "plastic_wrap_large": (Decimal("101"), "per_roll"),
    "pipejacks": (Decimal("1800"), "each"),
    "pipejacks_large": (Decimal("2200"), "each"),
    "supervisor": (Decimal("1950"), "per_day"),
    "trainer_torch": (Decimal("6000"), "per_package"),
    "kickoff_training": (Decimal("6000"), "per_package"),
    "training_package": (Decimal("400"), "per_package"),
}

# Service pricing
MILLING_PRICE = Decimal("30")
PAINTING_PRICE = Decimal("40")


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

    # Metadata
    sales_rep: str = "Jamee Hamilton"
    payment_terms: str = "Net 30"
    shipping_terms: str = "Prepay & Add"
    shipping_method: str = "Flatbed"

    # Requested by (from RFQ)
    requested_by_name: str | None = None
    requested_by_email: str | None = None
    requested_by_phone: str | None = None


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
    wt_key = str(wall_thickness)

    # Find the matching wall thickness tier
    if wt_key in PRICE_PER_LB:
        prices = PRICE_PER_LB[wt_key]
    elif wall_thickness >= 0.5:
        prices = PRICE_PER_LB["0.5"]
    elif wall_thickness >= 0.375:
        prices = PRICE_PER_LB["0.375"]
    elif wall_thickness >= 0.3125:
        prices = PRICE_PER_LB["0.3125"]
    else:
        prices = PRICE_PER_LB["0.25"]

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
        unit_price += MILLING_PRICE
    if painting:
        unit_price += PAINTING_PRICE

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
    for min_dia, max_dia, price in GIRTH_WELD_PRICING:
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

    Format: GW-{diameter}-{wt_code}-{grade}-{length}
    """
    # Wall thickness codes
    wt_codes = {
        0.25: "14",
        0.3125: "516",
        0.375: "38",
        0.5: "12",
        0.625: "58",
        0.75: "34",
    }

    wt_code = wt_codes.get(wall_thickness, str(wall_thickness).replace(".", ""))

    # Format diameter
    if diameter == int(diameter):
        dia_str = str(int(diameter))
    else:
        dia_str = f"{diameter:.3f}".rstrip("0").rstrip(".")

    # Format length
    if length_ft == int(length_ft):
        len_str = str(int(length_ft))
    else:
        len_str = f"{length_ft:.1f}".rstrip("0").rstrip(".")

    return f"GW-{dia_str}-{wt_code}-{grade}-{len_str}"


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

    return f'Girth Weld Sleeve, {diameter}" ID, {wt_str} w/t, A572 GR{grade}, {length_ft}\' long'


def generate_sleeve_part_number(
    diameter: float,
    wall_thickness: float,
    grade: int,
    length_ft: float,
    milling: bool = False,
    painting: bool = False,
) -> str:
    """Generate a part number for a sleeve.

    Format: S-{sleeve_id}-{wt_code}-{grade}-{length}[-M][-P]
    """
    # Wall thickness codes
    wt_codes = {
        0.25: "14",
        0.3125: "516",
        0.375: "38",
        0.5: "12",
        0.625: "58",
        0.75: "34",
    }

    wt_code = wt_codes.get(wall_thickness, str(wall_thickness).replace(".", ""))

    # Format diameter
    if diameter == int(diameter):
        dia_str = str(int(diameter))
    else:
        dia_str = f"{diameter:.3f}".rstrip("0").rstrip(".")

    # Format length
    if length_ft == int(length_ft):
        len_str = str(int(length_ft))
    else:
        len_str = f"{length_ft:.1f}".rstrip("0").rstrip(".")

    part_num = f"S-{dia_str}-{wt_code}-{grade}-{len_str}"

    if milling:
        part_num += "-M"
    if painting:
        part_num += "-P"

    return part_num


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

    desc = f'Sleeve, Sealing, {diameter}" ID, {wt_str} w/t, A572 GR{grade}, {length_ft}\' long'

    services = []
    if milling:
        services.append("Milled")
    if painting:
        services.append("Painted")

    if services:
        desc += f" ({', '.join(services)})"

    return desc


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
    Format: OS-{diameter}-{wt_code}-{grade}-{length}[-M][-P]
    """
    # Wall thickness codes
    wt_codes = {
        0.25: "14",
        0.3125: "516",
        0.375: "38",
        0.5: "12",
        0.625: "58",
        0.75: "34",
    }

    wt_code = wt_codes.get(wall_thickness, str(wall_thickness).replace(".", ""))

    # Format diameter
    if diameter == int(diameter):
        dia_str = str(int(diameter))
    else:
        dia_str = f"{diameter:.3f}".rstrip("0").rstrip(".")

    # Format length
    if length_ft == int(length_ft):
        len_str = str(int(length_ft))
    else:
        len_str = f"{length_ft:.1f}".rstrip("0").rstrip(".")

    part_num = f"OS-{dia_str}-{wt_code}-{grade}-{len_str}"

    if milling:
        part_num += "-M"
    if painting:
        part_num += "-P"

    return part_num


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

    desc = f'Oversleeve, {diameter}" ID, {wt_str} w/t, A572 GR{grade}, {length_ft}\' long'

    services = []
    if milling:
        services.append("Milled")
    if painting:
        services.append("Painted")

    if services:
        desc += f" ({', '.join(services)})"

    return desc


def price_item(item: ParsedItem, sort_order: int) -> QuoteLineItem | None:
    """Calculate price for a single parsed item.

    Returns None if item cannot be priced (missing required data).
    """
    if item.product_type == "sleeve":
        if not all([item.diameter, item.wall_thickness, item.grade, item.length_ft]):
            return None

        unit_price, weight_per_ft, price_per_lb = calculate_sleeve_price(
            item.diameter,
            item.wall_thickness,
            item.grade,
            item.length_ft,
            item.milling,
            item.painting,
        )

        total = unit_price * Decimal(str(item.quantity))

        return QuoteLineItem(
            sort_order=sort_order,
            product_type="sleeve",
            part_number=generate_sleeve_part_number(
                item.diameter,
                item.wall_thickness,
                item.grade,
                item.length_ft,
                item.milling,
                item.painting,
            ),
            description=generate_sleeve_description(
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
        )

    if item.product_type == "oversleeve":
        if not all([item.diameter, item.wall_thickness, item.grade, item.length_ft]):
            return None

        # Oversleeves use same weight-based pricing as regular sleeves
        unit_price, weight_per_ft, price_per_lb = calculate_sleeve_price(
            item.diameter,
            item.wall_thickness,
            item.grade,
            item.length_ft,
            item.milling,
            item.painting,
        )

        total = unit_price * Decimal(str(item.quantity))

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
        )

    if item.product_type == "girth_weld":
        if not all([item.diameter, item.wall_thickness, item.grade, item.length_ft]):
            return None

        unit_price = get_girth_weld_price(item.diameter)
        if unit_price is None:
            return None

        total = unit_price * Decimal(str(item.quantity))

        return QuoteLineItem(
            sort_order=sort_order,
            product_type="girth_weld",
            part_number=generate_girth_weld_part_number(
                item.diameter,
                item.wall_thickness,
                item.grade,
                item.length_ft,
            ),
            description=generate_girth_weld_description(
                item.diameter,
                item.wall_thickness,
                item.grade,
                item.length_ft,
            ),
            quantity=item.quantity,
            unit_price=unit_price,
            total=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )

    # TODO: Add pricing for other product types
    return None


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

    for i, item in enumerate(rfq.items, start=1):
        priced = price_item(item, i)
        if priced:
            line_items.append(priced)

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
        line_items=line_items,
        subtotal=subtotal,
        shipping_amount=None,
        tax_amount=Decimal("0"),
        total=subtotal,
        notes=rfq.notes,
    )
