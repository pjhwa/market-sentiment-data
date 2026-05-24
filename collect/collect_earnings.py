#!/usr/bin/env python3
"""
Earnings Intelligence 수집기
① yfinance .calendar + .earnings_history로 어닝 데이터 수집
② Grok(Hermes)으로 어닝 리스크 해석 생성
③ earnings/latest.json + earnings/history/<date>.json 저장 → git push
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
HERMES_CMD = os.environ.get("HERMES_CMD", "/Users/jerry/.local/bin/hermes")
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))
HERMES_RETRY = int(os.environ.get("HERMES_RETRY", "1"))

WATCHLIST = ["TSLA", "AAPL", "NVDA", "META", "AMZN", "GOOGL"]
UPCOMING_WINDOW_DAYS = 60
RECENT_QUARTERS = 8


def fetch_earnings_data(symbols: list[str], today: datetime) -> tuple[list[dict], list[dict]]:
    """워치리스트 전체 어닝 데이터 수집. (upcoming_raw, recent_raw) 반환."""
    upcoming_raw = []
    recent_raw = []

    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)

            cal = ticker.calendar
            earnings_date = None
            if cal is not None and not (hasattr(cal, 'empty') and cal.empty):
                if hasattr(cal, 'columns') and 'Earnings Date' in cal.columns:
                    ed_val = cal['Earnings Date'].iloc[0] if len(cal) > 0 else None
                elif isinstance(cal, dict) and 'Earnings Date' in cal:
                    ed_val = cal['Earnings Date']
                    if isinstance(ed_val, list):
                        ed_val = ed_val[0] if ed_val else None
                else:
                    ed_val = None

                if ed_val is not None:
                    try:
                        if hasattr(ed_val, 'date'):
                            earnings_date = ed_val.date()
                        else:
                            earnings_date = datetime.strptime(str(ed_val)[:10], "%Y-%m-%d").date()
                    except Exception:
                        earnings_date = None

            eps_estimate = None
            rev_estimate_b = None
            if cal is not None:
                try:
                    if hasattr(cal, 'columns'):
                        if 'EPS Estimate' in cal.columns:
                            eps_estimate = float(cal['EPS Estimate'].iloc[0])
                        if 'Revenue Estimate' in cal.columns:
                            rev_estimate_b = round(float(cal['Revenue Estimate'].iloc[0]) / 1e9, 2)
                    elif isinstance(cal, dict):
                        if 'EPS Estimate' in cal:
                            val = cal['EPS Estimate']
                            eps_estimate = float(val[0] if isinstance(val, list) else val)
                        if 'Revenue Estimate' in cal:
                            val = cal['Revenue Estimate']
                            rev_raw = float(val[0] if isinstance(val, list) else val)
                            rev_estimate_b = round(rev_raw / 1e9, 2)
                except Exception:
                    pass

            hist = ticker.earnings_history
            beat_count = 0
            total_count = 0
            last_result = None

            if hist is not None and not hist.empty:
                hist = hist.sort_index(ascending=False)
                recent = hist.head(RECENT_QUARTERS)
                for _, row in recent.iterrows():
                    actual = row.get('epsActual') if hasattr(row, 'get') else None
                    if actual is None:
                        actual = row.get('EPS Actual')
                    estimate = row.get('epsEstimate') if hasattr(row, 'get') else None
                    if estimate is None:
                        estimate = row.get('EPS Estimate')
                    if actual is not None and estimate is not None:
                        try:
                            total_count += 1
                            if float(actual) > float(estimate):
                                beat_count += 1
                        except (TypeError, ValueError):
                            pass

                if len(hist) > 0:
                    last_row = hist.iloc[0]
                    actual = last_row.get('epsActual') or last_row.get('EPS Actual')
                    estimate_last = last_row.get('epsEstimate') or last_row.get('EPS Estimate')
                    report_date = hist.index[0]
                    if actual is not None and estimate_last is not None:
                        try:
                            actual_f = float(actual)
                            estimate_f = float(estimate_last)
                            surprise_pct = ((actual_f - estimate_f) / abs(estimate_f) * 100) if estimate_f != 0 else 0.0
                            last_result = {
                                "symbol": sym,
                                "report_date": str(report_date)[:10],
                                "eps_actual": round(actual_f, 2),
                                "eps_estimate": round(estimate_f, 2),
                                "surprise_pct": round(surprise_pct, 2),
                            }
                        except (TypeError, ValueError):
                            pass

            beat_rate = round(beat_count / total_count, 2) if total_count >= 4 else None

            if earnings_date is not None:
                days_until = (earnings_date - today.date()).days
                if 0 <= days_until <= UPCOMING_WINDOW_DAYS:
                    upcoming_raw.append({
                        "symbol": sym,
                        "earnings_date": str(earnings_date),
                        "days_until": days_until,
                        "eps_estimate": round(eps_estimate, 2) if eps_estimate is not None else None,
                        "revenue_estimate_b": rev_estimate_b,
                        "historical_beat_rate": beat_rate,
                    })

            if last_result is not None:
                recent_raw.append(last_result)

            print(f"[OK]   {sym}: earnings_date={earnings_date}, beat_rate={beat_rate}")

        except Exception as e:
            print(f"[WARN] {sym}: 수집 실패 — {e}", file=sys.stderr)

    upcoming_raw.sort(key=lambda x: x["days_until"])
    return upcoming_raw, recent_raw


def build_earnings_prompt(upcoming_raw: list[dict], recent_raw: list[dict]) -> str:
    upcoming_block = "\n".join([
        f"- {u['symbol']}: {u['days_until']}일 후 ({u['earnings_date']}), "
        f"EPS estimate={u['eps_estimate']}, revenue_estimate={u['revenue_estimate_b']}B, "
        f"historical_beat_rate={u['historical_beat_rate']}"
        for u in upcoming_raw
    ]) or "없음"

    recent_block = "\n".join([
        f"- {r['symbol']}: {r['report_date']}, "
        f"EPS actual={r['eps_actual']} vs estimate={r['eps_estimate']} "
        f"(surprise {r['surprise_pct']:+.1f}%)"
        for r in recent_raw
    ]) or "없음"

    return f"""You are a professional earnings analyst. Based on the following data, generate earnings intelligence in JSON format.

