> English docs: [PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md)

# market-sentiment-data — 프로젝트 컨텍스트

<!-- AUTO-GENERATED: 2026-05-31 -->

Claude Code와 개발자를 위한 아키텍처 및 코드 레퍼런스. 수집기, 스키마, 데이터 구조를 수정하기 전에 반드시 읽으세요.

---

## 1. 아키텍처 개요

소셜 심리 데이터는 **3개 계층**으로 분리되어 있습니다. 이것이 핵심 설계 원칙입니다 — 수집 액터(Hermes/서버)와 소비 액터(SniperBoard)는 GitHub 저장소를 공유 스토리지로 사용해 느슨하게 결합됩니다.

```
┌─────────────────────────┐     ┌──────────────────────────┐     ┌──────────────────────┐
│  계층 1: 수집             │     │  계층 2: 저장             │     │  계층 3: 소비         │
│  (서버 크론)               │     │  (이 GitHub 레포)         │     │  (SniperBoard 등)    │
│                          │     │                           │     │                      │
│  4개 수집기:              │ git │  latest.json              │ raw │  FastAPI 서비스        │
│  · collect_sentiment.py  │push │  history/                 │fetch│  /api/sentiment      │
│  · collect_brief.py      │────▶│  brief/                   │────▶│  /api/brief          │
│  · collect_earnings.py   │     │  earnings/                │     │  /api/earnings       │
│  · collect_macro_insight │     │  macro/                   │     │  /api/macro-insight  │
│                          │     │  schema.json              │     │                      │
└─────────────────────────┘     └──────────────────────────┘     └──────────────────────┘
```

**이 방식을 택한 이유:**
- **수집과 소비의 분리** — SniperBoard 장애 = 수집 중단 아님. 각 계층을 독립적으로 수정·재시작 가능.
- **재사용성** — GitHub의 표준 JSON. 미래의 어떤 대시보드나 노트북도 `raw.githubusercontent.com`에서 바로 읽을 수 있음.
- **이력 보존** — 일별 스냅샷이 누적되어 SniperBoard가 직접 계산 없이 추세 변화를 데이터에서 읽음.
- **비용/속도 분리** — 느린 LLM 호출은 크론이 미리 처리. SniperBoard는 저장된 JSON을 즉시 반환.

---

## 2. 저장소 파일 맵

```
market-sentiment-data/
├── collect_sentiment.py          # 수집기 1 — 진입점: python collect_sentiment.py
├── collect/
│   ├── __init__.py
│   ├── collect_brief.py          # 수집기 2 — python -m collect.collect_brief
│   ├── collect_earnings.py       # 수집기 3 — python -m collect.collect_earnings
│   ├── collect_macro_insight.py  # 수집기 4 — python -m collect.collect_macro_insight
│   ├── price_context.py          # 중립적 가격 맥락 fetcher (수집기 1 전용)
│   ├── git_utils.py              # commit_and_push() 공용 헬퍼
│   ├── test_collect_sentiment.py
│   ├── test_collect_brief.py
│   ├── test_collect_brief_context.py
│   └── test_price_context.py
├── latest.json                   # 심리: 항상 최신 스냅샷
├── history/YYYY-MM-DD_<slot>.json
├── brief/latest.json             # AI 일일 브리프: 항상 최신
├── brief/history/YYYY-MM-DD_<slot>.json
├── earnings/latest.json          # 어닝 인텔리전스: 항상 최신
├── earnings/history/YYYY-MM-DD.json
├── macro/latest.json             # 매크로 인사이트: 항상 최신
├── macro/history/YYYY-MM-DD_<slot>.json
├── schema.json                   # JSON Schema draft-07 v2.0 (심리 전용)
├── README.md / README.ko.md
└── PROJECT_CONTEXT.md / PROJECT_CONTEXT.ko.md
```

---

## 3. 환경변수

