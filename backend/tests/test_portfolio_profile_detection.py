"""Portfolio profile detection: category × stage × evidence.

Pins the contract that the Kaizen dashboard probe returns a structured
profile (category + stage + evidence list) alongside the legacy
``portfolio_type`` string, so that:

* a *Non-Trainee Higher* portfolio surfaces as ``non_training_higher``
  rather than being silently collapsed into the generic ``sas`` bucket;
* a non-training shape whose stage cannot be read from the page renders
  as ``non_training_unknown`` (no silent mapping to HST/ACCS/Intermediate
  or to a specific non-training stage);
* trainee shapes (ACCS / Intermediate / HST) keep their existing strings
  so the form catalogue map and tests stay stable.

Boundary: pure-function tests against the rendered title + body text. No
live Kaizen, no CDP, no browser.
"""

from __future__ import annotations

import pytest

from engine.portfoliotypes.base import (
    detect_portfolio_profile,
    detect_portfolio_type,
)


# ─── trainee shapes keep their existing strings ─────────────────────────────


def test_higher_trainee_title_classifies_as_training_higher():
    profile = detect_portfolio_profile(
        "Higher Trainee Dashboard",
        "Welcome to your Higher Trainee portfolio. Create event…",
    )
    assert profile.category == "training"
    assert profile.stage == "higher"
    assert profile.portfolio_type == "hst"
    assert any("higher trainee" in e.lower() for e in profile.evidence)


def test_accs_trainee_classifies_as_training_accs():
    profile = detect_portfolio_profile(
        "ACCS Trainee Dashboard",
        "Anaesthesia Common Stem ACCS Trainee portfolio.",
    )
    assert profile.category == "training"
    assert profile.stage == "accs"
    assert profile.portfolio_type == "accs"


def test_intermediate_trainee_classifies_as_training_intermediate():
    profile = detect_portfolio_profile(
        "Intermediate Dashboard",
        "Intermediate trainee portfolio for emergency medicine.",
    )
    assert profile.category == "training"
    assert profile.stage == "intermediate"
    assert profile.portfolio_type == "intermediate"


def test_accs_plus_intermediate_combines_to_accs_intermediate():
    profile = detect_portfolio_profile(
        "ACCS Trainee Dashboard",
        "ACCS and Intermediate access on this account.",
    )
    assert profile.category == "training"
    assert profile.portfolio_type == "accs_intermediate"


def test_higher_trainee_supervisor_copy_does_not_become_assessor():
    """A trainee dashboard can mention the assigned Clinical Supervisor.

    That is not the same as the logged-in user being a Clinical
    Supervisor / assessor. Personal portfolio signals must win over this
    weak body-copy signal.
    """
    body = (
        "Welcome to your Higher Trainee portfolio. "
        "Your assigned Clinical Supervisor is listed below. "
        "Supervisor feedback and meeting forms are available."
    )
    profile = detect_portfolio_profile("Higher Trainee Dashboard", body)
    assert profile.category == "training"
    assert profile.stage == "higher"
    assert profile.portfolio_type == "hst"


# ─── non-training: known higher stage ───────────────────────────────────────


def test_non_trainee_higher_classifies_with_explicit_higher_stage():
    """A Kaizen Non-Trainee Higher account exposes both the non-training
    signal and the Higher stage signal — surface both, do not collapse
    to the generic ``sas`` bucket.
    """
    profile = detect_portfolio_profile(
        "Higher Trainee Dashboard",
        "Non-Trainee Higher portfolio. SAS doctor pathway.",
    )
    assert profile.category == "non_training"
    assert profile.stage == "higher"
    assert profile.portfolio_type == "non_training_higher"
    evidence_lower = " ".join(profile.evidence).lower()
    assert "non-trainee" in evidence_lower or "non-training" in evidence_lower
    assert "higher" in evidence_lower


def test_non_trainee_higher_without_title_still_detected_from_body():
    profile = detect_portfolio_profile(
        "Dashboard",
        "This is a Non-Trainee Higher portfolio for an SAS doctor.",
    )
    assert profile.category == "non_training"
    assert profile.stage == "higher"
    assert profile.portfolio_type == "non_training_higher"


def test_non_trainee_supervisor_copy_does_not_become_assessor():
    profile = detect_portfolio_profile(
        "Higher Trainee Dashboard",
        (
            "Non-Trainee Higher portfolio for an SAS doctor. "
            "Your Clinical Supervisor has not yet completed feedback."
        ),
    )
    assert profile.category == "non_training"
    assert profile.stage == "higher"
    assert profile.portfolio_type == "non_training_higher"


# ─── non-training: unknown stage ────────────────────────────────────────────


def test_cesr_without_stage_signal_classifies_as_non_training_unknown():
    """CESR / Portfolio Pathway accounts may not advertise a stage.
    The guardrail: surface non-training with stage ``unknown`` rather
    than silently choosing Higher.
    """
    profile = detect_portfolio_profile(
        "Dashboard",
        "CESR Portfolio Pathway candidate.",
    )
    assert profile.category == "non_training"
    assert profile.stage == "unknown"
    assert profile.portfolio_type == "non_training_unknown"


def test_specialty_doctor_without_stage_signal_is_non_training_unknown():
    profile = detect_portfolio_profile(
        "Dashboard",
        "Welcome, Specialty Doctor. Associate Specialist portfolio.",
    )
    assert profile.category == "non_training"
    assert profile.stage == "unknown"
    assert profile.portfolio_type == "non_training_unknown"


