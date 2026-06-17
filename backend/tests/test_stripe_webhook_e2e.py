"""Deterministic local proof for the Stripe checkout -> webhook -> tier flip path.

These tests exercise the full FastAPI surface that production uses, with
mocked Stripe SDK boundaries:

* `POST /webhook/stripe` runs `handle_webhook_event`, which writes to the
  bot's SQLite `user_profiles` table via `set_user_tier`. We assert the
  tier actually flips.
* `POST /api/create-checkout-session` is the route the hub calls. It
  authenticates a Supabase JWT, resolves the linked telegram_user_id from
  Supabase, and returns a Stripe Checkout URL. We mock both Supabase and
  Stripe and assert the URL is returned and the right tier is requested.

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


def test_create_checkout_session_returns_url(client, monkeypatch):
    """End-to-end: valid JWT + linked user -> Stripe URL returned to caller."""
    import asyncio

    # Make the linked user known in the bot's SQLite store so set_user_tier
    # can find the customer later (not strictly required for this test, but
    # mirrors the production flow).
    asyncio.run(usage.set_user_tier(42, "free"))

    # Mock Supabase auth: any token resolves to a fixed emgurus_user_id.
    class _FakeAuthResp:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "emgurus-uuid-42"}

    class _FakeHttpx:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **kw): return _FakeAuthResp()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", _FakeHttpx)

    # Mock the Supabase mirror's portfolio_users lookup.
    class _FakeQuery:
        def __init__(self): self.data = [{"telegram_user_id": 42}]
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def execute(self): return self

    class _FakeSb:
        def table(self, _name): return _FakeQuery()

    import supabase_sync
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: _FakeSb())

    captured = {}

    class _FakeSession:
        url = "https://stripe.test/checkout/cs_test_abc"

    def _create(**kwargs):
        captured.update(kwargs)
        return _FakeSession()

    monkeypatch.setattr(stripe_handler.stripe.checkout.Session, "create", _create)

    resp = client.post(
        "/api/create-checkout-session",
        headers={"Authorization": "Bearer jwt.test.token"},
        json={"tier": "pro_plus"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"url": "https://stripe.test/checkout/cs_test_abc"}
    # The Unlimited price ID is what was offered to Stripe.
    assert captured["line_items"][0]["price"] == "price_unlimited_test"
    assert captured["metadata"] == {"telegram_user_id": "42"}
    # The success URL is the hub's dashboard, not the bot.
    assert "emgurus.com/portfolio/dashboard" in captured["success_url"]


def test_create_checkout_session_blocks_when_unlinked(client, monkeypatch):
    """Unlinked emgurus users get a 409 with the link-first message."""
    class _FakeAuthResp:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "emgurus-uuid-unlinked"}

    class _FakeHttpx:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **kw): return _FakeAuthResp()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", _FakeHttpx)

    class _FakeQuery:
        def __init__(self): self.data = []
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def limit(self, *a, **kw): return self
        def execute(self): return self

    class _FakeSb:
        def table(self, _name): return _FakeQuery()

    import supabase_sync
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: _FakeSb())

    resp = client.post(
        "/api/create-checkout-session",
        headers={"Authorization": "Bearer jwt.test.token"},
        json={"tier": "pro_plus"},
    )

    assert resp.status_code == 409
    assert "Link your Telegram" in resp.json()["detail"]
