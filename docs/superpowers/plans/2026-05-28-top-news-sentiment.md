# top_news 심리 수집 + SentimentBoard 표시 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** sentiment 수집 시 각 종목/마켓에서 가장 많이 언급되는 뉴스 1건을 `top_news`(headline + summary + source)로 추출해 latest.json에 저장하고, sniperboard SentimentBoard UI에 표시한다.

**Architecture:** 기존 Grok 프롬프트 JSON 스키마에 `top_news` 필드를 추가하여 별도 API 호출 없이 동일 Grok 응답에서 뉴스를 수집한다. `top_news`는 optional — 없으면 `null`이고 수집 실패 원인이 되지 않는다. schema_version을 `1.4`로 올리고, sniperboard 백엔드 Pydantic 스키마와 프론트엔드 TypeScript 타입도 함께 업데이트한다. UI는 각 카드 아래 뉴스 박스로 표시한다.

**Repos:**
- `/Users/jerry/dev/market-sentiment-data` — 수집 파이프라인
- `/Users/jerry/dev/sniperboard` — 대시보드 (백엔드 + 프론트엔드)

**Tech Stack:** Python 3.11, unittest, JSON Schema draft-07, FastAPI/Pydantic, Next.js/TypeScript/React

---

## File Map

| 레포 | 파일 | 변경 유형 | 역할 |
|------|------|-----------|------|
| market-sentiment-data | `collect_sentiment.py` | Modify | 프롬프트 확장, 파싱/검증, 엔트리 빌더 수정 |
| market-sentiment-data | `schema.json` | Modify | TopNews 정의 추가, schema_version 1.4 |
| market-sentiment-data | `collect/test_collect_sentiment.py` | Modify | top_news 단위 테스트 추가 |
| sniperboard | `backend/api/schemas.py` | Modify | TopNews Pydantic 모델, SymbolSentiment/MarketSentiment에 top_news 필드 |
| sniperboard | `backend/tests/test_sentiment_service.py` | Modify | top_news 포함 스냅샷 픽스처 업데이트 |
| sniperboard | `frontend/app/types.ts` | Modify | TopNews interface, SymbolSentiment/MarketSentiment에 top_news 추가 |
| sniperboard | `frontend/components/boards/SentimentBoard.tsx` | Modify | 각 카드에 TopNewsBox 컴포넌트 렌더링 |

---

### Task 1: schema.json에 TopNews 정의 추가 + schema_version 1.4

**Files:**
- Modify: `schema.json`

- [ ] **Step 1: `definitions`에 `TopNews` 오브젝트 추가**

`schema.json`의 `"definitions"` 블록 안에 다음을 추가한다 (기존 `DivergenceEnum` 앞):

```json
"TopNews": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "headline": {
      "type": "string",
      "description": "가장 많이 언급된 뉴스/포스트의 원문 제목 또는 캡션 (영문 그대로)"
    },
    "summary": {
      "type": "string",
      "description": "1-2문장 한국어 요약"
    },
    "source": {
      "type": "string",
      "description": "출처 문자열 (예: Bloomberg, @elonmusk, CNBC)"
    }
  }
},
```

- [ ] **Step 2: `SymbolSentiment`에 `top_news` 필드 추가**

`SymbolSentiment.properties` 블록 마지막에 추가:

```json
"top_news": {
  "oneOf": [
    { "$ref": "#/definitions/TopNews" },
    { "type": "null" }
  ],
  "description": "가장 많이 언급된 뉴스/포스트 1건. 없으면 null. v1.4 추가."
}
```

- [ ] **Step 3: `MarketSentiment`에 `top_news` 필드 추가**

`MarketSentiment.properties` 블록 마지막에 추가 (위와 동일):

```json
"top_news": {
  "oneOf": [
    { "$ref": "#/definitions/TopNews" },
    { "type": "null" }
  ],
  "description": "가장 많이 언급된 뉴스/포스트 1건. 없으면 null. v1.4 추가."
}
```

- [ ] **Step 4: `schema_version` enum에 `"1.4"` 추가, description 업데이트**

