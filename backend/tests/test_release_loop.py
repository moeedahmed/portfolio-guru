"""Offline behavioural guards for scripts/release_loop.sh.

Approved ship-path tests use isolated fake repositories and executables. They
never fetch, push, deploy, restart, call GitHub, or contact Telegram/Kaizen.

The harness copies the real scripts/release_card.py into the fake repo, so card
schema, approval binding and attestation are exercised as shipped code rather
than re-implemented here. The fake git stub models refs and pushes; everything
that needs real Git semantics — the pinned bootstrap reading from objects, the
deterministic non-mutating rollback commit, interruption before the journal —
lives in test_release_loop_git.py against a real local bare remote.

The harness injects the environment the bootstrap would have set
(RELEASE_LOOP_BOOTSTRAP, the pinned SHA, absolute git/python/bash paths and the
card helper path) so the loop's own contract can be driven directly here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "release_loop.sh"
CARD_TOOL = REPO_ROOT / "scripts" / "release_card.py"
BOOTSTRAP = REPO_ROOT / "scripts" / "release_bootstrap.py"
BOT_QA = REPO_ROOT / "scripts" / "telegram_bot_qa.sh"
PYTHON = os.path.realpath(sys.executable)
BASH = os.path.realpath(shutil.which("bash") or "/bin/bash")
PUSHED_SHA = "a" * 40
OTHER_SHA = "b" * 40
# Rollback needs three genuinely distinct commits: the released SHA (R), the
# known-good SHA the card froze (K), and the forward rollback commit (B). Reusing
# one SHA for two of them would hide exactly the confusions this guards against.
KNOWN_GOOD_SHA = "c" * 40
ROLLBACK_SHA = "d" * 40
KNOWN_GOOD_TREE = "e" * 40
UNRELATED_TREE = "1" * 40
ZERO_DIGEST = "0" * 64
LIVE_TARGET = "portfolio_guru_bot"
LIVE_APPROVAL = "portfolio-guru-live-qa-approved"
CI_FERNET_KEY = "5Wv33F9sq99WGD2lEzwwd3J_JH5p6vxKdDiAwCWqoYQ="
TELETHON_ENV = {
    "TELETHON_SESSION": "test-session",
    "TELEGRAM_API_ID": "123",
    "TELEGRAM_API_HASH": "test-hash",
    "TELEGRAM_BOT_USERNAME": LIVE_TARGET,
}

# Sentinel: "approve exactly the card on disk", computed per call.
APPROVED = object()


def run(*args, env=None, script=None):
    """Run release_loop.sh from the repo root, capturing output and exit code."""
    return subprocess.run(
        ["bash", str(script or SCRIPT), *args],
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
        "printf '%s FERNET=%s TOKEN=%s GOOGLE=%s LIVE_APPROVED=%s BOT_USERNAME=%s RELEASE_TARGET=%s RELEASE_ALLOWLIST=%s\\n' "
        f"'{label}' "
        '"${FERNET_SECRET_KEY:-unset}" "${TELEGRAM_BOT_TOKEN:-unset}" "${GOOGLE_API_KEY:-unset}" '
        '"${TELEGRAM_LIVE_APPROVED:-unset}" "${TELEGRAM_BOT_USERNAME:-unset}" '
        '"${RELEASE_LIVE_TARGET:-unset}" "${RELEASE_LIVE_ALLOWLIST:-unset}" '
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
# only worth testing against a repo whose origin/main actually moves when the
# loop pushes to it, so an exact-SHA `push` records where main landed and later
# invocations read that back. HEAD never moves: the rollback design leaves the
# checkout and every local ref alone, and a fake that could move HEAD would hide
# a regression back towards the old read-tree/update-ref design.
_FAKE_GIT = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "@@GIT_LOG@@"
if [[ -n "${FAKE_GIT_FAIL_COMMAND:-}" && "$1 $2" == "$FAKE_GIT_FAIL_COMMAND" ]]; then exit 9; fi

ORIGIN_STATE="@@ORIGIN_STATE@@"
R="@@R@@"
K="@@K@@"
B="@@B@@"
K_TREE="@@K_TREE@@"
OTHER_TREE="@@OTHER_TREE@@"

current_head() { printf '%s\\n' "${FAKE_HEAD_SHA:-$R}"; }
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
      *) printf '%s\\n' "$2" ;;
    esac
    exit 0 ;;
  branch) printf 'fix/release-proof\\n'; exit 0 ;;
  status)
    if [[ -n "${FAKE_TREE_STATUS:-}" ]]; then printf '%s\\n' "$FAKE_TREE_STATUS"; fi
    exit 0 ;;
  ls-files) exit 0 ;;
  fetch) exit 0 ;;
  merge-base) exit 0 ;;
  show) printf '2026-08-13T09:00:00+00:00\\n'; exit 0 ;;
  rev-list)
    if [[ "$2" == "--parents" ]]; then
      printf '%s %s\\n' "$5" "${FAKE_ROLLBACK_PARENTS:-${FAKE_ROLLBACK_PARENT:-$R}}"
      exit 0
    fi
    printf '0 1\\n'; exit 0 ;;
  commit-tree)
    if [[ -n "${FAKE_COMMIT_TREE_BROKEN:-}" ]]; then exit 1; fi
    printf '%s\\n' "${FAKE_COMMIT_TREE_SHA:-$B}"; exit 0 ;;
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


def _fake_git(root, git_log, origin_state) -> str:
    script = _FAKE_GIT
    for token, value in (
        ("@@GIT_LOG@@", str(git_log)),
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
    tmp_path = tmp_path.resolve()
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
    origin_state = tmp_path / "fake-origin-main"
    _write_executable(fake_bin / "git", _fake_git(fake_root, git_log, origin_state))
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
    (gh_runs / "Tests.api.json").write_text(_api_payload(_workflow_runs()))
    (gh_runs / "Deploy Mac Mini.api.json").write_text(
        _api_payload(
            _workflow_runs(event="workflow_run", created_at="2026-08-13T10:06:00Z", updated_at="2026-08-13T10:10:00Z")
        )
    )

    live_log = tmp_path / "live.log"
    env_log = tmp_path / "child-env.log"
    env = {
        "PATH": f"{fake_bin}:{_path_only()}",
        "RELEASE_LOOP_PROOF_TIMEOUT": "0",
        "RELEASE_LOOP_PROOF_INTERVAL": "0",
        "RELEASE_LOOP_TEST_MODE": "1",
        "RELEASE_TEST_LIVE_LOG": str(live_log),
        "RELEASE_TEST_ENV_LOG": str(env_log),
        # What the pinned bootstrap would have set: mutating modes refuse without it.
        "RELEASE_LOOP_BOOTSTRAP": "1",
        "RELEASE_LOOP_PINNED_SHA": PUSHED_SHA,
        "RELEASE_LOOP_GIT": str(fake_bin / "git"),
        "RELEASE_LOOP_PYTHON": PYTHON,
        "RELEASE_LOOP_BASH": BASH,
        "RELEASE_LOOP_CARD_TOOL": str(scripts / "release_card.py"),
    }
    return {
        "root": fake_root,
        "env": env,
        "fake_bin": fake_bin,
        "git_log": git_log,
        "gh_log": gh_log,
        "gh_runs": gh_runs,
        "runtime_log": runtime_log,
        "live_log": live_log,
        "env_log": env_log,
        "card_dir": fake_root / ".release",
        "origin_state": origin_state,
    }


def _card_file(harness, sha=PUSHED_SHA):
    return harness["card_dir"] / f"{sha}.card.json"


def _card(harness, sha=PUSHED_SHA):
    return json.loads(_card_file(harness, sha).read_text())


def _digest(card: dict) -> str:
    """Independent statement of the canonical form: sorted keys, compact
    separators, ASCII escapes, UTF-8, one trailing newline, SHA-256."""
    canonical = json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _approval(harness, sha=PUSHED_SHA):
    """The token prepare prints: the card SHA plus the canonical digest of the
    card as it is on disk right now. `sha` lets a test name a different commit
    while keeping a real digest."""
    card_file = _card_file(harness)
    digest = _digest(json.loads(card_file.read_text())) if card_file.exists() else ZERO_DIGEST
    return f"{sha}:{digest}"


def _resolve_approval(harness, approved):
    if approved is APPROVED:
        return _approval(harness)
    return approved


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
    approved=APPROVED,
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
    approved = _resolve_approval(harness, approved)
    if approved is not None:
        args += ["--approved", approved]
    return run(*args, *extra_args, env=env)


def _attest(harness, risk="telegram", *, result="pass", note="Focused case journey drafted and saved.", approved=APPROVED, extra_env=None):
    env = dict(harness["env"])
    if extra_env:
        env.update(extra_env)
    args = ["--surface", "telegram", "--mode", "attest", "--risk", risk]
    approved = _resolve_approval(harness, approved)
    if approved is not None:
        args += ["--approved", approved]
    if result is not None:
        args += ["--result", result]
    if note is not None:
        args += ["--note", note]
    return run(*args, env=env)


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


def _rollback(harness, risk="internal", *, approved=APPROVED, extra_env=None, extra_args=()):
    env = dict(harness["env"])
    if extra_env:
        env.update(extra_env)
    args = ["--surface", "telegram", "--mode", "rollback", "--risk", risk]
    approved = _resolve_approval(harness, approved)
    if approved is not None:
        args += ["--approved", approved]
    return run(*args, *extra_args, env=env)


def _rollback_state_file(harness, sha=PUSHED_SHA):
    return harness["card_dir"] / f"{sha}.rollback.json"


def _rollback_state(harness, sha=PUSHED_SHA):
    return json.loads(_rollback_state_file(harness, sha).read_text())


def _git_lines(harness, prefix):
    if not harness["git_log"].exists():
        return []
    return [line for line in harness["git_log"].read_text().splitlines() if line.startswith(prefix)]


def _env_lines(harness, label):
    if not harness["env_log"].exists():
        return []
    return [line for line in harness["env_log"].read_text().splitlines() if line.startswith(label + " ")]


def _bootstrap_prefix(sha=PUSHED_SHA):
    return f"show {sha}:scripts/release_bootstrap.py | "


# --- shape and usage -------------------------------------------------------


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert SCRIPT.stat().st_mode & 0o111, "release_loop.sh should be executable"


def test_bootstrap_helper_exists_and_is_stdlib_only():
    src = BOOTSTRAP.read_text()
    assert "import subprocess" in src
    for forbidden in ("requests", "urllib", "http.client", "socket"):
        assert f"import {forbidden}" not in src


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
    assert "--mode rollback --risk <class> --approved <sha>:<digest>" in out
    assert "SHA-only" in out


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
        "--surface", "telegram", "--mode", "ship", "--risk", "internal", "--approved", PUSHED_SHA,
        env={"PATH": _path_only(), **extra_env},
    )
    assert result.returncode == 64
    assert "base-10 whole seconds" in result.stderr


def test_zero_proof_bound_requires_explicit_test_mode():
    result = run(
        "--surface", "telegram", "--mode", "ship", "--risk", "internal", "--approved", PUSHED_SHA,
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
    assert card["schema_version"] == 3
    assert card["sha"] == PUSHED_SHA
    assert card["surface"] == "telegram"
    assert card["risk"] == "telegram"
    assert card["effect"] == "Draft preview now names the chosen form."
    assert card["proof_mode"] in ("automated", "manual")
    assert card["live_target"] == LIVE_TARGET
    assert card["live_allowlist"] == [LIVE_TARGET], "the recipient set must be the frozen singleton"
    assert card["known_good_sha"] == PUSHED_SHA, "approval must see the already-verified rollback target"
    assert card["rollback_parent_sha"] == PUSHED_SHA, "the rollback parent is the release itself"
    assert card["rollback_mode"] == "operator-triggered", "the card must say rollback is never silent"
    assert card["exclusions"], "a card without exclusions would let one approval mean anything"
    for key in ("bootstrap_git", "bootstrap_python", "bootstrap_bash"):
        assert os.path.isabs(card[key]), f"{key} must be an absolute path frozen at prepare"
        assert os.access(card[key], os.X_OK)
    assert card["bootstrap_python"] == PYTHON
    assert card["bootstrap_bash"] == BASH
    assert card["created_at"].endswith("Z")

    token = _approval(ship_harness)
    assert "RELEASE CARD" in result.stdout
    assert f"APPROVAL_TOKEN={token}" in result.stdout
    assert f"--mode ship --risk telegram --approved {token}" in result.stdout
    assert "not covered" in result.stdout
    # The one approval also covers rollback, so the card must print the exact
    # command rather than leaving the operator to invent one under pressure.
    assert f"--mode rollback --risk telegram --approved {token}" in result.stdout
    assert "operator-triggered" in result.stdout
    # Both printed commands bootstrap from Git objects with the frozen absolute
    # paths, never from the checkout copy of this script.
    assert result.stdout.count(_bootstrap_prefix()) == 2
    assert f"--python {PYTHON} --sha {PUSHED_SHA} --" in result.stdout
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
    assert _card_file(ship_harness).exists()
    for label in ("preflight.sh", "telegram_qa_offline.sh"):
        lines = _env_lines(ship_harness, label)
        assert lines, f"{label} did not run"
        assert all(f"FERNET={CI_FERNET_KEY}" in line for line in lines)
        assert all("TOKEN=fake" in line and "GOOGLE=fake" in line for line in lines)


def test_prepare_resolves_its_toolchain_without_the_bootstrap_environment(ship_harness):
    """A direct prepare has no bootstrap to hand it paths: it resolves them from
    PATH once, makes them absolute and freezes them on the card."""
    env = {k: v for k, v in ship_harness["env"].items() if not k.startswith("RELEASE_LOOP_")}
    env.update({"RELEASE_LOOP_PROOF_TIMEOUT": "0", "RELEASE_LOOP_PROOF_INTERVAL": "0", "RELEASE_LOOP_TEST_MODE": "1"})
    python_link = ship_harness["fake_bin"] / "python3"
    python_link.symlink_to(PYTHON)
    result = _prepare(ship_harness, "internal", extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    card = _card(ship_harness)
    assert card["bootstrap_git"] == os.path.realpath(ship_harness["fake_bin"] / "git")
    assert card["bootstrap_python"] == PYTHON


def test_prepare_writes_no_card_when_the_tree_is_not_release_ready(ship_harness):
    result = _prepare(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "merge-base --is-ancestor"})
    assert result.returncode == 1
    assert "No card was written" in result.stdout
    assert not _card_file(ship_harness).exists()


def test_prepare_writes_no_card_when_live_known_good_cannot_be_verified(ship_harness):
    result = _prepare(ship_harness, "internal", extra_env={"FAKE_RUNTIME_SHA": OTHER_SHA})
    assert result.returncode == 1
    assert "live runtime did not verify" in result.stdout
    assert not _card_file(ship_harness).exists()
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
    assert card["live_allowlist"] == [LIVE_TARGET]


def test_internal_card_freezes_an_empty_recipient_set(ship_harness):
    _prepared(ship_harness, "internal")
    card = _card(ship_harness)
    assert card["live_target"] is None
    assert card["live_allowlist"] == []


# --- prepare is immutable for a SHA ----------------------------------------


def _card_bytes(harness, sha=PUSHED_SHA):
    return _card_file(harness, sha).read_bytes()


def test_prepare_reuses_an_identical_card_without_rewriting_it(ship_harness):
    """Re-preparing the same commit is an ordinary repeat, not a new decision:
    the card is reused and an approval already given for that SHA still stands."""
    effect = "Draft preview now names the chosen form."
    first = _prepare(ship_harness, "internal", effect=effect)
    assert first.returncode == 0, first.stdout + first.stderr
    before = _card_bytes(ship_harness)
    token = _approval(ship_harness)

    second = _prepare(ship_harness, "internal", effect=effect)

    assert second.returncode == 0, second.stdout + second.stderr
    assert "Reusing it unchanged" in second.stdout
    assert "already given for" in second.stdout
    assert "FINAL_RELEASE_STATE=release-ready" in second.stdout
    assert _card_bytes(ship_harness) == before, "a repeat prepare must not rewrite the approved card"
    assert f"APPROVAL_TOKEN={token}" in second.stdout, "the same card must print the same token"


def test_prepare_refuses_to_rewrite_a_card_with_a_different_effect(ship_harness):
    """The approval names a SHA, so rewriting that SHA's card would let one
    approval quietly cover a release the operator never read."""
    effect = "Draft preview now names the chosen form."
    assert _prepare(ship_harness, "internal", effect=effect).returncode == 0
    before = _card_bytes(ship_harness)

    result = _prepare(ship_harness, "internal", effect="Also rewrites the consent copy.")

    assert result.returncode == 2
    assert "immutable for a SHA" in result.stderr
    assert "effect" in result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "release-ready" not in result.stdout
    assert _card_bytes(ship_harness) == before
    assert _card(ship_harness)["effect"] == effect


def test_prepare_refuses_to_move_the_live_target_under_an_existing_card(ship_harness):
    effect = "telegram release: nothing a doctor sees changes shape"
    _prepared(ship_harness, "telegram", live_target=LIVE_TARGET)
    before = _card_bytes(ship_harness)

    result = _prepare(ship_harness, "telegram", effect=effect, live_target="portfolio_guru_staging_bot")

    assert result.returncode == 2
    assert "live_target" in result.stderr
    assert "live_allowlist" in result.stderr
    assert _card(ship_harness)["live_target"] == LIVE_TARGET
    assert _card_bytes(ship_harness) == before


def test_prepare_refuses_to_upgrade_an_existing_manual_card_to_automated_proof(ship_harness):
    """Credentials appearing after a manual card was prepared must not be able to
    turn that same SHA into a live-sending card under the old approval."""
    _prepared(ship_harness, "telegram")
    assert _card(ship_harness)["proof_mode"] == "manual"
    before = _card_bytes(ship_harness)

    result = _prepare(
        ship_harness,
        "telegram",
        effect="telegram release: nothing a doctor sees changes shape",
        live_target=LIVE_TARGET,
        extra_env=TELETHON_ENV,
    )

    assert result.returncode == 2
    assert "proof_mode" in result.stderr
    assert _card(ship_harness)["proof_mode"] == "manual"
    assert _card_bytes(ship_harness) == before


def test_prepare_refuses_to_move_the_rollback_target_under_an_existing_card(ship_harness):
    _prepared(ship_harness, "internal", prepare_env=ROLLBACK_PREPARE_ENV)
    assert _card(ship_harness)["known_good_sha"] == KNOWN_GOOD_SHA
    before = _card_bytes(ship_harness)

    result = _prepare(ship_harness, "internal")

    assert result.returncode == 2
    assert "known_good_sha" in result.stderr
    assert _card(ship_harness)["known_good_sha"] == KNOWN_GOOD_SHA
    assert _card_bytes(ship_harness) == before


def test_prepare_refuses_to_move_a_bootstrap_path_under_an_existing_card(ship_harness):
    """The toolchain is part of what the approval covers: a card prepared for
    one git binary is not re-prepared for another."""
    _prepared(ship_harness, "internal")
    before = _card_bytes(ship_harness)
    other_bin = ship_harness["fake_bin"].parent / "bin2"
    other_bin.mkdir()
    shutil.copy(ship_harness["fake_bin"] / "git", other_bin / "git")

    result = _prepare(ship_harness, "internal", extra_env={"RELEASE_LOOP_GIT": str(other_bin / "git")})

    assert result.returncode == 2
    assert "bootstrap_git" in result.stderr
    assert _card_bytes(ship_harness) == before


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda card: card.update(exclusions=["anything goes"]), "immutable for a SHA"),
        (lambda card: card.update(rollback_mode="silent"), "did not validate"),
        (lambda card: card.update(live_allowlist=["another_bot"]), "did not validate"),
        (lambda card: card.update(rollback_parent_sha=OTHER_SHA), "did not validate"),
    ],
    ids=["weakened-exclusions", "unauthorised-rollback-mode", "widened-recipients", "moved-rollback-parent"],
)
def test_prepare_refuses_to_overwrite_a_hand_edited_card(ship_harness, mutation, expected):
    """Re-preparing must not launder a card someone edited by hand into a fresh
    one that looks prepared."""
    _prepared(ship_harness, "internal")
    card_file = _card_file(ship_harness)
    card = json.loads(card_file.read_text())
    mutation(card)
    card_file.write_text(json.dumps(card))
    before = card_file.read_bytes()

    result = _prepare(ship_harness, "internal")

    assert result.returncode == 2
    assert expected in result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert card_file.read_bytes() == before


# --- truthful prepare states -----------------------------------------------


def test_prepare_says_release_ready_only_after_verifying_the_tree_and_runtime(ship_harness):
    result = _prepare(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FINAL_RELEASE_STATE=release-ready" in result.stdout
    assert f"known_good_sha={PUSHED_SHA}" in result.stdout
    assert f"digest={_digest(_card(ship_harness))}" in result.stdout
    assert "verified=prepare" in result.stdout


@pytest.mark.parametrize(
    "blocking_env",
    [{"FAKE_GIT_FAIL_COMMAND": "merge-base --is-ancestor"}, {"FAKE_RUNTIME_SHA": OTHER_SHA}],
    ids=["not-fast-forwardable", "runtime-does-not-verify"],
)
def test_prepare_that_writes_no_card_never_says_release_ready(ship_harness, blocking_env):
    result = _prepare(ship_harness, "internal", extra_env=blocking_env)

    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "release-ready" not in result.stdout
    assert not _card_file(ship_harness).exists()


# --- approval binding ------------------------------------------------------


def test_ship_refuses_without_approval():
    """ship must refuse and exit 2 before any live action when unapproved.

    The receipt used to say `release-ready` here, which was a claim about a tree
    and a runtime nothing in this run had looked at. release-ready is now only
    what a verified prepare emits."""
    env = {"PATH": _path_only()}
    result = run("--surface", "telegram", "--mode", "ship", "--risk", "internal", env=env)
    assert result.returncode == 2
    assert "approval required" in result.stderr.lower()
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "release-ready" not in result.stdout


@pytest.mark.parametrize("token", ["telegram-19990101", "yes", "a" * 39, "z" * 40, f"{'a' * 40}:{'0' * 63}"])
def test_ship_refuses_any_approval_that_is_not_a_full_approval_token(token):
    """A dated or bare approval used to cover a whole release class. Now one
    approval names exactly one SHA and one card digest."""
    result = run(
        "--surface", "telegram", "--mode", "ship", "--risk", "internal", "--approved", token,
        env={"PATH": _path_only()},
    )
    assert result.returncode == 2
    assert "stale" in result.stderr.lower()
    assert "<40-hex sha>:<64-hex digest>" in result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "release-ready" not in result.stdout


@pytest.mark.parametrize("mode", ["ship", "attest", "rollback"])
def test_legacy_sha_only_approval_fails_closed_with_an_explicit_message(mode):
    """Schema-2 approvals named only a SHA. They no longer bind the card's
    content, so they are refused — loudly, not silently — before anything runs."""
    args = ["--surface", "telegram", "--mode", mode, "--risk", "telegram", "--approved", PUSHED_SHA]
    if mode == "attest":
        args += ["--result", "pass", "--note", "x"]
    result = run(*args, env={"PATH": _path_only()})
    assert result.returncode == 2
    assert "SHA-only" in result.stderr
    assert "legacy" in result.stderr
    assert "<sha>:<digest>" in result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout


def test_release_approved_environment_variable_must_also_name_a_token():
    result = run(
        "--surface", "telegram", "--mode", "ship", "--risk", "internal",
        env={"PATH": _path_only(), "RELEASE_APPROVED": "telegram-19990101"},
    )
    assert result.returncode == 2
    assert "stale" in result.stderr.lower()


def test_ship_refuses_an_approval_naming_a_different_sha(ship_harness):
    _prepared(ship_harness, "internal")
    result = _ship(ship_harness, "internal", skip_prepare=True, approved=_approval(ship_harness, OTHER_SHA))
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
    card_file = _card_file(ship_harness)
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
        (lambda card: card.update(bootstrap_git="scripts/git"), "must be an absolute path"),
    ],
    ids=["missing-rollback-target", "proof-mode-drift", "extra-field", "relative-bootstrap-path"],
)
def test_ship_refuses_tampering_with_immutable_card_fields(ship_harness, mutation, expected):
    _prepared(ship_harness, "internal")
    card_file = _card_file(ship_harness)
    card = json.loads(card_file.read_text())
    mutation(card)
    card_file.write_text(json.dumps(card))

    result = _ship(ship_harness, "internal", skip_prepare=True)

    assert result.returncode == 2
    assert expected in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda card: card.update(effect="A different but perfectly valid effect line."),
        lambda card: card.update(known_good_sha=KNOWN_GOOD_SHA),
        lambda card: card.update(created_at="2026-01-01T00:00:00Z"),
        lambda card: card.update(exclusions=card["exclusions"][:-1]),
    ],
    ids=["effect", "rollback-target", "timestamp", "dropped-exclusion"],
)
def test_ship_refuses_a_semantically_valid_card_that_is_not_the_approved_one(ship_harness, mutation):
    """Schema validation alone let a valid-but-different card through. The
    approval digest covers every field, so any edit after approval is refused
    even when the card still validates."""
    _prepared(ship_harness, "internal")
    token = _approval(ship_harness)
    card_file = _card_file(ship_harness)
    card = json.loads(card_file.read_text())
    mutation(card)
    card_file.write_text(json.dumps(card, indent=2) + "\n")

    result = _ship(ship_harness, "internal", skip_prepare=True, approved=token)

    assert result.returncode == 2
    assert "not the approved digest" in result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "push origin" not in ship_harness["git_log"].read_text()


@pytest.mark.parametrize("mode", ["ship", "attest", "rollback"])
@pytest.mark.parametrize("mutation", [
    lambda c: c.update(effect="different approved effect"),
    lambda c: c.update(proof_mode="manual"),
    lambda c: c.update(live_target="other_bot", live_allowlist=["other_bot"]),
    lambda c: c.update(known_good_sha=OTHER_SHA),
    lambda c: c.update(exclusions=["only this exclusion"]),
    lambda c: c.update(bootstrap_python="/bin/false"),
])
def test_original_approval_refuses_valid_scope_edits_in_every_mode(ship_harness, mode, mutation):
    _prepared(ship_harness, "telegram", prepare_env=TELETHON_ENV)
    original = _approval(ship_harness)
    card = _card(ship_harness)
    mutation(card)
    _card_file(ship_harness).write_text(json.dumps(card))
    extra = ["--result", "pass", "--note", "offline test only"] if mode == "attest" else []
    result = run("--surface", "telegram", "--mode", mode, "--risk", "telegram",
                 "--approved", original, *extra, env=ship_harness["env"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not the approved digest" in result.stderr
    assert not _git_lines(ship_harness, "push ")
    assert not _git_lines(ship_harness, "commit-tree ")
    assert not ship_harness["live_log"].exists()


def test_card_digest_is_canonical_so_reformatting_the_file_does_not_block_resume(ship_harness):
    """Key order and whitespace are not content. The same fields in a different
    layout are the same card and the same approval."""
    _prepared(ship_harness, "internal")
    token = _approval(ship_harness)
    card_file = _card_file(ship_harness)
    card = json.loads(card_file.read_text())
    reordered = {key: card[key] for key in sorted(card, reverse=True)}
    card_file.write_text(json.dumps(reordered, indent=None, separators=(", ", ": ")))
    assert card_file.read_bytes() != json.dumps(card, indent=2).encode()

    result = _ship(ship_harness, "internal", skip_prepare=True, approved=token)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FINAL_RELEASE_STATE=live" in result.stdout


def test_ship_refuses_when_head_moved_away_from_the_approved_sha(ship_harness):
    _prepared(ship_harness, "internal")
    result = _ship(ship_harness, "internal", skip_prepare=True, extra_env={"FAKE_HEAD_SHA": OTHER_SHA})
    assert result.returncode == 2
    assert f"Approval names {PUSHED_SHA} but HEAD is {OTHER_SHA}" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


# --- the bootstrap is the only execution path ------------------------------


@pytest.mark.parametrize("mode", ["ship", "attest", "rollback"])
def test_mutating_modes_refuse_the_checkout_copy_and_reprint_the_bootstrap_command(ship_harness, mode):
    _prepared(ship_harness, "telegram")
    env = {k: v for k, v in ship_harness["env"].items() if k not in ("RELEASE_LOOP_BOOTSTRAP", "RELEASE_LOOP_PINNED_SHA")}
    token = _approval(ship_harness)
    args = ["--surface", "telegram", "--mode", mode, "--risk", "telegram", "--approved", token]
    if mode == "attest":
        args += ["--result", "pass", "--note", "seen"]

    result = run(*args, env=env)

    assert result.returncode == 3
    assert "pinned bootstrap" in result.stderr
    assert _bootstrap_prefix() in result.stderr
    assert f"--mode {mode} --risk telegram --approved {token}" in result.stderr
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert _git_lines(ship_harness, "push ") == []
    assert _git_lines(ship_harness, "commit-tree ") == []


def test_ship_refuses_a_bootstrap_pinned_to_a_different_sha(ship_harness):
    _prepared(ship_harness, "internal")
    result = _ship(ship_harness, "internal", skip_prepare=True, extra_env={"RELEASE_LOOP_PINNED_SHA": OTHER_SHA})
    assert result.returncode == 3
    assert f"pinned to {OTHER_SHA}, but the approval names {PUSHED_SHA}" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_ship_refuses_a_toolchain_that_is_not_the_one_frozen_on_the_card(ship_harness):
    _prepared(ship_harness, "internal")
    other_bin = ship_harness["fake_bin"].parent / "bin2"
    other_bin.mkdir()
    shutil.copy(ship_harness["fake_bin"] / "git", other_bin / "git")

    result = _ship(ship_harness, "internal", skip_prepare=True, extra_env={"RELEASE_LOOP_GIT": str(other_bin / "git")})

    assert result.returncode == 2
    assert "froze bootstrap git=" in result.stderr
    assert "push origin" not in ship_harness["git_log"].read_text()


def test_release_loop_never_calls_a_bare_git_python_or_bash_from_path():
    """Every tool the loop runs is the absolute path frozen on the card."""
    src = SCRIPT.read_text()
    assert 'git() { "$GIT_BIN" "$@"; }' in src
    for forbidden in ("python3 -c", "python3 - ", " bash \"$ROOT", "\tbash "):
        assert forbidden not in src, f"release loop must not call {forbidden!r} from PATH"
    assert ".release/runner" not in src, "no mutable runner cache may exist"


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
    assert _bootstrap_prefix() in result.stdout, "the attest command must also be the pinned bootstrap"
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


def test_live_child_is_told_the_approved_target_and_frozen_allowlist_explicitly(ship_harness):
    """TELEGRAM_BOT_USERNAME alone was not enough. The child reads backend/.env
    after it starts, and a dotenv username would have redirected an approved live
    proof at a bot the card never named, so the approved target and the card's
    frozen singleton allowlist are passed as their own values for the child to
    hold read-only and re-check."""
    result = _ship(ship_harness, "telegram", prepare_env=TELETHON_ENV, extra_env=TELETHON_ENV)

    assert result.returncode == 0, result.stdout + result.stderr
    live_lines = _env_lines(ship_harness, "telegram_bot_qa.sh")
    assert live_lines, "the live child did not run"
    assert all(f"RELEASE_TARGET={LIVE_TARGET}" in line for line in live_lines)
    assert all(f"RELEASE_ALLOWLIST={LIVE_TARGET}" in line for line in live_lines)

    for label in ("preflight.sh", "telegram_qa_offline.sh", "verify_live_runtime.py"):
        lines = _env_lines(ship_harness, label)
        assert lines, f"{label} did not run"
        assert all("RELEASE_TARGET=unset" in line for line in lines), f"{label} must not see the live target"
        assert all("RELEASE_ALLOWLIST=unset" in line for line in lines), f"{label} must not see the allowlist"


def test_live_child_refusing_the_approved_target_is_pending_not_a_failed_journey(ship_harness):
    """Nothing was sent, so this is proof that could not be taken — not a live
    journey that failed, and not a reason to talk about rolling back."""
    result = _ship(
        ship_harness,
        "telegram",
        prepare_env=TELETHON_ENV,
        extra_env={**TELETHON_ENV, "FAKE_LIVE_QA_EXIT": "21"},
    )

    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "did not survive its own environment load" in result.stdout
    assert "No live message was sent" in result.stdout
    assert "stays live until a targeted rollback" not in result.stdout


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
    assert 'RELEASE_LIVE_ALLOWLIST="$CARD_LIVE_ALLOWLIST"' in src


def test_telegram_bot_qa_direct_call_guard_is_unchanged():
    """The release loop supplies the guard per-child; the QA script's own refusal
    to run live without it must stay exactly as strict."""
    src = BOT_QA.read_text()
    assert 'LIVE_APPROVAL_VALUE="portfolio-guru-live-qa-approved"' in src
    assert 'REQUIRE_LIVE="${REQUIRE_TELEGRAM_LIVE:-0}"' in src
    assert "live-telegram: SKIP (explicit approval missing)" in src
    assert "ERROR: live Telegram QA required, but approval/credentials/target allowlist are incomplete." in src
    assert "exit 20" in src
    # The approved target and allowlist are captured read-only before the dotenv load.
    assert "readonly APPROVED_LIVE_TARGET" in src
    assert "readonly APPROVED_LIVE_ALLOWLIST" in src


# --- telegram_bot_qa.sh: the child's own target/allowlist guard -------------


@pytest.fixture
def qa_app(tmp_path):
    """A throwaway app dir for the real telegram_bot_qa.sh: its interpreter is
    this test's Python and its backend/.env is whatever the test writes."""
    tmp_path = tmp_path.resolve()
    backend = tmp_path / "backend"
    (backend / "venv" / "bin").mkdir(parents=True)
    (backend / "venv" / "bin" / "python3").symlink_to(sys.executable)
    (tmp_path / "scripts").mkdir()
    return {"root": tmp_path, "backend": backend, "dotenv": backend / ".env"}


