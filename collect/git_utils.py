#!/usr/bin/env python3
"""
공용 Git 커밋 + Push 유틸리티 (cron 환경 대응)

모든 수집기(brief, sentiment, earnings)에서 공통으로 사용.

주요 특징:
- cron 환경에서도 ~/.ssh/id_ed25519 키를 강제로 사용
- git identity가 없으면 자동 설정
- 변경사항이 있을 때만 commit/push
- push 실패 시 명확한 에러 메시지 + False 반환 (호출 측에서 exit 1 처리 권장)
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


DEFAULT_SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")


def _run(cmd: list[str], cwd: Path, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
    )


def ensure_git_identity(repo: Path) -> None:
    """git user.name / user.email이 없으면 기본값으로 설정 (cron 대응)"""
    def get_config(key: str) -> str:
        res = _run(["git", "config", key], cwd=repo, capture=True)
        return res.stdout.strip() if res.returncode == 0 else ""

    if not get_config("user.name"):
        _run(["git", "config", "user.name", "Jerry (Collector Bot)"], cwd=repo)
    if not get_config("user.email"):
        _run(["git", "config", "user.email", "collector@jerrymacmini.local"], cwd=repo)


def get_ssh_command() -> Optional[str]:
    """
    GIT_SSH_COMMAND이 이미 설정되어 있으면 그대로 사용.
    없으면 기본 키(~/.ssh/id_ed25519)를 사용하도록 반환.
    """
    existing = os.environ.get("GIT_SSH_COMMAND")
    if existing:
        return existing

    if Path(DEFAULT_SSH_KEY).exists():
        # StrictHostKeyChecking=no 는 cron 환경에서 편의상 사용 (필요시 config로 대체 가능)
        return f'ssh -i {DEFAULT_SSH_KEY} -o StrictHostKeyChecking=no'
    return None


def commit_and_push(
    repo: Path,
    commit_message: str,
    files_to_add: list[str],
    push: bool = True,
) -> bool:
    """
    지정한 파일들을 add → commit (변경사항 있을 때만) → push.

    Returns:
        True: 성공 (변경 없음 포함)
        False: commit 또는 push 실패
    """
    ensure_git_identity(repo)

    # 변경사항 스테이징
    add_cmd = ["git", "add"] + files_to_add
    _run(add_cmd, cwd=repo, capture=False)

    # 변경사항이 있는지 확인
    diff = _run(["git", "diff", "--cached", "--quiet"], cwd=repo)
    if diff.returncode == 0:
        print("[INFO] 커밋할 변경사항 없음", file=sys.stderr)
        return True

    # 커밋
    commit_res = _run(["git", "commit", "-m", commit_message], cwd=repo)
    if commit_res.returncode != 0:
        print(f"[ERROR] git commit 실패:\n{commit_res.stderr}", file=sys.stderr)
        return False

    print(f"[INFO] 커밋 완료: {commit_message}")

    if not push:
        return True

    # Push (SSH 키 강제 지정)
    ssh_cmd = get_ssh_command()
    env = os.environ.copy()
    if ssh_cmd:
        env["GIT_SSH_COMMAND"] = ssh_cmd

    push_res = subprocess.run(
        ["git", "push", "origin", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )

    if push_res.returncode != 0:
        print(f"[ERROR] git push 실패:\n{push_res.stderr}", file=sys.stderr)
        print(
            "[HINT] cron에서는 GIT_SSH_COMMAND 또는 ~/.ssh/config 로 키를 명시적으로 지정해야 합니다.",
            file=sys.stderr,
        )
        return False

    print("[OK] GitHub push 성공")
    return True
