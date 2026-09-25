"""Persistence that keeps clinical content in memory and off the disk.

``retention.py`` used to claim conversation persistence "holds at most the
in-flight draft, cleared on save". It didn't. A live inspection on 2026-08-24
found ``bot_persistence`` holding case narrative and drafted clinical text for
20 users — ``last_filed_case_text``, ``last_amend_case_text``,
``last_amend_draft``, ``last_draft_preview`` — indefinitely, in an unencrypted
pickle, long after each case was filed to Kaizen.

Those keys aren't dead weight: they back the same-case refile button, the amend
flow and draft preview restore. So the fix isn't to delete them, it's to stop
writing them down. They stay in ``context.user_data`` for as long as the process
lives, and every feature that reads them keeps working. They simply never reach
the disk, so a stolen machine, a leaked backup or a stale pickle yields no
clinical narrative.

The trade, stated plainly: after a restart the doctor can't amend or re-file
from a case sent before it. The encrypted ``draft_backup`` file is what survives
a crash instead, and it expires on its own.

The case still in progress is different. Dropping it meant every deploy left
doctors mid-case at "draft ready" with no draft, so their next tap said the
draft had expired. ``WORKING_CASE_KEYS`` — the live case only, never the
filed-case history — are kept across a restart in a per-user Fernet file that
fails closed without a key, expires after ``PG_WORKING_CASE_TTL_HOURS``
(default 24), disappears as soon as the case is filed or cancelled (the keys
leave ``user_data``), and is erased by ``/reset``.
"""
from __future__ import annotations

import logging
import os
import pickle
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telegram.ext import PicklePersistence

from data_paths import data_path

logger = logging.getLogger(__name__)

# Keys whose values are, or are derived from, the doctor's case narrative.
# Adding a new key that holds case content means adding it here — the test suite
# pins this set against bot.py so a new one can't slip through unnoticed.
CLINICAL_USER_DATA_KEYS: frozenset[str] = frozenset({
    # Raw case narrative, in-flight and retained.
    "case_text",
    "pending_new_case_text",
    "accumulation_additions",
    "last_filed_case_text",
    "last_amend_case_text",
    # Drafted clinical content.
    "draft_data",
    "pending_draft_data",
    "last_amend_draft",
    "last_draft_preview",
    # Model output quoting or reasoning over the case.
    "form_recommendations",
    "form_recommendations_text",
    # Raw excerpts of the doctor's own prior portfolio entries.
    "voice_examples",
})


