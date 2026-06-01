> 한국어 문서: [PROJECT_CONTEXT.ko.md](./PROJECT_CONTEXT.ko.md)

# market-sentiment-data — Project Context

<!-- AUTO-GENERATED: 2026-06-01 (TIER1/TIER2 재편) -->

Architecture and code reference for Claude Code and developers. Read this before modifying any collector, schema, or data structure.

---

## 1. Architecture Overview

Social sentiment data is separated into **3 layers**. This separation is the core design principle — the collection actor (Hermes/server) and the consumption actor (SniperBoard) are loosely coupled through a GitHub repository as shared storage.

```
┌─────────────────────────┐     ┌──────────────────────────┐     ┌──────────────────────┐
│  Layer 1: Collect        │     │  Layer 2: Storage         │     │  Layer 3: Consume    │
│  (server cron)           │     │  (this GitHub repo)       │     │  (SniperBoard etc.)  │
│                          │     │                           │     │                      │
│  4 collectors:           │ git │  sentiment/latest.json    │ raw │  FastAPI services    │
│  · collect_sentiment.py  │push │  sentiment/history/       │fetch│  /api/sentiment      │
│  · collect_brief.py      │────▶│  brief/                   │────▶│  /api/brief          │
│  · collect_earnings.py   │     │  earnings/                │     │  /api/earnings       │
│  · collect_macro_insight │     │  macro/                   │     │  /api/macro-insight  │
│                          │     │  schema.json              │     │                      │
└─────────────────────────┘     └──────────────────────────┘     └──────────────────────┘
```

**Why this separation:**
- **Decoupled collection and consumption** — SniperBoard down ≠ collection stops. Each layer can be independently fixed and restarted.
- **Reusability** — Standard JSON on GitHub; any future dashboard or notebook reads from `raw.githubusercontent.com` without changes.
- **History preservation** — Daily snapshots accumulate; SniperBoard reads trend changes from data rather than computing them.
- **Cost/speed separation** — Slow LLM calls are handled in advance by cron; SniperBoard returns stored JSON immediately.

---

## 2. Repository File Map

```
market-sentiment-data/
├── collect/
│   ├── __init__.py
│   ├── collect_sentiment.py      # Collector 1 — python -m collect.collect_sentiment
│   ├── collect_brief.py          # Collector 2 — python -m collect.collect_brief
│   ├── collect_earnings.py       # Collector 3 — python -m collect.collect_earnings
│   ├── collect_macro_insight.py  # Collector 4 — python -m collect.collect_macro_insight
│   ├── probe_mention_volume.py   # Tier 선별용 1회성 멘션 볼륨 프로브 (169개 후보)
│   ├── price_context.py          # Neutral price-context fetcher (used by Collector 1)
│   ├── git_utils.py              # commit_and_push() shared helper
│   ├── test_collect_sentiment.py
│   ├── test_collect_brief.py
│   ├── test_collect_brief_context.py
│   └── test_price_context.py
├── sentiment/
│   ├── latest.json               # Sentiment: always-current snapshot
│   ├── sentiment.log             # Cron log for collect_sentiment
│   ├── history/YYYY-MM-DD_<slot>.json
│   └── probe/                    # 멘션 볼륨 프로브 결과
│       ├── latest.json           # 최신 프로브 결과 (항상 덮어씀)
│       └── YYYY-MM-DD_HHmm.json  # 실행별 누적 보관
├── brief/
│   ├── latest.json               # AI Daily Brief: always-current
│   ├── brief.log                 # Cron log for collect_brief
│   └── history/YYYY-MM-DD_<slot>.json
├── earnings/
│   ├── latest.json               # Earnings Intelligence: always-current
│   ├── earnings.log              # Cron log for collect_earnings
│   └── history/YYYY-MM-DD.json
├── macro/
│   ├── latest.json               # Macro Insight: always-current
│   ├── macro.log                 # Cron log for collect_macro_insight
│   └── history/YYYY-MM-DD_<slot>.json
├── schema.json                   # JSON Schema draft-07 v2.0 (sentiment only)
├── README.md / README.ko.md
└── PROJECT_CONTEXT.md / PROJECT_CONTEXT.ko.md
```

---

## 3. Environment Variables

All config is injected via environment variables. Never hardcode paths or tokens.

