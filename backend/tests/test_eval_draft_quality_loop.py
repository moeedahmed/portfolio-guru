import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import eval_draft_quality_loop as loop  # noqa: E402
import extractor  # noqa: E402
from models import FormDraft, FormTypeRecommendation  # noqa: E402


def test_generate_scenarios_are_synthetic_and_cover_schema_order():
    scenarios = loop.generate_scenarios(limit=5)

    assert len(scenarios) == 5
    assert scenarios[0].form_type == "CBD"
    assert all(s.synthetic for s in scenarios)
    assert [s.input_source for s in scenarios] == ["text", "voice", "document", "image", "text"]
    assert "supporting evidence only" in scenarios[3].source_text


def test_generate_scenarios_rejects_unknown_form():
    with pytest.raises(ValueError, match="Unknown form type"):
        loop.generate_scenarios(forms=["NOPE"])


def test_score_draft_flags_unsupported_high_risk_claims():
    scenario = loop.SyntheticScenario(
        case_id="synthetic-test",
        form_type="CBD",
        form_name="Case-Based Discussion",
        input_source="voice",
        source_text="Voice transcript: patient had chest pain, ECG normal, discharged.",
    )
    fields = {
        "date_of_encounter": "2026-07-05",
        "clinical_setting": "Emergency Department",
        "patient_presentation": "Chest pain",
        "stage_of_training": "Higher/ST4-ST6",
        "trainee_role": "I assessed the patient.",
        "clinical_reasoning": "I arranged intubation and cath lab transfer.",
        "reflection": "I learned to escalate earlier.",
        "level_of_supervision": "Indirect",
    }

    score = loop.score_draft(
        scenario=scenario,
        fields=fields,
        recommended_forms=["CBD"],
    )

    assert score.grounding < 1.0
    assert any("unsupported high-risk" in issue.lower() for issue in score.issues)


def test_score_draft_does_not_treat_learner_count_as_reflection():
    scenario = loop.SyntheticScenario(
        case_id="synthetic-stat",
        form_type="STAT",
        form_name="Structured Teaching Assessment Tool",
        input_source="text",
        source_text="I taught junior doctors ECG interpretation.",
    )
    fields = {
        "date_of_encounter": "2026-07-05",
        "stage_of_training": "Higher/ST4-ST6",
        "number_of_learners": "Less than 5",
        "session_title": "ECG interpretation",
    }

    score = loop.score_draft(scenario=scenario, fields=fields, recommended_forms=["STAT"])

    assert score.reflection == 1.0
    assert not any("first-person" in issue for issue in score.issues)


def test_score_draft_recognises_complaint_learning_fields_as_reflection():
    scenario = loop.SyntheticScenario(
        case_id="synthetic-complaint",
        form_type="COMPLAINT",
        form_name="Reflection on Complaints",
        input_source="text",
        source_text="I reflected on a complaint and learned to assign family updates explicitly.",
    )
    fields = {
        "reflection_title": "Communication complaint",
        "date_of_complaint": "2026-07-05",
        "key_features": "Complaint about delayed family communication.",
        "key_aspects": "I did not allocate a clear update role.",
        "learning_points": "I learned to explicitly assign a team member to update relatives during resus.",
        "further_action": "I will document family updates more clearly in future.",
    }

    score = loop.score_draft(
        scenario=scenario,
        fields=fields,
        recommended_forms=["COMPLAINT"],
    )

    assert score.reflection == 1.0
    assert not any("No reflection" in issue for issue in score.issues)


def test_scenario_hints_include_realistic_date_signal():
    scenario = loop.generate_scenarios(forms=["EDU_ACT"], limit=1)[0]

    assert "yesterday" in scenario.source_text.lower()


def test_deterministic_recommender_handles_explicit_niche_form_request():
    recs = extractor._deterministic_recommend_form_types(
        "I need to create a Management: Procedure to Reduce Risk entry after reviewing a new risk process."
    )

    assert recs is not None
    assert recs[0].form_type == "MGMT_RISK_PROC"


def test_deterministic_recommender_handles_punctuation_heavy_form_name():
    recs = extractor._deterministic_recommend_form_types(
        "I need to create a Higher Progression Form (ST4-ST6) entry for my ARCP evidence."
    )

    assert recs is not None
    assert recs[0].form_type == "HIGHER_PROG"


def test_deterministic_recommender_handles_accs_procedure_forms():
    recs = extractor._deterministic_recommend_form_types(
        "On ACCS I performed a lumbar puncture under supervision and reflected on positioning."
    )

    assert recs is not None
    assert recs[0].form_type == "PROCEDURAL_LOG_ACCS"


def test_deterministic_recommender_prefers_accs_dops_over_generic_dops():
    recs = extractor._deterministic_recommend_form_types(
        "During my ACCS anaesthetic placement I inserted a chest drain using "
        "Seldinger technique as an observed DOPS. My supervisor watched the procedure."
    )

    assert recs is not None
    assert recs[0].form_type == "DOPS_ACCS"


def test_deterministic_recommender_no_formal_dops_stays_procedure_log():
    recs = extractor._deterministic_recommend_form_types(
        "I performed a shoulder reduction in ED with senior advice available but no formal DOPS."
    )

    assert recs is not None
    assert recs[0].form_type == "PROC_LOG"


def test_deterministic_recommender_distinguishes_attended_teaching_from_delivered():
    recs = extractor._deterministic_recommend_form_types(
        "I attended a regional paediatric emergency medicine teaching day yesterday."
    )

    assert recs is not None
    assert recs[0].form_type == "EDU_ACT"


