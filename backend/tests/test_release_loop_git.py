"""Real-Git regression tests for scripts/release_loop.sh.

These run the shipped release loop, card helper and bootstrap against a real
local repository with a real local *bare* remote. Only the things that must not
happen offline are stubbed: GitHub (`gh`), launchd, the Mac Mini runtime
verifier and the product gates. Git semantics — objects, refs, pushes, hooks,
`commit-tree`, cleanliness — are the real thing.

What they prove that the fake-git harness cannot:

- the printed ship/rollback commands bootstrap this script and its card helper
  out of Git objects at the approved SHA, not from the checkout copies;
- rollback builds a deterministic forward commit (parent = released SHA, tree =
  known-good tree) without touching the working tree, the index or any local
  ref, and pushes exactly that commit;
- an interruption before the journal — at a fixed, injected checkpoint, never a
  wall-clock kill — leaves the checkout untouched, and the rerun reconciles onto
  the very same commit with exactly one push in total.

Nothing here contacts a network. The "remote" is a directory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
GIT = shutil.which("git")
PYTHON = os.path.realpath(sys.executable)
LIVE_TARGET = "portfolio_guru_bot"

pytestmark = pytest.mark.skipif(GIT is None, reason="a real git binary is required")


def git(cwd, *args, env=None, check=True) -> str:
    result = subprocess.run([GIT, *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def repo(tmp_path):
    tmp = tmp_path.resolve()
    home = tmp / "home"
    home.mkdir()
    base_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.invalid",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "author@example.invalid",
    }

    # The remote: a bare repository whose post-receive hook logs every ref update,
    # so the tests can count pushes exactly rather than infer them.
    bare = tmp / "origin.git"
    git(tmp, "init", "--bare", str(bare), env=base_env)
    git(bare, "symbolic-ref", "HEAD", "refs/heads/main", env=base_env)
    push_log = tmp / "pushes.log"
    _write_executable(
        bare / "hooks" / "post-receive",
        f"#!/usr/bin/env bash\nwhile read -r old new ref; do printf '%s %s %s\\n' \"$old\" \"$new\" \"$ref\" >> \"{push_log}\"; done\n",
    )

    # The checkout: the real release scripts plus stubbed product gates.
    work = tmp / "work"
    work.mkdir()
    git(work, "init", env=base_env)
    git(work, "symbolic-ref", "HEAD", "refs/heads/main", env=base_env)
    scripts = work / "scripts"
    scripts.mkdir()
    for name in ("release_loop.sh", "release_card.py", "release_bootstrap.py"):
        shutil.copy(SCRIPTS / name, scripts / name)
        (scripts / name).chmod(0o755)
    for name in ("preflight.sh", "telegram_qa_offline.sh", "telegram_bot_qa.sh", "dogfood_smoke.sh"):
        _write_executable(scripts / name, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        scripts / "verify_live_runtime.py",
        "#!/usr/bin/env bash\n"
        'expected="$2"\n'
        "printf 'LIVE_RUNTIME_OK expected_sha=%s checkout_sha=%s runtime_sha=%s\\n' "
        '"$expected" "${FAKE_CHECKOUT_SHA:-$expected}" "${FAKE_RUNTIME_SHA:-$expected}"\n',
    )
    (work / "backend").mkdir()
    (work / "backend" / "bot.py").write_text("print('known good')\n")
    (work / ".gitignore").write_text(".release/\n")
    git(work, "add", "-A", env=base_env)
    git(work, "commit", "-q", "-m", "known good", env=base_env)
    known_good = git(work, "rev-parse", "HEAD", env=base_env)
    git(work, "remote", "add", "origin", str(bare), env=base_env)
    git(work, "push", "-q", "-u", "origin", "main", env=base_env)
    push_log.write_text("")  # only pushes made by the loop count

    git(work, "checkout", "-q", "-b", "fix/release", env=base_env)
    (work / "backend" / "bot.py").write_text("print('released')\n")
    git(work, "commit", "-q", "-am", "release change", env=base_env)
    released = git(work, "rev-parse", "HEAD", env=base_env)

    # Stub CI keyed to whatever is actually on the bare remote's main, so the
    # provenance gates see runs for exactly the SHA the loop pushed.
    fake_bin = tmp / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3").symlink_to(PYTHON)
    _write_executable(
        fake_bin / "gh",
        f"""#!/usr/bin/env bash
