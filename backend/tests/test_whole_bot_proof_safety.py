"""Phase 5A regression proofs. No real clients, secrets or network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock
import pytest
from tests import whole_bot_aggregate as qa
from tests.telegram_live_policy import COMMAND_POLICY, control_policy

@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, -15, 124])
def test_early_live_failure(tmp_path, code):
    qa.write_layer(tmp_path, "live-process", "run", "failed", exit_code=code)
    assert qa.aggregate(tmp_path, "run")["status"] == "failed"

@pytest.mark.parametrize("xml", ['<testsuite><testcase><error/></testcase></testsuite>',
    '<testsuite errors="1"><error/></testsuite>', '<testsuite><testcase><failure/></testcase></testsuite>'])
def test_early_junit_failure(tmp_path, xml):
    qa.write_layer(tmp_path, "live-process", "run", "passed", exit_code=0)
    (tmp_path / "live.xml").write_text(xml)
    assert qa.aggregate(tmp_path, "run")["status"] == "failed"

@pytest.mark.parametrize("command", "health unsigned settings start arcp voice plan upgrade link gather pathway".split())
def test_protected_command(command):
    assert COMMAND_POLICY[command] == "protected"

@pytest.mark.parametrize("payload", ["ACTION|health", "ACTION|health_limited", "ACTION|health_back_to_report",
    "ACTION|health_view|scan", "ACTION|health_queue|draft|1", "ACTION|health_page|2",
    "ACTION|health_detail|basis", "ACTION|health_review_select|2026-09", "ACTION|health_review_setup",
    "UNSIGNED|3m", "UNSIGNED|6m", "UNSIGNED|12m", "UNSIGNED|all", "UNSIGNED|custom",
    "ACTION|unsigned", "ACTION|settings", "ACTION|retry_recommend", "ACTION|retry_template",
    "FORM|CBD", "AMEND|update_current", "VOICE|path_kaizen", "ACTION|refresh_portfolio"])
def test_protected_control(payload):
    assert control_policy(payload, "", {"forms": ["CBD"]}) == "protected"

@pytest.mark.parametrize("head,dirty", [("a" * 40, " M bot.py"), ("b" * 40, ""), ("a" * 40, "?? untracked.py")])
def test_candidate_refusal_precedes_runtime(tmp_path, monkeypatch, head, dirty):
    from tests import whole_bot_identity as identity
    child = Mock(side_effect=[subprocess.CompletedProcess([], 0, head), subprocess.CompletedProcess([], 0, dirty)])
    monkeypatch.setattr(subprocess, "run", child)
    runtime = Mock(side_effect=AssertionError("must not verify"))
    monkeypatch.setattr(identity, "bounded_process", runtime)
    assert identity.verify_candidate(tmp_path, tmp_path, "run", "portfolio_guru_bot", "a" * 40)["status"] == "pending"
    runtime.assert_not_called()

@pytest.mark.parametrize("sha,target", [("abc", "portfolio_guru_bot"), ("a" * 40, "other_bot"), ("", "portfolio_guru_bot")])
def test_invalid_candidate_has_no_process_access(tmp_path, monkeypatch, sha, target):
    from tests.whole_bot_identity import verify_candidate
    child = Mock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(subprocess, "run", child)
    assert verify_candidate(tmp_path, tmp_path, "run", target, sha)["status"] == "pending"
    child.assert_not_called()

@pytest.mark.parametrize("code", [0, 1])
def test_canonical_runtime_verifier(tmp_path, monkeypatch, code):
    from tests import whole_bot_identity as identity
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=[subprocess.CompletedProcess([], 0, "a" * 40), subprocess.CompletedProcess([], 0, "")]))
    child = Mock(return_value={"exit_code": code, "status": "failed" if code else "passed", "reason": "exit"})
    monkeypatch.setattr(identity, "bounded_process", child)
    value = identity.verify_candidate(tmp_path, tmp_path, "run", "portfolio_guru_bot", "a" * 40)
    assert value["status"] == ("failed" if code else "passed")
    assert child.call_args.args[0] == [sys.executable, str(tmp_path / "scripts/verify_live_runtime.py"), "--expected-sha", "a" * 40]
    assert "PORTFOLIO_GURU_RUNTIME_IDENTITY" not in child.call_args.kwargs["env"]
    if code:
        assert qa.aggregate(tmp_path, "run")["status"] == "failed"

@pytest.mark.parametrize("interrupt", [False, True])
def test_owned_tree_terminated_and_receipt_retained(tmp_path, interrupt):
    from tests.whole_bot_process import bounded_process
    pidfile = tmp_path / "pid"
    child = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    code = f"import subprocess,sys,time,os,signal; p=subprocess.Popen([sys.executable,'-c',{child!r}]); open({str(pidfile)!r},'w').write(str(p.pid)); "
    code += "time.sleep(.2); os.kill(os.getppid(), signal.SIGINT); " if interrupt else ""
    code += "time.sleep(60)"
    start = time.monotonic()
    result = bounded_process([sys.executable, "-c", code], cwd=tmp_path, env={},
        log_path=tmp_path / "log", timeout=.7, grace=.3,
        receipt_path=tmp_path / "process.json", metadata={"run_id": "run"})
    assert time.monotonic() - start < 5
    assert result["status"] == "failed" and result["reason"] == ("interrupted" if interrupt else "timeout")
    assert json.loads((tmp_path / "process.json").read_text()) == result
    deadline = time.monotonic() + 2
    while True:
        try:
            os.kill(int(pidfile.read_text()), 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "descendant was not reaped"
        time.sleep(.02)


@pytest.mark.parametrize("layer", ["live_graph", "clinical", "cleanup", "live-process"])
@pytest.mark.parametrize("key", ["run_id", "target", "candidate_sha", "runtime_sha"])
def test_each_live_layer_requires_same_provenance(tmp_path, layer, key):
    from tests.test_whole_bot_coverage import evidence
    evidence(tmp_path)
    assert qa.aggregate(tmp_path, "run")["status"] == "passed"
    path = tmp_path / (layer + ".json")
    value = json.loads(path.read_text())
    value[key] = "old"
    path.write_text(json.dumps(value))
    assert qa.aggregate(tmp_path, "run")["status"] != "passed"

@pytest.mark.parametrize("layer", ["live_graph", "clinical", "cleanup"])
@pytest.mark.parametrize("key", ["run_id", "target", "candidate_sha", "runtime_sha"])
def test_transcript_requires_same_provenance(tmp_path, layer, key):
    from tests.test_whole_bot_coverage import evidence
    evidence(tmp_path)
    path = tmp_path / (layer + "-transcript.json")
    value = json.loads(path.read_text())
    value.pop(key)
    path.write_text(json.dumps(value))
    assert qa.aggregate(tmp_path, "run")["status"] != "passed"


def test_older_live_runtime_and_matching_local_digest_cannot_pass(tmp_path):
    from tests.test_whole_bot_coverage import evidence
    evidence(tmp_path)
    # All old live receipts agree, including the unchanged local registration
    # digest. The candidate requested for THIS run is newer.
    path = tmp_path / "run-context.json"
    value = json.loads(path.read_text())
    value["candidate_sha"] = "b" * 40
    path.write_text(json.dumps(value))
    assert qa.aggregate(tmp_path, "run")["status"] == "pending"


def test_transcript_writer_includes_only_public_identity(tmp_path, monkeypatch):
    from tests.test_whole_bot_coverage import evidence
    from tests.telegram_live_harness import write_transcript_artifact, TelegramExchange
    evidence(tmp_path)
    for key, value in {"WHOLE_BOT_RUN_ID": "run", "WHOLE_BOT_ARTIFACT_DIR": str(tmp_path),
        "TELEGRAM_E2E_ARTIFACT_DIR": str(tmp_path), "WHOLE_BOT_LIVE_PROOF": "1",
        "TELEGRAM_BOT_USERNAME": "portfolio_guru_bot", "PORTFOLIO_GURU_EXPECTED_SHA": "a" * 40,
        "TELEGRAM_QA_USER_ID": "123456789", "TELETHON_SESSION": "private-session-value",
        "TELETHON_API_HASH": "private-hash-value"}.items():
        monkeypatch.setenv(key, value)
    write_transcript_artifact([TelegramExchange("cancel", "send:/cancel", "Cancelled private-session-value email@example.com 123456789")])
    raw = (tmp_path / "portfolio-guru-telegram-transcript.json").read_text()
    value = json.loads(raw)
    assert set(value) == {"run_id", "target", "candidate_sha", "runtime_sha", "events"}
    assert value["run_id"] == "run" and value["runtime_sha"] == "a" * 40
    assert all(secret not in raw for secret in ("private-session-value", "private-hash-value", "email@example.com", "123456789"))


@pytest.mark.parametrize("mode", ["offline-timeout", "live-timeout", "live-interrupted", "fixture-error"])
def test_driver_bounds_processes_and_reserves_cleanup(tmp_path, monkeypatch, mode):
    from tests.test_whole_bot_coverage import evidence
    monkeypatch.setenv("RUN_LIVE_TELEGRAM", "1")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "portfolio_guru_bot")
    monkeypatch.setenv("PORTFOLIO_GURU_EXPECTED_SHA", "a" * 40)
    monkeypatch.setenv("WHOLE_BOT_OFFLINE_TIMEOUT", "17")
    monkeypatch.setenv("WHOLE_BOT_LIVE_TIMEOUT", "19")
    monkeypatch.setenv("WHOLE_BOT_CLEANUP_TIMEOUT", "3")
    monkeypatch.setattr("tests.telegram_live_harness.has_telethon_env", lambda: True)
    calls = []
    def child(args, **kwargs):
        calls.append((args, kwargs))
        run_id = kwargs["metadata"]["run_id"]
        if len(calls) == 1:
            evidence(tmp_path, run_id)
            for layer in ("live_graph", "clinical", "cleanup"):
                qa.write_layer(tmp_path, layer, run_id, "pending")
            if mode != "offline-timeout":
                return {"status": "passed", "exit_code": 0, "reason": "exit"}
        reason = "interrupted" if mode == "live-interrupted" else "timeout" if "timeout" in mode else "exit"
        value = {**kwargs["metadata"], "status": "failed", "exit_code": 130 if reason == "interrupted" else 124 if reason == "timeout" else 2, "reason": reason}
        qa.atomic_json(kwargs["receipt_path"], value)
        return value
    monkeypatch.setattr(qa, "bounded_process", child)
    monkeypatch.setattr(qa, "verify_candidate", lambda *args: json.loads((tmp_path / "runtime.json").read_text()))
    assert qa.run(tmp_path) == 1
    assert calls[0][1]["timeout"] == 17
    assert "TELETHON_SESSION" not in calls[0][1]["env"]
    if mode == "offline-timeout":
        assert len(calls) == 1
    else:
        assert len(calls) == 3
        assert calls[1][1]["timeout"] == 19 and calls[2][1]["timeout"] == 3
        assert "test_live_cancel_cleanup" in str(calls[2][0])
    assert json.loads((tmp_path / "whole-bot-aggregate.json").read_text())["status"] == "failed"


def test_interruption_between_children_is_terminal(tmp_path, monkeypatch):
    def stop(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(qa, "bounded_process", stop)
    assert qa.run(tmp_path) == 1
    assert json.loads((tmp_path / "runner.json").read_text())["reason"] == "interrupted"
    assert json.loads((tmp_path / "whole-bot-aggregate.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1"])
def test_invalid_timeout_is_terminal(tmp_path, monkeypatch, value):
    monkeypatch.setenv("WHOLE_BOT_OFFLINE_TIMEOUT", value)
    assert qa.run(tmp_path) == 1
    assert json.loads((tmp_path / "whole-bot-aggregate.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("stage", ["dirty", "runtime-failed"])
def test_client_readiness_never_precedes_runtime(tmp_path, monkeypatch, stage):
    from tests import test_whole_bot_live as live
    monkeypatch.setenv("WHOLE_BOT_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(live, "verify_candidate", lambda *args: {"status": "pending" if stage == "dirty" else "failed"})
    readiness = Mock(side_effect=AssertionError("credentials must not be inspected"))
    monkeypatch.setattr(live, "has_telethon_env", readiness)
    with pytest.raises(AssertionError, match="identity not verified"):
        live.whole_bot_ready.__wrapped__()
    readiness.assert_not_called()


def test_interrupt_while_starting_live_reserves_cleanup(tmp_path, monkeypatch):
    from tests.test_whole_bot_coverage import evidence
    monkeypatch.setenv("RUN_LIVE_TELEGRAM", "1")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "portfolio_guru_bot")
    monkeypatch.setenv("PORTFOLIO_GURU_EXPECTED_SHA", "a" * 40)
    monkeypatch.setattr("tests.telegram_live_harness.has_telethon_env", lambda: True)
    monkeypatch.setattr(qa, "verify_candidate", lambda *args: json.loads((tmp_path / "runtime.json").read_text()))
    calls = []
    def child(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            evidence(tmp_path, kwargs["metadata"]["run_id"])
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {"status": "passed", "exit_code": 0, "reason": "exit"}
    monkeypatch.setattr(qa, "bounded_process", child)
    assert qa.run(tmp_path) == 1
    assert len(calls) == 3 and "test_live_cancel_cleanup" in str(calls[-1])


@pytest.mark.parametrize("forwarding", [False, True])
def test_whole_bot_shell_forwards_interruption_to_owner(tmp_path, forwarding):
    import signal
    script = Path(__file__).resolve().parents[2] / "scripts/telegram_bot_qa.sh"
    # Negative control reproduces the previous shell wrapper which swallowed
    # direct termination instead of delivering it to the process-tree owner.
    if not forwarding:
        copy = tmp_path / "old-wrapper.sh"
        copy.write_text(script.read_text().replace("exec env PYTHON_DOTENV_DISABLED=1", "env PYTHON_DOTENV_DISABLED=1"))
        script = copy
    interpreter = tmp_path / "backend/venv/bin/python3"
    interpreter.parent.mkdir(parents=True)
    ready, stopped = tmp_path / "ready", tmp_path / "stopped"
    interpreter.write_text("#!/bin/sh\ntrap 'echo interrupted > " + str(stopped) + "; exit 130' TERM\necho ready > " + str(ready) + "\nwhile :; do sleep .1; done\n")
    interpreter.chmod(0o755)
    process = subprocess.Popen(["bash", str(script), "--whole-bot"],
        env={"PATH": os.environ["PATH"], "PORTFOLIO_GURU_APP_DIR": str(tmp_path)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(.02)
        process.terminate()
        process.wait(timeout=3)
        assert stopped.exists() == forwarding
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


@pytest.mark.parametrize("exit_code", [0, 124])
def test_cleanup_permission_error_always_retains_failed_receipt(tmp_path, monkeypatch, exit_code):
    from tests import whole_bot_process as process
    child = Mock(pid=12345)
    child.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 1), 0] if exit_code else [0, 0]
    monkeypatch.setattr(process.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(process.os, "killpg", Mock(side_effect=PermissionError("synthetic denial")))
    result = process.bounded_process(["synthetic"], cwd=tmp_path, env={}, log_path=tmp_path / "log",
        timeout=.1, grace=.01, receipt_path=tmp_path / "process.json", metadata={"run_id": "run"})
    assert result["status"] == "failed" and result["termination_error"] == "PermissionError"
    assert json.loads((tmp_path / "process.json").read_text()) == result
