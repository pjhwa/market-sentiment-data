#!/usr/bin/env python3
"""
AI Daily Brief 수집기 (Phase 2: Accuracy-hardened)

① Sniperboard API에서 Regime, DD, Macro, 종목별 Stage2/신호/Prepost 수집
② earnings/latest.json에서 실적 일정 로드
③ sentiment/latest.json에서 소셜 심리 로드
④ Grok(Hermes)으로 brief JSON 생성 (authoritative binding table 포함)
⑤ brief/latest.json + brief/history/<date>_<slot>.json 저장
⑥ **성공 시 반드시 git commit + push** → sniperboard가 최신 context를 즉시 볼 수 있게 함

중요: push가 실패하면 전체 작업을 실패로 처리합니다. (cron 알림 목적)
"""

import json
import os
import re
import subprocess
import sys
import datetime as dt
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

# TIER1: 빅테크/대형주 — 개별 심층 분석 대상
WATCHLIST = [
    ("TSM",   "TSMC"),
    ("NVDA",  "Nvidia"),
    ("META",  "Meta Platforms"),
    ("TSLA",  "Tesla"),
    ("PLTR",  "Palantir"),
    ("MU",    "Micron Technology"),
    ("CRWD",  "CrowdStrike"),
    ("AMZN",  "Amazon"),
    ("MSFT",  "Microsoft"),
    ("AAPL",  "Apple"),
    ("GOOGL", "Alphabet / Google"),
]


# ─────────────────────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────────────────────

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


def _build_earnings_lookup(earnings_data: dict, now_kst_date=None) -> dict:
    """종목별 실적 발표일 조회 dict. days_until은 KST 날짜로 재계산."""
    if now_kst_date is None:
        now_kst_date = (datetime.now(timezone.utc) + dt.timedelta(hours=9)).date()

    lookup: dict = {}
    for e in earnings_data.get("upcoming_earnings", []):
        sym = e.get("symbol")
        if sym and sym not in lookup:
            earn_date_str = e.get("earnings_date") or e.get("report_date")
            try:
                earn_date = dt.date.fromisoformat(earn_date_str) if earn_date_str else None
            except ValueError:
                earn_date = None
            days_until = (earn_date - now_kst_date).days if earn_date else None
            lookup[sym] = {
                "earnings_date":              earn_date_str,
                "days_until":                 days_until,
                "eps_estimate":               e.get("eps_estimate"),
                "already_reported_possible":  (days_until is not None and days_until <= 0),
            }
    return lookup


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 수집
# ─────────────────────────────────────────────────────────────────────────────

