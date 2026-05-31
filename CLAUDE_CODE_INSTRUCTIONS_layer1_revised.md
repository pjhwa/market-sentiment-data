> 한국어 문서: [CLAUDE_CODE_INSTRUCTIONS_layer1_revised.ko.md](./CLAUDE_CODE_INSTRUCTIONS_layer1_revised.ko.md)

# Claude Code Instructions — Layer 1 Revised (Price-Context-Enriched Collector)

> **This document replaces and extends the "3. Layer 1 — Collection Script" section of the existing `CLAUDE_CODE_INSTRUCTIONS_sentiment.md`.**
> Layer 2 (schema) and Layer 3 (SniperBoard consumer) follow the existing instructions, but must reflect the schema additions specified here (`price_context`, `divergence`).
> The data repository is confirmed: **`https://github.com/pjhwa/market-sentiment-data`**

---

## 0. What Changed (Revision Summary)

The previous collector passed only the ticker symbol to Grok and asked for sentiment "blind." The revised version **first fetches neutral price context from the SniperBoard backend API before collecting**, using it to narrow Grok's search scope and help detect sarcasm. Then, **after** receiving the sentiment, it compares against the price direction to compute divergence.

```
[Before]  hermes -z "Tell me TSLA sentiment"  →  JSON

[After]
  ① SniperBoard API fetch ──→ Extract neutral volatility/volume/position cues
                                (Direction and judgment REMOVED!)
  ② hermes -z "TSLA sentiment. Note: unusually large move today / volume Nx avg" ──→ JSON
  ③ After receiving sentiment, compare against SniperBoard price direction ──→ compute divergence
  ④ Attach price_context + divergence → build latest.json → push
```

---

## 1. Most Important Principle — The Contamination Firewall (READ FIRST)

The success of this revision depends on a single rule. **Violating it turns the sentiment data into a shadow of price, making it analytically worthless.**

> ### ⛔ Absolute Rule
> **Price information is used ONLY to guide "where Grok looks", never to tell it "what to feel".**

### Allowed in Grok prompt (neutral observational cues)
- **Magnitude** of price move: "There was an unusually large price move today" (no direction)
- **Volume**: "Today's volume was Nx the recent average"
- **Position**: "Near the 52-week high" / "Near a key price level" (position only, no judgment about breakout/breakdown)

### Never allowed in Grok prompt (direction or conclusions)
- ❌ "went up / fell / surged / crashed"
- ❌ "bullish signal appeared / buy signal / Stage 2 score high / Risk Regime RISK_ON"
- ❌ Any buy/sell/hold judgment from SniperBoard
- ❌ RSI values, EMA alignment, or any indicator that implies direction

**Why:** Giving direction lets Grok infer the answer without actually reading X posts — "it went up so sentiment must be positive." That makes sentiment derived from price, destroying its only analytical value: independence from price. Think of it like telling a detective "investigate this time window" (a clue) vs. "this person is the killer" (a conclusion that contaminates the investigation).

> **Claude Code self-check:** After writing the prompt-builder function, add an `assert` or unit test verifying the generated prompt string contains none of these direction words: `up/down/올랐/떨어/급등/급락/bullish/bearish/buy/sell/strong`. This guard is the mechanical guarantee of the contamination firewall.

---

## 2. Data to Fetch from SniperBoard (After Removing Direction)

SniperBoard already has the "neutral volatility" information we need — no new calculations required. Read from existing endpoints, but **discard direction fields and use only magnitude, position, and volume**.

| Endpoint | Extract (neutral) | Discard (direction/judgment) |
|----------|------------------|------------------------------|
| `GET /api/daily?symbol=` | ATR14, today's price range (absolute value / ATR multiple), 52-week high distance (distance only) | Stage2 score, market_structure, EMA alignment, signals |
| `GET /api/ohlcv?symbol=&tf=` | Recent bar volume ÷ vol_avg20 (multiple) | 6 signal booleans, RSI direction |
| `GET /api/macro` (market-wide) | ^VIX level (volatility environment label: low/normal/high) | SPY/QQQ direction |

**Derived neutral cues (`price_context` object):**

```json
{
  "volatility": "normal",        // "calm" | "normal" | "elevated" | "extreme"
                                  // = today's price range ÷ ATR14 (no direction, size only)
  "volume_ratio": 2.3,           // recent volume ÷ vol_avg20
  "near_key_level": "near_52w_high", // "none" | "near_52w_high" | "near_52w_low"
                                     // determined by distance only (±3%), no breakout/breakdown judgment
  "abnormal_move": true          // true if |today's move| > 1.5 × ATR14 (direction-agnostic)
}
```

> **Concrete implementation of direction removal:** Always treat today's price range as an **absolute value**. Use `abs(close - open) / atr14`. Never include sign (+/−) in `price_context`. For `near_key_level`, say "near the high" only — never "broke out" or "failed to break" (those imply direction).

