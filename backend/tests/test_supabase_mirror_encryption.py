"""Regression guard: extracted clinical fields must never leave the bot as plaintext.

mirror_case() writes a filed case to Supabase. The free-text narrative was already
encrypted, but the structured `extracted_fields` dict (which can carry age, sex,
presentation and any identifiers the extractor lifted) used to be inserted as
plaintext JSON. This test pins the invariant that it is now Fernet-encrypted at the
persistence boundary, and fails closed (never plaintext) if encryption is unavailable.
"""
import json

from cryptography.fernet import Fernet

import credentials
import supabase_sync


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


def _patch_common(monkeypatch, sink, key):
    monkeypatch.setattr(credentials, "FERNET_KEY", key)
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: _CapturingClient(sink))
    monkeypatch.setattr(supabase_sync, "_resolve_emgurus_user_id", lambda _uid: "uuid-123")


def test_extracted_fields_are_encrypted_not_plaintext(monkeypatch):
    sink = {}
    key = Fernet.generate_key()
    _patch_common(monkeypatch, sink, key)

    secret_fields = {
        "patient_name": "Jane Doe",
        "age": "47",
        "presentation": "chest pain",
        "hospital_number": "RX-99281",
    }

    supabase_sync.mirror_case(
        12345,
        form_type="CBD",
        status="success",
        extracted_fields=secret_fields,
    )

    payload = sink["payload"]
    stored = payload["extracted_fields"]

    # The stored value is only the ciphertext envelope — no plaintext keys/values.
    assert set(stored.keys()) == {"_encrypted"}
    blob = json.dumps(payload)
    assert "Jane Doe" not in blob
    assert "RX-99281" not in blob
    assert "chest pain" not in blob

    # And it round-trips back to the original with the key.
    decrypted = json.loads(Fernet(key).decrypt(stored["_encrypted"].encode()).decode())
    assert decrypted == secret_fields


def test_empty_fields_stay_empty(monkeypatch):
    sink = {}
    _patch_common(monkeypatch, sink, Fernet.generate_key())

    supabase_sync.mirror_case(12345, form_type="CBD", status="partial", extracted_fields=None)

    assert sink["payload"]["extracted_fields"] == {}


def test_fails_closed_when_encryption_unavailable(monkeypatch):
    """If the Fernet key is missing/invalid, drop the fields — never emit plaintext."""
    sink = {}
    monkeypatch.setattr(credentials, "FERNET_KEY", b"")  # _fernet() raises ValueError
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: _CapturingClient(sink))
    monkeypatch.setattr(supabase_sync, "_resolve_emgurus_user_id", lambda _uid: "uuid-123")

    supabase_sync.mirror_case(
        12345,
        form_type="CBD",
        status="success",
        extracted_fields={"patient_name": "Jane Doe"},
    )

    stored = sink["payload"]["extracted_fields"]
    assert stored == {}
    assert "Jane Doe" not in json.dumps(sink["payload"])