모든 설정은 환경변수로 주입합니다. 경로나 토큰을 절대 하드코딩하지 마세요.

| 변수 | 기본값 | 사용처 |
|------|--------|--------|
| `SENTIMENT_REPO_PATH` | 스크립트 디렉토리 | 모든 수집기 |
| `HERMES_CMD` | `/Users/jerry/.local/bin/hermes` | 모든 수집기 |
| `HERMES_PROVIDER` | `""` (빈값 = `--provider` 플래그 없음) | 모든 수집기 |
| `HERMES_TIMEOUT` | `120` | 모든 수집기 |
| `HERMES_RETRY` | `1` | 모든 수집기 |
| `SNIPERBOARD_API_BASE` | `http://localhost:5001` | 수집기 1, 2, 4 |
| `SENTIMENT_SLOT` | UTC 시간으로 자동 감지 | 수집기 1, 2, 4 |

**슬롯 감지 로직** (`SENTIMENT_SLOT`으로 오버라이드 가능):
- UTC 09:00~17:59 → `pre_open`
- UTC 18:00~08:59 → `post_close`

---

## 4. 수집기 1 — 소셜 심리 (`collect_sentiment.py`)

### 개요

메인 심리 수집기. 하루 2회 실행. 7개 워치리스트 종목 + 미국 시장 전체에 대해:
1. SniperBoard에서 중립적 가격 맥락 수집 (방향 없음)
2. 맥락을 관찰 단서로만 Grok 프롬프트에 주입
3. `hermes -z`로 Grok 호출; JSON 응답 파싱·검증
4. divergence 계산 (Grok 완료 후)
5. composite_score 계산
6. `latest.json` + `history/YYYY-MM-DD_<slot>.json` 저장
7. `git commit + push`

**워치리스트:** `TSLA, AAPL, NVDA, META, AMZN, GOOGL, PLTR`

### 오염 방지선 (가장 중요한 원칙)

> **절대 규칙: 가격 정보는 "Grok이 어디를 볼지" 안내하는 데만 쓰고, "무엇을 느낄지"는 절대 알려주지 않는다.**

**Grok 프롬프트에 넣어도 되는 것 (중립적 관찰 단서):**
- 가격 변동의 크기: "오늘 비정상적으로 큰 가격 변동이 있었다" (방향 없음)
- 거래량: "오늘 거래량이 평소의 N배"
- 위치: "최근 52주 고점 부근이다" (위치만, 돌파/이탈 판정 없음)

**Grok 프롬프트에 절대 넣으면 안 되는 것:**
- ❌ "올랐다 / 떨어졌다 / 급등 / 급락" (방향)
- ❌ "강세 신호 / Stage 2 점수 높음 / RISK_ON" (결론)
- ❌ RSI 수치, EMA 정배열 여부 (방향성을 함의하는 지표)

**이유:** 방향을 알려주면 Grok이 X 게시물을 안 읽고도 답을 추론할 수 있습니다. 그러면 심리가 가격의 그림자가 되어, 독립적 보조 지표로서의 가치가 사라집니다.

**기계적 시행:** `build_prompt()`는 생성된 프롬프트 문자열에 방향 단어가 있으면 `AssertionError`를 발생시킵니다. `price_context.py`도 반환 dict마다 `_assert_no_direction()`을 실행합니다.

### `collect/price_context.py`

3개 함수:

| 함수 | 목적 |
|------|------|
| `fetch_price_context(symbol)` | SniperBoard `/api/daily`에서 volatility / volume_ratio / near_key_level / abnormal_move 반환. **방향 없음.** 실패 시: `available: False` |
| `fetch_market_context()` | `/api/macro`에서 VIX 수준(low/normal/high)만 반환 |
| `fetch_close_direction(symbol)` | `up`/`down`/`flat` 반환. **후처리 전용.** 절대 프롬프트 빌더로 흘리지 말 것 |

### Divergence 계산 (수집 후처리)

