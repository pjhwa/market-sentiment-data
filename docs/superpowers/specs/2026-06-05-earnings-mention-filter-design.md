# Design: Earnings Mention Filter (14-Day Threshold)

**Date:** 2026-06-05  
**Repos:** market-sentiment-data  
**Files:** `collect/collect_brief.py`, `collect/collect_morning_briefing.py`

---

## Problem

Both briefing collectors instruct Grok to write "30일 이내 실적 발표 없음" for every stock that has no upcoming earnings. Since most TIER1/TIER2 stocks don't have earnings within 30 days at any given time, this phrase is repeated 10–20 times per briefing, diluting signal with meaningless noise.

Root cause is a 3-layer instruction pattern in both files:
1. Symbol data block includes `【실적=30일 이내 없음】` label
2. Binding rules say: "실적일 N/A → '30일 이내 없음'으로 처리"
3. Analysis instructions say: "N/A = write '30일 이내 실적 발표 없음'"

Grok faithfully follows all three layers and repeats the phrase for every stock.

---

## Decision

**Only mention earnings when within 14 days.** Earnings 14+ days away (or absent) are completely omitted from both the Grok context and output text.

- `already_reported_possible` stocks: always shown (hard constraint, regardless of threshold)
- 1–14 days away: shown in data block, Grok must mention in brief
- 15+ days away / no date: omitted from data block, Grok must not mention

---

## Changes

### `collect_brief.py` — 3 locations

**1. `_format_symbol_block` (line ~406)**

```python
# Before
elif earn_date:
    earn_str = f"【실적={earn_date} ({days_earn}일후) / EPS추정=${eps_est}】"
else:
    earn_str = "【실적=30일 이내 없음】"

# After
elif earn_date and days_earn is not None and days_earn <= 14:
    earn_str = f"【실적={earn_date} ({days_earn}일후) / EPS추정=${eps_est}】"
else:
    earn_str = ""  # omit entirely
```

In the `lines.append(...)` block, only include `earn_str` line when non-empty.

**2. `_format_authoritative_table` binding rule (line ~299)**

```
# Before
[4] N/A이면 추측 금지. 실적일 N/A → '30일 이내 없음'. 절대 '곧'/'다음 주' 금지.

# After
[4] N/A이면 추측 금지. 실적일 N/A이거나 14일 초과 → brief_en/ko에서 실적 언급 금지. 절대 '곧'/'다음 주' 금지.
```

**3. `build_brief_prompt` analysis/SELF-CHECK (line ~533, ~557)**

- SELF-CHECK: change "Earnings dates: exact from table, not approximated?" → "Earnings: only mentioned if ≤14 days away; omit entirely if absent or >14 days"
- `brief_en`/`brief_ko` description: remove "N/A = write no earnings within 30 days" pattern; add "If no earnings within 14 days, do NOT mention earnings at all"

---

### `collect_morning_briefing.py` — 3 locations

**4. `_format_symbol_block` (line ~381)**

Same logic as above: only include earnings block when `days_earn is not None and days_earn <= 14`. Use `earn_str = ""` otherwise, and conditionally include in output.

**5. Binding rule (line ~325)**

```
# Before
[4] 값이 N/A이면 해당 수치를 추측하지 말 것. 실적일이 N/A면 '30일 이내 없음'으로 처리.

# After
[4] 값이 N/A이면 해당 수치를 추측하지 말 것. 실적일 N/A이거나 14일 초과 → analysis에서 실적 언급 금지.
```

**6. Analysis instructions (lines ~851, ~933–934, ~947)**

- Line 851: `"N/A" = write "30일 이내 실적 발표 없음"` → `"N/A or >14 days = do NOT mention earnings — omit the topic entirely"`
- Lines 933–934: Remove "no earnings within 30 days" from analysis_en/ko description
- Line 947: `earnings_alert` field: clarify it should only list stocks with actual upcoming dates or `⚠이미발표됨`; if no such stocks exist, use empty string or omit

---

## What Does NOT Change

- `already_reported_possible` handling — unchanged, always displayed prominently
- Earnings data still fetched and stored in the snapshot JSON (for historical record)
- `earnings_alert_en/ko` fields still exist in morning briefing JSON schema, but only populated when there are actual events
- 30-day earnings calendar block (`_format_earnings_block`) in morning briefing — unchanged (it's a separate calendar section, not per-stock prose)

---

## Expected Outcome

A briefing where "실적" appears only 0–3 times total (for stocks genuinely close to earnings), not 11–21 times as boilerplate noise.
