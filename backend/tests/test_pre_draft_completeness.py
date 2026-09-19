"""Handler/flow-level proof for the pre-draft and post-preview completeness gate.

One canonical decision (`_pre_draft_completeness_gaps`) is exercised through the
actual Telegram handlers here, not just as a bare helper call: form choice
(first preview), the awaiting-detail follow-up route, and the save handler's
post-preview safeguard against an essential removed by an edit.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import ConversationHandler

from models import CBDData, FormDraft
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


REFLECTIVE_CASE_TEXT = (
    "45M with chest pain, troponin positive, managed as ACS. "
    "I learned to escalate ECG review earlier and will do so in future."
)


def _cbd_draft(**overrides) -> FormDraft:
    fields = {**COMPLETE_CBD_FIELDS, **overrides}
    return FormDraft(form_type="CBD", uuid="uuid-cbd", fields=fields)


def _common_patches():
    return (
        patch("bot.has_credentials", return_value=True),
        patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)),
        patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))),
    )


@pytest.mark.asyncio
async def test_complete_draft_previews_immediately_no_gap_question():
    from bot import AWAIT_APPROVAL, handle_form_choice

    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = (
        "45M with chest pain, troponin positive, managed as ACS. "
        "I learned to escalate ECG review earlier and will do so in future."
    )

    draft = _cbd_draft()
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    text = sim.get_last_text()
    assert "still need" not in text.lower()
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons


@pytest.mark.asyncio
async def test_missing_reflection_shows_draft_with_add_reflection_offer():
    """Reflection is still nudged strongly — Save isn't offered until it's
    added — but the draft itself is shown immediately, never blocked on it."""
    from bot import AWAIT_APPROVAL, handle_form_choice

    sim = BotSimulator()
    original_case = "45M with chest pain, troponin positive, managed as ACS."
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = original_case

    thin_draft = _cbd_draft(reflection="")
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=thin_draft)):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    gap_note = sim.messages_sent[-2][1].lower()
    assert "reflection" in gap_note
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "ACTION|add_reflection_detail" in buttons
    assert "APPROVE|draft" not in buttons


@pytest.mark.asyncio
async def test_missing_clinical_setting_names_it_but_still_offers_save():
    """A missing non-reflection essential is an offer, not a wall: the draft
    shows immediately with Save still available."""
    from bot import AWAIT_APPROVAL, handle_form_choice

    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = (
        "45M with chest pain, troponin positive, managed as ACS. "
        "I learned to escalate ECG review earlier and will do so in future."
    )

    draft = _cbd_draft(clinical_setting="")
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    gap_note = sim.messages_sent[-2][1].lower()
    assert "clinical setting" in gap_note
    assert "reflection" not in gap_note
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons


@pytest.mark.asyncio
async def test_two_missing_items_named_together_reflection_still_gates_save():
    """Both gaps are named in one offer; Save stays gated only on reflection,
    the more clinically essential of the two."""
    from bot import AWAIT_APPROVAL, handle_form_choice

    sim = BotSimulator()
    original_case = "45M with chest pain, troponin positive, managed as ACS."
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = original_case

    both_missing = _cbd_draft(reflection="", clinical_setting="")
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=both_missing)):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    combined_text = sim.messages_sent[-2][1].lower()
    assert "reflection" in combined_text
    assert "clinical setting" in combined_text
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "ACTION|add_reflection_detail" in buttons
    assert "APPROVE|draft" not in buttons


@pytest.mark.asyncio
async def test_complete_followup_clears_gate_and_shows_save_without_reflection_warning():
    from bot import AWAIT_APPROVAL, handle_case_input

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = REFLECTIVE_CASE_TEXT
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True

    followup_update = sim._make_text_update(
        "Setting was ED. I learned to escalate ECG review earlier next time."
    )
    complete_draft = _cbd_draft()
    analyse = AsyncMock(return_value=complete_draft)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patch("bot._analyse_selected_form", new=analyse):
        result = await handle_case_input(followup_update, context)

    assert result == AWAIT_APPROVAL
    assert context.user_data.get("needs_reflection_detail") is not True
    text = sim.get_last_text()
    assert "reflection is needed before saving" not in text.lower()
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons
    assert "ACTION|add_reflection_detail" not in buttons


@pytest.mark.asyncio
async def test_original_source_and_attachment_state_preserved_across_followup():
    from bot import handle_case_input

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = (
        "45M with chest pain, troponin positive, managed as ACS. "
        "I learned to escalate ECG review earlier and will do so in future."
    )
    context.user_data["case_input_source"] = "voice"
    context.user_data["case_has_user_context"] = True
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True
    context.user_data["attachment_path"] = "/tmp/evidence.jpg"
    context.user_data["attachment_name"] = "evidence.jpg"
    context.user_data["attachment_kind"] = "photo"

    followup_update = sim._make_text_update("Setting was ED.")
    complete_draft = _cbd_draft()
    analyse = AsyncMock(return_value=complete_draft)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patch("bot._analyse_selected_form", new=analyse):
        await handle_case_input(followup_update, context)

    # A follow-up answering the gate's own question must not drop the
    # attachment or the case's original voice/photo provenance flags.
    assert context.user_data["attachment_path"] == "/tmp/evidence.jpg"
    assert context.user_data["attachment_name"] == "evidence.jpg"
    assert context.user_data["attachment_kind"] == "photo"
    assert context.user_data["case_input_source"] == "mixed"
    assert context.user_data["case_has_user_context"] is True
    merged_case_text = analyse.await_args.args[2]
    assert "troponin positive" in merged_case_text
    assert "Setting was ED" in merged_case_text


@pytest.mark.asyncio
async def test_document_followup_preserves_case_and_attachment_then_previews():
    """A document sent while awaiting the gate's own missing-detail answer must
    merge into the original case (not replace it) and go straight back to the
    chosen form's draft, not restart form recommendation."""
    from bot import AWAIT_APPROVAL, handle_document_intent

    sim = BotSimulator()
    context = sim._make_context()
    original_case = "45M with chest pain, troponin positive, managed as ACS."
    context.user_data["case_text"] = original_case
    context.user_data["case_input_source"] = "text"
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True
    context.user_data["attachment_path"] = "/tmp/evidence.jpg"
    context.user_data["attachment_name"] = "evidence.jpg"
    context.user_data["attachment_kind"] = "photo"

    update = sim._make_callback_update("DOCUSE|info")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "discharge-note.pdf"}

    complete_draft = _cbd_draft()
    analyse = AsyncMock(return_value=complete_draft)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.extract_from_document", new=AsyncMock(
             return_value="Setting was ED. I learned to escalate ECG review earlier and will do so in future."
         )), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_document_intent(update, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    merged_case_text = analyse.await_args.args[2]
    assert original_case in merged_case_text
    assert "Setting was ED" in merged_case_text
    assert context.user_data["case_input_source"] == "mixed"
    assert context.user_data["attachment_path"] == "/tmp/evidence.jpg"
    assert context.user_data["attachment_name"] == "evidence.jpg"
    assert context.user_data["attachment_kind"] == "photo"
    assert context.user_data["chosen_form"] == "CBD"
    text = sim.get_last_text()
    assert "still need" not in text.lower()
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_document_followup_still_names_remaining_gap_but_shows_draft():
    """A document reply that only fills one of two gaps must keep the case
    merged, name only what's still missing (not the answered gap), and show
    the draft immediately rather than asking again."""
    from bot import AWAIT_APPROVAL, handle_document_intent

    sim = BotSimulator()
    context = sim._make_context()
    original_case = "45M with chest pain, troponin positive, managed as ACS."
    context.user_data["case_text"] = original_case
    context.user_data["case_input_source"] = "text"
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True

    update = sim._make_callback_update("DOCUSE|info")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "discharge-note.pdf"}

    setting_still_missing = _cbd_draft(clinical_setting="")
    analyse = AsyncMock(return_value=setting_still_missing)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.extract_from_document", new=AsyncMock(
             return_value="I learned to escalate ECG review earlier next time."
         )), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_document_intent(update, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    merged_case_text = analyse.await_args.args[2]
    assert original_case in merged_case_text
    assert "escalate ECG review earlier" in merged_case_text
    remaining_ask = sim.messages_sent[-2][1].lower()
    assert "clinical setting" in remaining_ask
    assert "reflection" not in remaining_ask
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_optional_only_gaps_still_preview():
    from bot import AWAIT_APPROVAL, handle_form_choice

    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = REFLECTIVE_CASE_TEXT

    # curriculum_links/key_capabilities are optional CBD fields.
    draft = _cbd_draft(curriculum_links=[], key_capabilities=[])
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons


@pytest.mark.asyncio
async def test_nonreflective_draft_is_exempt_and_previews():
    from bot import AWAIT_APPROVAL, handle_form_choice

    sim = BotSimulator()
    update = sim._make_callback_update("FORM|OTHER")
    context = sim._make_context()
    context.user_data["case_text"] = "Certificate of attendance for a resuscitation course."

    # No reflection-shaped key at all, and an unregistered form_type has no
    # schema-required fields — a factual evidence record, not a reflective log.
    draft = FormDraft(
        form_type="OTHER",
        fields={
            "description": "A factual portfolio record.",
            "date_of_encounter": "2026-03-17",
        },
    )
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    text = sim.get_last_text().lower()
    assert "reflection is needed before saving" not in text


@pytest.mark.asyncio
async def test_removed_essential_field_after_preview_blocks_file_call():
    from bot import AWAIT_CASE_INPUT, handle_approval_approve

    sim = BotSimulator()
    update = sim._make_callback_update("APPROVE|draft")
    context = sim._make_context()
    context.user_data["case_text"] = REFLECTIVE_CASE_TEXT
    context.user_data["chosen_form"] = "CBD"
    # Reflection present but clinical_setting was cleared by an edit after preview.
    edited_draft = _cbd_draft(clinical_setting="")
    context.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": edited_draft.form_type,
        "fields": edited_draft.fields,
        "uuid": edited_draft.uuid,
    }

    route_filing = AsyncMock()
    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.route_filing", new=route_filing):
        result = await handle_approval_approve(update, context)

    assert result == AWAIT_CASE_INPUT
    route_filing.assert_not_awaited()
    text = sim.get_last_text().lower()
    assert "clinical setting" in text
    assert context.user_data["awaiting_detail"] is True
    # The still-valid case text/draft context is preserved for the follow-up merge.
    assert context.user_data["case_text"] == REFLECTIVE_CASE_TEXT


