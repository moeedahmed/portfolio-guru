"""Off-device backup must fail loudly, and must prove the copy landed.

Guards the production incident of 2026-06-26 → 2026-08-17: `gcloud storage cp`
was invoked with `>/dev/null 2>&1` and its failure collapsed into a WARNING on
an exit-0 run. Every nightly upload failed for 53 consecutive nights while the
log said "Backup complete" and the runbook described off-device protection as
LIVE. Nothing surfaced it because nothing checked.

The invariants below are the ones that would have caught it:

1. An off-device copy that does not land is a FAILED run (non-zero exit), not a
   warning attached to a successful one.
2. The transport's own error text reaches the log rather than /dev/null.
3. A run only claims success when the off-device copy is verifiably present.

These are shell-level behaviours, so the tests drive the real script offline
against a local rsync-style destination — no network, no GCS, no Telegram.
"""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup_db.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("sqlite3") is None,
    reason="backup_db.sh needs gpg and sqlite3 available",
)


def _make_data_dir(tmp_path: Path) -> Path:
    """A minimal but realistic data dir: one sqlite db plus a flat state file."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "portfolio_guru.db")
    conn.execute("CREATE TABLE usercredential (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    (data_dir / "health_profiles.json").write_text("{}")
    return data_dir


def _run_backup(tmp_path: Path, remote: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PORTFOLIO_GURU_DATA_DIR": str(_make_data_dir(tmp_path)),
        "PG_BACKUP_DIR": str(tmp_path / "archives"),
        "PG_BACKUP_REMOTE": remote,
        "PG_BACKUP_GPG_PASSPHRASE": "test-passphrase-not-a-secret",
        # Never page the operator or touch BWS from a test run.
        "PG_BACKUP_DISABLE_ALERTS": "1",
    }
    return subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_unreachable_offdevice_target_fails_the_run(tmp_path):
    """The regression itself: a failed off-device copy must not exit 0.

    Before the fix this run printed "Backup complete." and exited 0, which is
    what let 53 nights of failure pass unnoticed.
    """
    unwritable = tmp_path / "nonexistent-parent" / "backups"

    result = _run_backup(tmp_path, str(unwritable))

    assert result.returncode != 0, (
        "off-device failure must fail the run; exited 0 with:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "BACKUP FAILED off-device" in result.stderr
    # The success sentence must not appear when the copy did not land.
    assert "off-device copy verified" not in result.stdout


def test_failure_surfaces_the_transport_error(tmp_path):
    """The underlying error text must reach the log, not /dev/null.

    The original bug was undiagnosable from the log alone: the real cause (a
    gcloud CommandLoadFailure under launchd's Python 3.9) was discarded.
    """
    result = _run_backup(tmp_path, str(tmp_path / "nonexistent-parent" / "backups"))

    combined = result.stdout + result.stderr
    # rsync's own diagnostic mentions the path it could not write.
    assert "nonexistent-parent" in combined, (
        f"transport error was swallowed; got:\n{combined}"
    )


def test_successful_offdevice_copy_lands_and_is_encrypted(tmp_path):
    """A green run must leave a real, encrypted artifact at the destination."""
    remote = tmp_path / "offdevice"
    remote.mkdir()

    result = _run_backup(tmp_path, str(remote))

    assert result.returncode == 0, (
        f"expected success; stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "off-device copy verified" in result.stdout

    landed = list(remote.glob("portfolio-guru-backup-*.tar.gz.gpg"))
    assert len(landed) == 1, f"expected exactly one encrypted archive, got {landed}"

    # Clinical data must never leave the box in the clear: the off-device copy
    # is GPG-encrypted, so it must NOT be a readable gzip archive.
    assert landed[0].read_bytes()[:2] != b"\x1f\x8b", (
        "off-device artifact is a plain gzip — encryption did not happen"
    )


def test_local_archive_survives_offdevice_failure(tmp_path):
    """Failing the run must not cost the local backup we did successfully take."""
    result = _run_backup(tmp_path, str(tmp_path / "nonexistent-parent" / "backups"))

    assert result.returncode != 0
    archives = list((tmp_path / "archives").glob("portfolio-guru-backup-*.tar.gz"))
    assert len(archives) == 1, f"local archive should still exist, got {archives}"
    # And the transient encrypted copy must be cleaned up, not left on disk.
    assert not list((tmp_path / "archives").glob("*.gpg"))
