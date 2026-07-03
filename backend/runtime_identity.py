"""Runtime identity marker for the live Portfolio Guru bot process."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_IDENTITY_PATH = "/tmp/portfolio-guru-runtime.json"


def runtime_identity_path() -> Path:
    return Path(os.environ.get("PORTFOLIO_GURU_RUNTIME_IDENTITY", DEFAULT_RUNTIME_IDENTITY_PATH))


def git_identity(repo_root: str | Path) -> tuple[str, str]:
    root = str(repo_root)
    commit = subprocess.check_output(
        ["git", "-C", root, "rev-parse", "--short", "HEAD"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    branch = subprocess.check_output(
        ["git", "-C", root, "branch", "--show-current"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip() or "detached"
    return commit, branch


def build_runtime_identity(
    repo_root: str | Path,
    *,
    pid: int | None = None,
    service_label: str | None = None,
) -> dict[str, Any]:
    repo_path = Path(repo_root).resolve()
    commit, branch = git_identity(repo_path)
    return {
        "app": "portfolio-guru",
        "pid": pid if pid is not None else os.getpid(),
        "parent_pid": os.getppid(),
        "commit": commit,
        "branch": branch,
        "repo_root": str(repo_path),
        "backend_dir": str((repo_path / "backend").resolve()),
        "service_label": service_label or os.environ.get("PORTFOLIO_GURU_SERVICE_LABEL", "com.portfolioguru.bot"),
        "started_at": datetime.now(UTC).isoformat(),
    }


def write_runtime_identity(
    repo_root: str | Path,
    *,
    pid: int | None = None,
    service_label: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    identity = build_runtime_identity(repo_root, pid=pid, service_label=service_label)
    target = Path(path) if path is not None else runtime_identity_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(identity, handle, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return identity