@pytest.mark.asyncio
async def test_intact_draft_still_saves_normally_after_the_essential_check():
    from bot import handle_approval_approve

    sim = BotSimulator()
    update = sim._make_callback_update("APPROVE|draft")
    context = sim._make_context()
    context.user_data["case_text"] = REFLECTIVE_CASE_TEXT
    context.user_data["chosen_form"] = "CBD"
    complete_draft = _cbd_draft()
    context.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": complete_draft.form_type,
        "fields": complete_draft.fields,
        "uuid": complete_draft.uuid,
    }

    route_filing = AsyncMock(
        return_value={"status": "success", "filled": [], "skipped": [], "method": "deterministic"}
    )
    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.route_filing", new=route_filing):
        result = await handle_approval_approve(update, context)

    assert result == ConversationHandler.END
    route_filing.assert_awaited()


@pytest.mark.asyncio
async def test_cancellation_resets_pending_detail_state():
    from bot import handle_callback

    sim = BotSimulator()
    update = sim._make_callback_update("CANCEL|draft")
    context = sim._make_context()
    context.user_data["case_text"] = REFLECTIVE_CASE_TEXT
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True

    result = await handle_callback(update, context)

    assert result == ConversationHandler.END
    assert "awaiting_detail" not in context.user_data
    assert "chosen_form" not in context.user_data
    assert "case_text" not in context.user_data


