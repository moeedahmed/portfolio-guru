"""
Public-facing FastAPI server for Portfolio Guru.

Reachable at https://stripe.solvorolabs.com via Cloudflare tunnel
(`stripe.solvorolabs.com -> localhost:8099` in ~/.cloudflared/config.yml).
Started under launchd alongside the bot.

Two responsibilities:

1. Receive Stripe webhook events at /webhook/stripe and dispatch to
   stripe_handler.handle_webhook_event, which updates the bot's SQLite
   user_profiles table and mirrors the tier change to Supabase.

2. Serve /api/create-checkout-session for the EM Gurus Hub web app, so
   authenticated hub users can upgrade to Unlimited without going via
   Telegram. The endpoint authenticates the caller using their Supabase
   JWT (sent as Bearer token), resolves their linked telegram_user_id
   from portfolio_users, and returns a Stripe Checkout URL.
"""
import os
import logging
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from stripe_handler import handle_webhook_event, create_checkout_session

logger = logging.getLogger(__name__)

app = FastAPI(title="Portfolio Guru Webhooks")

# Web origins allowed to call /api/create-checkout-session.
# emgurus.com hosts the portfolio module; localhost is for dev.
_CORS_ORIGINS = [
    "https://emgurus.com",
    "https://www.emgurus.com",
    "http://localhost:5173",
    "http://localhost:8080",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


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


# ---------------------------------------------------------------------------
# Web-app facing endpoints
# ---------------------------------------------------------------------------

class CheckoutRequest(BaseModel):
    tier: str = "pro_plus"


def _verify_supabase_token(authorization: str | None) -> str:
    """Return the emgurus_user_id for the bearer token, or raise 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(status_code=500, detail="Supabase not configured on the bot")
    # Use the JWT to call /auth/v1/user — succeeds iff token is valid.
    import httpx
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": SUPABASE_SERVICE_ROLE_KEY,
                },
            )
    except Exception as exc:
        logger.warning("Supabase auth lookup errored: %s", exc)
        raise HTTPException(status_code=502, detail="Auth service unreachable")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")
    user = resp.json()
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No user id on token")
    return user_id


def _resolve_telegram_user_id(emgurus_user_id: str) -> int:
    """Look up portfolio_users; raise 409 if the user hasn't linked yet."""
    from supabase_sync import _supabase
    sb = _supabase()
    if sb is None:
        raise HTTPException(status_code=500, detail="Supabase mirror not configured")
    try:
        resp = (
            sb.table("portfolio_users")
            .select("telegram_user_id")
            .eq("emgurus_user_id", emgurus_user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("portfolio_users lookup failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not look up linked account")
    if not resp.data or not resp.data[0].get("telegram_user_id"):
        raise HTTPException(
            status_code=409,
            detail="Link your Telegram bot before upgrading on the web.",
        )
    return int(resp.data[0]["telegram_user_id"])


@app.post("/api/create-checkout-session")
async def create_checkout(
    body: CheckoutRequest,
    authorization: str | None = Header(default=None),
):
    """Create a Stripe Checkout session for the authenticated hub user.

    Flow:
      1. Verify the Supabase JWT (from the hub session) and extract the
         emgurus_user_id.
      2. Resolve the linked telegram_user_id via portfolio_users. 409 if
         unlinked — the user must run /link in the bot first.
      3. Create a Stripe Checkout session via stripe_handler. The session's
         success URL routes back to the dashboard.
      4. Return the Stripe URL — the web app redirects there.
    """
    if body.tier not in ("pro", "pro_plus"):
        raise HTTPException(status_code=400, detail="Invalid tier")
    # Pro is legacy / not sold any more — funnel any stale request to Unlimited.
    tier = "pro_plus" if body.tier == "pro" else body.tier

    emgurus_user_id = _verify_supabase_token(authorization)
    telegram_user_id = _resolve_telegram_user_id(emgurus_user_id)

    try:
        url = await create_checkout_session(
            telegram_user_id,
            tier,
            success_url="https://emgurus.com/portfolio/dashboard?upgraded=1",
            cancel_url="https://emgurus.com/portfolio/dashboard?upgrade=cancelled",
        )
    except Exception as exc:
        logger.error("Stripe checkout creation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Stripe checkout creation failed")

    return {"url": url}


@app.get("/health")
async def health():
    """Tiny liveness check — useful when validating the tunnel."""
    return {"status": "ok"}


async def _send_telegram_message(chat_id: int, text: str):
    """Send a simple Telegram message via Bot API."""
    import httpx
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": chat_id, "text": text})
