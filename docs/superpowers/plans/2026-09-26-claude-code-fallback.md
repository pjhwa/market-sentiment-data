# Claude Code Headless Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the hermes/Grok CLI call fails for any reason (credit exhaustion, auth error, timeout), automatically fall back to Claude Code headless (`claude -p`) so the 4 AI collectors (`collect_sentiment.py`, `collect_brief.py`, `collect_earnings.py`, `collect_macro_insight.py`) keep producing data instead of skipping the cycle.

**Architecture:** Wire the fallback into the single existing choke point, `call_hermes()` in `collect/grok_utils.py`. All three failure exits (non-zero exit, timeout exhausted, binary not found) route through one helper that shells out to `claude -p <prompt> --output-format text --tools ""` (all built-in tools disabled — this is pure text generation, not an agentic session) and returns its stdout in place of `None`. Because `call_hermes_json` / `call_hermes_json_array` already retry by calling `call_hermes()` again on each JSON-retry iteration, they get the fallback for free with zero changes to their retry/parsing logic. A module-level `LAST_BACKEND` flag (`"hermes"` | `"claude_fallback"`), reset at the top of every `call_hermes()` call, lets callers know which backend actually produced the result they got back — this is what `collect_sentiment.py` uses to mark entries as a degraded fallback.

`collect_sentiment.py`'s per-symbol/per-market `source` field is the only place in the data contract that already carries provider attribution, so degraded-fallback status is encoded there as a string suffix (`"claude-code-headless (degraded fallback)"`) rather than a new boolean field — no schema change needed for `SymbolSentiment`. `MarketSentiment` has no `source` field today, so one optional field is added to `schema.json` (backward compatible, not required) so the market-level entry can carry the same attribution.

