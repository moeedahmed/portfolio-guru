"""Tests for the deterministic vNext form-type recommender.

Covers:
- CBD recommendation for clinical case management cases (high/medium confidence)
- DOPS recommendation when a trainee skill has a supervisor present
- PROC_LOG recommendation for trainee skills without a confirmed observer
- US_CASE recommendation for POCUS/FAST scan procedures
- REFLECT_LOG recommendation for pure reflection/learning signals
- InsufficientFacts for missing setting, missing complaint/diagnosis, or no context
- Cath lab and other management-pathway procedures do not trigger PROC_LOG/DOPS
- Pure function contract: facts are not mutated by recommend()
"""

from __future__ import annotations

from conversational_case_engine import CaseFact, SourceType
from vnext_form_recommender import (
    FormRecommendation,
    InsufficientFacts,
    recommend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_facts(**kwargs: str) -> tuple[CaseFact, ...]:
    return tuple(
        CaseFact(key=k, value=v, source_type=SourceType.TEXT, source_turn_id="t1")
        for k, v in kwargs.items()
    )


# ---------------------------------------------------------------------------
# CBD recommendations
# ---------------------------------------------------------------------------


def test_stemi_ed_case_recommends_cbd_high_confidence():
    """Full STEMI clinical case -> CBD at high confidence."""
    facts = _make_facts(
        age="62",
        sex="M",
        setting="ED",
        presenting_complaint="chest pain",
        diagnosis="STEMI",
        procedure="cath lab",
        supervision="consultant",
        learning_point="learned to escalate early",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "CBD"
    assert result.confidence == "high"
    assert "ED" in result.reason
    assert "STEMI" in result.reason


def test_case_with_complaint_no_diagnosis_recommends_cbd_medium():
    """Setting + complaint but no diagnosis -> CBD at medium confidence."""
    facts = _make_facts(
        setting="ICU",
        presenting_complaint="dyspnoea",
        supervision="registrar",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "CBD"
    assert result.confidence == "medium"
    assert "dyspnoea" in result.reason


def test_case_with_resus_setting_and_diagnosis_recommends_cbd():
    facts = _make_facts(setting="resus", diagnosis="anaphylaxis")
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "CBD"
    assert result.confidence == "high"


def test_cath_lab_is_not_trainee_skill_cbd_takes_precedence():
    """Cath lab activation is a management pathway - CBD wins over PROC_LOG."""
    facts = _make_facts(
        setting="ED",
        diagnosis="STEMI",
        procedure="cath lab",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "CBD"


def test_thrombolysis_is_not_trainee_skill_cbd_takes_precedence():
    facts = _make_facts(
        setting="ED",
        diagnosis="PE",
        procedure="thrombolysis",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "CBD"


def test_surgical_review_is_not_trainee_skill():
    facts = _make_facts(
        setting="ED",
        diagnosis="appendicitis",
        procedure="surgical review",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "CBD"


# ---------------------------------------------------------------------------
# PROC_LOG recommendations
# ---------------------------------------------------------------------------


def test_rsi_without_supervision_recommends_proc_log():
    facts = _make_facts(
        age="45",
        setting="ED",
        diagnosis="anaphylaxis",
        procedure="RSI",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "PROC_LOG"
    assert result.confidence == "medium"


def test_chest_drain_no_supervision_recommends_proc_log():
    facts = _make_facts(setting="ED", procedure="chest drain")
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "PROC_LOG"


def test_lumbar_puncture_no_supervision_recommends_proc_log():
    facts = _make_facts(procedure="lumbar puncture", setting="ED")
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "PROC_LOG"


def test_nerve_block_no_supervisor_recommends_proc_log():
    facts = _make_facts(procedure="nerve block", setting="theatres")
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "PROC_LOG"


# ---------------------------------------------------------------------------
# DOPS recommendations
# ---------------------------------------------------------------------------


def test_rsi_with_consultant_recommends_dops_medium():
    facts = _make_facts(
        setting="resus",
        procedure="RSI",
        supervision="consultant",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "DOPS"
    assert result.confidence == "medium"
    assert "consultant" in result.reason


def test_chest_drain_with_registrar_recommends_dops():
    facts = _make_facts(
        setting="ED",
        procedure="chest drain",
        supervision="registrar",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "DOPS"


def test_central_line_with_consultant_recommends_dops():
    facts = _make_facts(procedure="central line", supervision="consultant")
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "DOPS"


def test_rsi_independently_recommends_proc_log_not_dops():
    """'independently' is not an observer word - should return PROC_LOG."""
    facts = _make_facts(procedure="RSI", supervision="independently")
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "PROC_LOG"


# ---------------------------------------------------------------------------
# US_CASE recommendations
# ---------------------------------------------------------------------------


def test_pocus_recommends_us_case_high():
    facts = _make_facts(
        setting="ED",
        procedure="POCUS",
        supervision="consultant",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "US_CASE"
    assert result.confidence == "high"


def test_fast_scan_recommends_us_case():
    facts = _make_facts(setting="resus", procedure="FAST scan")
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "US_CASE"


def test_us_case_takes_precedence_over_setting_diagnosis():
    """POCUS signal wins even when CBD signals are also present."""
    facts = _make_facts(
        setting="ED",
        diagnosis="haemorrhage",
        procedure="POCUS",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "US_CASE"


# ---------------------------------------------------------------------------
# REFLECT_LOG recommendations
# ---------------------------------------------------------------------------


def test_learning_point_only_recommends_reflect_log():
    """Pure reflection without clinical case context -> REFLECT_LOG."""
    facts = _make_facts(learning_point="learned to escalate early")
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "REFLECT_LOG"
    assert result.confidence == "medium"


def test_learning_point_with_clinical_context_does_not_force_reflect_log():
    """When there is also setting+diagnosis, clinical rules take priority."""
    facts = _make_facts(
        setting="ED",
        diagnosis="sepsis",
        learning_point="learned to escalate early",
    )
    result = recommend(facts)
    assert isinstance(result, FormRecommendation)
    assert result.form_type == "CBD"


# ---------------------------------------------------------------------------
# InsufficientFacts paths
# ---------------------------------------------------------------------------


def test_diagnosis_without_setting_returns_insufficient_with_setting_prompt():
    facts = _make_facts(age="62", diagnosis="STEMI")
    result = recommend(facts)
    assert isinstance(result, InsufficientFacts)
    assert "setting" in result.missing_prompt.lower()


def test_complaint_without_setting_returns_insufficient():
    facts = _make_facts(presenting_complaint="chest pain")
    result = recommend(facts)
    assert isinstance(result, InsufficientFacts)
    assert "setting" in result.missing_prompt.lower()


def test_setting_without_clinical_content_returns_insufficient():
    facts = _make_facts(age="62", sex="M", setting="ED")
    result = recommend(facts)
    assert isinstance(result, InsufficientFacts)
    assert "ED" in result.missing_prompt


def test_empty_facts_returns_insufficient():
    result = recommend(())
    assert isinstance(result, InsufficientFacts)


def test_demographics_only_returns_insufficient():
    facts = _make_facts(age="45", sex="F")
    result = recommend(facts)
    assert isinstance(result, InsufficientFacts)


# ---------------------------------------------------------------------------
# Pure function contract
# ---------------------------------------------------------------------------


def test_recommend_does_not_mutate_facts():
    """recommend() is a pure function - facts tuple is unchanged after call."""
    facts = _make_facts(setting="ED", diagnosis="STEMI")
    facts_before = tuple(facts)
    recommend(facts)
    assert facts == facts_before


def test_recommend_is_deterministic():
    """Same facts always produce the same result."""
    facts = _make_facts(setting="ED", diagnosis="STEMI")
    assert recommend(facts) == recommend(facts)
