#!/usr/bin/env python3
"""
SniperBoard 소셜 심리 수집기 (계층 1, v1.2)
① SniperBoard에서 중립적 가격 맥락 fetch (방향 제거)
② 맥락을 끼워 넣어 Grok에 질의 (방향 단어 가드 통과 필수)
③ 심리 수집 후 가격 방향과 비교해 divergence 계산
④ price_context + divergence 포함해 latest.json 빌드 → push
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _find_hermes() -> str:
    """HERMES_CMD 환경변수 → PATH 자동탐색 → 플랫폼별 기본 경로 순으로 탐색."""
    if val := os.environ.get("HERMES_CMD"):
        return val
    if found := shutil.which("hermes"):
        return found
    candidates = [
        Path.home() / ".local/bin/hermes",       # Linux (pip install)
        Path("/opt/homebrew/bin/hermes"),          # macOS Apple Silicon
        Path("/usr/local/bin/hermes"),             # macOS Intel / Linux
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return str(Path.home() / ".local/bin/hermes")

from collect.git_utils import commit_and_push
from collect.price_context import (
    fetch_close_direction,
    fetch_market_context,
    fetch_price_context,
)

# ── 설정 ──────────────────────────────────────────────────────────────────────
REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
HERMES_CMD = _find_hermes()
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT = int(os.environ.get("HERMES_TIMEOUT", "120"))

# TIER1: 빅테크/대형주 — 개별 심층 분석, 하루 2회 (pre_open + post_close)
TIER1_WATCHLIST = [
    ("TSM",   "TSMC"),
    ("NVDA",  "Nvidia"),
    ("META",  "Meta Platforms"),
    ("TSLA",  "Tesla"),
    ("PLTR",  "Palantir"),
    ("MU",    "Micron"),
    ("CRWD",  "CrowdStrike"),
    ("AMZN",  "Amazon"),
    ("MSFT",  "Microsoft"),
    ("AAPL",  "Apple"),
    ("GOOGL", "Alphabet / Google"),
    ("SPCX",  "SpaceX"),
]

# TIER2: 모멘텀/테마주 — 배치 묶음 분석, 하루 1회 (post_close 전용)
TIER2_WATCHLIST = [
    ("RKLB",  "Rocket Lab"),
    ("CEG",   "Constellation Energy"),
    ("VST",   "Vistra Energy"),
    ("ALAB",  "Astera Labs"),
    ("OKLO",  "Oklo"),
    ("APP",   "AppLovin"),
    ("ANET",  "Arista Networks"),
    ("NVO",   "Novo Nordisk"),
    ("QBTS",  "D-Wave Quantum"),
    ("SOFI",  "SoFi"),
]

SENTIMENT_SCORE_MAP = {
    "very_fearful": -2,
    "fearful": -1,
    "neutral": 0,
    "optimistic": 1,
    "euphoric": 2,
}

# 오염 방지선: 프롬프트에 이 단어가 들어가면 AssertionError
_PROMPT_DIRECTION_PATTERN = re.compile(
    r"\b(up|down|bullish|bearish|buy|sell|strong|weak|rally|drop|surge|crash|rose|fell"
    r"|올랐|떨어|급등|급락|상승|하락)\b",
    re.IGNORECASE,
)

# ── 슬롯 감지 ──────────────────────────────────────────────────────────────────

def detect_slot(now: datetime) -> str:
    """UTC 시각으로 수집 슬롯 판별. SENTIMENT_SLOT 환경변수로 오버라이드 가능.
    09:00–17:59 UTC → pre_open (미국 장 개장 전)
    그 외 → post_close (미국 장 마감 후)
    """
    override = os.environ.get("SENTIMENT_SLOT", "").strip()
    if override in ("pre_open", "post_close"):
        return override
    if 9 <= now.hour < 18:
        return "pre_open"
    return "post_close"


def history_filename(date_str: str, slot: str) -> Path:
    return REPO_PATH / "sentiment" / "history" / f"{date_str}_{slot}.json"


# ── Grok 프롬프트 빌더 ─────────────────────────────────────────────────────────

_NEAR_KEY_LEVEL_HUMAN = {
    "near_52w_high": "near its 52-week high",
    "near_52w_low": "near its 52-week low",
    "none": "not near any key level",
}

_SYMBOL_PROMPT_BASE = """\
You are a data extraction tool, not an analyst. Read current public X (Twitter) posts \
about {COMPANY} (ticker: {SYMBOL}) and report the crowd's sentiment. \
Search using both the company name "{COMPANY}" and the ticker "${SYMBOL}" to capture \
all relevant discussion. Respond with ONE JSON object ONLY — no prose, no code fences.
{CONTEXT_BLOCK}
Schema (exact enums):
{{
  "symbol": "{SYMBOL}",
  "sentiment": one of ["very_fearful","fearful","neutral","optimistic","euphoric"],
  "trend_vs_yesterday": one of ["cooling","stable","heating"],
  "mention_volume": one of ["low","normal","elevated","surging"],
  "key_reason_en": "one short sentence in English",
  "key_reason_ko": "한국어로 한 문장",
  "bot_suspected": one of ["yes","no","unclear"],
  "confidence": one of ["high","med","low"],
  "top_news": {{"headline_en": "original English headline or most-shared post caption", "headline_ko": "한국어 제목 또는 번역", "summary_en": "1-2 sentence English summary", "summary_ko": "1-2문장 한국어 요약", "source": "출처(Bloomberg/@username 등)"}} or null if no clear top story
}}