`collect_brief.py`, `collect_earnings.py`, `collect_macro_insight.py` need **no code changes** — they already call `call_hermes_json()` and inherit the fallback automatically. They have no `source`/provider field in their output today and none is added; per user instruction, only `collect_sentiment.py` marks fallback usage as *degraded* (its data depends on Grok's live X/Twitter access, which Claude Code cannot replicate; the other three collectors only need reasoning over already-fetched data, so their fallback output is not degraded).

**Tech Stack:** Python 3, `subprocess`, `unittest` + `unittest.mock`, existing `hermes` CLI, `claude` CLI (Claude Code, installed at `/Users/jerry/.local/bin/claude`, confirmed working via `claude -p "..." --output-format text --tools ""`).

**Spec:** This document (no separate spec file — requirements were established in conversation with the user: add Claude Code headless as a fallback AI backend when Grok/hermes fails, and mark `collect_sentiment.py` output as degraded when the fallback is used).

## Global Constraints

- Never hardcode paths/tokens — all new config via env vars, following `HERMES_CMD` / `HERMES_PROVIDER` precedent (`_find_hermes()` pattern).
- `additionalProperties: false` is used throughout `schema.json` — any new field must be added explicitly to the relevant `definitions` block, not just written by the producer.
- Existing `call_hermes` / `call_hermes_json` / `call_hermes_json_array` public signatures and retry semantics (env vars `HERMES_RETRY`, `JSON_PARSE_RETRY`, `JSON_RETRY_DELAY`) must not change — the fallback is purely additive inside `call_hermes()`.
- `build_symbol_entry()` and `build_market_entry()` in `collect_sentiment.py` are called from existing tests with their current positional signature — new `backend` param must default to `"hermes"` so existing tests keep passing unmodified.
- Per CLAUDE.md: any code change here requires updating `PROJECT_CONTEXT.md` and `README.md` before the session ends (env var table, collector description), included in the same commit.

## Review Focus

- **Fallback disabled entirely**: `CLAUDE_FALLBACK_ENABLED=0` must make `call_hermes()` behave exactly as before (return `None` on hermes failure, no `claude` subprocess spawned) — covered in Task 1.
- **`claude` binary missing**: if Claude Code isn't installed on a given host, `call_claude_fallback()` must fail closed (`None`), not crash the collector — covered in Task 1 (`FileNotFoundError` path).
- **Fallback also fails**: hermes fails AND claude fallback fails (e.g. both out of credit/quota) → `call_hermes()` must still return `None` cleanly so `call_hermes_json`'s existing empty-response retry loop handles it the same as today — covered in Task 1.
- **Mixed backends within one run**: `collect_sentiment.py` calls `call_hermes_json` once per TIER1 symbol plus once for TIER2 batch plus once for market — a mid-run fallback must only mark the entries produced by the fallback call as degraded, not the whole run's other entries (this is why `LAST_BACKEND` is read immediately after each call, not once globally) — covered in Task 2.
- **`degraded` string must not corrupt schema-valid JSON**: since `source` is a free-form string field with no enum in `schema.json`, appending `(degraded fallback)` text must stay a `type: string` value — no schema change required for `SymbolSentiment.source`, only for the new `MarketSentiment.source` field — covered in Task 3.

---

### Task 1: Wire Claude Code headless fallback into `grok_utils.call_hermes()`

**Files:**
- Modify: `collect/grok_utils.py`
- Test: `collect/test_grok_utils.py`

**Interfaces:**
- Produces: `grok_utils.LAST_BACKEND: str` (module-level, values `"hermes"` or `"claude_fallback"`, reset to `"hermes"` at the start of every `call_hermes()` call).
- Produces: `grok_utils.get_last_backend() -> str` — returns current value of `LAST_BACKEND`.
- Produces: `grok_utils.call_claude_fallback(prompt: str, timeout: int | None = None) -> str | None` — standalone, testable helper.
- Consumes: nothing new from other tasks (this is the foundation task).

- [ ] **Step 1: Write failing tests for `call_claude_fallback`**

Add to `collect/test_grok_utils.py`, after the `TestCallHermes` class:

```python
class TestCallClaudeFallback(unittest.TestCase):
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run):
        mock_run.return_value = _proc('{"ok": true}')
        result = gu.call_claude_fallback("test prompt")
        self.assertEqual(result, '{"ok": true}')

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_on_nonzero_exit(self, mock_run):
        mock_run.return_value = _proc("", returncode=1, stderr="usage limit reached")
        result = gu.call_claude_fallback("test prompt")
        self.assertIsNone(result)

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=180)
        result = gu.call_claude_fallback("test prompt")
        self.assertIsNone(result)

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_on_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = gu.call_claude_fallback("test prompt")
        self.assertIsNone(result)

    @patch("collect.grok_utils.subprocess.run")
    def test_uses_tools_disabled_and_text_output(self, mock_run):
        mock_run.return_value = _proc('{}')
        gu.call_claude_fallback("test prompt")
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertIn("--output-format", cmd)
        self.assertIn("text", cmd)
        self.assertIn("--tools", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")

    @patch("collect.grok_utils.subprocess.run")
    def test_custom_timeout_used(self, mock_run):
        mock_run.return_value = _proc('{}')
        gu.call_claude_fallback("prompt", timeout=60)
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 60)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest collect/test_grok_utils.py -v -k TestCallClaudeFallback`
Expected: FAIL with `AttributeError: module 'collect.grok_utils' has no attribute 'call_claude_fallback'`

- [ ] **Step 3: Implement `_find_claude`, config, and `call_claude_fallback` in `grok_utils.py`**

Add right after the existing `HERMES_*` module-level block (after line 42, before `def call_hermes`):

```python
def _find_claude() -> str:
    """CLAUDE_FALLBACK_CMD env var → PATH search → platform-specific defaults."""
    if val := os.environ.get("CLAUDE_FALLBACK_CMD"):
        return val
    if found := shutil.which("claude"):
        return found
    for p in [
        Path.home() / ".local/bin/claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ]:
        if p.exists():
            return str(p)
    return str(Path.home() / ".local/bin/claude")


CLAUDE_FALLBACK_CMD     = _find_claude()
CLAUDE_FALLBACK_ENABLED = os.environ.get("CLAUDE_FALLBACK_ENABLED", "1") != "0"
CLAUDE_FALLBACK_TIMEOUT = int(os.environ.get("CLAUDE_FALLBACK_TIMEOUT", "180"))

LAST_BACKEND = "hermes"


def get_last_backend() -> str:
    """Which backend produced the most recent call_hermes() result: 'hermes' or 'claude_fallback'."""
    return LAST_BACKEND


def call_claude_fallback(prompt: str, timeout: int | None = None) -> str | None:
    """Call Claude Code headless (`claude -p`) as a fallback when hermes/Grok fails.

    All built-in tools are disabled (--tools "") — this is a pure text completion,
    not an agentic session. No retry here; retry is handled by the caller
    (call_hermes_json's JSON-retry loop calls call_hermes, which calls this, again).
    """
    cmd = [CLAUDE_FALLBACK_CMD, "-p", prompt, "--output-format", "text", "--tools", ""]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    effective_timeout = timeout if timeout is not None else CLAUDE_FALLBACK_TIMEOUT

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=effective_timeout, env=env
        )
        if result.returncode != 0:
            print(
                f"[ERROR] claude fallback 비정상 종료 (rc={result.returncode}): {result.stderr[:300]}",
                file=sys.stderr,
            )
            return None
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"[ERROR] claude fallback 타임아웃 ({effective_timeout}초)", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(
            f"[ERROR] claude 명령 없음: {CLAUDE_FALLBACK_CMD}. "
            "CLAUDE_FALLBACK_CMD 환경변수로 절대경로를 지정하거나 PATH를 확인하세요.",
            file=sys.stderr,
        )
        return None
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest collect/test_grok_utils.py -v -k TestCallClaudeFallback`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add collect/grok_utils.py collect/test_grok_utils.py
git commit -m "feat: add call_claude_fallback() standalone Claude Code headless helper"
```

- [ ] **Step 6: Write failing tests for `call_hermes()` falling back**

Add to `collect/test_grok_utils.py`, after `TestCallClaudeFallback`:

```python
class TestCallHermesFallback(unittest.TestCase):
    def _run(self, cmd, *args, **kwargs):
        # cmd[0] distinguishes which binary the code thinks it's invoking
        if cmd[0] == "HERMES_BIN":
            return self._hermes_result
        elif cmd[0] == "CLAUDE_BIN":
            return self._claude_result
        raise AssertionError(f"unexpected cmd: {cmd}")

    def setUp(self):
        gu.HERMES_CMD = "HERMES_BIN"
        gu.CLAUDE_FALLBACK_CMD = "CLAUDE_BIN"
        gu.CLAUDE_FALLBACK_ENABLED = True

    @patch("collect.grok_utils.subprocess.run")
    def test_falls_back_on_hermes_nonzero_exit(self, mock_run):
        self._hermes_result = _proc("", returncode=1, stderr="credit exhausted")
        self._claude_result = _proc('{"ok": true}')
        mock_run.side_effect = self._run

        result = gu.call_hermes("prompt")
        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(gu.get_last_backend(), "claude_fallback")

    @patch("collect.grok_utils.subprocess.run")
    def test_falls_back_on_hermes_file_not_found(self, mock_run):
        def run(cmd, *a, **k):
            if cmd[0] == "HERMES_BIN":
                raise FileNotFoundError()
            return self._claude_result
        self._claude_result = _proc('{"ok": true}')
        mock_run.side_effect = run

        result = gu.call_hermes("prompt")
        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(gu.get_last_backend(), "claude_fallback")

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_when_both_backends_fail(self, mock_run):
        self._hermes_result = _proc("", returncode=1)
        self._claude_result = _proc("", returncode=1)
        mock_run.side_effect = self._run

        result = gu.call_hermes("prompt")
        self.assertIsNone(result)

    @patch("collect.grok_utils.subprocess.run")
    def test_fallback_disabled_returns_none_without_calling_claude(self, mock_run):
        gu.CLAUDE_FALLBACK_ENABLED = False
        mock_run.return_value = _proc("", returncode=1)

        result = gu.call_hermes("prompt")
        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 1)  # hermes only, no fallback attempt

    @patch("collect.grok_utils.subprocess.run")
    def test_last_backend_resets_to_hermes_on_success(self, mock_run):
        gu.LAST_BACKEND = "claude_fallback"  # simulate leftover state from a prior call
        mock_run.return_value = _proc('{"ok": true}')  # HERMES_BIN succeeds directly
        self._hermes_result = _proc('{"ok": true}')

        def run(cmd, *a, **k):
            self.assertEqual(cmd[0], "HERMES_BIN")
            return _proc('{"ok": true}')
        mock_run.side_effect = run

        gu.call_hermes("prompt")
        self.assertEqual(gu.get_last_backend(), "hermes")
