#!/usr/bin/env python3
"""
아침 브리핑 수집기 (Morning Briefing Collector)

매일 KST 07:30 (UTC 22:30) 실행.
SniperBoard API + 기존 JSON 파일에서 전체 데이터를 수집하여
Grok(hermes)으로 일반인 친화적 종합 브리핑을 생성한다.

기존 collect_brief.py와의 차이:
  - collect_brief.py : 트레이딩 신호 중심, 종목별 간결 분석
  - collect_morning_briefing.py : 큰 그림 + 개별 종목 상태·스퀴즈·조정 가능성
                                   일반인 이해 가능한 언어로 작성

실행:
  python3 -m collect.collect_morning_briefing

출력:
  briefing/latest.json
  briefing/history/YYYY-MM-DD.json
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
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "180"))
HERMES_RETRY = int(os.environ.get("HERMES_RETRY", "1"))
SNIPERBOARD_API = os.environ.get("SNIPERBOARD_API_BASE", "http://localhost:5001")
CALL_TIMEOUT_GLOBAL = int(os.environ.get("HERMES_TIMEOUT_GLOBAL", "150"))

_VALID_GC_CATEGORIES = {"trade_tariff", "geopolitical", "central_bank", "ai_regulation"}
_VALID_GC_TIERS = {"breaking", "ongoing"}
_VALID_GC_CONFIDENCE = {"confirmed", "developing", "unverified"}
_VALID_GC_IMPACT = {"positive", "negative", "neutral", "watch"}
_VALID_GC_DIRECTION = {"escalating", "de-escalating", "stable_elevated", "stable_fading"}

ALL_SYMBOLS = [
    ("TSM",   "TSMC",                  1),
    ("NVDA",  "Nvidia",                1),
    ("META",  "Meta Platforms",        1),
    ("TSLA",  "Tesla",                 1),
    ("PLTR",  "Palantir",              1),
    ("MU",    "Micron Technology",     1),
    ("CRWD",  "CrowdStrike",           1),
    ("AMZN",  "Amazon",                1),
    ("MSFT",  "Microsoft",             1),
    ("AAPL",  "Apple",                 1),
    ("GOOGL", "Alphabet / Google",     1),
    ("RKLB",  "Rocket Lab",            2),
    ("CEG",   "Constellation Energy",  2),
    ("VST",   "Vistra Energy",         2),
    ("ALAB",  "Astera Labs",           2),
    ("OKLO",  "Oklo",                  2),
    ("APP",   "AppLovin",              2),
    ("ANET",  "Arista Networks",       2),
    ("NVO",   "Novo Nordisk",          2),
    ("QBTS",  "D-Wave Quantum",        2),
    ("SOFI",  "SoFi Technologies",     2),
]


def _api_get(path: str, params: dict | None = None) -> dict | None:
    try:
        resp = requests.get(f"{SNIPERBOARD_API}/api{path}", params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[WARN] API {path} 실패: {e}", file=sys.stderr)
        return None


def _load_json(rel_path: str) -> dict:
    p = REPO_PATH / rel_path
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] {rel_path} 읽기 실패: {e}", file=sys.stderr)
        return {}


def _build_earnings_lookup(earnings_data: dict) -> dict:
    """종목별 실적 발표일·EPS 예상치 조회 dict. upcoming_earnings 기준."""
    lookup: dict = {}
    for e in earnings_data.get("upcoming_earnings", []):
        sym = e.get("symbol")
        if sym and sym not in lookup:
            lookup[sym] = {
                "earnings_date": e.get("earnings_date") or e.get("report_date"),
                "days_until":    e.get("days_until"),
                "eps_estimate":  e.get("eps_estimate"),
            }
    return lookup


def fetch_all_data() -> dict:
    """SniperBoard API + 저장된 JSON 파일에서 전체 시장 데이터 수집."""
    print("[INFO] 시장 데이터 수집 중...")

    regime = _api_get("/regime") or {}
    dd = _api_get("/distribution-days") or {}
    macro = _api_get("/macro") or {}
    watchlist = _api_get("/watchlist") or {}

    sentiment = _load_json("sentiment/latest.json")
    earnings = _load_json("earnings/latest.json")
    earnings_lookup = _build_earnings_lookup(earnings)

    # 21종목 전체 일봉 상세 (스퀴즈/조정 분석용)
    symbol_detail: dict = {}
    for sym, _, _ in ALL_SYMBOLS:
        daily = _api_get("/daily", {"symbol": sym})
        if daily and daily.get("stage2"):
            s2 = daily["stage2"]
            checks = s2.get("checks", {})
            price = s2.get("latest_close", 0)
            entry = s2.get("entry", 0)
            pct_high = round(s2.get("pct_from_52w_high", 0), 1)
            # 52주 고점 절대가: 현재가 / (1 - 고점대비%) — Grok 레벨 계산용
            try:
                high_52w = round(price / (1 - pct_high / 100), 2) if 0 <= pct_high < 100 else round(price, 2)
            except ZeroDivisionError:
                high_52w = round(price, 2)

            earn = earnings_lookup.get(sym, {})
            symbol_detail[sym] = {
                "price":                  round(price, 2),
                "change_pct_1d":          round(daily.get("change_pct_1d") or s2.get("change_pct_1d") or 0.0, 2),
                "high_52w_price":         high_52w,
                "earnings_date":          earn.get("earnings_date"),
                "days_until_earnings":    earn.get("days_until"),
                "eps_estimate":           earn.get("eps_estimate"),
                "stage2_score":           s2.get("score", 0),
                "rs_score":               round(s2.get("rs_score", 50), 1),
                "market_structure":       s2.get("market_structure", "NEUTRAL"),
                "monthly_phase":          s2.get("monthly_phase", "UNKNOWN"),
                "ema200_slope":           round(s2.get("ema200_slope", 0), 4),
                "pct_from_52w_high":      pct_high,
                "pullback_pct":           round(s2.get("pullback_pct", 0), 1),
                "pct_vs_entry":           round((price - entry) / entry * 100, 1) if entry else None,
                "entry":                  round(entry, 2),
                # Stage2 체크 (스퀴즈 핵심 지표)
                "volume_contracting":     checks.get("volume_contracting", False),
                "near_52w_high":          checks.get("near_52w_high", False),
                "pullback_shallow":       checks.get("pullback_shallow", False),
                "price_above_emas":       checks.get("price_above_emas", False),
                "ema200_rising":          checks.get("ema200_rising", False),
                # 가우시안 채널
                "gc_above":               s2.get("gc_above", False),
                "gc_breakout":            s2.get("gc_breakout", False),
                "gc_retest":              s2.get("gc_retest", False),
                # 조정/하락 패턴
                "bear_flag":              s2.get("bear_flag", False),
                "rsi_divergence_bearish": s2.get("rsi_divergence_bearish", False),
                "rsi_divergence_bullish": s2.get("rsi_divergence_bullish", False),
            }

    return {
        "regime":        regime,
        "distribution":  dd,
        "macro":         macro,
        "watchlist":     watchlist.get("watchlist", []),
        "symbol_detail": symbol_detail,
        "sentiment":     sentiment,
        "earnings":      earnings,
    }


def _format_authoritative_table(data: dict) -> str:
    """
    Grok 참조용 수치 바인딩 테이블.
    Grok이 분석 텍스트에 쓰는 모든 가격·등락률·실적일은 반드시 이 테이블에서 가져와야 한다.
    """
    detail = data["symbol_detail"]
    hdr = f"{'심볼':<6} {'현재가':>10} {'1일등락':>8} {'52주고점':>11} {'고점%':>7}  {'실적발표일':<12} {'EPS예상':>9}"
    sep = "-" * 72
    rows = [hdr, sep]
    for sym, _, _ in ALL_SYMBOLS:
        d = detail.get(sym)
        if not d:
            rows.append(f"{sym:<6} {'데이터없음':>10}")
            continue
        price_s  = f"${d['price']:,.2f}"
        chg_s    = f"{d.get('change_pct_1d', 0):+.2f}%"
        high_s   = f"${d['high_52w_price']:,.2f}" if d.get("high_52w_price") else "N/A"
        highp_s  = f"{d['pct_from_52w_high']:.1f}%"
        earn_s   = d.get("earnings_date") or "N/A"
        eps_s    = f"${d['eps_estimate']}" if d.get("eps_estimate") is not None else "N/A"
        rows.append(f"{sym:<6} {price_s:>10} {chg_s:>8} {high_s:>11} {highp_s:>7}  {earn_s:<12} {eps_s:>9}")
    rows.append(sep)
    rows.append("⚠ BINDING: 위 표의 값과 다른 가격·등락률·실적일을 브리핑에 쓰는 것은 금지.")
    rows.append("  값이 N/A이면 해당 수치를 추측하지 말 것. 실적일이 N/A면 '30일 이내 없음'으로 처리.")
    return "\n".join(rows)


def _format_symbol_block(data: dict) -> str:
    """21종목 데이터를 Grok 프롬프트용 텍스트로 변환."""
    detail = data["symbol_detail"]
    sent_by_sym = {s.get("symbol"): s for s in data["sentiment"].get("symbols", [])}
    lines = []

    for sym, company, tier in ALL_SYMBOLS:
        d = detail.get(sym)
        if not d:
            lines.append(f"{sym} ({company}) [T{tier}]: 데이터 없음")
            continue

        sent = sent_by_sym.get(sym, {})

        # 기술적 신호를 설명형으로 변환 (Grok이 자연어로 해석할 수 있게)
        signals = []
        if d["price_above_emas"]:
            signals.append("모든 이평선 위")
        else:
            signals.append("이평선 아래")
        if d["ema200_rising"]:
            signals.append("200일선 상승중")
        if d["gc_above"] and not d["gc_breakout"]:
            signals.append("가우시안채널 위(돌파전)")
        if d["gc_breakout"]:
            signals.append("가우시안채널 돌파")
        if d["gc_retest"]:
            signals.append("가우시안채널 재테스트")
        if d["volume_contracting"]:
            signals.append("거래량 감소(잠재적 에너지 축적)")
        if d["near_52w_high"]:
            signals.append("52주 고점 인근")
        if d["bear_flag"]:
            signals.append("⚠베어플래그패턴")
        if d["rsi_divergence_bearish"]:
            signals.append("⚠모멘텀둔화신호")
        if d["rsi_divergence_bullish"]:
            signals.append("✓모멘텀강화신호")

        vs_entry = f"{d['pct_vs_entry']:+.1f}%" if d["pct_vs_entry"] is not None else "N/A"
        chg_1d = d.get("change_pct_1d", 0.0)
        chg_1d_str = f"{chg_1d:+.2f}%" if chg_1d != 0.0 else "0.00%(데이터없음)"
        earn_date = d.get("earnings_date")
        days_earn = d.get("days_until_earnings")
        eps_est = d.get("eps_estimate")
        if earn_date:
            earn_str = f"【실적발표={earn_date} ({days_earn}일후) / EPS예상=${eps_est}】"
        else:
            earn_str = "【실적발표=해당없음(30일이내없음)】"
        sent_reason = sent.get('key_reason_en') or sent.get('key_reason', '')
        sent_ko = sent.get('key_reason_ko', '')

        lines.append(
            f"{sym} ({company}) [T{tier}]\n"
            f"  Stage2점수={d['stage2_score']}/7  시장상대강도RS={d['rs_score']}  "
            f"구조={d['market_structure']}  월봉추세={d['monthly_phase']}\n"
            f"  현재가=${d['price']}  【오늘1일등락={chg_1d_str}】  "
            f"52주고점=${d['high_52w_price']}(대비{d['pct_from_52w_high']}%)  "
            f"돌파목표대비={vs_entry}  최근눌림={d['pullback_pct']}%\n"
            f"  {earn_str}\n"
            f"  기술신호: {', '.join(signals)}\n"
            f"  소셜심리: {sent.get('sentiment','N/A')} (점수={sent.get('composite_score','N/A')})\n"
            f"  투자자반응: {sent_reason}\n"
            f"  투자자반응(KO): {sent_ko}"
        )

    return "\n\n".join(lines)


def _format_macro_block(macro_data: dict) -> str:
    """매크로 주요 지표를 프롬프트용 요약 텍스트로 변환."""
    items = macro_data.get("macro", [])
    key_syms = {"^VIX", "^TNX", "DX-Y.NYB", "CL=F", "GLD", "TLT", "HYG", "BTC-USD"}
    lines = []
    for item in items:
        sym = item.get("symbol", "")
        if sym not in key_syms:
            continue
        lines.append(
            f"{sym}: ${item.get('price','?')}  "
            f"1D={item.get('change_pct_1d','?')}%  "
            f"5D={item.get('change_pct_5d','?')}%  "
            f"구조={item.get('market_structure','?')}"
        )
    return "\n".join(lines) if lines else "매크로 데이터 없음"


def _format_earnings_block(earnings_data: dict) -> str:
    """향후 실적 발표 일정 요약."""
    upcoming = earnings_data.get("upcoming_earnings", [])
    if not upcoming:
        return "향후 7일 내 주요 실적 없음"
    lines = []
    for e in upcoming[:5]:
        sym = e.get("symbol", "?")
        date = e.get("report_date", "?")
        lines.append(f"  {sym} {date} (EPS예상: {e.get('eps_estimate','?')})")
    return "\n".join(lines)


def _format_global_context_block(global_ctx: dict) -> str:
    """글로벌 컨텍스트를 2차 Grok 프롬프트 주입용 텍스트로 변환."""
    issues = global_ctx.get("issues", [])
    if not issues:
        return "GLOBAL CONTEXT: No verified global issues retrieved (search failed or no significant events)."

    lines = [
        "━━━ GLOBAL MACRO & GEOPOLITICAL CONTEXT ━━━",
        f"(Verified within 48h as of {global_ctx.get('fetched_at', 'unknown')})",
        "Use this context to enrich your briefing. Each issue includes current state, direction, and per-ticker impact.\n",
    ]
    for iss in issues:
        conf = iss.get("confidence", "confirmed")
        conf_tag = "" if conf == "confirmed" else f" [{conf.upper()}]"
        direction = iss.get("direction", "unknown")
        lines.append(
            f"[{iss.get('rank')}][{iss.get('tier', '').upper()}][{iss.get('category', '')}]"
            f"[{direction.upper()}]{conf_tag} {iss.get('title_en', '')}"
            f"\n  Source: {iss.get('source_hint', 'unknown')}"
            f"\n  Current State: {iss.get('current_state_en', '')}"
            f"\n  Summary: {iss.get('summary_en', '')}"
            f"\n  Asymmetric Impact: {iss.get('asymmetric_impact_en', '')}"
            f"\n  Investor Insight: {iss.get('market_insight_en', '')}"
        )

    paradox = global_ctx.get("market_paradox_en", "")
    if paradox:
        lines.append(f"\n⚠ MARKET PARADOX: {paradox}")

    no_update = global_ctx.get("ongoing_no_update", [])
    if no_update:
        lines.append(f"\nDormant background (no near-term market impact): {', '.join(no_update)}")

    lines.append("""
