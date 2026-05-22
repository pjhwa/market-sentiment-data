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


if __name__ == "__main__":
    unittest.main()
