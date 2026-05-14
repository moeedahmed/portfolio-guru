import importlib

import pytest


@pytest.fixture()
def stripe_modules(monkeypatch, tmp_path):
    import usage
    import stripe_handler

    db_path = tmp_path / "usage.db"
    monkeypatch.setattr(usage, "DB_PATH", str(db_path))
    monkeypatch.setattr(stripe_handler, "PRO_PRICE_ID", "price_pro")
    monkeypatch.setattr(stripe_handler, "PRO_PLUS_PRICE_ID", "price_unlimited")
    return usage, stripe_handler


def _event(event_type, obj, event_id="evt_test"):
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


def _subscription(subscription_id="sub_123", customer="cus_123", price="price_unlimited", status="active"):
    return {
        "id": subscription_id,
        "customer": customer,
        "status": status,
        "items": {"data": [{"price": {"id": price}}]},
    }


@pytest.mark.asyncio
async def test_checkout_completed_upgrades_to_unlimited_and_stores_stripe_ids(stripe_modules, monkeypatch):
    usage, stripe_handler = stripe_modules
    monkeypatch.setattr(
        stripe_handler.stripe.Subscription,
        "retrieve",
        lambda subscription_id: _subscription(subscription_id=subscription_id),
    )

    result = await stripe_handler._handle_constructed_event(
        _event(
            "checkout.session.completed",
            {
                "metadata": {"telegram_user_id": "12345"},
                "customer": "cus_123",
                "subscription": "sub_123",
            },
        ),
        "checkout.session.completed",
    )

    assert result == {"action": "upgraded", "user_id": 12345, "tier": "pro_plus"}
    assert await usage.get_user_tier(12345) == "pro_plus"
    assert await usage.get_user_by_stripe_customer("cus_123") == 12345
    assert await usage.get_user_by_stripe_subscription("sub_123") == 12345


@pytest.mark.asyncio
async def test_subscription_deleted_downgrades_existing_customer(stripe_modules):
    usage, stripe_handler = stripe_modules
    await usage.set_user_tier(12345, "pro_plus", "cus_123", "sub_123")

    result = await stripe_handler._handle_constructed_event(
        _event("customer.subscription.deleted", _subscription()),
        "customer.subscription.deleted",
    )

    assert result == {"action": "downgraded", "user_id": 12345}
    assert await usage.get_user_tier(12345) == "free"


@pytest.mark.asyncio
async def test_subscription_deleted_unknown_customer_is_ignored(stripe_modules):
    _usage, stripe_handler = stripe_modules

    result = await stripe_handler._handle_constructed_event(
        _event("customer.subscription.deleted", _subscription(customer="cus_missing")),
        "customer.subscription.deleted",
    )

    assert result["action"] == "ignored"
    assert result["error"] == "user not found"


@pytest.mark.asyncio
async def test_past_due_subscription_update_downgrades_user(stripe_modules):
    usage, stripe_handler = stripe_modules
    await usage.set_user_tier(12345, "pro_plus", "cus_123", "sub_123")

    result = await stripe_handler._handle_constructed_event(
        _event("customer.subscription.updated", _subscription(status="past_due")),
        "customer.subscription.updated",
    )

    assert result["action"] == "downgraded"
    assert result["reason"] == "subscription_past_due"
    assert await usage.get_user_tier(12345) == "free"


@pytest.mark.asyncio
async def test_invoice_payment_failed_downgrades_user(stripe_modules):
    usage, stripe_handler = stripe_modules
    await usage.set_user_tier(12345, "pro_plus", "cus_123", "sub_123")

    result = await stripe_handler._handle_constructed_event(
        _event("invoice.payment_failed", {"customer": "cus_123", "subscription": "sub_123"}),
        "invoice.payment_failed",
    )

    assert result == {"action": "downgraded", "user_id": 12345, "reason": "invoice_payment_failed"}
    assert await usage.get_user_tier(12345) == "free"


@pytest.mark.asyncio
async def test_duplicate_webhook_event_is_harmless(stripe_modules, monkeypatch):
    usage, stripe_handler = stripe_modules
    event = _event("invoice.payment_failed", {"customer": "cus_123", "subscription": "sub_123"}, event_id="evt_dup")
    await usage.set_user_tier(12345, "pro_plus", "cus_123", "sub_123")
    monkeypatch.setattr(stripe_handler.stripe.Webhook, "construct_event", lambda payload, sig, secret: event)

    first = await stripe_handler.handle_webhook_event(b"{}", "sig", "secret")
    second = await stripe_handler.handle_webhook_event(b"{}", "sig", "secret")

    assert first["action"] == "downgraded"
    assert second == {"action": "duplicate", "event_id": "evt_dup", "event_type": "invoice.payment_failed"}
    assert await usage.get_user_tier(12345) == "free"
