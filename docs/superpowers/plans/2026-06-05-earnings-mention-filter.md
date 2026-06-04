# Earnings Mention Filter (14-Day Threshold) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove meaningless "30일 이내 실적 발표 없음" repetition from briefings by only mentioning earnings when within 14 days of an actual event.

**Architecture:** Three-layer fix per file — (1) remove the "없음" label from the Grok data block, (2) update the binding rule, (3) update analysis instructions. Two files: `collect/collect_brief.py` and `collect/collect_morning_briefing.py`. `already_reported_possible` stocks are always shown regardless of threshold.

**Tech Stack:** Python 3, unittest, pytest

---

### Task 1: Tests for `collect_brief._format_symbol_block` earnings filtering

**Files:**
- Modify: `collect/test_collect_brief.py`

These tests verify that `_format_symbol_block` only includes an earnings line when `days_until_earnings <= 14`, and omits it otherwise.

- [ ] **Step 1: Add imports and helper at top of `collect/test_collect_brief.py`**

Add after existing imports:

```python
from collect.collect_brief import _format_symbol_block, WATCHLIST
```

- [ ] **Step 2: Add test class**

Append to `collect/test_collect_brief.py`:

```python
class TestFormatSymbolBlockEarningsFilter(unittest.TestCase):
    def _make_tech(self, sym, days_until, earn_date="2026-06-10", already=False):
        """Minimal tech dict for _format_symbol_block."""
        d = {
            "price": 100.0,
            "change_pct_prev_day": 0.5,
            "high_52w_price": 120.0,
            "price_date": "2026-06-04",
            "stage2_score": 5,
            "rs_score": 60.0,
            "market_structure": "UPTREND",
            "monthly_phase": "ADVANCING",
            "ema200_slope": 0.001,
            "pct_from_52w_high": -5.0,
            "pullback_pct": 3.0,
            "pct_vs_entry": 2.0,
            "entry": 98.0,
            "rsi14": 55.0,
            "ema200": 90.0,
            "ema50": 95.0,
            "ema21": 98.0,
            "atr14": 2.5,
            "price_above_emas": True,
            "ema200_rising": True,
            "volume_contracting": False,
            "near_52w_high": False,
            "bear_flag": False,
            "rsi_divergence_bullish": False,
            "rsi_divergence_bearish": False,
            "gc_above": False,
            "gc_breakout": False,
            "gc_retest": False,
            "earnings_date": earn_date if days_until is not None else None,
            "days_until_earnings": days_until,
            "eps_estimate": 1.23,
            "already_reported_possible": already,
        }
        return {
            "symbol_detail": {sym: d},
            "prepost": {},
        }

    def test_earnings_within_14_days_included(self):
        tech = self._make_tech("NVDA", days_until=7)
        result = _format_symbol_block(tech, {})
        self.assertIn("실적=", result)
        self.assertIn("2026-06-10", result)

    def test_earnings_exactly_14_days_included(self):
        tech = self._make_tech("NVDA", days_until=14)
        result = _format_symbol_block(tech, {})
        self.assertIn("실적=", result)

    def test_earnings_15_days_omitted(self):
        tech = self._make_tech("NVDA", days_until=15)
        result = _format_symbol_block(tech, {})
        self.assertNotIn("실적=", result)
        self.assertNotIn("30일 이내 없음", result)

    def test_no_earnings_date_omitted(self):
        tech = self._make_tech("NVDA", days_until=None, earn_date=None)
        result = _format_symbol_block(tech, {})
        self.assertNotIn("실적=", result)
        self.assertNotIn("30일 이내 없음", result)

    def test_already_reported_always_shown(self):
        tech = self._make_tech("NVDA", days_until=0, earn_date="2026-06-05", already=True)
        result = _format_symbol_block(tech, {})
        self.assertIn("이미발표됨", result)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_brief.py::TestFormatSymbolBlockEarningsFilter -v
```

Expected: FAIL — `ImportError` or assertion failures (current code always outputs "30일 이내 없음").

---

### Task 2: Fix `collect_brief._format_symbol_block` earnings logic

**Files:**
- Modify: `collect/collect_brief.py:406-444`

- [ ] **Step 1: Update `earn_str` logic in `_format_symbol_block`**

Find lines 406-420 in `collect/collect_brief.py`:

