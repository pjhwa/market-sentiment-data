#!/usr/bin/env python3
"""
SniperBoard 종합 헬스 모니터 — 점검 가능한 모든 항목 검사

점검 카테고리:
  1. 수집 데이터 freshness     — 5개 수집 파일 + 허용 최대 경과 시간
  2. 수집 데이터 품질          — 심볼 커버리지, 필수 필드, 값 유효성
  3. cron 실행 이력            — 로그 파일 최종 갱신 시각 vs 예상 주기
  4. 수집 로그 오류            — 최근 100줄에서 ERROR/FAIL 탐지
  5. Git / GitHub              — 로컬↔원격 동기화, GitHub API 접근성
  6. Hermes 바이너리           — 실행 파일 존재 확인
  7. Docker 컨테이너           — 실행 중, 재시작 횟수, 메모리 사용량
  8. SniperBoard API 엔드포인트 — 9개 엔드포인트 응답 및 데이터 검증
  9. 프론트엔드 접근성          — localhost:4000 응답
 10. 신호 로그 DB              — 파일 존재, 읽기 가능, 장기 미갱신 경보
 11. APScheduler 실행 여부     — backend 로그에서 job 실행 흔적 확인
 12. 시스템 자원               — 디스크 여유 공간
 13. 인터넷 연결               — 외부 네트워크 접근 가능 여부
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 경로 상수 ─────────────────────────────────────────────────────────────────
REPO_PATH = Path(__file__).parent.parent
SNIPERBOARD_API = "http://localhost:5001"
FRONTEND_URL = "http://localhost:4000"
SIGNAL_DB = Path.home() / "dev/sniperboard/backend/data/signal_log.db"

# ── 심볼 목록 ─────────────────────────────────────────────────────────────────
TIER1 = ["TSM", "NVDA", "META", "TSLA", "PLTR", "MU", "CRWD",
         "AMZN", "MSFT", "AAPL", "GOOGL", "SPCX"]
TIER2 = ["RKLB", "CEG", "VST", "ALAB", "OKLO", "APP", "ANET",
         "NVO", "QBTS", "SOFI"]

# ── 수집 데이터 정의: (파일, 최대허용시간h, 이름) ────────────────────────────
DATA_FILES = [
    (REPO_PATH / "sentiment/latest.json",  25, "Sentiment"),
    (REPO_PATH / "brief/latest.json",      25, "Daily Brief"),
    (REPO_PATH / "macro/latest.json",      25, "Macro Insight"),
    (REPO_PATH / "earnings/latest.json",   26, "Earnings"),
    (REPO_PATH / "briefing/latest.json",   26, "Morning Briefing"),
]

# ── cron 로그: (파일, 최대허용시간h, 이름) ────────────────────────────────────
CRON_LOGS = [
    (REPO_PATH / "sentiment/sentiment.log",    14, "sentiment cron"),
    (REPO_PATH / "brief/brief.log",            14, "brief cron"),
    (REPO_PATH / "macro/macro.log",            14, "macro cron"),
    (REPO_PATH / "earnings/earnings.log",      26, "earnings cron"),
    (REPO_PATH / "briefing/briefing.log",      26, "briefing cron"),
    (REPO_PATH / "briefing/auto_improve.log",  26, "auto_improve cron"),
]

VALID_SENTIMENTS  = {"euphoric", "optimistic", "neutral", "fearful", "panic"}
VALID_CONFIDENCE  = {"high", "med", "low"}
VALID_DIVERGENCE  = {"aligned", "bullish_divergence", "bearish_divergence", "none"}
DOCKER_CONTAINERS = ["sniperboard-backend", "sniperboard-frontend"]
MAX_RESTART_COUNT = 5
MAX_MEM_MB        = 1500
DISK_MIN_GB       = 10

issues: list[tuple[str, str]] = []   # (category, message)
warnings: list[tuple[str, str]] = [] # 경고 (알림 X, 로그만)


def fail(cat: str, msg: str):
    issues.append((cat, msg))
    print(f"  [FAIL] [{cat}] {msg}")


def warn(cat: str, msg: str):
    warnings.append((cat, msg))
    print(f"  [WARN] [{cat}] {msg}")


def ok(cat: str, msg: str):
    print(f"  [OK]   [{cat}] {msg}")


def http_get(url: str, timeout: int = 6):
    """단순 HTTP GET → (status, body_str) or raises."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", errors="replace")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── 1. 수집 데이터 freshness ─────────────────────────────────────────────────