def fetch_technical_context() -> dict:
    """Sniperboard API + 로컬 JSON에서 시장 전체 + 종목별 기술적 데이터 수집."""
    regime  = _api_get("/regime") or {}
    dd      = _api_get("/distribution-days") or {}
    macro   = _api_get("/macro") or {}

    earnings  = _load_json("earnings/latest.json")
    earnings_lookup = _build_earnings_lookup(earnings)

    symbol_data: dict = {}
    prepost_data: dict = {}

    for sym, _ in WATCHLIST:
        # 일봉 상세 (Stage2 + 가격 앵커)
        daily = _api_get("/daily", {"symbol": sym})
        if daily and daily.get("stage2"):
            s2 = daily["stage2"]
            checks = s2.get("checks", {})
            price = s2.get("latest_close", 0)
            entry = s2.get("entry", 0)
            pct_high = round(s2.get("pct_from_52w_high", 0), 1)
            try:
                denom = 1 + pct_high / 100
                high_52w = round(price / denom, 2) if 0 < denom < 10 else round(price, 2)
            except ZeroDivisionError:
                high_52w = round(price, 2)

            # 전일 등락률 (D-2 → D-1)
            candles = daily.get("candles", [])
            if len(candles) >= 2:
                p1 = candles[-2].get("close", 0)
                p2 = candles[-1].get("close", price)
                chg_prev_day = round((p2 - p1) / p1 * 100, 2) if p1 else 0.0
            else:
                chg_prev_day = 0.0

            # RSI14: 마지막 캔들에서 추출
            rsi14 = None
            if candles:
                r = candles[-1].get("rsi14")
                if r is not None:
                    rsi14 = round(float(r), 1)

            earn = earnings_lookup.get(sym, {})
            symbol_data[sym] = {
                "price":                    round(price, 2),
                "change_pct_prev_day":      chg_prev_day,
                "high_52w_price":           high_52w,
                "price_date":               s2.get("price_date"),
                "stage2_score":             s2.get("score", 0),
                "rs_score":                 round(s2.get("rs_score", 50.0), 1),
                "market_structure":         s2.get("market_structure", "NEUTRAL"),
                "monthly_phase":            s2.get("monthly_phase", "UNKNOWN"),
                "ema200_slope":             round(s2.get("ema200_slope", 0), 4),
                "pct_from_52w_high":        pct_high,
                "pullback_pct":             round(s2.get("pullback_pct", 0), 1),
                "pct_vs_entry":             round((price - entry) / entry * 100, 1) if entry else None,
                "entry":                    round(entry, 2),
                # 가격 앵커 (hallucination 방지)
                "rsi14":                    rsi14,
                "ema200":                   round(s2.get("latest_ema200", 0), 2),
                "ema50":                    round(s2.get("latest_ema50", 0), 2),
                "ema21":                    round(s2.get("latest_ema21", 0), 2),
                "atr14":                    round(s2.get("latest_atr", 0), 2),
                # Stage2 체크
                "price_above_emas":         checks.get("price_above_emas", False),
                "ema200_rising":            checks.get("ema200_rising", False),
                "volume_contracting":       checks.get("volume_contracting", False),
                "near_52w_high":            checks.get("near_52w_high", False),
                # 가우시안 채널
                "gc_above":                 s2.get("gc_above", False),
                "gc_breakout":              s2.get("gc_breakout", False),
                "gc_retest":                s2.get("gc_retest", False),
                # 조정/하락 패턴
                "bear_flag":                s2.get("bear_flag", False),
                "rsi_divergence_bullish":   s2.get("rsi_divergence_bullish", False),
                "rsi_divergence_bearish":   s2.get("rsi_divergence_bearish", False),
                # 실적
                "earnings_date":            earn.get("earnings_date"),
                "days_until_earnings":      earn.get("days_until"),
                "eps_estimate":             earn.get("eps_estimate"),
                "already_reported_possible": earn.get("already_reported_possible", False),
            }

        # 프리마켓 / 포스트마켓 (아침·저녁 슬롯 모두 수집)
        prepost = _api_get("/prepost", {"symbol": sym})
        if prepost:
            prepost_data[sym] = {
                "market_state":           prepost.get("market_state"),
                "pre_market_price":       prepost.get("pre_market_price"),
                "pre_market_change_pct":  prepost.get("pre_market_change_pct"),
                "post_market_price":      prepost.get("post_market_price"),
                "post_market_change_pct": prepost.get("post_market_change_pct"),
                "regular_close":          prepost.get("regular_close"),
            }

    return {
        "regime":        regime,
        "distribution_days": dd,
        "macro":         macro,
        "symbol_detail": symbol_data,
        "prepost":       prepost_data,
        "earnings":      earnings,
    }


def load_sentiment() -> dict:
    return _load_json("sentiment/latest.json")


# ─────────────────────────────────────────────────────────────────────────────
# 프롬프트 포매터
# ─────────────────────────────────────────────────────────────────────────────

