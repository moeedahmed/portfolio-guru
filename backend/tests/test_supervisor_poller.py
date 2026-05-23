"""Tests for the supervisor polling loop.

The poller fetches the assessor queue, diffs against persisted state, and
returns only newly-seen tickets. It never opens a form or clicks a write
control — that is left to ``assessor_reader.open_ticket_readonly`` after the
supervisor chooses to act on a notification.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

import supervisor_poller
from assessor_mapper import AssessorTicketSummary
from state_tracker import TrackedState


def _summary(
    uuid: str,
    *,
    title: str = "CBD - Case Based Discussion",
    state: str | None = "Pending",
    fill_action: bool | None = None,
) -> AssessorTicketSummary:
    return AssessorTicketSummary(
        title=title,
        href=f"https://kaizenep.com/events/view-section/{uuid}",
        uuid=uuid,
        state=state,
        section_view=True,
        fill_action=fill_action,
    )


def test_diff_against_state_returns_only_new_summaries(tmp_path):
    state = TrackedState(path=tmp_path / "state.json")
    state.mark_seen("seen-1", status="unfilled")

    summaries = [_summary("seen-1"), _summary("new-1"), _summary("new-2")]
    new = supervisor_poller.diff_against_state(summaries, state)

    new_uuids = [s.uuid for s in new]
    assert new_uuids == ["new-1", "new-2"]


def test_diff_against_state_returns_empty_when_all_seen(tmp_path):
    state = TrackedState(path=tmp_path / "state.json")
    state.mark_seen("a", status="unfilled")
    state.mark_seen("b", status="filled")

    new = supervisor_poller.diff_against_state([_summary("a"), _summary("b")], state)

    assert new == []


def test_diff_against_state_skips_summaries_without_uuid(tmp_path):
    state = TrackedState(path=tmp_path / "state.json")
    no_uuid = AssessorTicketSummary(title="orphan", href=None, uuid=None)

    new = supervisor_poller.diff_against_state([no_uuid, _summary("new-1")], state)

    assert [s.uuid for s in new] == ["new-1"]


def test_classify_ticket_status_marks_pending_as_unfilled():
    assert supervisor_poller.classify_ticket_status(_summary("u1", state="Pending")) == "unfilled"
    assert supervisor_poller.classify_ticket_status(_summary("u2", state="In progress")) == "unfilled"


def test_classify_ticket_status_marks_completed_as_filled():
    assert supervisor_poller.classify_ticket_status(_summary("u1", state="Complete")) == "filled"
    assert supervisor_poller.classify_ticket_status(_summary("u2", state="Submitted")) == "filled"


def test_classify_ticket_status_defaults_to_unknown_when_state_missing():
    assert supervisor_poller.classify_ticket_status(_summary("u1", state=None)) == "unknown"


def test_classify_ticket_status_uses_fill_action_when_state_blank():
    assert (
        supervisor_poller.classify_ticket_status(_summary("u1", state=None, fill_action=True))
        == "unfilled"
    )
    assert (
        supervisor_poller.classify_ticket_status(_summary("u2", state="", fill_action=True))
        == "unfilled"
    )
    assert (
        supervisor_poller.classify_ticket_status(_summary("u3", state=None, fill_action=False))
        == "filled"
    )


def test_classify_ticket_status_state_text_wins_over_fill_action():
    # Defensive: if Kaizen ever surfaces both signals and they disagree, the
    # explicit state badge is more trustworthy than a stale Fill in anchor.
    assert (
        supervisor_poller.classify_ticket_status(
            _summary("u1", state="Complete", fill_action=True)
        )
        == "filled"
    )
    assert (
        supervisor_poller.classify_ticket_status(
            _summary("u2", state="Pending", fill_action=False)
        )
        == "unfilled"
    )


async def test_poll_marks_new_tickets_as_seen(tmp_path):
    state = TrackedState(path=tmp_path / "state.json")
    page = AsyncMock()

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=[_summary("new-1"), _summary("new-2", state="Complete")]),
    ):
        result = await supervisor_poller.poll_assessment_queue(state, page=page)

    assert result.error is None
    assert [s.uuid for s in result.new_tickets] == ["new-1", "new-2"]
    assert state.seen_tickets == {"new-1": "unfilled", "new-2": "filled"}


async def test_poll_does_not_refire_known_tickets(tmp_path):
    state = TrackedState(path=tmp_path / "state.json")
    state.mark_seen("seen-1", status="unfilled")
    page = AsyncMock()

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=[_summary("seen-1"), _summary("new-1")]),
    ):
        result = await supervisor_poller.poll_assessment_queue(state, page=page)

    assert [s.uuid for s in result.new_tickets] == ["new-1"]
    assert state.seen_tickets["seen-1"] == "unfilled"
    assert state.seen_tickets["new-1"] == "unfilled"


async def test_poll_returns_error_without_mutating_state(tmp_path):
    state = TrackedState(path=tmp_path / "state.json")
    state.mark_seen("existing", status="unfilled")
    page = AsyncMock()

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(side_effect=RuntimeError("Kaizen 500")),
    ):
        result = await supervisor_poller.poll_assessment_queue(state, page=page)

    assert result.new_tickets == []
    assert result.error is not None
    assert "Kaizen 500" in result.error
    # Existing state preserved, nothing new added
    assert state.seen_tickets == {"existing": "unfilled"}


async def test_poll_filters_to_unfilled_when_requested(tmp_path):
    state = TrackedState(path=tmp_path / "state.json")
    page = AsyncMock()

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=[
            _summary("unfilled-1", state="Pending"),
            _summary("done-1", state="Complete"),
        ]),
    ):
        result = await supervisor_poller.poll_assessment_queue(
            state, page=page, unfilled_only=True
        )

    assert [s.uuid for s in result.new_tickets] == ["unfilled-1"]
    # Both still recorded as seen so we don't re-poll either
    assert set(state.seen_tickets.keys()) == {"unfilled-1", "done-1"}


async def test_poll_persists_state_to_disk(tmp_path):
    path = tmp_path / "state.json"
    state = TrackedState(path=path)
    page = AsyncMock()

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=[_summary("new-1")]),
    ):
        await supervisor_poller.poll_assessment_queue(state, page=page, persist=True)

    assert path.exists()
    reloaded = TrackedState.load(path)
    assert reloaded.is_new_ticket("new-1") is False


async def test_poll_with_live_shaped_null_state_classifies_as_unknown(tmp_path):
    """Live evidence (docs/assessor-mapping/raw-output.json, 2026-05-23) shows the
    assessor queue rows return ``state: null`` because ``.event-section-progress-state``
    is not in the queue markup. The poller still records every UUID so we never
    re-fire, but ``unfilled_only=True`` will currently drop every row because
    they classify as ``unknown``. This test pins that limitation so the queue
    extractor enhancement can be planned against an explicit gap.
    """
    state = TrackedState(path=tmp_path / "state.json")
    page = AsyncMock()
    live_shaped = [
        AssessorTicketSummary(
            title="DOPS - (ST3-ST6 - 2025 update)",
            href="https://kaizenep.com/events/view-section/ec49d6ab-d18e-4fe8-b53d-2a1ec89c41b7",
            uuid="ec49d6ab-d18e-4fe8-b53d-2a1ec89c41b7",
            state=None,
            section_view=True,
        ),
        AssessorTicketSummary(
            title="CBD - Case Based Discussion (2025 update)",
            href="https://kaizenep.com/events/view-section/58fdde9d-8cc7-4c87-888c-2b7e9f2403c1",
            uuid="58fdde9d-8cc7-4c87-888c-2b7e9f2403c1",
            state=None,
            section_view=True,
        ),
    ]

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=live_shaped),
    ):
        seen_all = await supervisor_poller.poll_assessment_queue(state, page=page)
        # Reset state for the unfilled-only probe so we re-evaluate the same rows.
        state.seen_tickets.clear()
        seen_unfilled_only = await supervisor_poller.poll_assessment_queue(
            state, page=page, unfilled_only=True
        )

    assert {s.uuid for s in seen_all.new_tickets} == {
        "ec49d6ab-d18e-4fe8-b53d-2a1ec89c41b7",
        "58fdde9d-8cc7-4c87-888c-2b7e9f2403c1",
    }
    # Gap: with state=None on every row, unfilled_only filters everything out.
    assert seen_unfilled_only.new_tickets == []
    # But every UUID is still recorded as seen so re-poll won't refire either row.
    assert set(state.seen_tickets.keys()) == {
        "ec49d6ab-d18e-4fe8-b53d-2a1ec89c41b7",
        "58fdde9d-8cc7-4c87-888c-2b7e9f2403c1",
    }
    assert all(status == "unknown" for status in state.seen_tickets.values())


async def test_poll_emits_unfilled_when_state_null_and_fill_action_true(tmp_path):
    """Headline fix: Ahmed Mahdi's queue carries ``state=None`` on every row, so
    the textual signal alone left ``unfilled_only=True`` returning nothing. With
    Fill in detection enriching the queue rows the poller now classifies rows
    by ``fill_action`` and emits the actionable ones — while filled/completed
    rows (no Fill in) are correctly recorded as ``filled`` and suppressed.
    """
    state = TrackedState(path=tmp_path / "state.json")
    page = AsyncMock()
    live_shaped = [
        AssessorTicketSummary(
            title="DOPS - (ST3-ST6 - 2025 update)",
            href="https://kaizenep.com/events/view-section/ec49d6ab-d18e-4fe8-b53d-2a1ec89c41b7",
            uuid="ec49d6ab-d18e-4fe8-b53d-2a1ec89c41b7",
            state=None,
            section_view=True,
            fill_action=True,
        ),
        AssessorTicketSummary(
            title="CBD - Case Based Discussion (2025 update)",
            href="https://kaizenep.com/events/view-section/58fdde9d-8cc7-4c87-888c-2b7e9f2403c1",
            uuid="58fdde9d-8cc7-4c87-888c-2b7e9f2403c1",
            state=None,
            section_view=True,
            fill_action=True,
        ),
        AssessorTicketSummary(
            title="DOPS - (ST3-ST6 - 2025 update)",
            href="https://kaizenep.com/events/view-section/50ee434b-1234-1234-1234-12345624f0ce",
            uuid="50ee434b-1234-1234-1234-12345624f0ce",
            state=None,
            section_view=True,
            fill_action=False,
        ),
    ]

    with patch(
        "supervisor_poller.extract_assessment_rows",
        new=AsyncMock(return_value=live_shaped),
    ):
        result = await supervisor_poller.poll_assessment_queue(
            state, page=page, unfilled_only=True
        )

    emitted = {s.uuid for s in result.new_tickets}
    assert emitted == {
        "ec49d6ab-d18e-4fe8-b53d-2a1ec89c41b7",
        "58fdde9d-8cc7-4c87-888c-2b7e9f2403c1",
    }
    # All three are recorded so a re-poll won't refire any of them — including
    # the filled DOPS, which is suppressed from the emit list but still tracked.
    assert state.seen_tickets == {
        "ec49d6ab-d18e-4fe8-b53d-2a1ec89c41b7": "unfilled",
        "58fdde9d-8cc7-4c87-888c-2b7e9f2403c1": "unfilled",
        "50ee434b-1234-1234-1234-12345624f0ce": "filled",
    }


def test_poller_module_never_clicks_write_controls():
    source = inspect.getsource(supervisor_poller)
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
        assert snippet not in source, f"Poller source contains forbidden write action: {snippet}"
