# Claude Code 작업 지침 — SniperBoard 소셜 심리 파이프라인

> **이 문서는 Claude Code가 읽고 실행하기 위한 작업 명세서입니다.**
> 사람이 읽어도 되지만, 1차 독자는 Claude Code입니다. 각 섹션의 "Claude Code 프롬프트" 블록을 순서대로 실행하면 전체 파이프라인이 완성됩니다.

---

## 0. 한눈에 보는 아키텍처

소셜 심리 데이터를 **3개 계층으로 분리**합니다. 이 분리가 이 설계의 핵심입니다 — 수집 주체(Hermes)와 소비 주체(SniperBoard)가 서로를 직접 알 필요 없이, 가운데의 **GitHub 리포지토리를 공용 데이터 소스**로 삼아 느슨하게 결합합니다. 덕분에 나중에 다른 프로그램도 같은 데이터를 그대로 가져다 쓸 수 있습니다.

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  계층 1: 수집        │     │  계층 2: 저장소       │     │  계층 3: 소비        │
│  (맥미니 cron)       │     │  (GitHub repo)        │     │  (SniperBoard 등)    │
│                      │     │                       │     │                      │
│  hermes -z 로        │ git │  sentiment-data/      │ raw │  FastAPI가 fetch     │
│  Grok에 질의 →       │push │   ├─ latest.json      │fetch│   → /api/sentiment   │
│  JSON 파싱 →         │────▶│   ├─ history/         │────▶│   → 새 Sentiment 탭  │
│  파일로 커밋         │     │   │   └─ YYYY-MM-DD.json│     │                      │
│                      │     │   └─ schema.json      │     │  (다른 프로그램도    │
│                      │     │                       │     │   동일하게 소비 가능)│
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
```

**왜 이렇게 나누나:**

- **수집과 소비의 분리** — SniperBoard가 죽어도 데이터 수집은 계속되고, 반대도 마찬가지. 각 계층을 독립적으로 고치고 재시작할 수 있습니다.
- **재사용성** — 심리 데이터가 표준 JSON으로 GitHub에 있으니, 향후 만들 다른 대시보드·봇·노트북이 동일 데이터를 `raw.githubusercontent.com`에서 바로 읽습니다.
- **이력 보존** — 매일 스냅샷을 `history/`에 누적하므로, "어제 대비 변화"를 SniperBoard가 직접 계산하지 않고 데이터에서 읽어오면 됩니다.
- **비용·속도 분리** — 느린 LLM 호출(수 초~수십 초)은 cron이 미리 처리하고, SniperBoard는 저장된 JSON만 즉시 반환하므로 UI가 빠릅니다.

---

## 1. 사전 조건 (이미 갖춰진 것 / 새로 필요한 것)

### 이미 갖춰진 것 (사용자 환경)
- 맥미니에 Hermes 설치 완료, SuperGrok과 OAuth 연결 완료 → `hermes -z ... --provider grok-oauth` 호출 가능
- SniperBoard가 Docker로 구동 중 (backend 5001, frontend 4000)

### 새로 준비해야 하는 것
| 항목 | 설명 | 누가 |
|------|------|------|
| GitHub 데이터 리포 | 심리 데이터 전용 리포 (예: `pjhwa/market-sentiment-data`). private 가능 | 사용자가 GitHub에서 생성 |
| 배포용 토큰 | cron이 push할 때 쓸 PAT 또는 deploy key. private 리포면 SniperBoard fetch에도 필요 | 사용자 |
| 로컬 clone 경로 | 맥미니에서 수집 스크립트가 commit/push할 작업 경로 | 사용자 결정 (예: `~/sentiment-collector`) |

> **Claude Code에게:** 위 "새로 준비할 것"은 사람이 GitHub UI에서 해야 하는 일이다. 너는 이것들이 준비되었다고 가정하고, 환경변수로 주입받는 코드를 작성하라. 값을 하드코딩하지 마라.

---

## 2. 계층 2 먼저 정하기 — 데이터 계약(schema)

가장 먼저 **데이터 형식을 고정**합니다. 세 계층이 모두 이 형식에 의존하므로, 이게 흔들리면 전부 깨집니다. 수집·소비 코드를 짜기 전에 이 스키마부터 확정하세요.

### 2-1. 종목 심리 객체 (per-symbol)

```json
{
  "symbol": "TSLA",
  "as_of": "2026-05-21T14:30:00Z",
  "sentiment": "optimistic",
  "sentiment_score": 1,
  "trend_vs_yesterday": "cooling",
  "mention_volume": "elevated",
  "key_reason": "Q2 인도량 가이던스 상향에 대한 기대",
  "bot_suspected": "no",
  "confidence": "high",
  "source": "grok-oauth via hermes"
}
```

| 필드 | 허용값 | 의미 |
|------|--------|------|
| `symbol` | 티커 문자열 | 종목 |
| `as_of` | ISO8601 UTC | 이 심리의 기준 시각 |
| `sentiment` | `very_fearful` `fearful` `neutral` `optimistic` `euphoric` | 5단계 범주 (정량 % 아님) |
| `sentiment_score` | 정수 −2 ~ +2 | 위 범주의 수치 매핑 (계산 편의용) |
| `trend_vs_yesterday` | `cooling` `stable` `heating` | 어제 대비 변화 |
| `mention_volume` | `low` `normal` `elevated` `surging` | 언급량 |
| `key_reason` | 한 줄 문자열 | 화제의 핵심 사유 |
| `bot_suspected` | `yes` `no` `unclear` | 봇·펌프성 글 의심 여부 |
| `confidence` | `high` `med` `low` | Grok 자체 신뢰도. `low`면 소비측에서 중립 취급 |
| `source` | 문자열 | 출처 추적용 |

> **중요:** 정량값(`sentiment_score`)은 범주(`sentiment`)에서 결정론적으로 파생된 것일 뿐, Grok이 "73% 긍정" 같은 가짜 정밀도를 주게 하지 마라. 이전 분석에서 합의된 원칙이다 — Grok은 표본을 통제하지 못하므로 범주값만 신뢰한다.

### 2-2. 묶음 파일 (`latest.json`)

```json
{
  "generated_at": "2026-05-21T14:30:00Z",
  "market": {
    "as_of": "2026-05-21T14:30:00Z",
    "sentiment": "neutral",
    "sentiment_score": 0,
    "trend_vs_yesterday": "stable",
    "extreme_flag": "none",
    "key_reason": "FOMC 앞두고 관망세",
    "confidence": "med"
  },
  "symbols": [ /* 위 per-symbol 객체 배열, WATCHLIST 6종목 */ ],
  "schema_version": "1.0"
}
```

`market` 객체에는 추가로 `extreme_flag`(`none` `extreme_fear` `extreme_greed`)를 둡니다 — 결합 매트릭스의 "공포/도취 극단" 판정에 직접 쓰입니다.

### Claude Code 프롬프트 — 스키마 파일 생성

```
계층 2의 데이터 계약을 코드로 고정하라. 데이터 리포의 루트에 다음을 만들어라:

1. `schema.json` — 위 2-1, 2-2 명세를 JSON Schema (draft-07)로 작성. 
   sentiment/trend/volume/bot/confidence의 enum을 명시하고, 
   sentiment와 sentiment_score의 매핑 관계를 description에 기록.
2. `README.md` — 이 리포가 무엇이고, latest.json/history/ 구조가 어떻게 되며,
   다른 프로그램이 어떻게 소비하면 되는지(raw URL 예시 포함) 설명.
3. 빈 `history/.gitkeep`, 그리고 예시 `latest.json` 한 개(위 예시 그대로).

JSON Schema는 수집·소비 양쪽 코드가 검증에 재사용할 수 있게 정확히 작성하라.
```

---

## 3. 계층 1 — 수집 스크립트 (맥미니 + Hermes)

`hermes -z`(programmatic one-shot 모드: 프롬프트 하나 넣으면 최종 답변 텍스트만 stdout으로 반환, 배너·스피너 없음)를 호출해 Grok에게 심리를 묻고, 응답 JSON을 파싱·검증한 뒤 데이터 리포에 commit/push 합니다.

### 3-1. 핵심 — Hermes를 헤드리스로 호출하는 법

```bash
# 종목 하나에 대한 일회성 질의 (배너/장식 없이 순수 답변만)
hermes -z "<프롬프트>" --provider grok-oauth

