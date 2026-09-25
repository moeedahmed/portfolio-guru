"""Essential-first workflow: every essential is judged before drafting.

Owner's decisions: 19 Sep 2026 (judge the case against the form's essentials
before drafting) and 25 Sep 2026 (draft first: never make the doctor answer
questions before they see a draft). The bot picks the form, then reads the
case the doctor already sent against that form's genuinely essential
requirements *before* the drafting call:

* still missing → the draft is shown at once with those fields left blank,
  never filled by the model, and one closing line names what is still needed
  and invites a reply; Save stays available as a Kaizen draft;
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


# --- reflection titles: composed by the drafter, never asked for -----------
#
# Demo regression (24 Sep 2026): an Ultrasound Case's only required field is
# "Case reflection title". The gate asked the doctor for it, the doctor read it
# as "your reflection" and sent reflections, and the model kept judging the
# *title* missing — a loop with no way through to a draft. A title summarises
# the case; the drafter writes it and the doctor reviews it in the preview.

US_CASE_TEXT = (
    "Patient with a suspected AAA in the ED. I did a bedside aortic scan, got usable "
    "images and measured the aorta at 6 cm, which changed management to an urgent "
    "vascular referral. I learned to scan early when the pain does not fit."
)


def _us_case_draft(**overrides) -> FormDraft:
    fields = {
        "case_reflection_title": "Bedside ultrasound confirming a suspected AAA",
        "date_of_case": "2026-09-24",
        "clinical_scenario": "Suspected AAA in the ED.",
        "how_used": "Bedside aortic scan.",
        "learning_points": "I learned to scan early when the pain does not fit.",
        **overrides,
    }
    return FormDraft(form_type="US_CASE", uuid="uuid-us", fields=fields)


@pytest.mark.parametrize(
    ("form_type", "title_key"),
    [
        ("US_CASE", "case_reflection_title"),
        ("US_CASE_2021", "case_reflection_title"),
        ("SDL", "reflection_title"),
        ("COMPLAINT", "reflection_title"),
        ("SERIOUS_INC", "reflection_title"),
    ],
)
def test_reflection_titles_are_never_a_pre_draft_question(form_type, title_key):
    assert title_key not in {item["key"] for item in bot._form_essential_requirements(form_type)}


def test_factual_titles_are_still_asked_for():
    """A journal paper's or teaching session's title is a fact the drafter
    cannot know, unlike a reflection title it composes from the case."""
    assert "paper_title" in {item["key"] for item in bot._form_essential_requirements("JCF")}
    assert "title_of_session" in {item["key"] for item in bot._form_essential_requirements("TEACH")}


def test_a_title_is_not_the_reflection():
    fields = {"case_reflection_title": "AAA scan", "learning_points": "I learned to scan early."}
    assert bot._find_reflection_keys(fields, "US_CASE") == ["learning_points"]
    assert bot._find_reflection_keys({"reflection_title": "Sepsis module"}, "SDL") == []
    # The schema, not the title, decides whether a reflection is required.
    assert bot._form_requires_reflection("US_CASE") is False
    assert bot._form_requires_reflection("COMPLAINT") is True


def test_ai_declaration_is_never_appended_to_a_title():
    declared = bot._with_rcem_ai_declaration(_us_case_draft(learning_points=""))
    assert declared.fields["case_reflection_title"] == "Bedside ultrasound confirming a suspected AAA"


@pytest.mark.asyncio
async def test_ultrasound_case_drafts_without_asking_for_a_title():
    sim = BotSimulator()
    update = sim._make_callback_update("FORM|US_CASE")
    context = sim._make_context()
    context.user_data["case_text"] = US_CASE_TEXT

    assess = AsyncMock(return_value={})
    analyse = AsyncMock(return_value=_us_case_draft())
    with patch("bot.assess_form_essentials", new=assess), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(update, context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    assert all("I still need" not in text for text in _texts(sim))
    assert context.user_data.get("needs_reflection_detail") is not True
    buttons = {data for _, data in sim.get_last_buttons()}
    assert "APPROVE|draft" in buttons
    assert "ACTION|add_reflection_detail" not in buttons


def test_a_blank_title_is_still_caught_at_save():
    """Not asking for the title is not the same as filing without one."""
    context = BotSimulator()._make_context()
    gaps = bot._pre_draft_completeness_gaps(
        context, _us_case_draft(case_reflection_title=""), "US_CASE"
    )
    assert [gap["key"] for gap in gaps] == ["case_reflection_title"]


# --- the follow-up: every input mode, nothing lost, nothing re-asked -------


async def _draft_with_gaps(sim, context, *, form_type="CBD", missing=("reflection",)):
    update = sim._make_callback_update(f"FORM|{form_type}")
    statuses = _all(form_type, ESSENTIAL_PRESENT, **{key: ESSENTIAL_MISSING for key in missing})
    draft = _cbd_draft(**{key: "" for key in missing})
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot._analyse_selected_form", new=AsyncMock(return_value=draft)):
        state = await handle_form_choice(update, context)
    assert state == AWAIT_APPROVAL
    sim.clear_messages()


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
async def test_cancel_clears_the_essentials_state():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    await _draft_with_gaps(sim, context)

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
    await _draft_with_gaps(sim, context)

    await handle_callback(sim._make_callback_update("CASE|new"), context)

    assert bot._ESSENTIALS_ASSESSMENT_KEY not in context.user_data


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


# --- draft first: missing essentials become gaps inside the draft ----------


@pytest.mark.asyncio
async def test_missing_essentials_draft_at_once_with_the_gaps_named():
    """Owner's decision, 25 Sep 2026: no questions before the draft."""
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE

    analyse = AsyncMock(return_value=_cbd_draft(reflection="", level_of_supervision=""))
    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_MISSING, level_of_supervision=ESSENTIAL_MISSING)
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot._analyse_selected_form", new=analyse):
        result = await handle_form_choice(sim._make_callback_update("FORM|CBD"), context)

    assert result == AWAIT_APPROVAL
    analyse.assert_awaited_once()
    joined = " ".join(_texts(sim))
    assert "I still need" not in joined
    hint = sim.get_last_text().lower().split("still needed:", 1)[1]
    assert "reply with them" in hint
    assert "reflection" in hint and "level of supervision" in hint
    assert "patient presentation" not in hint, "only the gaps are named"
    buttons = sim.get_last_buttons()
    assert ("💾 Save draft now, finish in Kaizen", "APPROVE|draft") in buttons