def scrub(data: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``data`` with clinical keys removed.

    A copy, never a mutation: the live ``user_data`` the handlers read from must
    keep its clinical content for the rest of the conversation.
    """
    if not data:
        return {}
    return {k: v for k, v in data.items() if k not in CLINICAL_USER_DATA_KEYS}


# The case in progress: what a doctor loses if a restart lands mid-case.
# Filed-case history (last_*) and voice examples stay memory-only.
WORKING_CASE_KEYS: frozenset[str] = frozenset({
    "case_text",
    "pending_new_case_text",
    "accumulation_additions",
    "draft_data",
    "pending_draft_data",
    "form_recommendations",
    "form_recommendations_text",
})
# In-flight guards that only mean something while this process runs. Written
# to disk they outlived a crash and blocked every later Save ("Already saving").
TRANSIENT_USER_DATA_KEYS: frozenset[str] = frozenset({"filing_in_progress", "form_choice_in_progress"})
_WORKING_CASE_TTL_ENV = "PG_WORKING_CASE_TTL_HOURS"
_WORKING_CASE_SUFFIX = ".enc"
_USER_FILE = re.compile(r"^-?\d+$")


def working_case_dir() -> Path:
    return data_path("working-cases")


def _working_case_ttl() -> timedelta:
    try:
        return timedelta(hours=max(1, int(os.environ.get(_WORKING_CASE_TTL_ENV, "24"))))
    except ValueError:
        return timedelta(hours=24)


def _working_case_file(user_id: int) -> Path:
    return working_case_dir() / f"{int(user_id)}{_WORKING_CASE_SUFFIX}"


def save_working_case(user_id: int, data: dict[str, Any] | None) -> None:
    """Encrypt the live case to disk, or remove the file when there is none.

    Never raises: losing restart recovery is recoverable, a crashed flush or a
    plaintext fallback is not.
    """
    working = {k: v for k, v in (data or {}).items() if k in WORKING_CASE_KEYS}
    path = _working_case_file(user_id)
    if not working:
        path.unlink(missing_ok=True)
        return
    try:
        from credentials import _fernet

        payload = pickle.dumps({"saved_at": datetime.now(timezone.utc), "data": working})
        token = _fernet().encrypt(payload)
    except Exception:
        logger.warning("Working case not kept for restart: encryption unavailable", exc_info=True)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(token)
        path.chmod(0o600)
    except OSError:
        logger.warning("Working case not kept for restart: write failed", exc_info=True)


def load_working_cases(now: datetime | None = None) -> dict[int, dict[str, Any]]:
    """Decrypt every unexpired working case; delete expired or unreadable ones."""
    directory = working_case_dir()
    if not directory.is_dir():
        return {}
    now = now or datetime.now(timezone.utc)
    restored: dict[int, dict[str, Any]] = {}
    for path in directory.glob(f"*{_WORKING_CASE_SUFFIX}"):
        stem = path.name[: -len(_WORKING_CASE_SUFFIX)]
        if not _USER_FILE.match(stem):
            continue
        try:
            from credentials import _fernet

            payload = pickle.loads(_fernet().decrypt(path.read_bytes()))
            fresh = now - payload["saved_at"] <= _working_case_ttl()
        except Exception:
            logger.warning("Working case %s unreadable; removing", path.name, exc_info=True)
            fresh, payload = False, None
        if fresh:
            restored[int(stem)] = payload["data"]
        else:
            path.unlink(missing_ok=True)
    return restored


def purge_working_case(user_id: int) -> int:
    """Erase a user's working case. Wired into /reset (GDPR Art. 17)."""
    path = _working_case_file(user_id)
    if path.exists():
        path.unlink()
        return 1
    return 0


class ClinicalScrubbingPersistence(PicklePersistence):
    """PicklePersistence that keeps clinical keys out of the pickle.

    The live case goes to an encrypted side file instead and is merged back
    when the bot starts.
    """

    async def get_user_data(self) -> dict[int, dict[str, Any]]:
        stored = await super().get_user_data()
        for data in stored.values():
            for key in TRANSIENT_USER_DATA_KEYS:
                data.pop(key, None)
        for user_id, working in load_working_cases().items():
            stored.setdefault(user_id, {}).update(working)
        return stored

    async def update_user_data(self, user_id: int, data: dict[str, Any]) -> None:
        save_working_case(user_id, data)
        durable = {k: v for k, v in scrub(data).items() if k not in TRANSIENT_USER_DATA_KEYS}
        await super().update_user_data(user_id, durable)

    async def drop_user_data(self, user_id: int) -> None:
        purge_working_case(user_id)
        await super().drop_user_data(user_id)


def purge_existing_file(path) -> dict[str, Any]:
    """Strip clinical keys from an existing persistence file, in place.

    One-shot repair for the pickle that predates this module. Returns a summary
    for the operator; never raises, because a failed cleanup must not stop the
    bot from starting.
    """
    import pathlib
    import pickle

    path = pathlib.Path(path)
    if not path.exists():
        return {"status": "absent"}
    try:
        payload = pickle.loads(path.read_bytes())
    except Exception as exc:
        logger.warning("Persistence purge could not read %s: %s", path, exc)
        return {"status": "unreadable", "error": str(exc)}

    user_data = payload.get("user_data") or {}
    removed: dict[str, int] = {}
    for uid, data in list(user_data.items()):
        if not isinstance(data, dict):
            continue
        for key in list(data):
            if key in CLINICAL_USER_DATA_KEYS:
                del data[key]
                removed[key] = removed.get(key, 0) + 1
    if not removed:
        return {"status": "clean", "users": len(user_data)}

    try:
        path.write_bytes(pickle.dumps(payload))
    except Exception as exc:
        logger.warning("Persistence purge could not rewrite %s: %s", path, exc)
        return {"status": "write-failed", "error": str(exc)}
    return {"status": "purged", "users": len(user_data), "removed": removed}


__all__ = [
    "CLINICAL_USER_DATA_KEYS",
    "ClinicalScrubbingPersistence",
    "WORKING_CASE_KEYS",
    "load_working_cases",
    "purge_existing_file",
    "purge_working_case",
    "save_working_case",
    "scrub",
]
