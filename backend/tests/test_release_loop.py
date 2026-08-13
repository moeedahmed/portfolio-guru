"""Offline behavioural guards for scripts/release_loop.sh.

Approved ship-path tests use isolated fake repositories and executables. They
never fetch, push, deploy, restart, call GitHub, or contact Telegram/Kaizen.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "release_loop.sh"
PUSHED_SHA = "a" * 40
OTHER_SHA = "b" * 40


def run(*args, env=None):
    """Run release_loop.sh from the repo root, capturing output and exit code."""
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _path_only():
    """Minimal PATH so standard tools remain resolvable without leaking secrets."""
    return os.environ.get("PATH", "/usr/bin:/bin")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _workflow_runs(
    sha: str = PUSHED_SHA,
    *,
    conclusion: str = "success",
    event: str = "push",
    created_at: str = "2026-08-13T10:00:00Z",
    updated_at: str = "2026-08-13T10:05:00Z",
) -> str:
    # The unrelated run is deliberately first/newer: exact-SHA selection must ignore it.
    return json.dumps(
        [
            {
                "databaseId": 9002,
                "headSha": OTHER_SHA,
                "status": "completed",
                "conclusion": "success",
                "event": event,
                "createdAt": created_at,
                "startedAt": created_at,
                "updatedAt": updated_at,
            },
            {
                "databaseId": 9001,
                "headSha": sha,
                "status": "completed",
                "conclusion": conclusion,
                "event": event,
                "createdAt": created_at,
                "startedAt": created_at,
                "updatedAt": updated_at,
            },
        ]
    )


def _api_payload(cli_payload: str) -> str:
    runs = json.loads(cli_payload)
    for run in runs:
        for camel, snake in (
            ("headSha", "head_sha"),
            ("databaseId", "id"),
            ("createdAt", "created_at"),
            ("startedAt", "run_started_at"),
            ("updatedAt", "updated_at"),
        ):
            if camel in run:
                run[snake] = run.pop(camel)
    return json.dumps({"workflow_runs": runs})


@pytest.fixture
def ship_harness(tmp_path):
    fake_root = tmp_path / "repo"
    scripts = fake_root / "scripts"
    scripts.mkdir(parents=True)
    for name in ("preflight.sh", "telegram_qa_offline.sh"):
        _write_executable(scripts / name, "#!/usr/bin/env bash\nexit 0\n")

    runtime_log = tmp_path / "runtime.log"
    _write_executable(
        scripts / "verify_live_runtime.py",
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {runtime_log!s}\nprintf 'LIVE_RUNTIME_OK expected_sha={PUSHED_SHA} checkout_sha={PUSHED_SHA} runtime_sha={PUSHED_SHA}\\n'\n",
    )
    _write_executable(
        scripts / "telegram_bot_qa.sh",
        "#!/usr/bin/env bash\nprintf 'telegram_bot_qa.sh %s\\n' \"$*\" >> \"$RELEASE_TEST_LIVE_LOG\"\nexit 0\n",
    )
    _write_executable(
        scripts / "dogfood_smoke.sh",
        "#!/usr/bin/env bash\nprintf 'dogfood_smoke.sh %s\\n' \"$*\" >> \"$RELEASE_TEST_LIVE_LOG\"\nexit 0\n",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_log = tmp_path / "git.log"
    _write_executable(
        fake_bin / "git",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{git_log}"
if [[ -n "${{FAKE_GIT_FAIL_COMMAND:-}}" && "$1 $2" == "$FAKE_GIT_FAIL_COMMAND" ]]; then exit 9; fi
case "$1 $2" in
  "rev-parse --show-toplevel") printf '%s\\n' "{fake_root}" ;;
  "rev-parse --short") printf '{PUSHED_SHA[:7]}\\n' ;;
  "rev-parse --verify") exit 0 ;;
  "rev-parse HEAD") printf '{PUSHED_SHA}\\n' ;;
  "rev-parse origin/main") printf '{PUSHED_SHA}\\n' ;;
  "branch --show-current") printf 'fix/release-proof\\n' ;;
  "status --porcelain") exit 0 ;;
  "ls-files --others") exit 0 ;;
  "fetch --quiet"|"fetch origin") exit 0 ;;
  "merge-base --is-ancestor") exit 0 ;;
  "rev-list --left-right") printf '0 1\\n' ;;
  "checkout main") exit 0 ;;
  "pull --ff-only") exit 0 ;;
  "merge --ff-only") exit 0 ;;
  "push origin") exit 0 ;;
  "checkout fix/release-proof") exit 0 ;;
  *) exit 0 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "launchctl",
        "#!/usr/bin/env bash\n[[ \"$1\" == print ]] && exit 0\nexit 1\n",
    )

    gh_runs = tmp_path / "gh-runs"
    gh_runs.mkdir()
    (gh_runs / "Tests.json").write_text(_workflow_runs())
    (gh_runs / "Deploy Mac Mini.json").write_text(
        _workflow_runs(event="workflow_run", created_at="2026-08-13T10:06:00Z", updated_at="2026-08-13T10:10:00Z")
    )
    gh_log = tmp_path / "gh.log"
    _write_executable(
        fake_bin / "gh",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{gh_log}"
if [[ "$1 $2" == "auth status" ]]; then [[ "${{FAKE_GH_AUTH:-1}}" == "1" ]]; exit; fi
if [[ "$1 $2" == "run list" ]]; then
  workflow=""
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--workflow" ]]; then workflow="$2"; break; fi
    shift
  done
  test -n "$workflow" || exit 2
  /bin/cat "{gh_runs}/$workflow.json"
  exit 0
fi
exit 2
""",
    )
    _write_executable(
        fake_bin / "curl",
        f"""#!/usr/bin/env bash
url="${{@: -1}}"
case "$url" in
  *test.yml*) /bin/cat "{gh_runs}/Tests.api.json" ;;
  *deploy-mac.yml*) /bin/cat "{gh_runs}/Deploy Mac Mini.api.json" ;;
  *) exit 2 ;;
esac
""",
    )
    api_tests = json.loads(_workflow_runs())
    for run in api_tests:
        run.update(
            head_sha=run.pop("headSha"),
            id=run.pop("databaseId"),
            created_at=run.pop("createdAt"),
            run_started_at=run.pop("startedAt"),
            updated_at=run.pop("updatedAt"),
        )
    (gh_runs / "Tests.api.json").write_text(json.dumps({"workflow_runs": api_tests}))
    api_deploy = json.loads(
        _workflow_runs(event="workflow_run", created_at="2026-08-13T10:06:00Z", updated_at="2026-08-13T10:10:00Z")
    )
    for run in api_deploy:
        run.update(
            head_sha=run.pop("headSha"),
            id=run.pop("databaseId"),
            created_at=run.pop("createdAt"),
            run_started_at=run.pop("startedAt"),
            updated_at=run.pop("updatedAt"),
        )
    (gh_runs / "Deploy Mac Mini.api.json").write_text(json.dumps({"workflow_runs": api_deploy}))

    live_log = tmp_path / "live.log"
    env = {
        "PATH": f"{fake_bin}:{_path_only()}",
        "RELEASE_LOOP_PROOF_TIMEOUT": "0",
        "RELEASE_LOOP_PROOF_INTERVAL": "0",
        "RELEASE_LOOP_TEST_MODE": "1",
        "RELEASE_TEST_LIVE_LOG": str(live_log),
    }
    return {
        "root": fake_root,
        "env": env,
        "git_log": git_log,
        "gh_log": gh_log,
        "gh_runs": gh_runs,
        "runtime_log": runtime_log,
        "live_log": live_log,
    }


