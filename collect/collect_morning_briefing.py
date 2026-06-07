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


def _build_earnings_lookup(earnings_data: dict, now_kst_date=None) -> dict:
    """종목별 실적 발표일·EPS 예상치 조회 dict. upcoming_earnings 기준.

    days_until은 파일 저장 시점이 아닌 브리핑 실행 시점 KST 날짜로 재계산한다.
    earnings_date == today KST 이면 미국 장 마감 후 이미 발표됐을 가능성이 높으므로
    already_reported_possible=True 플래그를 세운다.
    (US after-hours ~9PM ET = KST 익일 06:00, 브리핑은 06:45 KST 실행)
    """
    import datetime as _dt
    if now_kst_date is None:
        now_kst_date = (datetime.now(timezone.utc) + _dt.timedelta(hours=9)).date()

    lookup: dict = {}
    for e in earnings_data.get("upcoming_earnings", []):
        sym = e.get("symbol")
        if sym and sym not in lookup:
            earn_date_str = e.get("earnings_date") or e.get("report_date")
            try:
                earn_date = _dt.date.fromisoformat(earn_date_str) if earn_date_str else None
            except ValueError:
                earn_date = None
            days_until = (earn_date - now_kst_date).days if earn_date else None
            lookup[sym] = {
                "earnings_date":           earn_date_str,
                "days_until":              days_until,
                "eps_estimate":            e.get("eps_estimate"),
                "already_reported_possible": (days_until is not None and days_until <= 0),
            }
    return lookup


