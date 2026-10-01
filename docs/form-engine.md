# Offline form engine (sandbox)

Standard-library package: saved HTML/Kaizen JSON → versioned FormMap → deterministic
draft plan → inert mock DOM. No live bot integration, network or credentials.

## Local CLI

```sh
python -m backend.form_engine read saved.html --platform example --form reflection
python -m backend.form_engine verify form_maps/example/reflection.json --field description
python -m backend.form_engine verify form_maps/example/reflection.json --field custom --concept evidence_note
python -m backend.form_engine plan form_maps/example/reflection.json values.json
python -m backend.form_engine read backend/unified_dom_map.json --platform kaizen --form CBD --kaizen-json
```

`values.json` supplies canonical keys and doctor-provided values only: unchanged
text, ISO dates, exact option values, JSON booleans. No inferred curriculum/content.

## Maps and review

`form_maps/` (`--output-dir` overrides) stores one JSON per platform/form: labels,
kinds, options, required flags, sections, selectors and states. SHA-256 covers
ordered structure, excluding values/review metadata. Invalid hashes/versions fail.

- `unmapped`: observed without a canonical meaning.
- `candidate`: a conservative label suggestion or explicit `suggest()` call.
- `verified`: an explicit human confirmation through `verify()` or the CLI.

Reads may suggest date, description, reflection and stage of training. Re-reads
reconcile existing maps (`--previous` selects another): changed fields lose verified
state; missing fields remain as audit records. Drift needs explicit review;
unmapped fields never auto-promote. Missing fields must reappear before verification.

Plans use verified fields (`--allow-candidates` opts into undrifted candidates).
Skips have reasons; ambiguous mappings, invalid choices and protected controls fail.

## Adding a platform and limits

Read a saved form under a new platform/form identifier, review meanings, then test
synthetic values; ordinary controls need no platform code. API: `read_html`,
`import_kaizen`, `suggest`, `verify`, `reread`, `save_map`, `load_map`, `make_plan`, `execute`.

Offline proof is not live compatibility. JavaScript widgets, shadow DOM, iframes
and browser validation are unsupported; disabled/readonly/multi-select controls
are skipped. Ambiguous keys fail. Kaizen imports lack options; none are invented.
Execute against saved HTML with matching structure; stale plans fail. File steps
store filenames only, never read/upload files. Only local paths/file URLs work;
all HTTP URLs (localhost included) fail. Scripts/assets never load.
Actions: fill, select, choose, check, set_file; no submit/sign/send/approve/click.
Maps are review data, not signed attestations. `execute(plan, local_html_path)`
returns resulting mock field values.