def _ship(harness, risk="internal", *, extra_env=None, extra_args=()):
    env = dict(harness["env"])
    if extra_env:
        env.update(extra_env)
    return run(
        "--surface",
        "telegram",
        "--mode",
        "ship",
        "--approved",
        "--risk",
        risk,
        *extra_args,
        env=env,
    )


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert SCRIPT.stat().st_mode & 0o111, "release_loop.sh should be executable"


def test_shell_syntax_is_valid():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_help_lists_required_surfaces_modes_and_risks():
    result = run("--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for expected in ("--surface", "telegram", "prepare", "ship", "--risk", "internal", "broad"):
        assert expected in out


def test_missing_mode_is_usage_error():
    result = run("--surface", "telegram")
    assert result.returncode == 64
    assert "Missing --mode" in result.stderr


def test_invalid_mode_is_usage_error():
    result = run("--surface", "telegram", "--mode", "bogus")
    assert result.returncode == 64
    assert "Invalid --mode" in result.stderr


def test_invalid_risk_is_usage_error():
    result = run("--surface", "telegram", "--mode", "prepare", "--risk", "reckless")
    assert result.returncode == 64
    assert "Invalid --risk" in result.stderr


@pytest.mark.parametrize("option", ["--surface", "--mode", "--risk", "--release-sha"])
def test_value_taking_options_refuse_a_missing_or_option_value(option):
    result = run(option, "--approved")
    assert result.returncode == 64
    assert f"{option} requires a value" in result.stderr


def test_ship_refuses_omitted_risk_before_approval_or_mutation():
    result = run("--surface", "telegram", "--mode", "ship", "--approved", env={"PATH": _path_only()})
    assert result.returncode == 64
    assert "--risk is required" in result.stderr


@pytest.mark.parametrize(
    "extra_env",
    [
        {"RELEASE_LOOP_PROOF_TIMEOUT": "-1"},
        {"RELEASE_LOOP_PROOF_INTERVAL": "1.5"},
        {"RELEASE_LOOP_PROOF_TIMEOUT": "08x"},
    ],
)
def test_invalid_proof_bounds_are_usage_errors(extra_env):
    result = run(
        "--surface",
        "telegram",
        "--mode",
        "ship",
        "--risk",
        "internal",
        "--approved",
        env={"PATH": _path_only(), **extra_env},
    )
    assert result.returncode == 64
    assert "base-10 whole seconds" in result.stderr


def test_zero_proof_bound_requires_explicit_test_mode():
    result = run(
        "--surface",
        "telegram",
        "--mode",
        "ship",
        "--risk",
        "internal",
        "--approved",
        env={"PATH": _path_only(), "RELEASE_LOOP_PROOF_TIMEOUT": "0"},
    )
    assert result.returncode == 64
    assert "positive outside explicit test mode" in result.stderr


def test_unsupported_surface_is_usage_error():
    result = run("--surface", "web", "--mode", "prepare")
    assert result.returncode == 64
    assert "Unsupported --surface" in result.stderr


def test_ship_refuses_without_approval():
    """ship must refuse and exit 2 before any live action when unapproved."""
    env = {"PATH": _path_only()}
    result = run("--surface", "telegram", "--mode", "ship", "--risk", "internal", env=env)
    assert result.returncode == 2
    assert "approval required" in result.stderr.lower()
    assert "FINAL_RELEASE_STATE=release-ready" in result.stdout


def test_ship_refuses_with_stale_approval_token():
    env = {"PATH": _path_only(), "RELEASE_APPROVED": "telegram-19990101"}
    result = run("--surface", "telegram", "--mode", "ship", "--risk", "internal", env=env)
    assert result.returncode == 2
    assert "stale or wrong surface" in result.stderr.lower()


def test_release_loop_has_no_passive_deploy_script_literal_and_real_guard_allows_wrapper():
    """Keep the wrapper portable and prove the installed Hermes guard when available."""
    assert "deploy_mac.sh" not in SCRIPT.read_text()

    hermes_root = Path.home() / ".hermes" / "hermes-agent"
    python = hermes_root / "venv" / "bin" / "python"
    guard = hermes_root / "cron" / "lifecycle_guard.py"
    if not (python.exists() and guard.exists()):
        pytest.skip("installed Hermes lifecycle guard is not available")
    probe = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(hermes_root)!r}); "
                "from cron.lifecycle_guard import contains_gateway_lifecycle_command_or_referenced_script as guard; "
                f"raise SystemExit(1 if guard('bash scripts/release_loop.sh --surface telegram --mode ship --approved', cwd={str(REPO_ROOT)!r}) else 0)"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_ship_keys_both_workflow_proofs_to_pushed_sha_and_internal_needs_no_dogfood(ship_harness):
    result = _ship(ship_harness, "internal")

    assert result.returncode == 0, result.stderr
    assert f"PUSHED_SHA={PUSHED_SHA}" in result.stdout
    assert "FINAL_RELEASE_STATE=live" in result.stdout
    assert "head_sha=" + PUSHED_SHA in result.stdout
    assert not ship_harness["live_log"].exists()
    gh_log = ship_harness["gh_log"].read_text()
    assert "--workflow Tests" in gh_log
    assert "--workflow Deploy Mac Mini" in gh_log
    assert "startedAt,updatedAt" in gh_log
    assert ship_harness["runtime_log"].read_text() == f"--expected-sha {PUSHED_SHA}\n"


