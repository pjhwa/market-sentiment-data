# Global Context — Morning Briefing Enhancement
**Date:** 2026-06-03
**Status:** Approved for implementation
**Repos:** market-sentiment-data + sniperboard

---

## 1. Goal

Enhance the morning briefing (`briefing/latest.json`) with a `global_context` section containing the top 3 global macro/geopolitical issues that could move US stocks today. Surface these as dedicated cards in the SniperBoard `MorningBriefingBoard`.

---

## 2. Data Flow

```
[cron: 07:15 KST — single entry, unchanged]
  └─ collect_morning_briefing.py
        │
        ├── [Grok 1차 호출] fetch_global_context()
        │      timeout: HERMES_TIMEOUT_GLOBAL (default 90s)
        │      → 웹 검색으로 Top 3 글로벌 이슈 추출
        │      → 실패 시 {} 반환, 브리핑 계속 진행
        │
        └── [Grok 2차 호출] build_prompt(data, global_ctx)
               timeout: HERMES_TIMEOUT (default 300s)
               → global_ctx가 프롬프트 상단에 주입됨
               → briefing/latest.json에 global_context 섹션 포함 저장
```

- No new cron entry. No new files. No new Python module.
- SniperBoard backend passes JSON through unchanged — no backend changes needed.

---

## 3. JSON Schema — `global_context` section

Added to `briefing/latest.json` after `big_picture`, before `sector_analysis`.
`schema_version`: `"1.0"` → `"1.1"`

```json
"global_context": {
  "fetched_at": "2026-06-03T22:15:00Z",
  "search_window": "48h",
  "issues": [
    {
      "rank": 1,
      "tier": "breaking",
      "category": "trade_tariff",
      "title_en": "US expands chip export controls to 5 new countries",
      "title_ko": "미국, 반도체 수출통제 5개국 추가",
      "summary_en": "2-3 sentences. What happened, where reported, why it matters. Unconfirmed details prefixed with 'unconfirmed:'",
      "summary_ko": "같은 내용 2-3문장 한국어",
      "source_hint": "Reuters 2026-06-03",
      "confidence": "confirmed",
      "us_stock_impact_en": "NVDA and MU face direct export headwind. TSM indirectly affected via fab customers.",
      "us_stock_impact_ko": "NVDA·MU 직접 영향, TSM 간접 영향.",
      "impact_direction": "negative"
    }
  ],
  "ongoing_no_update": ["central_bank", "ai_regulation"]
}
```

**Field contracts:**
- `tier`: `"breaking"` (new in 48h) | `"ongoing"` (persistent situation, no new dev)
- `category`: `"trade_tariff"` | `"geopolitical"` | `"central_bank"` | `"ai_regulation"`
- `confidence`: `"confirmed"` | `"developing"` | `"unverified"`
- `impact_direction`: `"positive"` | `"negative"` | `"neutral"` | `"watch"`
- `issues`: always 3 items; empty array `[]` on fallback
- `ongoing_no_update`: list of categories checked but with no 48h update

---

## 4. Prompt Design — 1st Grok Call

### Key best practices applied
| Problem | Solution |
|---------|----------|
| Hallucination / fabrication | Explicit "DO NOT" instructions > positive instructions |
| Stale news presented as new | 48h strict time window + date anchoring |
| Missing ongoing situations | Hardcoded Persistent Watchlist — always checked regardless of trending |
| Unverified claims stated as fact | Attribution (source_hint) required + "unconfirmed:" prefix mandate |

### Prompt: `build_global_context_prompt(now_kst, now_iso)`

```
You are a professional financial intelligence analyst with live web search access.
Today is {now_kst} (KST) / {now_iso} (UTC).

━━━ TASK ━━━
Search the web for global macro and geopolitical developments from the LAST 48 HOURS
that could move US stock prices TODAY. Structure output into TWO tiers:

TIER 1 — BREAKING (last 48h): New developments that just happened.
TIER 2 — ONGOING WATCH: Persistent situations with NO new development today
  but still carrying active market risk. Check ALL of the following even if not trending:
  · US-China trade / semiconductor export controls (NVDA, TSM, MU impact)
  · Taiwan Strait military tension (TSM, NVDA supply chain)
  · Middle East conflict / Strait of Hormuz (oil, defense: CEG, VST)
  · Russia-Ukraine war (energy prices, European demand)
  · ECB / BOJ / BOE policy stance (USD direction, rate-sensitive tech)
  · US AI/antitrust regulation (GOOGL, META, MSFT, AAPL)
  · US tariff / trade deal negotiations
  If a category has NO meaningful update in 48h, set status "no_update" and add to ongoing_no_update.

━━━ RULES — READ CAREFULLY ━━━
✓ ONLY report events attributable to a real, verifiable source.
  Include source_hint: e.g. "Reuters 2026-06-03", "White House statement", "BOJ press release"
✓ Time-box strictly: if you cannot confirm an event occurred within 48 hours, DO NOT include it as new.
✓ If uncertain about a fact, write "unconfirmed:" at the start of that sentence.
✗ DO NOT fabricate specific figures (percentages, dates, names) you cannot verify.
✗ DO NOT present a viral social media claim as confirmed fact.
✗ DO NOT include background/historical context as if it were a new development.
✗ DO NOT speculate on price targets or predict market direction.
✗ DO NOT smooth over uncertainty — if developing and unclear, say so explicitly.

━━━ WATCHLIST TICKERS FOR IMPACT MAPPING ━━━
TSM NVDA META TSLA PLTR MU CRWD AMZN MSFT AAPL GOOGL
RKLB CEG VST ALAB OKLO APP ANET NVO QBTS SOFI

━━━ OUTPUT FORMAT (raw JSON only, no markdown) ━━━
{
  "fetched_at": "{now_iso}",
  "search_window": "48h",
  "issues": [
    {
      "rank": 1,
      "tier": "breaking|ongoing",
      "category": "trade_tariff|geopolitical|central_bank|ai_regulation",
      "title_en": "≤80 chars — factual headline, no spin",
      "title_ko": "30자 이내 — 사실 위주",
      "summary_en": "2-3 sentences. WHAT happened, WHERE reported, WHY markets care. Unconfirmed prefix.",
      "summary_ko": "같은 내용 한국어 2-3문장.",
      "source_hint": "e.g. Reuters 2026-06-03",
      "confidence": "confirmed|developing|unverified",
      "us_stock_impact_en": "Name specific tickers and explain direction. 'impact unclear pending confirmation' if unknown.",
      "us_stock_impact_ko": "감시 종목 티커 명시 + 방향",
      "impact_direction": "positive|negative|neutral|watch"
    }
  ],
  "ongoing_no_update": ["category names with no 48h development"]
}
```

