import pytest


FORBIDDEN_INTERNAL_FORM_CODES = (
    "PROC_LOG",
    "MINI_CEX",
    "REFLECT_LOG",
    "US_CASE",
    "ESLE_ASSESS",
    "SERIOUS_INC",
    "EDU_ACT",
    "FORMAL_COURSE",
    "TEACH_OBS",
    "CLIN_GOV",
    "MGMT_RISK_PROC",
)


def assert_no_internal_form_codes(text: str) -> None:
    for code in FORBIDDEN_INTERNAL_FORM_CODES:
        assert code not in text


def test_public_form_name_replaces_internal_form_keys():
    from form_display import public_form_name, sanitize_internal_form_codes

    assert public_form_name("PROC_LOG") == "Procedural Log"
    assert public_form_name("MINI_CEX") == "Mini-Clinical Evaluation Exercise"
    assert public_form_name("SERIOUS_INC_2021") == "Serious Incident Reflection"

    text = sanitize_internal_form_codes(
        "Consider DOPS, PROC_LOG, MINI_CEX, ESLE_ASSESS, SERIOUS_INC and FORMAL_COURSE."
    )

    assert_no_internal_form_codes(text)
    assert "Procedural Log" in text
    assert "Mini-Clinical Evaluation Exercise" in text
    assert "Formal Course" in text


def test_bot_form_display_and_recommendation_copy_are_public():
    from bot import _form_display_name, _format_failed_filing_summary, _recommendation_line
    from models import FormTypeRecommendation

    assert _form_display_name("PROC_LOG") == "Procedural Log"

    line = _recommendation_line(
        FormTypeRecommendation(
            form_type="PROC_LOG",
            rationale="Better than MINI_CEX; consider PROC_LOG for recent activity.",
            uuid="uuid",
        ),
        index=0,
        total=1,
        curriculum="2025",
    )
    assert_no_internal_form_codes(line)
    assert "Procedural Log" in line
    assert "Mini-Clinical Evaluation Exercise" in line

    summary = _format_failed_filing_summary(
        "PROC_LOG required field failed",
        ["higher_procedural_skill", "PROC_LOG"],
    )
    assert_no_internal_form_codes(summary)
    assert "Procedural Log" in summary


@pytest.mark.asyncio
async def test_answer_question_form_list_does_not_show_internal_keys(monkeypatch):
    import extractor

    monkeypatch.setattr(extractor, "_get_client", lambda: object())

    answer = await extractor.answer_question("Do you support Procedural Log?")

    assert_no_internal_form_codes(answer)
    assert "Procedural Log" in answer


@pytest.mark.asyncio
async def test_recent_activity_llm_output_is_sanitised(monkeypatch):
    import extractor

    async def fake_generate(prompt):
        return "You filed a CBD; consider adding PROC_LOG or MINI_CEX next."

    monkeypatch.setattr(extractor, "_generate", fake_generate)

    text = await extractor.summarise_recent_activity(
        [
            {"form_type": "CBD", "filed_at": "2026-05-01"},
            {"form_type": "PROC_LOG", "filed_at": "2026-05-10"},
            {"form_type": "DOPS", "filed_at": "2026-05-20"},
        ],
        "CBD",
    )

    assert_no_internal_form_codes(text)
    assert "Procedural Log" in text
    assert "Mini-Clinical Evaluation Exercise" in text
