"""
Post-Filing Quality Assessment Discipline
=========================================

After every Kaizen filing, the bot inspects the saved form in the CDP browser
and compares what was drafted against what actually landed on the form. The
output is a structured QA report that feeds two loops:

1. Per-filing telemetry: bands (GREEN / AMBER / RED) make it obvious when a
   filing landed short of what the user dictated.
2. DOM mapping improvement queue: every gap carries the form_type, field key
   and DOM id so it can be turned directly into a `FORM_FIELD_MAP` fix.

The runtime hook lives in `kaizen_form_filer._verify_filing_qa` — this module
holds the pure helpers (scoring, formatting, gap classification) so they can
be unit-tested without a browser and reused by reporting tools.

The full discipline is documented in `backend/docs/post-filing-quality-assessment.md`.

How the QA pass works at runtime
--------------------------------

1. The CDP browser is already logged in and parked on the saved draft URL
   (`/events/fillin/{doc_id}?autosave=...`).
2. For each entry in `FORM_FIELD_MAP[form_type]`, Playwright `page.evaluate`
   reads the DOM element's value / selectedIndex / checked state.
3. Each field is bucketed into one of three lists:
     - `filled`              — DOM has content.
     - `empty_expected`      — DOM is empty BUT the caller passed a value
                                (this is a gap that needs fixing).
     - `empty_acceptable`    — DOM is empty and nothing was drafted for it.
4. Curriculum KCs from `expected_fields["key_capabilities"]` (or
   `curriculum_links`) are probed by visible label and reported with a
   `kc:<target>` prefix in the same buckets.
5. Counts and a quality band are computed from the buckets, and per-gap
   metadata is emitted so each gap can become an actionable DOM mapping task.

Field comparison rules
----------------------

- Text / textarea: filled iff the DOM `value`/`textContent` is non-empty.
  Substring match against the drafted text is intentionally NOT used —
  Kaizen reformats text on save and a strict match would create false gaps.
- Dates: treated as text; "filled iff non-empty". The deterministic filer
  has already normalised dates to `d/m/yyyy` before save.
- Dropdowns: filled iff `selectedIndex > 0` (anything other than the default
  placeholder option).
- Checkboxes / KCs: filled iff `checked === true`.
- Missing DOM elements: bucketed as `empty_expected` if the field was
  drafted, and logged as a WARNING with the configured DOM id so the gap
  surfaces in the DOM mapping queue.

Quality bands
-------------

The band is computed from the ratio of drafted fields that ended up filled:

- GREEN: ≥ 90 % of drafted fields landed.
- AMBER: 70 – 89 %.
- RED:   < 70 %.

If no fields were drafted (degenerate case — usually a filing failure
upstream), the band falls back to RED so it cannot be silently green.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


# Quality band thresholds, expressed as the inclusive lower bound for the
# fraction of drafted fields that successfully landed on the form.
GREEN_THRESHOLD = 0.90
AMBER_THRESHOLD = 0.70

QUALITY_BANDS = ("GREEN", "AMBER", "RED")


def score_qa_buckets(
    filled: Iterable[str],
    empty_expected: Iterable[str],
    empty_acceptable: Iterable[str],
) -> Dict[str, Any]:
    """Compute a quality band + percentages from the three QA buckets.

    `filled`, `empty_expected`, `empty_acceptable` are the lists produced by
    `_verify_filing_qa`. `empty_expected` is the count of drafted fields that
    did NOT land on the form — these are the actionable gaps.
    """
    filled_n = sum(1 for _ in filled)
    drafted_short_n = sum(1 for _ in empty_expected)
    optional_empty_n = sum(1 for _ in empty_acceptable)

    drafted_n = filled_n + drafted_short_n
    total_n = drafted_n + optional_empty_n

    if drafted_n == 0:
        filled_pct = 0
        band = "RED"
    else:
        filled_pct = round(100 * filled_n / drafted_n)
        if filled_pct >= GREEN_THRESHOLD * 100:
            band = "GREEN"
        elif filled_pct >= AMBER_THRESHOLD * 100:
            band = "AMBER"
        else:
            band = "RED"

    drafted_pct = round(100 * drafted_n / total_n) if total_n else 0

    return {
        "band": band,
        "filled_pct": filled_pct,
        "drafted_pct": drafted_pct,
        "filled_n": filled_n,
        "drafted_n": drafted_n,
        "empty_but_drafted_n": drafted_short_n,
        "empty_not_drafted_n": optional_empty_n,
    }


def format_qa_summary(qa: Dict[str, Any]) -> str:
    """One-line human-readable summary of a QA result.

    Suitable for logs, Telegram replies, and the post-filing report card.
    """
    if not qa:
        return "QA: not run"
    score = qa.get("score") or {}
    band = score.get("band", "?")
    filled_pct = score.get("filled_pct", 0)
    counts = qa.get("counts") or {}
    filled_n = counts.get("filled", len(qa.get("filled") or []))
    drafted_n = counts.get("drafted", filled_n + len(qa.get("empty_expected") or []))
    gap_n = len(qa.get("gaps") or [])
    pieces = [f"QA {band}", f"{filled_n}/{drafted_n} drafted fields filled ({filled_pct}%)"]
    if gap_n:
        pieces.append(f"{gap_n} gap(s)")
    return " · ".join(pieces)


def gaps_to_dom_fix_tasks(qa: Dict[str, Any]) -> List[Dict[str, str]]:
    """Project gaps into a list of "fix this in FORM_FIELD_MAP" tasks.

    Each task is a small dict suitable for handing to a human reviewer or
    dropping into a backlog. The improvement cycle is:

        gap -> open CDP -> inspect DOM -> fix FORM_FIELD_MAP entry -> retest
    """
    tasks: List[Dict[str, str]] = []
    for gap in qa.get("gaps") or []:
        form_type = gap.get("form_type", "?")
        field = gap.get("field", "?")
        dom_id = gap.get("dom_id") or "(no dom_id)"
        reason = gap.get("reason", "unknown")
        kind = gap.get("kind", "field")
        if reason == "dom_element_missing":
            action = (
                f"Inspect {form_type} form in CDP browser, locate the actual "
                f"{kind} for '{field}', and update FORM_FIELD_MAP['{form_type}']"
                f"['{field}'] (currently {dom_id})."
            )
        elif reason == "kc_not_ticked":
            action = (
                f"KC '{gap.get('expected_preview')}' was requested but did not "
                f"tick on {form_type}. Verify SLO expansion and the KC label "
                f"matcher in TICK_KC_JS."
            )
        else:
            action = (
                f"Field '{field}' ({kind}) on {form_type} accepted the fill "
                f"but the value did not persist. Re-check selector strategy "
                f"for dom_id={dom_id}."
            )
        tasks.append({
            "form_type": form_type,
            "field": field,
            "dom_id": dom_id,
            "reason": reason,
            "kind": kind,
            "action": action,
        })
    return tasks


__all__ = [
    "QUALITY_BANDS",
    "GREEN_THRESHOLD",
    "AMBER_THRESHOLD",
    "score_qa_buckets",
    "format_qa_summary",
    "gaps_to_dom_fix_tasks",
]
