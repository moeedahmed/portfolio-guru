"""
Gap-to-Fix Helpers
==================

In-process gap log used by the Gap-to-Fix discipline (see
`backend/qa_discipline.md`). The post-filing QA pass in
`kaizen_form_filer._verify_filing_qa` calls `record_gap()` for every gap
that is deterministically fixable (dropdowns, checkboxes, KC checkboxes,
missing DOM elements). A reviewer — or a follow-up filing of the same
form type — calls `mark_fixed()` once the verifying filing comes back
clean.

The log is intentionally in-memory: it reflects "gaps that surfaced this
session", not a durable backlog. Use `pending_fixes()` to drain it into
your tracker of choice.

Typical loop:

    1. File a case. QA emits gaps. record_gap() captures the fixable ones.
    2. Inspect `pending_fixes()` and apply ONE fix (one DOM id / one
       FORM_FIELD_MAP entry / one *_VALUES table).
    3. Re-file the same case type. QA reruns.
    4. If the gap is gone in the new QA result, call mark_fixed() with
       the original gap dict.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


FIXABLE_GAP_KINDS = {"dropdown", "checkbox", "kc_checkbox"}
FIXABLE_GAP_REASONS = {"dom_element_missing", "value_not_persisted", "kc_not_ticked"}


GAP_LOG: List[Dict[str, Any]] = []


def is_fixable_gap(gap: Dict[str, Any]) -> bool:
    """A gap is fixable if it maps to a deterministic FORM_FIELD_MAP change.

    Free-text/textarea gaps are excluded: those usually mean the extractor
    produced nothing, which is an upstream issue rather than a filer fix.
    """
    if gap.get("missing_dom"):
        return True
    return (
        gap.get("kind") in FIXABLE_GAP_KINDS
        and gap.get("reason") in FIXABLE_GAP_REASONS
    )


def record_gap(
    form_type: str,
    field_key: str,
    gap_kind: str,
    reason: str,
    *,
    dom_id: Optional[str] = None,
    discovery_url: Optional[str] = None,
    expected_preview: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a fixable gap to the in-process log and return the entry."""
    entry = {
        "form_type": form_type,
        "field_key": field_key,
        "gap_kind": gap_kind,
        "reason": reason,
        "dom_id": dom_id,
        "discovery_url": discovery_url,
        "expected_preview": expected_preview,
        "recorded_at": datetime.now().isoformat(),
        "fix_applied": None,
        "fixed_at": None,
    }
    GAP_LOG.append(entry)
    return entry


def mark_fixed(gap: Dict[str, Any], fix_applied: Optional[str] = None) -> None:
    """Mark a previously recorded gap as fixed.

    The verifying filing must show the gap cleared before this is called.
    """
    gap["fix_applied"] = fix_applied
    gap["fixed_at"] = datetime.now().isoformat()


def pending_fixes() -> List[Dict[str, Any]]:
    return [g for g in GAP_LOG if g["fixed_at"] is None]


def reset_gap_log() -> None:
    GAP_LOG.clear()


__all__ = [
    "FIXABLE_GAP_KINDS",
    "FIXABLE_GAP_REASONS",
    "GAP_LOG",
    "is_fixable_gap",
    "record_gap",
    "mark_fixed",
    "pending_fixes",
    "reset_gap_log",
]
