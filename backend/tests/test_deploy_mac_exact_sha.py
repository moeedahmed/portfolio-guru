from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "deploy_mac.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-mac.yml"


def _run(args, *, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, text: str) -> str:
    (repo / "release.txt").write_text(text)
    _run(["git", "add", "release.txt"], cwd=repo)
    _run(["git", "commit", "-m", text], cwd=repo)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


@pytest.fixture
def deploy_repo(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    app = tmp_path / "app"
    _run(["git", "init", "--bare", str(origin)], cwd=tmp_path)
    _run(["git", "init", "-b", "main", str(seed)], cwd=tmp_path)
    _run(["git", "config", "user.email", "test@example.invalid"], cwd=seed)
    _run(["git", "config", "user.name", "Release Test"], cwd=seed)
    initial = _commit(seed, "initial")
    _run(["git", "remote", "add", "origin", str(origin)], cwd=seed)
    _run(["git", "push", "-u", "origin", "main"], cwd=seed)
    _run(["git", "clone", "--branch", "main", str(origin), str(app)], cwd=tmp_path)
    expected = _commit(seed, "expected")
    _run(["git", "push", "origin", "main"], cwd=seed)

    (app / "backend" / ".venv" / "bin").mkdir(parents=True)
    fake_python = app / "backend" / ".venv" / "bin" / "python3"
    fake_python.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_python.chmod(0o755)
    (app / "backend" / "requirements.txt").write_text("")
    (app / "backend" / "bot.py").write_text("")
    (app / "scripts").mkdir()
    verifier = app / "scripts" / "verify_live_runtime.py"
    verifier.write_text("#!/usr/bin/env bash\nexit 0\n")
    verifier.chmod(0o755)

    home = tmp_path / "home"
    plist = home / "Library" / "LaunchAgents" / "com.portfolioguru.bot.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("test")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    commands = {
        "launchctl": "#!/usr/bin/env bash\nif [[ \"$1\" == print && \"$2\" == user/* ]]; then echo 'Could not find service' >&2; exit 113; fi\nif [[ \"$1\" == print ]]; then printf 'pid = %s\\n' \"$TEST_SERVICE_PID\"; fi\nexit 0\n",
        "pgrep": "#!/usr/bin/env bash\nexit 1\n",
        "lsof": "#!/usr/bin/env bash\nexit 1\n",
        "sleep": "#!/usr/bin/env bash\nexit 0\n",
    }
    for name, content in commands.items():
        path = fake_bin / name
        path.write_text(content)
        path.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(home),
        "PORTFOLIO_GURU_APP_DIR": str(app),
        "PORTFOLIO_GURU_DEPLOY_LOCK": str(tmp_path / "deploy.lock"),
        "TEST_SERVICE_PID": str(os.getpid()),
    }
    return app, initial, expected, env


def test_workflow_binds_both_events_to_required_full_expected_sha():
    source = WORKFLOW.read_text()
    assert "expected_sha:" in source
    assert "required: true" in source
    assert "github.event.workflow_run.head_sha" in source
    assert "inputs.expected_sha" in source
    assert "DEPLOY_EXPECTED_SHA" in source


def test_workflow_checks_out_and_executes_the_exact_sha_source_outside_production():
    source = WORKFLOW.read_text()

    assert re.search(r"uses:\s*actions/checkout@", source)
    assert re.search(r"ref:\s*\$\{\{\s*env\.DEPLOY_EXPECTED_SHA\s*\}\}", source)
    assert re.search(r"path:\s*release-source\b", source)
    assert "PORTFOLIO_GURU_APP_DIR=/Users/moeedahmed/projects/portfolio-guru" in source
    assert 'bash "$GITHUB_WORKSPACE/release-source/scripts/deploy_mac.sh"' in source
    assert "cd /Users/moeedahmed/projects/portfolio-guru" not in source
    assert "bash scripts/deploy_mac.sh" not in source


