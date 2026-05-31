> English docs: [CLAUDE_CODE_INSTRUCTIONS_layer1_revised.md](./CLAUDE_CODE_INSTRUCTIONS_layer1_revised.md)

# Claude Code 작업 지침 — 계층 1 개정판 (가격 맥락 보강 수집기)

> **이 문서는 기존 `CLAUDE_CODE_INSTRUCTIONS_sentiment.md`의 "3. 계층 1 — 수집 스크립트" 섹션을 대체·확장합니다.**
> 계층 2(스키마)와 계층 3(SniperBoard 소비측)은 기존 지침을 그대로 따르되, 이 문서에 명시된 스키마 추가 필드(`price_context`, `divergence`)만 반영하면 됩니다.
> 데이터 저장소는 확정되었습니다: **`https://github.com/pjhwa/market-sentiment-data`**

---

## 0. 무엇이 바뀌었나 (개정 요약)

기존 수집기는 종목명만 Grok에 넘겨 "맨눈으로" 심리를 물었습니다. 개정판은 **수집 전에 SniperBoard 백엔드 API에서 중립적 가격 맥락을 먼저 받아**, 그것으로 Grok의 검색을 좁히고 반어 판독을 돕습니다. 그리고 심리를 받은 **후에** 가격 방향과 비교해 다이버전스를 계산합니다.

```
[개정 전]  hermes -z "TSLA 심리 알려줘"  →  JSON

[개정 후]
  ① SniperBoard API fetch ──→ 중립적 변동성/거래량/위치 단서 추출
                                (방향·판정 제거!)
  ② hermes -z "TSLA 심리. 참고로 오늘 변동성 큼/거래량 N배" ──→ JSON
  ③ 심리 받은 후 SniperBoard 가격 방향과 비교 ──→ divergence 계산
  ④ price_context + divergence 를 더해 latest.json 빌드 → push
```

---

## 1. 가장 중요한 원칙 — 오염 방지선 (READ FIRST)

이 개정의 성패는 단 하나의 규칙에 달려 있습니다. **위반하면 심리 데이터가 가격의 그림자가 되어 분석 가치가 0이 됩니다.**

> ### ⛔ 절대 규칙
> **가격 정보는 "Grok이 어디를 볼지" 안내하는 데만 쓰고, "무엇을 느낄지"는 절대 알려주지 않는다.**

### Grok 프롬프트에 넣어도 되는 것 (중립적 관찰 단서)
- 가격 **변동의 크기**: "오늘 비정상적으로 큰 가격 변동이 있었다" (방향 없음)
- **거래량**: "오늘 거래량이 평소의 N배"
- **위치**: "최근 52주 고점 부근이다" / "주요 가격 레벨 근처다" (돌파/이탈 같은 판정 없이 위치만)

### Grok 프롬프트에 절대 넣으면 안 되는 것 (방향·결론)
- ❌ "올랐다 / 떨어졌다 / 급등 / 급락"
- ❌ "강세 신호가 떴다 / 매수 신호 / Stage 2 점수 높음 / Risk Regime RISK_ON"
- ❌ SniperBoard의 어떤 매수·매도·홀딩 판정도
- ❌ RSI 수치, EMA 정배열 여부 등 방향성을 함의하는 지표

**이유:** 방향을 알려주면 Grok이 실제 X 게시물을 읽는 대신 "올랐으니 긍정이겠지"라고 *추론*해버린다. 그러면 심리가 가격에서 파생되어, 가격과 독립적이라는 — 소셜 심리의 유일한 분석적 가치가 — 사라진다. 형사에게 "이 시간대 현장을 집중 조사하라"(단서)는 되지만 "범인은 이 사람"(결론)을 미리 주면 수사가 오염되는 것과 같다.

> **Claude Code 자기점검:** 프롬프트 빌더 함수를 작성한 뒤, 생성된 프롬프트 문자열에 방향 단어(up/down/올랐/떨어/급등/급락/bullish/bearish/buy/sell/strong)가 들어가지 않는지 검사하는 assert 또는 단위 테스트를 반드시 넣어라. 이 가드가 오염 방지선의 기계적 보증이다.

---

## 2. SniperBoard에서 가져올 데이터 (방향 제거 후 사용)

SniperBoard는 이미 필요한 "중립적 변동성" 정보를 갖고 있으므로 새 계산이 필요 없습니다. 기존 엔드포인트를 그대로 읽되, **방향성 필드는 버리고 크기·위치·거래량만** 추출합니다.

