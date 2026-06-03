import json
import re
from unittest.mock import AsyncMock, patch

import pytest


def _sample_value(field):
    field_type = field["type"]
    if field_type == "date":
        return "2026-05-21"
    if field_type == "dropdown":
        return field.get("options", [""])[0]
    if field_type == "multi_select":
        return field.get("options", [])[:1]
    if field_type == "kc_tick":
        return ["SLO2"]
    return "Sample portfolio evidence from the case."


def _user_selectable_forms():
    from bot import FORM_CATEGORIES, TRAINING_LEVEL_FORMS, _filter_forms_by_curriculum

    configured = (
        {form for forms in FORM_CATEGORIES.values() for form in forms}
        | {form for forms in TRAINING_LEVEL_FORMS.values() for form in forms}
    )
    selectable = set(configured)
    selectable.update(_filter_forms_by_curriculum(configured, "2021"))
    selectable.update(_filter_forms_by_curriculum(configured, "2025"))
    return selectable


def test_user_selectable_forms_have_schema_uuid_and_route():
    from extractor import FORM_UUIDS
    from extractor import schema_form_type
    from filer_router import PLATFORM_REGISTRY
    from form_schemas import FORM_SCHEMAS
    from kaizen_form_filer import FORM_FIELD_MAP, FORM_UUIDS as KAIZEN_FORM_UUIDS

    selectable_forms = _user_selectable_forms()
    supported_forms = set(PLATFORM_REGISTRY["kaizen"]["supported_forms"])

    assert selectable_forms
    assert selectable_forms <= set(FORM_UUIDS)
    assert selectable_forms <= set(KAIZEN_FORM_UUIDS)
    assert selectable_forms <= supported_forms
    assert {schema_form_type(form) for form in selectable_forms} <= set(FORM_SCHEMAS)

    deterministic_selectable = selectable_forms & supported_forms
    assert deterministic_selectable <= set(FORM_FIELD_MAP)


def test_profile_catalogue_forms_have_category_and_complete_wiring_or_status():
    from extractor import FORM_UUIDS
    from extractor import schema_form_type
    from filer_router import PLATFORM_REGISTRY
    from form_schemas import FORM_SCHEMAS
    from kaizen_form_filer import FORM_FIELD_MAP, FORM_UUIDS as KAIZEN_FORM_UUIDS
    from bot import FORM_CATEGORIES, KAIZEN_CATALOGUE_STATUS, TRAINING_LEVEL_FORMS

    category_forms = {form for forms in FORM_CATEGORIES.values() for form in forms}
    profile_forms = {form for forms in TRAINING_LEVEL_FORMS.values() for form in forms}
    supported_forms = set(PLATFORM_REGISTRY["kaizen"]["supported_forms"])

    gaps = []
    for form in sorted(profile_forms | category_forms):
        if form in KAIZEN_CATALOGUE_STATUS:
            continue
        if form not in category_forms:
            gaps.append(f"{form}:missing category")
        if form not in FORM_UUIDS:
            gaps.append(f"{form}:missing extractor UUID")
        if form not in KAIZEN_FORM_UUIDS:
            gaps.append(f"{form}:missing Kaizen UUID")
        if form not in supported_forms:
            gaps.append(f"{form}:missing deterministic route")
        if schema_form_type(form) not in FORM_SCHEMAS:
            gaps.append(f"{form}:missing schema")
        if form in supported_forms and form not in FORM_FIELD_MAP:
            gaps.append(f"{form}:missing FORM_FIELD_MAP")

    assert gaps == []