Grok 응답 완료 후에 계산합니다. 여기서만 `fetch_close_direction()` 결과를 사용해도 됩니다.

```
price_dir == "up"   and sentiment_score < 0  →  "bearish_divergence"
price_dir == "down" and sentiment_score > 0  →  "bullish_divergence"
그 외                                         →  "aligned" 또는 "none"
```

### composite_score 계산

모든 신호를 −2.0 ~ +2.0 범위로 가중 결합:

```python
conf_mult  = {"high": 1.0, "med": 0.85, "low": 0.5}[confidence]
bot_mult   = {"yes": 0.6, "unclear": 0.85, "no": 1.0}[bot_suspected]
vol_mult   = {"low": 0.7, "normal": 1.0, "elevated": 1.2, "surging": 1.3}[mention_volume]
div_adj    = {"bullish_divergence": -0.5, "bearish_divergence": 0.5, ...}[divergence]
trend_adj  = {"cooling": -0.3, "stable": 0.0, "heating": 0.3}[trend_vs_yesterday]
shift_adj  = {"cooling": -0.2, "stable": 0.0, "heating": 0.2}[intraday_shift]

score = sentiment_score * conf_mult * bot_mult * vol_mult + div_adj + trend_adj + shift_adj
composite_score = clamp(round(score, 1), -2.0, 2.0)
```

### `collect_sentiment.py` 주요 함수

| 함수 | 역할 |
|------|------|
| `detect_slot(now)` | `pre_open` 또는 `post_close` 반환 |
| `build_prompt(symbol, company, ctx)` | 중립 맥락 주입 프롬프트 빌드; 방향 단어 assert |
| `call_hermes(prompt)` | 타임아웃+재시도 포함 subprocess 호출 |
| `extract_json(text)` | LLM 출력에서 첫 `{`~마지막 `}` 추출 |
| `validate_symbol_fields(data, symbol)` | 열거형 및 필수 필드 검증 |
| `validate_top_news(data)` | `top_news` 선택 구조 검증 (v2.0 _en/_ko 필수) |
| `compute_divergence(price_dir, score)` | divergence 로직 (후처리 전용) |
| `compute_intraday_shift(prev, curr)` | pre_open vs post_close 점수 비교 |
| `load_pre_open_scores(path)` | intraday_shift용 pre_open 파일 읽기 |
| `compute_symbol_composite(...)` | 종목 composite_score |
| `compute_market_composite(...)` | 시장 전체 composite_score |
| `build_symbol_entry(...)` | 최종 per-symbol JSON 조립 |
| `build_market_entry(...)` | 최종 market JSON 조립 |
| `git_commit_push(...)` | `collect/git_utils.commit_and_push()` 위임 |

---

## 5. 수집기 2 — AI 일일 브리프 (`collect/collect_brief.py`)

### 개요

심리 수집기 완료 후 실행. 기술적 데이터 + 소셜 심리 → Grok → 구조화된 브리프.

**데이터 소스:**
- `GET /api/regime` → Risk Regime 라벨 + 총점 + 컴포넌트
- `GET /api/distribution-days` → SPY/QQQ distribution day 수
- `GET /api/watchlist` → 워치리스트 수준 데이터
- `GET /api/daily?symbol=` (종목별) → Stage2 점수, RS 점수, market_structure, 신호
- `latest.json` → 종목별 composite_score, sentiment, key_reason

**Grok 출력 스키마:**
```json
{
  "market_brief": {
    "summary_en": "...", "summary_ko": "...",
    "tone": "bullish|cautious|bearish|neutral",
    "key_themes_en": [...], "key_themes_ko": [...],
    "watch_points_en": "...", "watch_points_ko": "..."
  },
  "symbol_briefs": [{
    "symbol": "TSLA",
    "setup_quality": "A+|A|B|C|D",
    "brief_en": "...", "brief_ko": "...",
    "key_risk_en": "...", "key_risk_ko": "...",
    "key_opportunity_en": "...", "key_opportunity_ko": "...",
    "action_bias": "buy|hold|watch|avoid"
  }]
}
```

