"""Consent records must have a durable copy off the single disk.

Consent is the evidence of the lawful basis for every past act of processing.
It lived only in SQLite on one unencrypted Mac Mini, so losing that disk meant
losing the ability to demonstrate compliance at all — the one store where the
loss is not recoverable from Kaizen or from the user.

Also pins the property that erasure never deletes it.
"""
from __future__ import annotations

import pytest

import consent
import supabase_sync
import usage


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "DB_PATH", str(tmp_path / "usage.db"))
    return tmp_path


@pytest.fixture
def mirrored(monkeypatch):
    calls: list[dict] = []

    def _capture(user_id, **kwargs):
        calls.append({"user_id": user_id, **kwargs})

    monkeypatch.setattr(supabase_sync, "mirror_consent", _capture)
    return calls


@pytest.mark.asyncio
async def test_granting_consent_is_mirrored(db, mirrored):
    await consent.record_consent(4242)

    assert len(mirrored) == 1
    record = mirrored[0]
    assert record["user_id"] == 4242
    assert record["action"] == "granted"
    assert record["consent_version"] == consent.CONSENT_VERSION
    assert record["consent_text_hash"] == consent.consent_text_hash()
    assert record["lawful_basis"] == consent.LAWFUL_BASIS


@pytest.mark.asyncio
async def test_withdrawal_is_mirrored_as_a_new_record(db, mirrored):
    await consent.record_consent(4242)
    await consent.record_withdrawal(4242)

    assert [r["action"] for r in mirrored] == ["granted", "withdrawn"]


@pytest.mark.asyncio
async def test_a_mirror_failure_never_blocks_consent(db, monkeypatch):
    """A doctor must be able to consent while Supabase is down. The local row is
    written first, so the mirror is genuinely best-effort."""
    def _boom(*_a, **_k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(supabase_sync, "mirror_consent", _boom)

    await consent.record_consent(4242)

    assert await consent.has_current_consent(4242) is True


@pytest.mark.asyncio
async def test_withdrawal_without_a_grant_mirrors_nothing(db, mirrored):
    """An unconsented /reset must not log a spurious withdrawal, locally or up."""
    await consent.record_withdrawal(4242)

    assert mirrored == []


def test_consent_is_not_in_the_erasable_set():
    assert "pg_consent_records" not in supabase_sync.ERASABLE_TABLES
