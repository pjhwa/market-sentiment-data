#!/usr/bin/env python3
"""
X(트위터) 멘션 볼륨 프로브 — Tier 분류를 위한 1회성 사전 스캔

후보 종목 169개를 배치로 나눠 Grok에 질의해 mention_volume을 측정.
결과를 볼륨 순으로 정렬해 화면 출력 + JSON 파일 저장.

실행:
    python -m collect.probe_mention_volume
    HERMES_PROVIDER=grok-3 python -m collect.probe_mention_volume
    PROBE_BATCH_SIZE=5 HERMES_TIMEOUT=240 python -m collect.probe_mention_volume

출력 파일:
    sentiment/probe/YYYY-MM-DD_HHmm.json   — 실행 시각별 누적
    sentiment/probe/latest.json            — 항상 최신 결과 덮어씀
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
    # 플랫폼별 대표 설치 경로 fallback
    candidates = [
        Path.home() / ".local/bin/hermes",       # Linux (pip install)
        Path("/opt/homebrew/bin/hermes"),          # macOS Apple Silicon
        Path("/usr/local/bin/hermes"),             # macOS Intel / Linux
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return str(Path.home() / ".local/bin/hermes")  # 없으면 기본값 유지 (에러는 호출 시 발생)


# ── 설정 (collect_sentiment.py 와 동일한 환경변수 재사용) ──────────────────────
HERMES_CMD      = _find_hermes()
HERMES_PROVIDER = os.environ.get("HERMES_PROVIDER", "")
CALL_TIMEOUT    = int(os.environ.get("HERMES_TIMEOUT", "120"))
BATCH_SIZE      = int(os.environ.get("PROBE_BATCH_SIZE", "10"))

# ── 후보 종목 (169개) ─────────────────────────────────────────────────────────
# 1차 47개 + 2차 100개 + 3차 22개 (핵에너지·BTC채굴·GLP-1 등 신규 테마) 통합 목록.
PROBE_CANDIDATES: list[tuple[str, str]] = [

    # ════════════════════════════════════════════════════════════════════════
    # 1차 47개 (기존 WATCHLIST + 1차 추가분)
    # ════════════════════════════════════════════════════════════════════════

    # ── 기존 WATCHLIST 7개 ──
    ("TSLA",  "Tesla"),
    ("AAPL",  "Apple"),
    ("NVDA",  "Nvidia"),
    ("META",  "Meta Platforms"),
    ("AMZN",  "Amazon"),
    ("GOOGL", "Alphabet / Google"),
    ("PLTR",  "Palantir"),

    # ── 메가캡 Tech ──
    ("MSFT",  "Microsoft"),
    ("NFLX",  "Netflix"),
    ("ORCL",  "Oracle"),

    # ── AI / 반도체 ──
    ("AMD",   "AMD"),
    ("INTC",  "Intel"),
    ("AVGO",  "Broadcom"),
    ("QCOM",  "Qualcomm"),
    ("MU",    "Micron"),
    ("SMCI",  "Super Micro Computer"),
    ("ARM",   "Arm Holdings"),
    ("TSM",   "TSMC"),

    # ── 크립토 / 핀테크 ──
    ("MSTR",  "MicroStrategy"),
    ("COIN",  "Coinbase"),
    ("HOOD",  "Robinhood"),
    ("SQ",    "Block (Square)"),
    ("PYPL",  "PayPal"),
    ("SOFI",  "SoFi"),

    # ── EV / 자동차 ──
    ("RIVN",  "Rivian"),
    ("LCID",  "Lucid Motors"),
    ("NIO",   "NIO"),
    ("XPEV",  "XPeng"),
    ("LI",    "Li Auto"),
    ("F",     "Ford"),
    ("GM",    "General Motors"),

    # ── 양자컴퓨팅 ──
    ("IONQ",  "IonQ"),
    ("QUBT",  "Quantum Computing Inc"),
    ("RGTI",  "Rigetti Computing"),
    ("QBTS",  "D-Wave Quantum"),

    # ── 클라우드 / SaaS ──
    ("CRWD",  "CrowdStrike"),
    ("SNOW",  "Snowflake"),
    ("NET",   "Cloudflare"),
    ("DDOG",  "Datadog"),
    ("MDB",   "MongoDB"),
    ("SHOP",  "Shopify"),

    # ── 소비재 / 엔터 ──
    ("UBER",  "Uber"),
    ("ABNB",  "Airbnb"),
    ("RBLX",  "Roblox"),
    ("SPOT",  "Spotify"),
    ("DIS",   "Disney"),
    ("NKLA",  "Nikola"),

    # ════════════════════════════════════════════════════════════════════════
    # 2차 100개 (상위 시총 필수 + 섹터 확장)
    # ════════════════════════════════════════════════════════════════════════

    # ── 제약 / 바이오테크 ──
    ("LLY",   "Eli Lilly"),
    ("NVO",   "Novo Nordisk"),
    ("MRNA",  "Moderna"),
    ("BNTX",  "BioNTech"),
    ("PFE",   "Pfizer"),

    # ── 클린에너지 ──
    ("ENPH",  "Enphase Energy"),
    ("FSLR",  "First Solar"),
    ("PLUG",  "Plug Power"),
    ("CHPT",  "ChargePoint"),
    ("NEE",   "NextEra Energy"),

    # ── 우주 / 방산 ──
    ("RKLB",  "Rocket Lab"),
    ("ASTS",  "AST SpaceMobile"),
    ("LUNR",  "Intuitive Machines"),
    ("BA",    "Boeing"),
    ("LMT",   "Lockheed Martin"),

    # ── 금융 ──
    ("V",     "Visa"),
    ("MA",    "Mastercard"),
    ("AFRM",  "Affirm"),
    ("UPST",  "Upstart"),
    ("JPM",   "JPMorgan Chase"),

    # ── AI 소프트웨어 ──
    ("AI",    "C3.ai"),
    ("SOUN",  "SoundHound AI"),
    ("BBAI",  "BigBear.ai"),
    ("PATH",  "UiPath"),
    ("GTLB",  "GitLab"),

    # ── 반도체 (추가) ──
    ("MRVL",  "Marvell Technology"),
    ("ASML",  "ASML"),
    ("ON",    "ON Semiconductor"),
    ("PANW",  "Palo Alto Networks"),
    ("ZS",    "Zscaler"),

    # ── 커뮤니케이션 / SaaS ──
    ("ZM",    "Zoom"),
    ("SNAP",  "Snap"),
    ("PINS",  "Pinterest"),
    ("TWLO",  "Twilio"),
    ("OKTA",  "Okta"),

    # ── 소비재 ──
    ("NKE",   "Nike"),
    ("LULU",  "Lululemon"),
    ("WMT",   "Walmart"),
    ("COST",  "Costco"),
    ("CELH",  "Celsius Holdings"),

    # ── 게임 / 모빌리티 ──
    ("EA",    "Electronic Arts"),
    ("TTWO",  "Take-Two Interactive"),
    ("U",     "Unity Software"),
    ("LYFT",  "Lyft"),
    ("DASH",  "DoorDash"),

    # ── 밈주 / 기타 ──
    ("GME",   "GameStop"),
    ("AMC",   "AMC Entertainment"),
    ("CVNA",  "Carvana"),
    ("RTX",   "RTX (Raytheon)"),
    ("GS",    "Goldman Sachs"),

    # ────────────────────────────────────────────────────────────────────────
    # 추가 50개: 상위 시총 누락 + 신흥 인기주
    # ────────────────────────────────────────────────────────────────────────

    # ── 상위 시총 필수 (Top 15~25) ──
    ("UNH",   "UnitedHealth Group"),
    ("BRK-B", "Berkshire Hathaway"),
    ("XOM",   "ExxonMobil"),
    ("JNJ",   "Johnson & Johnson"),
    ("PG",    "Procter & Gamble"),
    ("ABBV",  "AbbVie"),
    ("HD",    "Home Depot"),
    ("BAC",   "Bank of America"),
    ("CVX",   "Chevron"),
    ("KO",    "Coca-Cola"),

    # ── 상위 시총 필수 (Top 25~40) ──
    ("MRK",   "Merck"),
    ("PEP",   "PepsiCo"),
    ("CRM",   "Salesforce"),
    ("CSCO",  "Cisco"),
    ("MS",    "Morgan Stanley"),
    ("NOW",   "ServiceNow"),
    ("ADBE",  "Adobe"),
    ("INTU",  "Intuit"),
    ("AMAT",  "Applied Materials"),
    ("IBM",   "IBM"),

    # ── 통신 / 유틸리티 ──
    ("T",     "AT&T"),
    ("VZ",    "Verizon"),
    ("TMUS",  "T-Mobile"),

    # ── 추가 반도체 / 하드웨어 ──
    ("TXN",   "Texas Instruments"),
    ("LRCX",  "Lam Research"),
    ("KLAC",  "KLA Corporation"),
    ("ANET",  "Arista Networks"),
    ("NXPI",  "NXP Semiconductors"),
    ("ADI",   "Analog Devices"),
    ("DELL",  "Dell Technologies"),

    # ── 헬스케어 추가 ──
    ("AMGN",  "Amgen"),
    ("ISRG",  "Intuitive Surgical"),
    ("GILD",  "Gilead Sciences"),
    ("REGN",  "Regeneron"),
    ("VRTX",  "Vertex Pharmaceuticals"),

    # ── 소비재 / 리테일 추가 ──
    ("MCD",   "McDonald's"),
    ("SBUX",  "Starbucks"),
    ("CAT",   "Caterpillar"),
    ("FTNT",  "Fortinet"),
    ("ACN",   "Accenture"),

    # ── 신흥 인기주 / 틈새 ──
    ("APP",   "AppLovin"),
    ("AXON",  "Axon Enterprise"),
    ("HIMS",  "Hims & Hers"),
    ("RDDT",  "Reddit"),
    ("DUOL",  "Duolingo"),
    ("BABA",  "Alibaba"),
    ("PDD",   "PDD Holdings (Temu)"),
    ("BIDU",  "Baidu"),
    ("SE",    "Sea Limited"),
    ("MNDY",  "Monday.com"),

    # ════════════════════════════════════════════════════════════════════════
    # 3차 추가: 2025~2026 신규 인기 테마
    # ════════════════════════════════════════════════════════════════════════

    # ── 핵에너지 / AI 전력 인프라 ──
    ("CEG",   "Constellation Energy"),
    ("VST",   "Vistra Energy"),
    ("VRT",   "Vertiv Holdings"),
    ("ETN",   "Eaton Corporation"),
    ("OKLO",  "Oklo"),
    ("SMR",   "NuScale Power"),
    ("PWR",   "Quanta Services"),

    # ── 비트코인 채굴 ──
    ("MARA",  "Marathon Digital Holdings"),
    ("RIOT",  "Riot Platforms"),
    ("CLSK",  "CleanSpark"),
    ("CORZ",  "Core Scientific"),

    # ── AI 인프라 / 반도체 추가 ──
    ("ALAB",  "Astera Labs"),
    ("WOLF",  "Wolfspeed"),
    ("MCHP",  "Microchip Technology"),
    ("HPE",   "HP Enterprise"),

    # ── GLP-1 / 비만치료 바이오 ──
    ("VKTX",  "Viking Therapeutics"),
    ("ZNTL",  "Zentalis Pharmaceuticals"),

    # ── 방산 테크 ──
    ("KTOS",  "Kratos Defense"),
    ("HII",   "Huntington Ingalls"),

    # ── 중국 테크 추가 ──
    ("JD",    "JD.com"),
    ("KWEB",  "KraneShares China Internet ETF"),

    # ── 기타 신규 인기주 ──
    ("RBRK",  "Rubrik"),
]

# 중복 제거 (순서 유지)
seen: set[str] = set()
CANDIDATES: list[tuple[str, str]] = []
for sym, co in PROBE_CANDIDATES:
    if sym not in seen:
        seen.add(sym)
        CANDIDATES.append((sym, co))

# ── 등급 정의 ────────────────────────────────────────────────────────────────
VOLUME_ORDER = ["surging", "elevated", "normal", "low"]
TIER1_VOLUMES = {"surging", "elevated"}

# ── 복합 점수 계산 ────────────────────────────────────────────────────────────
# 각 필드의 가중치로 0~100 점수를 산출. Tier 선별 기준으로 사용.

_VOLUME_SCORE    = {"surging": 40, "elevated": 30, "normal": 15, "low": 5}
_CONSIST_SCORE   = {"steady": 20, "event_driven": 12, "sporadic": 5}
_RETAIL_SCORE    = {"high": 15, "med": 10, "low": 5}
_CLARITY_SCORE   = {"clear": 15, "mixed": 8, "noisy": 3}
_BOT_SCORE       = {"low": 10, "med": 6, "high": 2}
_CONF_MULT       = {"high": 1.0, "med": 0.85, "low": 0.6}


def compute_probe_score(entry: dict) -> float:
    """mention_volume·consistency·retail_dominance·sentiment_clarity·bot_ratio
    5개 차원을 결합해 0~100 복합 점수 반환."""
    raw = (
        _VOLUME_SCORE.get(entry.get("mention_volume", "low"), 5)
        + _CONSIST_SCORE.get(entry.get("consistency", "sporadic"), 5)
        + _RETAIL_SCORE.get(entry.get("retail_dominance", "low"), 5)
        + _CLARITY_SCORE.get(entry.get("sentiment_clarity", "noisy"), 3)
        + _BOT_SCORE.get(entry.get("bot_ratio", "high"), 2)
    )
    mult = _CONF_MULT.get(entry.get("confidence", "low"), 0.6)
    return round(raw * mult, 1)


# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────

def build_probe_prompt(batch: list[tuple[str, str]]) -> str:
    ticker_list = ", ".join(f"{sym} ({co})" for sym, co in batch)
    n = len(batch)
    return f"""\
