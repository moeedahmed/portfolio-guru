from __future__ import annotations

import os
import subprocess
import sys
import shutil
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
if sys.argv[1].endswith('.py'):
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
elif sys.argv[1] == '-':
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
        names = []
        for selector in selectors:
            name = selector.split('::')[1]
            if name == 'test_e2e_form_variety_ready_draft_to_cancel_journey':
                names.extend(f'{name}[{code}]' for code in (
                    'LAT', 'TEACH', 'QIAT', 'MGMT_ROTA', 'SERIOUS_INC',
                    'PROC_LOG', 'US_CASE', 'FORMAL_COURSE', 'REFLECT_LOG'))
            else:
                names.append(name)
        if os.environ.get('FAKE_MISSING_JOURNEY'): names.pop(0)
        for name in names:
            case = ET.SubElement(suite, 'testcase', name=name, classname='tests.test_e2e')
            if os.environ.get('FAKE_VOICE_SKIP') and 'voice' in name:
                ET.SubElement(case, 'skipped', message='local say/ffmpeg unavailable')
            if os.environ.get('FAKE_SWITCH_SKIP') and 'form_switching' in name:
                ET.SubElement(case, 'skipped', message='missing form switching proof')
            if os.environ.get('FAKE_FORM_SKIP') and '[LAT]' in name:
                ET.SubElement(case, 'skipped', message='missing form variety proof')
        ET.ElementTree(suite).write(report)
