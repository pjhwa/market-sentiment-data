#!/usr/bin/env python3
"""
Macro Insight AI 해석 수집기 (Phase 2: Accuracy-hardened)

① /api/macro   — 25개 심볼 실시간 가격/지표 데이터
② /api/macro/insight — yfinance 계산 신호(green/yellow/red) — 텍스트 방향의 Ground Truth
③ Grok으로 6개 그룹별 해석 텍스트 + 종합 요약 생성
   → 계산된 신호와 Grok 텍스트의 방향이 반드시 일치해야 함
④ macro/latest.json + macro/history/<date>_<slot>.json 저장
⑤ git commit + push

핵심 원칙:
- /api/macro/insight 신호(green/yellow/red)가 방향의 Ground Truth
- Grok은 그 방향을 '왜'인지 설명만 함 — 방향을 반전시키면 안 됨
- 바인딩 테이블의 수치를 직접 인용할 때는 정확히 사용해야 함
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

REPO_PATH     = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
HERMES_CMD    = os.environ.get("HERMES_CMD", "/Users/jerry/.local/bin/hermes")
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT  = int(os.environ.get("HERMES_TIMEOUT", "120"))
HERMES_RETRY  = int(os.environ.get("HERMES_RETRY", "1"))
SNIPERBOARD_API = os.environ.get("SNIPERBOARD_API_BASE", "http://localhost:5001")

# 6개 그룹별 심볼 매핑 (sniperboard macro_rules.py와 동기화)
GROUP_SYMBOLS: dict[str, list[str]] = {
    "volatility":  ["^VIX", "^VVIX", "^VIX9D"],
    "breadth":     ["SPY", "QQQ", "RSP", "IWM", "MAGS"],
    "credit":      ["HYG", "JNK", "LQD", "IEF"],
    "rates":       ["TLT", "^TNX"],
    "commodities": ["CL=F", "GLD", "BTC-USD"],
    "sectors":     ["SMH", "XLE", "XLY", "XHB", "ITA"],
}

GROUP_LABELS: dict[str, str] = {
    "volatility":  "변동성 (VIX/VVIX/VIX9D)",
    "breadth":     "시장 폭 (SPY/QQQ/RSP/IWM/MAGS)",
    "credit":      "크레딧 (HYG/JNK/LQD/IEF)",
    "rates":       "금리 (TLT/^TNX)",
    "commodities": "원자재 (Oil/Gold/BTC)",
    "sectors":     "섹터 (SMH/XLE/XLY/XHB/ITA)",
}

# 신호별 방향 정의 (Grok 텍스트가 이 방향을 반영해야 함)
SIGNAL_DIRECTION_EN: dict[str, str] = {
    "green":  "constructive/positive/supportive",
    "yellow": "mixed/neutral/watchful",
    "red":    "cautious/negative/risk-off",
}
SIGNAL_DIRECTION_KO: dict[str, str] = {
    "green":  "긍정적/건설적/우호적",
    "yellow": "혼조/중립/주의",
    "red":    "부정적/위험회피/경계적",
}

# 바인딩 대상 핵심 심볼 (수치 인용 시 이 값만 허용)
BINDING_SYMS = {"^VIX", "^TNX", "DX-Y.NYB", "BTC-USD", "SPY", "QQQ", "GLD", "CL=F"}


def detect_slot(now: datetime) -> str:
    override = os.environ.get("SENTIMENT_SLOT", "").strip()
    if override in ("pre_open", "post_close"):
        return override
    return "pre_open" if 9 <= now.hour < 18 else "post_close"


def _api_get(path: str) -> dict | list | None:
    try:
        resp = requests.get(f"{SNIPERBOARD_API}/api{path}", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] {path} 호출 실패: {e}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 수집
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all() -> tuple[list, dict]:
    """매크로 원시 데이터 + 계산된 신호 그룹을 함께 수집."""
    raw = _api_get("/macro")
    macro_items: list = raw.get("macro", []) if isinstance(raw, dict) else []

    insight_raw = _api_get("/macro/insight")
    insight: dict = insight_raw if isinstance(insight_raw, dict) else {}

    if not macro_items:
        print("[ERROR] /api/macro 데이터 없음", file=sys.stderr)
    if not insight:
        print("[WARN] /api/macro/insight 데이터 없음 — 신호 방향 제약 없이 진행", file=sys.stderr)

    return macro_items, insight


# ─────────────────────────────────────────────────────────────────────────────
# 프롬프트 포매터
# ─────────────────────────────────────────────────────────────────────────────

def _format_binding_table(items_by_sym: dict) -> str:
    """핵심 수치 바인딩 테이블 — 이 값을 인용할 때는 반드시 그대로 사용."""
    def val(sym, field):
        v = items_by_sym.get(sym, {}).get(field)
        return f"{v:.4g}" if isinstance(v, (int, float)) else "N/A"

    def chg(sym, field):
        v = items_by_sym.get(sym, {}).get(field)
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "N/A"

    lines = [
        "━━━ NUMERIC BINDING TABLE ━━━",
        "If you cite any of these values in text, use EXACTLY these numbers (no rounding, no recall):",
        f"  VIX  = {val('^VIX','price')}  (1D={chg('^VIX','change_pct_1d')}  RSI={val('^VIX','rsi14')})",
        f"  TNX  = {val('^TNX','price')}%  (1D={chg('^TNX','change_pct_1d')})",
        f"  DXY  = {val('DX-Y.NYB','price')}  (1D={chg('DX-Y.NYB','change_pct_1d')})",
        f"  SPY  = ${val('SPY','price')}  (1D={chg('SPY','change_pct_1d')}  RSI={val('SPY','rsi14')})",
        f"  QQQ  = ${val('QQQ','price')}  (1D={chg('QQQ','change_pct_1d')}  RSI={val('QQQ','rsi14')})",
        f"  GLD  = ${val('GLD','price')}  (1D={chg('GLD','change_pct_1d')}  RSI={val('GLD','rsi14')})",
        f"  OIL  = ${val('CL=F','price')}  (1D={chg('CL=F','change_pct_1d')}  RSI={val('CL=F','rsi14')})",
        f"  BTC  = ${val('BTC-USD','price')}  (1D={chg('BTC-USD','change_pct_1d')}  5D={chg('BTC-USD','change_pct_5d')}  RSI={val('BTC-USD','rsi14')})",
        "⚠ Do NOT write VIX=18 if table shows 16.06. Do NOT write BTC up if 1D is negative.",
    ]
    return "\n".join(lines)


def _format_grouped_data(items_by_sym: dict, insight_groups: dict) -> str:
    """6개 그룹별로 데이터를 구조화하여 주입 + 계산된 신호를 Ground Truth로 명시."""
    signal_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    lines = []

    for grp_key, syms in GROUP_SYMBOLS.items():
        computed = insight_groups.get(grp_key, {})
        signal   = computed.get("signal", "unknown")
        direction = computed.get("direction", "unknown")
        emoji    = signal_emoji.get(signal, "❓")

        lines.append(
            f"\n{'─'*60}\n"
            f"GROUP: {GROUP_LABELS[grp_key]}\n"
            f"COMPUTED SIGNAL: {emoji} {signal.upper()} | direction={direction}\n"
            f"⚠ text_en/ko must reflect: {SIGNAL_DIRECTION_EN.get(signal,'?')} tone\n"
            f"   Do NOT write {SIGNAL_DIRECTION_EN.get('red' if signal=='green' else 'green','?')} tone for this group.\n"
            f"Symbol data:"
        )
        for sym in syms:
            m = items_by_sym.get(sym, {})
            if not m:
                lines.append(f"  {sym}: 데이터없음")
                continue
            price   = m.get("price", "N/A")
            chg_1d  = m.get("change_pct_1d", "N/A")
            chg_5d  = m.get("change_pct_5d", "N/A")
            rsi     = m.get("rsi14", "N/A")
            above21 = m.get("above_ema21", False)
            struct  = m.get("market_structure", "N/A")
            ema21_flag = "↑EMA21" if above21 else "↓EMA21"
            lines.append(
                f"  {sym}: ${price}  1D={chg_1d}%  5D={chg_5d}%  RSI={rsi}  "
                f"{struct}  {ema21_flag}"
            )

    return "\n".join(lines)


def _format_signal_constraints(insight: dict) -> str:
    """전체 판단 및 각 그룹 신호를 제약 조건으로 정리."""
    overall = insight.get("overall", {})
    groups  = insight.get("groups", {})
    judgment = overall.get("judgment", "UNKNOWN")

    lines = [
        "━━━ COMPUTED SIGNAL CONSTRAINTS (Ground Truth — DO NOT CONTRADICT) ━━━",
        f"Overall Judgment: {judgment}",
        "  → overall.summary must reflect this judgment direction.",
        "  → bullets must support this judgment (don't mix bullish+bearish without noting the tension).",
        "",
        "Per-group signal (your text_en/ko direction must match):",
    ]
    signal_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    for grp_key in GROUP_SYMBOLS:
        g = groups.get(grp_key, {})
        signal   = g.get("signal", "unknown")
        direction = g.get("direction", "unknown")
        emoji    = signal_emoji.get(signal, "❓")
        dir_en   = SIGNAL_DIRECTION_EN.get(signal, "?")
        lines.append(
            f"  {emoji} {grp_key:<12}: {signal.upper()} ({direction}) "
            f"→ text must be {dir_en}"
        )

    lines += [
        "",
        "ANTI-CONTRADICTION RULES:",
        "  green group → text must NOT say 'concern', 'risk', 'weakness', 'caution' as dominant tone",
        "  red group   → text must NOT say 'strength', 'bullish', 'supportive' as dominant tone",
        "  yellow group → text may note mixed signals; state the dominant factor first",
        "  If data within a group is mixed, still lead with the signal direction and note the exception",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 프롬프트 빌더
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt(macro_items: list, insight: dict, slot: str) -> str:
    items_by_sym = {m["symbol"]: m for m in macro_items}
    insight_groups = insight.get("groups", {})
    overall = insight.get("overall", {})
    judgment = overall.get("judgment", "UNKNOWN")

    binding_table      = _format_binding_table(items_by_sym)
    grouped_data       = _format_grouped_data(items_by_sym, insight_groups)
    signal_constraints = _format_signal_constraints(insight)
    slot_kor = "장 개장 전" if slot == "pre_open" else "장 마감 후"

    # 그룹별 방향 사전 계산 (f-string 내 {} 중첩 방지)
    def _grp_dir(key: str) -> str:
        sig = insight_groups.get(key, {}).get("signal", "?")
        return SIGNAL_DIRECTION_EN.get(sig, "?")

    vol_dir  = _grp_dir("volatility")
    brd_dir  = _grp_dir("breadth")
    crd_dir  = _grp_dir("credit")
    rat_dir  = _grp_dir("rates")
    com_dir  = _grp_dir("commodities")
    sec_dir  = _grp_dir("sectors")

    return f"""You are a professional macro market analyst generating a bilingual AI insight report.
