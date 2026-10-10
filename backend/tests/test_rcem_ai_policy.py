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
from tests.helpers import isolate_bot_storage


def _context(case_text: str, *, source: str = "text", has_user_context: bool = True):
    return SimpleNamespace(
        user_data={
            "case_text": case_text,
            "case_input_source": source,
            "case_has_user_context": has_user_context,
            "case_user_text": [case_text] if has_user_context else [],
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
    context.user_data["awaiting_reflection_detail"] = True
    _clear_case_review_state(context, keep_case=False)
    assert "rcem_personal_reflection_confirmed" not in context.user_data
    assert "awaiting_reflection_detail" not in context.user_data


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["new_case", "cancel", "reset"])
async def test_case_lifecycle_clears_pending_reflection(action):
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(case_text="Synthetic old case", awaiting_reflection_detail=True)
    bot._store_draft(context, CBDData(reflection=""))
    update = sim._make_text_update(f"/{action}")
    with patch("bot.recommend_form_types", new=AsyncMock(return_value=[])):
        if action == "new_case":
            bot._clear_case_review_state(context, keep_case=False)
            # Also guard direct captures that do not use the review-state helper.
            context.user_data["awaiting_reflection_detail"] = True
            await bot._process_case_text(update.message, context, sim.user_id, "Synthetic new case", "text")
        elif action == "cancel":
            await bot.cancel_command(update, context)
        else:
            before_draft = context.user_data["draft_data"]
            await bot.reset_data(update, context)
            assert context.user_data["draft_data"] == before_draft
            assert "Reset Portfolio Guru?" in sim.get_last_text()
    assert not context.user_data.get("awaiting_reflection_detail")


def test_ai_declaration_is_visible_and_accountability_is_explicit():
    from bot import _format_draft_preview, _with_rcem_ai_declaration

    draft = FormDraft(
        form_type="REFLECT_LOG",
        fields={"reflection": "I learned to pause and seek a second perspective."},
    )
    declared = _with_rcem_ai_declaration(draft)
    preview = _format_draft_preview(draft)
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

    preview = _format_draft_preview(draft)
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
    assert "💾 Save to Kaizen" in labels


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

    preview = _format_draft_preview(draft)
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

    supplied_reflection = "I learned to escalate earlier and will do so in future."
    context.user_data["case_text"] = supplied_reflection
    context.user_data["case_user_text"].append(supplied_reflection)
    bot._store_draft(context, CBDData(
        date_of_encounter="2026-03-17", patient_presentation="Chest pain", clinical_setting="Emergency Department",
        stage_of_training="Higher/ST4-ST6", trainee_role="Assessed", clinical_reasoning="Assessed the patient.",
        reflection="I learned to escalate earlier.", level_of_supervision="Indirect",
    ))
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
    context.user_data["case_user_text"].append(context.user_data["case_text"])
    draft = draft.model_copy(update={"reflection": context.user_data["case_text"]})
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


@pytest.mark.parametrize("form_type,fields", [
    ("TEACH", {"learning_outcomes": "Recognise sepsis early.", "reflection": "Injected text."}),
    ("STAT", {"learning_outcomes": "Recognise sepsis early."}),
    ("JCF", {"learning_points": "Check the evidence."}),
    ("US_CASE", {"case_reflection_title": "AAA scan", "learning_points": "Check the aorta."}),
])
def test_brief_footer_never_appears_without_a_schema_reflection_field(form_type, fields):
    draft = FormDraft(form_type=form_type, fields=fields)
    assert bot._draft_coach_note(draft) == ""
    assert "This reflection is brief" not in bot._format_draft_preview(draft, include_safety_layer=False)


@pytest.mark.parametrize("reflection,shows_note", [
    ("I learned to escalate sooner.", True),
    ("I learned to escalate sooner when observations change. Next time I will review the trend and discuss my concerns with the senior clinician early.", False),
])
def test_brief_footer_uses_actual_reflection_length(reflection, shows_note):
    draft = FormDraft(form_type="DOPS", fields={"reflection": reflection})
    preview = bot._format_draft_preview(draft, include_safety_layer=False)
    assert ("This reflection is brief" in preview) is shows_note


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


@pytest.mark.parametrize("learned,needs_prompt", [
    ("I learned to check understanding before ending a referral.", False),
    ("", True),
    ("   ", True),
])
def test_photo_reflection_hint_checks_the_draft_fields(learned, needs_prompt):
    narrative = "I referred the patient to the surgical team."
    context = _context(f"{narrative} {learned}".strip(), source="photo", has_user_context=True)
    context.user_data.update(chosen_form="REFLECT_LOG", needs_reflection_detail=True)
    draft = FormDraft(form_type="REFLECT_LOG", fields={
        "date_of_encounter": "2026-10-09",
        "reflection": "I referred the patient to the surgical team.",
        "learned": learned,
    })
    bot._store_draft(context, draft)
    hint = bot._draft_reply_hint(context)
    assert ("your reflection" in hint) is needs_prompt
    preview = bot._format_draft_preview_for_context(draft, context)
    assert "Source:" not in preview
    assert "I won't write them for you" not in preview


def test_photo_source_with_supplied_learning_does_not_force_save_gate():
    reflection = "I learned to check understanding before ending a referral."
    context = _context(reflection, source="photo", has_user_context=True)
    draft = FormDraft(form_type="REFLECT_LOG", fields={"reflection": reflection, "learned": reflection})
    assert bot._set_reflection_detail_gate(context, draft) is False


@pytest.mark.parametrize("source", ["text", "voice", "photo"])
def test_doctor_supplied_reflection_survives_preview_without_a_missing_reflection_prompt(source):
    reflection = "I learned to check understanding before ending a referral."
    context = _context(reflection, source=source, has_user_context=True)
    draft = CBDData(reflection=reflection)
    bot._store_draft(context, draft)
    assert bot._load_draft(context).reflection == reflection
    assert reflection in bot._format_draft_preview_for_context(draft, context)
    assert "your reflection" not in bot._draft_reply_hint(context)
    assert bot._set_reflection_detail_gate(context, draft) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("form_type", ["CBD", "DOPS", "REFLECT_LOG"])
async def test_captionless_photo_learning_is_blank_in_preview_and_filing(form_type):
    reflection = "Learning points: check understanding before ending a referral."
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(_context(reflection, source="photo", has_user_context=False).user_data)
    context.user_data.update(chosen_form=form_type, rcem_personal_reflection_confirmed=True)
    draft = FormDraft(form_type=form_type, fields={
        "reflection": reflection, "learned": reflection,
        "replay_differently": reflection, "why": reflection,
        "different_outcome": reflection, "focussing_on": reflection,
    })
    await bot._show_draft_review(sim._make_text_update("Synthetic photo").message,
                                 context, draft, form_type, edit=False)
    assert reflection not in sim.get_last_text()
    stored = bot._load_draft(context)
    assert stored.fields["reflection"] == ""
    if form_type == "REFLECT_LOG":
        assert all(not value for value in stored.fields.values())
    if form_type != "DOPS":
        assert "Still needed:" in sim.get_last_text()
        assert "your reflection" in sim.get_last_text()
    # Older persisted payloads also need the guard at the filing boundary.
    context.user_data["draft_data"] = bot._serialise_draft(draft)
    route = AsyncMock(return_value={"status": "failed", "filled": [], "skipped": [],
                                   "error": "offline payload capture", "method": "deterministic"})
    with patch("bot.get_credentials", return_value=("synthetic", "synthetic")), \
         patch("bot.route_filing", new=route), \
         patch("bot.compose_filing_recovery_copy", new=AsyncMock(return_value="")), \
         patch("bot._alert_filing_failure", new=AsyncMock()):
        await bot.handle_approval_approve(sim._make_callback_update("APPROVE|draft"), context)
    route.assert_awaited_once()
    assert route.await_args.kwargs["fields"]["reflection"] == ""


@pytest.mark.parametrize("form_type", ["CBD", "REFLECT_LOG"])
def test_curriculum_preview_starts_after_a_blank_line(form_type):
    fields = {"reflection": "Brief note.", "learned": "Check understanding.",
              "curriculum_links": ["SLO9"], "key_capabilities": ["SLO9 KC2"]}
    draft = CBDData(reflection="Brief note.", curriculum_links=["SLO9"], key_capabilities=["SLO9 KC2"]) if form_type == "CBD" else FormDraft(form_type=form_type, fields=fields)
    assert "\n\n📚 *Curriculum:*" in bot._format_draft_preview(draft)


# 9 Oct 2026: reflection provenance is doctor-authored input, never media OCR.
async def _choose_cbd_from_gathering(sim, context, reflection):
    with patch("bot.recommend_form_types", new=AsyncMock(return_value=[])):
        assert await bot.handle_gathering_input(
            sim._make_text_update("done"), context,
        ) == bot.AWAIT_FORM_CHOICE
    draft = FormDraft(form_type="CBD", fields={
        "patient_presentation": "Synthetic chest pain case",
        "clinical_reasoning": "I assessed chest pain and escalated to the senior.",
        "reflection": reflection,
    })
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        assert await bot.handle_form_choice(
            sim._make_callback_update("FORM|CBD"), context,
        ) == bot.AWAIT_APPROVAL
    return bot._load_draft(context)


async def _capture_approved_fields(sim, context, *, status="failed"):
    route = AsyncMock(return_value={"status": status, "filled": [], "skipped": [],
                                   "error": "offline payload capture" if status == "failed" else "",
                                   "method": "deterministic"})
    with patch("bot.get_credentials", return_value=("synthetic", "synthetic")), \
         patch("bot.route_filing", new=route), \
         patch("bot.compose_filing_recovery_copy", new=AsyncMock(return_value="")), \
         patch("bot._alert_filing_failure", new=AsyncMock()):
        await bot.handle_approval_approve(sim._make_callback_update("APPROVE|draft"), context)
        if context.user_data.get("awaiting_attachment_confirmation"):
            await bot.handle_attachment_confirm(sim._make_callback_update("ATTACH|no"), context)
    route.assert_awaited_once()
    return route.await_args.kwargs["fields"]


@pytest.mark.asyncio
@pytest.mark.parametrize("form_type", ["CBD", "DOPS"])
@pytest.mark.parametrize("generated", [
    "I learned to escalate later next time.",
    "I learned to escalate earlier next time and perform a surgical airway independently.",
    "I learned to escalate earlier next time. I can perform a surgical airway independently.",
])
async def test_unsupported_reflection_falls_back_in_preview_storage_and_filing(form_type, generated):
    sim = BotSimulator()
    context = sim._make_context()
    own = "I learned to escalate earlier next time."
    context.user_data.update(_context(f"Synthetic ED assessment. {own}").user_data)
    context.user_data["chosen_form"] = form_type
    draft = (CBDData(reflection=generated) if form_type == "CBD" else
             FormDraft(form_type=form_type, fields={"reflection": generated}))
    await bot._show_draft_review(sim._make_text_update(own).message,
                                 context, draft, form_type, edit=False)
    stored = bot._load_draft(context)
    assert bot._draft_fields_for_review(stored)["reflection"] == own
    assert own in sim.get_last_text()
    assert generated not in sim.get_last_text()
    assert not context.user_data.get("needs_reflection_detail")
    bot._store_pending_draft(context, draft)
    assert bot._draft_fields_for_review(bot._load_pending_draft(context))["reflection"] == own
    # Legacy stored drafts get the same result at the filing boundary.
    context.user_data["draft_data"] = bot._serialise_draft(draft)
    fields = await _capture_approved_fields(sim, context)
    assert fields["reflection"].startswith(own)
    assert generated not in fields["reflection"]


def test_reflection_fallback_uses_only_authored_reflective_sentences_verbatim():
    own = ["  Synthetic ED assessment. I learned to escalate earlier next time.  ",
           "\nNext time I will check understanding with my senior.\n"]
    context = _context("OCR: I learned to perform a surgical airway independently.")
    context.user_data["case_user_text"] = own
    draft = CBDData(reflection="I learned to perform a surgical airway independently.")
    filtered = bot._without_unsupported_reflection(context, draft)
    assert filtered.reflection == ("I learned to escalate earlier next time.\n\n"
                                   "Next time I will check understanding with my senior.")
    assert bot._without_unsupported_reflection(context, filtered) == filtered


@pytest.mark.parametrize("own,field", [
    ("next time I will escalate sooner to my senior", "Next time, I will escalate sooner to my senior."),
    ("I learned to escalate earlier next time.", "I learnt to escalate earlier next time."),
    ("Learning to escalate earlier next time.", "Learned to escalate earlier next time."),
    ("Next time I will be escalating sooner to my senior.", "Next time I will escalate sooner to my senior."),
])
def test_faithful_light_reflection_rewording_is_kept(own, field):
    draft = CBDData(reflection=field)
    assert bot._without_unsupported_reflection(_context(own), draft).reflection == field


@pytest.mark.parametrize("own,field", [
    ("I learned not to escalate later next time.", "I learned to escalate later next time."),
    ("I learned to ask my senior to assess me.", "I learned to ask me to assess my senior."),
    ("I learned to escalate earlier. Next time I will discharge later.",
     "I learned to escalate later."),
    ("I learned to escalate earlier and discharge later.", "I learned to escalate later."),
])
def test_reflection_support_preserves_polarity_roles_and_sentence_grounding(own, field):
    draft = CBDData(reflection=field)
    assert bot._without_unsupported_reflection(_context(own), draft).reflection == own.replace(". Next", ".\n\nNext")


@pytest.mark.asyncio
async def test_typed_case_then_uncaptioned_dops_image_cannot_supply_reflection(tmp_path, monkeypatch):
    """Doctor-authored clinical facts do not make later OCR their reflection."""
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    clinical_text = (
        "I reduced a displaced distal radius fracture under sedation in the ED "
        "with the consultant present and documented the neurovascular examination."
    )
    reflection = "I learned to escalate earlier next time."
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))):
        assert await bot.handle_case_input(sim._make_text_update(clinical_text), context) == bot.AWAIT_GATHERING
    with patch("bot.recommend_form_types", new=AsyncMock(return_value=[])):
        assert await bot.handle_gathering_input(sim._make_text_update("done"), context) == bot.AWAIT_FORM_CHOICE
    first_draft = FormDraft(form_type="DOPS", fields={
        "procedure_name": "Fracture / Dislocation manipulation", "indication": clinical_text,
        "trainee_performance": clinical_text, "reflection": "",
    })
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=first_draft)):
        assert await bot.handle_form_choice(sim._make_callback_update("FORM|DOPS"), context) == bot.AWAIT_APPROVAL

    media_path = tmp_path / "synthetic.jpg"
    media_path.write_bytes(b"synthetic media")
    bot._queue_pending_media(context, {"path": str(media_path), "name": media_path.name,
                                      "kind": "image", "text": reflection})
    context.user_data["awaiting_detail"] = True
    extracted = first_draft.model_copy(update={"fields": {**first_draft.fields, "reflection": reflection}})
    with patch("bot._read_image_text", new=AsyncMock(return_value=(reflection, []))), \
         patch("bot._analyse_selected_form", new=AsyncMock(return_value=extracted)):
        assert await bot.handle_document_intent(sim._make_callback_update("DOCUSE|both"), context) == bot.AWAIT_APPROVAL

    assert reflection in context.user_data["case_text"], "OCR remains useful case evidence"
    assert clinical_text in context.user_data["case_user_text"]
    assert reflection not in "\n".join(context.user_data["case_user_text"])
    assert bot._load_draft(context).fields["reflection"] == ""
    assert reflection not in sim.get_last_text()
    assert context.user_data.get("needs_reflection_detail") is not True, "DOPS reflection is optional"
    # Persisted pre-fix drafts must get the same filtering at the save boundary.
    context.user_data["draft_data"] = bot._serialise_draft(extracted)
    fields = await _capture_approved_fields(sim, context)
    assert fields["reflection"] == ""


