# AI Brief & Earnings Intelligence — Design Spec

**Date:** 2026-05-24  
**Repos:** market-sentiment-data, sniperboard  
**Status:** Approved

---

## 1. 목표

소셜 심리 데이터(market-sentiment-data → GitHub → Sniperboard) 패턴을 그대로 확장하여 두 가지 AI 생성 데이터를 추가한다.

- **Feature A — AI Daily Brief**: yfinance 기술적 지표 + 소셜 심리를 Grok이 종합한 종목별·시장별 내러티브
- **Feature B — Earnings Intelligence**: yfinance 어닝 캘린더 + 히스토리를 Grok이 해석한 실적 리스크 요약

---

## 2. 아키텍처

```
[Mac Mini Cron]
  ├── collect/collect_brief.py       pre_open(13:00 UTC) + post_close(21:00 UTC)
  │     ├── yfinance → 워치리스트 기술적 지표
  │     ├── latest.json 읽기 (소셜 심리 참조)
  │     └── Grok → 내러티브 → brief/latest.json 저장 + GitHub push
  │
  └── collect/collect_earnings.py   pre_open only (13:00 UTC, 일 1회)
        ├── yfinance → .calendar + .earnings_history (워치리스트 6종목)
        └── Grok → 어닝 해석 → earnings/latest.json 저장 + GitHub push

[GitHub: market-sentiment-data]
  ├── latest.json                    (기존)
  ├── brief/
  │   ├── latest.json                NEW
  │   └── history/YYYY-MM-DD_<slot>.json
  └── earnings/
      ├── latest.json                NEW
      └── history/YYYY-MM-DD.json

[Sniperboard Backend — FastAPI]
  ├── GET /api/sentiment             (기존)
  ├── GET /api/brief                 NEW — GitHub raw URL 프록시
  └── GET /api/earnings              NEW — GitHub raw URL 프록시

[Sniperboard Frontend — Next.js]
  ├── hooks/useBrief.ts              NEW
  ├── hooks/useEarnings.ts           NEW
  ├── components/boards/OverviewBoard.tsx   MODIFIED
  └── components/boards/DailyBoard.tsx     MODIFIED
```

---

## 3. 데이터 스키마

### 3-1. `brief/latest.json`

```jsonc
{
  "generated_at": "2026-05-24T13:00:00Z",   // ISO8601 UTC
  "schema_version": "1.0",
  "slot": "pre_open",                         // "pre_open" | "post_close"
  "market_brief": {
    "summary": "SPY EMA200 위 유지, DD 4개로 경고권. VIX 안정.",
    "tone": "cautious",                        // "bullish" | "cautious" | "bearish" | "neutral"
    "key_themes": ["Fed 금리 동결 기대", "AI 관련주 모멘텀 지속"],
    "watch_points": "QQQ Distribution Day 증가 추이 주시"
  },
  "symbol_briefs": [
    {
      "symbol": "NVDA",
      "setup_quality": "A+",                  // "A+" | "A" | "B" | "C" | "D"
      "brief": "VCP 패턴 형성 중. 소셜 심리 optimistic, bot 없음. EMA21 위 유지.",
      "key_risk": "어닝 4일 전, 포지션 크기 조절 필요",
      "key_opportunity": "거래량 수반 돌파 시 목표가 +18%",
      "action_bias": "watch"                  // "buy" | "hold" | "watch" | "avoid"
    }
    // ... 워치리스트 6종목 전부
  ]
}
```

**수집 규칙:**
- `summary`, `tone`, `key_themes`, `watch_points`: 항상 필수
- `symbol_briefs`: 워치리스트 6종목 전부 (TSLA, AAPL, NVDA, META, AMZN, GOOGL)
- `setup_quality`: Grok이 기술적 지표 + 소셜 심리 + 어닝 근접도를 종합 판단
- `action_bias`: Grok의 단기 액션 방향성. 매매 신호가 아닌 참고 지표임을 UI에 명시

