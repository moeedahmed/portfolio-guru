"""Tests for the periodic supervisor poll tick.

The scheduler exists so the bot can react to inbound assessor tickets
without the supervisor having to open Telegram first. Its safety
properties matter much more than its happy path:

* When there are zero assessor users, the tick is a silent no-op — no
  log spam, no Playwright session, no Kaizen calls.
* When there is an assessor user but no credentials cached, the tick
  skips that user; it never tries to drive a live login.
* When the CDP session at ``localhost:18800`` is unavailable, the tick
  logs a warning and returns; trainee users continue to be served by
  the rest of the bot.
* When a per-user poll raises, the tick logs and moves on — one bad
  user must not poison the others.

These tests stub the Playwright connection, the role-detector, and the
queue extractor so the scheduler logic can be exercised without a live
browser. The dispatch helper is also stubbed; that surface has its own
direct tests in ``test_supervisor_bot``.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import supervisor_scheduler
from assessor_mapper import AssessorTicketSummary


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


@pytest.fixture
def patched_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor_scheduler, "SUPERVISOR_STATE_DIR", tmp_path)
    return tmp_path


def _summary(uuid: str, *, fill_action: bool | None = True) -> AssessorTicketSummary:
    return AssessorTicketSummary(
        title="CBD - Case Based Discussion (2025 update)",
        href=f"https://kaizenep.com/events/view-section/{uuid}",
        uuid=uuid,
        state=None,
        section_view=True,
        fill_action=fill_action,
    )


# ── eligibility / inert paths ───────────────────────────────────────────────


async def test_tick_is_silent_noop_when_no_assessor_users(profile_db, patched_state_dir):
    # Populate trainee-only rows.
    profile_db.store_kaizen_role(11, "trainee")
    profile_db.store_kaizen_role(12, "trainee")

    bot = AsyncMock()
    connect = AsyncMock()  # must NOT be called

    await supervisor_scheduler.supervisor_poll_tick(
        bot=bot,
        connect_cdp=connect,
        notify=AsyncMock(),
    )

    connect.assert_not_awaited()
    bot.send_message.assert_not_called()


async def test_tick_skips_assessor_user_with_no_credentials(profile_db, patched_state_dir):
    profile_db.store_kaizen_role(21, "assessor")
    # No store_credentials → has_credentials returns False.
    connect = AsyncMock()
    notify = AsyncMock()

    with patch.object(supervisor_scheduler, "has_credentials", return_value=False):
        await supervisor_scheduler.supervisor_poll_tick(
            bot=AsyncMock(),
            connect_cdp=connect,
            notify=notify,
        )

    connect.assert_not_awaited()
    notify.assert_not_awaited()


async def test_tick_swallows_cdp_connection_failure(profile_db, patched_state_dir):
    profile_db.store_kaizen_role(31, "assessor")
    failing_connect = AsyncMock(side_effect=RuntimeError("CDP refused"))
    notify = AsyncMock()

    with patch.object(supervisor_scheduler, "has_credentials", return_value=True):
        await supervisor_scheduler.supervisor_poll_tick(
            bot=AsyncMock(),
            connect_cdp=failing_connect,
            notify=notify,
        )

    failing_connect.assert_awaited_once()
    notify.assert_not_awaited()


# ── happy path: run_supervisor_poll → dispatch ──────────────────────────────


async def test_tick_dispatches_one_notification_per_payload(profile_db, patched_state_dir):
    profile_db.store_kaizen_role(41, "assessor")

    page = AsyncMock()
    connect = AsyncMock(return_value=page)
    notify = AsyncMock()

    with patch.object(supervisor_scheduler, "has_credentials", return_value=True), \
         patch(
             "supervisor_poller.extract_assessment_rows",
             new=AsyncMock(return_value=[_summary("u-1"), _summary("u-2")]),
         ):
        await supervisor_scheduler.supervisor_poll_tick(
            bot=AsyncMock(),
            connect_cdp=connect,
            notify=notify,
        )

    connect.assert_awaited_once()
    assert notify.await_count == 2
    user_ids = {call.kwargs.get("telegram_user_id") or call.args[1] for call in notify.await_args_list}
    assert user_ids == {41}


async def test_tick_continues_when_one_user_poll_errors(profile_db, patched_state_dir):
    profile_db.store_kaizen_role(51, "assessor")
    profile_db.store_kaizen_role(52, "assessor")

    page = AsyncMock()
    connect = AsyncMock(return_value=page)
    notify = AsyncMock()

    # Patch run_supervisor_poll directly so we can vary outcome per user.
    async def _poll(user_id, *, page, state_path, **kwargs):
        if user_id == 51:
            raise RuntimeError("Kaizen 502")
        from supervisor_workflow import SupervisorNotificationPayload, SupervisorPollOutcome
        payload = SupervisorNotificationPayload(
            ticket_uuid="u-from-52",
            ticket_url="https://kaizenep.com/events/view-section/u-from-52",
            form_type="CBD",
            redacted_title="CBD",
            status="unfilled",
        )
        return SupervisorPollOutcome(role="assessor", payloads=[payload])

    with patch.object(supervisor_scheduler, "has_credentials", return_value=True), \
         patch("supervisor_scheduler.supervisor_workflow.run_supervisor_poll", new=AsyncMock(side_effect=_poll)):
        await supervisor_scheduler.supervisor_poll_tick(
            bot=AsyncMock(),
            connect_cdp=connect,
            notify=notify,
        )

    # User 52's payload must still be dispatched even though user 51 errored.
    assert notify.await_count == 1


async def test_tick_uses_per_user_state_path(profile_db, patched_state_dir):
    profile_db.store_kaizen_role(61, "assessor")
    profile_db.store_kaizen_role(62, "assessor")

    seen_paths: list[Path] = []

    async def _poll(user_id, *, page, state_path, **kwargs):
        from supervisor_workflow import SupervisorPollOutcome
        seen_paths.append(Path(state_path))
        return SupervisorPollOutcome(role="assessor", payloads=[])

    with patch.object(supervisor_scheduler, "has_credentials", return_value=True), \
         patch("supervisor_scheduler.supervisor_workflow.run_supervisor_poll", new=AsyncMock(side_effect=_poll)):
        await supervisor_scheduler.supervisor_poll_tick(
            bot=AsyncMock(),
            connect_cdp=AsyncMock(return_value=AsyncMock()),
            notify=AsyncMock(),
        )

    # Paths are per-user, distinct, and live under the patched state dir.
    assert len(set(seen_paths)) == 2
    for p in seen_paths:
        assert p.parent == patched_state_dir
        assert "61" in p.name or "62" in p.name


async def test_tick_passes_refresh_role_false_by_default(profile_db, patched_state_dir):
    """The MyTimeline probe runs at login; the periodic tick must not re-fire it."""
    profile_db.store_kaizen_role(71, "assessor")

    captured_kwargs: dict = {}

    async def _poll(user_id, *, page, state_path, **kwargs):
        captured_kwargs.update(kwargs)
        from supervisor_workflow import SupervisorPollOutcome
        return SupervisorPollOutcome(role="assessor", payloads=[])

    with patch.object(supervisor_scheduler, "has_credentials", return_value=True), \
         patch("supervisor_scheduler.supervisor_workflow.run_supervisor_poll", new=AsyncMock(side_effect=_poll)):
        await supervisor_scheduler.supervisor_poll_tick(
            bot=AsyncMock(),
            connect_cdp=AsyncMock(return_value=AsyncMock()),
            notify=AsyncMock(),
        )

    assert captured_kwargs.get("refresh_role") is False


# ── source scan: no write-side Kaizen actions ───────────────────────────────


def test_scheduler_module_never_clicks_write_controls():
    """The scheduler must never reference any Kaizen write-side action."""
    source = inspect.getsource(supervisor_scheduler)
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
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source, (
            f"supervisor_scheduler source contains forbidden write action: {snippet}"
        )
