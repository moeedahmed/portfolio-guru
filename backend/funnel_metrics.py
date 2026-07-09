"""
Portfolio Guru Funnel Metrics
=============================

PHI-free NDJSON event log for the Telegram beta funnel.

The goal is to answer launch-readiness questions without reading bot logs:

1. How many real users reached case -> preview -> Kaizen draft?
2. How many real users repeated that outcome?
3. Where is the funnel dropping users?

No case text, credentials, decrypted values, or raw message content are stored.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, Optional

from filing_attempt_log import is_synthetic_user

logger = logging.getLogger(__name__)

_SAFE_METADATA_KEYS = frozenset(
    {
        "source",
        "form_type",
        "state",
        "count",
        "has_draft",
        "has_missing",
        "tier",
        "reason",
    }
)

_KEY_EVENTS = (
    "case_started",
    "recommendation_shown",
    "form_chosen",
    "best_fit_chosen",
    "draft_previewed",
    "save_attempted",
    "draft_saved",
    "filing_failed",
)


def default_log_path() -> pathlib.Path:
    override = os.environ.get("PORTFOLIO_GURU_FUNNEL_LOG_PATH")
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".openclaw" / "data" / "portfolio-guru" / "funnel-events.ndjson"


def safe_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the allowlisted metadata subset safe for durable logging."""
    if not isinstance(metadata, dict):
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if key in _SAFE_METADATA_KEYS and value is not None
    }


def log_event(
    *,
    user_id: Optional[int],
    username: Optional[str],
    event: str,
    metadata: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    log_path: Optional[pathlib.Path] = None,
) -> Optional[Dict[str, Any]]:
    """Append one PHI-free funnel event. Returns the record or None on I/O error."""
    record: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "username": str(username) if username else None,
        "synthetic": is_synthetic_user(user_id),
        "event": str(event),
        "metadata": safe_metadata(metadata),
        "session_id": str(session_id) if session_id else None,
        "version": 1,
    }
    path = log_path or default_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        logger.warning("Funnel event log append failed", exc_info=True)
        return None
    return record


def iter_records(log_path: Optional[pathlib.Path] = None) -> Iterator[Dict[str, Any]]:
    path = log_path or default_log_path()
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def summarise(
    records: Iterable[Dict[str, Any]],
    *,
    include_synthetic: bool = False,
) -> Dict[str, Any]:
    """Summarise real-user funnel progress and repeat value."""
    events_by_user: dict[Any, Counter[str]] = defaultdict(Counter)
    events_by_name: Counter[str] = Counter()
    unique_by_event: dict[str, set[Any]] = defaultdict(set)
    synthetic_count = 0
    total = 0

    for record in records:
        if record.get("synthetic"):
            synthetic_count += 1
            if not include_synthetic:
                continue
        event = str(record.get("event") or "unknown")
        user_id = record.get("user_id")
        total += 1
        events_by_name[event] += 1
        if user_id is not None:
            events_by_user[user_id][event] += 1
            unique_by_event[event].add(user_id)

    saved_users = {
        user_id
        for user_id, counts in events_by_user.items()
        if counts.get("draft_saved", 0) > 0
    }
    preview_users = {
        user_id
        for user_id, counts in events_by_user.items()
        if counts.get("draft_previewed", 0) > 0
    }
    completed_users = saved_users & preview_users
    repeat_users = {
        user_id
        for user_id, counts in events_by_user.items()
        if counts.get("draft_saved", 0) >= 2
    }

    key_counts = {
        event: {
            "events": events_by_name.get(event, 0),
            "users": len(unique_by_event.get(event, set())),
        }
        for event in _KEY_EVENTS
    }
    key_counts["form_picked"] = {
        "events": events_by_name.get("form_chosen", 0) + events_by_name.get("best_fit_chosen", 0),
        "users": len(unique_by_event.get("form_chosen", set()) | unique_by_event.get("best_fit_chosen", set())),
    }

    return {
        "total": total,
        "unique_users": len(events_by_user),
        "completed_users": len(completed_users),
        "completed_saves": events_by_name.get("draft_saved", 0),
        "repeat_users": len(repeat_users),
        "key_counts": key_counts,
        "events_by_name": dict(events_by_name),
        "synthetic_excluded": synthetic_count if not include_synthetic else 0,
        "synthetic_total": synthetic_count,
    }


def format_admin_report(summary: Dict[str, Any]) -> str:
    total = summary["total"]
    if total == 0:
        synthetic = summary["synthetic_excluded"] or summary["synthetic_total"]
        suffix = (
            f"\n(Excluded {synthetic} synthetic test event{'s' if synthetic != 1 else ''}.)"
            if synthetic
            else ""
        )
        return "📈 Telegram funnel\n\nNo real-user funnel events on record yet." + suffix

    counts = summary["key_counts"]
    lines = [
        "📈 Telegram funnel (real users)",
        "",
        f"Events: {total}  |  Users seen: {summary['unique_users']}",
        f"Completed preview → Kaizen draft: {summary['completed_users']} user{'s' if summary['completed_users'] != 1 else ''} / {summary['completed_saves']} save event{'s' if summary['completed_saves'] != 1 else ''}",
        f"Repeat users with 2+ saved drafts: {summary['repeat_users']}",
        "",
        "Core funnel:",
        f"  • Case started: {counts['case_started']['users']} users / {counts['case_started']['events']} events",
        f"  • Recommendation shown: {counts['recommendation_shown']['users']} users / {counts['recommendation_shown']['events']} events",
        f"  • Form picked: {counts['form_picked']['users']} users / {counts['form_picked']['events']} events",
        f"  • Preview shown: {counts['draft_previewed']['users']} users / {counts['draft_previewed']['events']} events",
        f"  • Save attempted: {counts['save_attempted']['users']} users / {counts['save_attempted']['events']} events",
        f"  • Draft saved: {counts['draft_saved']['users']} users / {counts['draft_saved']['events']} events",
        f"  • Filing failed: {counts['filing_failed']['users']} users / {counts['filing_failed']['events']} events",
    ]
    if summary["synthetic_excluded"]:
        lines.extend(
            [
                "",
                f"(Excluded {summary['synthetic_excluded']} synthetic test event{'s' if summary['synthetic_excluded'] != 1 else ''}.)",
            ]
        )
    return "\n".join(lines)


def build_report(
    *,
    log_path: Optional[pathlib.Path] = None,
    include_synthetic: bool = False,
) -> str:
    summary = summarise(iter_records(log_path), include_synthetic=include_synthetic)
    return format_admin_report(summary)


__all__ = [
    "build_report",
    "default_log_path",
    "format_admin_report",
    "iter_records",
    "log_event",
    "safe_metadata",
    "summarise",
]