```python
        # 실적 정보
        earn_date = d.get("earnings_date")
        days_earn = d.get("days_until_earnings")
        eps_est   = d.get("eps_estimate")
        already   = d.get("already_reported_possible", False)
        if earn_date and already:
            earn_str = (
                f"【⚠이미발표됨({earn_date}) / EPS추정=${eps_est}】\n"
                f"  ⛔ HARD RULE: brief_en/ko에 'beat','miss','exceeded','상회','하회',"
                f"'split','분할' 절대 금지. 실제 결과는 이 데이터에 없음.\n"
                f"  ✅ 허용: '오늘 장 마감 후 발표됨 — EPS 추정 ${eps_est}, 실제 결과 확인 필요'"
            )
        elif earn_date:
            earn_str = f"【실적={earn_date} ({days_earn}일후) / EPS추정=${eps_est}】"
        else:
            earn_str = "【실적=30일 이내 없음】"
```

Replace with:

```python
        # 실적 정보 (14일 이내만 표시)
        earn_date = d.get("earnings_date")
        days_earn = d.get("days_until_earnings")
        eps_est   = d.get("eps_estimate")
        already   = d.get("already_reported_possible", False)
        if earn_date and already:
            earn_str = (
                f"【⚠이미발표됨({earn_date}) / EPS추정=${eps_est}】\n"
                f"  ⛔ HARD RULE: brief_en/ko에 'beat','miss','exceeded','상회','하회',"
                f"'split','분할' 절대 금지. 실제 결과는 이 데이터에 없음.\n"
                f"  ✅ 허용: '오늘 장 마감 후 발표됨 — EPS 추정 ${eps_est}, 실제 결과 확인 필요'"
            )
        elif earn_date and days_earn is not None and days_earn <= 14:
            earn_str = f"【실적={earn_date} ({days_earn}일후) / EPS추정=${eps_est}】"
        else:
            earn_str = ""
```

- [ ] **Step 2: Conditionally include `earn_str` in the output block**

Find the `lines.append(...)` call in `_format_symbol_block` (around line 430). It currently always includes `f"  {earn_str}\n"`. Change the append to skip when empty:

```python
        earn_line = f"  {earn_str}\n" if earn_str else ""
        lines.append(
            f"{sym} ({company})\n"
            f"  Stage2={d['stage2_score']}/7  RS={d['rs_score']}  "
            f"구조={d['market_structure']}  월봉={d['monthly_phase']}\n"
            f"  [전일종가=${d['price']:,.2f}]  【전일등락={d.get('change_pct_prev_day',0):+.2f}%】  "
            f"52주고점=${d['high_52w_price']:,.2f}({d['pct_from_52w_high']:.1f}%)  "
            f"진입목표대비={vs_entry}  눌림={d['pullback_pct']:.1f}%\n"
            f"  [{pp_str}]\n"
            f"  가격앵커: RSI14={rsi_str}  EMA21={ema21_str}  EMA50={ema50_str}  EMA200={ema200_str}  ATR14={atr_str}\n"
            f"{earn_line}"
            f"  기술신호: {', '.join(signals)}\n"
            f"  소셜심리: {sent.get('sentiment','N/A')} (점수={sent.get('composite_score','N/A')})\n"
            f"  소셜근거: {sent.get('key_reason_en') or sent.get('key_reason','N/A')}"
        )
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_brief.py::TestFormatSymbolBlockEarningsFilter -v
```

Expected: All 5 tests PASS.

---

### Task 3: Fix `collect_brief` binding rule and prompt instructions

**Files:**
- Modify: `collect/collect_brief.py:299` (binding rule)
- Modify: `collect/collect_brief.py:533-540` (SELF-CHECK)
- Modify: `collect/collect_brief.py:557` (brief_en description)

- [ ] **Step 1: Update binding rule in `_format_authoritative_table`**

Find line 299:
```python
    rows.append("  [4] N/A이면 추측 금지. 실적일 N/A → '30일 이내 없음'. 절대 '곧'/'다음 주' 금지.")
```

Replace with:
```python
    rows.append("  [4] N/A이면 추측 금지. 실적일 N/A이거나 14일 초과 → brief_en/ko에서 실적 언급 금지(완전 생략). 절대 '곧'/'다음 주' 금지.")
```

- [ ] **Step 2: Update SELF-CHECK in `build_brief_prompt`**

Find (around line 540):
```python
  □ Earnings dates: exact from table, not approximated?
```

Replace with:
```python
  □ Earnings: mentioned ONLY if ≤14 days away? If absent or >14 days, completely omitted from brief_en/ko?
```

- [ ] **Step 3: Update `brief_en`/`brief_ko` description in the JSON schema block**

Find (around line 557):
```python
      "brief_en": "2-3 sentences: (1) exact price from table + key technical signal, (2) setup strength or vulnerability, (3) social catalyst or risk. NO invented prices.",
      "brief_ko": "2-3문장: (1) 테이블 정확한 가격 + 핵심 기술 신호, (2) 셋업 강도 또는 취약점, (3) 소셜 촉매 또는 리스크.",
```