def _format_authoritative_table(tech: dict) -> str:
    """Grok 참조용 종목 수치 바인딩 테이블.

    컬럼:
    - 전일종가: 마지막 미국 장 종가 (D-1)
    - 전일등락: D-2→D-1 변화율
    - 프리/포스트: 현재 프리마켓 또는 어제 포스트마켓 가격 (없으면 N/A)
    - 52주고점, 고점%: 역산 절대가 및 현재 대비 거리
    - 실적일, EPS: 예정 실적 (추정치)
    """
    detail = tech["symbol_detail"]
    prepost = tech.get("prepost", {})

    today_kst = (datetime.now(timezone.utc) + dt.timedelta(hours=9)).date()
    prev_trading_day = today_kst - dt.timedelta(days=1)
    if today_kst.weekday() == 0:
        prev_trading_day = today_kst - dt.timedelta(days=3)

    stale_syms: list[str] = []
    hdr = f"{'심볼':<6} {'전일종가':>10} {'전일등락':>8} {'프리/포스트':>14} {'52주고점':>11} {'고점%':>7}  {'실적일':<12} {'EPS':>9} 상태"
    sep = "-" * 110
    rows = [hdr, sep]

    for sym, _ in WATCHLIST:
        d = detail.get(sym)
        if not d:
            rows.append(f"{sym:<6} {'데이터없음':>10}")
            continue

        price_s  = f"${d['price']:,.2f}"
        chg_s    = f"{d.get('change_pct_prev_day', 0):+.2f}%"
        high_s   = f"${d['high_52w_price']:,.2f}" if d.get("high_52w_price") else "N/A"
        highp_s  = f"{d['pct_from_52w_high']:.1f}%"
        earn_s   = d.get("earnings_date") or "N/A"
        eps_s    = f"${d['eps_estimate']}" if d.get("eps_estimate") is not None else "N/A"

        pp = prepost.get(sym, {})
        pre_p  = pp.get("pre_market_price")
        pre_c  = pp.get("pre_market_change_pct")
        post_p = pp.get("post_market_price")
        post_c = pp.get("post_market_change_pct")
        if pre_p and pre_c is not None:
            pp_s = f"PRE:${pre_p:,.2f}({pre_c:+.2f}%)"
        elif post_p and post_c is not None:
            pp_s = f"POST:${post_p:,.2f}({post_c:+.2f}%)"
        else:
            pp_s = "N/A"

        flags = []
        if d.get("already_reported_possible"):
            flags.append("⚠이미발표됨")
        price_date_str = d.get("price_date")
        if price_date_str:
            try:
                price_date = dt.date.fromisoformat(price_date_str)
                if price_date < prev_trading_day:
                    flags.append(f"⚠가격구({price_date_str})")
                    stale_syms.append(sym)
            except ValueError:
                pass

        rows.append(
            f"{sym:<6} {price_s:>10} {chg_s:>8} {pp_s:>14} {high_s:>11} {highp_s:>7}  "
            f"{earn_s:<12} {eps_s:>9} {' '.join(flags)}"
        )

    rows.append(sep)
    rows.append("⚠ BINDING RULES (위반 시 brief 무효):")
    rows.append("  [1] 가격·등락률·실적일은 이 테이블 값만 사용. 추측·근사·학습 데이터 금지.")
    rows.append("  [2] '전일종가'는 마지막 미국 거래일 종가. '전일등락'은 그 전날 대비 (D-2→D-1).")
    rows.append("  [3] 프리/포스트 값이 있으면 오늘/어제 방향 언급 시 이 값만 사용. N/A면 방향 언급 금지.")
    rows.append("  [4] N/A이면 추측 금지. 실적일 N/A이거나 14일 초과 → brief_en/ko에서 실적 언급 금지(완전 생략). 절대 '곧'/'다음 주' 금지.")
    rows.append("  [5] ⚠이미발표됨: 이미 발표됨. 'beat/miss/상회/하회/split/분할' 절대 금지.")
    rows.append("  [6] 지지/저항 레벨: 전일종가 ±25% 내, EMA21/50/200 기반만 허용.")
    if stale_syms:
        rows.append(f"  ⚠가격구 표시 종목: {', '.join(stale_syms)} — '데이터 기준 $X' 형태로 유보 표현 사용.")
    return "\n".join(rows)


def _format_macro_binding(macro_data: dict) -> str:
    """VIX/TNX/DXY/BTC 바인딩 테이블 — market_brief 수치 근거."""
    items = {item['symbol']: item for item in macro_data.get('macro', [])}

    def val(sym, field):
        v = items.get(sym, {}).get(field)
        return f"{v:.2f}" if isinstance(v, (int, float)) else "N/A"

    def chg(sym, field):
        v = items.get(sym, {}).get(field)
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "N/A"

    vix   = val('^VIX', 'price')
    tnx   = val('^TNX', 'price')
    dxy   = val('DX-Y.NYB', 'price')
    spy_p = val('SPY', 'price');  spy_1d = chg('SPY', 'change_pct_1d')
    qqq_p = val('QQQ', 'price');  qqq_1d = chg('QQQ', 'change_pct_1d')
    btc_p = val('BTC-USD', 'price')
    btc_1d = chg('BTC-USD', 'change_pct_1d')
    btc_5d = chg('BTC-USD', 'change_pct_5d')

    # BTC 앵커 문장 (Grok이 수치를 재작성하지 못하도록 사전 생성)
    btc_raw_1d = items.get('BTC-USD', {}).get('change_pct_1d')
    btc_raw_5d = items.get('BTC-USD', {}).get('change_pct_5d')
    btc_raw_p  = items.get('BTC-USD', {}).get('price')
    if btc_raw_p and btc_raw_1d is not None and btc_raw_5d is not None:
        _dir = "down" if float(btc_raw_1d) < 0 else "up"
        btc_anchor = (
            f"BTC anchor (use VERBATIM in market_brief if referencing BTC): "
            f"Bitcoin is at ${float(btc_raw_p):,.2f}, {_dir} {abs(float(btc_raw_1d)):.2f}% "
            f"today and {abs(float(btc_raw_5d)):.2f}% over five days."
        )
    else:
        btc_anchor = "BTC anchor: unavailable."

    lines = [
        "━━━ MACRO BINDING TABLE — market_brief 수치는 이 값만 사용 ━━━",
        f"VIX={vix}  |  10Y={tnx}%  |  DXY={dxy}",
        f"SPY=${spy_p}({spy_1d})  |  QQQ=${qqq_p}({qqq_1d})",
        f"BTC=${btc_p}  1D={btc_1d}  5D={btc_5d}",
        btc_anchor,
        "⚠ BINDING: VIX/TNX/DXY/BTC 수치는 위 표 기준. 학습 데이터·웹 검색값 금지.",
    ]
    return "\n".join(lines)


