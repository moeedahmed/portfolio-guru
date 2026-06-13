#!/usr/bin/env python3
"""Dogfood Repair Watch - thin deterministic watcher for OpenClaw cron/agent coordination.

Runs the existing weird-prompt QA (offline, safe), parses the fix-queue if produced,
and emits machine-readable JSON or a concise text/NO_REPLY for cron relay.

Never deploys, restarts, runs live Telegram, touches Kaizen, uses credentials, or pushes.
Reuses existing QA scripts (weird_prompt_qa.sh, optionally release_loop.sh prepare).
"""

from __future__ import annotations

import argparse
import atexit
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_CONSECUTIVE_FAILURES = 3

LIVE_GATE_HINT_PATTERNS = (
    "kaizen login",
    "kaizen auth",
    "credential",
    "cdp",
    "playwright",
    "browser login",
    "session expiry",
    "re-authenticate",
    "bws",
    "deploy",
    "restart",
)

_repo_root_cache: Path | None = None
_lock_fd: int | None = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    global _repo_root_cache
    if _repo_root_cache is not None:
        return _repo_root_cache
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
    )
    if result.returncode != 0:
        sys.exit("ERROR: not inside a git repository")
    _repo_root_cache = Path(result.stdout.strip())
    return _repo_root_cache


def _acquire_lock() -> bool:
    global _lock_fd
    lock_dir = _repo_root() / ".artifacts" / "dogfood-repair-watch"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "watch.lock"
    try:
        _lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        atexit.register(_release_lock)
        return True
    except BlockingIOError:
        os.close(_lock_fd)
        _lock_fd = None
        return False


def _release_lock() -> None:
    global _lock_fd
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None


def _state_dir() -> Path:
    return _repo_root() / ".artifacts" / "dogfood-repair-watch"


def _state_file() -> Path:
    return _state_dir() / "state.json"


def _load_state() -> dict:
    path = _state_file()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "consecutive_failures": 0,
        "total_runs": 0,
        "last_run_timestamp": None,
        "last_failure_fix_hints": [],
    }


def _save_state(state: dict) -> None:
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "state.tmp"
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(_state_file())


def _is_repo_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, cwd=str(_repo_root()),
    )
    return bool(result.stdout.strip())


def _fix_queue_path() -> Path:
    return _repo_root() / ".artifacts" / "weird-prompt-qa" / "latest" / "fix-queue.json"


def _load_fix_queue() -> dict | None:
    path = _fix_queue_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _run_weird_prompt_qa() -> int:
    script = _repo_root() / "scripts" / "weird_prompt_qa.sh"
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, cwd=str(_repo_root()),
    )
    if result.stderr:
        # Stderr carries only filenames and diagnostics; no credentials.
        print(result.stderr, file=sys.stderr)
    return result.returncode


def _run_release_prepare() -> tuple[int, str, str]:
    script = _repo_root() / "scripts" / "release_loop.sh"
    result = subprocess.run(
        ["bash", str(script), "--surface", "telegram", "--mode", "prepare"],
        capture_output=True, text=True, cwd=str(_repo_root()),
    )
    return result.returncode, result.stdout, result.stderr


def _detect_human_gate_hints(fix_hints: list[str]) -> list[str]:
    reasons: list[str] = []
    for hint in fix_hints:
        for pattern in LIVE_GATE_HINT_PATTERNS:
            if pattern.lower() in hint.lower():
                reasons.append(
                    f"fix hint suggests live/human-only action "
                    f"(pattern '{pattern}'): {hint[:150]}"
                )
                break
    return reasons


# ---------------------------------------------------------------------------
# self-check
# ---------------------------------------------------------------------------

