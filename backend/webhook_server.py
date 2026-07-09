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
import time
from dataclasses import dataclass, field
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
        try:
            import ops_alert
            ops_alert.notify_operator_sync(f"Stripe webhook FAILED: {e}", key="webhook_fail")
        except Exception:
            logger.debug("operator alert failed", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    # A paid event that did not result in a tier change is a billing-correctness
    # problem (e.g. unknown price id, unlinked user) — these return a 200 today,
    # so without an alert a charged-but-unupgraded customer is invisible.
    if result.get("action") in {"error", "ignored", "user_not_found"}:
        try:
            import ops_alert
            ops_alert.notify_operator_sync(
                f"Stripe webhook unhandled outcome: {result.get('action')} "
                f"({result.get('error') or result.get('type') or ''})",
                key="webhook_unhandled",
            )
        except Exception:
            logger.debug("operator alert failed", exc_info=True)

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
# Portfolio Guru WhatsApp channel inbound bridge
# ---------------------------------------------------------------------------
# Portfolio Guru uses WhatsApp as a thin channel connector. For tester rollout,
# the connector must be tied to a dedicated Portfolio Guru WhatsApp
# number/account, not the general EMGurus account, and not an EMGurus fan-out
# gateway. (A Hermes profile is only one optional thin-transport connector, never
# the product brain.) The connector owns the WhatsApp number and DM-vs-group
# routing; it calls this endpoint to learn whether Portfolio Guru will take a
# turn (DIRECT) or refuse it (GROUP / empty). This is a thin wrapper around
# channel_contract.accept_inbound — it starts no workflow, touches no
# credential, and never echoes inbound content back into a shared thread.
#
# The bridge is private: callers must present the shared secret in
# PORTFOLIO_INBOUND_SECRET via the X-Gateway-Secret header. The gateway holds
# the matching value; this server only ever reads it from the environment.

from channel_actions import ChannelReply, render_numbered, resolve_numbered_choice
from channel_reply_policy import select_deterministic_reply
from channel_contract import (
    Channel,
    ConversationScope,
    InboundDisposition,
    InboundMessage,
    MediaRef,
    SessionRef,
    accept_inbound,
)
from conversation_supervisor import (
    DRAFT_NOW_ACTION,
    GatheringTurnKind,
    decide_gathering_turn,
)
from conversational_router import ConversationalIntent, route_message
from message_policy import render_message
from portfolio_first_contact import classify_first_contact, first_contact_reply


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


@dataclass
class _InboundWorkflowState:
    """In-process WhatsApp case bundle for the current direct conversation.

    This deliberately stores only transient channel state. It is not a durable
    filing workflow, and it never writes to Kaizen.
    """

    created_at: float
    updated_at: float
    parts: list[str] = field(default_factory=list)

    def append(self, text: str | None, *, now: float) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        self.parts.append(cleaned)
        self.updated_at = now

    def combined_text(self) -> str:
        return "\n\n".join(part for part in self.parts if part.strip())


class _InboundSessionStore:
    """Small in-process freshness tracker for the private WhatsApp bridge.

    The bridge is not the durable conversation engine. It only needs to know
    whether a direct channel conversation has been seen recently so fixed
    first-contact onboarding does not repeat on every WhatsApp turn.
    """

    def __init__(self, ttl_seconds: int, *, clock=time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._last_seen: dict[str, float] = {}
        self._last_action_replies: dict[str, ChannelReply] = {}
        self._workflow_states: dict[str, _InboundWorkflowState] = {}

    def mark_seen(self, *, channel: str, conversation_id: str) -> bool:
        """Return True when this is a fresh session, then update last-seen."""
        now = self._clock()
        self._prune(now)
        key = self._key(channel=channel, conversation_id=conversation_id)
        previous = self._last_seen.get(key)
        self._last_seen[key] = now
        if previous is None:
            return True
        return now - previous > self.ttl_seconds

    def resolve_action(
        self,
        *,
        channel: str,
        conversation_id: str,
        text: str | None,
    ) -> str | None:
        """Resolve text against the last rendered numbered actions, if any."""
        key = self._key(channel=channel, conversation_id=conversation_id)
        reply = self._last_action_replies.get(key)
        if reply is None:
            return None
        return resolve_numbered_choice(reply, text)

    def remember_reply(
        self,
        *,
        channel: str,
        conversation_id: str,
        reply: ChannelReply,
    ) -> None:
        """Store action-bearing replies and clear stale actions otherwise."""
        key = self._key(channel=channel, conversation_id=conversation_id)
        if reply.actions:
            self._last_action_replies[key] = reply
        else:
            self._last_action_replies.pop(key, None)

    def workflow_state(
        self,
        *,
        channel: str,
        conversation_id: str,
    ) -> _InboundWorkflowState | None:
        return self._workflow_states.get(
            self._key(channel=channel, conversation_id=conversation_id)
        )

    def start_workflow(
        self,
        *,
        channel: str,
        conversation_id: str,
        initial_text: str | None = None,
    ) -> _InboundWorkflowState:
        key = self._key(channel=channel, conversation_id=conversation_id)
        now = self._clock()
        state = self._workflow_states.get(key)
        if state is None:
            state = _InboundWorkflowState(created_at=now, updated_at=now)
            self._workflow_states[key] = state
        state.append(initial_text, now=now)
        return state

    def append_workflow_text(
        self,
        *,
        channel: str,
        conversation_id: str,
        text: str | None,
    ) -> _InboundWorkflowState:
        state = self.start_workflow(
            channel=channel,
            conversation_id=conversation_id,
        )
        state.append(text, now=self._clock())
        return state

    def clear_workflow(
        self,
        *,
        channel: str,
        conversation_id: str,
    ) -> None:
        self._workflow_states.pop(
            self._key(channel=channel, conversation_id=conversation_id),
            None,
        )

    def reset(self) -> None:
        """Clear tracked sessions; used by offline tests."""
        self._last_seen.clear()
        self._last_action_replies.clear()
        self._workflow_states.clear()

    def _prune(self, now: float) -> None:
        expired = [
            key for key, last_seen in self._last_seen.items()
            if now - last_seen > self.ttl_seconds
        ]
        for key in expired:
            self._last_seen.pop(key, None)
            self._last_action_replies.pop(key, None)
            self._workflow_states.pop(key, None)

    def _key(self, *, channel: str, conversation_id: str) -> str:
        return f"{channel}:{conversation_id}"


def _session_ttl_seconds() -> int:
    raw = os.environ.get("PORTFOLIO_INBOUND_SESSION_TTL_SECONDS", "").strip()
    if not raw:
        return 60 * 60
    try:
        parsed = int(raw)
    except ValueError:
        return 60 * 60
    return max(60, parsed)


_inbound_session_store = _InboundSessionStore(_session_ttl_seconds())


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
# the gateway's WhatsApp outbound send endpoint.  The four env vars below
# configure that path; the feature is inert when any of them is absent so
# generic / non-WhatsApp installs are unaffected.
#
#   PORTFOLIO_OUTBOUND_URL          — base URL of the OpenClaw gateway
#   PORTFOLIO_OUTBOUND_ACCOUNT_ID   — WhatsApp account id to route through
#   PORTFOLIO_OUTBOUND_SECRET       — shared secret sent as X-Portfolio-Secret
#   PORTFOLIO_OUTBOUND_GATEWAY_TOKEN — gateway bearer token for plugin route auth


class _OutboundConfig:
    """Resolved configuration for the Portfolio Guru outbound send path."""

    def __init__(
        self,
        *,
        url: str,
        account_id: str,
        secret: str,
        gateway_token: str,
    ) -> None:
        self.url = url
        self.account_id = account_id
        self.secret = secret
        self.gateway_token = gateway_token


def _resolve_outbound_config() -> "_OutboundConfig | None":
    """Read the outbound config from the environment; return None if incomplete."""
    url = os.environ.get("PORTFOLIO_OUTBOUND_URL", "").strip()
    account_id = os.environ.get("PORTFOLIO_OUTBOUND_ACCOUNT_ID", "").strip()
    secret = os.environ.get("PORTFOLIO_OUTBOUND_SECRET", "").strip()
    gateway_token = os.environ.get("PORTFOLIO_OUTBOUND_GATEWAY_TOKEN", "").strip()
    if not url or not account_id or not secret or not gateway_token:
        return None
    return _OutboundConfig(
        url=url,
        account_id=account_id,
        secret=secret,
        gateway_token=gateway_token,
    )


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


_RICH_CASE_WORD_THRESHOLD = 15
_DIRECT_INBOUND_POLICY_INTENTS = frozenset(
    {
        ConversationalIntent.SETUP_OR_CREDENTIALS,
    }
)


def _has_rich_case_content(text: str | None) -> bool:
    """True when text is substantive enough to route directly to the drafting path.

    Pure word-count heuristic — no LLM call.  Short greetings and vague help
    requests continue to the gathering prompt; detailed case descriptions skip
    it and receive a form recommendation with targeted missing-info asks.
    """
    if not text:
        return False
    return len(text.split()) >= _RICH_CASE_WORD_THRESHOLD


def _select_direct_policy_reply(text: str | None) -> ChannelReply | None:
    """Return a deterministic direct-channel reply for safe side questions."""
    routed = route_message(text or "")
    if routed.intent not in _DIRECT_INBOUND_POLICY_INTENTS:
        return None
    return select_deterministic_reply(text, include_first_contact=False)


def _make_resolved_action_reply(action_id: str) -> ChannelReply:
    """Return static WhatsApp-safe guidance for a resolved plain-text action."""
    if action_id == "ACTION|setup":
        return ChannelReply(
            body=(
                "🔗 Connect Kaizen\n\n"
                "Use the secure Portfolio Guru setup flow on Telegram to connect "
                "or update Kaizen. I can't collect Kaizen credentials in WhatsApp.\n\n"
                "Open Portfolio Guru on Telegram, send /start, choose Connect "
                "Kaizen, and complete the private setup there. Once connected, "
                "you can send anonymised case details here and I can help with "
                "the portfolio draft."
            )
        )
    if action_id == "ACTION|settings":
        return ChannelReply(
            body=(
                "⚙️ Settings\n\n"
                "Account settings, Kaizen connection changes, and credential "
                "updates are handled in the secure Portfolio Guru Telegram flow. "
                "I can't change settings or collect credentials in WhatsApp.\n\n"
                "Open Portfolio Guru on Telegram and send /settings or /start."
            )
        )
    return ChannelReply(
        body=(
            "That option is not available in WhatsApp yet.\n\n"
            "Send anonymised case details here, or use the Portfolio Guru "
            "Telegram bot for setup, settings, and approval-gated filing."
        )
    )


def _make_gathering_captured_reply() -> ChannelReply:
    """Channel-neutral capture acknowledgement with a WhatsApp-resolvable action."""
    return ChannelReply(
        body=render_message("gathering_captured"),
        actions=(DRAFT_NOW_ACTION,),
    )


async def _answer_inbound_side_question(text: str) -> str:
    """Deterministic WhatsApp side-question answer for active gathering.

    Telegram can use a richer grounded answer path inside the supervisor. The
    WhatsApp bridge stays safer for now: it answers from deterministic channel
    policy and points the user back to the active case without making LLM calls.
    """
    reply = select_deterministic_reply(text, include_first_contact=False)
    if reply is None:
        return render_message("capability_overview")
    return reply.full_text()


def _looks_like_unmatched_plain_choice(text: str | None) -> bool:
    stripped = (text or "").strip()
    return bool(stripped) and stripped.isdigit()


async def _make_workflow_finish_reply(
    state: _InboundWorkflowState | None,
) -> ChannelReply:
    """Resolve a WhatsApp Draft now/done turn without saving or generating a draft."""
    case_text = state.combined_text() if state is not None else ""
    if not case_text.strip():
        return ChannelReply(
            body=(
                "📋 Case details needed\n\n"
                "I do not have a case captured for that option yet. Send "
                "anonymised case details, then choose Draft now."
            )
        )
    return await _make_case_insight_reply(case_text)


async def _select_active_workflow_reply(
    text: str | None,
    *,
    channel: str,
    conversation_id: str,
) -> ChannelReply:
    """Handle a subsequent WhatsApp turn inside the transient gathering workflow."""
    if _looks_like_unmatched_plain_choice(text):
        return ChannelReply(
            body=(
                "That option is no longer available.\n\n"
                "Send anonymised case details, or type done when you are ready "
                "for me to check the best-fit form."
            )
        )

    decision = await decide_gathering_turn(
        text,
        answer_question=_answer_inbound_side_question,
    )
    state = _inbound_session_store.workflow_state(
        channel=channel,
        conversation_id=conversation_id,
    )

    if decision.kind is GatheringTurnKind.FINISH_CASE:
        return await _make_workflow_finish_reply(state)

    if decision.add_to_case:
        _inbound_session_store.append_workflow_text(
            channel=channel,
            conversation_id=conversation_id,
            text=text,
        )
        return _make_gathering_captured_reply()

    assert decision.reply is not None
    return decision.reply


async def _make_case_insight_reply(text: str) -> ChannelReply:
    """Draft-aware reply when the inbound message is a substantive clinical case.

    Calls recommend_form_types to surface the most applicable WPBA form, then
    returns a ChannelReply naming the form, stating the rationale, and asking
    only for details genuinely absent from the description.  No Kaizen write
    occurs — this is a recommendation and targeted fact-gather, not a filing.
    Falls back to the standard gathering prompt if recommendation fails.
    """
    from extractor import recommend_form_types
    from form_display import public_form_name

    try:
        recommendations = await recommend_form_types(text)
    except Exception as exc:
        logger.warning("Form recommendation failed, using gathering prompt: %s", exc)
        return _make_initial_gathering_reply()

    if not recommendations:
        return _make_initial_gathering_reply()

    top = recommendations[0]
    form_label = public_form_name(top.form_type) or top.form_type

    body = (
        f"Based on your description, the recommended WPBA form is:\n"
        f"{form_label} ({top.form_type})\n\n"
        f"{top.rationale}\n\n"
        "To complete your draft I need a few details:\n"
        "- Date of the activity (dd/mm/yyyy)\n"
        "- Your training grade and current placement\n"
        "- Your specific role or contribution\n"
        "- Supervisor / assessor name and grade\n"
        "- Key metrics or outcomes (if applicable)\n\n"
        "Reply with the above and I'll prepare your portfolio entry."
    )

    return ChannelReply(body=body, continuation=None)


async def _select_inbound_reply(
    text: str | None,
    *,
    fresh_start: bool = True,
    channel: str = "whatsapp",
    conversation_id: str = "",
) -> ChannelReply:
    """Pick the first Portfolio Guru reply for a handled DIRECT turn.

    First-contact openings — a ``/start`` / ``start`` command, a bare greeting,
    or a capability question — get the same FIXED onboarding copy the Telegram
    welcome uses, so a WhatsApp user is oriented instead of being told to
    "describe the clinical case" before they know what the service is. Only when
    the turn is a real case does routing fall through to the rich-case draft
    insight (>= threshold words) or the generic gathering prompt. The
    classification is deterministic and LLM-free (see
    :mod:`portfolio_first_contact`).
    """
    if fresh_start:
        onboarding = first_contact_reply(classify_first_contact(text))
        if onboarding is not None:
            return onboarding
    policy_reply = _select_direct_policy_reply(text)
    if policy_reply is not None:
        return policy_reply
    if _inbound_session_store.workflow_state(
        channel=channel,
        conversation_id=conversation_id,
    ) is not None:
        return await _select_active_workflow_reply(
            text,
            channel=channel,
            conversation_id=conversation_id,
        )
    if _has_rich_case_content(text):
        _inbound_session_store.start_workflow(
            channel=channel,
            conversation_id=conversation_id,
            initial_text=text,
        )
        return await _make_case_insight_reply(text or "")
    _inbound_session_store.start_workflow(
        channel=channel,
        conversation_id=conversation_id,
    )
    return _make_initial_gathering_reply()


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
            headers={
                "Authorization": f"Bearer {cfg.gateway_token}",
                "X-Portfolio-Secret": cfg.secret,
            },
            timeout=10.0,
        )
        resp.raise_for_status()


def _is_whatsapp_user_jid(value: str | None) -> bool:
    """True when ``value`` is a direct WhatsApp user JID, not a group/session label."""
    if not value:
        return False
    lowered = value.strip().lower()
    return lowered.endswith("@s.whatsapp.net") or lowered.endswith("@lid")


def _reply_target_for_inbound(body: InboundRequest) -> str:
    """Choose the safest outbound target for a handled inbound WhatsApp turn.

    ``conversation_id`` is the stable channel conversation key and can be a LID.
    Baileys may also provide ``gateway_user_id`` as the phone-number JID
    (``senderPn`` / ``participantPn``); use that when present for visible
    delivery, otherwise fall back to the conversation id.
    """
    if body.channel == "whatsapp" and _is_whatsapp_user_jid(body.gateway_user_id):
        return body.gateway_user_id or body.conversation_id
    return body.conversation_id


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
        fresh_start = _inbound_session_store.mark_seen(
            channel=body.channel,
            conversation_id=body.conversation_id,
        )
        outbound_cfg = _resolve_outbound_config()
        reply_sent = False
        if outbound_cfg is not None:
            action_id = _inbound_session_store.resolve_action(
                channel=body.channel,
                conversation_id=body.conversation_id,
                text=body.text,
            )
            if action_id == DRAFT_NOW_ACTION.action_id:
                reply = await _make_workflow_finish_reply(
                    _inbound_session_store.workflow_state(
                        channel=body.channel,
                        conversation_id=body.conversation_id,
                    )
                )
            elif action_id is not None:
                reply = _make_resolved_action_reply(action_id)
            else:
                reply = await _select_inbound_reply(
                    body.text,
                    fresh_start=fresh_start,
                    channel=body.channel,
                    conversation_id=body.conversation_id,
                )
            rendered = render_numbered(reply)
            _inbound_session_store.remember_reply(
                channel=body.channel,
                conversation_id=body.conversation_id,
                reply=reply,
            )
            try:
                await _send_portfolio_turn_reply(
                    _reply_target_for_inbound(body), rendered, outbound_cfg
                )
                reply_sent = True
            except Exception as exc:
                logger.warning("Portfolio outbound send failed: %s", exc)
        return {
            "disposition": decision.disposition.value,
            "refusal": None,
            "reply_sent": reply_sent,
            "fresh_start": fresh_start,
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
