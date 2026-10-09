"""Offline pass-ledger and exact-SHA release coverage contracts."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('telegram_journey_proof', ROOT / 'scripts/telegram_journey_proof.py')
journeys = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(journeys)


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
