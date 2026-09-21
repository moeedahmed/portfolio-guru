"""Essential-first workflow: sufficiency is settled before anything is drafted.

Owner's decision (19 Sep 2026). The bot picks the form, then reads the case
the doctor already sent against that form's genuinely essential requirements
*before* the drafting call. A complete judgement that every essential is
present is the only thing that permits drafting:

* still missing → one grouped question naming only what is genuinely still
  absent; an item the doctor has since supplied is never asked again, and an
  item still absent is never waved through because it was asked about once;
* unavailable to the doctor → the case is kept and a different form offered,
  never invented and never skipped;
* judgement incomplete (outage, timeout, partial/duplicated/malformed answer)
  → the case is kept and the check retried, because a requirement that was
  never judged cannot be assumed satisfied.

These tests drive the real handlers, and several assert the *drafting call
count is zero*. The model's judgement is stubbed at
`bot.assess_form_essentials` — that proves the routing and the workflow, not
the model's clinical reading (see `.essential-first-report.md`).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

import bot
from bot import (
    AWAIT_APPROVAL,
    AWAIT_CASE_INPUT,
    ESSENTIAL_MISSING,
    ESSENTIAL_PRESENT,
    ESSENTIAL_UNAVAILABLE,
    handle_callback,
    handle_case_input,
    handle_document_intent,
    handle_form_choice,
)
from models import FormDraft
from tests.bot_simulator import BotSimulator


COMPLETE_CASE = (
    "45M presented to the Emergency Department with chest pain and a positive troponin. "
    "I assessed him, managed it as ACS with indirect consultant supervision, and discussed "
    "the ECG changes. I learned to escalate ECG review earlier and will do so in future."
)

THIN_CASE = "45M with chest pain, troponin positive, managed as ACS."

CBD_FIELDS = {
    "date_of_encounter": "2026-03-17",
    "clinical_setting": "Emergency Department",
    "patient_presentation": "Chest pain",
    "stage_of_training": "Higher/ST4-ST6",
    "trainee_role": "Assessed and managed the patient",
    "clinical_reasoning": "Managed as ACS.",
    "reflection": "I learned to escalate ECG review earlier and will do so in future.",
    "level_of_supervision": "Indirect",
    "curriculum_links": ["SLO1"],
    "key_capabilities": ["SLO1 KC1: Assess and stabilise the patient"],
}

DOPS_FIELDS = {
    "date_of_encounter": "2026-03-17",
    "procedure_name": "Fracture / Dislocation manipulation",
    "procedural_skill": "Fracture / Dislocation manipulation",
    "clinical_setting": "Emergency Department",
    "stage_of_training": "Higher/ST4-ST6",
    "indication": "Displaced distal radius fracture needing reduction.",
    "trainee_performance": "I reduced the fracture under sedation with the consultant present.",
    "reflection": "",
}


def _cbd_draft(**overrides) -> FormDraft:
    return FormDraft(form_type="CBD", uuid="uuid-cbd", fields={**CBD_FIELDS, **overrides})


def _dops_draft(**overrides) -> FormDraft:
    return FormDraft(form_type="DOPS", uuid="uuid-dops", fields={**DOPS_FIELDS, **overrides})


def _all(form_type: str, status: str, **overrides) -> dict[str, str]:
    """Every essential of `form_type` at `status`, with named exceptions."""
    statuses = {item["key"]: status for item in bot._form_essential_requirements(form_type)}
    statuses.update(overrides)
    return statuses


def _assess(statuses: dict[str, str]) -> AsyncMock:
    return AsyncMock(return_value=statuses)


def _common_patches():
    return (
        patch("bot.has_credentials", return_value=True),
        patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)),
        patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))),
    )


def _texts(sim: BotSimulator) -> list[str]:
    return [text or "" for _, text, _ in sim.messages_sent]


# --- complete case: no questions, one draft --------------------------------


@pytest.mark.asyncio
async def test_sufficient_case_drafts_directly_with_no_question():
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = COMPLETE_CASE

    analyse = AsyncMock(return_value=_cbd_draft())
    assess = _assess(_all("CBD", ESSENTIAL_PRESENT))
    with patch("bot.assess_form_essentials", new=assess), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    assess.assert_awaited_once()
    joined = " ".join(_texts(sim)).lower()
    assert "i still need" not in joined
    assert "isn't available" not in joined
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons


@pytest.mark.asyncio
async def test_sufficient_case_preview_is_the_draft_only():
    """No reflection warning, no missing-field footer, no collection
    instructions riding along with a draft whose essentials are settled."""
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = COMPLETE_CASE

    with patch("bot.assess_form_essentials", new=_assess(_all("CBD", ESSENTIAL_PRESENT))), \
         patch("bot._analyse_selected_form", new=AsyncMock(return_value=_cbd_draft())):
        await handle_form_choice(update, context)

    preview = sim.get_last_text().lower()
    assert "reflection is needed before saving" not in preview
    assert "still needed:" not in preview
    assert "send it as" not in preview
    assert context.user_data.get("needs_reflection_detail") is not True


# --- missing essentials: one grouped question, before drafting -------------


@pytest.mark.asyncio
async def test_missing_essentials_asked_once_before_any_drafting_call():
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE

    analyse = AsyncMock(return_value=_cbd_draft())
    statuses = _all(
        "CBD",
        ESSENTIAL_PRESENT,
        reflection=ESSENTIAL_MISSING,
        level_of_supervision=ESSENTIAL_MISSING,
    )
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_CASE_INPUT
    analyse.assert_not_awaited()

    asks = [text for text in _texts(sim) if "I still need" in (text or "")]
    assert len(asks) == 1, "essentials must be one grouped question, not several"
    ask = asks[0].lower()
    assert "reflection" in ask
    assert "level of supervision" in ask
    # Only the missing ones are named, and optional fields never appear.
    assert "patient presentation" not in ask
    assert "curriculum" not in ask and "key capabilities" not in ask
    assert context.user_data["awaiting_detail"] is True
    assert context.user_data["chosen_form"] == "CBD"
    assert context.user_data["case_text"] == THIN_CASE


@pytest.mark.asyncio
async def test_optional_fields_are_never_asked_for():
    """A case missing only optional detail drafts straight through: the gate
    is built from schema-required fields alone."""
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|MINI_CEX")
    context = sim._make_context()
    context.user_data["case_text"] = COMPLETE_CASE

    essentials = {item["key"] for item in bot._form_essential_requirements("MINI_CEX")}
    assert "complexity" not in essentials
    assert "curriculum_links" not in essentials
    assert "key_capabilities" not in essentials
    assert "date_of_encounter" not in essentials
    assert "stage_of_training" not in essentials

    draft = FormDraft(form_type="MINI_CEX", uuid="uuid-mini", fields={
        "date_of_encounter": "2026-03-17",
        "clinical_setting": "Emergency Department",
        "patient_presentation": "Chest pain",
        "stage_of_training": "Higher/ST4-ST6",
        "clinical_reasoning": "Managed as ACS.",
        "reflection": "I learned to escalate ECG review earlier.",
        "complexity": "",
    })
    analyse = AsyncMock(return_value=draft)
    with patch("bot.assess_form_essentials", new=_assess(_all("MINI_CEX", ESSENTIAL_PRESENT))), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()


@pytest.mark.asyncio
async def test_dops_optional_reflection_is_not_a_blocker():
    """DOPS marks reflection optional in the Kaizen schema: it must not be
    asked for before drafting, and must not gate the save afterwards."""
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|DOPS")
    context = sim._make_context()
    context.user_data["case_text"] = (
        "Manipulated a displaced distal radius fracture under sedation in the ED "
        "with the consultant present."
    )

    assert "reflection" not in {item["key"] for item in bot._form_essential_requirements("DOPS")}

    analyse = AsyncMock(return_value=_dops_draft())
    with patch("bot.assess_form_essentials", new=_assess(_all("DOPS", ESSENTIAL_PRESENT))), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    assert context.user_data.get("needs_reflection_detail") is not True
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons
    assert "ACTION|add_reflection_detail" not in buttons
    assert "reflection is needed before saving" not in sim.get_last_text().lower()


@pytest.mark.asyncio
async def test_cbd_reflection_is_essential_per_schema():
    assert "reflection" in {item["key"] for item in bot._form_essential_requirements("CBD")}
    assert bot._form_requires_reflection("CBD") is True
    assert bot._form_requires_reflection("DOPS") is False


# --- the follow-up: every input mode, nothing lost, nothing re-asked -------


async def _ask_first(sim, context, *, form_type="CBD", missing=("reflection",)):
    update = sim._make_callback_update(f"FORM|{form_type}")
    statuses = _all(form_type, ESSENTIAL_PRESENT, **{key: ESSENTIAL_MISSING for key in missing})
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot._analyse_selected_form", new=AsyncMock()):
        state = await handle_form_choice(update, context)
    assert state == AWAIT_CASE_INPUT
    sim.clear_messages()


@pytest.mark.asyncio
async def test_text_followup_merges_and_drafts_once_without_reasking():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _ask_first(sim, context)

    analyse = AsyncMock(return_value=_cbd_draft())
    reply = sim._make_text_update("I learned to escalate ECG review earlier and will do so in future.")
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.assess_form_essentials", new=_assess(_all("CBD", ESSENTIAL_PRESENT))), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_case_input(reply, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    merged = analyse.await_args.args[2]
    assert THIN_CASE in merged
    assert "escalate ECG review earlier" in merged
    assert "I still need" not in " ".join(_texts(sim))


@pytest.mark.asyncio
async def test_partial_answer_asks_only_the_remaining_essential_and_does_not_draft():
    """One of two gaps answered: the case keeps everything already sent, the
    answered gap is never asked again, the one genuinely still missing is
    asked for on its own — and nothing is drafted from it."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _ask_first(sim, context, missing=("reflection", "level_of_supervision"))

    analyse = AsyncMock(return_value=_cbd_draft(level_of_supervision=""))
    reply = sim._make_text_update("I learned to escalate ECG review earlier next time.")
    still_missing = _all(
        "CBD",
        ESSENTIAL_PRESENT,
        level_of_supervision=ESSENTIAL_MISSING,
    )
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.assess_form_essentials", new=_assess(still_missing)), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_case_input(reply, context)

    assert result == AWAIT_CASE_INPUT
    assert analyse.await_count == 0, "a partial answer must not produce a draft"
    # Everything the doctor has sent so far is still the case.
    assert THIN_CASE in context.user_data["case_text"]
    assert "escalate ECG review earlier" in context.user_data["case_text"]

    asks = [text for text in _texts(sim) if "I still need" in text]
    assert len(asks) == 1
    assert "level of supervision" in asks[0].lower()
    assert "reflection" not in asks[0].lower(), "an answered item must never be asked again"