if [[ "$1 $2" == "auth status" ]]; then exit 0; fi
if [[ "$1 $2" != "run list" ]]; then exit 2; fi
workflow=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--workflow" ]]; then workflow="$2"; break; fi
  shift
done
sha="$("{GIT}" --git-dir="{bare}" rev-parse main)"
conclusion="${{FAKE_CI_CONCLUSION:-success}}"
if [[ "$workflow" == "Tests" ]]; then
  event=push; created=2026-08-13T10:00:00Z; updated=2026-08-13T10:05:00Z; id=9001
else
  event=workflow_run; created=2026-08-13T10:06:00Z; updated=2026-08-13T10:10:00Z; id=9002
fi
printf '[{{"databaseId": %s, "headSha": "%s", "status": "completed", "conclusion": "%s", "event": "%s", "createdAt": "%s", "startedAt": "%s", "updatedAt": "%s"}}]\\n' \\
  "$id" "$sha" "$conclusion" "$event" "$created" "$created" "$updated"
""",
    )
    _write_executable(fake_bin / "launchctl", "#!/usr/bin/env bash\n[[ \"$1\" == print ]] && exit 0\nexit 1\n")

    env = dict(base_env)
    env.update(
        PATH=f"{fake_bin}:{base_env['PATH']}",
        RELEASE_LOOP_PROOF_TIMEOUT="0",
        RELEASE_LOOP_PROOF_INTERVAL="0",
        RELEASE_LOOP_TEST_MODE="1",
    )
    return {
        "work": work,
        "bare": bare,
        "env": env,
        "push_log": push_log,
        "known_good": known_good,
        "released": released,
        "card_dir": work / ".release",
    }


def _loop(repo, *args, env_extra=None):
    env = dict(repo["env"])
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(repo["work"] / "scripts" / "release_loop.sh"), *args],
        cwd=str(repo["work"]), capture_output=True, text=True, env=env,
    )


def _prepare(repo, risk="internal"):
    args = ["--surface", "telegram", "--mode", "prepare", "--risk", risk, "--effect", "Real git harness release."]
    if risk == "telegram":
        args += ["--live-target", LIVE_TARGET]
    result = _loop(repo, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _printed(stdout: str, label: str) -> str:
    """The exact command the card printed for `ship` / `roll back`."""
    match = re.search(rf"^  {re.escape(label)}\s+(.+)$", stdout, re.MULTILINE)
    assert match, f"no {label!r} command in:\n{stdout}"
    return match.group(1)


def _run_printed(repo, command: str, env_extra=None):
    """Run a printed command exactly as an operator would: through a shell."""
    env = dict(repo["env"])
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", "-c", command], cwd=str(repo["work"]), capture_output=True, text=True, env=env)


def _card(repo):
    return json.loads((repo["card_dir"] / f"{repo['released']}.card.json").read_text())


def _token(repo):
    return subprocess.run(
        [PYTHON, str(SCRIPTS / "release_card.py"), "approval", "--path", str(repo["card_dir"] / f"{repo['released']}.card.json")],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _remote_main(repo):
    return git(repo["bare"], "rev-parse", "main", env=repo["env"])


def _pushes(repo):
    return [line for line in repo["push_log"].read_text().splitlines() if line.strip()]


def _head(repo):
    return git(repo["work"], "rev-parse", "HEAD", env=repo["env"])


def _tree(repo, ref):
    return git(repo["work"], "rev-parse", f"{ref}^{{tree}}", env=repo["env"])


def _parents(repo, ref):
    return git(repo["work"], "rev-list", "--parents", "-n", "1", ref, env=repo["env"]).split()[1:]


def _checkout_untouched(repo):
    """Working tree and index both still exactly HEAD's tree, and HEAD is the release."""
    assert git(repo["work"], "status", "--porcelain", env=repo["env"]) == ""
    assert git(repo["work"], "write-tree", env=repo["env"]) == _tree(repo, "HEAD")
    assert _head(repo) == repo["released"]


