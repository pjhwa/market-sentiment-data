"""collect_prediction 단위 테스트 — Kalshi API는 mock 처리"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from collect.collect_prediction import (
    _parse_outcome,
    build_snapshot,
    detect_slot,
    fetch_fomc_probabilities,
    fetch_next_fomc_event,
)


# ---------------------------------------------------------------------------
# detect_slot
# ---------------------------------------------------------------------------

class TestDetectSlot:
    def test_pre_open_hour(self):
        now = datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc)
        assert detect_slot(now) == "pre_open"

    def test_post_close_hour(self):
        now = datetime(2026, 6, 29, 22, 0, tzinfo=timezone.utc)
        assert detect_slot(now) == "post_close"

    def test_boundary_pre_open_start(self):
        now = datetime(2026, 6, 29, 9, 0, tzinfo=timezone.utc)
        assert detect_slot(now) == "pre_open"

    def test_boundary_pre_open_end(self):
        now = datetime(2026, 6, 29, 17, 59, tzinfo=timezone.utc)
        assert detect_slot(now) == "pre_open"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SENTIMENT_SLOT", "pre_open")
        now = datetime(2026, 6, 29, 22, 0, tzinfo=timezone.utc)
        assert detect_slot(now) == "pre_open"


# ---------------------------------------------------------------------------
# _parse_outcome
# ---------------------------------------------------------------------------

class TestParseOutcome:
    def test_no_change(self):
        assert _parse_outcome("FOMC-26JUL29-UNCHANGED") == "no_change"

    def test_cut_25(self):
        assert _parse_outcome("FOMC-26JUL29-DOWN25") == "cut_25bps"

    def test_cut_50(self):
        assert _parse_outcome("FOMC-26JUL29-DOWN50") == "cut_50bps"

    def test_hike_25(self):
        assert _parse_outcome("FOMC-26JUL29-UP25") == "hike_25bps"

    def test_cut25_alternative_keyword(self):
        assert _parse_outcome("FOMC-26JUL29-CUT25") == "cut_25bps"

    def test_unknown_returns_none(self):
        assert _parse_outcome("FOMC-26JUL29-SOMETHING_WEIRD") is None

    def test_50bps_takes_priority_over_25bps(self):
        # DOWN50은 DOWN25보다 먼저 매칭되어야 함
        assert _parse_outcome("FOMC-26JUL29-DOWN50") == "cut_50bps"

    def test_case_insensitive(self):
        assert _parse_outcome("fomc-26jul29-unchanged") == "no_change"


# ---------------------------------------------------------------------------
# fetch_next_fomc_event
# ---------------------------------------------------------------------------

MOCK_EVENTS_RESPONSE = {
    "events": [
        {
            "event_ticker": "FOMC-26JUL29",
            "end_date": "2026-07-29",
            "scheduled_close_time": "2026-07-29T18:00:00Z",
        },
        {
            "event_ticker": "FOMC-26SEP17",
            "end_date": "2026-09-17",
            "scheduled_close_time": "2026-09-17T18:00:00Z",
        },
    ]
}


class TestFetchNextFomcEvent:
    @patch("collect.collect_prediction._kalshi_get")
    def test_returns_nearest_event(self, mock_get):
        mock_get.return_value = MOCK_EVENTS_RESPONSE
        result = fetch_next_fomc_event()
        assert result["event_ticker"] == "FOMC-26JUL29"

    @patch("collect.collect_prediction._kalshi_get")
    def test_returns_none_when_no_events(self, mock_get):
        mock_get.return_value = {"events": []}
        result = fetch_next_fomc_event()
        assert result is None

    @patch("collect.collect_prediction._kalshi_get")
    def test_returns_none_on_api_failure(self, mock_get):
        mock_get.return_value = None
        result = fetch_next_fomc_event()
        assert result is None

    @patch("collect.collect_prediction._kalshi_get")
    def test_sorts_by_end_date_ascending(self, mock_get):
        # 역순으로 넘겨도 가장 가까운 날짜 선택
        mock_get.return_value = {
            "events": [
                {"event_ticker": "FOMC-26SEP17", "end_date": "2026-09-17"},
                {"event_ticker": "FOMC-26JUL29", "end_date": "2026-07-29"},
            ]
        }
        result = fetch_next_fomc_event()
        assert result["event_ticker"] == "FOMC-26JUL29"


# ---------------------------------------------------------------------------
# fetch_fomc_probabilities
# ---------------------------------------------------------------------------

MOCK_EVENT_DETAIL = {
    "markets": [
        {"ticker": "FOMC-26JUL29-UNCHANGED", "yes_ask": 72},
        {"ticker": "FOMC-26JUL29-DOWN25",    "yes_ask": 23},
        {"ticker": "FOMC-26JUL29-DOWN50",    "yes_ask": 4},
        {"ticker": "FOMC-26JUL29-UP25",      "yes_ask": 1},
    ]
}


class TestFetchFomcProbabilities:
    @patch("collect.collect_prediction._kalshi_get")
    def test_parses_all_outcomes(self, mock_get):
        mock_get.return_value = MOCK_EVENT_DETAIL
        probs = fetch_fomc_probabilities("FOMC-26JUL29")
        assert set(probs.keys()) == {"no_change", "cut_25bps", "cut_50bps", "hike_25bps"}

    @patch("collect.collect_prediction._kalshi_get")
    def test_converts_cents_to_decimal(self, mock_get):
        mock_get.return_value = MOCK_EVENT_DETAIL
        probs = fetch_fomc_probabilities("FOMC-26JUL29")
        assert probs["no_change"] == pytest.approx(0.72, abs=0.001)
        assert probs["cut_25bps"] == pytest.approx(0.23, abs=0.001)

    @patch("collect.collect_prediction._kalshi_get")
    def test_decimal_price_passthrough(self, mock_get):
        # 이미 소수점 형태로 오는 경우 (0.0~1.0)
        mock_get.return_value = {
            "markets": [{"ticker": "FOMC-26JUL29-UNCHANGED", "yes_ask": 0.80}]
        }
        probs = fetch_fomc_probabilities("FOMC-26JUL29")
        assert probs["no_change"] == pytest.approx(0.80, abs=0.001)

    @patch("collect.collect_prediction._kalshi_get")
    def test_skips_unknown_tickers(self, mock_get):
        mock_get.return_value = {
            "markets": [
                {"ticker": "FOMC-26JUL29-UNCHANGED", "yes_ask": 80},
                {"ticker": "FOMC-26JUL29-WEIRD_THING", "yes_ask": 20},
            ]
        }
        probs = fetch_fomc_probabilities("FOMC-26JUL29")
        assert "no_change" in probs
        assert len(probs) == 1

    @patch("collect.collect_prediction._kalshi_get")
    def test_returns_empty_on_api_failure(self, mock_get):
        mock_get.return_value = None
        probs = fetch_fomc_probabilities("FOMC-26JUL29")
        assert probs == {}

    @patch("collect.collect_prediction._kalshi_get")
    def test_skips_market_with_no_price(self, mock_get):
        mock_get.return_value = {
            "markets": [
                {"ticker": "FOMC-26JUL29-UNCHANGED", "yes_ask": None, "yes_ask_price": None, "last_price": None},
            ]
        }
        probs = fetch_fomc_probabilities("FOMC-26JUL29")
        assert probs == {}


# ---------------------------------------------------------------------------
# build_snapshot
# ---------------------------------------------------------------------------

class TestBuildSnapshot:
    @patch("collect.collect_prediction.fetch_fomc_probabilities")
    @patch("collect.collect_prediction.fetch_next_fomc_event")
    def test_full_snapshot_structure(self, mock_event, mock_probs):
        mock_event.return_value = {
            "event_ticker": "FOMC-26JUL29",
            "end_date": "2026-07-29",
        }
        mock_probs.return_value = {
            "no_change": 0.72,
            "cut_25bps": 0.23,
            "cut_50bps": 0.04,
            "hike_25bps": 0.01,
        }
        now = datetime(2026, 6, 29, 6, 30, tzinfo=timezone.utc)
        snap = build_snapshot("pre_open", now)

        assert snap["schema_version"] == "1.0"
        assert snap["source"] == "kalshi"
        assert snap["slot"] == "pre_open"
        assert snap["next_fomc"]["event_ticker"] == "FOMC-26JUL29"
        assert snap["next_fomc"]["meeting_date"] == "2026-07-29"
        assert snap["next_fomc"]["dominant_outcome"] == "no_change"
        assert snap["next_fomc"]["dominant_probability"] == pytest.approx(0.72)

    @patch("collect.collect_prediction.fetch_next_fomc_event")
    def test_null_next_fomc_when_no_event(self, mock_event):
        mock_event.return_value = None
        now = datetime(2026, 6, 29, 6, 30, tzinfo=timezone.utc)
        snap = build_snapshot("pre_open", now)
        assert snap["next_fomc"] is None
        assert snap["schema_version"] == "1.0"

    @patch("collect.collect_prediction.fetch_fomc_probabilities")
    @patch("collect.collect_prediction.fetch_next_fomc_event")
    def test_snapshot_is_json_serializable(self, mock_event, mock_probs):
        mock_event.return_value = {
            "event_ticker": "FOMC-26JUL29",
            "end_date": "2026-07-29",
        }
        mock_probs.return_value = {"no_change": 0.80}
        now = datetime(2026, 6, 29, 6, 30, tzinfo=timezone.utc)
        snap = build_snapshot("pre_open", now)
        serialized = json.dumps(snap)
        assert "FOMC-26JUL29" in serialized

    @patch("collect.collect_prediction.fetch_fomc_probabilities")
    @patch("collect.collect_prediction.fetch_next_fomc_event")
    def test_dominant_outcome_is_highest_probability(self, mock_event, mock_probs):
        mock_event.return_value = {"event_ticker": "FOMC-26JUL29", "end_date": "2026-07-29"}
        mock_probs.return_value = {
            "no_change": 0.20,
            "cut_25bps": 0.65,  # 가장 높음
            "cut_50bps": 0.15,
        }
        now = datetime(2026, 6, 29, 6, 30, tzinfo=timezone.utc)
        snap = build_snapshot("pre_open", now)
        assert snap["next_fomc"]["dominant_outcome"] == "cut_25bps"
        assert snap["next_fomc"]["dominant_probability"] == pytest.approx(0.65)

    @patch("collect.collect_prediction.fetch_fomc_probabilities")
    @patch("collect.collect_prediction.fetch_next_fomc_event")
    def test_empty_probabilities_yields_none_dominant(self, mock_event, mock_probs):
        mock_event.return_value = {"event_ticker": "FOMC-26JUL29", "end_date": "2026-07-29"}
        mock_probs.return_value = {}
        now = datetime(2026, 6, 29, 6, 30, tzinfo=timezone.utc)
        snap = build_snapshot("pre_open", now)
        assert snap["next_fomc"]["dominant_outcome"] is None
        assert snap["next_fomc"]["dominant_probability"] is None
