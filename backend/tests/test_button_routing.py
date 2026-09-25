"""Every tap and message the bot can receive reaches a handler that answers it.

Demos broke on taps that matched no handler (Telegram's spinner, then nothing),
on Retry being taken by a global handler that threw the case state away, on
side flows swallowing the next case, and on old Save/Cancel buttons acting on
the live case. These tests drive the production handler registration.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message, Update, Voice
from telegram.ext import ConversationHandler

import bot
from tests.helpers import TEST_CHAT, TEST_USER, build_offline_application, make_callback_update, make_text_update
from tests.whole_bot_catalogue import CALLBACK_BRANCHES


@pytest.fixture(scope="module")
def app():
    return build_offline_application()


def _conversation(app, name):
    for handler in app.handlers[0]:
        if isinstance(handler, ConversationHandler) and handler.name == name:
            return handler
    raise LookupError(name)


def _route(app, update, *, case_state=None):
    """(conversation name, callback name) PTB would pick for this update in group 0."""
    for handler in app.handlers[0]:
        if isinstance(handler, ConversationHandler):
            handler._conversations.clear()
    if case_state is not None:
        _conversation(app, "case_conv")._conversations[(TEST_USER.id, TEST_USER.id)] = case_state
    for handler in app.handlers[0]:
        result = handler.check_update(update)
        if result in (None, False):
            continue
        if isinstance(handler, ConversationHandler):
            inner = result[2] if isinstance(result, tuple) and len(result) > 2 else None
            return handler.name, getattr(getattr(inner, "callback", None), "__name__", None)
        return None, handler.callback.__name__
    return None, None


def _voice_update():
    message = Message(
        message_id=7, date=datetime.now(timezone.utc), chat=TEST_CHAT, from_user=TEST_USER,
        voice=Voice(file_id="f", file_unique_id="u", duration=2),
    )
    return Update(update_id=7, message=message)


def test_retry_stays_inside_the_case_conversation(app):
    update = make_callback_update("ACTION|retry_filing")
    assert _route(app, update, case_state=bot.AWAIT_APPROVAL)[0] == "case_conv"
    assert _route(app, update)[0] == "case_conv", "an idle Retry must re-enter the case flow"


def test_passwordless_reconnect_stays_inside_the_case_conversation(app):
    update = make_callback_update("ACTION|pwl_reconnected")
    assert _route(app, update) == ("case_conv", "passwordless_reconnected")
    assert _route(app, update, case_state=bot.AWAIT_APPROVAL) == ("case_conv", "passwordless_reconnected")


@pytest.mark.parametrize("branch", sorted(CALLBACK_BRANCHES))
def test_no_known_button_is_silently_dropped_when_idle(app, branch):
    data = branch.replace("*", "2025" if branch.startswith("FILING_CURRICULUM|") else "aaaaaaaa")
    assert _route(app, make_callback_update(data)) != (None, None)


def test_an_unknown_tap_is_answered(app):
    assert _route(app, make_callback_update("NOT_A_BUTTON|x")) == (None, "_answer_unhandled_button")


def test_input_a_step_cannot_use_gets_a_reply(app):
    assert _route(app, make_text_update("I'm an ST4"), case_state=bot.AWAIT_TRAINING_LEVEL) == (
        "case_conv", "_reply_use_current_step")
    assert _route(app, _voice_update(), case_state=bot.AWAIT_EDIT_FIELD) == (
        "case_conv", "_reply_use_current_step")


def test_side_flows_time_out(app):
    side_flows = [h for h in app.handlers[0] if isinstance(h, ConversationHandler) and h.name != "case_conv"]
    assert len(side_flows) == 3
    assert all(h.conversation_timeout == bot.SIDE_FLOW_TIMEOUT for h in side_flows)


CASE = "45M with pleuritic chest pain, D-dimer raised, CTPA showed a segmental PE and I started apixaban"


@pytest.mark.asyncio
async def test_a_case_pasted_during_setup_is_not_taken_as_the_email():
    update = make_text_update(CASE)
    context = MagicMock(user_data={"_setup_state_hint": "username"})
    with patch.object(Message, "reply_text", new=AsyncMock()) as reply:
        assert await bot.setup_username(update, context) == ConversationHandler.END
    assert "looks like a case" in reply.call_args.args[0]


@pytest.mark.asyncio
async def test_a_case_pasted_during_setup_is_never_tried_as_the_password():
    update = make_text_update(CASE)
    context = MagicMock(user_data={"setup_username": "doc@example.nhs.uk", "_setup_state_hint": "password"})
    with patch.object(Message, "reply_text", new=AsyncMock()), \
         patch.object(Message, "delete", new=AsyncMock()) as delete, \
         patch.object(bot, "_test_kaizen_login", new=AsyncMock()) as login:
        assert await bot.setup_password(update, context) == ConversationHandler.END
    login.assert_not_awaited()
    delete.assert_not_awaited()
    assert "setup_username" not in context.user_data


def test_a_password_with_spaces_is_still_a_password():
    assert not bot._looks_like_case_not_credential("correct horse battery staple", min_words=12, min_chars=80)


def _button_data(keyboard):
    return {b.callback_data for row in keyboard.inline_keyboard for b in row}


def test_save_and_cancel_buttons_carry_the_case_token():
    context = MagicMock(user_data={})
    data = _button_data(bot._build_approval_keyboard(context=context))
    token = context.user_data["case_token"]
    assert data == {f"APPROVE|draft|{token}", f"CANCEL|draft|{token}"}


def test_buttons_from_before_stamping_still_work():
    context = MagicMock(user_data={"case_token": "abc123"})
    assert not bot._is_stale_case_button(context, "APPROVE|draft")
    assert not bot._is_stale_case_button(context, "CANCEL|draft")
    assert not bot._is_stale_case_button(context, "CANCEL|draft|abc123")
    assert bot._is_stale_case_button(context, "CANCEL|draft|0ld0ld")


@pytest.mark.asyncio
async def test_a_second_tap_on_the_same_save_button_does_not_file_again():
    context = MagicMock(user_data={})
    save = next(d for d in _button_data(bot._build_approval_keyboard(context=context)) if d.startswith("APPROVE"))

    with patch.object(bot, "get_credentials", side_effect=RuntimeError("stop after the guard")), \
         patch("telegram.CallbackQuery.answer", new=AsyncMock()) as answer, \
         patch("telegram.CallbackQuery.edit_message_reply_markup", new=AsyncMock()):
        with pytest.raises(RuntimeError):
            await bot.handle_approval_approve(make_callback_update(save), context)
        context.user_data.pop("filing_in_progress", None)  # the first attempt failed and released
        assert await bot.handle_approval_approve(make_callback_update(save), context) is None
    assert "earlier step" in answer.call_args.args[0]


@pytest.mark.asyncio
async def test_an_old_cancel_does_not_wipe_the_live_case():
    context = MagicMock(user_data={"case_token": "live01", "case_text": CASE})
    with patch("telegram.CallbackQuery.answer", new=AsyncMock()):
        assert await bot.handle_callback(make_callback_update("CANCEL|draft|0ld0ld"), context) is None
    assert context.user_data["case_text"] == CASE
