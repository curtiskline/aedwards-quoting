"""Tests for the pricing module."""

from decimal import Decimal

from allenedwards.pricing import (
    calculate_sleeve_price,
    calculate_sleeve_weight_per_ft,
    generate_sleeve_description,
    generate_sleeve_part_number,
    get_price_per_lb,
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
