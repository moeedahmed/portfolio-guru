"""Guarantees for the local draft backup (data-architecture plan, decision 2).

Each test here fails if a specific promise regresses:
  - the backup is never written in the clear;
  - a confirmed Kaizen save leaves no local copy behind;
  - /reset erases a user's backups and only that user's;
  - orphaned backups from failed filings expire.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import draft_backup


CLINICAL = "72-year-old with dysuria, hypotension and confusion; urosepsis suspected."


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_GURU_DRAFT_BACKUP_DIR", str(tmp_path))
    return tmp_path


def _fields():
    return {"clinical_reasoning": CLINICAL, "date_of_encounter": "2026-08-24"}


def test_backup_is_encrypted_not_plaintext(backup_dir):
    path = draft_backup.save(4242, "CBD", _fields())

    assert path is not None and path.exists()
    raw = path.read_bytes()
    assert CLINICAL.encode() not in raw, "clinical narrative written in the clear"
    assert b"clinical_reasoning" not in raw, "field names written in the clear"
    # And it must still be recoverable — an unreadable backup is not a backup.
    assert draft_backup.load(path)["fields"]["clinical_reasoning"] == CLINICAL


def test_save_is_skipped_rather_than_written_plaintext_without_a_key(backup_dir, monkeypatch):
    """Fail closed: no encryption key means no file, never a plaintext fallback."""
    import credentials

    monkeypatch.setattr(credentials, "_fernet", lambda: (_ for _ in ()).throw(ValueError("no key")))

    assert draft_backup.save(4242, "CBD", _fields()) is None
    assert list(backup_dir.iterdir()) == []


def test_discard_removes_the_backup_after_a_confirmed_save(backup_dir):
    draft_backup.save(4242, "CBD", _fields())
    assert len(list(backup_dir.glob("*.enc"))) == 1

    assert draft_backup.discard(4242, "CBD") == 1
    assert list(backup_dir.glob("*.enc")) == []


def test_discard_still_finds_the_backup_when_the_date_rolled_over(backup_dir):
    """A filing started before midnight and confirmed after it must not orphan."""
    yesterday = date.today() - timedelta(days=1)
    draft_backup.save(4242, "CBD", _fields(), on=yesterday)

    assert draft_backup.discard(4242, "CBD") == 1
    assert list(backup_dir.glob("*.enc")) == []


def test_purge_user_erases_only_that_users_backups(backup_dir):
    draft_backup.save(4242, "CBD", _fields())
    draft_backup.save(4242, "DOPS", _fields())
    draft_backup.save(9001, "CBD", _fields())

    assert draft_backup.purge_user(4242) == 2
    remaining = [p.name for p in backup_dir.glob("*.enc")]
    assert len(remaining) == 1 and remaining[0].startswith("9001_")


def test_reset_clears_draft_backups(backup_dir):
    """The /reset erasure path must actually reach this store."""
    import asyncio

    import bot

    draft_backup.save(4242, "CBD", _fields())
    cleared = asyncio.run(bot._clear_local_portfolio_account_data(4242, reason="test"))

    assert cleared.get("draft_backups") == 1
    assert list(backup_dir.glob("*.enc")) == []


def test_expired_orphans_are_purged_and_fresh_ones_kept(backup_dir, monkeypatch):
    monkeypatch.setenv("PG_DRAFT_BACKUP_TTL_DAYS", "7")
    fresh = draft_backup.save(4242, "CBD", _fields())
    stale = draft_backup.save(9001, "DOPS", _fields())

    old = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    import os

    os.utime(stale, (old, old))

    result = draft_backup.purge_expired()

    assert result["removed"] == 1
    assert fresh.exists()
    assert not stale.exists()


def test_filenames_cannot_escape_the_backup_directory(backup_dir):
    """A form type is attacker-adjacent input; it must not traverse."""
    path = draft_backup.save(4242, "../../etc/passwd", _fields())

    assert path is not None
    assert path.parent == backup_dir
    assert ".." not in path.name
