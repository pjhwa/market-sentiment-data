# Prediction Market Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kalshi FOMC 금리 결정 확률(동결/인하/인상)을 하루 2회 수집하여 `prediction/latest.json`에 저장하고 GitHub에 push하는 Collector 6을 구현한다.

**Architecture:** Kalshi REST API에서 다음 FOMC 이벤트를 자동 탐색하여 마켓별 yes_ask_price(확률)를 수집한다. Grok 없이 순수 확률 데이터만 저장. 기존 `commit_and_push()` 유틸리티를 재사용하며 `prediction/latest.json` + `prediction/history/<date>_<slot>.json` 패턴을 따른다.

**Tech Stack:** Python 3.11+, `requests`, Kalshi REST API v2, `collect.git_utils.commit_and_push`

## Global Constraints

- `KALSHI_API_KEY` 환경변수 필수 — 없으면 즉시 exit 1
- Kalshi API base: `https://trading-api.kalshi.com/trade-api/v2`
- 인증 헤더: `Authorization: Bearer <KALSHI_API_KEY>`
- slot 판정: UTC 09:00–17:59 → `pre_open`, 그 외 → `post_close` (기존 동일)
- `SENTIMENT_REPO_PATH` 환경변수로 repo 루트 지정 (기본: 스크립트 부모 디렉토리)
- schema_version: `"1.0"` (prediction 전용, sentiment schema와 별개)
- `yes_ask_price` 값 그대로 사용 (0.00~1.00), 가공 없음
- 모든 datetime은 UTC ISO8601

---

## File Map

| 파일 | 역할 |
|------|------|
| `collect/collect_prediction.py` | 신규 — Collector 6 메인 |
| `collect/test_collect_prediction.py` | 신규 — 단위 테스트 |
| `prediction/.gitkeep` | 신규 — 디렉토리 추적용 |
| `prediction/history/.gitkeep` | 신규 — 디렉토리 추적용 |
| `PROJECT_CONTEXT.md` | 수정 — Collector 6 항목 추가 |
| `README.md` | 수정 — prediction 섹션 추가 |

---

## Task 1: 디렉토리 초기화 & 스켈레톤

**Files:**
- Create: `prediction/.gitkeep`
- Create: `prediction/history/.gitkeep`
- Create: `collect/collect_prediction.py` (스켈레톤)

**Interfaces:**
- Produces: `python -m collect.collect_prediction` 실행 가능한 엔트리포인트

- [ ] **Step 1: prediction 디렉토리 생성**

```bash
mkdir -p /Users/jerry/dev/market-sentiment-data/prediction/history
touch /Users/jerry/dev/market-sentiment-data/prediction/.gitkeep
touch /Users/jerry/dev/market-sentiment-data/prediction/history/.gitkeep
```

- [ ] **Step 2: collect_prediction.py 스켈레톤 작성**

`collect/collect_prediction.py` 전체 내용:

```python
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

    # 오늘 이후 날짜 이벤트 중 가장 가까운 것 선택
    future_events = []
    for ev in events:
        end_date = ev.get("end_date") or ev.get("scheduled_close_time", "")[:10]
        if end_date >= today_str:
            future_events.append((end_date, ev))

    if not future_events:
        # 날짜 필터 없이 첫 번째 이벤트 사용
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
        # Kalshi는 센트 단위(0~100)로 반환하는 경우가 있음
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
    # 회의 날짜: end_date 또는 scheduled_close_time 앞 10자
    meeting_date = event.get("end_date") or (event.get("scheduled_close_time", "")[:10])

    probabilities = fetch_fomc_probabilities(event_ticker)

    next_fomc: dict = {
        "event_ticker": event_ticker,
        "meeting_date": meeting_date,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "probabilities": probabilities,
    }

    # dominant_outcome: 확률이 가장 높은 결과
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
```

- [ ] **Step 3: 임포트 오류 없는지 확인**

```bash
cd /Users/jerry/dev/market-sentiment-data && PYTHONPATH=. python3 -c "from collect.collect_prediction import detect_slot, _parse_outcome, build_snapshot; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/collect_prediction.py prediction/.gitkeep prediction/history/.gitkeep
git commit -m "feat: add collect_prediction.py skeleton and prediction/ directory"
```

---

## Task 2: 단위 테스트 작성 & 통과

**Files:**
- Create: `collect/test_collect_prediction.py`

**Interfaces:**
- Consumes: `detect_slot`, `_parse_outcome`, `fetch_next_fomc_event`, `fetch_fomc_probabilities`, `build_snapshot` from `collect.collect_prediction`
- Produces: pytest로 실행 가능한 단위 테스트 스위트

- [ ] **Step 1: 테스트 파일 작성**

`collect/test_collect_prediction.py` 전체 내용:

```python
"""collect_prediction 단위 테스트 — Kalshi API는 mock 처리"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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
        now = datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc)  # UTC 10시
        assert detect_slot(now) == "pre_open"

    def test_post_close_hour(self):
        now = datetime(2026, 6, 29, 22, 0, tzinfo=timezone.utc)  # UTC 22시
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

    def test_cut25_alternative(self):
        assert _parse_outcome("FOMC-26JUL29-CUT25") == "cut_25bps"

    def test_unknown_returns_none(self):
        assert _parse_outcome("FOMC-26JUL29-SOMETHING_WEIRD") is None

    def test_50bps_takes_priority_over_25bps(self):
        # DOWN50은 DOWN25보다 먼저 매칭되어야 함
        assert _parse_outcome("FOMC-26JUL29-DOWN50") == "cut_50bps"


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


# ---------------------------------------------------------------------------
# fetch_fomc_probabilities
# ---------------------------------------------------------------------------

MOCK_EVENT_DETAIL = {
    "markets": [
        {"ticker": "FOMC-26JUL29-UNCHANGED", "yes_ask": 72},
        {"ticker": "FOMC-26JUL29-DOWN25", "yes_ask": 23},
        {"ticker": "FOMC-26JUL29-DOWN50", "yes_ask": 4},
        {"ticker": "FOMC-26JUL29-UP25", "yes_ask": 1},
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
    def test_skips_unknown_tickers(self, mock_get):
        mock_get.return_value = {
            "markets": [
                {"ticker": "FOMC-26JUL29-UNCHANGED", "yes_ask": 80},
                {"ticker": "FOMC-26JUL29-WEIRD_THING", "yes_ask": 20},
            ]
        }
        probs = fetch_fomc_probabilities("FOMC-26JUL29")
        assert "no_change" in probs
        assert len(probs) == 1  # WEIRD_THING 건너뜀

    @patch("collect.collect_prediction._kalshi_get")
    def test_returns_empty_on_api_failure(self, mock_get):
        mock_get.return_value = None
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
        assert snap["next_fomc"]["dominant_outcome"] == "no_change"
        assert snap["next_fomc"]["dominant_probability"] == pytest.approx(0.72)

    @patch("collect.collect_prediction.fetch_next_fomc_event")
    def test_null_next_fomc_when_no_event(self, mock_event):
        mock_event.return_value = None
        now = datetime(2026, 6, 29, 6, 30, tzinfo=timezone.utc)
        snap = build_snapshot("pre_open", now)
        assert snap["next_fomc"] is None

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
        # 직렬화 가능한지 확인
        serialized = json.dumps(snap)
        assert "FOMC-26JUL29" in serialized
```

- [ ] **Step 2: 테스트 실행 (모두 통과해야 함)**

```bash
cd /Users/jerry/dev/market-sentiment-data && PYTHONPATH=. python3 -m pytest collect/test_collect_prediction.py -v
```

Expected: 전체 PASSED (약 15개 테스트)

- [ ] **Step 3: commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/test_collect_prediction.py
git commit -m "test: add collect_prediction unit tests"
```

---

## Task 3: Cron 등록

**Files:**
- Modify: crontab (시스템 파일)

**Interfaces:**
- Consumes: `collect/collect_prediction.py` (Task 1에서 완성)
- Produces: 하루 2회 자동 실행 (KST 05:45, 21:45)

- [ ] **Step 1: 현재 crontab 백업**

```bash
crontab -l > /tmp/crontab_backup_$(date +%Y%m%d).txt
```

- [ ] **Step 2: prediction 수집 라인 추가**

기존 crontab에 다음 라인을 추가한다 (마지막 기존 collector 라인 바로 아래):

```
45 21,5 * * * cd /Users/jerry/dev/market-sentiment-data && GIT_SSH_COMMAND="ssh -F /Users/jerry/.ssh/config -o StrictHostKeyChecking=no" PYTHONPATH=/Users/jerry/dev/market-sentiment-data KALSHI_API_KEY="<YOUR_KEY>" /opt/homebrew/bin/python3 -u -m collect.collect_prediction >> prediction/prediction.log 2>&1
```

추가 방법:
```bash
(crontab -l; echo '45 21,5 * * * cd /Users/jerry/dev/market-sentiment-data && GIT_SSH_COMMAND="ssh -F /Users/jerry/.ssh/config -o StrictHostKeyChecking=no" PYTHONPATH=/Users/jerry/dev/market-sentiment-data KALSHI_API_KEY="<YOUR_KEY>" /opt/homebrew/bin/python3 -u -m collect.collect_prediction >> prediction/prediction.log 2>&1') | crontab -
```

**주의:** `<YOUR_KEY>` 자리에 실제 Kalshi API 키를 입력해야 한다.

- [ ] **Step 3: cron 확인**

```bash
crontab -l | grep collect_prediction
```

Expected: 방금 추가한 라인이 출력됨

---

## Task 4: 수동 실행 검증

**Files:**
- (생성됨) `prediction/latest.json`
- (생성됨) `prediction/history/<date>_<slot>.json`

**Interfaces:**
- Consumes: 실제 Kalshi API (KALSHI_API_KEY 필요)

- [ ] **Step 1: KALSHI_API_KEY 확인**

```bash
echo $KALSHI_API_KEY | head -c 10
```

키가 설정되어 있어야 함. 없으면 환경에 설정 후 진행.

- [ ] **Step 2: 수동 실행**

```bash
cd /Users/jerry/dev/market-sentiment-data && PYTHONPATH=. KALSHI_API_KEY="$KALSHI_API_KEY" /opt/homebrew/bin/python3 -u -m collect.collect_prediction
```

Expected 출력 예시:
```
[INFO] Prediction 수집 시작 — slot=post_close, UTC=2026-06-29 ...
[INFO] 다음 FOMC: FOMC-26JUL29 (2026-07-29)
  no_change: 72.0%
  cut_25bps: 23.0%
  ...
