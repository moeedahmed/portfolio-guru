"""Channel-neutral inbound contract for the Portfolio Guru WhatsApp boundary.

Portfolio Guru is a 1:1 portfolio/ARCP filing service. WhatsApp is a channel
connector around the deterministic engine, not a place for product logic.
Current rollout requires a dedicated Portfolio Guru WhatsApp number/account and
a thin channel connector before tester traffic — not the general EMGurus account
and not an EMGurus fan-out gateway. A Hermes profile is optional: it may be used
only as a thin transport for that connector, never as the product brain. The
connector owns WhatsApp plumbing and DM-vs-group routing; Portfolio Guru owns
only direct portfolio turns and refuses group/community scope.

This module is the *inbound* counterpart to :mod:`channel_actions` (which
renders a channel-neutral :class:`~channel_actions.ChannelReply` to a Telegram
inline keyboard or a WhatsApp-friendly numbered block). Here we describe what
the gateway hands *in*: a channel-neutral :class:`InboundMessage` envelope
carrying the channel, the conversation scope, a stable session key, an optional
resolved gateway user id, and the content — text and/or media references — with
portfolio content marked **private by default**.

Responsibility split (the gateway-owned vs Portfolio Guru-owned boundary):

* **Gateway owns** the dedicated WhatsApp number, Meta/WhatsApp API plumbing,
  DM-vs-group routing, identity resolution, and delivery.
* **Portfolio Guru owns** only the DIRECT (1:1) portfolio-filing conversation:
  extraction, drafting, and draft-only Kaizen saves behind existing
  confirmation gates. It refuses GROUP scope — that is the gateway's job — and
  treats portfolio evidence as private 1:1 state that must never be shared into
  any group/community agent context.

The boundary is deliberately a *contract + guard* only. It does not connect to
Meta/WhatsApp, read or write any credential, or wire into the live Telegram
handlers; the existing Telegram path is untouched. A future gateway adapter
constructs an :class:`InboundMessage` and calls :func:`accept_inbound` to learn
whether Portfolio Guru will handle the turn (DIRECT) or refuse it (GROUP / empty
content). The module is import-clean of ``python-telegram-bot`` so it can run
inside a gateway process that never loads Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from channel_actions import ChannelReply


class Channel(str, Enum):
    """The channel the gateway resolved the conversation on.

    Portfolio Guru is channel-neutral: the channel is routing metadata for the
    gateway adapter, not a switch in any portfolio workflow.
    """

    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    WEB = "web"


class ConversationScope(str, Enum):
    """Who the conversation is with.

    Only :attr:`DIRECT` (1:1) is owned by Portfolio Guru. :attr:`GROUP` is a
    gateway/community responsibility and is always refused here so private
    portfolio evidence can never be drafted into a shared thread.
    """

    DIRECT = "direct"
    GROUP = "group"


@dataclass(frozen=True)
class SessionRef:
    """Stable, opaque routing key for one conversation.

    ``channel`` + ``conversation_id`` identify the channel-side conversation;
    ``gateway_user_id`` is the channel/user identity the gateway resolved, when
    known. None of these carry clinical content — they are routing only, so the
    contract requires channel/session context without ever requiring the body.
    """

    channel: Channel
    conversation_id: str
    gateway_user_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.channel, Channel):
            raise ValueError("channel must be a Channel")
        if not self.conversation_id:
            raise ValueError("conversation_id is required")


@dataclass(frozen=True)
class MediaRef:
    """A channel-neutral pointer to one inbound media item.

    The gateway fetches the bytes and provides a reference (``uri``); raw
    credentials and decrypted blobs never travel through this contract.
    ``kind`` mirrors the existing engine source types (voice/audio/photo/
    document) so a gateway adapter maps cleanly onto the case engine.
    """

    kind: str
    uri: str | None = None
    mime_type: str | None = None
    caption: str | None = None

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("media kind is required")


@dataclass(frozen=True)
class InboundMessage:
    """A channel-neutral inbound turn handed in by the gateway.

    ``private`` defaults to ``True``: portfolio evidence is private 1:1 state by
    default and must never be replayed into group/community context. Construction
    is permissive (the boundary receives untrusted gateway input); a turn with
    no content — no text and no media — is caught at :func:`accept_inbound` as
    :attr:`InboundDisposition.REFUSE_EMPTY` rather than crashing here.
    """

    session: SessionRef
    scope: ConversationScope
    text: str | None = None
    media: tuple[MediaRef, ...] = field(default_factory=tuple)
    private: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ConversationScope):
            raise ValueError("scope must be a ConversationScope")

    def has_content(self) -> bool:
        return bool((self.text or "").strip()) or bool(self.media)


class InboundDisposition(str, Enum):
    """What Portfolio Guru will do with an inbound turn."""

    HANDLE = "handle"
    REFUSE_GROUP = "refuse_group"
    REFUSE_EMPTY = "refuse_empty"
    # A frame that is not a routable 1:1 user turn at all — an internal/protocol
    # Baileys frame with no ``key.remoteJid`` or otherwise too malformed to carry
    # a conversation identity. It is dropped locally by the connector and never
    # forwarded; it is a transport-plumbing disposition, not a product refusal, so
    # no channel-neutral refusal copy is rendered for it.
    REFUSE_INVALID = "refuse_invalid"


@dataclass(frozen=True)
class InboundDecision:
    """The routing verdict for one inbound turn.

    ``message`` is set only when ``disposition`` is
    :attr:`InboundDisposition.HANDLE`; ``refusal`` is a channel-neutral
    :class:`~channel_actions.ChannelReply` only on a refusal, so the gateway can
    render the same wording on any channel.

    ``fresh_start`` signals whether this is the opening turn of a new session.
    Portfolio Guru currently has no server-side session store, so this is always
    ``True`` on HANDLE responses. The gateway (OpenClaw WhatsApp bridge) is
    authoritative for session continuity: it maintains an in-memory TTL per
    conversationId and suppresses the "Starting…" acknowledgement on
    continuation turns, regardless of this flag. When Portfolio Guru implements
    its own session store this field will reflect backend-tracked state and the
    gateway can defer to it.
    """

    disposition: InboundDisposition
    message: InboundMessage | None = None
    refusal: ChannelReply | None = None
    fresh_start: bool = True


# Refusal copy lives here rather than in ``message_policy`` because it is
# gateway-boundary infrastructure copy, not part of the live Telegram user-flow
# templates. It names the responsibility split for the user without echoing any
# inbound content back into a shared thread.
_GROUP_REFUSAL = ChannelReply(
    body=(
        "🔒 Portfolio filing is private and one-to-one. I can't work on portfolio "
        "evidence in a group chat — that keeps your patient details and drafts "
        "out of shared threads."
    ),
    continuation="💬 Message me directly and we'll pick up your portfolio there.",
)


def accept_inbound(message: InboundMessage) -> InboundDecision:
    """Decide whether Portfolio Guru handles this inbound turn — no side effects.

    DIRECT (1:1) turns with content are handled; GROUP turns are refused as a
    gateway responsibility with a channel-neutral refusal that never echoes the
    inbound content; contentless turns are refused as empty. This is the single
    entrypoint a gateway adapter calls before touching any portfolio workflow.
    """
    if message.scope is not ConversationScope.DIRECT:
        return InboundDecision(
            disposition=InboundDisposition.REFUSE_GROUP,
            refusal=_GROUP_REFUSAL,
        )
    if not message.has_content():
        return InboundDecision(disposition=InboundDisposition.REFUSE_EMPTY)
    return InboundDecision(disposition=InboundDisposition.HANDLE, message=message)
