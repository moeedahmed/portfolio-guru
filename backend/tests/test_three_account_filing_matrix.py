"""Offline pins for the three-account basic-filing validation matrix.

Plan: ``docs/roadmap/three-account-filing-validation-2026-06.md``.

The three accounts our trusted-tester pool covers exercise three different
portfolio shapes. Each shape branches the same internal code path differently,
and a silent regression on Harris's or Sana's shape is exactly the class of
bug the recent Portfolio Health sprints risk introducing.

This file is **offline only**: no Kaizen, no credentials, no BWS, no
Playwright, no Telegram. It pins the per-shape contract on the pure helpers
that any live filing run ultimately hits, so a regression shows up in CI
before a live smoke ever runs.

Shapes covered:

1. Moeed     — HIGHER / HST (CCT pathway, ST4–ST6).
2. Harris    — DREAM Pathway junior with ACCS **and** Intermediate Portfolio
               access. Profile bucket lives in ``training_level`` as either
               ``ACCS`` or ``INTERMEDIATE`` depending on which Kaizen role
               was last detected.
3. Sana      — SAS doctor planning CESR / Portfolio Pathway. No HST stage,
               no annual ARCP cadence, Kaizen stage select cannot match.

Known live-impact gaps (pinned here so a future silent change is visible):

- ``training_level == "SAS"`` returns an empty stage string from
  ``_stage_value_from_training_level``. Live consequence: Kaizen's
  Stage-of-training dropdown is left blank. This is the right behaviour
  today (we refuse to invent a training year for an SAS doctor) but it
  must not flip to a default of "Higher" silently.
- ``TRAINING_LEVEL_FORMS["SAS"]`` is explicit and smaller than the HST/ST5
  catalogue. Sana must not inherit trainee-only SLEs such as DOPS, ACAT, or
  Mini-CEX, while still seeing the supported CESR/non-trainee evidence forms
  verified from her Kaizen create-list.
- The Kaizen-role ``accs_intermediate`` (Harris) maps to the
  ``INTERMEDIATE`` storage bucket. That is Harris's dual-access alias, not a
  claim that ACCS and Intermediate are normally one portfolio type. Local
  catalogues for ACCS and Intermediate must stay distinct objects, with
  Kaizen-visible unsupported forms recorded rather than exposed as buttons.
"""

from __future__ import annotations

import pytest


# ─── Shape fixtures ──────────────────────────────────────────────────────


MOEED_LEVELS = ("HIGHER", "ST4", "ST5", "ST6")
HARRIS_LEVELS = ("ACCS", "INTERMEDIATE")
SANA_LEVEL = "SAS"

# Standard WPBAs whose stage select carries the four grouped bands
# (``Intermediate/ST3``, ``Higher/ST4-ST6``, ``PEM Sub-specialty``,
# ``ACCS ST1-ST2/CT1-CT2``). Forms like ACAT and ESLE have no stage field at
# all and are intentionally excluded.
GROUPED_BAND_WPBAS = ("CBD", "DOPS", "MINI_CEX", "LAT")


# ─── Stage defaulter on grouped-band WPBAs ───────────────────────────────


@pytest.mark.parametrize("level", MOEED_LEVELS)
def test_moeed_hst_stage_resolves_to_higher_on_grouped_band_wpbas(level):
    from bot import _stage_value_from_training_level

    for form_type in GROUPED_BAND_WPBAS:
        assert (
            _stage_value_from_training_level(level, form_type)
            == "Higher/ST4-ST6"
        ), f"{level} on {form_type} must default to Higher/ST4-ST6"


def test_harris_accs_stage_resolves_to_accs_band_on_grouped_band_wpbas():
    from bot import _stage_value_from_training_level

    for form_type in GROUPED_BAND_WPBAS:
        assert (
            _stage_value_from_training_level("ACCS", form_type)
            == "ACCS ST1-ST2/CT1-CT2"
        )


def test_harris_intermediate_stage_resolves_to_intermediate_on_grouped_band_wpbas():
    from bot import _stage_value_from_training_level

    for form_type in GROUPED_BAND_WPBAS:
        assert (
            _stage_value_from_training_level("INTERMEDIATE", form_type)
            == "Intermediate/ST3"
        )


