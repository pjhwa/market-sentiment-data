#!/usr/bin/env python3
"""
AI Daily Brief 수집기 (Phase 1 Context 포함)

① Sniperboard API에서 Regime, DD, 종목별 Stage2/신호 수집
② latest.json에서 소셜 심리 읽기
③ Grok(Hermes)으로 brief JSON 생성 (context 포함)
④ brief/latest.json + brief/history/<date>_<slot>.json 저장
⑤ **성공 시 반드시 git commit + push** → sniperboard가 최신 context를 즉시 볼 수 있게 함

중요: push가 실패하면 전체 작업을 실패로 처리합니다. (cron 알림 목적)
cron 환경에서는 SSH deploy key를 사용하는 것을 강력 권장합니다.
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

WATCHLIST = [
    ("TSLA", "Tesla"),
    ("AAPL", "Apple"),
    ("NVDA", "Nvidia"),
    ("META", "Meta Platforms"),
    ("AMZN", "Amazon"),
    ("GOOGL", "Alphabet / Google"),
    ("PLTR", "Palantir"),
]


def detect_slot(now: datetime) -> str:
    override = os.environ.get("SENTIMENT_SLOT", "").strip()
    if override in ("pre_open", "post_close"):
        return override
    if 9 <= now.hour < 18:
        return "pre_open"
    return "post_close"


def _api_get(path: str, params: dict | None = None) -> dict | None:
    try:
        resp = requests.get(f"{SNIPERBOARD_API}/api{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[WARN] API {path} 호출 실패: {e}", file=sys.stderr)
        return None


def fetch_technical_context() -> dict:
    """Sniperboard API에서 시장 전체 + 종목별 기술적 데이터 수집."""
    regime = _api_get("/regime") or {}
    dd = _api_get("/distribution-days") or {}
    watchlist = _api_get("/watchlist") or {}

    symbol_data = {}
    for sym, _ in WATCHLIST:
        daily = _api_get("/daily", {"symbol": sym})
        if daily and daily.get("stage2"):
            s2 = daily["stage2"]
            symbol_data[sym] = {
                "stage2_score": s2.get("score", 0),
                "rs_score": round(s2.get("rs_score", 50.0), 1),
                "pct_from_52w_high": round(s2.get("pct_from_52w_high", 0.0), 1),
                "market_structure": s2.get("market_structure", "NEUTRAL"),
                "entry": round(s2.get("entry", 0.0), 2),
                "gc_above": s2.get("gc_above", False),
                "gc_breakout": s2.get("gc_breakout", False),
                "bear_flag": s2.get("bear_flag", False),
                "rsi_divergence_bullish": s2.get("rsi_divergence_bullish", False),
                "rsi_divergence_bearish": s2.get("rsi_divergence_bearish", False),
            }

    return {
        "regime": regime,
        "distribution_days": dd,
        "watchlist": watchlist.get("watchlist", []),
        "symbol_detail": symbol_data,
    }


def build_brief_context_snapshot(tech: dict, sentiment: dict, captured_at: str) -> dict:
    """Phase 1 Context Attribution용 스냅샷 생성.

    Brief 생성 시점의 기술적/레짐/심리 맥락을 구조화하여 brief JSON에 embed.
    나중에 /api/brief 응답에서 "이 Brief가 생성될 당시 시장 상황"을 보여주기 위함.

    스키마는 sniperboard/docs/yf-accuracy-harden-data-model.md 를 기준으로 함 (v1).
    """
    regime = tech.get("regime", {}) or {}
    dd = tech.get("distribution_days", {}) or {}
    sym_detail = tech.get("symbol_detail", {}) or {}

    # avg_stage2 / avg_rs_score 계산 (WATCHLIST 종목 기준)
    stage2_scores = []
    rs_scores = []
    for sym, _ in WATCHLIST:
        s = sym_detail.get(sym, {})
        if "stage2_score" in s:
            stage2_scores.append(s["stage2_score"])
        if "rs_score" in s:
            rs_scores.append(s["rs_score"])

    avg_stage2 = round(sum(stage2_scores) / len(stage2_scores), 1) if stage2_scores else None
    avg_rs = round(sum(rs_scores) / len(rs_scores), 1) if rs_scores else None

    spy_dd = dd.get("spy", {}) or {}
    regime_components = regime.get("components", {}) or {}

    # 간단 key_factors (v1)
    key_factors = []
    if regime.get("regime") in ("RISK_ON", "CONSTRUCTIVE"):
        key_factors.append(f"Regime {regime.get('regime')}")
    if avg_stage2 and avg_stage2 >= 5:
        key_factors.append("Stage2 평균 5점 이상")
    if avg_rs and avg_rs >= 60:
        key_factors.append("평균 RS 60 이상")
    if spy_dd.get("count", 0) >= 5:
        key_factors.append("SPY Distribution Day 다수")

    market_sent = sentiment.get("market", {}) or {}

    return {
        "captured_at": captured_at,
        "source": "sniperboard",
        "regime": {
            "total": regime.get("total"),
            "label": regime.get("regime"),
        },
        "technical_summary": {
            "avg_stage2": avg_stage2,
            "avg_rs_score": avg_rs,
            "spy_vs_ema200_pct": regime_components.get("trend"),  # 근사 (trend 컴포넌트가 % 기반)
            "distribution_day_spy": spy_dd.get("count"),
        },
        "market_sentiment": {
            "composite_score": market_sent.get("composite_score"),
            "label": market_sent.get("sentiment"),
        },
        "key_factors": key_factors or ["데이터 기반 요약"],
    }


def load_sentiment() -> dict:
    """latest.json에서 소셜 심리 로드."""
    latest_path = REPO_PATH / "latest.json"
    if not latest_path.exists():
        return {}
    try:
        with open(latest_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] latest.json 읽기 실패: {e}", file=sys.stderr)
        return {}


def build_brief_prompt(tech: dict, sentiment: dict, slot: str) -> str:
    regime = tech.get("regime", {})
    dd = tech.get("distribution_days", {})
    spy_dd = dd.get("spy", {})
    qqq_dd = dd.get("qqq", {})
    sym_detail = tech.get("symbol_detail", {})

    sentiment_by_sym: dict = {}
    for sym_obj in sentiment.get("symbols", []):
        sym_sentiment = sym_obj.get("symbol")
        if sym_sentiment:
            sentiment_by_sym[sym_sentiment] = sym_obj

    symbol_summaries = []
    for sym, company in WATCHLIST:
        s2 = sym_detail.get(sym, {})
        sent = sentiment_by_sym.get(sym, {})
        symbol_summaries.append(
            f"- {sym} ({company}): Stage2={s2.get('stage2_score', 'N/A')}/7, "
            f"RS={s2.get('rs_score', 'N/A')}, "
            f"52w_from_high={s2.get('pct_from_52w_high', 'N/A')}%, "
            f"structure={s2.get('market_structure', 'N/A')}, "
            f"gc_above={s2.get('gc_above', False)}, "
            f"gc_breakout={s2.get('gc_breakout', False)}, "
            f"bear_flag={s2.get('bear_flag', False)}, "
            f"social_sentiment={sent.get('sentiment', 'N/A')}, "
            f"composite_score={sent.get('composite_score', 'N/A')}, "
            f"social_reason={sent.get('key_reason', 'N/A')}"
        )

    symbols_block = "\n".join(symbol_summaries)
    slot_kor = "장 개장 전" if slot == "pre_open" else "장 마감 후"

    return f"""You are a professional stock market analyst. Based on the following technical and social data, generate a trading brief in JSON format.