```

- [ ] **Step 7: Run to verify failure**

Run: `python -m pytest collect/test_grok_utils.py -v -k TestCallHermesFallback`
Expected: FAIL (hermes failures currently return `None` immediately, no fallback attempted — `test_falls_back_on_hermes_nonzero_exit` and `test_falls_back_on_hermes_file_not_found` fail; `test_fallback_disabled_returns_none_without_calling_claude` passes already but keep it for regression coverage)

- [ ] **Step 8: Modify `call_hermes()` to route failures through the fallback**

In `collect/grok_utils.py`, replace the body of `call_hermes()` (the function currently spanning the `for attempt in range(1 + HERMES_RETRY):` loop) so every `return None` becomes `return _fallback_or_none(prompt, effective_timeout)`, and reset `LAST_BACKEND` at entry. Full replacement:

```python
def call_hermes(
    prompt: str,
    timeout: int | None = None,
    *,
    toolsets: str | None = None,
) -> str | None:
    """Call hermes CLI subprocess. Retries on timeout (HERMES_RETRY). Returns stdout or None.

    Does NOT retry on empty stdout — that is handled by call_hermes_json.
    On non-zero exit, exhausted timeout retries, or missing binary, falls back to
    Claude Code headless (call_claude_fallback) unless CLAUDE_FALLBACK_ENABLED=0.
    Check get_last_backend() after a successful call to see which backend answered.

    toolsets: optional comma-separated hermes toolsets (e.g. "web") passed as -t.
              Falls back to HERMES_TOOLSETS env when not provided.
    """
    global LAST_BACKEND
    LAST_BACKEND = "hermes"

    cmd = [HERMES_CMD, "-z", prompt]
    ts = toolsets if toolsets is not None else os.environ.get("HERMES_TOOLSETS", "")
    if ts:
        cmd += ["-t", ts]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    effective_timeout = timeout if timeout is not None else HERMES_TIMEOUT

    for attempt in range(1 + HERMES_RETRY):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=effective_timeout, env=env
            )
            if result.returncode != 0:
                print(
                    f"[ERROR] hermes 비정상 종료 (rc={result.returncode}): {result.stderr[:300]}",
                    file=sys.stderr,
                )
                return _fallback_or_none(prompt, effective_timeout)
            return result.stdout
        except subprocess.TimeoutExpired:
            remaining = HERMES_RETRY - attempt
            if remaining > 0:
                print(
                    f"[WARN] hermes 타임아웃 ({effective_timeout}초) — 재시도 {remaining}회 남음",
                    file=sys.stderr,
                )
            else:
                print("[ERROR] hermes 타임아웃 — 재시도 소진", file=sys.stderr)
                return _fallback_or_none(prompt, effective_timeout)
        except FileNotFoundError:
            print(
                f"[ERROR] hermes 명령 없음: {HERMES_CMD}. "
                "HERMES_CMD 환경변수로 절대경로를 지정하거나 PATH를 확인하세요.",
                file=sys.stderr,
            )
            return _fallback_or_none(prompt, effective_timeout)
    return _fallback_or_none(prompt, effective_timeout)