def test_sana_sas_stage_is_blank_on_grouped_band_wpbas():
    """SAS must not receive a fabricated training-year default.

    Live consequence: Kaizen's stage dropdown stays blank for Sana, which is
    the right behaviour — she is not in a training year — and the doctor
    chooses how to handle it. If this flips to ``Higher/ST4-ST6`` silently,
    Portfolio Guru is inventing a training stage for a non-training doctor.
    """
    from bot import _stage_value_from_training_level

    for form_type in GROUPED_BAND_WPBAS:
        assert _stage_value_from_training_level("SAS", form_type) == ""


# ─── Stage defaulter on QIAT (individual-year select) ────────────────────
#
# QIAT's stage dropdown lists individual years rather than the grouped bands.
# It is also the only WPBA schema today that exposes a
# ``Portfolio pathway (CESR)`` option — the natural place to map an SAS / CESR
# user — but the current defaulter does not use it. These tests pin the
# observable behaviour and the gap.


def test_moeed_higher_on_qiat_has_no_default_until_an_exact_year_is_chosen():
    """HIGHER without an explicit year cannot resolve on QIAT.

    The defaulter intentionally refuses to invent an exact training year
    (e.g. ST5) for a profile bucket that only knows "HST". The user picks
    the year. ST4/ST5/ST6 themselves DO resolve, as the test below confirms.
    """
    from bot import _stage_value_from_training_level

    assert _stage_value_from_training_level("HIGHER", "QIAT") == ""


@pytest.mark.parametrize(
    "level,expected",
    [("ST4", "ST4"), ("ST5", "ST5"), ("ST6", "ST6")],
)
def test_moeed_exact_year_resolves_on_qiat(level, expected):
    from bot import _stage_value_from_training_level

    assert _stage_value_from_training_level(level, "QIAT") == expected


def test_harris_accs_and_intermediate_resolve_to_year_buckets_on_qiat():
    from bot import _stage_value_from_training_level

    assert _stage_value_from_training_level("ACCS", "QIAT") == "ST1/CT1"
    assert _stage_value_from_training_level("INTERMEDIATE", "QIAT") == "ST3/CT3"


def test_sana_sas_does_not_pick_up_qiat_cesr_option_today():
    """Known gap pinned: QIAT exposes ``Portfolio pathway (CESR)`` and
    ``Non-training`` options that would be the natural home for an SAS /
    CESR doctor, but ``_stage_value_from_training_level`` does not map
    ``"SAS"`` to either. Today Sana sees a blank QIAT stage select.

    This pin protects against two opposite regressions:

    - A silent mapping ``"SAS" -> "Higher/ST4-ST6"`` (fabricating a stage).
    - A future fix that adds ``"SAS" -> "Portfolio pathway (CESR)"`` without
      surfacing it in the draft preview copy.

    When the gap is addressed, update this test alongside the user-visible
    copy change so the behaviour change is intentional.
    """
    from bot import _stage_value_from_training_level

    assert _stage_value_from_training_level("SAS", "QIAT") == ""


def test_unknown_level_returns_blank_stage():
    from bot import _stage_value_from_training_level

    assert _stage_value_from_training_level(None, "CBD") == ""
    assert _stage_value_from_training_level("", "CBD") == ""


# ─── Filer's Angular stage UUIDs match the defaulter ─────────────────────


def test_kaizen_filer_recognises_each_band_returned_by_defaulter():
    """The stage string the defaulter returns must be resolvable by the
    Playwright filer. Otherwise Kaizen's stage dropdown gets typed but no
    option matches and the field is silently left blank.
    """
    from kaizen_form_filer import STAGE_SELECT_VALUES

    # The bands the defaulter actually returns for our three shapes.
    bands_to_filer_key = {
        "Higher/ST4-ST6": "Higher",
        "Intermediate/ST3": "Intermediate",
        "ACCS ST1-ST2/CT1-CT2": "ACCS",
    }
    for _, filer_key in bands_to_filer_key.items():
        assert filer_key in STAGE_SELECT_VALUES, (
            f"STAGE_SELECT_VALUES is missing {filer_key!r}; Harris/Moeed "
            f"drafts will land on Kaizen with the stage select blank."
        )


