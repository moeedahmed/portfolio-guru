"""
Stripe integration for Portfolio Guru.
Handles Checkout session creation and webhook processing.
"""
import stripe
import os
import logging
from usage import set_user_tier

logger = logging.getLogger(__name__)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# Price IDs — set via env vars (created in Stripe dashboard)
PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID")
PRO_PLUS_PRICE_ID = os.environ.get("STRIPE_PRO_PLUS_PRICE_ID")


async def create_checkout_session(telegram_user_id: int, tier: str) -> str:
    """Create a Stripe Checkout session and return the URL."""
    price_id = PRO_PRICE_ID if tier == "pro" else PRO_PLUS_PRICE_ID
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url="https://t.me/PortfolioGuruBot?start=upgraded",
        cancel_url="https://t.me/PortfolioGuruBot?start=cancelled",
        metadata={"telegram_user_id": str(telegram_user_id)},
    )
    return session.url


async def handle_webhook_event(payload: bytes, sig_header: str, webhook_secret: str) -> dict:
    """Process a Stripe webhook event. Returns action dict."""
    event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = int(session["metadata"]["telegram_user_id"])
        # Determine tier from price
        subscription = stripe.Subscription.retrieve(session["subscription"])
        price_id = subscription["items"]["data"][0]["price"]["id"]
        tier = "pro" if price_id == PRO_PRICE_ID else "pro_plus"
        await set_user_tier(user_id, tier,
                           stripe_customer_id=session.get("customer"),
                           stripe_subscription_id=session.get("subscription"))
        return {"action": "upgraded", "user_id": user_id, "tier": tier}

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]
        return {"action": "downgraded", "customer_id": customer_id}

    return {"action": "ignored"}
