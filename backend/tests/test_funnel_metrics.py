"""Tests for the PHI-free Telegram funnel metrics log and admin report."""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import funnel_metrics as fm


@pytest.fixture
def log_path(tmp_path, monkeypatch) -> pathlib.Path:
    target = tmp_path / "funnel-events.ndjson"
    monkeypatch.setenv("PORTFOLIO_GURU_FUNNEL_LOG_PATH", str(target))
    return target


def test_safe_metadata_strips_unknown_keys():
    assert fm.safe_metadata(
        {
            "source": "voice",
            "form_type": "CBD",
            "case_text": "clinical content",
            "password": "secret",
        }
    ) == {"source": "voice", "form_type": "CBD"}


def test_log_event_writes_phi_free_record(log_path):
    record = fm.log_event(
        user_id=12345,
        username="doctor",
        event="case_started",
        metadata={"source": "text", "case_text": "clinical content"},
        session_id="session-1",
    )

    assert record is not None
    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["user_id"] == 12345
    assert parsed["synthetic"] is False
    assert parsed["event"] == "case_started"
    assert parsed["metadata"] == {"source": "text"}
    assert parsed["session_id"] == "session-1"


def test_summarise_answers_completed_and_repeat_users(log_path):
    for event in ("case_started", "recommendation_shown", "form_chosen", "draft_previewed", "save_attempted", "draft_saved"):
        fm.log_event(user_id=111, username=None, event=event, metadata={})
    for event in ("case_started", "draft_previewed", "draft_saved", "draft_saved"):
        fm.log_event(user_id=222, username=None, event=event, metadata={})
    fm.log_event(user_id=333, username=None, event="case_started", metadata={})
    fm.log_event(user_id=99999999, username="synthetic", event="draft_saved", metadata={})

    summary = fm.summarise(fm.iter_records(log_path))

    assert summary["unique_users"] == 3
    assert summary["completed_users"] == 2
    assert summary["completed_saves"] == 3
    assert summary["repeat_users"] == 1
    assert summary["synthetic_excluded"] == 1


def test_format_admin_report_includes_core_funnel(log_path):
    fm.log_event(user_id=111, username=None, event="case_started", metadata={})
    fm.log_event(user_id=111, username=None, event="draft_previewed", metadata={"form_type": "CBD"})
    fm.log_event(user_id=111, username=None, event="draft_saved", metadata={"form_type": "CBD"})

    report = fm.build_report(log_path=log_path)

    assert "Telegram funnel" in report
    assert "Completed preview" in report
    assert "Repeat users" in report
    assert "Case started" in report


def test_bot_track_funnel_event_appends_durable_metric(log_path):
    import bot as bot_module

    context = SimpleNamespace(user_data={"_audit_user_id": 12345})

    bot_module._track_funnel_event(
        context,
        "draft_previewed",
        form_type="CBD",
        has_missing=False,
        case_text="should not be logged",
    )

    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["event"] == "draft_previewed"
    assert parsed["user_id"] == 12345
    assert parsed["metadata"] == {"form_type": "CBD", "has_missing": False}


@pytest.fixture
def fake_admin_update():
    import bot as bot_module

    update = MagicMock()
    update.effective_user.id = bot_module.ADMIN_USER_ID
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


@pytest.fixture
def fake_non_admin_update():
    update = MagicMock()
    update.effective_user.id = 12345
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


@pytest.fixture
def fake_context():
    context = MagicMock()
    context.args = []
    return context


@pytest.mark.asyncio
async def test_funnelreport_rejects_non_admin(fake_non_admin_update, fake_context, log_path):
    import bot as bot_module

    await bot_module.funnelreport_command(fake_non_admin_update, fake_context)

    text = fake_non_admin_update.message.reply_text.await_args.args[0]
    assert "Admin only" in text


@pytest.mark.asyncio
async def test_funnelreport_renders_real_user_summary(fake_admin_update, fake_context, log_path):
    import bot as bot_module

    fm.log_event(user_id=111, username=None, event="case_started", metadata={})
    fm.log_event(user_id=111, username=None, event="draft_previewed", metadata={})
    fm.log_event(user_id=111, username=None, event="draft_saved", metadata={})
    fm.log_event(user_id=99999999, username="synthetic", event="draft_saved", metadata={})

    await bot_module.funnelreport_command(fake_admin_update, fake_context)

    text = fake_admin_update.message.reply_text.await_args.args[0]
    assert "Telegram funnel" in text
    assert "Completed preview" in text
    assert "Excluded 1 synthetic" in text


@pytest.mark.asyncio
async def test_alert_filing_failure_pages_operator(monkeypatch):
    import bot as bot_module
    import ops_alert

    calls = []

    async def fake_notify_operator(bot_obj, text, *, key="generic", cooldown=300):
        calls.append({"bot": bot_obj, "text": text, "key": key, "cooldown": cooldown})

    monkeypatch.setattr(ops_alert, "notify_operator", fake_notify_operator)
    context = SimpleNamespace(bot=object())

    await bot_module._alert_filing_failure(
        context,
        form_type="CBD",
        status="failed",
        reason="SAVE_FAILURE",
        user_id=12345,
    )

    assert len(calls) == 1
    assert "Kaizen filing failed" in calls[0]["text"]
    assert "SAVE_FAILURE" in calls[0]["text"]
    assert calls[0]["key"] == "kaizen_filing_failure:CBD:failed:SAVE_FAILURE"
    assert calls[0]["cooldown"] == 900
