"""HTTP bridge for the EMGurus WhatsApp Gateway inbound boundary.

`POST /api/portfolio/inbound` is the smallest authenticated surface the gateway
calls to learn whether Portfolio Guru will take a turn. It is a thin wrapper
around :func:`channel_contract.accept_inbound` — no side effects, no workflow,
no credential. These tests pin the boundary the gateway depends on:

* the endpoint is private: a request without the shared gateway secret is
  rejected before any routing decision;
* a DIRECT 1:1 turn with content is handled;
* a GROUP turn is refused as a gateway responsibility, and the refusal never
  echoes the inbound content back into a shared thread;
* a contentless turn is refused as empty.

They use the in-process FastAPI app via TestClient — no network, no live
WhatsApp, no Stripe, no Telegram.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import webhook_server

_SECRET = "test-gateway-secret"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PORTFOLIO_INBOUND_SECRET", _SECRET)
    return TestClient(webhook_server.app)


def _direct_body(text: str = "58M chest pain, CBD reflection") -> dict:
    return {
        "channel": "whatsapp",
        "conversation_id": "wa:+440000000000",
        "gateway_user_id": "emgurus-user-123",
        "scope": "direct",
        "text": text,
    }


def test_direct_text_turn_is_handled(client: TestClient):
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["refusal"] is None


def test_group_turn_is_refused_and_does_not_echo_content(client: TestClient):
    secret_text = "patient John Doe MRN 12345 chest pain"
    resp = client.post(
        "/api/portfolio/inbound",
        json={
            "channel": "whatsapp",
            "conversation_id": "wa:120363@g.us",
            "scope": "group",
            "text": secret_text,
        },
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "refuse_group"
    assert data["refusal"] is not None
    assert data["refusal"]["body"]
    # The refusal must never replay private content into a shared thread.
    assert secret_text not in data["refusal"]["body"]
    assert secret_text not in (data["refusal"].get("continuation") or "")


def test_empty_direct_turn_is_refused_empty(client: TestClient):
    resp = client.post(
        "/api/portfolio/inbound",
        json={
            "channel": "whatsapp",
            "conversation_id": "wa:+440000000000",
            "scope": "direct",
        },
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "refuse_empty"


def test_media_only_direct_turn_is_handled(client: TestClient):
    resp = client.post(
        "/api/portfolio/inbound",
        json={
            "channel": "whatsapp",
            "conversation_id": "wa:+440000000000",
            "scope": "direct",
            "media": [{"kind": "voice", "uri": "gw://blob/abc"}],
        },
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "handle"


def test_missing_secret_is_unauthorized(client: TestClient):
    resp = client.post("/api/portfolio/inbound", json=_direct_body())
    assert resp.status_code == 401


def test_wrong_secret_is_unauthorized(client: TestClient):
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(),
        headers={"X-Gateway-Secret": "not-the-secret"},
    )
    assert resp.status_code == 401


def test_unconfigured_secret_returns_500(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_INBOUND_SECRET", raising=False)
    client = TestClient(webhook_server.app)
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 500


def test_handle_response_includes_fresh_start(client: TestClient):
    """fresh_start is always True until Portfolio Guru tracks server-side sessions."""
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data.get("fresh_start") is True


def test_invalid_scope_is_rejected(client: TestClient):
    resp = client.post(
        "/api/portfolio/inbound",
        json={
            "channel": "whatsapp",
            "conversation_id": "wa:+440000000000",
            "scope": "broadcast",
            "text": "hello",
        },
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 422