# 스크립트에서 캡처
answer=$(hermes -z "$PROMPT" --provider grok-oauth)
```

> **Claude Code 주의:** `hermes -z`는 최종 텍스트만 반환하지만, LLM이 가끔 JSON 앞뒤에 설명을 붙일 수 있다. 프롬프트에서 "JSON만 출력, 코드펜스·서문 금지"를 강하게 지시하고, 파싱 시에는 응답에서 첫 `{`부터 마지막 `}`까지를 추출하는 방어 코드를 넣어라. 파싱 실패 시 그 종목은 건너뛰되 로그를 남기고, 절대 가짜 데이터로 채우지 마라.

### 3-2. Grok에게 줄 프롬프트 (스크립트가 종목명만 바꿔 재사용)

```
You are a data extraction tool, not an analyst. Look at current public X (Twitter) 
posts about $SYMBOL and respond with ONE JSON object ONLY — no prose, no code fences, 
no explanation before or after.

Schema (use these exact enum values):
{
  "symbol": "SYMBOL",
  "sentiment": one of ["very_fearful","fearful","neutral","optimistic","euphoric"],
  "trend_vs_yesterday": one of ["cooling","stable","heating"],
  "mention_volume": one of ["low","normal","elevated","surging"],
  "key_reason": "one short sentence in Korean",
  "bot_suspected": one of ["yes","no","unclear"],
  "confidence": one of ["high","med","low"]
}

Rules:
- Do NOT invent precise percentages. Use only the categorical enums above.
- If the sample seems thin or very noisy, set confidence to "low".
- If you cannot determine a field, use "neutral"/"stable"/"normal"/"unclear" and lower confidence.
- Output the raw JSON object and nothing else.
```

시장 전체용 프롬프트는 위와 동일하되 대상이 `US equity market broadly (S&P 500, rates, recession)`이고, 추가 필드 `extreme_flag` (`none`/`extreme_fear`/`extreme_greed`)를 요구합니다.

### 3-3. 스크립트가 할 일 (의사 흐름)

```
1. WATCHLIST = ["TSLA","AAPL","NVDA","META","AMZN","GOOGL"]  # SniperBoard와 동일하게 유지
2. 각 종목에 대해 hermes -z 호출 → JSON 파싱 → schema.json으로 검증
3. 시장 전체 1회 호출 → market 객체 생성
4. sentiment → sentiment_score 매핑 적용 (very_fearful=-2 ... euphoric=+2)
5. trend_vs_yesterday 보정: history/ 의 어제 파일과 비교해 교차검증 (선택)
6. latest.json 빌드 + history/<오늘날짜>.json 으로도 저장
7. git add / commit / push  (커밋 메시지: "sentiment: <date> <time> update")
8. 모든 단계 로그를 남기고, 실패한 종목 수를 요약 출력
```

### Claude Code 프롬프트 — 수집기 작성

```
계층 1 수집기를 작성하라. 새 디렉토리(사용자가 환경변수 SENTIMENT_REPO_PATH로 지정한 
로컬 clone 경로)에서 동작하는 Python 스크립트 `collect_sentiment.py`를 만들어라.

요구사항:
- WATCHLIST 6종목 + 시장 전체에 대해 `hermes -z "<prompt>" --provider grok-oauth`를 
  subprocess로 호출 (3-2 프롬프트 사용). 종목명만 치환.
- 각 응답을 방어적으로 JSON 파싱 (첫 { ~ 마지막 } 추출), schema.json으로 jsonschema 검증.
- 검증 실패/호출 실패한 종목은 건너뛰고 stderr에 로그. 가짜값 금지.
- sentiment→score 매핑 함수 작성.
- latest.json 과 history/YYYY-MM-DD.json 둘 다 쓰기 (UTC 기준 날짜).
- GitPython 또는 subprocess git으로 add/commit/push. 
  push 실패 시 명확히 에러 출력(인증 문제일 가능성 안내).
- 모든 설정(리포 경로, WATCHLIST, hermes 명령, provider)은 상단 상수 또는 환경변수로.
- 마지막에 "N/7 종목 수집 성공" 요약 한 줄 출력.

