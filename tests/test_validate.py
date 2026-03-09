"""Tests for the validation tool's GT item normalization."""

import sys
from pathlib import Path

# Allow import from tools/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from validate import _normalize_gt_item, _item_similarity


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
