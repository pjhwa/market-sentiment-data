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