호출 타임아웃(예: 종목당 120초)을 두고, hermes가 멈춰도 스크립트 전체가 
무한정 매달리지 않게 하라.
```

### 3-4. 자동화 — cron 등록

```bash
# 미국장 마감 후(ET 16:00 = 보통 KST 다음날 새벽)와 장중 1회 등 하루 1~2회 권장.
# 예: 매일 KST 06:30, 22:30 두 번
30 6,22 * * *  cd ~/sentiment-collector && /usr/bin/python3 collect_sentiment.py >> ~/sentiment.log 2>&1
```

> **비용·예의 주의:** Grok 호출 빈도 = 비용/부하. 하루 1~2회로 시작하라. 분 단위 폴링은 보조 지표에 과하다. 또한 cron 환경에는 PATH가 빈약하므로 `hermes` 절대경로를 쓰거나 스크립트 안에서 PATH를 보강하라.

---

## 4. 계층 2 — 리포 구조 최종형

```
market-sentiment-data/
├── README.md              # 소비 방법 안내 (raw URL 예시)
├── schema.json            # 데이터 계약 (JSON Schema)
├── latest.json            # 가장 최근 스냅샷 (소비측이 주로 읽음)
└── history/
    ├── 2026-05-20.json
    ├── 2026-05-21.json    # 일별 누적
    └── ...
```

소비측이 읽을 raw URL 형태 (private면 토큰 헤더 필요):
```
https://raw.githubusercontent.com/<user>/market-sentiment-data/main/latest.json
```

> **Claude Code:** README.md에 위 raw URL 패턴과, private 리포일 때 토큰으로 인증해 fetch하는 curl 예시를 반드시 적어라. 이게 "다른 프로그램에서도 활용"의 진입점이다.

---

## 5. 계층 3 — SniperBoard 소비측 구현

SniperBoard 코드베이스(PROJECT_CONTEXT.md 구조 기준)에 **새 엔드포인트 + 새 탭**을 추가합니다. **기존 yfinance 신호 로직은 절대 건드리지 않습니다** — 소셜 심리는 독립된 보조 기능입니다.

### 5-1. 백엔드 — 새 서비스 + 엔드포인트

기존 패턴을 따릅니다: 외부 데이터는 `services/`에서 가져오고, 라우팅은 `api/endpoints.py`, 응답 모델은 `api/schemas.py`.

```
backend/
├── services/
│   └── sentiment_service.py   # 신규: GitHub raw에서 latest.json + 어제 history fetch
├── api/
│   ├── endpoints.py           # 수정: GET /api/sentiment 추가
│   └── schemas.py             # 수정: SentimentResponse 등 Pydantic 모델 추가
```

`sentiment_service.py`의 역할:
- 환경변수 `SENTIMENT_DATA_URL`(raw latest.json URL)과 선택적 `SENTIMENT_DATA_TOKEN`(private 리포용)을 읽어 fetch
- 응답을 검증하고, 어제 `history/` 파일도 가져와 종목별 비교(스코어 델타) 부가
- **짧은 캐시(예: 5~10분 TTL)** — GitHub raw에 매 요청마다 때리지 않도록. 기존 TanStack 폴링과 별개로 백엔드에서 캐시.
- fetch 실패 시 명확한 에러 객체 반환(프론트가 "데이터 없음"을 우아하게 표시할 수 있게)

> **CORS/네트워크 주의:** SniperBoard backend는 Docker 컨테이너 안에서 돈다. 컨테이너에서 `raw.githubusercontent.com` 으로 아웃바운드가 되는지 확인하라(보통 됨). 토큰은 docker-compose 환경변수로 주입하고 이미지에 굽지 마라.

### Claude Code 프롬프트 — 백엔드

```
SniperBoard backend에 소셜 심리 소비 기능을 추가하라. 기존 신호/지표 로직은 건드리지 마라.

