"""Direct Telegram-keyed London writes, with no network or Hub dependency."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import supabase_sync as sync


class FakeClient:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []

    def table(self, name):
        call = {"table": name, "filters": []}
        self.calls.append(call)
        return FakeQuery(call, self.rows)


class FakeQuery:
    def __init__(self, call, rows):
        self.call, self.rows = call, rows

    def upsert(self, payload, **kwargs):
        self.call.update(op="upsert", payload=payload, **kwargs)
        return self

    def insert(self, payload):
        self.call.update(op="insert", payload=payload)
        return self

    def update(self, payload):
        self.call.update(op="update", payload=payload)
        return self

    def select(self, columns):
        self.call.update(op="select", columns=columns)
        return self

    def delete(self):
        self.call.update(op="delete")
        return self

    def eq(self, column, value):
        self.call["filters"].append((column, value))
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


WRITES = [
    ("mirror_credentials", (42, b"cipher-user", b"cipher-pass"), {}, "pg_credentials", "upsert",
     {"kaizen_username_enc": "cipher-user", "kaizen_password_enc": "cipher-pass"}),
    ("mirror_profile", (42,), {"training_level": "ST3", "voice_profile_json": '{"style":"brief"}', "voice_examples_count": 0}, "pg_profile", "upsert",
     {"training_level": "ST3", "voice_profile": {"style": "brief"}, "voice_examples_count": 0}),
    ("mirror_tier", (42, "pro_plus"), {"stripe_customer_id": "cus_test", "stripe_subscription_id": "sub_test"}, "pg_users", "upsert",
     {"tier": "pro_plus", "stripe_customer_id": "cus_test", "stripe_subscription_id": "sub_test"}),
    ("mirror_usage", (42, "CBD"), {}, "pg_usage", "insert", {"form_type": "CBD"}),
    ("mirror_case", (42, "CBD", "success"), {}, "pg_filings", "insert",
     {"form_type": "CBD", "status": "success", "source": "bot", "curriculum_links": [], "key_capabilities": []}),
    ("store_beta_request", (42, "tester"), {}, "pg_beta_requests", "insert", {"username": "tester", "status": "pending"}),
]


@pytest.mark.parametrize("name,args,kwargs,table,op,fields", WRITES)
def test_every_mirror_writes_directly_by_telegram_id(monkeypatch, name, args, kwargs, table, op, fields):
    client = FakeClient()
    monkeypatch.setattr(sync, "_supabase", lambda: client)
    getattr(sync, name)(*args, **kwargs)
    assert len(client.calls) == 1  # No lookup or prerequisite account creation.
    call = client.calls[0]
    assert (call["table"], call["op"]) == (table, op)
    payload = dict(call["payload"])
    if op == "upsert":
        assert call["on_conflict"] == "telegram_user_id"
        assert datetime.fromisoformat(payload.pop("updated_at")).utcoffset().total_seconds() == 0
    elif table == "pg_beta_requests":
        assert datetime.fromisoformat(payload.pop("created_at")).tzinfo is not None
    assert payload == {"telegram_user_id": 42, **fields}


@pytest.mark.parametrize("name,args,kwargs,table,op,fields", WRITES)
def test_mirror_errors_never_raise_or_log_payload(monkeypatch, caplog, name, args, kwargs, table, op, fields):
    client = Mock()
    client.table.side_effect = RuntimeError("secret-echoed-by-provider")
    monkeypatch.setattr(sync, "_supabase", lambda: client)
    assert getattr(sync, name)(*args, **kwargs) is None
    assert "secret-echoed-by-provider" not in caplog.text


@pytest.mark.parametrize("name,args,kwargs,table,op,fields", WRITES)
def test_unconfigured_mirrors_are_noops(monkeypatch, name, args, kwargs, table, op, fields):
    monkeypatch.setattr(sync, "_supabase", lambda: None)
    assert getattr(sync, name)(*args, **kwargs) is None


def test_credential_bytes_fallback_is_lossless(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(sync, "_supabase", lambda: client)
    sync.mirror_credentials(42, b"\xffcipher", b"ascii-cipher")
    payload = client.calls[0]["payload"]
    assert payload["kaizen_username_enc"].encode("latin1") == b"\xffcipher"
    assert payload["kaizen_password_enc"].encode("ascii") == b"ascii-cipher"


def test_partial_profile_and_tier_do_not_clear_omitted_fields(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(sync, "_supabase", lambda: client)
    sync.mirror_profile(42, curriculum="2025")
    sync.mirror_tier(42, "free")
    assert set(client.calls[0]["payload"]) == {"telegram_user_id", "curriculum", "updated_at"}
    assert set(client.calls[1]["payload"]) == {"telegram_user_id", "tier", "updated_at"}
    sync.mirror_profile(42)
    sync.mirror_profile(42, voice_profile_json="invalid json")
    assert len(client.calls) == 2


def test_beta_helpers_keep_caller_id_alias_and_scope_approval(monkeypatch):
    client = FakeClient([{"telegram_user_id": 42, "username": "tester"}])
    monkeypatch.setattr(sync, "_supabase", lambda: client)
    assert sync.get_beta_request_by_username("@tester")["user_id"] == 42
    assert client.calls[0]["table"] == "pg_beta_requests"
    assert client.calls[0]["filters"] == [("username", "tester"), ("status", "pending")]
    assert sync.approve_beta_request(42, tier="pro_plus") is True
    call = client.calls[1]
    assert call["table"] == "pg_beta_requests"
    assert call["filters"] == [("telegram_user_id", 42), ("status", "pending")]
    assert call["payload"]["status"] == "approved"
    assert datetime.fromisoformat(call["payload"]["approved_at"]).tzinfo is not None


def test_credential_delete_is_scoped(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(sync, "_supabase", lambda: client)
    sync.delete_mirrored_credentials(42)
    assert client.calls == [{"table": "pg_credentials", "op": "delete", "filters": [("telegram_user_id", 42)]}]


def test_chase_never_contacts_supabase_or_logs_assessor_data(monkeypatch, caplog):
    client = Mock(side_effect=AssertionError("Must not connect"))
    monkeypatch.setattr(sync, "_supabase", client)
    with caplog.at_level("DEBUG"):
        sync.mirror_chase(42, "private@example.invalid", "Private Assessor", "2026-09-30")
    client.assert_not_called()
    assert "private@example.invalid" not in caplog.text
    assert "Private Assessor" not in caplog.text


def test_beta_and_delete_failures_are_best_effort(monkeypatch):
    client = Mock()
    client.table.side_effect = RuntimeError("offline")
    monkeypatch.setattr(sync, "_supabase", lambda: client)
    assert sync.get_beta_request_by_username("tester") is None
    assert sync.approve_beta_request(42) is False
    assert sync.delete_mirrored_credentials(42) is None
    monkeypatch.setattr(sync, "_supabase", lambda: None)
    assert sync.get_beta_request_by_username("tester") is None
    assert sync.approve_beta_request(42) is False
    assert sync.delete_mirrored_credentials(42) is None
