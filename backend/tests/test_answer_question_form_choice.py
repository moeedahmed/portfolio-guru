import pytest

from extractor import answer_question


@pytest.mark.asyncio
async def test_procedural_sedation_form_choice_gets_specific_recommendation():
    answer = await answer_question("What form is best for doing procedural sedation?")

    assert answer.startswith("🩺")
    assert "DOPS" in answer
    assert "Procedural Log" in answer
    assert "case details" in answer
    assert "I support 45 RCEM forms" not in answer