def _format_symbol_block(tech: dict, sentiment_by_sym: dict) -> str:
    """TIER1 종목 상세 데이터 블록 (Grok 분석 근거)."""
    detail = tech["symbol_detail"]
    prepost = tech.get("prepost", {})
    lines = []

    for sym, company in WATCHLIST:
        d = detail.get(sym)
        if not d:
            lines.append(f"{sym} ({company}): 데이터 없음")
            continue

        sent = sentiment_by_sym.get(sym, {})

        # 기술 신호 요약
        signals = []
        if d["price_above_emas"]:
            signals.append("모든 이평선 위")
        else:
            signals.append("이평선 아래")
        if d["ema200_rising"]:
            signals.append("EMA200 상승중")
        if d["gc_breakout"]:
            signals.append("GC 돌파")
        elif d["gc_above"]:
            signals.append("GC 위")
        if d["gc_retest"]:
            signals.append("GC 재테스트")
        if d["volume_contracting"]:
            signals.append("거래량 수축(에너지 축적)")
        if d["near_52w_high"]:
            signals.append("52주고점 인근")
        if d["bear_flag"]:
            signals.append("⚠베어플래그")
        if d["rsi_divergence_bearish"]:
            signals.append("⚠RSI하락다이버전스")
        if d["rsi_divergence_bullish"]:
            signals.append("✓RSI상승다이버전스")

        # 프리/포스트마켓
        pp = prepost.get(sym, {})
        pre_p = pp.get("pre_market_price")
        pre_c = pp.get("pre_market_change_pct")
        post_p = pp.get("post_market_price")
        post_c = pp.get("post_market_change_pct")
        if pre_p and pre_c is not None:
            pp_str = f"프리마켓=${pre_p:,.2f}({pre_c:+.2f}%)"
        elif post_p and post_c is not None:
            pp_str = f"포스트마켓=${post_p:,.2f}({post_c:+.2f}%)"
        else:
            pp_str = "프리/포스트=N/A"

        # 실적 정보 (14일 이내만 표시)
        earn_date = d.get("earnings_date")
        days_earn = d.get("days_until_earnings")
        eps_est   = d.get("eps_estimate")
        already   = d.get("already_reported_possible", False)
        if earn_date and already:
            earn_str = (
                f"【⚠이미발표됨({earn_date}) / EPS추정=${eps_est}】\n"
                f"  ⛔ HARD RULE: brief_en/ko에 'beat','miss','exceeded','상회','하회',"
                f"'split','분할' 절대 금지. 실제 결과는 이 데이터에 없음.\n"
                f"  ✅ 허용: '오늘 장 마감 후 발표됨 — EPS 추정 ${eps_est}, 실제 결과 확인 필요'"
            )
        elif earn_date and days_earn is not None and days_earn <= 14:
            earn_str = f"【실적={earn_date} ({days_earn}일후) / EPS추정=${eps_est}】"
        else:
            earn_str = ""

        # 가격 앵커
        rsi_str   = f"{d['rsi14']:.1f}" if d.get("rsi14") is not None else "N/A"
        ema21_str = f"${d['ema21']:,.2f}" if d.get("ema21") else "N/A"
        ema50_str = f"${d['ema50']:,.2f}" if d.get("ema50") else "N/A"
        ema200_str= f"${d['ema200']:,.2f}" if d.get("ema200") else "N/A"
        atr_str   = f"${d['atr14']:,.2f}" if d.get("atr14") else "N/A"
        vs_entry  = f"{d['pct_vs_entry']:+.1f}%" if d.get("pct_vs_entry") is not None else "N/A"

        earn_line = f"  {earn_str}\n" if earn_str else ""
        lines.append(
            f"{sym} ({company})\n"
            f"  Stage2={d['stage2_score']}/7  RS={d['rs_score']}  "
            f"구조={d['market_structure']}  월봉={d['monthly_phase']}\n"
            f"  [전일종가=${d['price']:,.2f}]  【전일등락={d.get('change_pct_prev_day',0):+.2f}%】  "
            f"52주고점=${d['high_52w_price']:,.2f}({d['pct_from_52w_high']:.1f}%)  "
            f"진입목표대비={vs_entry}  눌림={d['pullback_pct']:.1f}%\n"
            f"  [{pp_str}]\n"
            f"  가격앵커: RSI14={rsi_str}  EMA21={ema21_str}  EMA50={ema50_str}  EMA200={ema200_str}  ATR14={atr_str}\n"
            f"{earn_line}"
            f"  기술신호: {', '.join(signals)}\n"
            f"  소셜심리: {sent.get('sentiment','N/A')} (점수={sent.get('composite_score','N/A')})\n"
            f"  소셜근거: {sent.get('key_reason_en') or sent.get('key_reason','N/A')}"
        )

    return "\n\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 프롬프트 빌더
