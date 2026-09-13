"""Regression coverage for `_retire_active_missing_essentials_prompt` and the
Cancel-button staleness it must produce.

The retirement used to forget the tracked message id unconditionally, even
when both the edit-in-place and the reply-markup removal failed, leaving a
live Cancel button on an abandoned prompt with no way to recognise a later
tap on it as stale. These tests prove: (1) a genuine double failure is
recorded rather than silently swallowed, (2) a real "message not modified"
race is treated as an already-clean retirement, not a failure, and (3) a
stale Cancel tap on the retired prompt cannot cancel whatever draft is
active by the time it arrives.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_both_retirement_operations_failing_is_recorded_not_swallowed():
    from bot import _retire_active_missing_essentials_prompt

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id
    context.bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message to edit not found"))
    context.bot.edit_message_reply_markup = AsyncMock(side_effect=BadRequest("Message to edit not found"))

    audited = []
    with patch("bot._audit_event", side_effect=lambda *a, **kw: audited.append(kw.get("action"))):
        await _retire_active_missing_essentials_prompt(context)

    assert "missing_essentials_retire_failed" in audited
    # Tracking keys are still cleared so the caller sends a fresh message.
    assert "last_bot_msg_id" not in context.user_data
    assert "last_bot_chat_id" not in context.user_data


@pytest.mark.asyncio
async def test_identical_state_error_is_treated_as_already_retired():
    """A 'message is not modified' BadRequest means the prompt was already
    edited (e.g. a prior retirement attempt actually succeeded server-side
    but the response was lost) — this must not be treated as a real
    failure, and must not trigger a second, unnecessary markup-removal call."""
    from bot import _retire_active_missing_essentials_prompt

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id
    context.bot.edit_message_text = AsyncMock(
        side_effect=BadRequest("Message is not modified")
    )

    audited = []
    with patch("bot._audit_event", side_effect=lambda *a, **kw: audited.append(kw.get("action"))):
        await _retire_active_missing_essentials_prompt(context)

    assert "missing_essentials_prompt_already_retired" in audited
    assert "missing_essentials_retire_failed" not in audited
    context.bot.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_retire_after_failed_cleanup_still_marks_stale():
    """If the caller invokes retirement twice for the same failed prompt
    (e.g. a redelivered update), the second call must still be safe — no
    exception, and the prompt remains recognised as stale even though the
    tracked ids were already forgotten after the first attempt."""
    from bot import _is_retired_essentials_prompt_ref, _retire_active_missing_essentials_prompt

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id
    context.bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message to edit not found"))
    context.bot.edit_message_reply_markup = AsyncMock(side_effect=BadRequest("Message to edit not found"))

    await _retire_active_missing_essentials_prompt(context)
    assert _is_retired_essentials_prompt_ref(context, sim.user_id, 42)

    # Second call: tracking keys are already gone, so this is a no-op, but
    # must not raise and must not un-mark the earlier stale reference.
    await _retire_active_missing_essentials_prompt(context)
    assert _is_retired_essentials_prompt_ref(context, sim.user_id, 42)


@pytest.mark.asyncio
async def test_stale_cancel_after_new_draft_does_not_cancel_it():
    """A Cancel tap that lands on a retired essentials prompt (edit/markup
    removal both failed, so the button is still visually live) must not wipe
    out a new draft that has since become active."""
    from bot import handle_callback

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id
    context.bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message to edit not found"))
    context.bot.edit_message_reply_markup = AsyncMock(side_effect=BadRequest("Message to edit not found"))

    from bot import _retire_active_missing_essentials_prompt

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
