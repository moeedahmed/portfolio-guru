from __future__ import annotations

import os
import subprocess
import sys
import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOGFOOD = ROOT / "scripts" / "dogfood_smoke.sh"
SUMMARY_POLICY = ROOT / "scripts" / "dogfood_summary_policy.sh"
TELEGRAM_QA = ROOT / "scripts" / "telegram_bot_qa.sh"


@pytest.fixture
def wider_qa_harness(tmp_path):
    root = tmp_path / "repo"
    python = root / "backend/venv/bin/python3"
    python.parent.mkdir(parents=True)
    log = tmp_path / "python.log"
    # All pytest calls are stubs. Dotenv and completeness execute their actual
    # standard-library code; no backend, transport or provider is imported.
    python.write_text(f"#!{sys.executable}\n" + '''import os, sys
from pathlib import Path
import xml.etree.ElementTree as ET
with open(os.environ['FAKE_QA_LOG'], 'a') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1] == '-':
    code = sys.stdin.read()
    if 'from tests.telegram_live_harness import has_telethon_env' in code:
        print(os.environ.get('FAKE_HAS_TELETHON', '1'))
    else:
        sys.argv = sys.argv[1:]
        exec(compile(code, '<qa-stdin>', 'exec'))
else:
    report = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--junitxml=')), None)
    if report:
        suite = ET.Element('testsuite')
        selectors = [arg for arg in sys.argv if '::test_e2e_' in arg]
        if os.environ.get('FAKE_MISSING_JOURNEY'): selectors.pop()
        for selector in selectors:
            case = ET.SubElement(suite, 'testcase', name=selector.split('::')[1])
            if os.environ.get('FAKE_VOICE_SKIP') and 'voice' in selector:
                ET.SubElement(case, 'skipped', message='local say/ffmpeg unavailable')
        ET.ElementTree(suite).write(report)
''')
    python.chmod(0o755)
    env = {
        "PATH": os.environ["PATH"], "HOME": str(tmp_path),
        "PORTFOLIO_GURU_APP_DIR": str(root), "FAKE_QA_LOG": str(log),
        "TELEGRAM_BOT_USERNAME": "portfolio_guru_test_bot",
        "TELEGRAM_LIVE_ALLOWED_BOTS": "portfolio_guru_test_bot",
        "TELEGRAM_LIVE_APPROVED": "portfolio-guru-live-qa-approved",
        "RUN_LIVE_TELEGRAM": "1",
    }
    return root, log, env


def _run_wider_qa(harness, *, extra=None, flags=("--wider-journeys",)):
    return subprocess.run(["bash", str(TELEGRAM_QA), *flags], env={**harness[2], **(extra or {})},
                          capture_output=True, text=True, timeout=10)


def test_wider_mode_selects_exact_five_journeys(wider_qa_harness):
    result = _run_wider_qa(wider_qa_harness)
    assert result.returncode == 0, result.stdout + result.stderr
    live = next(line for line in wider_qa_harness[1].read_text().splitlines() if '::test_e2e_cbd' in line)
    assert live.count('tests/test_e2e.py::') == 5
    for kind in ('cbd', 'photo', 'voice', 'document'):
        assert f'test_e2e_{kind}_ready_draft_to_cancel_journey' in live
    assert 'test_e2e_settings_read_only_journey' in live
    assert 'test_e2e_live.py' not in live
    assert 'wider-completeness: PASS' in result.stdout


@pytest.mark.parametrize('extra', [
    {'TELEGRAM_BOT_USERNAME': 'portfolio_guru_bot'},
    {'RELEASE_LIVE_TARGET': 'other_bot'},
    {'TELEGRAM_LIVE_ALLOWED_BOTS': 'portfolio_guru_test_bot,portfolio_guru_bot'},
    {'RELEASE_LIVE_ALLOWLIST': 'portfolio_guru_bot'},
])
def test_wider_mode_refuses_non_test_target_before_any_step(wider_qa_harness, extra):
    result = _run_wider_qa(wider_qa_harness, extra=extra)
    assert result.returncode == 21
    assert 'Nothing was sent' in result.stderr
    assert not wider_qa_harness[1].exists()


@pytest.mark.parametrize('line', ['TELEGRAM_BOT_USERNAME=portfolio_guru_bot',
                                 'TELEGRAM_LIVE_ALLOWED_BOTS=portfolio_guru_bot', 'WIDER_JOURNEYS=0'])
def test_wider_mode_refuses_dotenv_redirection(wider_qa_harness, line):
    (wider_qa_harness[0] / 'backend/.env').write_text(line + '\n')
    result = _run_wider_qa(wider_qa_harness)
    assert result.returncode == 21, result.stdout + result.stderr
    assert 'Nothing was sent' in result.stderr
    assert 'pytest' not in wider_qa_harness[1].read_text()


@pytest.mark.parametrize('extra', [{'RUN_LIVE_TELEGRAM': '0', 'REQUIRE_TELEGRAM_LIVE': '0'},
                                 {'FAKE_HAS_TELETHON': '0'}])
def test_wider_mode_requires_live_proof(wider_qa_harness, extra):
    result = _run_wider_qa(wider_qa_harness, extra=extra)
    assert result.returncode == 20
    assert '::test_e2e_' not in wider_qa_harness[1].read_text()


@pytest.mark.parametrize('extra', [{'FAKE_VOICE_SKIP': '1'}, {'FAKE_MISSING_JOURNEY': '1'}])
def test_wider_mode_never_passes_incomplete_proof(wider_qa_harness, extra):
    result = _run_wider_qa(wider_qa_harness, extra=extra)
    assert result.returncode == 1
    assert 'wider-completeness' in result.stdout


@pytest.mark.parametrize('flags', [('--wider-journeys', '--focused-release'),
                                 ('--whole-bot', '--wider-journeys'), ('--wider',)])
def test_wider_mode_rejects_conflicting_or_unknown_flags(wider_qa_harness, flags):
    result = _run_wider_qa(wider_qa_harness, flags=flags)
    assert result.returncode == 64
    assert not wider_qa_harness[1].exists()


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


@pytest.mark.parametrize("approved", [False, True])
def test_whole_bot_never_accepts_missing_proof(tmp_path, approved):
    root, log = _fake_python_reporting_telethon_env(tmp_path, has_telethon_env=True)
    import sys
    python = root / "backend/venv/bin/python3"
    original = python.read_text()
    python.write_text(original.replace("exit 0", 'if [[ "$*" == *"tests.whole_bot_aggregate"* ]]; then echo "PENDING: aggregate proof missing"; exit 20; fi\nexit 0'))
    env = {**os.environ, "PORTFOLIO_GURU_APP_DIR": str(root), "RUN_LIVE_TELEGRAM": "1"}
    env["TELEGRAM_LIVE_APPROVED"] = "portfolio-guru-live-qa-approved" if approved else ""
    result = subprocess.run(["bash", str(TELEGRAM_QA), "--whole-bot"], env=env, capture_output=True, text=True)
    assert result.returncode == 20
    assert "PENDING" in result.stdout + result.stderr
    if approved:
        assert "tests.whole_bot_aggregate" in log.read_text()
        assert "test_e2e_cbd" not in log.read_text()