| 엔드포인트 | 뽑을 것 (중립) | 버릴 것 (방향·판정) |
|-----------|---------------|--------------------|
| `GET /api/daily?symbol=` | ATR14, 당일 가격 변동폭(절대값/ATR 배수), 52주 고점 이격(거리만) | Stage2 점수, market_structure, EMA 정배열, 신호 |
| `GET /api/ohlcv?symbol=&tf=` | 최근 봉 거래량 ÷ vol_avg20 (배수) | 6개 신호 불리언, RSI 방향 |
| `GET /api/macro` (시장 전체용) | ^VIX 수준(변동성 환경 라벨: 낮음/보통/높음) | SPY/QQQ 등락 방향 |

**파생할 중립 단서 (price_context 객체):**

```json
{
  "volatility": "normal",        // "calm" | "normal" | "elevated" | "extreme"
                                  // = 당일 변동폭 ÷ ATR14 로 산출 (방향 없음, 크기만)
  "volume_ratio": 2.3,           // 최근 거래량 ÷ vol_avg20
  "near_key_level": "near_52w_high", // "none" | "near_52w_high" | "near_52w_low"
                                     // 거리만 보고 판정(±3% 이내), 돌파/이탈 여부는 안 봄
  "abnormal_move": true          // |당일변동| > 1.5 × ATR14 이면 true (방향 무관)
}
```

> **방향 제거의 구체 구현:** 당일 변동폭은 반드시 **절대값**으로만 다뤄라. `abs(close - open) / atr14` 처럼. 부호(+/−)를 price_context에 절대 담지 마라. `near_key_level`도 "고점 근처"까지만 말하고 "돌파했다/실패했다"는 판정을 넣지 마라(그건 방향 함의).

### Claude Code 프롬프트 — 가격 맥락 fetcher

```
SniperBoard 백엔드에서 중립적 가격 맥락만 추출하는 모듈을 작성하라.

파일: collect/price_context.py (수집기 디렉토리 내)

함수 fetch_price_context(symbol) -> dict:
- 환경변수 SNIPERBOARD_API_BASE (예: http://localhost:5000) 사용.
- GET /api/daily?symbol= 와 GET /api/ohlcv?symbol=&tf=5m 호출 (timeout 10s, try/except).
- 다음만 계산해 반환:
    volatility: abs(당일 변동폭)/atr14 → calm(<0.5)/normal(<1.0)/elevated(<1.5)/extreme(>=1.5)
    volume_ratio: 최근봉 거래량 / vol_avg20 (소수 1자리)
    near_key_level: 52주 고점/저점 ±3% 이내면 해당 라벨, 아니면 none
    abnormal_move: abs(당일 변동) > 1.5*atr14
- ⛔ 절대 반환하지 말 것: 등락 방향, 부호, Stage2 점수, 신호, RSI, EMA 정배열, regime.
- API 실패 시 모든 필드 null + available:false 로 반환 (수집은 맥락 없이도 진행되어야 함).

함수 fetch_market_context() -> dict:
- GET /api/macro 에서 ^VIX 만 읽어 vix_level: low(<16)/normal(<22)/high(>=22) 반환.
- 그 외 방향성 정보는 무시.

작성 후, 반환 dict를 문자열화했을 때 방향 단어(up/down/bull/bear/올랐/떨어/급등/급락)가 
없음을 확인하는 단위 테스트를 추가하라.
```

---

## 3. 개정된 Grok 프롬프트 (맥락 주입, 방향 제거)

가격 맥락을 **중립 단서로만** 끼워 넣습니다. Grok은 여전히 실제 X 게시물을 읽어 심리를 판단하되, 더 좁고 정확한 맥락에서 읽습니다.

```
You are a data extraction tool, not an analyst. Read current public X (Twitter) posts 
about $SYMBOL and report the crowd's sentiment. Respond with ONE JSON object ONLY — 
no prose, no code fences.

CONTEXT (use ONLY to focus your search and judge sarcasm — do NOT let it decide the sentiment):
- This stock had an UNUSUALLY LARGE price move today (size only; direction unknown to you).
- Today's volume was about {volume_ratio}x its recent average.
- Price is currently {near_key_level_human}.
  (e.g. "near its 52-week high" / "near its 52-week low" / "not near any key level")

IMPORTANT about the context:
- The context tells you WHERE to look and helps you tell sincere posts from sarcastic ones.
- It does NOT tell you whether sentiment is positive or negative. You must determine that 
  ONLY from the actual posts you read. Do not assume a big move means a particular mood.

Schema (exact enums):
{
  "symbol": "SYMBOL",
  "sentiment": ["very_fearful","fearful","neutral","optimistic","euphoric"],
  "trend_vs_yesterday": ["cooling","stable","heating"],
  "mention_volume": ["low","normal","elevated","surging"],
  "key_reason": "one short sentence in Korean",
  "bot_suspected": ["yes","no","unclear"],
  "confidence": ["high","med","low"]
}

Rules:
- Determine sentiment ONLY from real posts, never inferred from the price context.
- No invented percentages. Categorical enums only.
- Thin/noisy sample → confidence "low".
- Output raw JSON only.
```

