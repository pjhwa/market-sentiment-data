# Global Context Morning Briefing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 2-stage Grok pipeline to `collect_morning_briefing.py` that fetches top-3 global macro/geopolitical issues first, then injects them as context into the main briefing — surfacing them as dedicated cards in SniperBoard's `MorningBriefingBoard`.

**Architecture:** A single cron entry stays unchanged. `collect_morning_briefing.py` makes a lightweight 1st Grok call (web search → global context JSON), then injects that result into the existing 2nd Grok call (full briefing). The final `briefing/latest.json` gains a `global_context` section. SniperBoard's frontend adds TypeScript types and a new `GlobalContextSection` component with no backend changes required.

**Tech Stack:** Python 3.11 (collect side), TypeScript / React (sniperboard frontend), pytest (testing), xAI Grok via `hermes` CLI

**Spec:** `docs/superpowers/specs/2026-06-03-global-context-morning-briefing-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `collect/collect_morning_briefing.py` | Modify | 2-stage Grok calls, new prompt/parser functions |
| `collect/test_collect_morning_briefing.py` | Create | Unit tests for new functions |
| `PROJECT_CONTEXT.md` | Modify | Document 2-stage flow + schema_version 1.1 |
| `README.md` | Modify | briefing/ section update |
| `sniperboard/frontend/hooks/useMorningBriefing.ts` | Modify | GlobalIssue + GlobalContext interfaces |
| `sniperboard/frontend/components/boards/MorningBriefingBoard.tsx` | Modify | GlobalContextSection component + share text |

---

## Phase 1: market-sentiment-data (Python)

### Task 1: Create test file + `validate_global_context()`

**Files:**
- Create: `collect/test_collect_morning_briefing.py`
- Modify: `collect/collect_morning_briefing.py` (add `validate_global_context` function)

- [ ] **Step 1.1: Write the failing tests**

Create `collect/test_collect_morning_briefing.py`:

```python
"""
collect_morning_briefing 단위 테스트
python -m pytest collect/test_collect_morning_briefing.py -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from collect.collect_morning_briefing import validate_global_context


def _valid_issue(rank=1):
    return {
        "rank": rank,
        "tier": "breaking",
        "category": "trade_tariff",
        "title_en": "US expands chip export controls",
        "title_ko": "미국 반도체 수출통제 확대",
        "summary_en": "The US Commerce Department added 5 countries. Markets concerned about NVDA.",
        "summary_ko": "미 상무부가 5개국을 추가했다. NVDA 영향 우려.",
        "source_hint": "Reuters 2026-06-03",
        "confidence": "confirmed",
        "us_stock_impact_en": "NVDA and MU face direct export headwind.",
        "us_stock_impact_ko": "NVDA·MU 직접 영향.",
        "impact_direction": "negative",
    }


class TestValidateGlobalContext(unittest.TestCase):

    def test_valid_single_issue_passes(self):
        self.assertTrue(validate_global_context({"issues": [_valid_issue()]}))

    def test_valid_three_issues_passes(self):
        data = {"issues": [_valid_issue(1), _valid_issue(2), _valid_issue(3)]}
        self.assertTrue(validate_global_context(data))

    def test_empty_issues_passes(self):
        # fallback case — 0 items is valid
        self.assertTrue(validate_global_context({"issues": []}))

    def test_more_than_three_issues_fails(self):
        data = {"issues": [_valid_issue(i) for i in range(1, 5)]}
        self.assertFalse(validate_global_context(data))

    def test_missing_issues_key_fails(self):
        self.assertFalse(validate_global_context({}))

    def test_invalid_category_fails(self):
        issue = _valid_issue()
        issue["category"] = "politics"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_invalid_tier_fails(self):
        issue = _valid_issue()
        issue["tier"] = "new"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_invalid_confidence_fails(self):
        issue = _valid_issue()
        issue["confidence"] = "maybe"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_invalid_impact_direction_fails(self):
        issue = _valid_issue()
        issue["impact_direction"] = "bad"
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_missing_title_en_fails(self):
        issue = _valid_issue()
        del issue["title_en"]
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_missing_summary_ko_fails(self):
        issue = _valid_issue()
        del issue["summary_ko"]
        self.assertFalse(validate_global_context({"issues": [issue]}))

    def test_non_dict_input_fails(self):
        self.assertFalse(validate_global_context("not a dict"))

    def test_ongoing_no_update_field_optional(self):
        data = {
            "issues": [_valid_issue()],
            "ongoing_no_update": ["central_bank"],
        }
        self.assertTrue(validate_global_context(data))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
python -m pytest collect/test_collect_morning_briefing.py -v
```

Expected: `ImportError: cannot import name 'validate_global_context'`

- [ ] **Step 1.3: Add `validate_global_context` to `collect_morning_briefing.py`**

Add these constants near the top of `collect_morning_briefing.py`, after the existing `SNIPERBOARD_API` line:

```python
CALL_TIMEOUT_GLOBAL = int(os.environ.get("HERMES_TIMEOUT_GLOBAL", "90"))

_VALID_GC_CATEGORIES = {"trade_tariff", "geopolitical", "central_bank", "ai_regulation"}
_VALID_GC_TIERS = {"breaking", "ongoing"}
_VALID_GC_CONFIDENCE = {"confirmed", "developing", "unverified"}
_VALID_GC_IMPACT = {"positive", "negative", "neutral", "watch"}
```

Add the function after the `_format_earnings_block` function (before `build_prompt`):

```python
def validate_global_context(data: dict) -> bool:
    """1차 Grok 응답 글로벌 컨텍스트 검증. 0개 이슈는 fallback으로 유효."""
    if not isinstance(data, dict):
        return False
    issues = data.get("issues")
    if not isinstance(issues, list):
        return False
    if len(issues) == 0:
        return True
    if len(issues) > 3:
        print(f"[WARN] global_context: 이슈 {len(issues)}개 — 3개 초과", file=sys.stderr)
        return False
    for iss in issues:
        if not isinstance(iss, dict):
            return False
        if iss.get("category") not in _VALID_GC_CATEGORIES:
            print(f"[WARN] global_context: category={iss.get('category')!r}", file=sys.stderr)
            return False
        if iss.get("tier") not in _VALID_GC_TIERS:
            print(f"[WARN] global_context: tier={iss.get('tier')!r}", file=sys.stderr)
            return False
        if iss.get("confidence") not in _VALID_GC_CONFIDENCE:
            print(f"[WARN] global_context: confidence={iss.get('confidence')!r}", file=sys.stderr)
            return False
        if iss.get("impact_direction") not in _VALID_GC_IMPACT:
            print(f"[WARN] global_context: impact_direction={iss.get('impact_direction')!r}", file=sys.stderr)
            return False
        for field in ("title_en", "title_ko", "summary_en", "summary_ko"):
            if not isinstance(iss.get(field), str) or not iss[field]:
                print(f"[WARN] global_context: {field} 누락", file=sys.stderr)
                return False
    return True
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
python -m pytest collect/test_collect_morning_briefing.py -v
```

Expected: All 13 tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add collect/test_collect_morning_briefing.py collect/collect_morning_briefing.py
git commit -m "feat: add validate_global_context + tests for morning briefing 2-stage"
```

---

### Task 2: Add `build_global_context_prompt()` + `parse_global_context()`

**Files:**
- Modify: `collect/collect_morning_briefing.py`
- Modify: `collect/test_collect_morning_briefing.py`

- [ ] **Step 2.1: Add tests for `parse_global_context`**

Append to `collect/test_collect_morning_briefing.py`:

```python
from collect.collect_morning_briefing import parse_global_context


class TestParseGlobalContext(unittest.TestCase):

    def _valid_json(self):
        return '''
        {
          "fetched_at": "2026-06-03T22:15:00Z",
          "search_window": "48h",
          "issues": [
            {
              "rank": 1,
              "tier": "breaking",
              "category": "trade_tariff",
              "title_en": "US chip controls expanded",
              "title_ko": "미국 칩 수출 확대",
              "summary_en": "Commerce Dept added 5 countries. Verified by Reuters.",
              "summary_ko": "상무부가 5개국을 추가했다.",
              "source_hint": "Reuters 2026-06-03",
              "confidence": "confirmed",
              "us_stock_impact_en": "NVDA negative.",
              "us_stock_impact_ko": "NVDA 부정적.",
              "impact_direction": "negative"
            }
          ],
          "ongoing_no_update": ["central_bank"]
        }
        '''

    def test_valid_json_returns_dict(self):
        result = parse_global_context(self._valid_json())
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result.get("issues", [])), 1)

    def test_empty_string_returns_empty_dict(self):
        self.assertEqual(parse_global_context(""), {})

    def test_no_json_in_text_returns_empty_dict(self):
        self.assertEqual(parse_global_context("sorry I cannot search the web right now"), {})

    def test_invalid_json_returns_empty_dict(self):
        self.assertEqual(parse_global_context("{not valid json}"), {})

    def test_invalid_structure_returns_empty_dict(self):
        # missing issues key — validate_global_context will reject
        self.assertEqual(parse_global_context('{"data": []}'), {})

    def test_json_embedded_in_prose_extracted(self):
        text = 'Here is the result:\n' + self._valid_json() + '\nEnd.'
        result = parse_global_context(text)
        self.assertIsInstance(result, dict)
        self.assertIn("issues", result)
```

- [ ] **Step 2.2: Run new tests to verify they fail**

```bash
python -m pytest collect/test_collect_morning_briefing.py::TestParseGlobalContext -v
```

Expected: `ImportError: cannot import name 'parse_global_context'`

- [ ] **Step 2.3: Add `build_global_context_prompt()` and `parse_global_context()` to `collect_morning_briefing.py`**

Add after `validate_global_context`, before `build_prompt`:

```python
def build_global_context_prompt(now_kst: str, now_iso: str) -> str:
    return f"""You are a professional financial intelligence analyst with live web search access.
