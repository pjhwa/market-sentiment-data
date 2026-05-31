> English docs: [README.md](./README.md)

# market-sentiment-data

**계층 2 — SniperBoard AI 시장 인텔리전스 파이프라인의 공유 데이터 저장소.**

Mac mini 크론 잡이 매일 4개의 수집기를 실행하여 Hermes를 통해 Grok에 쿼리하고 SniperBoard 백엔드에서 데이터를 수집합니다. 결과는 표준 JSON 형식으로 이 저장소에 커밋됩니다. SniperBoard를 포함한 모든 소비 프로그램은 raw GitHub URL만 있으면 됩니다.

---

## 저장소 구조

```
market-sentiment-data/
├── README.md                        # 영어 문서
├── README.ko.md                     # 이 문서 (한국어)
├── PROJECT_CONTEXT.md               # 아키텍처 & 코드 레퍼런스 (영어)
├── PROJECT_CONTEXT.ko.md            # 아키텍처 & 코드 레퍼런스 (한국어)
├── schema.json                      # 데이터 계약 (JSON Schema draft-07, v2.0)
│
├── collect_sentiment.py             # 수집기 1: 소셜 심리 (메인)
├── collect/
│   ├── collect_brief.py             # 수집기 2: AI 일일 브리프
│   ├── collect_earnings.py          # 수집기 3: 어닝 인텔리전스
│   ├── collect_macro_insight.py     # 수집기 4: 매크로 인사이트
│   ├── price_context.py             # 중립적 가격 맥락 fetcher (심리 수집용)
│   └── git_utils.py                 # 공용 git commit/push 헬퍼
│
├── latest.json                      # 심리 스냅샷 — 항상 최신
├── history/
│   ├── YYYY-MM-DD_pre_open.json     # 미국 장 개장 전 스냅샷 (UTC 09:00~17:59)
│   └── YYYY-MM-DD_post_close.json   # 미국 장 마감 후 스냅샷 (UTC 18:00~)
│
├── brief/
│   ├── latest.json                  # AI 일일 브리프 — 항상 최신
│   └── history/
│       └── YYYY-MM-DD_<slot>.json
│
├── earnings/
│   ├── latest.json                  # 어닝 인텔리전스 — 항상 최신
│   └── history/
│       └── YYYY-MM-DD.json
│
└── macro/
    ├── latest.json                  # 매크로 인사이트 — 항상 최신
    └── history/
        └── YYYY-MM-DD_<slot>.json
```

---

## 4개의 수집기

### 1. 소셜 심리 (`collect_sentiment.py`)

**하루 2회** 실행 (pre_open, post_close 슬롯). 7개 워치리스트 종목 + 미국 시장 전체에 대해:

1. SniperBoard에서 **중립적 가격 맥락** 수집 (변동성 크기, 거래량 비율, 52주 위치 — 방향 제거)
2. 맥락을 관찰 단서로만 Grok 프롬프트에 주입 (오염 방지선: 방향 단어 기계적 차단)
3. `hermes -z --provider grok-oauth`로 Grok 쿼리
4. Grok 응답 후 **divergence** 계산 (가격 방향 vs 심리 부호)
5. **composite_score** (−2.0~+2.0) 계산 — 신뢰도·봇의심·언급량·divergence·추세 가중치 반영

**워치리스트:** TSLA, AAPL, NVDA, META, AMZN, GOOGL, PLTR

**출력: `latest.json` 및 `history/YYYY-MM-DD_<slot>.json`**

### 2. AI 일일 브리프 (`collect/collect_brief.py`)

심리 수집기 완료 후 실행. 다음 데이터를 결합:
- SniperBoard API에서 **기술적 맥락** (Risk Regime, Distribution Days, 종목별 Stage2 점수·RS 점수)
- `latest.json`에서 **소셜 심리**

결합 프롬프트를 Grok에 전송 → 시장 브리프 + 종목별 브리프 반환 (setup_quality A+/A/B/C/D, action_bias buy/hold/watch/avoid, 이중 언어 분석 텍스트).

생성 시점의 **맥락 스냅샷**도 함께 저장 (SniperBoard Brief 패널의 투명성 제공).

**출력: `brief/latest.json` 및 `brief/history/YYYY-MM-DD_<slot>.json`**

### 3. 어닝 인텔리전스 (`collect/collect_earnings.py`)

**yfinance** (calendar + earnings_history)로 7개 워치리스트 종목의 어닝 데이터 수집 후 Grok으로 리스크 해석 생성. 3단계 분류:

- **Imminent** (7일 이내): 이벤트 리스크 관리 구간
- **Approaching** (8~21일): 포지션 계획 시작 구간
- **Watching** (22~30일): 조기 인지 구간

calendar → `earnings_dates`/`earnings_estimate` 폴백, 수치·날짜 검증, 단일 종목 실패 시에도 부분 결과 지원, `--dry-run` 모드 제공.

**출력: `earnings/latest.json` 및 `earnings/history/YYYY-MM-DD.json`**

### 4. 매크로 인사이트 (`collect/collect_macro_insight.py`)

SniperBoard `/api/macro`에서 21개 매크로 자산 데이터(VIX, SPY, QQQ, 금리, 원자재, 섹터 ETF) 수집 후 Grok으로 그룹별 AI 해석 생성.

전체 요약, 핵심 불릿(신호 → 시장 의미 형식), 그룹별 텍스트(변동성, 폭, 신용, 금리, 원자재, 섹터) 반환.

**출력: `macro/latest.json` 및 `macro/history/YYYY-MM-DD_<slot>.json`**

