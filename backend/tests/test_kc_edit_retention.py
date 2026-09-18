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

from extractor import _apply_kc_edit_retention, extract_cbd_data


KC_INJURY = "SLO4 KC1: injury assessment and management (2025 Update)"
KC_ADULT = "SLO1 KC1: assessing and managing adult patients (2025 Update)"
KC_NEW = "SLO7 KC1: communication with patients and relatives (2025 Update)"

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
