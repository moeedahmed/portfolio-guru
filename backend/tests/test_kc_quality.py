"""KC-selection quality and curriculum-preview formatting guardrails.

Covers the post-beta draft-quality target: aim for 3 appropriate KCs where the
case genuinely supports them (communication-barrier cases prefer the SLO7
communication KC), never pad a sparse case, and render a clean curriculum
preview (no bare SLO2, no ultra-truncated KC line, no duplicate KC entries).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from extractor import _clinical_kc_supplement_codes, _supplement_supported_key_capabilities


# --- Deterministic supplement: communication barrier prefers SLO7 KC1 ---

DIFFICULT_CASE = (
    "Busy ED on a late shift. Adult with abdominal pain, limited English and an "
    "anxious family at the bedside. Sepsis risk was not obvious and I anchored on "
    "surgical pain, so escalation to my senior was delayed by about 30 minutes. "
    "After senior review the plan was corrected and there was no harm."
)


def test_clinical_supplement_prefers_communication_kc_for_language_barrier():
    codes = _clinical_kc_supplement_codes(DIFFICULT_CASE)
    assert "SLO7 KC1" in codes, f"language/family barrier should map to SLO7 KC1, got {codes}"
    # SLO2 (safe decisions/escalation) and SLO3 KC3 (sepsis) are also genuine here
    assert "SLO2 KC1" in codes
    assert "SLO3 KC3" in codes
    # The communication KC must rank ahead of the external-team KC for a barrier case
    assert codes.index("SLO7 KC1") < codes.index("SLO7 KC3")


def test_clinical_supplement_keeps_external_team_kc_for_referral():
    codes = _clinical_kc_supplement_codes(
        "I handed over the patient to the medical registrar and referred to ICU."
    )
    assert "SLO7 KC3" in codes
    assert "SLO7 KC1" not in codes


# Exact fictional software fixture from the reported ankle case (no live
# model call; deterministic supplement heuristic only).
ANKLE_CASE_DETERMINISTIC = (
    "Please create a Case-Based Discussion for today. Setting: Emergency Department. "
    "I assessed an adult with an ankle injury after a fall. I examined the ankle, "
    "checked and documented neurovascular status, and discussed the assessment and "
    "imaging decision with my supervisor. I provided discharge advice and safety-netting. "
    "Learning point: the importance of documenting neurovascular findings clearly and "
    "checking that the patient understands when to return for reassessment."
)


def test_clinical_supplement_does_not_credit_teaching_kc_for_being_supervised():
    """'my supervisor' names who supervised the trainee — the trainee did not
    teach or supervise anyone, so SLO9 KC1 (training/supervision *given* by
    the trainee) must not be fabricated from the bare 'supervis' stem. Only
    the two genuinely supported KCs (SLO2 KC1 for the decision discussed with
    a senior, SLO1 KC1 for the assessment) should be offered; a third KC must
    not be padded in without support."""
    codes = _clinical_kc_supplement_codes(ANKLE_CASE_DETERMINISTIC)
    assert "SLO9 KC1" not in codes, f"'my supervisor' must not imply the trainee taught/supervised, got {codes}"
    assert codes == ["SLO2 KC1", "SLO1 KC1"], (
        f"expected exactly the two genuinely supported KCs, got {codes}"
    )


def test_supplement_preserves_extracted_trainee_teaching():
    from extractor import KC_FULL_TEXT
    kc = KC_FULL_TEXT["SLO9 KC1"]
    out = _supplement_supported_key_capabilities(
        {"curriculum_links": ["SLO9"], "key_capabilities": [kc]},
        case_description="I taught a junior doctor and provided feedback.",
        schema_key="REFLECT_LOG", has_kc_tick=True,
    )
    assert kc in out["key_capabilities"]


@pytest.mark.parametrize(
    "case_description",
    [
        "I was supervised by my consultant throughout the case.",
        "I was supervised by a senior colleague.",
        "My consultant provided feedback on my assessment.",
        "I received feedback from my supervisor afterwards.",
        "I attended teaching on ankle injuries this week.",
        "Feedback was given to me by the registrar after the shift.",
    ],
)
def test_clinical_supplement_does_not_credit_teaching_kc_for_passive_receipt(case_description):
    """Being on the receiving end of supervision, feedback or teaching shows
    no training capability delivered by the trainee — 'was supervised',
    'received feedback' and 'attended teaching' must not be credited as
    SLO9 KC1 just because they share the teach/feedback/supervis stems with
    the genuinely trainee-delivered phrasing."""
    codes = _clinical_kc_supplement_codes(case_description)
    assert "SLO9 KC1" not in codes, f"passive receipt must not imply trainee-delivered teaching, got {codes}"


def test_clinical_supplement_does_not_pad_broad_kc1_without_support():
    # No assessment/management language and only one genuine signal (escalation)
    codes = _clinical_kc_supplement_codes(
        "I reviewed a set of blood results and documented a plan."
    )
    assert "SLO1 KC1" not in codes, f"broad SLO1 KC1 must not be auto-padded, got {codes}"


def test_supplement_reaches_three_appropriate_kcs_for_difficult_case():
    fields = {
        "curriculum_links": ["SLO2"],
        "key_capabilities": [
            "SLO2 KC1: able to support the pre-hospital, medical, nursing and "
            "administrative team in answering clinical questions and in making "
            "safe decisions for patients with appropriate levels of risk in the ED (2025 Update)"
        ],
    }
    out = _supplement_supported_key_capabilities(
        fields,
        case_description=DIFFICULT_CASE,
        schema_key="REFLECT_LOG",
        has_kc_tick=True,
    )
    kcs = out["key_capabilities"]
    assert len(kcs) == 3, f"difficult case should reach 3 appropriate KCs, got {kcs}"
    assert any(kc.startswith("SLO7 KC1:") for kc in kcs), f"missing communication KC: {kcs}"
    assert set(out["curriculum_links"]) >= {"SLO2", "SLO7"}


def test_supplement_does_not_pad_sparse_case_to_three():
    fields = {"curriculum_links": [], "key_capabilities": []}
    out = _supplement_supported_key_capabilities(
        fields,
        case_description="I reviewed a set of blood results and documented a plan.",
        schema_key="REFLECT_LOG",
        has_kc_tick=True,
    )
    assert len(out["key_capabilities"]) < 3, (
        f"sparse case must not be padded to 3, got {out['key_capabilities']}"
    )


@pytest.mark.asyncio
async def test_reflect_log_difficult_case_supplemented_end_to_end():
    from extractor import extract_form_data

    payload = {
        "date_of_encounter": "2026-06-29",
        "reflection_title": "Anchoring on surgical pain in a possible sepsis presentation",
        "reflection": (
            "I saw an adult with abdominal pain on a busy late shift. There was a "
            "language barrier and an anxious family. I initially anchored on a surgical "
            "cause and escalation to my senior was delayed."
        ),
        "replay_differently": "I would escalate to my senior sooner.",
        "curriculum_links": ["SLO2"],
        "key_capabilities": [
            "SLO2 KC1: able to support the pre-hospital, medical, nursing and "
            "administrative team in answering clinical questions and in making safe "
            "decisions for patients with appropriate levels of risk in the ED (2025 Update)"
        ],
    }
    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(payload))):
        draft = await extract_form_data(DIFFICULT_CASE, "REFLECT_LOG")

    kcs = draft.fields["key_capabilities"]
    assert len(kcs) == 3
    assert any(kc.startswith("SLO2 KC1:") for kc in kcs)
    assert any(kc.startswith("SLO7 KC1:") for kc in kcs)
    assert set(draft.fields["curriculum_links"]) >= {"SLO2", "SLO7"}


# --- CBD curriculum_links/key_capabilities drift (SLO4 KC1-alone regression) ---

ANKLE_CASE = (
    "Setting: Emergency Department. I assessed an adult with an ankle injury after "
    "a fall. I examined the ankle, checked and documented neurovascular status, and "
    "discussed the assessment and imaging decision with my supervisor. I provided "
    "discharge advice and safety-netting.\n\n"
    "Learning point: the importance of documenting neurovascular findings clearly "
    "and checking that the patient understands when to return for reassessment."
)


@pytest.mark.asyncio
async def test_cbd_curriculum_links_reflect_every_selected_kc():
    """Reproduces the reported *shape* of the defect for this exact fictional
    ankle case: a curriculum_links/key_capabilities drift where the model
    names only one SLO in curriculum_links despite selecting KCs across three.
    The three KCs below (SLO4 KC1, SLO2 KC1, SLO9 KC1) are genuine entries
    from `KC_FULL_TEXT`, but this payload is a constructed reproduction of the
    drift pattern, not a captured/verified historical model response for this
    case — no live model call was made or logged for it, so this proves the
    re-derivation fix, not what Gemini actually returned for the doctor's
    report. Because the preview hierarchy only shows a KC under an SLO
    already in curriculum_links, the other two KCs would otherwise silently
    vanish from the doctor's draft, appearing as if only "SLO4 KC1" had been
    selected. curriculum_links must be re-derived from the actual selected
    KCs so none are dropped."""
    from extractor import extract_cbd_data
    from bot import _format_curriculum_hierarchy

    payload = {
        "form_type": "CBD",
        "date_of_encounter": "2026-06-29",
        "patient_age": "adult",
        "patient_presentation": "Ankle injury after a fall",
        "clinical_setting": "Emergency Department",
        "stage_of_training": None,
        "trainee_role": "",
        "clinical_reasoning": "Examined the ankle and documented neurovascular status.",
        "reflection": (
            "The importance of documenting neurovascular findings clearly and "
            "checking that the patient understands when to return for reassessment."
        ),
        "level_of_supervision": "Indirect",
        "supervisor_name": None,
        # The model names only SLO4, even though key_capabilities below spans
        # SLO4, SLO2 and SLO9 — this is the drift that caused the defect.
        "curriculum_links": ["SLO4"],
        "key_capabilities": [
            "SLO4 KC1: be expert in assessment, investigation and clinical "
            "management of patients attending with all injuries, regardless of "
            "complexity (2025 Update)",
            "SLO2 KC1: able to support the pre-hospital, medical, nursing and "
            "administrative team in answering clinical questions and in making "
            "safe decisions for patients with appropriate levels of risk in the ED (2025 Update)",
            "SLO9 KC1: be able to undertake training and supervision of members "
            "of the ED team in the clinical environment (2025 Update)",
        ],
    }
    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(payload))):
        draft = await extract_cbd_data(ANKLE_CASE)

    assert len(draft.key_capabilities) == 3
    assert set(draft.curriculum_links) == {"SLO4", "SLO2", "SLO9"}, (
        f"curriculum_links must cover every selected KC's SLO, got {draft.curriculum_links}"
    )

    rendered = _format_curriculum_hierarchy(draft.curriculum_links, draft.key_capabilities)
    assert "SLO4" in rendered and "SLO2" in rendered and "SLO9" in rendered
    assert rendered.count("↳ KC1:") == 3, f"all three KCs must render, got:\n{rendered}"


@pytest.mark.asyncio
async def test_cbd_curriculum_links_untouched_when_no_kcs_selected():
    from extractor import extract_cbd_data

    payload = {
        "form_type": "CBD",
        "date_of_encounter": "",
        "patient_age": "",
        "patient_presentation": "",
        "clinical_setting": "",
        "stage_of_training": None,
        "trainee_role": "",
        "clinical_reasoning": "",
        "reflection": "",
        "level_of_supervision": "",
        "supervisor_name": None,
        "curriculum_links": [],
        "key_capabilities": [],
    }
    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(payload))):
        draft = await extract_cbd_data("Reviewed a set of blood results and documented a plan.")

    assert draft.curriculum_links == []
    assert draft.key_capabilities == []


def test_derive_curriculum_links_skips_malformed_kc_strings():
    """A malformed or empty KC entry (no SLO prefix) must not raise and must
    not block sound links from valid entries elsewhere in the same list."""
    from extractor import _derive_curriculum_links_from_kcs

    assert _derive_curriculum_links_from_kcs(None) == []
    assert _derive_curriculum_links_from_kcs([]) == []
    assert _derive_curriculum_links_from_kcs(["", "   ", "not a kc string"]) == []
    links = _derive_curriculum_links_from_kcs([
        "not a kc string",
        "SLO4 KC1: be expert in assessment, investigation and clinical management "
        "of patients attending with all injuries, regardless of complexity (2025 Update)",
        "",
        "SLO4 KC1: be expert in assessment, investigation and clinical management "
        "of patients attending with all injuries, regardless of complexity (2025 Update)",
        "SLO2 KC1: able to support the pre-hospital, medical, nursing and "
        "administrative team in answering clinical questions and in making safe "
        "decisions for patients with appropriate levels of risk in the ED (2025 Update)",
    ])
    assert links == ["SLO4", "SLO2"]


# --- Curriculum preview formatting ---

def test_preview_labels_slo2_with_a_title():
    from bot import _format_curriculum_hierarchy

    out = _format_curriculum_hierarchy(
        ["SLO2"],
        [
            "SLO2 KC1: able to support the pre-hospital, medical, nursing and "
            "administrative team in answering clinical questions and in making safe "
            "decisions for patients with appropriate levels of risk in the ED (2025 Update)"
        ],
    )
    assert "• *SLO2 — Clinical questions & safe decisions*" in out
    # never a bare untitled SLO2 line
    assert "• *SLO2*" not in out
    # KC snippet is scannable, not an ultra-truncated mid-list fragment
    assert "↳ KC1: supporting the team's safe decisions" in out
    assert "medical," not in out


def test_preview_slo3_uses_resuscitation_label_not_clinical_questions():
    from bot import _format_curriculum_hierarchy

    out = _format_curriculum_hierarchy(
        ["SLO3"],
        ["SLO3 KC3: manage all the life-threatening conditions including peri-arrest & arrest situations in the ED (2025 Update)"],
    )
    assert "SLO3 — Resuscitation & stabilisation" in out
    assert "Clinical questions & decisions" not in out


def test_preview_deduplicates_repeated_kc_entries():
    from bot import _format_curriculum_hierarchy

    out = _format_curriculum_hierarchy(
        ["SLO7"],
        [
            "SLO7 KC1: have expert communication skills to negotiate, manage complicated or evolving interactions (2025 Update)",
            "SLO7 KC1: have expert communication skills to negotiate, manage complicated or evolving interactions (2025 Update)",
        ],
    )
    assert out.count("↳ KC1:") == 1


def test_preview_fallback_truncation_is_clean_for_unknown_kc():
    from bot import _format_curriculum_hierarchy

    out = _format_curriculum_hierarchy(
        ["SLO1"],
        ["SLO1 KC9: be expert in some newly added capability that the curated map does not yet know about and keeps going (2025 Update)"],
    )
    # Unknown code falls back to a cleaned truncation: no "(2025 Update)" noise,
    # no trailing conjunction, leading filler stripped.
    assert "(2025 Update)" not in out
    assert "be expert in" not in out
    assert "↳ KC9:" in out


# --- Post-save confirmation: never leak raw internal KC/tag labels ---
#
# Live beta evidence (2026-06-30 Reflective Practice Log save): a tag-only KC
# miss surfaced to the doctor as
#   "Key capabilities (6 not ticked), Tag:slo1 kc1..., Tag:slo7 kc1... and 1 other"
# The filer reports these gaps with internal labels; the confirmation must
# normalise them to a single clinician-readable "Curriculum links" line.

LIVE_KC_SKIPPED = [
    "key_capabilities (6 not ticked)",
    "tag:SLO1 KC1: to be expert in assessing and managing all adult patients "
    "attending the ED. These capabilities will apply to patients attending with "
    "both physical and psychological ill health (2025 Update)",
    "tag:SLO7 KC1: have expert communication skills to negotiate, manage "
    "complicated or evolving interactions (2025 Update)",
    "tag:SLO2 KC1: able to support the pre-hospital, medical, nursing and "
    "administrative team in answering clinical questions and in making safe "
    "decisions for patients with appropriate levels of risk in the ED (2025 Update)",
]


def test_friendly_field_name_collapses_tag_label_to_curriculum_links():
    from bot import _friendly_field_name

    name = _friendly_field_name(
        "tag:SLO1 KC1: to be expert in assessing and managing all adult patients "
        "attending the ED (2025 Update)"
    )
    assert name == "Curriculum links"


def test_friendly_field_name_strips_not_ticked_annotation():
    from bot import _friendly_field_name

    name = _friendly_field_name("key_capabilities (6 not ticked)")
    # Curriculum family collapses; never the raw "(6 not ticked)" annotation.
    assert name == "Curriculum links"
    assert "not ticked" not in name
    assert "(" not in name


def test_friendly_skipped_names_collapses_curriculum_family_to_one_line():
    from bot import _friendly_skipped_names

    names = _friendly_skipped_names(LIVE_KC_SKIPPED)
    # The four raw curriculum entries collapse to exactly one review item.
    assert names == ["Curriculum links"]


def test_partial_skipped_display_exposes_no_raw_internal_labels():
    from bot import _friendly_skipped_names

    names = _friendly_skipped_names(LIVE_KC_SKIPPED)
    # Reproduce the handler's display join (>3 truncates with "and N others").
    if len(names) > 3:
        display = ", ".join(names[:3]) + f" and {len(names) - 3} others"
    else:
        display = ", ".join(names)
    lowered = display.lower()
    for leak in ("tag:", "kc1", "slo1", "slo7", "slo2", "not ticked", "_"):
        assert leak not in lowered, f"raw internal label leaked: {leak!r} in {display!r}"
    assert display == "Curriculum links"


def test_friendly_skipped_names_keeps_distinct_real_fields_in_order():
    from bot import _friendly_skipped_names

    names = _friendly_skipped_names(
        [
            "tag:SLO1 KC1: ...",
            "reflection",
            "tag:SLO7 KC1: ...",
            "reflection",
        ]
    )
    # Curriculum collapses to one entry, the real field is kept once, order held.
    assert names == ["Curriculum links", "Reflection"]


def test_field_edit_buttons_skip_curriculum_entries():
    from bot import _build_field_edit_buttons

    rows = _build_field_edit_buttons(LIVE_KC_SKIPPED)
    # Curriculum links aren't text-editable; no dead-end edit button (and no raw
    # internal label smuggled into a callback) should be produced.
    assert rows == []


def test_field_edit_buttons_still_offered_for_real_fields():
    from bot import _build_field_edit_buttons

    rows = _build_field_edit_buttons(["tag:SLO1 KC1: ...", "reflection"])
    callbacks = [btn.callback_data for row in rows for btn in row]
    assert callbacks == ["FIELD|reflection"]


# --- Tick matching: canonical SLOn KCm code is robust to verbose drift ---

def test_canonical_kc_code_extracts_prefix_from_verbose_text():
    from kaizen_form_filer import canonical_kc_code

    assert canonical_kc_code(
        "SLO1 KC1: to be expert in assessing and managing all adult patients (2025 Update)"
    ) == "SLO1 KC1"
    assert canonical_kc_code("tag:SLO7 KC1: have expert communication skills") == "SLO7 KC1"
    assert canonical_kc_code("SLO 2  Key Capability blah KC 3 something") == "SLO2 KC3"
    assert canonical_kc_code("SLO3") is None
    assert canonical_kc_code("") is None


def test_unticked_kc_targets_matches_by_code_not_verbose_text():
    from kaizen_form_filer import _unticked_kc_targets

    targets = [
        "SLO1 KC1: to be expert in assessing and managing adult patients (2025 Update)",
        "SLO7 KC1: have expert communication skills (2025 Update)",
        "SLO2 KC1: able to support the team (2025 Update)",
    ]
    # Ticked stored under a terser code form — must still count as ticked.
    assert _unticked_kc_targets(targets, ["SLO1 KC1", "SLO7 KC1", "SLO2 KC1"]) == []
    # A genuine miss is reported once, by its original verbose target.
    missed = _unticked_kc_targets(targets, ["SLO1 KC1"])
    assert len(missed) == 2
    assert missed[0].startswith("SLO7 KC1:")
    # Nothing ticked → every target is missed (no double-counting of errors).
    assert len(_unticked_kc_targets(targets, [])) == 3


# --- CBD prompt no longer stops at the first plausible KC: regression via
# actual bot dispatch (bot._analyse_selected_form -> extract_cbd_data ->
# _format_curriculum_hierarchy), not a hand-called extractor function. These
# payloads are fabricated stand-ins for what a full-curriculum-aware model
# response should look like; they are not captured live Gemini output. See
# KC_EVIDENCE.md for why a live call is still required before this can be
# called verified.

async def _dispatch_cbd_draft(monkeypatch, payload: dict):
    import bot

    monkeypatch.setattr(bot, "get_voice_profile", lambda user_id: "")
    monkeypatch.setattr(bot, "get_training_level", lambda user_id: None)
    monkeypatch.setattr(bot, "_audit_event", lambda *a, **k: None)

    context = MagicMock()
    context.user_data = {}

    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(payload))):
        draft = await bot._analyse_selected_form(context, 999999, ANKLE_CASE, "CBD")
    return draft


@pytest.mark.asyncio
async def test_cbd_dispatch_keeps_single_kc_when_only_one_is_supported(monkeypatch):
    """A sparse but genuinely one-KC-supported case must render exactly one
    KC — the prompt change must not introduce padding to hit ~3."""
    from bot import _format_curriculum_hierarchy

    payload = {
        "form_type": "CBD",
        "date_of_encounter": "2026-06-29",
        "patient_age": "adult",
        "patient_presentation": "Ankle injury after a fall",
        "clinical_setting": "Emergency Department",
        "stage_of_training": None,
        "trainee_role": "",
        "clinical_reasoning": "Examined the ankle and documented neurovascular status.",
        "reflection": (
            "The importance of documenting neurovascular findings clearly and "
            "checking that the patient understands when to return for reassessment."
        ),
        "level_of_supervision": "Indirect",
        "supervisor_name": None,
        "curriculum_links": ["SLO4"],
        "key_capabilities": [
            "SLO4 KC1: be expert in assessment, investigation and clinical "
            "management of patients attending with all injuries, regardless of "
            "complexity (2025 Update)",
        ],
    }
    draft = await _dispatch_cbd_draft(monkeypatch, payload)

    assert len(draft.key_capabilities) == 1
    rendered = _format_curriculum_hierarchy(draft.curriculum_links, draft.key_capabilities)
    assert rendered.count("↳ KC") == 1


@pytest.mark.asyncio
async def test_cbd_dispatch_renders_full_multi_kc_response_with_correct_slo_links(monkeypatch):
    """A case genuinely supporting multiple independent capabilities must
    render all of them, each under the correct SLO, using the full KC text."""
    from bot import _format_curriculum_hierarchy

    payload = {
        "form_type": "CBD",
        "date_of_encounter": "2026-06-29",
        "patient_age": "adult",
        "patient_presentation": "Ankle injury after a fall",
        "clinical_setting": "Emergency Department",
        "stage_of_training": None,
        "trainee_role": "",
        "clinical_reasoning": "Examined the ankle and documented neurovascular status.",
        "reflection": (
            "The importance of documenting neurovascular findings clearly and "
            "checking that the patient understands when to return for reassessment."
        ),
        "level_of_supervision": "Indirect",
        "supervisor_name": None,
        "curriculum_links": ["SLO4"],
        "key_capabilities": [
            "SLO4 KC1: be expert in assessment, investigation and clinical "
            "management of patients attending with all injuries, regardless of "
            "complexity (2025 Update)",
            "SLO2 KC1: able to support the pre-hospital, medical, nursing and "
            "administrative team in answering clinical questions and in making "
            "safe decisions for patients with appropriate levels of risk in the ED (2025 Update)",
            "SLO9 KC1: be able to undertake training and supervision of members "
            "of the multi-professional team (2025 Update)",
        ],
    }
    draft = await _dispatch_cbd_draft(monkeypatch, payload)

    assert len(draft.key_capabilities) == 3
    assert set(draft.curriculum_links) >= {"SLO4", "SLO2", "SLO9"}
    rendered = _format_curriculum_hierarchy(draft.curriculum_links, draft.key_capabilities)
    assert rendered.count("↳ KC") == 3
    assert "SLO4" in rendered and "SLO2" in rendered and "SLO9" in rendered


STEMI_CASE = (
    "I assessed an adult in the ED with chest pain and inferior ST elevation. "
    "I recognised an inferior STEMI, discussed safe immediate treatment with "
    "the nursing team, started emergency management and coordinated urgent "
    "transfer with the primary PCI team. I learned to activate the pathway early."
)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_count,reviewed_count", [(3, 3), (2, 3), (2, 2)])
async def test_cbd_reviews_short_kc_selection_without_padding(initial_count, reviewed_count):
    from extractor import KC_FULL_TEXT, RCEM_KC_MAP, extract_cbd_data

    kcs = [KC_FULL_TEXT[code] for code in ("SLO1 KC1", "SLO2 KC1", "SLO3 KC3")]
    source = STEMI_CASE if reviewed_count == 3 else (
        "I assessed an adult with a headache, explained my assessment to the "
        "nursing team and agreed a safe discharge plan."
    )
    payload = {"clinical_reasoning": source, "key_capabilities": kcs[:initial_count]}
    reviewed = {**payload, "key_capabilities": kcs[:reviewed_count],
                "clinical_reasoning": "Invented narrative must not replace the draft."}
    generate = AsyncMock(side_effect=[json.dumps(payload), json.dumps(reviewed)])
    with patch("extractor._generate", generate):
        draft = await extract_cbd_data(source)

    assert draft.key_capabilities == kcs[:reviewed_count]
    assert "Invented narrative" not in draft.clinical_reasoning
    assert draft.curriculum_links == ["SLO1", "SLO2", "SLO3"][:reviewed_count]
    assert generate.await_count == (1 if initial_count == 3 else 2)
    if initial_count < 3:
        review_prompt = generate.call_args.args[0]
        assert RCEM_KC_MAP in review_prompt
        assert source in review_prompt
        assert "fewer" in review_prompt.lower()


@pytest.mark.asyncio
async def test_cbd_short_selection_review_keeps_feedback_and_source_fidelity_rules():
    from extractor import KC_FULL_TEXT, _SOURCE_FIDELITY_RULES, extract_cbd_data

    kcs = [KC_FULL_TEXT[code] for code in ("SLO1 KC1", "SLO2 KC1")]
    source = "FAST was done, a CT was arranged."
    feedback = "I did not perform the FAST. No CT body region was specified."
    payload = {"clinical_reasoning": source, "key_capabilities": kcs}
    generate = AsyncMock(return_value=json.dumps(payload))
    with patch("extractor._generate", generate):
        draft = await extract_cbd_data(
            source, edit_feedback=feedback, current_draft=source,
            previous_key_capabilities=kcs,
        )
    assert generate.await_count == 2
    review_prompt = generate.call_args.args[0]
    assert feedback in review_prompt
    assert _SOURCE_FIDELITY_RULES in review_prompt
    assert draft.key_capabilities == kcs
    assert "I performed" not in draft.clinical_reasoning
    assert "CT head" not in draft.clinical_reasoning
    assert "CT abdomen" not in draft.clinical_reasoning


@pytest.mark.asyncio
@pytest.mark.parametrize("review_response", [
    "invalid JSON", "```json\ninvalid JSON\n```", "null", "[]", "{}",
    '{"key_capabilities": null}', '{"key_capabilities": "SLO3 KC3"}',
    '{"key_capabilities": [null]}', '{"key_capabilities": [{}]}',
    '{"key_capabilities": ["   "]}',
])
async def test_unusable_cbd_kc_review_preserves_original_without_padding_or_retry(review_response):
    from extractor import KC_FULL_TEXT, extract_cbd_data

    kcs = [KC_FULL_TEXT["SLO1 KC1"], KC_FULL_TEXT["SLO2 KC1"]]
    payload = {"clinical_reasoning": STEMI_CASE, "key_capabilities": kcs}
    generate = AsyncMock(side_effect=[json.dumps(payload), review_response])
    with patch("extractor._generate", generate):
        draft = await extract_cbd_data(STEMI_CASE)
    assert draft.key_capabilities == kcs
    assert draft.curriculum_links == ["SLO1", "SLO2"]
    assert "inferior STEMI" in draft.clinical_reasoning
    assert generate.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    None, RuntimeError("provider failed"), TimeoutError("provider timeout"),
    '{"key_capabilities": ["invented capability"]}',
    '{"key_capabilities": ["SLO99 KC1: unknown"]}',
    '{"key_capabilities": ["SLO1 KC99: unknown"]}',
    '{"key_capabilities": ["SLO1 KC1garbage"]}',
])
async def test_optional_review_failure_preserves_whole_draft(response):
    from extractor import KC_FULL_TEXT, extract_cbd_data

    payload = {"key_capabilities": [KC_FULL_TEXT["SLO1 KC1"]],
               "clinical_reasoning": STEMI_CASE, "reflection": "I learned to activate the pathway early."}
    with patch("extractor._generate", AsyncMock(return_value=json.dumps(payload))):
        baseline = await extract_cbd_data(STEMI_CASE)
    generate = AsyncMock(side_effect=[json.dumps(payload), response])
    with patch("extractor._generate", generate):
        draft = await extract_cbd_data(STEMI_CASE)
    assert draft == baseline
    assert generate.await_count == 2


@pytest.mark.asyncio
async def test_review_counts_and_adopts_canonical_distinct_identities():
    from extractor import KC_FULL_TEXT, extract_cbd_data

    initial = ["SLO1 KC1: adult assessment", "slo1 kc1: different wording", "SLO2 KC1: team decisions"]
    reviewed = [*initial, "SLO3 KC3: emergency management"]
    generate = AsyncMock(side_effect=[json.dumps({"key_capabilities": initial}),
                                      json.dumps({"key_capabilities": reviewed})])
    with patch("extractor._generate", generate):
        draft = await extract_cbd_data(STEMI_CASE)
    assert generate.await_count == 2
    assert draft.key_capabilities == [KC_FULL_TEXT[k] for k in ("SLO1 KC1", "SLO2 KC1", "SLO3 KC3")]


@pytest.mark.asyncio
@pytest.mark.parametrize("claims", [None, [], "invalid", [{}],
    [{"capability": "SLO2 KC1", "reason": {"invalid": True}}]])
async def test_review_cannot_erase_or_reintroduce_justified_removal(claims):
    from extractor import KC_FULL_TEXT, extract_cbd_data

    kcs = [KC_FULL_TEXT[k] for k in ("SLO1 KC1", "SLO2 KC1")]
    original = {"key_capabilities": kcs[:1], "dropped_key_capabilities": [
        {"capability": kcs[1], "reason": "I did not support team decisions."}]}
    reviewed = {"key_capabilities": kcs, "dropped_key_capabilities": claims}
    with patch("extractor._generate", AsyncMock(side_effect=[json.dumps(original), json.dumps(reviewed)])):
        draft = await extract_cbd_data(STEMI_CASE, edit_feedback="Remove team decisions.",
                                       previous_key_capabilities=kcs)
    assert draft.key_capabilities == kcs[:1]


@pytest.mark.asyncio
async def test_optional_review_has_real_timeout_and_cancels(monkeypatch):
    import asyncio
    import extractor

    monkeypatch.setattr(extractor, "_KC_REVIEW_TIMEOUT_SECONDS", 0.01, raising=False)
    cancelled = asyncio.Event()
    kcs = [extractor.KC_FULL_TEXT["SLO1 KC1"]]
    async def generate(prompt, **kwargs):
        if "KC selection review:" not in prompt:
            return json.dumps({"key_capabilities": kcs})
        assert kwargs["retries"] == 0
        assert kwargs["max_attempts"] == 1
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    with patch("extractor._generate", generate):
        draft = await asyncio.wait_for(extractor.extract_cbd_data(STEMI_CASE), timeout=0.5)
    assert cancelled.is_set()
    assert draft.key_capabilities == kcs


@pytest.mark.asyncio
async def test_optional_review_skipped_when_extraction_used_budget(monkeypatch):
    import asyncio
    import extractor

    monkeypatch.setattr(extractor, "_KC_REVIEW_DEADLINE_SECONDS", 0.01)
    kcs = [extractor.KC_FULL_TEXT["SLO1 KC1"]]
    async def slow_initial(*args, **kwargs):
        await asyncio.sleep(0.02)
        return json.dumps({"key_capabilities": kcs})
    generate = AsyncMock(side_effect=slow_initial)
    with patch("extractor._generate", generate):
        draft = await extractor.extract_cbd_data(STEMI_CASE)
    assert generate.await_count == 1
    assert draft.key_capabilities == kcs


@pytest.mark.asyncio
async def test_review_valid_removal_merges_with_original_exclusions():
    from extractor import KC_FULL_TEXT, extract_cbd_data

    kcs = [KC_FULL_TEXT[k] for k in ("SLO1 KC1", "SLO2 KC1", "SLO3 KC3")]
    original = {"key_capabilities": kcs[:1], "dropped_key_capabilities": [
        {"capability": kcs[1], "reason": "No team decisions."}]}
    reviewed = {"key_capabilities": kcs, "dropped_key_capabilities": [
        {"capability": "SLO3 KC3: other wording", "reason": "No emergency management."}]}
    with patch("extractor._generate", AsyncMock(side_effect=[json.dumps(original), json.dumps(reviewed)])):
        draft = await extract_cbd_data(STEMI_CASE, edit_feedback="Correct the curriculum links.",
                                       previous_key_capabilities=kcs)
    assert draft.key_capabilities == kcs[:1]


@pytest.mark.asyncio
async def test_generate_attempt_cap_prevents_retry_and_provider_fallback(monkeypatch):
    import extractor
    from unittest.mock import MagicMock

    providers = [{"name": "fixture", "type": "openai_compat", "model": "fixture",
                  "base_url": "https://example.invalid", "env_key": "FIXTURE_KEY"}] * 2
    monkeypatch.setenv("FIXTURE_KEY", "offline-fixture")
    post = AsyncMock(side_effect=RuntimeError("503 unavailable"))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = post
    with patch("extractor._select_providers", return_value=providers), \
         patch("extractor.httpx.AsyncClient", return_value=client), \
         patch("extractor.ai_telemetry.record"):
        with pytest.raises(RuntimeError, match="503"):
            await extractor._generate("fixture", retries=0, max_attempts=1)
    assert post.await_count == 1