| Variable | Default | Used by |
|----------|---------|---------|
| `SENTIMENT_REPO_PATH` | script directory | all collectors |
| `HERMES_CMD` | 자동탐색 (`shutil.which` → `~/.local/bin` → `/opt/homebrew/bin` → `/usr/local/bin`) | all collectors |
| `HERMES_PROVIDER` | `""` (empty = no `--provider` flag) | all collectors |
| `HERMES_TIMEOUT` | `120` | all collectors |
| `HERMES_RETRY` | `1` | all collectors |
| `SNIPERBOARD_API_BASE` | `http://localhost:5001` | collectors 1, 2, 4 |
| `SENTIMENT_SLOT` | auto-detect by UTC hour | collectors 1, 2, 4 |

**Slot detection logic** (overridable via `SENTIMENT_SLOT`):
- UTC 09:00–17:59 → `pre_open`
- UTC 18:00–08:59 → `post_close`

---

## 4. Probe Tool — Mention Volume Scanner (`collect/probe_mention_volume.py`)

One-shot script for data-driven Tier1/Tier2 symbol selection before expanding the watchlist to 20 symbols. Scans 169 candidate symbols across 25 sectors by querying Grok for 7 signal-quality dimensions.

**Run:** `PROBE_BATCH_SIZE=5 HERMES_TIMEOUT=240 python3 -m collect.probe_mention_volume`

**Output:** `sentiment/probe/latest.json` — ranked results + ready-to-paste `TIER1_WATCHLIST` / `TIER2_WATCHLIST` code blocks.

**Scoring (probe_score 0~100):**

| Dimension | Max pts | Rationale |
|-----------|---------|-----------|
| `mention_volume` | 40 | No data = no signal |
| `consistency` | 20 | Event-only stocks have lower daily collection value |
| `retail_dominance` | 15 | Retail posts drive actionable sentiment |
| `sentiment_clarity` | 15 | Clear mood → reliable signal |
| `bot_ratio` | 10 | High bots dilute signal quality |

Final score multiplied by `confidence` (1.0 / 0.85 / 0.6).

**Design doc:** `docs/superpowers/specs/2026-06-01-probe-mention-volume-design.md`

---

## 5. Collector 1 — Social Sentiment (`collect/collect_sentiment.py`)

### Overview

The main sentiment collector. Runs twice daily. For TIER1 symbols individually + the broad US market, and TIER2 symbols as a batch (post_close only):
1. Fetch neutral price context (no direction) from SniperBoard
2. Build Grok prompt with context injected as observational cues
3. Call Grok via `hermes -z`; parse and validate JSON response
4. Compute divergence (post-collection, after Grok is done)
5. Compute composite_score
6. Write `sentiment/latest.json` + `sentiment/history/YYYY-MM-DD_<slot>.json`
7. `git commit + push`

**TIER1 — 빅테크/대형주 (11종목, 개별 심층 분석, 하루 2회):**
`TSM, NVDA, META, TSLA, PLTR, MU, CRWD, AMZN, MSFT, AAPL, GOOGL`

**TIER2 — 모멘텀/테마주 (10종목, 배치 묶음 분석, post_close 전용):**
`RKLB, CEG, VST, ALAB, OKLO, APP, ANET, NVO, QBTS, SOFI`

**Collection modes:**
- **TIER1 (개별):** `pre_open` + `post_close` 슬롯 모두 실행. 종목별 개별 Grok 호출. 가격 맥락(price_context) 포함.
- **TIER2 (배치):** `post_close` 슬롯에만 실행. `build_tier2_batch_prompt()`로 10종목 단일 Grok 호출. price_context 생략.
- 각 엔트리에 `"tier": 1` 또는 `"tier": 2` 필드 저장.

### The Contamination Firewall (Most Important Principle)

> **Absolute rule: Price information is used ONLY to guide "where Grok looks", never to tell it "what to feel".**

**Allowed in Grok prompt (neutral observational cues):**
- Magnitude of price move: "unusually large price move today" (no direction)
- Volume: "today's volume was Nx the recent average"
- Position: "near its 52-week high" (position only, no breakout/breakdown judgment)

**Never allowed in Grok prompt:**
- ❌ "went up / fell / surged / crashed" (direction)
- ❌ "bullish signal / Stage 2 score high / RISK_ON" (conclusions)
- ❌ RSI values, EMA alignment (direction-implying indicators)

**Why:** Giving direction lets Grok infer the answer without reading X posts — sentiment becomes an echo of price, destroying its analytical value (independence from price).

**Mechanical enforcement:** `build_prompt()` runs a regex assert against the generated prompt string. Any direction word triggers `AssertionError`. `price_context.py` also runs `_assert_no_direction()` on every returned dict.

### `collect/price_context.py`

Three functions:

| Function | Purpose |
|----------|---------|
| `fetch_price_context(symbol)` | Returns volatility / volume_ratio / near_key_level / abnormal_move from SniperBoard `/api/daily`. **No direction.** On failure: `available: False`. |
| `fetch_market_context()` | Returns VIX level (low/normal/high) only from `/api/macro`. |
| `fetch_close_direction(symbol)` | Returns `up`/`down`/`flat`. **Post-processing ONLY.** Never flows into prompt builder. |

### Divergence Calculation (post-collection)

Computed after Grok responds. `fetch_close_direction()` result may be used here.

```
price_dir == "up"  and sentiment_score < 0  →  "bearish_divergence"
price_dir == "down" and sentiment_score > 0 →  "bullish_divergence"
otherwise                                   →  "aligned" or "none"
```

### composite_score Calculation

Weighted combination of all signals into −2.0 ~ +2.0:

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

### Key Functions in `collect_sentiment.py`

| Function | Purpose |
|----------|---------|
| `detect_slot(now)` | Returns `pre_open` or `post_close` |
| `build_prompt(symbol, company, ctx)` | Builds Grok prompt with neutral context; asserts no direction words |
| `call_hermes(prompt)` | Subprocess call with timeout + retry |
| `extract_json(text)` | Extracts first `{`…last `}` from LLM output |
| `extract_json_array(text)` | Extracts `[…]` array from LLM output (TIER2 배치 응답용) |
| `validate_symbol_fields(data, symbol)` | Validates enums and required fields |
| `validate_top_news(data)` | Validates `top_news` optional struct (v2.0 _en/_ko required) |
| `compute_divergence(price_dir, score)` | Divergence logic (post-processing only) |
| `compute_intraday_shift(prev, curr)` | Compares pre_open vs post_close scores |
| `load_pre_open_scores(path)` | Reads earlier pre_open file for intraday_shift |
| `compute_symbol_composite(...)` | composite_score for symbols |
| `compute_market_composite(...)` | composite_score for market object |
| `build_symbol_entry(..., tier)` | Assembles final per-symbol JSON object; `tier` 필드 포함 |
| `build_tier2_batch_prompt(watchlist)` | TIER2 10종목 배치 프롬프트 생성 |
| `build_market_entry(...)` | Assembles final market JSON object |
| `git_commit_push(...)` | Delegates to `collect/git_utils.commit_and_push()` |

---

## 6. Collector 2 — AI Daily Brief (`collect/collect_brief.py`)

### Overview

Runs after the sentiment collector. Combines technical and social data → Grok → structured brief.

**Data sources:**
- `GET /api/regime` → Risk Regime label + total score + components
- `GET /api/distribution-days` → SPY/QQQ distribution day counts
- `GET /api/watchlist` → Watchlist-level data
- `GET /api/daily?symbol=` (per symbol) → Stage2 score, RS score, market_structure, signals
- `latest.json` → composite_score, sentiment, key_reason per symbol

**Grok output schema:**
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

**setup_quality criteria:**
- `A+`: Stage2 6–7, social optimistic+, GC above/breakout, RS 70+
- `A`: Stage2 5–6, social neutral+, UPTREND structure
- `B`: Stage2 4–5, mixed signals
- `C`: Stage2 ≤3, social fearful or bear_flag
- `D`: Stage2 ≤2 or deepening downtrend

**Context snapshot (Phase 1):** `build_brief_context_snapshot()` captures the technical/regime/sentiment state at generation time. Embedded as `context` in the output JSON. Surfaced in SniperBoard's Brief panel for transparency.

---

## 7. Collector 3 — Earnings Intelligence (`collect/collect_earnings.py`)

### Overview

Fetches earnings data via yfinance and generates Grok-based risk interpretation.

**Data flow:**
1. `yf.Ticker(sym).calendar` → upcoming earnings date + EPS estimate (primary source)
2. Fallback: `earnings_dates` / `earnings_estimate` if calendar fails
3. `yf.Ticker(sym).earnings_history` → recent quarterly EPS actuals (up to 8 quarters)
4. Filter: only symbols within 30-day window (EPS consensus not yet formed beyond this)
5. Tier assignment: imminent (≤7d) / approaching (8–21d) / watching (22–30d)
6. Grok call with tiered data → risk interpretation per symbol
7. Write `earnings/latest.json` + history file

