"""Offline pass-ledger and exact-SHA release coverage contracts."""
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('telegram_journey_proof', ROOT / 'scripts/telegram_journey_proof.py')
journeys = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journeys)


@pytest.fixture
def dependency_tree(monkeypatch, tmp_path):
    """Git-visible source, with no repository, credentials or live process."""
    names = (
        'backend/bot.py', 'backend/tests/test_e2e.py', 'backend/tests/conftest.py',
        'scripts/telegram_bot_qa.sh', 'scripts/telegram_journey_proof.py',
        'scripts/verify_live_runtime.py', 'start-bot.sh', 'backend/run_local.sh',
        'backend/staging_env.sh', 'backend/model_config.py', 'backend/gemini_client.py',
        'scripts/install_staging.sh', 'scripts/deploy_staging.sh',
        'scripts/com.portfolioguru.staging-bot.plist', 'backend/.env.example',
        'backend/requirements.txt', 'backend/requirements-dev.txt',
    )
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# original source\n')
    monkeypatch.setattr(journeys.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(
        stdout='\0'.join(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')
                         if path.is_file()).encode()))
    return tmp_path


@pytest.mark.parametrize('name', [
    'start-bot.sh', 'backend/run_local.sh', 'backend/staging_env.sh',
    'scripts/install_staging.sh', 'scripts/deploy_staging.sh',
    'scripts/com.portfolioguru.staging-bot.plist', 'backend/.env.example',
    'backend/model_config.py', 'backend/gemini_client.py',
])
def test_launch_and_configuration_changes_invalidate_every_cached_pass(dependency_tree, name):
    before = journeys.fingerprints(dependency_tree)
    ledger = {key: {'fingerprint': value, 'sha': 'a' * 40, 'target': journeys.TARGET}
              for key, value in before.items()}
    assert journeys.select(before, ledger) == []
    (dependency_tree / name).write_text('# changed flag or launch configuration\n')
    after = journeys.fingerprints(dependency_tree)
    assert all(after[key] != before[key] for key in before)
    assert journeys.select(after, ledger) == list(journeys.JOURNEYS)


@pytest.mark.parametrize('name', ['backend/runtime.flags', 'backend/prompts/advice.md',
                                 'backend/tests/fixtures/teaching.pdf'])
def test_unknown_configuration_dependency_forces_every_journey_to_rerun(dependency_tree, name):
    # An opaque filename must not be silently omitted just because it has no
    # recognised extension or no statically resolvable Python import.
    path = dependency_tree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('PG_GATHERING_MODE=1\n')
    before = journeys.fingerprints(dependency_tree)
    ledger = {key: {'fingerprint': value, 'sha': 'a' * 40, 'target': journeys.TARGET}
              for key, value in before.items()}
    path.write_text('PG_GATHERING_MODE=0\n')
    assert journeys.select(journeys.fingerprints(dependency_tree), ledger) == list(journeys.JOURNEYS)


def test_missing_launch_dependency_blocks_cached_proof(dependency_tree):
    (dependency_tree / 'backend/staging_env.sh').unlink()
    with pytest.raises(ValueError, match='dependency source missing'):
        journeys.fingerprints(dependency_tree)


def test_snapshot_never_reads_private_dotenv(dependency_tree, monkeypatch):
    private = dependency_tree / 'backend/.env'
    private.write_text('NEVER_READ_THIS=private\n')
    original = Path.read_bytes
    def read_bytes(path):
        assert path != private, 'private dotenv must never be read'
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    assert 'backend/.env' not in journeys.snapshot(dependency_tree)


