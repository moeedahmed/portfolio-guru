#!/usr/bin/env python3
"""Verify that launchd is serving the checked-out Portfolio Guru commit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(os.environ.get("PORTFOLIO_GURU_APP_DIR", "/Users/moeedahmed/projects/portfolio-guru")).resolve()
SERVICE_LABEL = os.environ.get("PORTFOLIO_GURU_SERVICE_LABEL", "com.portfolioguru.bot")
IDENTITY_PATH = Path(os.environ.get("PORTFOLIO_GURU_RUNTIME_IDENTITY", "/tmp/portfolio-guru-runtime.json"))
WAIT_SECONDS = float(os.environ.get("PORTFOLIO_GURU_RUNTIME_WAIT_SECONDS", "30"))


def run_text(args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def expected_commit() -> str:
    return run_text(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]).strip()


def launchd_pid() -> int:
    output = run_text(["launchctl", "print", f"gui/{os.getuid()}/{SERVICE_LABEL}"])
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("pid ="):
            return int(stripped.split("=", 1)[1].strip())
    raise RuntimeError(f"{SERVICE_LABEL} has no launchd pid")


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_cwd(pid: int) -> str:
    output = run_text(["lsof", "-a", "-p", str(pid), "-d", "cwd"], check=False)
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    return lines[1].split()[-1]


def portfolio_bot_pids() -> list[int]:
    output = run_text(["pgrep", "-f", "bot.py"], check=False)
    pids: list[int] = []
    backend_dir = str(ROOT / "backend")
    for raw in output.splitlines():
        try:
            pid = int(raw.strip())
        except ValueError:
            continue
        if process_cwd(pid) == backend_dir:
            pids.append(pid)
    return sorted(set(pids))


def fail(message: str) -> int:
    print(f"LIVE_RUNTIME_FAIL: {message}", file=sys.stderr)
    return 1


def check_runtime() -> str:
    expected = expected_commit()

    try:
        service_pid = launchd_pid()
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    if not process_alive(service_pid):
        raise RuntimeError(f"launchd pid {service_pid} is not alive")

    if not IDENTITY_PATH.exists():
        raise RuntimeError(f"runtime identity file missing: {IDENTITY_PATH}")

    try:
        identity = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"runtime identity file is unreadable: {exc}") from exc

    runtime_pid = identity.get("pid")
    runtime_commit = identity.get("commit")
    runtime_repo = identity.get("repo_root")
    if runtime_pid != service_pid:
        raise RuntimeError(f"launchd pid {service_pid} != runtime identity pid {runtime_pid}")
    if runtime_commit != expected:
        raise RuntimeError(f"runtime commit {runtime_commit} != checkout commit {expected}")
    if runtime_repo != str(ROOT):
        raise RuntimeError(f"runtime repo {runtime_repo} != expected repo {ROOT}")

    pids = portfolio_bot_pids()
    if pids != [service_pid]:
        raise RuntimeError(f"expected one Portfolio Guru bot pid [{service_pid}], found {pids}")

    return (
        "LIVE_RUNTIME_OK "
        f"service={SERVICE_LABEL} pid={service_pid} commit={runtime_commit} branch={identity.get('branch')}"
    )


def main() -> int:
    deadline = time.monotonic() + max(0.0, WAIT_SECONDS)
    last_error = ""
    while True:
        try:
            print(check_runtime())
            return 0
        except RuntimeError as exc:
            last_error = str(exc)
            if time.monotonic() >= deadline:
                return fail(last_error)
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
