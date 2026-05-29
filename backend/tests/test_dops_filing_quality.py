"""Offline tests for the DOPS filing quality fixes.

Focused on the unstable AF / RVR / ketamine sedation / synchronised
cardioversion dogfood scenario where the previous filer left case_observed
blank and only tagged SLO6 KC1.

These tests deliberately avoid touching Playwright, Kaizen, or any browser —
they exercise the pure helpers in `dops_filing.py` plus the early-exit
quality gate inside `file_to_kaizen`. Live browser/Kaizen tests live behind
the `kaizen` marker and are not run here.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dops_filing import (  # noqa: E402
    derive_dops_curriculum_links,
    normalise_dops_placement,
    normalise_dops_fields,
    suggest_dops_kc_breadth,
)
from kaizen_form_filer import (  # noqa: E402
    FORM_FIELD_MAP,
    _fill_field_legacy,
    apply_common_header_defaults,
    file_to_kaizen,
)


UNSTABLE_AF_CASE = (
    "ST5 EM higher trainee in the resus room. 62-year-old presented in "
    "unstable atrial fibrillation with rapid ventricular response (RVR), "
    "hypotensive and clammy. We decided on emergency synchronised DC "
    "cardioversion under ketamine sedation. First two synchronised shocks "
    "did not capture; third shock at higher energy converted briefly then "
    "the rhythm became refractory. Loaded amiodarone and gave IV magnesium. "
    "Bedside echo showed adequate LV function. Escalated early to the med "
    "reg and ITU. Patient stabilised and was admitted to coronary care."
)


# ─── normalise_dops_fields ────────────────────────────────────────────────────


def test_normalise_dops_builds_case_observed_from_schema_keys():
    fields = {
        "date_of_encounter": "2026-05-19",
        "stage_of_training": "Higher/ST4-ST6",
        "clinical_setting": "Emergency Department - Resus",
        "procedure_name": "DC cardioversion",
        "indication": "Unstable AF with RVR, hypotensive",
        "trainee_performance": (
            "I led the synchronised cardioversion under ketamine sedation. "
            "Delivered three shocks; the third converted transiently."
        ),
        "clinical_reasoning": (
            "Haemodynamic instability mandated electrical cardioversion "
            "rather than rate control."
        ),
        "reflection": "Reinforced the need for early ITU escalation.",
    }
    out = normalise_dops_fields(fields)

    assert "case_observed" in out and out["case_observed"]
    case_observed = out["case_observed"]
    # Each schema-side narrative landed in the single DOM slot.
    assert "DC cardioversion" in case_observed
    assert "Unstable AF" in case_observed
    assert "ketamine sedation" in case_observed
    assert "Haemodynamic instability" in case_observed
    assert "Procedure observed:" not in case_observed
    assert "Indication:" not in case_observed
    assert "Trainee performance:" not in case_observed
    # Reflection is a separate DOM field — must NOT be folded into case_observed.
    assert "ITU escalation" not in case_observed


def test_normalise_dops_mirrors_procedure_keys_and_dates():
    out = normalise_dops_fields({
        "date_of_encounter": "2026-05-19",
        "procedure_name": "DC cardioversion",
    })
    # procedural_skill mirrors procedure_name (same DOM dropdown).
    assert out["procedural_skill"] == "DC cardioversion"
    # Dates fan out so Kaizen does not reject the draft for missing end/event.
    assert out["end_date"] == "2026-05-19"
    assert out["date_of_event"] == "2026-05-19"


def test_normalise_dops_rebuilds_case_observed_from_reviewed_schema_fields():
    stale_case_observed = "Existing thin summary that should not be filed."
    out = normalise_dops_fields({
        "date_of_encounter": "2026-05-19",
        "procedure_name": "DC cardioversion",
        "clinical_setting": "Emergency Department Resus",
        "case_observed": stale_case_observed,
        "indication": "Unstable atrial fibrillation with hypotension.",
        "trainee_performance": (
            "I prepared the resuscitation team, consented the patient, "
            "administered ketamine sedation, and delivered synchronised shocks."
        ),
    })
    # The preview fields win, so Kaizen receives the same narrative content the
    # user reviewed instead of a separate stale model summary.
    assert stale_case_observed not in out["case_observed"]
    assert "Unstable atrial fibrillation" in out["case_observed"]
    assert "prepared the resuscitation team" in out["case_observed"]
    assert "Indication:" not in out["case_observed"]
    assert "Trainee performance:" not in out["case_observed"]


def test_normalise_dops_preserves_case_observed_without_schema_narrative():
    user_case_observed = "Existing narrative the user typed."
    out = normalise_dops_fields({
        "date_of_encounter": "2026-05-19",
        "procedure_name": "DC cardioversion",
        "case_observed": user_case_observed,
    })
    assert out["case_observed"] == user_case_observed


def test_normalise_dops_promotes_clinical_setting_to_placement_when_blank():
    out = normalise_dops_fields({
        "date_of_encounter": "2026-05-19",
        "clinical_setting": "Emergency Department",
        "procedure_name": "DC cardioversion",
    })
    assert out["placement"] == "Emergency Department"


@pytest.mark.parametrize("clinical_setting", ["ED", "Resus", "Emergency Department - Resus"])
def test_normalise_dops_maps_ed_resus_aliases_to_placement(clinical_setting):
    out = normalise_dops_fields({
        "date_of_encounter": "2026-05-19",
        "clinical_setting": clinical_setting,
        "procedure_name": "DC cardioversion",
    })
    assert out["placement"] == "Emergency Department"


def test_normalise_dops_placement_returns_exact_kaizen_option():
    options = ["", "Emergency Medicine", "Acute Medical Ward", "Intensive Care Unit"]

    assert normalise_dops_placement("ED Resus", options) == "Emergency Medicine"
    assert normalise_dops_placement("ITU", options) == "Intensive Care Unit"


def test_normalise_dops_is_idempotent():
    fields = {
        "date_of_encounter": "2026-05-19",
        "stage_of_training": "Higher/ST4-ST6",
        "clinical_setting": "Emergency Department",
        "procedure_name": "DC cardioversion",
        "indication": "Unstable AF with RVR",
        "trainee_performance": "Led the cardioversion under sedation.",
    }
    once = normalise_dops_fields(fields)
    twice = normalise_dops_fields(once)
    assert once == twice





# ─── suggest_dops_kc_breadth ─────────────────────────────────────────────────


def test_dops_kc_breadth_for_unstable_af_dogfood_case():
    augmented = suggest_dops_kc_breadth(UNSTABLE_AF_CASE, existing_kcs=[])
    codes = [kc.split(":", 1)[0].strip() for kc in augmented]
    # SLO6 KC2 must appear — the trainee personally performed the procedure.
    assert "SLO6 KC2" in codes
    # The case text triggers life-threatening / peri-arrest resus territory.
    assert "SLO3 KC3" in codes
    # Circulatory support / unstable haemodynamics → SLO3 KC2.
    assert "SLO3 KC2" in codes
    # Early ITU and med-reg escalation → SLO3 KC5 (resus team leadership).
    assert "SLO3 KC5" in codes


def test_dops_kc_breadth_preserves_llm_selections_without_duplicating():
    llm_choice = (
        "SLO6 KC1: the clinical knowledge to identify when key EM "
        "practical/emergency skills are indicated (2025 Update)"
    )
    augmented = suggest_dops_kc_breadth(UNSTABLE_AF_CASE, existing_kcs=[llm_choice])
    # Original KC retained.
    assert llm_choice in augmented
    # No duplicate SLO6 KC2 if the LLM had already chosen it.
    again = suggest_dops_kc_breadth(UNSTABLE_AF_CASE, existing_kcs=augmented)
    assert again == augmented


def test_dops_kc_breadth_skips_unrelated_cases():
    bland_case = "Routine teaching observation about ECG interpretation."
    assert suggest_dops_kc_breadth(bland_case, existing_kcs=[]) == []


def test_derive_dops_curriculum_links_extracts_slo_codes():
    augmented = suggest_dops_kc_breadth(UNSTABLE_AF_CASE, existing_kcs=[])
    links = derive_dops_curriculum_links(augmented)
    assert "SLO3" in links
    assert "SLO6" in links


# ─── file_to_kaizen integration (no Playwright) ──────────────────────────────


def test_field_map_has_required_dops_dom_slots():
    dops_map = FORM_FIELD_MAP["DOPS"]
    for key in ("date_of_encounter", "end_date", "date_of_event",
                "stage_of_training", "procedure_name", "procedural_skill",
                "case_observed", "placement", "reflection"):
        assert key in dops_map, f"DOPS field map missing {key}"


def test_common_header_defaults_fill_static_kaizen_wrapper_fields():
    fields, meta = apply_common_header_defaults("CBD", {
        "clinical_setting": "Emergency Department",
        "patient_presentation": "Chest pain",
        "clinical_reasoning": "Managed as ACS with escalation.",
    })

    assert fields["date_of_encounter"]
    assert fields["end_date"] == fields["date_of_encounter"]
    assert "date_of_encounter" in meta["defaulted_fields"]
    assert "Chest pain" in fields["event_description"]


def test_common_header_description_is_complete_one_line_summary():
    fields, meta = apply_common_header_defaults("CBD", {
        "date_of_encounter": "2026-05-27",
        "patient_presentation": "A 70-year-old patient in ED resus with acute shortness of breath and hypotension",
        "clinical_reasoning": (
            "I assessed a 70-year-old patient in ED resus with acute shortness of breath and hypotension. "
            "I led the initial ABCDE assessment, recognised shock, started treatment and escalated."
        ),
    })

    description = fields["event_description"]
    assert description == meta["event_description"]
    assert "\n" not in description
    assert not description.endswith(("...", "…"))
    assert "recognis." not in description
    assert description.endswith(".") or description.endswith("hypotension")
    assert len(description) <= 110


def test_common_header_sanitises_supplied_clipped_description():
    fields, _ = apply_common_header_defaults("CBD", {
        "date_of_encounter": "2026-05-27",
        "event_description": (
            "I assessed a 70-year-old patient in ED resus with acute shortness of breath and hypotension. "
            "I led the initial ABCDE assessment, recognis..."
        ),
    })

    assert fields["event_description"] == (
        "I assessed a 70-year-old patient in ED resus with acute shortness of breath and hypotension."
    )


def test_common_header_sanitises_short_clipped_description_without_prior_sentence():
    fields, _ = apply_common_header_defaults("CBD", {
        "date_of_encounter": "2026-05-27",
        "event_description": "I led the initial ABCDE assessment, recognis...",
    })

    assert fields["event_description"] == "I led the initial ABCDE assessment."





@pytest.mark.asyncio
async def test_file_to_kaizen_dops_proceeds_when_only_date_is_missing():
    """Missing date + otherwise substantive narrative must not gate. Kaizen
    defaults the date to today; refusing to file would override the user's
    explicit Save as draft for a recoverable gap."""
    fields = {
        # No date_of_encounter — bot already warned the user before save.
        "stage_of_training": "Higher/ST4-ST6",
        "clinical_setting": "Emergency Department",
        "procedure_name": "DC cardioversion",
        "indication": "Unstable AF with RVR and hypotension.",
        "trainee_performance": (
            "I led the synchronised cardioversion under ketamine sedation, "
            "delivered three escalating shocks, escalated to ITU."
        ),
        "reflection": "Reinforced the value of early ITU escalation.",
    }

    with patch("kaizen_form_filer.KAIZEN_USE_CDP", False), \
         patch("kaizen_form_filer.async_playwright") as ap_mock, \
         patch("kaizen_form_filer._login", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._fill_field_legacy", new=AsyncMock(return_value=True)) as fill_mock, \
         patch("kaizen_form_filer._save_draft_legacy", new=AsyncMock(return_value=True)) as save_mock, \
         patch("kaizen_form_filer._verify_entry_saved", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._fill_curriculum_links", new=AsyncMock(return_value=([], []))), \
         patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        page = AsyncMock()
        page.url = "https://kaizenep.com/events/new-section/dops-uuid"
        page.goto = AsyncMock()
        browser = AsyncMock()
        browser.new_page = AsyncMock(return_value=page)
        pw = AsyncMock()
        pw.chromium.launch = AsyncMock(return_value=browser)
        ap_mock.return_value.start = AsyncMock(return_value=pw)

        result = await file_to_kaizen("DOPS", fields, "user", "pass")
        # Save was actually attempted — no hard quality-gate block.
        save_mock.assert_awaited()

    assert not result.get("quality_gate_failed")
    assert result["status"] in ("success", "partial")
    assert "date_of_encounter" in result["defaulted_fields"]
    assert "end_date" in result["filled"]
    assert "event_description" in result["filled"]
    filled_dom_ids = [call.args[1] for call in fill_mock.await_args_list]
    assert "startDate" in filled_dom_ids
    assert "endDate" in filled_dom_ids
    assert "event-description" in filled_dom_ids


@pytest.mark.asyncio
async def test_file_to_kaizen_dops_proceeds_when_fields_are_populated():
    fields = {
        "date_of_encounter": "2026-05-19",
        "stage_of_training": "Higher/ST4-ST6",
        "clinical_setting": "Emergency Department",
        "procedure_name": "DC cardioversion",
        "indication": "Unstable AF with RVR",
        "trainee_performance": "Led the cardioversion under ketamine sedation.",
        "reflection": "Reinforced the value of early ITU escalation.",
    }

    with patch("kaizen_form_filer.KAIZEN_USE_CDP", False), \
         patch("kaizen_form_filer.async_playwright") as ap_mock, \
         patch("kaizen_form_filer._login", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._fill_field_legacy", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._save_draft_legacy", new=AsyncMock(return_value=True)) as save_mock, \
         patch("kaizen_form_filer._verify_entry_saved", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._fill_curriculum_links", new=AsyncMock(return_value=([], []))), \
         patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        page = AsyncMock()
        page.url = "https://kaizenep.com/events/new-section/dops-uuid"
        page.goto = AsyncMock()
        browser = AsyncMock()
        browser.new_page = AsyncMock(return_value=page)
        pw = AsyncMock()
        pw.chromium.launch = AsyncMock(return_value=browser)
        ap_mock.return_value.start = AsyncMock(return_value=pw)

        result = await file_to_kaizen("DOPS", fields, "user", "pass")
        # When the gate passes, filing actually runs.
        save_mock.assert_awaited()

    assert result["status"] in ("success", "partial")
    # Even if status is partial (e.g. due to mocked verification), the
    # blocking error string from the gate must not appear.
    assert "Case observed narrative" not in (result.get("error") or "")


@pytest.mark.asyncio
async def test_file_to_kaizen_dops_attempts_normalised_placement_from_clinical_setting():
    fields = {
        "date_of_encounter": "2026-05-19",
        "stage_of_training": "Higher/ST4-ST6",
        "clinical_setting": "ED Resus",
        "procedure_name": "DC cardioversion",
        "indication": "Unstable AF with RVR and hypotension.",
        "trainee_performance": (
            "I prepared the resuscitation team and delivered synchronised "
            "cardioversion under direct supervision."
        ),
    }

    with patch("kaizen_form_filer.KAIZEN_USE_CDP", False), \
         patch("kaizen_form_filer.async_playwright") as ap_mock, \
         patch("kaizen_form_filer._login", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._fill_field_legacy", new=AsyncMock(return_value=True)) as fill_mock, \
         patch("kaizen_form_filer._save_draft_legacy", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._verify_entry_saved", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._fill_curriculum_links", new=AsyncMock(return_value=([], []))), \
         patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        page = AsyncMock()
        page.url = "https://kaizenep.com/events/new-section/dops-uuid"
        page.goto = AsyncMock()
        browser = AsyncMock()
        browser.new_page = AsyncMock(return_value=page)
        pw = AsyncMock()
        pw.chromium.launch = AsyncMock(return_value=browser)
        ap_mock.return_value.start = AsyncMock(return_value=pw)

        result = await file_to_kaizen("DOPS", fields, "user", "pass")

    assert result["status"] in ("success", "partial")
    placement_calls = [
        call for call in fill_mock.await_args_list
        if len(call.args) >= 4 and call.args[3] == "placement"
    ]
    assert placement_calls, "DOPS placement was not attempted by the deterministic filer"
    assert placement_calls[0].args[2] == "Emergency Department"


@pytest.mark.asyncio
async def test_legacy_field_fill_selects_exact_dops_placement_option():
    select = AsyncMock()
    select.count = AsyncMock(return_value=1)
    select.evaluate = AsyncMock(return_value="SELECT")
    select.select_option = AsyncMock()

    page = MagicMock()
    page.locator = MagicMock(return_value=select)
    page.evaluate = AsyncMock(return_value=["", "Emergency Medicine", "Intensive Care Unit"])

    ok = await _fill_field_legacy(
        page,
        FORM_FIELD_MAP["DOPS"]["placement"],
        "Resus",
        "placement",
        "DOPS",
    )

    assert ok is True
    select.select_option.assert_awaited_with(label="Emergency Medicine")