Rules:
- Determine sentiment ONLY from real posts, never inferred from the price context.
- No invented percentages. Categorical enums only.
- Thin/noisy sample → confidence "low".
- top_news: pick the single most-shared or most-discussed news/post about this ticker. Provide headline and summary in BOTH English (_en) and Korean (_ko). If nothing stands out, set it to null.
- Output raw JSON only."""

_CONTEXT_BLOCK_TEMPLATE = """\

CONTEXT (use ONLY to focus your search and judge sarcasm — do NOT let it decide the sentiment):
{CONTEXT_LINES}
IMPORTANT about the context:
- The context tells you WHERE to look and helps you tell sincere posts from sarcastic ones.
- It does NOT tell you whether sentiment is positive or negative. You must determine that \
ONLY from the actual posts you read. Do not assume a big move means a particular mood.

"""

_TIER2_BATCH_PROMPT_HEADER = """\
You are a data extraction tool, not an analyst. For each ticker listed below, read current \
public X (Twitter) posts and report the crowd's sentiment. Search using both the company \
name and the ticker symbol. Respond with ONE JSON ARRAY only — one object per ticker, \
in the exact same order as listed. No prose, no code fences.

Tickers:
{TICKER_LINES}

Schema for each object (exact enums):
{{
  "symbol": "TICKER",
  "sentiment": one of ["very_fearful","fearful","neutral","optimistic","euphoric"],
  "trend_vs_yesterday": one of ["cooling","stable","heating"],
  "mention_volume": one of ["low","normal","elevated","surging"],
  "key_reason_en": "one short sentence in English",
  "key_reason_ko": "한국어로 한 문장",
  "bot_suspected": one of ["yes","no","unclear"],
  "confidence": one of ["high","med","low"],
  "top_news": {{"headline_en": "...", "headline_ko": "...", "summary_en": "...", "summary_ko": "...", "source": "..."}} or null
}}

Rules:
- Determine sentiment ONLY from real posts, never infer from price direction.
- No invented percentages. Categorical enums only.
- Thin/noisy sample → confidence "low".
- top_news: pick the single most-shared/discussed news for that ticker in BOTH _en and _ko. null if nothing stands out.
- Output raw JSON array only."""


def build_tier2_batch_prompt(watchlist: list[tuple[str, str]]) -> str:
    ticker_lines = "\n".join(
        f"- {symbol} ({company})" for symbol, company in watchlist
    )
    return _TIER2_BATCH_PROMPT_HEADER.replace("{TICKER_LINES}", ticker_lines)


MARKET_PROMPT = """\
You are a data extraction tool, not an analyst. Look at current public X (Twitter) \
posts about the US equity market broadly (S&P 500, rates, recession) and respond with ONE JSON object ONLY \
— no prose, no code fences, no explanation before or after.