Replace with:
```python
      "brief_en": "2-3 sentences: (1) exact price from table + key technical signal, (2) setup strength or vulnerability, (3) social catalyst or risk. Mention earnings ONLY if ≤14 days away. NO invented prices.",
      "brief_ko": "2-3문장: (1) 테이블 정확한 가격 + 핵심 기술 신호, (2) 셋업 강도 또는 취약점, (3) 소셜 촉매 또는 리스크. 실적은 14일 이내일 때만 언급, 그 외 완전 생략.",
```

- [ ] **Step 4: Run existing tests to confirm no regression**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_brief.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/collect_brief.py collect/test_collect_brief.py
git commit -m "feat: only mention earnings in brief when within 14 days"
```

---

### Task 4: Tests for `collect_morning_briefing._format_symbol_block` earnings filtering

**Files:**
- Modify: `collect/test_collect_morning_briefing.py`

- [ ] **Step 1: Add import**

Add after existing imports in `collect/test_collect_morning_briefing.py`:

```python
from collect.collect_morning_briefing import _format_symbol_block as _mb_format_symbol_block
```

- [ ] **Step 2: Add test class**

Append to `collect/test_collect_morning_briefing.py`:

```python
class TestMorningBriefingEarningsFilter(unittest.TestCase):
    def _make_data(self, sym, days_until, earn_date="2026-06-10", already=False):
        d = {
            "price": 200.0,
            "change_pct_prev_day": -0.3,
            "high_52w_price": 250.0,
            "price_date": "2026-06-04",
            "stage2_score": 4,
            "rs_score": 55.0,
            "market_structure": "NEUTRAL",
            "monthly_phase": "ADVANCING",
            "ema200_slope": 0.0,
            "pct_from_52w_high": -10.0,
            "pullback_pct": 5.0,
            "pct_vs_entry": None,
            "entry": 0.0,
            "rsi14": 50.0,
            "ema200": 180.0,
            "ema50": 190.0,
            "ema21": 195.0,
            "atr14": 3.0,
            "price_above_emas": True,
            "ema200_rising": False,
            "volume_contracting": False,
            "near_52w_high": False,
            "bear_flag": False,
            "rsi_divergence_bullish": False,
            "rsi_divergence_bearish": False,
            "gc_above": False,
            "gc_breakout": False,
            "gc_retest": False,
            "earnings_date": earn_date if days_until is not None else None,
            "days_until_earnings": days_until,
            "eps_estimate": 2.50,
            "already_reported_possible": already,
        }
        return {
            "symbol_detail": {sym: d},
            "prepost": {},
            "sentiment": {"symbols": []},
        }

    def test_earnings_within_14_days_included(self):
        data = self._make_data("NVDA", days_until=5)
        result = _mb_format_symbol_block(data)
        self.assertIn("실적발표=", result)
        self.assertIn("2026-06-10", result)

    def test_earnings_exactly_14_days_included(self):
        data = self._make_data("NVDA", days_until=14)
        result = _mb_format_symbol_block(data)
        self.assertIn("실적발표=", result)

    def test_earnings_15_days_omitted(self):
        data = self._make_data("NVDA", days_until=15)
        result = _mb_format_symbol_block(data)
        self.assertNotIn("실적발표=", result)
        self.assertNotIn("30일이내없음", result)
        self.assertNotIn("해당없음", result)

    def test_no_earnings_date_omitted(self):
        data = self._make_data("NVDA", days_until=None, earn_date=None)
        result = _mb_format_symbol_block(data)
        self.assertNotIn("실적발표=", result)
        self.assertNotIn("해당없음", result)

    def test_already_reported_always_shown(self):
        data = self._make_data("NVDA", days_until=0, earn_date="2026-06-05", already=True)
        result = _mb_format_symbol_block(data)
        self.assertIn("이미발표됨", result)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_morning_briefing.py::TestMorningBriefingEarningsFilter -v