1. backend/services/sentiment_service.py 신규 작성:
   - 환경변수 SENTIMENT_DATA_URL (raw latest.json), SENTIMENT_DATA_HISTORY_BASE 
     (history/ 디렉토리 raw base), 선택적 SENTIMENT_DATA_TOKEN.
   - fetch_latest(): latest.json 가져와 dict 반환. 5분 TTL 인메모리 캐시.
   - enrich_with_delta(): 종목별로 어제 history 파일과 비교해 score_delta 추가.
     어제 파일 없으면 delta=None.
   - 모든 네트워크 호출에 timeout과 try/except. 실패 시 {"available": false, "error": ...}.
   - requests 사용(이미 의존성에 없으면 requirements.txt에 추가).

2. backend/api/schemas.py 에 Pydantic v2 모델 추가:
   SymbolSentiment, MarketSentiment, SentimentResponse(available, generated_at, market, symbols, error).

3. backend/api/endpoints.py 에 GET /api/sentiment 추가:
   sentiment_service로 latest fetch + delta enrich → SentimentResponse 반환.
   실패해도 200으로 available:false를 반환(프론트가 다루기 쉽게).

4. docker-compose.yml 의 backend 서비스에 환경변수 자리(SENTIMENT_DATA_URL 등) 추가.
   값은 빈 채로 두고 주석으로 채우는 법 안내.
