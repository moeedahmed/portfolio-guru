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


def test_edited_failed_draft_keeps_the_attempt_url():
    url = "https://kaizenep.com/events/fillin/synthetic"
    context = _context({"last_filing_status": "failed", "kaizen_draft_url": url,
                        "draft_data": _SNAPSHOT["last_amend_draft"]})
    bot._store_draft(context, bot.FormDraft(form_type="DOPS", fields={"summary": "synthetic correction"}))
    assert bot._draft_url_to_reuse(context, retrying=False) == url


def test_typed_save_of_failed_attempt_reuses_its_url():
    url = "https://kaizenep.com/events/fillin/synthetic"
    context = _context({"last_filing_status": "failed", "kaizen_draft_url": url})
    assert bot._draft_url_to_reuse(context, retrying=False) == url


def test_queue_keeps_a_legacy_single_document_before_new_files():
    first = {"path": "/synthetic/first.pdf", "name": "first.pdf"}
    second = {"path": "/synthetic/second.pdf", "name": "second.pdf"}
    context = _context({"_pending_doc": first})
    assert bot._queue_pending_media(context, second) == 2
    assert bot._pending_media_items(context) == [first, second]


async def test_field_edit_passes_the_selected_field_to_regeneration():
    context = _context({**_SNAPSHOT, "draft_data": _SNAPSHOT["last_amend_draft"],
                        "case_text": "synthetic case", "edit_field": "diagnosis"})
    update = MagicMock()
    update.message.text = "synthetic correction"
    update.message.voice = update.message.audio = None
    update.message.photo = []
    update.message.reply_text = AsyncMock()
    with patch("bot._regenerate_active_draft_with_feedback", AsyncMock(return_value=bot.AWAIT_APPROVAL)) as regenerate:
        await bot.handle_edit_value(update, context)
    assert "diagnosis" in regenerate.await_args.args[2].lower()
    assert "synthetic correction" in regenerate.await_args.args[2]
    assert "edit_field" not in context.user_data


async def test_same_case_another_obeys_usage_gate_without_losing_case():
    context = _context({"last_filed_case_text": "synthetic case", "last_filed_form_type": "DOPS"})
    before = dict(context.user_data)
    with patch("bot.check_can_file", AsyncMock(return_value=(False, 5, 5, "free"))), \
         patch("bot._process_case_text", AsyncMock()) as process:
        await bot.handle_same_case_another(_callback_update("ACTION|same_case_another"), context)
    process.assert_not_awaited()
    assert context.user_data == before


async def test_resume_amend_keeps_amend_cancel_button():
    context = _context({"draft_data": _SNAPSHOT["last_amend_draft"], "case_text": "synthetic case",
                        "amend_mode": True})
    with patch("bot._setup_needs_finishing", return_value=False), \
         patch("bot._format_draft_preview_for_context", return_value="synthetic preview"), \
         patch("bot._send_latest_message", AsyncMock()) as send:
        await bot._resume_paused_flow(_callback_update("ACTION|status"), context, "Resume")
    keyboard = send.await_args.kwargs["reply_markup"]
    assert "AMEND|cancel" in [button.callback_data for row in keyboard.inline_keyboard for button in row]


@pytest.mark.parametrize("data", ["ACTION|retry_filing|aaaaaa", "ACTION|retry_filing"])
async def test_old_retry_cannot_approve_a_newer_failed_case(data):
    context = _context({**_SNAPSHOT, "last_filing_status": "failed", "case_token": "bbbbbb"})
    with patch("bot.handle_approval_approve", AsyncMock()) as approve:
        await bot.handle_callback(_callback_update(data), context)
    approve.assert_not_awaited()
    assert "retry_filing_requested" not in context.user_data


async def test_current_stamped_retry_reaches_the_existing_approval_gate():
    context = _context({**_SNAPSHOT, "last_filing_status": "failed", "case_token": "bbbbbb"})
    with patch("bot.handle_approval_approve", AsyncMock()) as approve:
        await bot.handle_callback(_callback_update("ACTION|retry_filing|bbbbbb"), context)
    approve.assert_awaited_once()


