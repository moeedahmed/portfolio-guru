"""Deterministic staging deploy/receipt tests; every external command is stubbed."""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHA = 'a' * 40
PREV = 'b' * 40
SPEC = importlib.util.spec_from_file_location('staging_proof', ROOT / 'scripts/staging_proof.py')
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)


def executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


def test_receipts_reset_pass_and_human_approval(monkeypatch, tmp_path):
    monkeypatch.setenv('PORTFOLIO_GURU_STAGING_PROOF_DIR', str(tmp_path))
    assert proof.main(['gate', '--sha', SHA, '--risk', 'telegram']) == 1
    # Internal changes never need the test bot, even with no receipt at all.
    assert proof.main(['gate', '--sha', SHA, '--risk', 'internal']) == 0
    assert proof.main(['deploy', '--sha', SHA, '--result', 'pass']) == 0
    assert proof.main(['gate', '--sha', SHA, '--risk', 'telegram']) == 1
    coverage = tmp_path / 'coverage.json'
    fingerprints = {key: key + '-digest' for key in proof.journeys.JOURNEYS}
    monkeypatch.setattr(proof.journeys, 'fingerprints', lambda *a, **kw: fingerprints)
    coverage.write_text(json.dumps({'schema': 1, 'sha': SHA, 'target': proof.TARGET, 'journeys': {
        key: {'fingerprint': value, 'sha': PREV, 'target': proof.TARGET, 'status': 'reused'}
        for key, value in fingerprints.items()}}))
    assert proof.main(['automated', '--sha', SHA, '--result', 'pass', '--coverage', str(coverage)]) == 0
    assert proof.main(['gate', '--sha', SHA, '--risk', 'internal']) == 0
    assert proof.main(['gate', '--sha', SHA, '--risk', 'telegram']) == 1
    assert proof.main(['approve', '--sha', SHA, '--note', 'Moeed tapped Ship']) == 0
    assert proof.main(['gate', '--sha', SHA, '--risk', 'telegram']) == 0
    # A gate call that names no risk must fail, even with a full receipt.
    with pytest.raises(SystemExit):
        proof.main(['gate', '--sha', SHA])
    assert proof.main(['automated', '--sha', SHA, '--result', 'fail']) == 0
    record = proof.read(SHA)
    assert record['automated'] == 'fail' and record['moeed_approved'] is False
    assert proof.main(['approve', '--sha', SHA, '--note', 'cannot approve failed smoke']) == 1
    assert proof.main(['deploy', '--sha', SHA, '--result', 'pass']) == 0
    assert proof.read(SHA)['automated'] == 'pending'


@pytest.mark.parametrize('malformed', [
    {'sha': PREV, 'target': proof.TARGET, 'smoke': 'pass', 'automated': 'pass'},
    {'sha': SHA, 'target': 'portfolio_guru_bot', 'smoke': 'pass', 'automated': 'pass'},
    {'sha': SHA, 'target': proof.TARGET, 'smoke': True, 'automated': 'pass'},
    {'sha': SHA, 'target': proof.TARGET, 'smoke': 'pass', 'automated': 'pass', 'moeed_approved': 'true'},
])
def test_receipts_fail_closed_on_wrong_sha_target_or_types(monkeypatch, tmp_path, malformed):
    monkeypatch.setenv('PORTFOLIO_GURU_STAGING_PROOF_DIR', str(tmp_path))
    (tmp_path / f'{SHA}.json').write_text(json.dumps(malformed))
    assert proof.main(['gate', '--sha', SHA, '--risk', 'telegram']) == 1