@pytest.mark.asyncio
async def test_course_resources_do_not_mask_empty_required_reflection():
    sim = BotSimulator()
    context = sim._make_context()
    source = "I attended a simulation course and used the course handbook and resuscitation guidelines."
    context.user_data.update(_context(source).user_data)
    context.user_data["chosen_form"] = "FORMAL_COURSE"
    draft = FormDraft(form_type="FORMAL_COURSE", fields={
        "project_description": "Simulation course", "resources_used": "course handbook and resuscitation guidelines",
        "reflective_notes": "", "lessons_learned": "",
    })
    await bot._show_draft_review(sim._make_text_update(source).message, context, draft, "FORMAL_COURSE", edit=False)
    assert bot._load_draft(context).fields["reflective_notes"] == ""
    assert bot._load_draft(context).fields["lessons_learned"] == ""
    assert bot._load_draft(context).fields["resources_used"] == draft.fields["resources_used"]
    assert bot._draft_reflection_text(bot._load_draft(context)).strip() == ""
    assert "Still needed:" in sim.get_last_text()
    assert "Reflective notes from experience" in sim.get_last_text()
    assert "Lessons learned" in sim.get_last_text()
    assert context.user_data.get("needs_reflection_detail") is True
    fields = await _capture_approved_fields(sim, context)
    assert fields["resources_used"] == draft.fields["resources_used"]
    assert fields["reflective_notes"] == fields["lessons_learned"] == ""