### Claude Code Prompt — Price Context Fetcher

```
Write a module that extracts only neutral price context from the SniperBoard backend.

File: collect/price_context.py (inside the collector directory)

Function fetch_price_context(symbol) -> dict:
- Use env var SNIPERBOARD_API_BASE (e.g. http://localhost:5000).
- Call GET /api/daily?symbol= and GET /api/ohlcv?symbol=&tf=5m (timeout 10s, try/except).
- Compute and return only:
    volatility: abs(today's range)/atr14 → calm(<0.5)/normal(<1.0)/elevated(<1.5)/extreme(>=1.5)
    volume_ratio: recent bar volume / vol_avg20 (1 decimal place)
    near_key_level: "near_52w_high" or "near_52w_low" if within ±3%, otherwise "none"
    abnormal_move: abs(today's move) > 1.5*atr14
- ⛔ NEVER return: price direction, sign, Stage2 score, signals, RSI, EMA alignment, regime.
- On API failure, return all fields as null + available:false (collection must proceed without context).

Function fetch_market_context() -> dict:
- Read only ^VIX from GET /api/macro and return vix_level: low(<16)/normal(<22)/high(>=22).
- Ignore all other directional information.

After writing, add a unit test that verifies the returned dict, when stringified,
contains none of these direction words: up/down/bull/bear/올랐/떨어/급등/급락.
```

---

## 3. Revised Grok Prompt (Context Injection, Direction Removed)

Insert price context as **neutral cues only**. Grok still reads actual X posts to judge sentiment, but with a narrower, more accurate frame.

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
  "key_reason_en": "one short sentence in English",
  "key_reason_ko": "one short sentence in Korean",
  "bot_suspected": ["yes","no","unclear"],
  "confidence": ["high","med","low"]
}

Rules:
- Determine sentiment ONLY from real posts, never inferred from the price context.
- No invented percentages. Categorical enums only.
- Thin/noisy sample → confidence "low".
- Output raw JSON only.
```

> **Conditional injection:** If `abnormal_move` is false, omit the "UNUSUALLY LARGE price move today" line (don't inject false context). If `near_key_level` is `none`, use "not near any key level". If `price_context` is `available:false`, omit the entire CONTEXT block and ask Grok blind — don't stop collection just because context is unavailable.

> **Why give size but not direction (re-emphasis):** "Large move + 3x volume" narrows *what posts Grok finds* (improves search quality). But the moment you say "it went up," Grok can infer the answer without reading any posts (destroys independence). That single difference is the entire point of this revision.

---

## 4. After Sentiment Collection — Divergence Calculation (No Contamination Risk)

Divergence is not passed to Grok. It is computed by the script **after receiving sentiment**, by comparing against the actual price direction from SniperBoard. At this stage, price **direction** may be used — Grok's judgment is already done, so there is nothing left to contaminate.

```
Divergence calculation (post-collection):
  price_dir  = SniperBoard today's close direction (up / down / flat)  ← direction OK here
  senti_dir  = sentiment_score sign (positive / negative / neutral)

  if price_dir == up   and senti_dir == negative → "bearish_divergence"  (price↑ sentiment↓)
  if price_dir == down and senti_dir == positive → "bullish_divergence"  (price↓ sentiment↑)
  else → "aligned" or "none"
```

This `divergence` field is the most powerful signal in the combined matrix — it marks the point where price and the crowd diverge, which can be an early sign of trend reversal. Flagging it at collection time means the consumer side (SniperBoard tab) can use it immediately.

> **Separation of direction usage:** Section 3 (prompt) forbids direction; Section 4 (post-processing) allows it. Keep these clearly separated in code. The `price_context` fetcher does not return direction, but add a separate function `fetch_close_direction(symbol)` for divergence calculation — and ensure its result flows **only** into Section 4 post-processing. It must never leak into the Section 3 prompt builder.

---

## 5. Schema Additional Fields (Layer 2 Update)

Add two fields to the per-symbol object. Update the existing `schema.json`.

```json
{
  "symbol": "TSLA",
  "as_of": "2026-05-21T14:30:00Z",
  "sentiment": "fearful",
  "sentiment_score": -1,
  "trend_vs_yesterday": "heating",
  "mention_volume": "surging",
  "key_reason_en": "Widespread sell sentiment driven by recall concerns",
  "key_reason_ko": "리콜 우려로 매도 심리 확산",
  "bot_suspected": "no",
  "confidence": "high",
  "source": "grok-oauth via hermes",

  "price_context": {                    // ★ NEW: neutral cues used during collection (for audit/replay)
    "volatility": "extreme",
    "volume_ratio": 3.1,
    "near_key_level": "none",
    "abnormal_move": true
  },
  "divergence": "bullish_divergence"    // ★ NEW: post-processing calculation result
                                        // "aligned" | "none" | "bullish_divergence" | "bearish_divergence"
}
```

> Why store `price_context` with the data: so we can later audit "what context was used when this sentiment was collected" and replay the divergence judgment. Consumers of this data can also make smarter use of it when they have context alongside the sentiment.

> **Compatibility:** Bump `schema_version` from "1.0" to "1.1". The consumer side (SniperBoard) must treat both fields as optional so legacy history files (without these fields) don't break.

---

## 6. Revised Collector Full Flow

```
1. WATCHLIST = ["TSLA","AAPL","NVDA","META","AMZN","GOOGL"]
2. market_context = fetch_market_context()          # VIX level only
3. For each symbol:
     a. ctx = fetch_price_context(symbol)            # neutral cues (no direction)
     b. prompt = build_prompt(symbol, ctx)           # must pass direction-word guard
     c. raw = hermes -z prompt --provider grok-oauth # headless call, timeout
     d. obj = parse_and_validate(raw)                # extract first{~last} + schema validation
        on failure: skip + log (no fake values)
     e. obj["price_context"] = ctx
     f. close_dir = fetch_close_direction(symbol)    # direction — post-processing only
        obj["divergence"] = compute_divergence(close_dir, obj["sentiment_score"])
