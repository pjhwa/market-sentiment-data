> 한국어 문서: [CLAUDE_CODE_INSTRUCTIONS_sentiment.ko.md](./CLAUDE_CODE_INSTRUCTIONS_sentiment.ko.md)

# Claude Code Instructions — SniperBoard Social Sentiment Pipeline

> **This document is a work specification for Claude Code to read and execute.**
> Humans may read it too, but Claude Code is the primary audience. Execute the "Claude Code Prompt" blocks in each section in order to complete the full pipeline.

---

## 0. Architecture at a Glance

Social sentiment data is separated into **3 layers**. This separation is the core of this design — the collection actor (Hermes) and the consumption actor (SniperBoard) don't need to know about each other directly; instead, they use a **GitHub repository as the shared data source** for loose coupling. This means other programs can consume the same data in the future without any changes.

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  Layer 1: Collect   │     │  Layer 2: Storage     │     │  Layer 3: Consume   │
│  (Mac mini cron)    │     │  (GitHub repo)        │     │  (SniperBoard etc.) │
│                     │     │                       │     │                     │
│  Query Grok via     │ git │  sentiment-data/       │ raw │  FastAPI fetches    │
│  hermes -z →        │push │   ├─ latest.json      │fetch│   → /api/sentiment  │
│  parse JSON →       │────▶│   ├─ history/         │────▶│   → new Sentiment   │
│  commit to file     │     │   │   └─ YYYY-MM-DD.json│     │     tab             │
│                     │     │   └─ schema.json      │     │                     │
│                     │     │                       │     │  (other programs    │
│                     │     │                       │     │   can consume too)  │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
```

**Why separate this way:**

- **Decoupled collection and consumption** — If SniperBoard goes down, data collection continues, and vice versa. Each layer can be independently fixed and restarted.
- **Reusability** — Sentiment data is in standard JSON on GitHub, so future dashboards, bots, or notebooks can read the same data directly from `raw.githubusercontent.com`.
- **History preservation** — Daily snapshots accumulate in `history/`, so SniperBoard can read trend changes from the data rather than computing them itself.
- **Cost/speed separation** — Slow LLM calls (seconds to tens of seconds) are handled in advance by cron; SniperBoard just returns stored JSON immediately, keeping the UI fast.

---

## 1. Prerequisites (Already in Place / New Requirements)

### Already in place (user environment)
- Hermes installed on Mac mini, SuperGrok with OAuth connected → `hermes -z ... --provider grok-oauth` works
- SniperBoard running in Docker (backend 5001, frontend 4000)

### New requirements
| Item | Description | Who |
|------|-------------|-----|
| GitHub data repo | Dedicated repo for sentiment data (e.g. `pjhwa/market-sentiment-data`). Can be private | User creates on GitHub |
| Deploy token | PAT or deploy key for cron to push. Also needed for SniperBoard fetch if private repo | User |
| Local clone path | Working path on Mac mini where the collector script commits/pushes | User decides (e.g. `~/sentiment-collector`) |

> **For Claude Code:** The "new requirements" above are things the human must do in the GitHub UI. Assume they are ready and write code that receives them via environment variables. Never hardcode values.

---

## 2. Define Layer 2 First — Data Contract (Schema)

Fix the **data format first**. All three layers depend on this format — if it shifts, everything breaks. Confirm this schema before writing collection or consumption code.

### 2-1. Per-Symbol Sentiment Object

```json
{
  "symbol": "TSLA",
  "as_of": "2026-05-21T14:30:00Z",
  "sentiment": "optimistic",
  "sentiment_score": 1,
  "trend_vs_yesterday": "cooling",
  "mention_volume": "elevated",
  "key_reason_en": "Anticipation of raised Q2 delivery guidance",
  "key_reason_ko": "Q2 인도량 가이던스 상향에 대한 기대",
  "bot_suspected": "no",
  "confidence": "high",
  "source": "grok-oauth via hermes"
}
```

| Field | Allowed values | Meaning |
|-------|---------------|---------|
| `symbol` | ticker string | Stock symbol |
| `as_of` | ISO8601 UTC | Reference time for this sentiment |
| `sentiment` | `very_fearful` `fearful` `neutral` `optimistic` `euphoric` | 5-level categorical (not a percentage) |
| `sentiment_score` | integer −2 ~ +2 | Numeric mapping of the category (for computation convenience) |
| `trend_vs_yesterday` | `cooling` `stable` `heating` | Change vs. yesterday |
| `mention_volume` | `low` `normal` `elevated` `surging` | Mention volume level |
| `key_reason_en` | one-line string (English) | Main reason for the sentiment |
| `key_reason_ko` | one-line string (Korean) | Main reason for the sentiment |
| `bot_suspected` | `yes` `no` `unclear` | Whether bot/pump posts are suspected |
| `confidence` | `high` `med` `low` | Grok's self-reported confidence. `low` → treat as neutral on consumer side |
| `source` | string | For provenance tracking |
| `top_news` (optional) | `{headline_en, headline_ko, summary_en, summary_ko, source}` or `null` | Most-mentioned news at collection time. Added in v1.4. |

> **Important:** The quantitative value (`sentiment_score`) is deterministically derived from the category (`sentiment`) — do not let Grok produce fake precision like "73% positive." This is a previously agreed principle — since Grok doesn't control the sample, only categorical values are trusted.

### 2-2. Bundle File (`latest.json`)

```json
{
  "generated_at": "2026-05-21T14:30:00Z",
  "market": {
    "as_of": "2026-05-21T14:30:00Z",
    "sentiment": "neutral",
    "sentiment_score": 0,
    "trend_vs_yesterday": "stable",
    "extreme_flag": "none",
    "key_reason_en": "Wait-and-see ahead of FOMC",
    "key_reason_ko": "FOMC 앞두고 관망세",
    "confidence": "med"
  },
  "symbols": [ /* array of per-symbol objects above, WATCHLIST 6 symbols */ ],
  "schema_version": "1.0"
}
```

The `market` object additionally has `extreme_flag` (`none` `extreme_fear` `extreme_greed`) — used directly in the combined matrix "fear/euphoria extreme" judgment.

### Claude Code Prompt — Schema File Creation

```
Codify the Layer 2 data contract. Create the following in the data repo root:

1. `schema.json` — Write the 2-1 and 2-2 spec as JSON Schema (draft-07).
   Specify enums for sentiment/trend/volume/bot/confidence,
   and document the sentiment↔sentiment_score mapping in descriptions.
2. `README.md` — Explain what this repo is, the latest.json/history/ structure,
   and how other programs should consume it (including raw URL examples).
3. Empty `history/.gitkeep`, plus one example `latest.json` (use the example above verbatim).

Write the JSON Schema accurately enough that both collection and consumption code
can reuse it for validation.
```

---

## 3. Layer 1 — Collection Script (Mac mini + Hermes)

Call `hermes -z` (programmatic one-shot mode: feed one prompt, get only the final answer text on stdout, no banners or spinners) to ask Grok for sentiment, parse and validate the JSON response, then commit/push to the data repo.

### 3-1. Core — How to Call Hermes Headlessly

```bash
# One-time query for a single symbol (pure answer only, no banners/decorations)
hermes -z "<prompt>" --provider grok-oauth

# Capture in a script
answer=$(hermes -z "$PROMPT" --provider grok-oauth)
```

> **Claude Code note:** `hermes -z` returns only the final text, but LLMs sometimes prepend/append explanations around JSON. Strongly instruct in the prompt to "output JSON only, no code fences, no preamble," and in parsing, add defensive code to extract from first `{` to last `}`. On parse failure, skip that symbol with a log entry — never fill it with fake data.

### 3-2. Grok Prompt (script reuses with only the symbol swapped)

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
  "key_reason_en": "one short sentence in English",
  "key_reason_ko": "one short sentence in Korean",
  "bot_suspected": one of ["yes","no","unclear"],
  "confidence": one of ["high","med","low"]
}

Rules:
- Do NOT invent precise percentages. Use only the categorical enums above.
- If the sample seems thin or very noisy, set confidence to "low".
- If you cannot determine a field, use "neutral"/"stable"/"normal"/"unclear" and lower confidence.
- Output the raw JSON object and nothing else.
```

The market-wide prompt is the same but targets `US equity market broadly (S&P 500, rates, recession)` and requests the additional field `extreme_flag` (`none`/`extreme_fear`/`extreme_greed`).

### 3-3. Script Responsibilities (Pseudoflow)