> **조건부 주입:** `abnormal_move`가 false면 "UNUSUALLY LARGE price move today" 문장을 빼라 (거짓 맥락 주입 금지). `near_key_level`이 none이면 "not near any key level"로. price_context가 available:false면 CONTEXT 블록 전체를 생략하고 개정 전처럼 맨눈으로 물어라 — 맥락이 없다고 수집을 멈추지 않는다.

> **왜 size만 주고 direction은 빼나 (재강조):** "큰 변동이 있었다 + 거래량 3배"는 Grok이 *어떤 글을 찾을지*를 좁혀준다(검색 품질↑). 하지만 "올랐다"를 주는 순간 Grok은 글을 안 읽고도 답을 추론할 수 있게 된다(독립성 파괴). 이 한 끗 차이가 개정의 전부다.

---

## 4. 심리 수집 후 — 다이버전스 계산 (오염 위험 없음)

다이버전스는 Grok에게 주는 게 아니라, 심리를 **다 받은 뒤** 스크립트가 SniperBoard의 실제 가격 방향과 비교해 계산합니다. 이 단계에서는 비로소 가격 **방향**을 써도 됩니다 — Grok의 판단은 이미 끝났으므로 오염될 게 없습니다.

```
divergence 계산 (수집 후처리):
  price_dir  = SniperBoard 당일 종가 방향 (up / down / flat)  ← 여기선 방향 사용 OK
  senti_dir  = sentiment_score 부호 (positive / negative / neutral)

  if price_dir == up   and senti_dir == negative → "bearish_divergence"  (가격↑ 심리↓)
  if price_dir == down and senti_dir == positive → "bullish_divergence"  (가격↓ 심리↑)
  else → "aligned" 또는 "none"
```

이 `divergence` 필드는 결합 매트릭스의 가장 강력한 신호입니다 — 가격과 군중이 어긋나는 지점이라 추세 전환의 전조일 수 있습니다. 수집 시점에 미리 표시해두면 소비측(SniperBoard 탭)이 바로 활용합니다.

> **방향 사용의 분리:** 3장(프롬프트)에서는 방향 금지, 4장(후처리)에서는 방향 허용. 이 둘을 코드에서 명확히 분리하라. price_context fetcher는 방향을 반환하지 않지만, divergence 계산용으로 종가 방향만 따로 가져오는 별도 함수 `fetch_close_direction(symbol)`를 두고, 그 결과는 **오직 4장 후처리에만** 흘러가게 하라. 절대 3장 프롬프트 빌더로 새어들지 않게.

---

## 5. 스키마 추가 필드 (계층 2 업데이트)

per-symbol 객체에 두 필드를 추가합니다. 기존 `schema.json`을 갱신하세요.

```json
{
  "symbol": "TSLA",
  "as_of": "2026-05-21T14:30:00Z",
  "sentiment": "fearful",
  "sentiment_score": -1,
  "trend_vs_yesterday": "heating",
  "mention_volume": "surging",
  "key_reason": "리콜 우려로 매도 심리 확산",
  "bot_suspected": "no",
  "confidence": "high",
  "source": "grok-oauth via hermes",

  "price_context": {                    // ★ 신규: 수집에 쓴 중립 단서 (감사·재현용)
    "volatility": "extreme",
    "volume_ratio": 3.1,
    "near_key_level": "none",
    "abnormal_move": true
  },
  "divergence": "bullish_divergence"    // ★ 신규: 후처리 계산 결과
                                        // "aligned" | "none" | "bullish_divergence" | "bearish_divergence"
}
```

> `price_context`를 데이터에 함께 저장하는 이유: 나중에 "이 심리값이 어떤 맥락에서 수집됐는지" 감사하고, 다이버전스 판정을 재현할 수 있게 하기 위함. 다른 프로그램이 이 데이터를 소비할 때도 맥락을 함께 받으면 더 똑똑하게 쓸 수 있다.

> **호환성:** `schema_version`을 "1.0" → "1.1"로 올려라. 소비측(SniperBoard)이 두 필드를 optional로 다뤄, 구버전 history 파일(필드 없음)도 깨지지 않게 하라.

---

## 6. 개정된 수집기 전체 흐름

```
1. WATCHLIST = ["TSLA","AAPL","NVDA","META","AMZN","GOOGL"]
2. market_context = fetch_market_context()          # VIX 수준만
3. 각 종목 symbol 에 대해:
     a. ctx = fetch_price_context(symbol)            # 중립 단서 (방향 없음)
     b. prompt = build_prompt(symbol, ctx)           # 방향 단어 가드 통과 필수
     c. raw = hermes -z prompt --provider grok-oauth # 헤드리스 호출, timeout
     d. obj = parse_and_validate(raw)                # 첫{~마지막} 추출 + schema 검증
        실패 시 skip + 로그 (가짜값 금지)
     e. obj["price_context"] = ctx
     f. close_dir = fetch_close_direction(symbol)    # 방향 — 후처리 전용
        obj["divergence"] = compute_divergence(close_dir, obj["sentiment_score"])
4. market 객체도 동일 패턴 (extreme_flag 포함)
5. latest.json + history/YYYY-MM-DD.json 빌드 (schema_version "1.1")
6. git add/commit/push → pjhwa/market-sentiment-data
7. "N/7 종목 수집 성공" + 다이버전스 발생 종목 요약 출력
```

