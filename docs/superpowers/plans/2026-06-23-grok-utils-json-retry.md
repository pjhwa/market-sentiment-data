# Grok Utils & JSON Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract all Grok/hermes call logic into a shared `collect/grok_utils.py` module with comprehensive JSON retry, empty-response detection, and optional validator-callback retry — eliminating duplicate code across 5 collectors and making all of them resilient to transient Grok failures.

**Architecture:** Create `collect/grok_utils.py` as the single source of truth for `call_hermes()`, `extract_json()`, `extract_json_array()`, and the new high-level `call_hermes_json()` / `call_hermes_json_array()` wrappers that loop over the full hermes call on empty or malformed responses. Each collector removes its local duplicates and imports from `grok_utils`. Validation callbacks are passed into the wrappers so that schema failures also trigger retries.

**Tech Stack:** Python 3.11+, `subprocess`, `unittest` / `pytest`, existing `hermes` CLI

---

## Global Constraints

- Python 3.11+ (`str | None`, `tuple[...]` native types — no `from __future__ import annotations`)
- All new env vars have safe defaults; no existing behavior changes when env vars are unset
- `HERMES_RETRY` remains the *timeout* retry count (default 1) — unchanged semantics
- `JSON_PARSE_RETRY` default 2: budget for empty-response + JSON parse + validation retries combined
- `JSON_RETRY_DELAY` default 2.0s: sleep between JSON-level retry attempts
- Do NOT retry on `returncode != 0` or `FileNotFoundError` — those are config/auth errors, not transient
- Every collector must still pass its existing unit tests after migration
- `collect_sentiment.py` must retain the `_find_hermes()` logic (already has it); `grok_utils.py` inherits it
- Commit after each task

---

## Failure Mode Reference

| # | Failure type | Current behavior | New behavior |
|---|-------------|-----------------|--------------|
| 1 | `FileNotFoundError` (hermes binary missing) | Return `None` immediately | Unchanged |
| 2 | `subprocess.TimeoutExpired` | Retry `HERMES_RETRY` times | Unchanged |
| 3 | `returncode != 0` (auth/config error) | Return `None` immediately | Unchanged |
| 4 | Empty stdout (`""`) | Passed to `extract_json` → `None` | **Retry up to `JSON_PARSE_RETRY` times with `JSON_RETRY_DELAY` sleep** |
| 5 | JSON block not found in response | `extract_json` → `None`, no retry | **Retry up to remaining JSON budget** |
| 6 | `json.JSONDecodeError` | `extract_json` → `None`, no retry | **Retry up to remaining JSON budget** |
| 7 | Schema validation failure (wrong enum/missing field) | `sys.exit(1)` or skip, no retry | **Retry up to remaining JSON budget via `validator` callback** |

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `collect/grok_utils.py` | **Create** | hermes discovery, `call_hermes`, `extract_json`, `extract_json_array`, `call_hermes_json`, `call_hermes_json_array` |
| `collect/test_grok_utils.py` | **Create** | Unit tests for grok_utils — mock subprocess, test all retry paths |
| `collect/collect_sentiment.py` | **Modify** | Remove `_find_hermes`, `call_hermes`, `extract_json`, `extract_json_array` + 4 config lines; import from `grok_utils`; update 3 call sites |
| `collect/collect_brief.py` | **Modify** | Remove `call_hermes`, `extract_json` + 4 config lines; import from `grok_utils`; update 2 call sites |
| `collect/collect_earnings.py` | **Modify** | Remove `call_hermes`, `extract_json` + 4 config lines; import from `grok_utils`; update 1 call site |
| `collect/collect_macro_insight.py` | **Modify** | Remove `call_hermes`, `extract_json` + 4 config lines; import from `grok_utils`; update 1 call site |
| `collect/collect_morning_briefing.py` | **Modify** | Remove `call_hermes`, `extract_json` + 4 config lines; import from `grok_utils`; update 2 call sites (1차/2차) |

---

## Task 1: Create `collect/grok_utils.py` with full test suite

**Files:**
- Create: `collect/grok_utils.py`
- Create: `collect/test_grok_utils.py`

**Interfaces produced (used by Tasks 2–6):**
```python
# Constants
HERMES_CMD: str
HERMES_PROVIDER: str
HERMES_TIMEOUT: int        # default 120
HERMES_RETRY: int          # default 1 (timeout retry)
JSON_PARSE_RETRY: int      # default 2 (json/empty retry)
JSON_RETRY_DELAY: float    # default 2.0

# Functions
def call_hermes(prompt: str, timeout: int | None = None) -> str | None
def extract_json(text: str) -> dict | None
def extract_json_array(text: str) -> list | None
def call_hermes_json(
    prompt: str,
    timeout: int | None = None,
    json_retry: int = JSON_PARSE_RETRY,
    delay: float = JSON_RETRY_DELAY,
    validator: "Callable[[dict], bool] | None" = None,
) -> tuple[str | None, dict | None]
def call_hermes_json_array(
    prompt: str,
    timeout: int | None = None,
    json_retry: int = JSON_PARSE_RETRY,
    delay: float = JSON_RETRY_DELAY,
    validator: "Callable[[list], bool] | None" = None,
) -> tuple[str | None, list | None]
```

