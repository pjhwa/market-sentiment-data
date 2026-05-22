# Dual-Slot Sentiment Collection Design

**Date:** 2026-05-22  
**Status:** Approved  
**Scope:** market-sentiment-data 수집기 + SniperBoard /api/sentiment

---

## 배경 및 목적

crontab이 하루 2회(6 AM KST, 10 PM KST) 실행되지만 `history/YYYY-MM-DD.json` 단일 파일로 저장해 두 번째 실행이 첫 번째 데이터를 덮어쓰는 문제가 있다. 두 수집 시점은 미국 장 관점에서 명확히 구분된다:

| KST | UTC | 미국 장 상태 |
|-----|-----|------------|
| 10 PM | 13:00 | 개장 전 (pre_open) |
| 06 AM (다음날) | 21:00 | 마감 후 (post_close) |

UTC 기준 같은 날(예: 2026-05-21)에 두 스냅샷이 순서대로 쌓이므로, `post_close` 실행 시 당일 `pre_open` 파일을 읽어 장 내 심리 변화를 계산할 수 있다.

---

## 아키텍처

### 파일 저장 구조

```
history/
├── 2026-05-21_pre_open.json     # 13:00 UTC (10 PM KST)
├── 2026-05-21_post_close.json   # 21:00 UTC (06 AM KST 다음날)
└── ...
latest.json                       # 항상 최신 스냅샷 (덮어씀 유지)
```

`history/YYYY-MM-DD.json` 파일명은 더 이상 생성하지 않는다. 기존 파일은 그대로 보존.

### 슬롯 판별 로직

수집 스크립트는 실행 시각(UTC hour)으로 슬롯을 자동 판별한다:

```
UTC 09:00–17:59 → pre_open
UTC 18:00–08:59 → post_close
```

`SENTIMENT_SLOT=pre_open|post_close` 환경변수로 수동 오버라이드 가능 (테스트·수동 실행용). 환경변수가 있으면 시간 판별보다 우선한다.

### intraday_shift 계산

`post_close` 실행 시:
1. `history/{today_utc_date}_pre_open.json` 파일 존재 여부 확인
2. 있으면 각 symbol과 market의 `sentiment_score` 차이 계산
3. 차이 → `intraday_shift` enum 매핑:
   - `score_diff > 0` → `"heating"`
   - `score_diff < 0` → `"cooling"`
   - `score_diff == 0` → `"stable"`
4. 파일 없으면 `intraday_shift: null`

`pre_open` 실행 시에는 `intraday_shift: null` (비교 기준 없음).

---

## 스키마 v1.2

### 스냅샷 레벨 신규 필드

| 필드 | 타입 | 허용값 | 필수 |
|------|------|--------|------|
| `slot` | string | `"pre_open"`, `"post_close"` | v1.2부터 required |

### MarketSentiment / SymbolSentiment 신규 필드

| 필드 | 타입 | 허용값 | 설명 |
|------|------|--------|------|
| `intraday_shift` | string \| null | `"cooling"`, `"stable"`, `"heating"`, `null` | post_close에서만 값, pre_open은 항상 null |

### 버전 관리

- `schema_version`: `"1.2"` 추가
- 기존 `"1.0"`, `"1.1"` 그대로 유효 (소비측 하위 호환)

### 변경 전/후 예시

**pre_open 스냅샷 (2026-05-21_pre_open.json)**
```json
{
  "generated_at": "2026-05-21T13:00:00Z",
  "schema_version": "1.2",
  "slot": "pre_open",
  "market": {
    "as_of": "2026-05-21T13:00:00Z",
    "sentiment": "neutral",
    "sentiment_score": 0,
    "intraday_shift": null,
    ...
  },
  "symbols": [
    {
      "symbol": "TSLA",
      "sentiment_score": 1,
      "intraday_shift": null,
      ...
    }
  ]
}
```

**post_close 스냅샷 (2026-05-21_post_close.json)**
```json
{
  "generated_at": "2026-05-21T21:00:00Z",
  "schema_version": "1.2",
  "slot": "post_close",
  "market": {
    "as_of": "2026-05-21T21:00:00Z",
    "sentiment": "optimistic",
    "sentiment_score": 1,
    "intraday_shift": "heating",
    ...
  },
  "symbols": [
    {
      "symbol": "TSLA",
      "sentiment_score": 1,
      "intraday_shift": "stable",
      ...
    }
  ]
}
```

---

## SniperBoard API 변경

### GET /api/sentiment 응답 확장

```json
{
  "latest": { ...latest.json 전체... },
  "today": {
    "post_close": { ...history/{today}_post_close.json 또는 null... },
    "pre_open":   { ...history/{today}_pre_open.json 또는 null... }
  }
}
```

- `today`는 UTC 날짜 기준
- 해당 슬롯 파일이 아직 생성되지 않았으면 `null`
- 기존 `latest` 키가 최상위에서 객체 안으로 이동 — **소비 코드 수정 필요**

### SniperBoard 내부 변경

현재 구조:
- `backend/services/sentiment_service.py`: `fetch_latest()` + `enrich_with_delta()` (어제 history 비교)
- `backend/api/endpoints.py`: `GET /sentiment` → `fetch_latest()` + `enrich_with_delta()` 호출

변경:
1. `sentiment_service.py`에 `fetch_today_slots()` 함수 추가 — 당일 UTC 날짜로 `pre_open` / `post_close` 파일 fetch
2. `enrich_with_delta()` 수정 — 어제 `post_close` 파일 우선, 없으면 구 `YYYY-MM-DD.json` 폴백
3. `endpoints.py` `/sentiment` 응답 구조 변경: `{latest, today}` 래핑
4. 프론트엔드에서 `data` → `data.latest` 참조 경로 수정 필요 (breaking change)
5. pre_open / post_close 비교 UI는 이번 범위 밖 (데이터 계약만 확정)

---

## 변경 파일 목록

| 파일 | 변경 종류 |
|------|----------|
| `collect_sentiment.py` | 슬롯 감지, 파일명 변경, intraday_shift 계산 로직 추가 |
| `schema.json` | v1.2 추가, slot/intraday_shift 필드 정의 |
| `README.md` | 파일 구조, 스키마 요약 업데이트 |
| `sniperboard/backend/services/sentiment_service.py` | fetch_today_slots() 추가, enrich_with_delta() 수정 |
| `sniperboard/backend/api/endpoints.py` | /sentiment 응답 구조 변경 |
| sniperboard 프론트엔드 (sentiment 참조 코드) | data → data.latest 경로 수정 |

---

## 에러 처리

- 슬롯 판별 실패(UTC 시간 범위 모호): 환경변수 없으면 경고 후 `post_close`로 폴백
- `pre_open` 파일 없을 때 `post_close` 실행: `intraday_shift: null`로 정상 진행, 경고 로그
- SniperBoard에서 히스토리 파일 fetch 실패: 해당 슬롯 `null` 반환, `latest`는 항상 반환

---

## 하위 호환

- `latest.json`은 항상 최신 스냅샷 (기존 소비측 코드 무중단)
- `history/YYYY-MM-DD.json` 구 형식 파일은 삭제하지 않음 (보존)
- SniperBoard API 응답 구조 변경으로 프론트엔드 수정 필요 (breaking change)
