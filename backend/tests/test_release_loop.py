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
# Rollback needs three genuinely distinct commits: the released SHA (R), the
# known-good SHA the card froze (K), and the forward rollback commit (B). Reusing
# one SHA for two of them would hide exactly the confusions this guards against.
KNOWN_GOOD_SHA = "c" * 40
ROLLBACK_SHA = "d" * 40
KNOWN_GOOD_TREE = "e" * 40
UNRELATED_TREE = "1" * 40
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


# The fake git is stateful on purpose. A rollback that claims to be resumable is
# only worth testing against a repo whose HEAD and origin/main actually move when
# it moves them, so `update-ref` and an exact-SHA `push` record where they landed
# and later invocations read that back.
_FAKE_GIT = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "@@GIT_LOG@@"
if [[ -n "${FAKE_GIT_FAIL_COMMAND:-}" && "$1 $2" == "$FAKE_GIT_FAIL_COMMAND" ]]; then exit 9; fi

HEAD_STATE="@@HEAD_STATE@@"
ORIGIN_STATE="@@ORIGIN_STATE@@"
R="@@R@@"
K="@@K@@"
B="@@B@@"
K_TREE="@@K_TREE@@"
OTHER_TREE="@@OTHER_TREE@@"

current_head() {
  if [[ -n "${FAKE_HEAD_SHA:-}" ]]; then printf '%s\\n' "$FAKE_HEAD_SHA"
  elif [[ -s "$HEAD_STATE" ]]; then printf '%s\\n' "$(/bin/cat "$HEAD_STATE")"
  else printf '%s\\n' "$R"; fi
}
current_origin() {
  if [[ -n "${FAKE_ORIGIN_MAIN_SHA:-}" ]]; then printf '%s\\n' "$FAKE_ORIGIN_MAIN_SHA"
  elif [[ -s "$ORIGIN_STATE" ]]; then printf '%s\\n' "$(/bin/cat "$ORIGIN_STATE")"
  else printf '%s\\n' "$R"; fi
}

case "$1" in
  rev-parse)
    case "$2" in
      --show-toplevel) printf '%s\\n' "@@ROOT@@" ;;
      --short) printf '%s\\n' "@@SHORT@@" ;;
      --verify) exit 0 ;;
      HEAD) current_head ;;
      origin/main) current_origin ;;
      *'^{tree}')
        ref="${2%'^{tree}'}"
        case "$ref" in
          "$B") printf '%s\\n' "${FAKE_ROLLBACK_TREE:-$K_TREE}" ;;
          "$K") printf '%s\\n' "${FAKE_KNOWN_GOOD_TREE:-$K_TREE}" ;;
          *) printf '%s\\n' "$OTHER_TREE" ;;
        esac ;;
      *'^')
        ref="${2%'^'}"
        case "$ref" in
          "$B") printf '%s\\n' "${FAKE_ROLLBACK_PARENT:-$R}" ;;
          *) printf '%s\\n' "$OTHER_TREE" ;;
        esac ;;
      *) printf '%s\\n' "$2" ;;
    esac
    exit 0 ;;
  branch) printf 'fix/release-proof\\n'; exit 0 ;;
  status)
    if [[ -s "$HEAD_STATE" && -n "${FAKE_TREE_STATUS_AFTER_MOVE:-}" ]]; then
      printf '%s\\n' "$FAKE_TREE_STATUS_AFTER_MOVE"
    fi
    if [[ -n "${FAKE_TREE_STATUS:-}" ]]; then printf '%s\\n' "$FAKE_TREE_STATUS"; fi
    exit 0 ;;
  ls-files) exit 0 ;;
  fetch) exit 0 ;;
  merge-base) exit 0 ;;
  rev-list) printf '0 1\\n'; exit 0 ;;
  commit-tree)
    if [[ -n "${FAKE_COMMIT_TREE_BROKEN:-}" ]]; then exit 1; fi
    printf '%s\\n' "${FAKE_COMMIT_TREE_SHA:-$B}"; exit 0 ;;
  read-tree)
    if [[ -n "${FAKE_READ_TREE_FAIL:-}" && "$3" == "-m" ]]; then exit 1; fi
    exit 0 ;;
  update-ref)
    if [[ -n "${FAKE_UPDATE_REF_FAIL:-}" ]]; then exit 1; fi
    printf '%s' "${@: -2:1}" > "$HEAD_STATE"
    exit 0 ;;
  push)
    case "$3" in
      *:refs/heads/main)
        src="${3%%:*}"
        if [[ "$src" =~ ^[0-9a-f]{40}$ ]]; then printf '%s' "$src" > "$ORIGIN_STATE"; fi ;;
    esac
    exit 0 ;;
  *) exit 0 ;;
