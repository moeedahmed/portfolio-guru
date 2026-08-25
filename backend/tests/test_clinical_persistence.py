"""Clinical narrative must never reach the persistence file.

The pickle was found holding case text and drafted clinical content for 20 users
indefinitely. These tests fail if that can happen again — and if the in-memory
behaviour the post-save features depend on is broken in the process.
"""
from __future__ import annotations

import pickle
import re

import pytest

import clinical_persistence as cp


CASE = "68F, sudden severe headache, worst of life, GCS 14. SAH suspected."


def _user_data():
    return {
        "case_text": CASE,
        "last_filed_case_text": CASE,
        "last_amend_case_text": CASE,
        "last_amend_draft": {"fields": {"reflection": CASE}},
        "last_draft_preview": f"Draft preview: {CASE}",
        "draft_data": {"_type": "CBD", "clinical_reasoning": CASE},
        # Non-clinical state the bot genuinely needs across a restart.
        "user_tier": "pro_plus",
        "last_filing_status": "success",
        "audit_session_id": "pg-1",
    }


def test_scrub_removes_clinical_keys_and_keeps_operational_state():
    scrubbed = cp.scrub(_user_data())

    assert CASE not in repr(scrubbed), "case narrative survived the scrub"
    assert scrubbed == {
        "user_tier": "pro_plus",
        "last_filing_status": "success",
        "audit_session_id": "pg-1",
    }


def test_scrub_does_not_mutate_the_live_user_data():
    """Handlers keep reading these keys for the rest of the conversation —
    the same-case refile and amend flows depend on it."""
    live = _user_data()

    cp.scrub(live)

    assert live["case_text"] == CASE
    assert live["last_amend_draft"]["fields"]["reflection"] == CASE


@pytest.mark.asyncio
async def test_persistence_writes_no_clinical_content_to_disk(tmp_path):
    path = tmp_path / "bot_persistence"
    persistence = cp.ClinicalScrubbingPersistence(filepath=path)

    await persistence.update_user_data(4242, _user_data())
    await persistence.flush()

    on_disk = path.read_bytes()
    assert CASE.encode() not in on_disk, "case narrative was written to the pickle"
    assert b"clinical_reasoning" not in on_disk

    # Round-trip through a fresh instance to prove what a restart would restore.
    # PTB pickles with its own persistent-id pickler, so read it back its way.
    reloaded = await cp.ClinicalScrubbingPersistence(filepath=path).get_user_data()
    stored = reloaded[4242]
    assert stored["user_tier"] == "pro_plus", "operational state must survive"
    assert "case_text" not in stored
    assert "last_amend_draft" not in stored


def test_purge_strips_clinical_keys_from_an_existing_file(tmp_path):
    path = tmp_path / "bot_persistence"
    path.write_bytes(pickle.dumps({"user_data": {4242: _user_data(), 9001: {"user_tier": "free"}}}))

    result = cp.purge_existing_file(path)

    assert result["status"] == "purged"
    assert result["removed"]["last_filed_case_text"] == 1
    payload = pickle.loads(path.read_bytes())
    assert CASE.encode() not in path.read_bytes()
    assert payload["user_data"][4242]["user_tier"] == "pro_plus"
    assert payload["user_data"][9001] == {"user_tier": "free"}


def test_purge_is_idempotent_and_safe_on_a_missing_file(tmp_path):
    assert cp.purge_existing_file(tmp_path / "nope")["status"] == "absent"

    path = tmp_path / "bot_persistence"
    path.write_bytes(pickle.dumps({"user_data": {1: {"user_tier": "free"}}}))
    assert cp.purge_existing_file(path)["status"] == "clean"


def test_every_case_bearing_key_in_bot_is_declared_clinical():
    """A new user_data key holding case content must be added to the scrub set.

    This is the guard that stops the leak recurring: it reads bot.py for keys
    whose names say they carry case or draft content and fails if any is missing
    from CLINICAL_USER_DATA_KEYS.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "bot.py").read_text()
    assigned = set(re.findall(r'user_data\["([a-z_]+)"\]\s*=', source))
    case_bearing = {
        key for key in assigned
        if ("case_text" in key or key.endswith("_draft") or key.endswith("draft_data")
            or key.endswith("draft_preview"))
    }

    missing = case_bearing - cp.CLINICAL_USER_DATA_KEYS
    assert not missing, (
        f"user_data keys carrying case content are not scrubbed before "
        f"persistence: {sorted(missing)}"
    )
