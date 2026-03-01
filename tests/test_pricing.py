"""Tests for the pricing module."""

from decimal import Decimal

from allenedwards.parser import ParsedItem
from allenedwards.pricing import (
    calculate_sleeve_price,
    calculate_sleeve_weight_per_ft,
    generate_girth_weld_description,
    generate_girth_weld_part_number,
    generate_oversleeve_description,
    generate_oversleeve_part_number,
    generate_sleeve_description,
    generate_sleeve_part_number,
    get_girth_weld_price,
    get_price_per_lb,
    price_item,
)


def test_weight_per_ft_calculation():
    """Test sleeve weight per foot calculation.

    Formula: weight_per_ft = 10.69 * ((sleeve_id + wall_thickness) * wall_thickness) / 2
    """
    # 6.625" ID, 1/4" (0.25) wall
    weight = calculate_sleeve_weight_per_ft(6.625, 0.25)
    expected = Decimal("10.69") * ((Decimal("6.625") + Decimal("0.25")) * Decimal("0.25")) / 2
    assert weight == expected.quantize(Decimal("0.01"))


def test_price_per_lb_lookup():
    """Test price per pound lookup."""
    # GR50, 1/4" wall
    assert get_price_per_lb(0.25, 50) == Decimal("2.82")
    # GR65, 1/4" wall
    assert get_price_per_lb(0.25, 65) == Decimal("2.92")
    # GR50, 3/8" wall
    assert get_price_per_lb(0.375, 50) == Decimal("2.57")
    # GR65, 1/2" wall (tier for >= 0.5)
    assert get_price_per_lb(0.5, 65) == Decimal("2.62")


def test_sleeve_price_calculation():
    """Test full sleeve price calculation."""
    # 6.625" ID, 1/4" wall, GR50, 10' long
    unit_price, weight_per_ft, price_per_lb = calculate_sleeve_price(
        diameter=6.625,
        wall_thickness=0.25,
        grade=50,
        length_ft=10,
    )

    # Verify weight per ft
    expected_weight = Decimal("10.69") * ((Decimal("6.625") + Decimal("0.25")) * Decimal("0.25")) / 2
    assert weight_per_ft == expected_weight.quantize(Decimal("0.01"))

    # Verify price per lb
    assert price_per_lb == Decimal("2.82")

    # Verify unit price = weight_per_ft * price_per_lb * length
    expected_unit = expected_weight.quantize(Decimal("0.01")) * Decimal("2.82") * 10
    assert unit_price == expected_unit.quantize(Decimal("0.01"))


def test_sleeve_price_with_services():
    """Test sleeve price with milling and painting."""
    # Base price
    base_price, _, _ = calculate_sleeve_price(6.625, 0.25, 50, 10)

    # With milling (+$30)
    with_milling, _, _ = calculate_sleeve_price(6.625, 0.25, 50, 10, milling=True)
    assert with_milling == base_price + Decimal("30")

    # With painting (+$40)
    with_painting, _, _ = calculate_sleeve_price(6.625, 0.25, 50, 10, painting=True)
    assert with_painting == base_price + Decimal("40")

    # With both (+$70)
    with_both, _, _ = calculate_sleeve_price(6.625, 0.25, 50, 10, milling=True, painting=True)
    assert with_both == base_price + Decimal("70")


def test_part_number_generation():
    """Test sleeve part number generation."""
    # Basic sleeve
    pn = generate_sleeve_part_number(6.625, 0.25, 50, 10)
    assert pn == "S-6.625-14-50-10"

    # With milling
    pn = generate_sleeve_part_number(6.625, 0.25, 50, 10, milling=True)
    assert pn == "S-6.625-14-50-10-M"

    # With painting
    pn = generate_sleeve_part_number(6.625, 0.25, 50, 10, painting=True)
    assert pn == "S-6.625-14-50-10-P"

    # With both
    pn = generate_sleeve_part_number(6.625, 0.25, 50, 10, milling=True, painting=True)
    assert pn == "S-6.625-14-50-10-M-P"

    # Different wall thickness
    pn = generate_sleeve_part_number(12.75, 0.375, 65, 12)
    assert pn == "S-12.75-38-65-12"