def _shipped(repo):
    prepare = _prepare(repo)
    ship = _run_printed(repo, _printed(prepare.stdout, "ship"))
    assert ship.returncode == 0, ship.stdout + ship.stderr
    assert _remote_main(repo) == repo["released"]
    assert len(_pushes(repo)) == 1
    return prepare


def _journal(repo):
    path = repo["card_dir"] / f"{repo['released']}.rollback.json"
    return json.loads(path.read_text()) if path.exists() else None


# --- prepare and the printed bootstrap ---------------------------------------


def test_prepare_freezes_the_real_absolute_toolchain_and_prints_bootstrap_commands(repo):
    result = _prepare(repo)
    card = _card(repo)
    assert card["known_good_sha"] == repo["known_good"]
    assert card["rollback_parent_sha"] == repo["released"]
    assert card["bootstrap_git"] == os.path.realpath(GIT)
    assert card["bootstrap_python"] == PYTHON
    assert os.path.isabs(card["bootstrap_bash"])
    ship = _printed(result.stdout, "ship")
    assert ship.startswith(f"{card['bootstrap_git']} -C ")
    assert f"show {repo['released']}:scripts/release_bootstrap.py | {PYTHON} - " in ship
    assert f"--sha {repo['released']} -- --surface telegram --mode ship --risk internal --approved {_token(repo)}" in ship
    assert _remote_main(repo) == repo["known_good"], "prepare must not push"
    assert _pushes(repo) == []


def test_printed_ship_command_bootstraps_from_git_objects_and_pushes_the_exact_sha(repo):
    prepare = _prepare(repo)
    ship = _run_printed(repo, _printed(prepare.stdout, "ship"))

    assert ship.returncode == 0, ship.stdout + ship.stderr
    assert f"RELEASE_BOOTSTRAP sha={repo['released']} stage_mode=0700" in ship.stdout
    assert f"pinned to {repo['released']}" in ship.stdout
    assert "FINAL_RELEASE_STATE=live" in ship.stdout
    assert _remote_main(repo) == repo["released"]
    assert _pushes(repo) == [f"{repo['known_good']} {repo['released']} refs/heads/main"]
    _checkout_untouched(repo)


def test_bootstrap_runs_the_pinned_sources_not_the_checkout_copies(repo):
    """After prepare, both checkout copies are altered to announce themselves.
    The bootstrap never runs them: the refusal it produces (dirty tree) carries
    no trace of the tampering, because the code that ran came from Git objects."""
    prepare = _prepare(repo)
    loop = repo["work"] / "scripts" / "release_loop.sh"
    loop.write_text(loop.read_text().replace("set -euo pipefail\n", "set -euo pipefail\necho TAMPERED-LOOP >&2\n", 1))
    helper = repo["work"] / "scripts" / "release_card.py"
    helper.write_text("import sys\nsys.stderr.write('TAMPERED-HELPER\\n')\n" + helper.read_text())

    ship = _run_printed(repo, _printed(prepare.stdout, "ship"))

    assert ship.returncode == 3
    assert "uncommitted tracked changes" in ship.stderr
    assert "TAMPERED" not in ship.stdout + ship.stderr
    assert _remote_main(repo) == repo["known_good"]
    assert _pushes(repo) == []


def test_checkout_copy_refuses_mutating_modes_and_reprints_the_bootstrap_command(repo):
    _prepare(repo)
    token = _token(repo)
    result = _loop(repo, "--surface", "telegram", "--mode", "ship", "--risk", "internal", "--approved", token)
    assert result.returncode == 3
    assert "pinned bootstrap" in result.stderr
    assert f"show {repo['released']}:scripts/release_bootstrap.py" in result.stderr
    assert _remote_main(repo) == repo["known_good"]
    assert _pushes(repo) == []