def _fallback_or_none(prompt: str, timeout: int) -> str | None:
    """Try Claude Code headless after hermes has exhausted its own retries. Sets LAST_BACKEND."""
    global LAST_BACKEND
    if not CLAUDE_FALLBACK_ENABLED:
        return None
    print("[WARN] hermes 실패 — Claude Code headless fallback 시도", file=sys.stderr)
    result = call_claude_fallback(prompt, timeout=CLAUDE_FALLBACK_TIMEOUT)
    if result is not None:
        LAST_BACKEND = "claude_fallback"
    else:
        print("[ERROR] Claude Code headless fallback도 실패", file=sys.stderr)
    return result
```

- [ ] **Step 9: Update the one pre-existing test whose call-count assumption changes**

`test_retries_on_timeout_and_returns_none_when_exhausted` in `TestCallHermes` currently asserts `mock_run.call_count == 3` after hermes exhausts its timeout retries. With the fallback wired in, that same mocked `subprocess.run` would now also be hit by the fallback attempt. Disable the fallback for this test so it stays focused on hermes's own retry behavior:

```python
    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_timeout_and_returns_none_when_exhausted(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="hermes", timeout=120)
        with patch.dict("os.environ", {"HERMES_RETRY": "2", "CLAUDE_FALLBACK_ENABLED": "0"}):
            import importlib
            importlib.reload(gu)
            result = gu.call_hermes("prompt")
        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 3)  # 1 original + 2 retries