Slot: {slot_kor}

{binding_table}

{signal_constraints}

━━━ MACRO DATA BY GROUP ━━━
{grouped_data}

━━━ WRITING RULES ━━━
1. DIRECTION BINDING (highest priority):
   Each group's text_en/ko direction MUST match the COMPUTED SIGNAL above.
   If signal is 🟢 green → write constructive/positive tone.
   If signal is 🔴 red   → write cautious/negative tone.
   If signal is 🟡 yellow → write mixed/neutral tone; name the dominant factor.

2. NUMBER ACCURACY:
   If you cite a specific number (VIX=X, BTC=$Y), it MUST match the BINDING TABLE exactly.
   Do NOT round VIX 16.06 to "~15" or "18". Do NOT recall training-data prices.
   Prefer direction language: "VIX below 17" is safer than "VIX at 16.06" unless you are sure.
   For BTC: if 1D is negative, do NOT write "Bitcoin rising".

3. TEXT LENGTH:
   overall.summary_en: ≤60 chars — one crisp sentence on market condition
   overall.summary_ko: ≤40 chars
   overall.bullets_en: exactly 3 items, "key signal → market implication" (≤45 chars each)
   overall.bullets_ko: exactly 3 items (≤30 chars each)
   groups.text_en: ≤55 chars — what this group signals NOW
   groups.text_ko: ≤40 chars