def test_ship_falls_back_to_public_actions_api_when_gh_is_not_authenticated(ship_harness):
    result = _ship(ship_harness, "internal", extra_env={"FAKE_GH_AUTH": "0"})

    assert result.returncode == 0, result.stderr
    assert "FINAL_RELEASE_STATE=live" in result.stdout
    assert f"head_sha={PUSHED_SHA}" in result.stdout


@pytest.mark.parametrize("failed_workflow", ["Tests", "Deploy Mac Mini"])
def test_workflow_failure_never_reports_live(ship_harness, failed_workflow):
    (ship_harness["gh_runs"] / f"{failed_workflow}.json").write_text(
        _workflow_runs(
            conclusion="failure",
            event="push" if failed_workflow == "Tests" else "workflow_run",
            created_at="2026-08-13T10:00:00Z" if failed_workflow == "Tests" else "2026-08-13T10:06:00Z",
        )
    )
    result = _ship(ship_harness, "internal")

    assert result.returncode == 1, result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "conclusion=failure" in result.stdout


def test_workflow_timeout_ignores_unrelated_newer_success_and_never_reports_live(ship_harness):
    unrelated_only = json.dumps(
        [{"databaseId": 9002, "headSha": OTHER_SHA, "status": "completed", "conclusion": "success"}]
    )
    (ship_harness["gh_runs"] / "Tests.json").write_text(unrelated_only)
    result = _ship(ship_harness, "internal")

    assert result.returncode == 4, result.stderr
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert f"No Tests run for exact SHA {PUSHED_SHA}" in result.stdout
    assert not ship_harness["runtime_log"].exists()


