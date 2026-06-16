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
import hmac
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


# ---------------------------------------------------------------------------
# EMGurus WhatsApp Gateway inbound bridge
# ---------------------------------------------------------------------------
# Portfolio Guru sits behind the single EMGurus WhatsApp Gateway. The gateway
# owns the WhatsApp number and DM-vs-group routing; it calls this endpoint to
# learn whether Portfolio Guru will take a turn (DIRECT) or refuse it (GROUP /
# empty). This is a thin, side-effect-free wrapper around
# channel_contract.accept_inbound — it starts no workflow, touches no
# credential, and never echoes inbound content back into a shared thread.
#
# The bridge is private: callers must present the shared secret in
# PORTFOLIO_INBOUND_SECRET via the X-Gateway-Secret header. The gateway holds
# the matching value; this server only ever reads it from the environment.

from channel_actions import ChannelReply, render_numbered
from channel_contract import (
    Channel,
    ConversationScope,
    InboundDisposition,
    InboundMessage,
    MediaRef,
    SessionRef,
    accept_inbound,
)


class InboundMediaModel(BaseModel):
    kind: str
    uri: str | None = None
    mime_type: str | None = None
    caption: str | None = None


class InboundRequest(BaseModel):
    """Channel-neutral inbound envelope handed in by the gateway."""

    channel: str
    conversation_id: str
    gateway_user_id: str | None = None
    scope: str
    text: str | None = None
    media: list[InboundMediaModel] = []
    private: bool = True


def _verify_gateway_secret(provided: str | None) -> None:
    """Authenticate a gateway-to-Portfolio request, or raise 401/500."""
    expected = os.environ.get("PORTFOLIO_INBOUND_SECRET", "")
    if not expected:
        raise HTTPException(status_code=500, detail="Inbound bridge not configured")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid gateway secret")


def _build_inbound_message(body: InboundRequest) -> InboundMessage:
    """Map the validated request onto the channel-neutral contract types.

    Unknown channel/scope values are rejected as 422 rather than crashing —
    the boundary receives untrusted gateway input.
    """
    try:
        channel = Channel(body.channel)
        scope = ConversationScope(body.scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    media = tuple(
        MediaRef(kind=m.kind, uri=m.uri, mime_type=m.mime_type, caption=m.caption)
        for m in body.media
    )
    return InboundMessage(
        session=SessionRef(
            channel=channel,
            conversation_id=body.conversation_id,
            gateway_user_id=body.gateway_user_id,
        ),
        scope=scope,
        text=body.text,
        media=media,
        private=body.private,
    )


# ---------------------------------------------------------------------------
# Outbound reply helpers
# ---------------------------------------------------------------------------
# When Portfolio Guru handles a DIRECT turn, it replies back to the user via
# the gateway's WhatsApp outbound send endpoint.  The three env vars below
# configure that path; the feature is inert when any of them is absent so
# generic / non-WhatsApp installs are unaffected.
#
#   PORTFOLIO_OUTBOUND_URL          — base URL of the OpenClaw gateway
#   PORTFOLIO_OUTBOUND_ACCOUNT_ID   — WhatsApp account id to route through
#   PORTFOLIO_OUTBOUND_SECRET       — shared secret sent as X-Portfolio-Secret


class _OutboundConfig:
    """Resolved configuration for the Portfolio Guru outbound send path."""

    def __init__(self, *, url: str, account_id: str, secret: str) -> None:
        self.url = url
        self.account_id = account_id
        self.secret = secret


def _resolve_outbound_config() -> "_OutboundConfig | None":
    """Read the outbound config from the environment; return None if incomplete."""
    url = os.environ.get("PORTFOLIO_OUTBOUND_URL", "").strip()
    account_id = os.environ.get("PORTFOLIO_OUTBOUND_ACCOUNT_ID", "").strip()
    secret = os.environ.get("PORTFOLIO_OUTBOUND_SECRET", "").strip()
    if not url or not account_id or not secret:
        return None
    return _OutboundConfig(url=url, account_id=account_id, secret=secret)


def _make_initial_gathering_reply() -> ChannelReply:
    """Opening Portfolio Guru response for a handled WhatsApp turn.

    This is the real first workflow step: asking the doctor for their clinical
    case details so the conversation supervisor can classify and route the next
    turn.  Rendered via render_numbered so it works on any plain-text channel.
    """
    return ChannelReply(
        body=(
            "👋 Thanks for reaching out to Portfolio Guru.\n\n"
            "Please describe the clinical case you want to document — "
            "include what happened, your role, and any key details you remember."
        ),
        continuation="I'll help you choose the right form and complete the filing.",
    )


async def _send_portfolio_turn_reply(
    to: str,
    text: str,
    cfg: _OutboundConfig,
) -> None:
    """POST the rendered reply to the gateway's WhatsApp outbound send endpoint."""
    import httpx

    endpoint = (
        f"{cfg.url.rstrip('/')}/api/channels/whatsapp/{cfg.account_id}/send"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            endpoint,
            json={"to": to, "text": text},
            headers={"X-Portfolio-Secret": cfg.secret},
            timeout=10.0,
        )
        resp.raise_for_status()


@app.post("/api/portfolio/inbound")
async def portfolio_inbound(
    body: InboundRequest,
    x_gateway_secret: str | None = Header(default=None),
):
    """Handle a gateway-relayed turn: accept/refuse and run the first workflow step.

    For DIRECT turns with content (HANDLE), runs the initial Portfolio Guru
    gathering reply — rendered as a WhatsApp numbered block via render_numbered —
    and posts it back to the gateway's outbound send endpoint when configured.
    For GROUP and EMPTY turns, returns the refusal verdict with no side effects.
    """
    _verify_gateway_secret(x_gateway_secret)
    try:
        message = _build_inbound_message(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    decision = accept_inbound(message)

    if decision.disposition is InboundDisposition.HANDLE:
        outbound_cfg = _resolve_outbound_config()
        reply_sent = False
        if outbound_cfg is not None:
            reply = _make_initial_gathering_reply()
            rendered = render_numbered(reply)
            try:
                await _send_portfolio_turn_reply(body.conversation_id, rendered, outbound_cfg)
                reply_sent = True
            except Exception as exc:
                logger.warning("Portfolio outbound send failed: %s", exc)
        return {
            "disposition": decision.disposition.value,
            "refusal": None,
            "reply_sent": reply_sent,
            # fresh_start is always True here: Portfolio Guru has no server-side
            # session store yet.  The gateway is responsible for suppressing the
            # "Starting…" ACK on continuation turns via its own in-memory TTL.
            "fresh_start": decision.fresh_start,
        }

    refusal = (
        {
            "body": decision.refusal.body,
            "continuation": decision.refusal.continuation,
        }
        if decision.refusal is not None
        else None
    )
    return {
        "disposition": decision.disposition.value,
        "refusal": refusal,
        "fresh_start": decision.fresh_start,
    }


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