MARKET DATA ({slot_kor}):
- Risk Regime: {regime.get('regime', 'N/A')} (score: {regime.get('total', 'N/A')}/100)
- Regime components: Trend={regime.get('components', {}).get('trend', 'N/A')}, Breadth={regime.get('components', {}).get('breadth', 'N/A')}, Credit={regime.get('components', {}).get('credit', 'N/A')}, Volatility={regime.get('components', {}).get('volatility', 'N/A')}, Momentum={regime.get('components', {}).get('momentum', 'N/A')}
- SPY Distribution Days: {spy_dd.get('count', 'N/A')} ({spy_dd.get('level', 'N/A')})
- QQQ Distribution Days: {qqq_dd.get('count', 'N/A')} ({qqq_dd.get('level', 'N/A')})
- Market social sentiment: {sentiment.get('market', {}).get('sentiment', 'N/A')} (score={sentiment.get('market', {}).get('composite_score', 'N/A')})

SYMBOLS:
{symbols_block}

Generate ONE JSON object with this EXACT schema (no prose, no code fences):
{{
  "market_brief": {{
    "summary_en": "One-sentence market summary in English",
    "summary_ko": "시장 전체 한 문장 요약 (한국어, 30자 이내)",
    "tone": "one of bullish/cautious/bearish/neutral",
    "key_themes_en": ["theme1", "theme2"],
    "key_themes_ko": ["테마1", "테마2"],
    "watch_points_en": "Key thing to watch today in one sentence",
    "watch_points_ko": "오늘 주의할 점 한 문장 (한국어)"
  }},
  "symbol_briefs": [
    {{
      "symbol": "TICKER",
      "setup_quality": "one of A+/A/B/C/D",
      "brief_en": "2-3 sentence analysis in English",
      "brief_ko": "2-3문장 설명 (한국어)",
      "key_risk_en": "Key risk in one line",
      "key_risk_ko": "핵심 리스크 한 줄 (한국어)",
      "key_opportunity_en": "Key opportunity in one line",
      "key_opportunity_ko": "핵심 기회 한 줄 (한국어)",
      "action_bias": "one of buy/hold/watch/avoid"
    }}
  ]
}}

