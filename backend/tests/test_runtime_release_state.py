"""The runtime must know whether it is running released code.

The deploy checkout at ~/projects/portfolio-guru doubles as a dev workspace, so
it is routinely sitting on a feature branch with uncommitted edits. The bot
loads its code at process start, which means launchd's crash-restart can put
that unreleased code in front of paying doctors with nothing to announce it.

`build_runtime_identity` is what `scripts/verify_live_runtime.py` and the
startup warning both read, so the invariant guarded here is narrow and
concrete: the identity must report branch and dirtiness truthfully. If it
silently reports "clean" for a dirty tree, the startup warning never fires and
the hazard becomes invisible again.
"""

import subprocess

import pytest

from runtime_identity import build_runtime_identity, working_tree_dirty


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repo with one commit on `main`."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "app.py").write_text("VERSION = 1\n")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def test_clean_checkout_reports_not_dirty(repo):
    assert working_tree_dirty(repo) is False
    assert build_runtime_identity(repo)["dirty"] is False


def test_uncommitted_edit_to_tracked_file_is_dirty(repo):
    """The case that matters: edited code that a restart would load."""
    (repo / "app.py").write_text("VERSION = 2  # unreleased\n")

    assert working_tree_dirty(repo) is True
    assert build_runtime_identity(repo)["dirty"] is True


def test_untracked_file_alone_is_not_dirty(repo):
    """Scratch files are not importable code and must not cry wolf.

    A warning that fires on every stray .log trains the operator to ignore it,
    which is how the backup warnings were ignored for 53 nights.
    """
    (repo / "scratch.log").write_text("noise\n")

    assert working_tree_dirty(repo) is False
    assert build_runtime_identity(repo)["dirty"] is False


def test_identity_reports_the_actual_branch(repo):
    """The startup check compares this against the released branch."""
    assert build_runtime_identity(repo)["branch"] == "main"

    _git(repo, "checkout", "-b", "fix/some-wip")
    assert build_runtime_identity(repo)["branch"] == "fix/some-wip"


def test_staged_but_uncommitted_change_is_dirty(repo):
    """Staging is not releasing — a restart still loads the working copy."""
    (repo / "app.py").write_text("VERSION = 3\n")
    _git(repo, "add", "app.py")

    assert working_tree_dirty(repo) is True


def test_dirty_detection_never_raises_outside_a_repo(tmp_path):
    """Startup must not crash because the runtime is not in a git checkout."""
    assert working_tree_dirty(tmp_path / "not-a-repo") is False