@pytest.mark.asyncio
async def test_second_answer_completing_the_essentials_finally_drafts():
    """The other half of the partial-answer case: once the last genuinely
    missing essential arrives, the draft is produced from the full combined
    source — one drafting call, no earlier ones."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _ask_first(sim, context, missing=("reflection", "level_of_supervision"))

    analyse = AsyncMock(return_value=_cbd_draft())
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.assess_form_essentials", new=_assess(
             _all("CBD", ESSENTIAL_PRESENT, level_of_supervision=ESSENTIAL_MISSING))), \
         patch("bot._analyse_selected_form", new=analyse):
        await handle_case_input(
            sim._make_text_update("I learned to escalate ECG review earlier next time."),
            context,
        )
    assert analyse.await_count == 0

    with patches[0], patches[1], patches[2], \
         patch("bot.assess_form_essentials", new=_assess(_all("CBD", ESSENTIAL_PRESENT))), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_case_input(
            sim._make_text_update("The consultant supervised indirectly."), context
        )

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    merged = analyse.await_args.args[2]
    assert THIN_CASE in merged
    assert "escalate ECG review earlier" in merged
    assert "consultant supervised indirectly" in merged


@pytest.mark.asyncio
async def test_voice_followup_merges_and_drafts():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    context.user_data["case_input_source"] = "text"
    await _ask_first(sim, context)

    update = sim._make_text_update("")
    update.message.text = None
    voice = AsyncMock()
    voice.file_name = "voice.ogg"
    voice.mime_type = "audio/ogg"
    voice_file = AsyncMock()
    voice.get_file = AsyncMock(return_value=voice_file)
    voice_file.download_to_drive = AsyncMock()
    update.message.voice = voice

    analyse = AsyncMock(return_value=_cbd_draft())
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.transcribe_voice", new=AsyncMock(
             return_value="I learned to escalate ECG review earlier next time.")), \
         patch("bot.assess_form_essentials", new=_assess(_all("CBD", ESSENTIAL_PRESENT))), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_case_input(update, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    merged = analyse.await_args.args[2]
    assert THIN_CASE in merged
    assert "escalate ECG review earlier" in merged
    assert context.user_data["case_input_source"] == "mixed"


@pytest.mark.asyncio
async def test_photo_followup_merges_and_drafts():
    """A photo answering the question keeps its existing use/attach choice,
    then merges into the case exactly like text does."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    context.user_data["case_input_source"] = "text"
    await _ask_first(sim, context)

    update = sim._make_callback_update("DOCUSE|info")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
        temp_path = handle.name
        handle.write(b"not really a jpeg")
    context.user_data["_pending_doc"] = {
        "path": temp_path,
        "name": "notes.jpg",
        "kind": "image",
    }

    analyse = AsyncMock(return_value=_cbd_draft())
    patches = _common_patches()
    try:
        with patches[0], patches[1], patches[2], \
             patch("bot._read_image_text", new=AsyncMock(
                 return_value=("Learning point: escalate ECG review earlier next time.", []))), \
             patch("bot.assess_form_essentials", new=_assess(_all("CBD", ESSENTIAL_PRESENT))), \
             patch("bot._analyse_selected_form", new=analyse):
            result = await handle_document_intent(update, context)

        assert result == AWAIT_APPROVAL
        analyse.assert_awaited_once()
        merged = analyse.await_args.args[2]
        assert THIN_CASE in merged
        assert "escalate ECG review earlier" in merged
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@pytest.mark.asyncio
async def test_photo_answer_reaches_the_followup_route_not_a_new_case():
    """The photo itself lands on the existing use/attach prompt and keeps the
    open question's state, rather than restarting form recommendation."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _ask_first(sim, context)

    update = sim._make_text_update("")
    update.message.text = None
    photo = MagicMock()
    photo_file = AsyncMock()
    photo.get_file = AsyncMock(return_value=photo_file)
    photo_file.download_to_drive = AsyncMock()
    update.message.photo = [photo]

    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot._read_image_text", new=AsyncMock(return_value=("Learning point.", []))), \
         patch("bot._analyse_selected_form", new=AsyncMock()) as analyse:
        await handle_case_input(update, context)

    analyse.assert_not_awaited()
    assert context.user_data["case_text"] == THIN_CASE
    assert context.user_data["chosen_form"] == "CBD"
    assert context.user_data["awaiting_detail"] is True


@pytest.mark.asyncio
async def test_document_followup_merges_and_drafts():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    context.user_data["case_input_source"] = "text"
    await _ask_first(sim, context)

    update = sim._make_callback_update("DOCUSE|info")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        temp_path = handle.name
        handle.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "clinic-note.pdf"}

    analyse = AsyncMock(return_value=_cbd_draft())
    patches = _common_patches()
    try:
        with patches[0], patches[1], patches[2], \
             patch("bot.extract_from_document", new=AsyncMock(
                 return_value="Learning point: escalate ECG review earlier next time.")), \
             patch("bot.assess_form_essentials", new=_assess(_all("CBD", ESSENTIAL_PRESENT))), \
             patch("bot._analyse_selected_form", new=analyse):
            result = await handle_document_intent(update, context)

        assert result == AWAIT_APPROVAL
        analyse.assert_awaited_once()
        merged = analyse.await_args.args[2]
        assert THIN_CASE in merged
        assert "escalate ECG review earlier" in merged
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


# --- an essential the doctor cannot supply ---------------------------------


@pytest.mark.asyncio
async def test_unavailable_essential_keeps_case_and_offers_a_different_form():
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE

    analyse = AsyncMock(return_value=_cbd_draft())
    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_UNAVAILABLE)
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_CASE_INPUT
    analyse.assert_not_awaited()
    offer = sim.get_last_text().lower()
    assert "isn't available" in offer
    assert "won't invent it" in offer
    # The case is retained exactly as sent — nothing is discarded or invented.
    assert context.user_data["case_text"] == THIN_CASE
    buttons = {data for _, data in sim.get_last_buttons()}
    assert buttons & {"FORM|back", "FORM|show_all"}


@pytest.mark.asyncio
async def test_change_of_form_reassesses_against_the_new_form():
    """After the offer, picking a form whose schema does not require the
    unavailable detail drafts from the same retained case."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE

    cbd_update = sim._make_callback_update("FORM|CBD")
    with patch("bot.assess_form_essentials", new=_assess(
            _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_UNAVAILABLE))), \
         patch("bot._analyse_selected_form", new=AsyncMock()):
        assert await handle_form_choice(cbd_update, context) == AWAIT_CASE_INPUT

    sim.clear_messages()
    analyse = AsyncMock(return_value=_dops_draft())
    dops_update = sim._make_callback_update("FORM|DOPS")
    assess = _assess(_all("DOPS", ESSENTIAL_PRESENT))
    with patch("bot.assess_form_essentials", new=assess), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(dops_update, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    assert analyse.await_args.args[2] == THIN_CASE
    assess.assert_awaited_once()
    assert assess.await_args.args[1] == "DOPS"


# --- caching, cancellation, retry ------------------------------------------


@pytest.mark.asyncio
async def test_sufficiency_is_cached_per_case_and_form():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = COMPLETE_CASE

    assess = _assess(_all("CBD", ESSENTIAL_PRESENT))
    analyse = AsyncMock(return_value=_cbd_draft())
    with patch("bot.assess_form_essentials", new=assess), \
         patch("bot._analyse_selected_form", new=analyse):
        await handle_form_choice(sim._make_callback_update("FORM|CBD"), context)
        context.user_data["chosen_form"] = "CBD"
        await handle_callback(sim._make_callback_update("ACTION|retry_template"), context)

    assert assess.await_count == 1, "same case + same form must not be re-judged"
    assert analyse.await_count == 2


@pytest.mark.asyncio
async def test_changed_case_text_is_reassessed():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = COMPLETE_CASE

    assess = _assess(_all("CBD", ESSENTIAL_PRESENT))
    with patch("bot.assess_form_essentials", new=assess), \
         patch("bot._analyse_selected_form", new=AsyncMock(return_value=_cbd_draft())):
        await handle_form_choice(sim._make_callback_update("FORM|CBD"), context)
        assert await bot._assess_case_essentials(context, COMPLETE_CASE + " Extra.", "CBD")

    assert assess.await_count == 2


@pytest.mark.asyncio
async def test_cancel_clears_the_pending_essentials_question():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _ask_first(sim, context)

    result = await handle_callback(sim._make_callback_update("CANCEL|draft"), context)

    assert result == ConversationHandler.END
    assert bot._ESSENTIALS_ASSESSMENT_KEY not in context.user_data
    assert "awaiting_detail" not in context.user_data
    assert "case_text" not in context.user_data


@pytest.mark.asyncio
async def test_new_case_clears_essentials_state():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _ask_first(sim, context)

    await handle_callback(sim._make_callback_update("CASE|new"), context)

    assert bot._ESSENTIALS_ASSESSMENT_KEY not in context.user_data


@pytest.mark.asyncio
async def test_retry_with_the_case_unchanged_reuses_the_judgement_and_still_asks():
    """Retry is not a way past the question. The cached judgement stands (no
    second model call), the outstanding essential is still outstanding, and
    nothing is drafted."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _ask_first(sim, context)

    analyse = AsyncMock(return_value=_cbd_draft())
    assess = _assess(_all("CBD", ESSENTIAL_PRESENT))
    with patch("bot.assess_form_essentials", new=assess), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_callback(sim._make_callback_update("ACTION|retry_template"), context)

    assert result == AWAIT_CASE_INPUT
    assert analyse.await_count == 0
    assess.assert_not_awaited()
    assert any("I still need" in text for text in _texts(sim))


# --- an assessment that did not complete: never draft anyway ---------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        AsyncMock(side_effect=RuntimeError("provider down")),
        AsyncMock(side_effect=asyncio.TimeoutError()),
        AsyncMock(return_value={}),
    ],
    ids=["outage", "timeout", "unusable_answer"],
)
async def test_incomplete_assessment_never_drafts_and_offers_a_retry(failure):
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = COMPLETE_CASE

    analyse = AsyncMock(return_value=_cbd_draft())
    with patch("bot.assess_form_essentials", new=failure), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_CASE_INPUT
    assert analyse.await_count == 0, "a requirement that was never judged cannot be drafted from"
    retry = sim.get_last_text().lower()
    assert "couldn't finish checking" in retry
    assert "saved exactly as you sent it" in retry
    # The case survives, and an unusable judgement is never cached.
    assert context.user_data["case_text"] == COMPLETE_CASE
    assert bot._ESSENTIALS_ASSESSMENT_KEY not in context.user_data
    assert "ACTION|retry_template" in {data for _, data in sim.get_last_buttons()}


@pytest.mark.asyncio
async def test_retry_after_a_failed_check_reruns_it_and_drafts_when_it_succeeds():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = COMPLETE_CASE

    analyse = AsyncMock(return_value=_cbd_draft())
    with patch("bot.assess_form_essentials", new=AsyncMock(side_effect=RuntimeError("down"))), \
         patch("bot._analyse_selected_form", new=analyse):
        assert await handle_form_choice(sim._make_callback_update("FORM|CBD"), context) == AWAIT_CASE_INPUT
    assert analyse.await_count == 0

    with patch("bot.assess_form_essentials", new=_assess(_all("CBD", ESSENTIAL_PRESENT))), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_callback(sim._make_callback_update("ACTION|retry_template"), context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.essentials_gate
@pytest.mark.parametrize(
    "answer",
    [
        '{"items": [{"key": "reflection", "status": "present"}]}',
        '{"items": []}',
        "not json at all",
        '{"nope": true}',
    ],
    ids=["partial_keys", "empty_items", "unparseable", "wrong_shape"],
)
async def test_partial_or_malformed_model_answers_are_no_judgement_at_all(answer):
    """A judgement that does not cover every essential exactly once is not a
    judgement — returning the part that parsed would let an unjudged
    requirement through as "not flagged missing"."""
    import extractor

    async def fake_generate(prompt, *args, **kwargs):
        return answer

    with patch("extractor._generate", new=fake_generate):
        statuses = await extractor.assess_form_essentials(
            COMPLETE_CASE,
            "CBD",
            bot._form_essential_requirements("CBD"),
        )

    assert statuses == {}


@pytest.mark.asyncio
@pytest.mark.essentials_gate
async def test_duplicate_or_unasked_keys_are_rejected():
    import extractor

    essentials = [
        {"key": "reflection", "label": "Reflection"},
        {"key": "clinical_setting", "label": "Clinical Setting"},
    ]

    async def duplicated(prompt, *args, **kwargs):
        return (
            '{"items": [{"key": "reflection", "status": "present"},'
            ' {"key": "reflection", "status": "missing"},'
            ' {"key": "clinical_setting", "status": "present"}]}'
        )

    async def padded(prompt, *args, **kwargs):
        return (
            '{"items": [{"key": "reflection", "status": "present"},'
            ' {"key": "clinical_setting", "status": "present"},'
            ' {"key": "invented_requirement", "status": "missing"}]}'
        )

    with patch("extractor._generate", new=duplicated):
        assert await extractor.assess_form_essentials("A case.", "CBD", essentials) == {}
    with patch("extractor._generate", new=padded):
        assert await extractor.assess_form_essentials("A case.", "CBD", essentials) == {}


@pytest.mark.asyncio
@pytest.mark.essentials_gate
async def test_a_complete_valid_answer_is_accepted():
    import extractor

    essentials = [
        {"key": "reflection", "label": "Reflection"},
        {"key": "clinical_setting", "label": "Clinical Setting"},
    ]

    async def complete(prompt, *args, **kwargs):
        return (
            '```json\n{"items": [{"key": "reflection", "status": "missing"},'
            ' {"key": "clinical_setting", "status": "present"}]}\n```'
        )

    with patch("extractor._generate", new=complete):
        statuses = await extractor.assess_form_essentials("A case.", "CBD", essentials)

    assert statuses == {"reflection": "missing", "clinical_setting": "present"}


# --- every drafting entrypoint is gated ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["form_choice", "retry_template", "case_improve", "accumulate"])
async def test_all_drafting_entrypoints_route_through_the_gate(entrypoint):
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    context.user_data["chosen_form"] = "CBD"

    analyse = AsyncMock(return_value=_cbd_draft())
    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_MISSING)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot._analyse_selected_form", new=analyse):
        if entrypoint == "form_choice":
            result = await handle_form_choice(sim._make_callback_update("FORM|CBD"), context)
        elif entrypoint == "retry_template":
            result = await handle_callback(sim._make_callback_update("ACTION|retry_template"), context)
        elif entrypoint == "case_improve":
            context.user_data["pending_new_case_text"] = "The consultant supervised indirectly."
            result = await handle_callback(sim._make_callback_update("CASE|improve"), context)
        else:
            context.user_data["pending_draft_data"] = {
                "_type": "FORM",
                "form_type": "CBD",
                "fields": dict(CBD_FIELDS),
                "uuid": "uuid-cbd",
            }
            result = await bot._accumulate_and_refresh(
                sim._make_text_update("The consultant supervised indirectly."),
                context,
                "The consultant supervised indirectly.",
            )

    assert result == AWAIT_CASE_INPUT, f"{entrypoint} drafted without settling essentials"
    analyse.assert_not_awaited()
    assert any("I still need" in text for text in _texts(sim))
    # Whatever the doctor had already sent is still the case, exactly once.
    assert context.user_data["case_text"].count(THIN_CASE) == 1
    assert "accumulation_additions" not in context.user_data


@pytest.mark.asyncio
async def test_resume_paused_flow_is_gated_too():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    context.user_data["chosen_form"] = "CBD"
    context.user_data["pending_draft_data"] = {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {**CBD_FIELDS, "reflection": ""},
        "uuid": "uuid-cbd",
    }

    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_MISSING)
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot._setup_needs_finishing", return_value=False):
        result = await bot._resume_paused_flow(sim._make_text_update("anything"), context, "Resuming.")

    assert result == AWAIT_CASE_INPUT
    assert any("I still need" in text for text in _texts(sim))


# --- grounding ------------------------------------------------------------


def test_extraction_prompts_carry_the_source_fidelity_rules():
    """The instructions that keep a draft faithful to what the doctor wrote
    are in the prompt the product actually sends."""
    import extractor

    rules = extractor._SOURCE_FIDELITY_RULES
    assert "FAST was done" in rules
    assert "I performed" in rules
    assert "unspecified \"CT\"" in rules
    assert "Never write a personal reflection the doctor did not supply" in rules

    sources = open(extractor.__file__).read()
    assert sources.count("{_SOURCE_FIDELITY_RULES}") == 2, (
        "both the generic-form and CBD extraction prompts must carry them"
    )


@pytest.mark.asyncio
@pytest.mark.essentials_gate
async def test_essentials_prompt_judges_attribution_and_specificity():
    """The sufficiency call sends the doctor's words and the grounding rules,
    and never asks the model to write portfolio content."""
    import extractor

    captured = {}

    async def fake_generate(prompt, *args, **kwargs):
        captured["prompt"] = prompt
        captured["purpose"] = kwargs.get("purpose")
        return '{"items": [{"key": "reflection", "status": "missing"}]}'

    with patch("extractor._generate", new=fake_generate):
        statuses = await extractor.assess_form_essentials(
            "FAST was done, a CT was arranged.",
            "CBD",
            [{"key": "reflection", "label": "Reflection", "description": "the doctor's own learning"}],
        )

    assert statuses == {"reflection": "missing"}
    prompt = captured["prompt"]
    assert captured["purpose"] == "form_essentials_sufficiency"
    assert "FAST was done" in prompt
    assert "Attribution matters" in prompt
    assert "Specificity matters" in prompt
    assert "You are NOT writing the portfolio entry" in prompt


@pytest.mark.asyncio
async def test_no_draft_is_written_while_the_doctors_learning_is_still_missing():
    """RCEM: the model may structure a reflection, never author one. While a
    CBD's required reflection is still judged absent from the doctor's own
    words, no drafting call happens at all — there is no draft for a
    model-written reflection to appear in."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _ask_first(sim, context)

    invented = _cbd_draft(reflection="This case reinforced the importance of early ECG review.")
    analyse = AsyncMock(return_value=invented)
    reply = sim._make_text_update("The consultant supervised indirectly.")
    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_MISSING)
    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_case_input(reply, context)

    assert result == AWAIT_CASE_INPUT
    assert analyse.await_count == 0
    ask = [text for text in _texts(sim) if "I still need" in text]
    assert ask and "reflection" in ask[-1].lower()


