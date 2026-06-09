#!/usr/bin/env python3
"""
브리핑 자동 검증·개선 오케스트레이터 (Auto Verify-and-Improve)

cron: 15 7 * * * (매일 07:15 UTC = 16:15 KST, 브리핑 생성 30분 후)

워크플로우:
  1. verify_briefing (A-D 자동 체크 + E Claude 독립 검증)
  2. PASS → 검증 결과 저장, 종료
  3. FAIL → Claude에게 오류 리포트 + 현재 프롬프트 전달
           → Claude가 collect_morning_briefing.py 수정 (old/new JSON diff)
           → 변경사항 적용 + git 커밋

실행:
  python3 -m collect.auto_improve            # 최신 briefing/latest.json 검증
  python3 -m collect.auto_improve --dry-run  # 변경사항 출력만, 파일 미수정
"""

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
BRIEFING_COLLECTOR = REPO_PATH / "collect" / "collect_morning_briefing.py"
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "/Users/jerry/.local/bin/claude")
IMPROVE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT_IMPROVE", "300"))


# ─── 검증 실행 ────────────────────────────────────────────────────────────────

def run_verification(briefing_path: Path) -> dict:
    """verify_briefing을 subprocess로 실행, JSON 결과 반환."""
    date_str = date.today().isoformat()
    verify_json = REPO_PATH / "briefing" / f"verify_{date_str}.json"

    cmd = [
        sys.executable, "-m", "collect.verify_briefing",
        "--json",
    ]
    env = {**os.environ, "PYTHONPATH": str(REPO_PATH)}

    print("[1/4] verify_briefing 실행 중...")
    result = subprocess.run(
        cmd, capture_output=False, text=True, env=env, cwd=str(REPO_PATH)
    )

    if verify_json.exists():
        with open(verify_json, encoding="utf-8") as f:
            return json.load(f)

    return {
        "overall_passed": result.returncode == 0,
        "error_count": 0 if result.returncode == 0 else 1,
        "checks": [],
        "grok_report": {},
    }


# ─── 프롬프트 섹션 추출 ──────────────────────────────────────────────────────

def read_prompt_sections() -> str:
    """collect_morning_briefing.py에서 핵심 프롬프트 함수 부분만 추출."""
    src = BRIEFING_COLLECTOR.read_text(encoding="utf-8")
    lines = src.splitlines()

    sections = []
    capture_funcs = {
        "def build_global_context_prompt(",
        "def build_prompt(",
        "def validate_global_context(",
        "def _format_global_context_block(",
    }

    i = 0
    while i < len(lines):
        line = lines[i]
        if any(marker in line for marker in capture_funcs):
            # 함수 시작부터 다음 최상위 def/class까지 캡처
            start = i
            j = i + 1
            while j < len(lines):
                if lines[j] and not lines[j][0].isspace() and lines[j].startswith("def "):
                    break
                j += 1
            func_lines = lines[start:j]
            # 너무 길면 앞 100줄만
            if len(func_lines) > 120:
                func_lines = func_lines[:120] + ["    # ... (truncated)"]
            sections.append("\n".join(func_lines))
            i = j
        else:
            i += 1

    return "\n\n".join(sections)


# ─── Claude 호출 (개선 제안) ─────────────────────────────────────────────────

