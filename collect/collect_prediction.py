#!/usr/bin/env python3
"""
Prediction Market 수집기 (Collector 6) — Kalshi FOMC 금리 결정 확률

① Kalshi /events?series_ticker=FOMC&status=open 에서 다음 FOMC 이벤트 탐색
② 이벤트 내 마켓별 yes_ask_price(확률) 수집
③ prediction/latest.json + prediction/history/<date>_<slot>.json 저장
④ git commit + push

Grok 없음 — 순수 확률 데이터만 저장.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from collect.git_utils import commit_and_push

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_API_KEY = os.environ.get("KALSHI_API_KEY", "")


def detect_slot(now: datetime) -> str:
    override = os.environ.get("SENTIMENT_SLOT", "").strip()
    if override in ("pre_open", "post_close"):
        return override
    return "pre_open" if 9 <= now.hour < 18 else "post_close"


def _kalshi_get(path: str) -> dict | list | None:
    """Kalshi REST API GET 호출. 실패 시 None 반환."""
    if not KALSHI_API_KEY:
        print("[ERROR] KALSHI_API_KEY 환경변수가 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)
    try:
        resp = requests.get(
            f"{KALSHI_BASE}{path}",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        print(f"[ERROR] Kalshi API HTTP 오류 {e.response.status_code}: {path}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[ERROR] Kalshi API 호출 실패: {e}", file=sys.stderr)
        return None


# ticker 키워드 → outcome 이름 매핑 (순서 중요: 50bps를 25bps보다 먼저 체크)
_OUTCOME_MAP: list[tuple[list[str], str]] = [
    (["DOWN50", "CUT50"], "cut_50bps"),
    (["DOWN25", "CUT25"], "cut_25bps"),
    (["UP25", "HIKE25"], "hike_25bps"),
    (["UNCHANGED", "NO_CHANGE"], "no_change"),
]


def _parse_outcome(market_ticker: str) -> str | None:
    """마켓 ticker에서 outcome 이름 추출. 알 수 없으면 None."""
    ticker_upper = market_ticker.upper()
    for keywords, outcome in _OUTCOME_MAP:
        if any(kw in ticker_upper for kw in keywords):
            return outcome
    return None


def fetch_next_fomc_event() -> dict | None:
    """
    Kalshi에서 다음 FOMC 이벤트를 탐색한다.
    가장 가까운 미래 날짜의 open 이벤트를 반환. 없으면 None.
    """
    data = _kalshi_get("/events?series_ticker=FOMC&status=open&limit=20")
    if not data:
        return None

    events = data.get("events", [])
    if not events:
        print("[INFO] 열린 FOMC 이벤트 없음 (FOMC 직후 공백기일 수 있음)", file=sys.stderr)
        return None

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    future_events = []
    for ev in events:
        end_date = ev.get("end_date") or ev.get("scheduled_close_time", "")[:10]
        if end_date >= today_str:
            future_events.append((end_date, ev))

    if not future_events:
        return events[0]

    future_events.sort(key=lambda x: x[0])
    return future_events[0][1]


def fetch_fomc_probabilities(event_ticker: str) -> dict[str, float]:
    """
    이벤트 내 마켓에서 outcome별 확률(yes_ask_price)을 수집한다.
    파싱 불가 마켓은 건너뜀.
    """
    data = _kalshi_get(f"/events/{event_ticker}")
    if not data:
        return {}

    markets = data.get("markets", [])
    probabilities: dict[str, float] = {}

    for market in markets:
        ticker = market.get("ticker", "")
        outcome = _parse_outcome(ticker)
        if outcome is None:
            print(f"[WARN] 알 수 없는 마켓 ticker: {ticker} — 건너뜀", file=sys.stderr)
            continue

        # yes_ask_price: 0~100 정수(센트) 또는 0.0~1.0 소수 모두 처리
        raw_price = market.get("yes_ask") or market.get("yes_ask_price") or market.get("last_price")
        if raw_price is None:
            continue

        price = float(raw_price)
        if price > 1.0:
            price = price / 100.0

        probabilities[outcome] = round(price, 4)

    return probabilities


def build_snapshot(slot: str, now: datetime) -> dict:
    """
    Kalshi에서 데이터를 수집하여 prediction snapshot dict를 반환한다.
    FOMC 이벤트가 없으면 next_fomc: null.
    """
    event = fetch_next_fomc_event()

    if event is None:
        return {
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "schema_version": "1.0",
            "slot": slot,
            "source": "kalshi",
            "next_fomc": None,
        }

    event_ticker = event.get("event_ticker", "")
    meeting_date = event.get("end_date") or (event.get("scheduled_close_time", "")[:10])

    probabilities = fetch_fomc_probabilities(event_ticker)

    next_fomc: dict = {
        "event_ticker": event_ticker,
        "meeting_date": meeting_date,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "probabilities": probabilities,
    }

    if probabilities:
        dominant_outcome = max(probabilities, key=lambda k: probabilities[k])
        next_fomc["dominant_outcome"] = dominant_outcome
        next_fomc["dominant_probability"] = probabilities[dominant_outcome]
    else:
        next_fomc["dominant_outcome"] = None
        next_fomc["dominant_probability"] = None

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": "1.0",
        "slot": slot,
        "source": "kalshi",
        "next_fomc": next_fomc,
    }


def save_snapshot(snapshot: dict, slot: str, now: datetime) -> list[str]:
    """
    prediction/latest.json 과 history/<date>_<slot>.json 저장.
    저장된 파일 경로 목록 반환.
    """
    pred_dir = REPO_PATH / "prediction"
    history_dir = pred_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    latest_path = pred_dir / "latest.json"
    history_path = history_dir / f"{now.strftime('%Y-%m-%d')}_{slot}.json"

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
    latest_path.write_text(payload, encoding="utf-8")
    history_path.write_text(payload, encoding="utf-8")

    print(f"[OK] 저장 완료: {latest_path}")
    print(f"[OK] 저장 완료: {history_path}")

    return [str(latest_path), str(history_path)]


def main() -> None:
    if not KALSHI_API_KEY:
        print("[ERROR] KALSHI_API_KEY 환경변수가 설정되지 않았습니다.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    slot = detect_slot(now)
    print(f"[INFO] Prediction 수집 시작 — slot={slot}, UTC={now.strftime('%Y-%m-%d %H:%M')}")

    snapshot = build_snapshot(slot, now)

    fomc_info = snapshot.get("next_fomc")
    if fomc_info:
        print(f"[INFO] 다음 FOMC: {fomc_info.get('event_ticker')} ({fomc_info.get('meeting_date')})")
        probs = fomc_info.get("probabilities", {})
        for outcome, prob in probs.items():
            print(f"  {outcome}: {prob:.1%}")
    else:
        print("[INFO] 다음 FOMC 이벤트 없음 — next_fomc: null 저장")

    files = save_snapshot(snapshot, slot, now)

    date_str = now.strftime("%Y-%m-%d")
    ok = commit_and_push(
        repo=REPO_PATH,
        commit_message=f"prediction: {date_str} {slot} update",
        files_to_add=files,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
