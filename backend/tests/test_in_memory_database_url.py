"""An in-memory DATABASE_URL must never become a file on disk.

The stores used to strip "sqlite:///" off DATABASE_URL to find their file. For
the in-memory URL "sqlite://" that left the relative path "sqlite://", which
opened a stray file called "sqlite:" in whatever directory the process ran in
(backend/, during the subprocess-based live-harness tests).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _run_in(tmp_path: Path, database_url: str) -> None:
    code = (
        "import credentials, profile_store\n"
        "credentials.init_db()\n"
        "profile_store.init_profile_db()\n"
        "profile_store._migrate_add_column('curriculum', 'TEXT')\n"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        cwd=tmp_path,
        timeout=60,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(BACKEND),
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "DATABASE_URL": database_url,
            "FERNET_SECRET_KEY": "5Wv33F9sq99WGD2lEzwwd3J_JH5p6vxKdDiAwCWqoYQ=",
        },
    )


def test_in_memory_database_url_creates_no_files(tmp_path):
    _run_in(tmp_path, "sqlite://")

    assert list(tmp_path.iterdir()) == []


def test_file_database_url_still_creates_its_file(tmp_path):
    db = tmp_path / "nested" / "portfolio_guru.db"

    _run_in(tmp_path, f"sqlite:///{db}")

    assert db.exists()
