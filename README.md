> 한국어 문서: [README.ko.md](./README.ko.md)

# market-sentiment-data

**Layer 2 — shared data repository** for SniperBoard's AI-powered market intelligence pipeline.

A server cron job runs six collectors daily, querying Grok via Hermes and fetching data from the SniperBoard backend and external APIs. Results are committed to this repository as standard JSON. Any consuming program — including SniperBoard — only needs the raw GitHub URL.

---

## Repository Structure

```
market-sentiment-data/
├── monitor/
│   ├── health_check.py              # Health monitor — runs every 2h, macOS alert on failure
│   └── health_check.log            # Monitor log
├── README.md                        # This document (English)
├── README.ko.md                     # Korean version
├── PROJECT_CONTEXT.md               # Architecture & code reference (English)
├── PROJECT_CONTEXT.ko.md            # Architecture & code reference (Korean)
├── CLAUDE.md                        # Claude Code instructions
├── schema.json                      # Data contract (JSON Schema draft-07, v2.0)
│
├── collect/
│   ├── collect_sentiment.py         # Collector 1: Social sentiment (TIER1 individual + TIER2 batch)
│   ├── collect_brief.py             # Collector 2: AI Daily Brief
│   ├── collect_earnings.py          # Collector 3: Earnings Intelligence
│   ├── collect_macro_insight.py     # Collector 4: Macro Insight
│   ├── collect_morning_briefing.py  # Collector 5: Morning Briefing (2-stage Grok pipeline, global_context)
│   ├── collect_prediction.py        # Collector 6: Prediction Market (Kalshi FOMC 확률, no Grok)
│   ├── probe_mention_volume.py      # Symbol selection probe — mention volume scanner (169 candidates)
│   ├── price_context.py             # Neutral price-context fetcher (for sentiment)
│   ├── git_utils.py                 # Shared git commit/push helper
│   ├── test_collect_sentiment.py
│   ├── test_collect_brief.py
│   ├── test_collect_brief_context.py
│   └── test_price_context.py
│
├── sentiment/
│   ├── latest.json                  # Sentiment snapshot — always current
│   ├── sentiment.log                # Cron log
│   ├── history/
│   │   ├── YYYY-MM-DD_pre_open.json    # Pre-US-market snapshot (09:00–17:59 UTC)
│   │   └── YYYY-MM-DD_post_close.json  # Post-US-market snapshot (18:00+ UTC)
│   └── probe/
│       ├── latest.json              # Latest probe result (always overwritten)
│       ├── YYYY-MM-DD_HHmm.json     # Per-run archive
│       └── probe_run.log
│
├── brief/
│   ├── latest.json                  # AI Daily Brief — always current
│   ├── brief.log                    # Cron log
│   └── history/
│       └── YYYY-MM-DD_<slot>.json
│
├── briefing/
│   ├── latest.json                  # Morning Briefing (schema_version 1.1) — always current
│   ├── briefing.log                 # Cron log
│   └── history/
│       └── YYYY-MM-DD.json
│
├── earnings/
│   ├── latest.json                  # Earnings Intelligence — always current
│   ├── earnings.log                 # Cron log
│   └── history/
│       └── YYYY-MM-DD.json
│
├── macro/
│   ├── latest.json                  # Macro Insight — always current
│   ├── macro.log                    # Cron log
│   └── history/
│       └── YYYY-MM-DD_<slot>.json
│
├── prediction/
│   ├── latest.json                  # Prediction Market (Kalshi FOMC) — always current
│   ├── prediction.log               # Cron log
│   └── history/
│       └── YYYY-MM-DD_<slot>.json
│
└── docs/                            # Design specs and plans
```

---

## The Five Collectors

### 1. Social Sentiment (`collect/collect_sentiment.py`)

Runs **twice daily** (pre_open and post_close slots). Symbols are split into two tiers:

1. Fetches **neutral price context** from SniperBoard (volatility magnitude, volume ratio, 52-week position — direction removed)
2. Injects context into a Grok prompt as observational cues only (contamination firewall: no directional words allowed)
3. Queries Grok via `hermes -z`; parses and validates JSON response
4. Computes **divergence** (price direction vs. sentiment sign) after Grok responds
5. Computes **composite_score** (−2.0 ~ +2.0) weighting confidence, bot suspicion, mention volume, divergence, and trend

