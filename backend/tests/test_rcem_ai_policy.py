"""Behavioural proof for the September 2025 RCEM reflective-log AI policy."""

from types import SimpleNamespace

import bot
from unittest.mock import AsyncMock, patch

import pytest

from models import CBDData, FormDraft
from rcem_ai_policy import (
    AI_USE_DECLARATION,
    has_personal_reflective_input,
    with_ai_use_declaration,
)
from tests.bot_simulator import BotSimulator


def _context(case_text: str, *, source: str = "text", has_user_context: bool = True):
    return SimpleNamespace(
        user_data={
            "case_text": case_text,
            "case_input_source": source,
            "case_has_user_context": has_user_context,
        }
    )


def _callbacks(markup) -> set[str]:
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }


def test_personal_reflection_gate_uses_doctor_words_not_generated_length():
    assert has_personal_reflective_input(
        "I assessed the patient and I learned to call for senior help earlier next time."
    )
    assert has_personal_reflective_input(
        "Chest pain managed as ACS and reflected on escalating earlier next time."
    )
    assert not has_personal_reflective_input(
        "I assessed the patient, arranged blood tests and discussed admission with medicine."
    )
    assert not has_personal_reflective_input(
        "I would like you to draft the reflection and file this case for me."
    )


def test_personal_reflection_gate_recognises_genuine_learning_without_first_person_phrasing():
    # Synthetic doctor-authored source text (not customer data).
    assert has_personal_reflective_input(
        "Learning points: importance of checking neurovascular status and "
        "applying the Ottawa ankle rules before requesting imaging."
    )
    assert has_personal_reflective_input(
        "This reinforced the importance of documenting neurovascular findings "
        "before discharge and giving clear safety-netting advice."
    )
    assert has_personal_reflective_input(
        "The case highlighted the importance of early senior escalation in "
        "unclear presentations."
    )


def test_personal_reflection_gate_preserves_anonymised_child_case_regression():
    # Synthetic anonymised child-case reproduction (not customer text).
    source = (
        "A young child in the department could not communicate their pain "
        "clearly. Learning that a thorough examination is important when a "
        "patient cannot communicate pain was the key takeaway from this case."
    )
    assert has_personal_reflective_input(source)


def test_personal_reflection_gate_still_rejects_narrative_with_no_learning():
    assert not has_personal_reflective_input(
        "Chest pain assessed, bloods and ECG requested, discussed with "
        "medicine and admitted under the on-call team."
    )
    assert not has_personal_reflective_input(
        "Please just write the reflection for me and file the case, I don't "
        "have anything to add."
    )


def test_personal_reflection_gate_rejects_diagnostic_narrative_mentioning_showed():
    # Diagnostic narrative, not a stated learning.
    assert not has_personal_reflective_input(
        "It showed a fracture on the radiograph."
    )


def test_personal_reflection_gate_rejects_request_to_invent_learning_points():
    assert not has_personal_reflective_input(
        "Please invent some learning points for this case."
    )


def test_personal_reflection_gate_rejects_learning_points_heading_with_none_supplied():
    assert not has_personal_reflective_input(
        "Learning points: none supplied for this case."
    )


def test_personal_reflection_gate_accepts_actual_learning_point_sentence():
    # Deidentified doctor-authored source text.
    source = (
        "Learning point was how kids with autism or non-verbal would not "
        "tell about pain and injury, important for clinician to do "
        "thorough examination and assessment."
    )
    assert has_personal_reflective_input(source)


def test_ai_use_declaration_is_added_once():
    first = with_ai_use_declaration("I learned to escalate earlier in future.")
    second = with_ai_use_declaration(first)
    assert first == second
    assert first.count(AI_USE_DECLARATION) == 1


def test_reflective_draft_cannot_save_without_personal_reflective_input():
    from bot import _draft_needs_reflection_detail_before_save

    draft = CBDData(
        clinical_reasoning="I assessed the patient and discussed the plan with my consultant.",
        reflection="I learned to escalate earlier and will do so in future.",
    )
    context = _context(
        "I assessed the patient, arranged treatment and discussed the plan with my consultant."
    )
    assert _draft_needs_reflection_detail_before_save(context, draft)


def test_reflective_draft_can_save_after_personal_reflective_input():
    from bot import _draft_needs_reflection_detail_before_save

    reflection = "I realised I had anchored early. In future I will reopen the differential sooner."
    draft = CBDData(reflection=reflection)
    assert not _draft_needs_reflection_detail_before_save(_context(reflection), draft)


def test_non_reflective_draft_is_not_subject_to_reflection_gate():
    from bot import _draft_needs_reflection_detail_before_save

    draft = FormDraft(form_type="OTHER", fields={"description": "A factual portfolio record."})
    assert not _draft_needs_reflection_detail_before_save(_context("Factual record only."), draft)