def _run_qa(app, env_extra=None):
    env = {
        "PATH": _path_only(),
        "PORTFOLIO_GURU_APP_DIR": str(app["root"]),
        "TELEGRAM_BOT_QA_ARTIFACT_ROOT": str(app["root"] / "artifacts"),
        "RUN_LIVE_TELEGRAM": "0",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", str(BOT_QA), "--focused-release"], capture_output=True, text=True, env=env, cwd=str(app["root"]))


def test_qa_child_refuses_a_parent_environment_that_moves_the_approved_target(qa_app):
    result = _run_qa(qa_app, {"RELEASE_LIVE_TARGET": LIVE_TARGET, "TELEGRAM_BOT_USERNAME": "other_bot"})
    assert result.returncode == 21
    assert "changed the live Telegram target" in result.stderr
    assert "Nothing was sent" in result.stderr


def test_qa_child_refuses_a_frozen_allowlist_that_is_not_the_approved_target(qa_app):
    result = _run_qa(
        qa_app,
        {"RELEASE_LIVE_TARGET": LIVE_TARGET, "TELEGRAM_BOT_USERNAME": LIVE_TARGET, "RELEASE_LIVE_ALLOWLIST": "other_bot"},
    )
    assert result.returncode == 21
    assert "frozen release allowlist" in result.stderr


def test_qa_child_refuses_an_effective_allowlist_that_omits_the_target(qa_app):
    result = _run_qa(
        qa_app,
        {"RELEASE_LIVE_TARGET": LIVE_TARGET, "TELEGRAM_BOT_USERNAME": LIVE_TARGET, "TELEGRAM_LIVE_ALLOWED_BOTS": "other_bot"},
    )
    assert result.returncode == 21
    assert "not on the allowlist in force" in result.stderr


def test_qa_child_refuses_dotenv_attempts_to_redirect_an_approved_live_proof(qa_app):
    """backend/.env may not export the target, the allowlist, the guard, the
    release values or PATH during a release live proof. A conflicting target
    is refused before any proof step; matching protected values are ignored."""
    qa_app["dotenv"].write_text(
        "TELEGRAM_BOT_USERNAME=other_bot\n"
        "TELEGRAM_LIVE_ALLOWED_BOTS=other_bot\n"
        f"TELEGRAM_LIVE_APPROVED={LIVE_APPROVAL}\n"
        "PATH=/nowhere\n"
        "HARMLESS_SETTING=1\n"
    )
    result = _run_qa(
        qa_app,
        {"RELEASE_LIVE_TARGET": LIVE_TARGET, "TELEGRAM_BOT_USERNAME": LIVE_TARGET, "RELEASE_LIVE_ALLOWLIST": LIVE_TARGET},
    )
    assert result.returncode == 21, result.stderr
    assert "changed the live Telegram target" in result.stderr
    assert "Nothing was sent" in result.stderr
    assert "Running" not in result.stdout


def test_qa_child_direct_call_still_honours_dotenv(qa_app):
    """Without RELEASE_LIVE_TARGET this is not a release proof, and the script's
    direct-call behaviour is unchanged: nothing is protected, nothing is refused."""
    qa_app["dotenv"].write_text("TELEGRAM_BOT_USERNAME=other_bot\n")
    result = _run_qa(qa_app)
    assert result.returncode != 21
    assert "ignored protected name" not in result.stderr


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
    token = _approval(ship_harness)
    resume = [line for line in result.stdout.splitlines() if "RESUME_COMMAND=" in line]
    assert resume, result.stdout
    assert _bootstrap_prefix() in resume[0]
    assert (
        f"-- --surface telegram --mode ship --risk telegram --release-sha {PUSHED_SHA} --approved {token}"
    ) in resume[0]


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
    """The exact-SHA push to main already preserves the commit remotely, so no
    separate feature-branch backup push is taken."""
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
    command = [line for line in result.stdout.splitlines() if "ROLLBACK_COMMAND=" in line]
    assert command, result.stdout
    assert _bootstrap_prefix() in command[0]
    assert f"-- --surface telegram --mode rollback --risk telegram --approved {_approval(ship_harness)}" in command[0]
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
    assert attestation["card_digest"] == _digest(_card(ship_harness))
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


def test_attest_proves_the_exact_sha_tests_run_itself_before_recording_anything(ship_harness):
    """Attest closes the manual half only. It cannot assume a ship run happened,
    so it proves the automated half from GitHub itself."""
    _prepared(ship_harness, "telegram")
    (ship_harness["gh_runs"] / "Tests.json").write_text(_workflow_runs(conclusion="failure"))
    (ship_harness["gh_runs"] / "Tests.api.json").write_text(_api_payload(_workflow_runs(conclusion="failure")))

    result = _attest(ship_harness, "telegram")

    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "conclusion=failure" in result.stdout
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


def test_attest_requires_the_deploy_that_followed_that_tests_run(ship_harness):
    """A deploy that started before the selected Tests run is not proof of this
    SHA reaching the Mac Mini, whatever the operator saw in Telegram."""
    _prepared(ship_harness, "telegram")
    stale_deploy = _workflow_runs(
        event="workflow_run", created_at="2026-08-13T09:59:00Z", updated_at="2026-08-13T10:10:00Z"
    )
    (ship_harness["gh_runs"] / "Deploy Mac Mini.json").write_text(stale_deploy)
    (ship_harness["gh_runs"] / "Deploy Mac Mini.api.json").write_text(_api_payload(stale_deploy))

    result = _attest(ship_harness, "telegram")

    assert result.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


def test_attest_pass_reports_the_automated_half_it_proved_for_itself(ship_harness):
    _prepared(ship_harness, "telegram")

    result = _attest(ship_harness, "telegram")

    assert result.returncode == 0, result.stdout + result.stderr
    gh_log = ship_harness["gh_log"].read_text()
    assert "--workflow Tests" in gh_log
    assert "--workflow Deploy Mac Mini" in gh_log
    assert "tests=1 deploy=1 runtime=1" in result.stdout
    assert "proof=manual-operator-attestation" in result.stdout
    assert "manual proof attested by operator" in result.stdout
    assert not ship_harness["live_log"].exists(), "attest must never send anything"


def test_attest_refuses_an_approval_that_does_not_name_the_card(ship_harness):
    _shipped_manual_card(ship_harness)
    result = _attest(ship_harness, "telegram", approved=_approval(ship_harness, OTHER_SHA))
    assert result.returncode == 2
    assert not (ship_harness["card_dir"] / f"{OTHER_SHA}.attestation.json").exists()


def test_attest_refuses_a_card_edited_after_approval(ship_harness):
    _shipped_manual_card(ship_harness)
    token = _approval(ship_harness)
    card_file = _card_file(ship_harness)
    card = json.loads(card_file.read_text())
    card["effect"] = "A different but perfectly valid effect line."
    card_file.write_text(json.dumps(card))
    result = _attest(ship_harness, "telegram", approved=token)
    assert result.returncode == 2
    assert "not the approved digest" in result.stderr
    assert not (ship_harness["card_dir"] / f"{PUSHED_SHA}.attestation.json").exists()


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
    result = _rollback(ship_harness, "internal", approved=_approval(ship_harness, OTHER_SHA))
    assert result.returncode == 2
    assert "needs a new card and a new approval" in result.stderr
    assert _git_lines(ship_harness, "commit-tree ") == []
    assert _git_lines(ship_harness, "push ") == []


def test_rollback_refuses_a_card_edited_after_approval(ship_harness):
    _rollback_ready(ship_harness)
    token = _approval(ship_harness)
    card_file = _card_file(ship_harness)
    card = json.loads(card_file.read_text())
    card["known_good_sha"] = OTHER_SHA
    card_file.write_text(json.dumps(card))

    result = _rollback(ship_harness, "internal", approved=token)

    assert result.returncode == 2
    assert "not the approved digest" in result.stderr
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
    card_file = _card_file(ship_harness)
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
    state_file = _rollback_state_file(ship_harness)
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
    state_file = _rollback_state_file(ship_harness)
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


def test_rollback_never_touches_the_checkout_or_a_local_ref(ship_harness):
    """The whole point of building the commit from objects: HEAD stays at the
    released SHA and nothing writes the working tree or index."""
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    log = ship_harness["git_log"].read_text()
    for forbidden in ("read-tree", "update-ref", "checkout", "reset", "symbolic-ref", "merge ", "rebase"):
        assert forbidden not in log, f"rollback must never run git {forbidden!r}"
    assert f"HEAD stays at {PUSHED_SHA}" in result.stdout
    assert (ship_harness["root"] / "scripts" / "release_card.py").exists()
    assert not (ship_harness["card_dir"] / "runner").exists(), "no runner cache may be retained"


def test_rollback_refuses_a_commit_whose_parent_is_not_the_released_sha(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_ROLLBACK_PARENT": OTHER_SHA})

    assert result.returncode == 3
    assert "exactly one parent" in result.stderr
    assert _git_lines(ship_harness, "push ") == []
    assert not _rollback_state_file(ship_harness).exists()


def test_rollback_refuses_a_merge_whose_first_parent_is_the_released_sha(ship_harness):
    """Reading only the first parent would accept a merge, which drags a second
    line of history onto main under an approval that named one commit."""
    _rollback_ready(ship_harness)
    result = _rollback(
        ship_harness,
        "internal",
        extra_env={"FAKE_ROLLBACK_PARENTS": f"{PUSHED_SHA} {OTHER_SHA}"},
    )

    assert result.returncode == 3
    assert "exactly one parent" in result.stderr
    assert OTHER_SHA in result.stderr
    assert _git_lines(ship_harness, "push ") == []
    assert not _rollback_state_file(ship_harness).exists()


def test_rollback_refuses_a_commit_whose_tree_is_not_the_known_good_tree(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_ROLLBACK_TREE": UNRELATED_TREE})

    assert result.returncode == 3
    assert "not the known-good tree" in result.stderr
    assert _git_lines(ship_harness, "push ") == []
    assert not _rollback_state_file(ship_harness).exists()


def test_rollback_refuses_when_the_commit_object_cannot_be_created(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_COMMIT_TREE_BROKEN": "1"})

    assert result.returncode == 3
    assert "nothing was changed" in result.stderr
    assert _git_lines(ship_harness, "push ") == []
    assert not _rollback_state_file(ship_harness).exists()


# --- rollback: push, state and proof ---------------------------------------


def test_rollback_pushes_the_exact_rollback_sha_once_and_reconciles_after(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"]
    assert f"ROLLBACK_PUSHED_SHA={ROLLBACK_SHA}" in result.stdout
    assert ship_harness["origin_state"].read_text() == ROLLBACK_SHA


def test_rollback_state_is_written_before_the_push(ship_harness):
    """A crash between commit and push must leave a record to resume from; the
    commit itself is deterministic, so even without one nothing is lost."""
    _rollback_ready(ship_harness)
    result = _rollback(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "push origin"})

    assert result.returncode == 1
    state = _rollback_state(ship_harness)
    assert state["status"] == "committed"
    assert state["released_sha"] == PUSHED_SHA
    assert state["known_good_sha"] == KNOWN_GOOD_SHA
    assert state["rollback_sha"] == ROLLBACK_SHA
    assert state["schema_version"] == 3
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


def _journal_states(result):
    return [chunk.split()[0] for chunk in result.stdout.split("ROLLBACK_STATE=")[1:]]


def test_rollback_journal_walks_the_mutation_in_order(ship_harness):
    _rollback_ready(ship_harness)

    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _journal_states(result) == ["committed", "pushed", "proved"]


def test_rollback_resumes_a_local_commit_that_was_never_pushed(ship_harness):
    _rollback_ready(ship_harness)
    blocked = _rollback(ship_harness, "internal", extra_env={"FAKE_GIT_FAIL_COMMAND": "push origin"})
    assert blocked.returncode == 1
    assert _rollback_state(ship_harness)["status"] == "committed"
    assert not ship_harness["origin_state"].exists()

    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in result.stdout
    assert "Reusing recorded rollback commit" in result.stdout
    assert len(_git_lines(ship_harness, "commit-tree ")) == 1, "a resume must never make a second commit"
    # First attempt failed before changing origin; only the second landed.
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"] * 2
    assert ship_harness["origin_state"].read_text() == ROLLBACK_SHA


def test_rollback_resumes_proof_after_a_push_without_a_duplicate_push(ship_harness):
    _rollback_ready(ship_harness)
    (ship_harness["gh_runs"] / "Tests.json").write_text("[]")
    (ship_harness["gh_runs"] / "Tests.api.json").write_text(json.dumps({"workflow_runs": []}))
    pending = _rollback(ship_harness, "internal")
    assert pending.returncode == 4
    assert "FINAL_RELEASE_STATE=proof-pending" in pending.stdout
    assert _rollback_state(ship_harness)["status"] == "pushed"
    # The resume command is the same pinned bootstrap; nothing mutable is named.
    resume = [line for line in pending.stdout.splitlines() if "ROLLBACK_RESUME_COMMAND=" in line]
    assert resume, pending.stdout
    assert _bootstrap_prefix() in resume[0]
    assert f"-- --surface telegram --mode rollback --risk internal --approved {_approval(ship_harness)}" in resume[0]

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


def test_rollback_reuses_a_rollback_commit_already_on_main_when_the_journal_is_gone(ship_harness):
    """Recovery asks Git, not the journal: a commit on main with exactly the
    released parent and the known-good tree is this rollback, and is reused."""
    _rollback_ready(ship_harness)
    assert _rollback(ship_harness, "internal").returncode == 0
    _rollback_state_file(ship_harness).unlink()

    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "already a rollback commit" in result.stdout
    assert len(_git_lines(ship_harness, "commit-tree ")) == 1
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"]
    assert _rollback_state(ship_harness)["status"] == "proved"


# --- rollback: deterministic interruption ------------------------------------


def test_fault_before_the_journal_leaves_no_journal_and_no_push_and_the_rerun_completes(ship_harness):
    _rollback_ready(ship_harness)
    interrupted = _rollback(ship_harness, "internal", extra_env={"RELEASE_LOOP_FAULT_AT": "rollback-commit-created"})

    assert interrupted.returncode == 70
    assert "TEST FAULT injected at checkpoint 'rollback-commit-created'" in interrupted.stderr
    assert not _rollback_state_file(ship_harness).exists()
    assert _git_lines(ship_harness, "push ") == []
    assert not ship_harness["origin_state"].exists()

    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in result.stdout
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"]
    assert _rollback_state(ship_harness)["status"] == "proved"


def test_fault_after_the_push_before_the_journal_is_reconciled_without_a_second_push(ship_harness):
    """The 'unknown push': main already holds the rollback commit and nothing
    local says so. The rerun recognises it by shape and pushes nothing."""
    _rollback_ready(ship_harness)
    interrupted = _rollback(ship_harness, "internal", extra_env={"RELEASE_LOOP_FAULT_AT": "rollback-pushed"})

    assert interrupted.returncode == 70
    assert ship_harness["origin_state"].read_text() == ROLLBACK_SHA
    assert _rollback_state(ship_harness)["status"] == "committed"

    result = _rollback(ship_harness, "internal")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "already a rollback commit" in result.stdout
    assert "skipping a duplicate push" in result.stdout
    assert _git_lines(ship_harness, "push ") == [f"push origin {ROLLBACK_SHA}:refs/heads/main"]
    assert len(_git_lines(ship_harness, "commit-tree ")) == 1
    assert _rollback_state(ship_harness)["status"] == "proved"


def test_fault_checkpoints_are_inert_outside_test_mode(ship_harness):
    _rollback_ready(ship_harness)
    result = _rollback(
        ship_harness,
        "internal",
        extra_env={
            "RELEASE_LOOP_TEST_MODE": "0",
            "RELEASE_LOOP_PROOF_TIMEOUT": "1",
            "RELEASE_LOOP_PROOF_INTERVAL": "1",
            "RELEASE_LOOP_FAULT_AT": "rollback-commit-created",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TEST FAULT" not in result.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in result.stdout


def test_rollback_never_force_pushes_or_rewrites_history():
    src = SCRIPT.read_text()
    assert "commit-tree" in src, "rollback must build a normal forward commit"
    for forbidden in ("push --force", "--force-with-lease", "filter-branch", "update-ref", "read-tree", "symbolic-ref"):
        assert forbidden not in src, f"rollback must never contain {forbidden!r}"
    assert "git push origin \"$rollback_sha:refs/heads/main\"" in src


def test_rollback_commit_identity_is_fixed_and_dated_from_the_released_commit():
    """Determinism is what makes an interrupted rollback safe to rerun."""
    src = SCRIPT.read_text()
    assert 'ROLLBACK_IDENTITY_NAME="Portfolio Guru release loop"' in src
    assert 'GIT_AUTHOR_DATE="$when"' in src and 'GIT_COMMITTER_DATE="$when"' in src
    assert 'git show -s --format=%cI "$released"' in src


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