Today is {now_kst} (KST) / {now_iso} (UTC).

━━━ TASK ━━━
Search the web for global macro and geopolitical developments from the LAST 48 HOURS
that could move US stock prices TODAY. Structure output into TWO tiers:

TIER 1 — BREAKING (last 48h): New developments that just happened.
TIER 2 — ONGOING WATCH: Persistent situations with NO new development today
  but still carrying active market risk. Check ALL of the following even if not trending:
  · US-China trade / semiconductor export controls (NVDA, TSM, MU impact)
  · Taiwan Strait military tension (TSM, NVDA supply chain)
  · Middle East conflict / Strait of Hormuz (oil, defense: CEG, VST)
  · Russia-Ukraine war (energy prices, European demand)
  · ECB / BOJ / BOE policy stance (USD direction, rate-sensitive tech)
  · US AI/antitrust regulation (GOOGL, META, MSFT, AAPL)
  · US tariff / trade deal negotiations
  If a category has NO meaningful update in 48h, add its name to ongoing_no_update.

━━━ RULES — READ CAREFULLY ━━━
✓ ONLY report events attributable to a real, verifiable source.
  Include source_hint: e.g. "Reuters 2026-06-03", "White House statement", "BOJ press release"
✓ Time-box strictly: if you cannot confirm an event occurred within 48 hours, DO NOT include it as new.
✓ If uncertain about a fact, write "unconfirmed:" at the start of that sentence.
✗ DO NOT fabricate specific figures (percentages, dates, names) you cannot verify.
✗ DO NOT present a viral social media claim as confirmed fact.
✗ DO NOT include background/historical context as if it were a new development.
✗ DO NOT speculate on price targets or predict market direction.
✗ DO NOT smooth over uncertainty — if developing and unclear, say so explicitly.

