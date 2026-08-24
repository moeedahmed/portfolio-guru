"""Encrypted, short-lived local backup of the draft about to be filed.

Written immediately before a Kaizen save so a crash mid-filing doesn't lose the
draft the doctor already approved. Deleted the moment Kaizen confirms the save:
Kaizen then holds the evidence, and Portfolio Guru has no product reason to keep
a second copy of clinical narrative
(``docs/data-architecture-plan-2026-08-24.md``, decision 2).

Orphans — backups whose filing failed — expire after
``PG_DRAFT_BACKUP_TTL_DAYS`` (default 7), long enough to recover a failed save
over a shift or two and no longer.

Two rules this module exists to enforce:

1. **Fail closed.** If Fernet is unavailable the backup is skipped entirely
   rather than written as plaintext. Losing a crash-recovery copy is
   recoverable; writing unencrypted clinical narrative to disk is not. This
   replaces the plaintext ``drafts/*.json`` writer that used to live inline in
   ``bot.py`` and survived both ``/reset`` and the retention purge.
2. **Erasable.** ``purge_user`` is wired into ``/reset``, so these files are
   covered by the GDPR Art. 17 path like every other local store.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_PATH_ENV = "PORTFOLIO_GURU_DRAFT_BACKUP_DIR"
_TTL_ENV = "PG_DRAFT_BACKUP_TTL_DAYS"
DEFAULT_TTL_DAYS = 7

SUFFIX = ".json.enc"

# Anything that could escape the backup directory or collide across users.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
# Separators are already gone by the time this runs, so a surviving dot run
# can't traverse — but a filename containing ".." reads like a bug forever after.
_DOT_RUN = re.compile(r"\.{2,}")


def backup_dir() -> pathlib.Path:
    override = os.environ.get(_PATH_ENV)
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".openclaw" / "data" / "portfolio-guru" / "drafts"


def ttl_days() -> int:
    try:
        return max(1, int(os.environ.get(_TTL_ENV, str(DEFAULT_TTL_DAYS))))
    except ValueError:
        return DEFAULT_TTL_DAYS


def _slug(value: Any) -> str:
    return _DOT_RUN.sub(".", _UNSAFE.sub("-", str(value)))


def _filename(user_id: int, form_type: str, on: date | None = None) -> str:
    stamp = (on or date.today()).isoformat()
    return f"{_slug(user_id)}_{_slug(form_type)}_{stamp}{SUFFIX}"


def save(
    user_id: int,
    form_type: str,
    fields: dict[str, Any],
    *,
    on: date | None = None,
) -> pathlib.Path | None:
    """Encrypt and write the pre-filing backup. Returns the path, or None if the
    backup was skipped. Never raises — a filing must not fail because its
    crash-recovery copy could not be written."""
    try:
        from credentials import _fernet

        payload = json.dumps(
            {
                "form_type": form_type,
                "fields": fields,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            },
            default=str,
        )
        token = _fernet().encrypt(payload.encode())
    except Exception:
        # Fail closed: no key, no backup. Never a plaintext fallback.
        logger.warning(
            "Draft backup skipped — encryption unavailable; continuing with filing",
            exc_info=True,
        )
        return None

    try:
        directory = backup_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _filename(user_id, form_type, on)
        path.write_bytes(token)
        return path
    except OSError:
        logger.warning("Draft backup write failed; continuing with filing", exc_info=True)
        return None


def load(path: pathlib.Path) -> dict[str, Any] | None:
    """Decrypt a backup for crash recovery. Returns None if it can't be read."""
    try:
        from credentials import _fernet

        return json.loads(_fernet().decrypt(path.read_bytes()).decode())
    except Exception:
        logger.warning("Draft backup could not be read: %s", path.name, exc_info=True)
        return None


def _unlink_all(paths) -> int:
    removed = 0
    for path in paths:
        try:
            path.unlink()
            removed += 1
        except OSError:
            logger.debug("Could not remove draft backup %s", path.name, exc_info=True)
    return removed


def discard(user_id: int, form_type: str, *, on: date | None = None) -> int:
    """Delete the backup for a filing Kaizen has confirmed. Called on save
    success — this is what makes 'we don't keep clinical content' true."""
    directory = backup_dir()
    if not directory.is_dir():
        return 0
    exact = directory / _filename(user_id, form_type, on)
    if exact.exists():
        return _unlink_all([exact])
    # The date can roll over between writing the backup and Kaizen confirming.
    pattern = f"{_slug(user_id)}_{_slug(form_type)}_*{SUFFIX}"
    return _unlink_all(sorted(directory.glob(pattern)))


def purge_user(user_id: int) -> int:
    """Delete every backup for a user. Wired into /reset (GDPR Art. 17)."""
    directory = backup_dir()
    if not directory.is_dir():
        return 0
    return _unlink_all(sorted(directory.glob(f"{_slug(user_id)}_*{SUFFIX}")))


def purge_expired(now: datetime | None = None) -> dict[str, Any]:
    """Delete orphaned backups older than the TTL — filings that never
    succeeded, so ``discard`` never ran. Idempotent."""
    directory = backup_dir()
    if not directory.is_dir():
        return {"status": "ok", "removed": 0, "ttl_days": ttl_days()}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=ttl_days())
    cutoff_ts = cutoff.timestamp()
    expired = []
    for path in sorted(directory.glob(f"*{SUFFIX}")):
        try:
            if path.stat().st_mtime < cutoff_ts:
                expired.append(path)
        except OSError:
            continue
    return {
        "status": "ok",
        "removed": _unlink_all(expired),
        "ttl_days": ttl_days(),
    }


__all__ = [
    "backup_dir",
    "discard",
    "load",
    "purge_expired",
    "purge_user",
    "save",
    "ttl_days",
]