### 2nd Call injection: `_format_global_context_block(global_ctx)`

Prepended to existing `build_prompt()` output. Instructs Grok to:
- Reflect the most impactful issue in `big_picture.summary` (1 sentence)
- Use geopolitical/regulatory context in `sector_analysis`
- Mention relevant issues in `spotlight`/`watchlist` for directly named tickers
- Pass through `global_context` JSON into output as-is

---

## 5. Timeout Configuration

```python
CALL_TIMEOUT        = int(os.environ.get("HERMES_TIMEOUT", "300"))       # 2nd call (existing)
CALL_TIMEOUT_GLOBAL = int(os.environ.get("HERMES_TIMEOUT_GLOBAL", "90")) # 1st call (new)
```

Total max runtime: 90 + 300 = 390s. Existing cron timeout budget is sufficient.

---

## 6. Graceful Fallback

| Failure scenario | Behavior |
|-----------------|----------|
| 1st call timeout / error | `global_ctx = {}`, 2nd call proceeds normally |
| 1st call JSON parse failure | Same fallback |
| 1st call returns empty issues | Same fallback |
| Fallback active | `global_context: {"issues": [], "fallback": true}` in output JSON |

Briefing generation never aborts due to global context failure.

---

## 7. SniperBoard Frontend Changes

### 7a. `useMorningBriefing.ts` — New interfaces

```typescript
export interface GlobalIssue {
  rank: number;
  tier: 'breaking' | 'ongoing';
  category: 'trade_tariff' | 'geopolitical' | 'central_bank' | 'ai_regulation';
  title_en?: string;
  title_ko?: string;
  summary_en?: string;
  summary_ko?: string;
  source_hint?: string;
  confidence?: 'confirmed' | 'developing' | 'unverified';
  us_stock_impact_en?: string;
  us_stock_impact_ko?: string;
  impact_direction?: 'positive' | 'negative' | 'neutral' | 'watch';
}

export interface GlobalContext {
  fetched_at?: string;
  search_window?: string;
  issues: GlobalIssue[];
  ongoing_no_update?: string[];
  fallback?: boolean;
}
```

Add `global_context?: GlobalContext` to `MorningBriefingData`.

### 7b. `MorningBriefingBoard.tsx` — New section

**Placement:** Between `big_picture` card and `sector_analysis` card (Row 3 area).

**`GlobalContextSection` component:**
- Section header: `🌐 Global Macro & Geopolitical Context` / `🌐 글로벌 매크로 · 지정학 리스크`
- One card per issue (`span 4/3` grid, up to 3 cards)
- Each card shows:
  - Category badge (color-coded: `trade_tariff`=warn, `geopolitical`=bear, `central_bank`=info, `ai_regulation`=purple)
  - Tier badge: `BREAKING` (bull) | `ONGOING` (neutral)
  - Confidence badge: `DEVELOPING` or `UNVERIFIED` shown when not `confirmed`
  - `impact_direction` badge (positive=bull, negative=bear, neutral=neutral, watch=warn)
  - Title (bold)
  - Summary text
  - `source_hint` in small monospace
  - US stock impact row (highlighted box, lists affected tickers)
- If `fallback: true` or `issues` empty: section is hidden (no empty state shown)
- `ongoing_no_update` shown as small footnote: "No significant update in 48h: central_bank, ai_regulation"

### 7c. `buildShareText()` — Add global context to share text

Add a `🌐 Global Issues` section between macro and sectors in the share text output.

---

## 8. Files Changed

| Repo | File | Change |
|------|------|--------|
| market-sentiment-data | `collect/collect_morning_briefing.py` | 2-stage Grok, new prompt, validation |
| market-sentiment-data | `PROJECT_CONTEXT.md` | Document new flow + schema_version 1.1 |
| market-sentiment-data | `README.md` | briefing/ section updated |
| sniperboard | `frontend/hooks/useMorningBriefing.ts` | GlobalIssue, GlobalContext interfaces |
| sniperboard | `frontend/components/boards/MorningBriefingBoard.tsx` | GlobalContextSection component |

No new files. No cron changes. No backend changes. Backward compatible.
