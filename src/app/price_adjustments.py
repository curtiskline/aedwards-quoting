"""Quote-level pricing, shared by totals and all customer price projections.

Stored line prices are the editable basis. An explicit percentage applies after manual
price overrides, using their stored price as its basis. Repeated reads/saves
never compound the adjustment. Freight and tax are outside either calculation.
No adjustment percentage or unadjusted price belongs in a customer projection.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_percentage(raw):
    try:
        value = Decimal(str(raw or "0"))
        if not value.is_finite() or not -100 <= value <= 100:
            raise ValueError
        if value != value.quantize(CENT):
            raise ValueError
        return value
    except (InvalidOperation, ValueError):
        raise ValueError(
            "Price adjustment must be between -100 and 100, with at most two decimal places."
        ) from None


def percentage(quote):
    return parse_percentage(quote.price_adjustment_pct)


def line_prices(quote, item):
    unit, total = money(item.unit_price), money(item.line_total)
    pct = percentage(quote)
    if pct and item.product_type != "shipping":
        unit = money(unit * (1 + pct / 100))
        total = money(unit * Decimal(str(item.quantity)))
    return unit, total


def merchandise_subtotal(quote):
    return sum(
        (
            line_prices(quote, item)[1]
            for item in quote.line_items
            if item.product_type != "shipping"
        ),
        Decimal("0.00"),
    )


def adjustment_amount(quote):
    """Internal audit only: difference from the stored merchandise basis."""
    subtotal = merchandise_subtotal(quote)
    basis = sum(
        (money(item.line_total) for item in quote.line_items if item.product_type != "shipping"),
        Decimal("0.00"),
    )
    return subtotal - basis
