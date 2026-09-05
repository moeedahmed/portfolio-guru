"""Offline behavioural guards for scripts/release_loop.sh.

Approved ship-path tests use isolated fake repositories and executables. They
never fetch, push, deploy, restart, call GitHub, or contact Telegram/Kaizen.

The harness copies the real scripts/release_card.py into the fake repo, so card
schema, approval binding and attestation are exercised as shipped code rather
than re-implemented here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "release_loop.sh"
CARD_TOOL = REPO_ROOT / "scripts" / "release_card.py"
BOT_QA = REPO_ROOT / "scripts" / "telegram_bot_qa.sh"
PUSHED_SHA = "a" * 40
OTHER_SHA = "b" * 40
LIVE_TARGET = "portfolio_guru_bot"
LIVE_APPROVAL = "portfolio-guru-live-qa-approved"
CI_FERNET_KEY = "5Wv33F9sq99WGD2lEzwwd3J_JH5p6vxKdDiAwCWqoYQ="
TELETHON_ENV = {
    "TELETHON_SESSION": "test-session",
    "TELEGRAM_API_ID": "123",
    "TELEGRAM_API_HASH": "test-hash",
    "TELEGRAM_BOT_USERNAME": LIVE_TARGET,
}


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


def _env_probe(label: str) -> str:
    """Fake-child preamble recording which credentials that child could see."""
    return (
        "printf '%s FERNET=%s TOKEN=%s GOOGLE=%s LIVE_APPROVED=%s BOT_USERNAME=%s\\n' "
        f"'{label}' "
        '"${FERNET_SECRET_KEY:-unset}" "${TELEGRAM_BOT_TOKEN:-unset}" "${GOOGLE_API_KEY:-unset}" '
        '"${TELEGRAM_LIVE_APPROVED:-unset}" "${TELEGRAM_BOT_USERNAME:-unset}" '
        '>> "$RELEASE_TEST_ENV_LOG"\n'
    )


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
    shutil.copy(CARD_TOOL, scripts / "release_card.py")
    for name in ("preflight.sh", "telegram_qa_offline.sh"):
        _write_executable(scripts / name, f"#!/usr/bin/env bash\n{_env_probe(name)}exit 0\n")

    runtime_log = tmp_path / "runtime.log"
    _write_executable(
        scripts / "verify_live_runtime.py",
        "#!/usr/bin/env bash\n"
        + _env_probe("verify_live_runtime.py")
        + f"printf '%s\\n' \"$*\" >> {runtime_log!s}\n"
        + 'expected="$2"\n'
        + "printf 'LIVE_RUNTIME_OK expected_sha=%s checkout_sha=%s runtime_sha=%s\\n' "
        '"$expected" "${FAKE_CHECKOUT_SHA:-$expected}" "${FAKE_RUNTIME_SHA:-$expected}"\n',
    )
    _write_executable(
        scripts / "telegram_bot_qa.sh",
        "#!/usr/bin/env bash\n"
        + _env_probe("telegram_bot_qa.sh")
        + "printf 'telegram_bot_qa.sh %s\\n' \"$*\" >> \"$RELEASE_TEST_LIVE_LOG\"\n"
        + "exit ${FAKE_LIVE_QA_EXIT:-0}\n",
    )
    _write_executable(
        scripts / "dogfood_smoke.sh",
        "#!/usr/bin/env bash\n"
        + _env_probe("dogfood_smoke.sh")
        + "printf 'dogfood_smoke.sh %s\\n' \"$*\" >> \"$RELEASE_TEST_LIVE_LOG\"\nexit 0\n",
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
  "rev-parse HEAD") printf '%s\\n' "${{FAKE_HEAD_SHA:-{PUSHED_SHA}}}" ;;
  "rev-parse origin/main") printf '%s\\n' "${{FAKE_ORIGIN_MAIN_SHA:-{PUSHED_SHA}}}" ;;
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
    env_log = tmp_path / "child-env.log"
    env = {
        "PATH": f"{fake_bin}:{_path_only()}",
        "RELEASE_LOOP_PROOF_TIMEOUT": "0",
        "RELEASE_LOOP_PROOF_INTERVAL": "0",
        "RELEASE_LOOP_TEST_MODE": "1",
        "RELEASE_TEST_LIVE_LOG": str(live_log),
        "RELEASE_TEST_ENV_LOG": str(env_log),
    }
    return {
        "root": fake_root,
        "env": env,
        "git_log": git_log,
        "gh_log": gh_log,
        "gh_runs": gh_runs,
        "runtime_log": runtime_log,
        "live_log": live_log,
        "env_log": env_log,
        "card_dir": fake_root / ".release",
    }


def _prepare(harness, risk="internal", *, effect=None, live_target=None, extra_env=None):
    env = dict(harness["env"])
    if extra_env:
        env.update(extra_env)
    args = [
        "--surface",
        "telegram",
        "--mode",
        "prepare",
        "--risk",
        risk,
        "--effect",
        effect or f"{risk} release: nothing a doctor sees changes shape",
    ]
    if live_target:
        args += ["--live-target", live_target]
    return run(*args, env=env)


def _prepared(harness, risk="internal", *, live_target=None, prepare_env=None):
    if risk == "telegram" and live_target is None:
        live_target = LIVE_TARGET
    result = _prepare(harness, risk, live_target=live_target, extra_env=prepare_env)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _ship(
    harness,
    risk="internal",
    *,
    extra_env=None,
    extra_args=(),
    approved=PUSHED_SHA,
    prepare_env=None,
    prepare_risk=None,
    live_target=None,
    skip_prepare=False,
):
    if not skip_prepare:
        _prepared(harness, prepare_risk or risk, live_target=live_target, prepare_env=prepare_env)
    env = dict(harness["env"])
    if extra_env:
        env.update(extra_env)
    args = ["--surface", "telegram", "--mode", "ship", "--risk", risk]
    if approved is not None:
        args += ["--approved", approved]
    return run(*args, *extra_args, env=env)


def _attest(harness, risk="telegram", *, result="pass", note="Focused case journey drafted and saved.", approved=PUSHED_SHA, extra_env=None):
    env = dict(harness["env"])
    if extra_env:
        env.update(extra_env)
    args = ["--surface", "telegram", "--mode", "attest", "--risk", risk]
    if approved is not None:
        args += ["--approved", approved]
    if result is not None:
        args += ["--result", result]
    if note is not None:
        args += ["--note", note]
    return run(*args, env=env)


def _card(harness, sha=PUSHED_SHA):
    return json.loads((harness["card_dir"] / f"{sha}.card.json").read_text())


def _env_lines(harness, label):
    if not harness["env_log"].exists():
        return []
    return [line for line in harness["env_log"].read_text().splitlines() if line.startswith(label + " ")]


# --- shape and usage -------------------------------------------------------


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
    for expected in ("--surface", "telegram", "prepare", "ship", "attest", "--risk", "internal", "broad", "--approved"):
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


@pytest.mark.parametrize("option", ["--surface", "--mode", "--risk", "--release-sha", "--approved", "--effect", "--live-target", "--result", "--note"])
def test_value_taking_options_refuse_a_missing_or_option_value(option):
    result = run(option, "--no-dogfood")
    assert result.returncode == 64
    assert f"{option} requires a value" in result.stderr


@pytest.mark.parametrize("mode", ["prepare", "ship", "attest"])
def test_every_mode_requires_explicit_risk_before_approval_or_mutation(mode):
    result = run("--surface", "telegram", "--mode", mode, "--approved", PUSHED_SHA, env={"PATH": _path_only()})
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
        PUSHED_SHA,
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
        PUSHED_SHA,
        env={"PATH": _path_only(), "RELEASE_LOOP_PROOF_TIMEOUT": "0"},
    )
    assert result.returncode == 64
    assert "positive outside explicit test mode" in result.stderr


def test_unsupported_surface_is_usage_error():
    result = run("--surface", "web", "--mode", "prepare")
    assert result.returncode == 64
    assert "Unsupported --surface" in result.stderr


# --- prepare and the card --------------------------------------------------


def test_prepare_requires_an_effect_line():
    result = run("--surface", "telegram", "--mode", "prepare", "--risk", "internal", env={"PATH": _path_only()})
    assert result.returncode == 64
    assert "--effect is required" in result.stderr


def test_prepare_requires_a_single_line_effect():
    result = run(
        "--surface", "telegram", "--mode", "prepare", "--risk", "internal",
        "--effect", "first line\nsecond line",
        env={"PATH": _path_only()},
    )
    assert result.returncode == 64
    assert "single line" in result.stderr


def test_prepare_requires_an_exact_live_target_for_telegram_risk():
    result = run(
        "--surface", "telegram", "--mode", "prepare", "--risk", "telegram",
        "--effect", "Focused case journey copy change.",
        env={"PATH": _path_only()},
    )
    assert result.returncode == 64
    assert "--live-target is required" in result.stderr


def test_prepare_persists_every_card_field_and_prints_one_ship_command(ship_harness):
    result = _prepare(
        ship_harness,
        "telegram",
        effect="Draft preview now names the chosen form.",
        live_target=LIVE_TARGET,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    card = _card(ship_harness)
    assert card["schema_version"] == 1
    assert card["sha"] == PUSHED_SHA
    assert card["surface"] == "telegram"
    assert card["risk"] == "telegram"
    assert card["effect"] == "Draft preview now names the chosen form."
    assert card["proof_mode"] in ("automated", "manual")
    assert card["live_target"] == LIVE_TARGET
    assert card["known_good_sha"] is None, "known-good is only trustworthy once verified at ship"
    assert card["exclusions"], "a card without exclusions would let one approval mean anything"
    assert card["created_at"].endswith("Z")

    assert "RELEASE CARD" in result.stdout
    assert f"--mode ship --risk telegram --approved {PUSHED_SHA}" in result.stdout
    assert "not covered" in result.stdout
    # A prepared card is not a deployment: nothing external may have been touched.
    git_log = ship_harness["git_log"].read_text()
    assert "push origin" not in git_log
    assert "checkout main" not in git_log


def test_prepare_succeeds_from_a_minimal_shell_without_real_credentials(ship_harness):
    env = dict(ship_harness["env"])
    for leaked in ("FERNET_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "GOOGLE_API_KEY"):
        assert leaked not in env
    result = _prepare(ship_harness, "internal", extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (ship_harness["card_dir"] / f"{PUSHED_SHA}.card.json").exists()
    for label in ("preflight.sh", "telegram_qa_offline.sh"):
        lines = _env_lines(ship_harness, label)
        assert lines, f"{label} did not run"
        assert all(f"FERNET={CI_FERNET_KEY}" in line for line in lines)
        assert all("TOKEN=fake" in line and "GOOGLE=fake" in line for line in lines)


def test_prepare_writes_no_card_when_the_tree_is_not_release_ready(ship_harness):
    result = _prepare(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "merge-base --is-ancestor"})
    assert result.returncode == 1
    assert "No card was written" in result.stdout
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.card.json").exists()


@pytest.mark.parametrize(
    "readiness_env",
    [
        {},
        {**TELETHON_ENV, "TELETHON_SESSION": ""},
        {**TELETHON_ENV, "TELEGRAM_BOT_USERNAME": "some_other_bot"},
        {**TELETHON_ENV, "TELEGRAM_LIVE_ALLOWED_BOTS": "some_other_bot"},
    ],
    ids=["no-credentials", "no-session", "target-mismatch", "not-allowlisted"],
)
def test_automated_proof_needs_credentials_exact_target_and_allowlist(ship_harness, readiness_env):
    _prepared(ship_harness, "telegram", prepare_env=readiness_env)
    assert _card(ship_harness)["proof_mode"] == "manual"


def test_full_readiness_prepares_an_automated_card(ship_harness):
    _prepared(ship_harness, "telegram", prepare_env=TELETHON_ENV)
    card = _card(ship_harness)
    assert card["proof_mode"] == "automated"
    assert card["live_target"] == LIVE_TARGET


# --- approval binding ------------------------------------------------------


def test_ship_refuses_without_approval():
    """ship must refuse and exit 2 before any live action when unapproved."""
    env = {"PATH": _path_only()}
    result = run("--surface", "telegram", "--mode", "ship", "--risk", "internal", env=env)
    assert result.returncode == 2
    assert "approval required" in result.stderr.lower()
    assert "FINAL_RELEASE_STATE=release-ready" in result.stdout


@pytest.mark.parametrize("token", ["telegram-19990101", "yes", "a" * 39, "z" * 40])
def test_ship_refuses_any_approval_that_is_not_a_full_release_sha(token):
    """A dated or bare approval used to cover a whole release class. Now one
    approval names exactly one SHA, so approving yesterday cannot ship today."""
    result = run(
        "--surface", "telegram", "--mode", "ship", "--risk", "internal", "--approved", token,
        env={"PATH": _path_only()},
    )
    assert result.returncode == 2
    assert "stale" in result.stderr.lower()
    assert "exact full 40-character SHA" in result.stderr


def test_release_approved_environment_variable_must_also_name_a_sha():
    result = run(
        "--surface", "telegram", "--mode", "ship", "--risk", "internal",
        env={"PATH": _path_only(), "RELEASE_APPROVED": "telegram-19990101"},
    )
    assert result.returncode == 2
    assert "stale" in result.stderr.lower()


def test_ship_refuses_an_approval_naming_a_different_sha(ship_harness):
    result = _ship(ship_harness, "internal", approved=OTHER_SHA)
    assert result.returncode == 2
    assert OTHER_SHA in result.stderr
    assert "needs a new card and a new approval" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_ship_refuses_when_no_card_was_prepared(ship_harness):
    result = _ship(ship_harness, "internal", skip_prepare=True)
    assert result.returncode == 2
    assert "never prepared" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_ship_refuses_when_cli_risk_differs_from_the_approved_card(ship_harness):
    result = _ship(ship_harness, "telegram", prepare_risk="internal")
    assert result.returncode == 2
    assert "Card risk internal does not equal --risk telegram" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_ship_refuses_a_card_whose_surface_is_not_the_cli_surface(ship_harness):
    _prepared(ship_harness, "internal")
    card_file = ship_harness["card_dir"] / f"{PUSHED_SHA}.card.json"
    card = json.loads(card_file.read_text())
    card["surface"] = "horus"
    card_file.write_text(json.dumps(card))
    result = _ship(ship_harness, "internal", skip_prepare=True)
    assert result.returncode == 2
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_ship_refuses_when_head_moved_away_from_the_approved_sha(ship_harness):
    _prepared(ship_harness, "internal")
    result = _ship(ship_harness, "internal", skip_prepare=True, extra_env={"FAKE_HEAD_SHA": OTHER_SHA})
    assert result.returncode == 2
    assert f"Approval names {PUSHED_SHA} but HEAD is {OTHER_SHA}" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


# --- ship happy path and provenance ---------------------------------------


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
    # Known-good capture before the push, then runtime identity after deploy.
    assert ship_harness["runtime_log"].read_text() == f"--expected-sha {PUSHED_SHA}\n" * 2


def test_ship_records_a_verified_known_good_sha_on_the_card_before_pushing(ship_harness):
    result = _ship(ship_harness, "internal")
    assert result.returncode == 0, result.stderr
    assert f"KNOWN_GOOD_SHA={PUSHED_SHA}" in result.stdout
    assert _card(ship_harness)["known_good_sha"] == PUSHED_SHA
    # The capture has to happen before main moves, or the rollback target is a guess.
    assert result.stdout.index("KNOWN_GOOD_SHA=") < result.stdout.index("PUSHED_SHA=")


def test_ship_blocks_before_mutation_when_the_live_runtime_does_not_match_origin_main(ship_harness):
    result = _ship(ship_harness, "internal", extra_env={"FAKE_RUNTIME_SHA": OTHER_SHA})
    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "push origin" not in ship_harness["git_log"].read_text()
    assert _card(ship_harness)["known_good_sha"] is None


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


def test_running_tests_run_is_retryable_proof_pending(ship_harness):
    runs = json.loads(_workflow_runs())
    runs[1]["status"] = "in_progress"
    runs[1]["conclusion"] = None
    (ship_harness["gh_runs"] / "Tests.json").write_text(json.dumps(runs))
    result = _ship(ship_harness, "internal")
    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout


def test_runtime_proof_missing_never_reports_live(ship_harness):
    _prepared(ship_harness, "internal")
    (ship_harness["root"] / "scripts" / "verify_live_runtime.py").unlink()
    result = _ship(ship_harness, "internal", skip_prepare=True)

    assert result.returncode == 1, result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "no rollback target" in result.stdout.lower() or "no rollback target" in result.stderr.lower()
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_post_deploy_runtime_proof_missing_is_retryable_proof_pending(ship_harness):
    """On resume there is no pre-push capture, so this exercises the post-deploy
    identity gate on its own."""
    _prepared(ship_harness, "internal")
    (ship_harness["root"] / "scripts" / "verify_live_runtime.py").unlink()
    result = _ship(ship_harness, "internal", skip_prepare=True, extra_args=("--release-sha", PUSHED_SHA))

    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "runtime" in result.stdout.lower()


def test_post_deploy_runtime_running_a_different_sha_never_reports_live(ship_harness):
    result = _ship(
        ship_harness,
        "internal",
        extra_args=("--release-sha", PUSHED_SHA),
        extra_env={"FAKE_RUNTIME_SHA": OTHER_SHA},
    )
    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout


# --- proof mode, live guard and env confinement ----------------------------


def test_telegram_risk_does_not_silently_pass_without_live_approval(ship_harness):
    result = _ship(ship_harness, "telegram")

    assert result.returncode == 4, result.stderr
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert not ship_harness["live_log"].exists()


def test_proof_pending_on_a_manual_card_asks_for_manual_proof_not_approval(ship_harness):
    """The old failure mode was telling the operator to approve again when the
    real gap was an unrun manual journey."""
    result = _ship(ship_harness, "telegram")

    assert result.returncode == 4
    assert "Manual proof required" in result.stdout
    assert "--mode attest" in result.stdout
    assert "approval required" not in result.stdout.lower()


def test_manual_card_never_runs_the_live_child_even_if_credentials_appear_later(ship_harness):
    """Prepared without readiness, so the card is manual. Credentials arriving
    between prepare and ship must not silently turn it into a live send."""
    result = _ship(ship_harness, "telegram", extra_env={**TELETHON_ENV, "TELEGRAM_LIVE_APPROVED": LIVE_APPROVAL})

    assert _card(ship_harness)["proof_mode"] == "manual"
    assert result.returncode == 4
    assert not ship_harness["live_log"].exists()
    assert "Manual proof required" in result.stdout


def test_automated_card_refuses_the_live_child_once_readiness_disappears(ship_harness):
    result = _ship(
        ship_harness,
        "telegram",
        prepare_env=TELETHON_ENV,
        extra_env={**TELETHON_ENV, "TELEGRAM_LIVE_ALLOWED_BOTS": "some_other_bot"},
    )
    assert _card(ship_harness)["proof_mode"] == "automated"
    assert result.returncode == 4
    assert "no longer complete" in result.stdout
    assert not ship_harness["live_log"].exists()


def test_automated_card_sets_the_live_guard_on_the_live_child_only(ship_harness):
    result = _ship(ship_harness, "telegram", prepare_env=TELETHON_ENV, extra_env=TELETHON_ENV)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "telegram_bot_qa.sh --focused-release" in ship_harness["live_log"].read_text()

    live_lines = _env_lines(ship_harness, "telegram_bot_qa.sh")
    assert live_lines and all(f"LIVE_APPROVED={LIVE_APPROVAL}" in line for line in live_lines)
    assert all(f"BOT_USERNAME={LIVE_TARGET}" in line for line in live_lines)

    for label in ("preflight.sh", "telegram_qa_offline.sh", "verify_live_runtime.py"):
        lines = _env_lines(ship_harness, label)
        assert lines, f"{label} did not run"
        assert all("LIVE_APPROVED=unset" in line for line in lines), f"{label} must not see the live guard"


def test_offline_fake_credentials_never_reach_the_runtime_or_live_children(ship_harness):
    result = _ship(ship_harness, "telegram", prepare_env=TELETHON_ENV, extra_env=TELETHON_ENV)
    assert result.returncode == 0, result.stdout + result.stderr

    for label in ("preflight.sh", "telegram_qa_offline.sh"):
        lines = _env_lines(ship_harness, label)
        assert lines
        assert all(f"FERNET={CI_FERNET_KEY}" in line for line in lines)

    for label in ("verify_live_runtime.py", "telegram_bot_qa.sh"):
        lines = _env_lines(ship_harness, label)
        assert lines, f"{label} did not run"
        for line in lines:
            assert "FERNET=unset" in line, f"{label} inherited a fake Fernet key: {line}"
            assert "TOKEN=unset" in line, f"{label} inherited a fake bot token: {line}"
            assert "GOOGLE=unset" in line, f"{label} inherited a fake Google key: {line}"


def test_release_loop_never_exports_the_live_guard_into_its_own_environment():
    src = SCRIPT.read_text()
    assert "export TELEGRAM_LIVE_APPROVED" not in src
    assert "export FERNET_SECRET_KEY" not in src
    assert 'env TELEGRAM_LIVE_APPROVED="$LIVE_APPROVAL_VALUE"' in src


def test_telegram_bot_qa_direct_call_guard_is_unchanged():
    """The release loop supplies the guard per-child; the QA script's own refusal
    to run live without it must stay exactly as strict."""
    src = BOT_QA.read_text()
    assert 'LIVE_APPROVAL_VALUE="portfolio-guru-live-qa-approved"' in src
    assert 'REQUIRE_LIVE="${REQUIRE_TELEGRAM_LIVE:-0}"' in src
    assert "live-telegram: SKIP (explicit approval missing)" in src
    assert "ERROR: live Telegram QA required, but approval/credentials/target allowlist are incomplete." in src
    assert "exit 20" in src


def test_broad_risk_does_not_silently_pass_without_interactive_dogfood(ship_harness):
    result = _ship(ship_harness, "broad")

    assert result.returncode == 4, result.stderr
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "interactive" in result.stdout.lower()
    assert not ship_harness["live_log"].exists()


def test_broad_release_invokes_strict_dogfood_mode_source_contract():
    assert 'dogfood_smoke.sh" --strict-release' in SCRIPT.read_text()


# --- resume ----------------------------------------------------------------


def test_proof_pending_prints_exact_resumable_command(ship_harness):
    (ship_harness["gh_runs"] / "Tests.json").write_text("[]")
    result = _ship(ship_harness, "telegram")
    assert result.returncode == 4
    assert (
        f"scripts/release_loop.sh --surface telegram --mode ship --risk telegram "
        f"--release-sha {PUSHED_SHA} --approved {PUSHED_SHA}"
    ) in result.stdout


def test_resume_exact_sha_runs_proof_without_duplicate_push(ship_harness):
    result = _ship(ship_harness, "internal", extra_args=("--release-sha", PUSHED_SHA))
    assert result.returncode == 0, result.stderr
    git_log = ship_harness["git_log"].read_text()
    assert "push origin" not in git_log
    assert "checkout main" not in git_log
    assert f"RESUMING_RELEASE_SHA={PUSHED_SHA}" in result.stdout
    # Runtime identity is re-proved on resume, and only once (no pre-push capture).
    assert ship_harness["runtime_log"].read_text() == f"--expected-sha {PUSHED_SHA}\n"


def test_resume_requires_the_prepared_card(ship_harness):
    result = _ship(ship_harness, "internal", skip_prepare=True, extra_args=("--release-sha", PUSHED_SHA))
    assert result.returncode == 2
    assert "never prepared" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_resume_refuses_a_sha_that_is_not_the_approved_sha(ship_harness):
    result = _ship(ship_harness, "internal", extra_args=("--release-sha", OTHER_SHA))
    assert result.returncode == 3
    assert "is not the approved SHA" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_resume_refuses_when_origin_main_no_longer_equals_the_release_sha(ship_harness):
    _prepared(ship_harness, "internal")
    result = _ship(
        ship_harness,
        "internal",
        skip_prepare=True,
        extra_args=("--release-sha", PUSHED_SHA),
        extra_env={"FAKE_ORIGIN_MAIN_SHA": OTHER_SHA},
    )
    assert result.returncode == 3
    assert "HEAD == origin/main == --release-sha" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


# --- push discipline -------------------------------------------------------


def test_push_failure_blocks_and_leaves_the_checkout_alone(ship_harness):
    """A failed push must block, and must not have moved the working tree.

    The reconcile used to check out main and switch back afterwards, so a
    failure could strand the checkout on the wrong branch. It no longer
    switches at all — which is also what lets a release run while the live
    deployment worktree holds main."""
    result = _ship(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "push origin"})
    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    log = ship_harness["git_log"].read_text()
    assert "push origin HEAD:refs/heads/main" in log
    assert "checkout main" not in log


def test_reconcile_never_checks_out_main(ship_harness):
    """main is held by the live deployment worktree; checking it out here would
    fail outright, and in a shared checkout it would yank the tree out from
    under anything else working there."""
    _ship(ship_harness, "internal")
    log = ship_harness["git_log"].read_text()
    assert "checkout main" not in log
    assert "merge --ff-only" not in log
    assert "push origin HEAD:refs/heads/main" in log


def test_ship_pushes_exactly_once_and_never_to_a_second_branch(ship_harness):
    """Fable's leaner alternative: the exact-SHA push to main already preserves
    the commit remotely, so no separate feature-branch backup push is taken."""
    result = _ship(ship_harness, "internal")
    assert result.returncode == 0, result.stderr
    pushes = [line for line in ship_harness["git_log"].read_text().splitlines() if line.startswith("push ")]
    assert pushes == ["push origin HEAD:refs/heads/main"]


def test_release_loop_contains_no_force_push_or_history_rewrite():
    src = SCRIPT.read_text()
    for forbidden in ("--force", "-f refs/", "push --force", "reset --hard", "git clean", "filter-branch"):
        assert forbidden not in src, f"release loop must never contain {forbidden!r}"


def test_non_fast_forward_release_is_refused_before_any_push(ship_harness):
    """Refused up front by the pre-flight ancestry guard, so nothing is pushed
    and nothing is mutated. The reconcile carries its own second check for the
    same property, since it is the last thing standing before the push."""
    _prepared(ship_harness, "internal")
    result = _ship(
        ship_harness,
        "internal",
        skip_prepare=True,
        extra_env={"FAKE_GIT_FAIL_COMMAND": "merge-base --is-ancestor"},
    )
    assert result.returncode == 3
    assert "not an ancestor of HEAD" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


# --- rollback truth --------------------------------------------------------


def test_live_proof_failure_names_the_known_good_sha_without_claiming_a_rollback(ship_harness):
    result = _ship(
        ship_harness,
        "telegram",
        prepare_env=TELETHON_ENV,
        extra_env={**TELETHON_ENV, "FAKE_LIVE_QA_EXIT": "1"},
    )
    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "stays live until a targeted rollback" in result.stdout
    assert f"known-good SHA {PUSHED_SHA}" in result.stdout
    assert "never rewrite history" in result.stdout


# --- attest ----------------------------------------------------------------


def _shipped_manual_card(ship_harness):
    """Ship a manual telegram card far enough to record the known-good SHA."""
    result = _ship(ship_harness, "telegram")
    assert result.returncode == 4, result.stdout + result.stderr
    assert _card(ship_harness)["known_good_sha"] == PUSHED_SHA
    return result


@pytest.mark.parametrize("bad_result", ["maybe", "PASS", ""])
def test_attest_rejects_an_unknown_result(bad_result):
    args = ["--surface", "telegram", "--mode", "attest", "--risk", "telegram", "--approved", PUSHED_SHA, "--note", "x"]
    if bad_result:
        args += ["--result", bad_result]
    result = run(*args, env={"PATH": _path_only()})
    assert result.returncode == 64
    assert "--result must be exactly pass or fail" in result.stderr


@pytest.mark.parametrize("bad_note", ["", "first\nsecond"])
def test_attest_rejects_empty_or_multiline_notes(bad_note):
    result = run(
        "--surface", "telegram", "--mode", "attest", "--risk", "telegram",
        "--approved", PUSHED_SHA, "--result", "pass", "--note", bad_note,
        env={"PATH": _path_only()},
    )
    assert result.returncode == 64
    assert "--note" in result.stderr


def test_attest_rejects_an_oversized_note(ship_harness):
    _shipped_manual_card(ship_harness)
    result = _attest(ship_harness, "telegram", note="x" * 500)
    assert result.returncode == 3
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


def test_attest_pass_records_manual_proof_and_says_so_honestly(ship_harness):
    _shipped_manual_card(ship_harness)
    git_log_before = ship_harness["git_log"].read_text()
    result = _attest(ship_harness, "telegram", result="pass", note="Focused case journey drafted and saved.")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "manual proof attested by operator" in result.stdout
    assert "FINAL_RELEASE_STATE=live" in result.stdout
    assert "proof=manual-operator-attestation" in result.stdout

    attestation = json.loads((ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").read_text())
    assert attestation["sha"] == PUSHED_SHA
    assert attestation["result"] == "pass"
    assert attestation["proof_kind"] == "manual-operator-attestation"
    assert attestation["note"] == "Focused case journey drafted and saved."
    assert attestation["card_proof_mode"] == "manual"
    assert attestation["attested_at"].endswith("Z")
    # Attest closes proof; it must not push, deploy, or send anything.
    attest_git = ship_harness["git_log"].read_text()[len(git_log_before):]
    assert "push" not in attest_git
    assert not ship_harness["live_log"].exists()


def test_attest_fail_blocks_and_prints_the_bounded_rollback_target(ship_harness):
    _shipped_manual_card(ship_harness)
    result = _attest(ship_harness, "telegram", result="fail", note="Draft preview showed the wrong form name.")

    assert result.returncode == 1
    assert "manual proof attested by operator" in result.stdout
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert f"known-good SHA {PUSHED_SHA}" in result.stdout
    assert json.loads((ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").read_text())["result"] == "fail"


def test_attest_refuses_an_approval_that_does_not_name_the_card(ship_harness):
    _shipped_manual_card(ship_harness)
    result = _attest(ship_harness, "telegram", approved=OTHER_SHA)
    assert result.returncode == 2
    assert not (ship_harness["card_dir"] / f"{OTHER_SHA}.attestation.json").exists()


def test_attest_refuses_when_origin_main_is_not_the_release_sha(ship_harness):
    _shipped_manual_card(ship_harness)
    result = _attest(ship_harness, "telegram", extra_env={"FAKE_ORIGIN_MAIN_SHA": OTHER_SHA})
    assert result.returncode == 3
    assert "HEAD == origin/main == approved SHA" in result.stderr
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


def test_attest_is_proof_pending_when_the_runtime_cannot_be_verified(ship_harness):
    _shipped_manual_card(ship_harness)
    (ship_harness["root"] / "scripts" / "verify_live_runtime.py").unlink()
    result = _attest(ship_harness, "telegram")
    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


def test_attest_refuses_to_stand_in_for_a_ready_automated_card(ship_harness):
    _prepared(ship_harness, "telegram", prepare_env=TELETHON_ENV)
    result = _attest(ship_harness, "telegram", extra_env=TELETHON_ENV)
    assert result.returncode == 3
    assert "manual attestation must not stand in for it" in result.stderr
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


def test_attest_refuses_internal_risk_which_has_no_manual_journey(ship_harness):
    _prepared(ship_harness, "internal")
    result = _attest(ship_harness, "internal")
    assert result.returncode == 3
    assert "no manual journey to attest" in result.stderr


# --- write surface ---------------------------------------------------------


def test_release_state_directory_is_gitignored():
    assert ".release/" in (REPO_ROOT / ".gitignore").read_text()


def test_release_loop_writes_nothing_into_the_repo_outside_the_release_directory(ship_harness):
    root = ship_harness["root"]
    before = {path.relative_to(root) for path in root.rglob("*")}
    _shipped_manual_card(ship_harness)
    _attest(ship_harness, "telegram")
    after = {path.relative_to(root) for path in root.rglob("*")}
    created = sorted(str(path) for path in after - before)
    assert created, "the release loop should have written its card and attestation"
    assert all(path == ".release" or path.startswith(".release/") for path in created), created


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
