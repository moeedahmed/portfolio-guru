"""Tests for the supervisor Telegram surface.

Covers the seam between the scheduler/payload world and python-telegram-bot:

* :func:`send_supervisor_notification` — composes the PHI-free Telegram
  message + Open / Skip / Later keyboard and persists the payload in the
  notification cache so the callbacks can recover it later.
* :func:`handle_supervisor_callback` — parses ``SUP|<action>|<uuid>``
  callback data, looks up the cached payload, and runs the
  action-specific path:
    * ``open`` — fetches the ticket detail (read-only), edits the
      message to render :func:`supervisor_workflow.render_supervisor_ticket_detail_text`,
      starts an assessor session, and sends the intent prompt.
    * ``skip`` — edits to "Skipped." and drops the cache entry. Never
      navigates to Kaizen.
    * ``later`` — edits to "I'll keep this on your queue." Never
      navigates to Kaizen.
    * ``review`` / ``recapture`` / ``cancel-draft`` — manage the local
      assessor draft only. Never navigate to Kaizen.
* :func:`handle_assessor_intent_capture` — message handler that turns a
  supervisor's text or voice note into a structured draft preview when
  an assessor session is active. Inert (returns without raising) when
  no session exists so trainee handlers in the default group keep
  working untouched.

Read-only contract checks live alongside the behaviour tests: a source
scan ensures the module never references any Kaizen write-side action.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ApplicationHandlerStop

import assessor_session_store as session_store
import supervisor_bot
import supervisor_notification_cache as cache_mod
from assessor_drafter import AssessorDraft
from assessor_reader import AssessorTicketData
from supervisor_workflow import SupervisorNotificationPayload


def _payload(uuid: str = "uuid-test", *, form_type: str | None = "CBD") -> SupervisorNotificationPayload:
    return SupervisorNotificationPayload(
        ticket_uuid=uuid,
        ticket_url=f"https://kaizenep.com/events/view-section/{uuid}",
        form_type=form_type,
        redacted_title="CBD - Case Based Discussion (2025 update)",
        status="unfilled",
    )


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor_bot, "NOTIFICATION_CACHE_DIR", tmp_path)
    return tmp_path


# ── send_supervisor_notification ────────────────────────────────────────────


async def test_send_notification_uses_phi_free_text_and_open_skip_later_keyboard(cache_dir):
    bot = AsyncMock()
    payload = _payload("u-1", form_type="CBD")

    await supervisor_bot.send_supervisor_notification(
        bot=bot, telegram_user_id=42, payload=payload
    )

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    # PHI-free wording lives in supervisor_workflow.render_supervisor_notification_text.
    text = kwargs["text"]
    assert "CBD" in text
    assert "won't open the ticket on Kaizen" in text
    # Three callback buttons, all under the SUP| namespace, all carrying the uuid.
    markup = kwargs["reply_markup"]
    flat = [btn for row in markup.inline_keyboard for btn in row]
    callbacks = {btn.callback_data for btn in flat}
    assert callbacks == {"SUP|open|u-1", "SUP|skip|u-1", "SUP|later|u-1"}


async def test_send_notification_persists_payload_in_cache(cache_dir):
    bot = AsyncMock()
    payload = _payload("u-cache", form_type="DOPS")

    await supervisor_bot.send_supervisor_notification(
        bot=bot, telegram_user_id=99, payload=payload
    )

    recovered = cache_mod.lookup(cache_dir, telegram_user_id=99, ticket_uuid="u-cache")
    assert recovered == payload


# ── handle_supervisor_callback: parsing + lookup ────────────────────────────


def _callback_update(callback_data: str, *, telegram_user_id: int = 1):
    update = MagicMock()
    update.effective_user.id = telegram_user_id
    update.effective_chat.id = telegram_user_id
    update.callback_query = MagicMock()
    update.callback_query.data = callback_data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.reply_text = AsyncMock()
    return update


def _text_message_update(text: str, *, telegram_user_id: int = 1):
    update = MagicMock()
    update.effective_user.id = telegram_user_id
    update.effective_message = MagicMock()
    update.effective_message.text = text
    update.effective_message.voice = None
    update.effective_message.audio = None
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None
    return update


def _voice_message_update(*, telegram_user_id: int = 1, mime_type: str = "audio/ogg"):
    update = MagicMock()
    update.effective_user.id = telegram_user_id
    update.effective_message = MagicMock()
    update.effective_message.text = None
    voice = MagicMock()
    voice.mime_type = mime_type
    voice.get_file = AsyncMock(return_value=MagicMock(download_to_drive=AsyncMock()))
    update.effective_message.voice = voice
    update.effective_message.audio = None
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None
    return update


async def test_callback_open_renders_ticket_detail_via_assessor_reader(cache_dir):
    cache_mod.remember(cache_dir, telegram_user_id=1, payload=_payload("u-open"))

    ticket_data = AssessorTicketData(
        form_type="CBD",
        ticket_uuid="u-open",
        ticket_url="https://kaizenep.com/events/view-section/u-open",
        title="CBD - Case Based Discussion (2025 update)",
        state="Pending",
        trainee_section=[{"label": "Case to be discussed", "value": "Chest pain"}],
        pending_assessor_fields=[{"key": "feedback", "label": "Feedback"}],
        needs_write_side_mapping=True,
    )
    update = _callback_update("SUP|open|u-open", telegram_user_id=1)
    context = MagicMock()

    with patch(
        "supervisor_bot.connect_cdp_page",
        new=AsyncMock(return_value=AsyncMock()),
    ), patch(
        "supervisor_bot.open_ticket_readonly",
        new=AsyncMock(return_value=ticket_data),
    ) as open_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    open_mock.assert_awaited_once()
    edited_text = update.callback_query.edit_message_text.await_args.kwargs.get("text") \
        or update.callback_query.edit_message_text.await_args.args[0]
    # Detail render includes trainee section labels + values; assessor field labels only.
    assert "Chest pain" in edited_text
    assert "Feedback" in edited_text
    # Notification cache entry consumed once the supervisor opens the ticket.
    assert cache_mod.lookup(cache_dir, telegram_user_id=1, ticket_uuid="u-open") is None
    # Open also starts a local assessor session and prompts for the intent.
    session = session_store.get(cache_dir, telegram_user_id=1)
    assert session is not None
    assert session.ticket_uuid == "u-open"
    assert session.form_type == "CBD"
    update.callback_query.message.reply_text.assert_awaited_once()
    prompt_kwargs = update.callback_query.message.reply_text.await_args.kwargs
    prompt_text = prompt_kwargs.get("text") or update.callback_query.message.reply_text.await_args.args[0]
    assert "voice note" in prompt_text.lower() or "text or a voice" in prompt_text.lower()
    keyboard = prompt_kwargs.get("reply_markup")
    cancel_callbacks = {
        btn.callback_data for row in keyboard.inline_keyboard for btn in row
    }
    assert cancel_callbacks == {"SUP|cancel-draft|u-open"}


async def test_callback_open_handles_missing_cache_gracefully(cache_dir):
    update = _callback_update("SUP|open|u-vanished", telegram_user_id=1)
    context = MagicMock()

    with patch("supervisor_bot.connect_cdp_page", new=AsyncMock()) as connect_mock, \
         patch("supervisor_bot.open_ticket_readonly", new=AsyncMock()) as open_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    # Never reaches Kaizen when the cache row is missing — the message is
    # already stale.
    connect_mock.assert_not_awaited()
    open_mock.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()


async def test_callback_open_handles_cdp_failure_without_raising(cache_dir):
    cache_mod.remember(cache_dir, telegram_user_id=1, payload=_payload("u-cdp"))
    update = _callback_update("SUP|open|u-cdp", telegram_user_id=1)
    context = MagicMock()

    with patch(
        "supervisor_bot.connect_cdp_page",
        new=AsyncMock(side_effect=RuntimeError("CDP refused")),
    ), patch("supervisor_bot.open_ticket_readonly", new=AsyncMock()) as open_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    open_mock.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()
    # Cache row preserved so the user can retry once Kaizen is reachable.
    assert cache_mod.lookup(cache_dir, telegram_user_id=1, ticket_uuid="u-cdp") is not None


async def test_callback_skip_never_touches_kaizen(cache_dir):
    cache_mod.remember(cache_dir, telegram_user_id=1, payload=_payload("u-skip"))
    update = _callback_update("SUP|skip|u-skip", telegram_user_id=1)
    context = MagicMock()

    with patch("supervisor_bot.connect_cdp_page", new=AsyncMock()) as connect_mock, \
         patch("supervisor_bot.open_ticket_readonly", new=AsyncMock()) as open_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    connect_mock.assert_not_awaited()
    open_mock.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()
    edited_text = (
        update.callback_query.edit_message_text.await_args.kwargs.get("text")
        or update.callback_query.edit_message_text.await_args.args[0]
    )
    assert "Skipped" in edited_text
    # Cache entry dropped so the keyboard cannot be reused.
    assert cache_mod.lookup(cache_dir, telegram_user_id=1, ticket_uuid="u-skip") is None


async def test_callback_later_never_touches_kaizen(cache_dir):
    cache_mod.remember(cache_dir, telegram_user_id=1, payload=_payload("u-later"))
    update = _callback_update("SUP|later|u-later", telegram_user_id=1)
    context = MagicMock()

    with patch("supervisor_bot.connect_cdp_page", new=AsyncMock()) as connect_mock, \
         patch("supervisor_bot.open_ticket_readonly", new=AsyncMock()) as open_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    connect_mock.assert_not_awaited()
    open_mock.assert_not_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()
    edited_text = (
        update.callback_query.edit_message_text.await_args.kwargs.get("text")
        or update.callback_query.edit_message_text.await_args.args[0]
    )
    assert "queue" in edited_text.lower()
    # Cache row stays so Open remains valid later.
    assert cache_mod.lookup(cache_dir, telegram_user_id=1, ticket_uuid="u-later") is not None


async def test_callback_unknown_action_is_silently_ignored(cache_dir):
    update = _callback_update("SUP|nope|u-x")
    context = MagicMock()

    # Should not raise.
    with patch("supervisor_bot.connect_cdp_page", new=AsyncMock()) as connect_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    connect_mock.assert_not_awaited()
    update.callback_query.answer.assert_awaited_once()


# ── review / recapture / cancel-draft callbacks ─────────────────────────────


def _start_session(cache_dir: Path, *, user_id: int = 1, ticket_uuid: str = "u-draft") -> None:
    session_store.start(
        cache_dir,
        telegram_user_id=user_id,
        ticket_uuid=ticket_uuid,
        form_type="CBD",
        ticket_url=f"https://kaizenep.com/events/view-section/{ticket_uuid}",
        trainee_section=[{"label": "Case", "value": "Chest pain"}],
        pending_assessor_fields=[{"key": "feedback", "label": "Feedback"}],
    )


async def test_callback_review_renders_existing_draft(cache_dir):
    _start_session(cache_dir, ticket_uuid="u-rev")
    draft = AssessorDraft(
        form_type="CBD",
        ticket_uuid="u-rev",
        values={"feedback": "Good case, level 4 supervision."},
        missing_required=[{"key": "recommendation", "label": "Recommendation"}],
        risk_notes=["Some note"],
        source_intent="Good case, level 4 supervision.",
    )
    session_store.update_draft(cache_dir, telegram_user_id=1, draft=draft)
    update = _callback_update("SUP|review|u-rev", telegram_user_id=1)
    context = MagicMock()

    with patch("supervisor_bot.connect_cdp_page", new=AsyncMock()) as connect_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    connect_mock.assert_not_awaited()
    update.callback_query.message.reply_text.assert_awaited_once()
    preview = update.callback_query.message.reply_text.await_args.kwargs.get("text") \
        or update.callback_query.message.reply_text.await_args.args[0]
    assert "Assessor draft — CBD" in preview
    assert "Good case, level 4 supervision." in preview
    # Cache and session preserved so the user can keep iterating.
    assert session_store.get(cache_dir, telegram_user_id=1) is not None


async def test_callback_review_handles_missing_draft_gracefully(cache_dir):
    _start_session(cache_dir, ticket_uuid="u-rev2")
    update = _callback_update("SUP|review|u-rev2", telegram_user_id=1)
    context = MagicMock()

    await supervisor_bot.handle_supervisor_callback(update, context)

    update.callback_query.edit_message_text.assert_awaited_once()
    update.callback_query.message.reply_text.assert_not_awaited()


async def test_callback_recapture_clears_draft_and_reprompts(cache_dir):
    _start_session(cache_dir, ticket_uuid="u-rec")
    draft = AssessorDraft(form_type="CBD", ticket_uuid="u-rec", values={"feedback": "x"})
    session_store.update_draft(cache_dir, telegram_user_id=1, draft=draft)
    session_store.update_intent(cache_dir, telegram_user_id=1, intent="x")
    update = _callback_update("SUP|recapture|u-rec", telegram_user_id=1)
    context = MagicMock()

    with patch("supervisor_bot.connect_cdp_page", new=AsyncMock()) as connect_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    connect_mock.assert_not_awaited()
    refreshed = session_store.get(cache_dir, telegram_user_id=1)
    assert refreshed is not None
    assert refreshed.intent is None
    assert refreshed.draft is None
    update.callback_query.message.reply_text.assert_awaited_once()
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    keyboard = kwargs.get("reply_markup")
    callbacks = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
    assert callbacks == {"SUP|cancel-draft|u-rec"}


async def test_callback_cancel_draft_ends_session(cache_dir):
    _start_session(cache_dir, ticket_uuid="u-cancel")
    update = _callback_update("SUP|cancel-draft|u-cancel", telegram_user_id=1)
    context = MagicMock()

    with patch("supervisor_bot.connect_cdp_page", new=AsyncMock()) as connect_mock:
        await supervisor_bot.handle_supervisor_callback(update, context)

    connect_mock.assert_not_awaited()
    assert session_store.get(cache_dir, telegram_user_id=1) is None
    update.callback_query.edit_message_text.assert_awaited_once()
    edited = update.callback_query.edit_message_text.await_args.kwargs.get("text") \
        or update.callback_query.edit_message_text.await_args.args[0]
    assert "discarded" in edited.lower() or "cancelled" in edited.lower()


# ── handle_assessor_intent_capture ──────────────────────────────────────────


async def test_intent_capture_text_creates_draft_and_replies_with_preview(cache_dir):
    _start_session(cache_dir, ticket_uuid="u-intent", user_id=99)
    update = _text_message_update(
        "Strong reasoning, level 4 supervision throughout. Recommend more focus on "
        "documenting time-critical decisions next time.",
        telegram_user_id=99,
    )
    context = MagicMock()

    with pytest.raises(ApplicationHandlerStop):
        await supervisor_bot.handle_assessor_intent_capture(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    preview = update.effective_message.reply_text.await_args.kwargs.get("text") \
        or update.effective_message.reply_text.await_args.args[0]
    assert "Assessor draft — CBD" in preview
    assert "Level 4" in preview
    refreshed = session_store.get(cache_dir, telegram_user_id=99)
    assert refreshed is not None
    assert refreshed.draft is not None
    assert refreshed.draft["values"]["feedback"].startswith("Strong reasoning")


async def test_intent_capture_no_session_returns_quietly_for_trainee_flow(cache_dir):
    update = _text_message_update("My ED case from last shift...", telegram_user_id=42)
    context = MagicMock()

    # Must not raise ApplicationHandlerStop so the trainee handler downstream
    # in the default group still processes the same update.
    await supervisor_bot.handle_assessor_intent_capture(update, context)

    update.effective_message.reply_text.assert_not_awaited()


async def test_intent_capture_blank_text_warns_and_stops(cache_dir):
    _start_session(cache_dir, ticket_uuid="u-blank", user_id=77)
    update = _text_message_update("   ", telegram_user_id=77)
    context = MagicMock()

    with pytest.raises(ApplicationHandlerStop):
        await supervisor_bot.handle_assessor_intent_capture(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    warning = update.effective_message.reply_text.await_args.kwargs.get("text") \
        or update.effective_message.reply_text.await_args.args[0]
    assert "couldn't read" in warning.lower()


async def test_intent_capture_voice_transcribes_and_drafts(cache_dir):
    _start_session(cache_dir, ticket_uuid="u-voice", user_id=88)
    update = _voice_message_update(telegram_user_id=88)
    context = MagicMock()

    with patch(
        "whisper.transcribe_voice",
        new=AsyncMock(return_value="Patient was managed competently. Recommend reading on AKI staging."),
    ):
        with pytest.raises(ApplicationHandlerStop):
            await supervisor_bot.handle_assessor_intent_capture(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    preview = update.effective_message.reply_text.await_args.kwargs.get("text") \
        or update.effective_message.reply_text.await_args.args[0]
    assert "Assessor draft — CBD" in preview
    assert "Recommend" in preview


async def test_intent_capture_voice_transcription_failure_warns_and_stops(cache_dir):
    _start_session(cache_dir, ticket_uuid="u-vfail", user_id=66)
    update = _voice_message_update(telegram_user_id=66)
    context = MagicMock()

    with patch(
        "whisper.transcribe_voice",
        new=AsyncMock(side_effect=RuntimeError("Whisper down")),
    ):
        with pytest.raises(ApplicationHandlerStop):
            await supervisor_bot.handle_assessor_intent_capture(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    warning = update.effective_message.reply_text.await_args.kwargs.get("text") \
        or update.effective_message.reply_text.await_args.args[0]
    assert "couldn't transcribe" in warning.lower()
    # Session remains so the supervisor can retry by sending text or another voice note.
    assert session_store.get(cache_dir, telegram_user_id=66) is not None


async def test_intent_capture_unknown_form_type_short_circuits(cache_dir):
    session_store.start(
        cache_dir,
        telegram_user_id=55,
        ticket_uuid="u-noform",
        form_type=None,
        ticket_url=None,
    )
    update = _text_message_update("Solid case.", telegram_user_id=55)
    context = MagicMock()

    with pytest.raises(ApplicationHandlerStop):
        await supervisor_bot.handle_assessor_intent_capture(update, context)

    warning = update.effective_message.reply_text.await_args.kwargs.get("text") \
        or update.effective_message.reply_text.await_args.args[0]
    assert "couldn't identify" in warning.lower()


# ── safety: no write-side Kaizen actions referenced ─────────────────────────


def test_supervisor_bot_module_never_clicks_write_controls():
    source = inspect.getsource(supervisor_bot)
    forbidden_snippets = [
        "click('text=Sign",
        "click('text=Submit",
        "click('text=Approve",
        "click('text=Delete",
        "click('text=Save",
        "click('text=Send",
        "click('text=Fill",
        'click("text=Sign',
        'click("text=Submit',
        'click("text=Approve',
        'click("text=Delete',
        'click("text=Save',
        'click("text=Send',
        'click("text=Fill',
        "get_by_text('Sign",
        "get_by_text('Submit",
        "get_by_text('Approve",
        "get_by_text('Delete",
        "get_by_text('Save",
        "get_by_text('Send",
        "get_by_text('Fill in",
        ".fill(",
        "extract_assessor_completion_shape",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source, (
            f"supervisor_bot source contains forbidden write action: {snippet}"
        )