4. BULLET CONTENT RULES:
   bullet[0]: Overall regime / VIX signal (judgment + volatility)
   bullet[1]: Strongest positive signal (cite group + specific asset)
   bullet[2]: Biggest risk or yellow/red signal (cite group + specific asset)
   Each bullet must cite an ACTUAL data point.
   Good: "VIX 16 downtrend → fear low, risk friendly"
   Bad:  "Markets cautious amid uncertainty" (generic — rejected)

5. CONSISTENCY:
   overall.summary must align with judgment: {judgment}
   Bullets must not all be bullish if MIXED, or all bearish if RISK_ON.

SELF-CHECK before JSON output:
  □ overall judgment is {judgment} — does summary reflect this direction?
  □ Each group text direction matches its computed signal?
  □ Any number cited matches the BINDING TABLE?
  □ Exactly 3 bullets in bullets_en and bullets_ko?
  □ No contradiction (green group text has "concern" as main tone)?
  □ BTC: if 1D negative in table, text does not say "Bitcoin rising"?

Generate ONE JSON object (raw JSON only, no markdown):
{{
  "overall": {{
    "summary_en": "≤60 char sentence consistent with {judgment}",
    "summary_ko": "≤40자, {judgment} 방향 반영",
    "bullets_en": [
      "regime/VIX signal → implication",
      "strongest green signal → implication",
      "biggest risk/yellow/red signal → implication"
    ],
    "bullets_ko": [
      "레짐/VIX 신호 → 의미",
      "가장 강한 긍정 신호 → 의미",
      "주요 리스크 신호 → 의미"
    ]
  }},
  "groups": {{
    "volatility":  {{ "text_en": "≤55 char, {vol_dir} tone required", "text_ko": "≤40자" }},
    "breadth":     {{ "text_en": "≤55 char, {brd_dir} tone required", "text_ko": "≤40자" }},
    "credit":      {{ "text_en": "≤55 char, {crd_dir} tone required", "text_ko": "≤40자" }},
    "rates":       {{ "text_en": "≤55 char, {rat_dir} tone required", "text_ko": "≤40자" }},
    "commodities": {{ "text_en": "≤55 char, {com_dir} tone required", "text_ko": "≤40자" }},
    "sectors":     {{ "text_en": "≤55 char, {sec_dir} tone required", "text_ko": "≤40자" }}
  }}
}}