setup_quality 기준:
- A+: Stage2 6-7점, 소셜 optimistic 이상, GC above/breakout, RS 70+
- A: Stage2 5-6점, 소셜 중립 이상, 구조 UPTREND
- B: Stage2 4-5점, 혼재 신호
- C: Stage2 3점 이하, 소셜 공포 또는 bear_flag
- D: Stage2 2점 이하 또는 downtrend 심화

symbol_briefs에 WATCHLIST 7종목 전부 포함 순서: TSLA, AAPL, NVDA, META, AMZN, GOOGL, PLTR
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


VALID_TONES = {"bullish", "cautious", "bearish", "neutral"}
VALID_SETUP_QUALITY = {"A+", "A", "B", "C", "D"}
VALID_ACTION_BIAS = {"buy", "hold", "watch", "avoid"}


def validate_brief(data: dict) -> bool:
    mb = data.get("market_brief")
    if not isinstance(mb, dict):
        print("[WARN] market_brief 누락", file=sys.stderr)
        return False
    if mb.get("tone") not in VALID_TONES:
        print(f"[WARN] tone={mb.get('tone')!r} 허용값 아님", file=sys.stderr)
        return False
    if not isinstance(mb.get("key_themes_en"), list) or len(mb["key_themes_en"]) == 0:
        print("[WARN] key_themes_en 누락 또는 빈 배열", file=sys.stderr)
        return False
    if not isinstance(mb.get("key_themes_ko"), list) or len(mb["key_themes_ko"]) == 0:
        print("[WARN] key_themes_ko 누락 또는 빈 배열", file=sys.stderr)
        return False
    for field in ("summary_en", "summary_ko", "watch_points_en", "watch_points_ko"):
        if not isinstance(mb.get(field), str) or not mb[field]:
            print(f"[WARN] market_brief.{field} 누락", file=sys.stderr)
            return False
    sbs = data.get("symbol_briefs")
    if not isinstance(sbs, list) or len(sbs) == 0:
        print("[WARN] symbol_briefs 누락 또는 빈 배열", file=sys.stderr)
        return False
    for sb in sbs:
        if sb.get("setup_quality") not in VALID_SETUP_QUALITY:
            print(f"[WARN] setup_quality={sb.get('setup_quality')!r}", file=sys.stderr)
            return False
        if sb.get("action_bias") not in VALID_ACTION_BIAS:
            print(f"[WARN] action_bias={sb.get('action_bias')!r}", file=sys.stderr)
            return False
        for field in ("brief_en", "brief_ko", "key_risk_en", "key_risk_ko", "key_opportunity_en", "key_opportunity_ko"):
            if not isinstance(sb.get(field), str) or not sb[field]:
                print(f"[WARN] symbol_brief.{field} 누락", file=sys.stderr)
                return False
    return True


def git_commit_push(repo: Path, date_str: str, time_str: str, history_path: Path) -> bool:
    """
    Brief(+context) 생성 후 GitHub에 push.
    실패하면 전체 작업 실패로 처리 (cron에서 감지 가능하게).
    """
    rel_history = str(history_path.relative_to(repo))
    commit_message = f"brief: {date_str} {time_str} update (with context)"

    return commit_and_push(
        repo=repo,
        commit_message=commit_message,
        files_to_add=["brief/latest.json", rel_history],
        push=True,
    )


def main():
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    slot = detect_slot(now)
    print(f"[INFO] 슬롯: {slot}, 시각: {now_iso}")

    print("[INFO] 기술적 데이터 수집 중...")
    tech = fetch_technical_context()

    print("[INFO] 심리 데이터 로드 중...")
    sentiment = load_sentiment()

    prompt = build_brief_prompt(tech, sentiment, slot)
    print("[INFO] Grok 호출 중...")
    raw_text = call_hermes(prompt)
    if raw_text is None:
        print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    parsed = extract_json(raw_text)
    if parsed is None or not validate_brief(parsed):
        print("[ERROR] Brief 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    # Phase 1: Context Attribution — Brief 생성 시점의 기술/레짐/심리 맥락 스냅샷
    context_snapshot = build_brief_context_snapshot(tech, sentiment, now_iso)

    snapshot = {
        "generated_at": now_iso,
        "schema_version": "2.0",
        "slot": slot,
        "market_brief": parsed["market_brief"],
        "symbol_briefs": parsed["symbol_briefs"],
        "context": context_snapshot,   # Phase 1 추가: 생성 당시 맥락 (Context Attribution)
    }

    latest_path = REPO_PATH / "brief" / "latest.json"
    history_dir = REPO_PATH / "brief" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"{date_str}_{slot}.json"

    for path in (latest_path, history_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 저장 완료: {latest_path}, {history_path}")

    push_ok = git_commit_push(REPO_PATH, date_str, time_str, history_path)
    if not push_ok:
        print("[FATAL] GitHub push에 실패했습니다. 최신 context가 sniperboard에 반영되지 않았습니다.")
        sys.exit(1)

    print("[OK] Brief 수집 + GitHub push 완료 (최신 context 반영됨)")


if __name__ == "__main__":
    main()