@pytest.mark.asyncio
async def test_reflection_fields_are_grounded_individually_in_doctor_words():
    sim = BotSimulator()
    context = sim._make_context()
    own_reflection = "Next time I will escalate earlier."
    ocr_reflection = "I learned to check understanding before ending a referral."
    context.user_data.update(
        case_text=f"{own_reflection}\n\nDocument text:\n{ocr_reflection}",
        case_input_source="mixed", case_has_user_context=True,
        case_user_text=[own_reflection], chosen_form="REFLECT_LOG",
    )
    draft = FormDraft(form_type="REFLECT_LOG", fields={
        "reflection": "", "learned": ocr_reflection,
        "replay_differently": own_reflection,
    })
    await bot._show_draft_review(sim._make_text_update(own_reflection).message,
                                 context, draft, "REFLECT_LOG", edit=False)
    stored = bot._load_draft(context)
    assert stored.fields["replay_differently"] == own_reflection
    assert stored.fields["learned"] == own_reflection
    assert own_reflection in sim.get_last_text()
    assert ocr_reflection not in sim.get_last_text()
    fields = await _capture_approved_fields(sim, context)
    assert fields["replay_differently"].startswith(own_reflection)
    assert fields["learned"].startswith(own_reflection)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_prompt", [False, True])
