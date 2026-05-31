> 한국어 문서: [README.ko.md](./README.ko.md)

# market-sentiment-data

Layer 2 — **shared data repository** for SniperBoard's social sentiment pipeline.

Collects social sentiment data from X (Twitter) via Hermes + Grok running on a Mac mini cron job, stored in standard JSON format. Any consuming program — including SniperBoard — only needs the raw GitHub URL.

---

## Repository Structure

```
market-sentiment-data/
├── README.md                  # This document (English)
├── README.ko.md               # Korean version
├── schema.json                # Data contract (JSON Schema draft-07, v2.0)
├── latest.json                # Most recent snapshot — primary file consumers read
├── history/
│   ├── 2026-05-21_pre_open.json    # Pre-open slot (13:00 UTC)
│   ├── 2026-05-21_post_close.json  # Post-close slot (21:00 UTC)
│   └── ...
├── brief/
│   ├── latest.json             # AI Daily Brief latest snapshot
│   └── history/                # YYYY-MM-DD_<slot>.json
└── earnings/
    ├── latest.json             # Earnings Intelligence latest
    └── history/                # YYYY-MM-DD.json
```

- **`latest.json`**: Overwritten on every cron run. Always current.
- **`history/YYYY-MM-DD_pre_open.json`**: Pre-US-market snapshot (13:00 UTC).
- **`history/YYYY-MM-DD_post_close.json`**: Post-US-market snapshot (21:00 UTC). Includes `intraday_shift`.
- **`history/YYYY-MM-DD.json`**: Legacy pre-v1.2 files. Preserved for consumer fallback.

> **Schema version history:** 1.0 base | 1.1 price_context+divergence | 1.2 slot+intraday_shift | 1.3 composite_score | 1.4 top_news | **2.0 bilingual _en/_ko fields (current)**

---

## Consuming from Other Programs

### Public repo (no auth required)

```bash
# Get latest snapshot
curl https://raw.githubusercontent.com/<user>/market-sentiment-data/main/latest.json

# Get specific date history
curl https://raw.githubusercontent.com/<user>/market-sentiment-data/main/history/2026-05-21_post_close.json
```

### Private repo (PAT token required)

```bash
# Store token in environment
export SENTIMENT_DATA_TOKEN="github_pat_xxxx"

# Fetch latest.json
curl -H "Authorization: token $SENTIMENT_DATA_TOKEN" \
     https://raw.githubusercontent.com/<user>/market-sentiment-data/main/latest.json

# Python (requests)
import os, requests
resp = requests.get(
    "https://raw.githubusercontent.com/<user>/market-sentiment-data/main/latest.json",
    headers={"Authorization": f"token {os.environ['SENTIMENT_DATA_TOKEN']}"},
    timeout=10
)
data = resp.json()
```

> **Never hardcode tokens in source code or images.** Inject via docker-compose env or cron environment.

---

## Schema v2.0 Summary

See `schema.json` for full spec. Key enums:

| Field | Allowed values |
|-------|---------------|
| `sentiment` | `very_fearful` `fearful` `neutral` `optimistic` `euphoric` |
| `trend_vs_yesterday` | `cooling` `stable` `heating` |
| `mention_volume` | `low` `normal` `elevated` `surging` |
| `confidence` | `high` `med` `low` |
| `slot` | `pre_open` `post_close` |

**Bilingual text fields (v2.0):** All AI-generated human-readable text uses `_en`/`_ko` suffix pairs:
- `key_reason_en` / `key_reason_ko`
- `top_news.headline_en` / `top_news.headline_ko`
- `top_news.summary_en` / `top_news.summary_ko`
- Brief: `summary_en/ko`, `watch_points_en/ko`, `brief_en/ko`, `key_risk_en/ko`, `key_opportunity_en/ko`

**Consuming bilingual data:**
```python
# Select language based on user preference
locale = "en"  # or "ko"
reason = data["market"]["key_reason_en"] if locale == "en" else data["market"]["key_reason_ko"]
```

---

## Running the Pipeline

```bash
# Sentiment collection (runs at 13:00 and 21:00 UTC)
python collect_sentiment.py

# AI Daily Brief (runs after sentiment)
python -m collect.collect_brief

# Earnings Intelligence
python -m collect.collect_earnings

# Macro Insight
python -m collect.collect_macro_insight
```

---

## Tests

```bash
# Run all tests
python -m pytest collect/ -v

# Specific test files
python -m pytest collect/test_collect_sentiment.py -v
python -m pytest collect/test_collect_brief.py -v
```