def test_legacy_sha_only_approval_fails_closed_through_the_bootstrap(repo):
    prepare = _prepare(repo)
    command = _printed(prepare.stdout, "ship").replace(f"--approved {_token(repo)}", f"--approved {repo['released']}")
    result = _run_printed(repo, command)
    assert result.returncode == 2
    assert "SHA-only" in result.stderr and "legacy" in result.stderr
    assert _remote_main(repo) == repo["known_good"]
    assert _pushes(repo) == []


def test_a_hand_edited_but_valid_card_is_not_the_approved_card(repo):
    prepare = _prepare(repo)
    command = _printed(prepare.stdout, "ship")
    card_file = repo["card_dir"] / f"{repo['released']}.card.json"
    card = json.loads(card_file.read_text())
    card["effect"] = "A different but perfectly valid effect line."
    card_file.write_text(json.dumps(card, indent=2) + "\n")

    result = _run_printed(repo, command)

    assert result.returncode == 2
    assert "not the approved digest" in result.stderr
    assert _remote_main(repo) == repo["known_good"]
    assert _pushes(repo) == []


def test_reformatting_the_card_file_does_not_change_the_approved_digest(repo):
    prepare = _prepare(repo)
    command = _printed(prepare.stdout, "ship")
    card_file = repo["card_dir"] / f"{repo['released']}.card.json"
    card = json.loads(card_file.read_text())
    card_file.write_text(json.dumps({k: card[k] for k in sorted(card, reverse=True)}, indent=None))

    result = _run_printed(repo, command)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _remote_main(repo) == repo["released"]


# --- rollback: non-mutating, deterministic, reconciled -----------------------


def test_rollback_pushes_a_deterministic_forward_commit_and_leaves_the_checkout_alone(repo):
    prepare = _shipped(repo)
    rollback = _run_printed(repo, _printed(prepare.stdout, "roll back"))

    assert rollback.returncode == 0, rollback.stdout + rollback.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in rollback.stdout
    b = _remote_main(repo)
    assert b not in (repo["released"], repo["known_good"])
    assert _parents(repo, b) == [repo["released"]]
    assert _tree(repo, b) == _tree(repo, repo["known_good"])
    assert git(repo["work"], "log", "-1", "--format=%an <%ae>", b, env=repo["env"]) == (
        "Portfolio Guru release loop <release-loop@portfolio-guru.invalid>"
    )
    assert _pushes(repo) == [
        f"{repo['known_good']} {repo['released']} refs/heads/main",
        f"{repo['released']} {b} refs/heads/main",
    ]
    _checkout_untouched(repo)
    assert git(repo["work"], "rev-parse", "fix/release", env=repo["env"]) == repo["released"]
    assert (repo["work"] / "scripts" / "release_card.py").exists()
    assert not (repo["card_dir"] / "runner").exists()
    journal = _journal(repo)
    assert journal["status"] == "proved" and journal["rollback_sha"] == b


def test_rollback_interrupted_before_the_journal_reruns_onto_the_same_commit_with_one_push(repo):
    prepare = _shipped(repo)
    command = _printed(prepare.stdout, "roll back")

    interrupted = _run_printed(repo, command, {"RELEASE_LOOP_FAULT_AT": "rollback-commit-created"})

    assert interrupted.returncode == 70
    first_sha = re.search(r"ROLLBACK_COMMIT=([0-9a-f]{40})", interrupted.stdout).group(1)
    assert _journal(repo) is None
    assert _remote_main(repo) == repo["released"]
    assert len(_pushes(repo)) == 1
    _checkout_untouched(repo)

    resumed = _run_printed(repo, command)

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert _remote_main(repo) == first_sha, "the rerun must arrive at the very same commit"
    assert git(repo["work"], "rev-list", "--count", f"{repo['released']}..{first_sha}", env=repo["env"]) == "1"
    assert len(_pushes(repo)) == 2
    _checkout_untouched(repo)