esac
"""


def _fake_git(root, git_log, head_state, origin_state) -> str:
    script = _FAKE_GIT
    for token, value in (
        ("@@GIT_LOG@@", str(git_log)),
        ("@@HEAD_STATE@@", str(head_state)),
        ("@@ORIGIN_STATE@@", str(origin_state)),
        ("@@ROOT@@", str(root)),
        ("@@SHORT@@", PUSHED_SHA[:7]),
        ("@@R@@", PUSHED_SHA),
        ("@@K@@", KNOWN_GOOD_SHA),
        ("@@B@@", ROLLBACK_SHA),
        ("@@K_TREE@@", KNOWN_GOOD_TREE),
        ("@@OTHER_TREE@@", UNRELATED_TREE),
    ):
        script = script.replace(token, value)
    return script


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
    head_state = tmp_path / "fake-head"
    origin_state = tmp_path / "fake-origin-main"
    _write_executable(fake_bin / "git", _fake_git(fake_root, git_log, head_state, origin_state))
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
        "head_state": head_state,
        "origin_state": origin_state,
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


# Prepare while origin/main is the known-good SHA, so the card freezes a rollback
# target that is genuinely a different commit from the release.
ROLLBACK_PREPARE_ENV = {"FAKE_ORIGIN_MAIN_SHA": KNOWN_GOOD_SHA}


def _rollback_workflows(harness, sha=ROLLBACK_SHA):
    """Point the fake GitHub fixtures at the rollback commit."""
    tests = _workflow_runs(sha)
    deploy = _workflow_runs(
        sha, event="workflow_run", created_at="2026-08-13T10:06:00Z", updated_at="2026-08-13T10:10:00Z"
    )
    (harness["gh_runs"] / "Tests.json").write_text(tests)
    (harness["gh_runs"] / "Deploy Mac Mini.json").write_text(deploy)
    (harness["gh_runs"] / "Tests.api.json").write_text(_api_payload(tests))
    (harness["gh_runs"] / "Deploy Mac Mini.api.json").write_text(_api_payload(deploy))


def _rollback_ready(harness, risk="internal", *, live_target=None):
    """An approved card whose frozen known-good SHA really precedes the release."""
    _prepared(harness, risk, live_target=live_target, prepare_env=ROLLBACK_PREPARE_ENV)
    assert _card(harness)["known_good_sha"] == KNOWN_GOOD_SHA
    _rollback_workflows(harness)


def _rollback(harness, risk="internal", *, approved=PUSHED_SHA, extra_env=None, extra_args=()):
    env = dict(harness["env"])
    if extra_env:
        env.update(extra_env)
    args = ["--surface", "telegram", "--mode", "rollback", "--risk", risk]
    if approved is not None:
        args += ["--approved", approved]
    return run(*args, *extra_args, env=env)


def _rollback_state(harness, sha=PUSHED_SHA):
    return json.loads((harness["card_dir"] / f"{sha}.rollback.json").read_text())


def _git_lines(harness, prefix):
    if not harness["git_log"].exists():
        return []
    return [line for line in harness["git_log"].read_text().splitlines() if line.startswith(prefix)]


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
    for expected in (
        "--surface", "telegram", "prepare", "ship", "attest", "rollback",
        "--risk", "internal", "broad", "--approved",
    ):
        assert expected in out
    assert "--mode rollback --risk <class> --approved <40hex>" in out


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


@pytest.mark.parametrize("mode", ["prepare", "ship", "attest", "rollback"])
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
    assert card["schema_version"] == 2
    assert card["sha"] == PUSHED_SHA
    assert card["surface"] == "telegram"
    assert card["risk"] == "telegram"
    assert card["effect"] == "Draft preview now names the chosen form."
    assert card["proof_mode"] in ("automated", "manual")
    assert card["live_target"] == LIVE_TARGET
    assert card["known_good_sha"] == PUSHED_SHA, "approval must see the already-verified rollback target"
    assert card["rollback_mode"] == "operator-triggered", "the card must say rollback is never silent"
    assert card["exclusions"], "a card without exclusions would let one approval mean anything"
    assert card["created_at"].endswith("Z")

    assert "RELEASE CARD" in result.stdout
    assert f"--mode ship --risk telegram --approved {PUSHED_SHA}" in result.stdout
    assert "not covered" in result.stdout
    # The one approval also covers rollback, so the card must print the exact
    # command rather than leaving the operator to invent one under pressure.
    assert f"--mode rollback --risk telegram --approved {PUSHED_SHA}" in result.stdout
    assert "operator-triggered" in result.stdout
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


def test_prepare_writes_no_card_when_live_known_good_cannot_be_verified(ship_harness):
    result = _prepare(ship_harness, "internal", extra_env={"FAKE_RUNTIME_SHA": OTHER_SHA})
    assert result.returncode == 1
    assert "live runtime did not verify" in result.stdout
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.card.json").exists()
    assert "push origin" not in ship_harness["git_log"].read_text()


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


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda card: card.update(known_good_sha=None), "known-good sha"),
        (lambda card: card.update(proof_mode="manual"), "internal-risk cards must use automated proof"),
        (lambda card: card.update(unapproved_extra="anything"), "fields do not exactly match"),
    ],
    ids=["missing-rollback-target", "proof-mode-drift", "extra-field"],
)
def test_ship_refuses_tampering_with_immutable_card_fields(ship_harness, mutation, expected):
    _prepared(ship_harness, "internal")
    card_file = ship_harness["card_dir"] / f"{PUSHED_SHA}.card.json"
    card = json.loads(card_file.read_text())
    mutation(card)
    card_file.write_text(json.dumps(card))

    result = _ship(ship_harness, "internal", skip_prepare=True)

    assert result.returncode == 2
    assert expected in result.stderr
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
    # Prepare freezes known-good before approval, ship re-verifies it before the
    # push, then runtime identity is proved after deploy.
    assert ship_harness["runtime_log"].read_text() == f"--expected-sha {PUSHED_SHA}\n" * 3


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
    assert _card(ship_harness)["known_good_sha"] == PUSHED_SHA


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
    # Prepare froze the rollback target; resume adds one current-runtime proof
    # and never performs a pre-push capture or push.
    assert ship_harness["runtime_log"].read_text() == f"--expected-sha {PUSHED_SHA}\n" * 2


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
    # The receipt has to hand over the exact recovery command, not a description
    # of one: this is read at the worst possible moment.
    assert (
        f"ROLLBACK_COMMAND=scripts/release_loop.sh --surface telegram --mode rollback "
        f"--risk telegram --approved {PUSHED_SHA}"
    ) in result.stdout
    assert "operator-triggered, never silent" in result.stdout


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
    assert "proof mode cannot be changed after approval" in result.stderr
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


def test_attest_refuses_to_downgrade_an_automated_card_when_readiness_disappears(ship_harness):
    _prepared(ship_harness, "telegram", prepare_env=TELETHON_ENV)
    result = _attest(
        ship_harness,
        "telegram",
        extra_env={**TELETHON_ENV, "TELEGRAM_LIVE_ALLOWED_BOTS": "some_other_bot"},
    )
    assert result.returncode == 3
    assert "proof mode cannot be changed after approval" in result.stderr
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


def test_attest_refuses_internal_risk_which_has_no_manual_journey(ship_harness):
    _prepared(ship_harness, "internal")
    result = _attest(ship_harness, "internal")
    assert result.returncode == 3
    assert "no manual journey to attest" in result.stderr


# --- rollback: refusals before any mutation --------------------------------


def test_rollback_refuses_without_approval():
    result = run("--surface", "telegram", "--mode", "rollback", "--risk", "internal", env={"PATH": _path_only()})
    assert result.returncode == 2
    assert "approval required" in result.stderr.lower()


def test_rollback_refuses_when_no_card_was_prepared(ship_harness):
    result = _rollback(ship_harness, "internal")
    assert result.returncode == 2
    assert "never prepared" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_an_approval_naming_a_different_sha(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", approved=OTHER_SHA)
    assert result.returncode == 2
    assert "needs a new card and a new approval" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_when_cli_risk_differs_from_the_approved_card(ship_harness):
    _rollback_ready(ship_harness, "internal")
    result = _rollback(ship_harness, "telegram")
    assert result.returncode == 2
    assert "Card risk internal does not equal --risk telegram" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []


def test_rollback_refuses_a_card_that_does_not_authorise_operator_triggered_rollback(ship_harness):
    _rollback_ready(ship_harness)
    card_file = ship_harness["card_dir"] / f"{PUSHED_SHA}.card.json"
    card = json.loads(card_file.read_text())
    card["rollback_mode"] = "silent"
    card_file.write_text(json.dumps(card))

    result = _rollback(ship_harness, "internal")

    assert result.returncode == 2
    assert "rollback mode" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_when_the_card_known_good_sha_is_the_released_sha(ship_harness):
    """Nothing to roll back to is a refusal, not a no-op that reports success."""
    _prepared(ship_harness, "internal")
    result = _rollback(ship_harness, "internal")
    assert result.returncode == 2
    assert "nothing to roll back to" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []


def test_rollback_refuses_when_the_known_good_sha_is_not_an_ancestor(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "merge-base --is-ancestor"})
    assert result.returncode == 3
    assert "not an ancestor" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_an_uncommitted_tracked_tree(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_TREE_STATUS": " M backend/bot.py"})
    assert result.returncode == 3
    assert "uncommitted tracked changes" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []


def test_rollback_refuses_when_origin_main_drifted_to_an_unknown_sha(ship_harness):
    """Rolling back a release nobody here approved would land main somewhere new."""
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_ORIGIN_MAIN_SHA": OTHER_SHA})
    assert result.returncode == 3
    assert "neither the released SHA" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_when_head_is_not_the_released_sha(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_HEAD_SHA": OTHER_SHA})
    assert result.returncode == 3
    assert "rollback expects the released SHA" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []


def test_rollback_refuses_when_the_released_sha_cannot_be_shown_to_be_live(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_RUNTIME_SHA": OTHER_SHA})
    assert result.returncode == 4
    assert "cannot be shown to be running" in result.stderr
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert _git_lines(ship_harness, "commit-tree ") == []
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_when_the_runtime_already_reports_the_known_good_sha(ship_harness):
    """main says one thing and the Mac Mini says another. That is a deployment
    to reconcile, not the state this card's rollback describes."""
    _rollback_ready(ship_harness)
    result = _rollback(
        ship_harness,
        "internal",
        extra_env={"FAKE_RUNTIME_SHA": KNOWN_GOOD_SHA, "FAKE_CHECKOUT_SHA": KNOWN_GOOD_SHA},
    )

    assert result.returncode == 3
    assert "deployment inconsistency" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_a_tampered_rollback_state(ship_harness):
    _rollback_ready(ship_harness)
    assert _rollback(ship_harness, "internal").returncode == 0
    state_file = ship_harness["card_dir"] / f"{PUSHED_SHA}.rollback.json"
    state = json.loads(state_file.read_text())
    state["rollback_sha"] = OTHER_SHA
    state_file.write_text(json.dumps(state))

    before = len(_git_lines(ship_harness, "commit-tree "))
    result = _rollback(ship_harness, "internal")

    assert result.returncode == 3
    assert "recorded rollback commit" in result.stderr
    assert len(_git_lines(ship_harness, "commit-tree ")) == before, "a tampered state must not mint a new commit"


