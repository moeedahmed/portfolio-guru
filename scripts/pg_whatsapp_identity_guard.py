#!/usr/bin/env python3
"""Check Hermes WhatsApp account identity before Portfolio Guru rollout.

This guard reads only local Hermes Baileys ``creds.json`` files and emits
redacted fingerprints. It never prints phone numbers, JIDs, QR material, auth
keys, or raw credential values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_PG_CREDS = Path.home() / ".hermes/profiles/portfolio-guru/whatsapp/session/creds.json"
DEFAULT_EMGURUS_CREDS = Path.home() / ".hermes/profiles/emgurus/whatsapp/session/creds.json"


@dataclass(frozen=True)
class Identity:
    path: str
    present: bool
    fingerprint: str | None
    display_name: str | None
    platform: str | None


def _safe_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _identity_material(data: dict[str, Any]) -> str | None:
    me = data.get("me") if isinstance(data.get("me"), dict) else {}
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    candidates = [
        me.get("id"),
        me.get("lid"),
        account.get("accountSignatureKey"),
        account.get("deviceSignature"),
    ]
    parts = [str(part) for part in candidates if part]
    return "\n".join(parts) if parts else None


def read_identity(path: Path) -> Identity:
    if not path.is_file():
        return Identity(
            path=str(path),
            present=False,
            fingerprint=None,
            display_name=None,
            platform=None,
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    material = _identity_material(data)
    me = data.get("me") if isinstance(data.get("me"), dict) else {}
    return Identity(
        path=str(path),
        present=True,
        fingerprint=_safe_fingerprint(material) if material else None,
        display_name=me.get("name") if isinstance(me.get("name"), str) else None,
        platform=data.get("platform") if isinstance(data.get("platform"), str) else None,
    )


def evaluate(pg_creds: Path, emgurus_creds: Path) -> dict[str, Any]:
    pg = read_identity(pg_creds)
    emgurus = read_identity(emgurus_creds)
    checks: list[dict[str, str]] = []

    checks.append(
        {
            "name": "portfolio-creds-present",
            "status": "pass" if pg.present and pg.fingerprint else "block",
            "detail": "Portfolio Guru Hermes WhatsApp creds are readable"
            if pg.present and pg.fingerprint
            else "Portfolio Guru Hermes WhatsApp creds are missing or unreadable",
        }
    )
    checks.append(
        {
            "name": "emgurus-creds-present",
            "status": "pass" if emgurus.present and emgurus.fingerprint else "block",
            "detail": "EMGurus Hermes WhatsApp creds are readable"
            if emgurus.present and emgurus.fingerprint
            else "EMGurus Hermes WhatsApp creds are missing or unreadable",
        }
    )

    distinct = bool(
        pg.fingerprint
        and emgurus.fingerprint
        and pg.fingerprint != emgurus.fingerprint
    )
    checks.append(
        {
            "name": "distinct-hermes-whatsapp-identity",
            "status": "pass" if distinct else "block",
            "detail": "Portfolio Guru and EMGurus Hermes WhatsApp identities are distinct"
            if distinct
            else "Portfolio Guru and EMGurus Hermes WhatsApp identities match; running both bridges will cause connectionReplaced conflicts",
        }
    )

    blocked = [check for check in checks if check["status"] != "pass"]
    return {
        "status": "blocked" if blocked else "launch-ready",
        "summary": "Hermes WhatsApp identity conflict detected"
        if blocked
        else "Hermes WhatsApp identities are distinct",
        "portfolio_guru": asdict(pg),
        "emgurus": asdict(emgurus),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Portfolio Guru and EMGurus Hermes WhatsApp identities are distinct."
    )
    parser.add_argument("--portfolio-creds", type=Path, default=DEFAULT_PG_CREDS)
    parser.add_argument("--emgurus-creds", type=Path, default=DEFAULT_EMGURUS_CREDS)
    args = parser.parse_args(argv)
    result = evaluate(args.portfolio_creds, args.emgurus_creds)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "launch-ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
