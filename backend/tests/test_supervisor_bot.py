"""Tests for the supervisor Telegram surface.

Covers the seam between the scheduler/payload world and python-telegram-bot:

* :func:`send_supervisor_notification` — composes the PHI-free Telegram
  message + Open / Skip / Later keyboard and persists the payload in the
  notification cache so the callbacks can recover it later.
* :func:`handle_supervisor_callback` — parses ``SUP|<action>|<uuid>``
  callback data, looks up the cached payload, and runs the
  action-specific path:
    * ``open`` — fetches the ticket detail (read-only) and edits the
      message to render :func:`supervisor_workflow.render_supervisor_ticket_detail_text`.
    * ``skip`` — edits to "Skipped." and drops the cache entry. Never
      navigates to Kaizen.
    * ``later`` — edits to "I'll keep this on your queue." Never
      navigates to Kaizen.

Read-only contract checks live alongside the behaviour tests: a source
scan ensures the module never references any Kaizen write-side action.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import supervisor_bot
import supervisor_notification_cache as cache_mod
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
    update.callback_query = MagicMock()
    update.callback_query.data = callback_data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
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
    # Cache entry consumed.
    assert cache_mod.lookup(cache_dir, telegram_user_id=1, ticket_uuid="u-open") is None


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
