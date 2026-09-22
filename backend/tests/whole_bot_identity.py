"""Candidate binding reuses the canonical production runtime verifier."""
import os
from pathlib import Path
import re
import subprocess
import sys
from tests.whole_bot_process import atomic_json, bounded_process, seconds

TARGET = "portfolio_guru_bot"
SHA = re.compile(r"[0-9a-f]{40}")
KEYS = ("run_id", "target", "candidate_sha", "runtime_sha")


def provenance(run_id, target, candidate_sha, runtime_sha):
    return dict(zip(KEYS, (run_id, target, candidate_sha, runtime_sha)))


def valid_identity(value):
    return (isinstance(value, dict) and bool(value.get("run_id")) and
            value.get("target") == TARGET and
            isinstance(value.get("candidate_sha"), str) and
            SHA.fullmatch(value["candidate_sha"]) is not None and
            value.get("runtime_sha") == value["candidate_sha"])


def verify_candidate(repo, root, run_id, target, expected_sha):
    """No Telegram/credential access. Precondition refusal stays pending."""
    value = {**provenance(run_id, target, expected_sha, ""),
             "status": "pending", "reason": "candidate identity unavailable"}
    path = Path(root) / "runtime.json"
    if not run_id or target != TARGET or not SHA.fullmatch(expected_sha or ""):
        atomic_json(path, value)
        return value
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo, capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        if head != expected_sha or dirty:
            value["reason"] = "candidate must be a clean exact commit"
            atomic_json(path, value)
            return value
    except (OSError, subprocess.SubprocessError):
        atomic_json(path, value)
        return value
    # No inherited service, checkout or runtime-file overrides and no secrets.
    env = {"PATH": os.environ.get("PATH", ""), "PYTHON_DOTENV_DISABLED": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "PORTFOLIO_GURU_RUNTIME_WAIT_SECONDS": "0"}
    result = bounded_process([sys.executable, str(Path(repo) / "scripts/verify_live_runtime.py"),
        "--expected-sha", expected_sha], cwd=repo, env=env,
        log_path=Path(root) / "runtime.log", timeout=seconds("WHOLE_BOT_RUNTIME_TIMEOUT", 45),
        receipt_path=Path(root) / "runtime-process.json", metadata=value)
    value.update(status=result["status"], reason=result["reason"], exit_code=result["exit_code"])
    if result["status"] == "passed":
        value["runtime_sha"] = expected_sha
        result["runtime_sha"] = expected_sha
        atomic_json(Path(root) / "runtime-process.json", result)
    atomic_json(path, value)
    return value


def transcript_document(events, binding):
    if not valid_identity(binding):
        raise ValueError("transcript requires verified candidate identity")
    return {**{k: binding[k] for k in KEYS}, "events": events}


def current_binding():
    """Read only this live child's verified receipt, never an inherited identity."""
    import json
    root = Path(os.environ["WHOLE_BOT_ARTIFACT_DIR"])
    value = json.loads((root / "runtime.json").read_text())
    if (not valid_identity(value) or value.get("status") != "passed" or
        value["run_id"] != os.environ.get("WHOLE_BOT_RUN_ID") or
        value["target"] != os.environ.get("TELEGRAM_BOT_USERNAME", "").lstrip("@") or
        value["candidate_sha"] != os.environ.get("PORTFOLIO_GURU_EXPECTED_SHA")):
        raise ValueError("missing or mismatched live identity")
    return {k: value[k] for k in KEYS}


def live_transcript(events):
    # Offline fake-client tests retain their existing list format. Real whole-
    # bot runs explicitly enable provenance before the client fixture starts.
    if os.environ.get("WHOLE_BOT_LIVE_PROOF") == "1":
        return transcript_document(events, current_binding())
    return events
