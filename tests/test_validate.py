"""Tests for validation comparison helpers."""

import sys
from pathlib import Path

# tools/ isn't a package; add it to sys.path so we can import validate
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from validate import (
    _normalize_gt_item,
    _item_similarity,
    fuzzy_match,
    normalize_company_name,
)


# ---------------------------------------------------------------------------
# GT item normalization tests
# ---------------------------------------------------------------------------

class TestNormalizeGtItem:
    """Test extraction of structured fields from GT part_number and description."""

    def test_sleeve_part_number(self):
        item = _normalize_gt_item({
            "part_number": "S-12.34-38-50-10",
            "description": 'reg half sole, 12-3/4" ID, 3/8" w/t, A572 GR50, 10\' long.',
        })
        assert item["product_type"] == "sleeve"
        assert item["diameter"] == 12.34
        assert item["wall_thickness"] == 0.375
        assert item["grade"] == 50
        assert item["length_ft"] == 10.0

    def test_girth_weld_part_number(self):
        item = _normalize_gt_item({
            "part_number": "G-16-12-50-12",
            "description": 'girth weld sleeve, 16" ID, 1/2" w/t, A572 GR50, 12" long.',
        })
        assert item["product_type"] == "girth_weld"
        assert item["diameter"] == 16.0
        assert item["wall_thickness"] == 0.5
        assert item["grade"] == 50
        assert item["length_ft"] == 12.0

    def test_oversleeve_from_description(self):
        item = _normalize_gt_item({
            "part_number": "S-14.34-38-50-10",
            "description": 'ovsz half sole, 14-3/4" ID, 3/8" w/t, A572 GR50, 10\' long.',
        })
        assert item["product_type"] == "oversleeve"
        assert item["diameter"] == 14.34

    def test_backing_strip_is_accessory(self):
        item = _normalize_gt_item({
            "part_number": "Backing Strip-1.25",
            "description": '1-1/4" x 16GA, 10\' long, precrimped.',
        })
        assert item["product_type"] == "accessory"

    def test_bag_from_gtw_prefix(self):
        item = _normalize_gt_item({
            "part_number": "GTW 40-48in (14K)",
            "description": "Geotextile Bag Weight 40-48in Pipe MAX 14K",
        })
        assert item["product_type"] == "bag"

    def test_omegawrap_from_ow_prefix(self):
        item = _normalize_gt_item({
            "part_number": "OW-ACCESSORY KIT",
            "description": "Accessory Kit for OmegaWrap application",
        })
        assert item["product_type"] == "omegawrap"

    def test_516_wall_thickness_code(self):
        item = _normalize_gt_item({
            "part_number": "S-6.58-516-50-10",
            "description": 'reg. half sole, 6-5/8" ID, 5/16" w/t, A572 GR50, 10\' long.',
        })
        assert item["wall_thickness"] == 0.3125

    def test_milling_and_painting_suffix(self):
        item = _normalize_gt_item({
            "part_number": "S-13.12-38-50-1-MP",
            "description": "half sole, milled and painted",
        })
        assert item.get("milling") is True
        assert item.get("painting") is True

    def test_girth_weld_length_inches_to_feet(self):
        item = _normalize_gt_item({
            "part_number": "MISC AE (4030)",
            "description": 'girth weld sleeve, 16" ID, 1/4" w/t, A572 GR65, 24" long.',
        })
        assert item["product_type"] == "girth_weld"
        assert item["length_ft"] == 2.0  # 24" / 12

    def test_preserves_existing_fields(self):
        item = _normalize_gt_item({
            "part_number": "S-12.34-38-50-10",
            "product_type": "custom_type",
            "diameter": 99.0,
        })
        assert item["product_type"] == "custom_type"
        assert item["diameter"] == 99.0