```json
"schema_version": {
  "type": "string",
  "enum": ["1.0", "1.1", "1.2", "1.3", "1.4"],
  "description": "1.0: 기본. 1.1: price_context+divergence. 1.2: slot+intraday_shift. 1.3: composite_score. 1.4: top_news 추가."
},
```

- [ ] **Step 5: JSON 문법 검증**

```bash
python3 -c "import json; json.load(open('schema.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add schema.json
git commit -m "schema: add TopNews definition and schema_version 1.4"
```

---

### Task 2: validate_top_news 헬퍼 함수 추가 + 테스트

**Files:**
- Modify: `collect_sentiment.py` (validate_top_news 함수)
- Modify: `collect/test_collect_sentiment.py`

- [ ] **Step 1: 실패 테스트 작성**

`collect/test_collect_sentiment.py`에 클래스 추가:

```python
class TestValidateTopNews(unittest.TestCase):
    def test_valid_top_news(self):
        tn = {
            "headline": "BofA raises AAPL target to $250",
            "summary": "BofA가 애플 목표주가를 상향했다.",
            "source": "Bloomberg",
        }
        self.assertTrue(cs.validate_top_news(tn))

    def test_none_is_valid(self):
        self.assertTrue(cs.validate_top_news(None))

    def test_missing_headline_invalid(self):
        self.assertFalse(cs.validate_top_news({"summary": "요약", "source": "출처"}))

    def test_missing_summary_invalid(self):
        self.assertFalse(cs.validate_top_news({"headline": "제목", "source": "출처"}))

    def test_missing_source_invalid(self):
        self.assertFalse(cs.validate_top_news({"headline": "제목", "summary": "요약"}))

    def test_non_string_headline_invalid(self):
        self.assertFalse(cs.validate_top_news({"headline": 123, "summary": "요약", "source": "출처"}))

    def test_non_dict_non_none_invalid(self):
        self.assertFalse(cs.validate_top_news("not a dict"))
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
python -m pytest collect/test_collect_sentiment.py::TestValidateTopNews -v
```

Expected: `FAILED` (validate_top_news not defined)

- [ ] **Step 3: `validate_top_news` 구현**

`collect_sentiment.py`의 `validate_market_fields` 함수 바로 아래에 추가:

```python
def validate_top_news(data) -> bool:
    """top_news 구조 검증. None은 허용(optional 필드)."""
    if data is None:
        return True
    if not isinstance(data, dict):
        return False
    for field in ("headline", "summary", "source"):
        if field not in data or not isinstance(data[field], str):
            return False
    return True
```

- [ ] **Step 4: 테스트 실행 (통과 확인)**

```bash
python -m pytest collect/test_collect_sentiment.py::TestValidateTopNews -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add collect_sentiment.py collect/test_collect_sentiment.py
git commit -m "feat: add validate_top_news helper with tests"
```

---

### Task 3: Grok 프롬프트에 top_news 필드 추가

**Files:**
- Modify: `collect_sentiment.py` (`_SYMBOL_PROMPT_BASE`, `MARKET_PROMPT`)

- [ ] **Step 1: `_SYMBOL_PROMPT_BASE` 스키마 블록에 `top_news` 추가**

기존 스키마 블록:

```
  "confidence": one of ["high","med","low"]
}}
```

를 다음으로 교체:

```
  "confidence": one of ["high","med","low"],
  "top_news": {{"headline": "원문 제목 또는 가장 많이 공유된 포스트 캡션", "summary": "1-2문장 한국어 요약", "source": "출처(Bloomberg/@username 등)"}} or null if no clear top story
}}
```

- [ ] **Step 2: `_SYMBOL_PROMPT_BASE`의 Rules에 top_news 지시 추가**

기존 Rules 마지막 줄(`- Output raw JSON only.`) 앞에 추가:

```
- top_news: pick the single most-shared or most-discussed news/post about this ticker. If nothing stands out, set it to null.
```

- [ ] **Step 3: `MARKET_PROMPT` 스키마 블록에 `top_news` 추가**

기존 스키마 블록:

```
  "confidence": one of ["high","med","low"]
}
```

를 다음으로 교체:

