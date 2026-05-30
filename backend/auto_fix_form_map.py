"""
Auto Fix Form Map
=================

Autonomous gap-fix module for the Portfolio Guru self-improvement loop.
Reads the latest unhandled filing result from filing_result_logger and
applies the lightest fix to FORM_FIELD_MAP / *_VALUES tables.

Safety bounds (self-enforced, never bypassed):
- Only touches FORM_FIELD_MAP and *_VALUES tables in kaizen_form_filer.py
- Never modifies extractor prompts, flow logic, conversation handlers, or schemas
- Max 3 fixes per run (then gives up and logs for human review)
- Only fixes DOM/gap reasons: dom_element_missing, value_not_persisted,
  kc_not_ticked for dropdown, checkbox, and kc_checkbox kinds
- Never adds new form types — only patches existing mappings
- Writes a fix marker to the NDJSON log after applying

Usage (cron):
    cd backend && source venv/bin/activate
    python3 -m auto_fix_form_map --dry-run   # preview without changing
    python3 -m auto_fix_form_map              # apply if a gap exists
"""

from __future__ import annotations

import ast
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from filing_result_logger import (
    latest_unhandled,
    mark_fixed,
    mark_skipped,
    fix_count_today,
)

# ── Safety bounds ────────────────────────────────────────────────────────────

MAX_FIXES_PER_RUN = 3
MAX_FIXES_PER_DAY = 10  # emergency ceiling — if exceeded, refuse all fixes

# Patterns for identifying fix targets in kaizen_form_filer.py
_FIELD_MAP_PATTERN = re.compile(
    r'FORM_FIELD_MAP\["([A-Z_]+)"\]\s*=\s*\{'
)
_VALUES_MAP_PATTERN = re.compile(
    r'(STAGE_SELECT_VALUES|QIAT_STAGE_VALUES|COLLECTION_METHOD_VALUES|'
    r'SUPERVISOR_ROLE_VALUES|PROCEDURAL_SKILL_DEFAULTS'
    r')\s*=\s*\{'
)


# ── Classification ───────────────────────────────────────────────────────────

def _fix_description(gap: Dict[str, Any]) -> str:
    """Short human-readable description of the fix needed."""
    kind = gap.get("gap_kind") or gap.get("kind", "?")
    reason = gap.get("reason", "?")
    field = gap.get("field_key") or gap.get("field", "?")
    dom_id = gap.get("dom_id", "?")
    return f"{field}:{kind}/{reason} (dom_id={dom_id[:12]})"


def _is_auto_fixable(gap: Dict[str, Any]) -> bool:
    """A gap is auto-fixable only if it maps to a deterministic
    FORM_FIELD_MAP change that does not require human judgement."""
    kind = gap.get("gap_kind") or gap.get("kind", "")
    reason = gap.get("reason", "")
    # Missing DOM element — map has wrong UUID, needs replacing
    if gap.get("missing_dom") or reason == "dom_element_missing":
        return True
    # Dropdown value not persisting — missing entry in *_VALUES table
    if kind in ("dropdown",) and reason == "value_not_persisted":
        return True
    # Checkbox not ticked — selector or default logic issue
    if kind in ("checkbox", "kc_checkbox") and reason in (
        "value_not_persisted", "kc_not_ticked"
    ):
        return True
    return False


# ── Fix application ──────────────────────────────────────────────────────────

def _read_form_field_map(file_path: str) -> Optional[str]:
    """Read kaizen_form_filer.py into a string. Returns None on failure."""
    try:
        with open(file_path) as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError) as e:
        print(f"ERROR: cannot read {file_path}: {e}", file=sys.stderr)
        return None


def _write_form_field_map(file_path: str, content: str) -> bool:
    """Write updated content back to kaizen_form_filer.py. Returns True on
    success, False on failure."""
    try:
        with open(file_path, "w") as f:
            f.write(content)
        return True
    except (PermissionError, OSError) as e:
        print(f"ERROR: cannot write {file_path}: {e}", file=sys.stderr)
        return False


def _is_valid_python_before(file_path: str, content: str) -> Tuple[bool, str]:
    """Check that the modified file parses as valid Python. Returns
    (is_valid, error_message)."""
    try:
        ast.parse(content, filename=file_path)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"


def _fix_missing_dom_id(
    content: str,
    gap: Dict[str, Any],
    *,
    dry_run: bool = False,
) -> Tuple[bool, Optional[str]]:
    """Fix a missing DOM element by scanning the rendered HTML for the
    correct id.

    This is the hardest case to auto-fix — we can't know the correct DOM id
    without inspecting the live Kaizen page. For now, mark as skipped so the
    human can investigate. Future improvement: use the CDP browser to discover
    the actual DOM id from the Kaizen form page.
    """
    return False, "dom_element_missing requires live CDP inspection — auto-fix skipped"


