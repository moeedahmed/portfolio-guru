import asyncio
import json
from unittest.mock import patch

from extractor import _prepare_case_description_for_model, extract_cbd_data
from privacy_guard import deidentify_clinical_text, privacy_summary


def test_privacy_guard_deidentifies_uk_patient_identifiers():
    text = (
        "Patient Aisha Khan, NHS No 943 476 5919, MRN KGH-123456, "
        "hospital number KH1234567, DOB 14/02/1977, attended Kingston Hospital, Ward Astor."
    )

    redacted, findings = deidentify_clinical_text(text)

    assert findings
    for identifier in ("Aisha Khan", "943 476 5919", "KGH-123456", "KH1234567", "14/02/1977", "Kingston Hospital", "Ward Astor"):
        assert identifier not in redacted
    assert "[NHS number]" in redacted
    assert "[MRN]" in redacted
    assert "[hospital number]" in redacted
    assert "[date of birth]" in redacted


def test_privacy_summary_is_phi_safe():
    summary = privacy_summary(["MRN KGH-123456 at Kingston Hospital"])

    assert summary["status"] == "blocked"
    assert summary["high_risk_count"] >= 2
    assert "KGH-123456" not in json.dumps(summary)
    assert "Kingston Hospital" not in json.dumps(summary)


def test_prepare_case_description_for_model_removes_identifiers():
    cleaned = _prepare_case_description_for_model(
        "I saw Mr Ben Whitfield, NHS No 401 023 2137, in Ward Astor at Kingston Hospital."
    )

    assert "Ben Whitfield" not in cleaned
    assert "401 023 2137" not in cleaned
    assert "Ward Astor" not in cleaned
    assert "Kingston Hospital" not in cleaned


def test_extract_cbd_data_prompt_receives_deidentified_case():
    captured = {}

    async def fake_generate(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps({
            "form_type": "CBD",
            "date_of_encounter": "",
            "patient_age": "",
            "patient_presentation": "Chest pain",
            "clinical_setting": "Emergency Department",
            "stage_of_training": None,
            "trainee_role": "",
            "clinical_reasoning": "I assessed chest pain.",
            "reflection": "I will continue to use structured assessment.",
            "level_of_supervision": "Indirect",
            "supervisor_name": None,
            "curriculum_links": [],
            "key_capabilities": [],
        })

    with patch("extractor._generate", fake_generate):
        asyncio.run(
            extract_cbd_data("I saw Mr Ben Whitfield, MRN KGH-123456, at Kingston Hospital with chest pain.")
        )

    assert "Ben Whitfield" not in captured["prompt"]
    assert "KGH-123456" not in captured["prompt"]
    assert "Kingston Hospital" not in captured["prompt"]