- [ ] **Step 1.1: Write the failing tests**

Create `collect/test_grok_utils.py`:

```python
"""
grok_utils 단위 테스트
python -m pytest collect/test_grok_utils.py -v
"""
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import collect.grok_utils as gu


def _proc(stdout="", returncode=0, stderr=""):
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    m.stderr = stderr
    return m


class TestCallHermes(unittest.TestCase):
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run):
        mock_run.return_value = _proc('{"ok": true}')
        result = gu.call_hermes("test prompt")
        self.assertEqual(result, '{"ok": true}')

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_on_nonzero_exit(self, mock_run):
        mock_run.return_value = _proc("", returncode=1, stderr="auth error")
        result = gu.call_hermes("test prompt")
        self.assertIsNone(result)

    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_timeout_and_returns_none_when_exhausted(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="hermes", timeout=120)
        with patch.dict("os.environ", {"HERMES_RETRY": "2"}):
            import importlib
            importlib.reload(gu)
            result = gu.call_hermes("prompt")
        self.assertIsNone(result)
        self.assertEqual(mock_run.call_count, 3)  # 1 original + 2 retries

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_on_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = gu.call_hermes("prompt")
        self.assertIsNone(result)

    @patch("collect.grok_utils.subprocess.run")
    def test_returns_empty_string_as_is(self, mock_run):
        """call_hermes itself does not retry empty — that's call_hermes_json's job."""
        mock_run.return_value = _proc("")
        result = gu.call_hermes("prompt")
        self.assertEqual(result, "")

    @patch("collect.grok_utils.subprocess.run")
    def test_custom_timeout_used(self, mock_run):
        mock_run.return_value = _proc('{}')
        gu.call_hermes("prompt", timeout=300)
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["timeout"], 300)


class TestExtractJson(unittest.TestCase):
    def test_extracts_json_object(self):
        result = gu.extract_json('Some text {"key": "value"} more text')
        self.assertEqual(result, {"key": "value"})

    def test_returns_none_on_no_block(self):
        result = gu.extract_json("no json here")
        self.assertIsNone(result)

    def test_returns_none_on_decode_error(self):
        result = gu.extract_json("{bad json: }")
        self.assertIsNone(result)

    def test_returns_none_on_empty_string(self):
        result = gu.extract_json("")
        self.assertIsNone(result)

    def test_nested_json(self):
        result = gu.extract_json('{"a": {"b": 1}}')
        self.assertEqual(result, {"a": {"b": 1}})


class TestExtractJsonArray(unittest.TestCase):
    def test_extracts_json_array(self):
        result = gu.extract_json_array('[{"a": 1}, {"b": 2}]')
        self.assertEqual(result, [{"a": 1}, {"b": 2}])

    def test_returns_none_on_no_block(self):
        result = gu.extract_json_array("no array here")
        self.assertIsNone(result)

    def test_returns_none_on_decode_error(self):
        result = gu.extract_json_array("[bad]")
        self.assertIsNone(result)

    def test_returns_none_when_not_a_list(self):
        result = gu.extract_json_array('{"key": "value"}')
        self.assertIsNone(result)

    def test_empty_array_valid(self):
        result = gu.extract_json_array("[]")
        self.assertEqual(result, [])


class TestCallHermesJson(unittest.TestCase):
    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_success_on_first_attempt(self, mock_run, mock_sleep):
        mock_run.return_value = _proc('{"sentiment": "optimistic"}')
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0)
        self.assertEqual(parsed, {"sentiment": "optimistic"})
        mock_sleep.assert_not_called()
        self.assertEqual(mock_run.call_count, 1)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_empty_response(self, mock_run, mock_sleep):
        mock_run.side_effect = [_proc(""), _proc('{"sentiment": "neutral"}')]
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0)
        self.assertEqual(parsed, {"sentiment": "neutral"})
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_json_parse_failure(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            _proc("Grok says: here is your answer: { bad json"),
            _proc('{"sentiment": "fearful"}'),
        ]
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0)
        self.assertEqual(parsed, {"sentiment": "fearful"})
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_after_all_retries_exhausted(self, mock_run, mock_sleep):
        mock_run.return_value = _proc("")
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0)
        self.assertIsNone(parsed)
        self.assertEqual(mock_run.call_count, 3)  # 1 + 2 retries

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_none_when_hermes_fails(self, mock_run, mock_sleep):
        mock_run.return_value = _proc("", returncode=1)
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0)
        self.assertIsNone(raw)
        self.assertIsNone(parsed)
        self.assertEqual(mock_run.call_count, 1)  # no JSON retry for hermes failure

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_validator_callback_triggers_retry(self, mock_run, mock_sleep):
        # First response: valid JSON but fails validator; second: passes
        mock_run.side_effect = [
            _proc('{"sentiment": "INVALID_ENUM"}'),
            _proc('{"sentiment": "optimistic"}'),
        ]
        validator = lambda d: d.get("sentiment") in {"optimistic", "fearful", "neutral"}
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0, validator=validator)
        self.assertEqual(parsed, {"sentiment": "optimistic"})
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_validator_callback_returns_none_after_exhaustion(self, mock_run, mock_sleep):
        mock_run.return_value = _proc('{"sentiment": "WRONG"}')
        validator = lambda d: d.get("sentiment") in {"optimistic", "fearful", "neutral"}
        raw, parsed = gu.call_hermes_json("prompt", json_retry=2, delay=0.0, validator=validator)
        self.assertIsNone(parsed)
        self.assertEqual(mock_run.call_count, 3)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_sleep_called_between_retries(self, mock_run, mock_sleep):
        mock_run.side_effect = [_proc(""), _proc(""), _proc('{"ok": true}')]
        gu.call_hermes_json("prompt", json_retry=2, delay=1.5)
        mock_sleep.assert_called_with(1.5)
        self.assertEqual(mock_sleep.call_count, 2)


class TestCallHermesJsonArray(unittest.TestCase):
    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_success_on_first_attempt(self, mock_run, mock_sleep):
        mock_run.return_value = _proc('[{"symbol": "TSLA"}]')
        raw, parsed = gu.call_hermes_json_array("prompt", json_retry=2, delay=0.0)
        self.assertEqual(parsed, [{"symbol": "TSLA"}])

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_retries_on_json_parse_failure(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            _proc("[bad json"),
            _proc('[{"symbol": "NVDA"}]'),
        ]
        raw, parsed = gu.call_hermes_json_array("prompt", json_retry=2, delay=0.0)
        self.assertEqual(parsed, [{"symbol": "NVDA"}])
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_returns_none_after_all_retries(self, mock_run, mock_sleep):
        mock_run.return_value = _proc("")
        raw, parsed = gu.call_hermes_json_array("prompt", json_retry=1, delay=0.0)
        self.assertIsNone(parsed)
        self.assertEqual(mock_run.call_count, 2)

    @patch("collect.grok_utils.time.sleep")
    @patch("collect.grok_utils.subprocess.run")
    def test_validator_callback_triggers_retry(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            _proc('[{"symbol": "X"}]'),   # fails validator (unknown symbol)
            _proc('[{"symbol": "TSLA"}]'), # passes
        ]
        validator = lambda lst: all(d.get("symbol") in {"TSLA", "NVDA"} for d in lst)
        raw, parsed = gu.call_hermes_json_array("prompt", json_retry=2, delay=0.0, validator=validator)
        self.assertEqual(parsed, [{"symbol": "TSLA"}])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 1.2: Run tests to confirm they all fail**

```bash
python -m pytest collect/test_grok_utils.py -v 2>&1 | head -40
```

Expected: `ModuleNotFoundError: No module named 'collect.grok_utils'` or similar.

- [ ] **Step 1.3: Implement `collect/grok_utils.py`**

Create `collect/grok_utils.py`:

```python
"""
Shared Grok/hermes call utilities for all market-sentiment collectors.

Retry strategy:
  - Timeout (subprocess.TimeoutExpired): HERMES_RETRY times (env var, default 1)
  - Empty response / JSON parse fail / validation fail: JSON_PARSE_RETRY times (env var, default 2)
    with JSON_RETRY_DELAY seconds between attempts (env var, default 2.0)
  - Non-zero exit / FileNotFoundError: no retry (config/auth error, won't self-heal)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _find_hermes() -> str:
    """HERMES_CMD env var → PATH search → platform-specific defaults."""
    if val := os.environ.get("HERMES_CMD"):
        return val
    if found := shutil.which("hermes"):
        return found
    for p in [
        Path.home() / ".local/bin/hermes",
        Path("/opt/homebrew/bin/hermes"),
        Path("/usr/local/bin/hermes"),
    ]:
        if p.exists():
            return str(p)
    return str(Path.home() / ".local/bin/hermes")


HERMES_CMD      = _find_hermes()
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
HERMES_TIMEOUT  = int(os.environ.get("HERMES_TIMEOUT", "120"))
HERMES_RETRY    = int(os.environ.get("HERMES_RETRY", "1"))
JSON_PARSE_RETRY = int(os.environ.get("JSON_PARSE_RETRY", "2"))
JSON_RETRY_DELAY = float(os.environ.get("JSON_RETRY_DELAY", "2.0"))


def call_hermes(prompt: str, timeout: int | None = None) -> str | None:
    """Call hermes CLI subprocess. Retries on timeout (HERMES_RETRY). Returns stdout or None.

    Does NOT retry on empty stdout — that is handled by call_hermes_json.
    Does NOT retry on non-zero exit (auth/config error, won't self-heal).
    """
    cmd = [HERMES_CMD, "-z", prompt]
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
                return None
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
                return None
        except FileNotFoundError:
            print(
                f"[ERROR] hermes 명령 없음: {HERMES_CMD}. "
                "HERMES_CMD 환경변수로 절대경로를 지정하거나 PATH를 확인하세요.",
                file=sys.stderr,
            )
            return None
    return None


def extract_json(text: str) -> dict | None:
    """Extract first {...} block from text and parse as JSON dict. Returns None on failure."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 블록 없음. 응답: {text[:300]!r}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 파싱 실패: {e}. 원문: {match.group()[:300]!r}", file=sys.stderr)
        return None


