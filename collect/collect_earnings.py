#!/usr/bin/env python3
"""
Earnings Intelligence 수집기
① yfinance .calendar + .earnings_history로 어닝 데이터 수집
② Grok(Hermes)으로 어닝 리스크 해석 생성
③ earnings/latest.json + earnings/history/<date>.json 저장 → git push
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

from collect.git_utils import commit_and_push

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
HERMES_CMD = os.environ.get("HERMES_CMD", "/Users/jerry/.local/bin/hermes")
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))
HERMES_RETRY = int(os.environ.get("HERMES_RETRY", "1"))

WATCHLIST = ["TSLA", "AAPL", "NVDA", "META", "AMZN", "GOOGL", "PLTR"]
UPCOMING_WINDOW_DAYS = 30  # 30일 이후는 EPS 컨센서스 미형성 → 노이즈
RECENT_QUARTERS = 8

# 어닝 플레이의 실질적 가시권 임계값
TIER_IMMINENT_DAYS = 7    # 이벤트 위험 관리 구간
TIER_APPROACHING_DAYS = 21  # 포지션 계획 시작 구간
# 22-30일: watching 구간 (EPS estimate 없으면 제외)


def fetch_earnings_data(symbols: list[str], today: datetime) -> tuple[list[dict], list[dict], list[str]]:
    """워치리스트 전체 어닝 데이터 수집 (강화된 폴백/검증/로깅).
    Returns: (upcoming_raw, recent_raw, failed_syms)
    """
    upcoming_raw = []
    recent_raw = []
    failed_syms = []

    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)

            # Structured per-symbol logging: raw calendar shape when possible
            cal = None
            cal_shape = None
            cal_keys = None
            try:
                cal = ticker.calendar
                if cal is not None:
                    if hasattr(cal, "shape"):
                        cal_shape = cal.shape
                    elif isinstance(cal, (dict, list)):
                        cal_shape = f"len={len(cal)}"
                    if hasattr(cal, "columns"):
                        cal_keys = list(cal.columns)
                    elif isinstance(cal, dict):
                        cal_keys = list(cal.keys())[:8]
            except Exception as e:
                print(f"[DEBUG] {sym}: calendar access error (will fallback): {type(e).__name__}")
            print(f"[DEBUG] {sym}: raw_calendar shape={cal_shape} keys/cols={cal_keys}")

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
                    # Strengthened key fallbacks (yf version variance: EPS Estimate / Earnings Average / avg etc.)
                    eps_keys = ["EPS Estimate", "Earnings Average", "Earnings Mean", "avg", "mean"]
                    rev_keys = ["Revenue Estimate", "Revenue Average", "Revenue Mean"]
                    if hasattr(cal, 'columns'):
                        for k in eps_keys:
                            if k in cal.columns:
                                eps_estimate = float(cal[k].iloc[0])
                                break
                        for k in rev_keys:
                            if k in cal.columns:
                                rev_estimate_b = round(float(cal[k].iloc[0]) / 1e9, 2)
                                break
                    elif isinstance(cal, dict):
                        for k in eps_keys:
                            if k in cal:
                                val = cal[k]
                                eps_estimate = float(val[0] if isinstance(val, list) else val)
                                break
                        for k in rev_keys:
                            if k in cal:
                                val = cal[k]
                                rev_raw = float(val[0] if isinstance(val, list) else val)
                                rev_estimate_b = round(rev_raw / 1e9, 2)
                                break
                except Exception:
                    pass

            # Strengthened fallback chain: calendar → earnings_dates / earnings_estimate / other attrs
            # (defensive for yf version differences and scrape failures)
            if earnings_date is None or eps_estimate is None:
                for attr in ("earnings_estimate", "earnings_dates"):
                    try:
                        data = getattr(ticker, attr, None)
                        if data is None:
                            continue
                        if callable(data):
                            data = data()
                        if data is None or (hasattr(data, "empty") and data.empty):
                            continue
                        print(f"[DEBUG] {sym}: fallback source active: {attr} type={type(data).__name__}")
                        # Date extraction (earnings_dates often has date in index or col)
                        if earnings_date is None and hasattr(data, "index") and len(data) > 0:
                            try:
                                idx0 = data.index[0]
                                if hasattr(idx0, "date"):
                                    earnings_date = idx0.date()
                                else:
                                    earnings_date = datetime.strptime(str(idx0)[:10], "%Y-%m-%d").date()
                            except Exception:
                                pass
                        if hasattr(data, "columns"):
                            # eps from avg etc in estimate table (0q = next)
                            if eps_estimate is None:
                                for k in ("avg", "EPS Estimate", "Earnings Average"):
                                    if k in data.columns:
                                        try:
                                            eps_estimate = float(data[k].iloc[0])
                                            break
                                        except Exception:
                                            pass
                            # rev rarely here
                        # dict-like or iloc fallback for estimates
                        if eps_estimate is None and hasattr(data, "iloc") and len(data) > 0:
                            try:
                                row0 = data.iloc[0]
                                for k in ("avg", "EPS Estimate", "Earnings Average", "mean"):
                                    if k in getattr(row0, "keys", lambda: [])() or (hasattr(row0, "get") and row0.get(k) is not None):
                                        eps_estimate = float(row0.get(k) if hasattr(row0, "get") else row0[k])
                                        break
                            except Exception:
                                pass
                        if earnings_date is not None or eps_estimate is not None:
                            break  # good enough from this fallback
                    except Exception as e:
                        print(f"[DEBUG] {sym}: fallback {attr} skipped ({type(e).__name__})")
                        continue

            hist = ticker.earnings_history
            try:
                h_shape = getattr(hist, "shape", None) if hist is not None else None
                h_cols = list(getattr(hist, "columns", [])) if hist is not None and hasattr(hist, "columns") else None
                print(f"[DEBUG] {sym}: raw_earnings_history shape={h_shape} cols={h_cols}")
            except Exception:
                pass
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
                    # watching 구간(22-30일)에서 EPS 추정치 미형성 시 노이즈 — 제외
                    if days_until > TIER_APPROACHING_DAYS and eps_estimate is None:
                        print(f"[SKIP] {sym}: {days_until}일 후, EPS estimate 없음 — 가시권 밖")
                        continue

                    if days_until <= TIER_IMMINENT_DAYS:
                        tier = "imminent"
                    elif days_until <= TIER_APPROACHING_DAYS:
                        tier = "approaching"
                    else:
                        tier = "watching"

                    upcoming_raw.append({
                        "symbol": sym,
                        "earnings_date": str(earnings_date),
                        "days_until": days_until,
                        "relevance_tier": tier,
                        "eps_estimate": round(eps_estimate, 2) if eps_estimate is not None else None,
                        "revenue_estimate_b": rev_estimate_b,
                        "historical_beat_rate": beat_rate,
                    })

            if last_result is not None:
                recent_raw.append(last_result)

            # Clear per-symbol success path with key facts
            print(f"[OK]   {sym}: earnings_date={earnings_date}, eps_est={eps_estimate}, beat_rate={beat_rate}, days_until={(earnings_date - today.date()).days if earnings_date else None}")

        except Exception as e:
            print(f"[FAIL] {sym}: 수집 실패 — {e}", file=sys.stderr)
            failed_syms.append(sym)

    upcoming_raw.sort(key=lambda x: x["days_until"])
    return upcoming_raw, recent_raw, failed_syms


def build_earnings_prompt(upcoming_raw: list[dict], recent_raw: list[dict]) -> str:
    upcoming_block = "\n".join([
        f"- {u['symbol']}: {u['days_until']}일 후 ({u['earnings_date']}) "
        f"[tier={u['relevance_tier']}], "
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

UPCOMING EARNINGS (30일 이내, EPS estimate 미형성 종목은 사전 필터링됨):
{upcoming_block}

relevance_tier 의미:
- imminent (0-7일): 이벤트 위험 관리 구간. 포지션 직접 노출 최소화. 옵션 IV 급등.
- approaching (8-21일): 컨센서스 형성 중. 포지션 계획 및 진입 구간.
- watching (22-30일): EPS estimate 존재 시에만 포함. 모니터링만.

RECENT RESULTS (지난 분기):
{recent_block}

Generate ONE JSON object with this EXACT schema (no prose, no code fences):
{{
  "upcoming_earnings": [
    {{
      "symbol": "TICKER",
      "earnings_date": "YYYY-MM-DD",
      "days_until": 0,
      "relevance_tier": "imminent|approaching|watching",
      "eps_estimate": null,
      "revenue_estimate_b": null,
      "historical_beat_rate": null,
      "ai_summary": "2-3문장 어닝 맥락 설명. tier에 맞는 시의성 강조 (한국어)",
      "risk_level": "one of high/med/low",
      "action_note": "트레이더를 위한 한 줄 조언. tier별 구체적 행동 지침 (한국어)"
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

risk_level 기준 (날짜 우선, beat_rate 보조):
- high: days_until <= 7 (이벤트 임박, 포지션 직접 위험)
- med: days_until 8-21 (포지션 계획 구간, EPS estimate 존재)
- low: days_until 22-30 (모니터링 구간, EPS estimate 형성됨)

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
VALID_TIERS = {"imminent", "approaching", "watching"}

# Lightweight schema for earnings snapshot (used with jsonschema when available).
# Does not alter main schema.json (earnings is separate contract).
EARNINGS_SNAPSHOT_SCHEMA = {
    "type": "object",
    "required": ["generated_at", "schema_version", "upcoming_earnings", "recent_results"],
    "properties": {
        "generated_at": {"type": "string"},
        "schema_version": {"type": "string"},
        "upcoming_earnings": {"type": "array"},
        "recent_results": {"type": "array"},
        "partial": {"type": "boolean"}
    },
    "additionalProperties": True  # allow future additive fields
}


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
        # relevance_tier는 선택적 — 없어도 통과 (구버전 호환)
        tier = item.get("relevance_tier")
        if tier is not None and tier not in VALID_TIERS:
            print(f"[WARN] {item.get('symbol')}: relevance_tier={tier!r}", file=sys.stderr)
            return False
        # Numeric validation where expected (defensive for partial/ malformed AI)
        for num_key in ("eps_estimate", "revenue_estimate_b", "historical_beat_rate", "days_until"):
            v = item.get(num_key)
            if v is not None:
                try:
                    float(v)
                except (TypeError, ValueError):
                    print(f"[WARN] {item.get('symbol')}: {num_key} not numeric: {v!r}", file=sys.stderr)
                    return False
    for item in recent:
        for num_key in ("eps_actual", "eps_estimate", "surprise_pct"):
            v = item.get(num_key)
            if v is not None:
                try:
                    float(v)
                except (TypeError, ValueError):
                    print(f"[WARN] {item.get('symbol')}: {num_key} not numeric: {v!r}", file=sys.stderr)
                    return False
    return True


def validate_snapshot(snapshot: dict) -> bool:
    """Schema-ish validation before write: lightweight + jsonschema (if installed)."""
    # Lightweight structural check (always)
    required = ["generated_at", "schema_version", "upcoming_earnings", "recent_results"]
    for key in required:
        if key not in snapshot:
            print(f"[WARN] snapshot missing required key: {key}", file=sys.stderr)
            return False
    if not isinstance(snapshot.get("upcoming_earnings"), list) or not isinstance(snapshot.get("recent_results"), list):
        print("[WARN] upcoming_earnings / recent_results not lists", file=sys.stderr)
        return False

    # jsonschema when available (no hard dep)
    try:
        from jsonschema import validate as js_validate, ValidationError
        js_validate(instance=snapshot, schema=EARNINGS_SNAPSHOT_SCHEMA)
        print("[INFO] jsonschema validation passed for earnings snapshot")
        return True
    except ImportError:
        print("[INFO] jsonschema unavailable — lightweight validation only (ok)")
        return True
    except ValidationError as e:
        print(f"[WARN] jsonschema validation error: {e.message}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[WARN] schema validation unexpected issue: {e}", file=sys.stderr)
        return False


def git_commit_push(repo: Path, date_str: str, time_str: str, history_path: Path) -> bool:
    """어닝 데이터 push (모든 수집기 공통 안정 로직)"""
    rel_history = str(history_path.relative_to(repo))
    commit_message = f"earnings: {date_str} {time_str} update"

    return commit_and_push(
        repo=repo,
        commit_message=commit_message,
        files_to_add=["earnings/latest.json", rel_history],
        push=True,
    )


def main(dry_run: bool = False):
    """Main collection. Supports --dry-run to skip side effects (hermes, writes, git)."""
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    print(f"[INFO] 어닝 수집 시작: {now_iso} (dry_run={dry_run})")

    upcoming_raw, recent_raw, failed_syms = fetch_earnings_data(WATCHLIST, now)
    print(f"[INFO] upcoming={len(upcoming_raw)}, recent={len(recent_raw)}, failed={len(failed_syms)}")
    if failed_syms:
        print(f"[WARN] 부분 실패 심볼: {failed_syms}")

    partial = bool(failed_syms)

    if not upcoming_raw and not recent_raw:
        print("[INFO] 어닝 데이터 없음 — 빈 스냅샷 저장")
        parsed = {"upcoming_earnings": [], "recent_results": []}
    else:
        if dry_run:
            print("[DRY-RUN] Grok/hermes 호출 생략 — raw 데이터로 스텁 partial 시뮬레이션")
            partial = True
            parsed = {
                "upcoming_earnings": [
                    {
                        "symbol": u["symbol"],
                        "earnings_date": u["earnings_date"],
                        "days_until": u["days_until"],
                        "relevance_tier": u["relevance_tier"],
                        "eps_estimate": u.get("eps_estimate"),
                        "revenue_estimate_b": u.get("revenue_estimate_b"),
                        "historical_beat_rate": u.get("historical_beat_rate"),
                        "ai_summary": "[DRY-RUN] hermes 생략됨 — 원시 yfinance 데이터만.",
                        "risk_level": "high" if u.get("days_until", 99) <= 7 else ("med" if u.get("days_until", 99) <= 21 else "low"),
                        "action_note": "dry-run 모드"
                    }
                    for u in upcoming_raw
                ],
                "recent_results": [
                    {
                        "symbol": r["symbol"],
                        "report_date": r["report_date"],
                        "eps_actual": r["eps_actual"],
                        "eps_estimate": r["eps_estimate"],
                        "surprise_pct": r["surprise_pct"],
                        "ai_reaction": "[DRY-RUN] hermes 생략 (partial 시뮬)"
                    }
                    for r in recent_raw
                ]
            }
        else:
            prompt = build_earnings_prompt(upcoming_raw, recent_raw)
            print("[INFO] Grok 호출 중...")
            raw_text = call_hermes(prompt)
            if raw_text is None:
                print("[WARN] Grok 호출 실패 — partial 스냅샷 생성 (AI 스텁으로 원시 데이터 보존)", file=sys.stderr)
                partial = True
                # Produce usable output: factual yf data + stub AI fields (no crash)
                parsed = {
                    "upcoming_earnings": [
                        {
                            "symbol": u["symbol"],
                            "earnings_date": u["earnings_date"],
                            "days_until": u["days_until"],
                            "relevance_tier": u["relevance_tier"],
                            "eps_estimate": u.get("eps_estimate"),
                            "revenue_estimate_b": u.get("revenue_estimate_b"),
                            "historical_beat_rate": u.get("historical_beat_rate"),
                            "ai_summary": "Grok/Hermes 호출 실패 (partial). 원시 yfinance 데이터만 사용.",
                            "risk_level": "high" if u.get("days_until", 99) <= 7 else ("med" if u.get("days_until", 99) <= 21 else "low"),
                            "action_note": "부분 수집 실패 — 별도 확인 및 수동 모니터링 권장"
                        }
                        for u in upcoming_raw
                    ],
                    "recent_results": [
                        {
                            "symbol": r["symbol"],
                            "report_date": r["report_date"],
                            "eps_actual": r["eps_actual"],
                            "eps_estimate": r["eps_estimate"],
                            "surprise_pct": r["surprise_pct"],
                            "ai_reaction": "Grok/Hermes 호출 실패 — AI 반응 분석 생략 (partial)"
                        }
                        for r in recent_raw
                    ]
                }
            else:
                parsed = extract_json(raw_text)
                if parsed is None or not validate_earnings(parsed):
                    print("[WARN] 어닝 파싱/검증 실패 — partial 스냅샷으로 계속 (원시 데이터 우선)", file=sys.stderr)
                    partial = True
                    # Usable fallback from raw (same structure as hermes success path)
                    parsed = {
                        "upcoming_earnings": [
                            {
                                "symbol": u["symbol"],
                                "earnings_date": u["earnings_date"],
                                "days_until": u["days_until"],
                                "relevance_tier": u["relevance_tier"],
                                "eps_estimate": u.get("eps_estimate"),
                                "revenue_estimate_b": u.get("revenue_estimate_b"),
                                "historical_beat_rate": u.get("historical_beat_rate"),
                                "ai_summary": "Grok 응답 파싱 실패 (partial). 원시 데이터 기반.",
                                "risk_level": "high" if u.get("days_until", 99) <= 7 else ("med" if u.get("days_until", 99) <= 21 else "low"),
                                "action_note": "부분 수집 — AI 요약 없음, 수동 확인"
                            }
                            for u in upcoming_raw
                        ],
                        "recent_results": [
                            {
                                "symbol": r["symbol"],
                                "report_date": r["report_date"],
                                "eps_actual": r["eps_actual"],
                                "eps_estimate": r["eps_estimate"],
                                "surprise_pct": r["surprise_pct"],
                                "ai_reaction": "Grok 파싱 실패 — AI 반응 생략 (partial)"
                            }
                            for r in recent_raw
                        ]
                    }

    snapshot = {
        "generated_at": now_iso,
        "schema_version": "2.0",
        "upcoming_earnings": parsed["upcoming_earnings"],
        "recent_results": parsed["recent_results"],
        "partial": partial,
    }

    # Schema validation (jsonschema + lightweight) BEFORE any write — required by hardening
    schema_ok = validate_snapshot(snapshot)
    if not schema_ok:
        print("[WARN] 스키마 검증 실패 — partial 플래그 강제 설정", file=sys.stderr)
        snapshot["partial"] = True
        partial = True

    if dry_run:
        print(f"[DRY-RUN] 스냅샷 생성 완료 (partial={partial}, schema_ok={schema_ok}) — 파일 기록·git·추가 hermes 호출 모두 생략")
        # Structured summary for dry-run verification (no secrets)
        print(f"[DRY-RUN] upcoming count={len(snapshot['upcoming_earnings'])}, recent count={len(snapshot['recent_results'])}")
        if snapshot["upcoming_earnings"]:
            ex = snapshot["upcoming_earnings"][0]
            print(f"[DRY-RUN] sample upcoming: {ex.get('symbol')} on {ex.get('earnings_date')} tier={ex.get('relevance_tier')} eps={ex.get('eps_estimate')}")
        return

    latest_path = REPO_PATH / "earnings" / "latest.json"
    history_dir = REPO_PATH / "earnings" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"{date_str}.json"

    for path in (latest_path, history_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 저장 완료: {latest_path}")

    push_ok = git_commit_push(REPO_PATH, date_str, time_str, history_path)
    if not push_ok:
        print("[FATAL] git push 실패 — 최신 earnings 데이터가 GitHub에 반영되지 않았습니다.")
        sys.exit(1)

    print(f"[OK] 어닝 수집 + GitHub push 완료 (partial={partial})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Earnings Intelligence collector (hardened)")
    parser.add_argument("--dry-run", action="store_true", help="Collect/validate/log only; skip hermes calls after fetch, file writes, and git push")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
