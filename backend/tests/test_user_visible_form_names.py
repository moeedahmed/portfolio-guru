import pytest


FORBIDDEN_INTERNAL_FORM_CODES = (
    "DOPS_2021",
    "PROC_LOG",
    "PROC_LOG_2021",
    "MINI_CEX",
    "MINI_CEX_2021",
    "REFLECT_LOG",
    "REFLECT_LOG_2021",
    "US_CASE",
    "ESLE_ASSESS",
    "SERIOUS_INC",
    "SERIOUS_INC_2021",
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
    assert public_form_name("DOPS_2021") == "Direct Observation of Procedural Skills"

    text = sanitize_internal_form_codes(
        "DOPS_2021 draft ready. Consider DOPS, PROC_LOG_2021, MINI_CEX, ESLE_ASSESS, SERIOUS_INC_2021 and FORMAL_COURSE."
    )

    assert_no_internal_form_codes(text)
    assert "Direct Observation of Procedural Skills draft ready" in text
    assert "Procedural Log" in text
    assert "Mini-Clinical Evaluation Exercise" in text
    assert "Formal Course" in text


def test_variant_draft_preview_uses_public_name_and_base_schema():
    from bot import _format_generic_draft, _universal_pre_file_gate
    from models import FormDraft

    draft = FormDraft(
        form_type="DOPS_2021",
        fields={
            "date_of_encounter": "2026-06-03",
            "procedure_name": "Fracture / Dislocation manipulation",
            "clinical_setting": "ED resus",
            "stage_of_training": "Higher/ST4-ST6",
            "procedural_skill": "Adult sedation",
            "indication": "Displaced ankle fracture requiring closed reduction.",
            "trainee_performance": "Prepared monitoring, consented, sedated, reduced and reviewed safely.",
            "reflection": "I will verbalise sedation contingency plans earlier.",
        },
    )

    preview = _format_generic_draft(draft)

    assert "Direct Observation of Procedural Skills draft ready" in preview
    assert "DOPS_2021" not in preview
    assert "Procedure" in preview
    assert "Trainee Performance" in preview

    missing = _universal_pre_file_gate("DOPS_2021", {})
    assert "Procedure / procedural skill" in missing
    assert "Trainee Performance" in missing


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
async def test_answer_question_form_count_matches_product_copy(monkeypatch):
    import extractor

    monkeypatch.setattr(extractor, "_get_client", lambda: object())

    answer = await extractor.answer_question("What forms do you support?")

    assert "45 RCEM forms" in answer
    assert "19 RCEM" not in answer
    assert "19 forms" not in answer
    assert "and 9 more" not in answer
    assert "Examples include" in answer
    assert "Case-Based Discussion (Case-Based Discussion)" not in answer


@pytest.mark.asyncio
async def test_answer_question_capability_copy_is_short_and_deterministic(monkeypatch):
    import extractor

    monkeypatch.setattr(extractor, "_get_client", lambda: object())

    answer = await extractor.answer_question("what can you do?")

    assert answer.startswith("🩺")
    assert "portfolio drafts" in answer
    assert "Kaizen" in answer
    assert len(answer) < 300


@pytest.mark.asyncio
async def test_answer_question_style_copy_is_not_marketing_slop(monkeypatch):
    import extractor

    monkeypatch.setattr(extractor, "_get_client", lambda: object())

    answer = await extractor.answer_question("Can you make it sound less generic?")

    assert answer.startswith("🩺")
    assert "portfolio wording" in answer
    assert "supervisor spammed" not in answer
    assert "lock it in" not in answer


@pytest.mark.asyncio
async def test_answer_question_pricing_copy_is_not_free_hallucination(monkeypatch):
    import extractor

    monkeypatch.setattr(extractor, "_get_client", lambda: object())

    answer = await extractor.answer_question("How much does this cost?")

    assert "5 cases" in answer
    assert "£9.99/month" in answer
    assert "completely free" not in answer.lower()


@pytest.mark.asyncio
async def test_probabilistic_side_question_prompt_uses_style_envelope(monkeypatch):
    import extractor

    prompts = []

    async def fake_generate(prompt, **kwargs):
        prompts.append(prompt)
        return "Portfolio evidence should be specific and source-tied."

    monkeypatch.setattr(extractor, "_generate", fake_generate)

    answer = await extractor.answer_question(
        "How should I think about portfolio evidence after a messy shift?"
    )

    assert answer == "Portfolio evidence should be specific and source-tied."
    assert prompts
    prompt = prompts[-1]
    assert "Portfolio Guru flexible reply style:" in prompt
    assert "calm Emergency Medicine portfolio coach" in prompt
    assert "draft-only wording" in prompt
    assert "do not write long essays" in prompt


@pytest.mark.asyncio
async def test_case_specific_form_question_prompt_uses_style_envelope(monkeypatch):
    import extractor

    prompts = []

    async def fake_generate(prompt, **kwargs):
        prompts.append(prompt)
        return "CBD fits because the case centres on clinical reasoning."

    monkeypatch.setattr(extractor, "_generate", fake_generate)

    answer = await extractor.answer_question(
        "Which form is best?",
        case_context=(
            "Adult in ED with chest pain. I assessed, discussed ECG/troponin "
            "findings with a senior, managed risk and reflected on escalation."
        ),
    )

    assert "Case-Based Discussion fits" in answer
    assert prompts
    prompt = prompts[-1]
    assert "Portfolio Guru flexible reply style:" in prompt
    assert "calm Emergency Medicine portfolio coach" in prompt
    assert "do not change workflow decisions" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("What form is best for doing procedural sedation?", "Direct Observation of Procedural Skills"),
        ("Which form would be best for procedural sedation", "Direct Observation of Procedural Skills"),
        ("Form is best for procedural sedation?", "Direct Observation of Procedural Skills"),
        ("What form is best for a septic shock case?", "CBD"),
        ("I saw a child with wheeze, what should I use?", "Mini-CEX"),
        ("Can you write a teaching assessment?", "STAT"),
    ],
)
async def test_answer_question_form_choice_recommends_form_not_catalogue(monkeypatch, prompt, expected):
    import extractor

    monkeypatch.setattr(extractor, "_get_client", lambda: object())

    answer = await extractor.answer_question(prompt)

    assert expected in answer
    assert "45 RCEM forms" not in answer
    assert "Examples include" not in answer


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
