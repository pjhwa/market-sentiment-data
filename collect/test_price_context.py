"""
price_context 단위 테스트 — 오염 방지선 기계 검증.
python -m pytest collect/test_price_context.py -v
"""

import re
import sys
import unittest
from unittest.mock import MagicMock, patch

# 테스트 대상
sys.path.insert(0, __file__.rsplit("/collect/", 1)[0])
from collect.price_context import (
    _DIRECTION_PATTERN,
    _assert_no_direction,
    fetch_close_direction,
    fetch_market_context,
    fetch_price_context,
)


class TestDirectionWordGuard(unittest.TestCase):
    """_DIRECTION_PATTERN이 방향 단어를 올바로 탐지하는지."""

    def _forbidden(self, text: str) -> bool:
        return bool(_DIRECTION_PATTERN.search(text))

    def test_detects_up(self):
        self.assertTrue(self._forbidden("price went up today"))

    def test_detects_down(self):
        self.assertTrue(self._forbidden("stock fell down hard"))

    def test_detects_bullish(self):
        self.assertTrue(self._forbidden("bullish sentiment"))

    def test_detects_bearish(self):
        self.assertTrue(self._forbidden("bearish divergence"))

    def test_detects_buy(self):
        self.assertTrue(self._forbidden("buy signal"))

    def test_detects_sell(self):
        self.assertTrue(self._forbidden("sell pressure"))

    def test_detects_korean_up(self):
        self.assertTrue(self._forbidden("주가가 올랐습니다"))

    def test_detects_korean_down(self):
        self.assertTrue(self._forbidden("주가가 떨어졌습니다"))

    def test_detects_korean_surge(self):
        self.assertTrue(self._forbidden("급등세 지속"))

    def test_detects_korean_crash(self):
        self.assertTrue(self._forbidden("급락 우려"))

    # 허용 단어 (방향 함의 없음)
    def test_allows_near_52w_high(self):
        self.assertFalse(self._forbidden("near_52w_high"))

    def test_allows_near_52w_low(self):
        self.assertFalse(self._forbidden("near_52w_low"))

    def test_allows_elevated(self):
        self.assertFalse(self._forbidden("elevated volatility"))

    def test_allows_volume_ratio(self):
        self.assertFalse(self._forbidden("volume_ratio: 2.3"))

    def test_allows_extreme(self):
        self.assertFalse(self._forbidden("extreme volatility"))

    def test_allows_setup(self):
        # 'setup' 안에 'up'이 있지만 word-boundary로 걸리지 않아야 함
        self.assertFalse(self._forbidden("setup complete"))

    def test_allows_download(self):
        self.assertFalse(self._forbidden("download complete"))