```
  "confidence": one of ["high","med","low"],
  "top_news": {"headline": "원문 제목 또는 가장 많이 공유된 포스트 캡션", "summary": "1-2문장 한국어 요약", "source": "출처(Bloomberg/@username 등)"} or null if no clear top story
}
```

- [ ] **Step 4: `MARKET_PROMPT`의 Rules에 top_news 지시 추가**

기존 Rules 마지막 줄(`- Output the raw JSON object and nothing else.`) 앞에 추가:

```
- top_news: pick the single most-shared or most-discussed market news/macro post. If nothing stands out, set it to null.
```

- [ ] **Step 5: 프롬프트 방향 단어 가드가 여전히 통과하는지 확인**

```bash
python3 -c "
from collect_sentiment import build_prompt
ctx = {'available': False}
p = build_prompt('AAPL', 'Apple', ctx)
print('OK — no direction words in prompt')
"
```

Expected: `OK — no direction words in prompt` (AssertionError 없음)

- [ ] **Step 6: Commit**

```bash
git add collect_sentiment.py
git commit -m "feat: add top_news field to symbol and market Grok prompts"
```

---

### Task 4: build_symbol_entry / build_market_entry에 top_news 포함

**Files:**
- Modify: `collect_sentiment.py` (`build_symbol_entry`, `build_market_entry`, `main`)
- Modify: `collect/test_collect_sentiment.py`

- [ ] **Step 1: build_symbol_entry top_news 테스트 작성**

`collect/test_collect_sentiment.py`에 클래스 추가:

```python
class TestBuildSymbolEntryTopNews(unittest.TestCase):
    def _base_raw(self):
        return {
            "sentiment": "optimistic",
            "trend_vs_yesterday": "stable",
            "mention_volume": "normal",
            "key_reason": "테스트 이유",
            "bot_suspected": "no",
            "confidence": "med",
        }

    def test_top_news_included_when_present(self):
        raw = self._base_raw()
        raw["top_news"] = {
            "headline": "BofA raises AAPL to $250",
            "summary": "BofA가 목표주가를 상향했다.",
            "source": "Bloomberg",
        }
        entry = cs.build_symbol_entry(raw, "AAPL", "2026-05-28T13:00:00Z", {}, "aligned")
        self.assertIsNotNone(entry.get("top_news"))
        self.assertEqual(entry["top_news"]["source"], "Bloomberg")

    def test_top_news_null_when_absent(self):
        raw = self._base_raw()
        entry = cs.build_symbol_entry(raw, "AAPL", "2026-05-28T13:00:00Z", {}, "aligned")
        self.assertIsNone(entry.get("top_news"))

    def test_top_news_null_when_explicitly_none(self):
        raw = self._base_raw()
        raw["top_news"] = None
        entry = cs.build_symbol_entry(raw, "AAPL", "2026-05-28T13:00:00Z", {}, "aligned")
        self.assertIsNone(entry.get("top_news"))


class TestBuildMarketEntryTopNews(unittest.TestCase):
    def _base_raw(self):
        return {
            "sentiment": "fearful",
            "trend_vs_yesterday": "cooling",
            "extreme_flag": "none",
            "key_reason": "마켓 테스트",
            "confidence": "high",
        }

    def test_top_news_included_when_present(self):
        raw = self._base_raw()
        raw["top_news"] = {
            "headline": "Fed holds rates",
            "summary": "연준이 금리를 동결했다.",
            "source": "Reuters",
        }
        entry = cs.build_market_entry(raw, "2026-05-28T13:00:00Z")
        self.assertIsNotNone(entry.get("top_news"))
        self.assertEqual(entry["top_news"]["source"], "Reuters")

    def test_top_news_null_when_absent(self):
        raw = self._base_raw()
        entry = cs.build_market_entry(raw, "2026-05-28T13:00:00Z")
        self.assertIsNone(entry.get("top_news"))
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

```bash
python -m pytest collect/test_collect_sentiment.py::TestBuildSymbolEntryTopNews collect/test_collect_sentiment.py::TestBuildMarketEntryTopNews -v
```

Expected: FAILED (top_news not in entry)

- [ ] **Step 3: `build_symbol_entry`에 top_news 포함**

`build_symbol_entry` 함수에서 `entry` dict 생성 후 `entry["divergence"] = divergence` 줄 뒤에 추가:

```python
tn = raw.get("top_news")
entry["top_news"] = tn if validate_top_news(tn) and tn is not None else None
```

- [ ] **Step 4: `build_market_entry`에 top_news 포함**

`build_market_entry` 함수의 return dict 마지막에 추가:

```python
"top_news": raw.get("top_news") if validate_top_news(raw.get("top_news")) and raw.get("top_news") is not None else None,
```

- [ ] **Step 5: 테스트 실행 (통과 확인)**

```bash
python -m pytest collect/test_collect_sentiment.py -v
```

Expected: 전체 통과

- [ ] **Step 6: `collect_sentiment.py`의 `schema_version`을 `"1.4"`로 올리기**

`main()` 함수 안의 snapshot dict에서:

```python
"schema_version": "1.2",
```

를:

```python
"schema_version": "1.4",
```

으로 변경.

- [ ] **Step 7: 전체 테스트 통과 확인**

```bash
python -m pytest collect/ -v
```

Expected: 전체 통과

- [ ] **Step 8: Commit**

```bash
git add collect_sentiment.py collect/test_collect_sentiment.py
git commit -m "feat: include top_news in symbol and market sentiment entries (schema v1.4)"
```

---

---

### Task 5: sniperboard 백엔드 schemas.py에 TopNews 추가

**Files:**
- Modify: `/Users/jerry/dev/sniperboard/backend/api/schemas.py`
- Modify: `/Users/jerry/dev/sniperboard/backend/tests/test_sentiment_service.py`

- [ ] **Step 1: `TopNews` Pydantic 모델 추가**

`schemas.py`의 `# --- Sentiment (소셜 심리) ---` 블록 바로 위에 추가:

