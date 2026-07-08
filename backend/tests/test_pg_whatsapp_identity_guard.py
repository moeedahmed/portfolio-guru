"""Tests for the Hermes WhatsApp identity conflict guard."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "pg_whatsapp_identity_guard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("pg_whatsapp_identity_guard", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_creds(path: Path, *, jid: str, lid: str, name: str, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "me": {"id": jid, "lid": lid, "name": name},
                "platform": "smba",
                "account": {
                    "accountSignatureKey": key,
                    "deviceSignature": f"device-{key}",
                },
            }
        ),
        encoding="utf-8",
    )


def test_identity_guard_blocks_matching_hermes_whatsapp_accounts(tmp_path: Path) -> None:
    guard = _load_module()
    pg = tmp_path / "pg" / "creds.json"
    emgurus = tmp_path / "emgurus" / "creds.json"
    _write_creds(pg, jid="111:3@s.whatsapp.net", lid="999:3@lid", name="PG", key="same")
    _write_creds(
        emgurus, jid="111:3@s.whatsapp.net", lid="999:3@lid", name="EM", key="same"
    )

    result = guard.evaluate(pg, emgurus)

    assert result["status"] == "blocked"
    assert result["portfolio_guru"]["fingerprint"]
    assert result["portfolio_guru"]["fingerprint"] == result["emgurus"]["fingerprint"]
    assert any(
        check["name"] == "distinct-hermes-whatsapp-identity"
        and check["status"] == "block"
        for check in result["checks"]
    )


def test_identity_guard_allows_distinct_hermes_whatsapp_accounts(tmp_path: Path) -> None:
    guard = _load_module()
    pg = tmp_path / "pg" / "creds.json"
    emgurus = tmp_path / "emgurus" / "creds.json"
    _write_creds(pg, jid="111:3@s.whatsapp.net", lid="999:3@lid", name="PG", key="pg")
    _write_creds(
        emgurus, jid="222:3@s.whatsapp.net", lid="888:3@lid", name="EM", key="em"
    )

    result = guard.evaluate(pg, emgurus)

    assert result["status"] == "launch-ready"
    assert result["portfolio_guru"]["fingerprint"] != result["emgurus"]["fingerprint"]


def test_identity_guard_output_is_redacted(tmp_path: Path) -> None:
    guard = _load_module()
    pg = tmp_path / "pg" / "creds.json"
    emgurus = tmp_path / "emgurus" / "creds.json"
    _write_creds(
        pg,
        jid="447533190563:3@s.whatsapp.net",
        lid="84125843243120:3@lid",
        name="PG",
        key="pg-secret-key",
    )
    _write_creds(
        emgurus,
        jid="923001112222:3@s.whatsapp.net",
        lid="111222333444:3@lid",
        name="EM",
        key="em-secret-key",
    )

    result_text = json.dumps(guard.evaluate(pg, emgurus), sort_keys=True)

    assert "447533190563" not in result_text
    assert "84125843243120" not in result_text
    assert "923001112222" not in result_text
    assert "pg-secret-key" not in result_text
    assert "em-secret-key" not in result_text
