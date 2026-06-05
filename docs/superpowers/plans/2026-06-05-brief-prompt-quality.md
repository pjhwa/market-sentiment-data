# Brief Prompt Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Grok from generating (1) ambiguous causal/contrastive language across unrelated event domains, and (2) Japanese hiragana/katakana characters in Korean output fields.

**Architecture:** Two-layer defense — Layer 1 adds explicit `WRITING STYLE RULES` to `build_brief_prompt()` so Grok is instructed up front; Layer 2 adds `validate_output_quality()` post-processing that detects violations and triggers one corrective retry via `build_correction_prompt()`.

**Tech Stack:** Python 3.11+, `re` (stdlib), existing `call_hermes()` / `extract_json()` / `validate_brief()` pipeline in `collect/collect_brief.py`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `collect/collect_brief.py` | Modify | Add prompt rules, validation function, correction prompt builder, retry logic in `main()` |
| `collect/test_collect_brief.py` | Modify | Add tests for `validate_output_quality()` |

---

## Task 1: Add `validate_output_quality()` with failing tests

**Files:**
- Modify: `collect/test_collect_brief.py`
- Modify: `collect/collect_brief.py` (stub only)

- [ ] **Step 1: Write failing tests**

Append this class to `collect/test_collect_brief.py`:

```python
from collect.collect_brief import validate_output_quality


class TestValidateOutputQuality(unittest.TestCase):
    def _brief(self, summary_ko="정상 요약.", brief_ko="정상 설명."):
        return {
            "market_brief": {
                "summary_en": "Normal summary.",
                "summary_ko": summary_ko,
                "tone": "neutral",
                "key_themes_en": ["theme"],
                "key_themes_ko": ["테마"],
                "watch_points_en": "Watch SPY.",
                "watch_points_ko": "SPY 주시.",
            },
            "symbol_briefs": [
                {
                    "symbol": "TSLA",
                    "setup_quality": "B",
                    "brief_en": "Normal brief.",
                    "brief_ko": brief_ko,
                    "key_risk_en": "Risk.",
                    "key_risk_ko": "리스크.",
                    "key_opportunity_en": "Opportunity.",
                    "key_opportunity_ko": "기회.",
                    "action_bias": "watch",
                }
            ],
        }

    # ── Causal language tests ──────────────────────────────────────────────

    def test_clean_brief_has_no_violations(self):
        violations = validate_output_quality(self._brief())
        self.assertEqual(violations, [])

    def test_cross_domain_korean_connective_detected(self):
        # 관세(policy) + 비트코인(crypto) + 는데 → violation
        bad = self._brief(
            summary_ko="미중 칩 관세가 개별 허가제로 바뀌는데 비트코인이 14% 급락했다."
        )
        violations = validate_output_quality(bad)
        self.assertTrue(any("인과" in v or "causal" in v.lower() for v in violations),
                        f"Expected causal violation, got: {violations}")

    def test_cross_domain_english_connective_detected(self):
        bad = self._brief()
        bad["market_brief"]["summary_en"] = (
            "US chip tariffs shifted to licensing while Bitcoin dropped 14%."
        )
        violations = validate_output_quality(bad)
        self.assertTrue(any("causal" in v.lower() or "인과" in v for v in violations),
                        f"Expected causal violation, got: {violations}")

    def test_same_domain_connective_allowed(self):
        # SPY(equity) + QQQ(equity) + but → same domain, no violation
        ok = self._brief()
        ok["market_brief"]["summary_en"] = "SPY held gains but QQQ lagged slightly."
        violations = validate_output_quality(ok)
        self.assertEqual(violations, [])

    def test_causal_in_symbol_brief_ko_detected(self):
        bad = self._brief(
            brief_ko="관세 정책이 강화되는데 TSLA는 오히려 급등했다."
        )
        violations = validate_output_quality(bad)
        self.assertTrue(len(violations) > 0, f"Expected violation, got: {violations}")

    # ── Japanese character tests ───────────────────────────────────────────

    def test_hiragana_in_ko_field_detected(self):
        bad = self._brief(summary_ko="시장은 あいう 조정 중.")
        violations = validate_output_quality(bad)
        self.assertTrue(any("일본어" in v or "japanese" in v.lower() for v in violations),
                        f"Expected Japanese violation, got: {violations}")

    def test_katakana_in_ko_field_detected(self):
        bad = self._brief(brief_ko="TSLA アイウ 전략 지속.")
        violations = validate_output_quality(bad)
        self.assertTrue(any("일본어" in v or "japanese" in v.lower() for v in violations),
                        f"Expected Japanese violation, got: {violations}")

    def test_hangul_only_text_passes(self):
        ok = self._brief(
            summary_ko="건설적 레짐 속 SPY 분배 경고.",
            brief_ko="TSLA Stage2 5/7 UPTREND 유지.",
        )
        violations = validate_output_quality(ok)
        self.assertEqual(violations, [])

    def test_japanese_in_en_field_not_flagged(self):
        # Only _ko fields are scanned for Japanese
        ok = self._brief()
        ok["market_brief"]["summary_en"] = "Market rises (see: アイウ reference)."
        violations = validate_output_quality(ok)
        jp_violations = [v for v in violations if "일본어" in v or "japanese" in v.lower()]
        self.assertEqual(jp_violations, [])
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_brief.py::TestValidateOutputQuality -v 2>&1 | head -30
```