━━━ WATCHLIST TICKERS FOR IMPACT MAPPING ━━━
TSM NVDA META TSLA PLTR MU CRWD AMZN MSFT AAPL GOOGL
RKLB CEG VST ALAB OKLO APP ANET NVO QBTS SOFI

Output raw JSON only (no markdown, no prose before or after):
{{
  "fetched_at": "{now_iso}",
  "search_window": "48h",
  "issues": [
    {{
      "rank": 1,
      "tier": "breaking",
      "category": "trade_tariff|geopolitical|central_bank|ai_regulation",
      "title_en": "factual headline ≤80 chars",
      "title_ko": "사실 위주 30자 이내",
      "summary_en": "2-3 sentences: WHAT happened, WHERE reported, WHY markets care. Prefix unconfirmed details with 'unconfirmed:'",
      "summary_ko": "같은 내용 한국어 2-3문장.",
      "source_hint": "e.g. Reuters 2026-06-03",
      "confidence": "confirmed|developing|unverified",
      "us_stock_impact_en": "Name specific tickers and direction. Write 'impact unclear pending confirmation' if unknown.",
      "us_stock_impact_ko": "감시 종목 티커 명시 + 방향",
      "impact_direction": "positive|negative|neutral|watch"
    }}
  ],
  "ongoing_no_update": ["category names with no 48h development"]
}}"""


def parse_global_context(text: str) -> dict:
    """1차 Grok 응답에서 글로벌 컨텍스트 JSON 추출. 실패 시 {{}} 반환."""
    if not text:
        return {}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        print("[WARN] global_context: JSON 블록 없음", file=sys.stderr)
        return {}
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[WARN] global_context: JSON 파싱 실패: {e}", file=sys.stderr)
        return {}
    if not validate_global_context(data):
        return {}
    return data
```

- [ ] **Step 2.4: Run all tests**

```bash
python -m pytest collect/test_collect_morning_briefing.py -v
```

Expected: All 19 tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add collect/collect_morning_briefing.py collect/test_collect_morning_briefing.py
git commit -m "feat: add build_global_context_prompt + parse_global_context"
```

---

### Task 3: Add `_format_global_context_block()` + modify `build_prompt()`

**Files:**
- Modify: `collect/collect_morning_briefing.py`
- Modify: `collect/test_collect_morning_briefing.py`

- [ ] **Step 3.1: Write tests for `_format_global_context_block`**

Append to `collect/test_collect_morning_briefing.py`:

```python
from collect.collect_morning_briefing import _format_global_context_block


class TestFormatGlobalContextBlock(unittest.TestCase):

    def _ctx_with_one_issue(self):
        return {
            "fetched_at": "2026-06-03T22:15:00Z",
            "issues": [{
                "rank": 1,
                "tier": "breaking",
                "category": "trade_tariff",
                "title_en": "US chip controls expanded",
                "source_hint": "Reuters 2026-06-03",
                "confidence": "confirmed",
                "summary_en": "Commerce Dept added 5 countries.",
                "us_stock_impact_en": "NVDA negative.",
            }],
        }

    def test_empty_issues_returns_fallback_string(self):
        result = _format_global_context_block({"issues": []})
        self.assertIn("No verified global issues", result)

    def test_empty_dict_returns_fallback_string(self):
        result = _format_global_context_block({})
        self.assertIn("No verified global issues", result)

    def test_valid_ctx_contains_title(self):
        result = _format_global_context_block(self._ctx_with_one_issue())
        self.assertIn("US chip controls expanded", result)

    def test_valid_ctx_contains_source_hint(self):
        result = _format_global_context_block(self._ctx_with_one_issue())
        self.assertIn("Reuters 2026-06-03", result)

    def test_developing_confidence_shows_tag(self):
        ctx = self._ctx_with_one_issue()
        ctx["issues"][0]["confidence"] = "developing"
        result = _format_global_context_block(ctx)
        self.assertIn("[DEVELOPING]", result)

    def test_confirmed_confidence_no_tag(self):
        result = _format_global_context_block(self._ctx_with_one_issue())
        self.assertNotIn("[CONFIRMED]", result)

    def test_ongoing_no_update_shown(self):
        ctx = self._ctx_with_one_issue()
        ctx["ongoing_no_update"] = ["central_bank", "ai_regulation"]
        result = _format_global_context_block(ctx)
        self.assertIn("central_bank", result)

    def test_instructions_included(self):
        result = _format_global_context_block(self._ctx_with_one_issue())
        self.assertIn("big_picture.summary", result)
```

- [ ] **Step 3.2: Run new tests to verify they fail**

```bash
python -m pytest collect/test_collect_morning_briefing.py::TestFormatGlobalContextBlock -v
```

Expected: `ImportError: cannot import name '_format_global_context_block'`

- [ ] **Step 3.3: Add `_format_global_context_block()` to `collect_morning_briefing.py`**

Add after `_format_earnings_block`, before `validate_global_context`:

```python
def _format_global_context_block(global_ctx: dict) -> str:
    """글로벌 컨텍스트를 2차 Grok 프롬프트 주입용 텍스트로 변환."""
    issues = global_ctx.get("issues", [])
    if not issues:
        return "GLOBAL CONTEXT: No verified global issues retrieved (search failed or no significant events)."

    lines = [
        "━━━ GLOBAL MACRO & GEOPOLITICAL CONTEXT ━━━",
        f"(Verified within 48h as of {global_ctx.get('fetched_at', 'unknown')})",
        "Use this context to enrich your briefing. Items marked [DEVELOPING] or [UNVERIFIED] should be noted with caution.\n",
    ]
    for iss in issues:
        conf = iss.get("confidence", "confirmed")
        conf_tag = "" if conf == "confirmed" else f" [{conf.upper()}]"
        lines.append(
            f"[{iss.get('rank')}][{iss.get('tier', '').upper()}][{iss.get('category', '')}]{conf_tag} {iss.get('title_en', '')}"
            f"\n  Source: {iss.get('source_hint', 'unknown')}"
            f"\n  {iss.get('summary_en', '')}"
            f"\n  US Impact: {iss.get('us_stock_impact_en', '')}"
        )

    no_update = global_ctx.get("ongoing_no_update", [])
    if no_update:
        lines.append(f"\nOngoing situations with NO new 48h development: {', '.join(no_update)}")

    lines.append("""
