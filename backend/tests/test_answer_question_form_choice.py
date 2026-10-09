import pytest
import json
from unittest.mock import AsyncMock, patch

from extractor import answer_question


@pytest.mark.asyncio
async def test_procedural_sedation_form_choice_gets_specific_recommendation():
    answer = await answer_question("What form is best for doing procedural sedation?")

    assert answer.startswith("🩺")
    assert "DOPS" in answer
    assert "Procedural Log" in answer
    assert "case details" in answer
    assert "I support 45 RCEM forms" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", [
    "Attachments are sent straight to your e-portfolio",
    "Your access details are kept confidential",
    "CBD fits the secret rules of the panel",
])
async def test_case_form_advice_renders_only_reviewed_descriptions(claim):
    payload = {"form_codes": ["CBD", "DOPS", claim, "UNKNOWN", "CBD"], "reason": claim}
    with patch("extractor._generate", AsyncMock(return_value=json.dumps(payload))):
        answer = await answer_question("Which form is best?", case_context="I assessed an ankle injury.")
    assert claim not in answer
    assert "UNKNOWN" not in answer
    assert answer.count("Case-Based Discussion") == 1
    assert "clinical reasoning and management" in answer
    assert "observed procedure" in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [
    "Attachments are sent straight to your e-portfolio",
    "Your access details are kept confidential",
    '{"form_codes": ["UNKNOWN", 1, null, {}]}',
    '{"form_codes": "CBD"}', "null", "[]", "{}",
])
async def test_case_form_advice_invalid_output_uses_reviewed_fallback(output):
    from channel_reply_policy import select_deterministic_reply
    question = "Which form is best?"
    with patch("extractor._generate", AsyncMock(return_value=output)):
        answer = await answer_question(question, case_context="I assessed an ankle injury.")
    assert answer == select_deterministic_reply(question, include_first_contact=False).full_text()


@pytest.mark.asyncio
async def test_case_form_advice_validates_the_owning_supported_form_registry():
    from channel_reply_policy import select_deterministic_reply
    question = "Which form is best?"
    with patch('filer_router.PLATFORM_REGISTRY', {'kaizen': {'supported_forms': ['DOPS']}}), \
         patch('extractor._generate', AsyncMock(return_value='{"form_codes": ["CBD"]}')):
        answer = await answer_question(question, case_context="I assessed an ankle injury.")
    assert answer == select_deterministic_reply(question, include_first_contact=False).full_text()
