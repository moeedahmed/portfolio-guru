"""
Stripe integration for Portfolio Guru.
Handles Checkout session creation and webhook processing.
"""
import stripe
import os
import logging
from usage import (
    get_user_by_stripe_customer,
    get_user_by_stripe_subscription,
    has_processed_stripe_event,
    mark_stripe_event_processed,
    set_user_tier,
)

logger = logging.getLogger(__name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# Price IDs — set via env vars (created in Stripe dashboard)
PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID")
PRO_PLUS_PRICE_ID = os.environ.get("STRIPE_PRO_PLUS_PRICE_ID")

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}
INACTIVE_SUBSCRIPTION_STATUSES = {"past_due", "unpaid", "canceled", "incomplete_expired"}


def _get(obj, key, default=None):
    """Read a key from Stripe objects or plain mocked dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _subscription_price_id(subscription) -> str | None:
    items = _get(subscription, "items", {})
    data = _get(items, "data", []) or []
    if not data:
        return None
    price = _get(data[0], "price", {})
    return _get(price, "id")


def _tier_from_price(price_id: str | None) -> str | None:
    if price_id == PRO_PRICE_ID:
        return "pro"
    if price_id == PRO_PLUS_PRICE_ID:
        return "pro_plus"
    return None


async def _find_user_for_subscription(subscription) -> int | None:
    customer_id = _get(subscription, "customer")
    subscription_id = _get(subscription, "id")
    user_id = None
    if customer_id:
        user_id = await get_user_by_stripe_customer(customer_id)
    if user_id is None and subscription_id:
        user_id = await get_user_by_stripe_subscription(subscription_id)
    return user_id


async def create_checkout_session(
    telegram_user_id: int,
    tier: str,
    *,
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> str:
    """Create a Stripe Checkout session and return the URL.

    Defaults for success_url / cancel_url route back to the Telegram bot
    (used when the upgrade flow starts inside the bot). Web callers pass
    their own URLs so the user lands back on the hub dashboard.
    """
    price_id = PRO_PRICE_ID if tier == "pro" else PRO_PLUS_PRICE_ID
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url or "https://t.me/PortfolioGuruBot?start=upgraded",
        cancel_url=cancel_url or "https://t.me/PortfolioGuruBot?start=cancelled",
        metadata={"telegram_user_id": str(telegram_user_id)},
    )
    return session.url


async def handle_webhook_event(payload: bytes, sig_header: str, webhook_secret: str) -> dict:
    """Process a Stripe webhook event. Returns action dict."""
    event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    event_id = _get(event, "id")
    event_type = _get(event, "type")

    if event_id and await has_processed_stripe_event(event_id):
        return {"action": "duplicate", "event_id": event_id, "event_type": event_type}

    result = await _handle_constructed_event(event, event_type)
    if event_id:
        await mark_stripe_event_processed(event_id, event_type or "unknown")
    return result


async def _handle_constructed_event(event, event_type: str | None) -> dict:
    data = _get(event, "data", {})
    obj = _get(data, "object", {})

    if event_type == "checkout.session.completed":
        session = obj
        metadata = _get(session, "metadata", {}) or {}
        telegram_user_id = _get(metadata, "telegram_user_id")
        if not telegram_user_id:
            return {"action": "error", "error": "missing telegram_user_id metadata"}
        user_id = int(telegram_user_id)
        # Determine tier from price
        subscription_id = _get(session, "subscription")
        subscription = stripe.Subscription.retrieve(subscription_id)
        status = _get(subscription, "status")
        if status and status not in ACTIVE_SUBSCRIPTION_STATUSES:
            await set_user_tier(user_id, "free",
                                stripe_customer_id=_get(session, "customer"),
                                stripe_subscription_id=subscription_id)
            return {"action": "downgraded", "user_id": user_id, "reason": f"subscription_{status}"}
        price_id = _subscription_price_id(subscription)
        tier = _tier_from_price(price_id)
        if tier is None:
            return {"action": "error", "user_id": user_id, "error": "unknown subscription price"}
        await set_user_tier(user_id, tier,
                           stripe_customer_id=_get(session, "customer"),
                           stripe_subscription_id=subscription_id)
        return {"action": "upgraded", "user_id": user_id, "tier": tier}

    if event_type == "customer.subscription.deleted":
        subscription = obj
        customer_id = _get(subscription, "customer")
        user_id = await _find_user_for_subscription(subscription)
        if user_id is not None:
            await set_user_tier(user_id, "free")
            return {"action": "downgraded", "user_id": user_id}
        return {"action": "ignored", "customer_id": customer_id, "error": "user not found"}

    if event_type == "customer.subscription.updated":
        subscription = obj
        status = _get(subscription, "status")
        customer_id = _get(subscription, "customer")
        user_id = await _find_user_for_subscription(subscription)
        if user_id is None:
            return {"action": "ignored", "customer_id": customer_id, "error": "user not found"}
        if status in INACTIVE_SUBSCRIPTION_STATUSES:
            await set_user_tier(user_id, "free")
            return {"action": "downgraded", "user_id": user_id, "reason": f"subscription_{status}"}
        if status in ACTIVE_SUBSCRIPTION_STATUSES:
            price_id = _subscription_price_id(subscription)
            tier = _tier_from_price(price_id)
            if tier:
                await set_user_tier(user_id, tier,
                                    stripe_customer_id=customer_id,
                                    stripe_subscription_id=_get(subscription, "id"))
                return {"action": "updated", "user_id": user_id, "tier": tier}
        return {"action": "ignored", "user_id": user_id, "subscription_status": status}

    if event_type == "invoice.payment_failed":
        invoice = obj
        customer_id = _get(invoice, "customer")
        subscription_id = _get(invoice, "subscription")
        user_id = None
        if customer_id:
            user_id = await get_user_by_stripe_customer(customer_id)
        if user_id is None and subscription_id:
            user_id = await get_user_by_stripe_subscription(subscription_id)
        if user_id is not None:
            await set_user_tier(user_id, "free")
            return {"action": "downgraded", "user_id": user_id, "reason": "invoice_payment_failed"}
        return {"action": "ignored", "customer_id": customer_id, "error": "user not found"}

    return {"action": "ignored"}