@pytest.mark.parametrize("input_source", ["text", "voice", "audio"])
@pytest.mark.parametrize("reflection,generated,expected", [
    ("I learned to check understanding before ending a referral.",
     "I learned to check understanding before ending a referral.",
     "I learned to check understanding before ending a referral."),
    ("Escalate earlier.", "Escalate earlier.", "Escalate earlier."),
    ("Escalate earlier.", "I will escalate earlier.", "Escalate earlier."),
    ("I learned:\nEscalate earlier.\nCheck drug allergies.",
     "I learned to escalate earlier and check drug allergies.",
     "I learned:\nEscalate earlier.\nCheck drug allergies."),
    ("I learned to escalate earlier next time.", "I learned to escalate earlier.",
     "I learned to escalate earlier."),
])
async def test_reply_to_missing_photo_reflection_is_retained_as_doctor_words(reflection, generated, expected, explicit_prompt, input_source):
    sim = BotSimulator()
    context = sim._make_context()
    source = "Synthetic ED chest pain assessed with senior review."
    context.user_data.update(_context(source, source="photo", has_user_context=False).user_data)
    context.user_data["chosen_form"] = "CBD"
    draft = CBDData(patient_presentation=source, clinical_reasoning=source,
                    stage_of_training="Higher", trainee_role="Assessed the synthetic case",
                    clinical_setting="Emergency Department", level_of_supervision="Direct", reflection="")
    await bot._show_draft_review(sim._make_text_update(source).message, context, draft, "CBD", edit=False)
    assert "Still needed:" in sim.get_last_text()
    assert [gap["key"] for gap in bot._draft_gaps(context)] == ["reflection"]
    if explicit_prompt:
        context.user_data["awaiting_reflection_detail"] = True
    update = sim._make_text_update(reflection)
    if input_source != "text":
        update.message.text = None
        setattr(update.message, input_source, SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=AsyncMock())),
            mime_type="audio/ogg", file_name="reflection.ogg",
        ))
    with patch("bot.extract_cbd_data", new=AsyncMock(return_value=draft.model_copy(update={"reflection": generated}))), \
         patch("bot.classify_intent", new=AsyncMock(return_value="edit_detail")), \
         patch("bot.transcribe_voice", new=AsyncMock(return_value=reflection)), \
         patch("bot.get_voice_profile", return_value=""), \
         patch("bot.assess_form_essentials", new=AsyncMock(return_value={
             item["key"]: bot.ESSENTIAL_PRESENT for item in bot._form_essential_requirements("CBD")
         })):
        handler = bot.handle_mid_conversation_text if input_source == "text" else bot.handle_approval_media_feedback
        assert await handler(update, context) == bot.AWAIT_APPROVAL
    assert reflection in "\n".join(context.user_data["case_user_text"])
    assert source not in "\n".join(context.user_data["case_user_text"])
    assert bot._load_draft(context).reflection == expected
    assert "your reflection" not in sim.get_last_text()
    assert not context.user_data.get("awaiting_reflection_detail")
    fields = await _capture_approved_fields(sim, context)
    assert fields["reflection"].startswith(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["gap", "awaiting", "edit", "amend"])
@pytest.mark.parametrize("input_source", ["text", "voice", "audio"])
@pytest.mark.parametrize("feedback", ["change the date to 3 Oct", "Please change the date to 3 Oct."])
async def test_date_edit_with_only_reflection_gap_never_becomes_reflection(route, input_source, feedback):
    sim = BotSimulator()
    context = sim._make_context()
    source = "Synthetic ED chest pain assessed with senior review."
    context.user_data.update(_context(source, source="photo", has_user_context=False).user_data)
    context.user_data["chosen_form"] = "CBD"
    draft = CBDData(patient_presentation=source, clinical_reasoning=source,
                    stage_of_training="Higher", trainee_role="Assessed the synthetic case",
                    clinical_setting="Emergency Department", level_of_supervision="Direct", reflection="")
    await bot._show_draft_review(sim._make_text_update(source).message, context, draft, "CBD", edit=False)
    assert [gap["key"] for gap in bot._draft_gaps(context)] == ["reflection"]
    awaiting_reflection = route in {"awaiting", "amend"}
    if awaiting_reflection:
        await bot.handle_callback(sim._make_callback_update("ACTION|add_reflection_detail"), context)
    if route == "amend":
        context.user_data["amend_mode"] = True
    elif route == "edit":
        # Exercise the doctor's Edit button and its actual text handler.
        assert await bot.handle_approval_edit(sim._make_callback_update("EDIT|draft"), context) == bot.AWAIT_EDIT_VALUE
    regenerated = draft.model_copy(update={
        "date_of_encounter": "2026-10-03", "reflection": "I learned to escalate earlier.",
    })
    update = sim._make_text_update(feedback)
    if input_source != "text":
        update.message.text = None
        setattr(update.message, input_source, SimpleNamespace(
            get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=AsyncMock())),
            mime_type="audio/ogg", file_name="amendment.ogg",
        ))
    with patch("bot.extract_cbd_data", new=AsyncMock(return_value=regenerated)), \
         patch("bot.transcribe_voice", new=AsyncMock(return_value=feedback)), \
         patch("bot.extract_field_updates", new=AsyncMock(return_value={})), \
         patch("bot.classify_intent", new=AsyncMock(return_value="edit_detail")), \
         patch("bot.get_voice_profile", return_value=""), \
         patch("bot.assess_form_essentials", new=AsyncMock(return_value={
             item["key"]: bot.ESSENTIAL_PRESENT for item in bot._form_essential_requirements("CBD")
         })):
        handler = bot.handle_edit_value_with_intent if route == "edit" else bot.handle_mid_conversation_text
        if input_source != "text":
            handler = bot.handle_approval_media_feedback
        assert await handler(update, context) == bot.AWAIT_APPROVAL
    assert bot._load_draft(context).date_of_encounter == "2026-10-03"
    assert bot._load_draft(context).reflection == ""
    assert "I learned to escalate earlier." not in sim.get_last_text()
    assert context.user_data.get("awaiting_reflection_detail", False) is awaiting_reflection
    fields = await _capture_approved_fields(sim, context)
    assert fields["reflection"] == ""
    assert fields["date_of_encounter"] == "2026-10-03"