def test_suppressed_and_unsupported_catalogue_entries_are_not_user_selectable():
    from bot import FORM_CATEGORIES, KAIZEN_CATALOGUE_STATUS, TRAINING_LEVEL_FORMS

    selectable = (
        {form for forms in FORM_CATEGORIES.values() for form in forms}
        | {form for forms in TRAINING_LEVEL_FORMS.values() for form in forms}
    )
    statuses = {entry["status"] for entry in KAIZEN_CATALOGUE_STATUS.values()}
    hidden_statuses = {
        "supported-hidden-utility",
        "unsupported-pending-schema",
        "unsupported-out-of-scope",
    }

    assert {
        "ASAT",
        "EPA1",
        "EPA2",
        "ACCS_PROGRESS",
        "INTERMEDIATE_PROGRESS",
        "MCR_MTR_ACCS",
        "HALO_ICM",
        "HALO_PROCEDURAL_SEDATION",
        "IAC",
        "EDUCATIONAL_AGREEMENT",
        "ADD_POST",
        "ADD_SUPERVISOR",
        "FILE_UPLOAD",
        "OOP",
        "HIGHER_PROG",
        "ABSENCE",
        "CCT",
    } <= set(KAIZEN_CATALOGUE_STATUS)
    assert statuses <= hidden_statuses
    assert selectable.isdisjoint(KAIZEN_CATALOGUE_STATUS)


def test_accs_dops_and_procedural_log_are_user_selectable_with_2021_variants():
    from bot import TRAINING_LEVEL_FORMS, _filter_forms_by_curriculum
    from extractor import FORM_UUIDS, schema_form_type
    from filer_router import PLATFORM_REGISTRY
    from form_schemas import FORM_SCHEMAS
    from kaizen_form_filer import FORM_FIELD_MAP

    accs_forms = set(TRAINING_LEVEL_FORMS["ACCS"])
    assert {"DOPS_ACCS", "PROCEDURAL_LOG_ACCS"} <= accs_forms

    accs_2021 = set(_filter_forms_by_curriculum(accs_forms, "2021"))
    assert {"DOPS_ACCS_2021", "PROCEDURAL_LOG_ACCS_2021"} <= accs_2021

    supported = set(PLATFORM_REGISTRY["kaizen"]["supported_forms"])
    for form_type in {
        "DOPS_ACCS",
        "DOPS_ACCS_2021",
        "PROCEDURAL_LOG_ACCS",
        "PROCEDURAL_LOG_ACCS_2021",
    }:
        assert form_type in FORM_UUIDS
        assert form_type in FORM_FIELD_MAP
        assert form_type in supported
        assert schema_form_type(form_type) in FORM_SCHEMAS


def test_esle_user_facing_aliases_route_to_assessed_kaizen_form():
    from extractor import canonical_form_type as canonical_extractor_form_type
    from kaizen_form_filer import FORM_FIELD_MAP, FORM_UUIDS, canonical_form_type as canonical_kaizen_form_type

    assert canonical_extractor_form_type("ESLE") == "ESLE_ASSESS"
    assert canonical_kaizen_form_type("ESLE_ASSESS") == "ESLE_PART1_2"
    assert canonical_kaizen_form_type("ESLE") == "ESLE_PART1_2"
    assert FORM_UUIDS["ESLE_ASSESS"] == FORM_UUIDS["ESLE_PART1_2"]
    assert FORM_FIELD_MAP["ESLE_ASSESS"] == FORM_FIELD_MAP["ESLE_PART1_2"]


def test_2021_curriculum_converts_assessed_esle_to_2021_variant():
    from bot import _filter_forms_by_curriculum, _filtered_recommendations_for_curriculum
    from extractor import FORM_UUIDS
    from models import FormTypeRecommendation

    assert _filter_forms_by_curriculum(["ESLE_ASSESS"], "2021") == ["ESLE_2021"]

    [recommendation] = _filtered_recommendations_for_curriculum(
        [FormTypeRecommendation(form_type="ESLE_ASSESS", rationale="Formal ESLE", uuid=FORM_UUIDS["ESLE_ASSESS"])],
        "2021",
    )
    assert recommendation.form_type == "ESLE_2021"
    assert recommendation.uuid == FORM_UUIDS["ESLE_2021"]


def test_2021_manual_category_picker_shows_esle_2021_callback():
    from bot import _CAT_SLUGS, _build_category_forms_keyboard

    with patch("bot.get_training_level", return_value=None), patch("bot.get_curriculum", return_value="2021"):
        keyboard = _build_category_forms_keyboard(123, _CAT_SLUGS["🩺 Clinical"])

    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert "FORM|ESLE_2021" in callbacks
    assert "FORM|ESLE_ASSESS" not in callbacks