def test_new_case_clears_prior_reflection_confirmation():
    from bot import _clear_case_review_state

    context = _context("Prior reflective case.")
    context.user_data["rcem_personal_reflection_confirmed"] = True
    _clear_case_review_state(context, keep_case=False)
    assert "rcem_personal_reflection_confirmed" not in context.user_data


def test_ai_declaration_is_visible_and_accountability_is_explicit():
    from bot import _format_draft_preview, _with_rcem_ai_declaration

    draft = FormDraft(
        form_type="REFLECT_LOG",
        fields={"reflection": "I learned to pause and seek a second perspective."},
    )
    declared = _with_rcem_ai_declaration(draft)
    preview = _format_draft_preview(draft, needs_reflection_detail=False)
    assert declared.fields["reflection"].endswith(AI_USE_DECLARATION)
    # The declaration lives once, inline in the reflection field; there is no
    # second "AI assistance" footer repeating it.
    assert AI_USE_DECLARATION in preview
    assert preview.count(AI_USE_DECLARATION) == 1
    assert "AI assistance" not in preview


def test_genuine_non_first_person_learning_unlocks_save_with_keyboard_and_footer_agreement():
    from bot import (
        _build_approval_keyboard,
        _format_draft_preview,
        _set_reflection_detail_gate,
    )

    # Synthetic anonymised child-case reproduction (not customer text).
    source = (
        "A young child in the department could not communicate their pain "
        "clearly. Learning that a thorough examination is important when a "
        "patient cannot communicate pain was the key takeaway from this case."
    )
    draft = CBDData(
        clinical_reasoning="Assessed a distressed non-verbal child for a limb injury.",
        reflection=(
            "Learning that a thorough examination is important when a patient "
            "cannot communicate pain."
        ),
    )
    context = _context(source)

    needs_reflection_detail = _set_reflection_detail_gate(context, draft)
    assert needs_reflection_detail is False

    preview = _format_draft_preview(draft, needs_reflection_detail=needs_reflection_detail)
    callbacks = _callbacks(
        _build_approval_keyboard()
    )
    assert "ACTION|add_reflection_detail" not in callbacks
    assert "APPROVE|draft" in callbacks
    assert "add your own learning point" not in preview.lower()


def test_save_stays_available_while_the_reflection_is_missing():
    """Draft first (25 Sep 2026): the doctor can always save a Kaizen draft;
    a reflection they never wrote is saved blank, not held against them."""
    from bot import _build_approval_keyboard, _store_draft

    context = _context("I assessed the patient and discussed admission with medicine.")
    context.user_data["chosen_form"] = "CBD"
    _store_draft(context, CBDData(clinical_reasoning="Assessed chest pain.", reflection=""))

    labels = {button.text for row in _build_approval_keyboard(context=context).inline_keyboard for button in row}
    assert "💾 Save draft now, finish in Kaizen" in labels


def test_actual_learning_point_source_unlocks_save_without_warning():
    from bot import (
        _build_approval_keyboard,
        _format_draft_preview,
        _set_reflection_detail_gate,
    )

    # Deidentified doctor-authored source text.
    source = (
        "Learning point was how kids with autism or non-verbal would not "
        "tell about pain and injury, important for clinician to do "
        "thorough examination and assessment."
    )
    draft = CBDData(
        clinical_reasoning="Assessed a distressed non-verbal child for a limb injury.",
        reflection=source,
    )
    context = _context(source)

    needs_reflection_detail = _set_reflection_detail_gate(context, draft)
    assert needs_reflection_detail is False

    preview = _format_draft_preview(draft, needs_reflection_detail=needs_reflection_detail)
    callbacks = _callbacks(
        _build_approval_keyboard()
    )
    assert "ACTION|add_reflection_detail" not in callbacks
    assert "APPROVE|draft" in callbacks
    assert "reflection is needed before saving" not in preview.lower()


def test_footer_names_what_is_still_needed_and_how_to_add_it():
    from bot import _draft_reply_hint

    context = _context(
        "I assessed the patient, arranged blood tests and discussed admission with medicine."
    )
    context.user_data["chosen_form"] = "CBD"
    bot._store_draft(context, CBDData(clinical_reasoning="Assessed the patient.", reflection=""))
    footer = _draft_reply_hint(context).lower()
    assert "still needed" in footer and "reflection" in footer
    assert "reply with" in footer and "save now" in footer

    bot._store_draft(context, CBDData(
        date_of_encounter="2026-03-17", patient_presentation="Chest pain", clinical_setting="Emergency Department",
        stage_of_training="Higher/ST4-ST6", trainee_role="Assessed", clinical_reasoning="Assessed the patient.",
        reflection="I learned to escalate earlier.", level_of_supervision="Indirect",
    ))
    context.user_data["case_text"] = "I learned to escalate earlier and will do so in future."
    footer = _draft_reply_hint(context).lower()
    assert "still needed" not in footer
    assert "use the buttons below to save" in footer