def check_data_freshness():
    cat = "DataFreshness"
    for path, max_h, name in DATA_FILES:
        if not path.exists():
            fail(cat, f"{name}: 파일 없음 ({path})")
            continue
        try:
            data = json.loads(path.read_text())
            ts_str = data.get("generated_at", "")
            if not ts_str:
                fail(cat, f"{name}: generated_at 필드 없음")
                continue
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_h = (now_utc() - ts).total_seconds() / 3600
            if age_h > max_h:
                fail(cat, f"{name}: {age_h:.1f}h 경과 (한도 {max_h}h) — 마지막: {ts_str}")
            else:
                ok(cat, f"{name}: {age_h:.1f}h 전 업데이트")
        except Exception as e:
            fail(cat, f"{name}: 파싱 오류 — {e}")


# ── 2. 수집 데이터 품질 ───────────────────────────────────────────────────────
def check_data_quality():
    cat = "DataQuality"
    path = REPO_PATH / "sentiment/latest.json"
    if not path.exists():
        fail(cat, "sentiment/latest.json 없음 — 품질 점검 불가")
        return

    try:
        data = json.loads(path.read_text())
        symbols_data = {s["symbol"]: s for s in data.get("symbols", []) if "symbol" in s}
        present = set(symbols_data.keys())
        expected = set(TIER1 + TIER2)

        # 심볼 커버리지
        missing = expected - present
        if missing:
            fail(cat, f"심볼 누락: {sorted(missing)}")
        else:
            ok(cat, f"심볼 커버리지: {len(present)}/{len(expected)} 완전")

        # 필드 및 값 유효성 (SPCX는 최근 IPO라 price_context 없을 수 있음)
        invalid = []
        required_fields = ["sentiment", "confidence", "key_reason_en", "key_reason_ko"]
        for sym, s in symbols_data.items():
            if s.get("sentiment") not in VALID_SENTIMENTS:
                invalid.append(f"{sym}.sentiment={s.get('sentiment')!r}")
            if s.get("confidence") not in VALID_CONFIDENCE:
                invalid.append(f"{sym}.confidence={s.get('confidence')!r}")
            if s.get("divergence") not in VALID_DIVERGENCE:
                invalid.append(f"{sym}.divergence={s.get('divergence')!r}")
            for field in required_fields:
                if not s.get(field):
                    invalid.append(f"{sym}.{field}=비어있음")

        if invalid:
            fail(cat, f"유효하지 않은 값 {len(invalid)}건: {invalid[:5]}")
        else:
            ok(cat, "sentiment 필드/값 유효성 통과")

        # market 필드
        market = data.get("market", {})
        if not market.get("sentiment"):
            fail(cat, "market.sentiment 없음")
        else:
            ok(cat, f"market sentiment: {market['sentiment']}")

    except Exception as e:
        fail(cat, f"품질 점검 오류 — {e}")


# ── 3. cron 실행 이력 (로그 파일 최종 수정 시각) ─────────────────────────────
def _data_is_fresh(name: str) -> bool:
    """해당 수집기의 데이터 파일이 최신인지 확인."""
    for path, max_h, dname in DATA_FILES:
        if dname.lower().startswith(name.split()[0].lower()):
            if not path.exists():
                return False
            try:
                d = json.loads(path.read_text())
                ts = datetime.fromisoformat(
                    d.get("generated_at", "").replace("Z", "+00:00")
                )
                return (now_utc() - ts).total_seconds() / 3600 < max_h
            except Exception:
                return False
    return False


