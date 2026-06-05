"""Every live free-form bot answer must lead with the house emoji.

The conversation supervisor side-question path was fixed earlier, but the
live Telegram handlers in :mod:`bot` answer questions through direct
``answer_question`` calls. Those replies reached the user as bare LLM
prose with no leading emoji, breaking the Portfolio Guru message standard.
These tests pin every such handler path to the house emoji helper.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bot
from message_policy import HOUSE_EMOJI
from tests.bot_simulator import BotSimulator


def _last_text(sim: BotSimulator) -> str:
    return sim.messages_sent[-1][1]


@pytest.mark.asyncio
async def test_case_input_question_answer_leads_with_house_emoji():
    # Fresh question before any case (the capability/initial-prompt path in
    # the screenshot) must not arrive as bare prose.
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("Which forms do you support?")

    grounded = AsyncMock(return_value="I support 45 RCEM forms.")
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot.answer_question", new=grounded):
        await bot.handle_case_input(update, context)

    grounded.assert_awaited_once()
    text = _last_text(sim)
    assert text.startswith(f"{HOUSE_EMOJI} ")
    assert "I support 45 RCEM forms." in text


@pytest.mark.asyncio
async def test_template_review_question_answer_leads_with_house_emoji():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = "62M chest pain, STEMI."
    context.user_data["chosen_form"] = "CBD"
    update = sim._make_text_update("What does a CBD assess?")

    grounded = AsyncMock(return_value="A CBD assesses your clinical reasoning.")
    with patch("bot.classify_intent", new=AsyncMock(return_value="question_general")), \
         patch("bot.answer_question", new=grounded):
        await bot.handle_template_review_text(update, context)

    grounded.assert_awaited_once()
    text = _last_text(sim)
    assert text.startswith(f"{HOUSE_EMOJI} ")
    assert "A CBD assesses your clinical reasoning." in text
    # The continuation that keeps the case open is preserved.
    assert "still open" in text


@pytest.mark.asyncio
async def test_edit_value_question_answer_leads_with_house_emoji():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["edit_field"] = "diagnosis"
    context.user_data["case_text"] = "62M chest pain."
    update = sim._make_text_update("Is CBD supported?")

    grounded = AsyncMock(return_value="Yes, that is supported.")
    with patch("bot.classify_intent", new=AsyncMock(return_value="question_general")), \
         patch("bot.answer_question", new=grounded):
        await bot.handle_edit_value_with_intent(update, context)

    grounded.assert_awaited_once()
    text = _last_text(sim)
    assert text.startswith(f"{HOUSE_EMOJI} ")
    assert "Yes, that is supported." in text
    # The edit-mode continuation is preserved.
    assert "edit mode" in text


@pytest.mark.asyncio
async def test_mid_conversation_question_answer_leads_with_house_emoji():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("What forms do you support?")

    grounded = AsyncMock(return_value="I support 45 RCEM forms.")
    with patch("bot.classify_intent", new=AsyncMock(return_value="question_general")), \
         patch("bot.answer_question", new=grounded):
        await bot.handle_mid_conversation_text(update, context)

    grounded.assert_awaited_once()
    text = _last_text(sim)
    assert text.startswith(f"{HOUSE_EMOJI} ")
    assert "I support 45 RCEM forms." in text


@pytest.mark.asyncio
async def test_answer_already_leading_with_emoji_is_not_double_prefixed():
    # A grounded answer that already leads with an emoji must pass through
    # unchanged, never gaining a second marker.
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("Which forms do you support?")

    grounded = AsyncMock(return_value="📋 I support 45 RCEM forms.")
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot.answer_question", new=grounded):
        await bot.handle_case_input(update, context)

    text = _last_text(sim)
    assert text.startswith("📋 I support 45 RCEM forms.")
    assert HOUSE_EMOJI not in text
