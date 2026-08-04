"""Phase B1/B2 integrity checks (MSD collector-side)."""
from datetime import date

from collect.phase_b_integrity import (
    check_false_catalyst_attribution,
    scan_briefing_artifacts,
    verify_briefing_integrity,
)


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


def test_price_table_required_for_price_bind_fail():
    """Without price_table, absurd $999 cannot be flagged; with table it fails."""
    brief = {
        "watchlist": [
            {"symbol": "NVDA", "sentiment_mood": "neutral", "analysis_ko": "NVDA $999.00"},
        ],
    }
    no_table = verify_briefing_integrity(brief, as_of=date(2026, 7, 13))
    assert no_table.passed is True  # cannot judge without table
    with_table = verify_briefing_integrity(
        brief, price_table={"NVDA": 200.0}, as_of=date(2026, 7, 13)
    )
    assert with_table.passed is False
    assert any(i.code == "B1-price-bind" for i in with_table.issues)


def test_pltr_false_catalyst_from_real_pattern():
    brief = {
        "headline_en": "Chip export thaw and PLTR post-earnings surge set risk-on tone",
        "headline_ko": "칩 수출 규제 완화에 PLTR 급등",
        "spotlight": [{
            "symbol": "PLTR",
            "why_en": "post-market surge to $140.64 (+11.93%). Earnings reaction.",
        }],
        "watchlist": [{
            "symbol": "PLTR",
            "sentiment_mood": "euphoric",
            "analysis_en": "post-market surged to $140.64 (+11.93%). earnings beat.",
        }],
        "earnings_alert_en": "[PLTR] earnings 2026-08-04",
        "global_context": {
            "issues": [{
                "category": "trade_tariff",
                "tier": "ongoing",
                "direction": "stable_elevated",
                "title_en": "US-China chip export controls shift to case-by-case licensing",
                "title_ko": "미중 반도체 수출통제",
                "asymmetric_impact_en": "NVDA: positive; MU: positive; PLTR: unaffected",
                "asymmetric_impact_ko": "PLTR: 영향 없음",
            }],
        },
    }
    issues = check_false_catalyst_attribution(brief)
    assert any(i.code == "B2-false-catalyst" for i in issues)
    r = verify_briefing_integrity(brief, as_of=date(2026, 8, 3))
    assert r.passed is False
    rep = scan_briefing_artifacts(brief, as_of=date(2026, 8, 3))
    assert rep["flags"]["false_catalyst"] is True


def test_corrected_catalyst_headline_passes_b2():
    brief = {
        "headline_en": "PLTR post-earnings surge leads risk-on open",
        "headline_ko": "PLTR 실적 애프터 급등",
        "spotlight": [{
            "symbol": "PLTR",
            "why_en": "post-market surge to $140.64 (+11.93%).",
        }],
        "watchlist": [{
            "symbol": "PLTR",
            "sentiment_mood": "euphoric",
            "analysis_en": "post-market surged to $140.64 (+11.93%). earnings beat.",
        }],
        "global_context": {
            "issues": [{
                "category": "trade_tariff",
                "tier": "ongoing",
                "direction": "stable_elevated",
                "title_en": "US-China chip export controls shift",
                "title_ko": "미중 반도체 수출통제",
                "asymmetric_impact_en": "NVDA: positive; PLTR: unaffected",
                "asymmetric_impact_ko": "PLTR: 영향 없음",
            }],
        },
    }
    r = verify_briefing_integrity(brief, as_of=date(2026, 8, 3))
    assert not any(i.code == "B2-false-catalyst" for i in r.issues)
    assert r.passed is True
