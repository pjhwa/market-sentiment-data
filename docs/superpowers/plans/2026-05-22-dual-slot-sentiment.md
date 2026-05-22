# Dual-Slot Sentiment Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 하루 2회 수집(pre_open / post_close)을 올바르게 저장하고, 인트라데이 심리 변화를 계산해 SniperBoard까지 전달한다.

**Architecture:** 수집기가 UTC 시간으로 슬롯을 자동 감지해 `history/YYYY-MM-DD_{slot}.json`으로 저장한다. `post_close` 실행 시 당일 `pre_open` 파일을 읽어 `intraday_shift`를 계산한다. SniperBoard `/api/sentiment`는 `{latest, today: {pre_open, post_close}}` 구조로 응답을 확장한다.

**Tech Stack:** Python 3.11, unittest/mock, FastAPI/Pydantic, Next.js/TypeScript

---

## 파일 맵

| 파일 | 변경 |
|------|------|
| `market-sentiment-data/schema.json` | v1.2: slot, intraday_shift 필드 추가 |
| `market-sentiment-data/collect_sentiment.py` | 슬롯 감지, 파일명 변경, intraday_shift 계산 |
| `market-sentiment-data/collect/test_collect_sentiment.py` | 신규: 슬롯 감지·intraday_shift 단위 테스트 |
| `sniperboard/backend/services/sentiment_service.py` | fetch_today_slots(), enrich_with_delta() 수정 |
| `sniperboard/backend/tests/test_sentiment_service.py` | 신규: sentiment_service 단위 테스트 |
| `sniperboard/backend/api/schemas.py` | SnapshotData, TodaySlots 추가, SentimentResponse 재구성 |
| `sniperboard/backend/api/endpoints.py` | /sentiment 응답 구조 변경 |
| `sniperboard/frontend/app/types.ts` | SnapshotData, SentimentData 인터페이스 업데이트 |
| `sniperboard/frontend/components/SentimentTab.tsx` | data → data.latest 참조 경로 수정 |
| `market-sentiment-data/README.md` | 파일 구조 업데이트 |

---

## Task 1: schema.json v1.2

**Files:**
- Modify: `market-sentiment-data/schema.json`

- [ ] **Step 1: schema_version enum에 "1.2" 추가, slot 필드 정의 추가**

`schema.json`의 `schema_version` enum에 `"1.2"` 추가:
```json
"schema_version": {
  "type": "string",
  "enum": ["1.0", "1.1", "1.2"],
  "description": "1.0: 기본 스키마. 1.1: price_context + divergence 추가. 1.2: slot + intraday_shift 추가."
},
```

최상위 `properties`에 `slot` 필드 추가 (required는 건드리지 않음 — 하위 호환):
```json
"slot": {
  "type": "string",
  "enum": ["pre_open", "post_close"],
  "description": "수집 슬롯. pre_open: 미국 장 개장 전(13:00 UTC). post_close: 미국 장 마감 후(21:00 UTC)."
}
```

- [ ] **Step 2: definitions에 IntradayShiftEnum 추가**

`definitions` 섹션에 추가:
```json
"IntradayShiftEnum": {
  "type": ["string", "null"],
  "enum": ["cooling", "stable", "heating", null],
  "description": "post_close에서 당일 pre_open 대비 심리 변화. pre_open 슬롯에서는 항상 null."
}
```

- [ ] **Step 3: SymbolSentiment와 MarketSentiment에 intraday_shift 필드 추가**

`SymbolSentiment.properties`에 추가:
```json
"intraday_shift": {
  "$ref": "#/definitions/IntradayShiftEnum",
  "description": "post_close 슬롯에서 당일 pre_open 대비 심리 변화. v1.2 추가."
}
```

`MarketSentiment.properties`에 동일하게 추가.

- [ ] **Step 4: 커밋**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add schema.json
git commit -m "feat: schema v1.2 — slot and intraday_shift fields"
```

---

## Task 2: 슬롯 감지 + 파일명 변경 (collect_sentiment.py)

**Files:**
- Modify: `market-sentiment-data/collect_sentiment.py`
- Create: `market-sentiment-data/collect/test_collect_sentiment.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`market-sentiment-data/collect/test_collect_sentiment.py` 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_sentiment.py -v
```
Expected: `AttributeError: module 'collect_sentiment' has no attribute 'detect_slot'`

- [ ] **Step 3: collect_sentiment.py에 detect_slot, history_filename 추가**

`collect_sentiment.py`에서 `# ── 설정` 블록 아래에 추가:

```python
def detect_slot(now: datetime) -> str:
    """UTC 시각으로 수집 슬롯 판별. SENTIMENT_SLOT 환경변수로 오버라이드 가능.
    09:00–17:59 UTC → pre_open (미국 장 개장 전)
    그 외 → post_close (미국 장 마감 후)
    """
    override = os.environ.get("SENTIMENT_SLOT", "").strip()
    if override in ("pre_open", "post_close"):
        return override
    if 9 <= now.hour < 18:
        return "pre_open"
    return "post_close"


def history_filename(date_str: str, slot: str) -> Path:
    return REPO_PATH / "history" / f"{date_str}_{slot}.json"
```

- [ ] **Step 4: main()에서 슬롯 감지 + 파일명 변경 적용**

`main()` 함수 상단 `now_iso` 정의 직후에 추가:
```python
slot = detect_slot(now)
print(f"[INFO] 슬롯: {slot}")
```

`history_path` 정의를 수정:
```python
# 기존:
history_path = REPO_PATH / "history" / f"{date_str}.json"
# 변경 후:
history_path = history_filename(date_str, slot)
```

`snapshot` dict에 `slot` 필드 추가:
```python
snapshot = {
    "generated_at": now_iso,
    "schema_version": "1.2",
    "slot": slot,
    "market": market_entry,
    "symbols": symbol_entries,
}
```

`git_commit_push` 호출 시 history_path를 전달하도록 `git_commit_push` 시그니처 수정:
```python
# 기존:
def git_commit_push(repo: Path, date_str: str, time_str: str) -> bool:
    ...
    run(["git", "add", "latest.json", f"history/{date_str}.json"])
    result = run(["git", "commit", "-m", f"sentiment: {date_str} {time_str} update"])

# 변경 후:
def git_commit_push(repo: Path, date_str: str, time_str: str, history_path: Path) -> bool:
    ...
    run(["git", "add", "latest.json", str(history_path.relative_to(repo))])
    result = run(["git", "commit", "-m", f"sentiment: {date_str} {time_str} update"])
```

`main()` 하단 `git_commit_push` 호출 수정:
```python
push_ok = git_commit_push(REPO_PATH, date_str, time_str, history_path)
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_sentiment.py -v
```
Expected: 7개 PASS

- [ ] **Step 6: 커밋**

```bash
git add collect_sentiment.py collect/test_collect_sentiment.py
git commit -m "feat: slot detection and per-slot history filenames"
```

---

## Task 3: intraday_shift 계산 (collect_sentiment.py)

**Files:**
- Modify: `market-sentiment-data/collect_sentiment.py`
- Modify: `market-sentiment-data/collect/test_collect_sentiment.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`collect/test_collect_sentiment.py`에 클래스 추가:

```python
class TestComputeIntradayShift(unittest.TestCase):
    def test_heating(self):
        self.assertEqual(cs.compute_intraday_shift(0, 1), "heating")

    def test_cooling(self):
        self.assertEqual(cs.compute_intraday_shift(1, 0), "cooling")

    def test_stable(self):
        self.assertEqual(cs.compute_intraday_shift(1, 1), "stable")

    def test_large_jump(self):
        self.assertEqual(cs.compute_intraday_shift(-2, 2), "heating")


