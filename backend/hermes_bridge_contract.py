"""Hermes-to-Portfolio-Guru bridge contract.

Converts a Hermes-shaped inbound payload (a plain ``dict``) into the
channel-neutral types that the Portfolio Guru deterministic engine
expects, and serialises :class:`~channel_actions.ChannelReply` objects
back to plain dicts a Hermes action handler can render.

Design invariants:
- **No network, LLM, Kaizen, or Stripe calls.** Every function is a
  pure converter; the only I/O is dict ↔ dataclass translation.
- **No python-telegram-bot import.** This module must be importable
  inside a Hermes process that has never loaded the Telegram client.
- **No BWS / secrets access.** Credentials are never touched here.
- **No side effects.** Callers own the decision of what to do with the
  returned :class:`~channel_contract.InboundDecision`.

Typical call sequence
---------------------
::

    # 1. Hermes receives a message from the test bot and builds a payload.
    payload = {
        "channel": "telegram",
        "conversation_id": "tg:chat:12345",
        "gateway_user_id": "hermes-user-abc",
        "scope": "direct",
        "text": "62M chest pain, can you draft a CBD?",
    }

    # 2. Bridge converts payload → routing decision (no side effects).
    from hermes_bridge_contract import inbound_from_payload, serialise_decision
    decision = inbound_from_payload(payload)

    # 3. Hermes inspects the disposition and routes accordingly.
    if decision.disposition.value == "handle":
        # Pass decision.message to the next engine layer.
        ...
    else:
        # Render decision.refusal to the user.
        reply_dict = serialise_decision(decision)
        ...

Token isolation reminder
------------------------
This module never reads, stores, or logs Telegram bot tokens. The test
bot token (BWS secret name: ``TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST``;
OpenClaw/runtime alias: ``PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN``) is
owned exclusively by the Hermes profile. The live beta token
(``PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN``) belongs to the existing Python
bot. These two tokens must never be co-polled, swapped, or shared.
"""

from __future__ import annotations

from typing import Any

from channel_actions import ChannelAction, ChannelReply
from channel_contract import (
    Channel,
    ConversationScope,
    InboundDecision,
    InboundMessage,
    MediaRef,
    SessionRef,
    accept_inbound,
)

# ---------------------------------------------------------------------------
# Inbound: Hermes payload dict → InboundDecision
# ---------------------------------------------------------------------------

#: The set of keys a Hermes payload must (or may) carry.  Optional keys
#: default to sensible values so callers only need to supply the minimum.
PAYLOAD_CHANNEL_KEY = "channel"
PAYLOAD_CONVERSATION_ID_KEY = "conversation_id"
PAYLOAD_GATEWAY_USER_ID_KEY = "gateway_user_id"
PAYLOAD_SCOPE_KEY = "scope"
PAYLOAD_TEXT_KEY = "text"
PAYLOAD_MEDIA_KEY = "media"
PAYLOAD_PRIVATE_KEY = "private"


def inbound_from_payload(payload: dict[str, Any]) -> InboundDecision:
    """Convert a Hermes-shaped payload dict to an :class:`InboundDecision`.

    ``payload`` is the raw dict a Hermes action handler receives from the
    test bot adapter.  The function is total — it always returns an
    :class:`InboundDecision` and never raises for missing optional fields.

    :raises ValueError: if ``conversation_id`` is absent/empty, or if
        ``channel`` / ``scope`` are not recognised enum values.
    """
    channel = _parse_channel(payload.get(PAYLOAD_CHANNEL_KEY, "telegram"))
    scope = _parse_scope(payload.get(PAYLOAD_SCOPE_KEY, "direct"))
    conversation_id = str(payload.get(PAYLOAD_CONVERSATION_ID_KEY) or "").strip()
    if not conversation_id:
        raise ValueError(
            "Hermes payload missing required field 'conversation_id'."
        )

    session = SessionRef(
        channel=channel,
        conversation_id=conversation_id,
        gateway_user_id=payload.get(PAYLOAD_GATEWAY_USER_ID_KEY) or None,
    )

    raw_text = payload.get(PAYLOAD_TEXT_KEY)
    text: str | None = str(raw_text).strip() if raw_text else None
    if text == "":
        text = None

    media = _parse_media(payload.get(PAYLOAD_MEDIA_KEY) or [])
    private = bool(payload.get(PAYLOAD_PRIVATE_KEY, True))

    message = InboundMessage(
        session=session,
        scope=scope,
        text=text,
        media=media,
        private=private,
    )
    return accept_inbound(message)


def _parse_channel(raw: Any) -> Channel:
    try:
        return Channel(str(raw).lower())
    except ValueError:
        known = ", ".join(c.value for c in Channel)
        raise ValueError(
            f"Unknown channel value {raw!r}. Known values: {known}."
        )


def _parse_scope(raw: Any) -> ConversationScope:
    try:
        return ConversationScope(str(raw).lower())
    except ValueError:
        known = ", ".join(s.value for s in ConversationScope)
        raise ValueError(
            f"Unknown scope value {raw!r}. Known values: {known}."
        )


def _parse_media(raw_list: Any) -> tuple[MediaRef, ...]:
    if not isinstance(raw_list, (list, tuple)):
        return ()
    items: list[MediaRef] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if not kind:
            continue
        items.append(
            MediaRef(
                kind=kind,
                uri=item.get("uri") or None,
                mime_type=item.get("mime_type") or None,
                caption=item.get("caption") or None,
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# Outbound: ChannelReply / InboundDecision → plain dict
# ---------------------------------------------------------------------------


def serialise_reply(reply: ChannelReply) -> dict[str, Any]:
    """Serialise a :class:`~channel_actions.ChannelReply` to a plain dict.

    The dict is safe to JSON-encode and hand directly to a Hermes action
    renderer.  Action list order is preserved so numbered-channel fallback
    remains deterministic.
    """
    return {
        "body": reply.body,
        "continuation": reply.continuation,
        "actions": [
            {"action_id": action.action_id, "label": action.label}
            for action in reply.actions
        ],
    }


def serialise_decision(decision: InboundDecision) -> dict[str, Any]:
    """Serialise an :class:`~channel_contract.InboundDecision` to a plain dict.

    When ``disposition`` is ``"handle"``, ``"refusal"`` is absent.  When the
    disposition is a refusal, ``"refusal"`` contains the serialised
    :class:`~channel_actions.ChannelReply` the caller should render.
    ``"fresh_start"`` mirrors :attr:`~channel_contract.InboundDecision.fresh_start`.
    """
    result: dict[str, Any] = {
        "disposition": decision.disposition.value,
        "fresh_start": decision.fresh_start,
    }
    if decision.refusal is not None:
        result["refusal"] = serialise_reply(decision.refusal)
    return result


def deserialise_reply(data: dict[str, Any]) -> ChannelReply:
    """Reconstruct a :class:`~channel_actions.ChannelReply` from a serialised dict.

    Useful when a Hermes action handler receives a pre-built reply over a
    message boundary and needs to pass it to :func:`~channel_actions.to_telegram_keyboard`
    or :func:`~channel_actions.render_numbered`.
    """
    actions = tuple(
        ChannelAction(
            action_id=str(a["action_id"]),
            label=str(a["label"]),
        )
        for a in (data.get("actions") or [])
        if a.get("action_id") and a.get("label")
    )
    return ChannelReply(
        body=str(data.get("body") or ""),
        continuation=data.get("continuation") or None,
        actions=actions,
    )