def test_new_case_cleanup_cannot_reuse_a_previous_failed_url():
    context = _context({"last_filing_status": "failed", "kaizen_draft_url": "https://kaizenep.com/events/fillin/old"})
    bot._clear_case_review_state(context, keep_case=False)
    bot._store_draft(context, bot.FormDraft(form_type="DOPS", fields={"summary": "new synthetic case"}))
    assert bot._draft_url_to_reuse(context, retrying=False) is None


def test_switching_form_after_failure_does_not_reopen_the_wrong_form():
    context = _context({"last_filing_status": "failed", "kaizen_draft_url": "https://kaizenep.com/events/fillin/old",
                        "draft_data": _SNAPSHOT["last_amend_draft"]})
    bot._store_draft(context, bot.FormDraft(form_type="MINI_CEX", fields={"summary": "synthetic new form"}))
    assert bot._draft_url_to_reuse(context, retrying=False) is None


def test_production_retry_routes_accept_stamps_and_keep_dispatch_in_case_conversation():
    from tests.helpers import build_offline_application
    from telegram.ext import ConversationHandler, CallbackQueryHandler
    app = build_offline_application()
    case = next(handler for handler in app.handlers[0]
                if isinstance(handler, ConversationHandler) and handler.name == "case_conv")
    for handlers in (case.entry_points, case.states[bot.AWAIT_APPROVAL], case.fallbacks):
        assert any(isinstance(handler, CallbackQueryHandler) and handler.callback is bot.handle_callback
                   and handler.pattern.match("ACTION|retry_filing|abcdef") for handler in handlers)
    generic = next(handler for handler in app.handlers[0] if handler.callback is bot.handle_action_button)
    assert not generic.pattern.match("ACTION|retry_filing|abcdef")


async def test_cancel_preserves_the_doctors_gathering_preference():
    context = _context({"gathering_mode": False, "case_text": "synthetic"})
    with patch("bot._cancelled_next_step_text", return_value="Cancelled"), \
         patch("bot._build_next_step_keyboard", return_value=None):
        await bot.handle_action_button(_callback_update("ACTION|cancel"), context)
    assert context.user_data["gathering_mode"] is False
    assert "case_text" not in context.user_data


async def test_old_form_list_cannot_replace_newer_case_choice():
    context = _context({"case_text": "new synthetic case", "last_bot_msg_id": 20, "last_bot_chat_id": 4242})
    before = dict(context.user_data)
    with patch("bot._analyse_selected_form", AsyncMock()) as analyse:
        await bot.handle_form_choice(_callback_update("FORM|DOPS"), context)
    analyse.assert_not_awaited()
    assert context.user_data == before


@pytest.mark.parametrize("command", ["help", "settings"])
async def test_navigation_command_keeps_the_active_case_conversation(command):
    context = _context({"draft_data": _SNAPSHOT["last_amend_draft"], "case_text": "synthetic case"})
    before = dict(context.user_data)
    update = MagicMock()
    update.effective_user.id = 4242
    update.message.reply_text = AsyncMock()
    with patch("bot.get_user_tier", AsyncMock(return_value="free")), \
         patch("bot.get_cases_this_month", AsyncMock(return_value=0)), \
         patch("bot.is_beta_tester", AsyncMock(return_value=True)), \
         patch("bot._kaizen_connected", return_value=True), \
         patch("bot._safe_kaizen_sync_status", AsyncMock(return_value=None)):
        state = await getattr(bot, command + "_command")(update, context)
    assert state is None  # PTB keeps the current step for the next case reply.
    assert context.user_data == before


