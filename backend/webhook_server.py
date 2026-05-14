"""
Minimal webhook server for Stripe events.
NOT started automatically — will be wired up with Cloudflare tunnel later.

Usage:
    uvicorn webhook_server:app --port 8099
"""
import os
import logging
import asyncio
from fastapi import FastAPI, Request, HTTPException
from stripe_handler import handle_webhook_event

logger = logging.getLogger(__name__)

app = FastAPI(title="Portfolio Guru Webhooks")

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Handle incoming Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    try:
        result = await handle_webhook_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logger.error("Webhook processing failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

    # On upgrade, notify user via Telegram
    if result.get("action") == "upgraded" and TELEGRAM_BOT_TOKEN:
        user_id = result["user_id"]
        tier_label = "Pro" if result["tier"] == "pro" else "Portfolio Guru Unlimited"
        text = f"🎉 Welcome to {tier_label}! Your upgrade is active.\n\nSend a case to get started."
        await _send_telegram_message(user_id, text)

    return {"status": "ok", "action": result.get("action")}


async def _send_telegram_message(chat_id: int, text: str):
    """Send a simple Telegram message via Bot API."""
    import httpx
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "text": text})
