"""Offline pins for the per-shape recommender allowed-form fallback.

Plan: ``docs/roadmap/filing-reliability-readiness-sprint-2026-06.md`` § P1.c.

The recommender turns a clinical case into a set of allowed form ids based on
the saved ``training_level``. Three inline call sites in ``bot.py`` have
historically used ``TRAINING_LEVEL_FORMS.get(level, TRAINING_LEVEL_FORMS["ST5"])``
as the fallback, silently leaking the HST/ST5 superset to SAS / CESR shapes
and to unknown levels. This file pins the SAS-safe contract via a thin pure
helper, ``_allowed_forms_for_training_level``, so a regression on any of the
three representative account shapes (HST, ACCS-only, Intermediate-only,
the ``accs_intermediate`` storage bucket, SAS) is loud.

Per the plan's portfolio-type terminology: ACCS and Intermediate are separate
Kaizen portfolio types. ``accs_intermediate`` is a dual-access storage
alias (one trainee with access to both portfolios), not a standalone product
shape. The test ids keep ACCS-only and Intermediate-only distinct from the
dual-access alias, and the local catalogues must not silently alias ST3.

Offline-only: no Kaizen, no credentials, no BWS, no Playwright, no Telegram.
"""

from __future__ import annotations

import pytest


CORE_WPBAS = {"CBD", "DOPS", "MINI_CEX"}
SAS_CORE = {
    "CBD", "DOPS", "MINI_CEX", "ACAT", "ACAF", "MSF", "LAT", "QIAT",
    "JCF", "PROC_LOG", "AUDIT", "REFLECT_LOG", "SDL", "EDU_ACT",
    "FORMAL_COURSE", "TEACH", "STAT", "TEACH_OBS", "TEACH_CONFID",
    "COMPLAINT", "SERIOUS_INC", "APPRAISAL", "CLIN_GOV", "CRIT_INCIDENT",
    "US_CASE", "ESLE_ASSESS", "RESEARCH", "PDP", "EDU_MEETING",
    "EDU_MEETING_SUPP",
}
SAS_2021_FORMS = {
    "JCF_2021", "LAT_2021", "QIAT_2021", "REFLECT_LOG_2021", "AUDIT_2021",
}
QI_AUDIT_SCREENSHOT_TEXT = (
    "Please create the best-fit kaizen draft for an intermediate portfolio account.\n"
    "Quality improvement project in ED: improving time-to-antibiotics for adult sepsis alerts. "
    "Baseline audit showed delays from triage recognition to first antibiotic dose. "
    "Intervention included a sepsis prompt sticker on triage notes, short teaching for nurses "
    "and junior doctors, and a resus-room antibiotic grab-list. Re-audit after two weeks "
    "showed improved time to antibiotics and better documentation of lactate and blood cultures."
)


# ─── Helper exists and is pure ───────────────────────────────────────────


def test_allowed_forms_helper_is_importable_from_bot():
    """The thin pure helper centralising allowed forms must exist.

    Without it, the inline recommender call sites in ``bot.py`` each carry
    their own fallback expression and an SAS regression can land in one site
    but not the others.
    """
    from bot import _allowed_forms_for_training_level  # noqa: F401


# ─── Per-shape contract ──────────────────────────────────────────────────


def test_hst_uses_the_higher_catalogue():
    from bot import _allowed_forms_for_training_level, TRAINING_LEVEL_FORMS

    assert _allowed_forms_for_training_level("HIGHER") == list(
        TRAINING_LEVEL_FORMS["HIGHER"]
    )


@pytest.mark.parametrize(
    "level",
    [
        pytest.param("ACCS", id="accs_only"),
        pytest.param("INTERMEDIATE", id="intermediate_only"),
        pytest.param("INTERMEDIATE", id="accs_intermediate_dual_access_alias"),
    ],
)
def test_accs_intermediate_buckets_use_pinned_catalogue(level):
    """ACCS-only, Intermediate-only, and dual-access storage buckets
    are pinned per shape so portfolio-specific catalogue drift is deliberate.
    """
    from bot import _allowed_forms_for_training_level, TRAINING_LEVEL_FORMS

    assert _allowed_forms_for_training_level(level) == list(
        TRAINING_LEVEL_FORMS[level]
    )