@pytest.mark.parametrize("producer", ["template", "search", "search_empty"])
async def test_new_form_prompt_buttons_reach_their_next_step(producer):
    from types import SimpleNamespace
    from tests.bot_simulator import BotSimulator

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(case_text="synthetic case", chosen_form="CBD",
                             last_bot_msg_id=500, last_bot_chat_id=sim.user_id)
    update = sim._make_text_update("zzzz" if producer == "search_empty" else "DOPS")
    sent_prompt = sim._mock_message()
    update.message.reply_text = AsyncMock(return_value=sent_prompt)
    recommendation = SimpleNamespace(form_type="DOPS", uuid="synthetic", reason="Procedure")
    with patch("bot.classify_intent", AsyncMock(return_value="question_about_case")), \
         patch("bot.recommend_form_types", AsyncMock(return_value=[recommendation])), \
         patch("bot._filter_recommendations_for_allowed_forms", return_value=[recommendation]), \
         patch("bot._build_form_recommendation_text", return_value="Other forms"), \
         patch("bot._get_allowed_forms", return_value=["DOPS"]):
        handler = bot.handle_template_review_text if producer == "template" else bot.handle_form_search_text
        assert await handler(update, context) == bot.AWAIT_FORM_CHOICE

    prompt = update.message.reply_text.await_args
    payloads = [b.callback_data for row in prompt.kwargs["reply_markup"].inline_keyboard for b in row]
    payload = {"template": "FORM|best", "search": "FORM|DOPS", "search_empty": "FORM|show_all"}[producer]
    assert payload in payloads
    clicked = sim._make_callback_update(payload)
    # Use the actual returned message ID, not the value under test in user_data.
    clicked.callback_query.message.message_id = sent_prompt.message_id
    with patch("bot._analyse_selected_form", AsyncMock(return_value=bot.FormDraft(
            form_type="DOPS", fields={"summary": "synthetic"}))) as analyse, \
         patch("bot._essentials_gate_before_draft", AsyncMock(return_value=None)), \
         patch("bot._show_draft_review", AsyncMock(return_value=bot.AWAIT_APPROVAL)):
        state = await bot.handle_form_choice(clicked, context)
    if producer == "search_empty":
        assert state == bot.AWAIT_FORM_CHOICE
        clicked.callback_query.edit_message_text.assert_awaited_once()
    else:
        analyse.assert_awaited_once()
        assert context.user_data["chosen_form"] == "DOPS"


@pytest.mark.parametrize("entitlement_error", [False, True])
async def test_trial_refusal_keeps_save_usable_after_upgrade(entitlement_error):
    token = "abcdef"
    context = _context({"case_token": token, "draft_data": _SNAPSHOT["last_amend_draft"]})
    update = _callback_update(f"APPROVE|draft|{token}")
    denied = AsyncMock(side_effect=RuntimeError("offline entitlement failure")) if entitlement_error else AsyncMock(
        return_value=(False, 0, 0, "trial"))
    with patch("bot.free_trial_enabled", return_value=True), \
         patch("bot.check_can_file", denied), \
         patch("bot.route_filing", AsyncMock()) as filing:
        if entitlement_error:
            with pytest.raises(RuntimeError, match="offline entitlement failure"):
                await bot.handle_approval_approve(update, context)
        else:
            assert await bot.handle_approval_approve(update, context) == bot.AWAIT_APPROVAL
        filing.assert_not_awaited()
    assert context.user_data["case_token"] == token
    assert not context.user_data.get("filing_in_progress")
    update.callback_query.edit_message_reply_markup.assert_not_awaited()

    context.args = ["upgraded"]
    with patch("bot.get_user_tier", AsyncMock(return_value="pro_plus")):
        await bot.start(_start_update("upgraded"), context)
    with patch("bot.free_trial_enabled", return_value=True), \
         patch("bot.check_can_file", AsyncMock(return_value=(True, 0, -1, "pro_plus"))) as check, \
         patch("bot.get_credentials", return_value=("fake", "fake")), \
         patch("bot._needs_filing_curriculum_choice", return_value=True), \
         patch("bot.route_filing", AsyncMock()) as filing:
        assert await bot.handle_approval_approve(update, context) == bot.AWAIT_APPROVAL
        check.assert_awaited_once()
        assert context.user_data["awaiting_filing_curriculum_choice"]
        filing.assert_not_awaited()
    assert context.user_data["case_token"] != token


