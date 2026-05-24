"""Tests for the per-supervisor assessor capture session store.

The store is a thin file-based cache used by :mod:`supervisor_bot` to
remember which ticket the supervisor most recently tapped Open on, plus
any in-progress intent text and draft. The tests pin the lifecycle:

* ``start`` overwrites any prior session for the user.
* ``get`` returns the active session, or ``None`` when missing/corrupt.
* ``update_intent`` / ``update_draft`` mutate without losing other
  fields.
* ``end`` removes the file (idempotent).
* Missing or malformed files behave like "no session" — never raise.
* Module source contains no Kaizen write-action references.
"""

from __future__ import annotations

import inspect

import assessor_session_store as session_store
from assessor_drafter import AssessorDraft


def test_start_persists_session_for_user(tmp_path):
    sess = session_store.start(
        tmp_path,
        telegram_user_id=42,
        ticket_uuid="tk-1",
        form_type="CBD",
        ticket_url="https://kaizenep.com/events/view-section/tk-1",
        trainee_section=[{"label": "Case", "value": "Chest pain"}],
        pending_assessor_fields=[{"key": "feedback", "label": "Feedback"}],
    )

    assert sess.ticket_uuid == "tk-1"
    assert sess.form_type == "CBD"
    fetched = session_store.get(tmp_path, telegram_user_id=42)
    assert fetched is not None
    assert fetched.ticket_uuid == "tk-1"
    assert fetched.trainee_section == [{"label": "Case", "value": "Chest pain"}]


def test_get_returns_none_when_no_session(tmp_path):
    assert session_store.get(tmp_path, telegram_user_id=1) is None


def test_start_overwrites_prior_session_for_same_user(tmp_path):
    session_store.start(
        tmp_path,
        telegram_user_id=42,
        ticket_uuid="tk-1",
        form_type="CBD",
        ticket_url=None,
    )
    session_store.start(
        tmp_path,
        telegram_user_id=42,
        ticket_uuid="tk-2",
        form_type="DOPS",
        ticket_url=None,
    )

    fetched = session_store.get(tmp_path, telegram_user_id=42)
    assert fetched is not None
    assert fetched.ticket_uuid == "tk-2"
    assert fetched.form_type == "DOPS"


def test_separate_users_have_independent_sessions(tmp_path):
    session_store.start(
        tmp_path, telegram_user_id=1, ticket_uuid="tk-a", form_type="CBD", ticket_url=None
    )
    session_store.start(
        tmp_path, telegram_user_id=2, ticket_uuid="tk-b", form_type="DOPS", ticket_url=None
    )

    user1 = session_store.get(tmp_path, telegram_user_id=1)
    user2 = session_store.get(tmp_path, telegram_user_id=2)
    assert user1 is not None and user1.ticket_uuid == "tk-a"
    assert user2 is not None and user2.ticket_uuid == "tk-b"


def test_update_intent_preserves_other_fields(tmp_path):
    session_store.start(
        tmp_path,
        telegram_user_id=42,
        ticket_uuid="tk-1",
        form_type="CBD",
        ticket_url="https://example/tk-1",
        trainee_section=[{"label": "L", "value": "V"}],
    )

    updated = session_store.update_intent(
        tmp_path, telegram_user_id=42, intent="Good case, level 4."
    )

    assert updated is not None
    assert updated.intent == "Good case, level 4."
    assert updated.ticket_uuid == "tk-1"
    assert updated.trainee_section == [{"label": "L", "value": "V"}]


def test_update_intent_no_session_returns_none(tmp_path):
    result = session_store.update_intent(
        tmp_path, telegram_user_id=99, intent="orphan"
    )
    assert result is None


def test_update_draft_persists_draft_payload(tmp_path):
    session_store.start(
        tmp_path,
        telegram_user_id=42,
        ticket_uuid="tk-1",
        form_type="CBD",
        ticket_url=None,
    )
    draft = AssessorDraft(
        form_type="CBD",
        ticket_uuid="tk-1",
        values={"feedback": "Good"},
        missing_required=[],
        risk_notes=["Feedback is brief — consider adding clinical detail before saving."],
        source_intent="Good",
    )

    updated = session_store.update_draft(
        tmp_path, telegram_user_id=42, draft=draft
    )

    assert updated is not None
    assert updated.draft is not None
    assert updated.draft["values"] == {"feedback": "Good"}
    assert updated.draft["risk_notes"] == [
        "Feedback is brief — consider adding clinical detail before saving."
    ]


def test_end_removes_session_file(tmp_path):
    session_store.start(
        tmp_path,
        telegram_user_id=42,
        ticket_uuid="tk-1",
        form_type="CBD",
        ticket_url=None,
    )

    assert session_store.end(tmp_path, telegram_user_id=42) is True
    assert session_store.get(tmp_path, telegram_user_id=42) is None


def test_end_is_idempotent_when_no_session(tmp_path):
    assert session_store.end(tmp_path, telegram_user_id=42) is False


def test_get_returns_none_when_file_is_corrupt(tmp_path):
    path = tmp_path / "assessor_session_42.json"
    path.write_text("not valid json", encoding="utf-8")

    assert session_store.get(tmp_path, telegram_user_id=42) is None


def test_get_returns_none_when_file_payload_is_not_dict(tmp_path):
    path = tmp_path / "assessor_session_42.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert session_store.get(tmp_path, telegram_user_id=42) is None


# ── safety: no write-side Kaizen actions referenced ─────────────────────────


def test_session_store_module_never_clicks_write_controls():
    source = inspect.getsource(session_store)
    forbidden_snippets = [
        "click('text=Sign",
        "click('text=Submit",
        "click('text=Approve",
        "click('text=Delete",
        "click('text=Save",
        "click('text=Send",
        "click('text=Fill",
        'click("text=Sign',
        'click("text=Submit',
        'click("text=Approve',
        'click("text=Delete',
        'click("text=Save',
        'click("text=Send',
        'click("text=Fill',
        "get_by_text('Sign",
        "get_by_text('Submit",
        "get_by_text('Approve",
        "get_by_text('Delete",
        "get_by_text('Save",
        "get_by_text('Send",
        "get_by_text('Fill in",
        ".fill(",
        "extract_assessor_completion_shape",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source, (
            f"assessor_session_store source contains forbidden write action: {snippet}"
        )