def check_cron_logs():
    cat = "CronExecution"
    for path, max_h, name in CRON_LOGS:
        if not path.exists():
            fail(cat, f"{name}: 로그 파일 없음 ({path.name})")
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age_h = (now_utc() - mtime).total_seconds() / 3600
        if age_h > max_h:
            # 데이터 자체가 최신이면 수동 실행으로 데이터는 OK — WARN으로 격하
            if _data_is_fresh(name):
                warn(cat, f"{name}: 로그 {age_h:.1f}h 미갱신 (수동 실행으로 데이터는 최신)")
            else:
                fail(cat, f"{name}: 로그 {age_h:.1f}h 미갱신 (한도 {max_h}h) — cron 미실행 의심")
        else:
            ok(cat, f"{name}: {age_h:.1f}h 전 실행")


# ── 4. 수집 로그 오류 탐지 ────────────────────────────────────────────────────
def check_log_errors():
    cat = "LogErrors"
    error_keywords = ["ERROR", "FAIL", "Traceback", "Exception", "Critical"]
    for path, _, name in CRON_LOGS:
        if not path.exists():
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
            recent = lines[-100:]  # 최근 100줄만 검사
            found = [
                l.strip() for l in recent
                if any(kw in l for kw in error_keywords)
                and "NotFound" not in l  # SPCX 404는 정상
            ]
            if found:
                warn(cat, f"{name} 최근 오류 {len(found)}건: {found[0][:120]}")
            else:
                ok(cat, f"{name}: 최근 로그 오류 없음")
        except Exception as e:
            warn(cat, f"{name}: 로그 읽기 실패 — {e}")


# ── 5. Git / GitHub 상태 ─────────────────────────────────────────────────────
def check_git():
    cat = "Git"
    repo = str(REPO_PATH)
    try:
        # 원격 최신 상태 fetch
        subprocess.run(
            ["git", "fetch", "--quiet"], cwd=repo, capture_output=True, timeout=15
        )
        # 로컬이 원격보다 뒤처지는지
        behind = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            cwd=repo, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        ahead = subprocess.run(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        if behind and int(behind) > 0:
            fail(cat, f"로컬이 원격보다 {behind} 커밋 뒤처짐 — push 실패 가능성")
        elif ahead and int(ahead) > 0:
            warn(cat, f"로컬이 원격보다 {ahead} 커밋 앞섬 — 미push 커밋 존재")
        else:
            ok(cat, "로컬↔원격 동기화 완료")

        # 마지막 커밋 경과 시간
        log_ts = subprocess.run(
            ["git", "log", "-1", "--format=%ct"], cwd=repo,
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        if log_ts:
            last_commit = datetime.fromtimestamp(int(log_ts), tz=timezone.utc)
            age_h = (now_utc() - last_commit).total_seconds() / 3600
            if age_h > 26:
                warn(cat, f"마지막 커밋 {age_h:.1f}h 전 — 수집이 커밋을 생성하지 않았을 수 있음")
            else:
                ok(cat, f"마지막 커밋 {age_h:.1f}h 전")
    except Exception as e:
        fail(cat, f"git 상태 확인 실패 — {e}")

    # GitHub API 접근
    try:
        status, body = http_get(
            "https://api.github.com/repos/pjhwa/market-sentiment-data/commits?per_page=1",
            timeout=8
        )
        if status == 200:
            d = json.loads(body)
            ok(cat, f"GitHub API 접근 가능 — 최신 커밋: {d[0]['commit']['message'][:50]!r}")
        else:
            fail(cat, f"GitHub API HTTP {status}")
    except Exception as e:
        fail(cat, f"GitHub API 접근 불가 — {e}")


# ── 6. Hermes 바이너리 ────────────────────────────────────────────────────────
def check_hermes():
    cat = "Hermes"
    hermes_path = shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")
    if Path(hermes_path).exists():
        try:
            result = subprocess.run(
                [hermes_path, "--version"], capture_output=True, text=True, timeout=5
            )
            ver = result.stdout.strip().splitlines()[0] if result.stdout else "unknown"
            ok(cat, f"바이너리 확인: {ver} ({hermes_path})")
        except Exception as e:
            warn(cat, f"hermes --version 실패 — {e}")
    else:
        fail(cat, f"hermes 바이너리 없음: {hermes_path}")


# ── 7. Docker 컨테이너 ───────────────────────────────────────────────────────
def check_docker():
    cat = "Docker"
    try:
        # 실행 중인 컨테이너 목록
        ps = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.ID}}"],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        running = {line.split("\t")[0]: line for line in ps.splitlines()}

        for name in DOCKER_CONTAINERS:
            if name not in running:
                fail(cat, f"{name}: 중단됨 (실행 중이지 않음)")
                continue
            ok(cat, f"{name}: 실행 중")

            # 재시작 횟수
            inspect = subprocess.run(
                ["docker", "inspect", "--format",
                 "{{.RestartCount}} {{.State.Status}} {{.HostConfig.Memory}}",
                 name],
                capture_output=True, text=True, timeout=10
            ).stdout.strip().split()
            if inspect:
                restarts = int(inspect[0]) if inspect[0].isdigit() else 0
                state = inspect[1] if len(inspect) > 1 else "?"
                if restarts > MAX_RESTART_COUNT:
                    fail(cat, f"{name}: 재시작 {restarts}회 — 크래시 루프 의심")
                elif restarts > 0:
                    warn(cat, f"{name}: 재시작 {restarts}회")

        # 메모리 사용량
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.MemUsage}}"] + DOCKER_CONTAINERS,
            capture_output=True, text=True, timeout=15
        ).stdout.strip()
        for line in stats.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name, mem_str = parts[0], parts[1]
            # 예: "203.1MiB / 15.65GiB"
            used_str = mem_str.split("/")[0].strip()
            mb = _parse_mem_mb(used_str)
            if mb and mb > MAX_MEM_MB:
                warn(cat, f"{name}: 메모리 {used_str} 사용 (한도 {MAX_MEM_MB}MB)")
            elif mb:
                ok(cat, f"{name}: 메모리 {used_str}")

    except Exception as e:
        fail(cat, f"Docker 상태 확인 실패 — {e}")


