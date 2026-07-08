"""Tests for the repo-owned WhatsApp rollout readiness guard.

The guard is read-only, disabled by default, and only reaches launch-ready
when explicit approvals plus distinct safe account identifiers are supplied by
the operator. The hard gates are a dedicated Portfolio Guru number/account, a
distinct EMGurus account, a ready channel connector, and legal approval — a
Hermes profile is optional thin transport and is only gated when the chosen
connector is Hermes.
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
    """Happy-path env for the lean *direct* connector — no Hermes profile."""
    env = {
        "PG_WHATSAPP_ROLLOUT_APPROVED": "dedicated-portfolio-guru-whatsapp",
        "PG_WHATSAPP_LEGAL_APPROVED": "meta-whatsapp-processor-reviewed",
        "PG_WHATSAPP_NUMBER_APPROVED": "dedicated-number-ready",
        "PG_WHATSAPP_ACCOUNT_HEALTH_APPROVED": "verified-stable-no-restrictions",
        "PG_WHATSAPP_CONNECTOR_APPROVED": "channel-connector-ready",
        "PG_WHATSAPP_ACCOUNT_FINGERPRINT": "pg-safe-fingerprint",
        "EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT": "emgurus-safe-fingerprint",
    }
    env.update(overrides)
    return env


def _hermes_env(**overrides: str) -> dict[str, str]:
    """Happy-path env when the *optional* Hermes connector is chosen."""
    env = _approved_env(
        PG_WHATSAPP_CONNECTOR="hermes",
        PG_WHATSAPP_PROFILE_ID="portfolio-guru",
        EMGURUS_WHATSAPP_PROFILE_ID="emgurus",
        PG_WHATSAPP_HERMES_IDENTITY_APPROVED="distinct-live-hermes-identity",
    )
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
    assert "approval:PG_WHATSAPP_ACCOUNT_HEALTH_APPROVED" in blocked_names
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


def test_hermes_connector_requires_portfolio_guru_profile_id() -> None:
    guard = _load_module()

    result = guard.evaluate(
        REPO_ROOT,
        env=_hermes_env(PG_WHATSAPP_PROFILE_ID="portfolio-guru-shared"),
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


def test_direct_connector_launch_ready_without_any_hermes_profile() -> None:
    """The lean direct connector is launch-ready with NO Hermes profile at all.

    Hermes is optional thin transport, so the guard must not gate on any
    Hermes-profile identifier when the connector is not Hermes.
    """
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env=_approved_env())

    assert result["status"] == "launch-ready"
    check_names = {check["name"] for check in result["checks"]}
    # No Hermes-profile gate is even evaluated for a direct connector.
    assert "portfolio-guru-profile-id" not in check_names
    assert "distinct-hermes-profile" not in check_names
    assert "profile-shim-delegates" not in check_names
    assert "safe-id:PG_WHATSAPP_PROFILE_ID" not in check_names


def test_hermes_connector_requires_profile_ids() -> None:
    """Choosing the Hermes connector re-introduces the profile-id gates."""
    guard = _load_module()

    result = guard.evaluate(
        REPO_ROOT, env=_approved_env(PG_WHATSAPP_CONNECTOR="hermes")
    )

    assert result["status"] == "blocked"
    blocked_names = {
        check["name"] for check in result["checks"] if check["status"] == "block"
    }
    assert "safe-id:PG_WHATSAPP_PROFILE_ID" in blocked_names
    assert "approval:PG_WHATSAPP_HERMES_IDENTITY_APPROVED" in blocked_names


def test_hermes_connector_requires_live_identity_guard_approval() -> None:
    guard = _load_module()

    env = _hermes_env()
    env.pop("PG_WHATSAPP_HERMES_IDENTITY_APPROVED")
    result = guard.evaluate(REPO_ROOT, env=env)

    assert result["status"] == "blocked"
    blocked_names = {
        check["name"] for check in result["checks"] if check["status"] == "block"
    }
    assert "approval:PG_WHATSAPP_HERMES_IDENTITY_APPROVED" in blocked_names


def test_hermes_connector_launch_ready_with_profile() -> None:
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env=_hermes_env())

    assert result["status"] == "launch-ready"
    check_names = {check["name"] for check in result["checks"]}
    assert "portfolio-guru-profile-id" in check_names
    assert "profile-shim-delegates" in check_names


def test_linked_device_connector_is_recognised_and_launch_ready() -> None:
    """The explicit linked-device connector is a direct-family connector.

    It needs no Hermes profile and stays launch-ready with the same dedicated
    account + legal + fingerprint gates as the default direct connector.
    """
    guard = _load_module()

    result = guard.evaluate(
        REPO_ROOT, env=_approved_env(PG_WHATSAPP_CONNECTOR="linked-device")
    )

    assert result["status"] == "launch-ready"
    check_names = {check["name"] for check in result["checks"]}
    assert "connector-recognised" in check_names
    assert "linked-device-adapter-present" in check_names
    # No Hermes-profile gate is evaluated for a linked-device connector.
    assert "portfolio-guru-profile-id" not in check_names


def test_unknown_connector_value_is_blocked() -> None:
    guard = _load_module()

    result = guard.evaluate(
        REPO_ROOT, env=_approved_env(PG_WHATSAPP_CONNECTOR="whatsapp-cloud")
    )

    assert result["status"] == "blocked"
    blocked_names = {
        check["name"] for check in result["checks"] if check["status"] == "block"
    }
    assert "connector-recognised" in blocked_names


def test_official_api_connector_is_recognised_without_linked_device_gates() -> None:
    """A provider/BSP route should not require Baileys transport code.

    Official API connectors still require the same dedicated number, legal,
    account-health, connector approval, and distinct-account gates, but they
    avoid the linked-device readiness tiers entirely.
    """
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env=_approved_env(PG_WHATSAPP_CONNECTOR="kapso"))

    assert result["status"] == "launch-ready"
    check_names = {check["name"] for check in result["checks"]}
    assert "connector-recognised" in check_names
    assert "linked-device-adapter-present" not in check_names
    assert "connector-shell-present" not in check_names
    assert "linked-device-sidecar-present" not in check_names


def test_account_health_approval_is_required_after_review_or_restriction() -> None:
    guard = _load_module()

    env = _approved_env()
    env.pop("PG_WHATSAPP_ACCOUNT_HEALTH_APPROVED")
    result = guard.evaluate(REPO_ROOT, env=env)

    assert result["status"] == "blocked"
    blocked_names = {
        check["name"] for check in result["checks"] if check["status"] == "block"
    }
    assert "approval:PG_WHATSAPP_ACCOUNT_HEALTH_APPROVED" in blocked_names


def test_direct_connector_gates_on_runnable_connector_shell() -> None:
    """A direct connector ties readiness to the runnable relay shell, not just
    the adapter — this distinguishes adapter-present from connector-shell-present.
    The default (unset) connector is direct, so the shell tier is always evaluated.
    """
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env=_approved_env())

    check_names = {check["name"] for check in result["checks"]}
    assert "linked-device-adapter-present" in check_names
    assert "connector-shell-present" in check_names
    shell_check = next(
        c for c in result["checks"] if c["name"] == "connector-shell-present"
    )
    assert shell_check["status"] == "pass"
    # The guard must never assert a live-linked device — that is a manual runtime
    # state, not a repo fact.
    assert "live-linked" not in check_names


def test_direct_connector_gates_on_baileys_sidecar_present() -> None:
    """A direct connector adds a repo-owned sidecar-present tier: the isolated
    Baileys transport code must exist. It attests transport code only and must
    never assert a live-linked device.
    """
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env=_approved_env())

    check_names = {check["name"] for check in result["checks"]}
    assert "linked-device-sidecar-present" in check_names
    sidecar_check = next(
        c for c in result["checks"] if c["name"] == "linked-device-sidecar-present"
    )
    assert sidecar_check["status"] == "pass"
    # sidecar-present is a code-exists claim, never a linked-device claim: the
    # detail explicitly attests transport code only, not a live link.
    assert "live-linked" not in check_names
    assert "attests transport code only" in sidecar_check["detail"]


def test_default_direct_connector_gates_on_linked_device_adapter() -> None:
    """The default (unset) connector is direct and ties readiness to the adapter."""
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env=_approved_env())

    check_names = {check["name"] for check in result["checks"]}
    assert "linked-device-adapter-present" in check_names
    adapter_check = next(
        c for c in result["checks"] if c["name"] == "linked-device-adapter-present"
    )
    assert adapter_check["status"] == "pass"


def test_find_biased_transport_phrases_detects_official_route_first_bias() -> None:
    """The pure phrase detector catches wording that ranks one route as default."""
    guard = _load_module()

    biased = (
        "Preferred safety order after the account review incident:\n"
        "1. Official WhatsApp Business Platform / BSP route (`cloud-api`) first."
    )
    hits = guard._find_biased_transport_phrases(biased)
    assert "preferred safety order" in hits

    neutral = (
        "Transport decision gate (no route is the automatic default). Both "
        "transport families are valid and clear the same gates."
    )
    assert guard._find_biased_transport_phrases(neutral) == []


def test_guard_blocks_if_rollout_plan_reintroduces_transport_bias() -> None:
    """A no-transport-route-bias check runs and passes on the real rollout plan,
    so future edits cannot silently re-add 'official route first' wording."""
    guard = _load_module()

    result = guard.evaluate(REPO_ROOT, env=_approved_env())

    check_names = {check["name"] for check in result["checks"]}
    assert "no-transport-route-bias" in check_names
    bias_check = next(
        c for c in result["checks"] if c["name"] == "no-transport-route-bias"
    )
    assert bias_check["status"] == "pass"


def test_rollout_plan_uses_neutral_transport_decision_wording() -> None:
    """The rollout plan must present a neutral decision gate, not a ranked order."""
    rollout_doc = REPO_ROOT / "docs" / "hermes" / "WHATSAPP_ROLLOUT_PLAN.md"
    text = rollout_doc.read_text(encoding="utf-8").lower()

    assert "preferred safety order" not in text
    assert "transport decision gate" in text
    # Both routes must stay documented as valid — neither deprecated nor mandatory.
    assert "lean controlled-beta path" in text
    assert "durable production transport" in text


def test_official_and_linked_device_routes_are_gated_symmetrically() -> None:
    """Neither transport family is the implicit default: both reach launch-ready
    under identical approvals, and both stay blocked without account-health."""
    guard = _load_module()

    for connector in ("linked-device", "kapso"):
        ready = guard.evaluate(
            REPO_ROOT, env=_approved_env(PG_WHATSAPP_CONNECTOR=connector)
        )
        assert ready["status"] == "launch-ready", connector

        no_health = _approved_env(PG_WHATSAPP_CONNECTOR=connector)
        no_health.pop("PG_WHATSAPP_ACCOUNT_HEALTH_APPROVED")
        blocked = guard.evaluate(REPO_ROOT, env=no_health)
        assert blocked["status"] == "blocked", connector
        blocked_names = {
            c["name"] for c in blocked["checks"] if c["status"] == "block"
        }
        assert "approval:PG_WHATSAPP_ACCOUNT_HEALTH_APPROVED" in blocked_names


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