```python
class TopNews(BaseModel):
    headline: str
    summary: str
    source: str
```

- [ ] **Step 2: `SymbolSentiment`에 `top_news` 필드 추가**

`SymbolSentiment` 클래스의 `intraday_shift` 필드 아래에 추가:

```python
top_news: Optional[TopNews] = None
```

- [ ] **Step 3: `MarketSentiment`에 `top_news` 필드 추가**

`MarketSentiment` 클래스의 `intraday_shift` 필드 아래에 추가:

```python
top_news: Optional[TopNews] = None
```

- [ ] **Step 4: test_sentiment_service.py 스냅샷 픽스처에 top_news 추가**

`test_sentiment_service.py`의 `PRE_OPEN_SNAPSHOT`과 `POST_CLOSE_SNAPSHOT`의 `market` 및 `symbols[0]`에 추가:

```python
"top_news": {
    "headline": "Fed holds rates steady",
    "summary": "연준이 금리를 동결했다.",
    "source": "Reuters",
}
```

- [ ] **Step 5: 기존 테스트가 여전히 통과하는지 확인**

```bash
cd /Users/jerry/dev/sniperboard/backend && python -m pytest tests/test_sentiment_service.py -v
```

Expected: 전체 통과 (top_news는 Optional이므로 기존 테스트 실패 없음)

- [ ] **Step 6: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add backend/api/schemas.py backend/tests/test_sentiment_service.py
git commit -m "feat: add TopNews Pydantic model and top_news field to sentiment schemas"
```

---

### Task 6: sniperboard 프론트엔드 types.ts에 TopNews 추가

**Files:**
- Modify: `/Users/jerry/dev/sniperboard/frontend/app/types.ts`

- [ ] **Step 1: `TopNews` interface 추가**

`types.ts`의 `// --- Sentiment (소셜 심리) ---` 주석 바로 위에 추가:

```typescript
export interface TopNews {
  headline: string;
  summary: string;
  source: string;
}
```

- [ ] **Step 2: `SymbolSentiment`에 `top_news` 필드 추가**

`SymbolSentiment` interface의 `intraday_shift` 줄 아래에 추가:

```typescript
top_news?: TopNews | null;
```

- [ ] **Step 3: `MarketSentiment`에 `top_news` 필드 추가**

`MarketSentiment` interface의 `intraday_shift` 줄 아래에 추가:

```typescript
top_news?: TopNews | null;
```

- [ ] **Step 4: TypeScript 타입 검사**

```bash
cd /Users/jerry/dev/sniperboard/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: 에러 없음

- [ ] **Step 5: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add frontend/app/types.ts
git commit -m "feat: add TopNews interface and top_news field to sentiment types"
```

---

### Task 7: SentimentBoard.tsx에 TopNewsBox 컴포넌트 추가 및 렌더링

**Files:**
- Modify: `/Users/jerry/dev/sniperboard/frontend/components/boards/SentimentBoard.tsx`

- [ ] **Step 1: `TopNewsBox` 컴포넌트 추가**

`SentimentBoard.tsx`의 `ScoreBar` 함수 정의 위에 추가:

```tsx
function TopNewsBox({ topNews }: { topNews: import('@/app/types').TopNews | null | undefined }) {
  if (!topNews) return null;
  return (
    <div style={{
      marginTop: 8,
      padding: '7px 10px',
      borderRadius: 6,
      background: 'var(--em-soft)',
      borderLeft: '2px solid var(--em-500)',
    }}>
      <div style={{ fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--fg-subtle)', marginBottom: 3 }}>
        주요 뉴스
      </div>
      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg)', lineHeight: 1.4, marginBottom: 3 }}>
        {topNews.headline}
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--fg-muted)', lineHeight: 1.5, marginBottom: 4 }}>
        {topNews.summary}
      </div>
      <div style={{ fontSize: 9.5, color: 'var(--fg-subtle)' }}>
        출처: {topNews.source}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Market 카드에 TopNewsBox 렌더링**

Market 카드 안의 `market.key_reason` div 바로 아래에 추가:

```tsx
<TopNewsBox topNews={market.top_news} />
```

위치는 `key_reason` 텍스트 div 다음, `marginTop: 10` stats flex div 앞:

```tsx
<div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--fg-muted)', lineHeight: 1.6 }}>
  {market.key_reason}
</div>
<TopNewsBox topNews={market.top_news} />   {/* ← 이 줄 추가 */}
<div style={{ marginTop: 10, display: 'flex', gap: 12, fontSize: 10.5 }}>
```

- [ ] **Step 3: Symbol 카드에 TopNewsBox 렌더링**

Symbol 카드 안의 `key_reason` div 바로 아래에 추가:

```tsx
{/* 이유 */}
<div style={{ fontSize: 10.5, color: 'var(--fg-muted)', lineHeight: 1.5, marginBottom: 6 }}>
  {it.key_reason}
</div>
<TopNewsBox topNews={it.top_news} />   {/* ← 이 줄 추가 */}

{/* 메타 */}
```

- [ ] **Step 4: Glossary에 top_news 항목 추가**

`SENTIMENT_GLOSSARY` 배열의 마지막 항목 뒤에 추가:

```typescript
{ term: '주요 뉴스 (Top News)', plain: '이 심리 점수가 수집될 당시 X(트위터)에서 가장 많이 공유되거나 언급된 뉴스 또는 포스트 1건입니다. 소셜 심리의 주된 촉매를 빠르게 파악할 수 있습니다.' },
```

- [ ] **Step 5: TypeScript 타입 검사**

```bash
cd /Users/jerry/dev/sniperboard/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: 에러 없음

- [ ] **Step 6: Commit**

```bash
cd /Users/jerry/dev/sniperboard
git add frontend/components/boards/SentimentBoard.tsx
git commit -m "feat: render TopNewsBox in market and symbol sentiment cards"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** headline + summary + source 세 필드 모두 포함, symbol/market 양쪽 모두 반영, optional(null 허용) 처리, schema_version 1.4 업그레이드, sniperboard 백엔드 스키마 + 프론트엔드 타입 + UI 모두 반영
- [x] **Placeholder scan:** TBD/TODO 없음
- [x] **Type consistency:** `validate_top_news` / `TopNews` / `top_news` 이름이 모든 Task에서 동일, `build_symbol_entry` / `build_market_entry` 시그니처 변경 없음, Pydantic `Optional[TopNews]`와 TS `TopNews | null | undefined` 일치