def extract_json_array(text: str) -> list | None:
    """Extract first [...] block from text and parse as JSON array. Returns None on failure."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 배열 없음. 응답: {text[:300]!r}", file=sys.stderr)
        return None
    try:
        result = json.loads(match.group())
        if not isinstance(result, list):
            print(f"[ERROR] JSON 배열이 아님: {type(result)}", file=sys.stderr)
            return None
        return result
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 배열 파싱 실패: {e}. 원문: {match.group()[:300]!r}", file=sys.stderr)
        return None


def call_hermes_json(
    prompt: str,
    timeout: int | None = None,
    json_retry: int = JSON_PARSE_RETRY,
    delay: float = JSON_RETRY_DELAY,
    validator: "callable | None" = None,
) -> tuple[str | None, dict | None]:
    """Call hermes and parse JSON dict. Retries the full hermes call on:
      - empty response
      - JSON block not found
      - JSON parse error
      - validator(parsed) returns False (if validator provided)

    Args:
        prompt: The prompt to send to hermes.
        timeout: Per-call subprocess timeout in seconds (overrides HERMES_TIMEOUT).
        json_retry: Max number of JSON-level retry attempts (default: JSON_PARSE_RETRY).
        delay: Seconds to sleep between retries (default: JSON_RETRY_DELAY).
        validator: Optional callable(dict) -> bool. If provided and returns False,
                   the response is considered invalid and a retry is attempted.

    Returns:
        (raw_text, parsed_dict): raw_text is the last hermes stdout (may be empty string),
        parsed_dict is the validated dict or None if all retries failed.
        Returns (None, None) if hermes itself failed (non-zero exit, FileNotFoundError).
    """
    raw = None
    for attempt in range(1 + json_retry):
        raw = call_hermes(prompt, timeout=timeout)

        if raw is None:
            # hermes failed (non-zero exit, timeout exhausted, FileNotFoundError)
            # These won't self-heal with retries
            return None, None

        if not raw.strip():
            remaining = json_retry - attempt
            if remaining > 0:
                print(
                    f"[WARN] Grok 빈 응답 — 재시도 {remaining}회 남음 ({delay}초 후)",
                    file=sys.stderr,
                )
                time.sleep(delay)
            else:
                print("[ERROR] Grok 빈 응답 — 재시도 소진", file=sys.stderr)
            continue

        parsed = extract_json(raw)

        if parsed is None:
            remaining = json_retry - attempt
            if remaining > 0:
                print(
                    f"[WARN] JSON 파싱 실패 — 재시도 {remaining}회 남음 ({delay}초 후)",
                    file=sys.stderr,
                )
                time.sleep(delay)
            else:
                print("[ERROR] JSON 파싱 실패 — 재시도 소진", file=sys.stderr)
            continue

        if validator is not None and not validator(parsed):
            remaining = json_retry - attempt
            if remaining > 0:
                print(
                    f"[WARN] 응답 검증 실패 — 재시도 {remaining}회 남음 ({delay}초 후)",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            print("[ERROR] 응답 검증 실패 — 재시도 소진", file=sys.stderr)
            return raw, None

        return raw, parsed

    return raw, None


def call_hermes_json_array(
    prompt: str,
    timeout: int | None = None,
    json_retry: int = JSON_PARSE_RETRY,
    delay: float = JSON_RETRY_DELAY,
    validator: "callable | None" = None,
) -> tuple[str | None, list | None]:
    """Call hermes and parse JSON array. Retries on empty/parse failure/validation failure.

    Args:
        prompt: The prompt to send to hermes.
        timeout: Per-call subprocess timeout in seconds (overrides HERMES_TIMEOUT).
        json_retry: Max retry attempts for JSON-level failures.
        delay: Seconds to sleep between retries.
        validator: Optional callable(list) -> bool for additional validation.

    Returns:
        (raw_text, parsed_list) or (raw_text, None) or (None, None).
    """
    raw = None
    for attempt in range(1 + json_retry):
        raw = call_hermes(prompt, timeout=timeout)

        if raw is None:
            return None, None

        if not raw.strip():
            remaining = json_retry - attempt
            if remaining > 0:
                print(
                    f"[WARN] Grok 빈 응답 (배열) — 재시도 {remaining}회 남음 ({delay}초 후)",
                    file=sys.stderr,
                )
                time.sleep(delay)
            else:
                print("[ERROR] Grok 빈 응답 (배열) — 재시도 소진", file=sys.stderr)
            continue

        parsed = extract_json_array(raw)

        if parsed is None:
            remaining = json_retry - attempt
            if remaining > 0:
                print(
                    f"[WARN] JSON 배열 파싱 실패 — 재시도 {remaining}회 남음 ({delay}초 후)",
                    file=sys.stderr,
                )
                time.sleep(delay)
            else:
                print("[ERROR] JSON 배열 파싱 실패 — 재시도 소진", file=sys.stderr)
            continue

        if validator is not None and not validator(parsed):
            remaining = json_retry - attempt
            if remaining > 0:
                print(
                    f"[WARN] 배열 검증 실패 — 재시도 {remaining}회 남음 ({delay}초 후)",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            print("[ERROR] 배열 검증 실패 — 재시도 소진", file=sys.stderr)
            return raw, None

        return raw, parsed

    return raw, None
```

- [ ] **Step 1.4: Run tests — all should pass**

```bash
python -m pytest collect/test_grok_utils.py -v
```

Expected: All tests PASS. If a test fails, fix `grok_utils.py` before continuing.

- [ ] **Step 1.5: Commit**

```bash
git add collect/grok_utils.py collect/test_grok_utils.py
git commit -m "feat: add grok_utils.py — shared hermes/JSON call with retry logic"
```

---

## Task 2: Migrate `collect_sentiment.py`

**Files:**
- Modify: `collect/collect_sentiment.py`
- Test: `collect/test_collect_sentiment.py` (no changes — verify it still passes)

**Interfaces consumed:** `call_hermes_json`, `call_hermes_json_array`, `extract_json`, `extract_json_array` from `collect.grok_utils`

- [ ] **Step 2.1: Run existing tests to establish baseline**

```bash
python -m pytest collect/test_collect_sentiment.py -v
```

Expected: All PASS. Record the count.

- [ ] **Step 2.2: Remove duplicate code and add import**

In `collect/collect_sentiment.py`, make these changes:

**Remove** the `_find_hermes` function (lines 20–34) and the `HERMES_CMD` line (line 45).

**Change** the imports block. Before:
```python
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _find_hermes() -> str:
    """HERMES_CMD 환경변수 → PATH 자동탐색 → 플랫폼별 기본 경로 순으로 탐색."""
    if val := os.environ.get("HERMES_CMD"):
        return val
    if found := shutil.which("hermes"):
        return found
    candidates = [
        Path.home() / ".local/bin/hermes",       # Linux (pip install)
        Path("/opt/homebrew/bin/hermes"),          # macOS Apple Silicon
        Path("/usr/local/bin/hermes"),             # macOS Intel / Linux
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return str(Path.home() / ".local/bin/hermes")

from collect.git_utils import commit_and_push
from collect.price_context import (
    fetch_close_direction,
    fetch_market_context,
    fetch_price_context,
)

# ── 설정 ──────────────────────────────────────────────────────────────────────
REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
HERMES_CMD = _find_hermes()
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))
```

After:
```python
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from collect.git_utils import commit_and_push
from collect.grok_utils import (
    HERMES_PROVIDER,
    call_hermes_json,
    call_hermes_json_array,
    extract_json,
    extract_json_array,
)
from collect.price_context import (
    fetch_close_direction,
    fetch_market_context,
    fetch_price_context,
)

# ── 설정 ──────────────────────────────────────────────────────────────────────
REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
```

**Remove** the `HERMES_RETRY` line and the entire `call_hermes` function (lines 360–396).

**Remove** the `extract_json` function (lines 401–410) and the `extract_json_array` function (lines 413–426).

- [ ] **Step 2.3: Update the 3 call sites**

**TIER1 call site** (around line 573 after edits). Before:
```python
        raw_text = call_hermes(prompt)
        if raw_text is None:
            print(f"[SKIP] {symbol}: hermes 호출 실패", file=sys.stderr)
            continue

        parsed = extract_json(raw_text)
        if parsed is None:
            print(f"[SKIP] {symbol}: JSON 추출 실패", file=sys.stderr)
            continue

        if not validate_symbol_fields(parsed, symbol):
            print(f"[SKIP] {symbol}: 검증 실패", file=sys.stderr)
            continue
```

After:
```python
        _, parsed = call_hermes_json(prompt, validator=lambda d: validate_symbol_fields(d, symbol))
        if parsed is None:
            print(f"[SKIP] {symbol}: Grok 응답 최종 실패 (JSON/검증)", file=sys.stderr)
            continue
```

**TIER2 batch call site** (around line 621 after edits). Before:
```python
        batch_raw = call_hermes(batch_prompt)

        if batch_raw is None:
            print("[SKIP] TIER2 배치: hermes 호출 실패", file=sys.stderr)
        else:
            batch_parsed = extract_json_array(batch_raw)
            if batch_parsed is None:
                print("[SKIP] TIER2 배치: JSON 배열 추출 실패", file=sys.stderr)
            else:
```

After:
```python
        _, batch_parsed = call_hermes_json_array(batch_prompt)

        if batch_parsed is None:
            print("[SKIP] TIER2 배치: Grok 응답 최종 실패 (JSON/배열)", file=sys.stderr)
        else:
```

**MARKET call site** (around line 676 after edits). Before:
```python
    market_raw_text = call_hermes(MARKET_PROMPT)
    market_entry = None

    if market_raw_text is None:
        print("[SKIP] MARKET: hermes 호출 실패", file=sys.stderr)
    else:
        market_parsed = extract_json(market_raw_text)
        if market_parsed is None:
            print("[SKIP] MARKET: JSON 추출 실패", file=sys.stderr)
        elif not validate_market_fields(market_parsed):
            print("[SKIP] MARKET: 검증 실패", file=sys.stderr)
        else:
```

After:
```python
    _, market_parsed = call_hermes_json(MARKET_PROMPT, validator=validate_market_fields)
    market_entry = None

    if market_parsed is None:
        print("[SKIP] MARKET: Grok 응답 최종 실패 (JSON/검증)", file=sys.stderr)
    else:
```

- [ ] **Step 2.4: Run existing tests**

```bash
python -m pytest collect/test_collect_sentiment.py -v
```

Expected: Same count PASS as baseline. Fix any failures before continuing.

- [ ] **Step 2.5: Commit**

```bash
git add collect/collect_sentiment.py
git commit -m "refactor: collect_sentiment — use grok_utils, add JSON/validator retry"
```

---

## Task 3: Migrate `collect_brief.py`

**Files:**
- Modify: `collect/collect_brief.py`
- Test: `collect/test_collect_brief.py`, `collect/test_collect_brief_context.py` (no changes)

- [ ] **Step 3.1: Run existing tests to establish baseline**

```bash
python -m pytest collect/test_collect_brief.py collect/test_collect_brief_context.py -v
```

Expected: All PASS.

- [ ] **Step 3.2: Remove duplicate code and add import**

In `collect/collect_brief.py`, make these changes:

**Remove** lines importing `shutil`, `subprocess` from the top-level imports (keep `json`, `os`, `re`, `sys`, etc.).

**Change** the config block. Before:
```python
HERMES_CMD = os.environ.get("HERMES_CMD", "/Users/jerry/.local/bin/hermes")
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))
HERMES_RETRY = int(os.environ.get("HERMES_RETRY", "1"))
```

After — replace these 4 lines with one import:
```python
from collect.grok_utils import HERMES_PROVIDER, call_hermes, call_hermes_json, extract_json
```

**Remove** the entire `call_hermes` function and the `extract_json` function from `collect_brief.py`.

- [ ] **Step 3.3: Update the 2 call sites**

**Main brief call site** (in `main()` or equivalent). Before:
```python
    raw_text = call_hermes(prompt)
    if raw_text is None:
        print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    parsed = extract_json(raw_text)
    if parsed is None or not validate_brief(parsed):
        print("[ERROR] Brief 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)
```

After:
```python
    _, parsed = call_hermes_json(prompt, validator=validate_brief)
    if parsed is None:
        print("[ERROR] Brief 생성 최종 실패 — 종료", file=sys.stderr)
        sys.exit(1)
```

**Correction call site** (quality-fix retry block). Before:
```python
        raw_text2 = call_hermes(correction_prompt)
        if raw_text2:
            parsed2 = extract_json(raw_text2)
            if parsed2 and validate_brief(parsed2):
```

After:
```python
        _, parsed2 = call_hermes_json(correction_prompt, validator=validate_brief)
        if parsed2:
```

- [ ] **Step 3.4: Run existing tests**

```bash
python -m pytest collect/test_collect_brief.py collect/test_collect_brief_context.py -v
```

Expected: All PASS.

- [ ] **Step 3.5: Commit**

```bash
git add collect/collect_brief.py
git commit -m "refactor: collect_brief — use grok_utils, add JSON/validator retry"
```

---

## Task 4: Migrate `collect_earnings.py`

**Files:**
- Modify: `collect/collect_earnings.py`

- [ ] **Step 4.1: Remove duplicate code and add import**

In `collect/collect_earnings.py`:

**Remove** lines: `HERMES_CMD`, `HERMES_PROVIDER`, `CALL_TIMEOUT`, `HERMES_RETRY` (4 lines).

**Add** import (after existing imports):
```python
from collect.grok_utils import call_hermes_json, extract_json
```

**Remove** the `call_hermes` function and `extract_json` function.

- [ ] **Step 4.2: Update the 1 call site**

Before:
```python
            raw_text = call_hermes(prompt)
            if raw_text is None:
                print("[WARN] Grok 호출 실패 — partial 스냅샷 생성 (AI 스텁으로 원시 데이터 보존)", file=sys.stderr)
                partial = True
                parsed = { ... stub ... }
            else:
                parsed = extract_json(raw_text)
                if parsed is None or not validate_earnings(parsed):
                    print("[WARN] 어닝 파싱/검증 실패 — partial 스냅샷으로 계속 (원시 데이터 우선)", file=sys.stderr)
                    partial = True
                    parsed = { ... stub ... }
```

After:
```python
            _, parsed = call_hermes_json(prompt, validator=validate_earnings)
            if parsed is None:
                print("[WARN] Grok 응답 최종 실패 — partial 스냅샷 생성 (원시 데이터 보존)", file=sys.stderr)
                partial = True
                parsed = { ... same stub as before ... }
```

Note: The two stubs (hermes fail vs parse fail) collapse into one, since both are non-fatal and produce the same partial structure.

- [ ] **Step 4.3: Verify module imports cleanly**

```bash
python -c "from collect import collect_earnings; print('OK')"
```

Expected: `OK`

- [ ] **Step 4.4: Commit**

```bash
git add collect/collect_earnings.py
git commit -m "refactor: collect_earnings — use grok_utils, add JSON/validator retry"
```

---

## Task 5: Migrate `collect_macro_insight.py`

**Files:**
- Modify: `collect/collect_macro_insight.py`

- [ ] **Step 5.1: Remove duplicate code and add import**

In `collect/collect_macro_insight.py`:

**Remove** lines: `HERMES_CMD`, `HERMES_PROVIDER`, `CALL_TIMEOUT`, `HERMES_RETRY` (4 lines).

**Add** import:
```python
from collect.grok_utils import call_hermes_json, extract_json
```

**Remove** the `call_hermes` and `extract_json` functions.

- [ ] **Step 5.2: Update the 1 call site**

Before:
```python
    raw_text = call_hermes(prompt)
    if raw_text is None:
        print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    parsed = extract_json(raw_text)
    if parsed is None or not validate(parsed, insight_groups):
        print("[ERROR] 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)
```

After:
```python
    _, parsed = call_hermes_json(prompt, validator=lambda d: validate(d, insight_groups))
    if parsed is None:
        print("[ERROR] Macro 응답 최종 실패 — 종료", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 5.3: Verify module imports cleanly**

```bash
python -c "from collect import collect_macro_insight; print('OK')"
```

Expected: `OK`

- [ ] **Step 5.4: Commit**

```bash
git add collect/collect_macro_insight.py
git commit -m "refactor: collect_macro_insight — use grok_utils, add JSON/validator retry"
```

---

## Task 6: Migrate `collect_morning_briefing.py`

**Files:**
- Modify: `collect/collect_morning_briefing.py`
- Test: `collect/test_collect_morning_briefing.py` (no changes)

This is the most complex migration because:
1. Two separate Grok calls with different semantics (1차: optional global context, 2차: fatal if fails)
2. `CALL_TIMEOUT_GLOBAL` (150s) differs from `CALL_TIMEOUT` (180s) — both stay in this file
3. `parse_global_context()` has special fallback semantics (returns `{}` on failure) — stays in this file but is refactored to call `extract_json` then `validate_global_context` separately

- [ ] **Step 6.1: Run existing tests to establish baseline**

```bash
python -m pytest collect/test_collect_morning_briefing.py -v
```

Expected: All PASS.

- [ ] **Step 6.2: Remove duplicate code and add import**

In `collect/collect_morning_briefing.py`:

**Remove** lines: `HERMES_CMD`, `HERMES_PROVIDER`, `CALL_TIMEOUT`, `HERMES_RETRY` (lines 36–39).
Keep `CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "180"))` renamed to a local constant since morning briefing uses a different default:

Actually since `grok_utils.HERMES_TIMEOUT` defaults to 120 but morning briefing needs 180 — we pass the timeout explicitly per call. Keep `CALL_TIMEOUT` and `CALL_TIMEOUT_GLOBAL` as local constants:

```python
# morning briefing has longer timeouts than other collectors
CALL_TIMEOUT        = int(os.environ.get("HERMES_TIMEOUT", "180"))
CALL_TIMEOUT_GLOBAL = int(os.environ.get("HERMES_TIMEOUT_GLOBAL", "150"))
```

**Remove** `shutil` and `subprocess` from imports (no longer needed).

**Add** import:
```python
from collect.grok_utils import call_hermes, call_hermes_json, extract_json
```

**Remove** the `call_hermes` function and `extract_json` function from this file.

- [ ] **Step 6.3: Refactor `parse_global_context` and update 1차 call site**

**Refactor `parse_global_context`** to use `extract_json` from `grok_utils` instead of inline JSON parsing. The function already uses `re.search` + `json.loads` — replace with `extract_json`:

Before (lines 836–851):
```python
def parse_global_context(text: str) -> dict:
    """1차 Grok 응답에서 글로벌 컨텍스트 JSON 추출. 실패 시 {} 반환."""
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

After:
```python
def parse_global_context(text: str) -> dict:
    """1차 Grok 응답에서 글로벌 컨텍스트 JSON 추출. 실패 시 {} 반환."""
    if not text or not text.strip():
        return {}
    data = extract_json(text)
    if data is None:
        return {}
    if not validate_global_context(data):
        return {}
    return data
```

**Update 1차 call site** (global context). Before:
```python
    global_raw = call_hermes(global_context_prompt, timeout=CALL_TIMEOUT_GLOBAL)
    if global_raw:
        global_ctx = parse_global_context(global_raw)
        if global_ctx and global_ctx.get("issues"):
            print(f"[INFO] 글로벌 이슈 {len(global_ctx['issues'])}개 수집됨")
        else:
            print("[WARN] 글로벌 컨텍스트: 이슈 없음 — fallback으로 계속 진행", file=sys.stderr)
    else:
        print("[WARN] 글로벌 컨텍스트 Grok 호출 실패 — fallback으로 계속 진행", file=sys.stderr)
```

After (use `call_hermes_json` with `validate_global_context` as validator; failure is non-fatal):
```python
    _gc_raw, _gc_parsed = call_hermes_json(
        global_context_prompt,
        timeout=CALL_TIMEOUT_GLOBAL,
        validator=validate_global_context,
    )
    if _gc_parsed is not None:
        global_ctx = _gc_parsed
        issues_count = len(global_ctx.get("issues") or [])
        if issues_count > 0:
            print(f"[INFO] 글로벌 이슈 {issues_count}개 수집됨")
        else:
            print("[WARN] 글로벌 컨텍스트: 이슈 없음 — fallback으로 계속 진행", file=sys.stderr)
    else:
        print("[WARN] 글로벌 컨텍스트 최종 실패 — fallback으로 계속 진행", file=sys.stderr)
```

**Update 2차 call site** (main briefing). Before:
```python
    raw_text = call_hermes(prompt)
    if raw_text is None:
        print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    parsed = extract_json(raw_text)
    if parsed is None or not validate_briefing(parsed):
        print("[ERROR] 브리핑 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)
```

After:
```python
    _, parsed = call_hermes_json(prompt, timeout=CALL_TIMEOUT, validator=validate_briefing)
    if parsed is None:
        print("[ERROR] 브리핑 최종 실패 — 종료", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 6.4: Run existing tests**

```bash
python -m pytest collect/test_collect_morning_briefing.py -v
```

Expected: All PASS.

- [ ] **Step 6.5: Commit**

```bash
git add collect/collect_morning_briefing.py
git commit -m "refactor: collect_morning_briefing — use grok_utils, add JSON/validator retry"
```

---

## Task 7: Full verification

- [ ] **Step 7.1: Run the complete test suite**

```bash
python -m pytest collect/ -v 2>&1
```

Expected: All tests pass (including `test_grok_utils.py`, `test_collect_sentiment.py`, `test_collect_brief.py`, `test_collect_brief_context.py`, `test_collect_morning_briefing.py`).

- [ ] **Step 7.2: Verify each collector imports cleanly**

```bash
python -c "
from collect import collect_sentiment, collect_brief, collect_earnings, collect_macro_insight, collect_morning_briefing
print('All 5 collectors import OK')
"
```

Expected: `All 5 collectors import OK`

- [ ] **Step 7.3: Dry-run earnings collector (has --dry-run flag)**

```bash
python -m collect.collect_earnings --dry-run 2>&1 | tail -10
```

Expected: Ends with `[OK]` or partial notice (no crash, no ImportError).

- [ ] **Step 7.4: Verify no duplicate hermes/grok code remains in collectors**

```bash
grep -n "def call_hermes\|def extract_json\|HERMES_CMD\s*=" \
  collect/collect_sentiment.py \
  collect/collect_brief.py \
  collect/collect_earnings.py \
  collect/collect_macro_insight.py \
  collect/collect_morning_briefing.py
```

Expected: No output (all duplicates removed).

- [ ] **Step 7.5: Confirm JSON_PARSE_RETRY env var works**

```bash
python -c "
import os
os.environ['JSON_PARSE_RETRY'] = '3'
import importlib
import collect.grok_utils as gu
importlib.reload(gu)
print('JSON_PARSE_RETRY =', gu.JSON_PARSE_RETRY)
"
```

Expected: `JSON_PARSE_RETRY = 3`

- [ ] **Step 7.6: Final commit**

```bash
git add docs/superpowers/plans/2026-06-23-grok-utils-json-retry.md
git commit -m "docs: add grok_utils JSON retry implementation plan"
```
