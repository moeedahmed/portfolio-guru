#!/usr/bin/env python3
"""Verify that launchd serves the checked-out Portfolio Guru commit."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path("/Users/moeedahmed/projects/portfolio-guru")
DEFAULT_SERVICE_LABEL = "com.portfolioguru.bot"
DEFAULT_IDENTITY_PATH = Path("/tmp/portfolio-guru-runtime.json")
FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def validate_expected_sha(value: str | None) -> str | None:
    if value is None:
        return None
    if not FULL_SHA.fullmatch(value):
        raise ValueError("expected SHA must be a full 40-character hexadecimal value")
    return value.lower()


def resolve_root(*, expected_sha: str | None, inherited_root: str | None) -> Path:
    # Release proof is bound to the canonical production checkout. The no-arg
    # deploy smoke keeps its established app-dir override for isolated deploys.
    if expected_sha is not None:
        return DEFAULT_PROJECT_ROOT.resolve()
    return Path(inherited_root or DEFAULT_PROJECT_ROOT).resolve()


def run_text(args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def expected_commit(root: Path) -> str:
    return run_text(["git", "-C", str(root), "rev-parse", "HEAD"]).strip()


def launchd_pid(service_label: str = DEFAULT_SERVICE_LABEL) -> int:
    output = run_text(["launchctl", "print", f"gui/{os.getuid()}/{service_label}"])
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("pid ="):
            return int(stripped.split("=", 1)[1].strip())
    raise RuntimeError(f"{service_label} has no launchd pid")


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_cwd(pid: int) -> str:
    output = run_text(["lsof", "-a", "-p", str(pid), "-d", "cwd"], check=False)
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[1].split()[-1] if len(lines) >= 2 else ""


def portfolio_bot_pids(root: Path) -> list[int]:
    output = run_text(["pgrep", "-f", "bot.py"], check=False)
    backend_dir = str(root / "backend")
    pids: list[int] = []
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


def check_runtime(
    *,
    root: Path,
    identity_path: Path,
    expected_sha: str | None = None,
    service_label: str = DEFAULT_SERVICE_LABEL,
) -> str:
    checkout_sha = expected_commit(root)
    if not FULL_SHA.fullmatch(checkout_sha):
        raise RuntimeError(f"checkout commit is not a full SHA: {checkout_sha}")
    if expected_sha is not None and checkout_sha.lower() != expected_sha.lower():
        raise RuntimeError(f"checkout commit {checkout_sha} != expected SHA {expected_sha}")

    service_pid = launchd_pid(service_label)
    if not process_alive(service_pid):
        raise RuntimeError(f"launchd pid {service_pid} is not alive")
    if not identity_path.exists():
        raise RuntimeError(f"runtime identity file missing: {identity_path}")
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"runtime identity file is unreadable: {exc}") from exc

    runtime_pid = identity.get("pid")
    runtime_sha = identity.get("commit")
    runtime_repo = identity.get("repo_root")
    if runtime_pid != service_pid:
        raise RuntimeError(f"launchd pid {service_pid} != runtime identity pid {runtime_pid}")
    if not isinstance(runtime_sha, str) or not FULL_SHA.fullmatch(runtime_sha):
        raise RuntimeError(f"runtime identity commit is not a full SHA: {runtime_sha}")
    if expected_sha is not None and runtime_sha.lower() != expected_sha.lower():
        raise RuntimeError(f"runtime commit {runtime_sha} != expected SHA {expected_sha}")
    if runtime_sha.lower() != checkout_sha.lower():
        raise RuntimeError(f"runtime commit {runtime_sha} != checkout commit {checkout_sha}")
    if runtime_repo != str(root):
        raise RuntimeError(f"runtime repo {runtime_repo} != expected repo {root}")

    pids = portfolio_bot_pids(root)
    if pids != [service_pid]:
        raise RuntimeError(f"expected one Portfolio Guru bot pid [{service_pid}], found {pids}")

    stable_expected = expected_sha or checkout_sha
    return (
        "LIVE_RUNTIME_OK "
        f"service={service_label} pid={service_pid} expected_sha={stable_expected} "
        f"checkout_sha={checkout_sha} runtime_sha={runtime_sha} branch={identity.get('branch')}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", help="required exact full SHA for release proof")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        expected_sha = validate_expected_sha(args.expected_sha or os.environ.get("PORTFOLIO_GURU_EXPECTED_SHA"))
    except ValueError as exc:
        return fail(str(exc))
    root = resolve_root(expected_sha=expected_sha, inherited_root=os.environ.get("PORTFOLIO_GURU_APP_DIR"))
    identity_path = Path(os.environ.get("PORTFOLIO_GURU_RUNTIME_IDENTITY", str(DEFAULT_IDENTITY_PATH)))
    service_label = os.environ.get("PORTFOLIO_GURU_SERVICE_LABEL", DEFAULT_SERVICE_LABEL)
    wait_seconds = float(os.environ.get("PORTFOLIO_GURU_RUNTIME_WAIT_SECONDS", "30"))
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            print(check_runtime(root=root, identity_path=identity_path, expected_sha=expected_sha, service_label=service_label))
            return 0
        except RuntimeError as exc:
            if time.monotonic() >= deadline:
                return fail(str(exc))
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