**TIER1 — Large-cap / Big Tech (12 symbols): individual deep analysis, twice daily (pre_open + post_close)**
TSM, NVDA, META, TSLA, PLTR, MU, CRWD, AMZN, MSFT, AAPL, GOOGL, SPCX

**TIER2 — Momentum / Theme plays (10 symbols): batch analysis, once daily (post_close only)**
RKLB, CEG, VST, ALAB, OKLO, APP, ANET, NVO, QBTS, SOFI

Each symbol entry includes a `"tier": 1|2` field. TIER2 entries omit `price_context` (batch mode).

**Output: `sentiment/latest.json` and `sentiment/history/YYYY-MM-DD_<slot>.json`**

### 2. AI Daily Brief (`collect/collect_brief.py`)

Runs after the sentiment collector. Combines:
- **Technical context** from SniperBoard API (Risk Regime, Distribution Days, Stage2 scores, RS scores per symbol)
- **Social sentiment** from `latest.json`

Sends a combined prompt to Grok → returns a market brief + per-symbol briefs (setup_quality A+/A/B/C/D, action_bias buy/hold/watch/avoid, bilingual analysis text).

Also captures a **context snapshot** at generation time (for transparency in SniperBoard's Brief panel).

**Output: `brief/latest.json` and `brief/history/YYYY-MM-DD_<slot>.json`**

### 3. Earnings Intelligence (`collect/collect_earnings.py`)

Fetches earnings data via **yfinance** (calendar + earnings history) for all TIER1 symbols. Sends tiered data to Grok for risk interpretation:

- **Imminent** (≤7 days): event risk management zone
- **Approaching** (8–21 days): position planning zone
- **Watching** (22–30 days): early awareness zone

Features calendar → `earnings_dates`/`earnings_estimate` fallback, numeric/date validation, partial-results support (no crash on single-symbol failure), and `--dry-run` mode.

**Output: `earnings/latest.json` and `earnings/history/YYYY-MM-DD.json`**

### 4. Macro Insight (`collect/collect_macro_insight.py`)

Fetches 21 macro asset data points from SniperBoard's `/api/macro` (VIX, SPY, QQQ, rates, commodities, sector ETFs). Sends to Grok for group-level AI interpretation.

Returns structured insight with overall summary, key bullets (signal → market meaning format), and per-group text (volatility, breadth, credit, rates, commodities, sectors).

**Output: `macro/latest.json` and `macro/history/YYYY-MM-DD_<slot>.json`**

### 5. Morning Briefing (`collect/collect_morning_briefing.py`)

Runs once daily (KST 07:30). Uses a **2-stage Grok pipeline:**

1. **Stage 1:** Fetches top-3 global macro/geopolitical issues (trade/tariff, geopolitical, central bank, AI regulation) via Grok live web search within 48-hour window
2. **Stage 2:** Generates comprehensive morning briefing combining global context with watchlist sentiment

Returns `global_context` section with issue descriptions and market impacts, plus bilingual briefing text and key themes.

**Output: `briefing/latest.json` and `briefing/history/YYYY-MM-DD.json`** (schema_version 1.1)

### 6. Prediction Market (`collect/collect_prediction.py`)

Runs **twice daily** (KST 05:45 and 21:45). **No Grok** — pure probability data from [Kalshi](https://kalshi.com).

Fetches the next FOMC meeting's rate decision probabilities from Kalshi's prediction market:
- Automatically finds the nearest open FOMC event
- Maps market tickers to outcomes: `no_change`, `cut_25bps`, `cut_50bps`, `hike_25bps`
- Stores raw `yes_ask` price as probability (0.00~1.00)

**Requires:** `KALSHI_API_KEY` environment variable.

**Output: `prediction/latest.json` and `prediction/history/YYYY-MM-DD_<slot>.json`**

```json
{
  "schema_version": "1.0",
  "source": "kalshi",
  "next_fomc": {
    "event_ticker": "FOMC-26JUL29",
    "meeting_date": "2026-07-29",
    "probabilities": { "no_change": 0.72, "cut_25bps": 0.23, "cut_50bps": 0.04, "hike_25bps": 0.01 },
    "dominant_outcome": "no_change",
    "dominant_probability": 0.72
  }
}
```

When no FOMC event is open (post-meeting gap period), `next_fomc` is `null`.

---

## Schema v2.0 Summary

See `schema.json` for full spec. Key enums:

| Field | Allowed values |
|-------|----------------|
| `sentiment` | `very_fearful` `fearful` `neutral` `optimistic` `euphoric` |
| `sentiment_score` | integer −2 ~ +2 |
| `trend_vs_yesterday` | `cooling` `stable` `heating` |
| `mention_volume` | `low` `normal` `elevated` `surging` |
| `confidence` | `high` `med` `low` |
| `slot` | `pre_open` `post_close` |
| `divergence` | `none` `aligned` `bullish_divergence` `bearish_divergence` |
| `composite_score` | float −2.0 ~ +2.0 |
| `intraday_shift` | `cooling` `stable` `heating` (null for pre_open) |

**Bilingual text fields (v2.0):** All AI-generated text uses `_en`/`_ko` suffix pairs:
- `key_reason_en` / `key_reason_ko`
- `top_news.headline_en` / `top_news.headline_ko`
- `top_news.summary_en` / `top_news.summary_ko`
- Brief: `summary_en/ko`, `watch_points_en/ko`, `brief_en/ko`, `key_risk_en/ko`, `key_opportunity_en/ko`

**Consuming bilingual data:**
```python
locale = "en"  # or "ko"
reason = data["market"]["key_reason_en"] if locale == "en" else data["market"]["key_reason_ko"]

# Graceful fallback for v1.x data (no _en/_ko fields):
def get_field(obj, field, locale):
    en_val = obj.get(f"{field}_en")
    ko_val = obj.get(f"{field}_ko")
    fallback = obj.get(field)
    return (en_val or fallback or "") if locale == "en" else (ko_val or fallback or "")
```

> **Schema version history:** 1.0 base | 1.1 price_context+divergence | 1.2 slot+intraday_shift | 1.3 composite_score | 1.4 top_news | **2.0 bilingual _en/_ko fields (current)**

---

## Consuming from Other Programs

### Public repo (no auth required)

```bash
# Latest sentiment snapshot
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/sentiment/latest.json

# Latest AI Daily Brief
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/brief/latest.json

# Latest Earnings Intelligence
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/earnings/latest.json

# Latest Macro Insight
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/macro/latest.json

# Historical snapshot
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/sentiment/history/2026-05-30_post_close.json
```

### Private repo (PAT token required)

```bash
export SENTIMENT_DATA_TOKEN="github_pat_xxxx"

curl -H "Authorization: token $SENTIMENT_DATA_TOKEN" \
     https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/sentiment/latest.json
```

```python
import os, requests
resp = requests.get(
    "https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/sentiment/latest.json",
    headers={"Authorization": f"token {os.environ['SENTIMENT_DATA_TOKEN']}"},
    timeout=10,
)
data = resp.json()
```

> **Never hardcode tokens in source code.** Inject via docker-compose env or cron environment.

---

## Running the Pipeline

```bash
# 1. Social sentiment (runs twice daily)
python -m collect.collect_sentiment

# 2. AI Daily Brief (runs after sentiment)
python -m collect.collect_brief

# 3. Earnings Intelligence (runs once daily)
python -m collect.collect_earnings

# 4. Macro Insight (runs after sentiment)
python -m collect.collect_macro_insight

# 5. Morning Briefing (runs once daily, KST 07:30)
python -m collect.collect_morning_briefing

# Dry-run (earnings only, no git push)
python -m collect.collect_earnings --dry-run

# Probe: data-driven symbol selection (one-shot)
PROBE_BATCH_SIZE=5 HERMES_TIMEOUT=240 python3 -m collect.probe_mention_volume
```

**Required environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SENTIMENT_REPO_PATH` | script directory | Local path of this repo clone |
| `HERMES_CMD` | `/Users/jerry/.local/bin/hermes` | Absolute path to hermes binary |
| `HERMES_PROVIDER` | `""` | Hermes provider (e.g. `grok-oauth`) |
| `HERMES_TIMEOUT` | `120` | Per-call timeout in seconds |
| `HERMES_TIMEOUT_GLOBAL` | `90` | Timeout for global context fetch (Collector 5, stage 1) |
| `HERMES_RETRY` | `1` | Retry count on timeout |
| `SNIPERBOARD_API_BASE` | `http://localhost:5001` | SniperBoard backend URL |
| `SENTIMENT_SLOT` | auto-detect | Override slot: `pre_open` or `post_close` |

**Production crontab (Mac Mini, KST = UTC+9). `cd` prefix is mandatory — without it, log file paths fail silently and the script never runs:**

```bash
# sentiment: 05:30, 22:30 KST (twice daily)
30 5,22 * * * cd /Users/jerry/dev/market-sentiment-data && GIT_SSH_COMMAND="ssh -F /Users/jerry/.ssh/config -o StrictHostKeyChecking=no" PYTHONPATH=/Users/jerry/dev/market-sentiment-data HERMES_TIMEOUT=300 /opt/homebrew/bin/python3 -m collect.collect_sentiment >> sentiment/sentiment.log 2>&1

# brief + macro: 06:00/22:00 and 06:15/22:15 KST (twice daily)
00 6,22 * * * cd /Users/jerry/dev/market-sentiment-data && ... /opt/homebrew/bin/python3 -m collect.collect_brief >> brief/brief.log 2>&1
15 6,22 * * * cd /Users/jerry/dev/market-sentiment-data && ... /opt/homebrew/bin/python3 -m collect.collect_macro_insight >> macro/macro.log 2>&1

# earnings + briefing + auto_improve: 06:30/06:45/07:15 KST (once daily)
30 6 * * * cd /Users/jerry/dev/market-sentiment-data && ... /opt/homebrew/bin/python3 -m collect.collect_earnings >> earnings/earnings.log 2>&1
45 6 * * * cd /Users/jerry/dev/market-sentiment-data && ... /opt/homebrew/bin/python3 -m collect.collect_morning_briefing >> briefing/briefing.log 2>&1
15 7 * * * cd /Users/jerry/dev/market-sentiment-data && ... /opt/homebrew/bin/python3 -m collect.auto_improve >> briefing/auto_improve.log 2>&1

# health monitor: every 2 hours
0 */2 * * * cd /Users/jerry/dev/market-sentiment-data && /opt/homebrew/bin/python3 monitor/health_check.py >> monitor/health_check.log 2>&1
```

See `PROJECT_CONTEXT.md` Section 12 for complete crontab with all environment variables.

---

## Health Monitor

`monitor/health_check.py` — runs every 2 hours via crontab. Checks 13 categories (data freshness, data quality, cron execution, log errors, git/GitHub, Hermes, Docker, 9 API endpoints, frontend, signal DB, APScheduler, disk/network). Sends a macOS native notification on FAIL.

```bash
# Run manually
cd /Users/jerry/dev/market-sentiment-data && python3 monitor/health_check.py

# View log
tail -f monitor/health_check.log
```

---

## Tests

```bash
# Run all tests
python -m pytest collect/ -v

# Specific modules
python -m pytest collect/test_collect_sentiment.py -v
python -m pytest collect/test_collect_brief.py -v
python -m pytest collect/test_price_context.py -v
python -m pytest collect/test_collect_brief_context.py -v
```

48 tests passing as of Phase 5 (yf-accuracy-harden plan complete).

---

## Safety Principles

| Principle | Implementation |
|-----------|---------------|
| **Contamination firewall** | Price direction is never passed to Grok. Only magnitude, volume ratio, and key-level position are injected. Mechanical assert guard on every generated prompt. |
| **Categorical only, no fake precision** | `sentiment_score` is deterministically derived from `sentiment` enum. Grok never returns raw percentages. |
| **Fail silently, never fake** | Failed symbols: skip + log. Fetch failure: `available: false`. No invented placeholder values. |
| **Low confidence → downgrade** | `confidence: low` signals neutral treatment on consumer side; visually dimmed in SniperBoard. |
| **Layer independence** | A failure in one layer does not kill another. Timeout + try/except at all network boundaries. |
| **Secrets in env vars** | No tokens or paths hardcoded. Docker-compose env or cron environment only. |
