#!/usr/bin/env python3
"""Read-only readiness guard for Portfolio Guru WhatsApp rollout.

This script is intentionally inert by default. It inspects repo-owned files and
externally supplied, non-secret identifiers only. It never reads BWS, raw
credential material, Hermes profile state, or runtime service state.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


# The hard rollout requirement is a dedicated Portfolio Guru WhatsApp
# number/account behind a channel connector, distinct from the EMGurus account,
# with legal sign-off. A dedicated Hermes profile is NOT required — Hermes is an
# optional thin-transport connector, selected only when PG_WHATSAPP_CONNECTOR is
# "hermes". Portfolio Guru's deterministic engine is always the product brain.
EXPECTED_APPROVALS = {
    "PG_WHATSAPP_ROLLOUT_APPROVED": "dedicated-portfolio-guru-whatsapp",
    "PG_WHATSAPP_LEGAL_APPROVED": "meta-whatsapp-processor-reviewed",
    "PG_WHATSAPP_NUMBER_APPROVED": "dedicated-number-ready",
    "PG_WHATSAPP_ACCOUNT_HEALTH_APPROVED": "verified-stable-no-restrictions",
    "PG_WHATSAPP_CONNECTOR_APPROVED": "channel-connector-ready",
}

REQUIRED_SAFE_IDS = (
    "PG_WHATSAPP_ACCOUNT_FINGERPRINT",
    "EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT",
)

# Hermes-specific identifiers — only required when the chosen connector is
# Hermes (PG_WHATSAPP_CONNECTOR="hermes"). For a direct channel connector they
# are irrelevant and never gate the rollout.
HERMES_SAFE_IDS = (
    "PG_WHATSAPP_PROFILE_ID",
    "EMGURUS_WHATSAPP_PROFILE_ID",
)

# The direct linked-device connector family. These treat WhatsApp as a thin
# transport to the deterministic engine and need no Hermes profile. Their
# transport normaliser is the repo-owned backend/whatsapp_linked_device.py.
# "hermes" is the only value that pulls in the optional Hermes-profile gates.
# The set mirrors whatsapp_linked_device.LINKED_DEVICE_CONNECTORS.
LINKED_DEVICE_CONNECTORS = ("direct", "linked-device", "baileys")
OFFICIAL_API_CONNECTORS = (
    "cloud-api",
    "meta-cloud-api",
    "whatsapp-business-platform",
    "kapso",
    "2chat-waba",
)
KNOWN_CONNECTORS = (*LINKED_DEVICE_CONNECTORS, *OFFICIAL_API_CONNECTORS, "hermes")

STALE_PHRASES = (
    "WhatsApp should sit behind the " + "EMGurus gateway",
    "single " + "EMGurus WhatsApp Gateway",
    "single " + "EMGurus WhatsApp business number",
    "single " + "EMGurus/Guru WhatsApp front door",
    "behind one " + "EMGurus WhatsApp Gateway",
    "sits behind one " + "EMGurus",
)

# Wording that would re-encode an accidental bias toward one transport route as
# the automatic default. The rollout is route-neutral: Baileys/linked-device is a
# valid lean controlled-beta route and official/BSP/Cloud API is a valid durable
# production route, but neither is "preferred", "safe by default", or "first".
# The transport choice is an explicit, gated operations decision. If any of these
# phrases reappear in the rollout plan, the guard blocks so the bias cannot land
# silently. Matching is case-insensitive.
BIASED_TRANSPORT_PHRASES = (
    "preferred safety order",
    "official route first",
    "official whatsapp business platform / bsp route (",
    "default to the official",
    "cloud api first",
    "bsp first",
    "prefer the official route",
    "always use the official",
)


def _find_biased_transport_phrases(text: str) -> list[str]:
    """Return biased transport-route phrases present in ``text`` (lowercased)."""
    haystack = text.lower()
    return [phrase for phrase in BIASED_TRANSPORT_PHRASES if phrase in haystack]


def _connector(env: Mapping[str, str]) -> str:
    """The operator-selected WhatsApp channel connector.

    No transport route is the automatic default. When ``PG_WHATSAPP_CONNECTOR``
    is unset the guard falls back to the ``"direct"`` family only so the offline
    report has a connector to validate; that fallback is a reporting convenience,
    not a recommendation of one route over another. Only ``"hermes"`` pulls in
    the optional Hermes-profile gates below; every other value treats WhatsApp as
    a thin channel connector to the deterministic engine and requires no Hermes
    profile.
    """
    return (env.get("PG_WHATSAPP_CONNECTOR") or "direct").strip().lower()


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _check(condition: bool, name: str, pass_detail: str, block_detail: str) -> Check:
    return Check(
        name=name,
        status="pass" if condition else "block",
        detail=pass_detail if condition else block_detail,
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _file_contains(path: Path, needles: tuple[str, ...]) -> bool:
    if not path.is_file():
        return False
    text = _read_text(path).lower()
    return all(needle.lower() in text for needle in needles)


def _no_stale_phrases(root: Path) -> Check:
    files = (
        root / "WORKFLOWS.md",
        root / "docs" / "plan.md",
        root / "docs" / "PUBLIC_PRODUCT_PLAN_2026-06-17.md",
        root / "backend" / "channel_contract.py",
        root / "backend" / "webhook_server.py",
        root / "backend" / "whatsapp_linked_device.py",
        root / "backend" / "whatsapp_connector_runner.py",
        root / "backend" / "tests" / "test_channel_contract.py",
        root / "backend" / "tests" / "test_portfolio_inbound_bridge.py",
        root / "backend" / "tests" / "test_whatsapp_linked_device.py",
    )
    matches: list[str] = []
    for path in files:
        if not path.is_file():
            continue
        text = _read_text(path)
        for phrase in STALE_PHRASES:
            if phrase in text:
                matches.append(f"{path.relative_to(root)}: {phrase}")
    return _check(
        not matches,
        "stale-emgurus-gateway-wording",
        "no stale shared EMGurus WhatsApp rollout wording found",
        "shared-account rollout wording remains: " + "; ".join(matches),
    )


def _no_transport_route_bias(root: Path) -> Check:
    rollout_doc = root / "docs" / "hermes" / "WHATSAPP_ROLLOUT_PLAN.md"
    matches: list[str] = []
    if rollout_doc.is_file():
        for phrase in _find_biased_transport_phrases(_read_text(rollout_doc)):
            matches.append(f"{rollout_doc.relative_to(root)}: {phrase}")
    return _check(
        not matches,
        "no-transport-route-bias",
        "rollout plan keeps transport routes neutral (no route is the automatic default)",
        "rollout plan re-encodes transport-route bias: " + "; ".join(matches),
    )


def _approval_checks(env: Mapping[str, str]) -> list[Check]:
    checks: list[Check] = []
    for key, expected in EXPECTED_APPROVALS.items():
        checks.append(
            _check(
                env.get(key) == expected,
                f"approval:{key}",
                f"{key} is explicitly approved",
                f"{key} must equal {expected!r}",
            )
        )
    return checks


def _safe_id_checks(env: Mapping[str, str]) -> list[Check]:
    checks: list[Check] = []
    for key in REQUIRED_SAFE_IDS:
        checks.append(
            _check(
                bool(env.get(key, "").strip()),
                f"safe-id:{key}",
                f"{key} is supplied",
                f"{key} must be supplied as a non-secret identifier",
            )
        )

    pg_account = env.get("PG_WHATSAPP_ACCOUNT_FINGERPRINT", "").strip()
    emgurus_account = env.get("EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT", "").strip()
    checks.append(
        _check(
            bool(pg_account and emgurus_account and pg_account != emgurus_account),
            "distinct-whatsapp-account",
            "Portfolio Guru and EMGurus WhatsApp fingerprints are present and distinct",
            "Portfolio Guru and EMGurus WhatsApp fingerprints must be present and different",
        )
    )

    # Hermes profile gates apply only when Hermes is the chosen connector. A
    # direct channel connector needs no Hermes profile at all.
    if _connector(env) != "hermes":
        return checks

    for key in HERMES_SAFE_IDS:
        checks.append(
            _check(
                bool(env.get(key, "").strip()),
                f"safe-id:{key}",
                f"{key} is supplied",
                f"{key} must be supplied when PG_WHATSAPP_CONNECTOR=hermes",
            )
        )

    pg_profile = env.get("PG_WHATSAPP_PROFILE_ID", "").strip()
    emgurus_profile = env.get("EMGURUS_WHATSAPP_PROFILE_ID", "").strip()
    checks.append(
        _check(
            pg_profile == "portfolio-guru",
            "portfolio-guru-profile-id",
            "Portfolio Guru profile id is portfolio-guru",
            "PG_WHATSAPP_PROFILE_ID must equal 'portfolio-guru' when PG_WHATSAPP_CONNECTOR=hermes",
        )
    )
    checks.append(
        _check(
            bool(pg_profile and emgurus_profile and pg_profile != emgurus_profile),
            "distinct-hermes-profile",
            "Portfolio Guru and EMGurus profile ids are present and distinct",
            "Portfolio Guru and EMGurus profile ids must be present and different",
        )
    )
    return checks


def evaluate(root: Path, env: Mapping[str, str] | None = None) -> dict[str, object]:
    env = os.environ if env is None else env
    root = root.resolve()
    checks: list[Check] = []

    rollout_doc = root / "docs" / "hermes" / "WHATSAPP_ROLLOUT_PLAN.md"
    legal_doc = root / "docs" / "legal" / "whatsapp-meta-processor-review.md"
    shim = root / "scripts" / "hermes-profile" / "pg"
    linked_device_adapter = root / "backend" / "whatsapp_linked_device.py"
    connector_shell = root / "backend" / "whatsapp_connector_runner.py"
    sidecar_dir = root / "connectors" / "whatsapp-linked-device"
    sidecar_pkg = sidecar_dir / "package.json"
    sidecar_entry = sidecar_dir / "index.js"

    connector = _connector(env)
    checks.append(
        _check(
            connector in KNOWN_CONNECTORS,
            "connector-recognised",
            f"PG_WHATSAPP_CONNECTOR={connector!r} is a recognised connector",
            "PG_WHATSAPP_CONNECTOR must be one of "
            + ", ".join(repr(name) for name in KNOWN_CONNECTORS),
        )
    )
    # A direct linked-device connector is only launch-ready when its repo-owned
    # transport code exists. Readiness is tiered so the claim is tied to real
    # code, not configuration alone:
    #   * adapter-present  — the neutral transport normaliser exists;
    #   * shell-present    — the runnable relay shell that drives the normaliser
    #                        and forwards to the inbound bridge exists.
    #   * sidecar-present  — the isolated Baileys/WhatsApp-Web sidecar that emits
    #                        the QR and streams raw NDJSON events into the shell
    #                        exists as repo-owned transport code.
    # A further tier, "live-linked" (a real WhatsApp linked-device session), is a
    # runtime state proven manually out-of-band and is deliberately NOT asserted
    # here, so this guard never claims a device is linked. sidecar-present only
    # attests the transport code exists, never that a device has been linked.
    # Hermes uses its own shim gate below instead.
    if connector in LINKED_DEVICE_CONNECTORS:
        checks.append(
            _check(
                _file_contains(
                    linked_device_adapter,
                    ("normalize_message", "accept_inbound", "InboundMessage"),
                ),
                "linked-device-adapter-present",
                "direct linked-device transport normaliser is present and delegates to the neutral contract",
                "backend/whatsapp_linked_device.py must provide the direct linked-device transport normaliser",
            )
        )
        checks.append(
            _check(
                _file_contains(
                    connector_shell,
                    ("relay_events", "to_inbound_payload", "run_dry_run"),
                ),
                "connector-shell-present",
                "runnable linked-device connector shell is present and relays via the neutral normaliser",
                "backend/whatsapp_connector_runner.py must provide the runnable linked-device connector relay shell",
            )
        )
        checks.append(
            _check(
                _file_contains(sidecar_pkg, ("@whiskeysockets/baileys",))
                and _file_contains(
                    sidecar_entry, ("messages.upsert", "connection.update", "--mock")
                ),
                "linked-device-sidecar-present",
                "isolated Baileys/WhatsApp-Web sidecar transport is present (QR + NDJSON streaming); this attests transport code only, not a live-linked device",
                "connectors/whatsapp-linked-device must provide the Baileys sidecar entrypoint and package that streams raw events into the connector shell",
            )
        )

    checks.append(
        _check(
            _file_contains(
                rollout_doc,
                (
                    "dedicated portfolio guru whatsapp",
                    "readiness guard",
                    "channel connector",
                    "meta/whatsapp processor review",
                ),
            ),
            "rollout-plan",
            "WhatsApp rollout plan is present and names the dedicated-account gates",
            "docs/hermes/WHATSAPP_ROLLOUT_PLAN.md is missing required rollout language",
        )
    )
    checks.append(
        _check(
            _file_contains(
                legal_doc,
                (
                    "draft",
                    "meta",
                    "whatsapp",
                    "processor",
                    "transfer risk assessment",
                    "data processing terms",
                ),
            ),
            "legal-processor-note",
            "WhatsApp/Meta processor review note is present",
            "docs/legal/whatsapp-meta-processor-review.md is missing required review language",
        )
    )
    # The Hermes profile shim only gates the rollout when Hermes is the chosen
    # connector; a direct channel connector does not use it at all.
    if _connector(env) == "hermes":
        checks.append(
            _check(
                _file_contains(shim, ("hermes_pg_cli", "execvpe")),
                "profile-shim-delegates",
                "tracked Hermes profile shim delegates to backend/hermes_pg_cli.py",
                "scripts/hermes-profile/pg must remain a thin delegating shim",
            )
        )
    checks.append(_no_stale_phrases(root))
    checks.append(_no_transport_route_bias(root))
    checks.extend(_approval_checks(env))
    checks.extend(_safe_id_checks(env))

    blocked = [check for check in checks if check.status != "pass"]
    status = "blocked" if blocked else "launch-ready"
    return {
        "status": status,
        "summary": (
            "WhatsApp rollout remains blocked"
            if blocked
            else "Dedicated Portfolio Guru WhatsApp rollout gates are satisfied"
        ),
        "checks": [asdict(check) for check in checks],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Portfolio Guru WhatsApp rollout readiness guard."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Portfolio Guru repo root (defaults to this script's parent repo).",
    )
    args = parser.parse_args(argv)
    result = evaluate(args.repo_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "launch-ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
