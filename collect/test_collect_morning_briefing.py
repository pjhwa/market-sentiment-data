"""
collect_morning_briefing 단위 테스트
python -m pytest collect/test_collect_morning_briefing.py -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collect.collect_morning_briefing import validate_global_context, _format_global_context_block


def _valid_issue(rank=1):
    return {
        "rank": rank,
        "tier": "breaking",
        "category": "trade_tariff",
        "title_en": "US expands chip export controls",
        "title_ko": "미국 반도체 수출통제 확대",
        "current_state_en": "BIS shifted to case-by-case licensing plus 25% tariff as of Jan 2026.",
        "current_state_ko": "BIS가 2026년 1월부로 케이스바이케이스 라이선스 + 25% 관세로 전환.",
        "direction": "stable_elevated",
        "summary_en": "The US Commerce Department added 5 countries. Markets concerned about NVDA.",
        "summary_ko": "미 상무부가 5개국을 추가했다. NVDA 영향 우려.",
        "source_hint": "Reuters 2026-06-03",
        "confidence": "confirmed",
        "asymmetric_impact_en": "NVDA: negative on denial / positive on approval; MU: neutral (demand-driven).",
        "asymmetric_impact_ko": "NVDA: 거부 시 하방 / 승인 시 상방; MU: 중립(수요 주도).",
        "impact_direction": "negative",
        "market_insight_en": "Watch for BIS rule updates; NVDA approval headlines are short-term triggers.",
        "market_insight_ko": "BIS 룰 업데이트 주시. NVDA 승인 헤드라인이 단기 트리거.",
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

    def test_invalid_direction_fails(self):
        issue = _valid_issue()
        issue["direction"] = "unclear"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_missing_current_state_en_fails(self):
        issue = _valid_issue()
        del issue["current_state_en"]
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_missing_asymmetric_impact_fails(self):
        issue = _valid_issue()
        del issue["asymmetric_impact_en"]
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


from collect.collect_morning_briefing import parse_global_context


class TestParseGlobalContext(unittest.TestCase):

    def _valid_json(self):
        return '''
        {
          "fetched_at": "2026-06-03T22:15:00Z",
          "search_window": "48h",
          "issues": [
            {
              "rank": 1,
              "tier": "breaking",
              "category": "trade_tariff",
              "title_en": "US chip controls expanded",
              "title_ko": "미국 칩 수출 확대",
              "current_state_en": "BIS shifted to case-by-case licensing plus 25% tariff.",
              "current_state_ko": "BIS가 케이스바이케이스 라이선스 + 25% 관세로 전환.",
              "direction": "stable_elevated",
              "summary_en": "Commerce Dept added 5 countries. Verified by Reuters.",
              "summary_ko": "상무부가 5개국을 추가했다.",
              "source_hint": "Reuters 2026-06-03",
              "confidence": "confirmed",
              "asymmetric_impact_en": "NVDA: negative on denial / positive on approval; MU: neutral.",
              "asymmetric_impact_ko": "NVDA: 거부 시 하방 / 승인 시 상방; MU: 중립.",
              "impact_direction": "negative",
              "market_insight_en": "Watch BIS rule updates as short-term NVDA triggers.",
              "market_insight_ko": "BIS 룰 업데이트가 NVDA 단기 트리거."
            }
          ],
          "ongoing_no_update": ["central_bank"]
        }
        '''

    def test_valid_json_returns_dict(self):
        result = parse_global_context(self._valid_json())
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result.get("issues", [])), 1)

    def test_empty_string_returns_empty_dict(self):
        self.assertEqual(parse_global_context(""), {})

    def test_no_json_in_text_returns_empty_dict(self):
        self.assertEqual(parse_global_context("sorry I cannot search the web right now"), {})

    def test_invalid_json_returns_empty_dict(self):
        self.assertEqual(parse_global_context("{not valid json}"), {})

    def test_invalid_structure_returns_empty_dict(self):
        self.assertEqual(parse_global_context('{"data": []}'), {})

    def test_json_embedded_in_prose_extracted(self):
        text = 'Here is the result:\n' + self._valid_json() + '\nEnd.'
        result = parse_global_context(text)
        self.assertIsInstance(result, dict)
        self.assertIn("issues", result)


class TestFormatGlobalContextBlock(unittest.TestCase):

    def _ctx_with_one_issue(self):
        return {
            "fetched_at": "2026-06-03T22:15:00Z",
            "issues": [{
                "rank": 1,
                "tier": "breaking",
                "category": "trade_tariff",
                "title_en": "US chip controls expanded",
                "source_hint": "Reuters 2026-06-03",
                "confidence": "confirmed",
                "summary_en": "Commerce Dept added 5 countries.",
                "us_stock_impact_en": "NVDA negative.",
            }],
        }

    def test_empty_issues_returns_fallback_string(self):
        result = _format_global_context_block({"issues": []})
        self.assertIn("No verified global issues", result)

    def test_empty_dict_returns_fallback_string(self):
        result = _format_global_context_block({})
        self.assertIn("No verified global issues", result)

    def test_valid_ctx_contains_title(self):
        result = _format_global_context_block(self._ctx_with_one_issue())
        self.assertIn("US chip controls expanded", result)

    def test_valid_ctx_contains_source_hint(self):
        result = _format_global_context_block(self._ctx_with_one_issue())
        self.assertIn("Reuters 2026-06-03", result)

    def test_developing_confidence_shows_tag(self):
        ctx = self._ctx_with_one_issue()
        ctx["issues"][0]["confidence"] = "developing"
        result = _format_global_context_block(ctx)
        self.assertIn("[DEVELOPING]", result)

    def test_confirmed_confidence_no_tag(self):
        result = _format_global_context_block(self._ctx_with_one_issue())
        self.assertNotIn("[CONFIRMED]", result)

    def test_ongoing_no_update_shown(self):
        ctx = self._ctx_with_one_issue()
        ctx["ongoing_no_update"] = ["central_bank", "ai_regulation"]
        result = _format_global_context_block(ctx)
        self.assertIn("central_bank", result)

    def test_instructions_included(self):
        result = _format_global_context_block(self._ctx_with_one_issue())
        self.assertIn("big_picture.summary", result)


if __name__ == "__main__":
    unittest.main()