Schema (use these exact enum values):
{
  "sentiment": one of ["very_fearful","fearful","neutral","optimistic","euphoric"],
  "trend_vs_yesterday": one of ["cooling","stable","heating"],
  "extreme_flag": one of ["none","extreme_fear","extreme_greed"],
  "key_reason_en": "one short sentence in English",
  "key_reason_ko": "한국어로 한 문장",
  "confidence": one of ["high","med","low"],
  "top_news": {"headline_en": "original English headline or most-shared post caption", "headline_ko": "한국어 제목 또는 번역", "summary_en": "1-2 sentence English summary", "summary_ko": "1-2문장 한국어 요약", "source": "출처(Bloomberg/@username 등)"} or null if no clear top story
}

Rules:
- Do NOT invent precise percentages. Use only the categorical enums above.
- If the sample seems thin or very noisy, set confidence to "low".
- If you cannot determine a field, use "neutral"/"stable"/"none" and lower confidence.
- top_news: pick the single most-shared or most-discussed market news/macro post. Provide headline and summary in BOTH English (_en) and Korean (_ko). If nothing stands out, set it to null.
- Output the raw JSON object and nothing else."""


def build_prompt(symbol: str, company: str, ctx: dict) -> str:
    """가격 맥락을 중립 단서로만 끼워 넣어 프롬프트를 생성.
    ⛔ 생성된 프롬프트에 방향 단어가 없는지 가드(assert)를 통과해야 한다.
    """
    if not ctx.get("available"):
        # SniperBoard 응답 없으면 맨눈 폴백 (CONTEXT 블록 생략)
        context_block = "\n"
    else:
        lines = []

        if ctx.get("abnormal_move"):
            lines.append(
                "- This stock had an UNUSUALLY LARGE price move today (size only; direction unknown)."
            )

        vol = ctx.get("volume_ratio")
        if vol is not None:
            lines.append(f"- Today's volume was about {vol}x its recent average.")

        level = ctx.get("near_key_level", "none")
        level_human = _NEAR_KEY_LEVEL_HUMAN.get(level, "not near any key level")
        lines.append(f"- Price is currently {level_human}.")

        context_block = _CONTEXT_BLOCK_TEMPLATE.replace(
            "{CONTEXT_LINES}", "\n".join(lines) + "\n"
        )

    prompt = (
        _SYMBOL_PROMPT_BASE
        .replace("{SYMBOL}", symbol)
        .replace("{COMPANY}", company)
        .replace("{CONTEXT_BLOCK}", context_block)
    )

    # 오염 방지선 기계 검증
    m = _PROMPT_DIRECTION_PATTERN.search(prompt)
    assert m is None, (
        f"⛔ 방향 단어 오염 감지 [{symbol}]: '{m.group()}' — 프롬프트를 확인하라."
    )

    return prompt


# ── composite_score 계산 ───────────────────────────────────────────────────────

def compute_symbol_composite(
    sentiment_score: int,
    confidence: str,
    bot_suspected: str,
    mention_volume: str,
    divergence: str,
    trend_vs_yesterday: str,
    intraday_shift: str | None,
) -> float:
    """수집된 모든 신호를 결합해 -2.0~+2.0 범위의 복합 점수를 계산.

    raw sentiment_score는 Grok 응답을 정수로만 반환하지만,
    신뢰도·봇의심·언급량·가격다이버전스·추세 방향을 반영해 실제 시장 상황에 가까운 수치를 만든다.
    """
    conf_mult = {"high": 1.0, "med": 0.85, "low": 0.5}.get(confidence, 0.85)
    # 봇 의심 글이 많으면 신호 강도를 약하게
    bot_mult = {"yes": 0.6, "unclear": 0.85, "no": 1.0}.get(bot_suspected, 1.0)
    # 언급량이 낮으면 신호 희박 → 약화, 급증이면 증폭
    vol_mult = {"low": 0.7, "normal": 1.0, "elevated": 1.2, "surging": 1.3}.get(mention_volume, 1.0)
    # bullish_divergence: 가격↓인데 심리↑ → 낙관 과신 억제
    # bearish_divergence: 가격↑인데 심리↓ → 공포 과신 억제
    div_adj = {"bullish_divergence": -0.5, "bearish_divergence": 0.5, "aligned": 0.0, "none": 0.0}.get(divergence, 0.0)
    trend_adj = {"cooling": -0.3, "stable": 0.0, "heating": 0.3}.get(trend_vs_yesterday, 0.0)
    shift_adj = {"cooling": -0.2, "stable": 0.0, "heating": 0.2}.get(intraday_shift or "stable", 0.0)

    score = sentiment_score * conf_mult * bot_mult * vol_mult + div_adj + trend_adj + shift_adj
    return round(max(-2.0, min(2.0, score)), 1)


def compute_market_composite(
    sentiment_score: int,
    confidence: str,
    extreme_flag: str,
    trend_vs_yesterday: str,
    intraday_shift: str | None,
) -> float:
    """시장 전체 composite_score 계산."""
    conf_mult = {"high": 1.0, "med": 0.85, "low": 0.5}.get(confidence, 0.85)
    # 극단적 공포/탐욕은 신호 강도 증폭
    extreme_mult = 1.3 if extreme_flag in ("extreme_fear", "extreme_greed") else 1.0
    trend_adj = {"cooling": -0.3, "stable": 0.0, "heating": 0.3}.get(trend_vs_yesterday, 0.0)
    shift_adj = {"cooling": -0.2, "stable": 0.0, "heating": 0.2}.get(intraday_shift or "stable", 0.0)

    score = sentiment_score * conf_mult * extreme_mult + trend_adj + shift_adj
    return round(max(-2.0, min(2.0, score)), 1)


# ── divergence 계산 ────────────────────────────────────────────────────────────

def compute_divergence(price_dir: str, sentiment_score: int) -> str:
    """가격 방향과 심리 점수를 비교해 divergence 판정.
    이 함수는 Grok 호출이 끝난 뒤에만 호출해야 한다. price_dir는 절대 프롬프트로 흘리지 말 것.
    """
    if price_dir == "up" and sentiment_score < 0:
        return "bearish_divergence"
    if price_dir == "down" and sentiment_score > 0:
        return "bullish_divergence"
    if price_dir in ("up", "down", "flat") and sentiment_score != 0:
        return "aligned"
    return "none"


# ── intraday_shift 계산 ────────────────────────────────────────────────────

def compute_intraday_shift(prev_score: int, curr_score: int) -> str:
    if curr_score > prev_score:
        return "heating"
    if curr_score < prev_score:
        return "cooling"
    return "stable"


def load_pre_open_scores(path: Path) -> dict:
    """pre_open 스냅샷에서 sentiment_score를 추출.
    반환: {"market": int|None, "symbols": {symbol: score}}
    파일 없거나 파싱 실패 시 빈 구조 반환.
    """
    result: dict = {"market": None, "symbols": {}}
    if not path.exists():
        print(f"[INFO] pre_open 파일 없음 (intraday_shift=null): {path}", file=sys.stderr)
        return result
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        market = data.get("market") or {}
        result["market"] = market.get("sentiment_score")
        for sym in data.get("symbols") or []:
            if sym.get("symbol") and sym.get("sentiment_score") is not None:
                result["symbols"][sym["symbol"]] = sym["sentiment_score"]
    except Exception as e:
        print(f"[WARN] pre_open 파일 파싱 실패 ({e}), intraday_shift=null", file=sys.stderr)
    return result


# ── hermes 호출 ────────────────────────────────────────────────────────────────

HERMES_RETRY = int(os.environ.get("HERMES_RETRY", "1"))  # 타임아웃 시 재시도 횟수


def call_hermes(prompt: str) -> str | None:
    cmd = [HERMES_CMD, "-z", prompt]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}

    for attempt in range(1 + HERMES_RETRY):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=CALL_TIMEOUT,
                env=env,
            )
            if result.returncode != 0:
                print(f"[ERROR] hermes 비정상 종료 (rc={result.returncode}): {result.stderr[:200]}", file=sys.stderr)
                return None
            return result.stdout
        except subprocess.TimeoutExpired:
            remaining = HERMES_RETRY - attempt
            if remaining > 0:
                print(f"[WARN] hermes 타임아웃 ({CALL_TIMEOUT}초 초과) — 재시도 {remaining}회 남음", file=sys.stderr)
            else:
                print(f"[ERROR] hermes 타임아웃 — 재시도 소진, skip", file=sys.stderr)
                return None
        except FileNotFoundError:
            print(
                f"[ERROR] hermes 명령을 찾을 수 없음: {HERMES_CMD}. "
                "PATH를 확인하거나 HERMES_CMD 환경변수로 절대경로를 지정하세요.",
                file=sys.stderr,
            )
            return None
    return None


# ── JSON 파싱 / 검증 ──────────────────────────────────────────────────────────

def extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 블록을 찾을 수 없음. 응답: {text[:300]!r}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 파싱 실패: {e}. 원문: {match.group()[:300]!r}", file=sys.stderr)
        return None


def extract_json_array(text: str) -> list | None:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 배열을 찾을 수 없음. 응답: {text[:300]!r}", file=sys.stderr)
        return None
    try:
        result = json.loads(match.group())
        if not isinstance(result, list):
            print(f"[ERROR] JSON 배열이 아님: {type(result)}", file=sys.stderr)
            return None
        return result
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 배열 파싱 실패: {e}. 원문: {match.group()[:300]!r}", file=sys.stderr)
        return None


def validate_symbol_fields(data: dict, symbol: str) -> bool:
    required_enums = {
        "sentiment": list(SENTIMENT_SCORE_MAP.keys()),
        "trend_vs_yesterday": ["cooling", "stable", "heating"],
        "mention_volume": ["low", "normal", "elevated", "surging"],
        "bot_suspected": ["yes", "no", "unclear"],
        "confidence": ["high", "med", "low"],
    }
    for field, valid_values in required_enums.items():
        if field not in data:
            print(f"[WARN] {symbol}: 필드 누락 — {field}", file=sys.stderr)
            return False
        if data[field] not in valid_values:
            print(f"[WARN] {symbol}: {field}={data[field]!r} 허용값 아님", file=sys.stderr)
            return False
    for field in ("key_reason_en", "key_reason_ko"):
        if field not in data or not isinstance(data[field], str):
            print(f"[WARN] {symbol}: {field} 누락 또는 타입 오류", file=sys.stderr)
            return False
    return True


def validate_market_fields(data: dict) -> bool:
    required_enums = {
        "sentiment": list(SENTIMENT_SCORE_MAP.keys()),
        "trend_vs_yesterday": ["cooling", "stable", "heating"],
        "extreme_flag": ["none", "extreme_fear", "extreme_greed"],
        "confidence": ["high", "med", "low"],
    }
    for field, valid_values in required_enums.items():
        if field not in data:
            print(f"[WARN] market: 필드 누락 — {field}", file=sys.stderr)
            return False
        if data[field] not in valid_values:
            print(f"[WARN] market: {field}={data[field]!r} 허용값 아님", file=sys.stderr)
            return False
    return True


def validate_top_news(data: dict | None) -> bool:
    """top_news 구조 검증. None은 허용(optional 필드). v2.0: _en/_ko 필드 필수."""
    if data is None:
        return True
    if not isinstance(data, dict):
        return False
    for field in ("headline_en", "headline_ko", "summary_en", "summary_ko", "source"):
        if field not in data or not isinstance(data[field], str):
            return False
    return True


# ── 엔트리 빌더 ────────────────────────────────────────────────────────────────

def build_symbol_entry(raw: dict, symbol: str, now_iso: str, ctx: dict, divergence: str, tier: int = 1) -> dict:
    sentiment = raw["sentiment"]
    entry = {
        "symbol": symbol,
        "tier": tier,
        "as_of": now_iso,
        "sentiment": sentiment,
        "sentiment_score": SENTIMENT_SCORE_MAP[sentiment],
        "trend_vs_yesterday": raw["trend_vs_yesterday"],
        "mention_volume": raw["mention_volume"],
        "key_reason_en": raw.get("key_reason_en", ""),
        "key_reason_ko": raw.get("key_reason_ko", ""),
        "bot_suspected": raw["bot_suspected"],
        "confidence": raw["confidence"],
        "source": f"{'grok-oauth' if not HERMES_PROVIDER else HERMES_PROVIDER} via hermes",
    }
    # price_context: available 키는 내부용이므로 저장 시 제외
    pc = {k: v for k, v in ctx.items() if k != "available"} if ctx.get("available") else None
    if pc is not None:
        entry["price_context"] = pc
    entry["divergence"] = divergence
    tn = raw.get("top_news")
    entry["top_news"] = tn if validate_top_news(tn) and tn is not None else None
    return entry


def build_market_entry(raw: dict, now_iso: str) -> dict:
    sentiment = raw["sentiment"]
    return {
        "as_of": now_iso,
        "sentiment": sentiment,
        "sentiment_score": SENTIMENT_SCORE_MAP[sentiment],
        "trend_vs_yesterday": raw["trend_vs_yesterday"],
        "extreme_flag": raw["extreme_flag"],
        "key_reason_en": raw.get("key_reason_en", ""),
        "key_reason_ko": raw.get("key_reason_ko", ""),
        "confidence": raw["confidence"],
        "top_news": raw.get("top_news") if validate_top_news(raw.get("top_news")) and raw.get("top_news") is not None else None,
    }


# ── git ───────────────────────────────────────────────────────────────────────

def git_commit_push(repo: Path, date_str: str, time_str: str, history_path: Path) -> bool:
    """소셜 심리 데이터 push (cron 환경에서도 안정적으로 동작)"""
    rel_history = str(history_path.relative_to(repo))
    commit_message = f"sentiment: {date_str} {time_str} update"

    return commit_and_push(
        repo=repo,
        commit_message=commit_message,
        files_to_add=["sentiment/latest.json", rel_history],
        push=True,
    )


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    slot = detect_slot(now)
    print(f"[INFO] 슬롯: {slot}")

    print(f"[INFO] 수집 시작: {now_iso}")

    # VIX 수준만 (방향 없음)
    fetch_market_context()

    success_count = 0
    divergences: list[str] = []  # "TSLA(bullish_divergence)" 형식

    pre_open_path = history_filename(date_str, "pre_open")
    pre_open_scores = load_pre_open_scores(pre_open_path) if slot == "post_close" else {"market": None, "symbols": {}}

    symbol_entries = []

    # ── TIER1: 개별 심층 분석, 하루 2회 ──────────────────────────────────────
    for symbol, company in TIER1_WATCHLIST:
        print(f"[INFO] 질의 중: {symbol} ({company}) [Tier1]")

        ctx = fetch_price_context(symbol)

        try:
            prompt = build_prompt(symbol, company, ctx)
        except AssertionError as e:
            print(f"[ERROR] {symbol}: 프롬프트 오염 방지선 위반 — {e}", file=sys.stderr)
            continue

        raw_text = call_hermes(prompt)
        if raw_text is None:
            print(f"[SKIP] {symbol}: hermes 호출 실패", file=sys.stderr)
            continue

        parsed = extract_json(raw_text)
        if parsed is None:
            print(f"[SKIP] {symbol}: JSON 추출 실패", file=sys.stderr)
            continue

        if not validate_symbol_fields(parsed, symbol):
            print(f"[SKIP] {symbol}: 검증 실패", file=sys.stderr)
            continue

        close_dir = fetch_close_direction(symbol)
        sentiment_score = SENTIMENT_SCORE_MAP[parsed["sentiment"]]
        divergence = compute_divergence(close_dir, sentiment_score)

        entry = build_symbol_entry(parsed, symbol, now_iso, ctx, divergence, tier=1)
        prev_score = pre_open_scores["symbols"].get(symbol)
        entry["intraday_shift"] = (
            compute_intraday_shift(prev_score, entry["sentiment_score"])
            if prev_score is not None else None
        )
        entry["composite_score"] = compute_symbol_composite(
            sentiment_score=entry["sentiment_score"],
            confidence=entry["confidence"],
            bot_suspected=entry["bot_suspected"],
            mention_volume=entry["mention_volume"],
            divergence=entry.get("divergence", "none"),
            trend_vs_yesterday=entry["trend_vs_yesterday"],
            intraday_shift=entry.get("intraday_shift"),
        )
        symbol_entries.append(entry)
        success_count += 1
        print(
            f"[OK]   {symbol}: sentiment={entry['sentiment']} "
            f"confidence={entry['confidence']} divergence={divergence}"
        )

        if divergence in ("bullish_divergence", "bearish_divergence"):
            short = "bullish" if divergence == "bullish_divergence" else "bearish"
            divergences.append(f"{symbol}({short})")

    # ── TIER2: 배치 묶음 분석, post_close 전용 ───────────────────────────────
    if slot == "post_close":
        print(f"[INFO] TIER2 배치 질의 시작 ({len(TIER2_WATCHLIST)}종목)")
        batch_prompt = build_tier2_batch_prompt(TIER2_WATCHLIST)
        batch_raw = call_hermes(batch_prompt)

        if batch_raw is None:
            print("[SKIP] TIER2 배치: hermes 호출 실패", file=sys.stderr)
        else:
            batch_parsed = extract_json_array(batch_raw)
            if batch_parsed is None:
                print("[SKIP] TIER2 배치: JSON 배열 추출 실패", file=sys.stderr)
            else:
                # 심볼 순서 매핑 (Grok이 순서를 지키지 않을 수 있으므로 symbol 기준으로 매핑)
                tier2_map = {sym: comp for sym, comp in TIER2_WATCHLIST}
                for item in batch_parsed:
                    symbol = item.get("symbol", "").upper()
                    if symbol not in tier2_map:
                        print(f"[WARN] TIER2 배치: 알 수 없는 심볼 '{symbol}' — 스킵", file=sys.stderr)
                        continue
                    if not validate_symbol_fields(item, symbol):
                        print(f"[SKIP] TIER2 {symbol}: 검증 실패", file=sys.stderr)
                        continue

                    close_dir = fetch_close_direction(symbol)
                    sentiment_score = SENTIMENT_SCORE_MAP[item["sentiment"]]
                    divergence = compute_divergence(close_dir, sentiment_score)

                    ctx: dict = {"available": False}
                    entry = build_symbol_entry(item, symbol, now_iso, ctx, divergence, tier=2)
                    prev_score = pre_open_scores["symbols"].get(symbol)
                    entry["intraday_shift"] = (
                        compute_intraday_shift(prev_score, entry["sentiment_score"])
                        if prev_score is not None else None
                    )
                    entry["composite_score"] = compute_symbol_composite(
                        sentiment_score=entry["sentiment_score"],
                        confidence=entry["confidence"],
                        bot_suspected=entry["bot_suspected"],
                        mention_volume=entry["mention_volume"],
                        divergence=entry.get("divergence", "none"),
                        trend_vs_yesterday=entry["trend_vs_yesterday"],
                        intraday_shift=entry.get("intraday_shift"),
                    )
                    symbol_entries.append(entry)
                    success_count += 1
                    print(
                        f"[OK]   {symbol} [Tier2]: sentiment={entry['sentiment']} "
                        f"confidence={entry['confidence']} divergence={divergence}"
                    )

                    if divergence in ("bullish_divergence", "bearish_divergence"):
                        short = "bullish" if divergence == "bullish_divergence" else "bearish"
                        divergences.append(f"{symbol}({short})")
    else:
        print(f"[INFO] TIER2 건너뜀 (슬롯={slot}, post_close 전용)")

    # ── 시장 전체 수집 ────────────────────────────────────────────────────────
    print("[INFO] 질의 중: MARKET")
    market_raw_text = call_hermes(MARKET_PROMPT)
    market_entry = None

    if market_raw_text is None:
        print("[SKIP] MARKET: hermes 호출 실패", file=sys.stderr)
    else:
        market_parsed = extract_json(market_raw_text)
        if market_parsed is None:
            print("[SKIP] MARKET: JSON 추출 실패", file=sys.stderr)
        elif not validate_market_fields(market_parsed):
            print("[SKIP] MARKET: 검증 실패", file=sys.stderr)
        else:
            market_entry = build_market_entry(market_parsed, now_iso)
            prev_market_score = pre_open_scores["market"]
            market_entry["intraday_shift"] = (
                compute_intraday_shift(prev_market_score, market_entry["sentiment_score"])
                if prev_market_score is not None else None
            )
            market_entry["composite_score"] = compute_market_composite(
                sentiment_score=market_entry["sentiment_score"],
                confidence=market_entry["confidence"],
                extreme_flag=market_entry["extreme_flag"],
                trend_vs_yesterday=market_entry["trend_vs_yesterday"],
                intraday_shift=market_entry.get("intraday_shift"),
            )
            success_count += 1
            print(f"[OK]   MARKET: sentiment={market_entry['sentiment']} extreme_flag={market_entry['extreme_flag']}")

    if market_entry is None:
        market_entry = {
            "as_of": now_iso,
            "sentiment": "neutral",
            "sentiment_score": 0,
            "trend_vs_yesterday": "stable",
            "extreme_flag": "none",
            "key_reason_en": "Failed to collect market sentiment data",
            "key_reason_ko": "시장 전체 데이터 수집 실패",
            "confidence": "low",
            "intraday_shift": None,
        }

    # ── latest.json + history/<date>.json 저장 ────────────────────────────────
    snapshot = {
        "generated_at": now_iso,
        "schema_version": "2.0",
        "slot": slot,
        "market": market_entry,
        "symbols": symbol_entries,
    }

    latest_path = REPO_PATH / "sentiment" / "latest.json"
    history_path = history_filename(date_str, slot)
    history_path.parent.mkdir(exist_ok=True)

    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 파일 저장 완료: {latest_path}, {history_path}")

    # ── git commit/push ───────────────────────────────────────────────────────
    push_ok = git_commit_push(REPO_PATH, date_str, time_str, history_path)
    if not push_ok:
        print("[FATAL] git push 실패 — 최신 sentiment 데이터가 GitHub에 반영되지 않았습니다.")
        sys.exit(1)

    # ── 요약 출력 ─────────────────────────────────────────────────────────────
    expected = len(TIER1_WATCHLIST) + (len(TIER2_WATCHLIST) if slot == "post_close" else 0) + 1
    status = "[OK]" if push_ok else "[WARN]"
    print(f"\n{status} {success_count}/{expected} 수집 성공 (TIER1={len(TIER1_WATCHLIST)}, TIER2={'배치' if slot == 'post_close' else '건너뜀'}, MARKET=1)")
    if divergences:
        print(f"divergence 발생: {', '.join(divergences)}")
    else:
        print("divergence: 없음 (모두 aligned/none)")


if __name__ == "__main__":
    main()