@pytest.mark.asyncio
@pytest.mark.parametrize("message,intent,last_status", [
    ("Did it save?", "question_general", "success"),
    ("Did it save?", "question_general", None),
    ("Did it save?", "edit_detail", None),
    ("Will this be sent to my supervisor?", "question_general", None),
    ("Use the same case for DOPS", "edit_detail", None),
    ("Change this to a DOPS", "edit_detail", None),
    ("cancel this", "edit_detail", None),
])
async def test_reflection_prompt_side_messages_keep_draft_and_next_reflection(message, intent, last_status):
    sim = BotSimulator()
    context = sim._make_context()
    source = "Synthetic ED chest pain assessed with senior review."
    context.user_data.update(_context(source).user_data)
    context.user_data["chosen_form"] = "CBD"
    draft = CBDData(patient_presentation=source, reflection="")
    bot._store_draft(context, draft)
    await bot.handle_callback(sim._make_callback_update("ACTION|add_reflection_detail"), context)
    before = dict(context.user_data)
    if last_status:
        context.user_data.update(last_filing_status=last_status, last_filing_form_name="CBD")
    regenerate = AsyncMock(return_value=bot.AWAIT_APPROVAL)
    with patch("bot._regenerate_active_draft_with_feedback", new=regenerate), \
         patch("bot.extract_field_updates", new=AsyncMock(return_value={})) as field_updates, \
         patch("bot.classify_intent", new=AsyncMock(return_value=intent)), \
         patch("bot.answer_question", new=AsyncMock(return_value="Use the draft controls above.")):
        assert await bot.handle_mid_conversation_text(sim._make_text_update(message), context) == bot.AWAIT_APPROVAL
    regenerate.assert_not_awaited()
    field_updates.assert_not_awaited()
    assert context.user_data["draft_data"] == before["draft_data"]
    assert context.user_data["case_text"] == source
    assert context.user_data["case_user_text"] == before["case_user_text"]
    assert context.user_data.get("awaiting_reflection_detail") is True
    if last_status:
        assert "saved to Kaizen as a draft" in sim.get_last_text()
    elif "supervisor" in message:
        assert "No supervisor request" in sim.get_last_text()
    elif message == "cancel this":
        assert "haven't cancelled" in sim.get_last_text()
    reflection = "Escalate earlier."
    with patch("bot._regenerate_active_draft_with_feedback", new=regenerate), \
         patch("bot.classify_intent", new=AsyncMock(return_value="edit_detail")):
        assert await bot.handle_mid_conversation_text(sim._make_text_update(reflection), context) == bot.AWAIT_APPROVAL
    regenerate.assert_awaited_once()
    assert regenerate.await_args.args[2] == reflection
    assert regenerate.await_args.kwargs["reflection_reply"] is True
    assert not context.user_data.get("awaiting_reflection_detail")


@pytest.mark.asyncio
async def test_cancel_while_awaiting_reflection_uses_cancel_handler():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(case_text="Synthetic chest pain case", awaiting_reflection_detail=True)
    bot._store_draft(context, CBDData(reflection=""))
    cancel = AsyncMock(return_value=bot.ConversationHandler.END)
    regenerate = AsyncMock()
    with patch("bot.cancel_command", new=cancel), \
         patch("bot._regenerate_active_draft_with_feedback", new=regenerate), \
         patch("bot.classify_intent", new=AsyncMock(return_value="edit_detail")):
        update = sim._make_text_update("Cancel")
        assert await bot.handle_mid_conversation_text(update, context) == bot.ConversationHandler.END
    cancel.assert_awaited_once_with(update, context)
    regenerate.assert_not_awaited()


@pytest.mark.asyncio
async def test_genuine_plural_learning_has_the_same_reflection_in_preview_and_filing():
    sim = BotSimulator()
    context = sim._make_context()
    reflection = "We learned to check the ECG before discharge."
    context.user_data.update(_context(reflection).user_data)
    context.user_data["chosen_form"] = "CBD"
    draft = CBDData(clinical_reasoning="Synthetic chest pain case.", reflection=reflection)
    await bot._show_draft_review(sim._make_text_update(reflection).message, context, draft, "CBD", edit=False)
    assert bot._load_draft(context).reflection == reflection
    assert reflection in sim.get_last_text()
    assert "your reflection" not in sim.get_last_text()
    fields = await _capture_approved_fields(sim, context)
    assert fields["reflection"].startswith(reflection)
    assert fields["reflection"].endswith(AI_USE_DECLARATION)


@pytest.mark.asyncio
@pytest.mark.parametrize("narrative,ocr_reflection,expected", [
    ("I communicated clearly with the family.", "I learned to communicate clearly with the family.", ""),
    ("I reviewed the ECG before discharge.", "I learned to review the ECG before discharge.", ""),
    ("I will not escalate earlier next time.", "I will escalate earlier next time.",
     "I will not escalate earlier next time."),
    ("I learned to escalate earlier next time. I communicated clearly with the family.",
     "I learned to communicate clearly with the family.", "I learned to escalate earlier next time."),
])
async def test_similar_doctor_narrative_does_not_authenticate_ocr_learning(narrative, ocr_reflection, expected):
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        case_text=f"{narrative}\n\nDocument text:\n{ocr_reflection}",
        case_input_source="mixed", case_has_user_context=True,
        case_user_text=[narrative], chosen_form="DOPS",
    )
    draft = FormDraft(form_type="DOPS", fields={"trainee_performance": narrative, "reflection": ocr_reflection})
    await bot._show_draft_review(sim._make_text_update(narrative).message,
                                 context, draft, "DOPS", edit=False)
    assert bot._load_draft(context).fields["reflection"] == expected
    assert ocr_reflection not in sim.get_last_text()
    context.user_data["draft_data"] = bot._serialise_draft(draft)
    fields = await _capture_approved_fields(sim, context)
    if expected:
        assert fields["reflection"].startswith(expected)
    else:
        assert fields["reflection"] == ""
    assert ocr_reflection not in fields["reflection"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_form", [True, False])
async def test_improve_case_keeps_authored_reply_without_promoting_old_ocr(selected_form):
    sim = BotSimulator()
    context = sim._make_context()
    old_ocr = "I learned to check understanding before ending a referral."
    own_reflection = "Next time I will escalate earlier."
    context.user_data.update(
        case_text=f"Synthetic ED chest pain case. {old_ocr}", case_input_source="photo",
        case_has_user_context=False, case_user_text=[],
    )
    if selected_form:
        context.user_data["chosen_form"] = "REFLECT_LOG"
    await bot._show_open_case_new_case_gate(
        sim._make_text_update(own_reflection).message, context, own_reflection,
    )
    draft = FormDraft(form_type="REFLECT_LOG", fields={
        "reflection": "", "learned": old_ocr, "replay_differently": own_reflection,
    })
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)), \
         patch("bot.recommend_form_types", new=AsyncMock(return_value=[])):
        state = await bot.handle_callback(sim._make_callback_update("CASE|improve"), context)
        if not selected_form:
            assert state == bot.AWAIT_FORM_CHOICE
            state = await bot.handle_form_choice(sim._make_callback_update("FORM|REFLECT_LOG"), context)
    assert state == bot.AWAIT_APPROVAL
    assert old_ocr in context.user_data["case_text"], "the case retains evidence for its narrative"
    assert own_reflection in "\n".join(context.user_data["case_user_text"])
    assert old_ocr not in "\n".join(context.user_data["case_user_text"])
    assert bot._load_draft(context).fields["replay_differently"] == own_reflection
    assert bot._load_draft(context).fields["learned"] == own_reflection
    fields = await _capture_approved_fields(sim, context)
    assert fields["replay_differently"].startswith(own_reflection)
    assert fields["learned"].startswith(own_reflection)


