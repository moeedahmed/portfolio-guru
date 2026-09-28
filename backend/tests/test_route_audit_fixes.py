"""One job, one route: regression tests for the 2026-09-27 route audit.

Each test pins a place where the same user job used to take a different path
depending on how it was reached, or where doing it twice gave a different
result (a second Kaizen draft, a second welcome, a second checkout).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot


def _callback_update(data: str, user_id: int = 4242):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    update.callback_query.message.edit_text = AsyncMock()
    update.callback_query.message.chat_id = user_id
    update.callback_query.message.message_id = 10
    update.message = None
    return update


def _context(user_data: dict | None = None):
    context = MagicMock()
    context.user_data = dict(user_data or {})
    context.bot.send_message = AsyncMock()
    context.bot.edit_message_reply_markup = AsyncMock()
    return context


# --- Payments ---------------------------------------------------------------


async def test_upgrade_button_for_paying_user_opens_no_checkout(monkeypatch):
    monkeypatch.setenv("PG_PAYMENTS_ENABLED", "1")
    update = _callback_update("UPGRADE|pro_plus")
    context = _context()
    checkout = AsyncMock(return_value="https://checkout.example")
    with patch("bot.get_user_tier", AsyncMock(return_value="pro_plus")), \
         patch("stripe_handler.create_checkout_session", checkout), \
         patch("bot._flow_edit", AsyncMock()) as flow_edit:
        await bot.handle_upgrade_button(update, context)

    checkout.assert_not_called()
    assert flow_edit.await_args.args[2] == bot._ALREADY_UNLIMITED_TEXT


async def test_upgrade_button_for_free_user_still_opens_checkout(monkeypatch):
    monkeypatch.setenv("PG_PAYMENTS_ENABLED", "1")
    update = _callback_update("UPGRADE|pro_plus")
    context = _context()
    checkout = AsyncMock(return_value="https://checkout.example")
    with patch("bot.get_user_tier", AsyncMock(return_value="free")), \
         patch("stripe_handler.create_checkout_session", checkout), \
         patch("bot._flow_edit", AsyncMock()):
        await bot.handle_upgrade_button(update, context)

    checkout.assert_awaited_once()


def _start_update(arg: str, user_id: int = 4242):
    update = MagicMock()
    update.update_id = 777
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


@pytest.mark.parametrize("arg,tier,expected", [
    ("upgraded", "pro_plus", bot._PAYMENT_ACTIVE_TEXT),
    ("upgraded", "free", bot._PAYMENT_PENDING_TEXT),
    ("cancelled", "free", bot._PAYMENT_CANCELLED_TEXT),
])
async def test_return_from_payment_keeps_open_draft(arg, tier, expected):
    update = _start_update(arg)
    context = _context({"draft_data": {"form_type": "CBD"}, "case_text": "case"})
    context.args = [arg]
    with patch("bot.get_user_tier", AsyncMock(return_value=tier)):
        state = await bot.start(update, context)

    assert state is None  # stay in the current step
    assert context.user_data["draft_data"] == {"form_type": "CBD"}
    update.message.reply_text.assert_awaited_once_with(expected)


# --- Reset asks first -------------------------------------------------------


async def test_reset_command_asks_before_wiping():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = _context({"draft_data": {"form_type": "CBD"}})
    with patch("bot._perform_reset", AsyncMock()) as perform:
        await bot.reset_data(update, context)

    perform.assert_not_called()
    assert update.message.reply_text.await_args.args[0] == bot._RESET_CONFIRM_TEXT
    assert context.user_data == {"draft_data": {"form_type": "CBD"}}


async def test_keep_data_leaves_open_draft_alone():
    update = _callback_update("CONFIRM|keep")
    context = _context({"draft_data": {"form_type": "CBD"}, "case_text": "case"})
    await bot.handle_reset_keep(update, context)

    assert context.user_data == {"draft_data": {"form_type": "CBD"}, "case_text": "case"}
    update.callback_query.message.edit_text.assert_awaited_once_with(bot._RESET_KEPT_TEXT)


async def test_settings_reset_uses_the_same_question():
    update = _callback_update("ACTION|delete")
    context = _context()
    await bot.handle_action_button(update, context)

    kwargs = update.callback_query.message.edit_text.await_args
    assert kwargs.args[0] == bot._RESET_CONFIRM_TEXT
    buttons = [b.callback_data for row in kwargs.kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["CONFIRM|reset", "CONFIRM|keep"]


# --- No second Kaizen draft -------------------------------------------------

_SNAPSHOT = {
    "last_amend_draft": {"_type": "FORM", "form_type": "DOPS", "fields": {"a": "b"}, "uuid": None},
    "last_amend_case_text": "case",
    "last_amend_chosen_form": "DOPS",
    "last_filing_form_name": "DOPS",
}


def test_clean_partial_save_is_not_retryable():
    context = _context({**_SNAPSHOT, "last_filing_status": "partial", "last_filing_uncertain": False})
    assert not bot._has_retryable_failed_filing_draft(context)
    assert "draft_data" not in context.user_data
    assert bot._last_filing_saved(context)


@pytest.mark.parametrize("status,uncertain", [("failed", False), ("partial", True)])
def test_failed_or_uncertain_save_stays_retryable(status, uncertain):
    context = _context({**_SNAPSHOT, "last_filing_status": status, "last_filing_uncertain": uncertain})
    assert bot._has_retryable_failed_filing_draft(context)


async def test_old_retry_button_after_clean_save_files_nothing():
    update = _callback_update("ACTION|retry_filing")
    context = _context({**_SNAPSHOT, "last_filing_status": "partial", "last_filing_uncertain": False})
    with patch("bot.handle_approval_approve", AsyncMock()) as approve:
        await bot.handle_callback(update, context)

    approve.assert_not_called()
    update.callback_query.message.reply_text.assert_awaited_once_with(bot._already_saved_text(context))


def test_amend_reopens_the_saved_draft_and_retry_its_own_attempt():
    context = _context({"amend_mode": True, "amend_draft_url": "https://kaizenep.com/events/fillin/1",
                        "kaizen_draft_url": "https://kaizenep.com/events/fillin/2"})
    assert bot._draft_url_to_reuse(context, retrying=False) == "https://kaizenep.com/events/fillin/1"
    assert bot._draft_url_to_reuse(context, retrying=True) == "https://kaizenep.com/events/fillin/2"
    assert bot._draft_url_to_reuse(_context(), retrying=False) is None


def test_timeout_and_crash_share_one_check_drafts_message():
    keyboard = bot._build_uncertain_filing_keyboard()
    urls = [b.url for row in keyboard.inline_keyboard for b in row if b.url]
    assert urls == ["https://kaizenep.com/activities"]
    assert "Check your Kaizen drafts first" in bot._FILING_UNCERTAIN_TEXT


# --- Old buttons ------------------------------------------------------------


async def test_old_another_form_button_keeps_a_newer_open_case():
    update = _callback_update("ACTION|same_case_another")
    context = _context({"case_text": "newer case", "last_filed_case_text": "filed case"})
    with patch("bot._resume_paused_flow", AsyncMock(return_value=bot.AWAIT_FORM_CHOICE)) as resume, \
         patch("bot._process_case_text", AsyncMock()) as process:
        await bot.handle_same_case_another(update, context)

    resume.assert_awaited_once()
    process.assert_not_called()
    assert context.user_data["case_text"] == "newer case"


async def test_retry_suggestions_retires_button_and_keeps_input_source():
    update = _callback_update("ACTION|retry_recommend")
    context = _context({"case_text": "case", "case_input_source": "voice"})
    with patch("bot._retire_clicked_keyboard", AsyncMock()) as retire, \
         patch("bot._process_case_text", AsyncMock(return_value=bot.AWAIT_FORM_CHOICE)) as process:
        await bot.handle_callback(update, context)

    retire.assert_awaited_once()
    assert process.await_args.args[4] == "voice"
