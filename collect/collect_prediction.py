#!/usr/bin/env python3
"""
Prediction Market 수집기 (Collector 6) — FOMC 금리 결정 함의 확률

기본 소스: Polymarket 공개 Gamma API (API 키 불필요, 참고용 예측시장).
선택 소스: Kalshi (KALSHI_API_KEY 필요).

환경변수:
  PREDICTION_SOURCE = polymarket | kalshi  (default: polymarket)
  KALSHI_API_KEY    = Kalshi Bearer token (source=kalshi 일 때 필수)
  SENTIMENT_SLOT    = pre_open | post_close 오버라이드

스키마 1.1:
  usage: "reference_only"
  disclaimer_en/ko
  next_fomc: probabilities + volume + source

Grok 없음 — 순수 시세 데이터만 저장.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from collect.git_utils import commit_and_push

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_API_KEY = os.environ.get("KALSHI_API_KEY", "")
PREDICTION_SOURCE = os.environ.get("PREDICTION_SOURCE", "polymarket").strip().lower()
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
# Event-level minimum cumulative volume (USD) — thin markets rejected
MIN_EVENT_VOLUME_USD = float(os.environ.get("PREDICTION_MIN_VOLUME_USD", "100000"))

DISCLAIMER_EN = (
    "Crypto prediction-market odds (Polymarket) — reference only. "
    "Not official Fed guidance; participant base differs from regulated US prediction markets."
)
DISCLAIMER_KO = (
    "암호화폐 예측시장(Polymarket) 시세 기반 함의 확률 — 참고용입니다. "
    "연준 공식 전망이 아니며, 규제 시장(Kalshi 등)과 참여자 구성이 다릅니다."
)


def detect_slot(now: datetime) -> str:
    override = os.environ.get("SENTIMENT_SLOT", "").strip()
    if override in ("pre_open", "post_close"):
        return override
    return "pre_open" if 9 <= now.hour < 18 else "post_close"


# ── Kalshi (optional) ─────────────────────────────────────────────────────────

def _kalshi_get(path: str) -> dict | list | None:
    """Kalshi REST API GET 호출. 실패 시 None 반환."""
    if not KALSHI_API_KEY:
        print("[ERROR] KALSHI_API_KEY 환경변수가 설정되지 않았습니다.", file=sys.stderr)
        return None
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


_OUTCOME_MAP: list[tuple[list[str], str]] = [
    (["DOWN50", "CUT50"], "cut_50bps"),
    (["DOWN25", "CUT25"], "cut_25bps"),
    (["UP50", "HIKE50"], "hike_50bps"),
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
    """Kalshi에서 다음 FOMC 이벤트를 탐색한다."""
    data = _kalshi_get("/events?series_ticker=FOMC&status=open&limit=20")
    if not data:
        return None

    events = data.get("events", []) if isinstance(data, dict) else []
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
    """이벤트 내 마켓에서 outcome별 확률(yes_ask_price)을 수집한다."""
    data = _kalshi_get(f"/events/{event_ticker}")
    if not data or not isinstance(data, dict):
        return {}

    markets = data.get("markets", [])
    probabilities: dict[str, float] = {}

    for market in markets:
        ticker = market.get("ticker", "")
        outcome = _parse_outcome(ticker)
        if outcome is None:
            print(f"[WARN] 알 수 없는 마켓 ticker: {ticker} — 건너뜀", file=sys.stderr)
            continue

        raw_price = market.get("yes_ask") or market.get("yes_ask_price") or market.get("last_price")
        if raw_price is None:
            continue

        price = float(raw_price)
        if price > 1.0:
            price = price / 100.0

        probabilities[outcome] = round(price, 4)

    return probabilities


# ── Polymarket ────────────────────────────────────────────────────────────────

def _poly_get(path: str, params: dict | None = None) -> Any:
    """Gamma API GET. path starts with /."""
    url = f"{POLYMARKET_GAMMA}{path}"
    if params:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        url = f"{url}?{qs}"
    try:
        resp = requests.get(url, headers={"User-Agent": "SniperBoard-prediction/1.0"}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Polymarket GET 실패 {url}: {e}", file=sys.stderr)
        return None


def _maybe_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _parse_polymarket_outcome(question: str) -> str | None:
    """Map Polymarket market question → normalized outcome key."""
    q = (question or "").lower()
    # order matters: 50 before 25
    if re.search(r"decrease|cut|lower", q) and re.search(r"50", q):
        return "cut_50bps"
    if re.search(r"decrease|cut|lower", q) and re.search(r"25", q):
        return "cut_25bps"
    if re.search(r"increase|hike|raise", q) and re.search(r"50", q):
        return "hike_50bps"
    if re.search(r"increase|hike|raise", q) and re.search(r"25", q):
        return "hike_25bps"
    if re.search(r"no change|unchanged|hold rates|rates? remain", q):
        return "no_change"
    return None


def _yes_probability(market: dict) -> float | None:
    """Extract Yes probability from outcomePrices."""
    outcomes = _maybe_json(market.get("outcomes"))
    prices = _maybe_json(market.get("outcomePrices"))
    if not isinstance(outcomes, list) or not isinstance(prices, list):
        return None
    try:
        for o, p in zip(outcomes, prices):
            if str(o).lower() == "yes":
                return float(p)
        # single binary sometimes only prices[0] = Yes
        if len(prices) >= 1:
            return float(prices[0])
    except (TypeError, ValueError):
        return None
    return None


def _is_fed_decision_event(title: str, slug: str) -> bool:
    t = (title or "").lower()
    s = (slug or "").lower()
    if "fed decision" in t or "fed decision" in s:
        return True
    if "fomc" in t and "decision" in t:
        return True
    return False


def fetch_polymarket_next_fomc() -> dict | None:
    """Find nearest open Fed Decision event and return structured next_fomc dict."""
    search = _poly_get("/public-search", {"q": "Fed Decision"})
    if not search or not isinstance(search, dict):
        return None

    events = search.get("events") or []
    today = datetime.now(timezone.utc).date()
    candidates: list[tuple[str, dict]] = []

    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("closed") is True:
            continue
        title = ev.get("title") or ""
        slug = ev.get("slug") or ""
        if not _is_fed_decision_event(title, slug):
            continue
        end = (ev.get("endDate") or "")[:10]
        if not end:
            continue
        try:
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            continue
        if end_d < today:
            continue
        candidates.append((end, ev))

    if not candidates:
        print("[INFO] Polymarket: open Fed Decision 이벤트 없음", file=sys.stderr)
        return None

    candidates.sort(key=lambda x: x[0])
    end_date, meta = candidates[0]
    slug = meta.get("slug") or ""

    # Full event payload with markets
    full_list = _poly_get("/events", {"slug": slug})
    if not full_list or not isinstance(full_list, list) or not full_list:
        print(f"[WARN] Polymarket event detail empty for slug={slug}", file=sys.stderr)
        return None
    event = full_list[0]
    markets = _maybe_json(event.get("markets") or [])
    if not isinstance(markets, list):
        markets = []

    probabilities: dict[str, float] = {}
    outcomes_detail: list[dict] = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        if m.get("closed") is True:
            continue
        question = m.get("question") or m.get("groupItemTitle") or ""
        outcome = _parse_polymarket_outcome(question)
        if outcome is None:
            print(f"[WARN] Polymarket 미매핑 마켓: {question[:80]}", file=sys.stderr)
            continue
        yes_p = _yes_probability(m)
        if yes_p is None:
            continue
        yes_p = max(0.0, min(1.0, float(yes_p)))
        probabilities[outcome] = round(yes_p, 4)
        vol = m.get("volume")
        try:
            vol_f = float(vol) if vol is not None else None
        except (TypeError, ValueError):
            vol_f = None
        outcomes_detail.append({
            "label": outcome,
            "probability": round(yes_p, 4),
            "question": question,
            "volume_usd": round(vol_f, 2) if vol_f is not None else None,
        })

    try:
        volume_usd = float(event.get("volume") or meta.get("volume") or 0)
    except (TypeError, ValueError):
        volume_usd = 0.0
    try:
        liquidity_usd = float(event.get("liquidity") or meta.get("liquidity") or 0)
    except (TypeError, ValueError):
        liquidity_usd = 0.0

    if volume_usd < MIN_EVENT_VOLUME_USD:
        print(
            f"[WARN] Polymarket volume ${volume_usd:,.0f} < min ${MIN_EVENT_VOLUME_USD:,.0f} — reject",
            file=sys.stderr,
        )
        return None

    if not probabilities:
        print("[WARN] Polymarket: 파싱된 outcome 확률 없음", file=sys.stderr)
        return None

    # Soft normalize if sum slightly off (log only; keep raw market prices)
    psum = sum(probabilities.values())
    if psum > 0 and (psum < 0.85 or psum > 1.15):
        print(f"[WARN] probability sum={psum:.3f} outside [0.85,1.15] — keep raw", file=sys.stderr)

    dominant = max(probabilities, key=lambda k: probabilities[k])
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "event_ticker": slug,
        "event_title": event.get("title") or meta.get("title") or slug,
        "meeting_date": end_date,
        "as_of": now_iso,
        "probabilities": probabilities,
        "outcomes": outcomes_detail,
        "dominant_outcome": dominant,
        "dominant_probability": probabilities[dominant],
        "volume_usd": round(volume_usd, 2),
        "liquidity_usd": round(liquidity_usd, 2) if liquidity_usd else None,
        "url": f"https://polymarket.com/event/{slug}",
    }


# ── Snapshot builders ─────────────────────────────────────────────────────────

def build_snapshot_polymarket(slot: str, now: datetime) -> dict:
    next_fomc = fetch_polymarket_next_fomc()
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": "1.1",
        "slot": slot,
        "source": "polymarket",
        "usage": "reference_only",
        "disclaimer_en": DISCLAIMER_EN,
        "disclaimer_ko": DISCLAIMER_KO,
        "next_fomc": next_fomc,
    }


def build_snapshot_kalshi(slot: str, now: datetime) -> dict:
    """Kalshi 소스 스냅샷 (legacy 1.0 호환 + 1.1 필드)."""
    event = fetch_next_fomc_event()

    if event is None:
        return {
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "schema_version": "1.1",
            "slot": slot,
            "source": "kalshi",
            "usage": "reference_only",
            "disclaimer_en": "Kalshi prediction market odds — reference only.",
            "disclaimer_ko": "Kalshi 예측시장 시세 — 참고용입니다.",
            "next_fomc": None,
        }

    event_ticker = event.get("event_ticker", "")
    meeting_date = event.get("end_date") or (event.get("scheduled_close_time", "")[:10])
    probabilities = fetch_fomc_probabilities(event_ticker)

    next_fomc: dict = {
        "event_ticker": event_ticker,
        "event_title": event_ticker,
        "meeting_date": meeting_date,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "probabilities": probabilities,
        "outcomes": [
            {"label": k, "probability": v, "question": k, "volume_usd": None}
            for k, v in probabilities.items()
        ],
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
        "schema_version": "1.1",
        "slot": slot,
        "source": "kalshi",
        "usage": "reference_only",
        "disclaimer_en": "Kalshi prediction market odds — reference only.",
        "disclaimer_ko": "Kalshi 예측시장 시세 — 참고용입니다.",
        "next_fomc": next_fomc,
    }


def build_snapshot(slot: str, now: datetime) -> dict:
    """
    기본 엔트리 — PREDICTION_SOURCE에 따라 Polymarket 또는 Kalshi.
    테스트 호환: build_snapshot 이름 유지 (Kalshi 경로 테스트가 패치하는 fetch_* 는 kalshi 전용).
    """
    source = PREDICTION_SOURCE
    if source == "kalshi":
        return build_snapshot_kalshi(slot, now)
    # default polymarket
    return build_snapshot_polymarket(slot, now)


def save_snapshot(snapshot: dict, slot: str, now: datetime) -> list[str]:
    """prediction/latest.json 과 history/<date>_<slot>.json 저장."""
    pred_dir = REPO_PATH / "prediction"
    history_dir = pred_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    latest_path = pred_dir / "latest.json"
    history_path = history_dir / f"{now.strftime('%Y-%m-%d')}_{slot}.json"

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
    latest_path.write_text(payload + "\n", encoding="utf-8")
    history_path.write_text(payload + "\n", encoding="utf-8")

    print(f"[OK] 저장 완료: {latest_path}")
    print(f"[OK] 저장 완료: {history_path}")

    return [str(latest_path), str(history_path)]


def main() -> None:
    source = PREDICTION_SOURCE
    if source == "kalshi" and not KALSHI_API_KEY:
        print("[ERROR] PREDICTION_SOURCE=kalshi 이지만 KALSHI_API_KEY 없음", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    slot = detect_slot(now)
    print(f"[INFO] Prediction 수집 시작 — source={source} slot={slot} UTC={now.strftime('%Y-%m-%d %H:%M')}")

    snapshot = build_snapshot(slot, now)

    fomc_info = snapshot.get("next_fomc")
    if fomc_info:
        print(
            f"[INFO] next FOMC: {fomc_info.get('event_ticker')} "
            f"({fomc_info.get('meeting_date')}) vol=${fomc_info.get('volume_usd')}"
        )
        probs = fomc_info.get("probabilities") or {}
        for outcome, prob in sorted(probs.items(), key=lambda x: -x[1]):
            print(f"  {outcome}: {prob:.1%}")
        print(f"[INFO] dominant: {fomc_info.get('dominant_outcome')} "
              f"({fomc_info.get('dominant_probability')})")
    else:
        print("[INFO] next_fomc: null — 이벤트 없음 또는 품질 가드 탈락")

    files = save_snapshot(snapshot, slot, now)

    date_str = now.strftime("%Y-%m-%d")
    ok = commit_and_push(
        repo=REPO_PATH,
        commit_message=f"prediction: {date_str} {slot} {source} update",
        files_to_add=files,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
