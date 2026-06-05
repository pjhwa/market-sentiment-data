# Brief Prompt Quality: Causal Language & Japanese Prevention

**Date**: 2026-06-05  
**File**: `market-sentiment-data/collect/collect_brief.py`  
**Scope**: `build_brief_prompt()` + post-processing validation in `main()`

---

## Problem

1. **Causal/contrastive ambiguity**: Grok sometimes joins two unrelated events with Korean connectives like `~는데`, `~지만` or English `while`/`but`, making readers infer a false causal relationship.
   - Example: "미중 칩 관세가 개별 허가제로 바뀌는데 비트코인이 14% 급락했다" — implies tariff policy caused the BTC drop.

2. **Japanese character bleed**: Korean `_ko` fields occasionally contain hiragana or katakana characters, making the output unprofessional and confusing.

---

## Solution: Two-Layer Defense

### Layer 1 — Prompt Rules (Option A)

Add `WRITING STYLE RULES` section to `build_brief_prompt()`, inserted after `ANTI-HALLUCINATION` and before `SELF-CHECK`.

**Rule 6 — No cross-domain causal connectives**

Three domains are defined:
- `crypto`: BTC, 비트코인, bitcoin, 암호화폐
- `policy`: 관세, tariff, 허가제, 제재, sanctions, 칩 규제
- `rates`: 금리, 10Y, TNX, treasury, 연준, Fed

Forbidden: joining events from different domains with `~는데`, `~지만`, `~하지만`, `~인데`, `임에도`, `불구하고` (Korean) or `while`, `but`, `however`, `although`, `yet`, `despite` (English) when no direct causal link exists.

Required for unrelated events: separate sentences, starting with `한편,` (Korean) or `Separately,` (English).

**Rule 7 — One domain per sentence**

A single sentence may reference multiple domains only when a direct, verifiable causal link exists (e.g., "Fed rate hike압박으로 성장주 하락"). Otherwise split into separate sentences.

`summary_ko` (30-char limit) must cover a single domain theme.

**Rule 8 — Korean output: Hangul only**

All `_ko` fields must use Hangul. Hiragana (あいうえお…) and Katakana (アイウエオ…) are strictly forbidden. Standard CJK characters (漢字) are acceptable only if in common Korean usage.

**SELF-CHECK additions** (appended to existing checklist):
```
□ Contrastive connectors (~는데/~지만/but/while): both sides same domain OR direct causal link verified?
□ Unrelated events in one sentence → split + "한편,"/"Separately," prefix?
□ _ko fields: zero hiragana/katakana characters?
```

---

### Layer 2 — Post-processing Validation (Option C)

Add `validate_output_quality(data: dict) -> list[str]` function returning a list of violation descriptions.

**Check A — Cross-domain causal language**

For each text field in `market_brief` and each `symbol_briefs` entry:
1. Split into sentences (split on `.` / `。` / `\n`).
2. For each sentence, count how many domains have keyword hits.
3. If ≥2 domains present AND a causal/contrastive connector is found in the sentence → violation.

Return violation strings like:
```
"[market_brief.summary_ko] 무관한 도메인 연결: '관세가 바뀌는데 비트코인이 급락'"
```

**Check B — Japanese character detection**

Regex scan all `_ko` fields for Unicode ranges:
- Hiragana: `぀–ゟ`
- Katakana: `゠–ヿ`

Return violation strings like:
```
"[symbol_briefs.TSM.brief_ko] 일본어 문자 감지: 'アイ'"
```

**Retry logic in `main()`**

```
violations = validate_output_quality(parsed)
if violations:
    # Build correction prompt: original prompt + violation list + fix instructions
    corrected = call_hermes(build_correction_prompt(prompt, violations))
    parsed = extract_json(corrected)
    # Accept corrected output even if violations remain (warn only, don't hard-block)
    # Reason: avoid data loss on stubborn LLM outputs
```

Retry count: 1 (one correction attempt). If second output still has violations, log warnings and proceed — data completeness takes priority over blocking.

---

## Files Changed

| File | Change |
|------|--------|
| `collect/collect_brief.py` | Add rules to `build_brief_prompt()`; add `validate_output_quality()`; add `build_correction_prompt()`; update `main()` retry logic |

No schema changes. No frontend changes. No other collectors affected.

---

## Non-Goals

- Detecting Japanese kanji (CJK shared range) — too many false positives with Korean hanja usage
- Blocking on second violation — data loss risk outweighs quality gain
- Changing `brief/latest.json` schema

---

## Validation

After deploy, manually inspect `brief/history/` for:
- No `~는데`/`~지만` joining cross-domain events
- No hiragana/katakana in any `_ko` field
- Retry log lines appear when violations were caught
