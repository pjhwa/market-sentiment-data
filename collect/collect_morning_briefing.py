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
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
import yfinance as yf

from collect.git_utils import commit_and_push
from collect.grok_utils import call_hermes_json, extract_json

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
# morning briefing has longer timeouts than other collectors
CALL_TIMEOUT        = int(os.environ.get("HERMES_TIMEOUT", "180"))
CALL_TIMEOUT_GLOBAL = int(os.environ.get("HERMES_TIMEOUT_GLOBAL", "150"))
# Stage-1 must be able to use hermes web toolset for live search
HERMES_STAGE1_TOOLSETS = os.environ.get("HERMES_STAGE1_TOOLSETS", "web")
SNIPERBOARD_API = os.environ.get("SNIPERBOARD_API_BASE", "http://localhost:5001")

_VALID_GC_CATEGORIES = {
    "trade_tariff", "geopolitical", "central_bank", "ai_regulation",
    # earnings/market_structure: session-material non-geo catalysts (no daily quota)
    "earnings", "market_move",
}
_VALID_GC_TIERS = {"breaking", "ongoing"}
_VALID_GC_CONFIDENCE = {"confirmed", "developing", "unverified"}
_VALID_GC_IMPACT = {"positive", "negative", "neutral", "watch"}
_VALID_GC_DIRECTION = {"escalating", "de-escalating", "stable_elevated", "stable_fading"}