def test_accs_intermediate_catalogues_are_not_st3_aliases():
    from bot import TRAINING_LEVEL_FORMS

    assert TRAINING_LEVEL_FORMS["ACCS"] is not TRAINING_LEVEL_FORMS["ST3"]
    assert TRAINING_LEVEL_FORMS["INTERMEDIATE"] is not TRAINING_LEVEL_FORMS["ST3"]
    assert TRAINING_LEVEL_FORMS["ACCS"] is not TRAINING_LEVEL_FORMS["INTERMEDIATE"]
    assert "QIAT" in TRAINING_LEVEL_FORMS["INTERMEDIATE"]


def test_sas_uses_purpose_built_non_trainee_catalogue():
    """SAS / non-training uses its own catalogue rather than the HST/ST5 list."""
    from bot import (
        TRAINING_LEVEL_FORMS,
        _allowed_forms_for_training_level,
    )

    assert "SAS" in TRAINING_LEVEL_FORMS
    sas_allowed = _allowed_forms_for_training_level("SAS")
    assert sas_allowed == list(TRAINING_LEVEL_FORMS["SAS"])
    assert sas_allowed != list(TRAINING_LEVEL_FORMS["ST5"])


def test_sas_catalogue_is_base_family_and_resolves_to_2021_variants():
    """SAS / non-trainee draws the shared base form family; under its pinned
    2021 curriculum the base codes resolve to their 2021 variants.

    The raw catalogue carries no hand-pinned ``_2021`` entries — keeping the
    non-training catalogue identical in shape to the trainee catalogues (one
    shared family). The 2021 variants are produced by curriculum resolution,
    not by a duplicate pin in the profile list.
    """
    from bot import (
        _allowed_forms_for_training_level,
        _filter_forms_by_curriculum,
    )

    sas_allowed = set(_allowed_forms_for_training_level("SAS"))
    missing_core = SAS_CORE - sas_allowed
    assert not missing_core, (
        f"SAS catalogue must offer supported CESR evidence; missing: {missing_core}"
    )
    assert not any(ft.endswith("_2021") for ft in sas_allowed), (
        "Raw SAS catalogue must be base codes only; 2021 variants come from "
        "curriculum resolution, not pins."
    )

    resolved = set(_filter_forms_by_curriculum(sas_allowed, "2021"))
    missing_2021 = SAS_2021_FORMS - resolved
    assert not missing_2021, (
        f"SAS 2021 curriculum must resolve base codes to 2021 variants; "
        f"missing: {missing_2021}"
    )


def test_sas_curriculum_is_pinned_to_2021():
    """Non-training portfolios are pinned to the 2021 family regardless of any
    stored toggle — Kaizen only surfaces 2021 forms for them."""
    from bot import _default_curriculum_for_training_level

    assert _default_curriculum_for_training_level("SAS") == "2021"
    assert _default_curriculum_for_training_level("HIGHER") == "2025"
    assert _default_curriculum_for_training_level("ACCS") == "2025"
    assert _default_curriculum_for_training_level(None) == "2025"


@pytest.mark.parametrize(
    "level",
    [pytest.param(None, id="none"), pytest.param("", id="empty")],
)
def test_unknown_training_level_uses_fallback_union(level):
    """An unknown/empty training_level must not silently borrow the ST5 list.

    Same defensive contract as SAS — explicit unknown-default fallback so a
    Kaizen role-detection failure does not silently leak HST forms to a
    non-HST shape.
    """
    from bot import (
        TRAINING_LEVEL_FORMS,
        _allowed_forms_for_training_level,
        _default_allowed_forms_for_unknown_training,
    )

    allowed = _allowed_forms_for_training_level(level)
    assert allowed == _default_allowed_forms_for_unknown_training()
    assert allowed != list(TRAINING_LEVEL_FORMS["ST5"])


# ─── QI/audit projects must not become Teaching ───────────────────────────


def _recommendation(form_type: str, rationale: str = "fits"):
    from extractor import FORM_UUIDS
    from models import FormTypeRecommendation

    return FormTypeRecommendation(
        form_type=form_type,
        rationale=rationale,
        uuid=FORM_UUIDS.get(form_type),
    )


