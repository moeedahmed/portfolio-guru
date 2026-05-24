"""Tests for the Clinical Supervisor workflow helpers.

Covers:
- canonical role normalisation across every upstream provider string
- the demotion-safe ``set_role_if_better`` cache wrapper
- PHI-free notification payloads (no trainee names / case text / dates)
- ``run_supervisor_poll`` orchestration: role-gating, poll → payload,
  the ``unfilled_only`` default, and graceful error surfacing
- trainee role string never accidentally produces an assessor payload path
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import supervisor_workflow
from assessor_mapper import AssessorTicketSummary
from assessor_reader import AssessorTicketData
from state_tracker import TrackedState


def _memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def profile_db(monkeypatch):
    import profile_store

    engine = _memory_engine()
    monkeypatch.setattr(profile_store, "engine", engine)
    SQLModel.metadata.create_all(engine)
    return profile_store


def _summary(
    uuid: str,
    *,
    title: str = "CBD - Case Based Discussion (2025 update)",
    state: str | None = None,
    fill_action: bool | None = True,
) -> AssessorTicketSummary:
    return AssessorTicketSummary(
        title=title,
        href=f"https://kaizenep.com/events/view-section/{uuid}",
        uuid=uuid,
        state=state,
        section_view=True,
        fill_action=fill_action,
    )


# ── normalize_role ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("assessor", "assessor"),
        ("Supervisor", "assessor"),
        ("ASSESSOR", "assessor"),
        ("hst", "trainee"),
        ("accs", "trainee"),
        ("accs_intermediate", "trainee"),
        ("intermediate", "trainee"),
        ("sas", "trainee"),
        ("trainee", "trainee"),
        ("unknown", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
        ("garbage", "unknown"),
    ],
)
def test_normalize_role_maps_every_upstream_string(raw, expected):
    assert supervisor_workflow.normalize_role(raw) == expected


# ── set_role_if_better ──────────────────────────────────────────────────────


def test_set_role_if_better_stores_canonical_for_new_user(profile_db):
    assert supervisor_workflow.set_role_if_better(1001, "hst") == "trainee"
    assert profile_db.get_kaizen_role(1001) == "trainee"


def test_set_role_if_better_persists_assessor(profile_db):
    assert supervisor_workflow.set_role_if_better(1002, "assessor") == "assessor"
    assert profile_db.get_kaizen_role(1002) == "assessor"


def test_set_role_if_better_does_not_demote_known_assessor_to_unknown(profile_db):
    profile_db.store_kaizen_role(1003, "assessor")

    result = supervisor_workflow.set_role_if_better(1003, "unknown")

    assert result == "assessor"
    assert profile_db.get_kaizen_role(1003) == "assessor"


def test_set_role_if_better_does_not_demote_known_trainee_to_unknown(profile_db):
    profile_db.store_kaizen_role(1004, "trainee")

    result = supervisor_workflow.set_role_if_better(1004, None)

    assert result == "trainee"
    assert profile_db.get_kaizen_role(1004) == "trainee"


def test_set_role_if_better_allows_role_switch_between_known_values(profile_db):
    profile_db.store_kaizen_role(1005, "trainee")

    result = supervisor_workflow.set_role_if_better(1005, "assessor")

    assert result == "assessor"
    assert profile_db.get_kaizen_role(1005) == "assessor"


def test_set_role_if_better_writes_unknown_when_no_prior_value(profile_db):
    result = supervisor_workflow.set_role_if_better(1006, "unknown")

    assert result == "unknown"
    assert profile_db.get_kaizen_role(1006) == "unknown"


# ── notification payloads (PHI-free) ────────────────────────────────────────


def test_notification_payload_from_summary_extracts_form_type_and_redacts_title():
    summary = _summary(
        "uuid-1",
        title="CBD - Case Based Discussion (2025 update) for Jane Trainee",
    )

    payload = supervisor_workflow.notification_payload_from_summary(summary)

    assert payload is not None
    assert payload.form_type == "CBD"
    assert payload.redacted_title == "CBD - Case Based Discussion (2025 update)"
    # PHI guard — owner suffix never reaches the payload.
    assert "Jane Trainee" not in (payload.redacted_title or "")
    assert payload.status == "unfilled"
    assert payload.ticket_uuid == "uuid-1"
    assert payload.ticket_url.endswith("uuid-1")


def test_notification_payload_returns_none_when_uuid_missing():
    orphan = AssessorTicketSummary(title="CBD", href=None, uuid=None)

    assert supervisor_workflow.notification_payload_from_summary(orphan) is None


def test_notification_payload_classifies_completed_row_as_filled():
    summary = _summary("uuid-2", state="Complete", fill_action=False)

    payload = supervisor_workflow.notification_payload_from_summary(summary)

    assert payload is not None
    assert payload.status == "filled"


def test_build_notification_payloads_defaults_to_unfilled_only():
    summaries = [
        _summary("u-unfilled", fill_action=True),
        _summary("u-filled", state="Complete", fill_action=False),
        _summary("u-unknown", state=None, fill_action=None),
    ]

    payloads = supervisor_workflow.build_notification_payloads(summaries)

    assert {p.ticket_uuid for p in payloads} == {"u-unfilled"}


def test_build_notification_payloads_can_widen_to_unknown():
    summaries = [
        _summary("u-unfilled", fill_action=True),
        _summary("u-unknown", state=None, fill_action=None),
    ]

    payloads = supervisor_workflow.build_notification_payloads(
        summaries, statuses=("unfilled", "unknown")
    )

    assert {p.ticket_uuid for p in payloads} == {"u-unfilled", "u-unknown"}


def test_render_supervisor_notification_text_excludes_trainee_name():
    summary = _summary(
        "uuid-3",
        title="DOPS - (ST3-ST6 - 2025 update) for Jane Trainee",
    )
    payload = supervisor_workflow.notification_payload_from_summary(summary)
    assert payload is not None

    text = supervisor_workflow.render_supervisor_notification_text(payload)

    assert "Jane Trainee" not in text
    assert "*DOPS*" in text
    # Telegram message must explain the read-only contract before Open.
    assert "won't open the ticket on Kaizen" in text


def test_render_supervisor_ticket_detail_text_includes_assessor_field_labels_only():
    """Detail render carries PHI — the supervisor explicitly tapped Open.

    But the *assessor section* portion must never carry pre-filled values
    (the row is unfilled by definition); only the field labels.
    """
    data = AssessorTicketData(
        form_type="CBD",
        ticket_uuid="uuid-detail",
        ticket_url="https://kaizenep.com/events/view-section/uuid-detail",
        title="CBD - Case Based Discussion (2025 update)",
        state="Pending",
        trainee_section=[
            {"label": "Case to be discussed", "value": "Chest pain ACS rule-out"},
            {"label": "Reflection of event", "value": "Reflected on diagnostic anchoring"},
        ],
        pending_assessor_fields=[
            {"key": "feedback", "label": "Feedback"},
            {"key": "entrustment_scale", "label": "Entrustment Scale"},
        ],
        needs_write_side_mapping=True,
    )

    text = supervisor_workflow.render_supervisor_ticket_detail_text(data)

    # Trainee section is rendered (PHI is allowed here)
    assert "Chest pain ACS rule-out" in text
    # Assessor fields list shows only labels — never a value
    assert "Feedback" in text
    assert "Entrustment Scale" in text


# ── run_supervisor_poll orchestration ───────────────────────────────────────


async def test_run_supervisor_poll_skips_when_role_not_assessor(profile_db, tmp_path):
    profile_db.store_kaizen_role(2001, "trainee")
    page = AsyncMock()

    outcome = await supervisor_workflow.run_supervisor_poll(
        2001, page=page, state_path=tmp_path / "state.json", refresh_role=False
    )

    assert outcome.role == "trainee"
    assert outcome.payloads == []
    assert outcome.skipped_reason is not None
    assert "supervisor poll only runs" in outcome.skipped_reason


async def test_run_supervisor_poll_emits_payloads_for_assessor(profile_db, tmp_path):
    profile_db.store_kaizen_role(2002, "assessor")
    page = AsyncMock()
    live_shaped = [
        _summary("u-unfilled-1", fill_action=True),
        _summary("u-unfilled-2", fill_action=True),
        _summary("u-filled", state=None, fill_action=False),
    ]

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=live_shaped),
    ):
        outcome = await supervisor_workflow.run_supervisor_poll(
            2002,
            page=page,
            state_path=tmp_path / "state.json",
            refresh_role=False,
        )

    assert outcome.role == "assessor"
    assert outcome.error is None
    assert {p.ticket_uuid for p in outcome.payloads} == {"u-unfilled-1", "u-unfilled-2"}
    # State persisted so a second poll returns nothing new.
    persisted = TrackedState.load(tmp_path / "state.json")
    assert persisted.is_new_ticket("u-unfilled-1") is False
    assert persisted.is_new_ticket("u-filled") is False


async def test_run_supervisor_poll_does_not_refire_known_tickets(profile_db, tmp_path):
    profile_db.store_kaizen_role(2003, "assessor")
    state_path = tmp_path / "state.json"
    seeded = TrackedState(path=state_path)
    seeded.mark_seen("u-already", status="unfilled")
    seeded.save()

    page = AsyncMock()
    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=[_summary("u-already", fill_action=True)]),
    ):
        outcome = await supervisor_workflow.run_supervisor_poll(
            2003, page=page, state_path=state_path, refresh_role=False
        )

    assert outcome.payloads == []


async def test_run_supervisor_poll_surfaces_poller_errors_without_mutating_state(
    profile_db, tmp_path
):
    profile_db.store_kaizen_role(2004, "assessor")
    state_path = tmp_path / "state.json"
    seeded = TrackedState(path=state_path)
    seeded.mark_seen("u-existing", status="unfilled")
    seeded.save()

    page = AsyncMock()
    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(side_effect=RuntimeError("Kaizen 500")),
    ):
        outcome = await supervisor_workflow.run_supervisor_poll(
            2004, page=page, state_path=state_path, refresh_role=False
        )

    assert outcome.error is not None
    assert "Kaizen 500" in outcome.error
    assert outcome.payloads == []
    # Pre-existing state is preserved even though the poll errored.
    persisted = TrackedState.load(state_path)
    assert persisted.is_new_ticket("u-existing") is False


async def test_run_supervisor_poll_refreshes_role_without_demoting(profile_db, tmp_path):
    profile_db.store_kaizen_role(2005, "assessor")
    page = AsyncMock()
    page.goto = AsyncMock(return_value=None)
    # Simulate a transient MyTimeline empty body → role detector returns "unknown".
    page.evaluate = AsyncMock(return_value="")

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=[]),
    ):
        outcome = await supervisor_workflow.run_supervisor_poll(
            2005, page=page, state_path=tmp_path / "state.json", refresh_role=True
        )

    # Role remained assessor because the probe was inconclusive, not negative.
    assert outcome.role == "assessor"
    assert profile_db.get_kaizen_role(2005) == "assessor"


async def test_run_supervisor_poll_caches_new_assessor_when_first_seen(
    profile_db, tmp_path
):
    # No prior cache, MyTimeline returns the barrier text → cache becomes assessor.
    page = AsyncMock()
    page.goto = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value="You cannot create any events!")

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=[_summary("u-newbie", fill_action=True)]),
    ):
        outcome = await supervisor_workflow.run_supervisor_poll(
            2006, page=page, state_path=tmp_path / "state.json", refresh_role=True
        )

    assert outcome.role == "assessor"
    assert profile_db.get_kaizen_role(2006) == "assessor"
    assert {p.ticket_uuid for p in outcome.payloads} == {"u-newbie"}


# ── safety: no write-side action references in this module ──────────────────


def test_supervisor_workflow_module_never_clicks_write_controls():
    source = inspect.getsource(supervisor_workflow)
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
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source, (
            f"supervisor_workflow source contains forbidden write action: {snippet}"
        )
