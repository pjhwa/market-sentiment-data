#!/usr/bin/env python3
"""
브리핑 검증 스크립트 (Morning Briefing Verification Script)

실행:
  python3 -m collect.verify_briefing                   # 최신 briefing/latest.json
  python3 -m collect.verify_briefing --date 2026-06-05 # 특정 날짜 히스토리
  python3 -m collect.verify_briefing --skip-claude     # Claude 없이 데이터 검증만
  python3 -m collect.verify_briefing --json            # 콘솔 + JSON 파일 저장

검증 카테고리:
  A. SniperBoard 데이터 바인딩 — 가격/구조/매크로/프리마켓 수치
  B. 시장 분위기 정확성 — regime score → traffic_light 분류
  C. 거시환경 수치 정확성 — VIX/TNX/DXY/BTC big_picture 기재값
  D. 섹터동향 정확성 — macro_insight 신호 vs sector_analysis 텍스트
  E. 규칙 준수 — action rules / earnings_alert / global source quality / confidence 언어
  F. 완결성 — 종목 수 / 필수 필드 / 용어 설명 / 공유 본문 품질
  G. [Claude] 글로벌 이슈 현재성 / 중요 뉴스 누락 / 분위기·섹터 정합성
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

REPO_PATH = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
SNIPERBOARD_API = os.environ.get("SNIPERBOARD_API_BASE", "http://localhost:5001")
CLAUDE_CMD = os.environ.get("CLAUDE_CMD", "/Users/jerry/.local/bin/claude")
VERIFY_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT_VERIFY", "300"))

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
EXPECTED_SYMBOLS = [s for s, _, _ in ALL_SYMBOLS]

SOCIAL_PATTERNS = (
    "twitter", "x post", "x discussion", " @", "reddit", "telegram",
    "discord", "4chan", "/@", "warhorizon", "me_observer_", "globalflash",
)
ACCEPTED_OUTLETS = (
    "reuters", "bloomberg", "ap ", " ft.", "wsj", "nyt", "bbc",
    "white house", "bis", "sec.", "fed", "doj", "ftc", "court",
    "bloomberg law", "associated press",
)

# 용어 설명 확인 대상 (영문 용어 → 설명이 근방에 있어야 하는 패턴)
JARGON_PAIRS = [
    # (용어 regex, 설명 힌트 중 하나 이상 있어야 함)
    (r"\bRS\b",        ["시장 상대강도", "relative strength", "market vs", "시장보다"]),
    (r"\bStage2\b",    ["stage2", "스테이지2", "stage 2"]),
    (r"\bVIX\b",       ["공포", "fear", "변동성 지수", "volatility index"]),
    (r"\bDXY\b",       ["달러 지수", "dollar index", "달러"]),
    (r"\bEMA\b",       ["이동평균", "moving average", "이평선"]),
    (r"\bATR\b",       ["평균 진폭", "average true range", "일일 변동 범위"]),
    (r"\bGC\b",        ["가우시안", "gaussian"]),
    (r"\bBTC\b",       ["비트코인", "bitcoin"]),
]


# ─── 데이터 클래스 ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    severity: str = "error"   # error | warning | info

    def emoji(self) -> str:
        if self.passed:
            return "✅"
        return "❌" if self.severity == "error" else "⚠️"

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed,
                "severity": self.severity, "detail": self.detail}


@dataclass
class VerificationReport:
    briefing_path: str
    generated_at: str
    run_at: str
    results: list[CheckResult] = field(default_factory=list)
    grok_report: dict = field(default_factory=dict)

    def add(self, result: CheckResult):
        self.results.append(result)

    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == "warning"]

    def passed(self) -> bool:
        return len(self.errors()) == 0

    def as_dict(self) -> dict:
        return {
            "briefing_path": self.briefing_path,
            "generated_at": self.generated_at,
            "verified_at": self.run_at,
            "overall_passed": self.passed(),
            "error_count": len(self.errors()),
            "warning_count": len(self.warnings()),
            "checks": [r.as_dict() for r in self.results],
            "grok_report": self.grok_report,
        }


# ─── API 헬퍼 ──────────────────────────────────────────────────────────────────

def _api_get(path: str, params: dict | None = None, timeout: int = 10) -> dict | None:
    try:
        r = requests.get(f"{SNIPERBOARD_API}/api{path}", params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] API {path} 실패: {e}", file=sys.stderr)
        return None


# ─── 카테고리 A: SniperBoard 데이터 바인딩 ────────────────────────────────────

def check_structure_binding(brief: dict, report: VerificationReport):
    """A1: 22개 종목 market_structure 일치 확인."""
    watchlist = {w["symbol"]: w for w in brief.get("watchlist", [])}
    mismatches, errors = [], []

    for sym in EXPECTED_SYMBOLS:
        daily = _api_get("/daily", {"symbol": sym})
        if not daily:
            errors.append(sym)
            continue
        api_struct = daily.get("stage2", {}).get("market_structure", "?")
        w = watchlist.get(sym, {})
        en = w.get("analysis_en", "")
        ko = w.get("analysis_ko", "")
        bf_struct = next(
            (s for s in ("UPTREND", "DOWNTREND", "DISTRIBUTION") if s in en), None
        )
        if not bf_struct:
            # ko에서도 확인 (상승 추세, 하락 추세, 분배 구간)
            if "상승 추세" in ko:
                bf_struct = "UPTREND"
            elif "하락 추세" in ko:
                bf_struct = "DOWNTREND"
            elif "분배 구간" in ko:
                bf_struct = "DISTRIBUTION"

        if bf_struct is None:
            mismatches.append(f"{sym}: 브리핑에 구조 표기 없음 (API={api_struct})")
        elif bf_struct != api_struct:
            mismatches.append(f"{sym}: API={api_struct} vs 브리핑={bf_struct}")

    if errors:
        report.add(CheckResult(
            "A1-구조 바인딩", False,
            f"API 호출 실패: {', '.join(errors)}", "warning"
        ))
    if mismatches:
        report.add(CheckResult(
            "A1-구조 바인딩", False,
            f"불일치 {len(mismatches)}건:\n    " + "\n    ".join(mismatches)
        ))
    else:
        passed = len(EXPECTED_SYMBOLS) - len(errors)
        report.add(CheckResult("A1-구조 바인딩", True, f"{passed}/21 종목 일치"))


def check_action_rules(brief: dict, report: VerificationReport):
    """A2: action 규칙 준수 확인 (Rule 1/2/3/4) + Stage2/구조 데이터 일관성."""
    watchlist = {w["symbol"]: w for w in brief.get("watchlist", [])}
    violations = []
    data_inconsistencies = []

    for sym in EXPECTED_SYMBOLS:
        daily = _api_get("/daily", {"symbol": sym})
        if not daily:
            continue
        s2 = daily.get("stage2", {})
        stage2 = s2.get("score", 0)
        rs = s2.get("rs_score", 0)
        struct = s2.get("market_structure", "?")
        w = watchlist.get(sym, {})
        action = w.get("action", "?")
        mood = w.get("sentiment_mood", "?")

        # Rule 1 (HARD): avoid if DOWNTREND AND Stage2≤6, OR Stage2≤2
        rule1 = (struct == "DOWNTREND" and stage2 <= 6) or (stage2 <= 2)
        exception1 = (stage2 == 7 and rs >= 70 and struct == "DOWNTREND")
        must_avoid = rule1 and not exception1

        if must_avoid and action != "avoid":
            violations.append(
                f"{sym}: Rule1 → avoid 필요 (struct={struct},S2={stage2},RS={rs:.0f}), 실제={action}"
            )
        # Rule 2: buy if Stage2>=6, RS>=70, not DOWNTREND, mood=optimistic/euphoric
        rule2 = (stage2 >= 6 and rs >= 70 and struct != "DOWNTREND"
                 and mood in ("optimistic", "euphoric"))
        if not must_avoid and rule2 and action == "avoid":
            violations.append(
                f"{sym}: Rule2 조건 충족(S2={stage2},RS={rs:.0f}) 인데 action=avoid"
            )

        # Stage2 ≤ 2 + UPTREND → SniperBoard 데이터 불일치 감지
        if stage2 <= 2 and struct == "UPTREND":
            data_inconsistencies.append(
                f"{sym}: Stage2={stage2}≤2 이지만 market_structure=UPTREND (SniperBoard 데이터 불일치)"
            )

    if data_inconsistencies:
        report.add(CheckResult(
            "A2-데이터 일관성", False,
            "\n    ".join(data_inconsistencies), "warning"
        ))

    if violations:
        report.add(CheckResult(
            "A2-액션 규칙", False,
            f"위반 {len(violations)}건:\n    " + "\n    ".join(violations)
        ))
    else:
        report.add(CheckResult("A2-액션 규칙", True, "21/21 종목 규칙 준수"))


def check_macro_values(brief: dict, report: VerificationReport):
    """A3: VIX/TNX/DXY/BTC 수치 big_picture 기재값 vs API 일치 확인."""
    macro = _api_get("/macro")
    if not macro:
        report.add(CheckResult("A3-매크로 수치", False, "매크로 API 호출 실패", "warning"))
        return

    items = {i["symbol"]: i for i in macro.get("macro", [])}
    bp = brief.get("big_picture", {})
    issues = []

    def extract_value(text: str, patterns: list[str]) -> Optional[float]:
        """패턴 목록에서 첫 매칭 숫자 추출 (단위 컨텍스트 고려)."""
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except (ValueError, IndexError):
                    continue
        return None

    # 각 지표별 맥락에 맞는 패턴 사용
    checks = [
        # (API 심볼, field_key, label, 허용오차, 추출 패턴 목록)
        ("^VIX", "vix_note_en", "VIX", 1.5,
         [r"VIX\s+(?:sits?\s+at|at|of)\s+([\d.]+)",
          r"VIX\s+([\d.]+)",
          r"at\s+([\d.]+)\s+after"]),
        ("^TNX", "rates_note_en", "TNX", 0.15,
         [r"yield\s+(?:sits?\s+at|at|is)\s+([\d.]+)\s*(?:%|percent)",
          r"(?:at|sits?\s+at)\s+([\d.]+)\s*(?:%|percent)",
          r"([\d]\.\d+)\s*(?:%|percent)\s+(?:is|and|yield)",
          r"yield\s+at\s+([\d.]+)%",
          r"([\d]\.\d+)%\s+(?:is|and|yield)"]),
        ("DX-Y.NYB", "dollar_note_en", "DXY", 0.5,
         [r"DXY[^\d]{0,20}([\d]{2,3}\.[\d]+)",
          r"DXY\s+(?:is\s+)?at\s+([\d.]+)",
          r"DXY\s+at\s+([\d.]+)",
          r"DXY\s+([\d.]+)",
          r"at\s+([\d.]+)\s+(?:is|and|strengthening|weakening|essentially|flat)"]),
    ]
    for sym, field_key, label, tol, patterns in checks:
        api_val = items.get(sym, {}).get("price")
        txt = bp.get(field_key, "")
        bf_val = extract_value(txt, patterns)
        if api_val is None:
            continue
        if bf_val is None:
            issues.append(f"{label}: 브리핑에 수치 없음 (API={api_val})")
        elif abs(bf_val - api_val) > tol:
            issues.append(f"{label}: 브리핑={bf_val} vs API={api_val:.2f} (허용오차±{tol})")

    # BTC: btc_note에 pre-generated anchor 그대로 있어야 함
    btc_api = items.get("BTC-USD", {}).get("price")
    btc_txt = bp.get("btc_note_en", "")
    btc_bf = extract_value(btc_txt, [r"\$([\d,]+\.?\d*)", r"at\s+\$([\d,]+\.?\d*)"])
    if btc_api and btc_bf and abs(btc_bf - btc_api) > 2000:
        issues.append(f"BTC: 브리핑=${btc_bf:,.0f} vs API=${btc_api:,.0f} (2000달러 이상 차이)")

    if issues:
        report.add(CheckResult(
            "A3-매크로 수치", False,
            "\n    ".join(issues), "warning"
        ))
    else:
        report.add(CheckResult("A3-매크로 수치", True,
                               "VIX/TNX/DXY/BTC 수치 바인딩 정상"))


def check_regime_classification(brief: dict, report: VerificationReport):
    """A4: market_mood traffic_light이 regime score에 맞게 분류됐는지."""
    regime = _api_get("/regime")
    if not regime:
        report.add(CheckResult("A4-시장분위기 분류", False, "regime API 실패", "warning"))
        return

    api_score = regime.get("total", 0)
    mood = brief.get("market_mood", {})
    bf_score = mood.get("score", 0)
    bf_light = mood.get("traffic_light", "?")

    # 기대 traffic_light 계산 (regime 기준)
    if api_score >= 80:
        expected = "green"
    elif api_score >= 40:
        expected = "yellow"
    else:
        expected = "red"

    issues = []
    if abs(bf_score - api_score) > 1.0:
        issues.append(f"score: 브리핑={bf_score} vs API={api_score} (차이>{abs(bf_score - api_score):.1f})")
    if bf_light != expected:
        issues.append(f"traffic_light: 브리핑={bf_light} vs 기대={expected} (score={api_score})")

    if issues:
        report.add(CheckResult("A4-시장분위기 분류", False, " / ".join(issues)))
    else:
        report.add(CheckResult("A4-시장분위기 분류", True,
                               f"score={api_score:.1f} → traffic_light={bf_light} 정확"))


def check_premarket_in_spotlight(brief: dict, report: VerificationReport):
    """A5: spotlight 종목의 프리마켓 가격이 /api/prepost와 일치하는지."""
    issues = []
    for s in brief.get("spotlight", []):
        sym = s.get("symbol")
        why = s.get("why_en", "")
        pp = _api_get("/prepost", {"symbol": sym})
        if not pp:
            continue
        pre_price = pp.get("pre_market_price")
        if not pre_price:
            continue
        # 텍스트에서 프리마켓 가격 추출: "pre-market at $X" 패턴
        m = re.search(r"pre-market at \$([\d,]+\.?\d*)", why, re.IGNORECASE)
        if m:
            try:
                bf_price = float(m.group(1).replace(",", ""))
                if abs(bf_price - pre_price) > pre_price * 0.03:  # 3% 허용
                    issues.append(
                        f"{sym}: spotlight 프리마켓 ${bf_price} vs API ${pre_price:.2f}"
                    )
            except ValueError:
                pass

    if issues:
        report.add(CheckResult(
            "A5-프리마켓 바인딩", False,
            "\n    ".join(issues), "warning"
        ))
    else:
        report.add(CheckResult("A5-프리마켓 바인딩", True,
                               "spotlight 프리마켓 값 일치 (또는 검증 대상 없음)"))


# ─── 카테고리 B: 섹터동향 정확성 ──────────────────────────────────────────────

def check_sector_leaders_vs_downtrend(brief: dict, report: VerificationReport):
    """B1: DOWNTREND 종목이 섹터 리더로 오기재됐는지 확인."""
    sa = brief.get("sector_analysis", {})
    leaders_en = (sa.get("leaders_en") or "").lower()
    leaders_ko = (sa.get("leaders_ko") or "").lower()

    downtrend_syms = []
    for sym in EXPECTED_SYMBOLS:
        daily = _api_get("/daily", {"symbol": sym})
        if not daily:
            continue
        struct = daily.get("stage2", {}).get("market_structure", "?")
        if struct == "DOWNTREND":
            downtrend_syms.append(sym.lower())

    # DOWNTREND 종목이 "leaders" 텍스트에 리더로 언급되면 위반
    # 단, "DOWNTREND" 라는 단어와 함께 언급된 경우는 OK (올바른 caveat)
    violations = []
    for sym in downtrend_syms:
        # sym이 leaders 텍스트에 있고, 그 근방에 "downtrend"/"하락 추세" 단어가 없으면 위반
        sym_in_leaders = sym in leaders_en or sym in leaders_ko
        caveat_nearby = "downtrend" in leaders_en or "하락 추세" in leaders_ko
        if sym_in_leaders and not caveat_nearby:
            violations.append(f"{sym.upper()} (DOWNTREND) caveat 없이 leaders에 언급됨")

    if violations:
        report.add(CheckResult(
            "B1-섹터 리더 정확성", False,
            "\n    ".join(violations)
        ))
    else:
        report.add(CheckResult("B1-섹터 리더 정확성", True,
                               "DOWNTREND 종목 leaders 오기재 없음"))


def check_sector_signals(brief: dict, report: VerificationReport):
    """B2: sector_analysis 텍스트가 macro_insight 신호와 모순되지 않는지."""
    mi = _api_get("/macro/insight")
    if not mi:
        report.add(CheckResult("B2-섹터 신호", True, "macro/insight API 없음 — 스킵", "info"))
        return

    overall = mi.get("overall_judgment", "")
    groups = mi.get("groups", {})
    sa = brief.get("sector_analysis", {})
    leaders_en = (sa.get("leaders_en") or "").lower()

    # 전체 판단이 RISK_OFF인데 리더가 많다고 하면 경고
    risk_off = "risk_off" in overall.lower() or "red" in overall.lower()
    many_leaders_claimed = len(leaders_en.split("leading")) > 2

    issues = []
    if risk_off and many_leaders_claimed and "no sector" not in leaders_en:
        issues.append(
            f"macro_insight={overall} 인데 sector_analysis에서 다수 리더 언급"
        )

    # sectors 그룹이 red인데 leaders에 해당 섹터 이름 단독 언급하면 경고
    sector_map = {
        "breadth": ["spy", "qqq", "broad market", "시장 폭"],
        "credit":  ["hyg", "credit", "신용"],
        "volatility": ["vix", "변동성"],
    }
    for group_key, keywords in sector_map.items():
        grp = groups.get(group_key, {})
        if grp.get("signal") == "red":
            for kw in keywords:
                if kw in leaders_en and "red" not in leaders_en and "lagging" not in leaders_en:
                    issues.append(
                        f"macro_insight groups.{group_key}=RED 인데 leaders에 '{kw}' 우호적 언급"
                    )

    if issues:
        report.add(CheckResult(
            "B2-섹터 신호", False,
            "\n    ".join(issues), "warning"
        ))
    else:
        report.add(CheckResult("B2-섹터 신호", True,
                               "macro_insight 신호와 섹터 서술 모순 없음"))


# ─── 카테고리 C: 규칙 준수 ────────────────────────────────────────────────────

def check_earnings_alert_rule(brief: dict, report: VerificationReport):
    """C1: earnings_alert가 ≤14일 이내 종목만 포함하는지."""
    import datetime as dt
    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).date()
    ea_en = brief.get("earnings_alert_en", "")
    ea_ko = brief.get("earnings_alert_ko", "")

    # earnings/latest.json에서 종목별 days_until 재계산
    earnings_file = REPO_PATH / "earnings" / "latest.json"
    if not earnings_file.exists():
        report.add(CheckResult("C1-earnings_alert", True, "earnings 파일 없음 — 스킵", "info"))
        return

    with open(earnings_file, encoding="utf-8") as f:
        earn_data = json.load(f)

    violations = []
    for e in earn_data.get("upcoming_earnings", []):
        sym = e.get("symbol", "")
        edate_str = e.get("earnings_date") or e.get("report_date")
        if not edate_str:
            continue
        try:
            edate = dt.date.fromisoformat(edate_str)
        except ValueError:
            continue
        days_until = (edate - now_kst).days
        if days_until > 14:
            # 이 종목이 earnings_alert에 있으면 위반
            sym_in_alert = sym.lower() in ea_en.lower() or sym.lower() in ea_ko.lower()
            if sym_in_alert:
                violations.append(
                    f"{sym}: days_until={days_until} (>{14}) 인데 earnings_alert에 기재됨"
                )

    if ea_en and not violations:
        # earnings_alert가 비어있지 않은데 위반 없으면 OK
        report.add(CheckResult("C1-earnings_alert", True,
                               f"earnings_alert 규칙 준수 (내용 있음: {ea_en[:60]})"))
    elif violations:
        report.add(CheckResult("C1-earnings_alert", False,
                               "\n    ".join(violations)))
    else:
        report.add(CheckResult("C1-earnings_alert", True,
                               "earnings_alert 빈 문자열 — ≤14일 실적 없음 (정상)"))


def check_global_sources(brief: dict, report: VerificationReport):
    """C2: global_context 소스가 소셜미디어/Twitter를 사용하지 않는지."""
    gc = brief.get("global_context", {})
    issues_list = gc.get("issues", [])
    violations = []

    for iss in issues_list:
        src = (iss.get("source_hint") or "").lower()
        hit = next((p for p in SOCIAL_PATTERNS if p in src), None)
        if hit:
            violations.append(
                f"rank={iss.get('rank')}: 소셜미디어 소스 감지 ({hit!r}) — {src!r}"
            )
        # confirmed인데 알려진 기관 없으면 경고
        if iss.get("confidence") == "confirmed" and not any(o in src for o in ACCEPTED_OUTLETS):
            violations.append(
                f"rank={iss.get('rank')}: confidence=confirmed이지만 인정 소스 없음 (source={src!r})"
            )

    if violations:
        report.add(CheckResult(
            "C2-글로벌 소스 품질", False,
            "\n    ".join(violations)
        ))
    else:
        report.add(CheckResult("C2-글로벌 소스 품질", True,
                               f"글로벌 이슈 {len(issues_list)}건 모두 인정 소스 사용"))


def check_confidence_language(brief: dict, report: VerificationReport):
    """C3: developing/unverified 이슈가 big_picture에서 확정 사실로 서술되는지."""
    gc = brief.get("global_context", {})
    dev_issues = [i for i in gc.get("issues", [])
                  if i.get("confidence") in ("developing", "unverified")]
    if not dev_issues:
        report.add(CheckResult("C3-confidence 언어", True,
                               "developing/unverified 이슈 없음 — N/A"))
        return

    bp_summary = (brief.get("big_picture", {}).get("summary_en") or "").lower()
    bp_summary_ko = (brief.get("big_picture", {}).get("summary_ko") or "").lower()
    exec_en = " ".join(brief.get("executive_bullets_en", [])).lower()

    HEDGE_EN = ["reports indicate", "early reports", "reports suggest",
                "according to initial", "unverified", "unconfirmed", "reportedly",
                "sources indicate", "early developments"]
    HEDGE_KO = ["보도에 따르면", "초기 보도", "미확인 보도", "보도 기준", "보도에 의하면",
                "초기 보도 기준", "보고에 따르면"]

    has_hedge_en = any(h in bp_summary or h in exec_en for h in HEDGE_EN)
    has_hedge_ko = any(h in bp_summary_ko for h in HEDGE_KO)

    if not has_hedge_en and not has_hedge_ko:
        dev_titles = [i.get("title_en", "")[:50] for i in dev_issues]
        report.add(CheckResult(
            "C3-confidence 언어", False,
            f"developing 이슈 {len(dev_issues)}건인데 big_picture에 유보 표현 없음: {dev_titles}",
            "warning"
        ))
    else:
        report.add(CheckResult("C3-confidence 언어", True,
                               f"developing 이슈 → 유보 표현 확인 (en={has_hedge_en}, ko={has_hedge_ko})"))


# ─── 카테고리 D: 완결성 ───────────────────────────────────────────────────────

def check_watchlist_completeness(brief: dict, report: VerificationReport):
    """D1: 워치리스트에 22개 종목이 모두 있는지, 순서 확인."""
    wl_symbols = [w.get("symbol") for w in brief.get("watchlist", [])]
    missing = [s for s in EXPECTED_SYMBOLS if s not in wl_symbols]
    extra = [s for s in wl_symbols if s not in EXPECTED_SYMBOLS]
    order_ok = wl_symbols == EXPECTED_SYMBOLS

    issues = []
    if missing:
        issues.append(f"누락 종목: {missing}")
    if extra:
        issues.append(f"초과 종목: {extra}")
    if not order_ok and not missing:
        issues.append(f"순서 불일치 (기대: {EXPECTED_SYMBOLS[:5]}...)")

    if issues:
        report.add(CheckResult("D1-워치리스트 완결성", False, " / ".join(issues)))
    else:
        report.add(CheckResult("D1-워치리스트 완결성", True,
                               f"{len(wl_symbols)}/21 종목, 순서 정상"))


def check_spotlight_count(brief: dict, report: VerificationReport):
    """D2: spotlight 종목 수가 2-4개인지."""
    spots = brief.get("spotlight", [])
    n = len(spots)
    if 2 <= n <= 4:
        report.add(CheckResult("D2-spotlight 수", True, f"{n}개 (정상 범위 2-4)"))
    else:
        report.add(CheckResult("D2-spotlight 수", False, f"{n}개 — 2-4 범위 벗어남",
                               "warning"))


def check_required_fields(brief: dict, report: VerificationReport):
    """D3: 필수 최상위 필드 존재 및 비어있지 않은지."""
    REQUIRED = [
        "headline_en", "headline_ko",
        "executive_bullets_en", "executive_bullets_ko",
        "market_mood", "big_picture", "sector_analysis",
        "spotlight", "watchlist",
        "today_checkpoints_en", "today_checkpoints_ko",
    ]
    missing, empty = [], []
    for f in REQUIRED:
        val = brief.get(f)
        if val is None:
            missing.append(f)
        elif isinstance(val, (str, list, dict)) and not val:
            empty.append(f)

    # watchlist 각 종목에 analysis 있는지
    for w in brief.get("watchlist", []):
        if not w.get("analysis_en") and not w.get("analysis_ko"):
            empty.append(f"watchlist/{w.get('symbol')}/analysis")

    if missing or empty:
        detail = []
        if missing: detail.append(f"누락: {missing}")
        if empty:   detail.append(f"빈값: {empty[:5]}{'...' if len(empty)>5 else ''}")
        report.add(CheckResult("D3-필수 필드", False, " / ".join(detail)))
    else:
        report.add(CheckResult("D3-필수 필드", True, "모든 필수 필드 존재 및 비어있지 않음"))


def check_jargon_explanations(brief: dict, report: VerificationReport):
    """D4: 핵심 투자 용어가 설명 없이 사용되지 않는지 (샘플 검사)."""
    # 워치리스트 중 처음 5개 분석 텍스트를 샘플로 확인
    sample_texts = []
    for w in brief.get("watchlist", [])[:5]:
        sample_texts.append(w.get("analysis_en", "") + " " + w.get("analysis_ko", ""))
    combined = " ".join(sample_texts)

    missing_explanations = []
    for term_regex, hint_words in JARGON_PAIRS:
        if re.search(term_regex, combined):
            # 설명 힌트 중 하나라도 전체 브리핑에 있는지 확인
            all_text = json.dumps(brief, ensure_ascii=False).lower()
            has_explanation = any(h.lower() in all_text for h in hint_words)
            if not has_explanation:
                term_name = re.sub(r"\\b", "", term_regex)
                missing_explanations.append(term_name)

    if missing_explanations:
        report.add(CheckResult(
            "D4-용어 설명", False,
            f"설명 없이 사용된 용어: {missing_explanations}", "warning"
        ))
    else:
        report.add(CheckResult("D4-용어 설명", True,
                               "주요 투자 용어 설명 포함 확인 (샘플 검사)"))


def check_briefing_copy_quality(brief: dict, report: VerificationReport):
    """D5: 공유 본문(briefing_en/ko 또는 executive_bullets) 충실도."""
    # 핵심 공유 가능 컨텐츠의 길이와 품질 확인
    issues = []

    headline_en = brief.get("headline_en", "")
    if len(headline_en) > 120:
        issues.append(f"headline_en 길이 초과: {len(headline_en)}자 (≤120 기준)")
    if len(headline_en) < 30:
        issues.append(f"headline_en 너무 짧음: {len(headline_en)}자")

    headline_ko = brief.get("headline_ko", "")
    if len(headline_ko) > 30:
        issues.append(f"headline_ko 길이 초과: {len(headline_ko)}자 (≤30 기준)")

    bullets_en = brief.get("executive_bullets_en", [])
    if len(bullets_en) < 3:
        issues.append(f"executive_bullets_en 부족: {len(bullets_en)}개 (3개 기준)")
    for i, b in enumerate(bullets_en):
        if len(b) < 20:
            issues.append(f"executive_bullets_en[{i}] 너무 짧음: {b!r}")

    checkpoints = brief.get("today_checkpoints_en", [])
    if len(checkpoints) < 2:
        issues.append(f"today_checkpoints_en 부족: {len(checkpoints)}개")

    # spotlight에 watch_level 있는지
    for s in brief.get("spotlight", []):
        if not s.get("watch_level_en") and not s.get("watch_level_ko"):
            issues.append(f"spotlight/{s.get('symbol')}: watch_level 없음")

    if issues:
        report.add(CheckResult(
            "D5-공유 본문 품질", False,
            "\n    ".join(issues), "warning"
        ))
    else:
        report.add(CheckResult("D5-공유 본문 품질", True,
                               "공유 본문 형식 및 길이 정상"))


# ─── 카테고리 E: Claude 보조 검증 ────────────────────────────────────────────

def _call_claude(prompt: str, timeout: int) -> Optional[str]:
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    try:
        result = subprocess.run(
            [CLAUDE_CMD, "-p", prompt,
             "--allowedTools", "WebSearch",
             "--output-format", "json"],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if result.returncode != 0:
            return None
        try:
            envelope = json.loads(result.stdout)
            return envelope.get("result", "") or ""
        except json.JSONDecodeError:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def claude_verify(brief: dict, report: VerificationReport):
    """E1-E5: Claude로 글로벌 이슈 현재성, 중요 뉴스 누락, 분위기·섹터 정합성, 종목 오류 검증.

    Grok 자기검증(circular) 대신 독립 LLM(Claude)이 검증.
    claude -p 모드로 호출 → JSON 파싱 → CheckResult 생성.
    """
    now = datetime.now(timezone.utc)
    now_kst = (now + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    gc = brief.get("global_context", {})
    gc_issues = gc.get("issues", [])
    bp = brief.get("big_picture", {})
    sa = brief.get("sector_analysis", {})
    mood = brief.get("market_mood", {})

    gc_summary = "\n".join([
        f"[rank={i.get('rank')}][{i.get('confidence')}] {i.get('title_en')}"
        f"\n  State: {i.get('current_state_en','')}"
        f"\n  Source: {i.get('source_hint','')}"
        for i in gc_issues
    ]) or "(no global issues)"

    wl_summary = "\n".join([
        f"{w['symbol']}: action={w.get('action','?')} mood={w.get('sentiment_mood','?')} "
        f"— {(w.get('analysis_en','')[:80])}..."
        for w in brief.get("watchlist", [])[:10]
    ])

    prompt = f"""You are an independent financial fact-checker reviewing an AI-generated morning briefing.