def test_intermediate_qi_audit_project_keeps_qiat_as_best_fit():
    """Exact regression: Intermediate/ST3 exposes QIAT, so do not fall through
    to Teaching or an Audit workaround.
    """
    from bot import (
        _allowed_forms_for_training_level,
        _build_form_choice_keyboard,
        _filter_recommendations_for_allowed_forms,
    )

    assert "time-to-antibiotics" in QI_AUDIT_SCREENSHOT_TEXT
    recommendations = [
        _recommendation("QIAT", "Baseline audit, intervention, and re-audit."),
        _recommendation("TEACH", "Teaching was part of the intervention."),
    ]

    filtered = _filter_recommendations_for_allowed_forms(
        recommendations,
        _allowed_forms_for_training_level("INTERMEDIATE"),
        QI_AUDIT_SCREENSHOT_TEXT,
    )

    assert filtered[0].form_type == "QIAT"
    assert filtered[0].uuid
    assert filtered[1].form_type == "TEACH"

    keyboard = _build_form_choice_keyboard(filtered)
    best_button = keyboard.inline_keyboard[0][0]
    assert best_button.callback_data == "FORM|best"
    assert "Use best fit: QIAT" in best_button.text
    assert "Teaching" not in best_button.text


def test_hst_qi_audit_project_keeps_qiat_as_best_fit():
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    recommendations = [
        _recommendation("QIAT", "Baseline audit, intervention, and re-audit."),
        _recommendation("TEACH", "Teaching was part of the intervention."),
    ]

    filtered = _filter_recommendations_for_allowed_forms(
        recommendations,
        _allowed_forms_for_training_level("ST5"),
        QI_AUDIT_SCREENSHOT_TEXT,
    )

    assert [rec.form_type for rec in filtered[:2]] == ["QIAT", "TEACH"]


def test_intermediate_qi_audit_project_with_teaching_only_recommendation_gets_qiat():
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    filtered = _filter_recommendations_for_allowed_forms(
        [_recommendation("TEACH", "Teaching was part of the intervention.")],
        _allowed_forms_for_training_level("INTERMEDIATE"),
        QI_AUDIT_SCREENSHOT_TEXT,
    )

    assert [rec.form_type for rec in filtered[:2]] == ["QIAT", "TEACH"]


def test_genuine_teaching_session_stays_teaching_for_intermediate():
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    recommendations = [
        _recommendation("TEACH", "Delivered a structured teaching session."),
        _recommendation("EDU_ACT", "Educational activity also fits."),
    ]

    filtered = _filter_recommendations_for_allowed_forms(
        recommendations,
        _allowed_forms_for_training_level("INTERMEDIATE"),
        "Delivered a structured teaching session for junior doctors on adult sepsis alerts.",
    )

    assert [rec.form_type for rec in filtered[:2]] == ["TEACH", "EDU_ACT"]


# ─── Profile-blocked recommender fallback ─────────────────────────────────
#
# Regression: a SAS / Non-Training profile sent a directly-observed procedural
# sedation case and the bot replied "Nothing left to recommend for this case
# — browse all types below." The SAS catalogue correctly excludes the
# trainee-only SLEs DOPS / ACAT / Mini-CEX (and PROC_LOG is not wired for
# SAS today), but the profile-agnostic LLM recommender still returns those
# forms for procedural cases. Filtering then empties the list with no
# fallback. The contract below pins the fallback so SAS users never hit the
# dead-end state, while leaving the trainee-only-SLE exclusion intact.

NON_TRAINING_OBSERVED_PROCEDURE_CASE = (
    "I was directly observed performing procedural sedation and closed "
    "reduction of a displaced ankle fracture in ED resus. Adult patient, "
    "anonymised. I assessed neurovascular status, confirmed indication for "
    "reduction, discussed risks/benefits, gained consent, prepared "
    "monitoring and airway/resus equipment, used ketamine sedation with "
    "senior supervision, performed reduction and plaster backslab, "
    "arranged post-reduction X-ray, documented capacity/consent/sedation "
    "observations, and safety-netted for compartment syndrome and "
    "neurovascular compromise.\n\nFeedback received: good preparation, "
    "clear consent, safe monitoring and structured post-procedure review. "
    "Learning point: verbalise sedation contingency plans earlier and "
    "delegate documentation roles before starting."
)


