from __future__ import annotations

from types import SimpleNamespace

import dogfood_audit


def test_record_event_writes_redacted_local_ndjson(tmp_path):
    path = tmp_path / "dogfood-audit.ndjson"

    dogfood_audit.record_event(
        "user_input",
        user_id=123,
        username="doctor@example.com",
        session_id="session-1",
        payload={
            "text_preview": dogfood_audit.message_metadata(
                SimpleNamespace(text="Patient email test@example.com NHS 123 456 7890 phone 07123 456789"),
            )["text_preview"],
        },
        log_path=path,
    )

    records = list(dogfood_audit.iter_records(path))
    assert len(records) == 1
    record = records[0]
    assert record["event_type"] == "user_input"
    assert record["user_id"] == 123
    assert record["username"] == "[REDACTED_EMAIL]"
    preview = record["payload"]["text_preview"]
    assert "test@example.com" not in preview
    assert "07123" not in preview
    assert "123 456 7890" not in preview
    assert "[REDACTED_EMAIL]" in preview
    assert "[REDACTED_PHONE]" in preview or "[REDACTED_NHS_NUMBER]" in preview


def test_draft_payload_summary_keeps_fields_without_raw_paths():
    draft = SimpleNamespace(
        form_type="CBD",
        fields={
            "clinical_reasoning": "Discussed ECG for patient ID AB1234567 and changed plan.",
            "curriculum_links": ["SLO1 KC1"],
            "empty": "",
        },
    )

    summary = dogfood_audit.summarise_draft_payload(draft)

    assert summary["form_type"] == "CBD"
    assert "clinical_reasoning" in summary["present_fields"]
    assert "empty" not in summary["present_fields"]
    assert "AB1234567" not in summary["field_previews"]["clinical_reasoning"]
    assert "[REDACTED_ID]" in summary["field_previews"]["clinical_reasoning"]


def test_count_by_event(tmp_path):
    path = tmp_path / "audit.ndjson"
    dogfood_audit.record_event("user_input", log_path=path)
    dogfood_audit.record_event("decision_path", log_path=path)
    dogfood_audit.record_event("decision_path", log_path=path)

    assert dogfood_audit.count_by_event(dogfood_audit.iter_records(path)) == {
        "user_input": 1,
        "decision_path": 2,
    }
