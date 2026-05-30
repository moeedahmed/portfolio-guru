# Post-Filing Quality Assessment — Runbook

This is the operational discipline that runs after every Kaizen filing. It compares the drafted case (what the bot intended to file) against what actually landed on the form, scores the result, and produces a list of DOM mapping tasks for every gap.

The companion module is `backend/post_filing_qa.py` (pure helpers). The hook lives in `kaizen_form_filer._verify_filing_qa` and is called from `fill_kaizen_form` immediately after a successful draft save.

For the why — i.e. the concept behind this discipline — see the module docstring at the top of `backend/post_filing_qa.py`.

## How it runs (automatic)

1. The filer saves the draft and the CDP browser ends up on `/events/fillin/{doc_id}?autosave=...`.
2. `_verify_filing_qa(page, form_type, fields, field_map)` reads every mapped DOM element through `page.evaluate`.
3. Each field is bucketed:
   - `filled` — DOM has content (text non-empty, `selectedIndex > 0`, checkbox `checked`).
   - `empty_expected` — DOM empty but the caller drafted a value. These are the gaps.
   - `empty_acceptable` — DOM empty and nothing was drafted. Not a gap.
4. KCs from `key_capabilities` / `curriculum_links` are probed by visible label and bucketed with a `kc:` prefix.
5. `score_qa_buckets` derives a band: GREEN (≥90 % drafted-fields-filled), AMBER (70–89 %), RED (<70 %).
6. The result is returned on the filing result dict as `filing_qa` and never blocks the user — QA exceptions are caught and logged.

## How to read the QA result

```python
{
  "filled": ["reflection", "clinical_reasoning", "kc:SLO1 KC1"],
  "empty_expected": ["procedure_name"],     # gaps
  "empty_acceptable": ["optional_other"],
  "counts": {
    "filled": 3,
    "drafted": 4,                            # filled + empty_but_drafted
    "empty_but_drafted": 1,
    "empty_not_drafted": 1,
  },
  "score": {
    "band": "AMBER",
    "filled_pct": 75,                        # filled / drafted
    "drafted_pct": 80,                       # drafted / total
    "filled_n": 3,
    "drafted_n": 4,
    "empty_but_drafted_n": 1,
    "empty_not_drafted_n": 1,
  },
  "gaps": [
    {
      "field": "procedure_name",
      "dom_id": "8def931e-3a00-43ac-8529-44cdaf34be2d",
      "form_type": "DOPS",
      "kind": "dropdown",
      "missing_dom": false,
      "expected_preview": "Lumbar puncture",
      "reason": "value_not_persisted",
    }
  ],
}
```

Quick read order:

1. **Band** in `score.band`: GREEN ≈ ship it; AMBER ≈ inspect; RED ≈ stop and investigate.
2. **Gaps**: every entry is one DOM mapping task. `reason` says what kind of fix it needs (see below).
3. **`empty_not_drafted`** is signal too — fields the bot consistently leaves blank may indicate the extractor or schema is missing them upstream.

## Running a QA check manually

The runtime QA pass requires a live CDP session, so it cannot be replayed offline. To re-run against an existing saved draft:

```bash
# 1. Make sure the managed Chrome is running on port 18800 and logged into Kaizen.
# 2. From the backend venv:
cd /Users/moeedahmed/projects/portfolio-guru/backend
source venv/bin/activate

# 3. Drive the QA pass against a saved draft URL with a Python one-liner:
python - <<'PY'
import asyncio
from kaizen_form_filer import (
    _connect_cdp, _verify_filing_qa, FORM_FIELD_MAP, COMMON_HEADER_FIELD_MAP,
    canonical_form_type,
)

async def run():
    form_type = canonical_form_type("CBD")          # set per case
    saved_url = "https://kaizenep.com/events/fillin/<doc_id>?autosave=<autosave_id>"
    expected = {                                    # the drafted fields you want to check
        "date_of_encounter": "20/3/2026",
        "stage_of_training": "Higher",
        "clinical_reasoning": "…",
        "reflection": "…",
    }
    page, pw = await _connect_cdp()
    try:
        await page.goto(saved_url, wait_until="networkidle", timeout=30000)
        field_map = {**COMMON_HEADER_FIELD_MAP, **FORM_FIELD_MAP[form_type]}
        qa = await _verify_filing_qa(page, form_type, expected, field_map)
        print(qa["score"])
        for gap in qa["gaps"]:
            print("GAP:", gap)
    finally:
        await pw.stop()

asyncio.run(run())
PY
```

For ad-hoc summaries of a stored result, use `post_filing_qa.format_qa_summary(qa)`.

## Turning a gap into a DOM mapping fix

The improvement cycle:

```
gap  →  inspect CDP  →  fix FORM_FIELD_MAP  →  retest
```

1. **Read the gap.** Each gap carries `form_type`, `field`, `dom_id`, `kind`, and a `reason`:
   - `dom_element_missing` — `getElementById(dom_id)` returned nothing. The map is pointing at the wrong UUID (or Kaizen renamed it).
   - `value_not_persisted` — element exists but stayed empty. Selector strategy or fill technique is wrong for this control kind.
   - `kc_not_ticked` — the KC label could not be matched, or the SLO failed to expand.
2. **Inspect in the CDP browser.** Open the saved draft URL in the managed Chrome. Use DevTools to find the real `id`/`ng-model` for the field. For dropdowns, capture the Angular `value` string (e.g. `string:<uuid>`).
3. **Fix `FORM_FIELD_MAP[form_type]`** in `backend/kaizen_form_filer.py`. If it's a dropdown that needs an Angular value, also update the corresponding `*_VALUES` map (`STAGE_SELECT_VALUES`, `QIAT_STAGE_VALUES`, etc.) or add a new one if it's a new dropdown class.
4. **Retest** by re-running the filing flow against a fresh draft. The next QA pass should reclassify the field from `empty_expected` to `filled`.

`post_filing_qa.gaps_to_dom_fix_tasks(qa)` converts the gap list into one-line action items suitable for a TODO list or backlog ticket.

## Discipline rules

- **Never widen `empty_acceptable` to hide a gap.** If a field is empty because the schema does not require it, the right fix is upstream (extractor or schema), not the QA bucket.
- **Never substring-match drafted text against DOM text.** Kaizen reformats text on save; substring matching produces false gaps and trains the team to ignore the report.
- **Treat AMBER and RED as backlog items, not console noise.** Each gap should resolve to either a DOM mapping fix, an extractor fix, or a documented exception in `SCHEMA_REQUIRED_FIELD_HANDLING`.
- **Never fabricate fills to chase GREEN.** Per the project's no-fabrication rule, fields the user did not provide stay blank; they belong in `empty_not_drafted`, not `filled`.
