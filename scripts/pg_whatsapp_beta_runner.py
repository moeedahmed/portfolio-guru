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
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = REPO_ROOT / ".artifacts" / "whatsapp-live"
DEFAULT_AUTH_DIR = REPO_ROOT / "connectors" / "whatsapp-linked-device" / ".wa-auth"
PID_FILE = DEFAULT_STATE_DIR / "beta-runner.pid"
BRIDGE_PID_FILE = DEFAULT_STATE_DIR / "beta-bridge.pid"
LOG_FILE = DEFAULT_STATE_DIR / "beta-runner.log"
STATUS_FILE = DEFAULT_STATE_DIR / "beta-runner.json"
REQUIRED_ENV = (
    "PORTFOLIO_INBOUND_URL",
    "PORTFOLIO_INBOUND_SECRET",
    "PG_WA_SEND_PORT",
    "PG_WA_OUTBOUND_SECRET",
    "PG_WA_OUTBOUND_GATEWAY_TOKEN",
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


def _kill_pid_file(path: Path, *, timeout: float = 5.0) -> int | None:
    pid = _read_pid(path)
    if not pid or not _pid_alive(pid):
        path.unlink(missing_ok=True)
        return pid
    os.killpg(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            path.unlink(missing_ok=True)
            return pid
        time.sleep(0.1)
    os.killpg(pid, signal.SIGKILL)
    path.unlink(missing_ok=True)
    return pid


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


def _python_path() -> Path:
    backend_dir = REPO_ROOT / "backend"
    for candidate in (
        backend_dir / "venv" / "bin" / "python3",
        backend_dir / ".venv" / "bin" / "python3",
    ):
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _local_bridge_target(env: Mapping[str, str]) -> tuple[str, int] | None:
    parsed = urlparse(env.get("PORTFOLIO_INBOUND_URL", ""))
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return None
    if parsed.scheme == "https":
        return None
    return parsed.hostname or "127.0.0.1", parsed.port or 80


def _port_accepts(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, *, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_accepts(host, port):
            return True
        time.sleep(0.2)
    return False


def _bridge_env(env: Mapping[str, str]) -> dict[str, str]:
    merged = {**os.environ, **dict(env)}
    merged.setdefault("PORTFOLIO_OUTBOUND_URL", f"http://127.0.0.1:{env['PG_WA_SEND_PORT']}")
    merged.setdefault("PORTFOLIO_OUTBOUND_ACCOUNT_ID", "portfolio-guru")
    merged.setdefault("PORTFOLIO_OUTBOUND_SECRET", env["PG_WA_OUTBOUND_SECRET"])
    merged.setdefault("PORTFOLIO_OUTBOUND_GATEWAY_TOKEN", env["PG_WA_OUTBOUND_GATEWAY_TOKEN"])
    return merged


def _start_local_bridge_if_needed(env: Mapping[str, str]) -> int | None:
    if env.get("PG_WA_MANAGE_LOCAL_BRIDGE", "1").strip().lower() in {"0", "false", "no"}:
        return None
    target = _local_bridge_target(env)
    if target is None:
        return None
    host, port = target
    if _port_accepts(host, port):
        return None

    stale_pid = _read_pid(BRIDGE_PID_FILE)
    if stale_pid and not _pid_alive(stale_pid):
        BRIDGE_PID_FILE.unlink(missing_ok=True)

    backend_dir = REPO_ROOT / "backend"
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"bridge: starting local Portfolio inbound bridge on {host}:{port}\n")
        proc = subprocess.Popen(
            [
                str(_python_path()),
                "-m",
                "uvicorn",
                "webhook_server:app",
                "--host",
                host,
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(backend_dir),
            env=_bridge_env(env),
            stdout=handle,
            stderr=handle,
            start_new_session=True,
        )
    BRIDGE_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    if _wait_for_port(host, port):
        return proc.pid
    _kill_pid_file(BRIDGE_PID_FILE, timeout=1.0)
    raise RuntimeError(f"local Portfolio inbound bridge did not listen on {host}:{port}")


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
    try:
        bridge_pid = _start_local_bridge_if_needed(env)
    except Exception as exc:  # noqa: BLE001 - reported as a start blocker
        return RunnerStatus(
            status="blocked",
            detail=f"local Portfolio inbound bridge failed to start: {exc}",
            log_path=str(LOG_FILE),
        )

    command = _command(env, DEFAULT_AUTH_DIR, LOG_FILE)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"\n--- beta runner start {time.strftime('%Y-%m-%dT%H:%M:%S%z')} ---\n")
        if bridge_pid:
            handle.write(f"bridge: local Portfolio inbound bridge pid={bridge_pid}\n")
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
    pid = _kill_pid_file(PID_FILE)
    _kill_pid_file(BRIDGE_PID_FILE)
    return RunnerStatus(
        status="stopped",
        detail="Portfolio Guru WhatsApp beta runner stopped",
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
