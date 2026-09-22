from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOGFOOD = ROOT / "scripts" / "dogfood_smoke.sh"
SUMMARY_POLICY = ROOT / "scripts" / "dogfood_summary_policy.sh"
TELEGRAM_QA = ROOT / "scripts" / "telegram_bot_qa.sh"


def test_strict_dogfood_no_record_cannot_pass():
    result = subprocess.run(
        ["bash", str(DOGFOOD), "--no-record", "--strict-release"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "strict" in (result.stdout + result.stderr).lower()


def test_strict_summary_policy_requires_all_15_pass_and_zero_skip_fail():
    assert subprocess.run(["bash", str(SUMMARY_POLICY), "1", "1", "15", "0", "0"]).returncode == 0
    invalid = (("1", "1", "14", "0", "0"), ("1", "1", "15", "1", "0"), ("1", "1", "15", "0", "1"), ("1", "0", "15", "0", "0"))
    for values in invalid:
        assert subprocess.run(["bash", str(SUMMARY_POLICY), *values]).returncode != 0


def test_telegram_focused_selector_runs_only_representative_live_case(tmp_path):
    root = tmp_path / "repo"
    backend = root / "backend"
    python = backend / "venv" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    log = tmp_path / "python.log"
    python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {log}\n"
        "if [[ \"$*\" == '-' ]]; then printf '1\\n'; fi\n"
        "exit 0\n"
    )
    python.chmod(0o755)
    env = {
        **os.environ,
        "PORTFOLIO_GURU_APP_DIR": str(root),
        "RUN_LIVE_TELEGRAM": "1",
        "REQUIRE_TELEGRAM_LIVE": "1",
        "TELEGRAM_LIVE_APPROVED": "portfolio-guru-live-qa-approved",
        "TELETHON_SESSION": "test",
        "TELEGRAM_API_ID": "1",
        "TELEGRAM_API_HASH": "test",
    }
    result = subprocess.run(["bash", str(TELEGRAM_QA), "--focused-release"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text()
    assert "tests/test_e2e.py::test_e2e_cbd_ready_draft_to_cancel_journey" in calls
    live_call = next(line for line in calls.splitlines() if "test_e2e_cbd_ready_draft_to_cancel_journey" in line)
    assert "test_e2e_live.py" not in live_call
    assert live_call.endswith("-q -m e2e")


def _fake_python_reporting_telethon_env(tmp_path, *, has_telethon_env: bool):
    """A fake `venv/bin/python3` for a fake app root: succeeds for every real
    pytest invocation, and answers the harness's `has_telethon_env()` probe
    (run as `python3 -` with the check piped on stdin) with the given verdict."""
    root = tmp_path / "repo"
    backend = root / "backend"
    python = backend / "venv" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    log = tmp_path / "python.log"
    verdict = "1" if has_telethon_env else "0"
    python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {log}\n"
        f"if [[ \"$*\" == '-' ]]; then printf '{verdict}\\n'; fi\n"
        "exit 0\n"
    )
    python.chmod(0o755)
    return root, log


def test_telegram_focused_release_fails_closed_when_credentials_missing(tmp_path):
    """Focused release must fail closed (non-zero) rather than exit-0 skip
    when live approval/credentials are unavailable — it is required release
    proof, not optional coverage."""
    root, log = _fake_python_reporting_telethon_env(tmp_path, has_telethon_env=False)
    env = {
        **os.environ,
        "PORTFOLIO_GURU_APP_DIR": str(root),
    }
    for missing_var in (
        "RUN_LIVE_TELEGRAM", "REQUIRE_TELEGRAM_LIVE", "TELEGRAM_LIVE_APPROVED",
        "TELETHON_SESSION", "TELEGRAM_API_ID", "TELEGRAM_API_HASH",
    ):
        env.pop(missing_var, None)

    result = subprocess.run(["bash", str(TELEGRAM_QA), "--focused-release"], env=env, capture_output=True, text=True)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "required" in (result.stdout + result.stderr).lower()
    assert "live-telegram-focused" not in log.read_text()


def test_telegram_focused_release_fails_closed_when_run_live_explicitly_disabled(tmp_path):
    """`RUN_LIVE_TELEGRAM=0` must not silently skip the required focused live
    proof either — an explicit disable is still a failure to produce proof."""
    root, log = _fake_python_reporting_telethon_env(tmp_path, has_telethon_env=True)
    env = {
        **os.environ,
        "PORTFOLIO_GURU_APP_DIR": str(root),
        "RUN_LIVE_TELEGRAM": "0",
        "TELEGRAM_LIVE_APPROVED": "portfolio-guru-live-qa-approved",
        "TELETHON_SESSION": "test",
        "TELEGRAM_API_ID": "1",
        "TELEGRAM_API_HASH": "test",
    }

    result = subprocess.run(["bash", str(TELEGRAM_QA), "--focused-release"], env=env, capture_output=True, text=True)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "required" in (result.stdout + result.stderr).lower()
    assert "live-telegram-focused" not in log.read_text()
