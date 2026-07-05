import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import eval_draft_quality_loop as loop  # noqa: E402
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