Today is {now_kst}. The briefing was generated at {brief.get('generated_at','?')}.

Use your web search capability to verify the following content against current real-world information.

━━━ GLOBAL CONTEXT IN BRIEFING ━━━
{gc_summary}

━━━ MARKET MOOD ━━━
Traffic light: {mood.get('traffic_light','?')} | Score: {mood.get('score','?')}
Headline: {brief.get('headline_en','?')}
Summary: {bp.get('summary_en','?')}

━━━ SECTOR ANALYSIS ━━━
Leaders: {sa.get('leaders_en','?')}
Laggards: {sa.get('laggards_en','?')}
Rotation: {sa.get('rotation_signal_en','?')}

━━━ WATCHLIST ACTIONS (first 10) ━━━
{wl_summary[:2000]}

━━━ VERIFICATION TASKS ━━━
1. GLOBAL ISSUES: Search the web. For each issue in the briefing, is it accurate and current as of {now_kst}?
   Has anything changed in the last 24h that makes an issue outdated or inaccurate?

2. MISSING NEWS: What are the top market-moving events in the last 24h NOT covered in this briefing?
   Focus only on events materially affecting US stocks in our watchlist.

3. MARKET MOOD: Is the traffic_light/headline accurate given current market conditions?

4. SECTOR TRENDS: Does the sector analysis match current market dynamics?

