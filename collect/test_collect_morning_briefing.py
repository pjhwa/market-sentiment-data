"""
collect_morning_briefing 단위 테스트
python -m pytest collect/test_collect_morning_briefing.py -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collect.collect_morning_briefing import validate_global_context


def _valid_issue(rank=1):
    return {
        "rank": rank,
        "tier": "breaking",
        "category": "trade_tariff",
        "title_en": "US expands chip export controls",
        "title_ko": "미국 반도체 수출통제 확대",
        "summary_en": "The US Commerce Department added 5 countries. Markets concerned about NVDA.",
        "summary_ko": "미 상무부가 5개국을 추가했다. NVDA 영향 우려.",
        "source_hint": "Reuters 2026-06-03",
        "confidence": "confirmed",
        "us_stock_impact_en": "NVDA and MU face direct export headwind.",
        "us_stock_impact_ko": "NVDA·MU 직접 영향.",
        "impact_direction": "negative",
    }


class TestValidateGlobalContext(unittest.TestCase):

    def test_valid_single_issue_passes(self):
        self.assertTrue(validate_global_context({"issues": [_valid_issue()]}))

    def test_valid_three_issues_passes(self):
        data = {"issues": [_valid_issue(1), _valid_issue(2), _valid_issue(3)]}
        self.assertTrue(validate_global_context(data))

    def test_empty_issues_passes(self):
        self.assertTrue(validate_global_context({"issues": []}))

    def test_more_than_three_issues_fails(self):
        data = {"issues": [_valid_issue(i) for i in range(1, 5)]}
        self.assertFalse(validate_global_context(data))

    def test_missing_issues_key_fails(self):
        self.assertFalse(validate_global_context({}))

    def test_invalid_category_fails(self):
        issue = _valid_issue()
        issue["category"] = "politics"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_invalid_tier_fails(self):
        issue = _valid_issue()
        issue["tier"] = "new"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_invalid_confidence_fails(self):
        issue = _valid_issue()
        issue["confidence"] = "maybe"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_invalid_impact_direction_fails(self):
        issue = _valid_issue()
        issue["impact_direction"] = "bad"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_missing_title_en_fails(self):
        issue = _valid_issue()
        del issue["title_en"]
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_missing_summary_ko_fails(self):
        issue = _valid_issue()
        del issue["summary_ko"]
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_non_dict_input_fails(self):
        self.assertFalse(validate_global_context("not a dict"))

    def test_ongoing_no_update_field_optional(self):
        data = {
            "issues": [_valid_issue()],
            "ongoing_no_update": ["central_bank"],
        }
        self.assertTrue(validate_global_context(data))


if __name__ == "__main__":
    unittest.main()
