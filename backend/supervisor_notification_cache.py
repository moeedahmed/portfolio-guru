"""Per-user cache of pending supervisor notifications.

When the scheduler dispatches an inline-keyboard notification to a
supervisor, it stashes the PHI-free payload here. The Open / Skip /
Later callback handlers then look up the original ``ticket_url`` and
form type by ticket UUID without re-polling Kaizen.

The cache is intentionally minimal:

* One JSON file per Telegram user — small, easy to inspect, isolated.
* Corruption returns "empty" rather than raising — a misformatted file
  never blocks the bot from delivering trainee-facing flows.
* Stored payloads carry the same fields as
  :class:`supervisor_workflow.SupervisorNotificationPayload`. Trainee
  names, dates, narrative, and attachment metadata are *not* stored.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from supervisor_workflow import SupervisorNotificationPayload

logger = logging.getLogger(__name__)


def _cache_path(base_dir: Path, telegram_user_id: int) -> Path:
    return Path(base_dir) / f"supervisor_notifications_{telegram_user_id}.json"


def _load(base_dir: Path, telegram_user_id: int) -> dict[str, dict]:
    path = _cache_path(base_dir, telegram_user_id)
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Notification cache %s unreadable (%s); treating as empty", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(uuid): row for uuid, row in data.items() if isinstance(row, dict)}


def _save(base_dir: Path, telegram_user_id: int, entries: dict[str, dict]) -> None:
    path = _cache_path(base_dir, telegram_user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def remember(
    base_dir: Path,
    *,
    telegram_user_id: int,
    payload: SupervisorNotificationPayload,
) -> None:
    """Persist a notification payload keyed by ticket UUID."""
    entries = _load(base_dir, telegram_user_id)
    entries[payload.ticket_uuid] = asdict(payload)
    _save(base_dir, telegram_user_id, entries)


def lookup(
    base_dir: Path,
    *,
    telegram_user_id: int,
    ticket_uuid: str,
) -> SupervisorNotificationPayload | None:
    """Return the cached payload for ``(user, uuid)`` or ``None``."""
    entries = _load(base_dir, telegram_user_id)
    row = entries.get(ticket_uuid)
    if not row:
        return None
    try:
        return SupervisorNotificationPayload(**row)
    except TypeError as exc:
        # Schema drift between bot restarts — drop the row rather than crash.
        logger.warning("Notification cache row %s/%s malformed: %s", telegram_user_id, ticket_uuid, exc)
        return None


def forget(
    base_dir: Path,
    *,
    telegram_user_id: int,
    ticket_uuid: str,
) -> None:
    """Drop one payload from the cache. No-op when the entry is absent."""
    entries = _load(base_dir, telegram_user_id)
    if ticket_uuid not in entries:
        return
    entries.pop(ticket_uuid)
    _save(base_dir, telegram_user_id, entries)


def list_pending(
    base_dir: Path,
    *,
    telegram_user_id: int,
) -> list[SupervisorNotificationPayload]:
    """Return every cached payload for a user — useful for /supervisor."""
    entries = _load(base_dir, telegram_user_id)
    out: list[SupervisorNotificationPayload] = []
    for uuid, row in entries.items():
        try:
            out.append(SupervisorNotificationPayload(**row))
        except TypeError as exc:
            logger.warning("Notification cache row %s/%s malformed: %s", telegram_user_id, uuid, exc)
    return out