5. STOCK ACCURACY: Are there any factual errors in the watchlist analysis or mood descriptions?

Output ONLY valid JSON (no markdown fences):
{{
  "verified_at": "{now_iso}",
  "global_issues": [
    {{
      "rank": 1,
      "title": "...",
      "accuracy": "accurate|outdated|inaccurate|unverifiable",
      "note": ""
    }}
  ],
  "missing_major_news": [
    {{
      "title": "...",
      "impact": "which watchlist stocks affected and how",
      "severity": "critical|important|minor"
    }}
  ],
  "market_mood_check": {{
    "assessment": "accurate|questionable|inaccurate",
    "note": ""
  }},
  "sector_check": {{
    "assessment": "accurate|questionable|inaccurate",
    "note": ""
  }},
  "stock_errors": [
    {{
      "symbol": "TICKER",
      "error": "description of factual error"
    }}
  ],
  "overall_score": 85,
  "summary": "2-3 sentence overall assessment"
}}"""

    print(f"[INFO] Claude 검증 호출 중 (최대 {VERIFY_TIMEOUT}초)...")
    raw = _call_claude(prompt, timeout=VERIFY_TIMEOUT)
    if not raw:
        report.add(CheckResult("E-Claude 검증", False,
                               "Claude 호출 실패 또는 타임아웃", "warning"))
        return {}

    # markdown 코드 펜스 제거 후 JSON 추출
    stripped = re.sub(r"```(?:json)?\s*", "", raw)
    stripped = re.sub(r"```\s*", "", stripped)
    m = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not m:
        preview = raw[:300].replace("\n", " ") if raw else "(empty)"
        print(f"[DEBUG] Claude raw response: {preview!r}")
        report.add(CheckResult("E-Claude 검증", False,
                               f"Claude 응답에서 JSON 추출 실패 (응답: {preview[:120]!r})", "warning"))
        return {}

    try:
        claude_data = json.loads(m.group())
    except json.JSONDecodeError as e:
        report.add(CheckResult("E-Claude 검증", False,
                               f"Claude JSON 파싱 실패: {e}", "warning"))
        return {}

    # E1: 글로벌 이슈 정확성
    gi_results = claude_data.get("global_issues", [])
    inaccurate = [g for g in gi_results if g.get("accuracy") not in ("accurate", "unverifiable")]
    if inaccurate:
        details = [f"rank={g.get('rank','?')}: {g.get('accuracy')} — {g.get('note','')}"
                   for g in inaccurate]
        report.add(CheckResult("E1-글로벌 이슈 현재성", False,
                               "\n    ".join(details), "warning"))
    else:
        report.add(CheckResult("E1-글로벌 이슈 현재성", True,
                               f"{len(gi_results)}개 이슈 모두 정확 또는 검증 불가"))

    # E2: 누락 뉴스
    missing_news = claude_data.get("missing_major_news", [])
    critical_missing = [n for n in missing_news if n.get("severity") == "critical"]
    if critical_missing:
        details = [f"{n.get('title','')} (영향: {n.get('impact','')})" for n in critical_missing]
        report.add(CheckResult("E2-중요 뉴스 누락", False,
                               f"critical 누락 {len(critical_missing)}건:\n    " + "\n    ".join(details),
                               "error"))
    elif missing_news:
        details = [n.get("title", "") for n in missing_news[:3]]
        report.add(CheckResult("E2-중요 뉴스 누락", True,
                               f"minor/important 누락 {len(missing_news)}건 (critical 없음): {details}",
                               "info"))
    else:
        report.add(CheckResult("E2-중요 뉴스 누락", True, "주요 뉴스 누락 없음"))

    # E3: 시장 분위기 정확성
    mc = claude_data.get("market_mood_check", {})
    if mc.get("assessment") == "inaccurate":
        report.add(CheckResult("E3-시장 분위기 정확성", False,
                               mc.get("note", ""), "warning"))
    else:
        report.add(CheckResult("E3-시장 분위기 정확성", True,
                               f"{mc.get('assessment','?')}: {mc.get('note','')}"))

    # E4: 섹터 동향 정확성
    sc = claude_data.get("sector_check", {})
    if sc.get("assessment") == "inaccurate":
        report.add(CheckResult("E4-섹터 동향 정확성", False,
                               sc.get("note", ""), "warning"))
    else:
        report.add(CheckResult("E4-섹터 동향 정확성", True,
                               f"{sc.get('assessment','?')}: {sc.get('note','')}"))

    # E5: 종목 오류
    stock_errors = claude_data.get("stock_errors", [])
    if stock_errors:
        details = [f"{e.get('symbol')}: {e.get('error','')}" for e in stock_errors[:5]]
        report.add(CheckResult("E5-종목 설명 정확성", False,
                               "\n    ".join(details), "warning"))
    else:
        report.add(CheckResult("E5-종목 설명 정확성", True, "종목 설명 오류 없음"))

    return claude_data


# ─── 리포트 출력 ──────────────────────────────────────────────────────────────

def print_report(report: VerificationReport):
    """콘솔에 카테고리별 검증 결과 출력."""
    width = 68
    print()
    print("╔" + "═" * width + "╗")
    title = "브리핑 검증 리포트 (Morning Briefing Verification)"
    print("║" + title.center(width) + "║")
    sub = report.run_at[:16] + " KST"
    print("║" + sub.center(width) + "║")
    print("╚" + "═" * width + "╝")
    print()

    # 카테고리별 출력
    categories = {
        "A": "데이터 바인딩 (SniperBoard → 브리핑)",
        "B": "섹터동향 정확성",
        "C": "규칙 준수 (earnings / 소스 / confidence)",
        "D": "완결성 (종목 수 / 필드 / 용어 / 공유 본문)",
        "E": "Claude 독립 검증 (글로벌 이슈 / 뉴스 / 분위기·섹터)",
    }

    for cat_prefix, cat_name in categories.items():
        cat_results = [r for r in report.results if r.name.startswith(cat_prefix)]
        if not cat_results:
            continue
        cat_pass = all(r.passed for r in cat_results)
        print(f"[{cat_prefix}] {cat_name}  {'✅' if cat_pass else '❌'}")
        for r in cat_results:
            # 세부 항목: 이름에서 카테고리 접두어 제거
            short = r.name.split("-", 1)[-1] if "-" in r.name else r.name
            prefix = "  " + r.emoji() + " " + short + ":"
            # 멀티라인 detail 처리
            detail_lines = r.detail.split("\n")
            print(f"{prefix} {detail_lines[0]}")
            for line in detail_lines[1:]:
                print(f"    {line}")
        print()

    # 최종 결과
    n_err = len(report.errors())
    n_warn = len(report.warnings())
    claude_score = report.grok_report.get("overall_score", "N/A")
    claude_summary = report.grok_report.get("summary", "")

    print("─" * (width + 2))
    if report.passed():
        print(f"✅ PASS — 오류 없음  (경고 {n_warn}건  Claude 점수: {claude_score}/100)")
    else:
        print(f"❌ FAIL — 오류 {n_err}건  경고 {n_warn}건  Claude 점수: {claude_score}/100")
    if claude_summary:
        print(f"   Claude: {claude_summary[:120]}")
    print()


# ─── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="브리핑 사실 검증 스크립트")
    parser.add_argument("--date", help="히스토리 날짜 (YYYY-MM-DD). 미지정 시 latest.json")
    parser.add_argument("--skip-claude", action="store_true", help="Claude 호출 생략 (자동화 데이터 검증만)")
    parser.add_argument("--skip-grok", action="store_true", help="(deprecated) --skip-claude와 동일")
    parser.add_argument("--json", action="store_true", help="briefing/verify_YYYY-MM-DD.json 저장")
    args = parser.parse_args()

    # 브리핑 파일 결정
    if args.date:
        briefing_path = REPO_PATH / "briefing" / "history" / f"{args.date}.json"
    else:
        briefing_path = REPO_PATH / "briefing" / "latest.json"

    if not briefing_path.exists():
        print(f"[ERROR] 파일 없음: {briefing_path}", file=sys.stderr)
        sys.exit(1)

    with open(briefing_path, encoding="utf-8") as f:
        brief = json.load(f)

    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M")
    report = VerificationReport(
        briefing_path=str(briefing_path),
        generated_at=brief.get("generated_at", "?"),
        run_at=now_kst,
    )

    print(f"[INFO] 브리핑 검증 시작: {now_kst} KST")
    print(f"[INFO] 대상 파일: {briefing_path}")
    print(f"[INFO] 브리핑 생성일시: {brief.get('generated_at','?')}")
    print()

    # ── A: 데이터 바인딩 ──
    print("[A] SniperBoard 데이터 바인딩 확인 중...")
    check_structure_binding(brief, report)
    check_action_rules(brief, report)
    check_macro_values(brief, report)
    check_regime_classification(brief, report)
    check_premarket_in_spotlight(brief, report)

    # ── B: 섹터동향 ──
    print("[B] 섹터동향 정확성 확인 중...")
    check_sector_leaders_vs_downtrend(brief, report)
    check_sector_signals(brief, report)

    # ── C: 규칙 준수 ──
    print("[C] 규칙 준수 확인 중...")
    check_earnings_alert_rule(brief, report)
    check_global_sources(brief, report)
    check_confidence_language(brief, report)

    # ── D: 완결성 ──
    print("[D] 완결성 확인 중...")
    check_watchlist_completeness(brief, report)
    check_spotlight_count(brief, report)
    check_required_fields(brief, report)
    check_jargon_explanations(brief, report)
    check_briefing_copy_quality(brief, report)

    # ── E: Claude 독립 검증 ──
    skip = args.skip_claude or args.skip_grok
    if not skip:
        print("[E] Claude 독립 검증 시작...")
        claude_data = claude_verify(brief, report)
        report.grok_report = claude_data
    else:
        print("[E] Claude 검증 생략 (--skip-claude)")

    # 리포트 출력
    print_report(report)

    # JSON 저장
    if args.json:
        import datetime as dt
        date_str = args.date if args.date else dt.date.today().isoformat()
        out_path = REPO_PATH / "briefing" / f"verify_{date_str}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report.as_dict(), f, ensure_ascii=False, indent=2)
        print(f"[INFO] 검증 결과 저장: {out_path}")

    sys.exit(0 if report.passed() else 1)


if __name__ == "__main__":
    main()
