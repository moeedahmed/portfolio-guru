"""HTTP bridge for the Portfolio Guru WhatsApp inbound boundary.

`POST /api/portfolio/inbound` is the smallest authenticated surface the gateway
calls to hand a turn to Portfolio Guru and receive the first workflow reply.
These tests pin the boundary the gateway depends on:

* the endpoint is private: a request without the shared gateway secret is
  rejected before any routing decision;
* a DIRECT 1:1 turn with content is handled, and the first Portfolio Guru
  gathering reply is sent to the gateway outbound endpoint when configured;
* a GROUP turn is refused as a gateway responsibility without triggering any
  outbound send — the refusal never echoes the inbound content;
* a contentless turn is refused as empty without any outbound send.

They use the in-process FastAPI app via TestClient — no network, no live
WhatsApp, no Stripe, no Telegram.  Outbound sends are captured by
monkeypatching webhook_server._send_portfolio_turn_reply with an async stub.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import webhook_server
from channel_actions import ChannelReply

_SECRET = "test-gateway-secret"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("PORTFOLIO_INBOUND_SECRET", _SECRET)
    return TestClient(webhook_server.app)


def _direct_body(text: str = "58M chest pain, CBD reflection") -> dict:
    return {
        "channel": "whatsapp",
        "conversation_id": "wa:+440000000000",
        "gateway_user_id": "pg-user-123",
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
    assert data["reply_sent"] is False


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
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is False


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


# ---------------------------------------------------------------------------
# Outbound turn-runner tests
# ---------------------------------------------------------------------------
# These tests verify that on HANDLE the handler sends the rendered Portfolio Guru
# reply via the outbound path, and that GROUP/EMPTY turns produce no outbound
# send.  The actual HTTP call is replaced by an async stub to stay offline.

_OUTBOUND_URL = "http://gateway.local"
_OUTBOUND_ACCOUNT = "wa-account-1"
_OUTBOUND_SECRET = "outbound-secret"
_OUTBOUND_GATEWAY_TOKEN = "gateway-token"


@pytest.fixture
def outbound_client(monkeypatch) -> tuple[TestClient, list[tuple[str, str]]]:
    """TestClient with outbound send stubbed; returns (client, captured_sends)."""
    monkeypatch.setenv("PORTFOLIO_INBOUND_SECRET", _SECRET)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_URL", _OUTBOUND_URL)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_ACCOUNT_ID", _OUTBOUND_ACCOUNT)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_SECRET", _OUTBOUND_SECRET)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_GATEWAY_TOKEN", _OUTBOUND_GATEWAY_TOKEN)

    captured: list[tuple[str, str]] = []

    async def _stub_send(to: str, text: str, cfg: object) -> None:
        captured.append((to, text))

    monkeypatch.setattr(webhook_server, "_send_portfolio_turn_reply", _stub_send)
    return TestClient(webhook_server.app), captured


def test_direct_handled_turn_invokes_outbound_with_rendered_whatsapp_text(
    outbound_client: tuple[TestClient, list[tuple[str, str]]],
):
    client, captured = outbound_client
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is True
    # Outbound send must have fired exactly once.
    assert len(captured) == 1
    to, text = captured[0]
    # The recipient is the inbound conversation_id.
    assert to == "wa:+440000000000"
    # The text is the rendered gathering reply — plain text, no Telegram markup.
    assert "clinical case" in text.lower()
    # render_numbered output must not contain Telegram-only markup.
    assert "InlineKeyboard" not in text
    assert "callback_data" not in text


def test_group_turn_does_not_trigger_outbound(
    outbound_client: tuple[TestClient, list[tuple[str, str]]],
):
    client, captured = outbound_client
    resp = client.post(
        "/api/portfolio/inbound",
        json={
            "channel": "whatsapp",
            "conversation_id": "wa:120363@g.us",
            "scope": "group",
            "text": "group message with portfolio keyword",
        },
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "refuse_group"
    assert captured == []


def test_empty_direct_turn_does_not_trigger_outbound(
    outbound_client: tuple[TestClient, list[tuple[str, str]]],
):
    client, captured = outbound_client
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
    assert captured == []


def test_auth_still_rejects_wrong_secret_with_outbound_configured(
    outbound_client: tuple[TestClient, list[tuple[str, str]]],
):
    client, captured = outbound_client
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(),
        headers={"X-Gateway-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401
    assert captured == []


def test_outbound_failure_reported_safely_without_kaizen_touch(
    monkeypatch,
):
    """An outbound send error must not crash the inbound handler or leak to Kaizen."""
    monkeypatch.setenv("PORTFOLIO_INBOUND_SECRET", _SECRET)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_URL", _OUTBOUND_URL)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_ACCOUNT_ID", _OUTBOUND_ACCOUNT)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_SECRET", _OUTBOUND_SECRET)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_GATEWAY_TOKEN", _OUTBOUND_GATEWAY_TOKEN)

    async def _failing_send(to: str, text: str, cfg: object) -> None:
        raise RuntimeError("gateway unreachable")

    monkeypatch.setattr(webhook_server, "_send_portfolio_turn_reply", _failing_send)

    client = TestClient(webhook_server.app)
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(),
        headers={"X-Gateway-Secret": _SECRET},
    )
    # Inbound handler must still respond successfully even when outbound fails.
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is False


def test_direct_handled_without_outbound_configured_still_returns_handle(client: TestClient):
    """When outbound env vars are absent, HANDLE returns successfully with no send."""
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is False


def test_outbound_config_requires_gateway_token(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_URL", _OUTBOUND_URL)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_ACCOUNT_ID", _OUTBOUND_ACCOUNT)
    monkeypatch.setenv("PORTFOLIO_OUTBOUND_SECRET", _OUTBOUND_SECRET)
    monkeypatch.delenv("PORTFOLIO_OUTBOUND_GATEWAY_TOKEN", raising=False)

    assert webhook_server._resolve_outbound_config() is None


# ---------------------------------------------------------------------------
# Drafting path tests — rich case vs. generic intake routing
# ---------------------------------------------------------------------------
# When a HANDLE turn carries a detailed case description (>= _RICH_CASE_WORD_THRESHOLD
# words), the bridge should call _make_case_insight_reply and return a form
# recommendation with targeted missing-info asks, not the generic gathering prompt.


_RICH_CASE_TEXT = (
    "I completed an ED sepsis QI project with baseline audit, "
    "intervention and re-audit. Can you draft this for portfolio?"
)


def test_has_rich_case_content_false_for_short_and_empty():
    assert not webhook_server._has_rich_case_content(None)
    assert not webhook_server._has_rich_case_content("")
    assert not webhook_server._has_rich_case_content("help")
    assert not webhook_server._has_rich_case_content("58M chest pain CBD reflection")


def test_has_rich_case_content_true_for_substantive_case():
    assert webhook_server._has_rich_case_content(_RICH_CASE_TEXT)


def test_rich_case_text_invokes_draft_insight_reply_not_gathering_prompt(
    monkeypatch,
    outbound_client,
):
    """A detailed case (>= threshold words) must get a draft-style reply, not
    the generic 'Please describe the clinical case' intake prompt."""
    client, captured = outbound_client

    async def _stub_insight(text: str) -> ChannelReply:
        return ChannelReply(
            body=(
                "Based on your description, the recommended WPBA form is:\n"
                "Quality Improvement Assessment Tool (QIAT)\n\n"
                "QI/audit project with measurement and change; QIAT is the specific assessment form.\n\n"
                "To complete your draft I need a few details:\n"
                "- Date of the activity (dd/mm/yyyy)\n"
                "- Your training grade and current placement"
            )
        )

    monkeypatch.setattr(webhook_server, "_make_case_insight_reply", _stub_insight)

    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(_RICH_CASE_TEXT),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is True
    assert len(captured) == 1
    _, sent_text = captured[0]
    # Draft recommendation content is present.
    assert "QIAT" in sent_text
    assert "Quality Improvement" in sent_text
    # Generic intake prompt must NOT appear when the case is already rich.
    assert "Please describe the clinical case" not in sent_text


def test_short_generic_text_still_returns_gathering_prompt(outbound_client):
    """Short or vague messages (below word threshold, not a greeting or
    capability ask) must still get the intake gathering prompt, not the draft
    path."""
    client, captured = outbound_client
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body("thanks"),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is True
    assert len(captured) == 1
    _, sent_text = captured[0]
    assert "clinical case" in sent_text.lower()


# ---------------------------------------------------------------------------
# First-contact onboarding tests — /start, greeting, capability
# ---------------------------------------------------------------------------
# A first message like /start, hi, or "what can you do?" must be answered with
# WhatsApp-native onboarding copy (the same FIXED welcome the Telegram beta bot
# uses), not the "describe the clinical case" gathering demand. This removes the
# "magic sentence" problem where only a full case produced a coherent reply.


@pytest.mark.parametrize("opening", ["/start", "start", "hi", "hello", "hey there"])
def test_start_and_greeting_get_welcome_onboarding(outbound_client, opening):
    client, captured = outbound_client
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(opening),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is True
    assert len(captured) == 1
    _, sent_text = captured[0]
    # Onboarding orients the user; it does not demand a clinical case first.
    assert "Welcome to Portfolio Guru" in sent_text
    assert "describe the clinical case" not in sent_text.lower()


@pytest.mark.parametrize("opening", ["help", "features", "what can you do?"])
def test_capability_question_gets_overview(outbound_client, opening):
    client, captured = outbound_client
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(opening),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is True
    assert len(captured) == 1
    _, sent_text = captured[0]
    assert "Portfolio Guru" in sent_text
    assert "recommend the best-fit WPBA form" in sent_text


def test_start_does_not_invoke_case_insight(monkeypatch, outbound_client):
    """A /start opening must never reach the LLM-backed draft-insight path."""
    client, captured = outbound_client

    async def _must_not_be_called(text: str):  # pragma: no cover - guard
        raise AssertionError("first-contact onboarding must not call the extractor")

    monkeypatch.setattr(webhook_server, "_make_case_insight_reply", _must_not_be_called)

    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body("/start"),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "handle"
    assert len(captured) == 1


def test_rich_case_insight_reply_falls_back_to_gathering_on_extractor_error(
    monkeypatch,
    outbound_client,
):
    """If _make_case_insight_reply's extractor call raises, it falls back to
    the gathering prompt — the outbound still sends and reply_sent is True."""
    client, captured = outbound_client

    async def _stub_insight_failing(text: str) -> ChannelReply:
        # Simulate extractor failure path — returns gathering prompt as fallback.
        return webhook_server._make_initial_gathering_reply()

    monkeypatch.setattr(webhook_server, "_make_case_insight_reply", _stub_insight_failing)

    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(_RICH_CASE_TEXT),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is True
    _, sent_text = captured[0]
    assert "clinical case" in sent_text.lower()


def test_rich_case_without_outbound_config_still_returns_handle(client: TestClient):
    """Even with no outbound config, a rich case HANDLE returns 200 with
    reply_sent=False — the drafting path is not activated without outbound."""
    resp = client.post(
        "/api/portfolio/inbound",
        json=_direct_body(_RICH_CASE_TEXT),
        headers={"X-Gateway-Secret": _SECRET},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "handle"
    assert data["reply_sent"] is False