def _parse_mem_mb(s: str) -> float | None:
    s = s.strip()
    try:
        if s.endswith("GiB"):
            return float(s[:-3]) * 1024
        if s.endswith("MiB"):
            return float(s[:-3])
        if s.endswith("kB"):
            return float(s[:-2]) / 1024
    except Exception:
        pass
    return None


# ── 8. SniperBoard API 엔드포인트 ────────────────────────────────────────────
def check_api():
    cat = "API"
    tests = [
        ("/",                          None,                     "root"),
        ("/api/regime",                "regime",                 "regime"),
        ("/api/watchlist",             "watchlist",              "watchlist"),
        ("/api/sentiment",             "available",              "sentiment"),
        ("/api/brief",                 "available",              "brief"),
        ("/api/earnings",              "available",              "earnings"),
        ("/api/macro/insight",         "overall",                "macro/insight"),
        ("/api/signal-log/stats",      "n_total",                "signal-log/stats"),
        ("/api/morning-briefing",      "available",              "morning-briefing"),
    ]
    for path, key, name in tests:
        try:
            status, body = http_get(f"{SNIPERBOARD_API}{path}", timeout=8)
            if status != 200:
                fail(cat, f"{name}: HTTP {status}")
                continue
            if key:
                d = json.loads(body)
                if key not in d:
                    fail(cat, f"{name}: 응답에 '{key}' 필드 없음")
                else:
                    ok(cat, f"{name}: 정상")
            else:
                ok(cat, f"{name}: 정상")
        except urllib.error.HTTPError as e:
            fail(cat, f"{name}: HTTP {e.code}")
        except Exception as e:
            fail(cat, f"{name}: 응답 없음 — {e}")

    # 주요 심볼 daily 응답 확인 (NVDA: 데이터 충분)
    try:
        status, body = http_get(f"{SNIPERBOARD_API}/api/daily?symbol=NVDA", timeout=8)
        if status == 200:
            d = json.loads(body)
            candles = d.get("candles", [])
            close = candles[-1].get("close") if candles else None
            ok(cat, f"daily(NVDA): 정상 (최신 종가 {close})")
        else:
            fail(cat, f"daily(NVDA): HTTP {status}")
    except Exception as e:
        fail(cat, f"daily(NVDA): {e}")

    # SPCX daily는 IPO < 20일 → 404 정상
    try:
        http_get(f"{SNIPERBOARD_API}/api/daily?symbol=SPCX", timeout=8)
        warn(cat, "daily(SPCX): 200 반환 — IPO 20일 경과했을 수 있음, 확인 필요")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            ok(cat, "daily(SPCX): 404 정상 (IPO < 20일)")
        else:
            fail(cat, f"daily(SPCX): 예상치 못한 HTTP {e.code}")
    except Exception:
        pass

    # sentiment API에 SPCX 포함 여부
    try:
        _, body = http_get(f"{SNIPERBOARD_API}/api/sentiment", timeout=8)
        d = json.loads(body)
        syms = [s["symbol"] for s in d.get("latest", {}).get("symbols", [])]
        if "SPCX" in syms:
            ok(cat, "sentiment API에 SPCX 포함")
        else:
            warn(cat, f"sentiment API에 SPCX 없음 (현재 심볼: {syms})")
    except Exception as e:
        warn(cat, f"sentiment SPCX 확인 실패 — {e}")