[OK] 저장 완료: .../prediction/latest.json
[OK] 저장 완료: .../prediction/history/2026-06-29_post_close.json
[OK] GitHub push 성공
```

- [ ] **Step 3: latest.json 내용 확인**

```bash
cat /Users/jerry/dev/market-sentiment-data/prediction/latest.json
```

Expected: `schema_version`, `source`, `next_fomc.probabilities` 포함된 JSON

- [ ] **Step 4: git log 확인**

```bash
git -C /Users/jerry/dev/market-sentiment-data log --oneline -3
```

Expected: `prediction: 2026-06-29 post_close update` 커밋이 최상단

---

## Task 5: 문서 업데이트

**Files:**
- Modify: `PROJECT_CONTEXT.md`
- Modify: `README.md`

- [ ] **Step 1: PROJECT_CONTEXT.md — Layer 1 블록에 Collector 6 추가**

파일 내 `collect_morning_briefing.py` 라인 바로 아래에 추가:
```
│  · collect_prediction.py       # Collector 6 — Kalshi FOMC 예측시장 확률
```

파일 맵 섹션에도 추가:
```
├── prediction/
│   ├── latest.json               # Prediction Market: 다음 FOMC 결정 확률 (Kalshi)
│   ├── prediction.log            # Cron log for collect_prediction
│   └── history/YYYY-MM-DD_<slot>.json
```

환경변수 테이블에 추가:
```
| `KALSHI_API_KEY` | (required) | collect_prediction |
```

새 섹션 추가 (## 10. Collector 6 — Prediction Market):
```markdown
## 10. Collector 6 — Prediction Market (`collect/collect_prediction.py`)

Kalshi 예측시장에서 다음 FOMC 금리 결정 확률을 수집한다. Grok 없음 — 순수 확률 데이터.

**API:** `https://trading-api.kalshi.com/trade-api/v2`  
**인증:** `KALSHI_API_KEY` 환경변수 (Bearer 토큰)  
**스케줄:** 하루 2회 (KST 05:45 pre_open, 21:45 post_close)

**수집 흐름:**
1. `GET /events?series_ticker=FOMC&status=open` → 다음 FOMC 이벤트 탐색
2. `GET /events/{event_ticker}` → 마켓별 yes_ask_price 수집
3. outcome 매핑: UNCHANGED→no_change, DOWN25→cut_25bps, DOWN50→cut_50bps, UP25→hike_25bps
4. `prediction/latest.json` + `prediction/history/<date>_<slot>.json` 저장 → git push

**FOMC 이벤트 없을 때 (회의 직후 공백기):** `next_fomc: null` 저장, 정상 종료.
```

- [ ] **Step 2: README.md — Prediction Market 섹션 추가**

기존 collectors 테이블 또는 섹션 아래에 추가:

```markdown
## Collector 6 — Prediction Market (`prediction/`)

**Source:** Kalshi 예측시장 REST API  
**Output:** `prediction/latest.json`

다음 FOMC 금리 결정에 대한 시장 참여자들의 확률 베팅:

```json
{
  "next_fomc": {
    "event_ticker": "FOMC-26JUL29",
    "meeting_date": "2026-07-29",
    "probabilities": {
      "no_change": 0.72,
      "cut_25bps": 0.23,
      "cut_50bps": 0.04,
      "hike_25bps": 0.01
    },
    "dominant_outcome": "no_change",
    "dominant_probability": 0.72
  }
}
```

SniperBoard 연동: `prediction_service.py` → `/api/prediction` (별도 작업)
```

- [ ] **Step 3: commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add PROJECT_CONTEXT.md README.md
git commit -m "docs: add Collector 6 (prediction market) to PROJECT_CONTEXT and README"
```