def test_description_generation():
    """Test sleeve description generation."""
    # Basic sleeve
    desc = generate_sleeve_description(6.625, 0.25, 50, 10)
    assert desc == 'Sleeve, Sealing, 6.625" ID, 1/4" w/t, A572 GR50, 10\' long'

    # With services
    desc = generate_sleeve_description(6.625, 0.25, 50, 10, milling=True, painting=True)
    assert desc == 'Sleeve, Sealing, 6.625" ID, 1/4" w/t, A572 GR50, 10\' long (Milled, Painted)'


# Girth weld sleeve tests


def test_girth_weld_price_lookup():
    """Test girth weld price lookup by diameter tier."""
    # 2-18" tier = $300
    assert get_girth_weld_price(2) == Decimal("300")
    assert get_girth_weld_price(8.625) == Decimal("300")
    assert get_girth_weld_price(18) == Decimal("300")

    # 20-30" tier = $500
    assert get_girth_weld_price(20) == Decimal("500")
    assert get_girth_weld_price(24) == Decimal("500")
    assert get_girth_weld_price(30) == Decimal("500")

    # 32-44" tier = $800
    assert get_girth_weld_price(32) == Decimal("800")
    assert get_girth_weld_price(36) == Decimal("800")
    assert get_girth_weld_price(44) == Decimal("800")

    # Outside ranges returns None
    assert get_girth_weld_price(1) is None
    assert get_girth_weld_price(19) is None
    assert get_girth_weld_price(31) is None
    assert get_girth_weld_price(50) is None


def test_girth_weld_part_number():
    """Test girth weld part number generation."""
    # Basic girth weld sleeve (8-5/8" ID, 3/8" wall, GR50, 12' long)
    pn = generate_girth_weld_part_number(8.625, 0.375, 50, 12)
    assert pn == "GW-8.625-38-50-12"

    # Different wall thickness
    pn = generate_girth_weld_part_number(6.625, 0.25, 50, 10)
    assert pn == "GW-6.625-14-50-10"

    # GR65
    pn = generate_girth_weld_part_number(12.75, 0.5, 65, 8)
    assert pn == "GW-12.75-12-65-8"


def test_girth_weld_description():
    """Test girth weld description generation."""
    # Basic girth weld sleeve
    desc = generate_girth_weld_description(8.625, 0.375, 50, 12)
    assert desc == 'Girth Weld Sleeve, 8.625" ID, 3/8" w/t, A572 GR50, 12\' long'

    # Different specs
    desc = generate_girth_weld_description(6.625, 0.25, 65, 10)
    assert desc == 'Girth Weld Sleeve, 6.625" ID, 1/4" w/t, A572 GR65, 10\' long'


def test_price_item_girth_weld():
    """Test price_item handles girth_weld product type."""
    # 8-5/8" ID girth weld sleeve (in 2-18" tier = $300/set)
    item = ParsedItem(
        product_type="girth_weld",
        quantity=4,
        description="girth weld sleeve, 8-5/8\" ID, 3/8\" w/t, A572 GR50, 12\" long",
        diameter=8.625,
        wall_thickness=0.375,
        grade=50,
        length_ft=1,  # 12" = 1'
    )

    result = price_item(item, sort_order=1)

    assert result is not None
    assert result.product_type == "girth_weld"
    assert result.part_number == "GW-8.625-38-50-1"
    assert result.unit_price == Decimal("300")  # Per set
    assert result.total == Decimal("1200")  # 4 sets * $300
    assert result.quantity == 4


def test_price_item_girth_weld_different_tiers():
    """Test girth weld pricing across different diameter tiers."""
    # 24" diameter (20-30" tier = $500)
    item_24 = ParsedItem(
        product_type="girth_weld",
        quantity=2,
        description="girth weld sleeve, 24\" ID",
        diameter=24,
        wall_thickness=0.5,
        grade=50,
        length_ft=12,
    )
    result = price_item(item_24, sort_order=1)
    assert result is not None
    assert result.unit_price == Decimal("500")
    assert result.total == Decimal("1000")

    # 36" diameter (32-44" tier = $800)
    item_36 = ParsedItem(
        product_type="girth_weld",
        quantity=3,
        description="girth weld sleeve, 36\" ID",
        diameter=36,
        wall_thickness=0.5,
        grade=50,
        length_ft=12,
    )
    result = price_item(item_36, sort_order=2)
    assert result is not None
    assert result.unit_price == Decimal("800")
    assert result.total == Decimal("2400")