### 3-2. `earnings/latest.json`

```jsonc
{
  "generated_at": "2026-05-24T13:00:00Z",
  "schema_version": "1.0",
  "upcoming_earnings": [
    {
      "symbol": "NVDA",
      "earnings_date": "2026-05-28",          // YYYY-MM-DD
      "days_until": 4,
      "eps_estimate": 0.89,                   // null if unavailable
      "revenue_estimate_b": 43.1,             // 십억 달러 단위, null if unavailable
      "historical_beat_rate": 0.92,           // 최근 8분기 beat 비율 (0~1), null if < 4분기 데이터
      "ai_summary": "8분기 연속 beat. 데이터센터 수요 key. 어닝 전 변동성 주의",
      "risk_level": "high",                   // "high" | "med" | "low"
      "action_note": "어닝 전 신규 진입 자제"
    }
  ],
  "recent_results": [
    {
      "symbol": "AAPL",
      "report_date": "2026-05-02",
      "eps_actual": 1.65,
      "eps_estimate": 1.62,
      "surprise_pct": 1.85,                   // (actual - estimate) / |estimate| × 100
      "ai_reaction": "소폭 beat. 가이던스 보수적, 단기 모멘텀 약화"
    }
  ]
}
```

**수집 규칙:**
- `upcoming_earnings`: yfinance `.calendar`에서 60일 이내 어닝이 있는 종목만 포함
- `recent_results`: yfinance `.earnings_history`에서 최근 1분기 결과가 있는 종목
- 어닝 데이터가 없는 종목은 해당 섹션에서 제외 (빈 배열 허용)
- `days_until`: 음수면 recent_results로 이동

---

## 4. 수집 스크립트 설계

### 4-1. `collect/collect_brief.py`

```
1. yfinance로 워치리스트 6종목 daily/intraday 지표 수집
   - 종목별: Stage2 score, active signals, RSI, EMA 위치, ATR, volume ratio
   - 시장: SPY regime score, DD count, VIX level

2. latest.json 읽기 (소셜 심리 데이터 참조)
   - 종목별 sentiment, composite_score, key_reason

3. Grok 프롬프트 구성 (기존 collect_sentiment.py 패턴 준수)
   - system: 트레이더 관점의 데이터 분석가 역할
   - user: 수집된 기술적 지표 + 심리 데이터 → brief JSON 생성 요청
   - JSON mode 응답 요구

4. 응답 파싱 및 검증
   - 필수 필드 존재 확인
   - setup_quality enum 검증
   - action_bias enum 검증

5. 저장
   - brief/latest.json 덮어쓰기
   - brief/history/YYYY-MM-DD_<slot>.json 저장
   - git add / commit / push
```

### 4-2. `collect/collect_earnings.py`

```
1. yfinance로 워치리스트 6종목 어닝 데이터 수집
   - .calendar → earnings_date, eps_estimate, revenue_estimate
   - .earnings_history → 최근 8분기 actual vs estimate

2. days_until 계산, 60일 이내만 upcoming_earnings에 포함

3. historical_beat_rate 계산 (실제 > 예상 비율)

4. Grok 프롬프트 구성
   - 수집된 어닝 데이터 → ai_summary, risk_level, action_note 생성 요청

5. 저장
   - earnings/latest.json 덮어쓰기
   - earnings/history/YYYY-MM-DD.json 저장
   - git add / commit / push
```

### 4-3. 공통 패턴 (`collect/collect_sentiment.py` 계승)
- 환경변수: `GROK_API_KEY`, `GITHUB_TOKEN` (기존과 동일)
- 에러 처리: Grok 호출 실패 시 이전 latest.json 유지, stderr 로깅
- 재시도: 최대 2회

---

## 5. Sniperboard 백엔드 변경

### `backend/api/endpoints.py` 추가

```python
BRIEF_URL = "https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/brief/latest.json"
EARNINGS_URL = "https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/earnings/latest.json"

@router.get("/brief")
async def get_brief():
    # sentiment 엔드포인트와 동일한 패턴: requests.get + 캐싱
    ...

@router.get("/earnings")
async def get_earnings():
    ...
```

