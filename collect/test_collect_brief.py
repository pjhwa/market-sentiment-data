"""
collect_brief 검증 함수 단위 테스트
python -m pytest collect/test_collect_brief.py -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collect.collect_brief import validate_brief


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


if __name__ == "__main__":
    unittest.main()