def test_sas_catalogue_includes_risr_advance_visible_assessments():
    """Sanaz's RISR Advance create-event PDF exposes these assessment forms."""
    from bot import _allowed_forms_for_training_level

    sas_allowed = set(_allowed_forms_for_training_level("SAS"))
    assert {"DOPS", "ACAT", "MINI_CEX", "PROC_LOG", "ESLE_ASSESS"} <= sas_allowed


def test_sas_procedural_case_keeps_dops_when_risr_profile_exposes_it():
    """A procedural-sedation case under SAS must yield ≥1 sensible
    recommendation rather than the "Nothing left to recommend" dead-end.

    Reproduces the dogfood mismatch: the LLM picked DOPS + PROC_LOG, which is
    appropriate for the case and visible in Sanaz's RISR Advance create-event
    PDF. The profile filter must therefore keep DOPS instead of substituting a
    reflective fallback.
    """
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    llm_picks = [
        _recommendation("DOPS", "Directly observed procedural skill."),
        _recommendation("PROC_LOG", "Procedure logged in ED."),
    ]
    filtered = _filter_recommendations_for_allowed_forms(
        llm_picks,
        _allowed_forms_for_training_level("SAS"),
        NON_TRAINING_OBSERVED_PROCEDURE_CASE,
    )

    form_types = [rec.form_type for rec in filtered]
    assert form_types, (
        "SAS procedural case must not produce an empty recommendation list "
        "— DOPS / Procedural Log are visible in this RISR profile."
    )
    assert form_types[0] == "DOPS"
    assert "PROC_LOG" in form_types
    assert filtered[0].uuid, "Recommendations must carry a real UUID."


def test_sas_observation_case_keeps_visible_assessment_forms():
    """Mini-CEX / ACAT are visible in Sanaz's RISR Advance form list."""
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    llm_picks = [
        _recommendation("MINI_CEX", "Bedside observation of trainee."),
        _recommendation("ACAT", "Busy resus session with multiple patients."),
    ]
    filtered = _filter_recommendations_for_allowed_forms(
        llm_picks,
        _allowed_forms_for_training_level("SAS"),
        "Consultant watched me manage a complex acute take.",
    )

    form_types = [rec.form_type for rec in filtered]
    assert form_types == ["MINI_CEX", "ACAT"]


def test_sas_reuse_flow_keeps_dops_even_when_reflect_log_already_filed():
    """Reuse-case flow must not substitute away from PDF-visible DOPS."""
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    filtered = _filter_recommendations_for_allowed_forms(
        [_recommendation("DOPS"), _recommendation("PROC_LOG")],
        _allowed_forms_for_training_level("SAS"),
        NON_TRAINING_OBSERVED_PROCEDURE_CASE,
        excluded_form="REFLECT_LOG",
    )

    assert [rec.form_type for rec in filtered] == ["DOPS", "PROC_LOG"]


def test_hst_passthrough_when_recommendations_are_allowed():
    """The fallback must NOT trigger for trainee profiles whose catalogue
    accepts the LLM's first picks. HST has DOPS and PROC_LOG; both pass
    through untouched.
    """
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    filtered = _filter_recommendations_for_allowed_forms(
        [_recommendation("DOPS"), _recommendation("PROC_LOG")],
        _allowed_forms_for_training_level("HIGHER"),
        NON_TRAINING_OBSERVED_PROCEDURE_CASE,
    )

    assert [rec.form_type for rec in filtered] == ["DOPS", "PROC_LOG"]


def test_empty_llm_recommendations_do_not_trigger_fallback():
    """If the LLM returned nothing at all (parse failure, timeout proxy),
    the filter must not synthesise a recommendation — we have no signal
    that any form would have fit and the upstream caller already shows
    its own AI-unavailable copy.
    """
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    filtered = _filter_recommendations_for_allowed_forms(
        [],
        _allowed_forms_for_training_level("SAS"),
        NON_TRAINING_OBSERVED_PROCEDURE_CASE,
    )

    assert filtered == []