@pytest.fixture
def deploy_harness(tmp_path):
    repo = tmp_path / 'dev'
    scripts = repo / 'scripts'
    scripts.mkdir(parents=True)
    for name in ('deploy_staging.sh', 'stage.sh', 'install_staging.sh', 'staging_proof.py', 'telegram_journey_proof.py', 'com.portfolioguru.staging-bot.plist'):
        shutil.copy(ROOT / 'scripts' / name, scripts / name)
    home = tmp_path / 'home'
    staging = home / 'projects/portfolio-guru-staging'
    (staging / '.git').mkdir(parents=True)
    (staging / 'backend/venv/bin').mkdir(parents=True)
    (staging / 'scripts').mkdir()
    # Staging starts at the preceding staging-capable commit.
    state = tmp_path / 'state'
    state.write_text(PREV)
    log = tmp_path / 'commands'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'python3').symlink_to(sys.executable)
    executable(bin_dir / 'git', '''#!/bin/bash
printf 'git %s\\n' "$*" >> "$COMMAND_LOG"
[[ "$1" != -C ]] || shift 2
case "$1 $2" in
  'remote get-url') echo synthetic-origin ;;
  'rev-parse --git-common-dir') echo .git ;;
  'ls-files '*) exec /usr/bin/git -C "$SNAPSHOT_REPO" ls-files -z --cached --others --exclude-standard ;;
  'archive '*) exec /usr/bin/git -C "$SNAPSHOT_REPO" archive HEAD ;;
  'rev-parse HEAD') cat "$STATE" ;;
  'status --porcelain') [[ -z "${FAKE_DIRTY:-}" ]] || echo ' M bot.py' ;;
  'branch --show-current') echo feature/test ;;
  'for-each-ref '*) [[ -n "${FAKE_SHA_ABSENT:-}" ]] || echo refs/remotes/origin/feature/test ;;
  'checkout --detach') printf '%s' "$3" > "$STATE" ;;
  'cat-file -e') [[ -z "${FAKE_NO_ISOLATION:-}" ]] ;;
  *) exit 0 ;;
esac
''')
    executable(bin_dir / 'launchctl', '''#!/bin/bash
printf 'launchctl %s\\n' "$*" >> "$COMMAND_LOG"
if [[ "$1" == print ]]; then
  if [[ "$(cat "$STATE")" == "$CANDIDATE" && -n "${FAKE_RESTART:-}" ]]; then
    count=0; [[ ! -f "$PID_QUERIES" ]] || count="$(cat "$PID_QUERIES")"
    count=$((count + 1)); echo "$count" > "$PID_QUERIES"
    echo "pid = $((TEST_PID + count - 1))"
  else echo "pid = $TEST_PID"; fi
fi
exit 0
''')
    executable(bin_dir / 'pgrep', '#!/bin/bash\nexit 0\n')
    executable(bin_dir / 'sleep', '#!/bin/bash\nprintf "sleep %s\\n" "$*" >> "$COMMAND_LOG"\n')
    executable(staging / 'backend/venv/bin/python3', '''#!/bin/bash
printf 'staging-python %s\\n' "$*" >> "$COMMAND_LOG"
if [[ "$*" == *pip* && -n "${FAKE_PIP_FAIL:-}" && "$(cat "$STATE")" == "$CANDIDATE" ]]; then exit 1; fi
''')
    executable(scripts / 'verify_live_runtime.py', '''#!/usr/bin/env python3
import os,sys
with open(os.environ['COMMAND_LOG'], 'a') as log: log.write('verify ' + ' '.join(sys.argv[1:]) + '\\n')
if '--service-domain' in sys.argv: print('user/501')
elif os.environ.get('FAKE_RUNTIME_FAIL') and os.environ['CANDIDATE'] == open(os.environ['STATE']).read(): sys.exit(1)
else: print('LIVE_RUNTIME_OK')
''')
    executable(staging / 'scripts/telegram_bot_qa.sh', '''#!/bin/bash
/usr/bin/env > "$QA_ENV_LOG"
printf 'qa %s\\n' "$*" >> "$COMMAND_LOG"
if [[ "${FAKE_QA_EXIT:-0}" == 0 && -z "${FAKE_NO_REPORT:-}" ]]; then
  python3 - <<'COVERAGE'
import json,os,sys
from pathlib import Path
root = Path(os.environ['SNAPSHOT_REPO'])
sys.path.insert(0, str(root / 'scripts'))
import telegram_journey_proof as journeys
current = journeys.fingerprints(root)
if os.environ.get('FAKE_PARTIAL'): current = {'text': current['text']}
report = {'schema': 1, 'sha': os.environ['CANDIDATE'], 'target': journeys.TARGET,
          'journeys': {key: {'fingerprint': value, 'sha': os.environ['CANDIDATE'],
                            'target': journeys.TARGET, 'status': 'passed'} for key,value in current.items()}}
path = Path(os.environ['TELEGRAM_JOURNEY_COVERAGE_REPORT'])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report))
COVERAGE
fi
exit "${FAKE_QA_EXIT:-0}"
''')
    for name in ('telegram_journey_proof.py', 'verify_live_runtime.py'):
        shutil.copy(scripts / name, staging / 'scripts' / name)
    (staging / 'backend/tests').mkdir()
    (staging / 'backend/bot.py').write_text('VALUE = 1\n')
    (staging / 'backend/tests/test_e2e.py').write_text('CASE = "synthetic"\n')
    (staging / 'backend/tests/conftest.py').write_text('')
    (staging / '.gitignore').write_text('.artifacts/\n__pycache__/\nbackend/venv/\n')
    subprocess.run(['/usr/bin/git', 'init', '-q', str(staging)], check=True)
    subprocess.run(['/usr/bin/git', '-C', str(staging), 'add', '.'], check=True)
    subprocess.run(['/usr/bin/git', '-C', str(staging), '-c', 'user.name=QA', '-c', 'user.email=qa@example.invalid',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'synthetic fixture'], check=True)
    plist = home / 'Library/LaunchAgents/com.portfolioguru.staging-bot.plist'
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({'Label': 'com.portfolioguru.staging-bot', 'WorkingDirectory': str(staging),
        'ProgramArguments': ['/bin/bash', str(staging / 'start-bot.sh')], 'EnvironmentVariables': {'PG_ENV': 'staging'}}))
    env = {'PATH': str(bin_dir) + ':/usr/bin:/bin', 'HOME': str(home), 'STATE': str(state),
        'SNAPSHOT_REPO': str(staging), 'COMMAND_LOG': str(log), 'CANDIDATE': SHA, 'TEST_PID': str(os.getpid()),
        'PID_QUERIES': str(tmp_path / 'pid-queries'), 'QA_ENV_LOG': str(tmp_path / 'qa-env'),
        'PORTFOLIO_GURU_STAGING_PROOF_DIR': str(tmp_path / 'proofs'),
        'PORTFOLIO_GURU_STAGING_DEPLOY_LOCK': str(tmp_path / 'staging-deploy.lock')}
    return {'scripts': scripts, 'env': env, 'staging': staging, 'log': log, 'state': state, 'home': home}


