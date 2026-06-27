"""Channel-agnostic conversation output model.

A reply is described once — a response ``body``, an optional
``continuation`` line that points the user back to the active workflow,
and a tuple of channel-agnostic ``actions``. Renderers then turn that
single description into a Telegram inline keyboard or a WhatsApp-friendly
numbered/plain-text block.

The point is that button *text* is never the source of truth: the stable
``action_id`` is. Telegram uses ``action_id`` as inline-keyboard
callback data; a numbered channel (WhatsApp, SMS) lets the user reply
with the option number or the label, and :func:`resolve_numbered_choice`
maps that back to the same ``action_id``. Adding a new channel means
adding a renderer here, not rewording every handler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelAction:
    """A single offered action.

    ``action_id`` is the stable identifier the handler dispatches on
    (and what Telegram carries as ``callback_data``). ``label`` is the
    human-facing wording, free to change without breaking dispatch.
    """

    action_id: str
    label: str

    def __post_init__(self) -> None:
        if not self.action_id:
            raise ValueError("action_id is required")
        if not self.label:
            raise ValueError("label is required")


@dataclass(frozen=True)
class ChannelReply:
    """A complete, channel-agnostic reply ready to render anywhere."""

    body: str
    continuation: str | None = None
    actions: tuple[ChannelAction, ...] = field(default_factory=tuple)

    def full_text(self) -> str:
        """Body plus continuation, blank-line separated. No action text."""
        parts = [self.body.strip()]
        if self.continuation:
            parts.append(self.continuation.strip())
        return "\n\n".join(part for part in parts if part)


def to_telegram_keyboard(reply: ChannelReply):
    """Render actions as a one-button-per-row Telegram inline keyboard.

    Returns ``None`` when there are no actions so callers can pass it
    straight to ``reply_text(..., reply_markup=...)``. Telegram is
    imported lazily so the WhatsApp/numbered path carries no Telegram
    dependency.
    """
    if not reply.actions:
        return None
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [InlineKeyboardButton(action.label, callback_data=action.action_id)]
        for action in reply.actions
    ]
    return InlineKeyboardMarkup(rows)


def to_telegram_button_rows(reply: ChannelReply) -> list[list[dict[str, str]]]:
    """Render actions as Telegram Bot API compatible inline-keyboard rows.

    This is the dependency-free sibling of :func:`to_telegram_keyboard` for
    Hermes/profile boundaries that need a plain JSON payload instead of a
    ``python-telegram-bot`` object.
    """
    return [
        [{"text": action.label, "callback_data": action.action_id}]
        for action in reply.actions
    ]


def render_numbered(reply: ChannelReply) -> str:
    """Render the reply for a numbered/plain-text channel (e.g. WhatsApp).

    Labels are preserved verbatim so context is never lost when a channel
    cannot show buttons. The trailing hint tells the user how to choose.
    """
    blocks = [reply.full_text()]
    if reply.actions:
        options = "\n".join(
            f"{index}. {action.label}"
            for index, action in enumerate(reply.actions, start=1)
        )
        blocks.append(options)
        blocks.append("Reply with the number of your choice.")
    return "\n\n".join(block for block in blocks if block)


def resolve_numbered_choice(reply: ChannelReply, text: str | None) -> str | None:
    """Map a numbered/plain-text reply back to an ``action_id``.

    Accepts the option number (``"1"``) or a label match (case- and
    emoji-insensitive, exact or containment). Returns ``None`` when the
    text matches no offered action, so the caller can fall back to its
    normal text handling.
    """
    if not reply.actions:
        return None
    normalised = _normalise(text)
    if not normalised:
        return None

    if normalised.isdigit():
        index = int(normalised) - 1
        if 0 <= index < len(reply.actions):
            return reply.actions[index].action_id
        return None

    for action in reply.actions:
        label = _normalise(action.label)
        if normalised == label or normalised in label or label in normalised:
            return action.action_id
    return None


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    stripped = re.sub(r"[^0-9a-z ]+", " ", text.strip().lower())
    return " ".join(stripped.split())