```
1. WATCHLIST = ["TSLA","AAPL","NVDA","META","AMZN","GOOGL"]  # Keep in sync with SniperBoard
2. For each symbol: hermes -z call → JSON parse → validate against schema.json
3. One market-wide call → build market object
4. Apply sentiment → sentiment_score mapping (very_fearful=-2 ... euphoric=+2)
5. Cross-check trend_vs_yesterday against yesterday's history/ file (optional)
6. Build latest.json + save as history/<today's date>.json
7. git add / commit / push  (commit message: "sentiment: <date> <time> update")
8. Log all steps, output summary of failed symbols
```

### Claude Code Prompt — Write the Collector

```
Write the Layer 1 collector. Create a Python script `collect_sentiment.py` that operates
in a new directory (the local clone path specified by env var SENTIMENT_REPO_PATH).

Requirements:
- Call `hermes -z "<prompt>" --provider grok-oauth` via subprocess for WATCHLIST 6 symbols
  + market-wide (use 3-2 prompt, substitute only the symbol).
- Defensively JSON-parse each response (extract first { ~ last }), validate against schema.json.
- Skip failed/invalid symbols and log to stderr. No fake values.
- Write a sentiment→score mapping function.
- Write both latest.json and history/YYYY-MM-DD.json (UTC date).
- git add/commit/push using GitPython or subprocess git.
  On push failure, print clear error (suggest auth issue).
- All config (repo path, WATCHLIST, hermes command, provider) in top constants or env vars.
- Print "N/7 symbols collected successfully" summary at the end.

Set a per-call timeout (e.g. 120s per symbol) so a stalled hermes doesn't hang the script forever.
```

### 3-4. Automation — Cron Registration

```bash
# Recommended 1-2 times per day: after US market close (ET 16:00) and once intraday.
# Example: daily at KST 06:30 and 22:30
30 6,22 * * *  cd ~/sentiment-collector && /usr/bin/python3 collect_sentiment.py >> ~/sentiment.log 2>&1
```

> **Cost/courtesy note:** Grok call frequency = cost/load. Start with 1-2 times per day. Per-minute polling is excessive for a supplemental indicator. Also, cron environments have minimal PATH — use the absolute path to `hermes` or augment PATH inside the script.

**top_news addition (2026-05-28, schema v1.4):** Added `top_news` field to `collect_sentiment.py` prompt. Grok returns the most-shared/mentioned news item as `{headline_en, headline_ko, summary_en, summary_ko, source}`. Returns `null` if none. Structure validated by `validate_top_news()` helper, then included in `build_symbol_entry`/`build_market_entry`. Displayed as "Top News" box in SniperBoard SentimentBoard.

**Earnings collector separate hardening (2026-05 Phase 3, yf-accuracy-harden plan complete):** `collect/collect_earnings.py` features calendar → earnings_dates/earnings_estimate fallback, 0-30 day date + numeric EPS validation, raw shape logging (per-sym + overall), jsonschema + lightweight schema validation pre-write, partial flag + graceful usable output on failure (no crash, no sys.exit), --dry-run support. (schema.json does not define earnings-specific schema; a separate internal schema is used.) Phase 5: 48 collect/ tests green.

**Cross-repo linkage improvements (sniperboard yf-accuracy-harden):** Sniperboard backend now consumes earnings/latest.json via earnings_service (60m cache + meta: {fetched_at, age_minutes, source} attached to /api/earnings responses). FE uses meta for minimal freshness badges in OverviewBoard. Sniperboard data_adapter (single source of truth for yf prices) + Stage2 adjusted prices improvements pair with earnings intelligence for better accuracy/insight. Brief/earnings services in sniperboard provide age_minutes transparency (linkage via GitHub raw + shared cron collect_*.py). Full plan (adapter centralization, delegation, endpoint meta, FE badges, earnings hardening) + docs/tests/manual verification complete on feat/yf-accuracy-harden-2026-05-25.

---

## 4. Layer 2 — Final Repo Structure

```
market-sentiment-data/
├── README.md              # Consumer guide (with raw URL examples)
├── schema.json            # Data contract (JSON Schema)
├── latest.json            # Most recent snapshot (primarily read by consumers)
└── history/
    ├── 2026-05-20.json
    ├── 2026-05-21.json    # Accumulated daily
    └── ...
```

Consumer raw URL format (token header required if private):
```
https://raw.githubusercontent.com/<user>/market-sentiment-data/main/latest.json
```

> **Claude Code:** The README.md must include the raw URL pattern above and a curl example showing how to authenticate with a token for private repos. This is the entry point for "use from other programs."

---

