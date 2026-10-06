"""
Grok availability monitor — diagnose why hermes/Grok calls fail and alert on state changes.

`hermes -z` exits 0 with EMPTY stdout even when the provider rejects the request
(e.g. 403 spending-limit), so collectors only see "빈 응답". When a collector has exhausted
its retries on empty responses, grok_utils calls report_failure(); a successful call
calls report_success(). Alerts fire only on transitions (ok→down, cause change, recovery)
plus a periodic reminder, so a multi-day outage is one alert, not hundreds.

Channels: macOS notification, monitor/grok_alerts.log, and an optional shell command in
GROK_ALERT_CMD that receives the message on stdin (wire Telegram/ntfy/etc. here).

Nothing here may break collection: every public function swallows its own errors.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_PATH   = Path(os.environ.get("SENTIMENT_REPO_PATH", Path(__file__).parent.parent)).resolve()
STATUS_PATH = REPO_PATH / "monitor" / "grok_status.json"
ALERT_LOG   = REPO_PATH / "monitor" / "grok_alerts.log"

DIAG_MIN_INTERVAL = int(os.environ.get("GROK_DIAG_INTERVAL", "1200"))   # at most one diagnosis / 20 min
REALERT_INTERVAL  = int(os.environ.get("GROK_REALERT_INTERVAL", "21600"))  # reminder every 6h while down
DIAG_TIMEOUT      = int(os.environ.get("GROK_DIAG_TIMEOUT", "90"))


def enabled() -> bool:
    return os.environ.get("GROK_HEALTH", "1") != "0"


# order matters: first match wins
_CAUSE_PATTERNS = [
    ("credits_exhausted", r"spending-limit|run out of credits|insufficient (credits|funds)|need a grok subscription"),
    ("auth",              r"\b401\b|unauthori[sz]ed|invalid.{0,20}(token|credential|key)|re-?authenticat|login required|token.{0,20}expired"),
    ("rate_limited",      r"\b429\b|rate.?limit|too many requests"),
    ("network",           r"timed? ?out|connection (error|refused|reset)|name or service|network is unreachable|dns"),
]

CAUSE_HELP = {
    "credits_exhausted": "Grok 크레딧 소진/구독 필요 — https://grok.com/?_s=usage 에서 충전",
    "auth":              "Grok 인증 만료/오류 — `hermes login` 으로 재인증",
    "rate_limited":      "Grok 요청 한도 초과(429) — 잠시 후 자동 회복 여부 확인",
    "network":           "네트워크/타임아웃 — 인터넷 연결 확인",
    "hermes_missing":    "hermes 실행 파일 없음 — HERMES_CMD/PATH 확인",
    "unknown":           "원인 불명 — `hermes chat -q ok` 를 직접 실행해 오류 확인",
}


def classify(text: str) -> str:
    for cause, pat in _CAUSE_PATTERNS:
        if re.search(pat, text or "", re.IGNORECASE):
            return cause
    return "unknown"


def diagnose(hermes_cmd: str, timeout: int | None = None) -> tuple[bool, str, str]:
    """Run a tiny interactive-mode call (which, unlike -z, prints the provider error).

    Returns (healthy, cause, detail). Costs ~1.4K tokens when healthy, 0 when rejected.
    """
    cmd = [hermes_cmd, "chat", "-q", "Reply OK", "--ignore-rules", "-t", "clarify"]
    env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or DIAG_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return False, "network", "diagnosis timed out"
    except FileNotFoundError:
        return False, "hermes_missing", f"not found: {hermes_cmd}"
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and not re.search(r"\bError( code)?:", out):
        return True, "", ""
    detail = " ".join(re.findall(r"Error[^\n]*(?:\n\s+[^\n]+){0,3}", out)[:1]).replace("\n", " ")
    detail = re.sub(r"\s+", " ", detail)[:300]
    return False, classify(out), detail


def load_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text())
    except Exception:
        return {}


def _save_status(st: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    tmp.replace(STATUS_PATH)


def alert(title: str, message: str) -> None:
    """Deliver through every available channel; one channel failing never blocks another."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ALERT_LOG.open("a") as f:
            f.write(f"[{stamp}] {title} — {message}\n")
    except Exception as e:
        print(f"[WARN] grok_alerts.log 기록 실패: {e}", file=sys.stderr)
    try:
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}" sound name "Basso"'],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass
    cmd = os.environ.get("GROK_ALERT_CMD", "").strip()
    if cmd:
        try:
            subprocess.run(cmd, shell=True, input=f"{title}\n{message}\n", text=True, capture_output=True, timeout=30)
        except Exception as e:
            print(f"[WARN] GROK_ALERT_CMD 실패: {e}", file=sys.stderr)


def _fmt_dur(sec: float) -> str:
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    return f"{h}시간 {m}분" if h else f"{m}분"


def report_failure(hermes_cmd: str, source: str = "") -> None:
    """A collector exhausted its retries on empty responses. Diagnose and alert on transitions."""
    if not enabled():
        return
    try:
        now = time.time()
        st = load_status()
        if now - st.get("last_check", 0) < DIAG_MIN_INTERVAL:
            return
        healthy, cause, detail = diagnose(hermes_cmd)
        if healthy:
            # transient empty response; Grok itself answers fine
            st.update(last_check=now)
            if st.get("state") == "down":
                st.update(state="ok")
                _save_status(st)
                alert("Grok 복구", f"Grok 응답 정상 복구 (장애 {_fmt_dur(now - st.get('since', now))})")
            else:
                _save_status(st)
            return
        was_down = st.get("state") == "down"
        changed = (not was_down) or st.get("cause") != cause
        reminder = was_down and (now - st.get("last_alert", 0) >= REALERT_INTERVAL)
        notify = changed or reminder
        new = {
            "state": "down", "cause": cause, "detail": detail,
            "since": st["since"] if was_down else now,
            "first_source": st.get("first_source", source) if was_down else source,
            "last_check": now,
            "last_alert": now if notify else st.get("last_alert", 0),
        }
        _save_status(new)
        if notify:
            dur = f" (지속 {_fmt_dur(now - new['since'])})" if was_down else ""
            alert("Grok 장애 — 수집 중단", f"{CAUSE_HELP.get(cause, cause)}{dur}. 감지 지점: {source or '?'}. {detail[:120]}")
    except Exception as e:
        print(f"[WARN] grok_health.report_failure 오류(무시): {e}", file=sys.stderr)


def report_success() -> None:
    """A Grok call succeeded. Cheap no-op unless we were in the 'down' state."""
    if not enabled():
        return
    try:
        st = load_status()
        if st.get("state") != "down":
            return
        now = time.time()
        _save_status({**st, "state": "ok", "last_check": now, "recovered_at": now})
        alert("Grok 복구", f"Grok 호출 정상 복구 (장애 {_fmt_dur(now - st.get('since', now))}, 원인 {st.get('cause')})")
    except Exception as e:
        print(f"[WARN] grok_health.report_success 오류(무시): {e}", file=sys.stderr)