def test_sas_has_no_stage_uuid_and_that_is_intentional():
    """SAS is deliberately absent from STAGE_SELECT_VALUES.

    If a future commit adds ``"SAS"`` here it must come paired with explicit
    user-visible copy in the draft preview, otherwise Sana will see a
    fabricated training band auto-selected without consent.
    """
    from kaizen_form_filer import STAGE_SELECT_VALUES

    assert "SAS" not in STAGE_SELECT_VALUES


# ─── Form catalogue per shape ────────────────────────────────────────────


CORE_WPBAS = {"CBD", "DOPS", "MINI_CEX"}


def test_moeed_hst_catalogue_is_the_st6_superset():
    from bot import TRAINING_LEVEL_FORMS

    assert TRAINING_LEVEL_FORMS["HIGHER"] is TRAINING_LEVEL_FORMS["ST6"]
    assert CORE_WPBAS.issubset(set(TRAINING_LEVEL_FORMS["HIGHER"]))
    # HST shape must still offer LAT/QIAT/management forms that
    # don't appear on the junior catalogue.
    assert "LAT" in TRAINING_LEVEL_FORMS["HIGHER"]
    assert "QIAT" in TRAINING_LEVEL_FORMS["HIGHER"]
    assert any(
        f.startswith("MGMT_") for f in TRAINING_LEVEL_FORMS["HIGHER"]
    ), "HST catalogue must include the management section"


@pytest.mark.parametrize("level", HARRIS_LEVELS)
def test_harris_junior_catalogue_contains_core_wpbas(level):
    from bot import TRAINING_LEVEL_FORMS

    forms = set(TRAINING_LEVEL_FORMS[level])
    assert CORE_WPBAS.issubset(forms), (
        f"{level} catalogue must still offer CBD/DOPS/MINI_CEX — "
        f"missing: {CORE_WPBAS - forms}"
    )
    assert "QIAT" in forms, f"{level} catalogue must offer QIAT for junior QI/audit work"


def test_harris_accs_and_intermediate_catalogues_do_not_alias_st3():
    """ACCS and INTERMEDIATE are separate product shapes, not ST3 aliases."""
    from bot import TRAINING_LEVEL_FORMS

    assert TRAINING_LEVEL_FORMS["ACCS"] is not TRAINING_LEVEL_FORMS["ST3"]
    assert TRAINING_LEVEL_FORMS["INTERMEDIATE"] is not TRAINING_LEVEL_FORMS["ST3"]
    assert TRAINING_LEVEL_FORMS["ACCS"] is not TRAINING_LEVEL_FORMS["INTERMEDIATE"]


def test_accs_specific_visible_forms_are_recorded_but_not_clickable():
    from bot import FORM_CATEGORIES, KAIZEN_CATALOGUE_STATUS, TRAINING_LEVEL_FORMS

    accs_pending = {
        "ASAT",
        "EPA1",
        "EPA2",
        "DOPS_ACCS",
        "PROCEDURAL_LOG_ACCS",
        "ACCS_PROGRESS",
        "MCR_MTR_ACCS",
        "HALO_ICM",
        "HALO_PROCEDURAL_SEDATION",
        "IAC",
    }
    clickable = (
        set(TRAINING_LEVEL_FORMS["ACCS"])
        | {form for forms in FORM_CATEGORIES.values() for form in forms}
    )

    assert accs_pending <= set(KAIZEN_CATALOGUE_STATUS)
    assert {
        form for form in accs_pending
        if KAIZEN_CATALOGUE_STATUS[form]["status"] != "unsupported-pending-schema"
    } == set()
    assert accs_pending.isdisjoint(clickable)


def test_intermediate_progression_is_recorded_but_not_clickable():
    from bot import FORM_CATEGORIES, KAIZEN_CATALOGUE_STATUS, TRAINING_LEVEL_FORMS

    clickable = (
        set(TRAINING_LEVEL_FORMS["INTERMEDIATE"])
        | {form for forms in FORM_CATEGORIES.values() for form in forms}
    )

    assert KAIZEN_CATALOGUE_STATUS["INTERMEDIATE_PROGRESS"]["status"] == "unsupported-pending-schema"
    assert "INTERMEDIATE_PROGRESS" not in clickable


