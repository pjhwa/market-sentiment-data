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


class TestHistoryFilename(unittest.TestCase):
    def test_pre_open_filename(self):
        path = cs.history_filename("2026-05-21", "pre_open")
        self.assertEqual(path.name, "2026-05-21_pre_open.json")

    def test_post_close_filename(self):
        path = cs.history_filename("2026-05-21", "post_close")
        self.assertEqual(path.name, "2026-05-21_post_close.json")


if __name__ == "__main__":
    unittest.main()