```

Also add `importlib.reload(gu)` (or a `tearDown` that restores it) is already scoped inside the `with patch.dict(...)` block for this test, but since `importlib.reload(gu)` re-reads module-level env-derived globals, add a `tearDown` to `TestCallHermes` that reloads `gu` once more after each test to avoid leaking a reloaded module state into other test classes:

```python
class TestCallHermes(unittest.TestCase):
    def tearDown(self):
        import importlib
        importlib.reload(gu)
```

- [ ] **Step 10: Run the full fallback test suite**

Run: `python -m pytest collect/test_grok_utils.py -v`
Expected: PASS (all tests, including the previously-written `TestCallHermesFallback` and the updated `TestCallHermes` test)

- [ ] **Step 11: Commit**

```bash
git add collect/grok_utils.py collect/test_grok_utils.py
git commit -m "feat: fall back to Claude Code headless when hermes/Grok fails"
```

---

### Task 2: Mark `collect_sentiment.py` output as degraded when the fallback was used

**Files:**
- Modify: `collect/collect_sentiment.py`
- Test: `collect/test_collect_sentiment.py`

**Interfaces:**
- Consumes: `grok_utils.get_last_backend() -> str` from Task 1.
- Produces: `build_symbol_entry(raw, symbol, now_iso, ctx, divergence, tier=1, backend="hermes") -> dict` — `backend` is a new optional kwarg; entry `"source"` reflects it.
- Produces: `build_market_entry(raw, now_iso, backend="hermes") -> dict` — same pattern, adds a `"source"` key to the returned dict (new key, was absent before).

- [ ] **Step 1: Write failing tests for backend-aware `source`**

Add to `collect/test_collect_sentiment.py`, after `TestBuildSymbolEntryTopNews`:

```python
class TestBuildSymbolEntryBackend(unittest.TestCase):
    def _base_raw(self):
        return {
            "sentiment": "optimistic",
            "trend_vs_yesterday": "stable",
            "mention_volume": "normal",
            "key_reason_en": "Test reason",
            "key_reason_ko": "테스트 이유",
            "bot_suspected": "no",
            "confidence": "med",
        }

    def test_default_backend_source_mentions_hermes(self):
        entry = cs.build_symbol_entry(self._base_raw(), "AAPL", "2026-05-28T13:00:00Z", {}, "aligned")
        self.assertIn("hermes", entry["source"])

    def test_claude_fallback_backend_marks_degraded(self):
        entry = cs.build_symbol_entry(
            self._base_raw(), "AAPL", "2026-05-28T13:00:00Z", {}, "aligned",
            backend="claude_fallback",
        )
        self.assertIn("degraded", entry["source"].lower())
        self.assertIn("claude", entry["source"].lower())
