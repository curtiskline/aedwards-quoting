"""Tests for the canonical ship-to shape and the domestic-country check."""

import pytest

from allenedwards.ship_to import SHIP_TO_KEYS, is_domestic_ship_to, normalize_ship_to


def test_parser_shape_normalizes_street_into_address_line1():
    """Quotes written by the RFQ pipeline store `street`; the web app reads
    `address_line1`. Prod holds a mix of both, so the reader accepts either."""
    normalized = normalize_ship_to(
        {
            "company": "Buckeye Huntington",
            "attention": "Site Manager",
            "street": "1234 Pipeline Rd",
            "city": "Huntington",
            "state": "IN",
            "postal_code": "46750",
            "country": "United States",
        }
    )
    assert normalized["address_line1"] == "1234 Pipeline Rd"
    assert normalized["address_line2"] == ""
    assert normalized["company"] == "Buckeye Huntington"
    assert set(normalized) == set(SHIP_TO_KEYS)


def test_web_shape_is_preserved_and_gains_the_missing_keys():
    normalized = normalize_ship_to(
        {
            "address_line1": "123 Main St",
            "address_line2": "Dock 4",
            "city": "Tulsa",
            "state": "OK",
            "postal_code": "74117",
            "country": "US",
        }
    )
    assert normalized["address_line1"] == "123 Main St"
    assert normalized["address_line2"] == "Dock 4"
    assert normalized["company"] == ""
    assert set(normalized) == set(SHIP_TO_KEYS)


def test_address_line1_wins_over_a_legacy_street_key():
    normalized = normalize_ship_to({"address_line1": "New St", "street": "Old St"})
    assert normalized["address_line1"] == "New St"


@pytest.mark.parametrize("raw", [None, {}, "123 Main St", {"city": ""}, {"country": "None"}])
def test_empty_or_non_dict_values_normalize_to_none(raw):
    assert normalize_ship_to(raw) is None


def test_null_stringified_values_are_dropped():
    """K259: a Python None serialized as the literal string "None"."""
    normalized = normalize_ship_to({"city": "Tulsa", "country": "None", "state": "null"})
    assert normalized["country"] == ""
    assert normalized["state"] == ""
    assert normalized["city"] == "Tulsa"


@pytest.mark.parametrize(
    "country", ["", "US", "us", "USA", "U.S.", "United States", "united states of america"]
)
def test_domestic_country_names(country):
    assert is_domestic_ship_to({"postal_code": "74103", "country": country}) is True


@pytest.mark.parametrize("country", ["Malaysia", "Canada", "Mexico", "MY", "United Kingdom"])
def test_foreign_country_names(country):
    assert is_domestic_ship_to({"postal_code": "40160", "country": country}) is False


def test_empty_address_is_not_domestic():
    assert is_domestic_ship_to(None) is False
    assert is_domestic_ship_to({}) is False
