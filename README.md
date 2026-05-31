> 한국어 문서: [README.ko.md](./README.ko.md)

# market-sentiment-data

**Layer 2 — shared data repository** for SniperBoard's AI-powered market intelligence pipeline.

A server cron job runs four collectors daily, querying Grok via Hermes and fetching data from the SniperBoard backend. Results are committed to this repository as standard JSON. Any consuming program — including SniperBoard — only needs the raw GitHub URL.

---

## Repository Structure

```
market-sentiment-data/
├── README.md                        # This document (English)
├── README.ko.md                     # Korean version
├── PROJECT_CONTEXT.md               # Architecture & code reference (English)
├── PROJECT_CONTEXT.ko.md            # Architecture & code reference (Korean)
├── schema.json                      # Data contract (JSON Schema draft-07, v2.0)
│
├── collect_sentiment.py             # Collector 1: Social sentiment (main)
├── collect/
│   ├── collect_brief.py             # Collector 2: AI Daily Brief
│   ├── collect_earnings.py          # Collector 3: Earnings Intelligence
│   ├── collect_macro_insight.py     # Collector 4: Macro Insight
│   ├── price_context.py             # Neutral price-context fetcher (for sentiment)
│   └── git_utils.py                 # Shared git commit/push helper
│
├── latest.json                      # Sentiment snapshot — always current
├── history/
│   ├── YYYY-MM-DD_pre_open.json     # Pre-US-market snapshot (09:00–17:59 UTC)
│   └── YYYY-MM-DD_post_close.json   # Post-US-market snapshot (18:00+ UTC)
│
├── brief/
│   ├── latest.json                  # AI Daily Brief — always current
│   └── history/
│       └── YYYY-MM-DD_<slot>.json
│
├── earnings/
│   ├── latest.json                  # Earnings Intelligence — always current
│   └── history/
│       └── YYYY-MM-DD.json
│
└── macro/
    ├── latest.json                  # Macro Insight — always current
    └── history/
        └── YYYY-MM-DD_<slot>.json
```

---

## The Four Collectors

### 1. Social Sentiment (`collect_sentiment.py`)

Runs **twice daily** (pre_open and post_close slots). For each of 7 watchlist symbols + the broad US market:

1. Fetches **neutral price context** from SniperBoard (volatility magnitude, volume ratio, 52-week position — direction removed)
2. Injects context into a Grok prompt as observational cues only (contamination firewall: no directional words allowed)
3. Queries Grok via `hermes -z --provider grok-oauth`
4. Computes **divergence** (price direction vs. sentiment sign) after Grok responds
5. Computes **composite_score** (−2.0 ~ +2.0) weighting confidence, bot suspicion, mention volume, divergence, and trend

**Watchlist:** TSLA, AAPL, NVDA, META, AMZN, GOOGL, PLTR

**Output: `latest.json` and `history/YYYY-MM-DD_<slot>.json`**

### 2. AI Daily Brief (`collect/collect_brief.py`)

Runs after the sentiment collector. Combines:
- **Technical context** from SniperBoard API (Risk Regime, Distribution Days, Stage2 scores, RS scores per symbol)
- **Social sentiment** from `latest.json`

Sends a combined prompt to Grok → returns a market brief + per-symbol briefs (setup_quality A+/A/B/C/D, action_bias buy/hold/watch/avoid, bilingual analysis text).