def test_manual_tests_run_is_not_ordinary_ship_proof(ship_harness):
    (ship_harness["gh_runs"] / "Tests.json").write_text(_workflow_runs(event="workflow_dispatch"))
    result = _ship(ship_harness, "internal")
    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "event=push" in result.stdout


def test_deploy_must_start_after_selected_tests_completion(ship_harness):
    (ship_harness["gh_runs"] / "Deploy Mac Mini.json").write_text(
        _workflow_runs(event="workflow_run", created_at="2026-08-13T09:59:00Z", updated_at="2026-08-13T10:10:00Z")
    )
    result = _ship(ship_harness, "internal")
    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "event=workflow_run" in result.stdout


@pytest.mark.parametrize("response_family", ["cli", "api"])
@pytest.mark.parametrize("id_value", [None, "unknown", 0, -7, True])
def test_tests_success_requires_a_positive_numeric_run_id(ship_harness, response_family, id_value):
    runs = json.loads(_workflow_runs())
    target = runs[1]
    if id_value is None:
        target.pop("databaseId")
    else:
        target["databaseId"] = id_value
    payload = json.dumps(runs)
    if response_family == "api":
        (ship_harness["gh_runs"] / "Tests.api.json").write_text(_api_payload(payload))
        extra_env = {"FAKE_GH_AUTH": "0"}
    else:
        (ship_harness["gh_runs"] / "Tests.json").write_text(payload)
        extra_env = None

    result = _ship(ship_harness, "internal", extra_env=extra_env)

    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert not ship_harness["runtime_log"].exists()


