"""Key Capability retention across a draft edit.

The edit path re-runs the whole CBD extraction against the *unchanged* case
description (bot.py keeps `append_to_case=False` and puts the doctor's reply in
`edit_feedback`), so a second independent KC pass over the same facts could
silently drop a capability the first pass selected. These tests pin the
deterministic guard: a capability is only lost when the model names it and
states why, never by an unexplained re-selection.

Clinical correctness of any particular capability is NOT asserted here — the
payloads are fabricated contract fixtures, and the labels are only compared to
themselves.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from extractor import KC_FULL_TEXT, _apply_kc_edit_retention, extract_cbd_data


KC_INJURY = KC_FULL_TEXT["SLO4 KC1"]
KC_ADULT = KC_FULL_TEXT["SLO1 KC1"]
KC_NEW = KC_FULL_TEXT["SLO7 KC1"]

CASE = (
    "Setting: Emergency Department. I assessed an adult with an ankle injury "
    "after a fall. I examined the ankle and documented neurovascular status."
)
REFLECTION_ONLY_REPLY = (
    "Learning point: the importance of documenting neurovascular findings "
    "clearly and checking the patient understands when to return."
)


# --- Unit: the retention rule itself -------------------------------------

def test_unexplained_drop_is_restored():
    kept = _apply_kc_edit_retention([KC_INJURY], [KC_INJURY, KC_ADULT], None)
    assert kept == [KC_INJURY, KC_ADULT]


def test_drop_with_a_stated_reason_is_honoured():
    kept = _apply_kc_edit_retention(
        [KC_INJURY],
        [KC_INJURY, KC_ADULT],
        [{"capability": KC_ADULT, "reason": "the doctor asked to remove this curriculum link"}],
    )
    assert kept == [KC_INJURY]


def test_drop_claim_without_a_reason_does_not_count():
    """An empty reason is not a justification — otherwise the model could drop
    anything by emitting a bare entry."""
    kept = _apply_kc_edit_retention(
        [KC_INJURY],
        [KC_INJURY, KC_ADULT],
        [{"capability": KC_ADULT, "reason": "   "}],
    )
    assert kept == [KC_INJURY, KC_ADULT]


def test_newly_evidenced_capability_is_kept_and_order_preserved():
    kept = _apply_kc_edit_retention([KC_INJURY, KC_NEW], [KC_INJURY, KC_ADULT], None)
    assert kept == [KC_INJURY, KC_NEW, KC_ADULT]


def test_whitespace_and_case_differences_are_not_treated_as_a_drop():
    noisy = KC_ADULT.replace(" KC1:", "  KC1:").upper()
    kept = _apply_kc_edit_retention([KC_INJURY, noisy], [KC_INJURY, KC_ADULT], None)
    assert kept == [KC_INJURY, noisy]


def test_no_previous_capabilities_is_a_passthrough():
    assert _apply_kc_edit_retention([KC_INJURY], [], None) == [KC_INJURY]


# --- Contract: through the real extract_cbd_data edit path ---------------

def _payload(key_capabilities, **extra):
    data = {
        "form_type": "CBD",
        "date_of_encounter": "2026-06-29",
        "patient_age": "adult",
        "patient_presentation": "Ankle injury after a fall",
        "clinical_setting": "Emergency Department",
        "stage_of_training": None,
        "trainee_role": "",
        "clinical_reasoning": "I examined the ankle and documented neurovascular status.",
        "reflection": "I will document neurovascular findings more clearly.",
        "level_of_supervision": "Indirect",
        "supervisor_name": "",
        "curriculum_links": [],
        "key_capabilities": list(key_capabilities),
    }
    data.update(extra)
    return data


async def _edit(payload, previous):
    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(payload))) as gen:
        draft = await extract_cbd_data(
            CASE,
            edit_feedback=REFLECTION_ONLY_REPLY,
            current_draft="Key capabilities:\n" + "\n".join(previous),
            previous_key_capabilities=list(previous),
        )
    return draft, gen.await_args[0][0]


@pytest.mark.asyncio
async def test_reflection_only_reply_keeps_both_capabilities():
    """The reported defect: a second pass over unchanged facts returns one
    capability where the live draft had two. The draft must still show two."""
    draft, _ = await _edit(_payload([KC_INJURY]), [KC_INJURY, KC_ADULT])
    assert draft.key_capabilities == [KC_INJURY, KC_ADULT]
    # curriculum_links are re-derived from the retained set, so the preview
    # cannot render one capability under a missing SLO.
    assert set(draft.curriculum_links) == {"SLO4", "SLO1"}


@pytest.mark.asyncio
async def test_explicit_removal_with_a_reason_still_drops_the_capability():
    draft, _ = await _edit(
        _payload(
            [KC_INJURY],
            dropped_key_capabilities=[
                {"capability": KC_ADULT, "reason": "the doctor asked to remove SLO1"}
            ],
        ),
        [KC_INJURY, KC_ADULT],
    )
    assert draft.key_capabilities == [KC_INJURY]
    assert draft.curriculum_links == ["SLO4"]


@pytest.mark.asyncio
async def test_new_evidence_can_still_add_a_capability():
    draft, _ = await _edit(_payload([KC_INJURY, KC_ADULT, KC_NEW]), [KC_INJURY, KC_ADULT])
    assert draft.key_capabilities == [KC_INJURY, KC_ADULT, KC_NEW]


@pytest.mark.asyncio
async def test_retention_instruction_is_only_sent_on_the_edit_path():
    _, edit_prompt = await _edit(_payload([KC_INJURY]), [KC_INJURY, KC_ADULT])
    assert "KEY CAPABILITIES ALREADY ON THIS DRAFT" in edit_prompt
    assert "dropped_key_capabilities" in edit_prompt
    assert KC_ADULT in edit_prompt

    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(_payload([KC_INJURY])))) as gen:
        await extract_cbd_data(CASE)
    initial_prompt = gen.await_args[0][0]
    assert "KEY CAPABILITIES ALREADY ON THIS DRAFT" not in initial_prompt


@pytest.mark.asyncio
async def test_first_draft_selection_is_never_topped_up():
    """Retention applies to edits only — an initial draft still reports exactly
    what the model selected, with no floor on the count."""
    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(_payload([KC_INJURY])))):
        draft = await extract_cbd_data(CASE)
    assert draft.key_capabilities == [KC_INJURY]


@pytest.mark.asyncio
@pytest.mark.parametrize('form_type', ['CBD', 'TEACH'])
@pytest.mark.parametrize('instruction,requested,expected', [
    ('remove SLO9 KC2', ['SLO4 KC1', 'SLO4 KC2'], ['SLO4 KC1', 'SLO4 KC2']),
    ('change the KCs to SLO1 KC1', ['SLO1 KC1'], ['SLO1 KC1']),
    ('change the KCs to SLO1 KC1 and SLO99 KC1', ['SLO1 KC1', 'SLO99 KC1'], ['SLO1 KC1']),
    ('remove all KCs', [], []),
])
async def test_reply_kc_edit_is_validated_and_survives_regeneration(form_type, instruction, requested, expected):
    import bot
    from models import CBDData, FormDraft
    from tests.bot_simulator import BotSimulator

    selected = [KC_FULL_TEXT[k] for k in ('SLO4 KC1', 'SLO4 KC2')]
    candidate = {'capability': KC_FULL_TEXT['SLO9 KC2']}
    original = (CBDData(key_capabilities=selected, possible_key_capability=candidate)
                if form_type == 'CBD' else FormDraft(form_type=form_type,
                    fields={'key_capabilities': selected}, possible_key_capability=candidate))
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(case_text=CASE, chosen_form=form_type)
    bot._store_pending_draft(context, original)
    bot._store_draft(context, original)
    # Drive the real field-update parser and model validation; mock only AI
    # output and the separate essentials assessment.
    with patch('bot.classify_intent', AsyncMock(return_value='edit_detail')), \
         patch('extractor._generate', AsyncMock(return_value=json.dumps(
             {'updates': {'key_capabilities': requested}}))) as generate, \
         patch('bot._essentials_gate_before_draft', AsyncMock(return_value=None)):
        assert await bot.handle_mid_conversation_text(sim._make_text_update(instruction), context) == bot.AWAIT_APPROVAL
    assert 'SLO9 KC2' in generate.await_args.args[0]
    edited = bot._load_draft(context)
    fields = bot._cbd_filing_fields(edited) if form_type == 'CBD' else edited.fields
    assert fields['key_capabilities'] == [KC_FULL_TEXT[k] for k in expected]
    assert fields['curriculum_links'] == list(dict.fromkeys(k.split()[0] for k in expected))
    assert 'excluded_key_capabilities' not in fields
    # An old Show draft button must not resurrect the pending snapshot.
    with patch('bot._track_funnel_event'):
        await bot.handle_callback(sim._make_callback_update('ACTION|continue_thin'), context)
    assert bot._load_draft(context) == edited
    # The next extraction tries to put the removed default back.
    extractor_name = 'bot.extract_cbd_data' if form_type == 'CBD' else 'bot.extract_form_data'
    regenerated = (CBDData(key_capabilities=[*expected, 'SLO9 KC2'], possible_key_capability=candidate)
                   if form_type == 'CBD' else FormDraft(form_type=form_type,
                       fields={'key_capabilities': [*expected, 'SLO9 KC2']}, possible_key_capability=candidate))
    with patch(extractor_name, AsyncMock(return_value=regenerated)), \
         patch('bot._essentials_gate_before_draft', AsyncMock(return_value=None)), \
         patch('bot.get_voice_profile', return_value=''), patch('bot._safe_edit_text', AsyncMock()):
        await bot._regenerate_active_draft_with_feedback(sim._make_text_update('Update the date'), context, 'Update the date')
    updated = bot._load_draft(context)
    fields = bot._cbd_filing_fields(updated) if form_type == 'CBD' else updated.fields
    assert fields['key_capabilities'] == [KC_FULL_TEXT[k] for k in expected]


def test_default_three_kcs_have_only_save_and_cancel_buttons():
    import bot
    from models import CBDData
    from tests.bot_simulator import BotSimulator
    context = BotSimulator()._make_context()
    bot._store_draft(context, CBDData(key_capabilities=['SLO4 KC1', 'SLO4 KC2'],
                                    possible_key_capability={'capability': 'SLO9 KC2'}))
    assert len(bot._load_draft(context).key_capabilities) == 3
    for keyboard in (bot._active_draft_keyboard(context), bot._build_amend_keyboard(context=context)):
        assert [b.text for row in keyboard.inline_keyboard for b in row] == ['💾 Save to Kaizen', '❌ Cancel']


@pytest.mark.asyncio
@pytest.mark.parametrize('form_type', ['CBD', 'TEACH'])
async def test_reply_can_deliberately_select_a_previously_excluded_kc(form_type):
    import bot
    from models import CBDData, FormDraft
    from tests.bot_simulator import BotSimulator
    sim = BotSimulator()
    context = sim._make_context()
    removed = KC_FULL_TEXT['SLO9 KC2']
    draft = (CBDData(key_capabilities=['SLO4 KC1'], excluded_key_capabilities=[removed])
             if form_type == 'CBD' else FormDraft(form_type=form_type,
                 fields={'key_capabilities': ['SLO4 KC1']}, excluded_key_capabilities=[removed]))
    context.user_data.update(case_text=CASE, chosen_form=form_type)
    bot._store_draft(context, draft)
    with patch('bot.classify_intent', AsyncMock(return_value='edit_detail')), \
         patch('extractor._generate', AsyncMock(return_value=json.dumps(
             {'updates': {'key_capabilities': ['SLO4 KC1', 'SLO9 KC2']}}))), \
         patch('bot._essentials_gate_before_draft', AsyncMock(return_value=None)):
        await bot.handle_mid_conversation_text(sim._make_text_update('Add SLO9 KC2'), context)
    updated = bot._load_draft(context)
    fields = bot._cbd_filing_fields(updated) if form_type == 'CBD' else updated.fields
    assert fields['key_capabilities'] == [KC_FULL_TEXT['SLO4 KC1'], removed]
    assert not updated.excluded_key_capabilities
