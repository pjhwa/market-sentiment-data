"""Phase 1 Context Snapshot builder 테스트 (TDD 스타일).

build_brief_context_snapshot() 이 올바른 구조를 반환하는지 검증.
"""

import pytest
from datetime import datetime, timezone

from collect.collect_brief import build_brief_context_snapshot, WATCHLIST


def test_build_brief_context_snapshot_basic():
    """정상 입력 시 필수 필드와 계산이 올바른지 확인."""
    tech = {
        "regime": {
            "total": 68,
            "regime": "CONSTRUCTIVE",
            "components": {"trend": 7.5, "breadth": 4.2},
        },
        "distribution_days": {"spy": {"count": 4, "level": "WARNING"}},
        "symbol_detail": {
            "NVDA": {"stage2_score": 6, "rs_score": 82},
            "AAPL": {"stage2_score": 5, "rs_score": 61},
        },
    }
    sentiment = {
        "market": {"composite_score": 72, "sentiment": "optimistic"}
    }
    captured_at = "2026-05-25T12:00:00Z"

    ctx = build_brief_context_snapshot(tech, sentiment, captured_at)

    assert ctx["captured_at"] == captured_at
    assert ctx["source"] == "sniperboard"
    assert ctx["regime"]["total"] == 68
    assert ctx["regime"]["label"] == "CONSTRUCTIVE"

    ts = ctx["technical_summary"]
    assert ts["avg_stage2"] == 5.5
    assert ts["avg_rs_score"] == 71.5
    assert ts["distribution_day_spy"] == 4

    ms = ctx["market_sentiment"]
    assert ms["composite_score"] == 72

    assert "Regime CONSTRUCTIVE" in ctx["key_factors"]
    assert any("Stage2" in f for f in ctx["key_factors"])


def test_build_brief_context_snapshot_handles_missing_data():
    """데이터가 부족해도 None/빈 값으로 안전하게 처리."""
    tech = {"regime": {}, "distribution_days": {}, "symbol_detail": {}}
    sentiment = {}
    captured_at = datetime.now(timezone.utc).isoformat()

    ctx = build_brief_context_snapshot(tech, sentiment, captured_at)

    assert ctx["regime"]["total"] is None
    assert ctx["technical_summary"]["avg_stage2"] is None
    assert ctx["key_factors"] == ["데이터 기반 요약"]  # fallback
