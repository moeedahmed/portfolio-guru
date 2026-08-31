from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message
from telegram.ext import ConversationHandler

import bot
from tests.bot_simulator import BotSimulator
from tests.helpers import (
    BOT_USER,
    TEST_USER,
    make_callback_update,
    make_command_update,
    make_text_update,
)
from tests.test_e2e_offline import _prepare_update


@pytest.mark.asyncio
async def test_disconnected_email_text_starts_kaizen_password_step():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("doctor@example.com")

    with patch("bot.has_credentials", return_value=False):
        result = await bot.handle_case_input(update, context)

    assert result == bot.AWAIT_PASSWORD
    assert context.user_data["setup_username"] == "doctor@example.com"
    assert context.user_data["_setup_state_hint"] == "password"
    assert "Kaizen password" in sim.get_last_text()


@pytest.mark.asyncio
async def test_disconnected_reconnect_sentence_extracts_email():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update("Please reconnect my Kaizen email doctor@example.com")

    with patch("bot.has_credentials", return_value=False):
        result = await bot.handle_case_input(update, context)

    assert result == bot.AWAIT_PASSWORD
    assert context.user_data["setup_username"] == "doctor@example.com"


@pytest.mark.asyncio
async def test_disconnected_clinical_case_goes_straight_to_username_step():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_text_update(
        "Adult ED resus case: assessed chest pain, escalated to cardiology, "
        "documented ECG findings and reflected on earlier senior review."
    )

    process_case = AsyncMock()
    with patch("bot.has_credentials", return_value=False), \
         patch("bot._process_case_text", new=process_case):
        result = await bot.handle_case_input(update, context)

    assert result == bot.AWAIT_USERNAME
    process_case.assert_not_awaited()
    assert "Before I can save drafts to Kaizen" in sim.get_last_text()
    assert "Send your Kaizen username or email" in sim.get_last_text()
    # The forced connection prompt offers no Cancel: entering a username/email
    # is the only usable path for a disconnected user, so a Cancel button would
    # just loop back to the same state.
    assert sim.get_last_buttons() == []


@pytest.mark.asyncio
async def test_connected_case_text_still_uses_case_flow(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["gathering_mode"] = False
    update = sim._make_text_update(
        "Adult ED resus case: assessed chest pain, escalated to cardiology, "
        "documented ECG findings and reflected on earlier senior review."
    )

    process_case = AsyncMock(return_value=bot.AWAIT_FORM_CHOICE)
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot._process_case_text", new=process_case):
        result = await bot.handle_case_input(update, context)

    assert result == bot.AWAIT_FORM_CHOICE
    process_case.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_then_explicit_setup_cannot_leave_case_username_state(
    monkeypatch, tmp_path
):
    """The real PTB stack must end case_conv before setup_conv reconnects."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:FAKE")
    real_expanduser = bot.os.path.expanduser
    monkeypatch.setattr(
        bot.os.path,
        "expanduser",
        lambda path: str(tmp_path / "bot_persistence")
        if path.endswith("/bot_persistence")
        else real_expanduser(path),
    )

    connected = False
    handled_case_texts = []
    setup_username_texts = []
    real_handle_case_input = bot.handle_case_input
    real_setup_username = bot.setup_username

    async def tracked_handle_case_input(update, context):
        handled_case_texts.append(update.message.text)
        return await real_handle_case_input(update, context)

    async def tracked_setup_username(update, context):
        setup_username_texts.append(update.message.text)
        return await real_setup_username(update, context)

    async def successful_setup_password(update, context):
        nonlocal connected
        connected = True
        context.user_data.pop("setup_username", None)
        context.user_data.pop("_setup_state_hint", None)
        return ConversationHandler.END

    process_case = AsyncMock(return_value=bot.AWAIT_FORM_CHOICE)
    monkeypatch.setattr(bot, "has_credentials", lambda _user_id: connected)
    monkeypatch.setattr(bot, "handle_case_input", tracked_handle_case_input)
    monkeypatch.setattr(bot, "setup_username", tracked_setup_username)
    monkeypatch.setattr(bot, "setup_password", successful_setup_password)
    monkeypatch.setattr(bot, "check_can_file", AsyncMock(return_value=(True, 0, 10, "free")))
    monkeypatch.setattr(bot, "_process_case_text", process_case)

    app = bot.build_application()
    sent_texts = []
    real_bot = app.bot
    real_bot._unfreeze()
    real_bot._bot_user = BOT_USER
    real_bot._bot_initialized = True
    real_bot._requests_initialized = True
    bot_cls = type(real_bot)

    async def fake_send_message(_self, chat_id=None, text="", **_kwargs):
        sent_texts.append(text)
        message = MagicMock(spec=Message)
        message.message_id = 5000 + len(sent_texts)
        message.chat_id = chat_id
        message.text = text
        return message

    async def fake_edit_message_text(_self, text="", **_kwargs):
        sent_texts.append(text)
        return True

    monkeypatch.setattr(bot_cls, "send_message", fake_send_message)
    monkeypatch.setattr(bot_cls, "edit_message_text", fake_edit_message_text)
    monkeypatch.setattr(bot_cls, "answer_callback_query", AsyncMock(return_value=True))
    monkeypatch.setattr(bot_cls, "edit_message_reply_markup", AsyncMock(return_value=True))
    monkeypatch.setattr(bot_cls, "delete_message", AsyncMock(return_value=True))
    monkeypatch.setattr(bot_cls, "send_chat_action", AsyncMock(return_value=True))

    await app.initialize()
    try:
        case_conv = next(
            handler
            for handlers in app.handlers.values()
            for handler in handlers
            if isinstance(handler, ConversationHandler) and handler.name == "case_conv"
        )

        initial_case = make_text_update("Adult ED chest pain case needing Kaizen filing")
        _prepare_update(initial_case, app.bot)
        await app.process_update(initial_case)
        case_key = case_conv._get_key(initial_case)
        assert case_conv._conversations[case_key] == bot.AWAIT_USERNAME

        sent_texts.clear()
        cancel = make_command_update("cancel")
        _prepare_update(cancel, app.bot)
        await app.process_update(cancel)
        assert sent_texts == [bot._cancelled_next_step_text(TEST_USER.id)]
        assert case_key not in case_conv._conversations

        setup = make_callback_update("ACTION|setup")
        _prepare_update(setup, app.bot)
        await app.process_update(setup)

        username = make_text_update("doctor@example.com")
        _prepare_update(username, app.bot)
        await app.process_update(username)

        password = make_text_update("test-password")
        _prepare_update(password, app.bot)
        await app.process_update(password)
        assert connected is True
        assert case_key not in case_conv._conversations

        next_case_text = "Adult ED sepsis case with escalation and reflection"
        next_case = make_text_update(next_case_text)
        _prepare_update(next_case, app.bot)
        await app.process_update(next_case)

        assert next_case_text in handled_case_texts
        assert next_case_text not in setup_username_texts
        process_case.assert_awaited_once()
    finally:
        await app.shutdown()