# ── 9. 프론트엔드 접근성 ──────────────────────────────────────────────────────
def check_frontend():
    cat = "Frontend"
    try:
        status, _ = http_get(FRONTEND_URL, timeout=8)
        if status == 200:
            ok(cat, f"localhost:4000 응답 정상")
        else:
            fail(cat, f"localhost:4000 HTTP {status}")
    except Exception as e:
        fail(cat, f"프론트엔드 접근 불가 — {e}")


# ── 10. 신호 로그 DB ─────────────────────────────────────────────────────────
def check_signal_db():
    cat = "SignalDB"
    if not SIGNAL_DB.exists():
        fail(cat, f"signal_log.db 없음: {SIGNAL_DB}")
        return
    try:
        con = sqlite3.connect(str(SIGNAL_DB))
        con.row_factory = sqlite3.Row

        # 총 레코드 수
        total = con.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
        ok(cat, f"총 신호 {total}건")

        # 상태별 분포
        for row in con.execute(
            "SELECT status, COUNT(*) as n FROM signal_log GROUP BY status"
        ):
            ok(cat, f"  상태 {row['status']}: {row['n']}건")

        # 마지막 신호 갱신 시각
        last = con.execute(
            "SELECT MAX(created_at) FROM signal_log"
        ).fetchone()[0]
        if last:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            age_days = (now_utc() - last_dt).days
            if age_days > 14:
                fail(cat, f"신호 14일간 미갱신 (마지막: {last}) — APScheduler 확인 필요")
            else:
                ok(cat, f"마지막 신호: {last} ({age_days}일 전)")

        # 장기 PENDING 신호 (30일 이상)
        stale = con.execute(
            "SELECT symbol, signal_date FROM signal_log "
            "WHERE status='PENDING' AND signal_date < date('now','-30 days')"
        ).fetchall()
        if stale:
            syms = [r["symbol"] for r in stale]
            warn(cat, f"30일+ PENDING 신호: {syms}")

        con.close()
    except Exception as e:
        fail(cat, f"signal_log.db 읽기 오류 — {e}")


