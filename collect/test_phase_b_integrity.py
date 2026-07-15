"""Phase B1 integrity checks (MSD collector-side)."""
from datetime import date

from collect.phase_b_integrity import verify_briefing_integrity


def test_bad_already_reported_fails_promotion():
    r = verify_briefing_integrity(
        {"earnings_alert_ko": "TSM 오늘 미국 장 마감 후 실적 발표됨", "watchlist": []},
        upcoming_earnings=[{"symbol": "TSM", "earnings_date": "2026-07-16"}],
        as_of=date(2026, 7, 13),
    )
    assert r.passed is False
    assert any(i.code == "B1-rel-already" for i in r.issues)


def test_good_alert_passes():
    r = verify_briefing_integrity(
        {
            "earnings_alert_ko": "TSM 7월 16일 실적 (3일 후 발표)",
            "watchlist": [
                {"symbol": "TSM", "sentiment_mood": "cautious", "analysis_ko": "$421.58에서 -1.0%"},
            ],
        },
        upcoming_earnings=[{"symbol": "TSM", "earnings_date": "2026-07-16"}],
        price_table={"TSM": 421.58},
        as_of=date(2026, 7, 13),
    )
    assert r.passed is True
