"""Offline multi-user isolation checks for the filing readiness sprint.

No Telegram, Kaizen, credentials, browser, or network. These tests exercise
the serialised state shapes the bot stores through PicklePersistence so two
users filing around the same time cannot share draft state.
"""

from __future__ import annotations

from types import SimpleNamespace


def _context():
    return SimpleNamespace(user_data={})


def test_active_drafts_are_isolated_per_user_context():
    import bot
    from models import FormDraft

    user_a = _context()
    user_b = _context()

    bot._store_draft(
        user_a,
        FormDraft(
            form_type="CBD",
            fields={"reflection": "A reflection", "stage_of_training": "Higher"},
            uuid="a-draft",
        ),
    )
    bot._store_draft(
        user_b,
        FormDraft(
            form_type="DOPS",
            fields={"reflection": "B reflection", "stage_of_training": "ACCS"},
            uuid="b-draft",
        ),
    )

    draft_a = bot._load_draft(user_a)
    draft_b = bot._load_draft(user_b)

    assert draft_a.form_type == "CBD"
    assert draft_a.fields["reflection"] == "A reflection"
    assert draft_a.uuid == "a-draft"
    assert draft_b.form_type == "DOPS"
    assert draft_b.fields["reflection"] == "B reflection"
    assert draft_b.uuid == "b-draft"
    assert user_a.user_data["draft_data"] != user_b.user_data["draft_data"]


def test_retryable_last_filed_case_state_isolated_between_users():
    import bot

    user_a = _context()
    user_b = _context()

    user_a.user_data.update({
        "last_filing_status": "partial",
        "last_amend_draft": {
            "_type": "FORM",
            "form_type": "CBD",
            "fields": {"reflection": "A case"},
            "uuid": "a-draft",
        },
        "last_amend_case_text": "A patient case",
        "last_amend_chosen_form": "CBD",
    })
    user_b.user_data.update({
        "last_filing_status": "partial",
        "last_amend_draft": {
            "_type": "FORM",
            "form_type": "DOPS",
            "fields": {"reflection": "B case"},
            "uuid": "b-draft",
        },
        "last_amend_case_text": "B patient case",
        "last_amend_chosen_form": "DOPS",
    })

    assert bot._restore_retryable_draft(user_a) is True
    assert bot._restore_retryable_draft(user_b) is True

    assert bot._load_draft(user_a).uuid == "a-draft"
    assert bot._load_draft(user_b).uuid == "b-draft"
    assert user_a.user_data["case_text"] == "A patient case"
    assert user_b.user_data["case_text"] == "B patient case"