''')
    python.chmod(0o755)
    scripts = root / 'scripts'
    scripts.mkdir()
    for name in ('telegram_journey_proof.py', 'telegram_bot_qa.sh'):
        shutil.copy(ROOT / 'scripts' / name, scripts / name)
    (scripts / 'verify_live_runtime.py').write_text('print("synthetic runtime verified")\n')
    (root / 'backend/tests').mkdir()
    (root / 'backend/bot.py').write_text('import extraction\n')
    (root / 'backend/extraction.py').write_text('VALUE = 1\n')
    (root / 'backend/tests/test_e2e.py').write_text('SYNTHETIC_INPUT = "case"\n')
    (root / 'backend/tests/conftest.py').write_text('')
    (root / '.gitignore').write_text('.artifacts/\nbackend/venv/\n__pycache__/\n')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=QA', '-c', 'user.email=qa@example.invalid',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'synthetic fixture'], check=True)
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


def test_wider_mode_selects_exact_sixteen_journeys(wider_qa_harness):
    result = _run_wider_qa(wider_qa_harness)
    assert result.returncode == 0, result.stdout + result.stderr
    live = next(line for line in wider_qa_harness[1].read_text().splitlines() if '::test_e2e_text' in line)
    assert live.count('tests/test_e2e.py::') == 16  # nine parametrised forms + seven other journeys
    assert 'test_e2e_form_variety_ready_draft_to_cancel_journey' in live
    assert 'test_e2e_form_variety_pdf_ready_draft_to_cancel_journey' in live
    for kind in ('text', 'photo', 'voice', 'document'):
        assert f'test_e2e_{kind}_ready_draft_to_cancel_journey' in live
    assert 'test_e2e_settings_read_only_journey' in live
    assert 'test_e2e_form_switching_to_cancel_journey' in live
    assert 'test_e2e_live.py' not in live
    assert 'wider-completeness: PASS' in result.stdout


def test_form_variety_mode_selects_only_ten_journeys(wider_qa_harness):
    result = _run_wider_qa(wider_qa_harness, flags=('--form-variety',))
    assert result.returncode == 0, result.stdout + result.stderr
    live = next(line for line in wider_qa_harness[1].read_text().splitlines() if '::test_e2e_form_variety' in line)
    assert live.count('tests/test_e2e.py::') == 10
    assert 'test_e2e_form_variety_ready_draft_to_cancel_journey' in live
    assert 'test_e2e_form_variety_pdf_ready_draft_to_cancel_journey' in live
    assert 'form-variety-completeness: PASS' in result.stdout


def test_form_switching_mode_selects_only_switching_journey(wider_qa_harness):
    result = _run_wider_qa(wider_qa_harness, flags=('--form-switching',))
    assert result.returncode == 0, result.stdout + result.stderr
    live = next(line for line in wider_qa_harness[1].read_text().splitlines() if '::test_e2e_form_switching' in line)
    assert live.count('tests/test_e2e.py::') == 1
    assert 'test_e2e_form_variety' not in live
    assert 'form-switching-completeness: PASS' in result.stdout


@pytest.mark.parametrize('extra', [{'FAKE_MISSING_JOURNEY': '1'}, {'FAKE_SWITCH_SKIP': '1'}])
def test_form_switching_never_passes_incomplete_proof(wider_qa_harness, extra):
    result = _run_wider_qa(wider_qa_harness, extra=extra, flags=('--form-switching',))
    assert result.returncode == 1
    assert 'form-switching-completeness' in result.stdout


@pytest.mark.parametrize('mode', ['--wider-journeys', '--form-variety', '--form-switching'])
@pytest.mark.parametrize('extra', [
    {'TELEGRAM_BOT_USERNAME': 'portfolio_guru_bot'},
    {'RELEASE_LIVE_TARGET': 'other_bot'},
    {'TELEGRAM_LIVE_ALLOWED_BOTS': 'portfolio_guru_test_bot,portfolio_guru_bot'},
    {'RELEASE_LIVE_ALLOWLIST': 'portfolio_guru_bot'},
])
def test_wider_mode_refuses_non_test_target_before_any_step(wider_qa_harness, extra, mode):
    result = _run_wider_qa(wider_qa_harness, extra=extra, flags=(mode,))
    assert result.returncode == 21
    assert 'Nothing was sent' in result.stderr
    assert not wider_qa_harness[1].exists()


@pytest.mark.parametrize('mode', ['--wider-journeys', '--form-variety', '--form-switching'])
@pytest.mark.parametrize('line', ['TELEGRAM_BOT_USERNAME=portfolio_guru_bot',
                                 'TELEGRAM_LIVE_ALLOWED_BOTS=portfolio_guru_bot',
                                 'WIDER_JOURNEYS=0', 'FORM_VARIETY=0', 'FORM_SWITCHING=0'])
def test_wider_mode_refuses_dotenv_redirection(wider_qa_harness, line, mode):
    (wider_qa_harness[0] / 'backend/.env').write_text(line + '\n')
    result = _run_wider_qa(wider_qa_harness, flags=(mode,))
    assert result.returncode == 21, result.stdout + result.stderr
    assert 'Nothing was sent' in result.stderr
    assert 'pytest' not in wider_qa_harness[1].read_text()


@pytest.mark.parametrize('mode', ['--wider-journeys', '--form-variety', '--form-switching'])
@pytest.mark.parametrize('extra', [{'RUN_LIVE_TELEGRAM': '0', 'REQUIRE_TELEGRAM_LIVE': '0'},
                                 {'FAKE_HAS_TELETHON': '0'}])
def test_wider_mode_requires_live_proof(wider_qa_harness, extra, mode):
    result = _run_wider_qa(wider_qa_harness, extra=extra, flags=(mode,))
    assert result.returncode == 20
    assert '::test_e2e_' not in wider_qa_harness[1].read_text()


@pytest.mark.parametrize('extra', [{'FAKE_VOICE_SKIP': '1'}, {'FAKE_MISSING_JOURNEY': '1'}, {'FAKE_FORM_SKIP': '1'}, {'FAKE_SWITCH_SKIP': '1'}])
def test_wider_mode_never_passes_incomplete_proof(wider_qa_harness, extra):
    result = _run_wider_qa(wider_qa_harness, extra=extra)
    assert result.returncode == 1
    assert 'wider-completeness' in result.stdout


@pytest.mark.parametrize('extra', [{'FAKE_MISSING_JOURNEY': '1'}, {'FAKE_FORM_SKIP': '1'}])
def test_form_variety_mode_never_passes_incomplete_proof(wider_qa_harness, extra):
    result = _run_wider_qa(wider_qa_harness, extra=extra, flags=('--form-variety',))
    assert result.returncode == 1
    assert 'form-variety-completeness' in result.stdout


@pytest.mark.parametrize('flags', [('--wider-journeys', '--focused-release'),
                                 ('--whole-bot', '--wider-journeys'), ('--wider',),
                                 ('--form-variety', '--wider-journeys'),
                                 ('--form-variety', '--focused-release'), ('--form-variety', '--whole-bot')])
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


def _live_calls(harness):
    return [line for line in harness[1].read_text().splitlines() if line.startswith('-m pytest tests/test_e2e.py::')]


def _fixture_commit(root):
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=QA', '-c', 'user.email=qa@example.invalid',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'fixture change'], check=True)


def test_journey_ledger_reuses_without_claiming_fresh_pass(wider_qa_harness):
    assert _run_wider_qa(wider_qa_harness).returncode == 0
    first_calls = _live_calls(wider_qa_harness)
    result = _run_wider_qa(wider_qa_harness, flags=('--changed',))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _live_calls(wider_qa_harness) == first_calls
    assert result.stdout.count('already passed at') == 16
    assert 'fresh PASS' not in result.stdout
    assert '16/16 covered; 0 fresh' in result.stdout


def test_journey_changed_reruns_dependency_but_not_unrelated_file(wider_qa_harness):
    root = wider_qa_harness[0]
    assert _run_wider_qa(wider_qa_harness).returncode == 0
    (root / 'backend/unrelated.py').write_text('IGNORED = 2\n')
    _fixture_commit(root)
    assert _run_wider_qa(wider_qa_harness, flags=('--changed',)).returncode == 0
    assert len(_live_calls(wider_qa_harness)) == 1
    (root / 'backend/extraction.py').write_text('VALUE = 2\n')
    _fixture_commit(root)
    result = _run_wider_qa(wider_qa_harness, flags=('--changed',))
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_live_calls(wider_qa_harness)) == 2
    assert result.stdout.count('fresh PASS') == 16


def test_journey_only_selects_named_ids_and_records_partial_coverage(wider_qa_harness):
    result = _run_wider_qa(wider_qa_harness, flags=('--only', 'text,settings'))
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _live_calls(wider_qa_harness)
    assert len(calls) == 1 and calls[0].count('tests/test_e2e.py::') == 2
    assert 'test_e2e_text' in calls[0] and 'test_e2e_settings' in calls[0]
    assert '2/16 covered' in result.stdout


def test_journey_full_forces_all_even_after_cached_pass(wider_qa_harness):
    assert _run_wider_qa(wider_qa_harness).returncode == 0
    result = _run_wider_qa(wider_qa_harness, flags=('--full',))
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_live_calls(wider_qa_harness)) == 2
    assert _live_calls(wider_qa_harness)[-1].count('tests/test_e2e.py::') == 16
    assert result.stdout.count('fresh PASS') == 16
    assert 'already passed at' not in result.stdout


def test_failed_batch_remembers_completed_passes_and_reruns_only_skip(wider_qa_harness):
    result = _run_wider_qa(wider_qa_harness, extra={'FAKE_VOICE_SKIP': '1'})
    assert result.returncode == 1
    result = _run_wider_qa(wider_qa_harness, flags=('--changed',))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _live_calls(wider_qa_harness)[-1].count('tests/test_e2e.py::') == 1
    assert '::test_e2e_voice' in _live_calls(wider_qa_harness)[-1]
    assert result.stdout.count('already passed at') == 15


def test_dirty_dependency_never_earns_runtime_sha_proof(wider_qa_harness):
    (wider_qa_harness[0] / 'backend/bot.py').write_text('VALUE = 999\n')
    result = _run_wider_qa(wider_qa_harness)
    assert result.returncode == 1
    assert 'differ from committed runtime SHA' in result.stdout
    assert not _live_calls(wider_qa_harness)


@pytest.mark.parametrize('flags', [('--only', 'typo'), ('--only', '')])
def test_unknown_or_empty_journey_ids_never_send(wider_qa_harness, flags):
    result = _run_wider_qa(wider_qa_harness, flags=flags)
    assert result.returncode != 0
    if wider_qa_harness[1].exists(): assert not _live_calls(wider_qa_harness)


@pytest.mark.parametrize('flags', [('--only', 'text', '--full'), ('--changed', '--full'),
                                 ('--focused-release', '--only', 'text'), ('--whole-bot', '--full')])
def test_journey_selection_refuses_ambiguous_or_release_mode(wider_qa_harness, flags):
    result = _run_wider_qa(wider_qa_harness, flags=flags)
    assert result.returncode == 64
    assert not wider_qa_harness[1].exists()


def test_release_coverage_is_checked_against_exact_git_files(wider_qa_harness):
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location('coverage_check', ROOT / 'scripts/telegram_journey_proof.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    root = wider_qa_harness[0]
    assert _run_wider_qa(wider_qa_harness).returncode == 0
    sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    reports = list((root / '.artifacts/telegram-bot-qa').glob('*/journey-coverage.json'))
    report = json.loads(reports[0].read_text())
    checker.validate_coverage(report, root, sha, committed=True)
    # A new SHA containing an unrelated file may reuse complete content proof.
    (root / 'backend/unrelated.py').write_text('VALUE = 42\n')
    _fixture_commit(root)
    later = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    report['sha'] = later
    checker.validate_coverage(report, root, later, committed=True)
    # Matching the SHA label alone cannot hide changed journey dependencies.
    (root / 'backend/extraction.py').write_text('VALUE = 3\n')
    _fixture_commit(root)
    changed = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    report['sha'] = changed
    with pytest.raises(ValueError, match='stale'):
        checker.validate_coverage(report, root, changed, committed=True)


def test_ledger_keeps_proof_for_each_previously_passed_fingerprint(wider_qa_harness):
    root = wider_qa_harness[0]
    assert _run_wider_qa(wider_qa_harness).returncode == 0
    (root / 'backend/extraction.py').write_text('VALUE = 2\n')
    _fixture_commit(root)
    assert _run_wider_qa(wider_qa_harness).returncode == 0
    calls = _live_calls(wider_qa_harness)
    (root / 'backend/extraction.py').write_text('VALUE = 1\n')
    _fixture_commit(root)
    result = _run_wider_qa(wider_qa_harness)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _live_calls(wider_qa_harness) == calls
    assert result.stdout.count('already passed at') == 16