**Grok output schema (v2.0 — bilingual, per symbol):**
```json
{
  "upcoming_earnings": [{
    "symbol": "TSLA",
    "earnings_date": "2026-07-22",
    "days_until": 52,
    "eps_estimate": 0.62,
    "revenue_estimate_b": 27.4,
    "historical_beat_rate": 0.75,
    "ai_summary_en": "Strong delivery growth expected; FSD ramp key upside catalyst.",
    "ai_summary_ko": "강한 인도 성장 기대; FSD 확대가 핵심 상방 촉매.",
    "risk_level": "med",
    "action_note_en": "Hold through earnings; size down if already extended.",
    "action_note_ko": "실적 통과 시 보유 유지; 이미 과확장 시 비중 축소."
  }],
  "recent_results": [{
    "symbol": "AAPL",
    "report_date": "2026-05-01",
    "eps_actual": 1.65,
    "eps_estimate": 1.58,
    "surprise_pct": 4.4,
    "ai_reaction_en": "Beat on EPS and revenue; services growth re-accelerating.",
    "ai_reaction_ko": "EPS·매출 모두 상회; 서비스 부문 성장 재가속."
  }]
}
```

**v1.x backward-compat fields** (`ai_summary`, `action_note`, `ai_reaction`) are no longer generated but remain accepted by SniperBoard via `tField()` fallback.

**Hardening features:**
- calendar → `earnings_dates`/`earnings_estimate` fallback
- Numeric and date validation (0–30 day bounds, EPS sanity checks)
- Structured per-symbol and raw-shape logging
- `partial` flag + graceful output on single-symbol failure (no crash, no `sys.exit`)
- `--dry-run` flag: runs all collection but skips git push
- jsonschema + lightweight inline schema validation before writing

---

## 8. Collector 4 — Macro Insight (`collect/collect_macro_insight.py`)

### Overview

Fetches SniperBoard's macro data and generates group-level AI interpretation.

**Data source:** `GET /api/macro` → 21 macro assets:
- Volatility: VIX
- Breadth: SPY, QQQ, IWM, SMH
- Credit: HYG, LQD
- Rates: TLT, IEF, ^TNX
- Commodities: GLD, SLV, USO, DBA
- Sectors: XLF, XLE, XLK, XLV, XLU, XLB

**Grok output schema (v2.0 — bilingual):**
```json
{
  "overall": {
    "summary_en": "one-sentence market summary (English, ≤60 chars)",
    "summary_ko": "한 문장 시장 요약 (Korean, ≤40 chars)",
    "bullets_en": ["signal → meaning", "signal → meaning", "signal → meaning"],
    "bullets_ko": ["신호 → 시장 의미", "신호 → 시장 의미", "신호 → 시장 의미"]
  },
  "groups": {
    "volatility":  { "text_en": "...", "text_ko": "..." },
    "breadth":     { "text_en": "...", "text_ko": "..." },
    "credit":      { "text_en": "...", "text_ko": "..." },
    "rates":       { "text_en": "...", "text_ko": "..." },
    "commodities": { "text_en": "...", "text_ko": "..." },
    "sectors":     { "text_en": "...", "text_ko": "..." }
  }
}
```

Bullet format rule: "핵심 신호 → 시장 의미" (signal → market meaning), ≤25 chars each. Raw state listing is prohibited.

**v1.x backward-compat fields** (`summary`, `bullets[]`, `text`) are no longer generated but remain accepted by SniperBoard via `tField()` fallback.

---

## 9. Data Schema Reference (v2.0)

### `sentiment/latest.json` top-level structure

```json
{
  "generated_at": "2026-05-31T13:00:00Z",
  "schema_version": "2.0",
  "slot": "pre_open",
  "market": { ... },
  "symbols": [ ... ]
}
```

### Per-symbol object (v2.0 complete)