@pytest.mark.asyncio
async def test_refine_does_not_regenerate_a_draft_from_missing_essentials():
    """An edit is drafting too: with an essential judged missing in the
    combined source, the regeneration does not happen and the doctor's
    combined case — original plus the new reply — is kept."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    context.user_data["chosen_form"] = "CBD"
    context.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {**CBD_FIELDS, "reflection": ""},
        "uuid": "uuid-cbd",
    }

    extract = AsyncMock(return_value=_cbd_draft())
    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_MISSING)
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot.extract_cbd_data", new=extract), \
         patch("bot.extract_form_data", new=extract):
        result = await bot._regenerate_active_draft_with_feedback(
            sim._make_text_update("Add that the registrar was present."),
            context,
            "Add that the registrar was present.",
            append_to_case=True,
        )

    assert result == AWAIT_CASE_INPUT
    assert extract.await_count == 0, "an edit must not rebuild a draft from missing essentials"
    assert THIN_CASE in context.user_data["case_text"]
    assert "registrar was present" in context.user_data["case_text"]
    assert any("I still need" in text for text in _texts(sim))


@pytest.mark.asyncio
async def test_refine_regenerates_once_the_reply_completes_the_essentials():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    context.user_data["chosen_form"] = "CBD"
    context.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {**CBD_FIELDS, "reflection": ""},
        "uuid": "uuid-cbd",
    }

    extract = AsyncMock(return_value=_cbd_draft())
    with patch("bot.assess_form_essentials", new=_assess(_all("CBD", ESSENTIAL_PRESENT))), \
         patch("bot.extract_cbd_data", new=extract), \
         patch("bot.extract_form_data", new=extract):
        result = await bot._regenerate_active_draft_with_feedback(
            sim._make_text_update("I learned to escalate ECG review earlier."),
            context,
            "I learned to escalate ECG review earlier.",
            append_to_case=True,
        )

    assert result == AWAIT_APPROVAL
    extract.assert_awaited_once()
    assert context.user_data.get("needs_reflection_detail") is not True


# Functions allowed to reach the drafting model without the gate, and why.
# Each refines a draft the gate already cleared, from the *same* saved case
# text, so no unjudged requirement can enter through them. A path that folds
# the doctor's new words into the case is not refinement and is not listed
# here — `_regenerate_active_draft_with_feedback` re-gates instead.
_UNGATED_DRAFTING_CALLERS = {
    # The single drafting call the gate deliberately guards from outside.
    "_analyse_selected_form",
    # Post-preview refinements of an existing, already-gated draft.
    "handle_quick_improve",
    "handle_edit_value",
}


def test_no_drafting_entrypoint_can_skip_the_gate():
    """Structural guard: every function that reaches the drafting model also
    calls the essentials gate, so a new entrypoint cannot quietly bypass it.

    All three call shapes count — `_analyse_selected_form` and the two
    extractors it wraps — because a new entrypoint calling an extractor
    directly would be exactly as much of a bypass as one that did not.
    """
    import ast

    source = open(bot.__file__).read()
    tree = ast.parse(source)
    drafting_calls = ("_analyse_selected_form(", "extract_form_data(", "extract_cbd_data(")
    offenders = []
    seen_exempt = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        body = ast.get_source_segment(source, node) or ""
        if not any(call in body for call in drafting_calls):
            continue
        if node.name in _UNGATED_DRAFTING_CALLERS:
            seen_exempt.add(node.name)
            continue
        if "_essentials_gate_before_draft(" not in body:
            offenders.append(node.name)
    assert not offenders, f"drafting entrypoints without the essentials gate: {offenders}"
    # A stale exemption is its own risk: it would silently excuse a future
    # function that happened to reuse the name.
    assert seen_exempt == _UNGATED_DRAFTING_CALLERS, (
        "exemptions no longer match the code: "
        f"{_UNGATED_DRAFTING_CALLERS - seen_exempt}"
    )


@pytest.mark.asyncio
@pytest.mark.essentials_gate
async def test_unknown_status_from_the_model_is_ignored_not_guessed():
    import extractor

    async def fake_generate(prompt, *args, **kwargs):
        return '{"items": [{"key": "reflection", "status": "probably"}, {"key": "made_up", "status": "missing"}]}'

    with patch("extractor._generate", new=fake_generate):
        statuses = await extractor.assess_form_essentials(
            "A case.", "CBD", [{"key": "reflection", "label": "Reflection"}]
        )

    assert statuses == {}