def fetch_all_data() -> dict:
    """SniperBoard API + 저장된 JSON 파일에서 전체 시장 데이터 수집."""
    print("[INFO] 시장 데이터 수집 중...")

    regime = _api_get("/regime") or {}
    dd = _api_get("/distribution-days") or {}
    macro = _api_get("/macro") or {}
    macro_insight = _api_get("/macro/insight") or {}
    watchlist = _api_get("/watchlist") or {}

    sentiment = _load_json("sentiment/latest.json")
    earnings = _load_json("earnings/latest.json")
    earnings_lookup = _build_earnings_lookup(earnings)  # KST 날짜 자동 적용

    # 21종목 전체 일봉 상세 (스퀴즈/조정 분석용) + 프리마켓 데이터
    symbol_detail: dict = {}
    prepost_data: dict = {}
    for sym, _, _ in ALL_SYMBOLS:
        daily = _api_get("/daily", {"symbol": sym})
        if daily and daily.get("stage2"):
            s2 = daily["stage2"]
            checks = s2.get("checks", {})
            price = s2.get("latest_close", 0)
            entry = s2.get("entry", 0)
            pct_high = round(s2.get("pct_from_52w_high", 0), 1)
            # 52주 고점 절대가 계산 (음수 pct_high 대응 수정)
            # pct_from_52w_high = (latest_close - high52) / high52 * 100
            # → high52 = latest_close / (1 + pct_from_52w_high/100)
            # 예: pct_high=-9.21 → high52 = 214.75/(1-0.0921) ≈ 236.5
            try:
                denominator = 1 + pct_high / 100
                high_52w = round(price / denominator, 2) if 0 < denominator < 10 else round(price, 2)
            except ZeroDivisionError:
                high_52w = round(price, 2)

            # 전일 등락률: candles 마지막 2봉 (D-2 종가 → D-1 종가 변화)
            # 아침 브리핑 시점 기준으로 이것이 "전 거래일" 변동률임
            candles = daily.get("candles", [])
            if len(candles) >= 2:
                prev_close = candles[-2].get("close", 0)
                curr_close = candles[-1].get("close", price)
                chg_prev_day = round((curr_close - prev_close) / prev_close * 100, 2) if prev_close else 0.0
            else:
                chg_prev_day = 0.0

            # RSI14: 마지막 캔들에서 추출 (지표가 직렬화된 경우)
            rsi14 = None
            if candles:
                rsi14 = candles[-1].get("rsi14")
                if rsi14 is not None:
                    rsi14 = round(float(rsi14), 1)

            # EMA 수치: price-level 앵커용 (가격 수준 검증에 사용)
            ema200 = round(s2.get("latest_ema200", 0), 2)
            ema50  = round(s2.get("latest_ema50", 0), 2)
            ema21  = round(s2.get("latest_ema21", 0), 2)
            atr14  = round(s2.get("latest_atr", 0), 2)

            earn = earnings_lookup.get(sym, {})
            symbol_detail[sym] = {
                "price":                  round(price, 2),
                "change_pct_prev_day":    chg_prev_day,   # 전 거래일 등락 (D-2→D-1)
                "high_52w_price":         high_52w,
                "price_date":             s2.get("price_date"),  # 마지막 봉 날짜
                "earnings_date":          earn.get("earnings_date"),
                "days_until_earnings":    earn.get("days_until"),
                "eps_estimate":           earn.get("eps_estimate"),
                "already_reported_possible": earn.get("already_reported_possible", False),
                "stage2_score":           s2.get("score", 0),
                "rs_score":               round(s2.get("rs_score", 50), 1),
                "market_structure":       s2.get("market_structure", "NEUTRAL"),
                "monthly_phase":          s2.get("monthly_phase", "UNKNOWN"),
                "ema200_slope":           round(s2.get("ema200_slope", 0), 4),
                "pct_from_52w_high":      pct_high,
                "pullback_pct":           round(s2.get("pullback_pct", 0), 1),
                "pct_vs_entry":           round((price - entry) / entry * 100, 1) if entry else None,
                "entry":                  round(entry, 2),
                # 가격 앵커 지표 (hallucination 방지용)
                "rsi14":                  rsi14,
                "ema200":                 ema200,
                "ema50":                  ema50,
                "ema21":                  ema21,
                "atr14":                  atr14,
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

        # 프리마켓 데이터 수집 (아침 브리핑 핵심: 전날 장 마감 후 ~ 개장 전 움직임)
        prepost = _api_get("/prepost", {"symbol": sym})
        if prepost:
            prepost_data[sym] = {
                "market_state":          prepost.get("market_state"),
                "pre_market_price":      prepost.get("pre_market_price"),
                "pre_market_change_pct": prepost.get("pre_market_change_pct"),
                "post_market_price":     prepost.get("post_market_price"),
                "post_market_change_pct": prepost.get("post_market_change_pct"),
                "regular_close":         prepost.get("regular_close"),
            }

    return {
        "regime":        regime,
        "distribution":  dd,
        "macro":         macro,
        "macro_insight": macro_insight,
        "watchlist":     watchlist.get("watchlist", []),
        "symbol_detail": symbol_detail,
        "prepost":       prepost_data,
        "sentiment":     sentiment,
        "earnings":      earnings,
    }


def _format_authoritative_table(data: dict) -> str:
    """
    Grok 참조용 수치 바인딩 테이블.
    Grok이 분석 텍스트에 쓰는 모든 가격·등락률·실적일은 반드시 이 테이블에서 가져와야 한다.

    컬럼 설명:
    - 전일종가: 마지막 미국 장 종가 (D-1 종가, yfinance 일봉 기준)
    - 전일등락: D-2 → D-1 종가 변화율 (전 거래일 등락)
    - 프리마켓: 현재 프리마켓 가격 및 등락 (미국 장 개장 전 호가, 없으면 N/A)
    - 52주고점, 고점%: 52주 최고가 및 현재 대비 거리
    - 실적일, EPS: yfinance/earnings 데이터 기준 (추정치)
    """
    import datetime as _dt
    detail = data["symbol_detail"]
    prepost = data.get("prepost", {})
    # 브리핑 실행 시점의 전 거래일(KST 기준 어제) 계산
    today_kst = (datetime.now(timezone.utc) + _dt.timedelta(hours=9)).date()
    prev_trading_day = today_kst - _dt.timedelta(days=1)
    # 주말 감안: 월요일이면 전 거래일은 금요일(3일 전)
    if today_kst.weekday() == 0:  # 월요일
        prev_trading_day = today_kst - _dt.timedelta(days=3)

    stale_syms: list[str] = []

    hdr = f"{'심볼':<6} {'전일종가':>10} {'전일등락':>8} {'프리마켓':>12} {'52주고점':>11} {'고점%':>7}  {'실적발표일':<12} {'EPS예상':>9} {'상태'}"
    sep = "-" * 105
    rows = [hdr, sep]
    for sym, _, _ in ALL_SYMBOLS:
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

        # 프리마켓 가격 (아침 브리핑의 핵심 — 당일 장 방향성)
        pp = prepost.get(sym, {})
        pre_price = pp.get("pre_market_price")
        pre_chg   = pp.get("pre_market_change_pct")
        post_price = pp.get("post_market_price")
        post_chg   = pp.get("post_market_change_pct")
        market_state = pp.get("market_state", "")
        if pre_price and pre_chg is not None:
            pre_s = f"${pre_price:,.2f}({pre_chg:+.2f}%)"
        elif post_price and post_chg is not None:
            pre_s = f"POST:${post_price:,.2f}({post_chg:+.2f}%)"
        else:
            pre_s = "N/A"

        flags = []
        # 실적 발표 타이밍 플래그
        if d.get("already_reported_possible"):
            flags.append("⚠이미발표됨")
        # 가격 데이터 스테일니스 감지
        price_date_str = d.get("price_date")
        if price_date_str:
            try:
                price_date = _dt.date.fromisoformat(price_date_str)
                if price_date < prev_trading_day:
                    flags.append(f"⚠가격={price_date_str}(구)")
                    stale_syms.append(sym)
            except ValueError:
                pass

        flag_s = " ".join(flags)
        rows.append(f"{sym:<6} {price_s:>10} {chg_s:>8} {pre_s:>12} {high_s:>11} {highp_s:>7}  {earn_s:<12} {eps_s:>9} {flag_s}")
    rows.append(sep)
    rows.append("⚠ BINDING RULES (위반 시 브리핑 무효):")
    rows.append("  [1] 가격·등락률·실적일은 반드시 이 테이블 값만 사용. 추측·근사·학습 데이터 사용 금지.")
    rows.append("  [2] '전일종가'는 직전 미국 거래일 종가. '전일등락'은 그 전날 대비 등락 (D-2→D-1).")
    rows.append("  [3] '프리마켓' 값이 있으면 오늘 장 방향성 언급 시 이 값만 사용. N/A면 방향 언급 금지.")
    rows.append("  [4] 값이 N/A이면 해당 수치를 추측하지 말 것. 실적일 N/A이거나 14일 초과 → analysis에서 실적 언급 금지(완전 생략).")
    rows.append("  [5] ⚠이미발표됨: KST 오늘 날짜 실적 = 미국 장 마감 후 이미 발표됨. '오늘/내일 실적 예정' 금지.")
    rows.append("  [6] 지지/저항 가격은 전일종가 ±25% 범위 내에서만 언급. 범위 밖 수치 생성 금지.")
    if stale_syms:
        rows.append(f"  ⚠가격=(날짜)(구) 표시 종목: {', '.join(stale_syms)} — 이 종목들의 가격은 최신 종가보다 낮을 수 있음.")
        rows.append("    분석 시 '데이터 기준 $X (최신 종가 상이 가능)' 형태로 유보 표현을 쓸 것.")
    return "\n".join(rows)


def _format_symbol_block(data: dict) -> str:
    """21종목 데이터를 Grok 프롬프트용 텍스트로 변환."""
    detail = data["symbol_detail"]
    prepost = data.get("prepost", {})
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
        chg_prev = d.get("change_pct_prev_day", 0.0)
        chg_prev_str = f"{chg_prev:+.2f}%" if chg_prev != 0.0 else "0.00%(데이터없음)"
        earn_date = d.get("earnings_date")
        days_earn = d.get("days_until_earnings")
        eps_est = d.get("eps_estimate")
        already_reported = d.get("already_reported_possible", False)
        if earn_date and already_reported:
            earn_str = (
                f"【실적발표=⚠이미발표됨({earn_date}) / EPS예상=${eps_est}】\n"
                f"  ⛔ HARD RULE: analysis에 'beat','miss','상회','하회','exceeded','missed',"
                f"'split','분할','exceeded estimates' 절대 금지. 실제 결과는 데이터에 없음.\n"
                f"  ✅ 허용 표현: '오늘 장 마감 후 실적 발표됨 — EPS 추정 ${eps_est}, 실제 결과 확인 필요'"
            )
        elif earn_date and days_earn is not None and days_earn <= 14:
            earn_str = f"【실적발표={earn_date} ({days_earn}일후) / EPS예상=${eps_est}】"
        else:
            earn_str = ""
        sent_reason = sent.get('key_reason_en') or sent.get('key_reason', '')
        sent_ko = sent.get('key_reason_ko', '')

        # RSI/EMA 가격 앵커 (지지·저항 수준 검증용)
        rsi_str = f"{d['rsi14']:.1f}" if d.get("rsi14") is not None else "N/A"
        ema200_str = f"${d['ema200']:,.2f}" if d.get("ema200") else "N/A"
        ema50_str  = f"${d['ema50']:,.2f}" if d.get("ema50") else "N/A"
        ema21_str  = f"${d['ema21']:,.2f}" if d.get("ema21") else "N/A"
        atr14_str  = f"${d['atr14']:,.2f}" if d.get("atr14") else "N/A"

        # 프리마켓 / 포스트마켓 데이터 (아침 장 전 방향성)
        pp = prepost.get(sym, {})
        pre_price = pp.get("pre_market_price")
        pre_chg   = pp.get("pre_market_change_pct")
        post_price = pp.get("post_market_price")
        post_chg   = pp.get("post_market_change_pct")
        if pre_price and pre_chg is not None:
            prepost_str = f"프리마켓=${pre_price:,.2f}({pre_chg:+.2f}%) — 오늘 개장 전 방향"
        elif post_price and post_chg is not None:
            prepost_str = f"포스트마켓=${post_price:,.2f}({post_chg:+.2f}%) — 전날 장 마감 후"
        else:
            prepost_str = "프리/포스트마켓=N/A (사용 금지)"

        earn_line = f"  {earn_str}\n" if earn_str else ""
        lines.append(
            f"{sym} ({company}) [T{tier}]\n"
            f"  Stage2점수={d['stage2_score']}/7  시장상대강도RS={d['rs_score']}  "
            f"구조={d['market_structure']}  월봉추세={d['monthly_phase']}\n"
            f"  [전일종가(D-1)=${d['price']}]  【전일등락(D-2→D-1)={chg_prev_str}】  "
            f"52주고점=${d['high_52w_price']}(대비{d['pct_from_52w_high']}%)  "
            f"돌파목표대비={vs_entry}  최근눌림={d['pullback_pct']}%\n"
            f"  [{prepost_str}]\n"
            f"  가격앵커: RSI14={rsi_str}  EMA21={ema21_str}  EMA50={ema50_str}  EMA200={ema200_str}  ATR14={atr14_str}\n"
            f"{earn_line}"
            f"  기술신호: {', '.join(signals)}\n"
            f"  소셜심리: {sent.get('sentiment','N/A')} (점수={sent.get('composite_score','N/A')})\n"
            f"  투자자반응: {sent_reason}\n"
            f"  투자자반응(KO): {sent_ko}"
        )

    return "\n\n".join(lines)


def _format_macro_binding_header(macro_data: dict) -> str:
    """big_picture 섹션에서 사용할 핵심 매크로 수치 바인딩 헤더.
    이 값들은 big_picture의 vix_note / rates_note / dollar_note / btc_note에서
    반드시 그대로 사용해야 한다.
    """
    items = {item['symbol']: item for item in macro_data.get('macro', [])}
    def val(sym, field):
        v = items.get(sym, {}).get(field)
        return f"{v:.2f}" if isinstance(v, (int,float)) else str(v or 'N/A')
    def chg(sym, field):
        v = items.get(sym, {}).get(field)
        return f"{v:+.2f}%" if isinstance(v, (int,float)) else str(v or 'N/A')

    vix = val('^VIX', 'price')
    tnx = val('^TNX', 'price')
    dxy = val('DX-Y.NYB', 'price')
    btc_p  = val('BTC-USD', 'price')
    btc_1d = chg('BTC-USD', 'change_pct_1d')
    btc_5d = chg('BTC-USD', 'change_pct_5d')
    spy_p  = val('SPY', 'price')
    spy_1d = chg('SPY', 'change_pct_1d')
    qqq_p  = val('QQQ', 'price')
    qqq_1d = chg('QQQ', 'change_pct_1d')

    return (
        f"━━━ MACRO BINDING TABLE — big_picture 수치는 이 값만 사용 ━━━\n"
        f"VIX={vix}  |  10Y금리={tnx}%  |  DXY={dxy}  |  "
        f"BTC=${btc_p} (1D={btc_1d}, 5D={btc_5d})\n"
        f"SPY=${spy_p}({spy_1d})  |  QQQ=${qqq_p}({qqq_1d})\n"
        f"⚠ BINDING: VIX/TNX/DXY/BTC 수치는 위 표 기준. 학습 데이터·추측 금지.\n"
        f"   DXY={dxy} → dollar_note_en/ko에 이 수치를 반드시 인용할 것 (생략 또는 대체 금지).\n"
        f"   BTC 가격=${btc_p}, 1D%={btc_1d} — 브리핑 전 섹션에서 BTC 수치는 이 값만 사용. 학습 데이터 BTC 가격 사용 금지."
    )


def _format_macro_block(macro_data: dict) -> str:
    """매크로 주요 지표를 프롬프트용 요약 텍스트로 변환.

    BTC 대폭락 등 임계값 초과 시 ⚠ MANDATORY 경고를 주입한다.
    SPY/QQQ/RSP/IWM 등 주요 지수도 포함.
    """
    items = macro_data.get("macro", [])
    # 확장된 키 심볼 (지수·변동성·금리·원자재·섹터 모두 포함)
    key_syms = {
        "^VIX", "^TNX", "DX-Y.NYB", "CL=F", "GLD", "TLT", "HYG",
        "BTC-USD", "SPY", "QQQ", "RSP", "IWM", "SMH",
    }
    # 그룹별 정렬을 위한 순서
    sym_order = ["SPY", "QQQ", "RSP", "IWM", "^VIX", "^TNX", "TLT",
                 "DX-Y.NYB", "HYG", "GLD", "CL=F", "BTC-USD", "SMH"]
    items_by_sym = {item.get("symbol", ""): item for item in items}

    lines = []
    alerts = []

    for sym in sym_order:
        if sym not in key_syms:
            continue
        item = items_by_sym.get(sym)
        if not item:
            continue
        chg_1d = item.get("change_pct_1d") or 0
        chg_5d = item.get("change_pct_5d") or 0
        price  = item.get("price", "?")
        rsi14  = item.get("rsi14", "?")
        above_ema21 = item.get("above_ema21", None)
        ema21_flag = "EMA21위" if above_ema21 else ("EMA21아래" if above_ema21 is not None else "")
        line = (
            f"{sym}: ${price}  "
            f"1D={chg_1d:+.2f}%  "
            f"5D={chg_5d:+.2f}%  "
            f"RSI={rsi14}  "
            f"구조={item.get('market_structure','?')}  {ema21_flag}"
        )
        # BTC 대폭락 감지: 5D≤-10% 또는 1D≤-5%
        if sym == "BTC-USD":
            try:
                d1, d5 = float(chg_1d), float(chg_5d)
                if d5 <= -10 or d1 <= -5:
                    line += "  ⚠ BTC LARGE DROP DETECTED"
                    alerts.append(
                        f"⚠⚠⚠ BTC CRASH ALERT — MANDATORY in executive_bullets ⚠⚠⚠\n"
                        f"  BTC-USD: 1D={d1:.1f}%, 5D={d5:.1f}% — 임계값 초과 (기준: 1D≤-5% 또는 5D≤-10%)\n"
                        f"  이 정보는 반드시 executive_bullets_ko 중 한 항목에 포함되어야 함.\n"
                        f"  예시: '비트코인이 5일간 {d5:.1f}% 급락 — 위험자산 이탈 신호, 주식 변동성 선행 지표로 주시 요망'\n"
                        f"  BTC 급락 시 '증시 차분' '안정적' 등 낙관 표현은 executive_bullets에 단독으로 쓸 수 없음."
                    )
            except (TypeError, ValueError):
                pass
        lines.append(line)

    result = "\n".join(lines) if lines else "매크로 데이터 없음"
    if alerts:
        result = "\n\n".join(alerts) + "\n\n" + result
    return result


def _format_macro_insight_block(macro_insight: dict) -> str:
    """매크로 인사이트 시그널 그룹 (yfinance 계산 결과) 를 프롬프트용 텍스트로 변환.

    각 그룹의 green/yellow/red 신호와 방향을 제공해 Grok의 섹터 분석 근거로 활용.
    AI 텍스트(bilingual) 는 사용하지 않고 신호 판단만 사용.
    """
    groups = macro_insight.get("groups", {})
    overall = macro_insight.get("overall_judgment", "N/A")
    if not groups:
        return "매크로 인사이트: 데이터 없음"

    signal_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    group_names = {
        "volatility":   "변동성(VIX)",
        "breadth":      "브레드스(SPY/RSP)",
        "credit":       "크레딧(HYG/JNK)",
        "rates":        "금리(TLT/TNX)",
        "commodities":  "원자재(GLD/OIL/BTC)",
        "sectors":      "섹터(SMH/XLE/XLY/XHB/ITA)",
    }

    lines = [f"시장 신호 종합: {overall}"]
    for key, label in group_names.items():
        g = groups.get(key, {})
        signal = g.get("signal", "?")
        direction = g.get("direction", "?")
        emoji = signal_emoji.get(signal, "❓")
        lines.append(f"  {emoji} {label}: {signal.upper()} | {direction}")

    lines.append("(위 신호는 yfinance 실시간 계산값. sector_analysis 작성 시 이 신호 기반으로 작성할 것.)")
    return "\n".join(lines)


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
- Do NOT write "monitoring continues" or "situation ongoing" — state the direction and implication

CONFIDENCE → LANGUAGE MAPPING (mandatory — apply in big_picture.summary and executive_bullets):
  [confirmed]  → State as fact. No hedge needed. e.g. "The BIS tightened chip export rules..."
  [developing] → Use hedge: "Reports indicate...", "Early developments suggest...", "According to initial reports, ... — situation still evolving."
                 NEVER state a [developing] issue as established fact in summary or bullets.
  [unverified] → "Unconfirmed reports suggest..." or "Unverified: ..."
                 NEVER present in executive_bullets as a primary market-moving driver.
VIOLATION: Writing "[developing issue X] is driving markets" without hedge language = factual error.
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
        # 소셜미디어 소스 감지: Twitter/X/@handle/Reddit 등이 source_hint에 있으면 거부
        src = (iss.get("source_hint") or "").lower()
        _SOCIAL_PATTERNS = ("twitter", "x post", "x discussion", " @", "reddit", "telegram",
                            "discord", "4chan", "/@", "warhorizon", "me_observer_", "globalflash")
        social_hit = next((p for p in _SOCIAL_PATTERNS if p in src), None)
        if social_hit:
            print(f"[WARN] global_context: 소셜미디어 소스 감지 ({social_hit!r} in source_hint={src!r}) — 거부",
                  file=sys.stderr)
            return False
        # confirmed 신뢰도인데 소스가 없거나 날짜 없으면 경고 (soft — don't reject)
        if iss.get("confidence") == "confirmed" and not any(
            outlet in src for outlet in ("reuters", "bloomberg", "ap ", "bbc", "ft.", "wsj", "nyt",
                                         "white house", "bis", "sec", "fed", "doj", "ftc", "court")
        ):
            print(f"[WARN] global_context: confidence=confirmed이지만 알려진 기관 소스 없음 (source={src!r})",
                  file=sys.stderr)
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
✓ SOURCE REQUIREMENTS — READ BEFORE ASSIGNING CONFIDENCE:
  Accepted sources (may use "confirmed"): Reuters, Bloomberg, AP, BBC, Financial Times, WSJ, NYT,
  White House / official government press releases, official agency announcements (BIS, SEC, Fed,
  CFTC, DOJ, FTC), exchange announcements, verified court docket entries.
  Provisional sources (use "developing", never "confirmed"): Local news outlets, trade press,
  single-outlet reporting not yet corroborated.
  ✗ PROHIBITED sources — NEVER cite as ANY confidence level:
    Twitter / X posts (even from verified accounts or journalists)
    Reddit, Telegram, Discord, 4chan, anonymous blogs, personal opinion pieces, social media of ANY kind.
  If your only source is social media: either omit the item or source it from an accepted outlet.
  If you cannot find an accepted source → mark as "developing" with the last accepted-source date.
✓ Prefix genuinely unconfirmed facts with "unconfirmed:"
✓ CONFIDENCE ASSIGNMENT RULES (strict):
  "confirmed" = at least one accepted-source article with a specific date you can cite.
  "developing" = credible accepted source exists but situation is still evolving, partial info.
  "unverified" = only social media or rumor-level information — use with maximum caution.
  NEVER assign "confirmed" to information sourced only from social media or unverified accounts.

✗ FORBIDDEN PHRASES — these are analysis avoidance, not analysis:
  "impact unclear", "direction uncertain", "no new developments — impact unclear",
  "monitoring continues", "situation ongoing". Every issue must have a direction and ticker mapping.
✗ DO NOT list a stock as impacted without stating the direction (positive / negative / conditional).
✗ DO NOT use ongoing_no_update for any situation with active market risk (e.g. hot wars, open policy uncertainty).
  ongoing_no_update is ONLY for truly dormant background items with negligible near-term market impact.
✗ DO NOT fabricate figures, names, or dates you cannot verify.
✗ DO NOT include historical context as if it were a new development.
✗ DO NOT use specific military exercise or operation names (e.g., "Joint Sword-2026A", "Thunder-XXXX",
  "Operation X") unless you can cite a verifiable source with an exact date. Use general descriptions:
  "PLA conducted live-fire drills in the Taiwan Strait" is safe; inventing an exercise name is not.
✗ DO NOT mention corporate actions (stock splits, buybacks, M&A, IPO dates) for individual companies
  without a verifiable source. If uncertain, omit entirely rather than risk fabrication.
✗ CONFIDENCE SELF-CHECK: Before including any specific named event, ask yourself:
  "Can I cite a specific news outlet and date for this?" If no → mark as "unconfirmed:" or omit.

━━━ WATCHLIST TICKERS FOR IMPACT MAPPING ━━━
TSM NVDA META TSLA PLTR MU CRWD AMZN MSFT AAPL GOOGL
RKLB CEG VST ALAB OKLO APP ANET NVO QBTS SOFI

━━━ KNOWN AMBIGUOUS SITUATIONS — PICK ONE DIRECTION AND COMMIT ━━━
These topics have genuine two-sided debate. Do NOT flip between runs. Pick the dominant current view,
state the opposing risk, and commit. Do not write both sides as equal without resolving the direction.
· SpaceX IPO impact on RKLB: The DOMINANT current view is NEGATIVE for RKLB (liquidity absorption —
  large IPO historically draws capital away from similar-theme smaller names). The alternative view
  (halo effect / space theme lifting) is secondary. If you address SpaceX IPO, default to:
  "RKLB: negative near-term (liquidity competition from SpaceX IPO) / conditional positive if
  space sector re-rating follows post-IPO."
  DO NOT say SpaceX IPO is simply positive for RKLB without acknowledging the liquidity risk.

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
    regime_label = regime.get("regime", "UNKNOWN")
    regime_score = regime.get("total", "N/A")
    comps = regime.get("components", {})

    auth_table = _format_authoritative_table(data)
    symbol_block = _format_symbol_block(data)
    macro_binding = _format_macro_binding_header(data["macro"])
    macro_block = _format_macro_block(data["macro"])
    macro_insight_block = _format_macro_insight_block(data.get("macro_insight", {}))
    earnings_block = _format_earnings_block(data["earnings"])

    # BTC 앵커 문장: Grok이 수치를 임의로 변경하지 못하도록 사전 생성
    _macro_items = {item['symbol']: item for item in data["macro"].get('macro', [])}
    _btc = _macro_items.get('BTC-USD', {})
    _btc_price = _btc.get('price')
    _btc_1d    = _btc.get('change_pct_1d')
    _btc_5d    = _btc.get('change_pct_5d')
    if _btc_price and _btc_1d is not None and _btc_5d is not None:
        _btc_1d_abs = abs(float(_btc_1d))
        _btc_5d_abs = abs(float(_btc_5d))
        _btc_direction = "down" if float(_btc_1d) < 0 else "up"
        btc_anchor_en = (
            f"Bitcoin is at ${float(_btc_price):,.2f}, {_btc_direction} {_btc_1d_abs:.2f}% "
            f"today and {_btc_5d_abs:.2f}% over five days."
        )
        _btc_kor_dir = "하락" if float(_btc_1d) < 0 else "상승"
        btc_anchor_ko = (
            f"비트코인이 ${float(_btc_price):,.2f}로 오늘 {_btc_1d_abs:.2f}%, "
            f"5일간 {_btc_5d_abs:.2f}% {_btc_kor_dir}했습니다."
        )
    else:
        btc_anchor_en = "Bitcoin price data unavailable."
        btc_anchor_ko = "비트코인 데이터 없음."

    return f"""You are a friendly stock market expert writing a morning briefing for Korean retail investors who are NOT finance professionals.
Today is {now_kst} (KST).

━━━ DATA TIMING — READ FIRST ━━━
This briefing runs at ~06:45 KST (21:45 UTC previous day), BEFORE the US market opens.
- "전일종가" = last US session closing price (the most recent confirmed close)
- "전일등락" = that session's change vs the session before (D-2 → D-1)
- "프리마켓" = current pre-market price RIGHT NOW (if available) — use this for today's direction
- DO NOT write "오늘 X% 상승/하락" using 전일등락 — that is YESTERDAY's move, not today's.
- If 프리마켓 is N/A, you do NOT know today's direction — do not invent it.

{global_block}

━━━ SNIPERBOARD AUTHORITATIVE DATA TABLE ━━━
Source: yfinance real-time feeds + earnings calendar. These are the ONLY numbers allowed in your briefing.
Do NOT substitute, approximate, invent, or use training-data recollection for any price, %, or date.

{auth_table}

━━━ MACRO SIGNAL GROUPS (yfinance-computed, use for sector_analysis) ━━━
{macro_insight_block}

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
7. DATA BINDING (CRITICAL):
   - Prices: use ONLY 전일종가 from the table. Pre-market price if discussing today's direction.
   - % changes: use ONLY the table values. "0.00%(데이터없음)" means you do NOT know — write direction only.
   - Earnings: mention ONLY if within 14 days AND the date appears in the provided data. If N/A or >14 days, omit earnings entirely — do NOT write "30일 이내 실적 발표 없음" or any equivalent phrase. This applies to ALL sections including spotlight.
   - Support/resistance levels: must be within ±25% of 전일종가. EMA21/50/200 from 가격앵커 section.
   - If 프리마켓=N/A: do NOT write "오늘 상승 중" or any today direction claim.
   - market_structure: use the EXACT value from '구조=' field — 'UPTREND', 'DOWNTREND', or 'DISTRIBUTION'. Never write DOWNTREND for a stock whose data shows DISTRIBUTION. They are fundamentally different conditions.
   - Sentiment context (key_reason): use ONLY the 투자자반응/투자자반응(KO) field values from the provided data. Do NOT inject specific financial metrics (ARR%, EPS numbers, revenue figures, product names) from training memory.

MARKET DATA ({now_kst}):
- 리스크 레짐: {regime_label} ({regime_score}/100)
  [RISK_ON≥80=매수 우호 / CONSTRUCTIVE≥60=긍정적 / MIXED≥40=혼조 / DEFENSIVE≥20=방어적 / RISK_OFF<20=위험회피]
  추세점수={comps.get('trend','?')}  시장폭={comps.get('breadth','?')}  신용={comps.get('credit','?')}  변동성={comps.get('volatility','?')}  모멘텀={comps.get('momentum','?')}
- SPY 분배일(기관매도흔적): {spy_dd.get('count','?')}일 ({spy_dd.get('level','?')}) [4일미만=정상 / 4-5일=주의 / 6일이상=위험]
- QQQ 분배일: {qqq_dd.get('count','?')}일 ({qqq_dd.get('level','?')})
- 전체시장 소셜심리: {market_sent.get('sentiment','N/A')} (종합점수={market_sent.get('composite_score','N/A')})

{macro_binding}

주요 매크로 지표 (yfinance 전일 종가 기준):
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
    "summary_en": "2 sentences — the macro backdrop. CONFIDENCE RULE: [confirmed] global issues → state as fact. [developing] → 'Reports indicate...' or 'Early reports suggest...'. [unverified] → 'Unverified reports...' NEVER state a [developing] or [unverified] issue as established fact.",
    "summary_ko": "같은 내용 한국어 2문장. 신뢰도 규칙: [confirmed]는 사실로, [developing]은 '보도에 따르면...' 또는 '초기 보도 기준...', [unverified]는 '미확인 보도에 따르면...' — [developing]/[unverified]를 확정 사실처럼 서술하는 것은 오류.",
    "vix_note_en": "1-2 sentences: what is VIX at today, and what does it mean in human terms (fear/calm/overconfident?)",
    "vix_note_ko": "VIX가 얼마이고 그게 무슨 의미인지 — VIX를 모르는 사람도 이해하게.",
    "rates_note_en": "1-2 sentences: 10Y yield level and whether it's helping or hurting stocks today",
    "rates_note_ko": "미국 10년물 국채 금리(기준금리의 바로미터)가 오늘 주식 시장에 어떤 영향을 주는지.",
    "dollar_note_en": "MUST cite exact DXY value from MACRO BINDING TABLE. Format: 'The dollar index (DXY) is at [exact value]...' then explain direction and impact for tech/global earnings. Omitting the DXY number is a binding violation.",
    "dollar_note_ko": "반드시 MACRO BINDING TABLE의 정확한 DXY 수치 포함. 형식: '달러지수(DXY)가 [테이블의 정확한 수치]로...' 이후 달러 방향이 기술주·해외 투자자에게 미치는 영향 설명. DXY 수치 생략 금지.",
    "btc_note_en": "{btc_anchor_en} [Append 1 sentence only: what does this signal about risk appetite today? No numbers — only interpretation.]",
    "btc_note_ko": "{btc_anchor_ko} [뒤에 1문장만 추가: 위험 선호도에 무엇을 의미하는지. 추가 수치 금지.]"
  }},
  "sector_analysis": {{
    "leaders_en": "Based on MACRO SIGNAL GROUPS (🟢 green = technically strong). HARD RULE: Stocks with DOWNTREND market_structure are NEVER technical leaders. If a DOWNTREND stock benefits from a news theme (e.g. oil spike), write: '[sector]: narrative interest from [theme], but technically in DOWNTREND — not a structural leader.' Only stocks with UPTREND or neutral structure can be called leaders.",
    "leaders_ko": "MACRO SIGNAL GROUPS의 🟢 녹색 신호 기반. 핵심 규칙: DOWNTREND 종목은 절대 기술적 리더가 아님. 뉴스 테마 수혜라도 '해당 섹터: [테마] 수혜 내러티브, 단 기술적 구조는 DOWNTREND — 진정한 섹터 리더 아님'으로 작성할 것.",
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
      "why_en": "2-3 sentences. Price levels MUST match 전일종가/52주고점 from the AUTHORITATIVE DATA TABLE. If 프리마켓 is available, mention it as 'pre-market at $X (+Y%)'. Mention earnings ONLY if the data shows ≤14 days away — if >14 days, omit earnings entirely even in spotlight. Do NOT add financial metrics (ARR%, EPS results, guidance) from training memory — only use 투자자반응 field for catalyst context.",
      "why_ko": "오늘 이 종목이 특별히 주목받는 이유 2-3문장. 가격대는 반드시 테이블의 전일종가 기준. 프리마켓 값이 있으면 '개장 전 $X(+Y%)' 형태로 추가. 실적일은 14일 이내일 때만 언급(테이블 기준), 초과 시 완전 생략. ARR%·EPS 실적·가이던스 등 훈련 데이터 기반 수치 추가 금지.",
      "watch_level_en": "Use 전일종가 as anchor. Support/resistance from EMA21/EMA50/EMA200 or entry in 가격앵커. e.g. 'Break above $X (prev close $Y); EMA21 support at $Z (from data)'",
      "watch_level_ko": "테이블의 전일종가·EMA21/50/200·entry 값 기반. '$X 돌파(전일종가 $Y) / EMA21=$Z 이탈 시 주의' 형태. ±25% 범위 초과 수치 사용 금지."
    }}
  ],
  "watchlist": [
    {{
      "symbol": "TICKER",
      "company": "Company Name",
      "tier": 1,
      "analysis_en": "3-5 sentences flowing paragraph. (1) recent price level using EXACT 전일종가 from table; if 프리마켓 is available, mention today's pre-market direction with that exact value, (2) strength or vulnerability in plain language using market_structure and stage2 data, (3) upside or downside using EMA/ATR anchors from 가격앵커, (4) social sentiment. All $ values must match table. Mention earnings ONLY if ≤14 days away with exact date; otherwise omit earnings entirely.",
      "analysis_ko": "같은 내용 한국어 3-5문장. 전일종가는 테이블 값 그대로. 프리마켓 값이 있으면 '오늘 개장 전 $X(+Y%)' 형태로 사용. 없으면 오늘 방향 언급 금지. 실적은 14일 이내일 때만 정확한 날짜와 함께 언급, 그 외 완전 생략. 소셜 반응 자연스럽게 포함.",
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
  "earnings_alert_en": "List ONLY: (1) ⚠이미발표됨 stocks: '[SYM] already reported after US close (est. EPS $X — verify actual at broker)'; (2) stocks where the authoritative table shows earnings_date within 14 CALENDAR DAYS from today. Count days_until from the table — if days_until > 14 or N/A, the stock is EXCLUDED from this field entirely. If no qualifying stocks exist, write empty string. Never 'next week'/'soon'/'no earnings'. MU earnings on June 24 = 18 days away = EXCLUDED (>14 days).",
  "earnings_alert_ko": "다음 종목만 나열: (1) ⚠이미발표됨: '[심볼] 오늘 미국 장 마감 후 실적 발표됨 (EPS 추정 $X — 실제 결과는 증권사 확인)'; (2) 테이블상 실적일이 오늘 기준 14일 이내인 종목만 — days_until > 14이면 이 필드에서 완전 제외. 해당 종목이 없으면 빈 문자열. '다음 주'/'곧'/'실적 없음' 금지. 14일 초과 종목(예: MU 6/24 = 18일후)은 표기 금지."
}}

REQUIREMENTS:
- spotlight: 2-4 most interesting from the 21 (mix of opportunities and risks)
- watchlist: ALL 21 in order TSM,NVDA,META,TSLA,PLTR,MU,CRWD,AMZN,MSFT,AAPL,GOOGL,RKLB,CEG,VST,ALAB,OKLO,APP,ANET,NVO,QBTS,SOFI
ACTION RULES — apply in this EXACT priority order (first rule that applies wins):
  RULE 1 (HARD): action=avoid  IF: market_structure=DOWNTREND AND Stage2≤6
                               OR  Stage2≤2 (regardless of structure)
                               OR  (⚠이미발표됨 AND post-market drop>10%)
  RULE 1 EXCEPTION: Stage2=7 AND RS≥70 even with DOWNTREND → 'watch' not 'avoid'

  RULE 2: action=buy   IF: Stage2≥6 AND RS≥70 AND market_structure≠DOWNTREND AND (mood=optimistic OR euphoric)
  RULE 3: action=hold  IF: Stage2≥5 AND in solid technical position (near entry, recent breakout, EMA support)
  RULE 4: action=watch IF: any other case — interesting setup but mixed signals

  ⚠ DISTRIBUTION ≠ DOWNTREND (important distinction):
    DISTRIBUTION = high area with institutional selling pressure → use 'watch' not 'avoid' (if Stage2≥4)
    DOWNTREND = confirmed lower highs + lower lows pattern → use 'avoid' (per RULE 1)
    A stock with DISTRIBUTION structure and Stage2≥5 should be 'watch', not 'avoid'.

  RS adjustment (does NOT override the rules above, only shifts borderline cases):
    RS<30: downgrade one level (buy→hold, hold→watch, but NEVER watch→avoid by RS alone)
    RS≥70: supports 'buy' if other criteria met

  ⚠이미발표됨 with post-market drop >5% but <10%: max action='watch'
  ⚠이미발표됨 with post-market drop >10%: action='avoid'

TICKER-SPECIFIC DIRECTION RULES (override training-data defaults):
  RKLB + SpaceX IPO: SpaceX IPO is NEGATIVE for RKLB near-term (liquidity competition draws capital away).
    ✅ ALLOWED: "SpaceX IPO creates liquidity competition for RKLB"
    ❌ FORBIDDEN: "SpaceX IPO beneficiary", "halo effect", "space theme lift" for RKLB without explicit caveat

- sentiment_score: copy from the social data (composite_score field)
- analysis_ko must integrate sentiment naturally — not as a separate item at the end

ANTI-HALLUCINATION RULES — CRITICAL:
1. PRICE LEVELS (watch_level_en/ko):
   - Use ONLY 전일종가 from the authoritative table as the price anchor.
   - Support/resistance levels MUST be within ±25% of 전일종가.
   - Prefer EMA21/EMA50/EMA200 values from the 가격앵커 section for specific levels.
   - ATR14 from 가격앵커 defines the natural daily price range — do not suggest moves beyond 3×ATR14.
   - NEVER invent a price level not derivable from the provided data.

2. TODAY'S DIRECTION vs YESTERDAY'S CHANGE:
   - "전일등락(D-2→D-1)" is YESTERDAY's change, not today's. Do NOT write it as "오늘 X% 상승".
   - To describe TODAY's direction, use 프리마켓 value from the table. If 프리마켓=N/A, do NOT claim a direction.
   - If 전일등락=0.00%(데이터없음): you do NOT know that day's change — write direction only without a %.

3. EARNINGS HALLUCINATION — HIGHEST PRIORITY RULE:
   ▶ For ⚠이미발표됨 stocks — BANNED WORDS (automatic fail):
     beat, miss, exceeded, disappointed, strong beat, strong miss, EPS beat, EPS miss,
     상회, 하회, 어닝 서프라이즈, 실적 상회, 실적 하회, 어닝 쇼크,
     split, reverse split, 분할, 주식분할, 배당, buyback, 자사주매입
   ▶ REASON: We only have estimated EPS and the post-market price reaction.
     We do NOT know: actual EPS, revenue, guidance, split announcements, or any forward statement.
   ▶ The price reaction (post-market up/down) does NOT tell you if it was a beat or miss —
     stocks fall on beats and rise on misses. Do NOT infer result from price direction.
   ▶ ALLOWED template: "[SYM] reported after close today (est. EPS $X — verify actual at broker)"
   ▶ EARNINGS DATES: Use ONLY table dates. NEVER write "next week"/"soon" without exact date.

4. SECTOR LEADERS: Use the MACRO SIGNAL GROUPS section as the primary basis for sector_analysis.
   A "🟢 green" signal = technical strength. A "🔴 red" signal = technical weakness.
   A stock in DOWNTREND market_structure is NOT a technical leader — label it "narrative interest, DOWNTREND".
   Do NOT contradict the macro signal group judgments without explicit reasoning.

5. BTC LARGE MOVE ALERT: Check BTC-USD in macro data.
   If BTC-USD 1D ≤ -5% OR 5D ≤ -10%: MANDATORY include in executive_bullets_ko.
   BTC crash = macro risk signal, NOT just a crypto story. Do NOT write "증시 차분/안정적" alongside BTC crash.

6. NAMED EVENTS / CORPORATE ACTIONS: DO NOT mention specific military exercise names unless explicitly
   in the global context with a confirmed source_hint. Use general descriptions only.
   DO NOT mention stock splits, buybacks, M&A unless explicitly in the global context.

7. MARKET_STRUCTURE EXACT NAMING — CRITICAL:
   You MUST use the exact market_structure value from the '구조=' field in analysis text.
   The three valid values are: UPTREND, DOWNTREND, DISTRIBUTION.
   DISTRIBUTION and DOWNTREND are DIFFERENT conditions — never substitute one for the other.
   ✅ CORRECT: "sits in DISTRIBUTION (institutional selling pressure near highs)"
   ❌ FORBIDDEN: writing 'DOWNTREND' when 구조=DISTRIBUTION (even if Stage2 is low)
   In Korean, use: UPTREND→"상승 추세", DOWNTREND→"하락 추세", DISTRIBUTION→"분배 구간"

8. EXTERNAL FINANCIAL METRICS — STRICTLY FORBIDDEN:
   Do NOT add specific numbers or events from your training memory (e.g. "250% ARR growth",
   "Broadcom's AI guidance", "UK firearms contract", "MAI-Thinking-1", "Q1 beat/miss").
   ONLY use: (a) numbers explicitly in the provided data tables, OR (b) facts from global_context
   issues with a verified source_hint.
   For catalyst/sentiment context: use ONLY the 투자자반응/투자자반응(KO) field as provided.
   Violating this rule = hallucination, even if the fact happens to be true in training data.

SELF-CHECK before outputting JSON (fix any violation before output):
  □ All prices in analysis/watchlist/spotlight match 전일종가 column in authoritative table?
  □ All pre-market prices match 프리마켓 column (or N/A if not available)?
  □ Any ⚠이미발표됨 stock: does analysis contain 'beat','miss','상회','하회','split','분할'? → REMOVE
  □ Any DOWNTREND stock with action=buy? → change to 'watch' or 'avoid' per rule
  □ Any Stage2≤2 stock with action='watch' or 'hold'? → change to 'avoid'
  □ RKLB + SpaceX: is direction framed as negative (liquidity competition)?
  □ EMA levels in watch_level: do they match EMA21/50/200 from 가격앵커 section?
  □ All % changes: do they come from 전일등락(D-2→D-1) column, not invented?
  □ btc_note VIX/TNX/DXY/BTC values match MACRO BINDING TABLE exactly?
     BTC price, 1D%, 5D% must be the EXACT values from the binding table — no approximation.
  □ dollar_note_en/ko: does it cite the exact DXY numeric value from MACRO BINDING TABLE? If missing, add it before output.
  □ BTC price in ALL sections (executive_bullets, sector_analysis, watchlist, etc.): does every mention of BTC price match the MACRO BINDING TABLE value? Training-memory BTC price is forbidden anywhere in the briefing.
  □ headline_ko: count the characters — must be ≤30. If >30 chars, shorten before output. No exceptions.
  □ For each stock in watchlist/spotlight: does the written market_structure match 구조= field?
     DISTRIBUTION ≠ DOWNTREND — mixing them is a factual error. Fix before output.
  □ Any spotlight/watchlist analysis mention earnings for a stock with >14 days until earnings? → REMOVE
  □ Any analysis contain specific financial metrics (ARR%, guidance figures, product names) not
     in the provided tables or global_context with source_hint? → REMOVE those external facts.
  □ earnings_alert: does it contain any stock with days_until > 14? → REMOVE (write "" if none remain).
     Count from today's date. Showing a date in the authoritative table does NOT authorize mentioning it here.
  □ big_picture.summary: for each referenced global_context issue, does the language match the confidence level?
     [confirmed] = fact / [developing] = "Reports indicate..." / [unverified] = "Unverified reports..."

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
