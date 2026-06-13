#!/usr/bin/env python3
"""
SniperBoard Health Monitor
- 모든 수집 데이터 freshness 검사
- Docker 컨테이너 상태 검사
- SniperBoard API 응답 검사
- 이상 감지 시 macOS 알림 발송
"""

import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_PATH = Path(__file__).parent.parent
SNIPERBOARD_API = "http://localhost:5001"
SIGNAL_DB = Path.home() / "dev/sniperboard/backend/data/signal_log.db"

# 수집 데이터: (파일경로, 최대허용시간(시간), 표시명)
DATA_FILES = [
    (REPO_PATH / "sentiment/latest.json",  25, "Sentiment"),
    (REPO_PATH / "brief/latest.json",      25, "Daily Brief"),
    (REPO_PATH / "macro/latest.json",      25, "Macro Insight"),
    (REPO_PATH / "earnings/latest.json",   26, "Earnings"),
]

DOCKER_CONTAINERS = ["sniperboard-backend", "sniperboard-frontend"]


def notify(title: str, message: str, urgent: bool = False):
    """macOS 알림 발송."""
    sound = "Sosumi" if urgent else "default"
    script = (
        f'display notification "{message}" '
        f'with title "{title}" '
        f'sound name "{sound}"'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True)
    print(f"[ALERT] {title}: {message}")


def check_data_freshness() -> list[str]:
    issues = []
    now = datetime.now(timezone.utc)
    for path, max_hours, name in DATA_FILES:
        if not path.exists():
            issues.append(f"{name} 파일 없음: {path}")
            continue
        try:
            data = json.loads(path.read_text())
            generated_at = data.get("generated_at", "")
            if not generated_at:
                issues.append(f"{name}: generated_at 필드 없음")
                continue
            ts = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            age_hours = (now - ts).total_seconds() / 3600
            if age_hours > max_hours:
                issues.append(
                    f"{name} 데이터 오래됨: {age_hours:.1f}시간 전 "
                    f"(허용: {max_hours}h, 마지막: {generated_at})"
                )
        except Exception as e:
            issues.append(f"{name} 파싱 오류: {e}")
    return issues


def check_docker() -> list[str]:
    issues = []
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10
        )
        running = result.stdout.strip().splitlines()
        for name in DOCKER_CONTAINERS:
            if name not in running:
                issues.append(f"Docker 컨테이너 중단됨: {name}")
    except Exception as e:
        issues.append(f"Docker 상태 확인 실패: {e}")
    return issues


def check_api() -> list[str]:
    issues = []
    try:
        req = urllib.request.urlopen(f"{SNIPERBOARD_API}/", timeout=5)
        if req.status != 200:
            issues.append(f"SniperBoard API 응답 이상: HTTP {req.status}")
    except Exception as e:
        issues.append(f"SniperBoard API 접근 불가: {e}")
    return issues


def check_signal_log() -> list[str]:
    issues = []
    if not SIGNAL_DB.exists():
        issues.append(f"signal_log.db 없음: {SIGNAL_DB}")
        return issues
    try:
        import sqlite3
        con = sqlite3.connect(str(SIGNAL_DB))
        row = con.execute(
            "SELECT MAX(created_at) FROM signal_log"
        ).fetchone()
        con.close()
        if row and row[0]:
            last_ts = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - last_ts).days
            if age_days > 14:
                issues.append(
                    f"신호 트래커: {age_days}일간 신규 신호 없음 "
                    f"(마지막: {row[0]})"
                )
    except Exception as e:
        issues.append(f"signal_log.db 읽기 오류: {e}")
    return issues


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Health check 시작")
    all_issues: list[str] = []

    all_issues += check_data_freshness()
    all_issues += check_docker()
    all_issues += check_api()
    all_issues += check_signal_log()

    if all_issues:
        summary = f"{len(all_issues)}개 이상 감지"
        detail = " | ".join(all_issues[:3])  # 알림은 첫 3개만
        notify("SniperBoard 이상 감지", f"{summary}: {detail}", urgent=True)
        for issue in all_issues:
            print(f"  [ISSUE] {issue}")
        sys.exit(1)
    else:
        print("  [OK] 모든 항목 정상")
        sys.exit(0)


if __name__ == "__main__":
    main()