- 캐시 TTL: brief는 30분, earnings는 60분 (어닝은 하루 1회 갱신)
- 에러 시 503 반환 (데이터 없음 명시)

### `backend/api/schemas.py` 추가

- `BriefResponse`, `MarketBrief`, `SymbolBrief`
- `EarningsResponse`, `UpcomingEarning`, `RecentResult`

---

## 6. Sniperboard 프론트엔드 변경

### 6-1. 새 훅

**`hooks/useBrief.ts`**
- `GET /api/brief`, staleTime 30분, 에러 시 null 반환

**`hooks/useEarnings.ts`**
- `GET /api/earnings`, staleTime 60분, 에러 시 null 반환

### 6-2. `OverviewBoard.tsx` 변경

**AI Market Brief 카드** (기존 AI Insight placeholder 대체):
```
┌─────────────────────────────────────┐
│ AI Market Brief      [cautious 칩]  │
│ "SPY EMA200 위 유지, DD 4개로..."   │
│ 테마: Fed 금리 동결 기대  AI 모멘텀 │
│ 주시: QQQ Distribution Day 증가     │
└─────────────────────────────────────┘
```

**Earnings Calendar 카드** (새 섹션):
```
┌─────────────────────────────────────┐
│ Earnings Calendar                   │
│ NVDA  5/28  4일 후  ● HIGH          │
│ AAPL  6/05  12일 후 ● LOW           │
│ (어닝 없는 종목 표시 안 함)         │
└─────────────────────────────────────┘
```

### 6-3. `DailyBoard.tsx` 변경

선택 종목에 60일 이내 어닝이 있을 때:
- 차트 상단에 `EARNINGS IN 4D` 배너 (amber 색상)
- 카드 하단에 `action_note` 한 줄

### 6-4. `SentimentBoard.tsx` 변경

기존 종목별 심리 카드에 `setup_quality` 배지 추가:
- `A+` → green, `A` → teal, `B` → yellow, `C/D` → red

---

## 7. 파일 변경 목록

### market-sentiment-data

| 파일 | 변경 |
|------|------|
| `collect/collect_brief.py` | NEW |
| `collect/collect_earnings.py` | NEW |
| `brief/latest.json` | NEW (초기 빈 파일) |
| `earnings/latest.json` | NEW (초기 빈 파일) |
| `README.md` | 새 파일 섹션 추가 |

### sniperboard

| 파일 | 변경 |
|------|------|
| `backend/api/endpoints.py` | `/brief`, `/earnings` 엔드포인트 추가 |
| `backend/api/schemas.py` | Brief/Earnings Pydantic 모델 추가 |
| `frontend/hooks/useBrief.ts` | NEW |
| `frontend/hooks/useEarnings.ts` | NEW |
| `frontend/app/types.ts` | Brief/Earnings 타입 추가 |
| `frontend/components/boards/OverviewBoard.tsx` | AI Brief + Earnings Calendar 카드 |
| `frontend/components/boards/DailyBoard.tsx` | 어닝 배너 + action_note |
| `frontend/components/boards/SentimentBoard.tsx` | setup_quality 배지 |

---

## 8. 제약 및 주의사항

| 항목 | 내용 |
|------|------|
| yfinance 어닝 데이터 신뢰도 | `.calendar`는 날짜가 없거나 부정확할 수 있음. null 처리 필수 |
| Grok API 비용 | brief는 하루 2회, earnings는 하루 1회 — 기존 sentiment와 동일 수준 |
| action_bias 면책 | UI에 "참고용 AI 의견, 매매 신호 아님" 문구 명시 |
| 히스토리 저장 | brief history는 pre_open/post_close 구분, earnings history는 날짜만 |
| 환경변수 | 기존 GROK_API_KEY, GITHUB_TOKEN 재사용. 추가 설정 불필요 |