@pytest.mark.parametrize("mode", ["ignore", "info", "attach", "both"])
async def test_two_documents_share_one_combined_intent_prompt(tmp_path, monkeypatch, mode):
    from tests.bot_simulator import BotSimulator

    sim = BotSimulator()
    context = sim._make_context()
    monkeypatch.setattr(bot.tempfile, "gettempdir", lambda: str(tmp_path))
    with patch("bot._kaizen_connected", return_value=True), \
         patch("bot.check_can_file", AsyncMock(return_value=(True, 0, -1, "beta"))), \
         patch("bot.extract_from_document", AsyncMock()) as extract:
        for name in ("first.pdf", "second.pdf"):
            update = sim._make_text_update("")
            update.message.text = None
            update.message.document = MagicMock(file_name=name, mime_type="application/pdf")
            update.message.document.get_file = AsyncMock(return_value=MagicMock(download_to_drive=AsyncMock()))
            assert await bot.handle_case_input(update, context) == bot.AWAIT_DOC_INTENT
        extract.assert_not_awaited()
    edits = context.bot.edit_message_text.await_args
    assert edits is not None
    assert "2 documents" in edits.kwargs["text"]
    assert "Your choice applies to all of them" in edits.kwargs["text"]
    assert edits.kwargs["message_id"] == context.user_data["_pending_media_prompt"]["message_id"]
    assert len(bot._pending_media_items(context)) == 2
    assert len([kind for kind, _, _ in sim.messages_sent if kind == "delete"]) == 1
    paths = {item["path"] for item in bot._pending_media_items(context)}
    clicked = sim._make_callback_update(f"DOCUSE|{mode}")
    clicked.callback_query.message.message_id = edits.kwargs["message_id"]
    with patch("bot.extract_from_document", AsyncMock(side_effect=lambda path: f"Synthetic account {path}")) as extract, \
         patch("bot._process_case_text", AsyncMock(return_value=bot.AWAIT_FORM_CHOICE)) as process:
        await bot.handle_document_intent(clicked, context)
    if mode in {"info", "both"}:
        assert {call.args[0] for call in extract.await_args_list} == paths
        process.assert_awaited_once()
        assert all(path in process.await_args.args[3] for path in paths)
    else:
        extract.assert_not_awaited()
        process.assert_not_awaited()
    attached = {item["path"] for item in bot._case_attachments(context)}
    assert attached == (paths if mode in {"attach", "both"} else set())
    if mode == "ignore":
        assert all(not bot.os.path.exists(path) for path in paths)


@pytest.mark.parametrize("mode", ["ignore", "info", "attach", "both"])
async def test_superseded_document_prompt_cannot_act_on_pending_files(mode):
    context = _context({"_pending_media_prompt": {"chat_id": 4242, "message_id": 20}})
    bot._queue_pending_media(context, {"path": "/synthetic/first.pdf", "name": "first.pdf"})
    bot._queue_pending_media(context, {"path": "/synthetic/second.pdf", "name": "second.pdf"})
    before = list(bot._pending_media_items(context))
    with patch("bot.os.unlink") as unlink, patch("bot.extract_from_document", AsyncMock(return_value="synthetic")) as extract:
        assert await bot.handle_document_intent(_callback_update(f"DOCUSE|{mode}"), context) is None
    unlink.assert_not_called()
    extract.assert_not_awaited()
    assert bot._pending_media_items(context) == before
    assert not bot._case_attachments(context)


async def test_uneditable_document_prompt_is_retired_before_reanchoring():
    context = _context({"_pending_media_prompt": {"chat_id": 4242, "message_id": 10}})
    context.bot.edit_message_text = AsyncMock(side_effect=RuntimeError("uneditable"))
    bot._queue_pending_media(context, {"path": "/synthetic/first.pdf", "name": "first.pdf"})
    bot._queue_pending_media(context, {"path": "/synthetic/second.pdf", "name": "second.pdf"})
    ack = MagicMock(chat_id=4242, message_id=20, edit_text=AsyncMock())
    assert await bot._show_pending_media_prompt(context, ack, single="Document") == bot.AWAIT_DOC_INTENT
    context.bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=4242, message_id=10, reply_markup=None)
    assert "2 documents" in ack.edit_text.await_args.args[0]
    assert context.user_data["_pending_media_prompt"]["message_id"] == 20
