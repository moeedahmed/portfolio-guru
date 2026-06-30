"""KC-selection quality and curriculum-preview formatting guardrails.

Covers the post-beta draft-quality target: aim for 3 appropriate KCs where the
case genuinely supports them (communication-barrier cases prefer the SLO7
communication KC), never pad a sparse case, and render a clean curriculum
preview (no bare SLO2, no ultra-truncated KC line, no duplicate KC entries).
"""

import json
from unittest.mock import AsyncMock, patch

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
