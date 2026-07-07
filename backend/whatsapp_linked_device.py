"""Direct WhatsApp linked-device transport normaliser for Portfolio Guru.

Portfolio Guru is the deterministic product brain; WhatsApp is only a channel
transport. This module is the transport-side adapter for the *direct
linked-device* connector path: a Baileys / WhatsApp-Web multi-device style
session that receives raw WhatsApp message envelopes and must hand them to the
repo-owned product boundary as a channel-neutral
:class:`~channel_contract.InboundMessage`.

The boundary is deliberately narrow:

* **This connector owns** WhatsApp plumbing only — parsing the linked-device
  message envelope, resolving the conversation JID, distinguishing DM from
  group, and mapping WhatsApp media types onto the engine's neutral media
  kinds. It carries **no** Portfolio Guru product logic: no extraction, no form
  recommendation, no drafting, no Kaizen access, and no credential handling.
* **Portfolio Guru owns** the turn once :func:`normalize_message` has produced a
  neutral envelope. Routing is decided only by
  :func:`~channel_contract.accept_inbound`; forwarding to the running product is
  done by posting :func:`to_inbound_payload` to the repo-owned
  ``POST /api/portfolio/inbound`` bridge in :mod:`webhook_server`.

Because the normaliser targets the same :class:`~channel_contract.InboundMessage`
contract as every other channel, a future Meta Cloud API webhook can replace
this linked-device transport by supplying its own normaliser to the same DTO
without touching product logic.

Safety boundary:

* No bytes are fetched and no WhatsApp media key is ever read or forwarded. A
  media item is represented only by a safe, opaque pointer derived from the
  message id (``wa-linked-device://<message-id>#<n>``); the live connector
  resolves the actual bytes out-of-band, never through this contract.
* The module is import-clean of ``python-telegram-bot`` and of every Portfolio
  Guru engine module, so it can run inside a thin connector process that never
  loads Telegram or the product brain.
* Nothing here links a device, authenticates a session, reads secrets, or
  starts a service. The next live step (linking a dedicated WhatsApp Business
  account via Linked Devices) is a controlled manual action gated by
  ``scripts/pg_whatsapp_readiness.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from channel_contract import (
    Channel,
    ConversationScope,
    InboundDecision,
    InboundDisposition,
    InboundMessage,
    MediaRef,
    SessionRef,
    accept_inbound,
)

# The direct linked-device connector family. All of these treat WhatsApp as a
# thin transport to the deterministic engine and require no Hermes profile. The
# readiness guard recognises the same names; "hermes" is the only value that
# pulls in the optional Hermes-profile gates.
LINKED_DEVICE_CONNECTORS = frozenset({"direct", "linked-device", "baileys"})

# WhatsApp JID suffixes that are never a 1:1 portfolio conversation. Group,
# broadcast, newsletter and status JIDs are a gateway/community responsibility
# and are surfaced as GROUP scope so accept_inbound refuses them — private
# portfolio evidence must never be drafted into a shared thread.
_NON_DIRECT_JID_SUFFIXES = ("@g.us", "@broadcast", "@newsletter")

# Map a WhatsApp linked-device message container to the engine's neutral media
# kinds (voice/audio/photo/document/video). audioMessage is resolved separately
# because a push-to-talk clip (``ptt: true``) is a voice note, not an audio file.
_MEDIA_KIND_BY_CONTAINER = {
    "imageMessage": "photo",
    "documentMessage": "document",
    "videoMessage": "video",
    "stickerMessage": "sticker",
}


@dataclass(frozen=True)
class NormalizedInbound:
    """A normalised linked-device turn plus its routing verdict.

    ``message`` is the channel-neutral envelope handed to the product boundary;
    ``decision`` is the :func:`~channel_contract.accept_inbound` verdict. The
    connector never inspects clinical content — it only needs the disposition to
    decide whether to forward the turn or render the neutral refusal.
    """

    message: InboundMessage | None
    decision: InboundDecision


def _is_routable_frame(raw: Any) -> bool:
    """True only when ``raw`` carries a usable 1:1 routing identity.

    A live linked-device session streams more than user turns: internal /
    protocol frames, receipts, and partially-decoded envelopes can arrive with no
    ``key`` mapping or a blank ``key.remoteJid``. Those are not a conversation and
    cannot be normalised onto the neutral contract, so the connector must drop
    them locally rather than crash or forward them. This is the single structural
    gate that distinguishes such non-user traffic from a real turn; it inspects
    only routing shape, never content.
    """
    if not isinstance(raw, Mapping):
        return False
    key = raw.get("key")
    if not isinstance(key, Mapping):
        return False
    return bool(str(key.get("remoteJid") or "").strip())


def _require_mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{what} must be a mapping")
    return value


def _remote_jid(key: Mapping[str, Any]) -> str:
    jid = str(key.get("remoteJid") or "").strip()
    if not jid:
        raise ValueError("message key.remoteJid is required")
    return jid


def _scope_for_jid(remote_jid: str) -> ConversationScope:
    lowered = remote_jid.lower()
    if any(lowered.endswith(suffix) for suffix in _NON_DIRECT_JID_SUFFIXES):
        return ConversationScope.GROUP
    if lowered == "status@broadcast":
        return ConversationScope.GROUP
    return ConversationScope.DIRECT


def _gateway_user_id(key: Mapping[str, Any], remote_jid: str) -> str:
    """The resolved sender identity — a routing id, never clinical content.

    In a group envelope the actual sender is ``key.participant``; in a 1:1
    envelope the sender is the conversation JID itself.
    """
    participant = str(key.get("participant") or "").strip()
    return participant or remote_jid


def _extract_text(message: Mapping[str, Any]) -> str | None:
    """Plain body text of the turn, if any.

    Only true text bodies count here (``conversation`` and
    ``extendedTextMessage.text``). Media captions are carried on the media ref,
    not merged into the turn text, so the boundary keeps caption provenance.
    """
    conversation = message.get("conversation")
    if isinstance(conversation, str) and conversation.strip():
        return conversation

    extended = message.get("extendedTextMessage")
    if isinstance(extended, Mapping):
        text = extended.get("text")
        if isinstance(text, str) and text.strip():
            return text
    return None


def _media_kind(container: str, payload: Mapping[str, Any]) -> str:
    if container == "audioMessage":
        return "voice" if bool(payload.get("ptt")) else "audio"
    return _MEDIA_KIND_BY_CONTAINER[container]


def _safe_media_uri(message_id: str, index: int) -> str | None:
    """Opaque, non-secret pointer to one media item.

    The WhatsApp media key and encrypted bytes are never placed in the
    contract. The live connector resolves the bytes out-of-band from the
    message id; here we only carry a stable reference so downstream code knows a
    media item exists and can request it.
    """
    if not message_id:
        return None
    return f"wa-linked-device://{message_id}#{index}"


def _extract_media(
    message: Mapping[str, Any], message_id: str
) -> tuple[MediaRef, ...]:
    refs: list[MediaRef] = []
    index = 0
    for container in ("audioMessage", *_MEDIA_KIND_BY_CONTAINER):
        payload = message.get(container)
        if not isinstance(payload, Mapping):
            continue
        kind = _media_kind(container, payload)
        caption = payload.get("caption")
        refs.append(
            MediaRef(
                kind=kind,
                uri=_safe_media_uri(message_id, index),
                mime_type=(
                    str(payload["mimetype"]) if payload.get("mimetype") else None
                ),
                caption=caption if isinstance(caption, str) and caption else None,
            )
        )
        index += 1
    return tuple(refs)


def normalize_message(raw: Mapping[str, Any]) -> InboundMessage:
    """Map one raw linked-device WhatsApp envelope to the neutral contract.

    ``raw`` is a Baileys / WhatsApp-Web multi-device message object with a
    ``key`` (``remoteJid``, optional ``participant``, ``id``) and a ``message``
    container. Construction is permissive: a turn with no recognised text or
    media yields a contentless :class:`~channel_contract.InboundMessage` that
    :func:`~channel_contract.accept_inbound` refuses as empty, rather than
    raising. Only structurally missing routing data (no ``remoteJid``) raises.

    No product logic runs here and no bytes are fetched — this is pure transport
    normalisation onto the same DTO every channel shares.
    """
    raw = _require_mapping(raw, "linked-device message")
    key = _require_mapping(raw.get("key", {}), "message key")
    message = raw.get("message")
    message = message if isinstance(message, Mapping) else {}

    remote_jid = _remote_jid(key)
    message_id = str(key.get("id") or "").strip()

    return InboundMessage(
        session=SessionRef(
            channel=Channel.WHATSAPP,
            conversation_id=f"wa:{remote_jid}",
            gateway_user_id=_gateway_user_id(key, remote_jid),
        ),
        scope=_scope_for_jid(remote_jid),
        text=_extract_text(message),
        media=_extract_media(message, message_id),
        private=True,
    )


def normalize_and_route(raw: Mapping[str, Any]) -> NormalizedInbound:
    """Normalise a raw envelope and compute its routing verdict — no side effects.

    This is the single entrypoint a live linked-device connector calls before
    touching any product workflow: DIRECT turns with content are handled, GROUP
    turns are refused as a gateway responsibility, contentless turns are refused
    as empty, and internal/non-user frames with no routable ``remoteJid`` are
    refused as invalid and dropped locally (``message`` is ``None``) rather than
    raising, so a Baileys protocol frame can never crash the relay or be
    forwarded.
    """
    if not _is_routable_frame(raw):
        return NormalizedInbound(
            message=None,
            decision=InboundDecision(disposition=InboundDisposition.REFUSE_INVALID),
        )
    message = normalize_message(raw)
    return NormalizedInbound(message=message, decision=accept_inbound(message))


def to_inbound_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Build the JSON body for the repo-owned ``POST /api/portfolio/inbound`` bridge.

    The live connector posts this to :mod:`webhook_server` with the shared
    gateway secret; the bridge re-validates and re-routes it. Keeping the
    forward path as the same neutral envelope means the product boundary is
    identical whether the turn arrives over linked-device, a future Meta Cloud
    API webhook, or the Hermes thin transport.
    """
    message = normalize_message(raw)
    return {
        "channel": message.session.channel.value,
        "conversation_id": message.session.conversation_id,
        "gateway_user_id": message.session.gateway_user_id,
        "scope": message.scope.value,
        "text": message.text,
        "media": [
            {
                "kind": ref.kind,
                "uri": ref.uri,
                "mime_type": ref.mime_type,
                "caption": ref.caption,
            }
            for ref in message.media
        ],
        "private": message.private,
    }


