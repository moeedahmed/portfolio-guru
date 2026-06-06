import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_obvious_qi_project_recommends_qiat_without_llm():
    from extractor import recommend_form_types

    with patch("extractor._generate", new=AsyncMock(side_effect=AssertionError("LLM called"))):
        recommendations = await recommend_form_types(
            "I completed a QI project on ED sepsis antibiotics. Baseline audit "
            "showed delays, I introduced a checklist and teaching intervention, "
            "then re-audited compliance after the change cycle."
        )

    assert [rec.form_type for rec in recommendations][:2] == ["QIAT", "TEACH"]
    assert recommendations[0].uuid


@pytest.mark.asyncio
async def test_obvious_course_recommends_formal_course_without_llm():
    from extractor import recommend_form_types

    with patch("extractor._generate", new=AsyncMock(side_effect=AssertionError("LLM called"))):
        recommendations = await recommend_form_types(
            "I attended and completed my ATLS course and received the course certificate."
        )

    assert [rec.form_type for rec in recommendations] == ["FORMAL_COURSE"]
    assert recommendations[0].uuid


@pytest.mark.asyncio
async def test_observed_procedure_recommends_dops_without_llm():
    from extractor import recommend_form_types

    with patch("extractor._generate", new=AsyncMock(side_effect=AssertionError("LLM called"))):
        recommendations = await recommend_form_types(
            "I performed procedural sedation and closed reduction in ED. "
            "The consultant directly observed me and gave feedback."
        )

    assert [rec.form_type for rec in recommendations][:2] == ["DOPS", "PROC_LOG"]
    assert all(rec.uuid for rec in recommendations)


@pytest.mark.asyncio
async def test_ambiguous_clinical_case_still_falls_back_to_ai():
    from extractor import recommend_form_types

    async def fake_generate(prompt, retries=1, tier=""):
        assert "Case description:" in prompt
        return json.dumps([
            {"form_type": "CBD", "rationale": "Clinical case management."}
        ])

    generate = AsyncMock(side_effect=fake_generate)
    with patch("extractor._generate", new=generate):
        recommendations = await recommend_form_types(
            "I saw a patient with chest pain in ED, discussed the ECG with cardiology, "
            "and reflected on escalation."
        )

    generate.assert_awaited_once()
    assert [rec.form_type for rec in recommendations] == ["CBD"]


@pytest.mark.asyncio
async def test_course_detection_does_not_treat_also_as_also_course():
    from extractor import recommend_form_types

    async def fake_generate(prompt, retries=1, tier=""):
        assert "Case description:" in prompt
        return json.dumps([
            {"form_type": "CBD", "rationale": "Clinical case management."}
        ])

    generate = AsyncMock(side_effect=fake_generate)
    with patch("extractor._generate", new=generate):
        recommendations = await recommend_form_types(
            "I saw an elderly patient after a fall. I also completed the discharge "
            "summary and reflected on safe safety-netting."
        )

    generate.assert_awaited_once()
    assert [rec.form_type for rec in recommendations] == ["CBD"]


@pytest.mark.asyncio
async def test_photo_input_keeps_existing_ai_grounding_path():
    from extractor import recommend_form_types

    async def fake_generate(prompt, retries=1, tier=""):
        assert "image-derived input guard" in prompt.lower()
        return json.dumps([
            {"form_type": "US_CASE", "rationale": "POCUS image source needs grounding."}
        ])

    generate = AsyncMock(side_effect=fake_generate)
    with patch("extractor._generate", new=generate):
        recommendations = await recommend_form_types(
            "POCUS FAST scan image with free fluid noted.",
            input_source="photo",
        )

    generate.assert_awaited_once()
    assert [rec.form_type for rec in recommendations] == ["US_CASE"]
