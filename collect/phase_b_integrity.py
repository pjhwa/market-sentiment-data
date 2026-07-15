"""Phase B1 mechanical integrity checks for briefing snapshots (MSD side).

Mirrors sniperboard/backend/core/briefing_verify.py rules so verify_briefing
can fail-closed without importing SniperBoard.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional


@dataclass
class VerifyIssue:
    code: str
    message: str
    severity: str = "fail"


@dataclass
class VerifyResult:
    passed: bool
    issues: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "issues": [asdict(i) for i in self.issues],
            "fail_count": sum(1 for i in self.issues if i.severity == "fail"),
        }


def _parse_date(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def build_calendar(upcoming: list | None) -> dict[str, date]:
    cal: dict[str, date] = {}
    for it in upcoming or []:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").upper()
        ed = _parse_date(it.get("earnings_date") or it.get("report_date"))
        if sym and ed:
            cal[sym] = ed
    return cal


_RE_ALREADY = re.compile(
    r"(?P<sym>[A-Z]{1,5})\s*[^\n]{0,40}?오늘\s*(?:미국\s*)?장\s*마감\s*후\s*실적\s*발표됨",
    re.I,
)
_RE_DAYS = re.compile(
    r"(?P<sym>[A-Z]{1,5})\s*(?:[^\n]{0,30}?)(?P<n>\d+)\s*일\s*후",
    re.I,
)
_RE_PCT = re.compile(r"(?<![A-Za-z0-9])([+-]?\d+(?:\.\d+)?)\s*%")
_RE_PRICE = re.compile(r"\$([0-9]{2,5}(?:\.[0-9]+)?)")


def verify_briefing_integrity(
    briefing: dict,
    *,
    upcoming_earnings: list | None = None,
    price_table: dict[str, float] | None = None,
    as_of: Optional[date] = None,
) -> VerifyResult:
    issues: list[VerifyIssue] = []
    today = as_of or date.today()
    cal = build_calendar(upcoming_earnings)

    texts: list[str] = []
    for k in ("earnings_alert_ko", "earnings_alert_en", "headline_ko", "headline_en"):
        if briefing.get(k):
            texts.append(str(briefing[k]))
    for lk in ("today_checkpoints_ko", "today_checkpoints_en"):
        for item in briefing.get(lk) or []:
            texts.append(str(item))
    for w in briefing.get("watchlist") or []:
        if isinstance(w, dict):
            for k in ("analysis_ko", "analysis_en", "analysis"):
                if w.get(k):
                    texts.append(str(w[k]))
    blob = "\n".join(texts)

    for m in _RE_ALREADY.finditer(blob):
        sym = m.group("sym").upper()
        ed = cal.get(sym)
        if ed and ed > today:
            issues.append(VerifyIssue("B1-rel-already", f"{sym}: already-reported text but future date {ed}"))

    for m in _RE_DAYS.finditer(blob):
        sym = m.group("sym").upper()
        claimed = int(m.group("n"))
        ed = cal.get(sym)
        if not ed:
            continue
        live = (ed - today).days
        if live >= 0 and abs(claimed - live) > 1:
            issues.append(VerifyIssue("B1-rel-day", f"{sym}: text {claimed}d vs live {live}d"))

    for w in briefing.get("watchlist") or []:
        if not isinstance(w, dict):
            continue
        mood = str(w.get("sentiment_mood") or "").lower()
        if mood not in ("optimistic", "euphoric"):
            continue
        text = " ".join(str(w.get(k) or "") for k in ("analysis_ko", "analysis_en", "analysis"))
        m = _RE_PCT.search(text)
        if m and float(m.group(1)) <= -3.0:
            issues.append(VerifyIssue(
                "B1-mood-drop",
                f"{w.get('symbol')}: mood={mood} with drop {m.group(1)}%",
            ))

    if price_table:
        for w in briefing.get("watchlist") or []:
            if not isinstance(w, dict):
                continue
            sym = str(w.get("symbol") or "").upper()
            truth = price_table.get(sym)
            if truth is None or truth <= 0:
                continue
            text = " ".join(str(w.get(k) or "") for k in ("analysis_ko", "analysis_en", "analysis"))
            m = _RE_PRICE.search(text)
            if not m:
                continue
            claimed = float(m.group(1))
            if abs(claimed - truth) / truth > 0.03:
                issues.append(VerifyIssue(
                    "B1-price-bind",
                    f"{sym}: ${claimed} vs table ${truth:.2f}",
                ))

    fails = [i for i in issues if i.severity == "fail"]
    return VerifyResult(passed=len(fails) == 0, issues=issues)