def dry_run(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Offline routing preview for one raw envelope — never contacts a service.

    Returns only routing metadata (channel, scope, disposition, media kinds and
    whether any content is present). It deliberately excludes the message text
    and media captions so a dry run can be logged without spilling clinical
    content, mirroring the refusal-never-echoes rule at the boundary.
    """
    normalized = normalize_and_route(raw)
    message = normalized.message
    if message is None:
        # A non-user/internal frame with no routable identity — dropped locally.
        return {
            "channel": Channel.WHATSAPP.value,
            "conversation_id": None,
            "scope": None,
            "disposition": normalized.decision.disposition.value,
            "has_content": False,
            "media_kinds": [],
            "fresh_start": normalized.decision.fresh_start,
        }
    return {
        "channel": message.session.channel.value,
        "conversation_id": message.session.conversation_id,
        "scope": message.scope.value,
        "disposition": normalized.decision.disposition.value,
        "has_content": message.has_content(),
        "media_kinds": [ref.kind for ref in message.media],
        "fresh_start": normalized.decision.fresh_start,
    }


def main(argv: list[str] | None = None) -> int:
    """Offline CLI harness: normalise a linked-device payload and print the verdict.

    Reads a single JSON message envelope from ``--payload <file>`` or stdin,
    prints the :func:`dry_run` routing preview as JSON, and exits 0 on a handled
    turn or 2 on a refusal. It performs no network I/O, links no device, reads
    no secret, and starts no service — it is purely a deterministic harness for
    exercising the transport boundary with recorded payloads.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description=(
            "Offline dry-run for the Portfolio Guru direct WhatsApp "
            "linked-device connector. Reads one raw message envelope and prints "
            "its channel-neutral routing verdict. Touches no live service."
        )
    )
    parser.add_argument(
        "--payload",
        type=str,
        default=None,
        help="Path to a JSON linked-device message envelope (defaults to stdin).",
    )
    args = parser.parse_args(argv)

    if args.payload:
        with open(args.payload, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    else:
        raw = json.load(sys.stdin)

    result = dry_run(raw)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["disposition"] == "handle" else 2


if __name__ == "__main__":
    raise SystemExit(main())
