"""Deterministic first-contact intent for the channel-neutral inbound boundary.

At first contact a WhatsApp (or any-channel) user may open with a command
(``/start``), a bare greeting (``hi`` / ``hello``), a capability question
(``what can you do``), or an actual clinical case. The Telegram beta bot answers
``/start`` with an onboarding welcome and only asks for a case once the user is
oriented (see ``backend/bot.py`` ``start`` and ``message_policy`` welcome copy).

This module gives the channel-neutral inbound bridge the same first-touch
orientation without an LLM call. It classifies the opening turn and, for the
non-case openings, returns the same ``MessageClass.FIXED`` onboarding copy the
Telegram welcome uses — so a WhatsApp user is greeted and told what Portfolio
Guru does, instead of being told to "describe the clinical case" before they
know what the service is. That removes the "magic sentence" problem where the
only opening that produced a coherent reply was a full clinical case.

Boundary discipline: this module owns no product logic and no I/O. Extraction,
form recommendation, drafting, and Kaizen access all stay behind the bridge. It
only decides, deterministically, whether an opening turn is an onboarding turn
(START/greeting or capability) or a real case turn the engine should handle, and
composes onboarding replies from :mod:`message_policy` templates so the copy
never drifts from the Telegram surface. It is import-clean of
``python-telegram-bot`` so it can run inside a connector-facing process.
"""

from __future__ import annotations

from enum import Enum

from channel_actions import ChannelReply
from conversational_router import ConversationalIntent, route_message
from message_policy import render_message


class FirstContactKind(str, Enum):
    """What an opening turn is, before any workflow runs.

    ``CASE`` is the default and the only kind the deterministic engine handles;
    the other two are answered with FIXED onboarding copy and never touch the
    engine, extraction, or Kaizen.
    """

    START_OR_GREETING = "start_or_greeting"
    CAPABILITY = "capability"
    CASE = "case"


# Bare openings that mean "hello / get me started", never a clinical case. A
# leading slash is stripped first (see :func:`_normalise`) so the Telegram-style
# ``/start`` command and a plain ``start`` resolve identically on a channel such
# as WhatsApp that has no slash-command menu. Matching is exact against the
# normalised text so a real case that merely *begins* with "hi" is never
# swallowed here — it falls through to CASE.
_START_TOKENS: frozenset[str] = frozenset(
    {
        "start",
        "restart",
        "begin",
        "get started",
        "getting started",
        "hi",
        "hii",
        "hiya",
        "hey",
        "heya",
        "hello",
        "hello there",
        "hi there",
        "hey there",
        "yo",
        "good morning",
        "good afternoon",
        "good evening",
    }
)


def _normalise(text: str | None) -> str:
    """Lower-case, strip a single leading slash, and trim edge punctuation.

    Mirrors the light normalisation used elsewhere in the conversation layer so
    ``/start``, ``Start``, ``start.`` and ``hi!`` all collapse to a stable form.
    """
    if not text:
        return ""
    cleaned = text.strip().lower()
    if cleaned.startswith("/"):
        cleaned = cleaned[1:]
    return " ".join(cleaned.strip("?!.,").split())


def _is_capability(normalised: str) -> bool:
    """True when the opening is a help/capability question, not a case.

    Delegates to the existing deterministic router so bare ``help`` / ``features``
    and question-form capability asks ("what can you do?", "how does this work?")
    are recognised without duplicating that logic here.
    """
    return route_message(normalised).intent is ConversationalIntent.HELP_OR_CAPABILITY


def classify_first_contact(text: str | None) -> FirstContactKind:
    """Classify one opening turn — deterministic, no side effects, no LLM.

    An empty/None text (e.g. a media-only turn) is treated as CASE so the engine
    handles it exactly as before; only a bare greeting/start command or a
    capability question is diverted to onboarding.
    """
    normalised = _normalise(text)
    if not normalised:
        return FirstContactKind.CASE
    if normalised in _START_TOKENS:
        return FirstContactKind.START_OR_GREETING
    if _is_capability(normalised):
        return FirstContactKind.CAPABILITY
    return FirstContactKind.CASE


def first_contact_reply(kind: FirstContactKind) -> ChannelReply | None:
    """The onboarding reply for a non-case opening, or ``None`` for a case turn.

    Copy is pulled from the FIXED ``message_policy`` templates the Telegram
    welcome path uses, so the WhatsApp first-touch experience matches the beta
    bot and stays anti-fabrication safe. Returning ``None`` for CASE lets the
    caller fall through to the existing rich-case / gathering routing.
    """
    if kind is FirstContactKind.START_OR_GREETING:
        return ChannelReply(body=render_message("welcome_disconnected"))
    if kind is FirstContactKind.CAPABILITY:
        return ChannelReply(body=render_message("capability_overview"))
    return None