```

Add after `TestBuildMarketEntryTopNews`:

```python
class TestBuildMarketEntryBackend(unittest.TestCase):
    def _base_raw(self):
        return {
            "sentiment": "fearful",
            "trend_vs_yesterday": "cooling",
            "extreme_flag": "none",
            "key_reason_en": "Market test reason",
            "key_reason_ko": "마켓 테스트",
            "confidence": "high",
        }

    def test_default_backend_source_mentions_hermes(self):
        entry = cs.build_market_entry(self._base_raw(), "2026-05-28T13:00:00Z")
        self.assertIn("hermes", entry["source"])

    def test_claude_fallback_backend_marks_degraded(self):
        entry = cs.build_market_entry(
            self._base_raw(), "2026-05-28T13:00:00Z", backend="claude_fallback",
        )
        self.assertIn("degraded", entry["source"].lower())
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest collect/test_collect_sentiment.py -v -k Backend`
Expected: FAIL — `build_symbol_entry() got an unexpected keyword argument 'backend'` / `build_market_entry(...)` same, and market entry has no `"source"` key at all yet.

- [ ] **Step 3: Add `backend` param to `build_symbol_entry`**

In `collect/collect_sentiment.py`, replace the function at line 398:

```python
def build_symbol_entry(raw: dict, symbol: str, now_iso: str, ctx: dict, divergence: str, tier: int = 1, backend: str = "hermes") -> dict:
    sentiment = raw["sentiment"]
    if backend == "hermes":
        source = f"{'grok-oauth' if not HERMES_PROVIDER else HERMES_PROVIDER} via hermes"
    else:
        source = "claude-code-headless (degraded fallback)"
    entry = {
        "symbol": symbol,
        "tier": tier,
        "as_of": now_iso,
        "sentiment": sentiment,
        "sentiment_score": SENTIMENT_SCORE_MAP[sentiment],
        "trend_vs_yesterday": raw["trend_vs_yesterday"],
        "mention_volume": raw["mention_volume"],
        "key_reason_en": raw.get("key_reason_en", ""),
        "key_reason_ko": raw.get("key_reason_ko", ""),
        "bot_suspected": raw["bot_suspected"],
        "confidence": raw["confidence"],
        "source": source,
    }
```

(Leave the rest of the function body — `price_context`, `divergence`, `top_news` handling below it — unchanged.)

- [ ] **Step 4: Add `backend` param and `source` field to `build_market_entry`**

Replace the function at line 424:

```python
def build_market_entry(raw: dict, now_iso: str, backend: str = "hermes") -> dict:
    sentiment = raw["sentiment"]
    source = (
        f"{'grok-oauth' if not HERMES_PROVIDER else HERMES_PROVIDER} via hermes"
        if backend == "hermes"
        else "claude-code-headless (degraded fallback)"
    )
    return {
        "as_of": now_iso,
        "sentiment": sentiment,
        "sentiment_score": SENTIMENT_SCORE_MAP[sentiment],
        "trend_vs_yesterday": raw["trend_vs_yesterday"],
        "extreme_flag": raw["extreme_flag"],
        "key_reason_en": raw.get("key_reason_en", ""),
        "key_reason_ko": raw.get("key_reason_ko", ""),
        "confidence": raw["confidence"],
        "source": source,
        "top_news": raw.get("top_news") if validate_top_news(raw.get("top_news")) and raw.get("top_news") is not None else None,
    }