class TestLoadPreOpenScores(unittest.TestCase):
    def test_returns_scores_when_file_exists(self):
        import json, tempfile
        snapshot = {
            "slot": "pre_open",
            "market": {"sentiment_score": 1},
            "symbols": [
                {"symbol": "TSLA", "sentiment_score": -1},
                {"symbol": "AAPL", "sentiment_score": 0},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(snapshot, f)
            tmp = Path(f.name)
        try:
            result = cs.load_pre_open_scores(tmp)
            self.assertEqual(result["market"], 1)
            self.assertEqual(result["symbols"]["TSLA"], -1)
            self.assertEqual(result["symbols"]["AAPL"], 0)
        finally:
            tmp.unlink()

    def test_returns_empty_when_file_missing(self):
        result = cs.load_pre_open_scores(Path("/nonexistent/path.json"))
        self.assertIsNone(result["market"])
        self.assertEqual(result["symbols"], {})
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_sentiment.py::TestComputeIntradayShift collect/test_collect_sentiment.py::TestLoadPreOpenScores -v
```
Expected: `AttributeError: module 'collect_sentiment' has no attribute 'compute_intraday_shift'`

- [ ] **Step 3: compute_intraday_shift, load_pre_open_scores 추가**

`collect_sentiment.py`의 `# ── divergence 계산` 섹션 아래에 추가:

```python
# ── intraday_shift 계산 ────────────────────────────────────────────────────

def compute_intraday_shift(prev_score: int, curr_score: int) -> str:
    if curr_score > prev_score:
        return "heating"
    if curr_score < prev_score:
        return "cooling"
    return "stable"


def load_pre_open_scores(path: Path) -> dict:
    """pre_open 스냅샷에서 sentiment_score를 추출.
    반환: {"market": int|None, "symbols": {symbol: score}}
    파일 없거나 파싱 실패 시 빈 구조 반환.
    """
    result: dict = {"market": None, "symbols": {}}
    if not path.exists():
        print(f"[INFO] pre_open 파일 없음 (intraday_shift=null): {path}", file=sys.stderr)
        return result
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        market = data.get("market") or {}
        result["market"] = market.get("sentiment_score")
        for sym in data.get("symbols") or []:
            if sym.get("symbol") and sym.get("sentiment_score") is not None:
                result["symbols"][sym["symbol"]] = sym["sentiment_score"]
    except Exception as e:
        print(f"[WARN] pre_open 파일 파싱 실패 ({e}), intraday_shift=null", file=sys.stderr)
    return result
```

- [ ] **Step 4: main()에 intraday_shift 통합**

`main()`에서 `history_path` 정의 직후에 pre_open 로드 로직 추가:

```python
# intraday_shift 계산용 pre_open 스코어 로드 (post_close 슬롯에서만 의미 있음)
pre_open_path = history_filename(date_str, "pre_open")
pre_open_scores = load_pre_open_scores(pre_open_path) if slot == "post_close" else {"market": None, "symbols": {}}
```

`build_symbol_entry` 호출 부분을 수정해 `intraday_shift`를 추가:

```python
# 기존 build_symbol_entry 호출 아래에 intraday_shift 주입
entry = build_symbol_entry(parsed, symbol, now_iso, ctx, divergence)
prev_score = pre_open_scores["symbols"].get(symbol)
entry["intraday_shift"] = (
    compute_intraday_shift(prev_score, entry["sentiment_score"])
    if prev_score is not None else None
)
```

`build_market_entry` 호출 부분 아래에 intraday_shift 주입:

```python
market_entry = build_market_entry(market_parsed, now_iso)
prev_market_score = pre_open_scores["market"]
market_entry["intraday_shift"] = (
    compute_intraday_shift(prev_market_score, market_entry["sentiment_score"])
    if prev_market_score is not None else None
)
```

폴백 market_entry에도 추가:
```python
if market_entry is None:
    market_entry = {
        "as_of": now_iso,
        "sentiment": "neutral",
        "sentiment_score": 0,
        "trend_vs_yesterday": "stable",
        "extreme_flag": "none",
        "key_reason": "시장 전체 데이터 수집 실패",
        "confidence": "low",
        "intraday_shift": None,
    }
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_sentiment.py -v
```
Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```bash
git add collect_sentiment.py collect/test_collect_sentiment.py
git commit -m "feat: intraday_shift computation for post_close slot"
```

---

## Task 4: SniperBoard sentiment_service.py 수정

**Files:**
- Modify: `sniperboard/backend/services/sentiment_service.py`
- Create: `sniperboard/backend/tests/test_sentiment_service.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`sniperboard/backend/tests/test_sentiment_service.py` 생성:

```python
"""
sentiment_service 단위 테스트
cd sniperboard/backend && python -m pytest tests/test_sentiment_service.py -v
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import services.sentiment_service as svc


def _make_resp(data: dict):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


PRE_OPEN_SNAPSHOT = {
    "generated_at": "2026-05-21T13:00:00Z",
    "schema_version": "1.2",
    "slot": "pre_open",
    "market": {"sentiment_score": 0, "sentiment": "neutral",
               "trend_vs_yesterday": "stable", "extreme_flag": "none",
               "key_reason": "test", "confidence": "med", "as_of": "2026-05-21T13:00:00Z",
               "intraday_shift": None},
    "symbols": [
        {"symbol": "TSLA", "sentiment_score": -1, "sentiment": "fearful",
         "trend_vs_yesterday": "stable", "mention_volume": "normal",
         "key_reason": "test", "bot_suspected": "no", "confidence": "med",
         "source": "grok", "as_of": "2026-05-21T13:00:00Z", "intraday_shift": None},
    ],
}

POST_CLOSE_SNAPSHOT = {
    "generated_at": "2026-05-21T21:00:00Z",
    "schema_version": "1.2",
    "slot": "post_close",
    "market": {"sentiment_score": 1, "sentiment": "optimistic",
               "trend_vs_yesterday": "heating", "extreme_flag": "none",
               "key_reason": "test", "confidence": "high", "as_of": "2026-05-21T21:00:00Z",
               "intraday_shift": "heating"},
    "symbols": [
        {"symbol": "TSLA", "sentiment_score": 0, "sentiment": "neutral",
         "trend_vs_yesterday": "stable", "mention_volume": "normal",
         "key_reason": "test", "bot_suspected": "no", "confidence": "med",
         "source": "grok", "as_of": "2026-05-21T21:00:00Z", "intraday_shift": "heating"},
    ],
}


class TestFetchTodaySlots(unittest.TestCase):
    def test_returns_both_slots_when_available(self):
        def side_effect(url, headers=None, timeout=None):
            if "pre_open" in url:
                return _make_resp(PRE_OPEN_SNAPSHOT)
            if "post_close" in url:
                return _make_resp(POST_CLOSE_SNAPSHOT)
            raise ValueError(f"unexpected url: {url}")

        with patch("services.sentiment_service.requests.get", side_effect=side_effect):
            with patch.dict("os.environ", {"SENTIMENT_DATA_HISTORY_BASE": "https://example.com/history"}):
                result = svc.fetch_today_slots("2026-05-21")

        self.assertIsNotNone(result["pre_open"])
        self.assertIsNotNone(result["post_close"])
        self.assertEqual(result["pre_open"]["slot"], "pre_open")
        self.assertEqual(result["post_close"]["slot"], "post_close")

    def test_returns_none_when_slot_missing(self):
        def side_effect(url, headers=None, timeout=None):
            raise Exception("404")

        with patch("services.sentiment_service.requests.get", side_effect=side_effect):
            with patch.dict("os.environ", {"SENTIMENT_DATA_HISTORY_BASE": "https://example.com/history"}):
                result = svc.fetch_today_slots("2026-05-21")

        self.assertIsNone(result["pre_open"])
        self.assertIsNone(result["post_close"])


class TestEnrichWithDeltaNewSlot(unittest.TestCase):
    def test_uses_post_close_for_delta(self):
        """어제 post_close 파일로 delta 계산."""
        snapshot = {
            "available": True,
            "symbols": [{"symbol": "TSLA", "sentiment_score": 1}],
        }
        yesterday_post_close = {
            "slot": "post_close",
            "symbols": [{"symbol": "TSLA", "sentiment_score": -1}],
        }

        def side_effect(url, headers=None, timeout=None):
            if "post_close" in url:
                return _make_resp(yesterday_post_close)
            raise Exception("404")

        with patch("services.sentiment_service.requests.get", side_effect=side_effect):
            with patch.dict("os.environ", {"SENTIMENT_DATA_HISTORY_BASE": "https://example.com/history"}):
                result = svc.enrich_with_delta(snapshot)

        tsla = next(s for s in result["symbols"] if s["symbol"] == "TSLA")
        self.assertEqual(tsla["score_delta"], 2)  # 1 - (-1) = 2


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd /Users/jerry/dev/sniperboard/backend
python -m pytest tests/test_sentiment_service.py -v
```
Expected: `AttributeError: module 'services.sentiment_service' has no attribute 'fetch_today_slots'`

- [ ] **Step 3: fetch_today_slots() 추가, enrich_with_delta() 수정**

`sniperboard/backend/services/sentiment_service.py`에서 `fetch_latest()` 함수 아래에 추가:

```python
def fetch_today_slots(date_str: str) -> dict:
    """당일 UTC 날짜 기준으로 pre_open / post_close 슬롯 파일을 fetch.
    슬롯 파일이 없거나 fetch 실패 시 해당 키를 None으로 반환.
    반환: {"pre_open": dict|None, "post_close": dict|None}
    """
    result: dict = {"pre_open": None, "post_close": None}
    if not SENTIMENT_DATA_HISTORY_BASE:
        return result

    base = SENTIMENT_DATA_HISTORY_BASE.rstrip("/")
    for slot in ("pre_open", "post_close"):
        url = f"{base}/{date_str}_{slot}.json"
        data = _fetch_json(url)
        if data is not None:
            result[slot] = data
    return result
```

`enrich_with_delta()`에서 yesterday url 구성 부분 수정:

```python
# 기존:
yesterday_url = f"{SENTIMENT_DATA_HISTORY_BASE.rstrip('/')}/{yesterday}.json" if SENTIMENT_DATA_HISTORY_BASE else ""

# 변경 후 (post_close 우선, 없으면 구형 파일 폴백):
if SENTIMENT_DATA_HISTORY_BASE:
    base = SENTIMENT_DATA_HISTORY_BASE.rstrip("/")
    yesterday_post_close_url = f"{base}/{yesterday}_post_close.json"
    yesterday_legacy_url = f"{base}/{yesterday}.json"
    yesterday_data = _fetch_json(yesterday_post_close_url) or _fetch_json(yesterday_legacy_url)
else:
    yesterday_data = None
```

`yesterday_url` 변수를 직접 사용하던 하단 코드도 `yesterday_data`를 바로 참조하도록 수정 (더 이상 `_fetch_json(yesterday_url)` 호출 제거):

```python
# 기존:
yesterday_data = _fetch_json(yesterday_url) if yesterday_url else None

# 이 줄 삭제 (위에서 이미 처리)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd /Users/jerry/dev/sniperboard/backend
python -m pytest tests/test_sentiment_service.py -v
```
Expected: 4개 PASS

- [ ] **Step 5: 커밋**

```bash
cd /Users/jerry/dev/sniperboard
git add backend/services/sentiment_service.py backend/tests/test_sentiment_service.py
git commit -m "feat: fetch_today_slots and updated enrich_with_delta for dual-slot"
```

---

## Task 5: SniperBoard schemas.py + endpoints.py 수정

**Files:**
- Modify: `sniperboard/backend/api/schemas.py`
- Modify: `sniperboard/backend/api/endpoints.py`

- [ ] **Step 1: schemas.py — SymbolSentiment, MarketSentiment에 intraday_shift 추가**

`sniperboard/backend/api/schemas.py`에서 `SymbolSentiment` 클래스에 필드 추가:
```python
class SymbolSentiment(BaseModel):
    symbol: str
    as_of: str
    sentiment: str
    sentiment_score: int
    trend_vs_yesterday: str
    mention_volume: str
    key_reason: str
    bot_suspected: str
    confidence: str
    source: str
    score_delta: Optional[int] = None
    intraday_shift: Optional[str] = None  # 추가
```

`MarketSentiment` 클래스에 필드 추가:
```python
class MarketSentiment(BaseModel):
    as_of: str
    sentiment: str
    sentiment_score: int
    trend_vs_yesterday: str
    extreme_flag: str
    key_reason: str
    confidence: str
    intraday_shift: Optional[str] = None  # 추가
```

- [ ] **Step 2: schemas.py — SnapshotData, TodaySlots 클래스 추가, SentimentResponse 재구성**

`SentimentResponse` 클래스 위에 새 클래스 추가:
```python
class SnapshotData(BaseModel):
    generated_at: Optional[str] = None
    schema_version: Optional[str] = None
    slot: Optional[str] = None
    market: Optional[MarketSentiment] = None
    symbols: Optional[List[SymbolSentiment]] = None


class TodaySlots(BaseModel):
    pre_open: Optional[SnapshotData] = None
    post_close: Optional[SnapshotData] = None
```

`SentimentResponse` 재구성:
```python
class SentimentResponse(BaseModel):
    available: bool
    latest: Optional[SnapshotData] = None
    today: Optional[TodaySlots] = None
    error: Optional[str] = None
```

- [ ] **Step 3: endpoints.py — /sentiment 응답 구조 변경**

`sniperboard/backend/api/endpoints.py`의 import 수정:
```python
from services.sentiment_service import fetch_latest, enrich_with_delta, fetch_today_slots
```

`get_sentiment_endpoint()` 함수 전체 교체:
```python
@router.get("/sentiment", response_model=SentimentResponse)
async def get_sentiment_endpoint():
    """소셜 심리 최신 스냅샷 + 당일 슬롯. 실패 시 available:false로 200 반환."""
    try:
        from datetime import datetime, timezone
        snapshot = fetch_latest()
        snapshot = enrich_with_delta(snapshot)

        if not snapshot.get("available"):
            return {"available": False, "error": snapshot.get("error", "데이터 없음")}

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_slots = fetch_today_slots(today_str)

        latest_data = {k: v for k, v in snapshot.items() if k != "available"}
        return {
            "available": True,
            "latest": latest_data,
            "today": {
                "pre_open": today_slots["pre_open"],
                "post_close": today_slots["post_close"],
            },
        }
    except Exception as e:
        logger.error(f"Error in /sentiment endpoint: {e}", exc_info=True)
        return {"available": False, "error": "심리 데이터 처리 중 오류 발생"}
```

- [ ] **Step 4: 커밋**

```bash
cd /Users/jerry/dev/sniperboard
git add backend/api/schemas.py backend/api/endpoints.py
git commit -m "feat: dual-slot sentiment API response structure"
```

---

## Task 6: SniperBoard 프론트엔드 타입 + 컴포넌트 수정

**Files:**
- Modify: `sniperboard/frontend/app/types.ts`
- Modify: `sniperboard/frontend/components/SentimentTab.tsx`

- [ ] **Step 1: types.ts — SnapshotData 인터페이스 추가, SentimentData 재구성**

`sniperboard/frontend/app/types.ts`에서 기존 `SentimentData` 인터페이스를 교체:

`SymbolSentiment`에 `intraday_shift` 추가:
```typescript
export interface SymbolSentiment {
  symbol: string;
  as_of: string;
  sentiment: SentimentEnum;
  sentiment_score: number;
  trend_vs_yesterday: TrendEnum;
  mention_volume: VolumeEnum;
  key_reason: string;
  bot_suspected: 'yes' | 'no' | 'unclear';
  confidence: ConfidenceEnum;
  source: string;
  score_delta: number | null;
  intraday_shift: TrendEnum | null;  // 추가
}
```

`MarketSentiment`에 `intraday_shift` 추가:
```typescript
export interface MarketSentiment {
  as_of: string;
  sentiment: SentimentEnum;
  sentiment_score: number;
  trend_vs_yesterday: TrendEnum;
  extreme_flag: 'none' | 'extreme_fear' | 'extreme_greed';
  key_reason: string;
  confidence: ConfidenceEnum;
  intraday_shift: TrendEnum | null;  // 추가
}
```

`SnapshotData` 인터페이스 추가 (`SentimentData` 앞에):
```typescript
export interface SnapshotData {
  generated_at?: string;
  schema_version?: string;
  slot?: 'pre_open' | 'post_close';
  market?: MarketSentiment;
  symbols?: SymbolSentiment[];
}
```

`SentimentData` 재구성:
```typescript
export interface SentimentData {
  available: boolean;
  latest?: SnapshotData;
  today?: {
    pre_open?: SnapshotData | null;
    post_close?: SnapshotData | null;
  };
  error?: string;
}
```

- [ ] **Step 2: SentimentTab.tsx — data 참조를 data.latest로 수정**

`sniperboard/frontend/components/SentimentTab.tsx`에서 `SentimentTab` 함수의 변수 추가:

```tsx
export default function SentimentTab() {
  const { data, isLoading, isError } = useSentiment();

  // ... (isLoading, isError 처리 동일)

  if (!data.available) {
    // 동일
  }

  const snapshot = data.latest;  // ← 추가
  if (!snapshot) {
    return (
      <div className="glass-card rounded-2xl p-6 border border-zinc-700/40 text-zinc-400 text-sm">
        스냅샷 데이터가 없습니다.
      </div>
    );
  }

  const generatedAt = snapshot.generated_at
    ? new Date(snapshot.generated_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })
    : null;
```

이후 `data.market` → `snapshot.market`, `data.symbols` → `snapshot.symbols`, `data.generated_at` → 위에서 이미 처리된 `generatedAt` 사용으로 변경:

```tsx
  return (
    <div className="space-y-5 animate-fade-in">
      {snapshot.market && <MarketCard market={snapshot.market} />}

      {snapshot.symbols && snapshot.symbols.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {snapshot.symbols.map((sym) => (
            <SymbolCard key={sym.symbol} sym={sym} />
          ))}
        </div>
      )}

      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 pt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
        <p className="italic">
          ⚠ 소셜 심리는 보조 참고용입니다. 진입 결정은 가격 신호를 우선하세요.
        </p>
        {generatedAt && (
          <p className="shrink-0">수집: {generatedAt} KST{snapshot.slot ? ` (${snapshot.slot})` : ''}</p>
        )}
      </div>
    </div>
  );
```

- [ ] **Step 3: TypeScript 컴파일 확인**

```bash
cd /Users/jerry/dev/sniperboard/frontend
npx tsc --noEmit
```
Expected: 에러 없음

- [ ] **Step 4: 커밋**

```bash
cd /Users/jerry/dev/sniperboard
git add frontend/app/types.ts frontend/components/SentimentTab.tsx
git commit -m "feat: frontend types and SentimentTab updated for dual-slot API"
```

---

## Task 7: README 업데이트 + 기존 테스트 통과 확인

**Files:**
- Modify: `market-sentiment-data/README.md`

- [ ] **Step 1: 기존 테스트 전체 통과 확인**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/ -v
```
Expected: 전체 PASS (기존 price_context 테스트 포함)

- [ ] **Step 2: README 파일 구조 섹션 수정**

`README.md`의 리포 구조 부분 업데이트:
```markdown
## 리포 구조

\```
market-sentiment-data/
├── README.md              # 이 문서
├── schema.json            # 데이터 계약 (JSON Schema draft-07, v1.2)
├── latest.json            # 가장 최근 스냅샷 — 소비측이 주로 읽는 파일
└── history/
    ├── 2026-05-21_pre_open.json    # 당일 pre_open 슬롯 (13:00 UTC)
    ├── 2026-05-21_post_close.json  # 당일 post_close 슬롯 (21:00 UTC)
    └── ...
\```

- **`latest.json`**: cron 실행마다 덮어쓰기. 항상 최신 상태.
- **`history/YYYY-MM-DD_pre_open.json`**: 미국 장 개장 전(13:00 UTC) 스냅샷.
- **`history/YYYY-MM-DD_post_close.json`**: 미국 장 마감 후(21:00 UTC) 스냅샷. `intraday_shift` 포함.
- **`history/YYYY-MM-DD.json`**: v1.1 이전 구형 파일. 조회 시 폴백으로 사용.
```

스키마 요약 표에 추가:
```markdown
| `slot` | `pre_open` `post_close` |
| `intraday_shift` | `cooling` `stable` `heating` `null` |
```

- [ ] **Step 3: 커밋**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add README.md
git commit -m "docs: update README for dual-slot history structure"
```

---

## Self-Review

**Spec coverage 확인:**
- [x] `history/YYYY-MM-DD_pre_open.json` / `history/YYYY-MM-DD_post_close.json` 파일 구조 → Task 2
- [x] 슬롯 자동 감지 (UTC hour) + SENTIMENT_SLOT 오버라이드 → Task 2
- [x] schema v1.2 + slot + intraday_shift 필드 → Task 1, 3
- [x] `post_close` 시 당일 `pre_open` 읽어 intraday_shift 계산 → Task 3
- [x] SniperBoard `fetch_today_slots()` + `enrich_with_delta()` 수정 → Task 4
- [x] SniperBoard API `{latest, today: {pre_open, post_close}}` → Task 5
- [x] 프론트엔드 `data → data.latest` 참조 수정 → Task 6
- [x] README 업데이트 → Task 7
- [x] 하위 호환: 구형 `history/YYYY-MM-DD.json` 폴백 → Task 4 `enrich_with_delta()`

**Placeholder scan:** 없음.

**Type consistency:**
- `intraday_shift`: Python `Optional[str]` = `None` / TS `TrendEnum | null` — 일관됨
- `SnapshotData`: Python `SnapshotData(BaseModel)` / TS `SnapshotData` interface — 일관됨
- `fetch_today_slots(date_str: str) -> dict` — Task 4에서 정의, Task 5에서 import 일치
- `load_pre_open_scores(path: Path) -> dict` — Task 3에서 정의, main()에서 사용 일치
