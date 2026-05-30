# Post-Filing Quality Assurance Automation

## Concept

Use the CDP browser (which is already logged into Kaizen) to inspect the form immediately after filing and verify that every field the bot intended to fill was actually filled. This gives us real-time filing quality feedback without manual inspection.

## How it works

After `file_to_kaizen()` saves the draft, the draft URL (`/events/fillin/{uuid}`) is returned as `saved_url`. At this point the Kaizen page is open in the CDP browser with all filled values rendered. We can:

1. Navigate (or already be on) the saved draft page
2. Use Playwright `page.evaluate()` to read every form field value
3. Compare actual filled values against the expected field data
4. Log/report any mismatches

## What to check

For each DOM ID in `FORM_FIELD_MAP[form_type]`:
- **Text/textarea**: value is non-empty
- **Select/dropdown**: selectedIndex > 0 (non-default)
- **Date**: value is non-empty and parseable
- **Checkbox**: checked state matches expected

For the curriculum/KC section:
- Expand all SLO accordions
- Check which KC checkboxes are ticked
- Compare against expected KCs from the draft

## Integration point

Add a `_verify_filing(page, form_type, expected_fields)` function in `kaizen_form_filer.py` that:

1. Takes the current Playwright page (already on the fillin URL)
2. Reads all field values from the DOM
3. Compares against a set of "must have content" field keys
4. Returns a dict of `{field_key: "filled" | "empty" | "mismatch"}`

Call this right before/after `_save_form()` or as a post-save verification step.

## Future: scoring

Each form type could have a required vs optional field list. The QA check scores:
- Required fields filled: PASS
- Required fields empty: FAIL
- Optional fields filled: PASS
- Optional fields empty: WARN

A score of `{passed, failed, total}` per filing helps track long-term quality trends.
