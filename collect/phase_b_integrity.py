"""Phase B1/B2 mechanical integrity checks for briefing snapshots (MSD side).

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
            "warn_count": sum(1 for i in self.issues if i.severity == "warn"),
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

# ── Phase B2: cross-section consistency (no ticker/theme hardcodes) ───────
# See sniperboard/backend/core/briefing_verify.py for design notes.
_RE_UNAFFECTED = re.compile(
    r"\b([A-Z]{1,5})\s*:\s*"
    r"(unaffected|not\s+affected|no\s+direct\s+impact|no\s+impact|영향\s*없음|무관|해당\s*없음)",
    re.I,
)
_RE_POST_PCT = re.compile(
    r"(?:post[- ]?market|pre[- ]?market|after[- ]?hours|개장\s*전|장\s*마감\s*후)[^\n%]{0,48}?"
    r"([+-]?\d+(?:\.\d+)?)\s*%",
    re.I,
)
_RE_SESSION_EVENT = re.compile(
    r"(post[- ]?earnings|earnings\s+beat|earnings\s+miss|earnings\s+surge|"
    r"실적\s*(상회|하회|호조|서프라이즈|급등|발표)|"
    r"guidance\s+(raise|cut)|가이던스\s*(상향|하향))",
    re.I,
)
_RE_SESSION_ANCHOR = re.compile(
    r"(post[- ]?market|pre[- ]?market|after[- ]?hours|earnings|closed?\s+at|"
    r"surge|selloff|rally|급등|급락|실적|종가|애프터|프리마켓|개장\s*전|"
    r"[+-]?\d+(?:\.\d+)?\s*%|\d{4}-\d{2}-\d{2})",
    re.I,
)
_RE_EVERGREEN = re.compile(
    r"\b(linger(?:s|ing)?|remain(?:s|ing)?|ongoing|persist(?:s|ing)?|"
    r"continues?\s+to|still\s+a\s+risk)\b|지속|여전|상존|리스크\s*속",
    re.I,
)
_LEXICAL_STOP = frozenset({
    "with", "from", "that", "this", "have", "will", "into", "over", "under",
    "risk", "risks", "market", "markets", "stocks", "stock", "today", "after",
    "before", "while", "amid", "sets", "tone", "jump", "jumps", "surge", "surges",
    "still", "remain", "remains", "ongoing", "update", "report", "reports",
    "us", "china", "global", "major", "early", "latest", "shift", "shifts",
    "급등", "급락", "시장", "리스크", "오늘", "전일", "속", "중", "및", "대한",
})


def _headline_blob(briefing: dict) -> str:
    return f"{briefing.get('headline_en') or ''} {briefing.get('headline_ko') or ''}"


def _tokenize_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}|[가-힣]{2,}", (text or "").lower()))


def _issue_title_tokens(issue: dict) -> set[str]:
    t = f"{issue.get('title_en') or ''} {issue.get('title_ko') or ''}"
    return _tokenize_tokens(t)


def _issue_content_tokens(issue: dict) -> set[str]:
    parts: list[str] = []
    for k in (
        "title_en", "title_ko", "summary_en", "summary_ko",
        "current_state_en", "current_state_ko",
    ):
        if issue.get(k):
            parts.append(str(issue[k]))
    return _tokenize_tokens(" ".join(parts))


def parse_unaffected_tickers(*texts: str) -> set[str]:
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        for m in _RE_UNAFFECTED.finditer(str(text)):
            found.add(m.group(1).upper())
    return found


def _token_hits(a: set[str], b: set[str]) -> set[str]:
    hits: set[str] = set()
    for x in a:
        if x in _LEXICAL_STOP:
            continue
        for y in b:
            if y in _LEXICAL_STOP:
                continue
            if x == y or (len(x) >= 2 and len(y) >= 2 and (x in y or y in x)):
                hits.add(x)
                break
    return hits


def headline_binds_issue(headline: str, issue: dict) -> bool:
    ht = _tokenize_tokens(headline)
    it = _issue_content_tokens(issue)
    if not ht or not it:
        return False
    hits = _token_hits(ht, it)
    if len(hits) >= 2:
        return True
    if any(len(h) >= 4 for h in hits):
        return True
    return False


def _ticker_in_headline(headline: str, sym: str) -> bool:
    return bool(re.search(rf"\b{re.escape(sym)}\b", headline, re.I))


def _session_catalyst_for_symbol(briefing: dict, sym: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "post_pct": None,
        "has_session_event_language": False,
        "texts": [],
    }
    blobs: list[str] = []
    for block_key in ("spotlight", "watchlist"):
        for row in briefing.get(block_key) or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper() != sym:
                continue
            for pk in ("post_market_pct", "pre_market_pct", "post_pct", "pre_pct"):
                if row.get(pk) is not None and out["post_pct"] is None:
                    try:
                        out["post_pct"] = float(row[pk])
                    except (TypeError, ValueError):
                        pass
            for k in ("why_en", "why_ko", "analysis_en", "analysis_ko", "analysis"):
                if row.get(k):
                    blobs.append(str(row[k]))
    text = "\n".join(blobs)
    out["texts"] = blobs
    if out["post_pct"] is None:
        m = _RE_POST_PCT.search(text)
        if m:
            try:
                out["post_pct"] = float(m.group(1))
            except ValueError:
                pass
    if _RE_SESSION_EVENT.search(text):
        out["has_session_event_language"] = True
    alert = " ".join(
        str(briefing.get(k) or "")
        for k in ("earnings_alert_en", "earnings_alert_ko")
    )
    for lk in ("today_checkpoints_en", "today_checkpoints_ko"):
        for item in briefing.get(lk) or []:
            alert += " " + str(item)
    if re.search(rf"\b{re.escape(sym)}\b", alert, re.I) and re.search(
        r"earnings|실적", alert, re.I
    ):
        out["has_session_event_language"] = True
    return out


def check_false_catalyst_attribution(briefing: dict) -> list[VerifyIssue]:
    issues: list[VerifyIssue] = []
    headline = _headline_blob(briefing)
    if not headline.strip():
        return issues
    for iss in (briefing.get("global_context") or {}).get("issues") or []:
        if not isinstance(iss, dict):
            continue
        unaff = parse_unaffected_tickers(
            str(iss.get("asymmetric_impact_en") or ""),
            str(iss.get("asymmetric_impact_ko") or ""),
        )
        if not unaff:
            continue
        if not headline_binds_issue(headline, iss):
            continue
        cat = iss.get("category") or "?"
        for sym in sorted(unaff):
            if not _ticker_in_headline(headline, sym):
                continue
            cat_ev = _session_catalyst_for_symbol(briefing, sym)
            post = cat_ev.get("post_pct")
            strong = (
                (post is not None and abs(float(post)) >= 5.0)
                or cat_ev.get("has_session_event_language")
            )
            if strong:
                issues.append(VerifyIssue(
                    "B2-false-catalyst",
                    (
                        f"{sym}: headline co-binds to global issue ({cat}) that marks "
                        f"{sym} unaffected; same snapshot has session evidence "
                        f"post/pre_pct={post} session_event={cat_ev.get('has_session_event_language')}"
                    ),
                    severity="fail",
                ))
            else:
                issues.append(VerifyIssue(
                    "B2-false-catalyst",
                    (
                        f"{sym}: headline co-binds to global issue ({cat}) while "
                        f"asymmetric_impact marks {sym} unaffected"
                    ),
                    severity="fail",
                ))
    return issues


def check_theme_recurrence(
    briefing: dict,
    history: list[dict] | None,
    *,
    min_streak: int = 5,
    jaccard: float = 0.25,
) -> list[VerifyIssue]:
    issues: list[VerifyIssue] = []
    if not history:
        return issues
    headline = _headline_blob(briefing)
    seen: set[tuple[str, str]] = set()
    for iss in (briefing.get("global_context") or {}).get("issues") or []:
        if not isinstance(iss, dict):
            continue
        cat = str(iss.get("category") or "")
        if not cat:
            continue
        tokens = _issue_title_tokens(iss)
        direction = str(iss.get("direction") or "")
        tier = str(iss.get("tier") or "")

        cat_streak = 0
        for past in reversed(history):
            past_cats = {
                str(pi.get("category") or "")
                for pi in ((past.get("global_context") or {}).get("issues") or [])
                if isinstance(pi, dict)
            }
            if cat in past_cats:
                cat_streak += 1
            else:
                break

        sim_streak = 0
        for past in reversed(history):
            matched = False
            for pi in (past.get("global_context") or {}).get("issues") or []:
                if not isinstance(pi, dict) or str(pi.get("category") or "") != cat:
                    continue
                pt = _issue_title_tokens(pi)
                if not tokens or not pt:
                    continue
                j = len(tokens & pt) / len(tokens | pt)
                if j >= jaccard:
                    matched = True
                    break
            if matched:
                sim_streak += 1
            else:
                break

        stable = direction.startswith("stable") or direction in ("", "stable_elevated", "stable_fading")
        if cat_streak >= min_streak:
            key = ("B2-theme-recurrence", cat)
            if key not in seen:
                seen.add(key)
                sev = "warn"
                if headline_binds_issue(headline, iss) and stable and tier == "ongoing":
                    sev = "fail"
                issues.append(VerifyIssue(
                    "B2-theme-recurrence",
                    f"category={cat} present on {cat_streak}+ consecutive prior days "
                    f"(direction={direction or 'n/a'}, tier={tier or 'n/a'})",
                    severity=sev,
                ))
        if sim_streak >= min_streak and stable:
            key = ("B2-theme-stale", cat)
            if key not in seen:
                seen.add(key)
                issues.append(VerifyIssue(
                    "B2-theme-stale",
                    f"category={cat} near-duplicate title tokens for {sim_streak}+ prior days "
                    f"with direction={direction or 'stable'} (no material state change signal)",
                    severity="warn",
                ))
    return issues


def check_day_window_fitness(briefing: dict) -> list[VerifyIssue]:
    issues: list[VerifyIssue] = []
    headline = _headline_blob(briefing)
    if not headline.strip():
        return issues
    session_hits = len(_RE_SESSION_ANCHOR.findall(headline))
    evergreen_hits = len(_RE_EVERGREEN.findall(headline))
    if evergreen_hits > 0 and session_hits == 0:
        issues.append(VerifyIssue(
            "B2-day-window",
            "headline reads as evergreen/ongoing risk without last-session or premarket anchor",
            severity="warn",
        ))
    bullets = list(briefing.get("executive_bullets_en") or []) + list(
        briefing.get("executive_bullets_ko") or []
    )
    if bullets:
        with_anchor = sum(1 for b in bullets if _RE_SESSION_ANCHOR.search(str(b) or ""))
        if with_anchor == 0 and any(_RE_EVERGREEN.search(str(b) or "") for b in bullets):
            issues.append(VerifyIssue(
                "B2-day-window",
                "executive_bullets lack last-session anchors and use evergreen risk phrasing",
                severity="warn",
            ))
    return issues


def verify_briefing_integrity(
    briefing: dict,
    *,
    upcoming_earnings: list | None = None,
    price_table: dict[str, float] | None = None,
    as_of: Optional[date] = None,
    history: list[dict] | None = None,
) -> VerifyResult:
    issues: list[VerifyIssue] = []
    today = as_of or date.today()
    cal = build_calendar(upcoming_earnings)
    if briefing.get("_earnings_calendar"):
        cal.update(build_calendar(briefing["_earnings_calendar"]))

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

    issues.extend(check_false_catalyst_attribution(briefing))
    issues.extend(check_theme_recurrence(briefing, history))
    issues.extend(check_day_window_fitness(briefing))

    fails = [i for i in issues if i.severity == "fail"]
    return VerifyResult(passed=len(fails) == 0, issues=issues)


def scan_briefing_artifacts(
    latest: dict,
    history: list[dict] | None = None,
    *,
    upcoming_earnings: list | None = None,
    price_table: dict[str, float] | None = None,
    as_of: Optional[date] = None,
) -> dict[str, Any]:
    """Machine-readable scan of one briefing (+ optional prior history)."""
    hist = list(history or [])
    result = verify_briefing_integrity(
        latest,
        upcoming_earnings=upcoming_earnings,
        price_table=price_table,
        as_of=as_of,
        history=hist,
    )
    codes = [i.code for i in result.issues]
    return {
        "passed": result.passed,
        "fail_count": sum(1 for i in result.issues if i.severity == "fail"),
        "warn_count": sum(1 for i in result.issues if i.severity == "warn"),
        "codes": codes,
        "issues": [asdict(i) for i in result.issues],
        "flags": {
            "false_catalyst": any(c == "B2-false-catalyst" for c in codes),
            "theme_recurrence": any(c == "B2-theme-recurrence" for c in codes),
            "theme_stale": any(c == "B2-theme-stale" for c in codes),
            "day_window": any(c == "B2-day-window" for c in codes),
        },
        "history_n": len(hist),
        "generated_at": latest.get("generated_at"),
        "headline_en": latest.get("headline_en"),
        "headline_ko": latest.get("headline_ko"),
    }
