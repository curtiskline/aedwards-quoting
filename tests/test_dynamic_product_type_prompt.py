"""Task 362: the decode prompt's product_type enum is built live from the
editable ProductType table, and 'omegawrap' has been reconciled to 'composite'
end-to-end (decode -> pricing).
"""

from __future__ import annotations

import os
from decimal import Decimal

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import ProductType

from allenedwards import parser
from allenedwards.parser import ParsedItem
from allenedwards.pricing import price_item


def _make_app(db_url):
    os.environ["DATABASE_URL"] = db_url
    Config.SQLALCHEMY_DATABASE_URI = db_url
    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["TESTING"] = True
    return app


def test_prompt_uses_default_types_without_app_context():
    """No app/DB context -> sane default list, on the new taxonomy."""
    prompt = parser._parse_system_prompt()
    # Placeholder must be substituted, never leak to the model.
    assert parser._PRODUCT_TYPE_ENUM_PLACEHOLDER not in prompt
    # Post-terminology taxonomy: composite in, legacy omegawrap out.
    assert "composite" in prompt
    assert "omegawrap" not in prompt
    # The inline enum carries the composite slug.
    assert "sleeve|" in prompt


def test_prompt_reads_live_product_type_table(db_url):
    """Editing the ProductType table changes the prompt with no code change."""
    app = _make_app(db_url)
    with app.app_context():
        db.create_all()
        db.session.add_all(
            [
                ProductType(name="sleeve", display_label="Sleeve", sort_order=1, is_active=True),
                ProductType(name="composite", display_label="Composite", sort_order=2, is_active=True),
                # A type Chip adds live must appear in the prompt automatically.
                ProductType(name="megasleeve", display_label="Mega Sleeve", sort_order=3, is_active=True),
                # Inactive types must NOT appear.
                ProductType(name="retired", display_label="Retired", sort_order=4, is_active=False),
            ]
        )
        db.session.commit()

        prompt = parser._parse_system_prompt()

    assert "composite" in prompt
    assert "megasleeve" in prompt
    assert "Mega Sleeve" in prompt  # display_label surfaced in the authoritative block
    assert "retired" not in prompt
    assert "omegawrap" not in prompt
    # Live enum orders by sort_order: sleeve then composite then megasleeve.
    assert "sleeve|composite|megasleeve" in prompt


def test_prompt_falls_back_when_product_type_table_absent(db_url):
    """App context but no product_type table -> graceful default (no crash)."""
    app = _make_app(db_url)
    with app.app_context():
        # Deliberately do NOT create tables.
        prompt = parser._parse_system_prompt()
    assert "composite" in prompt
    assert "omegawrap" not in prompt


def test_composite_item_prices_via_omegawrap_variant():
    """A decoded wrap now carries product_type='composite' and still prices.

    The internal variant key (_match_omegawrap_key -> omegawrap_carbon) is
    unchanged; only the product_type string moved.
    """
    item = ParsedItem(
        product_type="composite",
        quantity=2,
        description="OmegaWrap Carbon composite repair",
    )
    result = price_item(item, sort_order=1)
    assert result is not None
    assert result.product_type == "composite"
    assert result.unit_price > Decimal("0")
    assert result.part_number == "OW-CARBON"
    # 2 rolls of carbon at the default per-roll rate.
    assert result.total == result.unit_price * 2


def test_legacy_omegawrap_product_type_no_longer_dispatches():
    """Guards the rename: the old slug must not reach the wrap pricing branch.

    With the dispatch keyed on 'composite', an item still labelled 'omegawrap'
    falls through to the unpriceable TBD path instead of pricing as a wrap.
    """
    item = ParsedItem(
        product_type="omegawrap",
        quantity=2,
        description="OmegaWrap Carbon composite repair",
    )
    result = price_item(item, sort_order=1)
    assert result is not None
    assert result.part_number == "TBD"
    assert result.unit_price == Decimal("0.00")