INSTRUCTIONS for using this context in your briefing:
- big_picture.summary: incorporate the highest-ranked issue naturally (1 sentence); flag the market_paradox if present
- sector_analysis: reflect the direction and asymmetric impact on sectors — use the direction field, not vague "remains a risk"
- spotlight/watchlist: for any ticker named in asymmetric_impact, reference the specific directional implication
- For [DEVELOPING] or [UNVERIFIED] items: mention with appropriate caution language
- Do NOT write "monitoring continues" or "situation ongoing" — state the direction and implication
""")
    return "\n".join(lines)


def validate_global_context(data: dict) -> bool:
    """1차 Grok 응답 글로벌 컨텍스트 검증. 0개 이슈는 fallback으로 유효."""
    if not isinstance(data, dict):
        return False
    issues = data.get("issues")
    if not isinstance(issues, list):
        return False
    if len(issues) == 0:
        return True
    if len(issues) > 3:
        print(f"[WARN] global_context: 이슈 {len(issues)}개 — 3개 초과", file=sys.stderr)
        return False
    for iss in issues:
        if not isinstance(iss, dict):
            return False
        if iss.get("category") not in _VALID_GC_CATEGORIES:
            print(f"[WARN] global_context: category={iss.get('category')!r}", file=sys.stderr)
            return False
        if iss.get("tier") not in _VALID_GC_TIERS:
            print(f"[WARN] global_context: tier={iss.get('tier')!r}", file=sys.stderr)
            return False
        if iss.get("confidence") not in _VALID_GC_CONFIDENCE:
            print(f"[WARN] global_context: confidence={iss.get('confidence')!r}", file=sys.stderr)
            return False
        if iss.get("impact_direction") not in _VALID_GC_IMPACT:
            print(f"[WARN] global_context: impact_direction={iss.get('impact_direction')!r}", file=sys.stderr)
            return False
        if iss.get("direction") not in _VALID_GC_DIRECTION:
            print(f"[WARN] global_context: direction={iss.get('direction')!r}", file=sys.stderr)
            return False
        for field in ("title_en", "title_ko", "current_state_en", "current_state_ko",
                      "summary_en", "summary_ko", "asymmetric_impact_en", "asymmetric_impact_ko",
                      "market_insight_en", "market_insight_ko"):
            if not isinstance(iss.get(field), str) or not iss[field]:
                print(f"[WARN] global_context: {field} 누락", file=sys.stderr)
                return False
    return True


def build_global_context_prompt(now_kst: str, now_iso: str) -> str:
    return f"""You are a professional financial intelligence analyst with live web search access.