def test_price_item_girth_weld_missing_data():
    """Test girth weld returns None for missing required data."""
    # Missing diameter
    item = ParsedItem(
        product_type="girth_weld",
        quantity=4,
        description="girth weld sleeve",
        diameter=None,
        wall_thickness=0.375,
        grade=50,
        length_ft=12,
    )
    assert price_item(item, sort_order=1) is None

    # Diameter outside supported ranges
    item_too_small = ParsedItem(
        product_type="girth_weld",
        quantity=4,
        description="girth weld sleeve",
        diameter=1,  # Too small (< 2")
        wall_thickness=0.375,
        grade=50,
        length_ft=12,
    )
    assert price_item(item_too_small, sort_order=1) is None


# Oversleeve tests


def test_oversleeve_part_number_generation():
    """Test oversleeve part number generation.

    Oversleeves use OS- prefix instead of S-.
    """
    # Basic oversleeve - example from task: 9-3/8" ID, 3/8" w/t, GR50, 10' long
    pn = generate_oversleeve_part_number(9.375, 0.375, 50, 10)
    assert pn == "OS-9.375-38-50-10"

    # With milling
    pn = generate_oversleeve_part_number(9.375, 0.375, 50, 10, milling=True)
    assert pn == "OS-9.375-38-50-10-M"

    # With painting
    pn = generate_oversleeve_part_number(9.375, 0.375, 50, 10, painting=True)
    assert pn == "OS-9.375-38-50-10-P"

    # With both
    pn = generate_oversleeve_part_number(9.375, 0.375, 50, 10, milling=True, painting=True)
    assert pn == "OS-9.375-38-50-10-M-P"

    # Different wall thickness
    pn = generate_oversleeve_part_number(12.75, 0.25, 65, 12)
    assert pn == "OS-12.75-14-65-12"


def test_oversleeve_description_generation():
    """Test oversleeve description generation."""
    # Basic oversleeve
    desc = generate_oversleeve_description(9.375, 0.375, 50, 10)
    assert desc == 'Oversleeve, 9.375" ID, 3/8" w/t, A572 GR50, 10\' long'

    # With services
    desc = generate_oversleeve_description(9.375, 0.375, 50, 10, milling=True, painting=True)
    assert desc == 'Oversleeve, 9.375" ID, 3/8" w/t, A572 GR50, 10\' long (Milled, Painted)'


def test_price_item_oversleeve():
    """Test price_item handles oversleeve product type.

    Oversleeves use the same weight-based pricing as regular sleeves.
    """
    # Example from task: 9-3/8" ID, 3/8" w/t, GR50, 10' long
    item = ParsedItem(
        product_type="oversleeve",
        quantity=1,
        description="ovsz. half sole, 9-3/8\" ID, 3/8\" w/t, A572 GR50, 10' long",
        diameter=9.375,
        wall_thickness=0.375,
        grade=50,
        length_ft=10,
    )

    result = price_item(item, sort_order=1)

    assert result is not None
    assert result.product_type == "oversleeve"
    assert result.part_number == "OS-9.375-38-50-10"
    assert 'Oversleeve, 9.375" ID' in result.description
    assert result.quantity == 1

    # Verify pricing uses same formula as regular sleeves
    # weight_per_ft = 10.69 * ((9.375 + 0.375) * 0.375) / 2
    expected_weight = Decimal("10.69") * ((Decimal("9.375") + Decimal("0.375")) * Decimal("0.375")) / 2
    assert result.weight_per_ft == expected_weight.quantize(Decimal("0.01"))
    assert result.price_per_lb == Decimal("2.57")  # 3/8" wall, GR50


def test_price_item_oversleeve_missing_data():
    """Test oversleeve returns None for missing required data."""
    # Missing diameter
    item = ParsedItem(
        product_type="oversleeve",
        quantity=1,
        description="oversleeve",
        diameter=None,
        wall_thickness=0.375,
        grade=50,
        length_ft=10,
    )
    assert price_item(item, sort_order=1) is None