def test_rollback_interrupted_after_the_push_before_the_journal_is_reconciled_without_a_second_push(repo):
    """The unknown push: main already holds the rollback commit, and nothing
    local records it. The rerun recognises it on main by shape."""
    prepare = _shipped(repo)
    command = _printed(prepare.stdout, "roll back")

    interrupted = _run_printed(repo, command, {"RELEASE_LOOP_FAULT_AT": "rollback-pushed"})

    assert interrupted.returncode == 70
    b = _remote_main(repo)
    assert _parents(repo, b) == [repo["released"]]
    journal = _journal(repo)
    assert journal is not None and journal["status"] == "committed"
    assert len(_pushes(repo)) == 2

    resumed = _run_printed(repo, command)

    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "already a rollback commit" in resumed.stdout
    assert "skipping a duplicate push" in resumed.stdout
    assert _remote_main(repo) == b
    assert len(_pushes(repo)) == 2, "no second push may be made"
    assert _journal(repo)["rollback_sha"] == b
    _checkout_untouched(repo)


def test_rollback_rerun_after_losing_the_journal_is_idempotent(repo):
    prepare = _shipped(repo)
    command = _printed(prepare.stdout, "roll back")
    assert _run_printed(repo, command).returncode == 0
    b = _remote_main(repo)
    (repo["card_dir"] / f"{repo['released']}.rollback.json").unlink()

    again = _run_printed(repo, command)

    assert again.returncode == 0, again.stdout + again.stderr
    assert _remote_main(repo) == b
    assert git(repo["work"], "rev-list", "--count", f"{repo['known_good']}..{b}", env=repo["env"]) == "2"
    assert len(_pushes(repo)) == 2
    _checkout_untouched(repo)


def test_rollback_refuses_when_main_holds_a_commit_that_is_not_this_rollback(repo):
    prepare = _shipped(repo)
    # Someone else moved main forward: same parent, but not the known-good tree.
    stray = git(
        repo["work"], "commit-tree", _tree(repo, repo["released"]), "-p", repo["released"], "-m", "stray",
        env=repo["env"],
    )
    git(repo["work"], "push", "-q", "origin", f"{stray}:refs/heads/main", env=repo["env"])
    pushes_before = len(_pushes(repo))

    result = _run_printed(repo, _printed(prepare.stdout, "roll back"))

    assert result.returncode == 3
    assert "neither the released SHA" in result.stderr
    assert _remote_main(repo) == stray
    assert len(_pushes(repo)) == pushes_before
    _checkout_untouched(repo)


def test_rollback_is_blocked_not_rolled_back_when_ci_fails_for_the_rollback_commit(repo):
    prepare = _shipped(repo)
    result = _run_printed(repo, _printed(prepare.stdout, "roll back"), {"FAKE_CI_CONCLUSION": "failure"})
    assert result.returncode == 1
    assert "FINAL_RELEASE_STATE=blocked" in result.stdout
    assert "rolled-back" not in result.stdout
    assert "The rollback is not live" in result.stderr
    assert _parents(repo, _remote_main(repo)) == [repo["released"]]
    _checkout_untouched(repo)


def test_fault_checkpoints_are_inert_outside_test_mode(repo):
    prepare = _shipped(repo)
    result = _run_printed(
        repo,
        _printed(prepare.stdout, "roll back"),
        {
            "RELEASE_LOOP_TEST_MODE": "0",
            "RELEASE_LOOP_PROOF_TIMEOUT": "1",
            "RELEASE_LOOP_PROOF_INTERVAL": "1",
            "RELEASE_LOOP_FAULT_AT": "rollback-pushed",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TEST FAULT" not in result.stderr
    assert "FINAL_RELEASE_STATE=rolled-back" in result.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
