"""Tests for the offline Portfolio Guru beta-vs-Hermes evaluator."""

from __future__ import annotations

import pytest

from eval_portfolio_guru_side_by_side import (
    EvidenceCase,
    FIXED_CASES,
    report_to_dict,
    run_evaluation,
)
from models import FormTypeRecommendation


def _rec(form_type: str, rationale: str = "stub") -> FormTypeRecommendation:
    return FormTypeRecommendation(form_type=form_type, rationale=rationale, uuid=None)


async def _stub_beta_recommender(text: str):
    lowered = text.lower()
    if "teaching session" in lowered:
        return [_rec("STAT", "Formal teaching session with observation/assessment stated explicitly.")]
    if "title: cbd chest pain" in lowered:
        return [_rec("CBD", "The title indicates a chest pain CBD.")]
    if "interesting chest pain" in lowered:
        return [_rec("CBD", "The trainee managed a chest pain case.")]
    if "registrar evening shift" in lowered:
        return [_rec("REFLECT_LOG", "Leadership reflection.")]
    if "wells score" in lowered:
        return [_rec("REFLECT_LOG", "Reflection on anchoring bias."), _rec("CBD", "PE case.")]
    return [_rec("CBD", "Clinical case discussion.")]


@pytest.mark.asyncio
async def test_fixed_cases_produce_advisory_not_replacement_verdict():
    report = await run_evaluation(beta_recommender=_stub_beta_recommender)

    assert report.verdict == "sit_beside_beta_as_advisory"
    assert report.advisory_ready is True
    assert report.replacement_ready is False
    assert all(
        item.hermes_score.replacement_readiness == 0 for item in report.cases
    )


@pytest.mark.asyncio
async def test_missing_evidence_penalises_beta_and_rewards_hermes_strictness():
    report = await run_evaluation(beta_recommender=_stub_beta_recommender)
    missing = next(item for item in report.cases if item.case.case_id == "missing_evidence")

    assert missing.beta.forms == ("CBD",)
    assert missing.beta_score.weak_missing_handling == 0
    assert missing.beta_score.arcp_readiness_judgement == 0
    assert missing.hermes.forms == ()
    assert missing.hermes_score.weak_missing_handling == 2
    assert missing.hermes_score.arcp_readiness_judgement == 2


@pytest.mark.asyncio
async def test_hermes_shadow_stays_clean_on_prompt_injection_case():
    report = await run_evaluation(beta_recommender=_stub_beta_recommender)
    safety = next(item for item in report.cases if item.case.case_id == "safety_trap")

    assert safety.case.safety_trap is True
    assert safety.hermes_score.clean_no_runtime_leakage == 2
    assert safety.beta_score.clean_no_runtime_leakage == 2
    assert "system prompt" not in safety.hermes.summary.lower()
    assert "hidden tools" not in safety.hermes.summary.lower()


@pytest.mark.asyncio
async def test_teaching_case_penalises_hallucinated_formal_observation():
    report = await run_evaluation(beta_recommender=_stub_beta_recommender)
    teaching = next(
        item for item in report.cases if item.case.case_id == "misfiled_teaching"
    )

    assert teaching.beta.forms == ("STAT",)
    assert teaching.beta_score.filing_accuracy == 2
    assert teaching.beta_score.no_hallucinated_claims == 0


@pytest.mark.asyncio
async def test_report_serialises_without_raw_case_text():
    report = await run_evaluation(beta_recommender=_stub_beta_recommender)
    payload = report_to_dict(report)
    blob = str(payload)

    assert payload["verdict"] == "sit_beside_beta_as_advisory"
    assert "55-year-old male" not in blob
    assert "Ignore previous instructions" not in blob


def test_fixed_case_contract_contains_the_agreed_six_shapes():
    case_ids = {case.case_id for case in FIXED_CASES}

    assert {
        "strong_cbd",
        "weak_evidence",
        "missing_evidence",
        "misfiled_teaching",
        "borderline_arcp",
        "safety_trap",
    } <= case_ids


@pytest.mark.asyncio
async def test_empty_case_list_is_not_ready():
    report = await run_evaluation(
        cases=(),
        beta_recommender=_stub_beta_recommender,
    )

    assert report.verdict == "not_ready"
    assert report.advisory_ready is False
    assert report.replacement_ready is False
