#!/usr/bin/env python3
"""
SniperBoard 소셜 심리 수집기 (계층 1)
hermes -z로 Grok에 질의 → JSON 파싱·검증 → git commit/push
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

# ── 설정 (환경변수 또는 상단 상수) ──────────────────────────────────────────
REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent))
HERMES_CMD = os.environ.get("HERMES_CMD", "/Users/jerry/.local/bin/hermes")
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "grok-oauth")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))  # 종목당 초

WATCHLIST = ["TSLA", "AAPL", "NVDA", "META", "AMZN", "GOOGL"]

SENTIMENT_SCORE_MAP = {
    "very_fearful": -2,
    "fearful": -1,
    "neutral": 0,
    "optimistic": 1,
    "euphoric": 2,
}

# ── 프롬프트 ────────────────────────────────────────────────────────────────
SYMBOL_PROMPT_TEMPLATE = """You are a data extraction tool, not an analyst. Look at current public X (Twitter) \
posts about ${SYMBOL} and respond with ONE JSON object ONLY — no prose, no code fences, \
no explanation before or after.

Schema (use these exact enum values):
{{
  "symbol": "{SYMBOL}",
  "sentiment": one of ["very_fearful","fearful","neutral","optimistic","euphoric"],
  "trend_vs_yesterday": one of ["cooling","stable","heating"],
  "mention_volume": one of ["low","normal","elevated","surging"],
  "key_reason": "one short sentence in Korean",
  "bot_suspected": one of ["yes","no","unclear"],
  "confidence": one of ["high","med","low"]
}}

Rules:
- Do NOT invent precise percentages. Use only the categorical enums above.
- If the sample seems thin or very noisy, set confidence to "low".
- If you cannot determine a field, use "neutral"/"stable"/"normal"/"unclear" and lower confidence.
- Output the raw JSON object and nothing else."""

MARKET_PROMPT = """You are a data extraction tool, not an analyst. Look at current public X (Twitter) \
posts about the US equity market broadly (S&P 500, rates, recession) and respond with ONE JSON object ONLY \
— no prose, no code fences, no explanation before or after.

Schema (use these exact enum values):
{
  "sentiment": one of ["very_fearful","fearful","neutral","optimistic","euphoric"],
  "trend_vs_yesterday": one of ["cooling","stable","heating"],
  "extreme_flag": one of ["none","extreme_fear","extreme_greed"],
  "key_reason": "one short sentence in Korean",
  "confidence": one of ["high","med","low"]
}

Rules:
- Do NOT invent precise percentages. Use only the categorical enums above.
- If the sample seems thin or very noisy, set confidence to "low".
- If you cannot determine a field, use "neutral"/"stable"/"none" and lower confidence.
- Output the raw JSON object and nothing else."""


# ── 유틸리티 ────────────────────────────────────────────────────────────────
def load_schema() -> dict:
    schema_path = REPO_PATH / "schema.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def call_hermes(prompt: str) -> str | None:
    """hermes -z 호출, 타임아웃 적용. 실패 시 None 반환."""
    try:
        result = subprocess.run(
            [HERMES_CMD, "-z", prompt, "--provider", HERMES_PROVIDER],
            capture_output=True,
            text=True,
            timeout=CALL_TIMEOUT,
            env={**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"},
        )
        if result.returncode != 0:
            print(f"[ERROR] hermes 비정상 종료 (rc={result.returncode}): {result.stderr[:200]}", file=sys.stderr)
            return None
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"[ERROR] hermes 타임아웃 ({CALL_TIMEOUT}초 초과)", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(f"[ERROR] hermes 명령을 찾을 수 없음: {HERMES_CMD}. PATH를 확인하거나 HERMES_CMD 환경변수로 절대경로를 지정하세요.", file=sys.stderr)
        return None


def extract_json(text: str) -> dict | None:
    """응답 텍스트에서 첫 { ~ 마지막 } 구간을 추출해 파싱."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 블록을 찾을 수 없음. 응답: {text[:300]!r}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 파싱 실패: {e}. 원문: {match.group()[:300]!r}", file=sys.stderr)
        return None


def validate_symbol_fields(data: dict, symbol: str) -> bool:
    """per-symbol 필수 필드 및 enum 값 검증."""
    required_enums = {
        "sentiment": list(SENTIMENT_SCORE_MAP.keys()),
        "trend_vs_yesterday": ["cooling", "stable", "heating"],
        "mention_volume": ["low", "normal", "elevated", "surging"],
        "bot_suspected": ["yes", "no", "unclear"],
        "confidence": ["high", "med", "low"],
    }
    for field, valid_values in required_enums.items():
        if field not in data:
            print(f"[WARN] {symbol}: 필드 누락 — {field}", file=sys.stderr)
            return False
        if data[field] not in valid_values:
            print(f"[WARN] {symbol}: {field}={data[field]!r} 허용값 아님", file=sys.stderr)
            return False
    if "key_reason" not in data or not isinstance(data["key_reason"], str):
        print(f"[WARN] {symbol}: key_reason 누락 또는 타입 오류", file=sys.stderr)
        return False
    return True


