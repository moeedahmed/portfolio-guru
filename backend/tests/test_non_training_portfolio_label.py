"""Bot-layer wiring for the Non-training portfolio detection slice.

Three contracts pinned here:

* the detected-role → ``training_level`` bucket map keeps non-training
  Higher and non-training Unknown on the purpose-built SAS catalogue
  (not silently re-routed to HST / ACCS / Intermediate);
* the canonical role normalisation in :mod:`supervisor_workflow` treats
  the new ``non_training_*`` strings as ``trainee`` for cache / queue
  gating (they own a personal portfolio, unlike a Clinical Supervisor);
* the settings label helper surfaces *Non-Training Profile (Higher
  level)* when the detected role carries an explicit stage signal, and
  *Non-Training Profile (level unknown)* when it does not — never a
  guessed stage.

Boundary: offline pure-function tests. No live Kaizen, no CDP, no
Telegram, no DB.
"""

from __future__ import annotations

import pytest


# ─── detected-role → training_level bucket map ──────────────────────────────


@pytest.mark.parametrize(
    "detected_role",
    ["non_training_higher", "non_training_unknown"],
)
def test_non_training_variants_map_to_sas_bucket(detected_role):
    """Both non-training variants share the purpose-built SAS catalogue
    so the form picker keeps offering supported CESR / Non-trainee
    evidence forms — never the trainee SLE superset."""
    from bot import detected_role_to_training_level

    assert detected_role_to_training_level(detected_role) == "SAS"


def test_non_training_variants_do_not_map_to_hst_accs_or_intermediate():
    """Guardrail: a non-training detection must never silently land in
    HST / ACCS / Intermediate. A regression here would route a non-
    training doctor to trainee-only forms.
    """
    from bot import detected_role_to_training_level

    for detected_role in ("non_training_higher", "non_training_unknown"):
        bucket = detected_role_to_training_level(detected_role)
        assert bucket not in {"HIGHER", "ACCS", "INTERMEDIATE"}, (
            f"{detected_role!r} silently mapped to trainee bucket {bucket!r}"
        )


# ─── canonical role normalisation for cache / queue gating ──────────────────


@pytest.mark.parametrize(
    "detected_role",
    ["non_training_higher", "non_training_unknown"],
)
def test_non_training_variants_canonicalise_as_trainee(detected_role):
    """Cache + supervisor queue gating runs on the canonical
    ``assessor`` / ``trainee`` / ``unknown`` vocabulary. Non-training
    shapes own a personal portfolio so they belong in the trainee bucket
    there — only assessor accounts (no personal portfolio) should fall
    out of the trainee surface.
    """
    from supervisor_workflow import normalize_role

    assert normalize_role(detected_role) == "trainee"


# ─── settings label helper ──────────────────────────────────────────────────


def test_settings_label_for_non_training_higher_surfaces_stage():
    """When the auto-detect path stored ``non_training_higher`` for a
    user, the settings screen must render the explicit *Higher level*
    suffix rather than the generic Non-Training Profile string. This
    is the user-visible diff that proves the new detection is wired
    through to the dashboard.
    """
    from bot import _portfolio_settings_label

    assert _portfolio_settings_label("SAS", "non_training_higher") == (
        "Non-Training Profile (Higher level)"
    )


def test_settings_label_for_non_training_unknown_surfaces_uncertainty():
    """No verified stage → render *(level unknown)*. Pin that we never
    silently swap that out for *(Higher level)* or fall through to the
    bucket label, which would hide the uncertainty.
    """
    from bot import _portfolio_settings_label

    assert _portfolio_settings_label("SAS", "non_training_unknown") == (
        "Non-Training Profile (level unknown)"
    )


@pytest.mark.parametrize(
    "raw_role,training_level,expected",
    [
        ("hst", "HIGHER", "HST Profile"),
        ("accs", "ACCS", "ACCS Profile"),
        ("intermediate", "INTERMEDIATE", "Intermediate Profile"),
        ("sas", "SAS", "Non-Training Profile"),
        (None, "HIGHER", "HST Profile"),
        (None, None, "Unknown"),
    ],
)
def test_settings_label_for_other_shapes_uses_bucket_label(
    raw_role, training_level, expected
):
    """The helper only specialises non-training Higher / Unknown. Every
    other shape — including trainee roles, legacy ``sas``, and the
    no-role default — keeps the bucket-derived label so existing
    screens do not change for those users.
    """
    from bot import _portfolio_settings_label

    assert _portfolio_settings_label(training_level, raw_role) == expected


# ─── privacy guardrail ──────────────────────────────────────────────────────


def test_user_facing_non_training_labels_are_generic():
    """User-facing strings rendered in /settings and the setup completion
    flow must not embed real beta-tester names. Pin that the granular
    label strings stay generic — drift here would land a real name in
    bot output.
    """
    from bot import _KAIZEN_ROLE_GRANULAR_LABELS

    for label in _KAIZEN_ROLE_GRANULAR_LABELS.values():
        lower = label.lower()
        for forbidden in ("sana", "haris", "harris", "moeed", "ahmed"):
            assert forbidden not in lower, (
                f"User-facing label {label!r} embeds real name {forbidden!r}"
            )


def test_training_level_labels_are_generic():
    """The bucket label map is the canonical user-facing copy for every
    /settings render and every setup-completion message. Pin that it
    never carries a real beta-tester name — the strings here are the
    most-quoted bot output we ship.
    """
    from bot import TRAINING_LEVEL_LABELS

    for level, label in TRAINING_LEVEL_LABELS.items():
        lower = label.lower()
        for forbidden in ("sana", "haris", "harris", "moeed", "ahmed"):
            assert forbidden not in lower, (
                f"TRAINING_LEVEL_LABELS[{level!r}]={label!r} embeds real name "
                f"{forbidden!r}"
            )
