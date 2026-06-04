"""
collect_morning_briefing 단위 테스트
python -m pytest collect/test_collect_morning_briefing.py -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collect.collect_morning_briefing import validate_global_context, _format_global_context_block, _format_symbol_block as _mb_format_symbol_block


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


class TestMorningBriefingEarningsFilter(unittest.TestCase):
    def _make_data(self, sym, days_until, earn_date="2026-06-10", already=False):
        d = {
            "price": 200.0,
            "change_pct_prev_day": -0.3,
            "high_52w_price": 250.0,
            "price_date": "2026-06-04",
            "stage2_score": 4,
            "rs_score": 55.0,
            "market_structure": "NEUTRAL",
            "monthly_phase": "ADVANCING",
            "ema200_slope": 0.0,
            "pct_from_52w_high": -10.0,
            "pullback_pct": 5.0,
            "pct_vs_entry": None,
            "entry": 0.0,
            "rsi14": 50.0,
            "ema200": 180.0,
            "ema50": 190.0,
            "ema21": 195.0,
            "atr14": 3.0,
            "price_above_emas": True,
            "ema200_rising": False,
            "volume_contracting": False,
            "near_52w_high": False,
            "bear_flag": False,
            "rsi_divergence_bullish": False,
            "rsi_divergence_bearish": False,
            "gc_above": False,
            "gc_breakout": False,
            "gc_retest": False,
            "earnings_date": earn_date if days_until is not None else None,
            "days_until_earnings": days_until,
            "eps_estimate": 2.50,
            "already_reported_possible": already,
        }
        return {
            "symbol_detail": {sym: d},
            "prepost": {},
            "sentiment": {"symbols": []},
        }

    def test_earnings_within_14_days_included(self):
        data = self._make_data("NVDA", days_until=5)
        result = _mb_format_symbol_block(data)
        self.assertIn("실적발표=", result)
        self.assertIn("2026-06-10", result)

    def test_earnings_exactly_14_days_included(self):
        data = self._make_data("NVDA", days_until=14)
        result = _mb_format_symbol_block(data)
        self.assertIn("실적발표=", result)

    def test_earnings_15_days_omitted(self):
        data = self._make_data("NVDA", days_until=15)
        result = _mb_format_symbol_block(data)
        self.assertNotIn("실적발표=", result)
        self.assertNotIn("30일이내없음", result)
        self.assertNotIn("해당없음", result)

    def test_no_earnings_date_omitted(self):
        data = self._make_data("NVDA", days_until=None, earn_date=None)
        result = _mb_format_symbol_block(data)
        self.assertNotIn("실적발표=", result)
        self.assertNotIn("해당없음", result)

    def test_already_reported_always_shown(self):
        data = self._make_data("NVDA", days_until=0, earn_date="2026-06-05", already=True)
        result = _mb_format_symbol_block(data)
        self.assertIn("이미발표됨", result)


if __name__ == "__main__":
    unittest.main()