You are a data tool. For each ticker below, search current X (Twitter) posts \
and return a JSON array with signal-quality metrics for each ticker.

Tickers ({n}): {ticker_list}

Each object schema:
{{
  "symbol": "TICKER",
  "mention_volume": one of ["low","normal","elevated","surging"],
  "consistency": one of ["steady","event_driven","sporadic"],
  "retail_dominance": one of ["high","med","low"],
  "sentiment_clarity": one of ["clear","mixed","noisy"],
  "bot_ratio": one of ["low","med","high"],
  "confidence": one of ["high","med","low"],
  "note": "one short English sentence summarising the current discussion"
}}

Field definitions:
- mention_volume   : current post frequency vs this ticker's own baseline.
                     surging=exceptional today · elevated=above avg · normal=typical · low=sparse.
- consistency      : steady=active most days · event_driven=only spikes on news · sporadic=irregular.
- retail_dominance : what fraction of posts come from retail/individual investors vs institutions/media.
- sentiment_clarity: clear=posts express a dominant mood · mixed=split opinions · noisy=irrelevant or ambiguous.
- bot_ratio        : estimated share of bot/spam accounts in the discussion. low=mostly human.
- confidence       : your confidence in the above ratings given available data.
- note             : one sentence on what is driving discussion today (or why it is quiet).

