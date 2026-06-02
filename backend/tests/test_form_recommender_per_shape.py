"""Offline pins for the per-shape recommender allowed-form fallback.

Plan: ``docs/roadmap/filing-reliability-readiness-sprint-2026-06.md`` § P1.c.

The recommender turns a clinical case into a set of allowed form ids based on
the saved ``training_level``. Three inline call sites in ``bot.py`` have
historically used ``TRAINING_LEVEL_FORMS.get(level, TRAINING_LEVEL_FORMS["ST5"])``
as the fallback, silently leaking the HST/ST5 superset to SAS / CESR shapes
and to unknown levels. This file pins the SAS-safe contract via a thin pure
helper, ``_allowed_forms_for_training_level``, so a regression on any of the
three trusted-tester account shapes (HST, ACCS-only, Intermediate-only,
Harris's ``accs_intermediate`` storage bucket, SAS) is loud.

Per the plan's portfolio-type terminology: ACCS and Intermediate are separate
Kaizen portfolio types. ``accs_intermediate`` is Harris's dual-access storage
alias (one trainee with access to both portfolios), not a standalone product
shape. The test ids keep ACCS-only and Intermediate-only distinct from the
dual-access alias, and the local catalogues must not silently alias ST3.

Offline-only: no Kaizen, no credentials, no BWS, no Playwright, no Telegram.
"""

from __future__ import annotations

import pytest


CORE_WPBAS = {"CBD", "DOPS", "MINI_CEX"}
SAS_BLOCKED_TRAINEE_SLES = {"DOPS", "ACAT", "MINI_CEX"}
SAS_CORE = {
    "CBD", "ACAF", "MSF", "LAT", "QIAT", "AUDIT", "REFLECT_LOG", "SDL",
    "EDU_ACT", "FORMAL_COURSE", "TEACH", "STAT", "TEACH_OBS",
    "TEACH_CONFID", "COMPLAINT", "SERIOUS_INC", "APPRAISAL", "CLIN_GOV",
    "CRIT_INCIDENT", "US_CASE", "RESEARCH", "PDP", "EDU_MEETING",
    "EDU_MEETING_SUPP",
}
SANA_2021_FORMS = {"JCF_2021", "LAT_2021", "QIAT_2021", "REFLECT_LOG_2021", "AUDIT_2021"}
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
    """ACCS-only, Intermediate-only, and Harris's dual-access storage bucket
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
    """SAS / CESR must not borrow the HST/ST5 trainee SLE catalogue."""
    from bot import (
        TRAINING_LEVEL_FORMS,
        _allowed_forms_for_training_level,
    )

    assert "SAS" in TRAINING_LEVEL_FORMS
    sas_allowed = _allowed_forms_for_training_level("SAS")
    assert sas_allowed == list(TRAINING_LEVEL_FORMS["SAS"])
    assert sas_allowed != list(TRAINING_LEVEL_FORMS["ST5"])
    assert SAS_BLOCKED_TRAINEE_SLES.isdisjoint(sas_allowed)


def test_sana_sas_catalogue_contains_supported_cesr_forms_and_2021_pins():
    """Sana is SAS/non-trainee on the 2021 curriculum."""
    from bot import _allowed_forms_for_training_level

    sas_allowed = set(_allowed_forms_for_training_level("SAS"))
    missing = (SAS_CORE | SANA_2021_FORMS) - sas_allowed
    assert not missing, (
        f"SAS catalogue must offer supported CESR/Sana evidence; missing: {missing}"
    )


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