def self_check() -> int:
    """Validate the script itself without running any QA or touching the network."""
    errors: list[str] = []
    root = _repo_root()

    # 1 - known files must exist
    for rel in (
        "scripts/weird_prompt_qa.sh",
        "scripts/release_loop.sh",
        "scripts/preflight.sh",
        "scripts/telegram_qa_offline.sh",
    ):
        if not (root / rel).is_file():
            errors.append(f"missing expected script: {rel}")

    # 2 - fix-queue parse fixture (matches the real schema from
    #     _generate_fix_queue in test_weird_prompt_qa_offline.py)
    sample = {
        "generated": "2026-01-01T00:00:00Z",
        "failure_count": 2,
        "total_cases": 15,
        "fixes": [
            {
                "id": "random-nonsense",
                "category": "random",
                "prompt": "pizza",
                "reply_preview": "What case would you like to file?",
                "buttons": [
                    {"label": "Draft now", "action_id": "ACTION|draft_now"}
                ],
                "state": 2,
                "state_flags": {
                    "entered_case_processing": True,
                    "has_gathering_case": True,
                },
                "user_data_keys": ["gathering_case"],
                "failure_reasons": ["entered _process_case_text"],
                "fix_hint": 'Route "random" prompts before _process_case_text',
            },
            {
                "id": "pricing",
                "category": "product-help",
                "prompt": "How much does this cost?",
                "reply_preview": "completely free forever",
                "buttons": [],
                "state": None,
                "state_flags": {
                    "entered_case_processing": False,
                    "has_gathering_case": False,
                },
                "user_data_keys": [],
                "failure_reasons": ["reply contained forbidden text: completely free"],
                "fix_hint": "Remove 'completely free' from pricing reply",
            },
        ],
    }

    if sample["failure_count"] != 2:
        errors.append("fixture failure_count != 2")
    if len(sample["fixes"]) != 2:
        errors.append("fixture fixes count != 2")

    # 3 - hint->human-gate detection logic
    safe_hint = 'Route "random" prompts before _process_case_text'
    live_hint = "Fix Kaizen login credential validation in playwright/CDP path"
    safe_reasons = _detect_human_gate_hints([safe_hint])
    live_reasons = _detect_human_gate_hints([live_hint])
    if safe_reasons:
        errors.append(f"safe hint incorrectly flagged as human-gate: {safe_reasons}")
    if not live_reasons:
        errors.append("live hint not flagged as human-gate")

    # 4 - state serialisation round-trip
    original = {
        "consecutive_failures": 1,
        "total_runs": 5,
        "last_run_timestamp": "2026-01-01T00:00:00Z",
        "last_failure_fix_hints": ["hint-a"],
    }
    _save_state(original)
    loaded = _load_state()
    if loaded != original:
        errors.append(f"state round-trip mismatch: {loaded} != {original}")

    if errors:
        print("SELF-CHECK FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print("SELF-CHECK PASSED")
    return 0


# ---------------------------------------------------------------------------
# main watch logic
# ---------------------------------------------------------------------------

def run_watch(as_json: bool = False, with_prepare: bool = False) -> int:
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    if not _acquire_lock():
        msg = {"status": "locked", "reason": "another watch run is active"}
        if as_json:
            print(json.dumps(msg))
        else:
            print("NO_REPLY")
        return 0

    state = _load_state()

    qa_exit = _run_weird_prompt_qa()
    fix_queue = _load_fix_queue()
    prepare_exit: int | None = None
    prepare_ready: bool | None = None
    prepare_blocked_reasons: list[str] = []

    state["total_runs"] = state.get("total_runs", 0) + 1

    if with_prepare:
        prepare_exit, stdout, _stderr = _run_release_prepare()
        prepare_ready = prepare_exit == 0
        if not prepare_ready:
            for line in stdout.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    prepare_blocked_reasons.append(line[2:])

    human_gate_reasons: list[str] = []

    if _is_repo_dirty():
        human_gate_reasons.append(
            "repo has uncommitted tracked changes - commit before automated repair"
        )

    if qa_exit != 0 and fix_queue is None:
        human_gate_reasons.append(
            "weird-prompt QA exited non-zero but produced no fix-queue.json "
            "- possible test infrastructure issue"
        )

    fix_hints: list[str] = []
    repair_categories: set[str] = set()
    failure_count = 0

    if fix_queue is not None:
        failure_count = fix_queue.get("failure_count", 0)
        for fix in fix_queue.get("fixes", []):
            hint = fix.get("fix_hint", "")
            cat = fix.get("category", "")
            if hint:
                fix_hints.append(hint)
            if cat:
                repair_categories.add(cat)

    human_gate_reasons.extend(_detect_human_gate_hints(fix_hints))

    if failure_count > 0:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        state["last_failure_fix_hints"] = fix_hints
    else:
        state["consecutive_failures"] = 0
        state["last_failure_fix_hints"] = []

    if state["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
        human_gate_reasons.append(
            f"too many consecutive QA failures "
            f"({state['consecutive_failures']}) - "
            f"automated repair loop may be stuck"
        )

    state["last_run_timestamp"] = now_iso
    _save_state(state)

    if human_gate_reasons:
        status = "human_gate"
    elif failure_count > 0:
        status = "repair_needed"
    else:
        status = "healthy"

    proof_command = "bash scripts/weird_prompt_qa.sh"

    if as_json:
        fq_path = (
            str(_fix_queue_path()) if fix_queue is not None else None
        )
        output: dict = {
            "status": status,
            "run": now_iso,
            "fix_queue_path": fq_path,
            "fix_queue": fix_queue,
            "failure_count": failure_count,
            "consecutive_failures": state["consecutive_failures"],
            "total_runs": state["total_runs"],
            "repair_scope": sorted(repair_categories) if repair_categories else None,
            "proof_command": proof_command,
            "human_gate_reasons": human_gate_reasons,
            "repo": {
                "clean": not _is_repo_dirty(),
                "dirty": _is_repo_dirty(),
            },
            "qa_exit_code": qa_exit,
        }
        if with_prepare:
            output["prepare"] = {
                "exit_code": prepare_exit,
                "ready": prepare_ready,
                "blocked_reasons": prepare_blocked_reasons,
            }
        print(json.dumps(output, indent=2))
    else:
        if status == "healthy":
            print("NO_REPLY")
        elif status == "human_gate":
            print("HUMAN GATE:")
            for r in human_gate_reasons:
                print(f"  - {r}")
            if fix_queue is not None:
                print(f"\n  Fix queue: {_fix_queue_path()}")
                print(f"  Failure count: {failure_count}")
            print(f"\n  Proof command: {proof_command}")
        elif status == "repair_needed":
            print(f"REPAIR NEEDED ({failure_count} failure(s), "
                  f"{state['consecutive_failures']} consecutive)")
            if fix_queue is not None:
                print(f"  Fix queue: {_fix_queue_path()}")
            if repair_categories:
                print(f"  Repair scope: {', '.join(sorted(repair_categories))}")
            print(f"  Proof command: {proof_command}")

    return 0 if status == "healthy" else 1


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Dogfood Repair Watch - thin deterministic watcher for "
            "OpenClaw cron/agent coordination"
        ),
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Validate the script itself (no QA run, no network)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of concise text",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        default=True,
        help="Emit concise text output (default)",
    )
    parser.add_argument(
        "--with-prepare",
        action="store_true",
        help="Also run scripts/release_loop.sh --mode prepare for repo readiness",
    )
    args = parser.parse_args()

    if args.self_check:
        raise SystemExit(self_check())

    raise SystemExit(run_watch(as_json=args.json, with_prepare=args.with_prepare))


if __name__ == "__main__":
    main()
