from unittest.mock import AsyncMock, patch

import pytest

import bot
from bot import (
    AWAIT_FORM_CHOICE,
    AWAIT_GATHERING,
    gather_done_callback,
    handle_callback,
    handle_case_input,
    handle_gathering_input,
)
from tests.bot_simulator import BotSimulator


_FIRST_CASE = (
    "A 62M patient presented to ED resus with chest pain and was diagnosed with STEMI. "
    "I assessed him with the consultant, arranged cath lab referral, and learned about "
    "early escalation in high-risk ACS cases."
)


@pytest.mark.asyncio
async def test_gathering_mode_starts_collection_instead_of_recommending(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(_FIRST_CASE)

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot._process_case_text", new=process_case):
        result = await handle_case_input(update, context)

    assert result == AWAIT_GATHERING
    assert process_case.await_count == 0
    assert context.user_data["gathering_case"]["parts"][0]["text"] == _FIRST_CASE
    assert any("Captured" in message for _, message, _ in sim.messages_sent)


@pytest.mark.asyncio
async def test_gathering_mode_combines_parts_when_user_says_done(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    bot._append_gathering_case(context, "Reflection: I need to escalate ACS cases earlier.", "text")
    update = sim._make_text_update("done")

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot._process_case_text", new=process_case):
        result = await handle_gathering_input(update, context)

    assert result == AWAIT_FORM_CHOICE
    process_case.assert_awaited_once()
    combined_text = process_case.await_args.args[3]
    assert "62M patient presented" in combined_text
    assert "escalate ACS cases earlier" in combined_text
    assert "gathering_case" not in context.user_data


@pytest.mark.asyncio
async def test_gathering_mode_answers_side_question_without_adding_case_detail(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    original_parts = list(context.user_data["gathering_case"]["parts"])
    update = sim._make_text_update("How does this work?")

    result = await handle_gathering_input(update, context)

    assert result == AWAIT_GATHERING
    assert context.user_data["gathering_case"]["parts"] == original_parts
    answered = sim.messages_sent[-1][1]
    assert "collect a case across several messages" in answered
    assert "Nothing goes to Kaizen until you approve it" in answered
    assert "Back to your case" in answered  # continuation keeps user in the filling flow
    lowered = answered.lower()
    assert "dogfood" not in lowered
    assert "vnext" not in lowered
    assert "test bot" not in lowered


@pytest.mark.asyncio
async def test_gathering_mode_portfolio_question_uses_grounded_answer(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    original_parts = list(context.user_data["gathering_case"]["parts"])
    update = sim._make_text_update("Which form would this map to?")

    grounded = AsyncMock(return_value="This fits a CBD based on the clinical reasoning you described.")
    with patch("bot.answer_question", new=grounded):
        result = await handle_gathering_input(update, context)

    assert result == AWAIT_GATHERING
    grounded.assert_awaited_once()
    assert context.user_data["gathering_case"]["parts"] == original_parts
    answered = sim.messages_sent[-1][1]
    assert "This fits a CBD" in answered
    assert "Back to your case" in answered  # continuation returns the user to filling


@pytest.mark.asyncio
async def test_gathering_mode_is_default_without_user_toggle(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    assert bot._gathering_enabled(context) is True


@pytest.mark.asyncio
async def test_user_can_opt_out_of_gathering_mode(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["gathering_mode"] = False
    assert bot._gathering_enabled(context) is False


@pytest.mark.asyncio
async def test_env_var_can_disable_gathering_mode_globally(monkeypatch):
    monkeypatch.setenv("PG_GATHERING_MODE", "off")
    sim = BotSimulator()
    context = sim._make_context()
    assert bot._gathering_enabled(context) is False


@pytest.mark.asyncio
async def test_gathering_reply_offers_done_button(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(_FIRST_CASE)

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot._process_case_text", new=AsyncMock(return_value=AWAIT_FORM_CHOICE)):
        await handle_case_input(update, context)

    assert sim.messages_sent[-1][1] == "📥 Captured. Add anything else before I draft this?"
    assert sim.get_last_buttons() == [
        ("✅ Draft now", "GATHER|done"),
        ("❌ Cancel", "ACTION|cancel"),
    ]


@pytest.mark.asyncio
async def test_gathering_cancel_button_clears_case(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    context.user_data["gathering_msg_id"] = 123
    context.user_data["gathering_chat_id"] = 456
    update = sim._make_callback_update("ACTION|cancel")

    with patch("bot.has_credentials", return_value=True):
        result = await handle_callback(update, context)

    assert result == bot.ConversationHandler.END
    assert "gathering_case" not in context.user_data
    assert "gathering_msg_id" not in context.user_data
    assert "gathering_chat_id" not in context.user_data
    update.callback_query.answer.assert_awaited()
    assert sim.get_last_text().startswith("↩️ Cancelled.")


@pytest.mark.asyncio
async def test_gather_done_callback_finishes_case(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    bot._append_gathering_case(context, _FIRST_CASE, "text")
    update = sim._make_callback_update("GATHER|done")

    process_case = AsyncMock(return_value=AWAIT_FORM_CHOICE)
    with patch("bot._process_case_text", new=process_case):
        result = await gather_done_callback(update, context)

    assert result == AWAIT_FORM_CHOICE
    process_case.assert_awaited_once()
    assert "gathering_case" not in context.user_data
    update.callback_query.answer.assert_awaited()
