"""Offline coverage for the conservative vNext text extractor.

The extractor is the source-tied fact adapter wired into the private
vNext test bot. These tests pin its safety contract:

* Only emit facts whose value appears verbatim (or case-normalised from
  a verbatim token) in the source text — never infer, extrapolate, or
  call any external service.
* When a category has no match, omit that key entirely.
* Return an empty tuple for empty / whitespace-only input.
"""

import pytest

from vnext_text_extractor import extract_text_facts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _facts_dict(text: str) -> dict[str, str]:
    return dict(extract_text_facts(text))


# ---------------------------------------------------------------------------
# Shorthand age/sex (62M, 45 F)
# ---------------------------------------------------------------------------


def test_shorthand_age_sex_no_space():
    facts = _facts_dict("62M with chest pain")
    assert facts["age"] == "62"
    assert facts["sex"] == "M"


def test_shorthand_age_sex_lowercase():
    facts = _facts_dict("Saw a 45f in resus")
    assert facts["age"] == "45"
    assert facts["sex"] == "F"


def test_shorthand_age_sex_with_space():
    facts = _facts_dict("Patient is 70 M, fall at home")
    assert facts["age"] == "70"
    assert facts["sex"] == "M"


def test_shorthand_does_not_match_embedded_letter():
    # "15Male" — no word boundary after M; no year-old phrasing.
    # No demographics.
    facts = _facts_dict("Code 15Male tube available")
    assert "age" not in facts
    assert "sex" not in facts


# ---------------------------------------------------------------------------
# "Year old" phrasing
# ---------------------------------------------------------------------------


def test_year_old_phrase_with_sex_word():
    facts = _facts_dict("62-year-old man with crushing chest pain")
    assert facts["age"] == "62"
    assert facts["sex"] == "M"


def test_year_old_phrase_with_female_synonym():
    facts = _facts_dict("Reviewed a 78 year old lady with sepsis")
    assert facts["age"] == "78"
    assert facts["sex"] == "F"


def test_year_old_phrase_without_nearby_sex_keeps_age_only():
    facts = _facts_dict("82 year old presenting with dyspnoea")
    assert facts["age"] == "82"
    assert "sex" not in facts


def test_year_old_sex_word_too_far_away_is_dropped():
    text = (
        "82 year old presenting with dyspnoea, hypoxia, escalating "
        "oxygen requirement; eventually escalated to NIV. He has COPD."
    )
    facts = _facts_dict(text)
    assert facts["age"] == "82"
    assert "sex" not in facts


# ---------------------------------------------------------------------------
# Refusal / safety — truly empty inputs
# ---------------------------------------------------------------------------


def test_empty_text_returns_empty_tuple():
    assert extract_text_facts("") == ()


def test_whitespace_only_text_returns_empty_tuple():
    assert extract_text_facts("   \n\t ") == ()


def test_implausible_age_is_refused():
    facts = _facts_dict("Saw 250M presenting with cough")
    assert "age" not in facts


# ---------------------------------------------------------------------------
# Setting
# ---------------------------------------------------------------------------


def test_extracts_setting_ed():
    facts = _facts_dict("62M chest pain in ED")
    assert facts.get("setting") == "ED"


def test_extracts_setting_icu():
    facts = _facts_dict("patient transferred to ICU post-intubation")
    assert facts.get("setting") == "ICU"


def test_extracts_setting_resus():
    facts = _facts_dict("Managed in resus with full team")
    assert "resus" in facts.get("setting", "").lower()


def test_no_setting_when_absent():
    facts = _facts_dict("Had a chest pain case without location mentioned")
    assert "setting" not in facts


# ---------------------------------------------------------------------------
# Presenting complaint
# ---------------------------------------------------------------------------


def test_extracts_chest_pain():
    facts = _facts_dict("62M chest pain in ED, STEMI on ECG")
    assert "presenting_complaint" in facts
    assert "chest pain" in facts["presenting_complaint"].lower()


def test_extracts_dyspnoea():
    facts = _facts_dict("82 year old with dyspnoea and low sats")
    assert "presenting_complaint" in facts


def test_extracts_collapse():
    facts = _facts_dict("Reviewed 55M collapse in waiting room")
    assert "presenting_complaint" in facts


def test_no_complaint_when_absent():
    facts = _facts_dict("Consultant supervised intubation in ICU")
    assert "presenting_complaint" not in facts


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------


def test_extracts_stemi():
    facts = _facts_dict("ECG showed STEMI, activated cath lab")
    assert facts.get("diagnosis") == "STEMI"


def test_extracts_sepsis():
    facts = _facts_dict("78 year old lady with sepsis, started antibiotics")
    assert "sepsis" in facts.get("diagnosis", "").lower()


def test_extracts_af():
    facts = _facts_dict("62M in AF, rate controlled with diltiazem")
    assert facts.get("diagnosis") == "AF"


def test_no_diagnosis_when_absent():
    facts = _facts_dict("62M chest pain, under consultant supervision")
    assert "diagnosis" not in facts