@pytest.mark.asyncio
async def test_a_model_guess_for_a_missing_essential_never_reaches_the_draft():
    """Drafting before the doctor answers must not let the model fill a role,
    supervision level or reflection they never described."""
    from models import CBDData

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    guessed = CBDData(
        date_of_encounter="2026-03-17",
        patient_presentation="Chest pain",
        trainee_role="Assessed and managed the patient",
        clinical_reasoning="Managed as ACS.",
        reflection="This case reinforced the importance of early ECG review.",
        level_of_supervision="Direct",
    )
    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_MISSING, level_of_supervision=ESSENTIAL_MISSING)
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot.extract_cbd_data", new=AsyncMock(return_value=guessed)), \
         patch("bot.get_voice_profile", return_value=""):
        assert await bot._essentials_gate_before_draft(MagicMock(), context, THIN_CASE, "CBD") is None
        draft = await bot._analyse_selected_form(context, 4242, THIN_CASE, "CBD")

    assert draft.reflection == ""
    assert draft.level_of_supervision is None
    assert draft.trainee_role == "Assessed and managed the patient", "present essentials are kept"


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["form_choice", "retry_template", "case_improve", "accumulate"])
async def test_every_drafting_entrypoint_judges_essentials_before_drafting(entrypoint):
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = THIN_CASE
    context.user_data["chosen_form"] = "CBD"

    order = []
    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_MISSING)

    async def assess(*args, **kwargs):
        order.append("assess")
        return statuses

    async def analyse(*args, **kwargs):
        order.append("draft")
        return _cbd_draft(reflection="")

    patches = _common_patches()
    with patches[0], patches[1], patches[2], \
         patch("bot.assess_form_essentials", new=assess), \
         patch("bot._analyse_selected_form", new=analyse):
        if entrypoint == "form_choice":
            await handle_form_choice(sim._make_callback_update("FORM|CBD"), context)
        elif entrypoint == "retry_template":
            await handle_callback(sim._make_callback_update("ACTION|retry_template"), context)
        elif entrypoint == "case_improve":
            context.user_data["pending_new_case_text"] = "The consultant supervised indirectly."
            await handle_callback(sim._make_callback_update("CASE|improve"), context)
        else:
            context.user_data["pending_draft_data"] = {
                "_type": "FORM",
                "form_type": "CBD",
                "fields": dict(CBD_FIELDS),
                "uuid": "uuid-cbd",
            }
            await bot._accumulate_and_refresh(
                sim._make_text_update("The consultant supervised indirectly."),
                context,
                "The consultant supervised indirectly.",
            )

    assert order and order[0] == "assess", f"{entrypoint} drafted before judging essentials"
    assert "I still need" not in " ".join(_texts(sim))


@pytest.mark.asyncio
async def test_a_reply_regenerates_the_draft_but_still_missing_essentials_stay_blank():
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
    extract = AsyncMock(return_value=_cbd_draft(reflection="An invented learning point."))
    statuses = _all("CBD", ESSENTIAL_PRESENT, reflection=ESSENTIAL_MISSING)
    with patch("bot.assess_form_essentials", new=_assess(statuses)), \
         patch("bot.extract_cbd_data", new=extract), \
         patch("bot.extract_form_data", new=extract):
        result = await bot._regenerate_active_draft_with_feedback(
            sim._make_text_update("The registrar was present."),
            context,
            "The registrar was present.",
            append_to_case=True,
        )

    assert result == AWAIT_APPROVAL
    assert extract.await_count == 1
    assert bot._load_draft(context).fields["reflection"] == ""
    assert "registrar was present" in context.user_data["case_text"]