```

- [ ] **Step 5: Run to verify the backend tests pass (schema not touched yet, that's Task 3)**

Run: `python -m pytest collect/test_collect_sentiment.py -v -k Backend`
Expected: PASS (4 tests)

- [ ] **Step 6: Wire `get_last_backend()` into the three call sites**

In `collect/collect_sentiment.py`:

1. Update the import at the top (line 18-24) to also import `get_last_backend`:

```python
from collect.grok_utils import (
    HERMES_PROVIDER,
    call_hermes_json,
    call_hermes_json_array,
    extract_json,
    extract_json_array,
    get_last_backend,
)
```

2. TIER1 loop — after line 489 (`_, parsed = call_hermes_json(...)`), capture the backend and pass it into `build_symbol_entry` at line 498:

```python
        _, parsed = call_hermes_json(prompt, validator=lambda d: validate_symbol_fields(d, symbol))
        if parsed is None:
            print(f"[SKIP] {symbol}: Grok 응답 최종 실패 (JSON/검증)", file=sys.stderr)
            continue
        backend = get_last_backend()

        close_dir = fetch_close_direction(symbol)
        sentiment_score = SENTIMENT_SCORE_MAP[parsed["sentiment"]]
        divergence = compute_divergence(close_dir, sentiment_score)

        entry = build_symbol_entry(parsed, symbol, now_iso, ctx, divergence, tier=1, backend=backend)
```

3. TIER2 batch — after line 528 (`_, batch_parsed = call_hermes_json_array(batch_prompt)`), capture the backend once for the whole batch and pass it at line 549:

```python
        _, batch_parsed = call_hermes_json_array(batch_prompt)
        batch_backend = get_last_backend()

        if batch_parsed is None:
            print("[SKIP] TIER2 배치: Grok 응답 최종 실패 (JSON/배열)", file=sys.stderr)
        else:
            ...
                entry = build_symbol_entry(item, symbol, now_iso, ctx, divergence, tier=2, backend=batch_backend)
```

(Keep the surrounding loop body — symbol/tier2_map lookups, validation, divergence computation — unchanged; only the `build_symbol_entry(...)` call gains `backend=batch_backend`.)

4. Market — after line 579 (`_, market_parsed = call_hermes_json(MARKET_PROMPT, validator=validate_market_fields)`), capture the backend and pass it at line 585:

```python
    _, market_parsed = call_hermes_json(MARKET_PROMPT, validator=validate_market_fields)
    market_backend = get_last_backend()
    market_entry = None

    if market_parsed is None:
        print("[SKIP] MARKET: Grok 응답 최종 실패 (JSON/검증)", file=sys.stderr)
    else:
            market_entry = build_market_entry(market_parsed, now_iso, backend=market_backend)
```

- [ ] **Step 7: Run the full collector test file**

Run: `python -m pytest collect/test_collect_sentiment.py -v`
Expected: PASS (all tests, no regressions in pre-existing classes)

- [ ] **Step 8: Commit**

```bash
git add collect/collect_sentiment.py collect/test_collect_sentiment.py
git commit -m "feat: mark collect_sentiment entries as degraded when Claude Code fallback was used"
```

---

### Task 3: Add optional `source` field to `MarketSentiment` in `schema.json`, update docs

**Files:**
- Modify: `schema.json`
- Modify: `PROJECT_CONTEXT.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing (schema/docs only).
- Produces: nothing consumed by later tasks (this is the last task).

- [ ] **Step 1: Add `source` to `MarketSentiment` definition**

In `schema.json`, the `MarketSentiment` definition starts at line 205. It is currently:

```json
    "MarketSentiment": {
      "type": "object",
      "required": [
        "as_of", "sentiment", "sentiment_score",
        "trend_vs_yesterday", "extreme_flag", "key_reason_en", "key_reason_ko", "confidence"
      ],
      "additionalProperties": false,
      "properties": {
```

Leave `required` unchanged (the field stays optional, matching `SymbolSentiment.price_context`/`divergence`/`top_news` precedent). Add a `"source"` property to the `properties` block (alongside `confidence`, mirroring `SymbolSentiment.source` at line 115):

```json
        "source": {
          "type": "string",
          "description": "출처 추적용 문자열 (예: 'grok-oauth via hermes', 'claude-code-headless (degraded fallback)'). v2.0에 추가된 optional 필드 — MARKET 항목에도 provider attribution 필요해짐 (Claude Code fallback 도입, 2026-09)."
        },
```

- [ ] **Step 2: Verify existing sentiment snapshots still validate**

Run:
```bash
python3 -c "
import json
from jsonschema import validate
schema = json.load(open('schema.json'))
data = json.load(open('sentiment/latest.json'))
validate(instance=data, schema=schema)
print('OK: latest.json still validates against updated schema.json')
"
```
Expected: `OK: latest.json still validates against updated schema.json` (adding an optional field never breaks validation of documents that don't use it).

- [ ] **Step 3: Update `PROJECT_CONTEXT.md`**

Add three rows to the Environment Variables table (after the `HERMES_RETRY` row, around line 106):

```markdown
| `CLAUDE_FALLBACK_CMD` | auto-detect (`shutil.which` → `~/.local/bin` → `/opt/homebrew/bin` → `/usr/local/bin`) | all collectors |
| `CLAUDE_FALLBACK_ENABLED` | `1` (set to `0` to disable) | all collectors |
| `CLAUDE_FALLBACK_TIMEOUT` | `180` | all collectors |
```

Add a short paragraph near the "Most Important Principle" / collector descriptions section noting: when hermes/Grok fails (any reason — credit exhaustion, auth error, timeout), `grok_utils.call_hermes()` automatically retries via Claude Code headless (`claude -p ... --tools ""`) before giving up; `collect_sentiment.py` marks entries produced this way with `"source": "claude-code-headless (degraded fallback)"` because it depends on Grok's live X/Twitter access which Claude Code cannot replicate — the other 3 collectors' fallback output is not marked degraded since they only reason over already-fetched data.

Also bump the "AUTO-GENERATED" date at the top of the file to today's date.

- [ ] **Step 4: Update `README.md`**

Near the `HERMES_CMD` / `HERMES_PROVIDER` env var rows (around line 295-296), add:

```markdown
| `CLAUDE_FALLBACK_ENABLED` | `1` | Fall back to Claude Code headless when hermes/Grok fails (`0` to disable) |
```

Near the collector description that mentions `hermes -z` (around line 94), add one sentence: hermes/Grok failures automatically retry via Claude Code headless before the collector gives up on that item.

- [ ] **Step 5: Commit**

```bash
git add schema.json PROJECT_CONTEXT.md README.md
git commit -m "docs: document Claude Code headless fallback env vars and schema field"
```

---

## Manual Smoke Test (after all 3 tasks are committed)

Not automated — run once by hand to confirm the real `claude` binary works end-to-end through the wired-up code path, not just mocks:

```bash
cd /Users/jerry/dev/market-sentiment-data
python3 -c "
import collect.grok_utils as gu
gu.HERMES_CMD = '/nonexistent/hermes'  # force hermes to fail with FileNotFoundError
raw, parsed = gu.call_hermes_json('Reply with exactly this JSON and nothing else: {\"ok\": true}')
print('backend:', gu.get_last_backend())
print('parsed:', parsed)
assert gu.get_last_backend() == 'claude_fallback'
assert parsed == {'ok': True}
print('SMOKE TEST PASSED')
"
```
Expected: `backend: claude_fallback`, `parsed: {'ok': True}`, `SMOKE TEST PASSED`.
