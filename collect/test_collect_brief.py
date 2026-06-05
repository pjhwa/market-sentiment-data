"""
collect_brief 검증 함수 단위 테스트
python -m pytest collect/test_collect_brief.py -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collect.collect_brief import validate_brief, _format_symbol_block, WATCHLIST, validate_output_quality


class TestValidateBriefBilingual(unittest.TestCase):
    def _valid_brief(self):
        return {
            "market_brief": {
                "summary_en": "Market holds constructive regime despite distribution pressure.",
                "summary_ko": "분배 압력에도 불구하고 시장은 건설적 체제를 유지 중.",
                "tone": "cautious",
                "key_themes_en": ["AI infrastructure", "Big tech growth"],
                "key_themes_ko": ["AI 인프라", "빅테크 성장"],
                "watch_points_en": "SPY at 4 distribution days — watch for a 5th.",
                "watch_points_ko": "SPY 분배일 4일 — 5일째 주의.",
            },
            "symbol_briefs": [
                {
                    "symbol": "TSLA",
                    "setup_quality": "A",
                    "brief_en": "Stage2 6/7 with UPTREND structure. Robotaxi enthusiasm drives optimistic social sentiment.",
                    "brief_ko": "Stage2 6/7에 UPTREND 구조 유지. 로보택시 열광으로 소셜 낙관적.",
                    "key_risk_en": "RS 45.1 suggests underperformance vs. market.",
                    "key_risk_ko": "RS 45.1로 시장 대비 약세 가능성.",
                    "key_opportunity_en": "Robotaxi event momentum.",
                    "key_opportunity_ko": "로보택시 이벤트 모멘텀 기대.",
                    "action_bias": "watch",
                }
            ],
        }

    def test_valid_bilingual_brief_passes(self):
        self.assertTrue(validate_brief(self._valid_brief()))

    def test_missing_summary_en_fails(self):
        brief = self._valid_brief()
        del brief["market_brief"]["summary_en"]
        self.assertFalse(validate_brief(brief))

    def test_missing_brief_ko_fails(self):
        brief = self._valid_brief()
        del brief["symbol_briefs"][0]["brief_ko"]
        self.assertFalse(validate_brief(brief))

    def test_old_summary_field_fails(self):
        brief = self._valid_brief()
        del brief["market_brief"]["summary_en"]
        del brief["market_brief"]["summary_ko"]
        brief["market_brief"]["summary"] = "old field"
        self.assertFalse(validate_brief(brief))

    def test_missing_key_risk_en_fails(self):
        brief = self._valid_brief()
        del brief["symbol_briefs"][0]["key_risk_en"]
        self.assertFalse(validate_brief(brief))

    def test_missing_key_themes_ko_fails(self):
        brief = self._valid_brief()
        del brief["market_brief"]["key_themes_ko"]
        self.assertFalse(validate_brief(brief))

    def test_invalid_tone_fails(self):
        brief = self._valid_brief()
        brief["market_brief"]["tone"] = "invalid_tone"
        self.assertFalse(validate_brief(brief))


class TestFormatSymbolBlockEarningsFilter(unittest.TestCase):
    def _make_tech(self, sym, days_until, earn_date="2026-06-10", already=False):
        """Minimal tech dict for _format_symbol_block."""
        d = {
            "price": 100.0,
            "change_pct_prev_day": 0.5,
            "high_52w_price": 120.0,
            "price_date": "2026-06-04",
            "stage2_score": 5,
            "rs_score": 60.0,
            "market_structure": "UPTREND",
            "monthly_phase": "ADVANCING",
            "ema200_slope": 0.001,
            "pct_from_52w_high": -5.0,
            "pullback_pct": 3.0,
            "pct_vs_entry": 2.0,
            "entry": 98.0,
            "rsi14": 55.0,
            "ema200": 90.0,
            "ema50": 95.0,
            "ema21": 98.0,
            "atr14": 2.5,
            "price_above_emas": True,
            "ema200_rising": True,
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
            "eps_estimate": 1.23,
            "already_reported_possible": already,
        }
        return {
            "symbol_detail": {sym: d},
            "prepost": {},
        }

    def test_earnings_within_14_days_included(self):
        tech = self._make_tech("NVDA", days_until=7)
        result = _format_symbol_block(tech, {})
        self.assertIn("실적=", result)
        self.assertIn("2026-06-10", result)

    def test_earnings_exactly_14_days_included(self):
        tech = self._make_tech("NVDA", days_until=14)
        result = _format_symbol_block(tech, {})
        self.assertIn("실적=", result)

    def test_earnings_15_days_omitted(self):
        tech = self._make_tech("NVDA", days_until=15)
        result = _format_symbol_block(tech, {})
        self.assertNotIn("실적=", result)
        self.assertNotIn("30일 이내 없음", result)

    def test_no_earnings_date_omitted(self):
        tech = self._make_tech("NVDA", days_until=None, earn_date=None)
        result = _format_symbol_block(tech, {})
        self.assertNotIn("실적=", result)
        self.assertNotIn("30일 이내 없음", result)

    def test_already_reported_always_shown(self):
        tech = self._make_tech("NVDA", days_until=0, earn_date="2026-06-05", already=True)
        result = _format_symbol_block(tech, {})
        self.assertIn("이미발표됨", result)


class TestValidateOutputQuality(unittest.TestCase):
    def _brief(self, summary_ko="정상 요약.", brief_ko="정상 설명."):
        return {
            "market_brief": {
                "summary_en": "Normal summary.",
                "summary_ko": summary_ko,
                "tone": "neutral",
                "key_themes_en": ["theme"],
                "key_themes_ko": ["테마"],
                "watch_points_en": "Watch SPY.",
                "watch_points_ko": "SPY 주시.",
            },
            "symbol_briefs": [
                {
                    "symbol": "TSLA",
                    "setup_quality": "B",
                    "brief_en": "Normal brief.",
                    "brief_ko": brief_ko,
                    "key_risk_en": "Risk.",
                    "key_risk_ko": "리스크.",
                    "key_opportunity_en": "Opportunity.",
                    "key_opportunity_ko": "기회.",
                    "action_bias": "watch",
                }
            ],
        }

    # ── Causal language tests ──────────────────────────────────────────────

    def test_clean_brief_has_no_violations(self):
        violations = validate_output_quality(self._brief())
        self.assertEqual(violations, [])

    def test_cross_domain_korean_connective_detected(self):
        bad = self._brief(
            summary_ko="미중 칩 관세가 개별 허가제로 바뀌는데 비트코인이 14% 급락했다."
        )
        violations = validate_output_quality(bad)
        self.assertTrue(any("인과" in v or "causal" in v.lower() for v in violations),
                        f"Expected causal violation, got: {violations}")

    def test_cross_domain_english_connective_detected(self):
        bad = self._brief()
        bad["market_brief"]["summary_en"] = (
            "US chip tariffs shifted to licensing while Bitcoin dropped 14%."
        )
        violations = validate_output_quality(bad)
        self.assertTrue(any("causal" in v.lower() or "인과" in v for v in violations),
                        f"Expected causal violation, got: {violations}")

    def test_same_domain_connective_allowed(self):
        ok = self._brief()
        ok["market_brief"]["summary_en"] = "SPY held gains but QQQ lagged slightly."
        violations = validate_output_quality(ok)
        self.assertEqual(violations, [])

    def test_causal_in_symbol_brief_ko_detected(self):
        bad = self._brief(
            brief_ko="관세 정책이 강화되는데 TSLA는 오히려 급등했다."
        )
        violations = validate_output_quality(bad)
        self.assertTrue(len(violations) > 0, f"Expected violation, got: {violations}")

    # ── Japanese character tests ───────────────────────────────────────────

    def test_hiragana_in_ko_field_detected(self):
        bad = self._brief(summary_ko="시장은 あいう 조정 중.")
        violations = validate_output_quality(bad)
        self.assertTrue(any("일본어" in v or "japanese" in v.lower() for v in violations),
                        f"Expected Japanese violation, got: {violations}")

    def test_katakana_in_ko_field_detected(self):
        bad = self._brief(brief_ko="TSLA アイウ 전략 지속.")
        violations = validate_output_quality(bad)
        self.assertTrue(any("일본어" in v or "japanese" in v.lower() for v in violations),
                        f"Expected Japanese violation, got: {violations}")

    def test_hangul_only_text_passes(self):
        ok = self._brief(
            summary_ko="건설적 레짐 속 SPY 분배 경고.",
            brief_ko="TSLA Stage2 5/7 UPTREND 유지.",
        )
        violations = validate_output_quality(ok)
        self.assertEqual(violations, [])

    def test_japanese_in_en_field_not_flagged(self):
        ok = self._brief()
        ok["market_brief"]["summary_en"] = "Market rises (see: アイウ reference)."
        violations = validate_output_quality(ok)
        jp_violations = [v for v in violations if "일본어" in v or "japanese" in v.lower()]
        self.assertEqual(jp_violations, [])


if __name__ == "__main__":
    unittest.main()