def deploy(harness, extra=None):
    return subprocess.run(['bash', str(harness['scripts'] / 'deploy_staging.sh'), SHA],
                          env={**harness['env'], **(extra or {})}, capture_output=True, text=True, timeout=10)


def test_deploy_uses_staging_only_and_writes_pass_receipt(deploy_harness):
    result = deploy(deploy_harness)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((Path(deploy_harness['env']['PORTFOLIO_GURU_STAGING_PROOF_DIR']) / f'{SHA}.json').read_text())
    assert record['sha'] == SHA and record['smoke'] == 'pass'
    assert record['automated'] == 'pending' and record['moeed_approved'] is False
    commands = deploy_harness['log'].read_text()
    assert f'checkout --detach {SHA}' in commands
    assert '--service-label com.portfolioguru.staging-bot' in commands
    assert f'--expected-sha {SHA}' in commands
    assert 'sleep 30' in commands
    assert '8099' not in commands and '8101' not in commands
    assert 'com.portfolioguru.bot' not in commands
    assert 'portfolio-guru-live' not in commands


@pytest.mark.parametrize('fault', ['FAKE_RUNTIME_FAIL', 'FAKE_RESTART', 'FAKE_PIP_FAIL'])
def test_staging_failure_restores_previous_sha_without_live_effects(deploy_harness, fault):
    result = deploy(deploy_harness, {fault: '1'})
    assert result.returncode == 1, result.stdout + result.stderr
    assert deploy_harness['state'].read_text() == PREV
    assert 'Staging rollback verified' in result.stderr
    record = json.loads((Path(deploy_harness['env']['PORTFOLIO_GURU_STAGING_PROOF_DIR']) / f'{SHA}.json').read_text())
    assert record['smoke'] == 'fail'
    assert 'com.portfolioguru.bot' not in deploy_harness['log'].read_text()