UPCOMING EARNINGS (60일 이내):
{upcoming_block}

RECENT RESULTS (지난 분기):
{recent_block}

Generate ONE JSON object with this EXACT schema (no prose, no code fences):
{{
  "upcoming_earnings": [
    {{
      "symbol": "TICKER",
      "earnings_date": "YYYY-MM-DD",
      "days_until": 0,
      "eps_estimate": null,
      "revenue_estimate_b": null,
      "historical_beat_rate": null,
      "ai_summary": "2-3문장 어닝 맥락 설명 (한국어)",
      "risk_level": "one of high/med/low",
      "action_note": "트레이더를 위한 한 줄 조언 (한국어)"
    }}
  ],
  "recent_results": [
    {{
      "symbol": "TICKER",
      "report_date": "YYYY-MM-DD",
      "eps_actual": 0.0,
      "eps_estimate": 0.0,
      "surprise_pct": 0.0,
      "ai_reaction": "시장 반응 및 트레이더 시사점 한 줄 (한국어)"
    }}
  ]
}}

risk_level 기준:
- high: 어닝 3일 이내, historical_beat_rate < 0.7, 혹은 가이던스 불확실성 높음
- med: 어닝 4-14일, beat_rate 0.7-0.85
- low: 어닝 15일 이상, beat_rate > 0.85

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


VALID_RISK_LEVELS = {"high", "med", "low"}


def validate_earnings(data: dict) -> bool:
    upcoming = data.get("upcoming_earnings")
    recent = data.get("recent_results")
    if not isinstance(upcoming, list) or not isinstance(recent, list):
        print("[WARN] upcoming_earnings 또는 recent_results 누락", file=sys.stderr)
        return False
    for item in upcoming:
        if item.get("risk_level") not in VALID_RISK_LEVELS:
            print(f"[WARN] {item.get('symbol')}: risk_level={item.get('risk_level')!r}", file=sys.stderr)
            return False
    return True


def git_commit_push(repo: Path, date_str: str, time_str: str, history_path: Path) -> bool:
    def run(args):
        return subprocess.run(args, cwd=repo, capture_output=True, text=True)

    rel_history = str(history_path.relative_to(repo))
    run(["git", "add", "earnings/latest.json", rel_history])
    result = run(["git", "commit", "-m", f"earnings: {date_str} {time_str} update"])
    if result.returncode != 0:
        if "nothing to commit" in result.stdout + result.stderr:
            print("[INFO] 커밋할 변경사항 없음", file=sys.stderr)
            return True
        print(f"[ERROR] git commit 실패: {result.stderr[:300]}", file=sys.stderr)
        return False
    result = run(["git", "push"])
    if result.returncode != 0:
        print(f"[ERROR] git push 실패: {result.stderr[:300]}", file=sys.stderr)
        return False
    return True


def main():
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    print(f"[INFO] 어닝 수집 시작: {now_iso}")

    upcoming_raw, recent_raw = fetch_earnings_data(WATCHLIST, now)
    print(f"[INFO] upcoming={len(upcoming_raw)}, recent={len(recent_raw)}")

    if not upcoming_raw and not recent_raw:
        print("[INFO] 어닝 데이터 없음 — 빈 스냅샷 저장")
        parsed = {"upcoming_earnings": [], "recent_results": []}
    else:
        prompt = build_earnings_prompt(upcoming_raw, recent_raw)
        print("[INFO] Grok 호출 중...")
        raw_text = call_hermes(prompt)
        if raw_text is None:
            print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
            sys.exit(1)
        parsed = extract_json(raw_text)
        if parsed is None or not validate_earnings(parsed):
            print("[ERROR] 어닝 검증 실패 — 종료", file=sys.stderr)
            sys.exit(1)

    snapshot = {
        "generated_at": now_iso,
        "schema_version": "1.0",
        "upcoming_earnings": parsed["upcoming_earnings"],
        "recent_results": parsed["recent_results"],
    }

    latest_path = REPO_PATH / "earnings" / "latest.json"
    history_dir = REPO_PATH / "earnings" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"{date_str}.json"

    for path in (latest_path, history_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 저장 완료: {latest_path}")

    push_ok = git_commit_push(REPO_PATH, date_str, time_str, history_path)
    print(f"{'[OK]' if push_ok else '[WARN]'} 어닝 수집 완료")


if __name__ == "__main__":
    main()
