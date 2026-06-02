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
dual-access alias even though all three currently share the ST3 catalogue, so
a future per-portfolio split is intentional, not silent.

Offline-only: no Kaizen, no credentials, no BWS, no Playwright, no Telegram.
"""

from __future__ import annotations

import pytest


CORE_WPBAS = {"CBD", "DOPS", "MINI_CEX"}
CESR_CORE = CORE_WPBAS | {"REFLECT_LOG"}
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
    all surface the current ST3 catalogue today. They are pinned per shape so
    a future per-portfolio split is deliberate.
    """
    from bot import _allowed_forms_for_training_level, TRAINING_LEVEL_FORMS

    assert _allowed_forms_for_training_level(level) == list(
        TRAINING_LEVEL_FORMS[level]
    )


def test_sas_does_not_leak_the_st5_superset():
    """SAS must go through the unknown-default fallback, not ``ST5``.

    The inline recommender call sites historically used
    ``TRAINING_LEVEL_FORMS.get(level, TRAINING_LEVEL_FORMS["ST5"])`` which
    silently maps SAS → ST5 (an HST-only superset). This pin makes the
    SAS-safe fallback the documented contract.
    """
    from bot import (
        TRAINING_LEVEL_FORMS,
        _allowed_forms_for_training_level,
        _default_allowed_forms_for_unknown_training,
    )

    assert "SAS" not in TRAINING_LEVEL_FORMS, (
        "If SAS gets its own catalogue, update this pin alongside the "
        "user-visible copy in the draft preview."
    )
    sas_allowed = _allowed_forms_for_training_level("SAS")
    assert sas_allowed == _default_allowed_forms_for_unknown_training()
    assert sas_allowed != list(TRAINING_LEVEL_FORMS["ST5"])


def test_sas_fallback_includes_cesr_core_wpbas():
    """SAS / CESR depends on CBD/DOPS/MINI_CEX/REFLECT_LOG being offered."""
    from bot import _allowed_forms_for_training_level

    sas_allowed = set(_allowed_forms_for_training_level("SAS"))
    missing = CESR_CORE - sas_allowed
    assert not missing, (
        f"SAS fallback catalogue must offer CESR core evidence; missing: {missing}"
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


# ─── QIAT fallback must not become Teaching ───────────────────────────────


def _recommendation(form_type: str, rationale: str = "fits"):
    from extractor import FORM_UUIDS
    from models import FormTypeRecommendation

    return FormTypeRecommendation(
        form_type=form_type,
        rationale=rationale,
        uuid=FORM_UUIDS.get(form_type),
    )


def test_intermediate_qi_audit_project_falls_back_to_audit_not_teaching():
    """Exact regression: QIAT is unavailable for Intermediate/ST3, but the
    remaining Teaching recommendation is only an intervention detail.
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

    assert filtered[0].form_type == "AUDIT"
    assert filtered[0].uuid
    assert filtered[1].form_type == "TEACH"

    keyboard = _build_form_choice_keyboard(filtered)
    best_button = keyboard.inline_keyboard[0][0]
    assert best_button.callback_data == "FORM|best"
    assert "Use best fit: Audit" in best_button.text
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


def test_intermediate_qi_audit_project_with_teaching_only_recommendation_gets_audit():
    from bot import (
        _allowed_forms_for_training_level,
        _filter_recommendations_for_allowed_forms,
    )

    filtered = _filter_recommendations_for_allowed_forms(
        [_recommendation("TEACH", "Teaching was part of the intervention.")],
        _allowed_forms_for_training_level("INTERMEDIATE"),
        QI_AUDIT_SCREENSHOT_TEXT,
    )

    assert [rec.form_type for rec in filtered[:2]] == ["AUDIT", "TEACH"]


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