def test_deploy_refuses_live_dev_and_unpublished_sha_before_restart(deploy_harness):
    for path in (deploy_harness['home'] / 'projects/portfolio-guru-live', deploy_harness['scripts'].parent):
        result = deploy(deploy_harness, {'PORTFOLIO_GURU_STAGING_DIR': str(path)})
        assert result.returncode != 0
        assert 'Refusing staging deploy' in result.stderr
    result = deploy(deploy_harness, {'FAKE_SHA_ABSENT': '1'})
    assert result.returncode != 0
    assert 'SHA is not on an origin branch' in result.stderr
    assert 'launchctl' not in deploy_harness['log'].read_text()
    assert deploy_harness['state'].read_text() == PREV


def test_stage_smoke_runs_only_exact_test_target_and_revokes_old_approval(deploy_harness):
    assert deploy(deploy_harness).returncode == 0
    env = deploy_harness['env']
    sha_record = Path(env['PORTFOLIO_GURU_STAGING_PROOF_DIR']) / f'{SHA}.json'
    for exit_code in (0, 1):
        result = subprocess.run(['bash', str(deploy_harness['scripts'] / 'stage.sh'), 'smoke', '--sha', SHA],
            env={**env, 'FAKE_QA_EXIT': str(exit_code)}, capture_output=True, text=True, timeout=10)
        assert result.returncode == exit_code, result.stdout + result.stderr
        record = json.loads(sha_record.read_text())
        assert record['automated'] == ('pass' if exit_code == 0 else 'fail')
        assert record['moeed_approved'] is False
        if exit_code == 0:
            record['moeed_approved'] = True
            sha_record.write_text(json.dumps(record))
    qa_env = dict(line.split('=', 1) for line in Path(env['QA_ENV_LOG']).read_text().splitlines() if '=' in line)
    for name in ('TELEGRAM_BOT_USERNAME', 'TELEGRAM_LIVE_ALLOWED_BOTS', 'RELEASE_LIVE_TARGET', 'RELEASE_LIVE_ALLOWLIST'):
        assert qa_env[name] == 'portfolio_guru_test_bot'
    assert qa_env['TELEGRAM_LIVE_APPROVED'] == 'portfolio-guru-live-qa-approved'
    assert deploy_harness['log'].read_text().count('qa --wider-journeys --changed') == 2


