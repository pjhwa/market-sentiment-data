# Prediction Market Collector — Design Spec

**Date:** 2026-06-29  
**Status:** Approved  
**Scope:** Kalshi FOMC 금리 결정 확률 수집 (Collector 6)

---

## 1. 목적

Kalshi 예측시장에서 다음 FOMC 회의의 금리 결정 확률(동결/인하/인상)을 수집하여 `prediction/latest.json`에 저장하고 GitHub에 push한다. SniperBoard가 이 데이터를 raw GitHub URL로 소비한다.

---

## 2. 아키텍처

```
Kalshi REST API
  → collect_prediction.py
  → prediction/latest.json
  → prediction/history/<date>_<slot>.json
  → git commit + push
  → SniperBoard /api/prediction (future)
```

기존 5개 컬렉터와 동일한 Layer 1 → Layer 2 → Layer 3 패턴을 따른다.

---

## 3. 파일 구조

```
collect/
  collect_prediction.py     # 신규 (Collector 6)

prediction/
  latest.json               # 항상 최신 스냅샷
  history/
    YYYY-MM-DD_pre_open.json
    YYYY-MM-DD_post_close.json
  prediction.log
```

---

## 4. Output JSON 스키마 (schema_version: "1.0")

```json
{
  "generated_at": "2026-06-29T06:30:00Z",
  "schema_version": "1.0",
  "slot": "pre_open",
  "source": "kalshi",
  "next_fomc": {
    "event_ticker": "FOMC-26JUL29",
    "meeting_date": "2026-07-29",
    "as_of": "2026-06-29T06:30:00Z",
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

- `probabilities` 값: Kalshi `yes_ask_price` (0.00~1.00) 그대로
- `next_fomc`: FOMC 이벤트 없으면 `null`
- 존재하는 마켓만 포함 (없는 항목 생략)

---

## 5. Kalshi API

**Base URL:** `https://trading-api.kalshi.com/trade-api/v2`  
**인증:** `Authorization: Bearer <KALSHI_API_KEY>`  
**환경변수:** `KALSHI_API_KEY` (필수)

**호출 순서:**
1. `GET /events?series_ticker=FOMC&status=open` → 가장 가까운 미래 이벤트 선택
2. `GET /events/{event_ticker}` → 마켓 목록 + yes_ask_price 수집

**ticker → outcome 매핑:**
- `*UNCHANGED*` → `no_change`
- `*DOWN25*` 또는 `*CUT25*` → `cut_25bps`
- `*DOWN50*` 또는 `*CUT50*` → `cut_50bps`
- `*UP25*` 또는 `*HIKE25*` → `hike_25bps`

---

## 6. 에러 처리

| 상황 | 동작 |
|---|---|
| `KALSHI_API_KEY` 없음 | 즉시 exit 1 |
| HTTP 401/429/5xx | 에러 로그 후 exit 1 |
| FOMC 이벤트 없음 | `next_fomc: null` 저장, 정상 종료 |
| 마켓 파싱 실패 | 해당 항목 건너뜀, 나머지 저장 |

---

## 7. Cron 스케줄 (KST)

```
45 5,21 * * *   collect_prediction.py
```

기존 컬렉터(00~30분) 이후 실행하여 git push 충돌 회피.

---

## 8. 구현 범위

- `collect/collect_prediction.py`
- `prediction/` 디렉토리 초기화
- cron 등록
- `PROJECT_CONTEXT.md` / `README.md` 업데이트

SniperBoard 연동 (`prediction_service.py`, `/api/prediction`)은 별도 작업.
