"""
collect_sentiment 슬롯 감지·파일명 단위 테스트
python -m pytest collect/test_collect_sentiment.py -v
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import collect_sentiment as cs


class TestDetectSlot(unittest.TestCase):
    def test_pre_open_at_13_utc(self):
        dt = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
        self.assertEqual(cs.detect_slot(dt), "pre_open")

    def test_post_close_at_21_utc(self):
        dt = datetime(2026, 5, 21, 21, 0, tzinfo=timezone.utc)
        self.assertEqual(cs.detect_slot(dt), "post_close")

    def test_post_close_at_midnight_utc(self):
        dt = datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(cs.detect_slot(dt), "post_close")

    def test_env_override_pre_open(self):
        dt = datetime(2026, 5, 21, 21, 0, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"SENTIMENT_SLOT": "pre_open"}):
            self.assertEqual(cs.detect_slot(dt), "pre_open")

    def test_env_override_post_close(self):
        dt = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"SENTIMENT_SLOT": "post_close"}):
            self.assertEqual(cs.detect_slot(dt), "post_close")

    def test_boundary_lower_pre_open(self):
        dt = datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(cs.detect_slot(dt), "pre_open")

    def test_boundary_below_lower_post_close(self):
        dt = datetime(2026, 5, 21, 8, 59, tzinfo=timezone.utc)
        self.assertEqual(cs.detect_slot(dt), "post_close")

    def test_boundary_upper_pre_open(self):
        dt = datetime(2026, 5, 21, 17, 59, tzinfo=timezone.utc)
        self.assertEqual(cs.detect_slot(dt), "pre_open")

    def test_boundary_upper_post_close(self):
        dt = datetime(2026, 5, 21, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(cs.detect_slot(dt), "post_close")

    def test_invalid_env_override_falls_back_to_time(self):
        dt = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"SENTIMENT_SLOT": "invalid_value"}):
            self.assertEqual(cs.detect_slot(dt), "pre_open")


class TestHistoryFilename(unittest.TestCase):
    def test_pre_open_filename(self):
        path = cs.history_filename("2026-05-21", "pre_open")
        self.assertEqual(path.name, "2026-05-21_pre_open.json")

    def test_post_close_filename(self):
        path = cs.history_filename("2026-05-21", "post_close")
        self.assertEqual(path.name, "2026-05-21_post_close.json")


class TestComputeIntradayShift(unittest.TestCase):
    def test_heating(self):
        self.assertEqual(cs.compute_intraday_shift(0, 1), "heating")

    def test_cooling(self):
        self.assertEqual(cs.compute_intraday_shift(1, 0), "cooling")

    def test_stable(self):
        self.assertEqual(cs.compute_intraday_shift(1, 1), "stable")

    def test_large_jump(self):
        self.assertEqual(cs.compute_intraday_shift(-2, 2), "heating")


class TestLoadPreOpenScores(unittest.TestCase):
    def test_returns_scores_when_file_exists(self):
        import json, tempfile
        snapshot = {
            "slot": "pre_open",
            "market": {"sentiment_score": 1},
            "symbols": [
                {"symbol": "TSLA", "sentiment_score": -1},
                {"symbol": "AAPL", "sentiment_score": 0},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(snapshot, f)
            tmp = Path(f.name)
        try:
            result = cs.load_pre_open_scores(tmp)
            self.assertEqual(result["market"], 1)
            self.assertEqual(result["symbols"]["TSLA"], -1)
            self.assertEqual(result["symbols"]["AAPL"], 0)
        finally:
            tmp.unlink()

    def test_returns_empty_when_file_missing(self):
        result = cs.load_pre_open_scores(Path("/nonexistent/path.json"))
        self.assertIsNone(result["market"])
        self.assertEqual(result["symbols"], {})


class TestValidateTopNews(unittest.TestCase):
    def test_valid_top_news(self):
        tn = {
            "headline_en": "BofA raises AAPL target to $250",
            "headline_ko": "BofA, 애플 목표주가 $250으로 상향",
            "summary_en": "BofA raised its Apple price target to $250.",
            "summary_ko": "BofA가 애플 목표주가를 상향했다.",
            "source": "Bloomberg",
        }
        self.assertTrue(cs.validate_top_news(tn))

    def test_none_is_valid(self):
        self.assertTrue(cs.validate_top_news(None))

    def test_missing_headline_en_invalid(self):
        self.assertFalse(cs.validate_top_news({
            "headline_ko": "제목", "summary_en": "summary", "summary_ko": "요약", "source": "출처",
        }))

    def test_missing_summary_en_invalid(self):
        self.assertFalse(cs.validate_top_news({
            "headline_en": "headline", "headline_ko": "제목", "summary_ko": "요약", "source": "출처",
        }))

    def test_missing_source_invalid(self):
        self.assertFalse(cs.validate_top_news({
            "headline_en": "headline", "headline_ko": "제목",
            "summary_en": "summary", "summary_ko": "요약",
        }))

    def test_non_string_headline_en_invalid(self):
        self.assertFalse(cs.validate_top_news({
            "headline_en": 123, "headline_ko": "제목",
            "summary_en": "summary", "summary_ko": "요약", "source": "출처",
        }))

    def test_non_dict_non_none_invalid(self):
        self.assertFalse(cs.validate_top_news("not a dict"))


class TestBuildSymbolEntryTopNews(unittest.TestCase):
    def _base_raw(self):
        return {
            "sentiment": "optimistic",
            "trend_vs_yesterday": "stable",
            "mention_volume": "normal",
            "key_reason_en": "Test reason",
            "key_reason_ko": "테스트 이유",
            "bot_suspected": "no",
            "confidence": "med",
        }

    def test_top_news_included_when_present(self):
        raw = self._base_raw()
        raw["top_news"] = {
            "headline_en": "BofA raises AAPL to $250",
            "headline_ko": "BofA, 애플 목표주가 $250으로 상향",
            "summary_en": "BofA raised its Apple price target.",
            "summary_ko": "BofA가 목표주가를 상향했다.",
            "source": "Bloomberg",
        }
        entry = cs.build_symbol_entry(raw, "AAPL", "2026-05-28T13:00:00Z", {}, "aligned")
        self.assertIsNotNone(entry.get("top_news"))
        self.assertEqual(entry["top_news"]["source"], "Bloomberg")

    def test_top_news_null_when_absent(self):
        raw = self._base_raw()
        entry = cs.build_symbol_entry(raw, "AAPL", "2026-05-28T13:00:00Z", {}, "aligned")
        self.assertIsNone(entry.get("top_news"))

    def test_top_news_null_when_explicitly_none(self):
        raw = self._base_raw()
        raw["top_news"] = None
        entry = cs.build_symbol_entry(raw, "AAPL", "2026-05-28T13:00:00Z", {}, "aligned")
        self.assertIsNone(entry.get("top_news"))


class TestBuildMarketEntryTopNews(unittest.TestCase):
    def _base_raw(self):
        return {
            "sentiment": "fearful",
            "trend_vs_yesterday": "cooling",
            "extreme_flag": "none",
            "key_reason_en": "Market test reason",
            "key_reason_ko": "마켓 테스트",
            "confidence": "high",
        }

    def test_top_news_included_when_present(self):
        raw = self._base_raw()
        raw["top_news"] = {
            "headline_en": "Fed holds rates",
            "headline_ko": "연준 금리 동결",
            "summary_en": "The Fed held rates steady at its latest meeting.",
            "summary_ko": "연준이 금리를 동결했다.",
            "source": "Reuters",
        }
        entry = cs.build_market_entry(raw, "2026-05-28T13:00:00Z")
        self.assertIsNotNone(entry.get("top_news"))
        self.assertEqual(entry["top_news"]["source"], "Reuters")

    def test_top_news_null_when_absent(self):
        raw = self._base_raw()
        entry = cs.build_market_entry(raw, "2026-05-28T13:00:00Z")
        self.assertIsNone(entry.get("top_news"))


class TestValidateBilingualFields(unittest.TestCase):
    def test_validate_symbol_fields_accepts_bilingual(self):
        data = {
            "symbol": "TSLA",
            "sentiment": "optimistic",
            "trend_vs_yesterday": "heating",
            "mention_volume": "elevated",
            "key_reason_en": "Robotaxi enthusiasm dominates",
            "key_reason_ko": "로보택시 열광이 지배적이다",
            "bot_suspected": "no",
            "confidence": "med",
        }
        self.assertTrue(cs.validate_symbol_fields(data, "TSLA"))

    def test_validate_symbol_fields_rejects_old_key_reason(self):
        data = {
            "symbol": "TSLA",
            "sentiment": "optimistic",
            "trend_vs_yesterday": "heating",
            "mention_volume": "elevated",
            "key_reason": "old field",
            "bot_suspected": "no",
            "confidence": "med",
        }
        self.assertFalse(cs.validate_symbol_fields(data, "TSLA"))

    def test_validate_top_news_accepts_bilingual(self):
        news = {
            "headline_en": "Tesla announces new model",
            "headline_ko": "테슬라 신모델 발표",
            "summary_en": "Tesla announced a new affordable model targeting mass market.",
            "summary_ko": "테슬라가 대중 시장을 겨냥한 저가형 신모델을 발표했다.",
            "source": "@elonmusk",
        }
        self.assertTrue(cs.validate_top_news(news))

    def test_validate_top_news_rejects_old_headline(self):
        news = {
            "headline": "old headline",
            "summary": "old summary",
            "source": "@foo",
        }
        self.assertFalse(cs.validate_top_news(news))

    def test_validate_top_news_accepts_none(self):
        self.assertTrue(cs.validate_top_news(None))

    def test_build_symbol_entry_uses_bilingual_fields(self):
        raw = {
            "sentiment": "optimistic",
            "trend_vs_yesterday": "heating",
            "mention_volume": "elevated",
            "key_reason_en": "Robotaxi enthusiasm dominates",
            "key_reason_ko": "로보택시 열광이 지배적이다",
            "bot_suspected": "no",
            "confidence": "med",
            "top_news": {
                "headline_en": "Tesla new model",
                "headline_ko": "테슬라 신모델",
                "summary_en": "Summary in English",
                "summary_ko": "한국어 요약",
                "source": "@foo",
            },
        }
        entry = cs.build_symbol_entry(raw, "TSLA", "2026-05-31T21:00:00Z", {"available": False}, "none")
        self.assertEqual(entry["key_reason_en"], "Robotaxi enthusiasm dominates")
        self.assertEqual(entry["key_reason_ko"], "로보택시 열광이 지배적이다")
        self.assertNotIn("key_reason", entry)

    def test_build_market_entry_uses_bilingual_fields(self):
        raw = {
            "sentiment": "optimistic",
            "trend_vs_yesterday": "heating",
            "extreme_flag": "none",
            "key_reason_en": "S&P 500 at record highs",
            "key_reason_ko": "S&P 500 사상 최고 기록",
            "confidence": "med",
            "top_news": None,
        }
        entry = cs.build_market_entry(raw, "2026-05-31T21:00:00Z")
        self.assertEqual(entry["key_reason_en"], "S&P 500 at record highs")
        self.assertNotIn("key_reason", entry)


if __name__ == "__main__":
    unittest.main()