Expected: `ImportError` or `AttributeError` — `validate_output_quality` not yet defined.

- [ ] **Step 3: Add stub to `collect_brief.py`**

Add immediately after the `validate_brief()` function (around line 652):

```python
def validate_output_quality(data: dict) -> list[str]:
    """Detect causal cross-domain language and Japanese chars in _ko fields."""
    return []
```

- [ ] **Step 4: Run tests to confirm stub gives right failure shape**

```bash
python -m pytest collect/test_collect_brief.py::TestValidateOutputQuality -v 2>&1 | head -40
```

Expected: `test_clean_brief_has_no_violations` PASS, all others FAIL (empty list returned).

- [ ] **Step 5: Commit stub + tests**

```bash
cd /Users/jerry/dev/market-sentiment-data
git add collect/collect_brief.py collect/test_collect_brief.py
git commit -m "test: add validate_output_quality tests (red)"
```

---

## Task 2: Implement `validate_output_quality()`

**Files:**
- Modify: `collect/collect_brief.py` (replace stub)

- [ ] **Step 1: Replace stub with full implementation**

Replace the stub `validate_output_quality` with:

```python
import re as _re

# ── Domain keyword sets ──────────────────────────────────────────────────────
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "crypto":  ["btc", "비트코인", "bitcoin", "crypto", "암호화폐"],
    "policy":  ["관세", "tariff", "허가제", "제재", "sanctions", "칩 규제", "chip"],
    "rates":   ["금리", "10y", "tnx", "treasury", "yield", "연준", "fed"],
}

# Causal/contrastive connectives
_CAUSAL_KO = ["는데", "지만", "하지만", "인데", "임에도", "불구하고"]
_CAUSAL_EN = [" while ", " but ", " however ", " although ", " yet ", " despite "]

# Japanese Unicode ranges (hiragana + katakana only; CJK excluded to avoid false positives)
_JP_RE = _re.compile(r"[぀-ゟ゠-ヿ]")


def _domains_in_text(text: str) -> set[str]:
    lower = text.lower()
    return {domain for domain, kws in _DOMAIN_KEYWORDS.items()
            if any(kw in lower for kw in kws)}


def _has_causal_connector(text: str) -> bool:
    lower = text.lower()
    return any(c in lower for c in _CAUSAL_KO + _CAUSAL_EN)


def _collect_ko_fields(data: dict) -> list[tuple[str, str]]:
    """Return (field_path, text) for every _ko field in the brief."""
    results: list[tuple[str, str]] = []
    mb = data.get("market_brief", {})
    for key in ("summary_ko", "watch_points_ko"):
        val = mb.get(key, "")
        if val:
            results.append((f"market_brief.{key}", val))
    for theme in mb.get("key_themes_ko", []):
        if theme:
            results.append(("market_brief.key_themes_ko[]", theme))
    for sb in data.get("symbol_briefs", []):
        sym = sb.get("symbol", "?")
        for key in ("brief_ko", "key_risk_ko", "key_opportunity_ko"):
            val = sb.get(key, "")
            if val:
                results.append((f"symbol_briefs.{sym}.{key}", val))
    return results


def _collect_all_fields(data: dict) -> list[tuple[str, str]]:
    """Return (field_path, text) for ALL text fields (en + ko)."""
    results: list[tuple[str, str]] = []
    mb = data.get("market_brief", {})
    for key in ("summary_en", "summary_ko", "watch_points_en", "watch_points_ko"):
        val = mb.get(key, "")
        if val:
            results.append((f"market_brief.{key}", val))
    for lang in ("en", "ko"):
        for theme in mb.get(f"key_themes_{lang}", []):
            if theme:
                results.append((f"market_brief.key_themes_{lang}[]", theme))
    for sb in data.get("symbol_briefs", []):
        sym = sb.get("symbol", "?")
        for key in ("brief_en", "brief_ko", "key_risk_en", "key_risk_ko",
                    "key_opportunity_en", "key_opportunity_ko"):
            val = sb.get(key, "")
            if val:
                results.append((f"symbol_briefs.{sym}.{key}", val))
    return results


def validate_output_quality(data: dict) -> list[str]:
    """Detect causal cross-domain language and Japanese chars in _ko fields.

    Returns a list of human-readable violation strings (empty = clean).
    """
    violations: list[str] = []

    # Check A: cross-domain causal connectives (all text fields)
    for field_path, text in _collect_all_fields(data):
        if not _has_causal_connector(text):
            continue
        domains = _domains_in_text(text)
        if len(domains) >= 2:
            snippet = text[:80].replace("\n", " ")
            violations.append(
                f"[인과/causal] {field_path}: 무관한 도메인({', '.join(domains)}) "
                f"연결 감지 — '{snippet}'"
            )

    # Check B: Japanese hiragana/katakana in _ko fields only
    for field_path, text in _collect_ko_fields(data):
        match = _JP_RE.search(text)
        if match:
            snippet = text[:80].replace("\n", " ")
            violations.append(
                f"[일본어/japanese] {field_path}: 히라가나/카타카나 감지 "
                f"('{match.group()}') — '{snippet}'"
            )

    return violations
```