Raw JSON only."""


# ─────────────────────────────────────────────────────────────────────────────
# Grok 호출 / JSON 추출 / 검증
# ─────────────────────────────────────────────────────────────────────────────

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


VALID_GROUP_KEYS = set(GROUP_SYMBOLS.keys())

# 신호 방향과 반대되는 단어 (방향 위반 감지용)
_POSITIVE_WORDS = {"bullish", "strong", "constructive", "supportive", "positive",
                   "uptrend", "recovery", "strength"}
_NEGATIVE_WORDS = {"bearish", "weak", "cautious", "risk-off", "negative",
                   "concern", "deteriorating", "warning", "pressure"}


def validate(data: dict, insight_groups: dict) -> bool:
    """스키마 검증 + 신호 방향 일관성 검증."""
    overall = data.get("overall", {})
    if not isinstance(overall, dict):
        print("[WARN] overall 누락", file=sys.stderr)
        return False
    for field in ("summary_en", "summary_ko"):
        if not overall.get(field):
            print(f"[WARN] overall.{field} 누락", file=sys.stderr)
            return False
    for field in ("bullets_en", "bullets_ko"):
        bl = overall.get(field)
        if not isinstance(bl, list) or len(bl) != 3:
            print(f"[WARN] overall.{field}: 3개 항목 필요 (현재={len(bl) if isinstance(bl,list) else 'None'})",
                  file=sys.stderr)
            return False

    groups = data.get("groups", {})
    if set(groups.keys()) != VALID_GROUP_KEYS:
        print(f"[WARN] groups 키 불일치: {set(groups.keys())}", file=sys.stderr)
        return False

    ok = True
    for key, grp in groups.items():
        if not grp.get("text_en") or not grp.get("text_ko"):
            print(f"[WARN] groups.{key}: text_en 또는 text_ko 누락", file=sys.stderr)
            ok = False
            continue
        # 신호 방향 일관성 검사
        computed_signal = insight_groups.get(key, {}).get("signal", "")
        text_lower = grp["text_en"].lower()
        if computed_signal == "green":
            contradictions = [w for w in _NEGATIVE_WORDS if w in text_lower]
            if contradictions:
                print(f"[WARN] {key}: signal=green 인데 부정어 발견: {contradictions}", file=sys.stderr)
        elif computed_signal == "red":
            contradictions = [w for w in _POSITIVE_WORDS if w in text_lower]
            if contradictions:
                print(f"[WARN] {key}: signal=red 인데 긍정어 발견: {contradictions}", file=sys.stderr)

    return ok


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────

def main():
    now      = datetime.now(timezone.utc)
    now_iso  = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    slot     = detect_slot(now)
    print(f"[INFO] 슬롯: {slot}, 시각: {now_iso}")

    print("[INFO] 매크로 데이터 수집 중...")
    macro_items, insight = fetch_all()
    if not macro_items:
        print("[ERROR] 매크로 데이터 없음 — 종료", file=sys.stderr)
        sys.exit(1)

    insight_groups = insight.get("groups", {})
    judgment = insight.get("overall", {}).get("judgment", "UNKNOWN")
    print(f"[INFO] 계산된 신호: {judgment} | 그룹: "
          + " ".join(f"{k}={v.get('signal','?')}" for k, v in insight_groups.items()))

    prompt = build_prompt(macro_items, insight, slot)
    print("[INFO] Grok 호출 중...")
    raw_text = call_hermes(prompt)
    if raw_text is None:
        print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    parsed = extract_json(raw_text)
    if parsed is None or not validate(parsed, insight_groups):
        print("[ERROR] 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    snapshot = {
        "generated_at":   now_iso,
        "schema_version": "2.0",
        "slot":           slot,
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
        "computed_signals": {
            key: {
                "signal":    grp.get("signal"),
                "direction": grp.get("direction"),
            }
            for key, grp in insight_groups.items()
        },
        "overall_judgment": judgment,
    }

    macro_dir   = REPO_PATH / "macro"
    macro_dir.mkdir(parents=True, exist_ok=True)
    history_dir = macro_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    latest_path  = macro_dir / "latest.json"
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
        print("[FATAL] GitHub push 실패", file=sys.stderr)
        sys.exit(1)

    print("[OK] Macro Insight 수집 + push 완료")


if __name__ == "__main__":
    main()