def test_generic_higher_profile_does_not_fill_qiat_exact_training_year():
    from bot import _format_curriculum_hierarchy, _stage_value_from_training_level

    assert _stage_value_from_training_level("HIGHER", "QIAT") == ""
    assert _stage_value_from_training_level("ST5", "QIAT") == "ST5"
    assert _stage_value_from_training_level("HIGHER", "DOPS") == "Higher/ST4-ST6"

    labels = _format_curriculum_hierarchy(["SLO10", "SLO11"], [])
    assert "SLO10 — Research" in labels
    assert "SLO11 — Quality improvement & safety" in labels


@pytest.mark.asyncio
async def test_filer_router_uses_deterministic_esle_assessed_alias():
    from filer_router import route_filing

    with patch("filer_router._route_deterministic", new=AsyncMock(return_value={
        "status": "success",
        "filled": ["reflection"],
        "skipped": [],
    })) as deterministic:
        result = await route_filing(
            platform="kaizen",
            form_type="ESLE_ASSESS",
            fields={"reflection": "Formal ESLE part 1 narrative"},
            credentials={"username": "u", "password": "p"},
        )

    assert result["status"] == "success"
    assert deterministic.await_args.args[1] == "ESLE_PART1_2"
    assert deterministic.await_args.kwargs["reuse_draft"] is False


@pytest.mark.asyncio
async def test_filer_router_reuses_existing_draft_only_when_requested():
    from filer_router import route_filing

    with patch("filer_router._route_deterministic", new=AsyncMock(return_value={
        "status": "success",
        "filled": ["reflection"],
        "skipped": [],
    })) as deterministic:
        await route_filing(
            platform="kaizen",
            form_type="DOPS",
            fields={"reflection": "Retry the same DOPS draft"},
            credentials={"username": "u", "password": "p"},
            reuse_draft=True,
        )

    assert deterministic.await_args.kwargs["reuse_draft"] is True


def test_schema_required_fields_have_map_merge_or_explicit_safe_skip():
    from extractor import schema_form_type
    from form_schemas import FORM_SCHEMAS
    from kaizen_form_filer import (
        FORM_FIELD_MAP,
        drop_consumed_unmapped_schema_fields,
        normalise_fields_for_deterministic_filing,
        required_field_handling,
    )

    selectable_forms = sorted(_user_selectable_forms())

    gaps = []
    for form_type in selectable_forms:
        schema = FORM_SCHEMAS[schema_form_type(form_type)]
        field_map = FORM_FIELD_MAP[form_type]
        required_keys = [field["key"] for field in schema["fields"] if field.get("required")]
        for key in required_keys:
            if key in field_map:
                continue
            handling = required_field_handling(form_type, key)
            if not handling:
                gaps.append(f"{form_type}.{key}")
                continue
            if handling.startswith("merge:"):
                target = handling.split(":", 1)[1]
                normalised = normalise_fields_for_deterministic_filing(form_type, {key: "Sample required value"})
                normalised = drop_consumed_unmapped_schema_fields(form_type, normalised)
                if target not in normalised or key in normalised:
                    gaps.append(f"{form_type}.{key}->{handling}")
            elif not handling.startswith("safe_skip:"):
                gaps.append(f"{form_type}.{key}->{handling}")

    assert gaps == []


@pytest.mark.asyncio
async def test_user_selectable_non_cbd_forms_extract_to_form_drafts_without_template_errors():
    from extractor import FORM_UUIDS, extract_form_data, schema_form_type
    from form_schemas import FORM_SCHEMAS
    from models import FormDraft

    selectable_forms = sorted(_user_selectable_forms())

    async def fake_generate(prompt, retries=1, tier=""):
        prompt_form = re.search(r"\(([^()]+)\) WPBA entry", prompt).group(1)
        schema = FORM_SCHEMAS[schema_form_type(prompt_form)]
        payload = {field["key"]: _sample_value(field) for field in schema["fields"]}
        if any(field["type"] == "kc_tick" for field in schema["fields"]):
            payload["key_capabilities"] = [
                "SLO2 KC1: able to support the pre-hospital, medical, nursing and administrative team in answering clinical questions and in making safe decisions for patients with appropriate levels of risk in the ED (2025 Update)"
            ]
        return json.dumps(payload)

    with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
        for form_type in selectable_forms:
            if form_type == "CBD":
                continue
            draft = await extract_form_data(
                "Observed unstable AF management with cardioversion, sedation consent, risk discussion, and reflection on patient experience.",
                form_type,
            )
            assert isinstance(draft, FormDraft), form_type
            assert draft.form_type == form_type
            assert draft.uuid == FORM_UUIDS[form_type]
            assert draft.fields