@pytest.mark.asyncio
async def test_new_case_resets_pending_detail_state_no_cross_case_contamination():
    from bot import handle_callback

    sim = BotSimulator()
    update = sim._make_callback_update("CASE|new")
    context = sim._make_context()
    context.user_data["case_text"] = REFLECTIVE_CASE_TEXT
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True

    await handle_callback(update, context)

    assert "awaiting_detail" not in context.user_data
    assert "chosen_form" not in context.user_data
    assert "case_text" not in context.user_data


@pytest.mark.asyncio
async def test_completeness_prompt_advertises_supported_reply_formats():
    """The essentials-gap prompt must name every reply format the AWAIT_CASE_INPUT
    handlers actually accept (text, voice/audio, photo, document, video), and for
    video it must say 'with a description' rather than implying transcription —
    the conversation handler map routes VOICE/AUDIO/PHOTO/VIDEO/Document.ALL to
    handle_case_input here, but a video attachment is only ever cached, never
    interpreted."""
    from bot import AWAIT_APPROVAL, handle_form_choice

    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = (
        "45M with chest pain, troponin positive, managed as ACS. "
        "I learned to escalate ECG review earlier and will do so in future."
    )

    thin_draft = _cbd_draft(reflection="")
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=thin_draft)):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    text = sim.messages_sent[-2][1].lower()
    assert "text" in text
    assert "voice" in text
    assert "audio" in text
    assert "photo" in text
    assert "document" in text
    assert "video" in text
    assert "description" in text
    assert "interpret" not in text and "transcri" not in text


@pytest.mark.asyncio
async def test_repeated_resume_with_unchanged_gap_edits_the_same_prompt():
    """A duplicate resume (retry/replay) with an unresolved gap must edit the
    already-tracked prompt message in place rather than sending a second one,
    so no accumulating stale prompt with a live Cancel button is left behind."""
    from bot import _resume_paused_flow

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["chosen_form"] = "CBD"
    context.user_data["pending_draft_data"] = {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {**COMPLETE_CBD_FIELDS, "reflection": ""},
        "uuid": "uuid-cbd",
    }

    update1 = sim._make_text_update("anything")
    await _resume_paused_flow(update1, context, "Resuming.")
    first_actions = [action for action, _, _ in sim.messages_sent]
    assert "reply" in first_actions
    tracked_id = context.user_data.get("last_bot_msg_id")
    assert tracked_id

    sim.clear_messages()
    update2 = sim._make_text_update("anything")
    await _resume_paused_flow(update2, context, "Resuming.")

    # The replay must edit the tracked message id, never send a fresh one.
    assert context.user_data.get("last_bot_msg_id") == tracked_id
    actions = [action for action, _, _ in sim.messages_sent]
    assert "bot_edit" in actions
    assert "reply" not in actions and "send" not in actions