- [ ] **Step 2: Run all quality tests**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -m pytest collect/test_collect_brief.py::TestValidateOutputQuality -v
```

Expected: all 9 tests PASS.

- [ ] **Step 3: Run full test suite to confirm no regressions**

```bash
python -m pytest collect/test_collect_brief.py -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add collect/collect_brief.py
git commit -m "feat: implement validate_output_quality (causal + Japanese detection)"
```

---

## Task 3: Add `build_correction_prompt()` and retry logic in `main()`

**Files:**
- Modify: `collect/collect_brief.py`

- [ ] **Step 1: Add `build_correction_prompt()` after `validate_output_quality()`**

```python
def build_correction_prompt(original_prompt: str, violations: list[str]) -> str:
    """Build a correction prompt that includes the original instructions plus violation details."""
    violation_block = "\n".join(f"  - {v}" for v in violations)
    return (
        original_prompt
        + f"""

━━━ CORRECTION REQUIRED ━━━
이전 출력에서 다음 위반 사항이 감지되었습니다. 해당 필드를 수정하여 전체 JSON을 다시 출력하세요.

위반 목록:
{violation_block}

수정 규칙:
1. [인과/causal] 표시 항목: 두 이벤트를 별개 문장으로 분리. 한국어는 "한편," 영어는 "Separately," 로 시작.
2. [일본어/japanese] 표시 항목: 해당 필드에서 히라가나·카타카나를 완전히 제거하고 한글로 재작성.
3. 수치·가격·날짜는 원본 데이터 테이블 값을 그대로 유지.

Raw JSON only."""
    )