def test_committed_launch_chain_uses_same_fingerprint_dependencies(dependency_tree, monkeypatch):
    def archive_run(*args, **kwargs):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            for path in dependency_tree.rglob('*'):
                if path.is_file():
                    tar.add(path, arcname=str(path.relative_to(dependency_tree)))
        return SimpleNamespace(stdout=archive.getvalue())
    local = journeys.fingerprints(dependency_tree)
    monkeypatch.setattr(journeys.subprocess, 'run', archive_run)
    before = journeys.fingerprints(dependency_tree, 'a' * 40)
    assert before == local
    ledger = {key: {'fingerprint': value, 'sha': 'a' * 40, 'target': journeys.TARGET}
              for key, value in before.items()}
    (dependency_tree / 'backend/staging_env.sh').write_text('# new isolation flags\n')
    after = journeys.fingerprints(dependency_tree, 'b' * 40)
    assert journeys.select(after, ledger) == list(journeys.JOURNEYS)


def test_unknown_source_symlink_cannot_reuse_cached_proof(dependency_tree):
    (dependency_tree / 'backend/unknown_runtime.flags').symlink_to('staging_env.sh')
    with pytest.raises(ValueError, match='not regular'):
        journeys.fingerprints(dependency_tree)


def test_cached_pass_is_reused_and_full_forces_rerun():
    fingerprints = {'text': 'digest', 'voice': 'new'}
    ledger = {'text': {'fingerprint': 'digest', 'sha': 'a' * 40, 'target': journeys.TARGET}}
    assert journeys.select(fingerprints, ledger) == ['voice']
    assert journeys.select(fingerprints, ledger, full=True) == ['text', 'voice']
    assert journeys.select(fingerprints, ledger, only=['text']) == []
    with pytest.raises(ValueError, match='Unknown journey'):
        journeys.select(fingerprints, ledger, only=['typo'])


def test_fingerprint_change_reruns_journey():
    assert journeys.select({'text': 'changed'}, {'text': {'fingerprint': 'old', 'sha': 'a' * 40, 'target': journeys.TARGET}}) == ['text']


def test_full_release_coverage_rejects_missing_stale_and_wrong_sha(monkeypatch, tmp_path):
    sha = 'a' * 40
    fingerprints = {key: key + '-digest' for key in journeys.JOURNEYS}
    monkeypatch.setattr(journeys, 'fingerprints', lambda *a, **kw: fingerprints)
    report = {'schema': 1, 'sha': sha, 'target': journeys.TARGET, 'journeys': {
        key: {'fingerprint': value, 'sha': 'b' * 40, 'target': journeys.TARGET, 'status': 'reused'}
        for key, value in fingerprints.items()}}
    journeys.validate_coverage(report, tmp_path, sha, committed=True)
    for broken in ('missing', 'stale', 'wrong_sha', 'failed', 'wrong_target'):
        candidate = json.loads(json.dumps(report))
        if broken == 'missing': candidate['journeys'].pop('text')
        if broken == 'stale': candidate['journeys']['text']['fingerprint'] = 'old'
        if broken == 'wrong_sha': candidate['sha'] = 'c' * 40
        if broken == 'failed': candidate['journeys']['text']['status'] = 'failed'
        if broken == 'wrong_target': candidate['target'] = 'portfolio_guru_bot'
        with pytest.raises(ValueError):
            journeys.validate_coverage(candidate, tmp_path, sha, committed=True)


def test_qiat_synthetic_input_includes_stage_of_training():
    import ast
    tree = ast.parse((ROOT / 'backend/tests/test_e2e.py').read_text())
    cases = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'FORM_VARIETY_CASES' for target in node.targets))
    assert 'Stage of Training: ST4 (Higher).' in cases['QIAT'][3]


def test_journey_registry_covers_current_parametrised_cases_and_real_tests():
    import ast
    tree = ast.parse((ROOT / 'backend/tests/test_e2e.py').read_text())
    cases = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'FORM_VARIETY_CASES' for target in node.targets))
    assert set(cases) == set(journeys.FORMS)
    functions = {node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef)}
    assert {node.split('::')[1].split('[')[0] for node in journeys.JOURNEYS.values()} <= functions
    assert len(journeys.JOURNEYS) == 16
