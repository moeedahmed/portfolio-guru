"""Scoped replay protection for the chosen_form+awaiting_detail essentials
followup (`handle_case_input`).

Telegram can redeliver the same update (webhook retry, or PTB firing a
matching handler twice for one update) while a doctor's answer to the
missing-essentials prompt is mid-flight. These tests prove that a replayed
identical inbound never re-appends the case text or re-runs extraction/draft,
while a genuinely different follow-up message is never affected.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

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


def _common_patches():
    return (
        patch("bot.has_credentials", return_value=True),
        patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)),
        patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))),
    )


def _awaiting_detail_context(sim: BotSimulator, original_case: str):
    context = sim._make_context()
    context.user_data["case_text"] = original_case
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True
    return context


@pytest.mark.asyncio
async def test_concurrent_duplicate_same_update_only_extracts_once():
    """Two overlapping deliveries of the exact same update must not both reach
    extraction — the guard must trip before the in-flight call's first await
    completes, so the duplicate is caught mid-flight rather than racing it."""
    from bot import AWAIT_APPROVAL, AWAIT_CASE_INPUT, handle_case_input

    sim = BotSimulator()
    original_case = "45M with chest pain, troponin positive, managed as ACS."
    context = _awaiting_detail_context(sim, original_case)

    followup_update = sim._make_text_update(
        "I learned to escalate ECG review earlier next time."
    )
    complete_draft = _cbd_draft()
    release = asyncio.Event()

    async def slow_analyse(*args, **kwargs):
        await release.wait()
        return complete_draft

    analyse = AsyncMock(side_effect=slow_analyse)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot._analyse_selected_form", new=analyse):
        task = asyncio.create_task(handle_case_input(followup_update, context))
        # Hand control back until the in-flight call is genuinely parked on
        # the extraction await, however many awaits precede it.
        for _ in range(100):
            if analyse.await_count:
                break
            await asyncio.sleep(0)

        # The concurrent duplicate must bail out immediately, without a second
        # extraction call and without re-appending the case text again.
        duplicate_result = await handle_case_input(followup_update, context)
        assert duplicate_result == AWAIT_CASE_INPUT
        analyse.assert_awaited_once()

        release.set()
        first_result = await task

    assert first_result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    merged_case_text = analyse.await_args.args[2]
    assert merged_case_text.count("escalate ECG review earlier") == 1


@pytest.mark.asyncio
async def test_replay_after_successful_preview_does_not_reextract():
    """A genuine retry of the same update after a completed, successful
    preview must be a silent no-op: no re-append, no re-extraction, no
    duplicate message."""
    from bot import AWAIT_APPROVAL, handle_case_input

    sim = BotSimulator()
    original_case = "45M with chest pain, troponin positive, managed as ACS."
    context = _awaiting_detail_context(sim, original_case)

    followup_update = sim._make_text_update(
        "I learned to escalate ECG review earlier next time."
    )
    complete_draft = _cbd_draft()
    analyse = AsyncMock(return_value=complete_draft)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patch("bot._analyse_selected_form", new=analyse):
        first_result = await handle_case_input(followup_update, context)
        assert first_result == AWAIT_APPROVAL
        analyse.assert_awaited_once()
        case_text_after_first = context.user_data["case_text"]

        sim.clear_messages()
        replay_result = await handle_case_input(followup_update, context)

    assert replay_result == AWAIT_APPROVAL
    analyse.assert_awaited_once()  # still just once — the replay did not re-extract
    assert context.user_data["case_text"] == case_text_after_first
    assert sim.messages_sent == []  # nothing was resent for the replay


@pytest.mark.asyncio
async def test_different_subsequent_message_is_not_blocked():
    """A genuinely different follow-up (new update_id) after a completed
    preview must be processed normally, not swallowed by the replay guard."""
    from bot import AWAIT_APPROVAL, handle_case_input

    sim = BotSimulator()
    original_case = "45M with chest pain, troponin positive, managed as ACS."
    context = _awaiting_detail_context(sim, original_case)

    first_update = sim._make_text_update(
        "I learned to escalate ECG review earlier next time."
    )
    complete_draft = _cbd_draft()
    analyse = AsyncMock(return_value=complete_draft)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patch("bot._analyse_selected_form", new=analyse):
        first_result = await handle_case_input(first_update, context)
        assert first_result == AWAIT_APPROVAL
        analyse.assert_awaited_once()

        sim.clear_messages()
        second_update = sim._make_text_update("Also: the patient's ECG showed anterior STEMI.")
        second_result = await handle_case_input(second_update, context)

    assert second_result == AWAIT_APPROVAL
    assert analyse.await_count == 2
    second_merged_case_text = analyse.await_args.args[2]
    assert "anterior STEMI" in second_merged_case_text
    assert sim.messages_sent != []
