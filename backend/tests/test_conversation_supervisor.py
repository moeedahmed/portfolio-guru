"""The single gathering-turn control loop.

These tests pin the behaviour the supervisor must guarantee: live
dogfood/test-bot copy can never leak, genuine portfolio/account/setup
questions go through the injected grounded answer path and always carry a
continuation back to the case, and the canonical intent + turn kind are
decided without any I/O.
"""

from unittest.mock import AsyncMock

import pytest

from conversation_supervisor import (
    DRAFT_NOW_ACTION,
    GatheringTurnKind,
    classify_gathering_turn,
    decide_gathering_turn,
)
from conversational_router import ConversationalIntent


async def _unused_answer(_: str) -> str:
    raise AssertionError("answer_question must not be called for this turn kind")


@pytest.mark.parametrize(
    "text",
    ["done", "that's all", "draft it", "show me the draft", "file this", "save draft"],
)
def test_completion_phrases_finish_the_case(text):
    kind, intent = classify_gathering_turn(text)
    assert kind is GatheringTurnKind.FINISH_CASE
    assert intent is ConversationalIntent.FILE_TO_KAIZEN


@pytest.mark.parametrize("text", ["hi", "hello", "hey"])
def test_greetings_are_capability_turns(text):
    kind, _ = classify_gathering_turn(text)
    assert kind is GatheringTurnKind.ANSWER_CAPABILITY


@pytest.mark.parametrize(
    "text", ["help", "what can you do", "how does this work", "features"]
)
def test_capability_phrases_are_capability_turns(text):
    kind, _ = classify_gathering_turn(text)
    assert kind is GatheringTurnKind.ANSWER_CAPABILITY


def test_portfolio_question_is_a_side_question():
    kind, intent = classify_gathering_turn("Which form would this map to?")
    assert kind is GatheringTurnKind.ANSWER_SIDE_QUESTION
    assert intent is ConversationalIntent.PORTFOLIO_QUESTION


def test_billing_question_is_a_side_question():
    kind, intent = classify_gathering_turn("How much does the paid plan cost?")
    assert kind is GatheringTurnKind.ANSWER_SIDE_QUESTION
    assert intent is ConversationalIntent.ACCOUNT_OR_BILLING


def test_plain_case_detail_continues_gathering():
    kind, _ = classify_gathering_turn(
        "He also became hypotensive so we started a noradrenaline infusion."
    )
    assert kind is GatheringTurnKind.CONTINUE_GATHERING


@pytest.mark.asyncio
async def test_finish_decision_defers_to_caller():
    decision = await decide_gathering_turn("done", answer_question=_unused_answer)
    assert decision.kind is GatheringTurnKind.FINISH_CASE
    assert decision.add_to_case is False
    assert decision.reply is None


@pytest.mark.asyncio
async def test_continue_decision_adds_to_case_and_offers_draft_now():
    decision = await decide_gathering_turn(
        "Then the patient was admitted to ICU.", answer_question=_unused_answer
    )
    assert decision.kind is GatheringTurnKind.CONTINUE_GATHERING
    assert decision.add_to_case is True
    assert decision.reply is not None
    assert decision.reply.actions == (DRAFT_NOW_ACTION,)


@pytest.mark.asyncio
async def test_capability_decision_is_templated_and_carries_continuation():
    decision = await decide_gathering_turn("what can you do", answer_question=_unused_answer)
    assert decision.kind is GatheringTurnKind.ANSWER_CAPABILITY
    assert decision.add_to_case is False
    text = decision.reply.full_text()
    assert "Nothing goes to Kaizen until you approve it" in text
    assert "Back to your case" in text


@pytest.mark.asyncio
async def test_side_question_uses_injected_grounded_answer():
    grounded = AsyncMock(return_value="That maps best to a CBD.")
    decision = await decide_gathering_turn(
        "Which form would this map to?", answer_question=grounded
    )
    grounded.assert_awaited_once_with("Which form would this map to?")
    assert decision.kind is GatheringTurnKind.ANSWER_SIDE_QUESTION
    assert decision.add_to_case is False
    text = decision.reply.full_text()
    assert "That maps best to a CBD." in text
    assert "Back to your case" in text  # never strands the user outside filling


@pytest.mark.asyncio
async def test_side_question_falls_back_to_capability_copy_on_error():
    failing = AsyncMock(side_effect=RuntimeError("LLM down"))
    decision = await decide_gathering_turn(
        "Which form would this map to?", answer_question=failing
    )
    assert decision.kind is GatheringTurnKind.ANSWER_SIDE_QUESTION
    text = decision.reply.full_text()
    assert "Portfolio Guru" in text
    assert "Back to your case" in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["hi", "what can you do", "Which form would this map to?", "Then we did an X-ray."],
)
async def test_no_dogfood_copy_can_leak(text):
    async def answer(_: str) -> str:
        return "Grounded answer."

    decision = await decide_gathering_turn(text, answer_question=answer)
    rendered = (decision.reply.full_text() if decision.reply else "").lower()
    assert "dogfood" not in rendered
    assert "vnext" not in rendered
    assert "test bot" not in rendered
    assert "private" not in rendered
