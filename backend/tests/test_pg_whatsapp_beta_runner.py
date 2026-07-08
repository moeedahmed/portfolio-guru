"""Tests for the saved-session WhatsApp beta runner supervisor.

The supervisor is the controlled-beta operating mode: it may start the already
linked local sidecar/relay, but it must never create a new QR or second writer.
These tests do not start WhatsApp, the bridge, or the sidecar.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "pg_whatsapp_beta_runner.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pg_whatsapp_beta_runner", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _approved_env(**overrides: str) -> dict[str, str]:
    env = {
        "PG_WHATSAPP_ROLLOUT_APPROVED": "dedicated-portfolio-guru-whatsapp",
        "PG_WHATSAPP_LEGAL_APPROVED": "meta-whatsapp-processor-reviewed",
        "PG_WHATSAPP_NUMBER_APPROVED": "dedicated-number-ready",
        "PG_WHATSAPP_ACCOUNT_HEALTH_APPROVED": "verified-stable-no-restrictions",
        "PG_WHATSAPP_CONNECTOR_APPROVED": "channel-connector-ready",
        "PG_WHATSAPP_CONNECTOR": "linked-device",
        "PG_WHATSAPP_ACCOUNT_FINGERPRINT": "pg-safe-fingerprint",
        "EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT": "emgurus-safe-fingerprint",
        "PORTFOLIO_INBOUND_URL": "http://127.0.0.1:8101/api/portfolio/inbound",
        "PORTFOLIO_INBOUND_SECRET": "test-secret",
        "PG_WA_SEND_PORT": "18795",
    }
    env.update(overrides)
    return env


def test_start_plan_blocks_without_saved_auth(tmp_path, monkeypatch) -> None:
    runner = _load_module()
    monkeypatch.setattr(runner, "PID_FILE", tmp_path / "beta-runner.pid")
    monkeypatch.setattr(runner, "LOG_FILE", tmp_path / "beta-runner.log")

    result = runner.build_start_plan(_approved_env(), auth_dir=tmp_path / ".wa-auth")

    assert result.status == "blocked"
    assert "saved linked-device auth is missing" in result.detail
    assert "QR is forbidden" in result.detail


def test_start_plan_requires_bridge_and_outbound_env(tmp_path, monkeypatch) -> None:
    runner = _load_module()
    auth_dir = tmp_path / ".wa-auth"
    auth_dir.mkdir()
    (auth_dir / "creds.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "PID_FILE", tmp_path / "beta-runner.pid")
    monkeypatch.setattr(runner, "LOG_FILE", tmp_path / "beta-runner.log")
    env = _approved_env()
    env.pop("PG_WA_SEND_PORT")

    result = runner.build_start_plan(env, auth_dir=auth_dir)

    assert result.status == "blocked"
    assert "PG_WA_SEND_PORT" in result.detail


def test_start_plan_ready_when_gates_auth_and_env_are_present(tmp_path, monkeypatch) -> None:
    runner = _load_module()
    auth_dir = tmp_path / ".wa-auth"
    auth_dir.mkdir()
    (auth_dir / "creds.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "PID_FILE", tmp_path / "beta-runner.pid")
    monkeypatch.setattr(runner, "LOG_FILE", tmp_path / "beta-runner.log")

    result = runner.build_start_plan(_approved_env(), auth_dir=auth_dir)

    assert result.status == "ready"
    assert "without QR" in result.detail


def test_supervised_command_forbids_qr_and_uses_saved_auth(tmp_path) -> None:
    runner = _load_module()
    auth_dir = tmp_path / ".wa-auth"
    log_file = tmp_path / "beta-runner.log"
    command = runner._command(_approved_env(), auth_dir, log_file)

    assert "--forbid-qr" in command
    assert "PG_WA_FORBID_QR=1" in command
    assert str(auth_dir) in command
    assert "whatsapp_connector_runner.py --relay" in command
    # The command uses env-var names and shell redirection, not literal secrets.
    assert "test-secret" not in command


def test_start_refuses_second_running_writer(tmp_path, monkeypatch) -> None:
    runner = _load_module()
    monkeypatch.setattr(runner, "PID_FILE", tmp_path / "beta-runner.pid")
    monkeypatch.setattr(runner, "LOG_FILE", tmp_path / "beta-runner.log")
    monkeypatch.setattr(runner, "_pid_alive", lambda _pid: True)
    (tmp_path / "creds.json").write_text("{}", encoding="utf-8")
    runner.PID_FILE.write_text("12345", encoding="utf-8")

    result = runner.build_start_plan(_approved_env(), auth_dir=tmp_path)

    assert result.status == "blocked"
    assert "already running" in result.detail