class TestItemSimilarity:
    """Test that normalized GT items match parser output items."""

    def test_sleeve_match(self):
        gt = _normalize_gt_item({
            "part_number": "S-12.34-38-50-10",
            "description": 'reg half sole, 12-3/4" ID, 3/8" w/t, A572 GR50, 10\' long.',
            "quantity": 5,
        })
        our = {
            "product_type": "sleeve",
            "diameter": 12.75,
            "quantity": 5,
            "description": '12-3/4" half sole 3/8" WT GR50',
        }
        score = _item_similarity(gt, our)
        assert score > 0.3, f"Expected match score > 0.3, got {score}"

    def test_girth_weld_match(self):
        gt = _normalize_gt_item({
            "part_number": "G-16-12-50-12",
            "description": 'girth weld sleeve, 16" ID, 1/2" w/t, A572 GR50, 12" long.',
            "quantity": 10,
        })
        our = {
            "product_type": "girth_weld",
            "diameter": 16.0,
            "quantity": 10,
            "description": '16" girth weld 1/2" WT GR50',
        }
        score = _item_similarity(gt, our)
        assert score > 0.3, f"Expected match score > 0.3, got {score}"

    def test_no_match_different_products(self):
        gt = _normalize_gt_item({
            "part_number": "S-12.34-38-50-10",
            "description": 'reg half sole, 12-3/4" ID',
            "quantity": 5,
        })
        our = {
            "product_type": "bag",
            "diameter": None,
            "quantity": 252,
            "description": "Geotextile Bag Weight",
        }
        score = _item_similarity(gt, our)
        assert score < 0.3, f"Expected no match, got score {score}"


# ---------------------------------------------------------------------------
# Company name normalization tests
# ---------------------------------------------------------------------------

class TestNormalizeCompanyName:
    def test_strips_inc(self):
        assert normalize_company_name("Allan Edwards Inc") == "allan edwards"
        assert normalize_company_name("Allan Edwards Inc.") == "allan edwards"

    def test_strips_llc(self):
        assert normalize_company_name("Arrow Pump & Supply of Seminole, LLC") == (
            "arrow pump and supply of seminole"
        )

    def test_strips_lp(self):
        assert normalize_company_name("Buckeye Partners, L.P.") == "buckeye partners"
        assert normalize_company_name("Buckeye Partners L.P.") == "buckeye partners"
        assert normalize_company_name("Buckeye Partners LP") == "buckeye partners"

    def test_strips_ltd(self):
        assert normalize_company_name("Bromby Welding Ltd.") == "bromby welding"
        assert normalize_company_name("Bromby Welding Ltd") == "bromby welding"

    def test_strips_company(self):
        assert normalize_company_name("South Jersey Gas Company") == "south jersey gas"
        assert normalize_company_name("DTE Gas Company") == "dte gas"

    def test_strips_corp(self):
        assert normalize_company_name("Black Hills Corp.") == "black hills"

    def test_strips_multiple_suffixes(self):
        assert normalize_company_name("Price Gregory International, LLC") == (
            "price gregory"
        )
        assert normalize_company_name("Bella Welding Mobile Services, LLC") == (
            "bella welding"
        )

    def test_normalizes_ampersand(self):
        assert normalize_company_name("Arrow Pump & Supply") == "arrow pump and supply"

    def test_normalizes_slashes(self):
        assert normalize_company_name("Boardwalk / Gulf South Pipeline Company, LP") == (
            "boardwalk gulf south"
        )

    def test_strips_pipeline(self):
        assert normalize_company_name("Phillips 66 Pipeline LLC") == "phillips 66"
        assert normalize_company_name("Centerpoint Energy Pipeline") == (
            "centerpoint energy"
        )

    def test_empty_and_none(self):
        assert normalize_company_name(None) == ""
        assert normalize_company_name("") == ""

    def test_preserves_core_name(self):
        assert normalize_company_name("BP") == "bp"
        assert normalize_company_name("NIPSCO") == "nipsco"
        assert normalize_company_name("Kinder Morgan") == "kinder morgan"


class TestFuzzyMatchCompany:
    def test_exact_after_normalization(self):
        assert fuzzy_match("Buckeye Partners L.P.", "Buckeye Partners, L.P.", company=True) == "exact_match"
        assert fuzzy_match("Bromby Welding Ltd.", "Bromby Welding Ltd", company=True) == "exact_match"
        assert fuzzy_match("South Jersey Gas Company", "South Jersey Gas", company=True) == "exact_match"
        assert fuzzy_match("Price Gregory", "Price Gregory International, LLC", company=True) == "exact_match"

    def test_close_match_on_containment(self):
        assert fuzzy_match("Buckeye Partners, L.P.", "Buckeye", company=True) == "close_match"

    def test_close_match_on_overlap(self):
        result = fuzzy_match("Texas Gas Transmission", "Boardwalk Texas Gas Transmission", company=True)
        assert result == "close_match"

    def test_missing_when_one_empty(self):
        assert fuzzy_match("Some Company", None, company=True) == "missing"
        assert fuzzy_match(None, "Some Company", company=True) == "missing"

    def test_backward_compatible_without_company_flag(self):
        # Without company=True, should use old behavior
        result = fuzzy_match("South Jersey Gas Company", "South Jersey Gas")
        assert result == "close_match"  # token overlap, not exact
