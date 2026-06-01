"""
Filing Attempt Log
==================

Durable NDJSON log of every Kaizen filing attempt from the live bot, plus
helpers to summarise the log into an internal admin report. The goal is to
answer two questions without grepping the bot log:

1. Are real users actually filing? (counts, success rate, recent failures)
2. What is the top failure category? (so we know what to fix next)

Synthetic test traffic (Telegram user id 99999999, plus anything listed in
the optional ``PORTFOLIO_GURU_SYNTHETIC_USER_IDS`` env var as a CSV) is
recorded but excluded from headline counts so beta-period reliability
numbers reflect real doctors, not test fixtures.

PHI-free: only the form type, status, classified error category, and the
filer's own (already generic) error string are stored. No case content,
no credentials, no decrypted values.

Log path
--------

Default: ``~/.openclaw/data/portfolio-guru/filing-log.ndjson``.
Override with ``PORTFOLIO_GURU_FILING_LOG_PATH`` (used by tests).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TEST_USER_IDS = frozenset({99999999})


def _synthetic_user_ids() -> frozenset[int]:
    raw = os.environ.get("PORTFOLIO_GURU_SYNTHETIC_USER_IDS", "")
    extras: List[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            extras.append(int(chunk))
        except ValueError:
            continue
    return _DEFAULT_TEST_USER_IDS | frozenset(extras)


def is_synthetic_user(user_id: Optional[int]) -> bool:
    """Whether ``user_id`` should be excluded from real-reliability counts."""
    if user_id is None:
        return False
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    return uid in _synthetic_user_ids()


def default_log_path() -> pathlib.Path:
    override = os.environ.get("PORTFOLIO_GURU_FILING_LOG_PATH")
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".openclaw" / "data" / "portfolio-guru" / "filing-log.ndjson"


def categorise_outcome(
    status: str,
    error: Optional[str],
    skipped: Optional[Iterable[Any]] = None,
    filled: Optional[Iterable[Any]] = None,
) -> str:
    """Bucket a filing result into a short, log-friendly category.

    Categories — chosen so the admin report can answer "where do we lose
    users?":

    - SAVE_SUCCESS    — saved with no skipped fields.
    - PARTIAL_SAVE    — saved but at least one field was skipped.
    - SAVE_UNVERIFIED — save click landed but post-save verification failed.
    - SAVE_FAILURE    — fields filled, save click/confirm did not land.
    - LOGIN_FAILED    — Kaizen rejected credentials or session expired.
    - TIMEOUT         — filing exceeded its wall-clock budget.
    - EXCEPTION       — uncaught exception bubbled out of the filer.
    - FILL_FAILURE    — nothing was filled (DOM map mismatch, blocked form).
    - UNKNOWN         — anything we don't yet have a marker for.

    The classifier is intentionally string-based: the filer emits stable
    English markers ("Save button not found", "could not confirm"), so we
    can categorise without coupling to internal exception types.
    """
    status_norm = (status or "").lower()
    err = (error or "").lower()
    skipped_list = list(skipped or [])
    filled_list = list(filled or [])

    if status_norm == "timeout":
        return "TIMEOUT"
    if status_norm == "exception":
        return "EXCEPTION"

    if any(token in err for token in (
        "login failed", "could not log in", "log in to kaizen",
    )):
        return "LOGIN_FAILED"

    save_markers = (
        "save button", "save may have failed", "save was clicked",
        "could not confirm",
    )
    if filled_list and any(marker in err for marker in save_markers):
        return "SAVE_UNVERIFIED" if status_norm == "partial" else "SAVE_FAILURE"

    if status_norm == "success":
        editable_skipped = [s for s in skipped_list if "attachment" not in str(s).lower()]
        return "PARTIAL_SAVE" if editable_skipped else "SAVE_SUCCESS"

    if status_norm == "partial":
        return "PARTIAL_SAVE" if filled_list else "FILL_FAILURE"

    if status_norm == "failed":
        if not filled_list:
            return "FILL_FAILURE"
        return "SAVE_FAILURE"

    return "UNKNOWN"


def log_attempt(
    *,
    user_id: Optional[int],
    username: Optional[str],
    form_type: str,
    status: str,
    error: Optional[str] = None,
    filled: Optional[Iterable[Any]] = None,
    skipped: Optional[Iterable[Any]] = None,
    method: Optional[str] = None,
    verified: Optional[bool] = None,
    log_path: Optional[pathlib.Path] = None,
) -> Optional[Dict[str, Any]]:
    """Append one filing attempt record. Returns the record (or None on I/O error).

    ``filled``/``skipped`` are recorded as counts only — the per-field lists
    are useful for ad-hoc debugging but bloat the log fast. The full lists
    of skipped field keys are kept because they're already PHI-free and tell
    us which DOM mappings need fixing.
    """
    skipped_list = [str(s) for s in (skipped or [])]
    filled_list = [str(s) for s in (filled or [])]
    record: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "username": str(username) if username is not None else None,
        "synthetic": is_synthetic_user(user_id),
        "form_type": form_type,
        "status": status,
        "category": categorise_outcome(status, error, skipped_list, filled_list),
        "error": str(error) if error is not None else None,
        "filled_count": len(filled_list),
        "skipped": skipped_list,
        "method": method,
        "verified": verified,
        "version": 2,
    }

    path = log_path or default_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        logger.warning("Filing-attempt log append failed", exc_info=True)
        return None
    return record


def iter_records(log_path: Optional[pathlib.Path] = None) -> Iterator[Dict[str, Any]]:
    """Yield parsed records from the NDJSON log. Skips malformed lines."""
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
    recent_failure_limit: int = 5,
) -> Dict[str, Any]:
    """Compute reliability counts over the given records.

    ``include_synthetic=False`` (the default) filters out test traffic.
    The synthetic count is still reported separately so the operator knows
    how much fixture noise was suppressed.
    """
    real_records: List[Dict[str, Any]] = []
    synthetic_count = 0
    for record in records:
        if record.get("synthetic"):
            synthetic_count += 1
            if not include_synthetic:
                continue
        real_records.append(record)

    total = len(real_records)
    by_status: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    by_form: Dict[str, int] = {}
    users: set[Any] = set()
    failures: List[Dict[str, Any]] = []

    for record in real_records:
        status = record.get("status", "unknown")
        category = record.get("category") or categorise_outcome(
            status,
            record.get("error"),
            record.get("skipped"),
            [True] * int(record.get("filled_count") or 0),
        )
        form_type = record.get("form_type", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_category[category] = by_category.get(category, 0) + 1
        by_form[form_type] = by_form.get(form_type, 0) + 1
        if record.get("user_id") is not None:
            users.add(record["user_id"])
        if status not in ("success",):
            failures.append(record)

    failures.sort(key=lambda r: r.get("ts", ""), reverse=True)

    successes = by_status.get("success", 0)
    saved = successes + by_status.get("partial", 0)
    return {
        "total": total,
        "successes": successes,
        "partials": by_status.get("partial", 0),
        "failures": total - saved,
        "saved": saved,
        "saved_rate": (saved / total) if total else 0.0,
        "success_rate": (successes / total) if total else 0.0,
        "unique_users": len(users),
        "by_status": by_status,
        "by_category": by_category,
        "by_form": by_form,
        "recent_failures": failures[:recent_failure_limit],
        "synthetic_excluded": synthetic_count if not include_synthetic else 0,
        "synthetic_total": synthetic_count,
    }


def format_admin_report(summary: Dict[str, Any]) -> str:
    """Render a concise, monospace-friendly text report for /filingreport."""
    total = summary["total"]
    if total == 0:
        synthetic = summary["synthetic_excluded"] or summary["synthetic_total"]
        suffix = (
            f"\n(Excluded {synthetic} synthetic test attempt{'s' if synthetic != 1 else ''}.)"
            if synthetic
            else ""
        )
        return "📋 Filing reliability\n\nNo real-user filing attempts on record yet." + suffix

    lines: List[str] = [
        "📋 Filing reliability (real users)",
        "",
        f"Attempts: {total}  |  Unique users: {summary['unique_users']}",
        f"Saved:    {summary['saved']} ({summary['saved_rate']*100:.0f}%)"
        f"  Success: {summary['successes']}  Partial: {summary['partials']}",
        f"Failures: {summary['failures']} ({(1 - summary['saved_rate'])*100:.0f}%)",
    ]

    if summary["by_category"]:
        lines.append("")
        lines.append("Top categories:")
        ordered = sorted(summary["by_category"].items(), key=lambda kv: kv[1], reverse=True)
        for category, count in ordered[:6]:
            lines.append(f"  • {category}: {count}")

    if summary["by_form"]:
        lines.append("")
        lines.append("Top forms:")
        ordered = sorted(summary["by_form"].items(), key=lambda kv: kv[1], reverse=True)
        for form_type, count in ordered[:6]:
            lines.append(f"  • {form_type}: {count}")

    if summary["recent_failures"]:
        lines.append("")
        lines.append("Recent failures:")
        for record in summary["recent_failures"]:
            ts = (record.get("ts") or "")[:19].replace("T", " ")
            form_type = record.get("form_type", "?")
            category = record.get("category") or "?"
            err = (record.get("error") or "").strip().splitlines()[0] if record.get("error") else ""
            if len(err) > 80:
                err = err[:77] + "…"
            user_id = record.get("user_id")
            user_suffix = f"  user={user_id}" if user_id is not None else ""
            err_suffix = f"  — {err}" if err else ""
            lines.append(f"  {ts}  {form_type}  {category}{user_suffix}{err_suffix}")

    if summary["synthetic_excluded"]:
        lines.append("")
        lines.append(
            f"(Excluded {summary['synthetic_excluded']} synthetic test "
            f"attempt{'s' if summary['synthetic_excluded'] != 1 else ''}.)"
        )

    return "\n".join(lines)


def build_report(
    *,
    log_path: Optional[pathlib.Path] = None,
    include_synthetic: bool = False,
    recent_failure_limit: int = 5,
) -> str:
    """Convenience: read the log and render the admin report in one call."""
    summary = summarise(
        iter_records(log_path),
        include_synthetic=include_synthetic,
        recent_failure_limit=recent_failure_limit,
    )
    return format_admin_report(summary)


__all__ = [
    "build_report",
    "categorise_outcome",
    "default_log_path",
    "format_admin_report",
    "is_synthetic_user",
    "iter_records",
    "log_attempt",
    "summarise",
]