```

Expected: FAIL (current code outputs "해당없음(30일이내없음)" for all no-earnings stocks).

---

### Task 5: Fix `collect_morning_briefing._format_symbol_block` earnings logic

**Files:**
- Modify: `collect/collect_morning_briefing.py:377-429`

- [ ] **Step 1: Update `earn_str` logic**

Find lines 377-391 in `collect/collect_morning_briefing.py`:

```python
        earn_date = d.get("earnings_date")
        days_earn = d.get("days_until_earnings")
        eps_est = d.get("eps_estimate")
        already_reported = d.get("already_reported_possible", False)
        if earn_date and already_reported:
            earn_str = (
                f"【실적발표=⚠이미발표됨({earn_date}) / EPS예상=${eps_est}】\n"
                f"  ⛔ HARD RULE: analysis에 'beat','miss','상회','하회','exceeded','missed',"
                f"'split','분할','exceeded estimates' 절대 금지. 실제 결과는 데이터에 없음.\n"
                f"  ✅ 허용 표현: '오늘 장 마감 후 실적 발표됨 — EPS 추정 ${eps_est}, 실제 결과 확인 필요'"
            )
        elif earn_date:
            earn_str = f"【실적발표={earn_date} ({days_earn}일후) / EPS예상=${eps_est}】"
        else:
            earn_str = "【실적발표=해당없음(30일이내없음)】"
```

Replace with:

```python
        earn_date = d.get("earnings_date")
        days_earn = d.get("days_until_earnings")
        eps_est = d.get("eps_estimate")
        already_reported = d.get("already_reported_possible", False)
        if earn_date and already_reported:
            earn_str = (
                f"【실적발표=⚠이미발표됨({earn_date}) / EPS예상=${eps_est}】\n"
                f"  ⛔ HARD RULE: analysis에 'beat','miss','상회','하회','exceeded','missed',"
                f"'split','분할','exceeded estimates' 절대 금지. 실제 결과는 데이터에 없음.\n"
                f"  ✅ 허용 표현: '오늘 장 마감 후 실적 발표됨 — EPS 추정 ${eps_est}, 실제 결과 확인 필요'"
            )
        elif earn_date and days_earn is not None and days_earn <= 14:
            earn_str = f"【실적발표={earn_date} ({days_earn}일후) / EPS예상=${eps_est}】"
        else:
            earn_str = ""
```

- [ ] **Step 2: Conditionally include `earn_str` in the output block**

Find the `lines.append(...)` in `_format_symbol_block` (around line 415). Change `f"  {earn_str}\n"` to be conditional:

```python
        earn_line = f"  {earn_str}\n" if earn_str else ""
        lines.append(
            f"{sym} ({company}) [T{tier}]\n"
            f"  Stage2점수={d['stage2_score']}/7  시장상대강도RS={d['rs_score']}  "
            f"구조={d['market_structure']}  월봉추세={d['monthly_phase']}\n"
            f"  [전일종가(D-1)=${d['price']}]  【전일등락(D-2→D-1)={chg_prev_str}】  "
            f"52주고점=${d['high_52w_price']}(대비{d['pct_from_52w_high']}%)  "
            f"돌파목표대비={vs_entry}  최근눌림={d['pullback_pct']}%\n"
            f"  [{prepost_str}]\n"
            f"  가격앵커: RSI14={rsi_str}  EMA21={ema21_str}  EMA50={ema50_str}  EMA200={ema200_str}  ATR14={atr14_str}\n"
            f"{earn_line}"
            f"  기술신호: {', '.join(signals)}\n"
            f"  소셜심리: {sent.get('sentiment','N/A')} (점수={sent.get('composite_score','N/A')})\n"
            f"  투자자반응: {sent_reason}\n"
            f"  투자자반응(KO): {sent_ko}"
        )
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_morning_briefing.py::TestMorningBriefingEarningsFilter -v
```

Expected: All 5 tests PASS.

---

### Task 6: Fix `collect_morning_briefing` binding rule and prompt instructions

**Files:**
- Modify: `collect/collect_morning_briefing.py:325` (binding rule)
- Modify: `collect/collect_morning_briefing.py:851` (analysis instruction)
- Modify: `collect/collect_morning_briefing.py:933-934` (watchlist analysis description)
- Modify: `collect/collect_morning_briefing.py:946-947` (earnings_alert field)

- [ ] **Step 1: Update binding rule**

Find line 325:
```python
    rows.append("  [4] 값이 N/A이면 해당 수치를 추측하지 말 것. 실적일이 N/A면 '30일 이내 없음'으로 처리.")
```

Replace with:
```python
    rows.append("  [4] 값이 N/A이면 해당 수치를 추측하지 말 것. 실적일 N/A이거나 14일 초과 → analysis에서 실적 언급 금지(완전 생략).")
```

- [ ] **Step 2: Update analysis instruction (line ~851)**

Find:
```python
   - Earnings: use ONLY exact dates from the table. "N/A" = write "30일 이내 실적 발표 없음".
```

Replace with:
```python
   - Earnings: mention ONLY if within 14 days. If N/A or >14 days, omit earnings entirely — do NOT write "30일 이내 실적 발표 없음" or any equivalent phrase.