INSTRUCTIONS for using this context:
- big_picture.summary: incorporate the most impactful issue naturally (1 sentence max)
- sector_analysis: reflect geopolitical/regulatory tailwinds or headwinds where relevant
- spotlight/watchlist: if an issue directly names a watchlist ticker, mention it in that stock's analysis
- For items marked [DEVELOPING] or [UNVERIFIED]: mention with appropriate caution language
""")
    return "\n".join(lines)
```

- [ ] **Step 3.4: Modify `build_prompt()` signature to accept `global_ctx`**

Find the existing `def build_prompt(data: dict, now_kst: str) -> str:` line and change it to:

```python
def build_prompt(data: dict, now_kst: str, global_ctx: dict | None = None) -> str:
    global_block = _format_global_context_block(global_ctx or {})
```

Then insert `global_block` into the return f-string, immediately after the opening line (before `WRITING RULES`). The return statement starts with:

```python
    return f"""You are a friendly stock market expert writing a morning briefing for Korean retail investors who are NOT finance professionals.
Today is {now_kst} (KST).
```

Change it to:

```python
    return f"""You are a friendly stock market expert writing a morning briefing for Korean retail investors who are NOT finance professionals.
Today is {now_kst} (KST).

{global_block}

WRITING RULES — follow strictly:
```

(Everything else in the `build_prompt` f-string stays identical.)

- [ ] **Step 3.5: Run all tests**

```bash
python -m pytest collect/test_collect_morning_briefing.py -v
```

Expected: All 27 tests PASS.

- [ ] **Step 3.6: Commit**

```bash
git add collect/collect_morning_briefing.py collect/test_collect_morning_briefing.py
git commit -m "feat: add _format_global_context_block, inject into build_prompt"
```

---

### Task 4: Modify `call_hermes()` + `main()` for 2-stage execution

**Files:**
- Modify: `collect/collect_morning_briefing.py`

- [ ] **Step 4.1: Add `timeout` parameter to `call_hermes()`**

Find the existing `def call_hermes(prompt: str) -> str | None:` and change it to:

```python
def call_hermes(prompt: str, timeout: int | None = None) -> str | None:
    cmd = [HERMES_CMD, "-z", prompt]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    effective_timeout = timeout if timeout is not None else CALL_TIMEOUT
    for attempt in range(1 + HERMES_RETRY):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=effective_timeout, env=env)
            if result.returncode != 0:
                print(f"[ERROR] hermes 비정상 종료: {result.stderr[:300]}", file=sys.stderr)
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            remaining = HERMES_RETRY - attempt
            if remaining > 0:
                print(f"[WARN] hermes 타임아웃 — 재시도 {remaining}회 남음", file=sys.stderr)
            else:
                print("[ERROR] hermes 타임아웃 — 재시도 소진", file=sys.stderr)
                return None
        except FileNotFoundError:
            print(f"[ERROR] hermes 명령 없음: {HERMES_CMD}", file=sys.stderr)
            return None
    return None
```

- [ ] **Step 4.2: Modify `main()` to run 2-stage Grok calls**

Find the `main()` function. Replace the section from `data = fetch_all_data()` through `prompt = build_prompt(data, now_kst)` with:

```python
    data = fetch_all_data()

    # ── 1차 호출: 글로벌 매크로/지정학 컨텍스트 수집 ──────────────────────────
    global_ctx: dict = {}
    global_context_prompt = build_global_context_prompt(now_kst, now_iso)
    print("[INFO] Grok 1차 호출: 글로벌 컨텍스트 수집 중 (최대 90초)...")
    global_raw = call_hermes(global_context_prompt, timeout=CALL_TIMEOUT_GLOBAL)
    if global_raw:
        global_ctx = parse_global_context(global_raw)
        if global_ctx and global_ctx.get("issues"):
            print(f"[INFO] 글로벌 이슈 {len(global_ctx['issues'])}개 수집됨")
        else:
            print("[WARN] 글로벌 컨텍스트: 이슈 없음 — fallback으로 계속 진행", file=sys.stderr)
    else:
        print("[WARN] 글로벌 컨텍스트 Grok 호출 실패 — fallback으로 계속 진행", file=sys.stderr)

    # ── 2차 호출: 아침 브리핑 생성 (글로벌 컨텍스트 주입) ───────────────────
    prompt = build_prompt(data, now_kst, global_ctx)
```

- [ ] **Step 4.3: Update snapshot to inject `global_context` and bump `schema_version`**

Find the `snapshot = {` block in `main()` and replace it:

```python
    snapshot = {
        "generated_at": now_iso,
        "schema_version": "1.1",
        "slot": "morning",
        **parsed,
        # Always set from 1st-call result — not Grok's pass-through (unreliable)
        "global_context": global_ctx if global_ctx else {"issues": [], "fallback": True},
    }
```

- [ ] **Step 4.4: Smoke-test the import**

```bash
python -c "from collect.collect_morning_briefing import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 4.5: Run full test suite**

```bash
python -m pytest collect/test_collect_morning_briefing.py -v
```

Expected: All tests PASS (no regressions — `call_hermes` signature change is backward compatible due to default `timeout=None`).

- [ ] **Step 4.6: Commit**

```bash
git add collect/collect_morning_briefing.py
git commit -m "feat: 2-stage Grok pipeline in main() — global context then briefing"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `PROJECT_CONTEXT.md`
- Modify: `README.md`

- [ ] **Step 5.1: Update `PROJECT_CONTEXT.md`**

Find the `collect_morning_briefing.py` description line and update it. Also find the `schema_version` or briefing section. Add the following under the morning briefing entry:

Under the architecture section, update the `collect_morning_briefing.py` description to:
```
│  · collect_morning_briefing.py  # Collector 5 — 아침 브리핑 (KST 07:30)
│                                 #   2-stage: global context (Grok 1차) → full briefing (Grok 2차)
```

Find the `briefing/` file map section and add:
```
├── briefing/
│   ├── latest.json               # Morning Briefing: schema_version 1.1
│   │                             # global_context section: top-3 global issues (trade/geo/central_bank/ai_reg)
```

Update the "AUTO-GENERATED" date comment at the top to today's date: `2026-06-03`.

- [ ] **Step 5.2: Update `README.md`**

Find the `briefing/` section in README.md and add a note about `global_context`:

```markdown
- `briefing/latest.json` — Morning Briefing (schema v1.1). Includes `global_context` section with top-3
  global macro/geopolitical issues (trade/tariff, geopolitical, central bank, AI regulation) sourced via
  Grok live web search within a 48-hour window.
```

- [ ] **Step 5.3: Commit docs**

```bash
git add PROJECT_CONTEXT.md README.md
git commit -m "docs: update PROJECT_CONTEXT and README for schema v1.1 global_context"
```

---

## Phase 2: sniperboard (TypeScript frontend)

> Switch working directory to `/Users/jerry/dev/sniperboard` for all tasks in this phase.

### Task 6: Add TypeScript types to `useMorningBriefing.ts`

**Files:**
- Modify: `frontend/hooks/useMorningBriefing.ts`

- [ ] **Step 6.1: Add `GlobalIssue` and `GlobalContext` interfaces**

Open `frontend/hooks/useMorningBriefing.ts`. After the `MorningSectorAnalysis` interface (around line 33), add:

```typescript
export interface GlobalIssue {
  rank: number;
  tier: 'breaking' | 'ongoing';
  category: 'trade_tariff' | 'geopolitical' | 'central_bank' | 'ai_regulation';
  title_en?: string;
  title_ko?: string;
  summary_en?: string;
  summary_ko?: string;
  source_hint?: string;
  confidence?: 'confirmed' | 'developing' | 'unverified';
  us_stock_impact_en?: string;
  us_stock_impact_ko?: string;
  impact_direction?: 'positive' | 'negative' | 'neutral' | 'watch';
}

export interface GlobalContext {
  fetched_at?: string;
  search_window?: string;
  issues: GlobalIssue[];
  ongoing_no_update?: string[];
  fallback?: boolean;
}
```

- [ ] **Step 6.2: Add `global_context` to `MorningBriefingData`**

Find the `MorningBriefingData` interface and add one line after `earnings_alert_ko`:

```typescript
  global_context?: GlobalContext;
```

- [ ] **Step 6.3: Verify TypeScript compiles**

```bash
cd /Users/jerry/dev/sniperboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: No errors (or only pre-existing errors unrelated to the new types).

- [ ] **Step 6.4: Commit**

```bash
git add frontend/hooks/useMorningBriefing.ts
git commit -m "feat: add GlobalIssue + GlobalContext types to useMorningBriefing"
```

---

### Task 7: Add `GlobalContextSection` to `MorningBriefingBoard.tsx`

**Files:**
- Modify: `frontend/components/boards/MorningBriefingBoard.tsx`

- [ ] **Step 7.1: Add string constants for the new section**

In the `S` constant object (starting around line 13), add after the `btc` entry:

```typescript
  globalTitle:    { en: '🌐 Global Macro & Geopolitical Context', ko: '🌐 글로벌 매크로 · 지정학 리스크' },
  breaking:       { en: 'BREAKING',   ko: '속보' },
  ongoing:        { en: 'ONGOING',    ko: '지속 리스크' },
  confirmed:      { en: 'CONFIRMED',  ko: '확인됨' },
  developing:     { en: 'DEVELOPING', ko: '진행중' },
  unverified:     { en: 'UNVERIFIED', ko: '미확인' },
  sourceLabel:    { en: 'Source',     ko: '출처' },
  usImpact:       { en: 'US Stock Impact', ko: '미국 주식 영향' },
  noUpdate:       { en: 'No significant update in 48h', ko: '48시간 내 주요 변동 없음' },
```

- [ ] **Step 7.2: Add category color + label helpers**

Add after the `sentimentLabel` function (around line 76):

```typescript
function categoryColor(cat?: string): string {
  switch (cat) {
    case 'trade_tariff':  return 'var(--warn)';
    case 'geopolitical':  return 'var(--bear)';
    case 'central_bank':  return 'var(--info)';
    case 'ai_regulation': return 'var(--purple)';
    default:              return 'var(--fg-subtle)';
  }
}

function categoryLabel(cat?: string, locale?: Locale): string {
  const M: Record<string, { en: string; ko: string }> = {
    trade_tariff:  { en: 'Trade / Tariff', ko: '무역 · 관세' },
    geopolitical:  { en: 'Geopolitical',   ko: '지정학' },
    central_bank:  { en: 'Central Bank',   ko: '중앙은행' },
    ai_regulation: { en: 'AI / Regulation',ko: 'AI · 규제' },
  };
  const entry = M[cat ?? ''];
  return entry ? t(entry, locale ?? 'en') : (cat ?? '');
}

function impactCls(dir?: string): string {
  if (dir === 'positive') return 'bull';
  if (dir === 'negative') return 'bear';
  if (dir === 'watch')    return 'warn';
  return 'neutral';
}

function confidenceCls(conf?: string): string {
  if (conf === 'developing') return 'warn';
  if (conf === 'unverified') return 'bear';
  return 'neutral';
}
```

- [ ] **Step 7.3: Add the `GlobalContextSection` sub-component**

Add after the `Tier2Row` component (around line 300), before the `GlossarySection`:

```typescript
// ── 서브컴포넌트: 글로벌 컨텍스트 카드 ───────────────────────────────────────
import type { GlobalIssue, GlobalContext } from '@/hooks/useMorningBriefing';

function GlobalIssueCard({ issue, locale }: { issue: GlobalIssue; locale: Locale }) {
  const title   = tField(issue.title_en,          issue.title_ko,          '', locale);
  const summary = tField(issue.summary_en,         issue.summary_ko,        '', locale);
  const impact  = tField(issue.us_stock_impact_en, issue.us_stock_impact_ko,'', locale);
  const catColor = categoryColor(issue.category);

  return (
    <div className="card" style={{ borderTop: `2px solid ${catColor}` }}>
      {/* 헤더 행 */}
      <div className="card__hd" style={{ flexWrap: 'wrap', gap: 5 }}>
        <span className="badge neutral" style={{ fontSize: 10, borderColor: catColor, color: catColor }}>
          {categoryLabel(issue.category, locale)}
        </span>
        <span className={`badge ${issue.tier === 'breaking' ? 'bull' : 'neutral'}`} style={{ fontSize: 10 }}>
          {issue.tier === 'breaking' ? t(S.breaking, locale) : t(S.ongoing, locale)}
        </span>
        {issue.confidence && issue.confidence !== 'confirmed' && (
          <span className={`badge ${confidenceCls(issue.confidence)}`} style={{ fontSize: 10 }}>
            {issue.confidence === 'developing' ? t(S.developing, locale) : t(S.unverified, locale)}
          </span>
        )}
        <span className={`badge ${impactCls(issue.impact_direction)}`} style={{ fontSize: 10, marginLeft: 'auto' }}>
          {issue.impact_direction === 'positive' ? '▲' : issue.impact_direction === 'negative' ? '▼' : issue.impact_direction === 'watch' ? '⚠' : '—'} {issue.impact_direction}
        </span>
      </div>

      {/* 본문 */}
      <div className="card__bd" style={{ paddingTop: 6 }}>
        <p style={{ margin: '0 0 8px', fontSize: 13.5, fontWeight: 700, lineHeight: 1.4 }}>{title}</p>
        <p style={{ margin: '0 0 10px', fontSize: 12.5, lineHeight: 1.7, color: 'var(--fg-muted)' }}>{summary}</p>
        {impact && (
          <div style={{ padding: '6px 10px', borderRadius: 'var(--r-sm)', background: 'var(--bg-subtle)', fontSize: 12 }}>
            <span style={{ fontWeight: 700, color: catColor, marginRight: 6 }}>{t(S.usImpact, locale)}:</span>
            <span style={{ color: 'var(--fg)' }}>{impact}</span>
          </div>
        )}
        {issue.source_hint && (
          <div style={{ marginTop: 6, fontSize: 10, color: 'var(--fg-faint)', fontFamily: 'var(--font-mono)' }}>
            {t(S.sourceLabel, locale)}: {issue.source_hint}
          </div>
        )}
      </div>
    </div>
  );
}

function GlobalContextSection({ ctx, locale }: { ctx: GlobalContext; locale: Locale }) {
  if (!ctx.issues || ctx.issues.length === 0) return null;

  return (
    <>
      <SectionDivider label={t(S.globalTitle, locale)} color="var(--em-500)" />
      <div style={{ gridColumn: 'span 4', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
        {ctx.issues.map(issue => (
          <GlobalIssueCard key={issue.rank} issue={issue} locale={locale} />
        ))}
      </div>
      {ctx.ongoing_no_update && ctx.ongoing_no_update.length > 0 && (
        <div style={{ gridColumn: 'span 4', fontSize: 11, color: 'var(--fg-faint)', paddingTop: 2 }}>
          {t(S.noUpdate, locale)}: {ctx.ongoing_no_update.join(', ')}
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 7.4: Insert `GlobalContextSection` into the board render**

In the main `MorningBriefingBoard` return JSX, find the `{/* ── Spotlight ── */}` comment (around line 700). Insert the `GlobalContextSection` **immediately before** it (after the sector/checkpoints row):

```tsx
      {/* ── 글로벌 컨텍스트 ── */}
      {d.global_context && !d.global_context.fallback && (
        <GlobalContextSection ctx={d.global_context} locale={locale} />
      )}

      {/* ── Spotlight ── */}
```

- [ ] **Step 7.5: Verify TypeScript compiles**

```bash
cd /Users/jerry/dev/sniperboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: No new errors.

- [ ] **Step 7.6: Commit**

```bash
git add frontend/hooks/useMorningBriefing.ts frontend/components/boards/MorningBriefingBoard.tsx
git commit -m "feat: add GlobalContextSection to MorningBriefingBoard"
```

---

### Task 8: Update `buildShareText()` to include global context

**Files:**
- Modify: `frontend/components/boards/MorningBriefingBoard.tsx`

- [ ] **Step 8.1: Add global context block to `buildShareText`**

Find the `buildShareText` function (around line 82). After the `bpLines` block and before the `saLines` block, add:

```typescript
  const gc = d.global_context;
  const gcLines = gc && gc.issues && gc.issues.length > 0
    ? gc.issues.map(iss => {
        const title  = ko ? iss.title_ko  : iss.title_en;
        const impact = ko ? iss.us_stock_impact_ko : iss.us_stock_impact_en;
        const dir = iss.impact_direction === 'positive' ? '▲'
                  : iss.impact_direction === 'negative' ? '▼'
                  : iss.impact_direction === 'watch'    ? '⚠' : '—';
        return `[${dir}] ${title ?? ''}${impact ? `\n  → ${impact}` : ''}`;
      }).join('\n')
    : '';
```

Then in the return array, after the `bpLines` block and before the `saLines` block, add:

```typescript
    gcLines ? `\n🌐 ${ko ? '글로벌 이슈' : 'Global Issues'}\n${gcLines}` : '',
```

- [ ] **Step 8.2: Verify TypeScript compiles**

```bash
cd /Users/jerry/dev/sniperboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: No new errors.

- [ ] **Step 8.3: Commit**

```bash
git add frontend/components/boards/MorningBriefingBoard.tsx
git commit -m "feat: include global context in morning briefing share text"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| 2-stage Grok calls in single cron | Task 4 (`main()`) |
| HERMES_TIMEOUT_GLOBAL env var | Task 1 (constant) |
| `build_global_context_prompt()` with 48h window + persistent watchlist + DO NOT rules | Task 2 |
| `parse_global_context()` with graceful fallback | Task 2 |
| `validate_global_context()` 1-3 issues, field validation | Task 1 |
| `_format_global_context_block()` injection into 2nd prompt | Task 3 |
| `schema_version` → `"1.1"` | Task 4 |
| `global_context` set from 1st-call result (not Grok pass-through) | Task 4 |
| Fallback: `{"issues": [], "fallback": true}` on failure | Task 4 |
| Docs update | Task 5 |
| TypeScript `GlobalIssue` + `GlobalContext` interfaces | Task 6 |
| `global_context?` added to `MorningBriefingData` | Task 6 |
| `GlobalContextSection` with category badge, tier badge, confidence badge, impact badge | Task 7 |
| Placed between `big_picture` area and `spotlight` | Task 7 |
| Hidden when fallback or empty | Task 7 |
| `ongoing_no_update` shown as footnote | Task 7 |
| Share text includes global issues | Task 8 |

**Placeholder scan:** No TBD, TODO, or "similar to above" in any step. All code blocks are complete.

**Type consistency check:**
- `GlobalIssue` defined in Task 6, used in Task 7 ✓
- `GlobalContext` defined in Task 6, used in Tasks 7 + 8 ✓
- `global_context?: GlobalContext` in `MorningBriefingData` — accessed as `d.global_context` in Task 7 ✓
- `validate_global_context` defined in Task 1, imported in Task 2 test ✓
- `parse_global_context` defined in Task 2, imported in Task 2 test ✓
- `_format_global_context_block` defined in Task 3, imported in Task 3 test ✓
- `call_hermes(prompt, timeout)` signature changed in Task 4 — default `timeout=None` preserves backward compat with existing call in `main()` ✓

**One fix applied:** Import statement for `GlobalIssue` / `GlobalContext` in `MorningBriefingBoard.tsx` (Task 7.3) — already included at top of `GlobalIssueCard` component.
