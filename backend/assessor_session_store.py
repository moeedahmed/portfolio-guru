"""Per-supervisor active assessor session store.

When a supervisor taps **Open** on a notification, the bot records a
session here so the next text or voice note from that user can be
captured as assessor intent without the supervisor having to repeat the
ticket UUID. The file layout mirrors :mod:`supervisor_notification_cache`:

* One JSON file per Telegram user — easy to inspect and isolated.
* Missing or corrupt files behave like "no session" — the supervisor's
  message simply falls through to the trainee flow rather than raising.
* Stored payloads contain only the metadata the bot needs to render and
  re-prompt: form type, ticket URL/UUID, the trainee section (already
  PHI-permitted post-Open), the latest intent text, and the latest
  draft. Nothing is sent to Kaizen from this layer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from assessor_drafter import AssessorDraft

logger = logging.getLogger(__name__)


@dataclass
class AssessorSession:
    """Active assessor capture session for one Telegram user."""

    ticket_uuid: str
    form_type: str | None
    ticket_url: str | None
    trainee_section: list[dict[str, Any]] = field(default_factory=list)
    pending_assessor_fields: list[dict[str, Any]] = field(default_factory=list)
    intent: str | None = None
    draft: dict[str, Any] | None = None


def _session_path(base_dir: Path, telegram_user_id: int) -> Path:
    return Path(base_dir) / f"assessor_session_{telegram_user_id}.json"


def _load_raw(base_dir: Path, telegram_user_id: int) -> dict[str, Any] | None:
    path = _session_path(base_dir, telegram_user_id)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Assessor session %s unreadable (%s); treating as empty", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _save(base_dir: Path, telegram_user_id: int, payload: dict[str, Any]) -> None:
    path = _session_path(base_dir, telegram_user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def start(
    base_dir: Path,
    *,
    telegram_user_id: int,
    ticket_uuid: str,
    form_type: str | None,
    ticket_url: str | None,
    trainee_section: list[dict[str, Any]] | None = None,
    pending_assessor_fields: list[dict[str, Any]] | None = None,
) -> AssessorSession:
    """Persist a fresh assessor capture session, overwriting any prior one."""
    session = AssessorSession(
        ticket_uuid=ticket_uuid,
        form_type=form_type,
        ticket_url=ticket_url,
        trainee_section=list(trainee_section or []),
        pending_assessor_fields=list(pending_assessor_fields or []),
    )
    _save(base_dir, telegram_user_id, asdict(session))
    return session


def get(base_dir: Path, *, telegram_user_id: int) -> AssessorSession | None:
    """Return the active session for ``telegram_user_id`` or ``None``."""
    raw = _load_raw(base_dir, telegram_user_id)
    if raw is None:
        return None
    try:
        return AssessorSession(**raw)
    except TypeError as exc:
        logger.warning(
            "Assessor session for %s malformed (%s); dropping row.",
            telegram_user_id,
            exc,
        )
        return None


def update_intent(
    base_dir: Path,
    *,
    telegram_user_id: int,
    intent: str,
) -> AssessorSession | None:
    """Attach the supervisor's latest intent text and persist."""
    session = get(base_dir, telegram_user_id=telegram_user_id)
    if session is None:
        return None
    session.intent = intent
    _save(base_dir, telegram_user_id, asdict(session))
    return session


def update_draft(
    base_dir: Path,
    *,
    telegram_user_id: int,
    draft: AssessorDraft,
) -> AssessorSession | None:
    """Attach the latest structured draft and persist."""
    session = get(base_dir, telegram_user_id=telegram_user_id)
    if session is None:
        return None
    session.draft = asdict(draft)
    _save(base_dir, telegram_user_id, asdict(session))
    return session


def end(base_dir: Path, *, telegram_user_id: int) -> bool:
    """Drop the session file. Returns True when a session was removed."""
    path = _session_path(base_dir, telegram_user_id)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("Failed to remove assessor session %s: %s", path, exc)
        return False
    return True