def call_claude_improve(error_summary: str, prompt_source: str) -> Optional[dict]:
    """오류 리포트와 현재 프롬프트를 Claude에게 전달, 수정 JSON 반환.

    반환 형식:
    {
      "changes": [{"old": "...", "new": "..."}],
      "explanation": "one-line summary"
    }
    """
    prompt = f"""You are a senior prompt engineer improving an AI briefing generator.

The generator (collect_morning_briefing.py) uses Grok to produce daily financial briefings.
The verification system found errors in today's briefing.

══════════════════════════════════════════════════════════════
ERRORS FOUND (from automated + Claude verification):
══════════════════════════════════════════════════════════════
{error_summary}

══════════════════════════════════════════════════════════════
CURRENT PROMPT SOURCE (key functions only):
══════════════════════════════════════════════════════════════
{prompt_source[:6000]}

══════════════════════════════════════════════════════════════
TASK:
══════════════════════════════════════════════════════════════
Analyze each error, identify which prompt rule is missing or weak, and produce targeted fixes.

Rules for your response:
- Change only the MINIMUM text needed to prevent each error from recurring
- Do not restructure or reformat unchanged sections
- Each "old" value must be a UNIQUE substring that exists verbatim in the source above
- If an error needs a new rule added (not replacement), use the surrounding context as "old"
  and include that context unchanged in "new" with the new rule appended
- If an error cannot be safely fixed automatically, set "old"/"new" to empty strings
  and explain in the "manual_review" field
- If no changes are needed (all errors are data issues, not prompt issues), return empty changes

Output ONLY valid JSON — no markdown, no explanation outside the JSON:
{{
  "changes": [
    {{
      "old": "exact substring to find in the file (verbatim)",
      "new": "replacement text"
    }}
  ],
  "explanation": "one-line summary of what was fixed and why",
  "manual_review": "describe any errors that need human review (empty string if none)"
}}"""

    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    try:
        result = subprocess.run(
            [CLAUDE_CMD, "-p", prompt],
            capture_output=True, text=True, timeout=IMPROVE_TIMEOUT, env=env,
        )
        if result.returncode != 0:
            print(f"[WARN] Claude 호출 실패 (exit={result.returncode})", file=sys.stderr)
            return None
        raw = result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[WARN] Claude 호출 예외: {e}", file=sys.stderr)
        return None

    import re
    # markdown 코드 펜스 제거 후 JSON 추출
    stripped = re.sub(r"```(?:json)?\s*", "", raw)
    stripped = re.sub(r"```\s*", "", stripped)
    m = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not m:
        print("[WARN] Claude 응답에서 JSON 추출 실패", file=sys.stderr)
        print(f"[DEBUG] Claude raw output (first 500):\n{raw[:500]}", file=sys.stderr)
        return None

    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"[WARN] Claude JSON 파싱 실패: {e}", file=sys.stderr)
        return None


# ─── 변경사항 적용 ────────────────────────────────────────────────────────────

def apply_changes(changes: list[dict], dry_run: bool = False) -> int:
    """collect_morning_briefing.py에 old→new 치환 적용. 성공 건수 반환."""
    if not changes:
        return 0

    src = BRIEFING_COLLECTOR.read_text(encoding="utf-8")
    applied = 0

    for ch in changes:
        old = ch.get("old", "")
        new = ch.get("new", "")
        if not old:
            continue
        if old not in src:
            print(f"[WARN] 변경 대상 문자열을 파일에서 찾지 못함 (첫 80자): {old[:80]!r}",
                  file=sys.stderr)
            continue
        if dry_run:
            print(f"[DRY-RUN] 변경 예정:\n  OLD: {old[:80]!r}\n  NEW: {new[:80]!r}")
            applied += 1
        else:
            src = src.replace(old, new, 1)
            applied += 1
            print(f"[APPLY] 적용: {old[:60]!r} → {new[:60]!r}")

    if not dry_run and applied > 0:
        BRIEFING_COLLECTOR.write_text(src, encoding="utf-8")
        print(f"[OK] {BRIEFING_COLLECTOR.name} 저장 완료 ({applied}건 적용)")

    return applied


# ─── 오류 요약 포맷 ──────────────────────────────────────────────────────────