### Claude Code 프롬프트 — 개정 수집기 통합

```
기존 collect_sentiment.py 를 개정하라 (또는 새로 작성). 변경점:

1. 2장의 price_context.py 를 import 해, 각 종목 Grok 호출 전에 
   fetch_price_context(symbol) 를 먼저 부른다.
2. 3장의 개정 프롬프트로 build_prompt(symbol, ctx) 작성:
   - abnormal_move=False면 "large move" 문장 생략.
   - near_key_level에 맞춰 사람이 읽는 문구로 변환.
   - ctx가 available:false면 CONTEXT 블록 전체 생략(맨눈 폴백).
   - ⛔ 생성된 프롬프트에 방향 단어가 없는지 가드(assert/검사) 통과.
3. Grok 응답 파싱·검증 후 obj["price_context"]=ctx 부착.
4. fetch_close_direction(symbol) 로 종가 방향만 따로 받아 
   compute_divergence() 로 divergence 필드 계산해 부착.
   — 이 방향 값이 build_prompt 로 새지 않음을 코드 구조로 보장하라.
5. schema_version "1.1", 두 신규 필드 포함해 latest.json/history 빌드.
6. push 대상은 pjhwa/market-sentiment-data. 인증은 환경변수 토큰.
7. 요약 출력에 "divergence 발생: TSLA(bullish), ..." 추가.

기존의 timeout, skip-on-failure, 가짜값 금지, 환경변수 설정 원칙은 모두 유지하라.
SniperBoard API가 죽어 있어도(맥락 fetch 실패) 수집은 맨눈 모드로 계속되어야 한다.
```

---

## 7. 자기점검 체크리스트 (Claude Code가 작성 후 확인)

```
[ ] price_context fetcher 가 방향/부호/판정을 절대 반환하지 않는다 (단위 테스트 통과)
[ ] build_prompt 출력에 방향 단어가 없다 (가드 통과)
[ ] abnormal_move=False / near_key_level=none / ctx unavailable 각각의 프롬프트 분기 확인
[ ] fetch_close_direction 의 결과가 프롬프트 빌더로 흘러들지 않는다 (호출 그래프 확인)
[ ] divergence 계산이 4가지 케이스(aligned/none/bullish/bearish)를 올바로 분기
[ ] SniperBoard API 다운 시 맨눈 폴백으로 수집 지속
[ ] schema.json 1.1 갱신, 신규 필드 optional, 구버전 history 호환
[ ] Grok 호출 timeout, 파싱 실패 skip+log, 가짜값 없음
[ ] 토큰·경로 환경변수화, push 대상 = pjhwa/market-sentiment-data
[x] (earnings 수집 별도) collect/collect_earnings.py 의 폴백·검증·partial·schema·structured logging (Phase 3 hardening complete as part of sniperboard yf-accuracy-harden plan): structured per-sym/raw logging, calendar fallback, numeric/date validation, jsonschema+light schema, partial+graceful usable on fail, --dry-run. 48 tests green (Phase 5). Cross-linkage: sniperboard earnings_service + /api/earnings meta age_minutes + FE badges consume the hardened output; pairs with sniperboard data_adapter/Stage2 accuracy work.
```

---

## 8. 한 줄 정리

> 가격 맥락은 Grok에게 **"어디를 보라"**고 안내하는 손전등이지, **"무엇을 느껴라"**고 명령하는 대본이 아니다. 변동의 *크기*와 *거래량*과 *위치*는 검색을 좁혀 정확도를 올리지만, 변동의 *방향*은 심리를 가격의 메아리로 만들어 가치를 없앤다. 방향은 오직 수집이 끝난 뒤 다이버전스를 계산할 때만 쓴다.

**Phase 5 / yf-accuracy-harden linkage note (2026-05-24):** Earnings collector hardening (above) + sniperboard-side data_adapter centralization (single source of truth for yf prices, full delegation, adj prices in Stage2 long-term metrics) + endpoint meta (age_minutes) + minimal FE badges complete. 48 collect tests + 29 sniper tests green. Cross-repo: GitHub raw + services provide freshness transparency for AI Brief/Earnings in SniperBoard dashboard. All mandatory docs (incl this + _sentiment.md + sniper PROJECT_CONTEXT/README) updated. Plan + exec-8 verification passed.