# Accepted-outlet RSS used as *mechanical* Stage-1 evidence (not LLM memory).
# First principles: rank from real headlines; do not invent category filler.
_STAGE1_RSS_FEEDS = (
    # US-equity-focused first (Google News RSS is reliable and not outlet-DNS-fragile)
    (
        "gnews_us_markets",
        "https://news.google.com/rss/search?q=US+stock+market+OR+Wall+Street+when:2d&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "gnews_fed",
        "https://news.google.com/rss/search?q=Federal+Reserve+OR+FOMC+OR+Treasury+yields+when:2d&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "gnews_oil_geo",
        "https://news.google.com/rss/search?q=oil+OR+crude+OR+OPEC+OR+Middle+East+markets+when:2d&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "gnews_semis_trade",
        "https://news.google.com/rss/search?q=semiconductor+OR+chip+export+OR+tariffs+when:2d&hl=en-US&gl=US&ceid=US:en",
    ),
    ("bbc_business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("cnbc_top", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("cnbc_world", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362"),
    ("marketwatch_top", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    # Reuters host is intermittently unresolvable in some networks — keep last
    ("reuters_business", "https://feeds.reuters.com/reuters/businessNews"),
    ("reuters_markets", "https://feeds.reuters.com/reuters/marketsNews"),
)

_MARKET_TITLE_HINT = re.compile(
    r"\b(stock|stocks|market|markets|fed|fomc|cpi|oil|crude|tariff|tariffs|"
    r"semiconductor|chip|nvidia|earnings|treasury|yield|yields|dow|nasdaq|"
    r"s&p|wall street|equity|equities|rate\s*cut|rate\s*hike|inflation|"
    r"antitrust|export|opec|iran|hormuz)\b",
    re.I,
)

_ACCEPTED_SOURCE_HINTS = (
    "reuters", "bloomberg", "ap ", "associated press", "bbc", "ft.com", "financial times",
    "wsj", "wall street journal", "nyt", "new york times", "cnbc", "marketwatch",
    "white house", "bis", "sec", "fed", "doj", "ftc", "court", "commerce",
    "treasury", "ecb", "boj", "imf", "oecd",
)

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
    ("SPCX",  "SpaceX",                1),
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


def _build_earnings_lookup(earnings_data: dict, now_date=None) -> dict:
    """종목별 실적 발표일·EPS 예상치 조회 dict. upcoming_earnings 기준.

    Absolute earnings_date is SoT. days_until is recomputed at collect time using
    **US/Eastern** calendar day (US equity after-close timing), NOT KST.
    Mixing KST "today" with ET earnings_date produced contradictory phrases like
    "already reported after US close" vs "in 3 days" in the same briefing.

    already_reported_possible: only when days_until < 0 (calendar date fully past
    in ET). Same calendar day (days_until==0) is "reports today / after close" —
    not "already reported" until the next ET day.
    """
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except Exception:
        et = timezone.utc
    if now_date is None:
        now_date = datetime.now(et).date()

    lookup: dict = {}
    for e in earnings_data.get("upcoming_earnings", []):
        sym = e.get("symbol")
        if sym and sym not in lookup:
            earn_date_str = e.get("earnings_date") or e.get("report_date")
            try:
                earn_date = _dt.date.fromisoformat(earn_date_str) if earn_date_str else None
            except ValueError:
                earn_date = None
            days_until = (earn_date - now_date).days if earn_date else None
            lookup[sym] = {
                "earnings_date":           earn_date_str,
                "days_until":              days_until,
                "eps_estimate":            e.get("eps_estimate"),
                # Past calendar day only — do NOT flag days_until==0 as already done
                "already_reported_possible": (days_until is not None and days_until < 0),
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
        if daily is None:
            # API 실패 시 yfinance에서 직접 기본 가격 데이터 시도 (신규 상장 등 데이터 부족 종목)
            try:
                hist = yf.Ticker(sym).history(period="5d")
                if not hist.empty and len(hist) >= 1:
                    closes = hist["Close"].dropna().tolist()
                    latest_price = round(float(closes[-1]), 2)
                    prev_chg = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else 0.0
                    symbol_detail[sym] = {
                        "price":                  latest_price,
                        "change_pct_prev_day":    prev_chg,
                        "high_52w_price":         None,
                        "stage2_score":           None,
                        "rs_score":               None,
                        "market_structure":       "UNKNOWN",
                        "ipo_pending":            True,
                        "ipo_days":               len(hist),
                    }
                    print(f"[INFO] {sym}: SniperBoard 데이터 없음 — yfinance 기본 가격만 수집 ({len(hist)}일 치)", file=sys.stderr)
            except Exception as yf_err:
                print(f"[WARN] {sym}: yfinance fallback 실패: {yf_err}", file=sys.stderr)
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
        if d.get("ipo_pending"):
            days = d.get("ipo_days", "?")
            rows.append(f"{sym:<6} ${d['price']:>9,.2f} {d.get('change_pct_prev_day', 0):>+7.2f}%  {'N/A':>12} {'N/A':>11} {'N/A':>7}  {'N/A':<12} {'N/A':>9} ⚠RECENT IPO({days}d) — Stage2/RS 데이터 없음")
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

        if d.get("ipo_pending"):
            days = d.get("ipo_days", "?")
            price = d.get("price", 0)
            chg = d.get("change_pct_prev_day", 0)
            lines.append(
                f"{sym} ({company}) [T{tier}]: ⚠RECENT IPO ({days}일 거래 기록)\n"
                f"  가격: ${price:,.2f} (전일 {chg:+.2f}%) | Stage2/RS 데이터 없음 (기술적 분석 불가)\n"
                f"  → watchlist 포함하되 action=watch, 기본 가격만 서술"
            )
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
                f"  ✅ 허용 표현: '{earn_date} 실적 발표됨 — EPS 추정 ${eps_est}, 실제 결과 확인 필요'"
            )
        elif earn_date and days_earn is not None and days_earn == 0:
            earn_str = (
                f"【실적발표={earn_date} (오늘·미국 장 마감 후 예정) / EPS예상=${eps_est}】\n"
                f"  ✅ 허용: '오늘({earn_date}) 장 마감 후 실적 예정'. ⛔ '이미 발표됨' 금지(아직 마감 전일 수 있음)."
            )
        elif earn_date and days_earn is not None and days_earn <= 14:
            earn_str = (
                f"【실적발표={earn_date} ({days_earn}일후) / EPS예상=${eps_est}】\n"
                f"  ✅ 상대일({days_earn}일후)과 절대일({earn_date})을 함께 쓸 것. 다른 섹션과 다른 상대일 금지."
            )
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

    lines.append("(위 신호는 yfinance 실시간 계산값. sector_analysis 작성 시 이 신호 기반으로 작성할 것. 단, 그룹 신호(RED/GREEN)를 원자재/금리 등 하위 구성 종목 전체에 동일하게 적용하지 말 것 — 'all flash RED'처럼 구성 종목 전원이 같은 방향이라고 단정하기 전에 MACRO BINDING TABLE에서 GLD/CL=F/BTC-USD 등 각 심볼의 실제 1D/5D 값을 개별 확인하고, 방향이 엇갈리면 반드시 그 차이를 명시할 것 — 혼합 신호를 단일 방향으로 뭉뚱그리는 것은 사실 오류임.)")
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
    """글로벌 컨텍스트를 2차 Grok 프롬프트 주입용 텍스트로 변환.

    First principles: inject evidence + short structural rules only.
    No event-specific MUST catalogs (named chokepoints, Fed anecdotes, IPO checklists).
    """
    issues = global_ctx.get("issues", [])
    if not issues:
        return "GLOBAL CONTEXT: No verified global issues retrieved (search failed or no significant events)."

    lines = [
        "━━━ GLOBAL MACRO & GEOPOLITICAL CONTEXT ━━━",
        f"(Fetched as of {global_ctx.get('fetched_at', 'unknown')}; search_window={global_ctx.get('search_window', '48h')})",
        "Each issue below is INPUT EVIDENCE for this run only — not a daily topic quota.",
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
        lines.append(
            f"\nNo material 48h delta (do not invent filler issues): {', '.join(str(x) for x in no_update)}"
        )

    lines.append("""
INSTRUCTIONS for using this context (evidence-bound; no topic hardcodes):
1. SCOPE — You may cite only: (a) issues listed above, (b) SniperBoard/authoritative tables in this prompt,
   (c) earnings block. Do NOT inject training-memory events (named wars, Fed speakers, non-listed IPOs,
   product metrics) that are absent from those inputs. If something material seems missing, write a short
   '[CONTEXT GAP]' note — do not fabricate the fact. This also covers market-internal statistics (e.g.
   distribution-day counts, breadth readings, 'hit records' claims) and calendar/session claims (e.g. which
   date was the 'last session') — state these only if a specific figure/date appears in the inputs above; do
   not recall a number or a trading-day claim from training memory, and do not assert a market event occurred
   on a date without confirming from the inputs that a session actually occurred that date.
2. CURRENT STATE BINDING — For any listed issue, do not contradict or soften Current State with older
   training recollection. Prefer the provided state, dates, and scope verbatim. Do NOT add causal or comparative framing (e.g., "undershot forecasts", "beat expectations", "reinforced cut bets") unless the provided Current State/Summary explicitly states both the actual figure and what it was compared against — if the evidence says a figure matched consensus, report it as in line, not as a beat/miss/undershoot, and do not name a data release as the primary driver of a market reaction unless the evidence explicitly says so.
3. ASYMMETRIC IMPACT — When discussing a ticker, if that ticker appears in Asymmetric Impact, use that
   direction. If it says unaffected/영향 없음, do NOT use the issue as the cause of that ticker's move.
   ADDITIONALLY: if a ticker highlighted elsewhere in this briefing (leaders/laggards/watchlist/action calls)
   had a major earnings-driven price move within the prior 3 trading sessions per the earnings block, you MUST
   reference that catalyst (direction, magnitude, date) even if the ticker is absent from Asymmetric Impact
   above — omitting a known earnings catalyst for a highlighted ticker is a critical-news omission. Use the
   exact price-move percentage given in the earnings block source data; do not restate an approximate or
   partial (e.g., early after-hours) figure when a fuller session figure is available there.
4. big_picture.summary — If the issue set above has 2 or fewer entries, or the reported index/session moves are larger than what the listed issues plausibly explain, do NOT present a single-cause narrative (e.g., attributing a selloff solely to yields/tariffs) — frame causation as partial ("among the drivers") and add a one-line '[CONTEXT GAP]' note that other unlisted factors may be contributing, rather than asserting a complete explanation. Otherwise: At most 1–2 sentences on the highest-ranked issue that has real novelty;
   include market_paradox if present. Do not restate every quiet ongoing risk.
5. CONFIDENCE LANGUAGE (apply in big_picture.summary / executive_bullets):
   [confirmed]  → may state as fact (still hedge if Current State itself is fragile/contested)
   [developing] → "Reports indicate…" / "Early reports suggest…" — never as settled fact
   [unverified] → "Unconfirmed…" — never as a primary executive bullet driver
6. NO FILLER — Do not write "monitoring continues" / "situation ongoing" without a direction and implication.
   Do not promote an issue that has no 48h delta into the headline or lead bullet.
7. SECTOR LEADERS ACCURACY — When listing sector/theme leaders, do not include a ticker whose SniperBoard market_structure/Stage is DOWNTREND without an explicit caveat (e.g., "despite DOWNTREND structure") directly beside it; never present a DOWNTREND ticker as an unqualified leader.
8. TICKER STRUCTURE BINDING — Every single time you write a ticker symbol anywhere in the output (leaders/laggards/watchlist/action calls, headline, and narrative sentences alike), format it as "TICKER (LABEL)" where LABEL is its exact market_structure/Stage value from the SniperBoard table verbatim (e.g., "TSM (ACCUMULATION)", "MU (DOWNTREND)"). This applies to EVERY ticker in the SniperBoard table without exception, including tickers you only mention once in passing — omitting the (LABEL) tag for any ticker is a critical error, not a stylistic choice. Never infer, recall from training memory, or carry over a structure label from a prior day; copy the value character-for-character from the SniperBoard table provided in THIS prompt, even if it contradicts what you expect the ticker's trend to be. A ticker symbol without "(LABEL)" immediately after it is a format error — there are no exceptions, including tickers only mentioned in prose. [SELF-CHECK] Before returning output, re-scan every line of your draft for ticker symbols and confirm each has a verbatim (LABEL) suffix — including ACCUMULATION, which is the label most often silently dropped; insert any missing label before finalizing. ACCUMULATION is the label most often silently dropped — treat it exactly like any other structure value (e.g., DOWNTREND) and never omit "(ACCUMULATION)" just because it seems like default/neutral state. BEFORE finalizing output, re-scan every ticker symbol you wrote (headline, leaders, laggards, watchlist, action calls, and prose) and confirm each has its "(LABEL)" — insert any missing one from the SniperBoard table before returning the result. because it sounds like a default/neutral state. If a ticker's label is DOWNTREND and it is being described as a leader/gainer, either drop it from that list or write it as "TICKER (DOWNTREND — despite structure)". Before finalizing output, re-scan every ticker you wrote — in every section (headline, leaders, laggards, watchlist, action calls, and prose) — and verify each individual occurrence carries its "(LABEL)" suffix; labeling a ticker correctly once does not exempt its other occurrences from this check. This check is MANDATORY for every ticker present in the SniperBoard table, not only ones you chose to discuss narratively — build a mental checklist of all SniperBoard tickers you referenced anywhere in the output and confirm each one carries its "(LABEL)" suffix at every occurrence before submitting; a ticker symbol appearing with zero "(LABEL)" tags anywhere in the output is a critical format error. Do NOT alter, soften, or invent a ticker's market_structure value to make it fit a narrative (e.g., writing UPTREND for a ticker so it can be called a leader when the table says DOWNTREND) — the label must always match the table exactly regardless of which section or narrative role the ticker is being used in; if the true label conflicts with the section's purpose, exclude the ticker or use the prescribed caveat sentence instead of falsifying the label.
8. DEFINE ACRONYMS — The first time you use a technical acronym or abbreviation anywhere in the output — including short ones that look like plain words, such as RS, EPS, IV — it must be written as "RS (Relative Strength)", "EPS (Earnings Per Share)", "IV (Implied Volatility)", etc. on that first occurrence. Before finalizing output, re-scan the full text for any acronym or abbreviation (RS, EPS, IV, PPI, etc.) used without its expansion on first occurrence.
9. CROSS-SECTION CONSISTENCY — If the same ticker appears in more than one section (e.g., headline/leaders/laggards vs. watchlist/action calls), the price-move magnitude, mood tag, and action must all agree, and must use the fullest/most accurate session figure available in the source data rather than an earlier partial (e.g., pre-market or early after-hours) figure. Do NOT describe a ticker with a large % move in one section while giving it a neutral mood and an "avoid" action elsewhere without an explicit reconciling note (e.g., "pre-market quote lagged the full-session move").
10. NAMED-BENCHMARK PRECISION — When citing a price for a named commodity/index benchmark (e.g., WTI vs. Brent crude, or any other pair of related-but-distinct benchmarks), the price figure and the benchmark name must come from the same evidence line — never attach one benchmark's number to a different benchmark's name, and never carry over a level/'N-week high' framing that is not stated for that specific benchmark in the inputs above.
""")
    return "\n".join(lines)


def _strip_html(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _rss_local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _parse_rss_items(xml_text: str, *, feed_id: str, max_items: int = 12) -> list[dict]:
    """Parse RSS/Atom XML into {title, link, published, source, feed_id} dicts."""
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out

    # RSS 2.0
    for item in root.iter():
        if _rss_local(item.tag) != "item":
            continue
        fields = {_rss_local(c.tag): (c.text or "") for c in list(item)}
        title = _strip_html(fields.get("title", ""))
        if not title:
            continue
        link = (fields.get("link") or "").strip()
        pub = fields.get("pubDate") or fields.get("date") or ""
        pub_iso = ""
        if pub:
            try:
                pub_iso = parsedate_to_datetime(pub).astimezone(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            except (TypeError, ValueError, IndexError):
                pub_iso = pub[:32]
        host = urlparse(link).netloc.replace("www.", "") if link else feed_id
        out.append({
            "title": title[:240],
            "link": link[:400],
            "published": pub_iso,
            "source": host or feed_id,
            "feed_id": feed_id,
        })
        if len(out) >= max_items:
            break

    if out:
        return out

    # Atom
    for entry in root.iter():
        if _rss_local(entry.tag) != "entry":
            continue
        title = ""
        link = ""
        pub = ""
        for c in list(entry):
            loc = _rss_local(c.tag)
            if loc == "title":
                title = _strip_html(c.text or "")
            elif loc == "link":
                link = (c.attrib.get("href") or c.text or "").strip()
            elif loc in ("updated", "published"):
                pub = (c.text or "").strip()
        if not title:
            continue
        host = urlparse(link).netloc.replace("www.", "") if link else feed_id
        out.append({
            "title": title[:240],
            "link": link[:400],
            "published": pub[:32],
            "source": host or feed_id,
            "feed_id": feed_id,
        })
        if len(out) >= max_items:
            break
    return out


def fetch_stage1_search_evidence(
    *,
    max_total: int = 28,
    per_feed: int = 8,
    timeout: float = 8.0,
) -> list[dict]:
    """Mechanically fetch recent headlines from accepted-outlet RSS feeds.

    Returns list of {title, link, published, source, feed_id}.
    Empty list on total failure — Stage-1 then relies on hermes web only.
    """
    items: list[dict] = []
    seen_titles: set[str] = set()
    headers = {
        "User-Agent": "SniperBoardMorningBriefing/1.1 (+local collector; RSS evidence)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    for feed_id, url in _STAGE1_RSS_FEEDS:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            batch = _parse_rss_items(resp.text, feed_id=feed_id, max_items=per_feed)
        except Exception as e:
            print(f"[WARN] Stage-1 RSS {feed_id} 실패: {e}", file=sys.stderr)
            continue
        for it in batch:
            key = it["title"].lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            items.append(it)
    # Prefer titles with market transmission keywords (no hard topic quotas — ranking only)
    items.sort(
        key=lambda it: (
            0 if _MARKET_TITLE_HINT.search(it.get("title") or "") else 1,
            it.get("published") or "",
        ),
    )
    return items[:max_total]


def format_stage1_evidence_block(evidence: list[dict]) -> str:
    """Render mechanical headlines for injection into Stage-1 prompt."""
    if not evidence:
        return (
            "━━━ MECHANICAL SEARCH EVIDENCE ━━━\n"
            "(No RSS headlines retrieved — you MUST use live web search tools now.)\n"
        )
    lines = [
        "━━━ MECHANICAL SEARCH EVIDENCE (accepted-outlet RSS, pre-fetched) ━━━",
        f"n={len(evidence)}. Rank US-equity market impact from THESE items first.",
        "You may run additional live web search to verify dates/details — do not invent events absent here "
        "unless live search confirms them with an accepted source.",
        "",
    ]
    for i, it in enumerate(evidence, 1):
        pub = it.get("published") or "?"
        src = it.get("source") or it.get("feed_id") or "?"
        title = it.get("title") or ""
        link = it.get("link") or ""
        lines.append(f"[{i}] ({src} | {pub}) {title}")
        if link:
            lines.append(f"    {link}")
    lines.append("")
    return "\n".join(lines)


def _issue_is_valid(iss: dict) -> bool:
    """Soft per-issue check — drop bad items instead of rejecting the whole payload."""
    if not isinstance(iss, dict):
        return False
    if iss.get("category") not in _VALID_GC_CATEGORIES:
        print(f"[WARN] global_context drop: category={iss.get('category')!r}", file=sys.stderr)
        return False
    if iss.get("tier") not in _VALID_GC_TIERS:
        print(f"[WARN] global_context drop: tier={iss.get('tier')!r}", file=sys.stderr)
        return False
    if iss.get("confidence") not in _VALID_GC_CONFIDENCE:
        print(f"[WARN] global_context drop: confidence={iss.get('confidence')!r}", file=sys.stderr)
        return False
    if iss.get("impact_direction") not in _VALID_GC_IMPACT:
        print(f"[WARN] global_context drop: impact_direction={iss.get('impact_direction')!r}", file=sys.stderr)
        return False
    if iss.get("direction") not in _VALID_GC_DIRECTION:
        print(f"[WARN] global_context drop: direction={iss.get('direction')!r}", file=sys.stderr)
        return False
    for field in (
        "title_en", "title_ko", "current_state_en", "current_state_ko",
        "summary_en", "summary_ko", "asymmetric_impact_en", "asymmetric_impact_ko",
        "market_insight_en", "market_insight_ko",
    ):
        if not isinstance(iss.get(field), str) or not str(iss.get(field)).strip():
            print(f"[WARN] global_context drop: {field} 누락", file=sys.stderr)
            return False
    src = (iss.get("source_hint") or "").lower()
    social = (
        "twitter", "x post", "x discussion", "reddit", "telegram",
        "discord", "4chan", "warhorizon", "me_observer_", "globalflash",
    )
    social_hit = next((p for p in social if p in src), None)
    if social_hit:
        print(
            f"[WARN] global_context drop: 소셜 소스 ({social_hit!r} in source_hint={src!r})",
            file=sys.stderr,
        )
        return False
    if iss.get("confidence") == "confirmed" and not any(o in src for o in _ACCEPTED_SOURCE_HINTS):
        # Downgrade rather than drop — keep signal, honest confidence
        print(
            f"[WARN] global_context: confirmed→developing (source={src!r})",
            file=sys.stderr,
        )
        iss["confidence"] = "developing"
    return True


def sanitize_global_context(data: dict) -> Optional[dict]:
    """Normalize Stage-1 payload: keep 0–3 valid issues; never invent filler."""
    if not isinstance(data, dict):
        return None
    raw_issues = data.get("issues")
    if not isinstance(raw_issues, list):
        return None
    cleaned: list[dict] = []
    for iss in raw_issues:
        if _issue_is_valid(iss):
            cleaned.append(iss)
        if len(cleaned) >= 3:
            break
    # Had issues but every item invalid → fail closed (retry), not silent empty success
    if len(raw_issues) > 0 and len(cleaned) == 0:
        print("[WARN] global_context: 모든 이슈 항목 검증 실패", file=sys.stderr)
        return None
    # Re-rank 1..n
    for i, iss in enumerate(cleaned, 1):
        iss["rank"] = i
    out = dict(data)
    out["issues"] = cleaned
    onu = out.get("ongoing_no_update")
    if onu is None:
        out["ongoing_no_update"] = []
    elif not isinstance(onu, list):
        out["ongoing_no_update"] = [str(onu)]
    if not isinstance(out.get("market_paradox_en"), str):
        out["market_paradox_en"] = str(out.get("market_paradox_en") or "")
    if not isinstance(out.get("market_paradox_ko"), str):
        out["market_paradox_ko"] = str(out.get("market_paradox_ko") or "")
    return out


def validate_global_context(data: dict) -> bool:
    """1차 Grok 응답 검증. Soft: invalid items dropped; empty issues allowed."""
    cleaned = sanitize_global_context(data)
    if cleaned is None:
        return False
    # Mutate in place so call_hermes_json keeps sanitized issues
    if isinstance(data, dict):
        data.clear()
        data.update(cleaned)
    return True


def build_global_context_prompt(
    now_kst: str,
    now_iso: str,
    evidence: list[dict] | None = None,
) -> str:
    evidence_block = format_stage1_evidence_block(list(evidence or []))
    n_ev = len(evidence or [])
    empty_rule = (
        "Returning issues=[] is allowed ONLY if the mechanical evidence list is empty AND "
        "live web search finds no market-moving item with an accepted source. "
        "If mechanical evidence has items, you SHOULD produce at least 1 issue grounded in them "
        "(or in live search confirmation of them). Dumping everything into ongoing_no_update "
        "while ignoring material headlines is a search-quality failure."
        if n_ev == 0
        else
        f"Mechanical evidence has {n_ev} headlines. Prefer 1–3 issues ranked by US-equity impact "
        "from that list (plus live search verification). issues=[] is a last resort and requires "
        "market_paradox_en explaining why none of the headlines re-price US equities."
    )
    return f"""You are a professional financial intelligence analyst with LIVE WEB SEARCH tools enabled.
Today is {now_kst} (KST) / {now_iso} (UTC).

{evidence_block}

━━━ TASK ━━━
Produce the top 1–3 global/macro/session issues that re-price (or are about to re-price) US equities
for THIS session. Rank by market impact evidence — NOT by a fixed topic checklist.

SEARCH PROCEDURE (do this before writing JSON):
1. Read MECHANICAL SEARCH EVIDENCE above.
2. Use web search tools to verify the top candidates and fill current_state / dates / sources.
   Suggested queries (adapt as needed; do not treat as mandatory slots):
   · "US stock market news last 48 hours"
   · "Federal Reserve OR FOMC OR yields OR CPI last 48 hours"
   · "oil OR energy markets last 48 hours"
   · "semiconductor OR export controls OR tariffs last 48 hours"
   · major watchlist earnings headlines if present in evidence
3. Rank candidates by: accepted-source recency + clear transmission to US equities.
4. Output JSON only.

For each issue provide:
(a) current_state — live state NOW
(b) direction — escalating | de-escalating | stable_elevated | stable_fading
(c) asymmetric_impact — only tickers with a clear mechanism (omit others)
(d) market_insight — what to watch today
(e) novelty_en/ko — what is new vs prior session; if ongoing with no delta but STILL market-relevant,
    write e.g. "No new delta; still priced risk because [mechanism + level]" — that is valid for tier=ongoing

SELECTION (first principles — no category quotas):
  · No daily reserved slot for any region/policy area.
  · Do not invent issues not supported by mechanical evidence or live search.
  · Prefer fewer strong issues over three weak rewrites.
  · {empty_rule}

SOURCE RULES:
  Accepted (may be confirmed): Reuters, Bloomberg, AP, BBC, FT, WSJ, NYT, CNBC, MarketWatch,
  White House / official agencies (BIS, SEC, Fed, DOJ, FTC), exchanges, court dockets.
  Developing: single credible outlet, still evolving.
  Never cite Twitter/X, Reddit, Telegram, Discord, anonymous blogs as source_hint.
  If only social: omit or find an accepted outlet.
  confidence=confirmed requires EVERY outlet listed in source_hint to be from the Accepted list above —
  do not mix an accepted outlet with a non-accepted one (e.g. general-news outlets like Al Jazeera, or
  industry/trade sites like EnergyNow) and still mark confirmed. If any cited outlet is outside the Accepted
  list, either drop it from source_hint (keep only accepted outlets) or downgrade confidence to developing.
  Example of the exact mistake to avoid: source_hint='cnbc, energynow' marked confirmed is WRONG — energynow
  is not in the Accepted list, so this must be either source_hint='cnbc' (confirmed) or confidence=developing.

WATCHLIST (impact mapping only when mechanism is clear):
TSM NVDA META TSLA PLTR MU CRWD AMZN MSFT AAPL GOOGL SPCX
RKLB CEG VST ALAB OKLO APP ANET NVO QBTS SOFI

Output raw JSON only (no markdown, no prose).
{{
  "fetched_at": "{now_iso}",
  "search_window": "48h",
  "issues": [
    {{
      "rank": 1,
      "tier": "breaking|ongoing",
      "category": "trade_tariff|geopolitical|central_bank|ai_regulation|earnings|market_move",
      "title_en": "factual status headline ≤80 chars",
      "title_ko": "현재 상태 중심 30자 이내",
      "current_state_en": "1-2 sentences: live state now",
      "current_state_ko": "지금 상태 1-2문장",
      "direction": "escalating|de-escalating|stable_elevated|stable_fading",
      "novelty_en": "what is new vs prior session (or why still market-relevant with no delta)",
      "novelty_ko": "전일 대비 새로움 또는 변화 없어도 시장 관련인 이유",
      "summary_en": "2-3 sentences: change, source, why it moves US equities",
      "summary_ko": "같은 내용 한국어",
      "source_hint": "Outlet + date, e.g. Reuters 2026-08-03",
      "confidence": "confirmed|developing|unverified",
      "asymmetric_impact_en": "TICKER: direction/reason; omit unrelated names",
      "asymmetric_impact_ko": "종목: 방향/이유",
      "impact_direction": "positive|negative|neutral|watch",
      "market_insight_en": "1 sentence investor action/watch trigger",
      "market_insight_ko": "투자자 행동/주시 1문장"
    }}
  ],
  "market_paradox_en": "VIX/rates vs risk mismatch if any; else empty string",
  "market_paradox_ko": "괴리 설명 또는 빈 문자열",
  "ongoing_no_update": ["short labels for quiet areas — not a substitute for ignoring material headlines"]
}}"""


def parse_global_context(text: str) -> dict:
    """1차 Grok 응답에서 글로벌 컨텍스트 JSON 추출. 실패 시 {} 반환."""
    if not text or not text.strip():
        return {}
    data = extract_json(text)
    if data is None:
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
   Bad: "RS=88" alone without explanation. This applies to EVERY occurrence of RS (or any technical term) across the entire output, not just the first mention — an earlier explanation elsewhere in the JSON does not excuse a later bare "RS=NN" in a different field. This includes fields outside the per-ticker analysis paragraphs (spotlight, today_checkpoints, sector_analysis leaders) — any bare 'RS' or 'RS=NN' anywhere in the JSON without the plain-language gloss is a violation.
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
   - market_structure: use the EXACT value from '구조=' field — 'UPTREND', 'DOWNTREND', 'DISTRIBUTION', 'ACCUMULATION', 'NEUTRAL', or 'UNKNOWN'. This label MUST be explicitly written in every ticker's analysis, including ACCUMULATION/NEUTRAL/UNKNOWN — omitting it is a critical error. Never write DOWNTREND for a stock whose data shows DISTRIBUTION, or omit the label for ACCUMULATION. They are fundamentally different conditions. A fluent analysis paragraph that never writes the word ACCUMULATION is treated as a missing required field, exactly like leaving market_structure blank — this is the single most common omission and must be checked per ticker, not assumed from earlier tickers.
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

감시 종목 22개 (기술적 데이터 + 소셜심리):
{symbol_block}

향후 실적 발표:
{earnings_block}

아래 JSON 스키마 그대로 출력하라 (raw JSON only, no markdown):

{{
  "headline_en": "One sentence — the most important LAST-SESSION / pre-open market story (HARD LIMIT ≤120 chars; target ≤100 chars to leave margin — after drafting, count the actual characters and shorten by trimming modifiers, not facts, if it exceeds 100). CAUSAL BINDING (structural): (1) For any named ticker, the primary clause must match the strongest same-snapshot evidence (large post/pre move, earnings calendar hit, or a global_context issue that maps that ticker with a non-unaffected impact). (2) NEVER attribute a ticker's move to a global_context issue whose asymmetric_impact marks that ticker unaffected/영향 없음. (3) When evidence conflicts, prefer measurable session evidence over narrative macro color; do not invent a causal 'because'.",
  "headline_ko": "지난 세션·프리마켓 기준 가장 중요한 한 줄 (30자 이내 — 하드 제한. 목표는 22자 이내로 여유를 두고 작성할 것 (하드 제한 30자에 임박한 26~30자 초안은 반려 대상이므로 목표부터 더 짧게 잡을 것); 작성 후 공백 포함 실제 글자 수를 한 글자씩 세어 30자 초과 시 수식어부터 줄일 것; 핵심 사실은 빼지 말 것). 구조 규칙: 헤드라인에 나온 종목의 원인 서술은 같은 스냅샷의 강한 증거(큰 애프터/프리 변동, 실적 캘린더, 또는 해당 종목을 unaffected가 아닌 방향으로 매핑한 global 이슈)와 일치해야 함. asymmetric_impact가 영향 없음인 이슈를 급등/급락 원인으로 쓰지 말 것. 원인 불명이면 사실(종목+변동)만 쓰고 원인을 지어내지 말 것.",
  "executive_bullets_en": [
    "Most important last-session / regime context with a concrete number or named move",
    "Best opportunity in the watchlist right now (session-anchored)",
    "Biggest risk today — only if material; if an ongoing risk has no delta, say so or omit"
  ],
  "executive_bullets_ko": [
    "지난 세션·레짐 핵심 (숫자 또는 구체 종목 움직임 포함)",
    "지금 가장 주목할 기회 (세션 앵커 포함, 구체 종목 가능)",
    "오늘 실질 리스크 — 반복 테마에 변화 없으면 '변화 없음' 또는 생략"
  ],
  "market_mood": {{
    "traffic_light": "green|yellow|red" — MUST be derived mechanically from {regime_score}: score≥80 → green, 40≤score<80 → yellow, score<40 → red. Do not use qualitative judgment to override this mapping.,
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
    "leaders_en": "Based on MACRO SIGNAL GROUPS (🟢 green = technically strong). HARD RULE: Stocks with DOWNTREND market_structure are NEVER technical leaders — do not name a DOWNTREND ticker as a leader even with a parenthetical tag attached; writing e.g. '[TICKER] (DOWNTREND)' as if it were a leader is itself a violation, not a valid format — this is a placeholder pattern only; never copy a specific example ticker like this literally into output. If a DOWNTREND stock benefits from a news theme (e.g. oil spike), it must appear ONLY inside the full caveat sentence: '[sector]: narrative interest from [theme], but technically in DOWNTREND — not a structural leader.' — never as a bare name+tag. Only stocks with UPTREND or neutral structure can be called leaders. FORMAT: every named ticker in this field MUST be immediately followed by its structure in parentheses, e.g. 'AAPL (UPTREND)' — naming a ticker as a leader with no parenthetical structure tag is itself a violation, since it hides a possible DOWNTREND stock being miscast as a leader.",
    "leaders_ko": "MACRO SIGNAL GROUPS의 🟢 녹색 신호 기반. 핵심 규칙: DOWNTREND 종목은 절대 기술적 리더가 아님. 뉴스 테마 수혜라도 '해당 섹터: [테마] 수혜 내러티브, 단 기술적 구조는 DOWNTREND — 진정한 섹터 리더 아님'으로 작성할 것.",
    "laggards_en": "Which are lagging and the simple reason why. FORMAT: every named ticker in this field MUST be immediately followed by its structure in parentheses using the EXACT market_structure/Stage value from the SniperBoard table (e.g., 'CRWD (ACCUMULATION)') — naming a ticker with no parenthetical structure tag is a format error, same as in leaders_en. Do NOT assign a sector/industry label to a ticker beyond what the SniperBoard table or provided data supports — if the lagging reason is a sector-wide de-rating rather than that ticker's own momentum/fundamentals, state the cause as given (e.g., valuation reset, sector de-rating) rather than inferring a category or driver not present in the inputs.",
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
      "analysis_en": "3-5 sentences flowing paragraph. (1) recent price level using EXACT 전일종가 from table; if 프리마켓 is available, mention today's pre-market direction with that exact value, (2) strength or vulnerability in plain language, explicitly naming market_structure as one of UPTREND/DOWNTREND/DISTRIBUTION/ACCUMULATION/NEUTRAL/UNKNOWN — ACCUMULATION is easy to skip since it sounds neutral, but it is MANDATORY like any other value — and stage2 data, (3) upside or downside using EMA/ATR anchors from 가격앵커, (4) social sentiment. All $ values must match table. Mention earnings ONLY if ≤14 days away using ABSOLUTE YYYY-MM-DD date only — NEVER 'in N days'/'tomorrow'/'next week'; otherwise omit earnings entirely.",
      "analysis_ko": "같은 내용 한국어 3-5문장. 전일종가는 테이블 값 그대로. 프리마켓 값이 있으면 '오늘 개장 전 $X(+Y%)' 형태로 사용. 없으면 오늘 방향 언급 금지. 실적은 14일 이내일 때만 YYYY-MM-DD 절대일로 언급('N일 후'/'내일' 금지), 그 외 완전 생략. 소셜 반응 자연스럽게 포함.",
      "sentiment_mood": "optimistic|cautious|neutral|fearful|euphoric — from the social data above. MUST be consistent with this ticker's same-session price change: if 전일등락 or 프리마켓 shows a decline ≥3%, mood MUST NOT be 'optimistic' or 'euphoric' unless a specific forward-looking catalyst (earnings beat, analyst upgrade) is explicitly stated in analysis_en/ko — assigning an upbeat mood to a sharply declining ticker without a stated catalyst is a critical error.",
      "sentiment_score": 0.0,
      "market_structure": "Copy the EXACT 구조= value verbatim (UPTREND|DOWNTREND|DISTRIBUTION|ACCUMULATION|NEUTRAL|UNKNOWN) for this ticker. This field is MANDATORY for all 22 watchlist tickers, including ACCUMULATION — a missing or blank value for a ticker whose table row has a 구조= value is a critical omission error.",
      "action": "buy|hold|watch|avoid"
    }}
  ],
  "today_checkpoints_en": [
    "Specific thing to watch — use exact price levels from the table, exact earnings dates from the table"
  ],
  "today_checkpoints_ko": [
    "오늘 주시할 포인트 — 가격은 테이블 기준, 실적일은 테이블 기준 정확한 날짜 명시"
  ],
  "earnings_alert_en": "List ONLY: (1) already-reported stocks with ABSOLUTE date: '[SYM] reported after US close on YYYY-MM-DD (est. EPS $X — verify actual)'; (2) stocks with earnings_date within 14 CALENDAR DAYS — format '[SYM] earnings YYYY-MM-DD'. NEVER relative timing ('in N days', 'tomorrow', 'next week', 'soon', 'D-n'). If days_until > 14 or N/A, EXCLUDE. Empty string if none.",
  "earnings_alert_ko": "다음만 나열: (1) 이미발표: '[심볼] YYYY-MM-DD 미국 장 마감 후 실적 발표됨 (EPS 추정 $X)'; (2) 실적일 14일 이내: '[심볼] 실적 YYYY-MM-DD'. 상대일 금지('N일 후'/'내일'/'다음 주'/'곧'/'D-n'). days_until>14이면 제외. 없으면 빈 문자열."
}}

REQUIREMENTS (first principles — evidence from THIS prompt only; no ticker/theme exception catalogs):

A. COVERAGE
- spotlight: 2–4 symbols from the 22 (mix of opportunity and risk)
- watchlist: ALL 22 in order TSM,NVDA,META,TSLA,PLTR,MU,CRWD,AMZN,MSFT,AAPL,GOOGL,SPCX,RKLB,CEG,VST,ALAB,OKLO,APP,ANET,NVO,QBTS,SOFI
- ⚠RECENT IPO rows: action=watch, market_structure=UNKNOWN, state data insufficiency; no buy/avoid from missing Stage2/RS
- BEFORE writing any analysis text: build an internal checklist of all 22 tickers paired with their EXACT 구조= value copied verbatim from the table. Use that checklist as the source of truth when writing each analysis paragraph and the market_structure field — tickers have been silently dropped or given an altered structure value in past runs; never paraphrase or infer a structure value from price action.

B. ACTION RULES (first match wins; driven only by table fields)
  1. avoid IF: (구조=DOWNTREND AND Stage2≤6) OR Stage2≤2 OR (⚠이미발표됨 AND post-market drop>10%)
     These three OR conditions are INDEPENDENT — apply each on its own regardless of market_structure. Stage2≤2 ALWAYS forces avoid even if 구조=ACCUMULATION/UPTREND/NEUTRAL; a high RS score never overrides this.
     EXCEPTION (both parts REQUIRED — never apply from RS alone): Stage2 must equal EXACTLY 7 AND RS≥70 with DOWNTREND → watch. RS≥70 with Stage2<7 does NOT qualify; DOWNTREND + Stage2≤6 still avoids no matter how high RS is.
  2. buy  IF: Stage2≥6 AND RS≥70 AND 구조≠DOWNTREND AND mood in (optimistic, euphoric)
  3. hold IF: Stage2≥5 and solid technical position (near entry / breakout / EMA support)
  4. watch otherwise
  DISTRIBUTION ≠ DOWNTREND: DISTRIBUTION + Stage2≥4 → typically watch; Stage2≤2 still avoid (rule 1) — NOT a soft guideline: DISTRIBUTION + Stage2≤2 MUST be action=avoid; do not let the 'DISTRIBUTION typically watch' default silently override the Stage2≤2 avoid trigger for that ticker.
  ⚠이미발표됨 post-market drop 5–10%: max action=watch; >10%: avoid.
  RS<30: downgrade one level but never force avoid by RS alone.

C. BINDING / ANTI-HALLUCINATION
  1. Prices, EMAs, ATR, %: only authoritative table / 가격앵커 / MACRO BINDING TABLE. No invented levels.
     Support/resistance within ±25% of 전일종가; prefer EMA21/50/200; no moves beyond ~3×ATR14.
  2. 전일등락 is YESTERDAY — not "오늘". TODAY direction only from 프리마켓; if N/A, do not invent direction.
  3. 구조= value: copy EXACT label into analysis (and market_structure field). Never swap DISTRIBUTION↔DOWNTREND.
     Korean gloss: UPTREND=상승 추세, DOWNTREND=하락 추세, DISTRIBUTION=분배 구간, ACCUMULATION=집적 구간.
  4. Stage2≤2 with 구조=UPTREND (or Stage2≥7 with DOWNTREND): this is a DATA CONFLICT — the analysis's first sentence must explicitly flag it (e.g. '데이터 상충: Stage2=1이지만 구조=UPTREND로 표시됨'); do not silently pick one value and ignore the other. CRITICAL: flagging the conflict does NOT change the action — Section B's rules still apply mechanically to the raw field values (e.g. Stage2≤2 still forces action=avoid per rule B.1 even when 구조=UPTREND is flagged as conflicting); never let the flagged/softer value override the action decision. Check this for EVERY one of the 22 tickers, not just the obvious ones. Separately: if 프리마켓 or 전일등락 shows a large same-session move (>7%) while 구조=DOWNTREND, and earnings_block shows a report within the prior 5 trading days for that ticker, do NOT describe it as merely 'weak'/'laggard'/'under pressure' — state both the technical structure label AND the earnings-driven price move together rather than letting one silently override the other in the prose framing.
  5. Earnings: only table dates; ≤14 calendar days in analysis/spotlight/earnings_alert. No relative "N days".
     For ⚠이미발표됨: do NOT claim beat/miss/상회/하회/split — only post-market reaction + est. EPS verify note.
  6. sentiment_score: copy composite_score from social data; mood consistent with session move (≥3% drop → not optimistic/euphoric unless analysis states a concrete forward catalyst from provided fields).
  7. External metrics/events (ARR%, product names, contracts, non-listed IPOs, military exercise codenames):
     FORBIDDEN unless in tables or global_context with source_hint. Use 투자자반응 fields for social context only.
  8. Sector leaders: MACRO SIGNAL GROUPS + 구조. DOWNTREND is never a technical leader. Before output, check every ticker named in leaders_en/leaders_ko against its 구조= value: any DOWNTREND ticker must appear ONLY inside the full caveat sentence ('narrative interest ... but technically in DOWNTREND — not a structural leader'), never as a bare name or name+tag alongside other leaders.
  9. BTC: if table shows 1D≤-5% or 5D≤-10%, include in executive_bullets as macro risk (binding numbers only).
 10. Causal binding (any ticker): headline/primary clause must not attribute a ticker move to a global issue
     that marks that ticker unaffected. Prefer same-snapshot session evidence (pre/post %, earnings alert).
     Do not invent causal "because/에" without support in this prompt's evidence.
 11. Confidence language for any cited global issue must match its confidence tag (developing → hedge).

SELF-CHECK (fix before output):
  □ Prices / pre-market / EMA / DXY / BTC / VIX / TNX match binding tables exactly?
  □ ⚠이미발표됨: no beat/miss/상회/하회/split language?
  □ every occurrence of 'RS' (or any technical term) anywhere in the JSON — including spotlight, today_checkpoints, sector_analysis leaders — carries its plain-language gloss, not a bare 'RS=NN'?
  □ action rules satisfied — each action recomputed against rule B.1's three independent OR conditions (구조=DOWNTREND AND Stage2≤6; OR Stage2≤2; OR post-market drop>10%), not just the Stage2≤2 case? For any ticker flagged as a Stage2/구조 DATA CONFLICT (rule C.4), re-verify the action still came from rule B.1's raw field values and was not softened toward 'watch' because the conflict was flagged?
  □ every ticker in leaders_en/leaders_ko re-checked against 구조=: any DOWNTREND ticker present without the full caveat sentence ('narrative interest ... but technically in DOWNTREND — not a structural leader') is a critical error — fix before output?
  □ every watchlist analysis states exact 구조= / market_structure; no synonym swaps? ACCUMULATION is the most commonly skipped value — verify each ACCUMULATION ticker's analysis text literally contains the word 'ACCUMULATION'. Before finalizing output, re-scan the draft JSON against the checklist built in section A and confirm every ticker's market_structure field is filled with its exact 구조= value (including ACCUMULATION) — do not submit output with this check unresolved.
  □ every ticker symbol appearing ANYWHERE in the JSON output — headline, executive_bullets, spotlight, narrative prose, not only leaders/watchlist fields — carries its "(LABEL)" structure tag per rule 7? List every ticker string present in the draft output and confirm each occurrence is tagged before submitting.
  □ Stage2 vs structure conflicts flagged inline when present?
  □ headline_en ≤120 chars; headline_ko ≤30 chars; causal binding consistent with asymmetric_impact + session evidence?
  □ headline_ko: count actual characters including spaces one by one right now; if the count exceeds 30, shorten before output — do not submit an uncounted draft.
  □ every technical acronym/abbreviation (RS, EPS, IV, PPI, etc.) expanded in parentheses on first use per rule 8?
  □ earnings only ≤14d and absolute YYYY-MM-DD; no training-memory metrics?
  □ developing global issues hedged in big_picture / bullets?

- Raw JSON only. No prose before or after."""


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

    # ── 1차 호출: 기계적 RSS 증거 + 웹 검색 Stage-1 ─────────────────────────
    global_ctx: dict = {}
    print("[INFO] Stage-1 기계적 검색 증거(RSS) 수집 중...")
    stage1_evidence = fetch_stage1_search_evidence()
    print(f"[INFO] Stage-1 RSS 헤드라인 {len(stage1_evidence)}건")
    global_context_prompt = build_global_context_prompt(
        now_kst, now_iso, evidence=stage1_evidence,
    )
    print(
        f"[INFO] Grok 1차 호출: 글로벌 컨텍스트 (toolsets={HERMES_STAGE1_TOOLSETS!r}, "
        f"timeout={CALL_TIMEOUT_GLOBAL}s)..."
    )
    _gc_raw, _gc_parsed = call_hermes_json(
        global_context_prompt,
        timeout=CALL_TIMEOUT_GLOBAL,
        validator=validate_global_context,
        toolsets=HERMES_STAGE1_TOOLSETS or None,
    )
    if _gc_parsed is not None:
        global_ctx = _gc_parsed
        # Attach mechanical evidence meta for debugging / downstream verify (not user-facing)
        global_ctx["_stage1_evidence_n"] = len(stage1_evidence)
        global_ctx["_stage1_evidence_sources"] = sorted({
            str(x.get("source") or x.get("feed_id") or "")
            for x in stage1_evidence
            if x.get("source") or x.get("feed_id")
        })
        issues_count = len(global_ctx.get("issues") or [])
        if issues_count > 0:
            print(f"[INFO] 글로벌 이슈 {issues_count}개 수집됨")
            for iss in global_ctx.get("issues") or []:
                print(
                    f"  - [{iss.get('tier')}/{iss.get('category')}] "
                    f"{iss.get('title_en') or iss.get('title_ko')} "
                    f"| {iss.get('source_hint')} | {iss.get('confidence')}"
                )
        else:
            print(
                "[WARN] 글로벌 컨텍스트: 이슈 0개 "
                f"(RSS={len(stage1_evidence)}, ongoing_no_update={global_ctx.get('ongoing_no_update')})",
                file=sys.stderr,
            )
    else:
        print("[WARN] 글로벌 컨텍스트 최종 실패 — fallback으로 계속 진행", file=sys.stderr)
        if stage1_evidence:
            # Minimal non-LLM fallback: surface that evidence existed so Stage-2 is not blind
            global_ctx = {
                "fetched_at": now_iso,
                "search_window": "48h",
                "issues": [],
                "market_paradox_en": (
                    f"Stage-1 model parse failed with {len(stage1_evidence)} RSS headlines available; "
                    "briefing proceeds without structured global issues."
                ),
                "market_paradox_ko": (
                    f"Stage-1 파싱 실패(RSS {len(stage1_evidence)}건 확보). 구조화 글로벌 이슈 없이 진행."
                ),
                "ongoing_no_update": [],
                "_stage1_evidence_n": len(stage1_evidence),
                "fallback": True,
            }

    # ── 2차 호출: 아침 브리핑 생성 (글로벌 컨텍스트 주입) ───────────────────
    prompt = build_prompt(data, now_kst, global_ctx)
    print("[INFO] Grok 2차 호출: 아침 브리핑 생성 중 (최대 3분 소요)...")
    _, parsed = call_hermes_json(prompt, timeout=CALL_TIMEOUT, validator=validate_briefing)
    if parsed is None:
        print("[ERROR] 브리핑 최종 실패 — 종료", file=sys.stderr)
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