---

## 스키마 v2.0 요약

전체 스펙은 `schema.json` 참조. 주요 열거형:

| 필드 | 허용값 |
|------|--------|
| `sentiment` | `very_fearful` `fearful` `neutral` `optimistic` `euphoric` |
| `sentiment_score` | 정수 −2 ~ +2 |
| `trend_vs_yesterday` | `cooling` `stable` `heating` |
| `mention_volume` | `low` `normal` `elevated` `surging` |
| `confidence` | `high` `med` `low` |
| `slot` | `pre_open` `post_close` |
| `divergence` | `none` `aligned` `bullish_divergence` `bearish_divergence` |
| `composite_score` | 실수 −2.0 ~ +2.0 |
| `intraday_shift` | `cooling` `stable` `heating` (pre_open은 null) |

**이중 언어 텍스트 필드 (v2.0):** AI 생성 텍스트는 `_en`/`_ko` 접미사 쌍 사용:
- `key_reason_en` / `key_reason_ko`
- `top_news.headline_en` / `top_news.headline_ko`
- 브리프: `summary_en/ko`, `watch_points_en/ko`, `brief_en/ko`, `key_risk_en/ko`, `key_opportunity_en/ko`

**이중 언어 데이터 소비 패턴:**
```python
locale = "ko"  # 또는 "en"
reason = data["market"]["key_reason_ko"] if locale == "ko" else data["market"]["key_reason_en"]

# v1.x 데이터 폴백 (_en/_ko 필드 없는 구버전):
def get_field(obj, field, locale):
    en_val = obj.get(f"{field}_en")
    ko_val = obj.get(f"{field}_ko")
    fallback = obj.get(field)
    return (ko_val or fallback or "") if locale == "ko" else (en_val or fallback or "")
```

> **스키마 버전 이력:** 1.0 기본 | 1.1 price_context+divergence | 1.2 slot+intraday_shift | 1.3 composite_score | 1.4 top_news | **2.0 이중 언어 _en/_ko 필드 (현재)**

---

## 다른 프로그램에서 소비하기

```bash
# 최신 심리 스냅샷
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/latest.json

# 최신 AI 일일 브리프
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/brief/latest.json

# 최신 어닝 인텔리전스
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/earnings/latest.json

# 최신 매크로 인사이트
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/macro/latest.json
```

비공개 레포의 경우 헤더에 PAT 토큰 추가:
```bash
curl -H "Authorization: token $SENTIMENT_DATA_TOKEN" \
     https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/latest.json
```

> **소스 코드에 토큰을 절대 하드코딩하지 마세요.** docker-compose env 또는 cron 환경으로 주입하세요.

---

## 파이프라인 실행

```bash
# 1. 소셜 심리 (UTC 13:00, 21:00 실행)
python collect_sentiment.py

# 2. AI 일일 브리프 (심리 수집 후 실행)
python -m collect.collect_brief

# 3. 어닝 인텔리전스 (하루 1회)
python -m collect.collect_earnings

# 4. 매크로 인사이트 (심리 수집 후 실행)
python -m collect.collect_macro_insight

# 드라이런 (어닝만, git push 없음)
python -m collect.collect_earnings --dry-run
```

**필수 환경변수:**

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SENTIMENT_REPO_PATH` | 스크립트 디렉토리 | 이 레포 클론 로컬 경로 |
| `HERMES_CMD` | `/Users/jerry/.local/bin/hermes` | hermes 바이너리 절대 경로 |
| `HERMES_PROVIDER` | `""` | Hermes 프로바이더 (예: `grok-oauth`) |
| `HERMES_TIMEOUT` | `120` | 호출당 타임아웃 (초) |
| `HERMES_RETRY` | `1` | 타임아웃 시 재시도 횟수 |
| `SNIPERBOARD_API_BASE` | `http://localhost:5001` | SniperBoard 백엔드 URL |
| `SENTIMENT_SLOT` | 자동 감지 | 슬롯 강제 지정: `pre_open` 또는 `post_close` |

---

## 테스트

```bash
# 전체 테스트 실행
python -m pytest collect/ -v

# 개별 모듈
python -m pytest collect/test_collect_sentiment.py -v
python -m pytest collect/test_collect_brief.py -v
python -m pytest collect/test_price_context.py -v
python -m pytest collect/test_collect_brief_context.py -v
```

Phase 5 (yf-accuracy-harden 플랜 완료) 기준 48개 테스트 통과.

---

## 안전 원칙

| 원칙 | 구현 |
|------|------|
| **오염 방지선** | 가격 방향은 Grok에 절대 전달 안 함. 크기·거래량·위치만 주입. 생성된 프롬프트마다 기계적 assert 검사. |
| **범주형만, 가짜 수치 금지** | `sentiment_score`는 `sentiment` 열거형에서 결정론적으로 도출. Grok은 백분율 반환 불가. |
| **실패 시 묵묵히, 가짜값 금지** | 실패 종목: skip + 로그. 데이터 fetch 실패: `available: false`. 임의 채움값 없음. |
| **신뢰도 낮음 → 다운그레이드** | `confidence: low`는 소비 측에서 neutral 처리, SniperBoard에서 시각적으로 흐리게 표시. |
| **계층 독립성** | 한 계층 장애가 다른 계층을 죽이지 않음. 모든 네트워크 경계에 timeout + try/except. |
| **시크릿은 환경변수로** | 토큰·경로 하드코딩 없음. docker-compose env 또는 cron 환경으로만 주입. |
