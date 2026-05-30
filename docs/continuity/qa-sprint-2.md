# QA Sprint 2 — DOM Mapping Gaps

## The data (from real filings tonight)

Every form filed successfully for text fields, dates, and dropdowns. The consistent failures are:

### 1. QIAT Form — Curriculum KCs
- The `_fill_curriculum_links` function uses JavaScript to find KC checkboxes by text label: `span.ng-binding.ng-scope`
- The QIAT form renders checkboxes using a `kzt-checkbox` Angular directive that puts text outside the standard DOM hierarchy
- Fix: Add a QIAT-specific curriculum expand/tick path in `_fill_curriculum_links` that matches checkboxes by DOM position within the expanded SLO section

### 2. QIAT Form — QI Journey Checkboxes (section 4.1)
- 6 QI Journey aspects were identified in the draft but none are ticked on the form
- The checkboxes are Angular `kzt-checkbox` components with no `id` attribute
- Fix: Add a dedicated section for 4.1 checkboxes in `FORM_FIELD_MAP["QIAT"]` with the Angular node UUIDs, or use DOM position mapping

### 3. Curriculum KCs on other forms
- The curriculum tree rendering varies by form type (CBD, DOPS, QIAT, LAT all use different Angular component structures)
- The fallback JS (`TICK_KC_FALLBACK_JS`) was added but it uses text pattern matching that doesn't work on QIAT

### 4. Stage of Training
- Many forms leave this empty because it's not in the case text
- The user's training level is stored in `UserProfile.training_level` via `/settings`
- Fix: Read training level from profile and fill Stage dropdown from that value

## Priority fix order
1. Stage of Training — read from profile (highest impact, simplest fix)
2. QIAT curriculum KCs — target the specific DOM structure
3. QI Journey checkboxes — add DOM IDs to field map
4. General curriculum KC fix — audit the `_fill_curriculum_links` approach for each form type