Today is {now_kst} (KST) / {now_iso} (UTC).

━━━ TASK ━━━
Search the web for the top 3 global macro and geopolitical issues that carry the HIGHEST market-moving
potential for US stocks TODAY. These can be new (last 48h) or ongoing situations with active risk.

For each issue you MUST provide:
(a) Current state — where things stand RIGHT NOW, not historical background
(b) Direction — is the situation escalating, de-escalating, or stable?
(c) Asymmetric ticker impact — which watchlist stocks benefit vs. which are hurt, and WHY
(d) Market insight — the actionable implication for an investor today

MANDATORY CHECK LIST — search and assess each even if quiet:
  · US-China semiconductor export controls / tariffs (NVDA, TSM, MU)
  · Taiwan Strait tension (TSM, NVDA supply chain)
  · Middle East / Iran / Strait of Hormuz (oil price, energy, macro VIX)
  · Russia-Ukraine war (energy, European equities)
  · ECB / BOJ / BOE policy (USD direction, rate-sensitive tech)
  · US AI / antitrust regulation (GOOGL, META, MSFT, AAPL)
  · US tariff / trade deal negotiations

━━━ ANALYSIS STANDARDS — READ CAREFULLY ━━━
✓ State the CURRENT STATUS and DIRECTION for every issue, not just that it exists.
  BAD: "US-China export controls remain in place — impact unclear"
  GOOD: "US-China export controls shifted Jan 2026 to case-by-case licensing + 25% tariff —
         direction: transactional (not pure blockade); NVDA: asymmetric upside on approval news"