# ─────────────────────────────────────────────────────────────────────────────

def build_brief_prompt(tech: dict, sentiment: dict, slot: str) -> str:
    regime = tech.get("regime", {})
    dd     = tech.get("distribution_days", {})
    spy_dd = dd.get("spy", {})
    qqq_dd = dd.get("qqq", {})

    sentiment_by_sym: dict = {}
    for sym_obj in sentiment.get("symbols", []):
        s = sym_obj.get("symbol")
        if s:
            sentiment_by_sym[s] = sym_obj

    auth_table   = _format_authoritative_table(tech)
    macro_binding = _format_macro_binding(tech.get("macro", {}))
    symbol_block  = _format_symbol_block(tech, sentiment_by_sym)

    regime_label = regime.get("regime", "UNKNOWN")
    regime_score = regime.get("total", "N/A")
    comps = regime.get("components", {})
    market_sent  = sentiment.get("market", {})
    slot_kor     = "장 개장 전(Pre-open)" if slot == "pre_open" else "장 마감 후(Post-close)"

    return f"""You are a professional stock market analyst generating a trading brief for TIER1 watchlist stocks.
Slot: {slot_kor}

━━━ DATA TIMING ━━━
'전일종가' = last confirmed US close (D-1). '전일등락' = that day's change vs prior day (D-2→D-1).
'프리마켓'/'포스트마켓' = current extended-hours price. N/A = direction unknown, do NOT claim one.
Do NOT write "오늘 X% 상승/하락" using 전일등락 — that is YESTERDAY's move.

{macro_binding}

━━━ SNIPERBOARD AUTHORITATIVE DATA TABLE ━━━
All prices, % changes, and earnings dates MUST come from this table only.
Do NOT substitute, approximate, invent, or recall from training data.

{auth_table}

━━━ TIER1 SYMBOL DATA ━━━
{symbol_block}

━━━ MARKET CONTEXT ━━━
- Risk Regime: {regime_label} ({regime_score}/100)
  [CONSTRUCTIVE≥60=긍정 / MIXED≥40=혼조 / DEFENSIVE=방어적]
  Trend={comps.get('trend','?')} Breadth={comps.get('breadth','?')} Credit={comps.get('credit','?')} Volatility={comps.get('volatility','?')} Momentum={comps.get('momentum','?')}
- SPY 분배일: {spy_dd.get('count','?')}일 ({spy_dd.get('level','?')}) [≥6=위험 / 4-5=주의 / <4=정상]
- QQQ 분배일: {qqq_dd.get('count','?')}일 ({qqq_dd.get('level','?')})
- 시장 소셜심리: {market_sent.get('sentiment','N/A')} (점수={market_sent.get('composite_score','N/A')})

━━━ SETUP_QUALITY RULES ━━━
Apply in this order (first match wins):
  A+: Stage2 6-7 AND RS≥70 AND (gc_breakout OR gc_above) AND mood≥optimistic — NOT DOWNTREND
  A : Stage2 5-6 AND RS≥60 AND UPTREND AND mood≥neutral
  B : Stage2 4-5 OR mixed signals (RS 40-70, UPTREND but no GC, or DISTRIBUTION+decent Stage2)
  C : Stage2 2-3 OR bear_flag OR fearful mood OR DOWNTREND with Stage2≥3
  D : Stage2 0-2 OR DOWNTREND+Stage2≤3 OR rsi_divergence_bearish+low RS
  ⚠ DISTRIBUTION ≠ DOWNTREND: DISTRIBUTION+Stage2≥5 → B or A, NOT C/D

━━━ ACTION_BIAS RULES ━━━
Priority order:
  avoid : DOWNTREND AND Stage2≤5  OR  Stage2≤1  OR  (⚠이미발표됨 AND post-market drop>10%)
          EXCEPTION: Stage2=7 AND RS≥70 with DOWNTREND → 'watch'
  buy   : Stage2≥6 AND RS≥70 AND UPTREND AND (mood=optimistic OR euphoric)  AND setup_quality≥A
  hold  : Stage2≥5 AND solid technical, no new entry but no exit signal
  watch : all other cases — interesting but entry not optimal

  RS adjustment: RS<30 → downgrade action one level. Never watch→avoid by RS alone.
  ⚠이미발표됨 + post-market drop >5% → max action='watch'. Drop >10% → 'avoid'.

━━━ ANTI-HALLUCINATION ━━━
1. PRICE: Every $ amount in brief_en/ko must come from 전일종가/프리마켓 columns in the table.
   Support/resistance: within ±25% of 전일종가. Use EMA21/50/200 from 가격앵커 for levels.
2. DIRECTION: 전일등락 = YESTERDAY's change. If 프리마켓=N/A, do not claim today's direction.
3. EARNINGS — ⚠이미발표됨 STOCKS:
   BANNED IN brief_en/ko: beat, miss, exceeded, disappointed, 상회, 하회, 어닝서프라이즈,
   split, 분할, 4-for-1, buyback, strong beat, earnings beat, EPS beat/miss.
   REASON: We only have estimated EPS + post-market price. Actual results are UNKNOWN.
   Price reaction does NOT reveal beat/miss — stocks fall on beats, rise on misses.
   ALLOWED: "[SYM] reported after close today (est. EPS $X — verify actual at broker)"
4. EARNINGS DATES: Use ONLY table dates. Never "next week"/"soon" without exact date.
5. SUPPORT/RESISTANCE: must be within ±25% of 전일종가. Cite EMA source.

SELF-CHECK before JSON output:
  □ All $ prices match 전일종가 column?
  □ ⚠이미발표됨 stocks: no beat/miss/split/분할 in brief_en/ko?
  □ DOWNTREND stocks: action_bias ≠ 'buy' (unless Stage2=7 AND RS≥70)?
  □ Stage2≤1 stocks: action_bias = 'avoid'?
  □ EMA levels in brief: match 가격앵커 values?
  □ Earnings: mentioned ONLY if ≤14 days away? If absent or >14 days, completely omitted from brief_en/ko?

Generate ONE JSON object (raw JSON only, no markdown):
{{
  "market_brief": {{
    "summary_en": "One-sentence overall market assessment referencing regime + key theme",
    "summary_ko": "시장 전체 한 문장 (30자 이내, 레짐·주요테마 포함)",
    "tone": "bullish|cautious|bearish|neutral",
    "key_themes_en": ["theme1 (cite specific data)", "theme2"],
    "key_themes_ko": ["테마1 (구체적 데이터 근거)", "테마2"],
    "watch_points_en": "Most important specific thing to watch — cite exact price or date from table",
    "watch_points_ko": "오늘 가장 중요한 주시 포인트 — 테이블의 구체적 수치 인용"
  }},
  "symbol_briefs": [
    {{
      "symbol": "TICKER",
      "setup_quality": "A+|A|B|C|D",
      "brief_en": "2-3 sentences: (1) exact price from table + key technical signal, (2) setup strength or vulnerability, (3) social catalyst or risk. Mention earnings ONLY if ≤14 days away. NO invented prices.",
      "brief_ko": "2-3문장: (1) 테이블 정확한 가격 + 핵심 기술 신호, (2) 셋업 강도 또는 취약점, (3) 소셜 촉매 또는 리스크. 실적은 14일 이내일 때만 언급, 그 외 완전 생략.",
      "key_risk_en": "Specific risk — cite EMA level or % from table if applicable",
      "key_risk_ko": "핵심 리스크 — 가능하면 테이블의 EMA 수치·% 인용",
      "key_opportunity_en": "Specific opportunity — cite exact entry or breakout level from data",
      "key_opportunity_ko": "핵심 기회 — 테이블의 진입가·돌파 수준 인용",
      "action_bias": "buy|hold|watch|avoid"
    }}
  ]
}}

symbol_briefs: TIER1 전 종목(11개), 순서: TSM, NVDA, META, TSLA, PLTR, MU, CRWD, AMZN, MSFT, AAPL, GOOGL
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


VALID_TONES        = {"bullish", "cautious", "bearish", "neutral"}
VALID_SETUP_QUALITY = {"A+", "A", "B", "C", "D"}
VALID_ACTION_BIAS  = {"buy", "hold", "watch", "avoid"}


def validate_brief(data: dict) -> bool:
    mb = data.get("market_brief")
    if not isinstance(mb, dict):
        print("[WARN] market_brief 누락", file=sys.stderr)
        return False
    if mb.get("tone") not in VALID_TONES:
        print(f"[WARN] tone={mb.get('tone')!r}", file=sys.stderr)
        return False
    for f in ("summary_en", "summary_ko", "watch_points_en", "watch_points_ko"):
        if not isinstance(mb.get(f), str) or not mb[f]:
            print(f"[WARN] market_brief.{f} 누락", file=sys.stderr)
            return False
    for f in ("key_themes_en", "key_themes_ko"):
        if not isinstance(mb.get(f), list) or len(mb[f]) == 0:
            print(f"[WARN] {f} 누락 또는 빈 배열", file=sys.stderr)
            return False

    sbs = data.get("symbol_briefs")
    if not isinstance(sbs, list) or len(sbs) == 0:
        print("[WARN] symbol_briefs 누락", file=sys.stderr)
        return False
    for sb in sbs:
        if sb.get("setup_quality") not in VALID_SETUP_QUALITY:
            print(f"[WARN] setup_quality={sb.get('setup_quality')!r}", file=sys.stderr)
            return False
        if sb.get("action_bias") not in VALID_ACTION_BIAS:
            print(f"[WARN] action_bias={sb.get('action_bias')!r}", file=sys.stderr)
            return False
        for f in ("brief_en", "brief_ko", "key_risk_en", "key_risk_ko",
                  "key_opportunity_en", "key_opportunity_ko"):
            if not isinstance(sb.get(f), str) or not sb[f]:
                print(f"[WARN] symbol_brief.{f} 누락", file=sys.stderr)
                return False
    return True


# ── Domain keyword sets (for cross-domain causal detection) ──────────────────
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "crypto":   ["btc", "비트코인", "bitcoin", "crypto", "암호화폐"],
    "policy":   ["관세", "tariff", "허가제", "제재", "sanctions", "칩 규제", "chip ban", "chip export"],
    "rates":    ["금리", "10y", "tnx", "treasury", "yield", "연준", "fed"],
    "equities": ["tsla", "aapl", "nvda", "msft", "amzn", "goog", "meta", "spy", "qqq", "주식"],
}
_CAUSAL_KO = ["는데", "지만", "하지만", "인데", "임에도", "불구하고"]
_CAUSAL_EN = [" while ", " but ", " however ", " although ", " yet ", " despite "]
_JP_RE = re.compile(r"[぀-ゟ゠-ヿ]")


def _domains_in_text(text: str) -> set[str]:
    lower = text.lower()
    return {domain for domain, kws in _DOMAIN_KEYWORDS.items()
            if any(kw in lower for kw in kws)}


def _has_causal_connector(text: str) -> bool:
    lower = text.lower()
    return any(c in lower for c in _CAUSAL_KO + _CAUSAL_EN)


def _collect_ko_fields(data: dict) -> list[tuple[str, str]]:
    """Return (field_path, text) for every _ko field in the brief."""
    results: list[tuple[str, str]] = []
    mb = data.get("market_brief", {})
    for key in ("summary_ko", "watch_points_ko"):
        val = mb.get(key, "")
        if val:
            results.append((f"market_brief.{key}", val))
    for theme in mb.get("key_themes_ko", []):
        if theme:
            results.append(("market_brief.key_themes_ko[]", theme))
    for sb in data.get("symbol_briefs", []):
        sym = sb.get("symbol", "?")
        for key in ("brief_ko", "key_risk_ko", "key_opportunity_ko"):
            val = sb.get(key, "")
            if val:
                results.append((f"symbol_briefs.{sym}.{key}", val))
    return results


def _collect_all_fields(data: dict) -> list[tuple[str, str]]:
    """Return (field_path, text) for ALL text fields (en + ko)."""
    results: list[tuple[str, str]] = []
    mb = data.get("market_brief", {})
    for key in ("summary_en", "summary_ko", "watch_points_en", "watch_points_ko"):
        val = mb.get(key, "")
        if val:
            results.append((f"market_brief.{key}", val))
    for lang in ("en", "ko"):
        for theme in mb.get(f"key_themes_{lang}", []):
            if theme:
                results.append((f"market_brief.key_themes_{lang}[]", theme))
    for sb in data.get("symbol_briefs", []):
        sym = sb.get("symbol", "?")
        for key in ("brief_en", "brief_ko", "key_risk_en", "key_risk_ko",
                    "key_opportunity_en", "key_opportunity_ko"):
            val = sb.get(key, "")
            if val:
                results.append((f"symbol_briefs.{sym}.{key}", val))
    return results


def validate_output_quality(data: dict) -> list[str]:
    """Detect causal cross-domain language and Japanese chars in _ko fields.

    Returns a list of human-readable violation strings (empty = clean).
    """
    violations: list[str] = []

    # Check A: cross-domain causal connectives (all text fields)
    for field_path, text in _collect_all_fields(data):
        if not _has_causal_connector(text):
            continue
        domains = _domains_in_text(text)
        if len(domains) >= 2:
            snippet = text[:80].replace("\n", " ")
            violations.append(
                f"[인과/causal] {field_path}: 무관한 도메인({', '.join(domains)}) "
                f"연결 감지 — '{snippet}'"
            )

    # Check B: Japanese hiragana/katakana in _ko fields only
    for field_path, text in _collect_ko_fields(data):
        match = _JP_RE.search(text)
        if match:
            snippet = text[:80].replace("\n", " ")
            violations.append(
                f"[일본어/japanese] {field_path}: 히라가나/카타카나 감지 "
                f"('{match.group()}') — '{snippet}'"
            )

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Context Attribution 스냅샷
# ─────────────────────────────────────────────────────────────────────────────

def build_brief_context_snapshot(tech: dict, sentiment: dict, captured_at: str) -> dict:
    """Brief 생성 시점의 기술/레짐/심리 맥락 스냅샷 (Phase 1 Context Attribution)."""
    regime   = tech.get("regime", {}) or {}
    dd       = tech.get("distribution_days", {}) or {}
    sym_detail = tech.get("symbol_detail", {}) or {}

    stage2_scores = [s["stage2_score"] for _, _ in WATCHLIST if "stage2_score" in sym_detail.get(_, {})]
    rs_scores     = [s["rs_score"]     for _, _ in WATCHLIST if "rs_score"     in sym_detail.get(_, {})]
    # 수정: 위 comprehension이 잘못됨, 직접 순회
    stage2_scores, rs_scores = [], []
    for sym, _ in WATCHLIST:
        s = sym_detail.get(sym, {})
        if "stage2_score" in s:
            stage2_scores.append(s["stage2_score"])
        if "rs_score" in s:
            rs_scores.append(s["rs_score"])

    avg_stage2 = round(sum(stage2_scores) / len(stage2_scores), 1) if stage2_scores else None
    avg_rs     = round(sum(rs_scores) / len(rs_scores), 1) if rs_scores else None
    spy_dd     = dd.get("spy", {}) or {}

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
        "source":      "sniperboard",
        "regime":      {"total": regime.get("total"), "label": regime.get("regime")},
        "technical_summary": {
            "avg_stage2":        avg_stage2,
            "avg_rs_score":      avg_rs,
            "distribution_day_spy": spy_dd.get("count"),
        },
        "market_sentiment": {
            "composite_score": market_sent.get("composite_score"),
            "label":           market_sent.get("sentiment"),
        },
        "key_factors": key_factors or ["데이터 기반 요약"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────

def main():
    now     = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    slot    = detect_slot(now)
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

    context_snapshot = build_brief_context_snapshot(tech, sentiment, now_iso)

    snapshot = {
        "generated_at":   now_iso,
        "schema_version": "2.0",
        "slot":           slot,
        "market_brief":   parsed["market_brief"],
        "symbol_briefs":  parsed["symbol_briefs"],
        "context":        context_snapshot,
    }

    latest_path  = REPO_PATH / "brief" / "latest.json"
    history_dir  = REPO_PATH / "brief" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / f"{date_str}_{slot}.json"

    for path in (latest_path, history_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 저장 완료: {latest_path}, {history_path}")

    rel_history    = str(history_path.relative_to(REPO_PATH))
    commit_message = f"brief: {date_str} {time_str} update (with context)"
    push_ok = commit_and_push(
        repo=REPO_PATH,
        commit_message=commit_message,
        files_to_add=["brief/latest.json", rel_history],
        push=True,
    )
    if not push_ok:
        print("[FATAL] GitHub push 실패 — 최신 context가 sniperboard에 반영되지 않았습니다.")
        sys.exit(1)

    print("[OK] Brief 수집 + GitHub push 완료")


if __name__ == "__main__":
    main()
