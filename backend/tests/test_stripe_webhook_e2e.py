"""Deterministic local proof for the Stripe checkout -> webhook -> tier flip path.

These tests exercise the full FastAPI surface that production uses, with
mocked Stripe SDK boundaries:

* `POST /webhook/stripe` runs `handle_webhook_event`, which writes to the
  bot's SQLite `user_profiles` table via `set_user_tier`. We assert the
  tier actually flips.
* `POST /api/create-checkout-session` rejects authenticated Hub users with
  410: web checkout is retired. It must not query the mirror or call Stripe.

The point of these tests is to give a green CI signal for the path
without needing live Stripe credentials or a public tunnel. Live Stripe
verification is documented in `docs/STRIPE_LOCAL_PROOF.md`.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import stripe_handler
import usage
import webhook_server


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / "usage.db"
    monkeypatch.setattr(usage, "DB_PATH", str(db_path))
    monkeypatch.setattr(stripe_handler, "PRO_PRICE_ID", "price_pro_test")
    monkeypatch.setattr(stripe_handler, "PRO_PLUS_PRICE_ID", "price_unlimited_test")
    return db_path


@pytest.fixture
def client(monkeypatch, isolated_db) -> TestClient:
    monkeypatch.setattr(webhook_server, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(webhook_server, "TELEGRAM_BOT_TOKEN", "")  # skip Telegram notify
    monkeypatch.setattr(webhook_server, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(webhook_server, "SUPABASE_SERVICE_ROLE_KEY", "sb-service-role-test")
    return TestClient(webhook_server.app)


def _subscription(price="price_unlimited_test", status="active"):
    return {
        "id": "sub_test",
        "customer": "cus_test",
        "status": status,
        "items": {"data": [{"price": {"id": price}}]},
    }


def test_checkout_completed_webhook_flips_tier_via_http(client, monkeypatch):
    """End-to-end through FastAPI: POST /webhook/stripe upgrades the tier."""
    event = {
        "id": "evt_checkout_completed",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"telegram_user_id": "42"},
                "customer": "cus_test",
                "subscription": "sub_test",
            }
        },
    }
    monkeypatch.setattr(
        stripe_handler.stripe.Webhook,
        "construct_event",
        lambda payload, sig, secret: event,
    )
    monkeypatch.setattr(
        stripe_handler.stripe.Subscription,
        "retrieve",
        lambda subscription_id: _subscription(),
    )

    resp = client.post(
        "/webhook/stripe",
        headers={"stripe-signature": "test-sig"},
        content=b"{}",
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "action": "upgraded"}

    import asyncio
    tier = asyncio.run(usage.get_user_tier(42))
    assert tier == "pro_plus"


def test_upgrade_welcome_sent_once_not_on_renewal(client, monkeypatch):
    """Checkout then a renewal invoice: the welcome message goes out once."""
    sent = []

    async def _fake_send(user_id, text):
        sent.append((user_id, text))

    monkeypatch.setattr(webhook_server, "TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setattr(webhook_server, "_send_telegram_message", _fake_send)
    monkeypatch.setattr(
        stripe_handler.stripe.Subscription, "retrieve", lambda subscription_id: _subscription(),
    )
    monkeypatch.setattr(stripe_handler, "get_user_by_stripe_customer", _async_return(42))
    events = [
        {"id": "evt_checkout", "type": "checkout.session.completed",
         "data": {"object": {"metadata": {"telegram_user_id": "42"},
                             "customer": "cus_test", "subscription": "sub_test"}}},
        {"id": "evt_first_invoice", "type": "invoice.paid",
         "data": {"object": {"subscription": "sub_test", "customer": "cus_test"}}},
        {"id": "evt_renewal_invoice", "type": "invoice.paid",
         "data": {"object": {"subscription": "sub_test", "customer": "cus_test"}}},
    ]
    for event in events:
        monkeypatch.setattr(
            stripe_handler.stripe.Webhook, "construct_event",
            lambda payload, sig, secret, _event=event: _event,
        )
        resp = client.post("/webhook/stripe", headers={"stripe-signature": "test-sig"}, content=b"{}")
        assert resp.json() == {"status": "ok", "action": "upgraded"}

    assert [user_id for user_id, _ in sent] == [42]


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


def test_invoice_payment_failed_webhook_downgrades_tier_via_http(client, monkeypatch):
    """End-to-end through FastAPI: failed invoice downgrades the user."""
    import asyncio
    asyncio.run(usage.set_user_tier(42, "pro_plus", "cus_test", "sub_test"))

    event = {
        "id": "evt_payment_failed",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "customer": "cus_test",
                "subscription": "sub_test",
            }
        },
    }
    monkeypatch.setattr(
        stripe_handler.stripe.Webhook,
        "construct_event",
        lambda payload, sig, secret: event,
    )

    resp = client.post(
        "/webhook/stripe",
        headers={"stripe-signature": "test-sig"},
        content=b"{}",
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "action": "downgraded"}
    assert asyncio.run(usage.get_user_tier(42)) == "free"


def test_webhook_missing_signature_secret_returns_500(client, monkeypatch):
    """If the webhook secret is misconfigured at runtime, the endpoint refuses."""
    monkeypatch.setattr(webhook_server, "STRIPE_WEBHOOK_SECRET", "")
    resp = client.post(
        "/webhook/stripe",
        headers={"stripe-signature": "test-sig"},
        content=b"{}",
    )
    assert resp.status_code == 500


def test_create_checkout_session_requires_bearer(client):
    resp = client.post("/api/create-checkout-session", json={"tier": "pro_plus"})
    assert resp.status_code == 401


@pytest.mark.parametrize("hub_user_id", ["previously-linked", "never-linked"])
def test_create_checkout_session_is_retired(client, monkeypatch, hub_user_id):
    from unittest.mock import AsyncMock, Mock
    import supabase_sync
    monkeypatch.setattr(webhook_server, "_verify_supabase_token", lambda _: hub_user_id)
    mirror = Mock(side_effect=AssertionError("Must not query Supabase"))
    checkout = AsyncMock(side_effect=AssertionError("Must not create checkout"))
    monkeypatch.setattr(supabase_sync, "_supabase", mirror)
    monkeypatch.setattr(webhook_server, "create_checkout_session", checkout)
    resp = client.post(
        "/api/create-checkout-session",
        headers={"Authorization": "Bearer fake-token"},
        json={"tier": "pro_plus"},
    )
    assert resp.status_code == 410
    assert resp.json()["detail"] == "Web checkout has been retired; upgrade in the Telegram bot."
    mirror.assert_not_called()
    checkout.assert_not_called()