✓ For geopolitical situations: distinguish between BACKGROUND NOISE and ACTIVE RISK.
  An ongoing war with a closed strait IS active risk regardless of 48h news silence.
✓ Flag market paradoxes: if VIX or rates seem inconsistent with actual risk level, call it out.
✓ ONLY use verifiable sources. Include source_hint: "Reuters 2026-06-03", "White House statement", etc.
✓ Prefix unconfirmed facts with "unconfirmed:"

✗ FORBIDDEN PHRASES — these are analysis avoidance, not analysis:
  "impact unclear", "direction uncertain", "no new developments — impact unclear",
  "monitoring continues", "situation ongoing". Every issue must have a direction and ticker mapping.
✗ DO NOT list a stock as impacted without stating the direction (positive / negative / conditional).
✗ DO NOT use ongoing_no_update for any situation with active market risk (e.g. hot wars, open policy uncertainty).
  ongoing_no_update is ONLY for truly dormant background items with negligible near-term market impact.
✗ DO NOT fabricate figures, names, or dates you cannot verify.
✗ DO NOT include historical context as if it were a new development.

━━━ WATCHLIST TICKERS FOR IMPACT MAPPING ━━━
TSM NVDA META TSLA PLTR MU CRWD AMZN MSFT AAPL GOOGL
RKLB CEG VST ALAB OKLO APP ANET NVO QBTS SOFI