def test_exact_form_request_does_not_match_teach_inside_teaching():
    recs = extractor._deterministic_recommend_form_types(
        "I need a portfolio entry after attending a safeguarding teaching day."
    )

    assert recs is not None
    assert recs[0].form_type != "TEACH"


def test_deterministic_recommender_distinguishes_observed_teaching():
    recs = extractor._deterministic_recommend_form_types(
        "A consultant observed me teaching an F2 doctor how to assess ankle injuries."
    )

    assert recs is not None
    assert recs[0].form_type == "TEACH_OBS"


def test_deterministic_recommender_handles_research_and_pdp():
    research = extractor._deterministic_recommend_form_types(
        "I recruited patients to an ED research study after GCP training."
    )
    image_research = extractor._deterministic_recommend_form_types(
        "Context supplied with image: the image is supporting evidence only; "
        "I recruited patients to an ED research study after GCP training.",
        input_source="image",
    )
    pdp = extractor._deterministic_recommend_form_types(
        "My PDP goal is to improve paediatric safeguarding confidence."
    )

    assert research is not None
    assert research[0].form_type == "RESEARCH"
    assert image_research is not None
    assert image_research[0].form_type == "RESEARCH"
    assert pdp is not None
    assert pdp[0].form_type == "PDP"


def test_deterministic_recommender_keeps_plain_audit_out_of_qiat():
    recs = extractor._deterministic_recommend_form_types(
        "I completed an audit of capacity documentation and presented the results."
    )

    assert recs is not None
    assert recs[0].form_type == "AUDIT"


def test_deterministic_date_fill_uses_explicit_relative_date_only():
    fields = {"date_of_activity": "", "brief_description": "Lumbar puncture"}
    schema = extractor.FORM_SCHEMAS["PROCEDURAL_LOG_ACCS"]

    filled = extractor._fill_blank_date_fields_from_source(
        fields,
        schema,
        "I performed this yesterday.",
    )
    unchanged = extractor._fill_blank_date_fields_from_source(
        fields,
        schema,
        "I performed this during ACCS.",
    )

    assert filled["date_of_activity"]
    assert unchanged["date_of_activity"] == ""


def test_clinical_setting_fill_maps_ed_majors_only_when_blank():
    blank = extractor._fill_blank_clinical_setting_from_source(
        {"clinical_setting": ""},
        "Yesterday in ED majors I assessed chest pain.",
    )
    existing = extractor._fill_blank_clinical_setting_from_source(
        {"clinical_setting": "Intensive Care Unit"},
        "Yesterday in ED majors I assessed chest pain.",
    )

    assert blank["clinical_setting"] == "Emergency Department"
    assert existing["clinical_setting"] == "Intensive Care Unit"


def test_acaf_polish_moves_existing_learning_into_reflection():
    fields = {
        "communicate_to_patient": "I learned to explain the limits of the evidence clearly.",
        "apply_to_practice": "Use age-adjusted D-dimer in low-risk patients.",
        "reflection": "",
    }

    polished = extractor._polish_acaf_fields(fields, "I learned to document limitations.")

    assert polished["reflection"] == "I learned to explain the limits of the evidence clearly."


def test_configure_eval_runtime_materialises_vertex_credentials(monkeypatch):
    monkeypatch.setenv("PG_USE_VERTEX", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "portfolio-guru-eu")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("GCP_VERTEX_SA_JSON", '{"type":"service_account"}')

    loop.configure_eval_runtime()

    path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == '{"type":"service_account"}'
    os.unlink(path)


@pytest.mark.asyncio
async def test_run_scenario_uses_real_engine_seams_without_supabase(monkeypatch):
    scenario = loop.generate_scenarios(forms=["DOPS"], limit=1)[0]

    async def fake_recommend(text, input_source="text"):
        return [
            FormTypeRecommendation(
                form_type="DOPS",
                rationale="Procedure with direct observation.",
                uuid="uuid",
            )
        ]

    async def fake_extract(text, form_type, input_source="text", **kwargs):
        return FormDraft(
            form_type=form_type,
            fields={
                "date_of_encounter": "2026-07-05",
                "procedure_name": "DC cardioversion",
                "clinical_setting": "Emergency Department",
                "stage_of_training": "Higher/ST4-ST6",
                "procedural_skill": "DC cardioversion",
                "indication": "Unstable atrial fibrillation.",
                "trainee_performance": "I performed the cardioversion under supervision.",
                "reflection": "I learned to prepare airway support earlier.",
            },
        )

    monkeypatch.setattr(loop, "recommend_form_types", fake_recommend)
    monkeypatch.setattr(loop, "extract_form_data", fake_extract)

    result = await loop.run_scenario(scenario)

    assert result["status"] == "completed"
    assert result["selected_form_type"] == "DOPS"
    assert result["quality"]["overall"] > 0.8
    assert result["scenario"]["synthetic"] is True


@pytest.mark.asyncio
async def test_run_evaluation_payload_is_local_synthetic_dry_run():
    scenarios = loop.generate_scenarios(forms=["CBD", "DOPS"], limit=2)

    payload = await loop.run_evaluation(scenarios, dry_run=True)

    assert payload["synthetic_only"] is True
    assert payload["storage"] == "local_eval_artifact"
    assert payload["completed"] == 2
    assert payload["scenarios_requested"] == 2
    assert "results" in payload