@pytest.mark.asyncio
async def test_document_caption_survives_preconsent_capture_and_resume(tmp_path, monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    reflection = "I learned to escalate earlier next time."
    clinical_text = "Synthetic ED chest pain case assessed with senior review."
    update = sim._make_text_update("")
    update.message.text = None
    update.message.caption = reflection
    update.message.document = SimpleNamespace(file_id="synthetic-document", file_name="synthetic.pdf")
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.consent.has_current_consent", new=AsyncMock(return_value=False)):
        await bot.handle_case_input(update, context)
    pending = context.user_data["_consent_pending_input"]
    assert pending["caption"] == reflection
    assert not context.user_data.get("case_user_text"), "no processing before consent"
    media_path = tmp_path / "synthetic.pdf"
    media_path.write_bytes(b"synthetic media")
    with patch("bot._download_pending_consent_file", new=AsyncMock(return_value=str(media_path))):
        state = await bot._resume_pending_consent_input(
            sim._make_callback_update("CONSENT|accept").callback_query, context, sim.user_id, pending,
        )
    assert state == bot.AWAIT_DOC_INTENT
    assert context.user_data["_pending_doc_context"] == reflection
    with patch("bot.extract_from_document", new=AsyncMock(return_value=clinical_text)):
        assert await bot.handle_document_intent(sim._make_callback_update("DOCUSE|both"), context) == bot.AWAIT_GATHERING
    stored = await _choose_cbd_from_gathering(sim, context, reflection)
    assert stored.fields["reflection"] == reflection
    assert context.user_data["case_user_text"] == [reflection]


@pytest.mark.asyncio
async def test_attach_only_caption_retains_author_words_for_same_case_reuse(tmp_path, monkeypatch):
    isolate_bot_storage(monkeypatch, tmp_path)
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    clinical_text = "Synthetic ED chest pain case assessed with senior review."
    reflection = "I learned to escalate earlier next time."
    bot._append_gathering_case(context, clinical_text, "text")
    media_path = tmp_path / "synthetic.jpg"
    media_path.write_bytes(b"synthetic media")
    bot._queue_pending_media(context, {"path": str(media_path), "name": media_path.name, "kind": "image"})
    context.user_data["_pending_doc_context"] = reflection
    read = AsyncMock()
    with patch("bot._read_image_text", new=read):
        assert await bot.handle_document_intent(sim._make_callback_update("DOCUSE|attach"), context) == bot.AWAIT_GATHERING
    read.assert_not_awaited()
    assert reflection in context.user_data["case_user_text"]
    stored = await _choose_cbd_from_gathering(sim, context, reflection)
    assert stored.fields["reflection"] == reflection
    await _capture_approved_fields(sim, context, status="partial")
    assert await bot._handle_reuse_request(
        sim._make_text_update("Use the same case for DOPS"), context, sim.user_id, "Use the same case for DOPS",
    ) == bot.AWAIT_FORM_CHOICE
    bot._store_draft(context, FormDraft(form_type="DOPS", fields={"reflection": reflection}))
    assert bot._load_draft(context).fields["reflection"] == reflection
    assert reflection in context.user_data["case_user_text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["photo", "document", "mixed"])
async def test_enrichment_before_form_choice_retains_only_actual_reply(source):
    sim = BotSimulator()
    context = sim._make_context()
    old_ocr = "I learned to check understanding before ending a referral."
    reply = "I reviewed the chest pain patient in the ED. Next time I will escalate earlier."
    own_reflection = "Next time I will escalate earlier."
    context.user_data.update(
        case_text=f"Synthetic ED chest pain case. {old_ocr}", case_input_source=source,
        case_has_user_context=False, case_user_text=[],
    )
    with patch("bot.classify_intent", new=AsyncMock(return_value="edit_detail")), \
         patch("bot.recommend_form_types", new=AsyncMock(return_value=[])):
        assert await bot.handle_mid_conversation_text(sim._make_text_update(reply), context) == bot.AWAIT_FORM_CHOICE
    assert context.user_data["case_user_text"] == [reply]
    assert old_ocr in context.user_data["case_text"]
    draft = FormDraft(form_type="REFLECT_LOG", fields={"learned": old_ocr, "replay_differently": own_reflection})
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        assert await bot.handle_form_choice(sim._make_callback_update("FORM|REFLECT_LOG"), context) == bot.AWAIT_APPROVAL
    assert bot._load_draft(context).fields["learned"] == own_reflection
    assert bot._load_draft(context).fields["replay_differently"] == own_reflection


@pytest.mark.asyncio
@pytest.mark.parametrize("media_route", ["failed_filing", "template"])
@pytest.mark.parametrize("choice", ["new", "improve"])
async def test_media_case_choice_never_promotes_uncaptioned_ocr_to_author_words(media_route, choice):
    sim = BotSimulator()
    context = sim._make_context()
    original = "Synthetic ED chest pain assessed with senior review."
    ocr_reflection = "I learned to escalate earlier next time."
    ocr = f"New case: synthetic patient with a fracture in the ED. {ocr_reflection}"
    context.user_data.update(
        case_text=original, case_input_source="photo", case_user_text=[],
        case_has_user_context=False, chosen_form="DOPS",
    )
    if media_route == "failed_filing":
        bot._store_draft(context, FormDraft(form_type="DOPS", fields={"trainee_performance": original}))
        context.user_data["last_filing_status"] = "failed"
    else:
        bot._store_pending_draft(context, FormDraft(form_type="DOPS", fields={"trainee_performance": original}))
    update = sim._make_text_update("")
    update.message.text = None
    update.message.photo = [SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(download_to_drive=AsyncMock())))]
    with patch("bot._read_image_text", new=AsyncMock(return_value=(ocr, []))), \
         patch("bot._cache_and_queue_attachment", return_value=None):
        if media_route == "failed_filing":
            assert await bot.handle_approval_media_feedback(update, context) == bot.AWAIT_APPROVAL
        else:
            assert await bot.handle_template_review_media(update, context) == bot.AWAIT_TEMPLATE_REVIEW
    assert context.user_data["pending_new_case_text"] == ocr
    extracted = FormDraft(form_type="DOPS", fields={"trainee_performance": original, "reflection": ocr_reflection})
    with patch("bot.recommend_form_types", new=AsyncMock(return_value=[])), \
         patch("bot._analyse_selected_form", new=AsyncMock(return_value=extracted)):
        state = await bot.handle_callback(sim._make_callback_update(f"CASE|{choice}"), context)
        if choice == "new":
            assert state == bot.AWAIT_FORM_CHOICE
            state = await bot.handle_form_choice(sim._make_callback_update("FORM|DOPS"), context)
    assert state == bot.AWAIT_APPROVAL
    assert ocr_reflection in context.user_data["case_text"]
    assert not context.user_data.get("case_user_text")
    assert bot._load_draft(context).fields["reflection"] == ""
    assert ocr_reflection not in sim.get_last_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_is_doctor", [True, False])
