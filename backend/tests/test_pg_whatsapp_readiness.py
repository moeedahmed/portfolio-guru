"""Tests for the repo-owned WhatsApp rollout readiness guard.

The guard is read-only, disabled by default, and only reaches launch-ready
when explicit approvals plus distinct safe account/profile identifiers are
supplied by the operator.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "pg_whatsapp_readiness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pg_whatsapp_readiness", SCRIPT_PATH)
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
        "PG_WHATSAPP_PROFILE_APPROVED": "dedicated-hermes-profile-ready",
        "PG_WHATSAPP_ACCOUNT_FINGERPRINT": "pg-safe-fingerprint",
        "EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT": "emgurus-safe-fingerprint",
        "PG_WHATSAPP_PROFILE_ID": "portfolio-guru",
        "EMGURUS_WHATSAPP_PROFILE_ID": "emgurus",
    }
    env.update(overrides)
    return env


def test_readiness_guard_blocks_by_default() -> None:
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env={})

    assert result["status"] == "blocked"
    blocked_names = {
        check["name"] for check in result["checks"] if check["status"] == "block"
    }
    assert "approval:PG_WHATSAPP_ROLLOUT_APPROVED" in blocked_names
    assert "safe-id:PG_WHATSAPP_ACCOUNT_FINGERPRINT" in blocked_names


def test_readiness_guard_requires_distinct_whatsapp_account_fingerprints() -> None:
    guard = _load_module()

    result = guard.evaluate(
        REPO_ROOT,
        env=_approved_env(
            PG_WHATSAPP_ACCOUNT_FINGERPRINT="same-account",
            EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT="same-account",
        ),
    )

    assert result["status"] == "blocked"
    blocked_names = {
        check["name"] for check in result["checks"] if check["status"] == "block"
    }
    assert "distinct-whatsapp-account" in blocked_names


def test_readiness_guard_requires_portfolio_guru_profile_id() -> None:
    guard = _load_module()

    result = guard.evaluate(
        REPO_ROOT,
        env=_approved_env(PG_WHATSAPP_PROFILE_ID="portfolio-guru-shared"),
    )

    assert result["status"] == "blocked"
    blocked_names = {
        check["name"] for check in result["checks"] if check["status"] == "block"
    }
    assert "portfolio-guru-profile-id" in blocked_names


def test_readiness_guard_launch_ready_with_explicit_approvals_and_distinct_ids() -> None:
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env=_approved_env())

    assert result["status"] == "launch-ready"
    assert all(check["status"] == "pass" for check in result["checks"])


def test_cli_outputs_json_and_does_not_expose_safe_identifier_values() -> None:
    env = _approved_env(
        PG_WHATSAPP_ACCOUNT_FINGERPRINT="pg-secret-looking-safe-id",
        EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT="emgurus-secret-looking-safe-id",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--repo-root", str(REPO_ROOT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "launch-ready"
    assert "pg-secret-looking-safe-id" not in result.stdout
    assert "emgurus-secret-looking-safe-id" not in result.stdout