Also captures a **context snapshot** at generation time (for transparency in SniperBoard's Brief panel).

**Output: `brief/latest.json` and `brief/history/YYYY-MM-DD_<slot>.json`**

### 3. Earnings Intelligence (`collect/collect_earnings.py`)

Fetches earnings data via **yfinance** (calendar + earnings history) for all 7 watchlist symbols. Sends tiered data to Grok for risk interpretation:

- **Imminent** (≤7 days): event risk management zone
- **Approaching** (8–21 days): position planning zone
- **Watching** (22–30 days): early awareness zone

Features calendar → `earnings_dates`/`earnings_estimate` fallback, numeric/date validation, partial-results support (no crash on single-symbol failure), and `--dry-run` mode.

**Output: `earnings/latest.json` and `earnings/history/YYYY-MM-DD.json`**

### 4. Macro Insight (`collect/collect_macro_insight.py`)

Fetches 21 macro asset data points from SniperBoard's `/api/macro` (VIX, SPY, QQQ, rates, commodities, sector ETFs). Sends to Grok for group-level AI interpretation.

Returns structured insight with overall summary, key bullets (signal → market meaning format), and per-group text (volatility, breadth, credit, rates, commodities, sectors).

**Output: `macro/latest.json` and `macro/history/YYYY-MM-DD_<slot>.json`**

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
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/latest.json

# Latest AI Daily Brief
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/brief/latest.json

# Latest Earnings Intelligence
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/earnings/latest.json

# Latest Macro Insight
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/macro/latest.json

# Historical snapshot
curl https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/history/2026-05-30_post_close.json
```

### Private repo (PAT token required)

```bash
export SENTIMENT_DATA_TOKEN="github_pat_xxxx"

curl -H "Authorization: token $SENTIMENT_DATA_TOKEN" \
     https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/latest.json
```

```python
import os, requests
resp = requests.get(
    "https://raw.githubusercontent.com/pjhwa/market-sentiment-data/main/latest.json",
    headers={"Authorization": f"token {os.environ['SENTIMENT_DATA_TOKEN']}"},
    timeout=10,
)
data = resp.json()
```

> **Never hardcode tokens in source code.** Inject via docker-compose env or cron environment.

---

## Running the Pipeline

```bash
# 1. Social sentiment (runs at 13:00 and 21:00 UTC)
python collect_sentiment.py

# 2. AI Daily Brief (runs after sentiment)
python -m collect.collect_brief

# 3. Earnings Intelligence (runs once daily)
python -m collect.collect_earnings

# 4. Macro Insight (runs after sentiment)
python -m collect.collect_macro_insight

# Dry-run (earnings only, no git push)
python -m collect.collect_earnings --dry-run
```

**Required environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SENTIMENT_REPO_PATH` | script directory | Local path of this repo clone |
| `HERMES_CMD` | `/Users/jerry/.local/bin/hermes` | Absolute path to hermes binary |
| `HERMES_PROVIDER` | `""` | Hermes provider (e.g. `grok-oauth`) |
| `HERMES_TIMEOUT` | `120` | Per-call timeout in seconds |
| `HERMES_RETRY` | `1` | Retry count on timeout |
| `SNIPERBOARD_API_BASE` | `http://localhost:5001` | SniperBoard backend URL |
| `SENTIMENT_SLOT` | auto-detect | Override slot: `pre_open` or `post_close` |

**Cron example (server, UTC-based):**
```bash
# pre_open: 13:00 UTC (22:00 KST)
0 13 * * 1-5  cd ~/dev/market-sentiment-data && python collect_sentiment.py >> ~/sentiment.log 2>&1
0 13 * * 1-5  cd ~/dev/market-sentiment-data && python -m collect.collect_brief >> ~/brief.log 2>&1
0 13 * * 1-5  cd ~/dev/market-sentiment-data && python -m collect.collect_macro_insight >> ~/macro.log 2>&1

# post_close: 21:00 UTC (06:00 KST)
0 21 * * 1-5  cd ~/dev/market-sentiment-data && python collect_sentiment.py >> ~/sentiment.log 2>&1
0 21 * * 1-5  cd ~/dev/market-sentiment-data && python -m collect.collect_brief >> ~/brief.log 2>&1
0 21 * * 1-5  cd ~/dev/market-sentiment-data && python -m collect.collect_macro_insight >> ~/macro.log 2>&1

# earnings: once daily at 14:00 UTC
0 14 * * 1-5  cd ~/dev/market-sentiment-data && python -m collect.collect_earnings >> ~/earnings.log 2>&1
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