def test_refinement_clears_stale_reflection_gate_after_initial_block():
    from bot import _set_reflection_detail_gate

    draft = CBDData(
        clinical_reasoning="I assessed the patient and discussed the plan with my consultant.",
        reflection="I learned to escalate earlier and will do so in future.",
    )
    context = _context(
        "I assessed the patient, arranged treatment and discussed the plan with my consultant."
    )
    assert _set_reflection_detail_gate(context, draft) is True
    assert context.user_data["needs_reflection_detail"] is True

    context.user_data["case_text"] = (
        "I realised I had anchored early. In future I will reopen the differential sooner."
    )
    assert _set_reflection_detail_gate(context, draft) is False
    assert "needs_reflection_detail" not in context.user_data


@pytest.mark.asyncio
async def test_quick_improve_cannot_originate_the_doctors_reflection():
    from bot import AWAIT_APPROVAL, handle_quick_improve

    sim = BotSimulator()
    update = sim._make_callback_update("IMPROVE|reflection")
    context = sim._make_context()
    context.user_data.update({
        "case_text": "I assessed the patient and discussed the treatment plan with medicine.",
        "case_input_source": "text",
        "draft_data": {
            "_type": "FORM",
            "form_type": "REFLECT_LOG",
            "fields": {"reflection": "AI-created learning point."},
            "uuid": None,
        },
    })

    extractor = AsyncMock()
    with patch("bot.extract_form_data", new=extractor):
        result = await handle_quick_improve(update, context)

    assert result == AWAIT_APPROVAL
    extractor.assert_not_awaited()
    assert context.user_data["awaiting_reflection_detail"] is True
    assert "add your own learning point" in (sim.get_last_text() or "").lower()


@pytest.mark.asyncio
async def test_approval_sends_ai_declaration_in_fields_to_filer():
    from bot import AWAIT_APPROVAL, handle_approval_approve

    sim = BotSimulator()
    update = sim._make_callback_update("APPROVE|draft")
    context = sim._make_context()
    context.user_data.update({
        "case_text": (
            "I managed the case and realised I had anchored too early. "
            "In future I will reopen the differential before disposition."
        ),
        "case_input_source": "text",
        "draft_data": {
            "_type": "FORM",
            "form_type": "REFLECT_LOG",
            "fields": {
                "date_of_encounter": "2026-03-17",
                "reflection": "I realised I had anchored early and will reopen the differential.",
            },
            "uuid": None,
        },
    })
    route = AsyncMock(return_value={
        "status": "failed",
        "filled": [],
        "skipped": [],
        "error": "deliberate offline stop after payload capture",
        "method": "deterministic",
    })

    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.route_filing", new=route), \
         patch("bot.compose_filing_recovery_copy", new=AsyncMock(return_value="")), \
         patch("bot._alert_filing_failure", new=AsyncMock()):
        result = await handle_approval_approve(update, context)

    assert result == AWAIT_APPROVAL
    route.assert_awaited_once()
    filed_fields = route.await_args.kwargs["fields"]
    assert filed_fields["reflection"].endswith(AI_USE_DECLARATION)
    assert filed_fields["reflection"].count(AI_USE_DECLARATION) == 1


@pytest.mark.parametrize("improved_once", [False, True])
def test_ready_draft_and_amend_offer_only_save_and_cancel(improved_once):
    from bot import _build_approval_keyboard, _build_amend_keyboard

    assert _callbacks(_build_approval_keyboard(improved_once=improved_once)) == {
        "APPROVE|draft", "CANCEL|draft",
    }
    assert _callbacks(_build_amend_keyboard(improved_once=improved_once)) == {
        "APPROVE|draft", "AMEND|cancel",
    }


@pytest.mark.parametrize("draft", [
    CBDData(reflection="I learned to escalate sooner."),
    FormDraft(form_type="DOPS", fields={"reflection": ""}),
], ids=["brief_cbd_reflection", "empty_optional_dops_reflection"])
def test_draft_coach_invites_reply_without_redundant_button(draft):
    from bot import _draft_coach_note

    note = _draft_coach_note(draft)
    assert "reply" in note.lower()
    assert "tap" not in note.lower()
    assert "Improve reflection" not in note


def test_no_coach_note_repeats_the_still_needed_line_for_a_required_reflection():
    from bot import _draft_coach_note

    assert _draft_coach_note(CBDData(reflection="")) == ""


@pytest.mark.asyncio
async def test_stale_improve_callback_without_draft_cannot_extract():
    from bot import AWAIT_CASE_INPUT, handle_quick_improve
    from tests.bot_simulator import BotSimulator

    sim = BotSimulator()
    context = sim._make_context()
    with patch("bot._resume_paused_flow", new_callable=AsyncMock,
               return_value=AWAIT_CASE_INPUT) as resume, \
         patch("bot.extract_cbd_data", new_callable=AsyncMock) as extract:
        state = await handle_quick_improve(
            sim._make_callback_update("IMPROVE|reflection"), context,
        )
    assert state == AWAIT_CASE_INPUT
    assert "no longer active" in resume.call_args.args[2]
    extract.assert_not_awaited()