**setup_quality 기준:**
- `A+`: Stage2 6~7점, 소셜 optimistic 이상, GC above/breakout, RS 70+
- `A`: Stage2 5~6점, 소셜 neutral 이상, UPTREND 구조
- `B`: Stage2 4~5점, 혼재 신호
- `C`: Stage2 3점 이하, 소셜 공포 또는 bear_flag
- `D`: Stage2 2점 이하 또는 하락 심화

**맥락 스냅샷 (Phase 1):** `build_brief_context_snapshot()`이 생성 시점의 기술적/레짐/심리 상태를 캡처. 출력 JSON의 `context` 필드에 embed. SniperBoard Brief 패널에서 투명성 제공 목적으로 표시.

---

## 6. 수집기 3 — 어닝 인텔리전스 (`collect/collect_earnings.py`)

### 개요

yfinance로 어닝 데이터를 수집하고 Grok으로 리스크 해석을 생성합니다.

**데이터 흐름:**
1. `yf.Ticker(sym).calendar` → 예정 어닝 날짜 + EPS 컨센서스 (1차 소스)
2. 폴백: calendar 실패 시 `earnings_dates`/`earnings_estimate`
3. `yf.Ticker(sym).earnings_history` → 최근 분기별 EPS 실적 (최대 8분기)
4. 필터링: 30일 이내 종목만 (그 이후는 EPS 컨센서스 미형성)
5. 단계 분류: imminent (7일 이내) / approaching (8~21일) / watching (22~30일)
6. 단계별 데이터로 Grok 호출 → 종목별 리스크 해석
7. `earnings/latest.json` + 이력 파일 저장

**강화 기능:**
- calendar → `earnings_dates`/`earnings_estimate` 폴백
- 수치·날짜 검증 (0~30일 범위, EPS 타당성 검사)
- 구조화된 per-symbol 및 raw shape 로깅
- `partial` 플래그 + 단일 종목 실패 시에도 부분 결과 (크래시 없음, `sys.exit` 없음)
- `--dry-run` 플래그: 수집은 하되 git push 건너뜀
- jsonschema + 경량 인라인 스키마 검증 후 저장

---

## 7. 수집기 4 — 매크로 인사이트 (`collect/collect_macro_insight.py`)

### 개요

SniperBoard의 매크로 데이터를 수집하고 그룹별 AI 해석을 생성합니다.

**데이터 소스:** `GET /api/macro` → 21개 매크로 자산:
- 변동성: VIX
- 폭(Breadth): SPY, QQQ, IWM, SMH
- 신용: HYG, LQD
- 금리: TLT, IEF, ^TNX
- 원자재: GLD, SLV, USO, DBA
- 섹터: XLF, XLE, XLK, XLV, XLU, XLB

**Grok 출력 스키마:**
```json
{
  "overall": {
    "summary": "시장 전체 한 문장 (한국어, 40자 이내)",
    "bullets": ["신호 → 의미", "신호 → 의미", "신호 → 의미"]
  },
  "groups": {
    "volatility":  { "text": "..." },
    "breadth":     { "text": "..." },
    "credit":      { "text": "..." },
    "rates":       { "text": "..." },
    "commodities": { "text": "..." },
    "sectors":     { "text": "..." }
  }
}
```

불릿 형식 규칙: "핵심 신호 → 시장 의미" 형식, 각 25자 이내. 단순 수치·상태 나열 금지.

---

## 8. 데이터 스키마 레퍼런스 (v2.0)

### `latest.json` 최상위 구조

```json
{
  "generated_at": "2026-05-31T13:00:00Z",
  "schema_version": "2.0",
  "slot": "pre_open",
  "market": { ... },
  "symbols": [ ... ]
}
```