Output raw JSON only (no markdown, no prose before or after).
CRITICAL: The "issues" array must contain EXACTLY 3 items.
{{
  "fetched_at": "{now_iso}",
  "search_window": "48h",
  "issues": [
    {{
      "rank": 1,
      "tier": "breaking|ongoing",
      "category": "trade_tariff|geopolitical|central_bank|ai_regulation",
      "title_en": "factual headline stating current status ≤80 chars",
      "title_ko": "현재 상태 중심 30자 이내",
      "current_state_en": "1-2 sentences: WHERE DOES THIS STAND RIGHT NOW? Not history — the live state as of today.",
      "current_state_ko": "지금 이 이슈의 현재 상태 1-2문장. 배경 설명 아님.",
      "direction": "escalating|de-escalating|stable_elevated|stable_fading",
      "summary_en": "2-3 sentences: what changed recently, source, why it moves markets. Prefix unconfirmed with 'unconfirmed:'",
      "summary_ko": "같은 내용 한국어 2-3문장.",
      "source_hint": "e.g. Reuters 2026-06-03 / White House statement / BIS rule update",
      "confidence": "confirmed|developing|unverified",
      "asymmetric_impact_en": "Per-ticker directional mapping. Format: 'NVDA: positive if X / negative if Y; TSM: neutral (demand-driven); MU: unaffected'. No 'unclear' without conditional direction.",
      "asymmetric_impact_ko": "종목별 방향 분석. 'NVDA: X 시 상방 / Y 시 하방; TSM: 중립(수요 주도)' 형태.",
      "impact_direction": "positive|negative|neutral|watch",
      "market_insight_en": "1 sentence: what should an investor watch or how to position given this issue RIGHT NOW.",
      "market_insight_ko": "지금 이 이슈를 보고 투자자가 취해야 할 행동 또는 주시할 트리거 한 문장."
    }}
  ],
  "market_paradox_en": "If VIX, rates, or market pricing appears inconsistent with the actual risk environment described above, flag it in 1-2 sentences. Empty string if no paradox.",
  "market_paradox_ko": "위에서 기술한 실제 리스크 수준과 VIX·금리·시장 가격 간 명백한 괴리가 있으면 1-2문장으로 기술. 없으면 빈 문자열.",
  "ongoing_no_update": ["ONLY truly dormant categories with negligible near-term market impact"]
}}"""


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


def build_prompt(data: dict, now_kst: str, global_ctx: dict | None = None) -> str:
    global_block = _format_global_context_block(global_ctx or {})
    regime = data["regime"]
    dd = data["distribution"]
    spy_dd = dd.get("spy", {})
    qqq_dd = dd.get("qqq", {})
    market_sent = data["sentiment"].get("market", {})
    slot = data["sentiment"].get("slot", "unknown")
    regime_label = regime.get("regime", "UNKNOWN")
    regime_score = regime.get("total", "N/A")
    comps = regime.get("components", {})

    auth_table = _format_authoritative_table(data)
    symbol_block = _format_symbol_block(data)
    macro_block = _format_macro_block(data["macro"])
    earnings_block = _format_earnings_block(data["earnings"])

    return f"""You are a friendly stock market expert writing a morning briefing for Korean retail investors who are NOT finance professionals.
Today is {now_kst} (KST).

{global_block}

━━━ SNIPERBOARD AUTHORITATIVE DATA TABLE ━━━
All values below come directly from real-time data feeds (prices, earnings calendars).
These are the ONLY numbers you are allowed to use in your briefing for prices, % changes, and earnings dates.
Do NOT substitute, approximate, invent, or recall from training data — use this table exclusively.

{auth_table}

WRITING RULES — follow strictly:
1. Write as if explaining to a smart friend who doesn't know stock jargon. Use everyday language.
2. When a technical term is unavoidable, explain it immediately in plain words.
   Good: "RS(시장 상대강도 — 이 주식이 전체 시장보다 얼마나 더 잘 움직이는지 나타내는 점수) 88점"
   Bad: "RS=88" alone without explanation.
3. For each stock: weave together in ONE flowing paragraph — recent price movement, current condition,
   upside potential OR downside risk (choose the more dominant factor), and what social investors are saying.
   Do NOT use section headers like "스퀴즈:", "조정:", "현재상태:" — write as natural prose.
4. Use concrete human language: "마치 스프링처럼 에너지가 축적된 상태", "기관들이 조용히 팔고 있는 흔적",
   "투자자들 사이에서 기대감이 높아지고 있다" etc.