@pytest.mark.asyncio
async def test_resolved_gap_retires_prompt_and_sends_fresh_draft_after_reply():
    """Once the gap is answered and the draft is complete, the old essentials
    prompt (which sits *before* the doctor's reply) must be retired — deleted
    outright, since it is bot-owned and fully resolved — and the draft
    preview sent as a fresh message after the reply, instead of the final
    draft being edited into a message that chronologically precedes the
    doctor's own answer."""
    from bot import AWAIT_APPROVAL, handle_case_input

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = REFLECTIVE_CASE_TEXT
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True

    # Simulate the essentials prompt already being the tracked bot message.
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id

    followup_update = sim._make_text_update("Setting was ED. I learned to escalate ECG review earlier next time.")
    complete_draft = _cbd_draft()
    analyse = AsyncMock(return_value=complete_draft)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patch("bot._analyse_selected_form", new=analyse):
        result = await handle_case_input(followup_update, context)

    assert result == AWAIT_APPROVAL
    actions = [action for action, _, _ in sim.messages_sent]
    # The old prompt (message 42) is retired by deleting it outright, then a
    # genuinely new message is sent for the acknowledgement, which is itself
    # edited into the draft.
    assert actions[0] == "bot_delete"
    assert "reply" in actions
    assert context.user_data.get("last_bot_msg_id") != 42
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons
    assert "ACTION|cancel" not in buttons


@pytest.mark.asyncio
async def test_exact_deidentified_screenshot_learning_sentence_accepted_in_preview():
    from bot import AWAIT_APPROVAL, handle_form_choice

    # Deidentified doctor-authored source text supplied for this exact repair.
    learning_sentence = (
        "Learning point was how kids with autism or non-verbal would not "
        "tell about pain and injury, important for clinician to do "
        "thorough examination and assessment."
    )
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = (
        "Assessed a distressed non-verbal child for a limb injury. " + learning_sentence
    )

    draft = _cbd_draft(reflection=learning_sentence)
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    text = sim.get_last_text()
    assert "reflection is needed before saving" not in text.lower()
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons
    assert "ACTION|add_reflection_detail" not in buttons


@pytest.mark.asyncio
async def test_edit_failure_on_remaining_gap_preserves_merged_answer_and_raises():
    """If Telegram genuinely fails to edit the fresh acknowledgement message
    with the still-outstanding gap (not the harmless 'message not modified'
    case), the failure must not be swallowed as a false success. The user's
    partial answer is merged into ``case_text`` before the edit is even
    attempted, so it — and the still-active gate — must survive the failed
    edit rather than being lost or silently reported as done."""
    from telegram.error import BadRequest

    from bot import handle_case_input

    sim = BotSimulator()
    context = sim._make_context()
    original_case = "45M with chest pain, troponin positive, managed as ACS."
    context.user_data["case_text"] = original_case
    context.user_data["chosen_form"] = "CBD"
    context.user_data["awaiting_detail"] = True
    context.user_data["last_bot_msg_id"] = 42
    context.user_data["last_bot_chat_id"] = sim.user_id

    followup_update = sim._make_text_update(
        "I learned to escalate ECG review earlier next time."
    )
    setting_still_missing = _cbd_draft(clinical_setting="")
    analyse = AsyncMock(return_value=setting_still_missing)

    # The old prompt (message 42) is deleted directly through the bot; the
    # failure under test happens on the fresh acknowledgement message's own
    # edit-in-place (its "still missing" gap update), not the deletion call.
    original_capture_edit = sim._capture_edit
    call_count = {"n": 0}

    async def flaky_edit(text=None, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise BadRequest("Message to edit not found")
        return await original_capture_edit(text, *args, **kwargs)

    sim._capture_edit = flaky_edit

    patches = _common_patches()
    with patches[0], patches[1], patches[2], patch("bot._analyse_selected_form", new=analyse):
        with pytest.raises(BadRequest):
            await handle_case_input(followup_update, context)

    assert call_count["n"] == 1
    # The old prompt was still retired (deleted) before the failure.
    actions = [action for action, _, _ in sim.messages_sent]
    assert "bot_delete" in actions
    # The merged answer and the still-open gate survive the failed edit —
    # nothing is lost, and the state is not falsely marked resolved.
    assert "escalate ECG review earlier" in context.user_data["case_text"]
    assert context.user_data["awaiting_detail"] is True
    assert context.user_data["chosen_form"] == "CBD"
