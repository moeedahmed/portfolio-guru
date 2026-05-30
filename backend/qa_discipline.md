# Gap-to-Fix Discipline

This is the improvement loop that sits on top of the post-filing QA pass
documented in `docs/post-filing-quality-assessment.md`. QA tells you what
broke on the most recent filing; the Gap-to-Fix discipline turns each gap
into a targeted, verified, permanent fix instead of a cycle of ad-hoc
patches that drift.

## Cycle

1. QA checks run after every filing → produces `filing_qa` with `gaps`.
2. Each gap that meets the "fixable" criteria → recorded via
   `qa_fix_script.record_gap()` into the in-process gap log.
3. One fix is applied at a time — targeted at the specific DOM id,
   selector, or schema entry called out by the gap.
4. The next filing of the same form type → QA verifies the fix.
5. If the gap is gone → `qa_fix_script.mark_fixed(gap)`.
   If the gap persists → leave it open and reopen the investigation.

## Rules

- **One fix per gap.** No scope creep. Don't refactor the surrounding
  area while you're in there.
- **Fix only the specific field/selector that was wrong.** Adjacent
  fields that happen to be in the same map block stay untouched.
- **Re-file the same case type after the fix.** QA must show the gap
  closed before the work is considered done.
- **If the fix touches the extractor prompt, verify with a similar
  case.** Prompt edits ripple — confirm a second case still extracts
  cleanly.
- **No cosmetic changes during a gap fix.** Text reformatting, unused
  imports, lint-only edits, or test reorganisation go in a separate
  change. Mixing them hides the real fix from `git log`/blame.

## What counts as a "fixable" gap

Fixable gaps are the subset of QA gaps that map to a deterministic code
change in `FORM_FIELD_MAP` (or one of the `*_VALUES` tables):

- **Dropdowns** that were drafted but did not persist — usually a missing
  Angular value mapping or wrong DOM id.
- **Checkboxes** that should have defaulted (e.g. procedural skills
  rows defaulting to "n/a") but came up empty.
- **KC checkboxes** (`kc_checkbox`) that were requested but failed to
  tick — selector or SLO-expansion issue.
- **Missing DOM elements** (`reason == "dom_element_missing"`) on any
  control kind — the map is pointing at a stale UUID.

Free-text fields and textareas where the extractor genuinely produced no
content are not gaps — they belong in `empty_acceptable` (nothing to fix
in the filer; the upstream issue, if any, is in extraction).

## When NOT to fix

- **The gap is a one-off data issue.** The case text had no date, so no
  date was filled. Recording or fixing this trains noise.
- **The gap is a Kaizen SPA rendering issue that affects <5 % of
  filings.** Flaky autosave race, intermittent rerender — file under
  "watch", not "fix".
- **The fix would require rebuilding an architectural component.** A
  single gap is not a mandate to redesign the filer. Open a dedicated
  ticket instead.

## How the discipline is wired in code

- `qa_fix_script.record_gap()` is called from
  `kaizen_form_filer._verify_filing_qa` for every fixable gap (the
  dropdown / checkbox / KC / missing-DOM cases above).
- `qa_fix_script.pending_fixes()` returns the open gap log so a
  reviewer or follow-up filing can iterate through them.
- `qa_fix_script.mark_fixed(gap)` is called by the human (or a
  follow-up tool) once the verifying filing has come back clean.

The gap log is in-process and intentionally not persisted — it reflects
"what surfaced during this session". Durable backlog lives in the
project tracker, fed from `pending_fixes()` when convenient.
