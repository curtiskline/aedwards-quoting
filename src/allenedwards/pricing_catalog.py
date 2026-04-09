"""Canonical pricing catalog used for defaults, seed data, and fallback pricing."""

from __future__ import annotations

from decimal import Decimal

# Price per pound lookup table: wall_thickness -> (GR50 price, GR65 price)
DEFAULT_PRICE_PER_LB: dict[str, tuple[Decimal, Decimal]] = {
    "0.25": (Decimal("2.82"), Decimal("2.92")),
    "0.3125": (Decimal("2.69"), Decimal("2.79")),
    "0.375": (Decimal("2.57"), Decimal("2.67")),
    "0.5": (Decimal("2.52"), Decimal("2.62")),
    "0.625": (Decimal("2.52"), Decimal("2.62")),
    "0.75": (Decimal("2.52"), Decimal("2.62")),
}

# Girth weld pricing by diameter range: (min_diameter, max_diameter, price_per_set)
DEFAULT_GIRTH_WELD_PRICING: list[tuple[int, int, Decimal]] = [
    (2, 19, Decimal("300")),
    (20, 31, Decimal("500")),
    (32, 44, Decimal("800")),
]

# Bag pricing: (pipe_size_min, pipe_size_max, part_number, pieces_per_pallet, price_per_bag)
DEFAULT_BAG_PRICING: list[tuple[int, int, str, int, Decimal]] = [
    (10, 13, "GTW 10-12", 110, Decimal("52.08")),
    (14, 19, "GTW 16", 52, Decimal("80.77")),
    (20, 27, "GTW 20-24", 34, Decimal("138.24")),
    (28, 39, "GTW 30-36", 30, Decimal("155.00")),
    (40, 48, "GTW 42-48", 21, Decimal("214.29")),
]

# Flat and accessory/service style rates: key -> (price, unit)
DEFAULT_OTHER_PRICING: dict[str, tuple[Decimal, str]] = {
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
    "weld_cap": (Decimal("15"), "each"),
    "backing_strip": (Decimal("10"), "each"),
    "supervisor": (Decimal("1950"), "per_day"),
    "trainer_torch": (Decimal("6000"), "per_package"),
    "kickoff_training": (Decimal("6000"), "per_package"),
    "training_package": (Decimal("400"), "per_package"),
}

DEFAULT_SERVICE_PRICES: dict[str, Decimal] = {
    "milling": Decimal("30"),
    "painting": Decimal("40"),
}


def default_pricing_rows() -> list[dict[str, object]]:
    """Flatten default pricing into pricing_table row payloads."""
    rows: list[dict[str, object]] = []

    for wall_thickness, (gr50, gr65) in DEFAULT_PRICE_PER_LB.items():
        rows.append(
            {
                "product_type": "sleeve",
                "key_fields": {"wall_thickness": wall_thickness, "grade": 50},
                "price": gr50,
            }
        )
        rows.append(
            {
                "product_type": "sleeve",
                "key_fields": {"wall_thickness": wall_thickness, "grade": 65},
                "price": gr65,
            }
        )

    for min_diameter, max_diameter, price in DEFAULT_GIRTH_WELD_PRICING:
        rows.append(
            {
                "product_type": "girth_weld",
                "key_fields": {"min_diameter": min_diameter, "max_diameter": max_diameter},
                "price": price,
            }
        )

    for pipe_size_min, pipe_size_max, part_number, pieces_per_pallet, price in DEFAULT_BAG_PRICING:
        rows.append(
            {
                "product_type": "bag",
                "key_fields": {
                    "pipe_size_min": pipe_size_min,
                    "pipe_size_max": pipe_size_max,
                    "part_number": part_number,
                    "pieces_per_pallet": pieces_per_pallet,
                },
                "price": price,
            }
        )

    for key, (price, unit) in DEFAULT_OTHER_PRICING.items():
        product_type = "flat"
        if key == "compression_sleeve":
            product_type = "compression"
        elif key.startswith("omegawrap_"):
            product_type = "omegawrap"
        elif key in {"supervisor", "trainer_torch", "kickoff_training", "training_package"}:
            product_type = "service"
        elif key not in {"compression_sleeve"}:
            product_type = "accessory"

        rows.append(
            {
                "product_type": product_type,
                "key_fields": {"key": key, "unit": unit},
                "price": price,
            }
        )

    for key, price in DEFAULT_SERVICE_PRICES.items():
        rows.append(
            {
                "product_type": "flat",
                "key_fields": {"key": key, "unit": "flat"},
                "price": price,
            }
        )

    return rows
