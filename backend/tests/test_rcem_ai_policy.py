"""Behavioural proof for the September 2025 RCEM reflective-log AI policy."""

from types import SimpleNamespace
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
    assert AI_USE_DECLARATION in preview
    assert "You remain responsible for its accuracy, authenticity and insight." in preview


def test_improve_and_save_are_hidden_until_doctor_adds_reflection():
    from bot import _build_approval_keyboard

    callbacks = _callbacks(_build_approval_keyboard(needs_reflection_detail=True))
    assert callbacks == {"ACTION|add_reflection_detail", "CANCEL|draft"}


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
