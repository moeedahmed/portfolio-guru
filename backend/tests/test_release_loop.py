"""Offline guards for scripts/release_loop.sh.

These tests cover the deterministic release-closure wrapper's safe surfaces:
shell syntax, --help, and every refusal/usage gate. They never invoke ship
with approval, never push/deploy/restart, and never run the heavy prepare
suites (those are exercised by scripts/preflight.sh itself). Every command
here exits before release_loop.sh performs any git fetch or mutation.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "release_loop.sh"


def run(*args, env=None):
    """Run release_loop.sh from the repo root, capturing output and exit code."""
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert SCRIPT.stat().st_mode & 0o111, "release_loop.sh should be executable"


def test_shell_syntax_is_valid():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_help_lists_required_surfaces_and_modes():
    result = run("--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "--surface" in out
    assert "telegram" in out
    assert "prepare" in out
    assert "ship" in out


def test_missing_mode_is_usage_error():
    result = run("--surface", "telegram")
    assert result.returncode == 64
    assert "Missing --mode" in result.stderr


def test_invalid_mode_is_usage_error():
    result = run("--surface", "telegram", "--mode", "bogus")
    assert result.returncode == 64
    assert "Invalid --mode" in result.stderr


def test_unsupported_surface_is_usage_error():
    result = run("--surface", "web", "--mode", "prepare")
    assert result.returncode == 64
    assert "Unsupported --surface" in result.stderr


def test_ship_refuses_without_approval():
    """ship must refuse and exit 2 before any live action when unapproved."""
    env = {"PATH": _path_only()}
    result = run("--surface", "telegram", "--mode", "ship", env=env)
    assert result.returncode == 2
    assert "approval required" in result.stderr.lower()
    assert "FINAL_RELEASE_STATE=release-ready" in result.stdout
    assert "FINAL_RELEASE_GATE=" in result.stdout


def test_ship_refuses_with_stale_approval_token():
    """A token for a different day/surface is rejected as stale."""
    env = {"PATH": _path_only(), "RELEASE_APPROVED": "telegram-19990101"}
    result = run("--surface", "telegram", "--mode", "ship", env=env)
    assert result.returncode == 2
    assert "stale or wrong surface" in result.stderr.lower()
    assert "FINAL_RELEASE_STATE=release-ready" in result.stdout


def test_ship_reports_proof_pending_when_live_proof_is_not_collected(tmp_path):
    """A gated ship without deploy/dogfood proof must not call itself live."""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    (fake_root / "scripts").mkdir()
    (fake_root / "scripts" / "preflight.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (fake_root / "scripts" / "telegram_qa_offline.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "git").write_text(
        f"""#!/usr/bin/env bash
case "$1 $2" in
  "rev-parse --show-toplevel") printf '%s\\n' "{fake_root}" ;;
  "rev-parse --short") printf 'abc1234\\n' ;;
  "branch --show-current") printf 'feature/final-state\\n' ;;
  "status --porcelain") exit 0 ;;
  "ls-files --others") exit 0 ;;
  "fetch origin") exit 0 ;;
  "merge-base --is-ancestor") exit 0 ;;
  "rev-list --left-right") printf '0 1\\n' ;;
  "checkout main") exit 0 ;;
  "pull --ff-only") exit 0 ;;
  "merge --ff-only") exit 0 ;;
  "push origin") exit 0 ;;
  "checkout feature/final-state") exit 0 ;;
  *) exit 0 ;;
esac
"""
    )
    (fake_bin / "git").chmod(0o755)
    env = {"PATH": f"{fake_bin}:{_path_only()}"}

    result = run(
        "--surface",
        "telegram",
        "--mode",
        "ship",
        "--approved",
        "--no-dogfood",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "FINAL_RELEASE_STATE=proof-pending" in result.stdout
    assert "FINAL_RELEASE_STATE=live" not in result.stdout
    assert "FINAL_RELEASE_GATE=collect deploy/restart proof" in result.stdout


def _path_only():
    """Minimal PATH so git is still resolvable but no extra env leaks in."""
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