def test_non_training_intermediate_or_core_stages_stay_unknown():
    """We have no verified Kaizen surface for Non-Trainee Intermediate
    or Core today. The guardrail says: do not invent these categories;
    keep stage ``unknown`` until evidence is added.
    """
    profile = detect_portfolio_profile(
        "Dashboard",
        "Non-Trainee Intermediate portfolio.",
    )
    assert profile.category == "non_training"
    assert profile.stage == "unknown"
    assert profile.portfolio_type == "non_training_unknown"


# ─── assessor and unknown ───────────────────────────────────────────────────


def test_clinical_supervisor_classifies_as_assessor():
    profile = detect_portfolio_profile(
        "Clinical Supervisor — Dashboard",
        "You cannot create any events!",
    )
    assert profile.category == "assessor"
    assert profile.stage == "unknown"
    assert profile.portfolio_type == "assessor"


def test_body_only_assessor_signal_requires_no_personal_portfolio_signal():
    profile = detect_portfolio_profile(
        "Dashboard",
        "Clinical Supervisor dashboard. You cannot create any events!",
    )
    assert profile.category == "assessor"
    assert profile.stage == "unknown"
    assert profile.portfolio_type == "assessor"


def test_empty_inputs_classify_as_unknown():
    profile = detect_portfolio_profile("", "")
    assert profile.category == "unknown"
    assert profile.stage == "unknown"
    assert profile.portfolio_type == "unknown"


# ─── back-compat: detect_portfolio_type string surface ──────────────────────


def test_detect_portfolio_type_returns_existing_strings_for_trainee_shapes():
    """Legacy callers consume the string return. Trainee shapes must
    keep producing the same strings so the form-catalogue map and the
    setup/login bucket map keep working unchanged.
    """
    assert detect_portfolio_type("Higher Trainee Dashboard", "") == "hst"
    assert detect_portfolio_type("ACCS Trainee Dashboard", "accs trainee") == "accs"
    assert detect_portfolio_type("", "Intermediate portfolio") == "intermediate"


def test_detect_portfolio_type_surfaces_non_training_higher_string():
    """The string surface now distinguishes Non-Trainee Higher from the
    generic SAS / Non-Trainee bucket so downstream code (settings label,
    setup completion copy) can render the more specific label.
    """
    assert detect_portfolio_type("", "Non-Trainee Higher portfolio") == (
        "non_training_higher"
    )


def test_detect_portfolio_type_uses_non_training_unknown_when_no_stage_signal():
    """No stage signal → ``non_training_unknown`` — never silently
    Higher / Intermediate / ACCS.
    """
    assert detect_portfolio_type("", "CESR Portfolio Pathway candidate") == (
        "non_training_unknown"
    )
    assert detect_portfolio_type("", "Specialty Doctor portfolio") == (
        "non_training_unknown"
    )


def test_detect_portfolio_type_assessor_and_unknown_unchanged():
    assert detect_portfolio_type("Clinical Supervisor", "") == "assessor"
    assert detect_portfolio_type("", "") == "unknown"


# ─── body-preview length: SAS / CESR signals can live past byte 200 ────────


def test_non_training_signal_is_detected_when_it_lands_past_byte_200():
    """The 2026-06-02 SAS / CESR reproducer surfaced a real bug: the Kaizen
    provider used to truncate ``document.body.innerText`` to the first 200
    chars before classifying. Real Kaizen dashboards render the global nav,
    header, breadcrumbs, and announcement bar before any portfolio-type
    text — so the *Non-Trainee Higher* / *CESR* / *Specialty Doctor* marker
    routinely lands past byte 200 and the truncation silently classified
    those accounts as ``unknown`` (or, worse, as ``hst`` from the title).

    Pin two things:

    * the detection function itself reads the full body it is handed
      (regression guard against re-introducing a low ``[:200]`` slice in
      the detector); and
    * the provider's pinned preview length is at least 2 KB (regression
      guard against the call-site re-tightening the slice it hands to the
      detector). The 2 KB threshold is comfortably larger than every
      Kaizen chrome prefix observed in the offline fixtures and still
      well below the 3 KB we already extract from
      ``document.body.innerText``.
    """
    chrome_prefix = "Kaizen ePortfolio dashboard. " * 40  # ~1.2 KB of chrome
    assert len(chrome_prefix) > 1000, "fixture chrome must exceed the legacy 200-char window"
    body = chrome_prefix + "Welcome — this is a Non-Trainee Higher portfolio for an SAS doctor."

    profile = detect_portfolio_profile("Higher Trainee Dashboard", body)
    assert profile.category == "non_training"
    assert profile.stage == "higher"
    assert profile.portfolio_type == "non_training_higher"

    from engine.providers.kaizen import KAIZEN_DASHBOARD_BODY_PREVIEW_CHARS

    assert KAIZEN_DASHBOARD_BODY_PREVIEW_CHARS >= 2000, (
        f"Kaizen body preview must be wide enough for SAS/CESR signals; "
        f"got {KAIZEN_DASHBOARD_BODY_PREVIEW_CHARS}"
    )


# ─── privacy guardrail: no real names in detection module copy ──────────────


def test_detection_module_does_not_leak_real_names():
    """The detection module ships in the public repo and (indirectly)
    drives user-facing copy. Pin that no real beta-tester names appear in
    the module text — drift here is exactly the privacy regression the
    rule is meant to prevent.
    """
    from pathlib import Path

    module_path = Path(__file__).resolve().parents[1] / "engine" / "portfoliotypes" / "base.py"
    text = module_path.read_text().lower()
    for forbidden in ("sana", "haris", "harris", "moeed", "ahmed"):
        assert forbidden not in text, (
            f"Detection module must not embed real name {forbidden!r}"
        )
