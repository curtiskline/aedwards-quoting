"""Carrier grades must select the confirmed sleeve material and price tier."""

from decimal import Decimal
from email.message import EmailMessage

import pytest

from allenedwards.parser import parse_rfq_multi
from allenedwards.pricing import calculate_sleeve_price, generate_quote
from allenedwards.providers.mock import MockProvider


def parse_items(tmp_path, items, body, *, legacy=False):
    message = EmailMessage()
    message["Subject"] = "Sleeve RFQ"
    message.set_content(body)
    path = tmp_path / "rfq.eml"
    path.write_bytes(message.as_bytes())
    response = {"items": items} if legacy else {"quotes": [{"items": items}]}
    return parse_rfq_multi(path, MockProvider(response))[0]


def sleeve(description, grade):
    return {
        "product_type": "sleeve", "description": description, "grade": grade,
        "quantity": 5, "diameter": "10.75", "wall_thickness": "0.375", "length_ft": 10,
    }


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("designation,expected", [
    ("API 5L GR B", 50), ("API 5L X-42", 50), ("API 5L X-46", 50),
    ("API 5L X-52", 50), ("API 5L X-65", 65), ("API 5L X-70", 65),
    ("x52", 50), ("X 52", 50), ("X–52", 50), ("API 5L Grade B", 50),
])
@pytest.mark.parametrize("location", ["description", "notes", "source", "grade"])
def test_confirmed_api_5l_mapping_overrides_model_grade(tmp_path, legacy, designation, expected, location):
    item = sleeve("Steel sleeve", "65" if expected == 50 else "50")
    body = f"Please quote sleeves for {designation} carrier pipe."
    if location != "source":
        item[location] = designation
        body = "Please quote the following sleeves."
    rfq = parse_items(tmp_path, [item], body, legacy=legacy)
    assert rfq.items[0].grade == expected


@pytest.mark.parametrize("designation", ["API 5L X-56", "API 5L X-60"])
@pytest.mark.parametrize("model_grade", [50, 65, None])
def test_pending_api_5l_mapping_preserves_model_and_logs_disagreement(tmp_path, caplog, designation, model_grade):
    rfq = parse_items(tmp_path, [sleeve(designation, model_grade)], f"Quote {designation} sleeves")
    assert rfq.items[0].grade == (model_grade if model_grade is not None else 50)
    assert ("retaining model grade" in caplog.text) == (model_grade != 65)


@pytest.mark.parametrize("model_grade", [None, "", "null"])
def test_unspecified_grade_defaults_to_50(tmp_path, model_grade):
    rfq = parse_items(tmp_path, [sleeve("Steel sleeves", model_grade)], "Please quote sleeves")
    assert rfq.items[0].grade == 50


def test_mixed_grade_request_keeps_each_item_mapping(tmp_path):
    rfq = parse_items(tmp_path, [sleeve("X52 sleeves", 65), sleeve("X70 sleeves", 50)],
                      "Quote X52 sleeves and X70 sleeves.")
    assert [item.grade for item in rfq.items] == [50, 65]


@pytest.mark.parametrize("description", ["A572 GR65 sleeves", "X70 sleeves", "X52 and X70 sleeves"])
def test_source_grade_overrides_model_generated_description(tmp_path, description):
    rfq = parse_items(tmp_path, [sleeve(description, 65)], "Carrier pipe: API 5L X52")
    assert rfq.items[0].grade == 50


def test_explicit_sleeve_material_is_preserved(tmp_path):
    rfq = parse_items(tmp_path, [sleeve("A572 GR65 sleeves", 65)],
                      "Quote A572 GR65 sleeves for API 5L X52 carrier pipe.")
    assert rfq.items[0].grade == 65


@pytest.mark.parametrize("dimension", ["x 60-foot long", "x 52 inches", 'x 52"', "x 70 mm"])
def test_dimensions_are_not_carrier_grades(tmp_path, dimension):
    rfq = parse_items(tmp_path, [sleeve(f"36 {dimension}", 65)], f"Quote a 36 {dimension} sleeve")
    assert rfq.items[0].grade == 65


def test_mixed_source_grades_do_not_apply_one_grade_to_unrelated_items(tmp_path):
    rfq = parse_items(tmp_path, [sleeve("A572 GR65 sleeves", 65)],
                      "Quote X52 and X70 sleeves for two projects.")
    assert rfq.items[0].grade == 65


def test_x52_normalization_selects_gr50_description_and_price(tmp_path):
    rfq = parse_items(tmp_path, [sleeve("API 5L X52 sleeves", "65")], "Quote X52 sleeves")
    quote = generate_quote(rfq, "126-TEST")
    line = next(line for line in quote.line_items if not line.is_note)
    gr50_price, _, _ = calculate_sleeve_price(10.75, 0.375, 50, 10)
    gr65_price, _, _ = calculate_sleeve_price(10.75, 0.375, 65, 10)
    assert gr50_price != gr65_price
    assert "A572 GR50" in line.description
    assert line.unit_price == gr50_price
    assert line.total == gr50_price * Decimal(5)
