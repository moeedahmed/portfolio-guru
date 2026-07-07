"""Tests for the runnable direct WhatsApp linked-device connector shell.

The runner is the transport half of the direct linked-device path: it normalises
raw linked-device envelopes via the repo-owned neutral contract and, in relay
mode, forwards only handled turns to the ``POST /api/portfolio/inbound`` bridge.
These tests pin that boundary:

* dry-run over a recorded batch returns routing metadata only and contacts no
  service, no WhatsApp, no secret;
* relay uses ``whatsapp_linked_device.to_inbound_payload`` for the bridge body
  and forwards **only** DIRECT non-empty turns — GROUP and empty turns are
  dropped locally and never posted;
* the live poster sends the shared gateway secret as ``X-Gateway-Secret`` and is
  configured by env-var name, never a hardcoded value;
* relay is gated on the readiness guard and refuses (default blocked) with no
  network call;
* the module is import-clean of Telegram and the product engine.

No network, no credentials, no live WhatsApp/Telegram service.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import whatsapp_connector_runner as runner

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "whatsapp_linked_device_events.json"
)


def _recorded_events() -> list[dict]:
    with open(_FIXTURE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def test_dry_run_over_recorded_batch_returns_routing_metadata_only():
    events = _recorded_events()
    results = runner.run_dry_run(events)

    assert [r["disposition"] for r in results] == [
        "handle",
        "handle",
        "refuse_group",
        "refuse_empty",
    ]
    # Routing metadata only — the recorded clinical/caption text must not appear.
    serialized = json.dumps(results)
    assert "synthetic direct case text" not in serialized
    assert "synthetic caption" not in serialized
    assert "synthetic group message" not in serialized


def test_relay_forwards_only_handled_turns_using_the_normaliser():
    events = _recorded_events()
    posted: list[dict] = []

    stats = runner.relay_events(events, posted.append)

    # Only the two DIRECT non-empty turns are forwarded.
    assert stats.total == 4
    assert stats.forwarded == 2
    assert stats.refused_group == 1
    assert stats.refused_empty == 1
    assert len(posted) == 2
    # The forwarded body is exactly the neutral normaliser payload.
    import whatsapp_linked_device as wld

    assert posted[0] == wld.to_inbound_payload(events[0])
    assert posted[0]["channel"] == "whatsapp"
    assert posted[0]["scope"] == "direct"


def test_relay_never_forwards_group_content():
    group_event = {
        "key": {"remoteJid": "120363000000000000@g.us", "id": "G1"},
        "message": {"conversation": "patient John Doe MRN 12345"},
    }
    posted: list[dict] = []
    stats = runner.relay_events([group_event], posted.append)

    assert stats.forwarded == 0
    assert stats.refused_group == 1
    assert posted == []


def test_make_bridge_poster_sends_shared_secret_header(monkeypatch):
    captured: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            captured["raised"] = True

    def _fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)

    poster = runner.make_bridge_poster("http://bridge.local/api/portfolio/inbound", "s3cr3t")
    poster({"channel": "whatsapp", "scope": "direct"})

    assert captured["url"] == "http://bridge.local/api/portfolio/inbound"
    assert captured["headers"]["X-Gateway-Secret"] == "s3cr3t"
    assert captured["json"] == {"channel": "whatsapp", "scope": "direct"}


def test_relay_cli_is_blocked_by_default_and_makes_no_network_call(monkeypatch, tmp_path):
    """With no approvals in the environment the readiness guard blocks relay."""
    # Guarantee no accidental approvals leak in from the ambient environment.
    for key in (
        "PG_WHATSAPP_ROLLOUT_APPROVED",
        "PG_WHATSAPP_LEGAL_APPROVED",
        "PG_WHATSAPP_NUMBER_APPROVED",
        "PG_WHATSAPP_CONNECTOR_APPROVED",
        "PG_WHATSAPP_ACCOUNT_FINGERPRINT",
        "EMGURUS_WHATSAPP_ACCOUNT_FINGERPRINT",
    ):
        monkeypatch.delenv(key, raising=False)

    # If relay ever tried to post, this would raise instead of silently passing.
    import httpx

    def _boom(*args, **kwargs):
        raise AssertionError("relay must not contact the bridge when blocked")

    monkeypatch.setattr(httpx, "post", _boom)

    payload_file = tmp_path / "events.json"
    payload_file.write_text(json.dumps(_recorded_events()), encoding="utf-8")

    rc = runner.main(["--relay", "--payload", str(payload_file)])
    assert rc == 3


def test_readiness_status_defaults_to_blocked():
    assert runner._readiness_status(env={}) == "blocked"


def test_iter_events_accepts_array_object_and_ndjson():
    obj = {"key": {"remoteJid": "x@s.whatsapp.net", "id": "1"}, "message": {"conversation": "hi"}}
    assert runner._iter_events(json.dumps(obj)) == [obj]
    assert runner._iter_events(json.dumps([obj, obj])) == [obj, obj]
    ndjson = json.dumps(obj) + "\n\n" + json.dumps(obj)
    assert runner._iter_events(ndjson) == [obj, obj]
    assert runner._iter_events("   ") == []


def test_cli_dry_run_prints_verdicts_without_content(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, "whatsapp_connector_runner.py", "--payload", _FIXTURE],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert [d["disposition"] for d in data] == [
        "handle",
        "handle",
        "refuse_group",
        "refuse_empty",
    ]
    assert "synthetic direct case text" not in result.stdout
    assert "synthetic caption" not in result.stdout


def test_module_imports_without_telegram_or_product_engine():
    """The connector shell must not pull in Telegram or the product brain."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, whatsapp_connector_runner; "
            "bad = [m for m in ('telegram', 'extractor', 'bot') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)",
        ],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"connector shell pulled in a forbidden module:\n{result.stderr}"
    )


def test_relay_configured_by_env_name_not_hardcoded_secret():
    """The runner references the inbound secret by env-var name only."""
    here = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(here, "..", "whatsapp_connector_runner.py")
    with open(module_path, "r", encoding="utf-8") as handle:
        source = handle.read()
    assert "PORTFOLIO_INBOUND_SECRET" in source
    assert "PORTFOLIO_INBOUND_URL" in source
    # The secret is read from the environment, never assigned a literal value.
    assert 'PORTFOLIO_INBOUND_SECRET"' in source
    assert "PORTFOLIO_INBOUND_SECRET =" not in source
