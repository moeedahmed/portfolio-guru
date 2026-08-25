"""The Supabase mirror must carry the fact of a filing, never its content.

This replaces an earlier guard that only checked the clinical payload was
*encrypted* before insert. Encryption made the mirror a well-protected Art. 9
store; it was still an Art. 9 store, in a second system, duplicating evidence
the doctor already has in Kaizen.

Decision 2 of docs/data-architecture-plan-2026-08-24.md removed the payload
entirely. These tests fail if any route puts it back — including by passing it
in, which callers may still do.
"""
import json

from cryptography.fernet import Fernet

import credentials
import supabase_sync


CASE = "47-year-old with central chest pain radiating to the jaw."
IDENTIFIABLE = {
    "patient_name": "Jane Doe",
    "age": "47",
    "presentation": "chest pain",
    "hospital_number": "RX-99281",
}


class _CapturingTable:
    def __init__(self, sink):
        self._sink = sink

    def insert(self, payload):
        self._sink["payload"] = payload
        return self

    def execute(self):
        return None


class _CapturingClient:
    def __init__(self, sink):
        self._sink = sink

    def table(self, name):
        self._sink["table"] = name
        return _CapturingTable(self._sink)


def _patch(monkeypatch, sink):
    monkeypatch.setattr(credentials, "FERNET_KEY", Fernet.generate_key())
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: _CapturingClient(sink))
    return sink


def test_clinical_content_is_discarded_even_when_passed_in(monkeypatch):
    """Callers may still pass the case; the mirror must drop it on the floor."""
    sink = _patch(monkeypatch, {})

    supabase_sync.mirror_case(
        12345,
        form_type="CBD",
        status="success",
        case_text_encrypted=b"anything-at-all",
        extracted_fields=IDENTIFIABLE,
    )

    payload = sink["payload"]
    assert "extracted_fields" not in payload
    assert "case_text_encrypted" not in payload

    blob = json.dumps(payload)
    for value in IDENTIFIABLE.values():
        assert value not in blob, f"{value!r} reached the mirror payload"


def test_the_filing_fact_and_taxonomy_still_mirror(monkeypatch):
    """What /health, KC coverage and ARCP projection actually read must survive."""
    sink = _patch(monkeypatch, {})

    supabase_sync.mirror_case(
        12345,
        form_type="CBD",
        status="success",
        kaizen_event_id="evt-77",
        curriculum_links=["SLO1"],
        key_capabilities=["SLO1 KC1"],
        extracted_fields=IDENTIFIABLE,
    )

    payload = sink["payload"]
    assert payload["form_type"] == "CBD"
    assert payload["status"] == "success"
    assert payload["kaizen_event_id"] == "evt-77"
    assert payload["curriculum_links"] == ["SLO1"]
    assert payload["key_capabilities"] == ["SLO1 KC1"]
    assert payload["telegram_user_id"] == 12345


def test_no_encryption_helper_remains_to_be_reintroduced(monkeypatch):
    """The old path encrypted fields into an ``{"_encrypted": ...}`` envelope.
    Its absence is what stops the payload quietly coming back."""
    assert not hasattr(supabase_sync, "_encrypt_fields")
