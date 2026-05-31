> 한국어 문서: [CLAUDE.ko.md](./CLAUDE.ko.md)

# market-sentiment-data — Claude Instructions

## Required at Session Start

When starting a new session, always read these two files first:
1. `PROJECT_CONTEXT.md` — full collector architecture, schema, data flow, env vars, cron schedule
2. `README.md` — user-facing description of all 4 collectors and data structure

These two files give you an immediate understanding of the project without reading the entire codebase.

---

## Required After Code Changes

**Before ending any session where you modified code files, you must:**

1. Update `PROJECT_CONTEXT.md`
   - Reflect any changed collector logic, schema fields, data flow, or env vars
   - Update the "AUTO-GENERATED" date to today

2. Update `README.md`
   - Reflect any user-facing changes (new collectors, schema fields, cron schedule)

3. Include both files in the git commit

**Exception**: Test-only or comment-only changes may skip this.

---

## Key Project Entry Points

- **Collector 1**: `collect_sentiment.py` — social sentiment, contamination firewall, divergence, composite_score
- **Collector 2**: `collect/collect_brief.py` — AI Daily Brief (technical + social → Grok)
- **Collector 3**: `collect/collect_earnings.py` — Earnings Intelligence (yfinance + Grok)
- **Collector 4**: `collect/collect_macro_insight.py` — Macro Insight (SniperBoard `/api/macro` + Grok)
- **Price context**: `collect/price_context.py` — neutral price cues fetcher (no direction). `fetch_close_direction()` is post-processing only — never flows into prompt builder.
- **Git helper**: `collect/git_utils.py` — shared `commit_and_push()`
- **Schema**: `schema.json` — JSON Schema draft-07 v2.0 (sentiment data contract)

See `PROJECT_CONTEXT.md` for full architecture, schema reference, and cron schedule.

---

## Most Important Principle — Contamination Firewall

> **Price direction must never be passed to Grok. Only magnitude, volume ratio, and key-level position are allowed.**

- `price_context.py` returns neutral cues only — mechanical `_assert_no_direction()` on every dict
- `build_prompt()` in `collect_sentiment.py` asserts no direction words before every Grok call
- `fetch_close_direction()` result flows **only** into divergence post-processing, never into the prompt

Violating this rule makes sentiment data analytically worthless (it becomes an echo of price).

---

## Related Repository: sniperboard

This repository is consumed by SniperBoard: **`https://github.com/pjhwa/sniperboard`**

| Data type | Source file | SniperBoard service |
|-----------|-------------|---------------------|
| Social sentiment | `latest.json` / `history/` | `backend/services/sentiment_service.py` |
| AI Daily Brief | `brief/latest.json` | `backend/services/brief_service.py` |
| Earnings Intelligence | `earnings/latest.json` | `backend/services/earnings_service.py` |
| Macro Insight | `macro/latest.json` | `backend/services/macro_insight_service.py` |

- SniperBoard fetches via raw GitHub URL; token injected via `SENTIMENT_DATA_TOKEN` env var.
- **Schema version**: 2.0 — all AI text fields use `_en`/`_ko` suffix pairs.
- See `sniperboard/PROJECT_CONTEXT.md` for SniperBoard-side consumer implementation details.