```

- [ ] **Step 3: Update watchlist analysis_en/ko description (lines ~933-934)**

Find:
```python
      "analysis_en": "3-5 sentences flowing paragraph. (1) recent price level using EXACT 전일종가 from table; if 프리마켓 is available, mention today's pre-market direction with that exact value, (2) strength or vulnerability in plain language using market_structure and stage2 data, (3) upside or downside using EMA/ATR anchors from 가격앵커, (4) social sentiment. All $ values must match table. For earnings: exact date from table or 'no earnings within 30 days'.",
      "analysis_ko": "같은 내용 한국어 3-5문장. 전일종가는 테이블 값 그대로. 프리마켓 값이 있으면 '오늘 개장 전 $X(+Y%)' 형태로 사용. 없으면 오늘 방향 언급 금지. 실적일도 테이블 기준. 소셜 반응 자연스럽게 포함.",
```

Replace with:
```python
      "analysis_en": "3-5 sentences flowing paragraph. (1) recent price level using EXACT 전일종가 from table; if 프리마켓 is available, mention today's pre-market direction with that exact value, (2) strength or vulnerability in plain language using market_structure and stage2 data, (3) upside or downside using EMA/ATR anchors from 가격앵커, (4) social sentiment. All $ values must match table. Mention earnings ONLY if ≤14 days away with exact date; otherwise omit earnings entirely.",
      "analysis_ko": "같은 내용 한국어 3-5문장. 전일종가는 테이블 값 그대로. 프리마켓 값이 있으면 '오늘 개장 전 $X(+Y%)' 형태로 사용. 없으면 오늘 방향 언급 금지. 실적은 14일 이내일 때만 정확한 날짜와 함께 언급, 그 외 완전 생략. 소셜 반응 자연스럽게 포함.",
```

- [ ] **Step 4: Update `earnings_alert` field description (lines ~946-947)**

Find:
```python
  "earnings_alert_en": "For ⚠이미발표됨 stocks: '[SYM] already reported after US close today (est. EPS $X — verify actual results at broker/financial site)'. For upcoming: EXACT date from table (e.g. 'MU on 2026-06-25, EPS est. $19.28'). Never 'next week'/'soon'.",
  "earnings_alert_ko": "⚠이미발표됨 종목: '[심볼]은 오늘 미국 장 마감 후 실적 발표됨 (EPS 추정 $X — 실제 결과는 증권사·뉴스 확인 필요)'. 향후 종목: 테이블 정확한 날짜 (예: 'MU 6월 25일, EPS 예상 $19.28'). '다음 주'/'곧' 금지."
```

Replace with:
```python
  "earnings_alert_en": "List ONLY: (1) ⚠이미발표됨 stocks: '[SYM] already reported after US close (est. EPS $X — verify actual at broker)'; (2) stocks with earnings ≤14 days away: exact date and EPS from table. If no such stocks exist, write empty string. Never 'next week'/'soon'/'no earnings'.",
  "earnings_alert_ko": "다음 종목만 나열: (1) ⚠이미발표됨: '[심볼] 오늘 미국 장 마감 후 실적 발표됨 (EPS 추정 $X — 실제 결과는 증권사 확인)'; (2) 14일 이내 실적 예정 종목: 테이블의 정확한 날짜와 EPS. 해당 종목이 없으면 빈 문자열. '다음 주'/'곧'/'실적 없음' 금지."
```

- [ ] **Step 5: Run all morning briefing tests**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_morning_briefing.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/collect_morning_briefing.py collect/test_collect_morning_briefing.py
git commit -m "feat: only mention earnings in morning briefing when within 14 days"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/ -v
```

Expected: All tests PASS, no regressions.

- [ ] **Step 2: Smoke test prompt output**

Check that the current `brief/latest.json` or a dry-run of the prompt builder doesn't include "30일 이내 없음":

```bash
cd /Users/jerry/dev/market-sentiment-data
python -c "
import json
data = json.load(open('brief/latest.json'))
for sb in data.get('symbol_briefs', []):
    text = sb.get('brief_ko','') + sb.get('brief_en','')
    if '30일 이내' in text or '없음' in text:
        print(f\"[FOUND] {sb['symbol']}: {text[:100]}\")
print('smoke test done')
"
```

Expected: No "[FOUND]" lines, or only from stocks that happen to be within 14 days.

- [ ] **Step 3: Update design doc commit**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add docs/superpowers/specs/2026-06-05-earnings-mention-filter-design.md docs/superpowers/plans/2026-06-05-earnings-mention-filter.md
git commit -m "docs: add earnings filter design and implementation plan"
```