```

- [ ] **Step 2: Update `main()` to add retry logic**

Find this block in `main()` (around line 734):

```python
    parsed = extract_json(raw_text)
    if parsed is None or not validate_brief(parsed):
        print("[ERROR] Brief 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)
```

Replace with:

```python
    parsed = extract_json(raw_text)
    if parsed is None or not validate_brief(parsed):
        print("[ERROR] Brief 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    # Quality check: causal language + Japanese characters
    violations = validate_output_quality(parsed)
    if violations:
        print(f"[WARN] 품질 위반 {len(violations)}건 감지 — 교정 재시도", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        correction_prompt = build_correction_prompt(prompt, violations)
        raw_text2 = call_hermes(correction_prompt)
        if raw_text2:
            parsed2 = extract_json(raw_text2)
            if parsed2 and validate_brief(parsed2):
                remaining = validate_output_quality(parsed2)
                if remaining:
                    print(f"[WARN] 교정 후에도 위반 {len(remaining)}건 잔존 — 원본 사용", file=sys.stderr)
                else:
                    print("[INFO] 교정 성공 — 수정본 사용", file=sys.stderr)
                    parsed = parsed2
            else:
                print("[WARN] 교정본 검증 실패 — 원본 사용", file=sys.stderr)
        else:
            print("[WARN] 교정 Grok 호출 실패 — 원본 사용", file=sys.stderr)
```

- [ ] **Step 3: Verify the edit is syntactically valid**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -c "from collect.collect_brief import build_correction_prompt, validate_output_quality, main; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add collect/collect_brief.py
git commit -m "feat: add correction prompt builder and retry logic in main()"
```

---

## Task 4: Add `WRITING STYLE RULES` to `build_brief_prompt()`

**Files:**
- Modify: `collect/collect_brief.py`

- [ ] **Step 1: Locate insertion point**

In `build_brief_prompt()`, find the `SELF-CHECK before JSON output:` block (around line 535). The new section goes immediately before that block.

- [ ] **Step 2: Insert `WRITING STYLE RULES` section**

Find this exact string in `build_brief_prompt()`:

```python
SELF-CHECK before JSON output:
  □ All $ prices match 전일종가 column?
  □ ⚠이미발표됨 stocks: no beat/miss/split/분할 in brief_en/ko?
  □ DOWNTREND stocks: action_bias ≠ 'buy' (unless Stage2=7 AND RS≥70)?
  □ Stage2≤1 stocks: action_bias = 'avoid'?
  □ EMA levels in brief: match 가격앵커 values?
  □ Earnings: mentioned ONLY if ≤14 days away? If absent or >14 days, completely omitted from brief_en/ko?
```

Replace with:

```python
━━━ WRITING STYLE RULES ━━━
6. CAUSAL / CONTRASTIVE LANGUAGE — NO CROSS-DOMAIN MIXING:
   Three independent domains exist:
     • crypto  : BTC, 비트코인, bitcoin, 암호화폐
     • policy  : 관세, tariff, 허가제, 제재, sanctions, 칩 규제
     • rates   : 금리, 10Y, TNX, treasury, yield, 연준, Fed
   FORBIDDEN: joining events from DIFFERENT domains with contrastive/causal connectives.
     ❌ Korean: ~는데, ~지만, ~하지만, ~인데, ~임에도, ~불구하고
     ❌ English: while, but, however, although, yet, despite
   EXCEPTION: connective is allowed ONLY when a DIRECT, VERIFIABLE causal link exists
              (e.g., "Fed 금리 인상 압박으로 성장주 하락" — rates → equity, direct link).
   REQUIRED for unrelated events: separate sentences.
     Korean → start second sentence with "한편,"
     English → start second sentence with "Separately,"
     ✅ "미중 칩 관세가 개별 허가제로 전환됐다. 한편, 비트코인은 별도 요인으로 14% 급락했다."
     ✅ "US chip tariffs shifted to licensing. Separately, Bitcoin fell 14% on unrelated factors."

7. ONE DOMAIN PER SENTENCE:
   A sentence may reference multiple domains ONLY when a direct causal link is verifiable.
   summary_ko (30-char limit): single domain theme only — never mix.

8. KOREAN OUTPUT — HANGUL ONLY:
   All _ko fields must use Hangul exclusively.
   STRICTLY FORBIDDEN: hiragana (あいうえお…), katakana (アイウエオ…).
   CJK characters (漢字) are acceptable ONLY if in standard Korean usage.

SELF-CHECK before JSON output:
  □ All $ prices match 전일종가 column?
  □ ⚠이미발표됨 stocks: no beat/miss/split/분할 in brief_en/ko?
  □ DOWNTREND stocks: action_bias ≠ 'buy' (unless Stage2=7 AND RS≥70)?
  □ Stage2≤1 stocks: action_bias = 'avoid'?
  □ EMA levels in brief: match 가격앵커 values?
  □ Earnings: mentioned ONLY if ≤14 days away? If absent or >14 days, completely omitted from brief_en/ko?
  □ Contrastive connectors (~는데/~지만/but/while): both sides same domain OR direct causal link?
  □ Unrelated events in one sentence → split + "한편,"/"Separately,"?
  □ All _ko fields: zero hiragana/katakana characters?
```

- [ ] **Step 3: Verify prompt builds without error**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -c "
from collect.collect_brief import build_brief_prompt
p = build_brief_prompt({'regime':{}, 'distribution_days':{}, 'macro':{'macro':[]}, 'symbol_detail':{}, 'prepost':{}, 'earnings':{}}, {}, 'post_close')
assert 'WRITING STYLE RULES' in p
assert 'HANGUL ONLY' in p
assert '한편,' in p
print('OK', len(p), 'chars')
"
```

Expected: `OK <N> chars` (no exception).

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest collect/test_collect_brief.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add collect/collect_brief.py
git commit -m "feat: add WRITING STYLE RULES to brief prompt (causal language + Japanese prevention)"
```

---

## Task 5: End-to-end smoke test

**Files:** read-only verification

- [ ] **Step 1: Confirm full module imports cleanly**

```bash
cd /Users/jerry/dev/market-sentiment-data
python -c "
import collect.collect_brief as cb
# Spot-check all new symbols are importable
_ = cb.validate_output_quality
_ = cb.build_correction_prompt
_ = cb.build_brief_prompt
print('All symbols importable: OK')
"
```

Expected: `All symbols importable: OK`

- [ ] **Step 2: Run complete test suite one final time**

```bash
python -m pytest collect/test_collect_brief.py -v
```

Expected: all tests PASS, zero failures.

- [ ] **Step 3: Inspect prompt output for new rules**

```bash
python -c "
from collect.collect_brief import build_brief_prompt
p = build_brief_prompt({'regime':{}, 'distribution_days':{}, 'macro':{'macro':[]}, 'symbol_detail':{}, 'prepost':{}, 'earnings':{}}, {}, 'post_close')
start = p.find('WRITING STYLE RULES')
print(p[start:start+800])
"
```

Expected: Rules 6, 7, 8 and updated SELF-CHECK visible in output.

---

## Self-Review

**Spec coverage:**
- ✅ Rule 6 (cross-domain causal connectives) → Task 4 prompt + Task 2 Check A
- ✅ Rule 7 (one domain per sentence, summary_ko) → Task 4 prompt
- ✅ Rule 8 (Hangul only) → Task 4 prompt + Task 2 Check B
- ✅ SELF-CHECK additions → Task 4
- ✅ `validate_output_quality()` → Task 2
- ✅ `build_correction_prompt()` → Task 3
- ✅ Retry logic in `main()` → Task 3
- ✅ Tests for all validation paths → Task 1

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:**
- `validate_output_quality(data: dict) -> list[str]` — consistent across Tasks 1, 2, 3
- `build_correction_prompt(original_prompt: str, violations: list[str]) -> str` — consistent across Tasks 3
- `_DOMAIN_KEYWORDS`, `_CAUSAL_KO`, `_CAUSAL_EN`, `_JP_RE` — module-level constants, referenced only in Task 2