@pytest.mark.parametrize("response_family", ["cli", "api"])
@pytest.mark.parametrize("field", ["createdAt", "startedAt", "updatedAt"])
@pytest.mark.parametrize("bad_value", [None, "not-a-timestamp"])
def test_tests_success_requires_all_ordering_timestamps_parseable(
    ship_harness, response_family, field, bad_value
):
    runs = json.loads(_workflow_runs())
    target = runs[1]
    if bad_value is None:
        target.pop(field)
    else:
        target[field] = bad_value
    payload = json.dumps(runs)
    if response_family == "api":
        (ship_harness["gh_runs"] / "Tests.api.json").write_text(_api_payload(payload))
        extra_env = {"FAKE_GH_AUTH": "0"}
    else:
        (ship_harness["gh_runs"] / "Tests.json").write_text(payload)
        extra_env = None

    result = _ship(ship_harness, "internal", extra_env=extra_env)

    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert not ship_harness["runtime_log"].exists()


@pytest.mark.parametrize("response_family", ["cli", "api"])
def test_missing_tests_completion_boundary_cannot_admit_older_same_sha_deploy(
    ship_harness, response_family
):
    tests = json.loads(_workflow_runs())
    tests[1].pop("updatedAt")
    deploy = _workflow_runs(
        event="workflow_run",
        created_at="2026-08-13T10:01:00Z",
        updated_at="2026-08-13T10:02:00Z",
    )
    if response_family == "api":
        (ship_harness["gh_runs"] / "Tests.api.json").write_text(_api_payload(json.dumps(tests)))
        (ship_harness["gh_runs"] / "Deploy Mac Mini.api.json").write_text(_api_payload(deploy))
        extra_env = {"FAKE_GH_AUTH": "0"}
    else:
        (ship_harness["gh_runs"] / "Tests.json").write_text(json.dumps(tests))
        (ship_harness["gh_runs"] / "Deploy Mac Mini.json").write_text(deploy)
        extra_env = None

    result = _ship(ship_harness, "internal", extra_env=extra_env)

    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert not ship_harness["runtime_log"].exists()


@pytest.mark.parametrize("response_family", ["cli", "api"])
@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("databaseId", None),
        ("databaseId", "unknown"),
        ("createdAt", None),
        ("createdAt", "bad"),
        ("startedAt", None),
        ("startedAt", "bad"),
        ("updatedAt", None),
        ("updatedAt", "bad"),
    ],
)
def test_malformed_newer_deploy_is_ignored_and_older_same_sha_deploy_stays_ineligible(
    ship_harness, response_family, field, bad_value
):
    older = json.loads(
        _workflow_runs(
            event="workflow_run",
            created_at="2026-08-13T09:58:00Z",
            updated_at="2026-08-13T09:59:00Z",
        )
    )[1]
    newer = json.loads(
        _workflow_runs(
            event="workflow_run",
            created_at="2026-08-13T10:06:00Z",
            updated_at="2026-08-13T10:10:00Z",
        )
    )[1]
    if bad_value is None:
        newer.pop(field)
    else:
        newer[field] = bad_value
    payload = json.dumps([newer, older])
    if response_family == "api":
        (ship_harness["gh_runs"] / "Deploy Mac Mini.api.json").write_text(_api_payload(payload))
        extra_env = {"FAKE_GH_AUTH": "0"}
    else:
        (ship_harness["gh_runs"] / "Deploy Mac Mini.json").write_text(payload)
        extra_env = None

    result = _ship(ship_harness, "internal", extra_env=extra_env)

    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert not ship_harness["runtime_log"].exists()


