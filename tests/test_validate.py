"""Tests for validation comparison helpers."""

import sys
from pathlib import Path

# tools/ isn't a package; add it to sys.path so we can import validate
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from validate import fuzzy_match, normalize_company_name


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