# ---------------------------------------------------------------------------
# Procedure / intervention
# ---------------------------------------------------------------------------


def test_extracts_rsi():
    facts = _facts_dict("Performed RSI with consultant supervision")
    assert facts.get("procedure") == "RSI"


def test_extracts_cath_lab():
    facts = _facts_dict("cath lab activated for STEMI")
    assert "cath lab" in facts.get("procedure", "").lower()


def test_extracts_central_line():
    facts = _facts_dict("Inserted central line in resus")
    assert "central line" in facts.get("procedure", "").lower()


def test_no_procedure_when_absent():
    facts = _facts_dict("62M chest pain with STEMI, transferred urgently")
    assert "procedure" not in facts


# ---------------------------------------------------------------------------
# Supervision
# ---------------------------------------------------------------------------


def test_extracts_consultant_supervision():
    facts = _facts_dict("62M, consultant supervised RSI in ED")
    assert "supervision" in facts
    assert "consultant" in facts["supervision"].lower()


def test_extracts_independent_practice():
    facts = _facts_dict("Managed independently after gaining competency")
    assert "supervision" in facts
    assert "independently" in facts["supervision"].lower()


def test_extracts_registrar_supervision():
    facts = _facts_dict("Performed with registrar present in resus")
    assert "supervision" in facts
    assert "registrar" in facts["supervision"].lower()


def test_no_supervision_when_absent():
    facts = _facts_dict("62M STEMI, cath lab activated, ECG confirmed")
    assert "supervision" not in facts


# ---------------------------------------------------------------------------
# Learning point
# ---------------------------------------------------------------------------


def test_extracts_learning_point_learned_to():
    facts = _facts_dict("62M STEMI, learned to escalate early")
    assert "learning_point" in facts
    assert "learned" in facts["learning_point"].lower()


def test_extracts_learning_point_learned_that():
    facts = _facts_dict("I learned that early recognition of sepsis matters")
    assert "learning_point" in facts


def test_no_learning_point_when_absent():
    facts = _facts_dict("62M chest pain in ED, STEMI on ECG")
    assert "learning_point" not in facts


# ---------------------------------------------------------------------------
# Full acceptance-criteria case
# ---------------------------------------------------------------------------


def test_full_stemi_case_produces_rich_source_tied_facts():
    """Canonical acceptance-criteria case: 5+ distinct facts, all verbatim."""
    text = (
        "62M chest pain in ED, STEMI on ECG, cath lab activated, "
        "consultant supervised, learned to escalate early"
    )
    facts = extract_text_facts(text)
    keys = {k for k, _ in facts}

    # Demographics
    assert "age" in keys
    assert "sex" in keys
    # Clinical facts
    assert "setting" in keys or "presenting_complaint" in keys
    assert "diagnosis" in keys
    assert "learning_point" in keys

    # Must have ≥ 5 distinct facts to trigger DRAFT_READY
    assert len(facts) >= 5

    # Verbatim invariant — every value appears verbatim in the source
    for k, v in facts:
        if v in {"M", "F"}:
            assert v.lower() in text.lower(), f"{k}: sex value {v!r} not in source"
        else:
            assert v in text, f"{k}: value {v!r} not found verbatim in source"


# ---------------------------------------------------------------------------
# Router-gate safety: side questions and save commands
# ---------------------------------------------------------------------------


def test_portfolio_question_extractor_is_gated_at_adapter_level():
    # The extractor is pure — the adapter decides whether to call it on
    # side questions. When called directly, it still only emits verbatim literals.
    text = "What forms support a 62M chest pain case?"
    facts = extract_text_facts(text)
    # Any returned values must be verbatim
    for k, v in facts:
        if v not in {"M", "F"}:
            assert v in text, f"{k}: value {v!r} not verbatim in {text!r}"


def test_save_command_yields_no_extractable_facts():
    # "file this to Kaizen" has no demographics, diagnosis, procedure, etc.
    assert extract_text_facts("file this to Kaizen") == ()
    assert extract_text_facts("save this as a draft please") == ()


# ---------------------------------------------------------------------------
# Verbatim invariant across a variety of clinical texts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "62M chest pain in ED, STEMI on ECG, cath lab activated, consultant supervised, learned to escalate early",
        "78 year old lady with sepsis, RSI performed in resus, registrar supervised",
        "Had a difficult airway case in resus, managed RSI with the consultant, transferred to ICU after intubation.",
        "55M collapse in ED, AF on monitor, DC cardioversion performed",
        "82 year old COPD exacerbation, admitted to HDU, independently reviewed",
    ],
)
def test_extracted_values_appear_verbatim_in_source(text):
    facts = extract_text_facts(text)
    for k, v in facts:
        if v in {"M", "F"}:
            assert v.lower() in text.lower(), f"{k}: sex {v!r} not in source"
        else:
            assert v in text, f"{k}: value {v!r} not found verbatim in {text!r}"
