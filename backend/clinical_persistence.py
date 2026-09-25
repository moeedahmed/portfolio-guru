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
"""
from __future__ import annotations

import logging
from typing import Any

from telegram.ext import PicklePersistence

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


class ClinicalScrubbingPersistence(PicklePersistence):
    """PicklePersistence that drops clinical keys on the way to disk."""

    async def update_user_data(self, user_id: int, data: dict[str, Any]) -> None:
        await super().update_user_data(user_id, scrub(data))


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
    "purge_existing_file",
    "scrub",
]
