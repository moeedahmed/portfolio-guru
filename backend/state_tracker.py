"""Lightweight JSON-backed state tracker for assessor ticket polling.

Persists which assessor ticket UUIDs the poller has already seen, so that a
re-poll does not refire notifications for known tickets. Used by
``supervisor_poller.py`` to diff a fresh Kaizen queue against on-disk state.

This module never touches Kaizen and never reads ticket content. It only
stores ``(ticket_uuid, status)`` rows in a JSON file on the local filesystem.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TrackedState:
    """In-memory mirror of the on-disk seen-tickets JSON file."""

    path: Path
    seen_tickets: dict[str, str] = field(default_factory=dict)

    def is_new_ticket(self, ticket_uuid: str) -> bool:
        return ticket_uuid not in self.seen_tickets

    def mark_seen(self, ticket_uuid: str, *, status: str) -> None:
        self.seen_tickets[ticket_uuid] = status

    def filter_new(self, ticket_uuids: list[str]) -> list[str]:
        return [uuid for uuid in ticket_uuids if self.is_new_ticket(uuid)]

    def save(self) -> None:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"seen_tickets": self.seen_tickets}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> "TrackedState":
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("State tracker file %s unreadable (%s); starting fresh", path, exc)
            return cls(path=path)
        seen = payload.get("seen_tickets") if isinstance(payload, dict) else None
        if not isinstance(seen, dict):
            return cls(path=path)
        return cls(path=path, seen_tickets={str(k): str(v) for k, v in seen.items()})
