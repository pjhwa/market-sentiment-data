"""
collect_morning_briefing 단위 테스트
python -m pytest collect/test_collect_morning_briefing.py -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collect.collect_morning_briefing import (
    validate_global_context,
    sanitize_global_context,
    _format_global_context_block,
    _format_symbol_block as _mb_format_symbol_block,
    _parse_rss_items,
    format_stage1_evidence_block,
    build_global_context_prompt,
    fetch_stage1_search_evidence,
)


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

    def test_more_than_three_issues_truncated_to_three(self):
        """Soft cap: keep first 3 valid issues rather than rejecting the whole payload."""
        data = {"issues": [_valid_issue(i) for i in range(1, 5)]}
        self.assertTrue(validate_global_context(data))
        self.assertEqual(len(data["issues"]), 3)
        self.assertEqual(data["issues"][0]["rank"], 1)
        self.assertEqual(data["issues"][2]["rank"], 3)

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

    def test_prompt_diet_no_event_hardcodes_in_global_block(self):
        """Global-context instructions must stay evidence-bound (no Hormuz/Fed/IPO catalogs)."""
        result = _format_global_context_block(self._ctx_with_one_issue())
        for banned in (
            "Strait of Hormuz",
            "Hormuz",
            "FOMC officials",
            "SpaceX IPO",
            "Unlisted catalyst applied",
            "scan training knowledge",
            "EQUALLY MANDATORY",
        ):
            self.assertNotIn(banned, result, f"event hardcode leaked into slim prompt: {banned}")
        # structural rules must remain
        self.assertIn("CURRENT STATE BINDING", result)
        self.assertIn("ASYMMETRIC IMPACT", result)
        self.assertLess(len(result), 8000, "global context block should stay compact after diet")


class TestStage1SearchQuality(unittest.TestCase):
    """Stage-1: mechanical evidence + soft sanitize (no category filler)."""

    def test_parse_rss_items_basic(self):
        xml = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item>
            <title>Fed holds rates steady amid sticky inflation</title>
            <link>https://www.reuters.com/example</link>
            <pubDate>Mon, 03 Aug 2026 12:00:00 GMT</pubDate>
          </item>
          <item>
            <title>Oil climbs on supply concerns</title>
            <link>https://www.reuters.com/oil</link>
            <pubDate>Mon, 03 Aug 2026 11:00:00 GMT</pubDate>
          </item>
        </channel></rss>"""
        items = _parse_rss_items(xml, feed_id="reuters_test", max_items=10)
        self.assertEqual(len(items), 2)
        self.assertIn("Fed holds", items[0]["title"])
        self.assertEqual(items[0]["source"], "reuters.com")
        self.assertTrue(items[0]["published"].startswith("2026-08-03"))

    def test_format_stage1_evidence_includes_titles(self):
        block = format_stage1_evidence_block([
            {
                "title": "US stocks rise on earnings optimism",
                "link": "https://www.cnbc.com/x",
                "published": "2026-08-03T10:00:00Z",
                "source": "cnbc.com",
                "feed_id": "cnbc_top",
            }
        ])
        self.assertIn("MECHANICAL SEARCH EVIDENCE", block)
        self.assertIn("US stocks rise on earnings optimism", block)
        self.assertIn("cnbc.com", block)

    def test_build_global_prompt_embeds_evidence(self):
        evidence = [{
            "title": "Chipmakers gain after export license news",
            "link": "https://www.reuters.com/chip",
            "published": "2026-08-03T09:00:00Z",
            "source": "reuters.com",
            "feed_id": "reuters_business",
        }]
        prompt = build_global_context_prompt(
            "2026-08-04 10:00 KST", "2026-08-04T01:00:00Z", evidence=evidence,
        )
        self.assertIn("Chipmakers gain after export license news", prompt)
        self.assertIn("SEARCH PROCEDURE", prompt)
        self.assertIn("live web search", prompt.lower())
        # no category quota language
        self.assertNotIn("EXACTLY 3", prompt)
        self.assertNotIn("KNOWN AMBIGUOUS", prompt)

    def test_sanitize_keeps_valid_drops_bad_partial(self):
        good = {
            "rank": 1,
            "tier": "breaking",
            "category": "central_bank",
            "title_en": "Fed holds rates",
            "title_ko": "연준 금리 동결",
            "current_state_en": "Fed kept rates unchanged.",
            "current_state_ko": "연준이 금리를 동결했다.",
            "direction": "stable_elevated",
            "summary_en": "Markets priced a hold.",
            "summary_ko": "시장은 동결을 반영.",
            "source_hint": "Reuters 2026-08-03",
            "confidence": "confirmed",
            "asymmetric_impact_en": "NVDA: neutral rates pause",
            "asymmetric_impact_ko": "NVDA: 중립",
            "impact_direction": "neutral",
            "market_insight_en": "Watch next CPI.",
            "market_insight_ko": "CPI 주시.",
        }
        bad = dict(good)
        bad["category"] = "not_a_category"
        bad["title_en"] = "junk"
        data = {"issues": [good, bad], "ongoing_no_update": []}
        out = sanitize_global_context(data)
        self.assertIsNotNone(out)
        self.assertEqual(len(out["issues"]), 1)
        self.assertEqual(out["issues"][0]["title_en"], "Fed holds rates")
        self.assertEqual(out["issues"][0]["rank"], 1)

    def test_sanitize_all_invalid_returns_none(self):
        data = {
            "issues": [{
                "category": "politics",
                "tier": "breaking",
                "confidence": "confirmed",
                "impact_direction": "watch",
                "direction": "escalating",
                "title_en": "x", "title_ko": "x",
                "current_state_en": "x", "current_state_ko": "x",
                "summary_en": "x", "summary_ko": "x",
                "asymmetric_impact_en": "x", "asymmetric_impact_ko": "x",
                "market_insight_en": "x", "market_insight_ko": "x",
                "source_hint": "blog",
            }],
        }
        self.assertIsNone(sanitize_global_context(data))
        self.assertFalse(validate_global_context(data))

    def test_earnings_category_allowed(self):
        iss = {
            "rank": 1,
            "tier": "breaking",
            "category": "earnings",
            "title_en": "PLTR post-earnings surge",
            "title_ko": "PLTR 실적 급등",
            "current_state_en": "Results beat; after-hours jump.",
            "current_state_ko": "실적 호조 후 급등.",
            "direction": "escalating",
            "summary_en": "After-hours reaction.",
            "summary_ko": "애프터 반응.",
            "source_hint": "CNBC 2026-08-04",
            "confidence": "developing",
            "asymmetric_impact_en": "PLTR: positive on beat",
            "asymmetric_impact_ko": "PLTR: 상방",
            "impact_direction": "positive",
            "market_insight_en": "Watch open follow-through.",
            "market_insight_ko": "시초가 주시.",
        }
        out = sanitize_global_context({"issues": [iss]})
        self.assertIsNotNone(out)
        self.assertEqual(out["issues"][0]["category"], "earnings")


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