### per-symbol 객체 (v2.0 전체)

```json
{
  "symbol": "TSLA",
  "as_of": "2026-05-31T13:00:00Z",
  "sentiment": "optimistic",
  "sentiment_score": 1,
  "trend_vs_yesterday": "heating",
  "mention_volume": "elevated",
  "key_reason_en": "Strong FSD progress boosted investor optimism",
  "key_reason_ko": "FSD 진전으로 투자자 낙관 심리 강화",
  "bot_suspected": "no",
  "confidence": "high",
  "source": "grok-oauth via hermes",
  "top_news": {
    "headline_en": "Tesla FSD v13 reaches 99% disengagement-free miles",
    "headline_ko": "테슬라 FSD v13, 자율주행 99% 달성",
    "summary_en": "Tesla's latest FSD update achieves near-full autonomy in most conditions.",
    "summary_ko": "테슬라 최신 FSD 업데이트가 대부분 조건에서 완전자율주행에 근접.",
    "source": "Bloomberg"
  },
  "price_context": {
    "volatility": "normal",
    "volume_ratio": 1.4,
    "near_key_level": "none",
    "abnormal_move": false
  },
  "divergence": "aligned",
  "intraday_shift": "heating",
  "composite_score": 1.2
}
```

### market 객체 추가 필드

- `extreme_flag`: `none` | `extreme_fear` | `extreme_greed`

### 스키마 버전 이력

| 버전 | 핵심 추가 |
|------|----------|
| 1.0 | 기본 스키마 |
| 1.1 | `price_context`, `divergence` |
| 1.2 | `slot`, `intraday_shift` |
| 1.3 | `composite_score` |
| 1.4 | `top_news` |
| 2.0 | 모든 AI 텍스트 필드에 `_en`/`_ko` 접미사 쌍 |

---

## 9. 계층 3 — SniperBoard 소비

SniperBoard는 백엔드 서비스를 통해 이 저장소를 소비합니다. 소비측은 v1.x 이후 추가된 모든 필드를 optional로 처리해야 이력 파일과의 하위 호환성이 유지됩니다.

**SniperBoard 엔드포인트 → 소스 파일 매핑:**

| SniperBoard 엔드포인트 | 소스 파일 | 캐시 TTL |
|----------------------|----------|----------|
| `GET /api/sentiment` | `latest.json` | 5~10분 |
| `GET /api/sentiment/history` | `history/*.json` | 5분 |
| `GET /api/brief` | `brief/latest.json` | 5~10분 |
| `GET /api/earnings` | `earnings/latest.json` | 60분 |
| `GET /api/macro-insight` | `macro/latest.json` | 5~10분 |

**fetch 패턴 (비공개 레포):**
```python
import os, requests
def fetch_raw(path: str) -> dict:
    token = os.environ.get("SENTIMENT_DATA_TOKEN")
    headers = {"Authorization": f"token {token}"} if token else {}
    resp = requests.get(
        f"https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/{path}",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
```

**하위 호환 필드 접근:**
```python
def get_field(obj: dict, field: str, locale: str) -> str:
    en_val = obj.get(f"{field}_en")
    ko_val = obj.get(f"{field}_ko")
    fallback = obj.get(field, "")
    return (ko_val or fallback) if locale == "ko" else (en_val or fallback)
```

---

## 10. 크론 스케줄