async def test_form_request_reply_only_credits_the_current_doctors_own_message(reply_is_doctor):
    sim = BotSimulator()
    context = sim._make_context()
    reflection = "I learned to escalate earlier next time."
    replied = f"Synthetic ED chest pain assessed with senior review. {reflection}"
    update = sim._make_text_update("File as DOPS")
    update.message.reply_to_message = SimpleNamespace(
        text=replied, caption=None,
        from_user=SimpleNamespace(id=sim.user_id if reply_is_doctor else 12345, is_bot=not reply_is_doctor),
    )
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))):
        assert await bot.handle_case_input(update, context) == bot.AWAIT_FORM_CHOICE
    authored = "\n".join(context.user_data["case_user_text"])
    assert (reflection in authored) is reply_is_doctor
    draft = FormDraft(form_type="DOPS", fields={"trainee_performance": "Synthetic assessment", "reflection": reflection})
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        assert await bot.handle_form_choice(sim._make_callback_update("FORM|DOPS"), context) == bot.AWAIT_APPROVAL
    assert bot._load_draft(context).fields["reflection"] == (reflection if reply_is_doctor else "")


@pytest.mark.asyncio
async def test_fresh_case_does_not_inherit_previous_filed_author_words(monkeypatch):
    monkeypatch.setenv("PG_GATHERING_MODE", "0")
    sim = BotSimulator()
    context = sim._make_context()
    old_reflection = "I learned to escalate earlier next time."
    context.user_data.update(last_filed_case_text=old_reflection, case_user_text=[old_reflection])
    new_case = "I reduced a distal radius fracture in the ED under consultant supervision and documented the neurovascular examination."
    with patch("bot.has_credentials", return_value=True), \
         patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))), \
         patch("bot.recommend_form_types", new=AsyncMock(return_value=[])):
        assert await bot.handle_case_input(sim._make_text_update(new_case), context) == bot.AWAIT_FORM_CHOICE
    assert context.user_data["case_user_text"] == [new_case]
    draft = FormDraft(form_type="DOPS", fields={"trainee_performance": new_case, "reflection": old_reflection})
    with patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        assert await bot.handle_form_choice(sim._make_callback_update("FORM|DOPS"), context) == bot.AWAIT_APPROVAL
    assert bot._load_draft(context).fields["reflection"] == ""
    assert old_reflection not in sim.get_last_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,context_input", [
    ("image", "caption"), ("document", "caption"),
    ("image", "text"), ("image", "voice"),
])
async def test_own_media_context_survives_gathering_preview_storage_and_filing(tmp_path, monkeypatch, kind, context_input):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    reflection = "I learned to escalate earlier next time."
    clinical_text = "Synthetic ED chest pain case assessed with senior review."
    media_path = tmp_path / ("synthetic.jpg" if kind == "image" else "synthetic.pdf")
    media_path.write_bytes(b"synthetic media")
    bot._queue_pending_media(context, {"path": str(media_path), "name": media_path.name, "kind": kind})
    if context_input == "caption":
        context.user_data["_pending_doc_context"] = reflection
    elif context_input == "text":
        await bot.handle_mid_conversation_text(sim._make_text_update(f"{clinical_text} {reflection}"), context)
    else:
        with patch("bot._transcribe_voice_message", new=AsyncMock(return_value=reflection)):
            await bot.handle_pending_media_context(sim._make_text_update(""), context)
    with patch("bot._read_image_text", new=AsyncMock(return_value=(clinical_text, []))), \
         patch("bot.extract_from_document", new=AsyncMock(return_value=clinical_text)):
        assert await bot.handle_document_intent(
            sim._make_callback_update("DOCUSE|both"), context,
        ) == bot.AWAIT_GATHERING
    stored = await _choose_cbd_from_gathering(sim, context, reflection)
    assert reflection in "\n".join(context.user_data["case_user_text"])
    if context_input != "text":
        assert clinical_text not in "\n".join(context.user_data["case_user_text"])
    assert stored.fields["reflection"] == reflection
    assert reflection in sim.get_last_text()
    fields = await _capture_approved_fields(sim, context)
    assert fields["reflection"].startswith(reflection)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["image", "document"])
