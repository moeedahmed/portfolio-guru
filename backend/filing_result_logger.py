"""
Filing Result Logger
====================

Appends one NDJSON record per Kaizen filing attempt so the autonomous
gap-fix loop (auto_fix_form_map.py) can consume the latest QA report
without parsing bot state or Telegram messages.

Called from kaizen_form_filer._verify_filing_qa after every QA pass.
Safe to call even if the QA pass raised — callers pass status="failed"
and omit the qa_result.

File location: backend/logs/filing_results.ndjson
Rotated implicitly: each record is self-contained, the fix loop reads
only the most recent unhandled entry.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# One directory up from this module
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "filing_results.ndjson")


def log_filing_result(
    form_type: str,
    status: str,  # "success" | "partial" | "failed"
    qa_result: Optional[Dict[str, Any]] = None,
    fixable_gaps: Optional[List[Dict[str, Any]]] = None,
    *,
    error_hint: Optional[str] = None,
) -> None:
    """Append one NDJSON record to the filing log.

    Args:
        form_type: The form type that was filed (e.g. "CBD", "DOPS").
        status: Filing outcome — "success", "partial", or "failed".
        qa_result: The full QA result dict from _verify_filing_qa.
            Can be None if the QA pass itself failed.
        fixable_gaps: Subset of gaps that passed is_fixable_gap().
            If omitted, extracted from qa_result.
        error_hint: Short free-text hint for failed filings (e.g.
            "browser timeout", "element not found"). Not used for fixes.
    """
    qa = qa_result or {}
    score = qa.get("score", {})
    gaps_raw = qa.get("gaps", [])
    counted_fixable = fixable_gaps or [
        g for g in gaps_raw
        if g.get("kind") in {"dropdown", "checkbox", "kc_checkbox"}
        or g.get("missing_dom")
    ]

    record: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "form_type": form_type.upper(),
        "status": status,
        "qa_band": score.get("band"),
        "filled_pct": score.get("filled_pct"),
        "drafted_pct": score.get("drafted_pct"),
        "gap_count": len(gaps_raw),
        "fixable_gap_count": len(counted_fixable),
        "gaps": counted_fixable,
        "has_fixable_gap": len(counted_fixable) > 0,
        "fix_applied": None,  # filled by auto_fix_form_map after a fix
        "fix_skipped_reason": None,  # filled if fix was attempted but skipped
    }

    if error_hint:
        record["error_hint"] = error_hint

    os.makedirs(_LOG_DIR, exist_ok=True)
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def latest_unhandled() -> Optional[Dict[str, Any]]:
    """Return the most recent NDJSON record that has no fix_applied marker.

    Returns None if the log is empty or all records have been handled.
    """
    if not os.path.isfile(_LOG_PATH):
        return None

    latest = None
    with open(_LOG_PATH) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if record.get("fix_applied") is None:
                latest = record

    return latest


def mark_fixed(form_type: str, fix_description: str) -> None:
    """Update the latest unhandled record with a fix_applied marker.

    Rewrites the entire log file to update the record in-place.
    This is intentionally a full rewrite — the log stays small (one
    record per filing, well under 1 MB at thousands of entries).
    """
    if not os.path.isfile(_LOG_PATH):
        return

    ts = datetime.now(timezone.utc).isoformat()
    lines: List[str] = []
    found = False

    with open(_LOG_PATH) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            # Mark the latest unhandled matching this form_type
            if (
                not found
                and record.get("fix_applied") is None
                and record.get("form_type", "").upper() == form_type.upper()
            ):
                record["fix_applied"] = fix_description
                record["fixed_at"] = ts
                found = True
            lines.append(json.dumps(record))

    if found:
        with open(_LOG_PATH, "w") as f:
            for line in lines:
                f.write(line + "\n")


def mark_skipped(reason: str) -> None:
    """Mark the latest unhandled record as skipped (not fixable, or fix
    attempted and failed)."""
    if not os.path.isfile(_LOG_PATH):
        return

    ts = datetime.now(timezone.utc).isoformat()
    lines: List[str] = []
    found = False

    with open(_LOG_PATH) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if not found and record.get("fix_applied") is None:
                record["fix_skipped_reason"] = reason
                record["skipped_at"] = ts
                found = True
            lines.append(json.dumps(record))

    if found:
        with open(_LOG_PATH, "w") as f:
            for line in lines:
                f.write(line + "\n")


def fix_count_today() -> int:
    """Return how many fixes have been applied today."""
    if not os.path.isfile(_LOG_PATH):
        return 0

    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    with open(_LOG_PATH) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            fixed_at = record.get("fixed_at", "")
            if fixed_at and fixed_at.startswith(today_prefix):
                count += 1
    return count


__all__ = [
    "log_filing_result",
    "latest_unhandled",
    "mark_fixed",
    "mark_skipped",
    "fix_count_today",
]
