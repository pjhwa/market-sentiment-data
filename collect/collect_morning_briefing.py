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
CALL_TIMEOUT_GLOBAL = int(os.environ.get("HERMES_TIMEOUT_GLOBAL", "90"))

_VALID_GC_CATEGORIES = {"trade_tariff", "geopolitical", "central_bank", "ai_regulation"}
_VALID_GC_TIERS = {"breaking", "ongoing"}
_VALID_GC_CONFIDENCE = {"confirmed", "developing", "unverified"}
_VALID_GC_IMPACT = {"positive", "negative", "neutral", "watch"}

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


def fetch_all_data() -> dict:
    """SniperBoard API + 저장된 JSON 파일에서 전체 시장 데이터 수집."""
    print("[INFO] 시장 데이터 수집 중...")

    regime = _api_get("/regime") or {}
    dd = _api_get("/distribution-days") or {}
    macro = _api_get("/macro") or {}
    watchlist = _api_get("/watchlist") or {}

    # 21종목 전체 일봉 상세 (스퀴즈/조정 분석용)
    symbol_detail: dict = {}
    for sym, _, _ in ALL_SYMBOLS:
        daily = _api_get("/daily", {"symbol": sym})
        if daily and daily.get("stage2"):
            s2 = daily["stage2"]
            checks = s2.get("checks", {})
            price = s2.get("latest_close", 0)
            entry = s2.get("entry", 0)
            symbol_detail[sym] = {
                "price":                  round(price, 2),
                "stage2_score":           s2.get("score", 0),
                "rs_score":               round(s2.get("rs_score", 50), 1),
                "market_structure":       s2.get("market_structure", "NEUTRAL"),
                "monthly_phase":          s2.get("monthly_phase", "UNKNOWN"),
                "ema200_slope":           round(s2.get("ema200_slope", 0), 4),
                "pct_from_52w_high":      round(s2.get("pct_from_52w_high", 0), 1),
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

    sentiment = _load_json("sentiment/latest.json")
    earnings = _load_json("earnings/latest.json")

    return {
        "regime":        regime,
        "distribution":  dd,
        "macro":         macro,
        "watchlist":     watchlist.get("watchlist", []),
        "symbol_detail": symbol_detail,
        "sentiment":     sentiment,
        "earnings":      earnings,
    }


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
        sent_reason = sent.get('key_reason_en') or sent.get('key_reason', '')
        sent_ko = sent.get('key_reason_ko', '')

        lines.append(
            f"{sym} ({company}) [T{tier}]\n"
            f"  Stage2점수={d['stage2_score']}/7  시장상대강도RS={d['rs_score']}  "
            f"구조={d['market_structure']}  월봉추세={d['monthly_phase']}\n"
            f"  현재가=${d['price']}  52주고점대비={d['pct_from_52w_high']}%  "
            f"돌파목표대비={vs_entry}  최근눌림={d['pullback_pct']}%\n"
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
        for field in ("title_en", "title_ko", "summary_en", "summary_ko"):
            if not isinstance(iss.get(field), str) or not iss[field]:
                print(f"[WARN] global_context: {field} 누락", file=sys.stderr)
                return False
    return True


def build_prompt(data: dict, now_kst: str) -> str:
    regime = data["regime"]
    dd = data["distribution"]
    spy_dd = dd.get("spy", {})
    qqq_dd = dd.get("qqq", {})
    market_sent = data["sentiment"].get("market", {})
    slot = data["sentiment"].get("slot", "unknown")
    regime_label = regime.get("regime", "UNKNOWN")
    regime_score = regime.get("total", "N/A")
    comps = regime.get("components", {})

    symbol_block = _format_symbol_block(data)
    macro_block = _format_macro_block(data["macro"])
    earnings_block = _format_earnings_block(data["earnings"])

    return f"""You are a friendly stock market expert writing a morning briefing for Korean retail investors who are NOT finance professionals.
Today is {now_kst} (KST).

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
      "why_en": "2-3 sentences: what makes this stock worth watching TODAY specifically. Catalyst, setup, or risk. Reference price levels.",
      "why_ko": "오늘 이 종목이 특별히 주목받는 이유 2-3문장. 구체적 가격대나 사건 포함.",
      "watch_level_en": "Specific price to watch — e.g. 'Break above $X triggers momentum; drop below $Y is a warning'",
      "watch_level_ko": "주시할 가격 레벨 — '$X 돌파 시 / $Y 하회 시' 형태로 구체적으로."
    }}
  ],
  "watchlist": [
    {{
      "symbol": "TICKER",
      "company": "Company Name",
      "tier": 1,
      "analysis_en": "3-5 sentences as a flowing paragraph. Cover: (1) how price has been moving recently, (2) whether it looks strong or vulnerable right now — explained in non-jargon terms, (3) the main upside potential OR downside risk (whichever is more relevant), (4) what social media investors are saying about this stock. Write naturally — no sub-headers, no bullet points inside this field.",
      "analysis_ko": "같은 내용을 한국어로 3-5문장 자연스럽게 작성. 기술 용어가 나오면 바로 괄호로 설명. 소셜 투자자들의 반응도 자연스럽게 녹여낼 것. 읽는 사람이 '아 이 종목은 지금 이런 상황이구나'를 바로 알 수 있게.",
      "sentiment_mood": "optimistic|cautious|neutral|fearful|euphoric — from the social data above",
      "sentiment_score": 0.0,
      "action": "buy|hold|watch|avoid"
    }}
  ],
  "today_checkpoints_en": [
    "Specific thing to watch today — event, price level, or catalyst"
  ],
  "today_checkpoints_ko": [
    "오늘 눈여겨볼 구체적 포인트 — 이벤트, 가격대, 또는 촉매"
  ],
  "earnings_alert_en": "One sentence about earnings releases in next 7 days that could move watchlist stocks.",
  "earnings_alert_ko": "향후 7일 내 감시종목 실적 발표 알림 한 문장."
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
- Raw JSON only. No prose before or after."""


def call_hermes(prompt: str) -> str | None:
    cmd = [HERMES_CMD, "-z", prompt]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    for attempt in range(1 + HERMES_RETRY):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT, env=env)
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
    prompt = build_prompt(data, now_kst)

    print("[INFO] Grok 호출 중 (최대 3분 소요)...")
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
        "schema_version": "1.0",
        "slot": "morning",
        **parsed,
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