## 5. Layer 3 — SniperBoard Consumer Implementation

Add a **new endpoint + new tab** to the SniperBoard codebase (per PROJECT_CONTEXT.md structure). **Never touch the existing yfinance signal logic** — social sentiment is an independent supplemental feature.

### 5-1. Backend — New Service + Endpoint

Follow the existing patterns: external data fetched in `services/`, routing in `api/endpoints.py`, response models in `api/schemas.py`.

```
backend/
├── services/
│   └── sentiment_service.py   # NEW: fetch latest.json + yesterday's history from GitHub raw
├── api/
│   ├── endpoints.py           # MODIFIED: add GET /api/sentiment
│   └── schemas.py             # MODIFIED: add SentimentResponse and related Pydantic models
```

`sentiment_service.py` responsibilities:
- Read env vars `SENTIMENT_DATA_URL` (raw latest.json URL) and optional `SENTIMENT_DATA_TOKEN` (for private repos) and fetch
- Validate response, also fetch yesterday's `history/` file and add per-symbol comparison (score delta)
- **Short cache (e.g. 5-10 min TTL)** — don't hammer GitHub raw on every request. Cache at backend, independent of TanStack polling
- On fetch failure, return a clear error object (so the frontend can display "data unavailable" gracefully)

> **CORS/network note:** SniperBoard backend runs inside a Docker container. Verify that outbound connections from the container to `raw.githubusercontent.com` work (usually they do). Inject tokens via docker-compose env vars — don't bake them into the image.

### Claude Code Prompt — Backend

```
Add social sentiment consumption to SniperBoard backend. Do not touch existing signal/indicator logic.

1. Create backend/services/sentiment_service.py:
   - Env vars: SENTIMENT_DATA_URL (raw latest.json), SENTIMENT_DATA_HISTORY_BASE 
     (history/ directory raw base), optional SENTIMENT_DATA_TOKEN.
   - fetch_latest(): fetch latest.json and return as dict. 5-minute TTL in-memory cache.
   - enrich_with_delta(): compare each symbol against yesterday's history file and add score_delta.
     If no yesterday file, delta=None.
   - All network calls wrapped in timeout and try/except. On failure: {"available": false, "error": ...}.
   - Use requests (add to requirements.txt if not already a dependency).

2. Add Pydantic v2 models to backend/api/schemas.py:
   SymbolSentiment, MarketSentiment, SentimentResponse(available, generated_at, market, symbols, error).

3. Add GET /api/sentiment to backend/api/endpoints.py:
   Use sentiment_service to fetch latest + enrich delta → return SentimentResponse.
   Even on failure, return 200 with available:false (easier for frontend to handle).

4. Add env var placeholders (SENTIMENT_DATA_URL etc.) to the backend service in docker-compose.yml.
   Leave values empty with comments explaining how to fill them.
```

### 5-2. Frontend — New Tab

Per PROJECT_CONTEXT, tabs are switched in `app/page.tsx`, each tab is a `components/*Tab.tsx`, data comes from `hooks/use*.ts` (TanStack Query), types and metadata constants are in `app/types.ts`.

```
frontend/
├── hooks/
│   └── useSentiment.ts        # NEW: GET /api/sentiment
├── components/
│   └── SentimentTab.tsx       # NEW: sentiment-specific screen
├── app/
│   ├── page.tsx               # MODIFIED: add 'sentiment' tab + routing
│   ├── types.ts               # MODIFIED: Sentiment types + SENTIMENT_META (colors, labels)
│   └── globals.css            # (optional) sentiment badge classes
```

