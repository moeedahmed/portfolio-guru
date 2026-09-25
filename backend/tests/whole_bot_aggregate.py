"""One terminal decision for the product-owned whole-bot proof entrypoint."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from tests.whole_bot_process import atomic_json, bounded_process, seconds
from tests.whole_bot_identity import KEYS, valid_identity, verify_candidate

LAYERS = ("catalogue", "offline", "live_graph", "clinical", "transcript", "cleanup")


def aggregate(root, run_id):
    from tests.whole_bot_coverage import REGISTRATION_DIGEST
    from tests.whole_bot_catalogue import CATALOGUE_DIGEST, PRODUCER_DIGEST, requirements_digest
    from tests.telegram_live_policy import COMMAND_POLICY
    root = Path(root)
    layers = {name: {"status": "pending", "reason": "missing evidence"} for name in LAYERS}
    artefacts = {}
    def read(name):
        path = root / name
        data = path.read_bytes()
        artefacts[name] = hashlib.sha256(data).hexdigest()
        return json.loads(data)
    def xml_tests(name):
        data = (root / name).read_bytes()
        artefacts[name] = hashlib.sha256(data).hexdigest()
        return list(ET.fromstring(data).iter("testcase"))
    binding = None
    runtime_status = "pending"
    try:
        runtime = read("runtime.json")
        if runtime.get("status") == "failed":
            runtime_status = "failed"
        else:
            context = read("run-context.json")
        if runtime_status != "failed" and (valid_identity(runtime) and runtime.get("status") == "passed" and
              all(runtime.get(k) == context.get(k) for k in ("run_id", "target", "candidate_sha")) and
              runtime.get("run_id") == run_id):
            process = read("runtime-process.json")
            if process.get("exit_code") != 0 or process.get("status") != "passed":
                runtime_status = "failed"
            elif all(process.get(k) == runtime.get(k) for k in KEYS):
                binding = {k: runtime[k] for k in KEYS}
                runtime_status = "passed"
    except FileNotFoundError:
        pass
    except (ValueError, TypeError, AttributeError):
        runtime_status = "failed"
    for name in LAYERS:
        if name == "transcript":
            continue
        try:
            value = read(name + ".json")
            if value.get("run_id") != run_id:
                layers[name] = {"status": "failed", "reason": "stale run identity"}
                continue
            status = value.get("status", "pending")
            if status not in {"passed", "pending", "failed"}:
                raise ValueError("invalid status")
            layers[name] = {"status": status, "reason": "layer result"}
            if name in {"catalogue", "live_graph"} and status == "passed" and value.get("registration_digest") != REGISTRATION_DIGEST:
                raise ValueError("registration identity mismatch")
            complete = True
            if name in {"live_graph", "clinical", "cleanup"} and status == "passed":
                if binding is None or any(value.get(k) != binding[k] for k in KEYS):
                    layers[name] = {"status": "pending", "reason": "missing or mismatched candidate/runtime identity"}
                    continue
            if name == "catalogue":
                if status == "passed" and (value.get("producer_digest") != PRODUCER_DIGEST or
                    value.get("requirements_digest") != CATALOGUE_DIGEST or requirements_digest(value.get("units", [])) != CATALOGUE_DIGEST):
                    raise ValueError("catalogue drift or omitted requirement")
                complete = value.get("catalogue_complete") is True and not value.get("unclassified", ["missing"]) and not value.get("uncovered", ["missing"])
                complete = complete and bool(value.get("units")) and all(u.get("status") in {"covered", "protected-boundary-covered"} and u.get("scenarios") and
                    (u.get("classification") != "protected-boundary" or u.get("status") == "protected-boundary-covered") for u in value["units"])
            elif name == "offline":
                tests = xml_tests("offline.xml")
                complete = bool(tests) and not any(t.find("skipped") is not None for t in tests)
                if any(t.find("failure") is not None or t.find("error") is not None for t in tests):
                    status = "failed"
            elif name == "live_graph":
                complete = bool(value.get("routes")) and bool(value.get("commands")) and not value.get("failures", ["missing"])
                safe_roots = {c for c, policy in COMMAND_POLICY.items() if policy == "safe"}
                complete = complete and set(value["commands"]) == set(COMMAND_POLICY)
                complete = complete and all(v == ("observed" if COMMAND_POLICY.get(c) == "safe" else "protected-command-not-invoked") for c, v in value["commands"].items())
                complete = complete and {r.get("root") for r in value["routes"]} == safe_roots and all(r.get("status") == "observed" for r in value["routes"])
            elif name == "clinical":
                complete = value.get("journeys", {}).get("cbd") == "passed" and value.get("journeys", {}).get("unstructured") == "passed" and any(p.get("payload") == "APPROVE|draft" and p.get("status") == "protected-boundary-reached" for p in value.get("protected", []))
            layers[name] = {"status": status if complete or status == "failed" else "pending", "reason": "verified" if complete and status == "passed" else "incomplete evidence" if not complete else "reported " + status}
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError):
            layers[name] = {"status": "failed", "reason": "invalid evidence"}
    try:
        transcripts = [read(name + "-transcript.json") for name in ("live_graph", "clinical", "cleanup")]
        assert binding is not None
        assert all(isinstance(t, dict) and all(t.get(k) == binding[k] for k in KEYS) for t in transcripts)
        transcripts = [t.get("events") for t in transcripts]
        assert all(isinstance(t, list) and t and any(e.get("received", "").strip() for e in t) for t in transcripts)
        assert any(e.get("action") == "send:/cancel" and re.match(r"^[^\w]*(?:cancelled|canceled)\b", e.get("received", ""), re.I) for e in transcripts[-1])
        layers["transcript"] = {"status": "passed", "reason": "nonempty transcripts and cancellation"}
    except (FileNotFoundError, AssertionError):
        pass
    except (ValueError, TypeError, AttributeError):
        layers["transcript"] = {"status": "failed", "reason": "invalid transcript"}
    # An attempted process is authoritative even if collection/fixtures failed
    # before a graph receipt could be emitted. Inspect process and XML separately.
    if (root / "live-process.json").exists() or (root / "live.xml").exists():
        process_failed = False
        process_complete = False
        try:
            process = read("live-process.json")
            process_failed = (process.get("run_id") != run_id or
                process.get("exit_code") != 0 or process.get("status") != "passed")
            process_complete = binding is not None and all(process.get(k) == binding[k] for k in KEYS)
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, AttributeError):
            process_failed = True
        try:
            data = (root / "live.xml").read_bytes()
            artefacts["live.xml"] = hashlib.sha256(data).hexdigest()
            tree = ET.fromstring(data)
            tests = list(tree.iter("testcase"))
            process_failed |= any(node.tag in {"error", "failure"} or
                any(int(node.get(key, "0")) > 0 for key in ("errors", "failures"))
                for node in tree.iter())
            process_complete &= bool(tests) and not any(t.find("skipped") is not None for t in tests)
        except FileNotFoundError:
            process_complete = False
        except (ValueError, TypeError, ET.ParseError):
            process_failed = True
        if process_failed:
            layers["live_graph"] = {"status": "failed", "reason": "attempted live process or JUnit failed"}
        elif not process_complete and layers["live_graph"]["status"] != "failed":
            layers["live_graph"] = {"status": "pending", "reason": "live process evidence incomplete"}
    elif layers["live_graph"]["status"] == "passed":
        layers["live_graph"] = {"status": "pending", "reason": "live process evidence missing"}
    if runtime_status == "failed":
        layers["live_graph"] = {"status": "failed", "reason": "runtime verification failed"}
    try:
        runner = read("runner.json")
        if runner.get("run_id") == run_id and runner.get("status") == "failed":
            layers["live_graph"] = {"status": "failed", "reason": "orchestrator interrupted or failed"}
    except FileNotFoundError:
        pass
    except (ValueError, TypeError, AttributeError):
        layers["live_graph"] = {"status": "failed", "reason": "invalid orchestrator evidence"}
    states = [v["status"] for v in layers.values()]
    status = "failed" if "failed" in states else "passed" if all(s == "passed" for s in states) else "pending"
    return {"schema": 1, "run_id": run_id, "status": status, "layers": layers, "artefacts": artefacts,
            "identity": binding, "scope": "Current registered interfaces/state kinds offline; bounded safe live graph and sampled synthetic clinical inputs. No protected live effects."}


def write_layer(root, name, run_id, status, **evidence):
    atomic_json(Path(root) / (name + ".json"), {"run_id": run_id, "status": status, **evidence})


def _run(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    target = os.environ.get("TELEGRAM_BOT_USERNAME", "").lstrip("@")
    expected_sha = os.environ.get("PORTFOLIO_GURU_EXPECTED_SHA", "")
    atomic_json(root / "run-context.json", {"run_id": run_id, "target": target, "candidate_sha": expected_sha})
    repo = Path(__file__).resolve().parents[2]
    final = root / "whole-bot-aggregate.json"
    for layer in LAYERS:
        if layer != "transcript":
            write_layer(root, layer, run_id, "pending")
    def finish():
        result = aggregate(root, run_id)
        atomic_json(final, result)
        print(f"{result['status'].upper()}: whole-bot aggregate; {final}")
        return {"passed": 0, "pending": 20, "failed": 1}[result["status"]]
    finish()  # Initial pending receipt; caught interruptions replace it atomically.
    scratch = root / "scratch"
    scratch.mkdir(exist_ok=True)
    python_paths = [str(repo / "backend")]
    hermes_source = Path.home() / ".hermes" / "hermes-agent"
    if (hermes_source / "hermes_cli" / "__init__.py").is_file():
        python_paths.append(str(hermes_source))
    offline_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(python_paths),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1", "TMPDIR": str(scratch),
        "DATABASE_URL": "sqlite:///" + str(scratch / "test.sqlite"),
        "USAGE_DB_PATH": str(scratch / "usage.sqlite"), "WHOLE_BOT_RUN_ID": run_id,
        "WHOLE_BOT_ARTIFACT_DIR": str(root), "PYTEST_ADDOPTS": "--junitxml=" + str(root / "offline.xml") + " -o faulthandler_timeout=30"}
    for key, filename in {"FUNNEL_LOG_PATH": "funnel.ndjson", "FILING_LOG_PATH": "filing.ndjson",
                          "DRAFT_BACKUP_DIR": "drafts", "HEALTH_PROFILE_PATH": "health.json",
                          "DOGFOOD_AUDIT_PATH": "audit.ndjson"}.items():
        offline_env["PORTFOLIO_GURU_" + key] = str(scratch / filename)
    result = bounded_process(["bash", "scripts/verify_release.sh"], cwd=repo, env=offline_env,
        log_path=root / "offline.log", timeout=seconds("WHOLE_BOT_OFFLINE_TIMEOUT", 1800),
        receipt_path=root / "offline-process.json", metadata={"run_id": run_id})
    write_layer(root, "offline", run_id, result["status"], exit_code=result["exit_code"], reason=result["reason"])
    offline_layers = aggregate(root, run_id)["layers"]
    if result["exit_code"] or any(offline_layers[name]["status"] != "passed" for name in ("catalogue", "offline")):
        return finish()
    # Offline work has finished before any live readiness/credential lookup.
    if os.environ.get("RUN_LIVE_TELEGRAM") in {"0", "false"}:
        return finish()
    from tests.telegram_live_harness import has_telethon_env
    frozen = os.environ.get("RELEASE_LIVE_TARGET", "").lstrip("@")
    if frozen and (os.environ.get("TELEGRAM_BOT_USERNAME", "").lstrip("@") != frozen or
                   os.environ.get("RELEASE_LIVE_ALLOWLIST", frozen).strip().lstrip("@") != frozen):
        write_layer(root, "live_graph", run_id, "pending", reason="approved target mismatch")
        return finish()
    binding = verify_candidate(repo, root, run_id, target, expected_sha)
    if binding["status"] != "passed":
        return finish()
    if not has_telethon_env():
        return finish()
    live_names = ("TELETHON_SESSION", "TELETHON_API_ID", "TELEGRAM_API_ID", "TELETHON_API_HASH",
                  "TELEGRAM_API_HASH", "TELEGRAM_LIVE_APPROVED", "TELEGRAM_BOT_USERNAME",
                  "TELEGRAM_LIVE_ALLOWED_BOTS", "TELEGRAM_QA_USER_ID")
    env = {**offline_env, **{k: os.environ[k] for k in live_names if k in os.environ},
           "FERNET_SECRET_KEY": "5Wv33F9sq99WGD2lEzwwd3J_JH5p6vxKdDiAwCWqoYQ=",
           "GOOGLE_API_KEY": "fake", "TELEGRAM_BOT_TOKEN": "0:FAKE",
           "TELEGRAM_E2E_ARTIFACT_DIR": str(root),
           "PORTFOLIO_GURU_EXPECTED_SHA": expected_sha, "WHOLE_BOT_LIVE_PROOF": "1",
           "WHOLE_BOT_RUNTIME_TIMEOUT": str(seconds("WHOLE_BOT_RUNTIME_TIMEOUT", 45))}
    env.pop("PYTEST_ADDOPTS", None)
    if frozen:
        env["TELEGRAM_LIVE_ALLOWED_BOTS"] = frozen
    metadata = {k: binding[k] for k in KEYS}
    for name in ("live_graph", "clinical", "cleanup"):
        write_layer(root, name, run_id, "pending", **{k: v for k, v in metadata.items() if k != "run_id"})
    live_result = {"status": "failed"}
    try:
        live_result = bounded_process([sys.executable, "-m", "pytest", "tests/test_whole_bot_live.py::test_live_whole_bot", "-q", "-m", "live",
            "--tb=no", "--show-capture=no", "-rN", "--junitxml=" + str(root / "live.xml")],
            cwd=repo / "backend", env=env, log_path=root / "live.log",
            timeout=seconds("WHOLE_BOT_LIVE_TIMEOUT", 1200), receipt_path=root / "live-process.json", metadata=metadata)
    finally:
        if live_result["status"] != "passed":
            # The child may have connected before a fixture error/timeout. A separate
            # bounded client is permitted only to verify identity and cancel.
            cleanup = bounded_process([sys.executable, "-m", "pytest",
                "tests/test_whole_bot_live.py::test_live_cancel_cleanup", "-q", "-m", "live",
                "--tb=no", "--show-capture=no", "-rN", "--junitxml=" + str(root / "cleanup.xml")],
                cwd=repo / "backend", env=env, log_path=root / "cleanup.log",
                timeout=seconds("WHOLE_BOT_CLEANUP_TIMEOUT", 60),
                receipt_path=root / "cleanup-process.json", metadata=metadata)
            if cleanup["status"] != "passed":
                write_layer(root, "cleanup", run_id, "failed", **{k: v for k, v in metadata.items() if k != "run_id"})
    # Re-check after the attempt so a candidate modified during proof cannot pass.
    verify_candidate(repo, root, run_id, target, expected_sha)
    return finish()


def run(root):
    import signal
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return _run(root)
    except (KeyboardInterrupt, Exception) as exc:
        root = Path(root)
        context = json.loads((root / "run-context.json").read_text())
        atomic_json(root / "runner.json", {"run_id": context["run_id"], "status": "failed",
            "reason": "interrupted" if isinstance(exc, KeyboardInterrupt) else type(exc).__name__})
        result = aggregate(root, context["run_id"])
        atomic_json(root / "whole-bot-aggregate.json", result)
        print("FAILED: whole-bot orchestration; " + str(root / "whole-bot-aggregate.json"))
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))