5. Be honest about risks — don't sugarcoat weak stocks.
6. Korean must read naturally — avoid literal translation feel.
7. DATA BINDING: Every price ($X), % change, and earnings date you write MUST match the table above.
   If the table shows 1일등락=0.00%(데이터없음), write directional movement without a specific % figure.
   If earnings date is N/A, write "30일 이내 실적 발표 없음" — never write "곧", "다음 주", "이번 주" without data.

MARKET DATA ({now_kst}):
- 리스크 레짐: {regime_label} ({regime_score}/100)
  [RISK_ON≥80=매수 우호 / CONSTRUCTIVE≥60=긍정적 / MIXED≥40=혼조 / DEFENSIVE≥20=방어적 / RISK_OFF<20=위험회피]
  추세점수={comps.get('trend','?')}  시장폭={comps.get('breadth','?')}  신용={comps.get('credit','?')}  변동성={comps.get('volatility','?')}  모멘텀={comps.get('momentum','?')}
- SPY 분배일(기관매도흔적): {spy_dd.get('count','?')}일 ({spy_dd.get('level','?')}) [4일미만=정상 / 4-5일=주의 / 6일이상=위험]
- QQQ 분배일: {qqq_dd.get('count','?')}일 ({qqq_dd.get('level','?')})
- 전체시장 소셜심리: {market_sent.get('sentiment','N/A')} (종합점수={market_sent.get('composite_score','N/A')})

주요 매크로 지표:
{macro_block}

감시 종목 21개 (기술적 데이터 + 소셜심리):
{symbol_block}

향후 실적 발표:
{earnings_block}

아래 JSON 스키마 그대로 출력하라 (raw JSON only, no markdown):

{{
  "headline_en": "One sentence — the most important market story today (≤120 chars)",
  "headline_ko": "오늘 시장에서 가장 중요한 한 줄 (30자 이내, 구어체)",
  "executive_bullets_en": [
    "Most important macro/regime context in plain words",
    "Best opportunity in the watchlist right now",
    "Biggest risk to be aware of today"
  ],
  "executive_bullets_ko": [
    "시장 환경 핵심 (쉬운 말로)",
    "지금 가장 주목할 기회 (구체적 종목 언급 가능)",
    "오늘 가장 조심해야 할 리스크"
  ],
  "market_mood": {{
    "traffic_light": "green|yellow|red",
    "label_en": "e.g. Cautiously Positive",
    "label_ko": "e.g. 조심스럽게 긍정적",
    "score": {regime_score},
    "explanation_en": "2 sentences in plain language. Use an analogy (e.g. traffic, weather, rowing upstream). Explain what the regime score means for someone deciding whether to buy stocks today.",
    "explanation_ko": "같은 내용 한국어 2문장. 비유 포함. '지금 주식을 사도 될까?'에 답하는 느낌으로 작성."
  }},
  "big_picture": {{
    "summary_en": "2 sentences — the macro backdrop explained like a news anchor would say it",
    "summary_ko": "같은 내용 한국어 2문장. 뉴스 앵커가 말하듯 자연스럽게.",
    "vix_note_en": "1-2 sentences: what is VIX at today, and what does it mean in human terms (fear/calm/overconfident?)",
    "vix_note_ko": "VIX가 얼마이고 그게 무슨 의미인지 — VIX를 모르는 사람도 이해하게.",
    "rates_note_en": "1-2 sentences: 10Y yield level and whether it's helping or hurting stocks today",
    "rates_note_ko": "미국 10년물 국채 금리(기준금리의 바로미터)가 오늘 주식 시장에 어떤 영향을 주는지.",
    "dollar_note_en": "1-2 sentences: DXY direction and impact — especially for tech/global earnings",
    "dollar_note_ko": "달러 강세/약세가 미국 기술주와 해외 투자자에게 어떤 의미인지.",
    "btc_note_en": "1-2 sentences: Bitcoin price level and what it signals about risk appetite today (is crypto leading risk-on or risk-off?)",
    "btc_note_ko": "비트코인 현재 가격과 그것이 오늘 투자자들의 위험 선호도에 대해 무엇을 말하는지 — 가상화폐를 잘 모르는 사람도 이해하게."
  }},
  "sector_analysis": {{
    "leaders_en": "Which sectors/themes are leading and why — 1-2 sentences with simple explanation",
    "leaders_ko": "어떤 업종이 돈을 끌어모으고 있는지, 이유는 무엇인지 — 업종 이름과 이유를 자연스럽게.",
    "laggards_en": "Which are lagging and the simple reason why",
    "laggards_ko": "어떤 업종이 힘을 못 쓰고 있는지, 왜 그런지.",
    "rotation_signal_en": "Is money rotating between sectors? Where is it going and what does that signal?",
    "rotation_signal_ko": "돈이 한 섹터에서 다른 섹터로 이동하고 있는가? 어디로 가고 있는지, 투자자에게 무슨 의미인지."
  }},
  "spotlight": [
    {{
      "symbol": "TICKER",
      "company": "Company Name",
      "tier": 1,
      "why_en": "2-3 sentences. Any price level mentioned MUST match the 현재가/52주고점 from the AUTHORITATIVE DATA TABLE.",
      "why_ko": "오늘 이 종목이 특별히 주목받는 이유 2-3문장. 가격대는 반드시 위 테이블의 현재가 기준.",
      "watch_level_en": "Use current price from the table as anchor. e.g. 'Break above $X (current $Y, table-verified); support near $Z (entry level from data)'",
      "watch_level_ko": "테이블의 현재가·52주고점·entry 값 기반. '$X 돌파(현재 $Y) / $Z 이탈 시 경고' 형태."
    }}
  ],
  "watchlist": [
    {{
      "symbol": "TICKER",
      "company": "Company Name",
      "tier": 1,
      "analysis_en": "3-5 sentences flowing paragraph. (1) recent price movement using EXACT price/change from the table, (2) strength or vulnerability in plain language, (3) upside or downside, (4) social sentiment. All $ values and % changes must match the AUTHORITATIVE DATA TABLE. For earnings: use exact date from table or write 'no earnings within 30 days'.",
      "analysis_ko": "같은 내용 한국어 3-5문장. 가격·등락률은 위 테이블 값 그대로. 실적일도 테이블 기준. 소셜 반응 자연스럽게 포함.",
      "sentiment_mood": "optimistic|cautious|neutral|fearful|euphoric — from the social data above",
      "sentiment_score": 0.0,
      "action": "buy|hold|watch|avoid"
    }}
  ],
  "today_checkpoints_en": [
    "Specific thing to watch — use exact price levels from the table, exact earnings dates from the table"
  ],
  "today_checkpoints_ko": [
    "오늘 주시할 포인트 — 가격은 테이블 기준, 실적일은 테이블 기준 정확한 날짜 명시"
  ],
  "earnings_alert_en": "Use EXACT dates from the authoritative table. e.g. 'CRWD reports on 2026-06-04 (EPS est. $1.07); MU reports on 2026-06-25 (EPS est. $19.28)'. Never approximate with 'next week' or 'soon'.",
  "earnings_alert_ko": "테이블의 정확한 실적 날짜 사용. 'CRWD 6월 4일(EPS 예상 $1.07), MU 6월 25일(EPS 예상 $19.28)' 형태. '다음 주', '곧' 등 근사치 금지."
}}