def test_install_renders_isolated_plist_without_loading(deploy_harness):
    result = subprocess.run(['bash', str(deploy_harness['scripts'] / 'install_staging.sh')],
        env={**deploy_harness['env'], 'PG_VERTEX_SA_SECRET_ID': 'synthetic-id',
             'PG_VERTEX_SA_CLIENT_EMAIL': 'synthetic@example.invalid', 'PG_VERTEX_BWS_TOKEN_PATH': '/synthetic/bws-token'},
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    p = plistlib.loads((deploy_harness['home'] / 'Library/LaunchAgents/com.portfolioguru.staging-bot.plist').read_bytes())
    assert p['Label'] == 'com.portfolioguru.staging-bot'
    assert p['WorkingDirectory'] == str(deploy_harness['staging'])
    assert p['EnvironmentVariables']['PG_ENV'] == 'staging'
    assert p['EnvironmentVariables']['PG_VERTEX_SA_SECRET_ID'] == 'synthetic-id'
    assert 'portfolio-guru-staging/bot.log' in p['StandardOutPath']
    assert not deploy_harness['log'].exists()  # no launchctl / network / secrets


def test_stage_wider_smoke_selects_opt_in_mode(deploy_harness):
    assert deploy(deploy_harness).returncode == 0
    result = subprocess.run(['bash', str(deploy_harness['scripts'] / 'stage.sh'), 'smoke', '--sha', SHA, '--wider'],
        env=deploy_harness['env'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'qa --wider-journeys' in deploy_harness['log'].read_text()
    assert 'qa --focused-release' not in deploy_harness['log'].read_text()


@pytest.mark.parametrize('action', ['deploy', 'approve', 'status'])
def test_stage_wider_flag_is_smoke_only(deploy_harness, action):
    result = subprocess.run(['bash', str(deploy_harness['scripts'] / 'stage.sh'), action, '--sha', SHA, '--wider'],
        env=deploy_harness['env'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 64
    assert '--wider is only valid for smoke' in result.stderr
    assert not deploy_harness['log'].exists()


@pytest.mark.parametrize('override,args', [
    ({}, ['--target', 'portfolio_guru_bot']),
    ({'TELEGRAM_BOT_USERNAME': 'portfolio_guru_bot'}, []),
    ({'RELEASE_LIVE_TARGET': 'other_bot'}, []),
    ({'TELEGRAM_LIVE_ALLOWED_BOTS': 'portfolio_guru_test_bot,portfolio_guru_bot'}, []),
    ({'RELEASE_LIVE_ALLOWLIST': 'portfolio_guru_bot'}, []),
])
def test_stage_wider_smoke_refuses_redirection_before_effects(deploy_harness, override, args):
    result = subprocess.run(['bash', str(deploy_harness['scripts'] / 'stage.sh'), 'smoke', '--sha', SHA, '--wider', *args],
        env={**deploy_harness['env'], **override}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 21
    assert not deploy_harness['log'].exists()


@pytest.mark.parametrize('flags', [('--only', 'text'), ('--changed',), ('--full',)])
def test_stage_forwards_journey_selection(deploy_harness, flags):
    assert deploy(deploy_harness).returncode == 0
    result = subprocess.run(['bash', str(deploy_harness['scripts'] / 'stage.sh'), 'smoke', '--sha', SHA, *flags],
        env=deploy_harness['env'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'qa --wider-journeys ' + ' '.join(flags) in deploy_harness['log'].read_text()


def test_stage_partial_smoke_does_not_authorise_ship(deploy_harness):
    assert deploy(deploy_harness).returncode == 0
    result = subprocess.run(['bash', str(deploy_harness['scripts'] / 'stage.sh'), 'smoke', '--sha', SHA, '--only', 'text'],
        env={**deploy_harness['env'], 'FAKE_PARTIAL': '1'}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((Path(deploy_harness['env']['PORTFOLIO_GURU_STAGING_PROOF_DIR']) / f'{SHA}.json').read_text())
    assert record['automated'] == 'partial' and record['moeed_approved'] is False
    result = subprocess.run(['bash', str(deploy_harness['scripts'] / 'stage.sh'), 'approve', '--sha', SHA, '--note', 'Ship'],
        env=deploy_harness['env'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1


def test_stage_missing_coverage_cannot_preserve_prior_pass(deploy_harness):
    assert deploy(deploy_harness).returncode == 0
    command = ['bash', str(deploy_harness['scripts'] / 'stage.sh'), 'smoke', '--sha', SHA]
    assert subprocess.run(command, env=deploy_harness['env'], capture_output=True).returncode == 0
    result = subprocess.run(command, env={**deploy_harness['env'], 'FAKE_NO_REPORT': '1'}, capture_output=True, text=True)
    assert result.returncode == 1
    record = json.loads((Path(deploy_harness['env']['PORTFOLIO_GURU_STAGING_PROOF_DIR']) / f'{SHA}.json').read_text())
    assert record['automated'] == 'fail' and record['moeed_approved'] is False


def test_legacy_focused_pass_cannot_authorise_release(monkeypatch, tmp_path):
    monkeypatch.setenv('PORTFOLIO_GURU_STAGING_PROOF_DIR', str(tmp_path))
    proof.write(SHA, {'sha': SHA, 'target': proof.TARGET, 'smoke': 'pass', 'automated': 'pass', 'moeed_approved': True})
    assert proof.main(['gate', '--sha', SHA, '--risk', 'telegram']) == 1
    assert proof.main(['approve', '--sha', SHA, '--note', 'Ship']) == 1