def test_running_tests_run_is_retryable_proof_pending(ship_harness):
    runs = json.loads(_workflow_runs())
    runs[1]["status"] = "in_progress"
    runs[1]["conclusion"] = None
    (ship_harness["gh_runs"] / "Tests.json").write_text(json.dumps(runs))
    result = _ship(ship_harness, "internal")
    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout


def test_runtime_proof_missing_never_reports_live(ship_harness):
    (ship_harness["root"] / "scripts" / "verify_live_runtime.py").unlink()
    result = _ship(ship_harness, "internal")

    assert result.returncode == 4, result.stderr
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "runtime" in result.stdout.lower()


def test_telegram_risk_does_not_silently_pass_without_live_approval(ship_harness):
    result = _ship(ship_harness, "telegram")

    assert result.returncode == 4, result.stderr
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "TELEGRAM_LIVE_APPROVED" in result.stdout
    assert not ship_harness["live_log"].exists()


def test_broad_risk_does_not_silently_pass_without_interactive_dogfood(ship_harness):
    result = _ship(ship_harness, "broad")

    assert result.returncode == 4, result.stderr
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "interactive" in result.stdout.lower()
    assert not ship_harness["live_log"].exists()


def test_proof_pending_prints_exact_resumable_command(ship_harness):
    (ship_harness["gh_runs"] / "Tests.json").write_text("[]")
    result = _ship(ship_harness, "telegram")
    assert result.returncode == 4
    assert (
        f"scripts/release_loop.sh --surface telegram --mode ship --risk telegram "
        f"--release-sha {PUSHED_SHA} --approved"
    ) in result.stdout


def test_resume_exact_sha_runs_proof_without_duplicate_push(ship_harness):
    result = _ship(ship_harness, "internal", extra_args=("--release-sha", PUSHED_SHA))
    assert result.returncode == 0, result.stderr
    git_log = ship_harness["git_log"].read_text()
    assert "push origin main" not in git_log
    assert "checkout main" not in git_log
    assert f"RESUMING_RELEASE_SHA={PUSHED_SHA}" in result.stdout


def test_push_failure_restores_original_feature_branch(ship_harness):
    result = _ship(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "push origin"})
    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    log = ship_harness["git_log"].read_text()
    assert "push origin main" in log
    assert "checkout fix/release-proof" in log


def test_branch_restoration_failure_is_blocking(ship_harness):
    result = _ship(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "checkout fix/release-proof"})
    assert result.returncode == 1
    assert "Failed to restore original feature branch" in result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout


def test_resume_refuses_sha_that_does_not_equal_head_and_origin_main(ship_harness):
    result = _ship(ship_harness, "internal", extra_args=("--release-sha", OTHER_SHA))
    assert result.returncode == 3
    assert "HEAD == origin/main == --release-sha" in result.stderr
    assert "push origin main" not in ship_harness["git_log"].read_text()


def test_telegram_release_uses_focused_selector(ship_harness):
    result = _ship(
        ship_harness,
        "telegram",
        extra_env={
            "TELEGRAM_LIVE_APPROVED": "portfolio-guru-live-qa-approved",
            "TELETHON_SESSION": "test-session",
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "test-hash",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "telegram_bot_qa.sh --focused-release" in ship_harness["live_log"].read_text()


def test_broad_release_invokes_strict_dogfood_mode_source_contract():
    assert 'dogfood_smoke.sh" --strict-release' in SCRIPT.read_text()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