# ── 11. APScheduler 실행 여부 (backend Docker 로그) ──────────────────────────
def check_apscheduler():
    cat = "APScheduler"
    try:
        logs = subprocess.run(
            ["docker", "logs", "--tail", "200", "sniperboard-backend"],
            capture_output=True, text=True, timeout=10
        )
        output = logs.stdout + logs.stderr
        markers = {
            "startup": "APScheduler started" in output or "Application startup complete" in output,
            "signal_scan": "signal scan" in output.lower() or "Scheduled signal" in output,
            "error": "APScheduler" in output and "ERROR" in output,
        }
        if not markers["startup"]:
            fail(cat, "backend 시작 로그 없음 — 컨테이너 이상 가능성")
        else:
            ok(cat, "backend 정상 시작 확인")

        if markers["error"]:
            fail(cat, "APScheduler 오류 로그 감지")

        # 스캔 실행은 야간에만 → 낮에는 없어도 정상
        hour_kst = (now_utc() + timedelta(hours=9)).hour
        in_market_hours = (22 <= hour_kst or hour_kst <= 5)
        if in_market_hours and not markers["signal_scan"]:
            warn(cat, "장 중이지만 최근 200줄에서 signal scan 실행 흔적 없음")
        elif markers["signal_scan"]:
            ok(cat, "signal scan 실행 흔적 확인")
        else:
            ok(cat, "장외 시간 — signal scan 미실행 정상")

    except Exception as e:
        warn(cat, f"APScheduler 로그 확인 실패 — {e}")


# ── 12. 시스템 자원 ──────────────────────────────────────────────────────────
def check_system():
    cat = "System"
    try:
        stat = shutil.disk_usage("/")
        free_gb = stat.free / (1024 ** 3)
        total_gb = stat.total / (1024 ** 3)
        used_pct = (stat.used / stat.total) * 100
        if free_gb < DISK_MIN_GB:
            fail(cat, f"디스크 여유 {free_gb:.1f}GB — 위험 수준 (한도 {DISK_MIN_GB}GB)")
        else:
            ok(cat, f"디스크: {free_gb:.0f}GB 여유 / {total_gb:.0f}GB ({used_pct:.0f}% 사용)")
    except Exception as e:
        warn(cat, f"디스크 확인 실패 — {e}")


# ── 13. 인터넷 연결 ───────────────────────────────────────────────────────────
def check_network():
    cat = "Network"
    targets = [
        ("https://api.github.com",    "GitHub",        False),
        ("https://finance.yahoo.com", "Yahoo Finance", True),   # 429 rate limit 허용
    ]
    for url, name, rate_limit_ok in targets:
        try:
            status, _ = http_get(url, timeout=6)
            ok(cat, f"{name} 접근 가능 (HTTP {status})")
        except urllib.error.HTTPError as e:
            if rate_limit_ok and e.code == 429:
                ok(cat, f"{name} 접근 가능 (HTTP 429 rate-limit — 정상)")
            else:
                fail(cat, f"{name} 접근 불가 — HTTP {e.code}")
        except Exception as e:
            fail(cat, f"{name} 접근 불가 — {e}")


# ── 알림 발송 ─────────────────────────────────────────────────────────────────
def notify(title: str, message: str, urgent: bool = False):
    sound = "Sosumi" if urgent else "default"
    script = (
        f'display notification "{message}" '
        f'with title "{title}" '
        f'sound name "{sound}"'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True)


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"SniperBoard Health Check — {ts}")
    print(f"{'='*60}")

    check_data_freshness()
    check_data_quality()
    check_cron_logs()
    check_log_errors()
    check_git()
    check_hermes()
    check_docker()
    check_api()
    check_frontend()
    check_signal_db()
    check_apscheduler()
    check_system()
    check_network()

    print(f"\n{'='*60}")
    print(f"결과: FAIL {len(issues)}건 / WARN {len(warnings)}건")

    if warnings:
        for cat, msg in warnings:
            print(f"  [WARN] [{cat}] {msg}")

    if issues:
        print(f"\n[이상 감지 — macOS 알림 발송]")
        summary = f"FAIL {len(issues)}건"
        first3 = " | ".join(f"[{c}] {m[:60]}" for c, m in issues[:3])
        notify("SniperBoard 이상 감지", f"{summary}: {first3}", urgent=True)
        for cat, msg in issues:
            print(f"  [FAIL] [{cat}] {msg}")
        sys.exit(1)
    else:
        print("[모든 항목 정상]")
        sys.exit(0)


if __name__ == "__main__":
    main()
