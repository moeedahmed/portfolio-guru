from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_live_runtime.py"
SPEC = importlib.util.spec_from_file_location("verify_live_runtime", SCRIPT)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)
SHA = "a" * 40


def test_expected_sha_must_be_full_hex():
    with pytest.raises(ValueError, match="40-character"):
        verify.validate_expected_sha("abc1234")


def test_expected_sha_mode_ignores_inherited_root_redirection(tmp_path):
    redirected = tmp_path / "redirected"
    assert verify.resolve_root(expected_sha=SHA, inherited_root=str(redirected)) == verify.DEFAULT_PROJECT_ROOT.resolve()


def test_no_arg_mode_keeps_deploy_smoke_root_override(tmp_path):
    assert verify.resolve_root(expected_sha=None, inherited_root=str(tmp_path)) == tmp_path.resolve()


def test_exact_runtime_and_checkout_sha_match(monkeypatch, tmp_path):
    identity = tmp_path / "runtime.json"
    identity.write_text(json.dumps({"pid": 42, "commit": SHA, "repo_root": str(tmp_path), "branch": "main"}))
    monkeypatch.setattr(verify, "expected_commit", lambda _root: SHA)
    monkeypatch.setattr(verify, "launchd_pid", lambda _label=verify.DEFAULT_SERVICE_LABEL: 42)
    monkeypatch.setattr(verify, "process_alive", lambda _pid: True)
    monkeypatch.setattr(verify, "portfolio_bot_pids", lambda _root: [42])
    output = verify.check_runtime(root=tmp_path, identity_path=identity, expected_sha=SHA)
    assert f"expected_sha={SHA}" in output
    assert f"checkout_sha={SHA}" in output
    assert f"runtime_sha={SHA}" in output


@pytest.mark.parametrize("checkout,runtime", [("b" * 40, SHA), (SHA, "b" * 40)])
def test_exact_sha_mismatch_fails(monkeypatch, tmp_path, checkout, runtime):
    identity = tmp_path / "runtime.json"
    identity.write_text(json.dumps({"pid": 42, "commit": runtime, "repo_root": str(tmp_path)}))
    monkeypatch.setattr(verify, "expected_commit", lambda _root: checkout)
    monkeypatch.setattr(verify, "launchd_pid", lambda _label=verify.DEFAULT_SERVICE_LABEL: 42)
    monkeypatch.setattr(verify, "process_alive", lambda _pid: True)
    with pytest.raises(RuntimeError, match="expected SHA"):
        verify.check_runtime(root=tmp_path, identity_path=identity, expected_sha=SHA)


@pytest.mark.parametrize("domain", ["gui", "user"])
def test_runtime_finds_the_single_registered_service_domain(monkeypatch, domain):
    import subprocess

    def run(args, **kwargs):
        target = args[-1]
        if target.startswith(domain + "/"):
            return subprocess.CompletedProcess(args, 0, "state = running\npid = 42\n", "")
        return subprocess.CompletedProcess(args, 113, "", "Could not find service")

    monkeypatch.setattr(verify.subprocess, "run", run)
    assert verify.launchd_pid() == 42


def test_runtime_refuses_duplicate_service_registrations(monkeypatch):
    import subprocess
    monkeypatch.setattr(verify.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "pid = 42\n", ""))
    with pytest.raises(RuntimeError, match="one registered"):
        verify.launchd_pid()


def test_runtime_does_not_treat_access_failure_as_an_absent_service(monkeypatch):
    import subprocess
    monkeypatch.setattr(verify.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "Permission denied"))
    with pytest.raises(RuntimeError, match="Permission denied"):
        verify.launchd_pid()


def test_service_domain_query_is_not_runtime_proof(monkeypatch, capsys):
    monkeypatch.setattr(verify, "launchd_service", lambda _label: ("user/501", 42))
    assert verify.main(["--service-domain"]) == 0
    assert capsys.readouterr().out == "user/501\n"


def test_domain_discovery_can_recover_a_stopped_service_without_claiming_runtime_proof(monkeypatch, capsys):
    import subprocess

    def run(args, **kwargs):
        if args[-1].startswith("user/"):
            return subprocess.CompletedProcess(args, 0, "state = waiting\n", "")
        return subprocess.CompletedProcess(args, 113, "", "Could not find service")

    monkeypatch.setattr(verify.subprocess, "run", run)
    assert verify.main(["--service-domain"]) == 0
    assert capsys.readouterr().out == f"user/{verify.os.getuid()}\n"
    with pytest.raises(RuntimeError, match="no launchd pid"):
        verify.launchd_pid()


def test_explicit_staging_runtime_flags_keep_live_defaults(monkeypatch, tmp_path, capsys):
    captured = {}
    def check(**kwargs):
        captured.update(kwargs)
        return 'LIVE_RUNTIME_OK'
    monkeypatch.setattr(verify, 'check_runtime', check)
    identity = tmp_path / 'staging-runtime.json'
    assert verify.main(['--root', str(tmp_path), '--identity', str(identity), '--service-label',
                        'com.portfolioguru.staging-bot', '--expected-sha', SHA]) == 0
    assert captured == {'root': tmp_path.resolve(), 'identity_path': identity,
                        'service_label': 'com.portfolioguru.staging-bot', 'expected_sha': SHA}
    assert verify.DEFAULT_SERVICE_LABEL == 'com.portfolioguru.bot'
    assert str(verify.DEFAULT_IDENTITY_PATH) == '/tmp/portfolio-guru-runtime.json'


def test_initial_staging_domain_is_not_runtime_proof(monkeypatch):
    import subprocess
    monkeypatch.setattr(verify.subprocess, 'run', lambda args, **kwargs: subprocess.CompletedProcess(args, 113, '', 'Could not find service'))
    assert verify.launchd_service('com.portfolioguru.staging-bot', allow_unregistered=True) == (f'user/{verify.os.getuid()}', None)
    assert verify.main(['--allow-unregistered', '--expected-sha', SHA]) == 1
