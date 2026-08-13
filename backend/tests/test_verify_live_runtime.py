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