async def test_captionless_sibling_ocr_never_becomes_own_reflection(tmp_path, monkeypatch, kind):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = sim._make_context()
    clinical_text = "Synthetic ED chest pain case assessed with senior review."
    # Start gathering from an uncaptioned image, then add two more together.
    bot._append_gathering_case(context, clinical_text, "photo")
    context.user_data.update(case_input_source="photo", case_has_user_context=False)
    reflection = "I learned to escalate earlier next time."
    for index, text in enumerate((clinical_text, reflection)):
        media_path = tmp_path / (f"synthetic-{index}.jpg" if kind == "image" else f"synthetic-{index}.pdf")
        media_path.write_bytes(b"synthetic media")
        bot._queue_pending_media(context, {"path": str(media_path), "name": media_path.name,
                                          "kind": kind, "text": text})
    with patch("bot._read_image_text", new=AsyncMock(return_value=(clinical_text, []))), \
         patch("bot.extract_from_document", new=AsyncMock(return_value=reflection)):
        assert await bot.handle_document_intent(
            sim._make_callback_update("DOCUSE|both"), context,
        ) == bot.AWAIT_GATHERING
    stored = await _choose_cbd_from_gathering(sim, context, reflection)
    assert not context.user_data.get("case_user_text")
    assert stored.fields["reflection"] == ""
    assert reflection not in sim.get_last_text()
    assert "Still needed:" in sim.get_last_text()
    fields = await _capture_approved_fields(sim, context)
    assert fields["reflection"] == ""


@pytest.mark.asyncio
async def test_reflective_log_replay_only_matches_preview_and_filing():
    sim = BotSimulator()
    context = sim._make_context()
    reflection = "Next time I will escalate earlier."
    context.user_data.update(_context(reflection).user_data)
    context.user_data["chosen_form"] = "REFLECT_LOG"
    draft = FormDraft(form_type="REFLECT_LOG", fields={
        "reflection": "", "learned": "", "replay_differently": reflection,
    })
    await bot._show_draft_review(sim._make_text_update(reflection).message,
                                 context, draft, "REFLECT_LOG", edit=False)
    assert reflection in sim.get_last_text()
    assert bot._load_draft(context).fields["replay_differently"] == reflection
    fields = await _capture_approved_fields(sim, context)
    assert fields["replay_differently"].startswith(reflection)
    assert fields["reflection"] == fields["learned"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("deletion_fails", [False, True])
async def test_draft_flow_never_sends_draft_ready_below(deletion_fails):
    sim = BotSimulator()
    context = sim._make_context()
    reflection = "I learned to escalate earlier next time."
    context.user_data.update(_context(reflection).user_data)
    progress = sim._make_callback_update("FORM|CBD").callback_query.message
    progress.delete = AsyncMock(side_effect=RuntimeError("synthetic deletion refusal") if deletion_fails else None)
    await bot._show_draft_review(progress, context, CBDData(reflection=reflection), "CBD")
    progress.delete.assert_awaited_once()
    assert any(kind == "send" and reflection in text for kind, text, _ in sim.messages_sent)
    assert all("draft ready below" not in text.lower() for _, text, _ in sim.messages_sent if isinstance(text, str))


@pytest.mark.asyncio
@pytest.mark.parametrize("own_words", [True, False])
@pytest.mark.parametrize("reuse_mode", ["text", "button"])
@pytest.mark.parametrize("ask_status", [False, True])
async def test_saved_case_reuse_keeps_own_word_provenance(own_words, reuse_mode, ask_status, monkeypatch, tmp_path):
    isolate_bot_storage(monkeypatch, tmp_path)
    sim = BotSimulator()
    context = sim._make_context()
    reflection = "I learned to escalate earlier next time."
    context.user_data.update(_context(reflection, source="photo", has_user_context=own_words).user_data)
    context.user_data["chosen_form"] = "CBD"
    bot._store_draft(context, CBDData(reflection=reflection))
    await _capture_approved_fields(sim, context, status="partial")
    assert context.user_data["last_filed_case_text"] == reflection
    if ask_status:
        with patch("bot.has_credentials", return_value=True), \
             patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)), \
             patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))):
            assert await bot.handle_case_input(sim._make_text_update("Did it save?"), context) == bot.ConversationHandler.END
        assert context.user_data["last_filed_case_text"] == reflection
        assert context.user_data["case_user_text"] == ([reflection] if own_words else [])
    if reuse_mode == "text":
        assert await bot.handle_case_input(
            sim._make_text_update("Use the same case for DOPS"), context,
        ) == bot.AWAIT_FORM_CHOICE
    else:
        with patch("bot.recommend_form_types", new=AsyncMock(return_value=[])):
            assert await bot.handle_same_case_another(
                sim._make_callback_update("ACTION|same_case_another"), context,
            ) == bot.AWAIT_FORM_CHOICE
    bot._remember_case_context_source(context, "same case")
    bot._store_draft(context, FormDraft(form_type="DOPS", fields={"reflection": reflection}))
    assert bot._load_draft(context).fields["reflection"] == (reflection if own_words else "")


@pytest.mark.parametrize("label", ["I learned:", "Reflection:", "Learning points:"])
def test_labelled_multiline_reflection_fallback_keeps_the_whole_authored_turn(label):
    reflection = f"{label}\nEscalate earlier.\nCheck drug allergies."
    context = _context("OCR: I learned to perform a surgical airway independently.",
                       source="photo", has_user_context=False)
    context.user_data["case_user_text"] = [reflection]
    draft = CBDData(reflection="I will escalate earlier and check drug allergies.")
    filtered = bot._without_unsupported_reflection(context, draft)
    assert filtered.reflection == reflection
    assert bot._without_unsupported_reflection(context, filtered) == filtered


@pytest.mark.asyncio
async def test_polite_cancel_while_awaiting_reflection_cancels_not_reflection():
    sim = BotSimulator()
    context = sim._make_context()
    source = "Synthetic ED chest pain assessed with senior review."
    context.user_data.update(_context(source).user_data)
    context.user_data["chosen_form"] = "CBD"
    bot._store_draft(context, CBDData(patient_presentation=source, reflection=""))
    await bot.handle_callback(sim._make_callback_update("ACTION|add_reflection_detail"), context)
    regenerate = AsyncMock(return_value=bot.AWAIT_APPROVAL)
    cancel = AsyncMock(return_value=bot.ConversationHandler.END)
    with patch("bot._regenerate_active_draft_with_feedback", new=regenerate), \
         patch("bot.cancel_command", new=cancel), \
         patch("bot.classify_intent", new=AsyncMock(return_value="edit_detail")):
        await bot.handle_mid_conversation_text(sim._make_text_update("Please cancel."), context)
    regenerate.assert_not_awaited()
    cancel.assert_awaited_once()
