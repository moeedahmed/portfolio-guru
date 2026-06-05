"""Conversation supervisor — the single gathering-turn control loop.

While the bot is gathering a case across several messages, every extra
user message is one of a few things: a request to draft now, a greeting
or capability question, a genuine portfolio/account/setup question, or
more case detail. Historically that decision was spread across a regex
router, an LLM intent call, an ad-hoc heuristic, and a dogfood reply
helper, each with its own copy.

This module consolidates that into one decision. It separates the
concerns the bot kept tangling together:

* **canonical intent** — from :func:`conversational_router.route_message`
  (channel-agnostic, no prose);
* **turn kind** — what the workflow should do next
  (:class:`GatheringTurnKind`);
* **response body + continuation + actions** — assembled as a
  channel-agnostic :class:`channel_actions.ChannelReply`, never as
  Telegram-specific text.

It owns no I/O. Grounded answers come from an injected ``answer_question``
callable, so the supervisor stays free of LLM and Telegram coupling and
is cheap to test. Copy comes from :mod:`message_policy`, so capability,
identity, and continuation wording live in one auditable place — and the
old "private vNext test bot / dogfood" copy can never leak through here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from channel_actions import ChannelAction, ChannelReply
from conversational_router import ConversationalIntent, route_message
from message_policy import render_message
from vnext_dialogue_policy import is_completion_request

# Stable id mirrors the existing Telegram callback (``GATHER|done``) so the
# inline button and a WhatsApp "reply 1 / draft now" resolve to one action.
DRAFT_NOW_ACTION = ChannelAction(action_id="GATHER|done", label="✅ Draft now")

AnswerFn = Callable[[str], Awaitable[str]]


class GatheringTurnKind(str, Enum):
    FINISH_CASE = "finish_case"
    ANSWER_CAPABILITY = "answer_capability"
    ANSWER_SIDE_QUESTION = "answer_side_question"
    CONTINUE_GATHERING = "continue_gathering"


@dataclass(frozen=True)
class GatheringDecision:
    """What to do with one gathering-mode turn.

    ``reply`` is ``None`` only for :attr:`GatheringTurnKind.FINISH_CASE`,
    where the caller drives the existing deterministic finish/draft path.
    ``add_to_case`` is ``True`` only when the message is real case detail
    that should be appended to the workspace.
    """

    kind: GatheringTurnKind
    intent: ConversationalIntent
    add_to_case: bool
    reply: ChannelReply | None


_GREETINGS: frozenset[str] = frozenset({"hi", "hello", "hey"})
_CAPABILITY_PHRASES: frozenset[str] = frozenset(
    {"help", "how does this work", "what can you do", "what do you do", "features", "feature"}
)
_SIDE_QUESTION_INTENTS: frozenset[ConversationalIntent] = frozenset(
    {
        ConversationalIntent.PORTFOLIO_QUESTION,
        ConversationalIntent.ACCOUNT_OR_BILLING,
        ConversationalIntent.SETUP_OR_CREDENTIALS,
    }
)


def classify_gathering_turn(
    text: str | None,
) -> tuple[GatheringTurnKind, ConversationalIntent]:
    """Decide the turn kind and canonical intent without side effects."""
    if is_completion_request(text):
        return GatheringTurnKind.FINISH_CASE, ConversationalIntent.FILE_TO_KAIZEN

    intent = route_message(text or "").intent
    if intent is ConversationalIntent.FILE_TO_KAIZEN:
        return GatheringTurnKind.FINISH_CASE, intent

    normalised = _normalise(text)
    if normalised in _GREETINGS or normalised in _CAPABILITY_PHRASES:
        return GatheringTurnKind.ANSWER_CAPABILITY, intent

    if intent in _SIDE_QUESTION_INTENTS:
        return GatheringTurnKind.ANSWER_SIDE_QUESTION, intent

    return GatheringTurnKind.CONTINUE_GATHERING, intent


async def decide_gathering_turn(
    text: str | None,
    *,
    answer_question: AnswerFn,
    actions: tuple[ChannelAction, ...] = (DRAFT_NOW_ACTION,),
) -> GatheringDecision:
    """Resolve one gathering turn into a channel-agnostic decision.

    Genuine portfolio/account/setup questions are answered through the
    grounded ``answer_question`` callable and always carry a continuation
    line back to the case, so a side question never strands the user
    outside the filling workflow. Capability/greeting copy is templated
    (deterministic, no LLM). Anything else is treated as case detail.
    """
    kind, intent = classify_gathering_turn(text)

    if kind is GatheringTurnKind.FINISH_CASE:
        return GatheringDecision(kind=kind, intent=intent, add_to_case=False, reply=None)

    if kind is GatheringTurnKind.CONTINUE_GATHERING:
        return GatheringDecision(
            kind=kind,
            intent=intent,
            add_to_case=True,
            reply=ChannelReply(body=render_message("gathering_captured"), actions=actions),
        )

    continuation = render_message("gathering_continuation")

    if kind is GatheringTurnKind.ANSWER_CAPABILITY:
        copy_key = "greeting_reply" if _normalise(text) in _GREETINGS else "capability_overview"
        return GatheringDecision(
            kind=kind,
            intent=intent,
            add_to_case=False,
            reply=ChannelReply(
                body=render_message(copy_key), continuation=continuation, actions=actions
            ),
        )

    # ANSWER_SIDE_QUESTION — grounded answer, then back to the case.
    try:
        body = (await answer_question(text or "")).strip()
    except Exception:
        body = render_message("capability_overview")
    if not body:
        body = render_message("capability_overview")

    return GatheringDecision(
        kind=kind,
        intent=intent,
        add_to_case=False,
        reply=ChannelReply(body=body, continuation=continuation, actions=actions),
    )


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.strip().lower().strip("?!.,").split())
