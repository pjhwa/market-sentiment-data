# SniperBoard yfinance Data Accuracy Hardening + Minimal Linkage Tie-ins Design

**Date**: 2026-05-25
**Status**: Approved via interactive brainstorming (user selected Approach B + minimal tie-ins)
**Scope**: Main: Eliminate latent inaccuracy risks in all yfinance-derived dashboard values (prices, indicators, Stage 2/RS/52w/entry, regime, DD, macro). Secondary: Minimal transparency features on the sniperboard ↔ market-sentiment-data flywheel for deeper investor trust/insight.
**Related Query Points**: 1 (verify match), 2 (find/fix), 3 (linkage analysis + insights)

---

## 1. Current State & Problem Analysis (from Deep Dive + Systematic Debugging)

### Repos Overview
- **sniperboard** (backend FastAPI + yfinance/pandas core; Next.js FE): Real-time signal dashboard (Livermore/O'Neil/Minervini). All numeric dashboard values (Market Overview cards, Intraday signals/RSI/EMA, Daily Stage2 0-7 + 52w + RS + RR calc + GC, Watchlist scores, Macro 21 syms, Regime 0-100 5-components, DD counts) originate from `backend/services/data_service.py` (yf.download) → `core/{signal_engine.py, regime_engine.py, distribution_day.py}` → `/api/*` → FE hooks/boards.
- **market-sentiment-data**: Data layer (cron collectors → GitHub raw JSON). Provides `latest.json` (sentiment), `brief/latest.json`, `earnings/latest.json`. **Bidirectional linkage**: collect_*.py fetches sniper `/api/regime|daily|watchlist` for neutral `price_context` before Grok prompt (anti-bias design); sniper `services/{brief,earnings,sentiment}_service.py` fetch GitHub raw (30-60min cache) for Overview AI/Earnings/SentimentBoard.

### Verification Results (Repro + Cross-Check)
- Ran full production pipeline (data_service.get_multi_daily/get_ohlcv + engines) against yf 1.3.0 in env (2026-sim data): all paths succeeded, no crashes/NaN propagation in key calcs.
- Key match: NVDA 52w high from Stage2 logic (raw iloc[-252:].max high) == yf.Ticker.info['fiftyTwoWeekHigh'] (236.54).
- Regime 75.1 CONSTRUCTIVE, SPY DD=6 DANGER, VIX~16.7, intraday rows/cols correct, macro 3mo data sane.
- **No current "wrong numbers"** vs yf source in tested paths/symbols (by construction for non-split cases).

### Root Causes Identified (Systematic Debugging Phases 1-3)
1. **yf version / MultiIndex fragility** (`data_service.py:24-38,60-84`): yf 1.3+ returns MultiIndex even for single-ticker (`(field, ticker)`) or group_by (`(ticker, field)`). Code's `get_level_values(0) + rename` + `levels[0]` checks works by luck for required cols but is brittle — future yf change or new symbol can silently drop data or produce wrong OHLCV → wrong signals/Stage2/regime/DD everywhere.
2. **No split adjustment for long-term metrics** (core issue for accuracy): `get_multi_daily(..., "2y")` (used by /daily, /watchlist, /regime) + `calculate_stage2_analysis` (252-window high/low, 63d RS ret, 20d ema200_slope, 20d pivot high for entry, pullback) + similar in regime/macro on **raw unadjusted** close/high. For any watchlist symbol with split in ~252d window (NVDA 2024-06-10 10:1 was borderline in past; future splits guaranteed), nominal prices discontinuous → **wrong 52w %, RS score, Stage2 total (0-7), entry/stop/target, ema slopes, breadth_narrow**. Currently not triggered in 2026 data but latent bug (violates "actual market data" fidelity). Intraday/ recent DD (25d) unaffected.
3. **Earnings collector brittleness** (`market-sentiment-data/collect/collect_earnings.py:42-100+`): Heavy `hasattr` / `isinstance` / col name fallbacks for `yf.Ticker.calendar` + `.earnings_history` (infamously unstable across yf versions). Can produce None/ wrong EPS dates/estimates/surprise → incomplete or misleading Earnings cards in sniper Overview/Daily. No schema validation before GitHub push.
4. **Secondary**: No explicit auto_adjust or version pin; no data freshness metadata on GitHub-fetched AI cards (investor can't tell if sentiment/brief/earnings is 10min or 2h stale); limited history for intraday (5d default); no automated cross-check vs yf.info ground truth in tests.

These are **not** random errors but architectural: yf treated as stable raw source without adaptation layer for its known quirks (splits, column evolution, calendar shape).

**Success Criteria** (for plan):
- All dashboard numbers for Mag7 + SPY/QQQ/RSP etc match yf "ground truth" (`.info` 52w, adjusted series, or manual calc) even after splits or yf upgrades.
- Pipeline never silently returns bad data (explicit errors or fallbacks + logs).
- Earnings data complete/valid for watchlist (or clear degradation).
- Minimal tie-in: investors see data age for AI cards (trust → deeper insight).
- Tests pass + new split-regression test; docs updated (sniper CLAUDE.md mandatory).
- No behavior change for current non-split data.

---

## 2. Recommended Architecture (Approach B)

### 2.1 Data Access Layer (New File + Updates)
- **Create** `backend/core/data_adapter.py` (or extend `services/base.py` + YFinanceDataService):
  - `get_ohlcv_intraday(symbol, tf="5m", period="5d")` → current logic + explicit `auto_adjust=False`, robust MultiIndex normalize (try both orientations, log yf version).
  - `get_daily(symbols: List[str], period="2y", adjusted=True)` → `yf.download(..., auto_adjust=adjusted, group_by='ticker')`, normalize columns to flat `open/high/low/close/volume/adj_close?`, return dict[sym, df]. For adjusted=True, high/low/close/volume are split/div adjusted (standard for %/levels/RS/52w); keep raw option for volume-sensitive (DD uses recent only).
  - Helper: `get_actions_aware_adjusted(df_raw, actions)` fallback if needed (rare).
  - `get_ticker_info(symbol)` thin wrapper for .info ground truth (52w, price) — used in tests only.
- Deprecate or proxy old module-level `get_ohlcv` / `get_multi_daily` (update 4 call sites in `api/endpoints.py`).
- Add `yf_version = yf.__version__` log on startup / first fetch.

**Why new adapter?** Clear boundary, testable isolation, future yf or provider swap easy. Follows "smaller focused files" principle.

### 2.2 Engine / Signal Updates (Minimal)
- `core/signal_engine.py:calculate_stage2_analysis` (and callers in endpoints/watchlist/daily):
  - If df has 'adj_close' or adjusted flag, prefer for: 52w high/low (use adj high/low or close for consistency), 63d stock/spy_ret, ema200_slope (on adj close), pullback (adj), pivot high for entry (adj high), breadth_narrow (adj close).
  - Fallback to raw if not present (backward compat during migration).
  - Add: `using_adjusted: bool` to returned dict for debugging.
- `regime_engine.py`, `distribution_day.py`: Minor — document "raw sufficient for recent windows"; optionally accept adjusted flag (DD volume impact low for 25d).
- No change to intraday signals (short window, no splits).

### 2.3 Earnings Path Hardening (Both Repos)
- `market-sentiment-data/collect/collect_earnings.py`:
  - Add structured logging (success/fail per sym, raw cal shape).
  - Fallback chain: calendar → earnings_dates → .earnings; validate dates in future 0-30d, eps numeric.
  - After build, `jsonschema.validate` against (extended) schema before write.
  - On partial fail: still write what we have + "partial" flag.
- `sniperboard/backend/services/earnings_service.py` + schema: expose `generated_at`, compute `age_minutes` in response.
- Similar light touch for brief/sentiment services (they already have generated_at).

### 2.4 Minimal Linkage Tie-in (Transparency = Insight)
- Backend: sentiment/brief/earnings endpoints always return `{"available": bool, "data": {...}, "meta": {"fetched_at": iso, "age_minutes": int, "source": "github-raw", "cache_ttl": 1800}}`.
- FE:
  - `app/types.ts`: extend responses with optional meta.
  - Small addition in `components/boards/OverviewBoard.tsx` (AI Insight + Earnings cards) and `SentimentBoard.tsx`: subtle badge `⏱ ${age}m ago` (gray if <30, warn if >90). Use existing `useBrief` etc staleTime.
- Effect: Investor sees "this Grok brief used 12min-old regime + daily context from sniper + sentiment" → understands the flywheel, trusts (or questions) the narrative → deeper insight without new heavy features.

### 2.5 Testing, Docs, Process
- **TDD**: New `backend/tests/test_data_adapter.py` (or extend test_signal_engine): mock yf or use real for NVDA/TSLA around known split dates; assert 52w/RS/entry match yf.info or manual adjusted calc; MultiIndex variants.
- Run full pytest + manual curl /api/daily?symbol=NVDA etc pre/post.
- **Docs (mandatory per CLAUDE.md in sniper)**: Update `PROJECT_CONTEXT.md` (sections 4,6 data flow), `README.md` (API note on adjusted, data freshness). Similar light update in market CLAUDE_CODE_INSTRUCTIONS_*.md.
- Git: small commits per component ("feat(data): robust MultiIndex + auto_adjust support").
- No prod break: feature flag or gradual (default adjusted=False first, then flip after tests).

**Out of Scope (YAGNI)**: Full provider abstraction, historical backtest UI, real-time push, more than 1-2 tie-in badges, option chain / gamma.

---

## 3. Files Changed (Exact)

**sniperboard (primary)**:
- backend/core/data_adapter.py (new, ~80 LOC)
- backend/services/data_service.py (thin wrappers or deprecate, update normalize)
- backend/core/signal_engine.py (use adj when present in 3-4 places)
- backend/api/endpoints.py (4 endpoints: switch to adapter for daily paths; add meta to 3 AI endpoints)
- backend/tests/test_data_adapter.py (new) + update existing
- frontend/app/types.ts (meta fields)
- frontend/components/boards/OverviewBoard.tsx + SentimentBoard.tsx (or shared StatCard) (badge, ~10 LOC)
- PROJECT_CONTEXT.md, README.md (update data flow, accuracy note)
- (optional) docs/ images or claude-code-brief.md

**market-sentiment-data**:
- collect/collect_earnings.py (logging + validation + fallback)
- (if schema change) schema.json minor
- CLAUDE_CODE_INSTRUCTIONS_*.md (light)

**No changes**: regime/distribution (doc only), intraday signals, most FE, docker, etc.

---

## 4. Risks, Trade-offs, Rollout

- **auto_adjust volume**: DD/regime use volume only on recent (low split prob). Mitigation: adapter provides raw_volume always; DD continues using volume (or raw).
- **Historical recompute**: Old cached? None (live yf). Watchlist/daily will see slightly different 52w/RS for split symbols post-deploy (improvement, not regression).
- **FE badge**: Optional — if UI polish concern, backend meta alone still valuable for API consumers.
- **yf 1.3 in prod?** Current docker likely older; adapter supports both.
- **Effort**: 4-6 focused days (TDD). High confidence from repro runs.
- **Rollback**: easy (adapter behind flag or revert 2 files).

---

## 5. Open Questions (Resolved in Plan)
- Exact adjusted high/low for entry pivot: use close_adj for all levels or only returns? (rec: consistent adj series for everything except pure volume).
- Badge text/location: final polish in impl.

---

## 6. Next Step
After this spec approved by user + self-review: invoke writing-plans skill → produce `docs/superpowers/plans/2026-05-25-sniperboard-yf-accuracy-harden-plan.md` with bite-sized TDD tasks, exact code diffs, test commands, commit messages, cross-repo coordination.

This delivers **provably accurate** yf values (closing point 1+2) + **visible trust** on the unique linkage (point 3) with minimal scope creep.

**Self-Review Notes (pre-commit)**: No TBDs, consistent terminology (adjusted vs raw), files exact, risks called out, YAGNI applied. Matches all prior approvals.

---

*Generated from interactive analysis of ~/dev/sniperboard + ~/dev/market-sentiment-data (2026-05).*