Rules:
- Search each ticker by both $TICKER and the company name.
- Base ratings ONLY on post activity — do NOT use price direction.
- Thin sample → confidence "low", volume "low".
- Return exactly {n} objects in the same order as the input list.
- Output raw JSON array only — no prose, no code fences."""


# ── hermes 호출 ────────────────────────────────────────────────────────────────

def call_hermes(prompt: str) -> str | None:
    cmd = [HERMES_CMD, "-z", prompt]
    if HERMES_PROVIDER:
        cmd += ["--provider", HERMES_PROVIDER]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT, env=env,
        )
        if result.returncode != 0:
            print(f"[ERROR] hermes rc={result.returncode}: {result.stderr[:200]}", file=sys.stderr)
            return None
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"[ERROR] hermes 타임아웃 ({CALL_TIMEOUT}초)", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(f"[ERROR] hermes 명령 없음: {HERMES_CMD}", file=sys.stderr)
        return None


# ── JSON 파싱 ──────────────────────────────────────────────────────────────────

def extract_json_array(text: str) -> list | None:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        print(f"[ERROR] JSON 배열을 찾을 수 없음: {text[:200]!r}", file=sys.stderr)
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON 파싱 실패: {e}", file=sys.stderr)
        return None


def validate_entry(entry: dict) -> bool:
    """필수 필드(symbol·mention_volume·confidence)만 엄격 검증.
    확장 필드(consistency 등)는 없어도 허용 — 기본값으로 fallback."""
    return (
        isinstance(entry.get("symbol"), str)
        and entry.get("mention_volume") in VOLUME_ORDER
        and entry.get("confidence") in ("high", "med", "low")
    )


# ── 결과 출력 ────────────────────────────────────────────────────────────────

VOLUME_ICON = {"surging": "🔥", "elevated": "📈", "normal": "➖", "low": "💤"}
CONF_ICON   = {"high": "●", "med": "◐", "low": "○"}


def _sort_key(r: dict) -> tuple:
    return (-r.get("probe_score", 0), r["symbol"])


def print_results(results: list[dict]) -> None:
    scored = sorted(results, key=_sort_key)
    symbol_map = {sym: co for sym, co in CANDIDATES}

    top20    = scored[:20]
    tier1_20 = [r for r in top20 if r.get("mention_volume") in TIER1_VOLUMES]
    tier2_20 = [r for r in top20 if r.get("mention_volume") not in TIER1_VOLUMES]

    print("\n" + "═" * 90)
    print(" X 멘션 볼륨 프로브 결과 — 복합 점수 순 (Tier 선별용)")
    print("═" * 90)
    print(f"{'#':<4} {'심볼':<7} {'점수':>5}  {'볼륨':<10} {'일관성':<14} {'리테일':<8} {'명확도':<8} {'봇':<7} {'신뢰':<5} 비고")
    print("─" * 90)

    for rank, r in enumerate(scored, 1):
        sym   = r.get("symbol", "?")
        score = r.get("probe_score", 0)
        vol   = r.get("mention_volume", "low")
        cons  = r.get("consistency", "-")
        ret   = r.get("retail_dominance", "-")
        clar  = r.get("sentiment_clarity", "-")
        bot   = r.get("bot_ratio", "-")
        conf  = r.get("confidence", "-")
        note  = r.get("note", "")[:38]
        icon  = VOLUME_ICON.get(vol, "?")
        marker = " ◀ TOP20" if rank <= 20 else ""
        print(f"{rank:<4} {sym:<7} {score:>5.1f}  {icon}{vol:<9} {cons:<14} {ret:<8} {clar:<8} {bot:<7} {CONF_ICON.get(conf,'?')}    {note}{marker}")

    print("─" * 90)
    print(f"\n총 {len(scored)}개 스캔 완료 | 상위 20개 자동 선별\n")

    # ── TOP20 요약 ──
    print("━" * 90)
    print(f" 상위 20 중 Tier1 권장 ({len(tier1_20)}개) — 개별 심층 분석 · 하루 2회")
    print("━" * 90)
    for r in tier1_20:
        print(f"  {r['symbol']:<7} {r.get('probe_score',0):>5.1f}점  {r.get('mention_volume',''):<10}  {r.get('note','')[:60]}")

    print()
    print("━" * 90)
    print(f" 상위 20 중 Tier2 권장 ({len(tier2_20)}개) — 배치 묶음 · 하루 1회")
    print("━" * 90)
    for r in tier2_20:
        print(f"  {r['symbol']:<7} {r.get('probe_score',0):>5.1f}점  {r.get('mention_volume',''):<10}  {r.get('note','')[:60]}")

    print()

    # ── collect_sentiment.py 복사용 코드 출력 ──
    t1_syms = [r["symbol"] for r in tier1_20]
    t2_syms = [r["symbol"] for r in tier2_20]

    print("─" * 90)
    print(" collect_sentiment.py 복사용")
    print("─" * 90)
    print("TIER1_WATCHLIST = [")
    for s in t1_syms:
        print(f'    ("{s:<5}", "{symbol_map.get(s, s)}"),')
    print("]")
    print()
    print("TIER2_WATCHLIST = [")
    for s in t2_syms:
        print(f'    ("{s:<5}", "{symbol_map.get(s, s)}"),')
    print("]")
    print()


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    total = len(CANDIDATES)
    batches = [CANDIDATES[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    print(f"[INFO] 후보 {total}개 → {len(batches)}개 배치 (배치당 {BATCH_SIZE}종목)")
    print(f"[INFO] HERMES_CMD={HERMES_CMD}  PROVIDER={HERMES_PROVIDER or '(기본값)'}")

    all_results: list[dict] = []
    failed_symbols: list[str] = []

    for i, batch in enumerate(batches, 1):
        syms = [s for s, _ in batch]
        print(f"\n[배치 {i}/{len(batches)}] {', '.join(syms)}")

        prompt = build_probe_prompt(batch)
        raw = call_hermes(prompt)

        if raw is None:
            print(f"  [SKIP] hermes 호출 실패", file=sys.stderr)
            failed_symbols.extend(syms)
            continue

        entries = extract_json_array(raw)
        if entries is None:
            print(f"  [SKIP] JSON 파싱 실패", file=sys.stderr)
            failed_symbols.extend(syms)
            continue

        for entry in entries:
            if not validate_entry(entry):
                sym = entry.get("symbol", "?")
                print(f"  [WARN] {sym}: 필드 검증 실패 — {entry}", file=sys.stderr)
                continue
            entry["probe_score"] = compute_probe_score(entry)
            vol   = entry.get("mention_volume", "low")
            conf  = entry.get("confidence", "low")
            score = entry["probe_score"]
            print(f"  {VOLUME_ICON.get(vol,'?')} {entry['symbol']:<6}: {vol:<10} score={score:>5.1f}  (신뢰: {conf})")
            all_results.append(entry)

    if failed_symbols:
        print(f"\n[WARN] 수집 실패 종목: {', '.join(failed_symbols)}", file=sys.stderr)

    if not all_results:
        print("[ERROR] 결과 없음 — hermes/Grok 연결을 확인하세요.", file=sys.stderr)
        sys.exit(1)

    save_results(all_results, failed_symbols)
    print_results(all_results)


# ── 파일 저장 ──────────────────────────────────────────────────────────────────

def save_results(results: list[dict], failed: list[str]) -> None:
    """결과를 JSON으로 저장.
    - sentiment/probe/YYYY-MM-DD_HHmm.json : 실행별 누적 보관
    - sentiment/probe/latest.json          : 항상 최신으로 덮어씀

    저장 구조:
      generated_at, probe_batch_size, provider, total_scanned, failed_symbols
      score_schema      : 점수 산출 기준 (재현성)
      selection          : top20 전체 / tier1_top10 / tier2_top10 (복사용 코드 포함)
      ranked_results     : 전체 결과를 probe_score 내림차순 정렬
    """
    repo_root = Path(__file__).parent.parent
    probe_dir = repo_root / "sentiment" / "probe"
    probe_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    fname_ts  = now.strftime("%Y-%m-%d_%H%M")

    symbol_map = {sym: co for sym, co in CANDIDATES}
    ranked = sorted(results, key=_sort_key)

    top20    = ranked[:20]
    tier1_20 = [r for r in top20 if r.get("mention_volume") in TIER1_VOLUMES]
    tier2_20 = [r for r in top20 if r.get("mention_volume") not in TIER1_VOLUMES]

    def _entry(r: dict) -> dict:
        return {
            "symbol":            r["symbol"],
            "company":           symbol_map.get(r["symbol"], r["symbol"]),
            "probe_score":       r.get("probe_score", 0),
            "mention_volume":    r.get("mention_volume", "low"),
            "consistency":       r.get("consistency", "-"),
            "retail_dominance":  r.get("retail_dominance", "-"),
            "sentiment_clarity": r.get("sentiment_clarity", "-"),
            "bot_ratio":         r.get("bot_ratio", "-"),
            "confidence":        r.get("confidence", "low"),
            "note":              r.get("note", ""),
        }

    def _code_block(sym_list: list[str]) -> str:
        lines = [f'    ("{s:<5}", "{symbol_map.get(s, s)}"),' for s in sym_list]
        return "\n".join(lines)

    t1_syms = [r["symbol"] for r in tier1_20]
    t2_syms = [r["symbol"] for r in tier2_20]

    payload = {
        "generated_at":    timestamp,
        "probe_batch_size": BATCH_SIZE,
        "provider":        HERMES_PROVIDER or "default",
        "total_scanned":   len(results),
        "failed_symbols":  failed,

        # ── 점수 산출 기준 (재현성 보장) ──
        "score_schema": {
            "mention_volume":    {"surging": 40, "elevated": 30, "normal": 15, "low": 5},
            "consistency":       {"steady": 20, "event_driven": 12, "sporadic": 5},
            "retail_dominance":  {"high": 15, "med": 10, "low": 5},
            "sentiment_clarity": {"clear": 15, "mixed": 8, "noisy": 3},
            "bot_ratio":         {"low": 10, "med": 6, "high": 2},
            "confidence_mult":   {"high": 1.0, "med": 0.85, "low": 0.6},
            "max_score":         100,
            "note": "score = (sum of field scores) × confidence_mult",
        },

        # ── 선별 결과 (핵심 섹션) ──
        "selection": {
            "top20": [_entry(r) for r in top20],
            "tier1_top10": {
                "count":       len(tier1_20),
                "symbols":     t1_syms,
                "description": "개별 심층 분석 · 하루 2회 · mention_volume surging/elevated",
                "code":        f"TIER1_WATCHLIST = [\n{_code_block(t1_syms)}\n]",
                "entries":     [_entry(r) for r in tier1_20],
            },
            "tier2_top10": {
                "count":       len(tier2_20),
                "symbols":     t2_syms,
                "description": "배치 묶음 · 하루 1회 · mention_volume normal/low but high score",
                "code":        f"TIER2_WATCHLIST = [\n{_code_block(t2_syms)}\n]",
                "entries":     [_entry(r) for r in tier2_20],
            },
        },

        # ── 전체 랭킹 ──
        "ranked_results": [_entry(r) for r in ranked],
    }

    timestamped = probe_dir / f"{fname_ts}.json"
    latest      = probe_dir / "latest.json"

    for path in (timestamped, latest):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n[저장] {timestamped}")
    print(f"[저장] {latest}  (latest 덮어씀)")


if __name__ == "__main__":
    main()
