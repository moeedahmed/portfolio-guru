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