**SentimentTab screen layout (keep supplemental-lens tone — don't overstate like a primary signal):**
- Top: Market-wide sentiment card (category label + extreme_flag highlight + vs-yesterday arrow + key_reason)
- Body: WATCHLIST 6-symbol grid. Each card shows sentiment badge, trend arrow (↑heating/↓cooling/→stable), mention_volume, score_delta, confidence, bot suspicion indicator
- `confidence: "low"` items visually dimmed (low opacity) + "Low confidence" caption
- `available: false` displays "Cannot load sentiment data — check collector/repo"
- Footer: `generated_at` display + "Supplemental reference only. Price signals take priority for entry decisions" disclaimer

> **Combined matrix hint (optional advanced):** If you want to cross-reference existing SniperBoard signal data with sentiment in this tab — showing "Confirmed/Warning/Avoid" badges — compute the combination logic on the client using `useDaily`/`useWatchlist` alongside `useSentiment`. Always display these as "reference badges" only, never as trade instructions.

### Claude Code Prompt — Frontend

```
Add a Sentiment-only tab to SniperBoard frontend. Do not touch the existing 4 tabs.

1. frontend/app/types.ts:
   - Add SymbolSentiment, MarketSentiment, SentimentResponse types (matching backend schema).
   - SENTIMENT_META: color/label/icon mapping for each sentiment category, trend arrow mapping.
   - Reuse the existing SYMBOLS constant as-is.

2. frontend/hooks/useSentiment.ts:
   - TanStack Query fetching GET ${API_BASE}/api/sentiment.
   - staleTime 5 minutes, refetchInterval 10 minutes (supplemental indicator, no need to poll frequently).

3. frontend/components/SentimentTab.tsx:
   - Per the "screen layout" above: market card + 6-symbol grid.
   - Dim confidence:low items with opacity reduction + caption. Handle available:false.
   - Reuse existing design system (glass-card etc. from globals.css, CSS variables).
   - Supplemental-lens tone: lower visual weight than signal tabs. Include disclaimer against trade instructions.

4. frontend/app/page.tsx:
   - Add 'sentiment' to tab list, render SentimentTab on click.
   - Tab label: "Social Sentiment" or "Sentiment".

Do not change signatures of existing components/hooks. Only add new files + minimal page.tsx edits.
Verify no impact on the build (NEXT_PUBLIC_API_URL bundle).
```

---

## 6. Integration Verification (Claude Code performs at the end)

Confirm everything is connected end-to-end.

```
Perform the following steps in order and report results:

1. [Layer 1] Run collect_sentiment.py once manually → confirm latest.json/history is created
   and git push completes. (On failure: diagnose auth/network/PATH issues)
2. [Layer 2] Confirm latest.json is accessible via raw URL using curl.
3. [Layer 3-backend] After docker compose up,
   confirm `curl http://localhost:5001/api/sentiment` returns available:true 
   with 6 symbols + market.
4. [Layer 3-frontend] Confirm the new tab is visible at http://localhost:4000 and data renders.
   Also verify: confidence:low dimming, available:false fallback.
5. With a yesterday history file present, confirm score_delta is populated.

For each step, report success/failure and, on failure, the cause and proposed fix.
```

---

## 7. Safety Guardrails · Design Principles (Non-Negotiable)

These principles were agreed upon in prior analysis and must be consistently reflected throughout the code.

| Principle | Meaning in code |
|-----------|-----------------|
| **Sentiment is supplemental, price is primary** | Never present the Sentiment tab/badges as trade instructions. Disclaimer required. Never replace stop-loss/target price values with sentiment. |
| **Categorical only, no fake precision** | `sentiment_score` derived from category. Prompt blocks Grok from producing percentages. |
| **Low confidence → downgrade** | `confidence: low` is treated as neutral by consumers + visually dimmed. |
| **Fail silently, never fake** | Failed collection symbols: skip+log. Fetch failure: `available:false`. Never invent blank fields. |
| **Layer independence** | A failure in one layer must not kill another. Timeout/try-except at all boundaries. |
| **Secrets in env vars** | Never bake tokens/paths into code or images. Use docker-compose env, cron environment. |
| **Moderate collection frequency** | 1-2 times per day. Per-minute polling is excessive for a supplemental indicator. |

---

## 8. Work Order Summary (Claude Code Execution Checklist)

```
[ ] 2.   Create schema.json + data repo README + example latest.json       (Layer 2 contract)
[ ] 3.   Write collect_sentiment.py (hermes -z call, parse/validate/git push)  (Layer 1 collect)
[ ] 3-4. Cron registration guide + absolute path/PATH fix                   (Layer 1 automation)
[ ] 5-1. sentiment_service.py + /api/sentiment + schemas + compose env     (Layer 3 backend)
[ ] 5-2. useSentiment + SentimentTab + page.tsx/types changes              (Layer 3 frontend)
[ ] 6.   5-step end-to-end verification                                    (Integration)
[ ] 7.   Self-check that safety guardrails are reflected throughout        (Quality)
```

> **Starting point:** Build Layer 2 (schema) first. Only when the data contract is fixed can Layers 1 and 3 work toward the same format independently. Then fill the repo with real data via Layer 1 (collection), and finally attach Layer 3 (consumption) to verify end-to-end.