def format_errors(report: dict) -> str:
    checks = report.get("checks", [])
    errors = [c for c in checks if not c.get("passed") and c.get("severity") in ("error", "warning")]

    if not errors:
        # Claude 검증 결과도 확인
        gr = report.get("grok_report", {})
        parts = []
        gi = [g for g in gr.get("global_issues", [])
              if g.get("accuracy") not in ("accurate", "unverifiable", "")]
        if gi:
            parts.append("Global issues inaccurate: " + ", ".join(
                f"rank={g['rank']}: {g.get('note','')}" for g in gi))
        mn = [n for n in gr.get("missing_major_news", []) if n.get("severity") == "critical"]
        if mn:
            parts.append("Critical missing news: " + ", ".join(n.get("title","") for n in mn))
        mc = gr.get("market_mood_check", {})
        if mc.get("assessment") == "inaccurate":
            parts.append(f"Market mood inaccurate: {mc.get('note','')}")
        sc = gr.get("sector_check", {})
        if sc.get("assessment") == "inaccurate":
            parts.append(f"Sector analysis inaccurate: {sc.get('note','')}")
        se = gr.get("stock_errors", [])
        if se:
            parts.append("Stock errors: " + ", ".join(
                f"{e.get('symbol')}: {e.get('error','')}" for e in se[:3]))
        return "\n".join(parts) if parts else "(no specific errors identified)"

    lines = []
    for c in errors:
        lines.append(f"[{c.get('severity','error').upper()}] {c.get('name','')}: {c.get('detail','')}")
    return "\n".join(lines)


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="브리핑 자동 검증·개선")
    parser.add_argument("--dry-run", action="store_true",
                        help="변경사항 출력만, 파일 미수정·커밋 없음")
    args = parser.parse_args()

    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M KST")
    date_str = date.today().isoformat()
    print(f"[auto_improve] 시작: {now_kst}")

    # ── Step 1: 검증 실행 ──
    briefing_path = REPO_PATH / "briefing" / "latest.json"
    if not briefing_path.exists():
        print(f"[ERROR] 브리핑 파일 없음: {briefing_path}", file=sys.stderr)
        sys.exit(1)

    report = run_verification(briefing_path)

    overall_passed = report.get("overall_passed", True)
    err_count = report.get("error_count", 0)
    warn_count = report.get("warning_count", 0)

    print(f"[2/4] 검증 결과: {'PASS' if overall_passed else 'FAIL'} "
          f"(오류 {err_count}건, 경고 {warn_count}건)")

    # ── Step 2: PASS면 종료 ──
    if overall_passed:
        print("[auto_improve] PASS — 프롬프트 변경 불필요")
        _commit_verify_report(date_str, args.dry_run, passed=True)
        sys.exit(0)

    # ── Step 3: 오류 있으면 Claude에게 개선 요청 ──
    error_summary = format_errors(report)
    print(f"[3/4] 오류 요약:\n{error_summary}")

    print("[3/4] Claude에게 프롬프트 개선 요청 중...")
    prompt_source = read_prompt_sections()
    improvement = call_claude_improve(error_summary, prompt_source)

    if not improvement:
        print("[WARN] Claude 개선 제안 실패 — 수동 검토 필요", file=sys.stderr)
        _commit_verify_report(date_str, args.dry_run, passed=False)
        sys.exit(2)

    changes = improvement.get("changes", [])
    explanation = improvement.get("explanation", "auto-fix")
    manual_review = improvement.get("manual_review", "")

    if manual_review:
        print(f"[WARN] 수동 검토 필요: {manual_review}", file=sys.stderr)

    # ── Step 4: 변경사항 적용 + 커밋 ──
    applied = apply_changes(changes, dry_run=args.dry_run)

    if applied == 0:
        print("[INFO] 적용 가능한 변경사항 없음 (데이터 문제이거나 수동 검토 필요)")
        _commit_verify_report(date_str, args.dry_run, passed=False)
        sys.exit(0)

    if not args.dry_run:
        _commit_improvements(date_str, explanation)

    print(f"[auto_improve] 완료: {applied}건 프롬프트 개선 적용")
    sys.exit(0)


def _commit_verify_report(date_str: str, dry_run: bool, passed: bool = True):
    """검증 결과 JSON만 커밋."""
    if dry_run:
        return
    verify_json = REPO_PATH / "briefing" / f"verify_{date_str}.json"
    if not verify_json.exists():
        return
    status = "PASS" if passed else "FAIL"
    from collect.git_utils import commit_and_push
    commit_and_push(
        repo=REPO_PATH,
        commit_message=f"briefing: verify {date_str} — {status}",
        files_to_add=[f"briefing/verify_{date_str}.json"],
        push=True,
    )


def _commit_improvements(date_str: str, explanation: str):
    """프롬프트 개선 + 검증 결과 함께 커밋."""
    from collect.git_utils import commit_and_push
    verify_json = REPO_PATH / "briefing" / f"verify_{date_str}.json"
    files = ["collect/collect_morning_briefing.py"]
    if verify_json.exists():
        files.append(f"briefing/verify_{date_str}.json")

    summary = explanation[:60] if explanation else "prompt auto-fix"
    commit_and_push(
        repo=REPO_PATH,
        commit_message=f"briefing: auto-fix prompt — {summary}",
        files_to_add=files,
        push=True,
    )
    print(f"[4/4] 커밋 완료: briefing: auto-fix prompt — {summary}")


if __name__ == "__main__":
    main()