def validate_market_fields(data: dict) -> bool:
    """market 객체 필드 검증."""
    required_enums = {
        "sentiment": list(SENTIMENT_SCORE_MAP.keys()),
        "trend_vs_yesterday": ["cooling", "stable", "heating"],
        "extreme_flag": ["none", "extreme_fear", "extreme_greed"],
        "confidence": ["high", "med", "low"],
    }
    for field, valid_values in required_enums.items():
        if field not in data:
            print(f"[WARN] market: 필드 누락 — {field}", file=sys.stderr)
            return False
        if data[field] not in valid_values:
            print(f"[WARN] market: {field}={data[field]!r} 허용값 아님", file=sys.stderr)
            return False
    return True


def build_symbol_entry(raw: dict, symbol: str, now_iso: str) -> dict:
    sentiment = raw["sentiment"]
    return {
        "symbol": symbol,
        "as_of": now_iso,
        "sentiment": sentiment,
        "sentiment_score": SENTIMENT_SCORE_MAP[sentiment],
        "trend_vs_yesterday": raw["trend_vs_yesterday"],
        "mention_volume": raw["mention_volume"],
        "key_reason": raw.get("key_reason", ""),
        "bot_suspected": raw["bot_suspected"],
        "confidence": raw["confidence"],
        "source": f"{HERMES_PROVIDER} via hermes",
    }


def build_market_entry(raw: dict, now_iso: str) -> dict:
    sentiment = raw["sentiment"]
    return {
        "as_of": now_iso,
        "sentiment": sentiment,
        "sentiment_score": SENTIMENT_SCORE_MAP[sentiment],
        "trend_vs_yesterday": raw["trend_vs_yesterday"],
        "extreme_flag": raw["extreme_flag"],
        "key_reason": raw.get("key_reason", ""),
        "confidence": raw["confidence"],
    }


def git_commit_push(repo: Path, date_str: str, time_str: str) -> bool:
    """git add / commit / push. 실패 시 False 반환."""
    def run(args):
        return subprocess.run(args, cwd=repo, capture_output=True, text=True)

    run(["git", "add", "latest.json", f"history/{date_str}.json"])
    commit_msg = f"sentiment: {date_str} {time_str} update"
    result = run(["git", "commit", "-m", commit_msg])
    if result.returncode != 0:
        # 변경사항 없으면 정상 처리
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            print("[INFO] 커밋할 변경사항 없음 (이미 최신)", file=sys.stderr)
            return True
        print(f"[ERROR] git commit 실패: {result.stderr[:300]}", file=sys.stderr)
        return False

    result = run(["git", "push"])
    if result.returncode != 0:
        print(
            f"[ERROR] git push 실패: {result.stderr[:300]}\n"
            "[HINT] 인증 문제일 가능성 — PAT/deploy key 설정 또는 SSH 키를 확인하세요.",
            file=sys.stderr,
        )
        return False
    return True


# ── 메인 ────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    print(f"[INFO] 수집 시작: {now_iso}")

    success_count = 0
    total = len(WATCHLIST) + 1  # 종목 6 + 시장 전체 1

    # ── 종목별 수집 ──────────────────────────────────────────────────────────
    symbol_entries = []
    for symbol in WATCHLIST:
        print(f"[INFO] 질의 중: {symbol}")
        prompt = SYMBOL_PROMPT_TEMPLATE.replace("{SYMBOL}", symbol)
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

        entry = build_symbol_entry(parsed, symbol, now_iso)
        symbol_entries.append(entry)
        success_count += 1
        print(f"[OK]   {symbol}: sentiment={entry['sentiment']} confidence={entry['confidence']}")

    # ── 시장 전체 수집 ───────────────────────────────────────────────────────
    print("[INFO] 질의 중: MARKET")
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
            market_entry = build_market_entry(market_parsed, now_iso)
            success_count += 1
            print(f"[OK]   MARKET: sentiment={market_entry['sentiment']} extreme_flag={market_entry['extreme_flag']}")

    if market_entry is None:
        # 시장 전체 실패 시 중립 기본값으로 폴백 (수집 실패임을 confidence=low로 명시)
        market_entry = {
            "as_of": now_iso,
            "sentiment": "neutral",
            "sentiment_score": 0,
            "trend_vs_yesterday": "stable",
            "extreme_flag": "none",
            "key_reason": "시장 전체 데이터 수집 실패",
            "confidence": "low",
        }

    # ── latest.json + history/<date>.json 저장 ───────────────────────────────
    snapshot = {
        "generated_at": now_iso,
        "schema_version": "1.0",
        "market": market_entry,
        "symbols": symbol_entries,
    }

    latest_path = REPO_PATH / "latest.json"
    history_path = REPO_PATH / "history" / f"{date_str}.json"
    history_path.parent.mkdir(exist_ok=True)

    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 파일 저장 완료: {latest_path}, {history_path}")

    # ── git commit/push ──────────────────────────────────────────────────────
    push_ok = git_commit_push(REPO_PATH, date_str, time_str)
    if not push_ok:
        print("[WARN] git push 실패 — 로컬 파일은 저장됨", file=sys.stderr)

    print(f"\n{'[OK]' if push_ok else '[WARN]'} {success_count}/{total} 종목 수집 성공")


if __name__ == "__main__":
    main()
