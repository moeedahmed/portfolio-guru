"""Regression coverage for `_retire_active_missing_essentials_prompt` and the
Cancel-button staleness it must produce.

The resolved essentials-gap prompt is bot-owned and fully answered by the
doctor's reply, so retirement deletes it outright rather than editing it in
place with an "Added — see below" placeholder. These tests prove: (1) the
normal path deletes the prompt with no leftover boilerplate message, (2) that
holds across every round of a multi-round (partial-answer) gathering
exchange, including when Telegram reports the message already gone, (3) a
genuine deletion refusal falls back to stripping the keyboard and a minimal
acknowledgement rather than silently leaving a live button behind, and (4) a
stale Cancel tap on a retired prompt cannot cancel whatever draft is active
by the time it arrives.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from telegram.error import BadRequest
from telegram.ext import ConversationHandler

from models import FormDraft
from tests.bot_simulator import BotSimulator


COMPLETE_CBD_FIELDS = {
    "date_of_encounter": "2026-03-17",
    "clinical_setting": "ED",
    "patient_presentation": "Chest pain",
    "stage_of_training": "Higher/ST4-ST6",
    "trainee_role": "Assessed and managed the patient",
    "clinical_reasoning": "Managed as ACS.",
    "reflection": "I learned to escalate ECG review earlier and will do so in future.",
    "level_of_supervision": "Indirect",
    "curriculum_links": ["SLO1"],
    "key_capabilities": ["SLO1 KC1: Assess and stabilise the patient"],
}


def _cbd_draft(**overrides) -> FormDraft:
    fields = {**COMPLETE_CBD_FIELDS, **overrides}
    return FormDraft(form_type="CBD", uuid="uuid-cbd", fields=fields)


@pytest.mark.asyncio
async def test_chronological_deletion_removes_prompt_without_boilerplate():
    """The normal path deletes the resolved prompt outright — no edit-in-place
    and no 'Added ... see below' placeholder left behind."""
    from bot import _retire_active_missing_essentials_prompt

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id

    audited = []
    with patch("bot._audit_event", side_effect=lambda *a, **kw: audited.append(kw.get("action"))):
        await _retire_active_missing_essentials_prompt(context)

    context.bot.delete_message.assert_awaited_once_with(chat_id=sim.user_id, message_id=42)
    context.bot.edit_message_text.assert_not_awaited()
    context.bot.edit_message_reply_markup.assert_not_awaited()
    assert "missing_essentials_prompt_deleted" in audited
    assert "last_bot_msg_id" not in context.user_data
    assert "last_bot_chat_id" not in context.user_data


@pytest.mark.asyncio
async def test_partial_answer_deletes_prompt_every_round():
    """Each round of a multi-round gathering exchange (the doctor's reply
    only partially closes the gap, so another essentials-gap prompt follows)
    must delete its own prompt the same way — including tolerating Telegram
    reporting the earlier one already gone."""
    from bot import _is_retired_essentials_prompt_ref, _retire_active_missing_essentials_prompt

    sim = BotSimulator()
    context = sim._make_context()

    # Round 1: doctor answers part of the gap; prompt deletes cleanly.
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id
    await _retire_active_missing_essentials_prompt(context)
    assert _is_retired_essentials_prompt_ref(context, sim.user_id, 42)

    # Round 2: a fresh essentials-gap prompt (still missing fields) is now
    # tracked; Telegram reports it already gone (e.g. a race with an earlier
    # retry) — that must be treated as idempotent success, not a failure.
    context.user_data["last_bot_msg_id"] = 55
    context.user_data["last_bot_chat_id"] = sim.user_id
    context.bot.delete_message = AsyncMock(side_effect=BadRequest("Message to delete not found"))

    audited = []
    with patch("bot._audit_event", side_effect=lambda *a, **kw: audited.append(kw.get("action"))):
        await _retire_active_missing_essentials_prompt(context)

    assert "missing_essentials_prompt_already_retired" in audited
    assert "missing_essentials_retire_failed" not in audited
    context.bot.edit_message_text.assert_not_awaited()
    context.bot.edit_message_reply_markup.assert_not_awaited()
    assert _is_retired_essentials_prompt_ref(context, sim.user_id, 55)


@pytest.mark.asyncio
async def test_delete_refusal_falls_back_to_details_received():
    """When deletion itself is refused (not merely 'already gone' — e.g. a
    permission or time-limit error), fall back to stripping the keyboard and
    a minimal 'Details received.' acknowledgement instead of leaving a live
    button on an abandoned prompt."""
    from bot import _is_retired_essentials_prompt_ref, _retire_active_missing_essentials_prompt

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id
    context.bot.delete_message = AsyncMock(
        side_effect=BadRequest("Message can't be deleted for everyone")
    )

    audited = []
    with patch("bot._audit_event", side_effect=lambda *a, **kw: audited.append(kw.get("action"))):
        await _retire_active_missing_essentials_prompt(context)

    context.bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=sim.user_id, message_id=42, reply_markup=None
    )
    context.bot.edit_message_text.assert_awaited_once_with(
        chat_id=sim.user_id, message_id=42, text="Details received."
    )
    assert "missing_essentials_reply_markup_removed" in audited
    assert "missing_essentials_retire_failed" not in audited
    assert _is_retired_essentials_prompt_ref(context, sim.user_id, 42)


@pytest.mark.asyncio
async def test_double_retirement_failure_is_recorded_not_swallowed():
    """If both the deletion and the keyboard-removal fallback are refused,
    that double failure is recorded rather than silently swallowed, and the
    prompt is still marked stale so a later Cancel tap on it is a no-op."""
    from bot import _is_retired_essentials_prompt_ref, _retire_active_missing_essentials_prompt

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id
    context.bot.delete_message = AsyncMock(side_effect=BadRequest("Message can't be deleted"))
    context.bot.edit_message_reply_markup = AsyncMock(side_effect=BadRequest("Message to edit not found"))

    audited = []
    with patch("bot._audit_event", side_effect=lambda *a, **kw: audited.append(kw.get("action"))):
        await _retire_active_missing_essentials_prompt(context)

    assert "missing_essentials_retire_failed" in audited
    assert _is_retired_essentials_prompt_ref(context, sim.user_id, 42)


@pytest.mark.asyncio
async def test_stale_callback_after_deletion_does_not_cancel_new_draft():
    """A Cancel tap that lands on a deleted (or refused-deletion) essentials
    prompt must not wipe out a new draft that has since become active."""
    from bot import _retire_active_missing_essentials_prompt, handle_callback

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id
    context.bot.delete_message = AsyncMock(side_effect=BadRequest("Message can't be deleted"))
    context.bot.edit_message_reply_markup = AsyncMock(side_effect=BadRequest("Message to edit not found"))

    await _retire_active_missing_essentials_prompt(context)

    # A new draft becomes active after the retirement (as happens once the
    # doctor's followup completes extraction).
    draft = _cbd_draft()
    context.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": draft.form_type,
        "fields": draft.fields,
        "uuid": draft.uuid,
    }
    context.user_data["chosen_form"] = "CBD"
    context.user_data["case_text"] = "45M with chest pain, troponin positive, managed as ACS."

    stale_cancel_update = sim._make_callback_update("ACTION|cancel")
    stale_cancel_update.callback_query.message.message_id = 42
    stale_cancel_update.callback_query.message.chat_id = sim.user_id

    result = await handle_callback(stale_cancel_update, context)

    # The active draft must survive — the stale tap was a no-op, not a reset.
    assert context.user_data.get("draft_data") is not None
    assert context.user_data.get("chosen_form") == "CBD"
    assert result != ConversationHandler.END or context.user_data.get("case_text")


@pytest.mark.asyncio
async def test_fresh_cancel_still_cancels_normally():
    """A Cancel tap on the current, non-retired message must still work as
    before — the staleness check must not interfere with ordinary cancels."""
    from bot import handle_callback

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = "45M with chest pain."
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True

    update = sim._make_callback_update("ACTION|cancel")
    result = await handle_callback(update, context)

    assert result == ConversationHandler.END
    assert "awaiting_detail" not in context.user_data
    assert "chosen_form" not in context.user_data
    assert "case_text" not in context.user_data