```

### 5-2. 프론트엔드 — 새 탭

PROJECT_CONTEXT 기준, 탭은 `app/page.tsx`에서 전환되고 각 탭은 `components/*Tab.tsx`, 데이터는 `hooks/use*.ts`(TanStack Query), 타입·메타는 `app/types.ts`.

```
frontend/
├── hooks/
│   └── useSentiment.ts        # 신규: GET /api/sentiment
├── components/
│   └── SentimentTab.tsx       # 신규: 심리 전용 화면
├── app/
│   ├── page.tsx               # 수정: 'sentiment' 탭 추가 + 라우팅
│   ├── types.ts               # 수정: Sentiment 타입 + SENTIMENT_META(색·라벨)
│   └── globals.css            # (선택) 심리 배지용 클래스
```

**SentimentTab 화면 구성 (보조 렌즈 톤 유지 — 1차 신호처럼 과장하지 말 것):**
- 상단: 시장 전체 심리 카드 (범주 라벨 + extreme_flag 강조 + 어제 대비 화살표 + key_reason)
- 본문: WATCHLIST 6종목 그리드. 각 카드에 sentiment 배지, trend 화살표(↑heating/↓cooling/→stable), mention_volume, score_delta, confidence, bot 의심 표시
- `confidence: "low"` 항목은 시각적으로 흐리게(opacity 낮춤) + "신뢰도 낮음" 캡션
- `available: false`면 "심리 데이터를 불러올 수 없습니다 — 수집기/리포 확인" 안내
- 푸터: `generated_at` 표시 + "보조 참고용. 진입 결정은 가격 신호 우선" 고지

> **결합 매트릭스 힌트(선택 고급):** 이 탭에서 SniperBoard의 기존 신호 데이터와 심리를 교차해 "확증/경고/회피" 배지를 보여주고 싶다면, useDaily/useWatchlist 결과와 useSentiment를 함께 읽어 조합 로직을 클라이언트에서 계산하라. 단, 어디까지나 "참고 배지"로만 표기하고 매매 지시처럼 보이지 않게 하라.

### Claude Code 프롬프트 — 프론트엔드

```
SniperBoard frontend에 Sentiment 전용 탭을 추가하라. 기존 4개 탭은 건드리지 마라.

1. frontend/app/types.ts:
   - SymbolSentiment, MarketSentiment, SentimentResponse 타입 추가 (백엔드 스키마와 일치).
   - SENTIMENT_META: 각 sentiment 범주의 색/라벨/아이콘, trend 화살표 매핑.
   - SYMBOLS 상수는 그대로 재사용.

2. frontend/hooks/useSentiment.ts:
   - TanStack Query로 GET ${API_BASE}/api/sentiment.
   - staleTime 5분, refetchInterval 10분(보조 지표라 자주 안 땡김).

3. frontend/components/SentimentTab.tsx:
   - 위 "화면 구성"대로. 시장 카드 + 6종목 그리드.
   - confidence low는 opacity 낮추고 캡션. available:false 처리. 
   - 기존 디자인 시스템(glass-card 등 globals.css 클래스, CSS 변수) 재사용.
   - 보조 렌즈 톤: 신호 탭보다 시각 비중 낮게. 매매 지시처럼 보이지 않게 고지문 포함.

4. frontend/app/page.tsx:
   - 탭 목록에 'sentiment' 추가, 클릭 시 SentimentTab 렌더.
   - 탭 라벨은 "소셜 심리" 또는 "Sentiment".

기존 컴포넌트/훅의 시그니처를 바꾸지 말고, 새 파일 추가 + page.tsx 최소 수정만 하라.
빌드(NEXT_PUBLIC_API_URL 번들) 영향 없는지 확인하라.
```

---

## 6. 통합 검증 (Claude Code가 마지막에 수행)

전체가 연결됐는지 end-to-end로 확인합니다.

```
검증을 순서대로 수행하고 결과를 보고하라:

1. [계층1] collect_sentiment.py 를 1회 수동 실행 → latest.json/history 가 
   생성되고 git push 까지 됐는지 확인. (실패 시 인증/네트워크/PATH 진단)
2. [계층2] raw URL 로 latest.json 이 실제로 접근되는지 curl 로 확인.
3. [계층3-backend] docker compose up 후 
   `curl http://localhost:5001/api/sentiment` 가 available:true 와 
   6종목 + market 을 반환하는지 확인.
4. [계층3-frontend] http://localhost:4000 에서 새 탭이 보이고 데이터가 
   렌더되는지 확인. confidence low 흐림 처리, available:false 폴백도 확인.
5. 어제 history 파일이 있을 때 score_delta 가 채워지는지 확인.

각 단계의 성공/실패와, 실패 시 원인·수정안을 요약하라.
```

---

## 7. 안전장치 · 설계 원칙 (반드시 지킬 것)

이 원칙들은 앞선 분석에서 합의된 것으로, 코드 전반에 일관되게 반영되어야 합니다.

| 원칙 | 코드에서의 의미 |
|------|----------------|
| **심리는 보조, 가격이 1차** | Sentiment 탭/배지를 매매 지시처럼 표현 금지. 고지문 필수. 손절·목표가 수치를 심리로 바꾸지 않음 |
| **범주만, 가짜 정밀도 금지** | sentiment_score는 범주에서 파생. Grok이 % 못 만들게 프롬프트로 차단 |
| **신뢰도 낮으면 격하** | `confidence: low`는 소비측에서 중립 취급 + 시각적으로 흐리게 |
| **실패는 조용히, 가짜는 절대 금지** | 수집 실패 종목은 skip+log. fetch 실패는 available:false. 빈칸을 지어내지 않음 |
| **계층 독립성** | 한 계층 장애가 다른 계층을 죽이지 않음. 모든 경계에 timeout/try-except |
| **비밀값은 환경변수** | 토큰·경로를 코드/이미지에 굽지 않음. docker-compose env, cron 환경 |
| **수집 빈도 절제** | 하루 1~2회. 보조 지표에 분 단위 폴링은 과함 |

---

## 8. 작업 순서 요약 (Claude Code 실행 체크리스트)

```
[ ] 2.   schema.json + 데이터 리포 README + 예시 latest.json 생성        (계층2 계약)
[ ] 3.   collect_sentiment.py 작성 (hermes -z 호출, 파싱·검증·git push)  (계층1 수집)
[ ] 3-4. cron 등록 안내 + 절대경로/PATH 보정                              (계층1 자동화)
[ ] 5-1. sentiment_service.py + /api/sentiment + schemas + compose env   (계층3 백엔드)
[ ] 5-2. useSentiment + SentimentTab + page.tsx/types 수정               (계층3 프론트)
[ ] 6.   end-to-end 검증 5단계                                           (통합)
[ ] 7.   안전장치 원칙이 전반에 반영됐는지 자기점검                       (품질)
```

> **시작점:** 계층 2(스키마)부터 만들어라. 데이터 계약이 고정돼야 계층 1과 3이 같은 형식을 바라보며 독립적으로 작업될 수 있다. 그다음 계층 1(수집)으로 실제 데이터를 리포에 채우고, 마지막에 계층 3(소비)을 붙여 검증하라.
