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


class StripeBillingConfigError(RuntimeError):
    """Raised when billing is configured in a mode that can charge users incorrectly."""


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
        success_url=success_url or "https://t.me/portfolio_guru_bot?start=upgraded",
        cancel_url=cancel_url or "https://t.me/portfolio_guru_bot?start=cancelled",
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
        logger.info("Portfolio Guru funnel event=checkout_completed user_id=%s tier=%s", user_id, tier)
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

    if event_type in ("invoice.paid", "invoice.payment_succeeded"):
        # Reactivation path: a payment that recovered after a failure (Stripe
        # dunning) does not always emit subscription.updated -> active, so a
        # user we downgraded on payment_failed would otherwise stay locked out
        # despite paying. Re-sync their tier from the live subscription.
        invoice = obj
        subscription_id = _get(invoice, "subscription")
        customer_id = _get(invoice, "customer")
        user_id = None
        if customer_id:
            user_id = await get_user_by_stripe_customer(customer_id)
        if user_id is None and subscription_id:
            user_id = await get_user_by_stripe_subscription(subscription_id)
        if user_id is None:
            return {"action": "ignored", "customer_id": customer_id, "error": "user not found"}
        if not subscription_id:
            return {"action": "ignored", "user_id": user_id, "error": "invoice has no subscription"}
        subscription = stripe.Subscription.retrieve(subscription_id)
        result = await _apply_subscription_to_user(user_id, subscription)
        logger.info(
            "Portfolio Guru funnel event=invoice_paid user_id=%s action=%s",
            user_id, result.get("action"),
        )
        return result

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


async def _apply_subscription_to_user(user_id: int, subscription) -> dict:
    """Set a user's tier from a Stripe subscription object.

    Shared by the invoice.paid branch and reconciliation so they cannot drift.
    active/trialing -> the price's tier; past_due/unpaid/canceled -> free.
    """
    status = _get(subscription, "status")
    customer_id = _get(subscription, "customer")
    subscription_id = _get(subscription, "id")
    if status in ACTIVE_SUBSCRIPTION_STATUSES:
        price_id = _subscription_price_id(subscription)
        tier = _tier_from_price(price_id)
        if tier is None:
            return {"action": "error", "user_id": user_id, "error": "unknown subscription price"}
        await set_user_tier(user_id, tier, stripe_customer_id=customer_id, stripe_subscription_id=subscription_id)
        return {"action": "upgraded", "user_id": user_id, "tier": tier}
    if status in INACTIVE_SUBSCRIPTION_STATUSES:
        await set_user_tier(user_id, "free", stripe_customer_id=customer_id, stripe_subscription_id=subscription_id)
        return {"action": "downgraded", "user_id": user_id, "reason": f"subscription_{status}"}
    return {"action": "ignored", "user_id": user_id, "subscription_status": status}


async def reconcile_subscription(telegram_user_id: int) -> dict:
    """Authoritatively re-sync a user's tier from Stripe.

    The webhook is the fast path but a single dropped event would otherwise
    strand a paying user. Call this on the checkout success-redirect and from a
    daily job so entitlement is eventually correct even if a webhook is missed.
    """
    from usage import get_stripe_ids_for_user

    customer_id, subscription_id = await get_stripe_ids_for_user(telegram_user_id)
    if not subscription_id and not customer_id:
        return {"action": "no_subscription", "user_id": telegram_user_id}

    subscription = None
    try:
        if subscription_id:
            subscription = stripe.Subscription.retrieve(subscription_id)
        elif customer_id:
            subs = stripe.Subscription.list(customer=customer_id, status="all", limit=1)
            data = _get(subs, "data", []) or []
            subscription = data[0] if data else None
    except Exception as e:
        logger.warning("reconcile_subscription: Stripe lookup failed for %s: %s", telegram_user_id, e)
        return {"action": "error", "user_id": telegram_user_id, "error": str(e)}

    if subscription is None:
        await set_user_tier(telegram_user_id, "free")
        return {"action": "downgraded", "user_id": telegram_user_id, "reason": "no_active_subscription"}
    return await _apply_subscription_to_user(telegram_user_id, subscription)


async def reconcile_all_subscriptions() -> dict:
    """Reconcile every subscribed user against Stripe. For the daily safety job.

    Returns a small summary; per-user failures are logged and do not abort the
    sweep.
    """
    from usage import get_subscribed_user_ids

    summary = {"checked": 0, "changed": 0, "errors": 0}
    try:
        user_ids = await get_subscribed_user_ids()
    except Exception as e:
        logger.warning("reconcile_all_subscriptions: could not list users: %s", e)
        return summary
    for uid in user_ids:
        summary["checked"] += 1
        try:
            result = await reconcile_subscription(uid)
            if result.get("action") in ("upgraded", "downgraded"):
                summary["changed"] += 1
        except Exception as e:
            summary["errors"] += 1
            logger.warning("reconcile_all_subscriptions: %s failed: %s", uid, e)
    logger.info("Stripe reconcile sweep: %s", summary)
    return summary


def stripe_mode() -> str:
    """'live' | 'test' | 'unknown', inferred from the secret key prefix."""
    key = os.environ.get("STRIPE_SECRET_KEY", "") or ""
    if key.startswith(("sk_live", "rk_live")):
        return "live"
    if key.startswith(("sk_test", "rk_test")):
        return "test"
    return "unknown"


def stripe_billing_config_errors() -> list[str]:
    """Return startup-blocking Stripe config errors.

    Billing can be absent in local/non-billing runs, but once a real Stripe key
    is present the price ids and webhook secret must be coherent. Failing fast
    is safer than allowing a user to pay without receiving the upgraded tier.
    """
    mode = stripe_mode()
    if mode == "unknown":
        return []

    values = {
        "STRIPE_PRO_PLUS_PRICE_ID": PRO_PLUS_PRICE_ID,
        "STRIPE_WEBHOOK_SECRET": os.environ.get("STRIPE_WEBHOOK_SECRET"),
    }
    errors = [f"{name} is required when STRIPE_SECRET_KEY is {mode}" for name, val in values.items() if not val]

    if mode == "live":
        for name, value in (
            ("STRIPE_PRO_PRICE_ID", PRO_PRICE_ID),
            ("STRIPE_PRO_PLUS_PRICE_ID", PRO_PLUS_PRICE_ID),
        ):
            normalised = (value or "").lower()
            if "test" in normalised or "placeholder" in normalised:
                errors.append(f"{name} looks like a non-live price id while STRIPE_SECRET_KEY is live")

    return errors


def validate_stripe_billing_config() -> None:
    errors = stripe_billing_config_errors()
    if errors:
        raise StripeBillingConfigError("; ".join(errors))


def log_stripe_mode() -> None:
    """Log the Stripe mode and fail on charge-risk billing config at startup.

    A mismatched key/price/secret (e.g. live key + unset price) otherwise fails
    silently as a charged-but-unupgraded customer. Stop startup instead.
    """
    mode = stripe_mode()
    if mode == "unknown":
        logger.warning("Stripe: secret key missing or unrecognized prefix — billing is effectively disabled")
    else:
        logger.info("Stripe mode: %s", mode)
    validate_stripe_billing_config()
