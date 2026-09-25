"""Form suggestion: the model decides the form, with two named-request exceptions.

Contract set by Moeed's decision on 18 September 2026. The clinical keyword
pre-pass used to pick a form from a single word anywhere in the text ("pocus",
"chest drain", "life support") while the model never read the case, which could
file the wrong form. Those rules are gone: ordinary clinical, teaching, audit
and course descriptions now go to the AI recommender, which reads the
authoritative RCEM definitions.

Two branches stay deterministic, both keyed on the user naming something about
their own request rather than on incidental clinical wording:

* the user names the target form ("file a DOPS form");
* the user names their programme (ACCS) before describing a procedure, whose
  ACCS-specific form family the general definitions do not cover.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest


async def _ai_returns(form_type: str, rationale: str = "Model-chosen form."):
    """A stand-in for the AI recommender, asserting it really was consulted."""

    async def fake_generate(prompt, retries=1, tier=""):
        assert "Case description:" in prompt
        return json.dumps([{"form_type": form_type, "rationale": rationale}])

    return AsyncMock(side_effect=fake_generate)


@pytest.mark.asyncio
async def test_qi_project_description_now_goes_to_the_model():
    """A QI description no longer picks QIAT from keywords alone."""
    from extractor import recommend_form_types

    generate = await _ai_returns("QIAT", "QI/audit cycle described.")
    with patch("extractor._generate", new=generate):
        recommendations = await recommend_form_types(
            "I completed a QI project on ED sepsis antibiotics. Baseline audit "
            "showed delays, I introduced a checklist and teaching intervention, "
            "then re-audited compliance after the change cycle."
        )

    generate.assert_awaited_once()
    assert [rec.form_type for rec in recommendations] == ["QIAT"]
    assert recommendations[0].uuid


@pytest.mark.asyncio
async def test_course_description_now_goes_to_the_model():
    """A single course word no longer short-circuits to FORMAL_COURSE."""
    from extractor import recommend_form_types

    generate = await _ai_returns("FORMAL_COURSE", "Course attendance described.")
    with patch("extractor._generate", new=generate):
        recommendations = await recommend_form_types(
            "I attended and completed my ATLS course and received the course certificate."
        )

    generate.assert_awaited_once()
    assert [rec.form_type for rec in recommendations] == ["FORMAL_COURSE"]


@pytest.mark.asyncio
async def test_observed_procedure_description_now_goes_to_the_model():
    """A procedure word no longer picks DOPS without the case being read."""
    from extractor import recommend_form_types

    async def fake_generate(prompt, retries=1, tier=""):
        assert "Case description:" in prompt
        return json.dumps([
            {"form_type": "DOPS", "rationale": "Observed hands-on procedure."},
            {"form_type": "PROC_LOG", "rationale": "Procedure also logged."},
        ])

    generate = AsyncMock(side_effect=fake_generate)
    with patch("extractor._generate", new=generate):
        recommendations = await recommend_form_types(
            "I performed procedural sedation and closed reduction in ED. "
            "The consultant directly observed me and gave feedback."
        )

    generate.assert_awaited_once()
    assert [rec.form_type for rec in recommendations][:2] == ["DOPS", "PROC_LOG"]
    assert all(rec.uuid for rec in recommendations)


@pytest.mark.asyncio
async def test_incidental_word_does_not_choose_the_form():
    """The measured failure: a passing mention decided the form unseen.

    "POCUS" appearing anywhere in a case description used to return US_CASE and
    stop, so a case that was really about managing a patient could never be read
    by the recommender.
    """
    from extractor import recommend_form_types

    generate = await _ai_returns("CBD", "Patient management and reasoning.")
    with patch("extractor._generate", new=generate):
        recommendations = await recommend_form_types(
            "I reviewed the POCUS images taken earlier, but my main involvement "
            "was managing the patient's sepsis and discussing escalation."
        )

    generate.assert_awaited_once()
    assert [rec.form_type for rec in recommendations] == ["CBD"]


@pytest.mark.asyncio
async def test_naming_the_form_returns_it_without_the_model():
    """The explicit-request branch survives: the user already knows the form."""
    from extractor import recommend_form_types

    with patch("extractor._generate", new=AsyncMock(side_effect=AssertionError("LLM called"))):
        recommendations = await recommend_form_types(
            "Please create a Case-Based Discussion entry for the chest drain case "
            "I discussed with my consultant yesterday."
        )

    assert [rec.form_type for rec in recommendations] == ["CBD"]
    assert recommendations[0].uuid


@pytest.mark.asyncio
async def test_accs_programme_procedure_keeps_the_accs_form_family():
    """ACCS variant selection stays deterministic: the general definitions do
    not carry the ACCS procedure forms."""
    from extractor import recommend_form_types

    with patch("extractor._generate", new=AsyncMock(side_effect=AssertionError("LLM called"))):
        recommendations = await recommend_form_types(
            "ACCS trainee. I performed a lumbar puncture under supervision and the "
            "consultant directly observed me."
        )

    assert [rec.form_type for rec in recommendations] == [
        "DOPS_ACCS",
        "PROCEDURAL_LOG_ACCS",
    ]


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