def test_deploy_refuses_missing_expected_sha_before_checkout_mutation(deploy_repo):
    app, initial, _expected, env = deploy_repo
    result = subprocess.run(["bash", str(SCRIPT)], cwd=app, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "DEPLOY_EXPECTED_SHA" in result.stdout + result.stderr
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip() == initial


def test_deploy_refuses_non_full_expected_sha_before_checkout_mutation(deploy_repo):
    app, initial, _expected, env = deploy_repo
    env["DEPLOY_EXPECTED_SHA"] = "abc1234"
    result = subprocess.run(["bash", str(SCRIPT)], cwd=app, env=env, capture_output=True, text=True)
    assert result.returncode == 64
    assert "full 40-character" in result.stdout + result.stderr
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip() == initial


def test_deploy_updates_only_to_exact_origin_main_sha(deploy_repo):
    app, _initial, expected, env = deploy_repo
    env["DEPLOY_EXPECTED_SHA"] = expected
    result = subprocess.run(["bash", str(SCRIPT)], cwd=app, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip() == expected
    assert f"DEPLOYED_SHA={expected}" in result.stdout


def test_deploy_refuses_when_origin_main_is_not_expected_sha(deploy_repo):
    app, initial, _expected, env = deploy_repo
    env["DEPLOY_EXPECTED_SHA"] = "f" * 40
    result = subprocess.run(["bash", str(SCRIPT)], cwd=app, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "origin/main" in result.stdout + result.stderr
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip() == initial


@pytest.mark.parametrize("domain", ["gui", "user"])
@pytest.mark.parametrize("failure", ["none", "verification", "bootstrap", "service_query"])
def test_deploy_keeps_the_existing_service_domain(deploy_repo, domain, failure):
    app, initial, expected, env = deploy_repo
    env["DEPLOY_EXPECTED_SHA"] = expected
    calls = app.parent / "launchctl-calls"
    fake = Path(env["PATH"].split(":")[0]) / "launchctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$TEST_LAUNCHCTL_CALLS"\n'
        'count=0; [[ ! -f "$TEST_BOOTSTRAP_COUNT" ]] || count=$(cat "$TEST_BOOTSTRAP_COUNT")\n'
        'if [[ "$1" == bootstrap ]]; then count=$((count + 1)); echo "$count" > "$TEST_BOOTSTRAP_COUNT"; fi\n'
        'if [[ "$count" == 1 && "$TEST_DEPLOY_FAILURE" == bootstrap && "$1" == bootstrap ]]; then exit 1; fi\n'
        'if [[ "$count" == 1 && "$TEST_DEPLOY_FAILURE" == service_query && "$1" == print ]]; then exit 1; fi\n'
        'if [[ "$1" == print && "$2" != "$TEST_SERVICE_DOMAIN/"* ]]; then\n'
        '  echo "Could not find service" >&2; exit 113\n'
        'fi\n'
        'if [[ "$1" == print ]]; then printf "pid = %s\\n" "$TEST_SERVICE_PID"; fi\n'
        'exit 0\n'
    )
    env["TEST_LAUNCHCTL_CALLS"] = str(calls)
    env["TEST_SERVICE_DOMAIN"] = f"{domain}/{os.getuid()}"
    env["TEST_BOOTSTRAP_COUNT"] = str(app.parent / "bootstrap-count")
    env["TEST_DEPLOY_FAILURE"] = failure
    if failure == "verification":
        (app / "scripts" / "verify_live_runtime.py").write_text("#!/usr/bin/env bash\nexit 1\n")
    result = subprocess.run(["bash", str(SCRIPT)], cwd=app, env=env, capture_output=True, text=True)
    assert result.returncode == (0 if failure == "none" else 1), result.stdout + result.stderr
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip() == (expected if failure == "none" else initial)
    mutations = [line for line in calls.read_text().splitlines() if line.startswith(("bootout ", "bootstrap ", "enable "))]
    assert mutations
    assert all(line.split()[1].startswith(env["TEST_SERVICE_DOMAIN"]) for line in mutations)


@pytest.mark.parametrize("registered", [False, True])
def test_deploy_refuses_missing_or_ambiguous_owner_before_mutating(deploy_repo, registered):
    app, initial, expected, env = deploy_repo
    env["DEPLOY_EXPECTED_SHA"] = expected
    fake = Path(env["PATH"].split(":")[0]) / "launchctl"
    calls = app.parent / "launchctl-calls"
    env["TEST_LAUNCHCTL_CALLS"] = str(calls)
    body = 'printf "pid = %s\\n" "$TEST_SERVICE_PID"; exit 0' if registered else 'echo "Could not find service" >&2; exit 113'
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$TEST_LAUNCHCTL_CALLS"\n' + body + '\n')
    result = subprocess.run(["bash", str(SCRIPT)], cwd=app, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Expected one registered" in result.stderr
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip() == initial
    assert all(line.startswith("print ") for line in calls.read_text().splitlines())