REQUIREMENTS:
- spotlight: 2-4 most interesting from the 21 (mix of opportunities and risks)
- watchlist: ALL 21 in order TSM,NVDA,META,TSLA,PLTR,MU,CRWD,AMZN,MSFT,AAPL,GOOGL,RKLB,CEG,VST,ALAB,OKLO,APP,ANET,NVO,QBTS,SOFI
- action=buy: Stage2≥6 AND RS≥70 AND strong upward momentum AND positive sentiment
- action=hold: solid setup, currently in position, no strong buy signal but not selling
- action=watch: interesting setup, wait for better entry or confirmation
- action=avoid: DOWNTREND structure OR Stage2≤2 OR high distribution signals
- sentiment_score: copy from the social data (composite_score field)
- analysis_ko must integrate sentiment naturally — not as a separate item at the end

ANTI-HALLUCINATION RULES — CRITICAL:
1. PRICE LEVELS (watch_level_en/ko): All price levels MUST be derived from the 현재가=$X field.
   Levels must fall within ±25% of the current price shown in the data.
   NEVER invent a support/resistance level that is more than 25% away from current price.
   If you do not have sufficient data to determine a specific level, write a range relative to current price
   (e.g. "5% above current price of $X" or "below the $Y entry level from the data").

2. PRICE CHANGES (% drops, gains): ONLY state percentage changes that appear in 【오늘1일등락=X%】 fields.
   If 오늘1일등락 shows 0.00%(데이터없음), you do NOT know today's % change — write "dropped" or "sold off"
   without inventing a specific percentage. NEVER state a % price change you cannot verify from the data.

3. EARNINGS DATES: ALL earnings dates and timing MUST come from the "향후 실적 발표" section.
   NEVER write "next week", "soon", or "imminent" for any stock unless its exact date appears within 7 days
   in the earnings data. If a stock's earnings date is more than 7 days out, write the EXACT date
   (e.g. "earnings on June 24") not a vague approximation.

4. SECTOR LEADERS: sector_analysis.leaders must reflect TECHNICAL leadership (market_structure=UPTREND/STAGE2).
   A stock in DOWNTREND structure has social buzz but is NOT a technical leader — if you mention it,
   explicitly note "narrative interest but in DOWNTREND" to distinguish from technical leaders.

