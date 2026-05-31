#!/usr/bin/env python3
"""
Macro Insight AI 해석 수집기

① Sniperboard /api/macro에서 21개 심볼 데이터 수집
② Hermes/Grok으로 그룹별 AI 해석 텍스트 + 종합 요약 생성
③ macro/latest.json + macro/history/<date>_<slot>.json 저장
④ git commit + push
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from collect.git_utils import commit_and_push

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
HERMES_CMD = os.environ.get("HERMES_CMD", "/Users/jerry/.local/bin/hermes")
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))
HERMES_RETRY = int(os.environ.get("HERMES_RETRY", "1"))
SNIPERBOARD_API = os.environ.get("SNIPERBOARD_API_BASE", "http://localhost:5001")


def detect_slot(now: datetime) -> str:
    override = os.environ.get("SENTIMENT_SLOT", "").strip()
    if override in ("pre_open", "post_close"):
        return override
    return "pre_open" if 9 <= now.hour < 18 else "post_close"


def fetch_macro_data() -> list:
    try:
        resp = requests.get(f"{SNIPERBOARD_API}/api/macro", timeout=15)
        resp.raise_for_status()
        return resp.json().get("macro", [])
    except Exception as e:
        print(f"[ERROR] /api/macro 호출 실패: {e}", file=sys.stderr)
        return []


def build_prompt(macro_items: list, slot: str) -> str:
    lines = [
        f"{m['symbol']} ({m.get('name','')}) price={m.get('price','N/A')} "
        f"1d={m.get('change_pct_1d','N/A')}% 5d={m.get('change_pct_5d','N/A')}% "
        f"above_ema21={m.get('above_ema21',False)} rsi={m.get('rsi14','N/A')} "
        f"structure={m.get('market_structure','N/A')}"
        for m in macro_items
    ]
    data_block = "\n".join(lines)
    slot_kor = "장 개장 전" if slot == "pre_open" else "장 마감 후"

    return f"""You are a professional macro market analyst. Based on the following real-time macro asset data, generate a bilingual JSON insight report.

MACRO DATA ({slot_kor}):
{data_block}

WRITING RULES:
- overall.summary_en: One sentence describing the overall market in English (≤60 chars)
- overall.summary_ko: Same in Korean (≤40 chars)
- overall.bullets_en: 3 items. Each: "key signal → market implication" (English, ≤40 chars each)
  Good: "VIX drops to 15 → fear low, buy-friendly"  "QQQ/SMH uptrend → tech strength"
  Bad: "VIX 15.3 DOWNTREND"  (no bare numbers/states)
- overall.bullets_ko: Same 3 bullets in Korean (≤25 chars each)
  Good: "VIX 15 하락 → 공포 낮음, 매수 우호"
- groups.text_en: One sentence on what this group signals now (English, ≤50 chars)
- groups.text_ko: Same in Korean (≤40 chars)

Generate ONE JSON object with this EXACT schema (no prose, no code fences):
{{
  "overall": {{
    "summary_en": "...",
    "summary_ko": "...",
    "bullets_en": ["signal1 → meaning1", "signal2 → meaning2", "signal3 → meaning3"],
    "bullets_ko": ["신호1 → 의미1", "신호2 → 의미2", "신호3 → 의미3"]
  }},
  "groups": {{
    "volatility":  {{ "text_en": "...", "text_ko": "..." }},
    "breadth":     {{ "text_en": "...", "text_ko": "..." }},
    "credit":      {{ "text_en": "...", "text_ko": "..." }},
    "rates":       {{ "text_en": "...", "text_ko": "..." }},
    "commodities": {{ "text_en": "...", "text_ko": "..." }},
    "sectors":     {{ "text_en": "...", "text_ko": "..." }}
  }}
}}

Output raw JSON only."""


def call_hermes(prompt: str) -> str | None:
    cmd = [HERMES_CMD, "-z", prompt]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    for attempt in range(1 + HERMES_RETRY):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT, env=env)
            if result.returncode != 0:
                print(f"[ERROR] hermes 비정상 종료: {result.stderr[:200]}", file=sys.stderr)
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


def extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 블록 없음. 응답: {text[:300]!r}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 파싱 실패: {e}", file=sys.stderr)
        return None


VALID_GROUP_KEYS = {"volatility", "breadth", "credit", "rates", "commodities", "sectors"}


def validate(data: dict) -> bool:
    overall = data.get("overall", {})
    if not isinstance(overall, dict):
        print("[WARN] overall 누락", file=sys.stderr)
        return False
    for field in ("summary_en", "summary_ko"):
        if not overall.get(field):
            print(f"[WARN] overall.{field} 누락", file=sys.stderr)
            return False
    for field in ("bullets_en", "bullets_ko"):
        if not isinstance(overall.get(field), list) or len(overall[field]) == 0:
            print(f"[WARN] overall.{field} 누락 또는 비어있음", file=sys.stderr)
            return False
    groups = data.get("groups", {})
    if set(groups.keys()) != VALID_GROUP_KEYS:
        print(f"[WARN] groups 키 불일치: {set(groups.keys())}", file=sys.stderr)
        return False
    for key, grp in groups.items():
        if not grp.get("text_en") or not grp.get("text_ko"):
            print(f"[WARN] groups.{key}: text_en 또는 text_ko 누락", file=sys.stderr)
            return False
    return True


def main():
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    slot = detect_slot(now)
    print(f"[INFO] 슬롯: {slot}, 시각: {now_iso}")

    print("[INFO] /api/macro 데이터 수집 중...")
    macro_items = fetch_macro_data()
    if not macro_items:
        print("[ERROR] 매크로 데이터 없음 — 종료", file=sys.stderr)
        sys.exit(1)

    prompt = build_prompt(macro_items, slot)
    print("[INFO] Grok 호출 중...")
    raw_text = call_hermes(prompt)
    if raw_text is None:
        print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    parsed = extract_json(raw_text)
    if parsed is None or not validate(parsed):
        print("[ERROR] 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    snapshot = {
        "generated_at": now_iso,
        "schema_version": "2.0",
        "slot": slot,
        "overall": {
            "summary_en": parsed["overall"]["summary_en"],
            "summary_ko": parsed["overall"]["summary_ko"],
            "bullets_en": parsed["overall"]["bullets_en"],
            "bullets_ko": parsed["overall"]["bullets_ko"],
        },
        "groups": {
            key: {"text_en": grp["text_en"], "text_ko": grp["text_ko"]}
            for key, grp in parsed["groups"].items()
        },
    }

    macro_dir = REPO_PATH / "macro"
    macro_dir.mkdir(parents=True, exist_ok=True)
    history_dir = macro_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    latest_path = macro_dir / "latest.json"
    history_path = history_dir / f"{date_str}_{slot}.json"

    for path in (latest_path, history_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 저장 완료: {latest_path}")

    rel_history = str(history_path.relative_to(REPO_PATH))
    ok = commit_and_push(
        repo=REPO_PATH,
        commit_message=f"macro: {date_str} {time_str} insight update",
        files_to_add=["macro/latest.json", rel_history],
        push=True,
    )
    if not ok:
        print("[FATAL] GitHub push 실패")
        sys.exit(1)

    print("[OK] Macro Insight 수집 + push 완료")


if __name__ == "__main__":
    main()
