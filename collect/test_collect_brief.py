"""
collect_brief 검증 함수 단위 테스트
python -m pytest collect/test_collect_brief.py -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collect.collect_brief import validate_brief, _format_symbol_block, WATCHLIST


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


if __name__ == "__main__":
    unittest.main()