- Raw JSON only. No prose before or after."""


def call_hermes(prompt: str, timeout: int | None = None) -> str | None:
    cmd = [HERMES_CMD, "-z", prompt]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    effective_timeout = timeout if timeout is not None else CALL_TIMEOUT
    for attempt in range(1 + HERMES_RETRY):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=effective_timeout, env=env)
            if result.returncode != 0:
                print(f"[ERROR] hermes 비정상 종료: {result.stderr[:300]}", file=sys.stderr)
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
        print(f"[ERROR] JSON 블록 없음. 응답 앞부분: {text[:400]!r}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 파싱 실패: {e}", file=sys.stderr)
        return None


VALID_TRAFFIC_LIGHT = {"green", "yellow", "red"}
VALID_ACTION = {"buy", "hold", "watch", "avoid"}
VALID_SENTIMENT_MOOD = {"optimistic", "cautious", "neutral", "fearful", "euphoric"}


def validate_briefing(data: dict) -> bool:
    for field in ("headline_en", "headline_ko"):
        if not isinstance(data.get(field), str) or not data[field]:
            print(f"[WARN] {field} 누락", file=sys.stderr)
            return False
    for field in ("executive_bullets_en", "executive_bullets_ko"):
        if not isinstance(data.get(field), list) or len(data[field]) == 0:
            print(f"[WARN] {field} 누락 또는 빈 배열", file=sys.stderr)
            return False

    mood = data.get("market_mood", {})
    if mood.get("traffic_light") not in VALID_TRAFFIC_LIGHT:
        print(f"[WARN] market_mood.traffic_light 유효하지 않음: {mood.get('traffic_light')!r}", file=sys.stderr)
        return False

    watchlist = data.get("watchlist", [])
    if len(watchlist) < 10:
        print(f"[WARN] watchlist 종목 수 부족: {len(watchlist)}", file=sys.stderr)
        return False
    for item in watchlist:
        if item.get("action") not in VALID_ACTION:
            print(f"[WARN] action 오류: {item.get('symbol')} = {item.get('action')!r}", file=sys.stderr)
            return False
        # analysis_en/ko 둘 중 하나 이상은 있어야 함
        if not item.get("analysis_en") and not item.get("analysis_ko"):
            print(f"[WARN] analysis 누락: {item.get('symbol')}", file=sys.stderr)
            return False

    spotlight = data.get("spotlight", [])
    if len(spotlight) == 0:
        print("[WARN] spotlight 비어 있음", file=sys.stderr)
        return False

    return True


def main():
    now = datetime.now(timezone.utc)
    # KST = UTC+9
    import datetime as dt
    kst_offset = dt.timedelta(hours=9)
    now_kst_dt = now + kst_offset
    now_kst = now_kst_dt.strftime("%Y-%m-%d %H:%M KST")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    print(f"[INFO] 아침 브리핑 시작: {now_kst}")

    data = fetch_all_data()

    # ── 1차 호출: 글로벌 매크로/지정학 컨텍스트 수집 ──────────────────────────
    global_ctx: dict = {}
    global_context_prompt = build_global_context_prompt(now_kst, now_iso)
    print("[INFO] Grok 1차 호출: 글로벌 컨텍스트 수집 중 (최대 90초)...")
    global_raw = call_hermes(global_context_prompt, timeout=CALL_TIMEOUT_GLOBAL)
    if global_raw:
        global_ctx = parse_global_context(global_raw)
        if global_ctx and global_ctx.get("issues"):
            print(f"[INFO] 글로벌 이슈 {len(global_ctx['issues'])}개 수집됨")
        else:
            print("[WARN] 글로벌 컨텍스트: 이슈 없음 — fallback으로 계속 진행", file=sys.stderr)
    else:
        print("[WARN] 글로벌 컨텍스트 Grok 호출 실패 — fallback으로 계속 진행", file=sys.stderr)

    # ── 2차 호출: 아침 브리핑 생성 (글로벌 컨텍스트 주입) ───────────────────
    prompt = build_prompt(data, now_kst, global_ctx)
    print("[INFO] Grok 2차 호출: 아침 브리핑 생성 중 (최대 3분 소요)...")
    raw_text = call_hermes(prompt)
    if raw_text is None:
        print("[ERROR] Grok 호출 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    parsed = extract_json(raw_text)
    if parsed is None or not validate_briefing(parsed):
        print("[ERROR] 브리핑 검증 실패 — 종료", file=sys.stderr)
        sys.exit(1)

    snapshot = {
        "generated_at": now_iso,
        "schema_version": "1.1",
        "slot": "morning",
        **parsed,
        "global_context": global_ctx if global_ctx else {"issues": [], "fallback": True},
    }

    briefing_dir = REPO_PATH / "briefing"
    briefing_dir.mkdir(exist_ok=True)
    history_dir = briefing_dir / "history"
    history_dir.mkdir(exist_ok=True)

    latest_path = briefing_dir / "latest.json"
    history_path = history_dir / f"{date_str}.json"

    for path in (latest_path, history_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 저장: {latest_path}, {history_path}")

    rel_history = str(history_path.relative_to(REPO_PATH))
    ok = commit_and_push(
        repo=REPO_PATH,
        commit_message=f"briefing: {date_str} morning update",
        files_to_add=["briefing/latest.json", rel_history],
        push=True,
    )
    if not ok:
        print("[FATAL] GitHub push 실패", file=sys.stderr)
        sys.exit(1)

    print("[OK] 아침 브리핑 완료 + GitHub push 성공")


if __name__ == "__main__":
    main()
