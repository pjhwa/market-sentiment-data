import json
import tempfile
import unittest
from pathlib import Path

from collect import xquik_social_mentions as xqm


class TestXquikSocialMentions(unittest.TestCase):
    def test_load_csv_and_match_explicit_ticker(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as handle:
            handle.write("ticker,text\nTSLA,Robotaxi chatter is active\n")
            path = Path(handle.name)

        try:
            rows = xqm.load_rows(path)
            summary = xqm.summarize_mentions(rows, ["TSLA", "AAPL"])
            self.assertEqual(summary["TSLA"]["mention_count"], 1)
            self.assertEqual(summary["TSLA"]["mention_volume"], "low")
            self.assertEqual(summary["AAPL"]["mention_count"], 0)
        finally:
            path.unlink()

    def test_load_json_nested_results_and_match_cashtag(self):
        payload = {
            "results": [
                {"tweet_text": "$NVDA demand remains intense"},
                {"tweet_text": "No watchlist symbol here"},
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
            json.dump(payload, handle)
            path = Path(handle.name)

        try:
            rows = xqm.load_rows(path)
            self.assertEqual(xqm.mentioned_symbols(rows[0], ["NVDA"]), ["NVDA"])
        finally:
            path.unlink()

    def test_load_jsonl_and_bucket_normal_volume(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as handle:
            handle.write(json.dumps({"content": "$AAPL services debate"}) + "\n")
            handle.write(json.dumps({"content": "$AAPL new product cycle"}) + "\n")
            handle.write(json.dumps({"content": "$AAPL margins thread"}) + "\n")
            path = Path(handle.name)

        try:
            rows = xqm.load_rows(path)
            summary = xqm.summarize_mentions(rows, ["AAPL"])
            self.assertEqual(summary["AAPL"]["mention_count"], 3)
            self.assertEqual(summary["AAPL"]["mention_volume"], "normal")
            self.assertEqual(len(summary["AAPL"]["samples"]), 3)
        finally:
            path.unlink()

    def test_text_requires_cashtag_for_word_like_ticker(self):
        prose = {"content": "The mobile app has a simpler onboarding flow."}
        cashtag = {"content": "Investors are comparing $APP margins."}

        self.assertEqual(xqm.mentioned_symbols(prose, ["APP"]), [])
        self.assertEqual(xqm.mentioned_symbols(cashtag, ["APP"]), ["APP"])

    def test_load_rows_rejects_unknown_extension(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as handle:
            path = Path(handle.name)

        try:
            with self.assertRaises(ValueError):
                xqm.load_rows(path)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