def test_sana_sas_catalogue_is_explicit_and_non_trainee():
    """Sana's RISR Advance profile exposes observed-assessment forms.

    The non-training catalogue is the shared base form family (no hand-pinned
    2021 entries); under its pinned 2021 curriculum those base codes resolve to
    the 2021 variants Kaizen actually shows her.
    """
    from bot import (
        TRAINING_LEVEL_FORMS,
        _filter_forms_by_curriculum,
        _default_curriculum_for_training_level,
    )

    assert "SAS" in TRAINING_LEVEL_FORMS

    forms = set(TRAINING_LEVEL_FORMS["SAS"])
    cesr_core = {
        "CBD", "DOPS", "MINI_CEX", "ACAT", "ACAF", "MSF", "LAT", "QIAT",
        "JCF", "PROC_LOG", "AUDIT", "REFLECT_LOG", "SDL", "EDU_ACT",
        "FORMAL_COURSE", "TEACH", "STAT", "TEACH_OBS", "TEACH_CONFID",
        "COMPLAINT", "SERIOUS_INC", "APPRAISAL", "CLIN_GOV", "CRIT_INCIDENT",
        "US_CASE", "ESLE_ASSESS", "RESEARCH", "PDP", "EDU_MEETING",
        "EDU_MEETING_SUPP",
    }
    missing = cesr_core - forms
    assert not missing, (
        f"SAS catalogue must offer supported CESR/Sana evidence; "
        f"missing: {missing}"
    )
    assert not any(ft.endswith("_2021") for ft in forms), (
        "Raw SAS catalogue must be base codes only; 2021 variants come from "
        "curriculum resolution, not pins."
    )

    assert _default_curriculum_for_training_level("SAS") == "2021"
    sana_2021 = {"JCF_2021", "LAT_2021", "QIAT_2021", "REFLECT_LOG_2021", "AUDIT_2021"}
    resolved = set(_filter_forms_by_curriculum(forms, "2021"))
    missing_2021 = sana_2021 - resolved
    assert not missing_2021, (
        f"SAS 2021 curriculum must resolve base codes to 2021 variants; "
        f"missing: {missing_2021}"
    )
    # One shared base family: SAS and the trainee profiles draw the same base
    # codes. The visible catalogue differs through curriculum resolution — SAS
    # surfaces the 2021 variants where the trainee ST5 surface stays on 2025.
    st5_visible = set(_filter_forms_by_curriculum(TRAINING_LEVEL_FORMS["ST5"], "2025"))
    assert resolved != st5_visible, (
        "SAS must surface a 2021 catalogue, not the trainee 2025 surface"
    )


# ─── Labels users see in the profile picker ──────────────────────────────


def test_profile_labels_distinguish_the_three_shapes():
    """The settings UI must show distinct labels for the supported shapes.

    If two of these collapse to the same label, users can't tell which bucket
    they're in. This is the cheapest UI-side guard against the
    dual-access alias leaking into product copy.
    """
    from bot import TRAINING_LEVEL_LABELS

    moeed_label = TRAINING_LEVEL_LABELS["HIGHER"]
    harris_accs_label = TRAINING_LEVEL_LABELS["ACCS"]
    harris_intermediate_label = TRAINING_LEVEL_LABELS["INTERMEDIATE"]
    sana_label = TRAINING_LEVEL_LABELS["SAS"]

    labels = {moeed_label, harris_accs_label, harris_intermediate_label, sana_label}
    assert len(labels) == 4, (
        f"Each portfolio shape must have a distinct label; got: {labels}"
    )
    # The non-training fixture label must read as non-training. Otherwise an SAS doctor sees
    # "HST Profile" or similar and loses trust in the recommender.
    assert (
        "SAS" in sana_label
        or "CESR" in sana_label
        or "Non-training" in sana_label
        or "Non-Training" in sana_label
    )