```json
{
  "symbol": "TSLA",
  "tier": 1,
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

### Market object additional fields

- `extreme_flag`: `none` | `extreme_fear` | `extreme_greed`

### Schema version history

| Version | Key addition |
|---------|-------------|
| 1.0 | Base schema |
| 1.1 | `price_context`, `divergence` |
| 1.2 | `slot`, `intraday_shift` |
| 1.3 | `composite_score` |
| 1.4 | `top_news` |
| 2.0 | All AI text fields use `_en`/`_ko` suffix pairs |

---

## 10. Layer 3 — SniperBoard Consumer

SniperBoard consumes this repository via its backend services. The consumer must treat all v1.x-added fields as optional to maintain backward compatibility with history files.

**SniperBoard endpoints consuming this repo:**

| SniperBoard endpoint | Source file | Cache TTL |
|---------------------|------------|-----------|
| `GET /api/sentiment` | `sentiment/latest.json` | 5–10 min |
| `GET /api/sentiment/history` | `sentiment/history/*.json` | 5 min |
| `GET /api/brief` | `brief/latest.json` | 5–10 min |
| `GET /api/earnings` | `earnings/latest.json` | 60 min |
| `GET /api/macro-insight` | `macro/latest.json` | 5–10 min |

**Fetch pattern (private repo):**
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

**Backward-compatible field access:**
```python
def get_field(obj: dict, field: str, locale: str) -> str:
    en_val = obj.get(f"{field}_en")
    ko_val = obj.get(f"{field}_ko")
    fallback = obj.get(field, "")
    return (en_val or fallback) if locale == "en" else (ko_val or fallback)
```

---

## 11. Cron Schedule

```bash
# ─── pre_open (06:00 KST / 22:00 KST) ──────────────────────────────────────
# All collectors run with PYTHONPATH and python -m module notation
00 6,22 * * * cd ~/dev/market-sentiment-data && PYTHONPATH=~/dev/market-sentiment-data HERMES_TIMEOUT=300 python3 -m collect.collect_sentiment >> sentiment/sentiment.log 2>&1
30 6,22 * * * cd ~/dev/market-sentiment-data && PYTHONPATH=~/dev/market-sentiment-data HERMES_TIMEOUT=300 python3 -m collect.collect_brief >> brief/brief.log 2>&1
45 6,22 * * * cd ~/dev/market-sentiment-data && PYTHONPATH=~/dev/market-sentiment-data HERMES_TIMEOUT=300 python3 -m collect.collect_macro_insight >> macro/macro.log 2>&1

# ─── earnings (once daily, 07:00) ────────────────────────────────────────────
00 7 * * * cd ~/dev/market-sentiment-data && PYTHONPATH=~/dev/market-sentiment-data python3 -m collect.collect_earnings >> earnings/earnings.log 2>&1
```

> **PATH note:** cron environments have minimal PATH. Use absolute paths to `python3` and `hermes`, or set `PATH` explicitly. All log files live inside their data directory (e.g. `sentiment/sentiment.log`).

---

## 12. Safety Guardrails (Non-Negotiable)

| Principle | Code implementation |
|-----------|---------------------|
| **Contamination firewall** | `build_prompt()` asserts no direction words. `price_context.py` runs `_assert_no_direction()` on every returned dict. `fetch_close_direction()` result never flows into prompt builder. |
| **Categorical only** | `sentiment_score` is always `SENTIMENT_SCORE_MAP[sentiment]`. Grok prompt explicitly bans percentages. |
| **Fail silently, never fake** | Per-symbol failure: `continue` (skip) + stderr log. Market failure: neutral placeholder. Network failure: `available: False`. |
| **Low confidence → downgrade** | `confidence: low` → `conf_mult = 0.5` in composite_score. Consumer dims it visually. |
| **Layer independence** | All inter-layer calls have explicit `timeout` + `try/except`. SniperBoard API down → blind mode (collection continues). |
| **Secrets in env vars** | `SENTIMENT_DATA_TOKEN`, `HERMES_CMD`, `SNIPERBOARD_API_BASE` all from environment. No hardcoded values. |
| **Supplemental framing** | Sentiment data is supplemental. SniperBoard displays a disclaimer. Never replaces price-based stop/target decisions. |

---

## 13. Testing

```bash
PYTHONPATH=/path/to/market-sentiment-data python -m pytest collect/ -v

# Key test files:
# collect/test_collect_sentiment.py   — prompt guard, divergence, composite_score, validation
# collect/test_price_context.py       — direction-word absence assertion, fallback behavior
# collect/test_collect_brief.py       — brief validation, context snapshot
# collect/test_collect_brief_context.py — context attribution structure
```

Tests are co-located in `collect/` and run with pytest. No external services required — mock SniperBoard API responses as needed.

---

## 14. Cross-Repo Linkage (SniperBoard)

- `sniperboard/backend/services/sentiment_service.py` — fetches `sentiment/latest.json` + `sentiment/history/`
- `sniperboard/backend/services/brief_service.py` — fetches `brief/latest.json`
- `sniperboard/backend/services/earnings_service.py` — fetches `earnings/latest.json` with 60-min cache; attaches `meta.age_minutes` to `/api/earnings` responses
- `sniperboard/backend/services/macro_insight_service.py` — fetches `macro/latest.json`
- `sniperboard/frontend/components/boards/SentimentBoard.tsx` — consumes `/api/sentiment`
- `sniperboard/frontend/components/boards/SentimentTrendChart.tsx` — historical chart
- SniperBoard `MACRO_SYMBOLS` uses English names matching this repo's macro asset list
