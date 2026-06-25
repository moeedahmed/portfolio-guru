"""Billing-correctness additions: invoice.paid reactivation, reconciliation, mode.

These guard the gaps the audit flagged: a recovered payment must re-upgrade the
user, and a missed webhook must be self-healing via reconciliation.
"""
import json

import pytest

import stripe_handler


class _Sub:
    """Minimal stand-in for a Stripe Subscription object."""
    def __init__(self, status, price_id, customer="cus_1", sid="sub_1"):
        self.status = status
        self.customer = customer
        self.id = sid
        self.items = {"data": [{"price": {"id": price_id}}]}


@pytest.fixture(autouse=True)
def _prices(monkeypatch):
    monkeypatch.setattr(stripe_handler, "PRO_PLUS_PRICE_ID", "price_plus")
    monkeypatch.setattr(stripe_handler, "PRO_PRICE_ID", "price_pro")


async def test_invoice_paid_reactivates_user(monkeypatch):
    set_calls = []
    monkeypatch.setattr(stripe_handler, "get_user_by_stripe_customer", _async(42))
    monkeypatch.setattr(stripe_handler, "set_user_tier", _record(set_calls))
    monkeypatch.setattr(stripe_handler.stripe.Subscription, "retrieve",
                        lambda sid: _Sub("active", "price_plus", sid=sid))

    event = {"type": "invoice.paid",
             "data": {"object": {"subscription": "sub_9", "customer": "cus_9"}}}
    result = await stripe_handler._handle_constructed_event(event, "invoice.paid")

    assert result == {"action": "upgraded", "user_id": 42, "tier": "pro_plus"}
    assert set_calls and set_calls[0][0] == (42, "pro_plus")


async def test_reconcile_active_subscription_sets_tier(monkeypatch):
    set_calls = []
    monkeypatch.setattr(stripe_handler, "set_user_tier", _record(set_calls))
    monkeypatch.setitem(__import__("sys").modules, "usage", _FakeUsage(("cus_1", "sub_1")))
    monkeypatch.setattr(stripe_handler.stripe.Subscription, "retrieve",
                        lambda sid: _Sub("active", "price_plus"))

    result = await stripe_handler.reconcile_subscription(7)
    assert result == {"action": "upgraded", "user_id": 7, "tier": "pro_plus"}


async def test_reconcile_canceled_downgrades(monkeypatch):
    set_calls = []
    monkeypatch.setattr(stripe_handler, "set_user_tier", _record(set_calls))
    monkeypatch.setitem(__import__("sys").modules, "usage", _FakeUsage(("cus_1", "sub_1")))
    monkeypatch.setattr(stripe_handler.stripe.Subscription, "retrieve",
                        lambda sid: _Sub("canceled", "price_plus"))

    result = await stripe_handler.reconcile_subscription(7)
    assert result["action"] == "downgraded"
    assert set_calls[0][0][1] == "free"


async def test_reconcile_no_subscription_is_noop(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "usage", _FakeUsage((None, None)))
    result = await stripe_handler.reconcile_subscription(7)
    assert result["action"] == "no_subscription"


def test_stripe_mode_from_key(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_abc")
    assert stripe_handler.stripe_mode() == "live"
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    assert stripe_handler.stripe_mode() == "test"
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert stripe_handler.stripe_mode() == "unknown"


# --- helpers ---------------------------------------------------------------

def _async(value):
    async def _f(*a, **k):
        return value
    return _f


def _record(sink):
    async def _f(*args, **kwargs):
        sink.append((args, kwargs))
    return _f


class _FakeUsage:
    """Replaces the `usage` module for reconcile's local import."""
    def __init__(self, ids):
        self._ids = ids

    async def get_stripe_ids_for_user(self, _uid):
        return self._ids