@pytest.mark.asyncio
async def test_mini_cex_extracts_from_same_case_text_without_live_model_call():
    from extractor import FORM_UUIDS, extract_form_data

    payload = {
        "date_of_encounter": "2026-05-21",
        "clinical_setting": "Emergency Department",
        "patient_presentation": "Unstable atrial fibrillation requiring assessment and cardioversion.",
        "stage_of_training": "Higher/ST4-ST6",
        "complexity": "High",
        "clinical_reasoning": "I assessed the patient, weighed infection risk, consented for sedation, and escalated when cardioversion did not revert the rhythm.",
        "reflection": "I should explain the subjective effects of ketamine more clearly, not just the procedural risks.",
        "curriculum_links": ["SLO2"],
        "key_capabilities": [
            "SLO2 KC1: able to support the pre-hospital, medical, nursing and administrative team in answering clinical questions and in making safe decisions for patients with appropriate levels of risk in the ED (2025 Update)"
        ],
    }

    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(payload))):
        draft = await extract_form_data(
            "Same case: unstable AF, sedation, cardioversion, consent and reflection on explaining ketamine effects.",
            "MINI_CEX",
        )

    assert draft.form_type == "MINI_CEX"
    assert draft.uuid == FORM_UUIDS["MINI_CEX"]
    assert draft.fields["clinical_setting"] == "Emergency Department"
    assert draft.fields["reflection"]


@pytest.mark.asyncio
async def test_qiat_blanks_unsourced_training_year_and_supplements_qi_kcs():
    from extractor import extract_form_data

    payload = {
        "date_of_encounter": "2026-05-21",
        "stage_of_training": "ST4",
        "placement": "Emergency Department",
        "pdp_summary": "Quality improvement project reviewing ED run chart data.",
        "qi_engagement": "I participated in audit and run-chart review.",
        "qi_understanding": "I learned how measurement links to safer systems.",
        "involved_in_project": "Yes",
        "qi_journey_aspects": ["Measurement"],
        "reflection": "HST-level quality improvement work using run charts.",
        "next_pdp": "Continue measurement and governance learning.",
        "curriculum_links": ["SLO11"],
        "key_capabilities": [
            "SLO11 KC1: be able to provide clinical leadership on effective Quality Improvement work (2025 Update)",
            "SLO11 KC2: be able to support and develop a culture of departmental safety, and good clinical governance (2025 Update)",
        ],
    }

    source = (
        "HST trainee quality improvement project using audit data and a run chart "
        "to improve departmental safety. No exact ST year was stated."
    )

    with patch("extractor._generate", new=AsyncMock(return_value=json.dumps(payload))):
        draft = await extract_form_data(source, "QIAT", input_source="image")

    assert draft.fields["stage_of_training"] == ""
    assert len(draft.fields["key_capabilities"]) >= 3
    assert any(kc.startswith("SLO12 KC2:") for kc in draft.fields["key_capabilities"])
    assert draft.fields["curriculum_links"] == ["SLO11", "SLO12"]


@pytest.mark.asyncio
async def test_2021_user_selectable_variant_routes_deterministically():
    from filer_router import route_filing

    with patch("filer_router._route_deterministic", new=AsyncMock(return_value={
        "status": "success",
        "filled": ["patient_presentation"],
        "skipped": [],
    })) as deterministic:
        result = await route_filing(
            platform="kaizen",
            form_type="MINI_CEX_2021",
            fields={
                "date_of_encounter": "2026-05-21",
                "patient_presentation": "Unstable AF assessment.",
                "clinical_reasoning": "I assessed and escalated promptly.",
            },
            credentials={"username": "u", "password": "p"},
        )

    assert result["status"] == "success"
    assert deterministic.await_args.args[1] == "MINI_CEX_2021"