class TestFetchPriceContextNeutral(unittest.TestCase):
    """fetch_price_context 반환값에 방향 단어가 없는지."""

    def _make_daily(self, close, open_price, atr14, high_52w, low_52w, volume=1_000_000, vol_avg20=500_000):
        # 실제 API 구조: candles 배열 + indicators.atr14 배열 + vol_avg20 배열
        # 52w high/low는 candles에서 계산하므로 candles에 명시적으로 포함
        candles = [
            {"time": "2025-01-01", "open": open_price, "high": high_52w, "low": low_52w, "close": close, "volume": volume},
            {"time": "2026-01-01", "open": open_price, "high": high_52w, "low": low_52w, "close": close, "volume": volume},
        ]
        return {
            "candles": candles,
            "indicators": {"atr14": [atr14]},
            "vol_avg20": [vol_avg20],
        }

    def _mock_get(self, daily, _ohlcv_unused=None):
        def side_effect(url, params=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = daily
            return resp

        return side_effect

    def test_normal_conditions_no_direction_words(self):
        daily = self._make_daily(150.0, 148.0, 5.0, 200.0, 100.0, 1_000_000, 500_000)
        with patch("collect.price_context.requests.get", side_effect=self._mock_get(daily)):
            result = fetch_price_context("TSLA")
        self.assertTrue(result["available"])
        payload = {k: v for k, v in result.items() if k != "available"}
        self.assertFalse(bool(_DIRECTION_PATTERN.search(str(payload))))

    def test_near_52w_high_no_direction_words(self):
        daily = self._make_daily(198.0, 195.0, 4.0, 200.0, 100.0, 3_000_000, 1_000_000)
        with patch("collect.price_context.requests.get", side_effect=self._mock_get(daily)):
            result = fetch_price_context("AAPL")
        self.assertEqual(result["near_key_level"], "near_52w_high")
        payload = {k: v for k, v in result.items() if k != "available"}
        self.assertFalse(bool(_DIRECTION_PATTERN.search(str(payload))))

    def test_near_52w_low_no_direction_words(self):
        daily = self._make_daily(101.0, 100.0, 3.0, 200.0, 100.0, 2_000_000, 800_000)
        with patch("collect.price_context.requests.get", side_effect=self._mock_get(daily)):
            result = fetch_price_context("META")
        self.assertEqual(result["near_key_level"], "near_52w_low")
        payload = {k: v for k, v in result.items() if k != "available"}
        self.assertFalse(bool(_DIRECTION_PATTERN.search(str(payload))))

    def test_abnormal_move_flag(self):
        # 변동폭 = 10, ATR14 = 5 → ratio=2.0 → extreme & abnormal
        daily = self._make_daily(160.0, 150.0, 5.0, 200.0, 100.0, 5_000_000, 1_000_000)
        with patch("collect.price_context.requests.get", side_effect=self._mock_get(daily)):
            result = fetch_price_context("NVDA")
        self.assertTrue(result["abnormal_move"])
        self.assertEqual(result["volatility"], "extreme")

    def test_api_failure_returns_unavailable(self):
        with patch("collect.price_context.requests.get", side_effect=Exception("timeout")):
            result = fetch_price_context("GOOGL")
        self.assertFalse(result["available"])
        self.assertIsNone(result["volatility"])


class TestFetchMarketContext(unittest.TestCase):
    def _mock_macro(self, vix):
        def side_effect(url, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            # 실제 API 구조: macro 배열에서 ^VIX 항목 찾기
            resp.json.return_value = {"macro": [{"symbol": "^VIX", "price": vix}]}
            return resp

        return side_effect

    def test_low_vix(self):
        with patch("collect.price_context.requests.get", side_effect=self._mock_macro(12.0)):
            result = fetch_market_context()
        self.assertEqual(result["vix_level"], "low")

    def test_normal_vix(self):
        with patch("collect.price_context.requests.get", side_effect=self._mock_macro(18.0)):
            result = fetch_market_context()
        self.assertEqual(result["vix_level"], "normal")

    def test_high_vix(self):
        with patch("collect.price_context.requests.get", side_effect=self._mock_macro(28.0)):
            result = fetch_market_context()
        self.assertEqual(result["vix_level"], "high")

    def test_api_failure(self):
        with patch("collect.price_context.requests.get", side_effect=Exception("error")):
            result = fetch_market_context()
        self.assertFalse(result["available"])


class TestFetchCloseDirection(unittest.TestCase):
    def _mock_daily(self, close, open_price):
        def side_effect(url, params=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            # 실제 API 구조: candles 배열의 마지막 항목
            resp.json.return_value = {
                "candles": [{"time": "2026-01-01", "open": open_price, "close": close, "high": close, "low": open_price, "volume": 1000000}],
                "indicators": {"atr14": [1.0]},
                "vol_avg20": [1000000],
            }
            return resp

        return side_effect

    def test_up(self):
        with patch("collect.price_context.requests.get", side_effect=self._mock_daily(105.0, 100.0)):
            self.assertEqual(fetch_close_direction("TSLA"), "up")

    def test_down(self):
        with patch("collect.price_context.requests.get", side_effect=self._mock_daily(95.0, 100.0)):
            self.assertEqual(fetch_close_direction("TSLA"), "down")

    def test_flat(self):
        with patch("collect.price_context.requests.get", side_effect=self._mock_daily(100.0, 100.0)):
            self.assertEqual(fetch_close_direction("TSLA"), "flat")

    def test_api_failure_returns_flat(self):
        with patch("collect.price_context.requests.get", side_effect=Exception("err")):
            self.assertEqual(fetch_close_direction("TSLA"), "flat")


if __name__ == "__main__":
    unittest.main()