```bash
# ─── pre_open (UTC 13:00 / KST 22:00) ──────────────────────────────────────
0 13 * * 1-5  cd ~/dev/market-sentiment-data && python collect_sentiment.py >> ~/sentiment.log 2>&1
5 13 * * 1-5  cd ~/dev/market-sentiment-data && python -m collect.collect_brief >> ~/brief.log 2>&1
10 13 * * 1-5 cd ~/dev/market-sentiment-data && python -m collect.collect_macro_insight >> ~/macro.log 2>&1

# ─── post_close (UTC 21:00 / KST 익일 06:00) ────────────────────────────────
0 21 * * 1-5  cd ~/dev/market-sentiment-data && python collect_sentiment.py >> ~/sentiment.log 2>&1
5 21 * * 1-5  cd ~/dev/market-sentiment-data && python -m collect.collect_brief >> ~/brief.log 2>&1
10 21 * * 1-5 cd ~/dev/market-sentiment-data && python -m collect.collect_macro_insight >> ~/macro.log 2>&1

# ─── earnings (하루 1회, UTC 14:00) ─────────────────────────────────────────
0 14 * * 1-5  cd ~/dev/market-sentiment-data && python -m collect.collect_earnings >> ~/earnings.log 2>&1
```

> **PATH 주의:** 크론 환경은 PATH가 최소화되어 있습니다. `python`과 `hermes`의 절대 경로를 사용하거나, 크론 라인 상단에 PATH를 명시하세요.

---

## 11. 안전 가드레일 (비협상 원칙)

| 원칙 | 코드 구현 |
|------|----------|
| **오염 방지선** | `build_prompt()`에서 방향 단어 assert. `price_context.py`에서 반환 dict마다 `_assert_no_direction()`. `fetch_close_direction()` 결과는 절대 프롬프트 빌더로 흘리지 않음. |
| **범주형만** | `sentiment_score`는 항상 `SENTIMENT_SCORE_MAP[sentiment]`. Grok 프롬프트에서 백분율 명시적 금지. |
| **실패 시 묵묵히, 가짜값 금지** | 종목 실패: `continue` (skip) + stderr 로그. 시장 실패: 중립 placeholder. 네트워크 실패: `available: False`. |
| **신뢰도 낮음 → 다운그레이드** | `confidence: low` → `conf_mult = 0.5`. 소비측에서 시각적으로 흐리게 표시. |
| **계층 독립성** | 모든 계층간 호출에 명시적 `timeout` + `try/except`. SniperBoard API 장애 시 blind 모드로 수집 계속. |
| **시크릿은 환경변수로** | `SENTIMENT_DATA_TOKEN`, `HERMES_CMD`, `SNIPERBOARD_API_BASE` 모두 환경에서만. 하드코딩 없음. |
| **보조 지표 프레이밍** | 심리 데이터는 보조 지표. SniperBoard가 disclaimer를 표시. 가격 기반 손절/목표가 결정을 대체하지 않음. |

---

## 12. 테스트

```bash
python -m pytest collect/ -v          # 48개 테스트 (Phase 5)

# 주요 테스트 파일:
# collect/test_collect_sentiment.py   — 프롬프트 가드, divergence, composite_score, 검증
# collect/test_price_context.py       — 방향 단어 부재 assert, 폴백 동작
# collect/test_collect_brief.py       — 브리프 검증, 맥락 스냅샷
# collect/test_collect_brief_context.py — 맥락 어트리뷰션 구조
```

테스트는 `collect/`에 co-located. pytest로 실행. 외부 서비스 불필요 — SniperBoard API 응답은 mock으로 처리.

---

## 13. 크로스 레포 연결 (SniperBoard)

- `sniperboard/backend/services/sentiment_service.py` — `latest.json` + history 수집
- `sniperboard/backend/services/brief_service.py` — `brief/latest.json` 수집
- `sniperboard/backend/services/earnings_service.py` — `earnings/latest.json` 수집, 60분 캐시; `/api/earnings` 응답에 `meta.age_minutes` 첨부
- `sniperboard/backend/services/macro_insight_service.py` — `macro/latest.json` 수집
- `sniperboard/frontend/components/boards/SentimentBoard.tsx` — `/api/sentiment` 소비
- `sniperboard/frontend/components/boards/SentimentTrendChart.tsx` — 이력 차트
- SniperBoard `MACRO_SYMBOLS`는 이 레포의 매크로 자산 목록과 영어 이름으로 일치
