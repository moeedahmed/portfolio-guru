"""Tests for backend.filing_attempt_log — the durable filing-attempt log
plus the internal admin report surface.

Acceptance criteria covered:
- log_attempt records success, partial, and failure outcomes.
- Synthetic user 99999999 is recorded but excluded from real-user counts.
- categorise_outcome buckets the common failure shapes correctly.
- format_admin_report shows attempts, saved/success/partial/failures,
  top categories, recent failures, and the synthetic exclusion footer.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import filing_attempt_log as fal


@pytest.fixture
def log_path(tmp_path, monkeypatch) -> pathlib.Path:
    target = tmp_path / "filing-log.ndjson"
    monkeypatch.setenv("PORTFOLIO_GURU_FILING_LOG_PATH", str(target))
    return target


# ─── is_synthetic_user ────────────────────────────────────────────────────


def test_is_synthetic_user_recognises_default_test_id():
    assert fal.is_synthetic_user(99999999) is True


def test_is_synthetic_user_treats_real_ids_as_real():
    assert fal.is_synthetic_user(6912896590) is False


def test_is_synthetic_user_handles_none_and_garbage():
    assert fal.is_synthetic_user(None) is False
    assert fal.is_synthetic_user("not an int") is False


def test_is_synthetic_user_respects_env_override(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_GURU_SYNTHETIC_USER_IDS", "111,222")
    assert fal.is_synthetic_user(111) is True
    assert fal.is_synthetic_user(222) is True
    # Default test id still synthetic alongside env entries.
    assert fal.is_synthetic_user(99999999) is True


# ─── categorise_outcome ───────────────────────────────────────────────────


def test_categorise_outcome_success_no_skipped_is_save_success():
    assert fal.categorise_outcome("success", None, skipped=[], filled=["reflection"]) == "SAVE_SUCCESS"


def test_categorise_outcome_success_with_editable_skip_is_partial_save():
    assert fal.categorise_outcome(
        "success",
        None,
        skipped=["clinical_reasoning"],
        filled=["reflection"],
    ) == "PARTIAL_SAVE"


def test_categorise_outcome_success_with_only_attachment_skip_stays_success():
    assert fal.categorise_outcome(
        "success",
        None,
        skipped=["attachment (unsupported type)"],
        filled=["reflection"],
    ) == "SAVE_SUCCESS"


def test_categorise_outcome_login_failed_marker():
    err = "Could not log in to Kaizen with your saved credentials. Use /settings to reconnect."
    assert fal.categorise_outcome("failed", err, skipped=[], filled=[]) == "LOGIN_FAILED"


def test_categorise_outcome_save_failure_filled_but_not_saved():
    err = "Save button not found or click failed"
    assert fal.categorise_outcome("failed", err, skipped=[], filled=["reflection"]) == "SAVE_FAILURE"


def test_categorise_outcome_save_unverified_is_partial_with_save_marker():
    err = "Save was clicked, but I could not confirm the entry in the activities list."
    assert fal.categorise_outcome("partial", err, skipped=[], filled=["reflection"]) == "SAVE_UNVERIFIED"


def test_categorise_outcome_fill_failure_nothing_filled():
    assert fal.categorise_outcome("failed", "No fields were filled", skipped=[], filled=[]) == "FILL_FAILURE"


def test_categorise_outcome_timeout_and_exception_status_take_precedence():
    assert fal.categorise_outcome("timeout", "anything", skipped=[], filled=[]) == "TIMEOUT"
    assert fal.categorise_outcome("exception", "anything", skipped=[], filled=[]) == "EXCEPTION"


# ─── log_attempt ──────────────────────────────────────────────────────────


def test_log_attempt_writes_record_with_expected_shape(log_path):
    record = fal.log_attempt(
        user_id=12345,
        username="real_doc",
        form_type="CBD",
        status="success",
        filled=["reflection", "clinical_reasoning"],
        skipped=[],
        method="deterministic",
        verified=True,
        portfolio_shape="hst",
    )

    assert record is not None
    assert log_path.exists()
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["user_id"] == 12345
    assert parsed["synthetic"] is False
    assert parsed["form_type"] == "CBD"
    assert parsed["status"] == "success"
    assert parsed["category"] == "SAVE_SUCCESS"
    assert parsed["portfolio_shape"] == "hst"
    assert parsed["filled_count"] == 2
    assert parsed["skipped"] == []
    assert parsed["method"] == "deterministic"
    assert parsed["verified"] is True
    assert parsed["version"] == 2


def test_log_attempt_flags_synthetic_user(log_path):
    fal.log_attempt(
        user_id=99999999,
        username="TestDoctor",
        form_type="CBD",
        status="success",
        filled=["reflection"],
    )
    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["synthetic"] is True


def test_log_attempt_normalises_portfolio_shape(log_path):
    fal.log_attempt(
        user_id=12345,
        username="real_doc",
        form_type="CBD",
        status="success",
        filled=["reflection"],
        portfolio_shape=" SAS ",
    )
    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["portfolio_shape"] == "sas"


def test_log_attempt_partial_records_skipped_keys(log_path):
    fal.log_attempt(
        user_id=12345,
        username="real_doc",
        form_type="DOPS",
        status="partial",
        error="Save was clicked, but I could not confirm the entry in the activities list.",
        filled=["reflection"],
        skipped=["procedure_safety"],
    )
    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["category"] == "SAVE_UNVERIFIED"
    assert parsed["skipped"] == ["procedure_safety"]


def test_log_attempt_failure_with_login_marker(log_path):
    fal.log_attempt(
        user_id=12345,
        username="real_doc",
        form_type="REFLECT_LOG",
        status="failed",
        error="Could not log in to Kaizen with your saved credentials. Use /settings to reconnect.",
        filled=[],
        skipped=[],
    )
    parsed = json.loads(log_path.read_text().splitlines()[0])
    assert parsed["category"] == "LOGIN_FAILED"


def test_log_attempt_swallows_io_errors_and_returns_none(monkeypatch, tmp_path):
    # Point the log at a path whose parent we make unwritable by replacing
    # mkdir to raise.
    target = tmp_path / "unwritable" / "filing-log.ndjson"
    monkeypatch.setenv("PORTFOLIO_GURU_FILING_LOG_PATH", str(target))

    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr("pathlib.Path.mkdir", boom)

    result = fal.log_attempt(
        user_id=12345,
        username="real_doc",
        form_type="CBD",
        status="success",
        filled=["reflection"],
    )
    assert result is None
    assert not target.exists()


# ─── summarise: synthetic exclusion + counts ──────────────────────────────


def _seed_mixed_log(log_path: pathlib.Path) -> None:
    fal.log_attempt(
        user_id=11111, username="a", form_type="CBD",
        status="success", filled=["reflection"], skipped=[],
        method="deterministic", verified=True,
    )
    fal.log_attempt(
        user_id=11111, username="a", form_type="CBD",
        status="partial",
        error="Save was clicked, but I could not confirm the entry in the activities list.",
        filled=["reflection"], skipped=["clinical_reasoning"],
        method="deterministic", verified=False,
    )
    fal.log_attempt(
        user_id=22222, username="b", form_type="DOPS",
        status="failed",
        error="Save button not found or click failed",
        filled=["procedure"], skipped=[],
        method="deterministic", verified=False,
    )
    fal.log_attempt(
        user_id=22222, username="b", form_type="REFLECT_LOG",
        status="failed",
        error="Could not log in to Kaizen with your saved credentials.",
        filled=[], skipped=[],
        method="deterministic", verified=None,
    )
    # Synthetic — must not affect counts when include_synthetic is False.
    fal.log_attempt(
        user_id=99999999, username="TestDoctor", form_type="CBD",
        status="success", filled=["reflection"], skipped=[],
    )
    fal.log_attempt(
        user_id=99999999, username="TestDoctor", form_type="CBD",
        status="failed", error="any", filled=[],
    )


def test_summarise_excludes_synthetic_users_by_default(log_path):
    _seed_mixed_log(log_path)

    summary = fal.summarise(fal.iter_records())
    assert summary["total"] == 4
    assert summary["successes"] == 1
    assert summary["partials"] == 1
    assert summary["failures"] == 2
    assert summary["saved"] == 2
    assert summary["unique_users"] == 2
    assert summary["synthetic_excluded"] == 2
    assert summary["synthetic_total"] == 2
    # Saved rate matches the headline shown to the operator.
    assert round(summary["saved_rate"], 2) == 0.5


def test_summarise_can_include_synthetic_for_debugging(log_path):
    _seed_mixed_log(log_path)
    summary = fal.summarise(fal.iter_records(), include_synthetic=True)
    assert summary["total"] == 6
    assert summary["successes"] == 2
    assert summary["failures"] == 3
    assert summary["synthetic_excluded"] == 0
    assert summary["synthetic_total"] == 2


def test_summarise_categories_capture_top_failure_modes(log_path):
    _seed_mixed_log(log_path)
    summary = fal.summarise(fal.iter_records())
    categories = summary["by_category"]
    assert categories.get("SAVE_SUCCESS") == 1
    assert categories.get("SAVE_UNVERIFIED") == 1
    assert categories.get("SAVE_FAILURE") == 1
    assert categories.get("LOGIN_FAILED") == 1


def test_summarise_groups_outcomes_by_portfolio_shape(log_path):
    fal.log_attempt(
        user_id=101,
        username="sas_fixture",
        form_type="CBD",
        status="partial",
        filled=["reflection", "clinical_reasoning"],
        skipped=["stage"],
        portfolio_shape="sas",
    )
    fal.log_attempt(
        user_id=102,
        username="accs_fixture",
        form_type="CBD",
        status="success",
        filled=["stage_of_training", "reflection"],
        skipped=[],
        portfolio_shape="accs",
    )
    fal.log_attempt(
        user_id=103,
        username="intermediate_fixture",
        form_type="CBD",
        status="success",
        filled=["stage_of_training", "reflection"],
        skipped=[],
        portfolio_shape="intermediate",
    )
    fal.log_attempt(
        user_id=104,
        username="dual_access_fixture",
        form_type="CBD",
        status="success",
        filled=["stage_of_training", "reflection"],
        skipped=[],
        portfolio_shape="accs_intermediate",
    )
    fal.log_attempt(
        user_id=105,
        username="hst_fixture",
        form_type="CBD",
        status="success",
        filled=["stage_of_training", "reflection", "management"],
        skipped=[],
        portfolio_shape="hst",
    )

    summary = fal.summarise(fal.iter_records())
    assert summary["by_shape"]["sas"]["partials"] == 1
    assert summary["by_shape"]["sas"]["by_category"]["PARTIAL_SAVE"] == 1
    assert summary["by_shape"]["sas"]["skipped"] == {"stage": 1}
    assert summary["by_shape"]["accs"]["successes"] == 1
    assert summary["by_shape"]["intermediate"]["successes"] == 1
    assert summary["by_shape"]["accs_intermediate"]["successes"] == 1
    assert summary["by_shape"]["hst"]["successes"] == 1


def test_two_user_attempts_append_distinct_rows_without_double_counting(log_path):
    fal.log_attempt(
        user_id=201,
        username="first_doc",
        form_type="CBD",
        status="success",
        filled=["reflection"],
        skipped=[],
        portfolio_shape="hst",
    )
    fal.log_attempt(
        user_id=202,
        username="second_doc",
        form_type="CBD",
        status="partial",
        filled=["reflection"],
        skipped=["stage"],
        portfolio_shape="sas",
    )

    rows = list(fal.iter_records())
    assert len(rows) == 2
    assert {row["user_id"] for row in rows} == {201, 202}

    summary = fal.summarise(rows)
    assert summary["total"] == 2
    assert summary["unique_users"] == 2
    assert summary["saved"] == 2
    assert summary["by_shape"]["hst"]["successes"] == 1
    assert summary["by_shape"]["sas"]["partials"] == 1


def test_two_user_attempts_preserve_synthetic_exclusion_per_user(log_path):
    fal.log_attempt(
        user_id=99999999,
        username="synthetic_fixture",
        form_type="CBD",
        status="success",
        filled=["reflection"],
        skipped=[],
        portfolio_shape="hst",
    )
    fal.log_attempt(
        user_id=202,
        username="real_doc",
        form_type="CBD",
        status="success",
        filled=["reflection"],
        skipped=[],
        portfolio_shape="intermediate",
    )

    rows = list(fal.iter_records())
    assert len(rows) == 2
    assert sum(1 for row in rows if row["synthetic"]) == 1

    summary = fal.summarise(rows)
    assert summary["total"] == 1
    assert summary["unique_users"] == 1
    assert summary["synthetic_excluded"] == 1
    assert "hst" not in summary["by_shape"]
    assert summary["by_shape"]["intermediate"]["successes"] == 1


def test_summarise_recent_failures_are_newest_first_and_capped(log_path):
    # Write seven failures in order; expect at most 5 in recent_failures,
    # newest first.
    for i in range(7):
        fal.log_attempt(
            user_id=42 + i, username=f"u{i}", form_type="CBD",
            status="failed", error=f"err {i}", filled=[],
        )
    summary = fal.summarise(fal.iter_records(), recent_failure_limit=5)
    assert len(summary["recent_failures"]) == 5
    # iter_records yields in append order, so the newest is the last log
    # entry — recent_failures should start with the highest 'i'.
    assert summary["recent_failures"][0]["error"] == "err 6"


# ─── format_admin_report ──────────────────────────────────────────────────


def test_format_admin_report_handles_empty_log(log_path):
    summary = fal.summarise(fal.iter_records())
    report = fal.format_admin_report(summary)
    assert "No real-user filing attempts" in report


def test_format_admin_report_handles_empty_log_with_synthetic_only(log_path):
    fal.log_attempt(
        user_id=99999999, username="TestDoctor", form_type="CBD",
        status="success", filled=["reflection"],
    )
    report = fal.format_admin_report(fal.summarise(fal.iter_records()))
    assert "No real-user filing attempts" in report
    assert "synthetic" in report.lower()


def test_format_admin_report_includes_counts_categories_and_recent(log_path):
    _seed_mixed_log(log_path)
    report = fal.format_admin_report(fal.summarise(fal.iter_records()))
    assert "Filing reliability" in report
    assert "Attempts: 4" in report
    assert "Unique users: 2" in report
    assert "Saved:    2" in report
    assert "Top categories:" in report
    assert "LOGIN_FAILED" in report
    assert "SAVE_FAILURE" in report
    assert "Top forms:" in report
    assert "CBD" in report
    assert "Recent failures:" in report
    assert "Excluded 2 synthetic" in report


def test_format_admin_report_surfaces_shape_specific_partial_stage(log_path):
    fal.log_attempt(
        user_id=101,
        username="sas_fixture",
        form_type="CBD",
        status="partial",
        filled=["reflection", "clinical_reasoning"],
        skipped=["stage"],
        portfolio_shape="sas",
    )
    fal.log_attempt(
        user_id=102,
        username="accs_fixture",
        form_type="CBD",
        status="success",
        filled=["stage_of_training", "reflection"],
        skipped=[],
        portfolio_shape="accs",
    )

    report = fal.format_admin_report(fal.summarise(fal.iter_records()))
    assert "Shape outcomes:" in report
    assert "sas:" in report
    assert "top PARTIAL_SAVE" in report
    assert "skipped: stage" in report
    assert "accs:" in report
    assert "top SAVE_SUCCESS" in report


def test_bot_log_wrapper_uses_raw_kaizen_role_for_portfolio_shape(monkeypatch):
    import bot as bot_module

    captured = {}

    def fake_log_attempt(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(bot_module, "get_kaizen_role", lambda user_id: "accs_intermediate")
    monkeypatch.setattr(bot_module, "get_training_level", lambda user_id: "INTERMEDIATE")
    monkeypatch.setattr("filing_attempt_log.log_attempt", fake_log_attempt)

    bot_module._log_filing_attempt(
        user_id=123,
        username="dual_access_fixture",
        form_type="CBD",
        status="success",
        filled=["stage_of_training"],
        skipped=[],
    )

    assert captured["portfolio_shape"] == "accs_intermediate"


def test_bot_log_wrapper_falls_back_to_training_level_when_raw_role_missing(monkeypatch):
    import bot as bot_module

    captured = {}

    def fake_log_attempt(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(bot_module, "get_kaizen_role", lambda user_id: None)
    monkeypatch.setattr(bot_module, "get_training_level", lambda user_id: "SAS")
    monkeypatch.setattr("filing_attempt_log.log_attempt", fake_log_attempt)

    bot_module._log_filing_attempt(
        user_id=124,
        username="sas_fixture",
        form_type="CBD",
        status="partial",
        filled=["reflection"],
        skipped=["stage"],
    )

    assert captured["portfolio_shape"] == "SAS"


def test_build_report_reads_default_log_path(log_path):
    _seed_mixed_log(log_path)
    report = fal.build_report()
    assert "Filing reliability" in report
    assert "Attempts: 4" in report


# ─── /filingreport admin command wiring ───────────────────────────────────


@pytest.fixture
def fake_admin_update():
    from unittest.mock import AsyncMock, MagicMock
    import bot as bot_module

    update = MagicMock()
    update.effective_user.id = bot_module.ADMIN_USER_ID
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


@pytest.fixture
def fake_non_admin_update():
    from unittest.mock import AsyncMock, MagicMock

    update = MagicMock()
    update.effective_user.id = 12345  # not the admin id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    return update


@pytest.fixture
def fake_context():
    from unittest.mock import MagicMock

    context = MagicMock()
    context.args = []
    return context


@pytest.mark.asyncio
async def test_filingreport_rejects_non_admin(fake_non_admin_update, fake_context, log_path):
    _seed_mixed_log(log_path)
    import bot as bot_module

    await bot_module.filingreport_command(fake_non_admin_update, fake_context)

    fake_non_admin_update.message.reply_text.assert_awaited_once()
    text = fake_non_admin_update.message.reply_text.await_args.args[0]
    assert "Admin only" in text


@pytest.mark.asyncio
async def test_filingreport_renders_real_user_summary(fake_admin_update, fake_context, log_path):
    _seed_mixed_log(log_path)
    import bot as bot_module

    await bot_module.filingreport_command(fake_admin_update, fake_context)

    fake_admin_update.message.reply_text.assert_awaited_once()
    text = fake_admin_update.message.reply_text.await_args.args[0]
    assert "Filing reliability" in text
    assert "Attempts: 4" in text
    # Real-user-only by default: synthetic attempts excluded from headline.
    assert "Excluded 2 synthetic" in text


@pytest.mark.asyncio
async def test_filingreport_all_includes_synthetic(fake_admin_update, fake_context, log_path):
    _seed_mixed_log(log_path)
    import bot as bot_module

    fake_context.args = ["all"]
    await bot_module.filingreport_command(fake_admin_update, fake_context)

    text = fake_admin_update.message.reply_text.await_args.args[0]
    assert "Attempts: 6" in text
    # When include_synthetic is on, the footer suppresses the exclusion line.
    assert "Excluded" not in text


@pytest.mark.asyncio
async def test_filingreport_empty_log_message(fake_admin_update, fake_context, log_path):
    import bot as bot_module

    # log_path exists but is empty — fixture has env var set, no records written.
    await bot_module.filingreport_command(fake_admin_update, fake_context)

    text = fake_admin_update.message.reply_text.await_args.args[0]
    assert "No real-user filing attempts" in text