def _log_and_apply_fix(
    gap: Dict[str, Any],
    form_type: str,
    dry_run: bool,
    *,
    fix_needed: bool = True,
    fix_description: str = "",
) -> bool:
    """Common logging for fix application. Returns True if applied/simulated,
    False if skipped."""
    ftype = form_type.upper()
    desc = fix_description or _fix_description(gap)

    if not fix_needed:
        if dry_run:
            print(f"  ⏭️  {ftype}: {desc} — no fix needed")
        return False

    if dry_run:
        print(f"  🎯 {ftype}: {desc}")
        return True

    print(f"  🔧 {ftype}: {desc}")
    return True


def _apply_form_field_map_fix(
    content: str,
    gap: Dict[str, Any],
    form_type: str,
    *,
    dry_run: bool = False,
    file_path: str = "",
) -> Tuple[bool, Optional[str]]:
    """Apply a fix to the FORM_FIELD_MAP in kaizen_form_filer.py.

    The specific fix depends on the gap kind and reason:

    - dom_element_missing / missing_dom: The DOM id configured in the map
      does not match what Kaizen rendered. We cannot auto-fix without
      inspecting the live page — skip to human review. Future: drive CDP
      to discover the correct DOM id dynamically.
    - dropdown value_not_persisted: The dropdown option exists in the DOM
      but the Angular value string is wrong or missing from *_VALUES tables.
      This requires inspecting Kaizen's rendered HTML to find the correct
      value string — skip to human review.
    - checkbox/kc_checkbox: May be a selector or default-ticks issue.
      Usually needs live inspection.

    For now, all auto-fixable gaps are logged but skipped to human review
    because the correct replacement value is only knowable by inspecting
    the live Kaizen page. Future improvement: wire CDP discovery.
    """
    # All current gap types need live Kaizen inspection to determine the right
    # fix value. We log them for visibility and let the human fix cycle handle
    # them (the filing already surfaces the gap in the bot's conversation).
    return False, "requires live Kaizen inspection — auto-fix not yet implemented"


# ── Main entry point ─────────────────────────────────────────────────────────

def run_auto_fix(*, dry_run: bool = False) -> int:
    """Main fix loop. Returns 0 if done/skipped, 1 if error.

    Only applies fixes that are deterministic and safe. Currently all
    auto-fixable gaps require live Kaizen page inspection to determine
    the correct DOM id or Angular value string — we log them, skip them,
    and let the human fix cycle (or future CDP-backed discovery) handle
    them.

    Future: wire CDP-backed DOM discovery in a separate safety-gated module.
    """
    import os

    repo_root = os.path.dirname(os.path.abspath(__file__))
    kaizen_file = os.path.join(repo_root, "kaizen_form_filer.py")

    if not os.path.isfile(kaizen_file):
        print(f"ERROR: kaizen_form_filer.py not found at {kaizen_file}", file=sys.stderr)
        return 1

    # Emergency ceiling check
    today_fix_count = fix_count_today()
    if today_fix_count >= MAX_FIXES_PER_DAY:
        print(f"SAFETY: {today_fix_count} fixes applied today (max {MAX_FIXES_PER_DAY}) — refusing.", file=sys.stderr)
        return 0

    # Read latest unhandled filing
    record = latest_unhandled()
    if record is None:
        print("No unhandled filing results. Nothing to fix.")
        return 0

    if not record.get("has_fixable_gap"):
        print(f"No fixable gaps in latest filing ({record.get('form_type')}, "
              f"status={record.get('status')}, band={record.get('qa_band')}).")
        return 0

    form_type = record.get("form_type", "UNKNOWN")
    gaps: List[Dict[str, Any]] = record.get("gaps", [])

    # Filter to auto-fixable gaps
    fixable = [g for g in gaps if _is_auto_fixable(g)]
    if not fixable:
        print(f"Gaps exist but none are auto-fixable ({form_type}).")
        mark_skipped("gaps_not_auto_fixable")
        return 0

    print(f"Found {len(fixable)} auto-fixable gap(s) for {form_type}:")
    for g in fixable:
        print(f"  - {_fix_description(g)}")

    # Cap to max per run
    to_fix = fixable[:MAX_FIXES_PER_RUN]
    skipped_rest = len(fixable) - len(to_fix)

    fixes_applied = 0
    for gap in to_fix:
        if dry_run:
            _log_and_apply_fix(gap, form_type, dry_run=True)
            continue

        success, reason = _apply_form_field_map_fix(
            "", gap, form_type, file_path=kaizen_file
        )
        if success:
            fixes_applied += 1

    if dry_run:
        print(f"\nDry-run complete. {len(to_fix)} gap(s) would be examined.")
        if skipped_rest:
            print(f"({skipped_rest} more gap(s) beyond the per-run cap.)")
        return 0

    # Mark the record
    if fixes_applied > 0:
        mark_fixed(form_type, f"auto_fixed:{fixes_applied}_gaps")
        print(f"\nApplied {fixes_applied} fix(es). Record marked as fixed.")
    else:
        mark_skipped("auto_fix_not_applied")
        print(f"\nNo fixes were applied (gaps require live Kaizen inspection). "
              f"Record marked as skipped.")

    if skipped_rest:
        print(f"({skipped_rest} gap(s) beyond the per-run cap remain in the log.)")

    return 0


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    sys.exit(run_auto_fix(dry_run=dry_run))


if __name__ == "__main__":
    main()

__all__ = ["run_auto_fix"]