def test_rollback_refuses_a_rollback_state_whose_target_is_not_the_card_target(ship_harness):
    _rollback_ready(ship_harness)
    assert _rollback(ship_harness, "internal").returncode == 0
    state_file = ship_harness["card_dir"] / f"{PUSHED_SHA}.rollback.json"
    state = json.loads(state_file.read_text())
    state["known_good_sha"] = OTHER_SHA
    state_file.write_text(json.dumps(state))

    result = _rollback(ship_harness, "internal")

    assert result.returncode == 3
    assert "the approved card names" in result.stderr


def test_rollback_ignores_release_sha_which_belongs_to_ship_resume():
    result = run(
        "--surface", "telegram", "--mode", "rollback", "--risk", "internal",
        "--approved", PUSHED_SHA, "--release-sha", PUSHED_SHA,
        env={"PATH": _path_only()},
    )
    assert result.returncode == 64
    assert "--release-sha is not used by rollback" in result.stderr


# --- rollback: the forward commit ------------------------------------------


def test_rollback_makes_one_forward_commit_with_the_released_parent_and_known_good_tree(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    commits = _git_lines(ship_harness, "commit-tree ")
    assert len(commits) == 1, commits
    # The commit is built from the known-good tree onto the released SHA: a
    # normal forward commit, never a reset of shared history.
    assert commits[0].startswith(f"commit-tree {KNOWN_GOOD_TREE} -p {PUSHED_SHA} -m ")
    assert f"ROLLBACK_COMMIT={ROLLBACK_SHA}" in result.stdout
    assert "update-ref" in ship_harness["git_log"].read_text()
    assert "checkout main" not in ship_harness["git_log"].read_text()


def test_rollback_refuses_a_commit_whose_parent_is_not_the_released_sha(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_ROLLBACK_PARENT": OTHER_SHA})

    assert result.returncode == 3
    assert "parent is" in result.stderr
    assert _git_lines(ship_harness, "push ") == []
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.rollback.json").exists()


def test_rollback_refuses_a_commit_whose_tree_is_not_the_known_good_tree(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_ROLLBACK_TREE": UNRELATED_TREE})

    assert result.returncode == 3
    assert "not the known-good tree" in result.stderr
    assert _git_lines(ship_harness, "push ") == []
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.rollback.json").exists()


def test_rollback_restores_the_tracked_preimage_when_the_tree_cannot_be_written(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_READ_TREE_FAIL": "1"})

    assert result.returncode == 3
    assert "restoring the tracked preimage" in result.stderr
    assert "read-tree -u --reset HEAD" in ship_harness["git_log"].read_text()
    assert _git_lines(ship_harness, "push ") == []
    assert not ship_harness["head_state"].exists(), "the branch must not have moved"


def test_rollback_restores_the_tracked_preimage_when_the_branch_cannot_be_moved(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_UPDATE_REF_FAIL": "1"})

    assert result.returncode == 3
    assert "restoring the tracked preimage" in result.stderr
    assert "read-tree -u --reset HEAD" in ship_harness["git_log"].read_text()
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_when_the_tree_does_not_match_after_the_commit(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(
        ship_harness, "internal", extra_env={"FAKE_TREE_STATUS_AFTER_MOVE": " M backend/bot.py"}
    )

    assert result.returncode == 3
    assert "does not exactly match the known-good tree" in result.stderr
    assert _git_lines(ship_harness, "push ") == []


# --- rollback: push, state and proof ---------------------------------------


def test_rollback_pushes_the_exact_rollback_sha_once_and_reconciles_after(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"]
    assert f"ROLLBACK_PUSHED_SHA={ROLLBACK_SHA}" in result.stdout
    assert ship_harness["origin_state"].read_text() == ROLLBACK_SHA
    assert ship_harness["head_state"].read_text() == ROLLBACK_SHA


def test_rollback_state_is_written_before_the_push(ship_harness):
    """A crash between commit and push must leave a record to resume from, not a
    dangling commit nobody remembers making."""
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "push origin"})

    assert result.returncode == 1
    state = _rollback_state(ship_harness)
    assert state["status"] == "committed"
    assert state["released_sha"] == PUSHED_SHA
    assert state["known_good_sha"] == KNOWN_GOOD_SHA
    assert state["rollback_sha"] == ROLLBACK_SHA
    assert state["schema_version"] == 2
    assert "main is unchanged" in result.stderr
    assert set(state) == {
        "schema_version", "released_sha", "known_good_sha", "rollback_sha",
        "surface", "risk", "status", "created_at", "updated_at",
    }


def test_rollback_proof_is_keyed_to_the_rollback_commit_and_runs_no_live_journey(ship_harness):
    _rollback_ready(ship_harness, "telegram", live_target=LIVE_TARGET)
    result = _rollback(ship_harness, "telegram", extra_env=TELETHON_ENV)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"head_sha={ROLLBACK_SHA}" in result.stdout
    gh_log = ship_harness["gh_log"].read_text()
    assert "--workflow Tests" in gh_log
    assert "--workflow Deploy Mac Mini" in gh_log
    # Runtime identity is proved for the rollback commit, never for the release.
    runtime_calls = ship_harness["runtime_log"].read_text().splitlines()
    assert runtime_calls[-1] == f"--expected-sha {ROLLBACK_SHA}"
    # A rollback restores a tree that already passed its own proof. It must never
    # be the reason a message reaches a real doctor.
    assert not ship_harness["live_log"].exists()


def test_rollback_reports_rolled_back_only_after_the_runtime_proves_it(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in result.stdout
    assert f"RELEASED_SHA={PUSHED_SHA}" in result.stdout
    assert f"ROLLBACK_COMMIT_SHA={ROLLBACK_SHA}" in result.stdout
    assert f"KNOWN_GOOD_TREE_SHA={KNOWN_GOOD_SHA}" in result.stdout
    assert _rollback_state(ship_harness)["status"] == "proved"


@pytest.mark.parametrize("failed_workflow", ["Tests", "Deploy Mac Mini"])
def test_rollback_never_reports_rolled_back_when_ci_or_deploy_failed(ship_harness, failed_workflow):
    _rollback_ready(ship_harness)
    (ship_harness["gh_runs"] / f"{failed_workflow}.json").write_text(
        _workflow_runs(
            ROLLBACK_SHA,
            conclusion="failure",
            event="push" if failed_workflow == "Tests" else "workflow_run",
            created_at="2026-08-13T10:00:00Z" if failed_workflow == "Tests" else "2026-08-13T10:06:00Z",
        )
    )
    result = _rollback(ship_harness, "internal")

    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "FINAL_RELEASE_STATE=rolled-back" not in result.stdout
    assert f"main is {ROLLBACK_SHA}" in result.stderr
    assert "The rollback is not live" in result.stderr


def test_rollback_says_main_is_b_but_runtime_is_still_the_released_sha(ship_harness):
    """The worst outcome: the rollback commit is on main, the deploy put the
    released code back, and the loop must not call that a rollback."""
    _rollback_ready(ship_harness)
    assert _rollback(ship_harness, "internal").returncode == 0

    result = _rollback(ship_harness, "internal", extra_env={"FAKE_RUNTIME_SHA": PUSHED_SHA})

    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "FINAL_RELEASE_STATE=rolled-back" not in result.stdout
    assert f"main is {ROLLBACK_SHA}, but the live runtime is still the released SHA {PUSHED_SHA}" in result.stderr
    assert "Nothing on the Mac Mini has been reverted" in result.stderr


# --- rollback: resume and idempotence --------------------------------------


def test_rollback_resumes_a_local_commit_that_was_never_pushed(ship_harness):
    _rollback_ready(ship_harness)
    blocked = _rollback(ship_harness, "internal", extra_env={"FAKE_UPDATE_REF_FAIL": "1"})
    assert blocked.returncode == 3
    assert _rollback_state(ship_harness)["status"] == "committed"
    assert not ship_harness["head_state"].exists()

    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in result.stdout
    assert "Reusing recorded rollback commit" in result.stdout
    assert len(_git_lines(ship_harness, "commit-tree ")) == 1, "a resume must never make a second commit"
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"]


def test_rollback_resumes_proof_after_a_push_without_a_duplicate_push(ship_harness):
    _rollback_ready(ship_harness)
    (ship_harness["gh_runs"] / "Tests.json").write_text("[]")
    (ship_harness["gh_runs"] / "Tests.api.json").write_text(json.dumps({"workflow_runs": []}))
    pending = _rollback(ship_harness, "internal")
    assert pending.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in pending.stdout
    assert _rollback_state(ship_harness)["status"] == "pushed"
    assert (
        f"ROLLBACK_RESUME_COMMAND=scripts/release_loop.sh --surface telegram --mode rollback "
        f"--risk internal --approved {PUSHED_SHA}"
    ) in pending.stdout

    _rollback_workflows(ship_harness)
    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in result.stdout
    assert "skipping a duplicate push" in result.stdout
    assert len(_git_lines(ship_harness, "commit-tree ")) == 1
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"]


def test_rerunning_a_completed_rollback_is_idempotent(ship_harness):
    _rollback_ready(ship_harness)
    first = _rollback(ship_harness, "internal")
    assert first.returncode == 0, first.stdout + first.stderr

    second = _rollback(ship_harness, "internal")

    assert second.returncode == 0, second.stdout + second.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in second.stdout
    assert len(_git_lines(ship_harness, "commit-tree ")) == 1
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"]
    assert _rollback_state(ship_harness)["status"] == "proved"


def test_rollback_never_force_pushes_or_rewrites_history():
    src = SCRIPT.read_text()
    assert "commit-tree" in src, "rollback must build a normal forward commit"
    for forbidden in ("push --force", "--force-with-lease", "filter-branch", "update-ref -d"):
        assert forbidden not in src, f"rollback must never contain {forbidden!r}"
    assert "git push origin \"$rollback_sha:refs/heads/main\"" in src


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
