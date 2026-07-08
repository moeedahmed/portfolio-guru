#!/usr/bin/env python3
"""Supervise the Portfolio Guru WhatsApp linked-device beta runner.

This wrapper is deliberately boring: it starts the already-linked Baileys
sidecar and Python relay as one local process group, writes redacted logs, and
refuses to emit or trigger a QR. It is for the controlled beta state after a
human has linked the dedicated Portfolio Guru WhatsApp Business account.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = REPO_ROOT / ".artifacts" / "whatsapp-live"
DEFAULT_AUTH_DIR = REPO_ROOT / "connectors" / "whatsapp-linked-device" / ".wa-auth"
PID_FILE = DEFAULT_STATE_DIR / "beta-runner.pid"
LOG_FILE = DEFAULT_STATE_DIR / "beta-runner.log"
STATUS_FILE = DEFAULT_STATE_DIR / "beta-runner.json"
REQUIRED_ENV = (
    "PORTFOLIO_INBOUND_URL",
    "PORTFOLIO_INBOUND_SECRET",
    "PG_WA_SEND_PORT",
)


@dataclass(frozen=True)
class RunnerStatus:
    status: str
    detail: str
    pid: int | None = None
    log_path: str | None = None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid(path: Path | None = None) -> int | None:
    path = PID_FILE if path is None else path
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def current_status() -> RunnerStatus:
    pid = _read_pid()
    if pid and _pid_alive(pid):
        return RunnerStatus(
            status="running",
            detail="Portfolio Guru WhatsApp beta runner process is alive",
            pid=pid,
            log_path=str(LOG_FILE),
        )
    return RunnerStatus(
        status="stopped",
        detail="Portfolio Guru WhatsApp beta runner is not running",
        pid=pid,
        log_path=str(LOG_FILE),
    )


def _load_readiness_guard():
    script = REPO_ROOT / "scripts" / "pg_whatsapp_readiness.py"
    spec = importlib.util.spec_from_file_location("pg_whatsapp_readiness", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load readiness guard at {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _readiness_status(env: Mapping[str, str]) -> str:
    guard = _load_readiness_guard()
    result = guard.evaluate(REPO_ROOT, env=env)
    return str(result["status"])


def _auth_ready(auth_dir: Path) -> bool:
    return (auth_dir / "creds.json").is_file()


def _missing_env(env: Mapping[str, str]) -> list[str]:
    return [name for name in REQUIRED_ENV if not env.get(name, "").strip()]


def _command(env: Mapping[str, str], auth_dir: Path, log_file: Path) -> str:
    node_dir = REPO_ROOT / "connectors" / "whatsapp-linked-device"
    backend_dir = REPO_ROOT / "backend"
    python = backend_dir / "venv" / "bin" / "python3"
    if not python.exists():
        python = backend_dir / ".venv" / "bin" / "python3"
    quoted_log = shlex.quote(str(log_file))
    return " ".join(
        [
            "set -o pipefail;",
            "cd",
            shlex.quote(str(node_dir)),
            "&&",
            f"PG_WA_AUTH_DIR={shlex.quote(str(auth_dir))}",
            "PG_WA_FORBID_QR=1",
            f"PG_WA_SEND_PORT={shlex.quote(env['PG_WA_SEND_PORT'])}",
            "node index.js --qr --forbid-qr",
            f"2>>{quoted_log}",
            "|",
            "(",
            "cd",
            shlex.quote(str(backend_dir)),
            "&&",
            shlex.quote(str(python)),
            "whatsapp_connector_runner.py --relay",
            ")",
            f">>{quoted_log} 2>&1",
        ]
    )


def build_start_plan(
    env: Mapping[str, str],
    auth_dir: Path = DEFAULT_AUTH_DIR,
) -> RunnerStatus:
    status = current_status()
    if status.status == "running":
        return RunnerStatus(
            status="blocked",
            detail="runner already running; stop it before starting a second writer",
            pid=status.pid,
            log_path=status.log_path,
        )
    if _readiness_status(env) != "launch-ready":
        return RunnerStatus(
            status="blocked",
            detail="readiness guard is not launch-ready",
            log_path=str(LOG_FILE),
        )
    missing = _missing_env(env)
    if missing:
        return RunnerStatus(
            status="blocked",
            detail="missing required env: " + ", ".join(missing),
            log_path=str(LOG_FILE),
        )
    if not _auth_ready(auth_dir):
        return RunnerStatus(
            status="blocked",
            detail="saved linked-device auth is missing; refuse to start beta runner because QR is forbidden",
            log_path=str(LOG_FILE),
        )
    return RunnerStatus(
        status="ready",
        detail="saved-session beta runner can start without QR",
        log_path=str(LOG_FILE),
    )


def start(env: Mapping[str, str] | None = None) -> RunnerStatus:
    env = os.environ if env is None else env
    DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    plan = build_start_plan(env)
    if plan.status != "ready":
        return plan

    command = _command(env, DEFAULT_AUTH_DIR, LOG_FILE)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"\n--- beta runner start {time.strftime('%Y-%m-%dT%H:%M:%S%z')} ---\n")
    proc = subprocess.Popen(
        ["bash", "-lc", command],
        cwd=str(REPO_ROOT),
        env={**os.environ, **dict(env)},
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    status = RunnerStatus(
        status="running",
        detail="Portfolio Guru WhatsApp beta runner started with QR emission forbidden",
        pid=proc.pid,
        log_path=str(LOG_FILE),
    )
    STATUS_FILE.write_text(json.dumps(asdict(status), indent=2, sort_keys=True), encoding="utf-8")
    return status


def stop() -> RunnerStatus:
    pid = _read_pid()
    if not pid or not _pid_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        return RunnerStatus(
            status="stopped",
            detail="Portfolio Guru WhatsApp beta runner was not running",
            pid=pid,
            log_path=str(LOG_FILE),
        )
    os.killpg(pid, signal.SIGTERM)
    deadline = time.time() + 5
    while time.time() < deadline:
        if not _pid_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            return RunnerStatus(
                status="stopped",
                detail="Portfolio Guru WhatsApp beta runner stopped",
                pid=pid,
                log_path=str(LOG_FILE),
            )
        time.sleep(0.1)
    os.killpg(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    return RunnerStatus(
        status="stopped",
        detail="Portfolio Guru WhatsApp beta runner killed after graceful stop timeout",
        pid=pid,
        log_path=str(LOG_FILE),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start/status/stop the saved-session Portfolio Guru WhatsApp beta runner."
    )
    parser.add_argument("action", choices=("start", "status", "stop", "plan"))
    args = parser.parse_args(argv)

    if args.action == "start":
        result = start()
    elif args.action == "stop":
        result = stop()
    elif args.action == "plan":
        result = build_start_plan(os.environ)
    else:
        result = current_status()

    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.status in {"ready", "running", "stopped"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