4. Same pattern for market object (including extreme_flag)
5. Build latest.json + history/YYYY-MM-DD.json (schema_version "1.1")
6. git add/commit/push → pjhwa/market-sentiment-data
7. Output "N/7 symbols collected" + summary of divergence occurrences
```

### Claude Code Prompt — Revised Collector Integration

```
Revise (or rewrite) collect_sentiment.py. Changes:

1. Import price_context.py from Section 2; call fetch_price_context(symbol) 
   before each Grok call.
2. Write build_prompt(symbol, ctx) using the Section 3 revised prompt:
   - Omit "large move" sentence if abnormal_move=False.
   - Convert near_key_level to human-readable phrasing.
   - If ctx is available:false, omit the entire CONTEXT block (blind fallback).
   - ⛔ Direction-word guard (assert/check) must pass on generated prompt.
3. After parsing/validating Grok response, attach obj["price_context"]=ctx.
4. Fetch close direction separately with fetch_close_direction(symbol),
   compute divergence with compute_divergence(), attach to obj.
   — Ensure by code structure that this direction value cannot leak into build_prompt.
5. Build latest.json/history with schema_version "1.1" and both new fields.
6. Push target is pjhwa/market-sentiment-data. Auth via env var token.
7. Add "divergence detected: TSLA(bullish), ..." to summary output.

Preserve all existing principles: timeout, skip-on-failure, no fake values, env var config.
If SniperBoard API is down (context fetch fails), collection continues in blind mode.
```

---

## 7. Self-Review Checklist (Claude Code confirms after writing)

```
[ ] price_context fetcher never returns direction/sign/judgment (unit test passes)
[ ] build_prompt output contains no direction words (guard passes)
[ ] Confirm each branch: abnormal_move=False / near_key_level=none / ctx unavailable
[ ] fetch_close_direction result does not flow into the prompt builder (call graph confirmed)
[ ] divergence calculation correctly handles all 4 cases (aligned/none/bullish/bearish)
[ ] SniperBoard API down → collection continues in blind fallback mode
[ ] schema.json bumped to 1.1, new fields optional, old history files still compatible
[ ] Grok call timeout, parse-failure skip+log, no fake values
[ ] Tokens/paths env-var'd, push target = pjhwa/market-sentiment-data
[x] (earnings collector separate) collect/collect_earnings.py fallback/validation/partial/schema/structured logging (Phase 3 hardening complete as part of sniperboard yf-accuracy-harden plan): structured per-sym/raw logging, calendar fallback, numeric/date validation, jsonschema+light schema, partial+graceful usable on fail, --dry-run. 48 tests green (Phase 5). Cross-linkage: sniperboard earnings_service + /api/earnings meta age_minutes + FE badges consume the hardened output; pairs with sniperboard data_adapter/Stage2 accuracy work.
```

---

## 8. One-Line Summary

> Price context is a flashlight that guides Grok to **"where to look"**, not a script telling it **"what to feel"**. The *magnitude* of the move, the *volume*, and the *position* narrow the search and improve accuracy; but the *direction* of the move turns sentiment into an echo of price, making it worthless. Direction is used only after collection is complete, to calculate divergence.

**Phase 5 / yf-accuracy-harden linkage note (2026-05-24):** Earnings collector hardening (above) + sniperboard-side data_adapter centralization (single source of truth for yf prices, full delegation, adj prices in Stage2 long-term metrics) + endpoint meta (age_minutes) + minimal FE badges complete. 48 collect tests + 29 sniper tests green. Cross-repo: GitHub raw + services provide freshness transparency for AI Brief/Earnings in SniperBoard dashboard. All mandatory docs (incl this + _sentiment.md + sniper PROJECT_CONTEXT/README) updated. Plan + exec-8 verification passed.
