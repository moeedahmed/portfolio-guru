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


def test_log_event_flags_operator_user(log_path):
    fm.log_event(user_id=6912896590, username="operator", event="case_started", metadata={})
    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["operator"] is True
    assert parsed["synthetic"] is False


def test_summarise_excludes_operator_traffic_by_default(log_path):
    fm.log_event(user_id=111, username=None, event="case_started", metadata={})
    fm.log_event(user_id=6912896590, username="operator", event="case_started", metadata={})
    summary = fm.summarise(fm.iter_records(log_path))
    assert summary["unique_users"] == 1
    assert summary["operator_excluded"] == 1
    assert summary["operator_total"] == 1


def test_summarise_can_include_operator_for_debugging(log_path):
    fm.log_event(user_id=111, username=None, event="case_started", metadata={})
    fm.log_event(user_id=6912896590, username="operator", event="case_started", metadata={})
    summary = fm.summarise(fm.iter_records(log_path), include_operator=True)
    assert summary["unique_users"] == 2
    assert summary["operator_excluded"] == 0
    assert summary["operator_total"] == 1


def test_summarise_derives_operator_for_legacy_records_without_operator_flag():
    records = [
        {
            "user_id": 111,
            "synthetic": False,
            "event": "case_started",
            "metadata": {},
        },
        {
            "user_id": 6912896590,
            "synthetic": False,
            "event": "case_started",
            "metadata": {},
        },
    ]

    summary = fm.summarise(records)
    assert summary["total"] == 1
    assert summary["unique_users"] == 1
    assert summary["operator_excluded"] == 1
    assert summary["operator_total"] == 1

    debug_summary = fm.summarise(records, include_operator=True)
    assert debug_summary["total"] == 2
    assert debug_summary["unique_users"] == 2
    assert debug_summary["operator_excluded"] == 0
    assert debug_summary["operator_total"] == 1


def test_summarise_respects_explicit_operator_flag_over_user_id():
    records = [
        {
            "user_id": 6912896590,
            "operator": False,
            "synthetic": False,
            "event": "case_started",
            "metadata": {},
        },
        {
            "user_id": 222,
            "operator": True,
            "synthetic": False,
            "event": "case_started",
            "metadata": {},
        },
    ]

    summary = fm.summarise(records)
    assert summary["total"] == 1
    assert summary["unique_users"] == 1
    assert summary["operator_excluded"] == 1
    assert summary["operator_total"] == 1


def test_summarise_null_identity_never_counts_as_completed_or_repeat(log_path):
    fm.log_event(user_id=111, username=None, event="case_started", metadata={})
    fm.log_event(user_id=111, username=None, event="draft_previewed", metadata={})
    fm.log_event(user_id=111, username=None, event="draft_saved", metadata={})
    # Unattributed events — no caller-supplied identity.
    fm.log_event(user_id=None, username=None, event="draft_previewed", metadata={})
    fm.log_event(user_id=None, username=None, event="draft_saved", metadata={})

    summary = fm.summarise(fm.iter_records(log_path))
    assert summary["unique_users"] == 1
    assert summary["completed_users"] == 1
    assert summary["repeat_users"] == 0
    assert summary["unattributed_total"] == 2


def test_format_admin_report_separates_synthetic_operator_and_unattributed(log_path):
    fm.log_event(user_id=111, username=None, event="case_started", metadata={})
    fm.log_event(user_id=99999999, username="synthetic", event="case_started", metadata={})
    fm.log_event(user_id=6912896590, username="operator", event="case_started", metadata={})
    fm.log_event(user_id=None, username=None, event="case_started", metadata={})

    report = fm.build_report(log_path=log_path)
    assert "1 synthetic test event" in report
    assert "1 operator/dogfood event" in report
    assert "1 legacy/unattributed event" in report
    assert "no user identity" in report
    assert "6912896590" not in report


def test_format_admin_report_hides_legacy_operator_user_id():
    records = [
        {
            "user_id": 6912896590,
            "synthetic": False,
            "event": "case_started",
            "metadata": {},
        }
    ]

    report = fm.format_admin_report(fm.summarise(records))
    assert "No real-user funnel events" in report
    assert "1 operator/dogfood event" in report
    assert "6912896590" not in report


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
