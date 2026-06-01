# Kaizen Mapping Sprint — June 2026

**Status:** Active sprint plan. Mapping is docs/planning + read-only verification only.
**Started:** 2026-06-01
**Supersedes / related:** extends `backend/engine/providers/kaizen/domain_skill/README.md` and `portfolio-structure.md` (platform reference, already authoritative); precondition for `docs/PORTFOLIO_HEALTH_SPEC.md` Phase 2 auto-populate; precondition for the trainee/CESR readiness overlays in `docs/roadmap/features-roadmap-2026-06.md`.

---

## Why this sprint

Portfolio Guru's filing engine grew up as a series of per-form, per-user patches. That worked for write-side WPBA filing, but Portfolio Health, ARCP readiness, and CESR equivalence cannot be answered by walking the user's Telegram history. They need a faithful read of the user's Kaizen portfolio — what's filed, when, against which curriculum target, in what status.

If we build that read as another per-user, per-form scrape on top of the existing filer, we will repeat the patch pile and never have a stable substrate to reason over. The product needs one shared **Kaizen platform adapter** — a versioned map of routes, entities, selectors, and extraction patterns — and then a per-user **read-only sync** that runs through that adapter and produces this user's normalised evidence inventory.

This sprint formalises that adapter, audits what's already mapped against what's still missing, and ships the first thin slice of read-only sync (Kaizen Portfolio Index v1).

## What stays out of scope this sprint

- No new write-side filing (no submit, no save-draft, no edits to Kaizen) beyond what already ships.
- No live runner restart, no launchd, no GitHub Actions runner changes, no push, no Telegram traffic, no production deploy.
- No new credentials handling: the existing `filer_router` + Fernet-encrypted credential store stays the source of truth; the adapter consumes already-authenticated CDP sessions only.
- No CESR-specific extraction beyond identifying which Kaizen surfaces differ for non-training accounts.
- No web dashboard, no Supabase schema change in Sprint scope. The Index v1 schema is local SQLite first; Supabase mirror is a follow-up.

---

## Public-product principle

The adapter is a **platform map**, not a per-user scrape. One shared description of Kaizen, used by every Portfolio Guru user.

| Layer                         | Shared / per-user             | Owns                                                                                               |
| ----------------------------- | ----------------------------- | -------------------------------------------------------------------------------------------------- |
| Platform map (this sprint)    | Shared across all users       | Routes, entity shapes, selectors, extraction patterns, gotchas, version + last-verified timestamps |
| Sync runtime                  | Shared code, per-user session | Drives the adapter against the logged-in user's Kaizen, produces normalised evidence records       |
| User evidence index           | Per user                      | This user's `evidence_items` + `index_runs`; never shared, never mixed                             |
| Pathway overlay (ARCP / CESR) | Shared frameworks             | Maps this user's index against the curriculum/equivalence framework                                |

This separation is the difference between "a clever script for Moeed's Higher trainee account" and "a Kaizen capability the product owns".

---

## Adapter contract

The Kaizen adapter is defined by, and only by, these artefacts. Everything else (Telegram, draft builder, readiness scoring) talks to it through this surface.

### 1. Routes

Canonical route list verified 2026-05-16 and frozen in `backend/engine/providers/kaizen/domain_skill/README.md` (see "Routes"). The adapter must address every route by name (e.g. `timeline_category(category)`, `event_view(uuid)`, `event_view_section(section_uuid)`, `goals_list(curriculum)`, `goal_work(goal_uuid)`, `report_run(template_uuid)`, `files`, `dashboard`, `profile_view`, `activities`, `inbox`). Adapter consumers never hand-build URLs.

### 2. Entities

Entity shapes verified in `portfolio-structure.md` (User → Programme/Locations/FTE/Curricula → Timeline → Event → Section → Field; Curriculum → SLO → KC; Report → Nodes → States). The adapter normalises each into one typed record per kind:

- `KaizenEvent` (timeline row + detail, ID = section UUID where applicable)
- `KaizenEventField` (label, value, required, field UUID when known)
- `KaizenTag` (one per linked KC; carries curriculum + SLO + KC label)
- `KaizenFile` (filename, parent event, created date, link)
- `KaizenGoal` (SLO) and `KaizenTarget` (KC) with count badges
- `KaizenReportNode` (hierarchical curriculum-coverage node + state)
- `KaizenProfile` (locations, programme, FTE, attached curricula)
- `KaizenActivity` (notification / activity feed row, including saved drafts)

Form-specific field UUIDs stay in the existing form mappings used by the filer; this sprint does **not** rewrite those.

### 3. Source priority

When the same fact appears on multiple surfaces, the adapter prefers in this order:

1. **Event detail view** (`/events/view/...` or `/events/view-section/...`) for canonical field values.
2. **Timeline row** for state badges, ownership, dates, and the linked-event title.
3. **Goal/KC view** for curriculum mappings (each KC's linked-event list is authoritative for "what counts").
4. **Reports** only as a secondary cross-check for coverage totals; never as the primary source of an event.
5. **Dashboard widgets** only for high-level counters; counters are (event, target) link rows, not distinct events — never confuse the two.
6. **Activities/Drafts** (`/activities`) as the authoritative source for saved-draft state — drafts do **not** appear on the timeline.

### 4. Extraction methods

For each route the adapter declares one of:

- `dom_extract` — deterministic DOM read (CSS selectors from `selectors.json` and `README.md`).
- `dom_extract_with_scroll` — same, plus infinite-scroll pagination using the interaction-skills helper.
- `dom_navigate_then_extract` — open detail surface, wait for Formly mount, then read.
- `unsupported` — not in scope; the adapter must refuse rather than guess.

Write-side methods (`fill`, `save_draft`, `submit`, …) belong to the filer, not the adapter. The adapter is read-only.

### 5. Page-render contract

Every adapter call must:

1. Navigate via `goto_url` (or stay on the current route if already there).
2. `wait_for_load()` → `wait_for_network_idle(timeout=15)` → `wait(3)` for AngularJS + Formly mount.
3. Re-read `h1` after the wait to detect the "Add a Post" placeholder rot; refuse the extraction if it hasn't settled.
4. Refuse to act if the URL is on `auth.kaizenep.com` — stop and surface a re-auth needed signal.

### 6. Gotchas (load-bearing)

Carried verbatim from the domain skill — the adapter must encode each as an assertion:

- Strip the leading 🟢/🔴 emoji from `<title>` before comparisons.
- Never use `fill_input` on Formly inputs — character doubling (write-side only, but assert no read codepath types into inputs).
- `/events/view-section/{uuid}` vs `/events/view/{uuid}` are both valid; do not normalise.
- Dates render `D MMM, YYYY`; new-event inputs accept `d/m/yyyy` — never US `m/d/yyyy`.
- Saved drafts live under `/activities`, not the timeline.
- Quoted attribute selectors in `js("...")` need escaped quotes.
- Field UUIDs are stable **per event-type version**; a `v8` bump rotates them — re-read labels.
- Two QIAT types coexist (`QIAT (EM ST4-ST6)` and `EM QIAT (2025 Update - v7)`); they satisfy overlapping but distinct targets.
- LAT appears in both Assessments and Manage/Administer/Lead categories — de-duplicate by event UUID, not by listing surface.

### 7. Versioning and detection

The adapter carries two version stamps:

- `platform_map_version` (sprint-owned; bumped when this doc, `README.md`, `portfolio-structure.md`, or `selectors.json` change in a way that affects extraction).
- `last_verified_at` per route (timestamp of the last successful read-only check against the live site).

On every sync run the adapter records:

- Routes that returned the expected entity shape (`ok`).
- Routes whose shape diverged from the map (`drift` — captured with a PHI-free diff for triage).
- Routes that 4xx/5xx or redirect to auth (`auth_or_unavailable`).

A `drift` on any route fails the sync run for that surface but does not corrupt the existing index — the prior values stay until the map is reconciled.

---

## What is already mapped

Cross-check against existing artefacts so this sprint does not re-do work.

| Surface                                                                                                   | Mapped in                                              | Status                     |
| --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ | -------------------------- |
| Routes (15 canonical paths)                                                                               | `README.md` → "Routes"                                 | Verified 2026-05-16        |
| Timeline categories (15 categories incl. Absence/CCT empty)                                               | `README.md` → "Timeline categories"                    | Verified 2026-05-16        |
| Timeline row shape (`.row.event-inner`, `h2.entry-title`, item count regex)                               | `README.md` + `portfolio-structure.md`                 | Verified                   |
| Event detail selectors (read-only render — `.form-text__*`, `.event-tag`, `.event-users`, progress state) | `README.md` → "Read-only event detail"                 | Verified                   |
| Verified field shapes — CBD (2025 update), Procedural Log ST3-ST6, Reflection on ESLE, Add a Post         | `portfolio-structure.md`                               | Verified                   |
| Curriculum tree (Higher EM 2025 Update SLOs + KC widget structure, procedural skills list)                | `README.md` + `portfolio-structure.md`                 | Verified                   |
| Reports inventory (ACCS LO1–LO11 templates, top-level coverage report UUIDs)                              | `portfolio-structure.md`                               | Verified                   |
| Files surface (`/files` reuses timeline rendering, 205 entries on test account)                           | `README.md` + `portfolio-structure.md`                 | Verified                   |
| Dashboard 11 widgets (titles + which counters are link-rows vs distinct)                                  | `README.md` → "Dashboard widgets"                      | Verified                   |
| Profile surface (locations, programme, FTE, attached curricula)                                           | `portfolio-structure.md` → "Entities"                  | Verified for trainee       |
| Activities / Saved drafts gotcha (not on timeline; SweetAlert2 delete)                                    | `README.md` → "Common gotchas"                         | Verified                   |
| 44+ form types with write-side field UUIDs                                                                | `docs/form-coverage.md` + filer code                   | Verified per-form by filer |
| Auth host and credentials boundary (`auth.kaizenep.com`, stop on 2FA/captcha)                             | `README.md` + filing routing discipline in `CLAUDE.md` | Verified                   |

The takeaway: **the platform map is substantially complete for trainee Higher accounts in the 2025 Update curriculum**. The sprint's job is to _promote_ it from per-form skill code into a contract the rest of the product can consume, and to close the read-only gaps below.

## Gaps to verify (read-only, foreground-owned)

These are the only places we still need the live browser. Each gap is a separate proof, each is read-only, each writes nothing back to Kaizen. Verification belongs to the foreground / orchestrator with a logged-in Chrome session — this sprint's docs ship without that proof, but the gaps must be enumerated.

1. **Live drift since 2026-05-16.** Re-verify the 15 canonical routes return the expected selectors. Specifically: timeline category labels and counts, dashboard widget titles, profile page layout, `/activities` saved-drafts header behaviour. Expected output: PHI-free diff against `README.md` + `selectors.json`.
2. **Goals per curriculum variant.** `/goals/list/all` and `/goals/list/{Intermediate%202021}` were not screenshotted in the original mapping pass. Confirm the goal list shape is identical across Higher 2025 Update, Intermediate 2021, Higher 2021, and PEM, or capture the deltas.
3. **Report usefulness for indexing.** Decide whether per-template `/report_templates/run/{uuid}` views give us anything Timeline doesn't already give us. If reports add nothing for an individual user's indexing (vs the organisation-wide coverage view), drop them from sync scope to save a navigation per run.
4. **Documents/file metadata limits.** Confirm `/files` exposes upload date, parent event title, and link — and whether file size or MIME is visible without download. The adapter records only what is visible without downloading the file.
5. **Activity/draft indexing.** `/activities` lists in-flight activities and saved drafts. Confirm: (a) the row shape, (b) draft URL pattern (`/events/view-section/{uuid}` not `/events/fillin/{uuid}` — already noted as a gotcha), (c) that a Portfolio Guru draft that "didn't file" cleanly is recoverable here.
6. **Status / reviewer / accepted states.** Existing `.event-section-progress-state` covers complete/pending. Confirm what reviewer-side states exist (e.g. "Submitted", "Returned for amendment", "Sign-off") on assessor-completed surfaces — these are the states Portfolio Health needs to call evidence "accepted". Do not navigate assessor-only surfaces from a trainee account.
7. **CESR / non-training account differences.** A non-training Kaizen account may have: a different curriculum attachment, a different default timeline category set, no ARCP form widget, different report templates. We do not have a CESR test account yet. Action this sprint: capture the assumption and flag the variants the adapter must tolerate (different curricula list, no `ARCP Form*` events, possibly no `End of Placement Report` types). Live verification deferred until a CESR candidate dogfoods.
8. **Curriculum version bump (`v8`+) detection.** No `v8` yet. The adapter must already declare the version check (compare field labels even when UUIDs change) so the first `v8` does not silently corrupt the index.

Out of scope this sprint, but recorded so the next sprint can pick them up.

---

## Quality gates

For the adapter to be considered "good enough to anchor Portfolio Health":

| Gate                     | Criterion                                                                                                                                                                                 |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Route coverage           | All 15 canonical routes have a declared `extraction_method` and a frozen selector set.                                                                                                    |
| Entity coverage          | Every entity in §2 has a typed record and at least one verified-from-DOM fixture.                                                                                                         |
| Selector tests           | The selectors used by the adapter are exercised by unit tests against captured HTML fixtures stored under `backend/engine/providers/kaizen/domain_skill/fixtures/` (PHI-free, sanitised). |
| Live smoke boundary      | At least one read-only run against the live Chrome session per route, recorded as `ok` / `drift` / `auth_or_unavailable`. Owned by orchestrator.                                          |
| Schema validation        | Every extracted record validates against the typed model before being written to the index.                                                                                               |
| Version-change detection | A label-based assertion runs alongside every UUID-based read; mismatch surfaces a `drift` and refuses to write that record.                                                               |
| Safety                   | No write codepath in the adapter. No credential read. No PHI in logs.                                                                                                                     |

A surface that does not meet all seven gates ships as `unsupported` in the adapter — it does not get silently half-mapped.

---

## First build slice — Kaizen Portfolio Index v1

The point of this sprint is a working substrate, not a documentation exercise. The first build slice (after this doc lands) is a thin, read-only sync that proves the adapter end-to-end.

### Scope

A `kaizen_index` module that, given an authenticated Chrome session for the current user:

1. Refreshes the user's `KaizenProfile` (locations, programme, FTE, attached curricula).
2. Walks the timeline categories that matter for Portfolio Health (Assessments, Procedural Logs, Reflections, Educational Review & Meetings, MSF, Teaching & Education, Research/Audit/QI, Manage/Administer/Lead, e-Learning, Exams, Documents). Skips the empty categories on a given account.
3. Reads the row shape per item, dereferences each item once to the detail view, and produces a normalised `evidence_item` record.
4. De-duplicates by event UUID (LAT-in-two-categories is one event).
5. Also pulls `/activities` for saved drafts and indexes them with a distinct `source = kaizen_draft` flag.
6. Writes an `index_runs` row recording started_at, finished_at, counts per category, drift, auth_or_unavailable, and total wall-clock.

### Local schema (SQLite, alongside existing `usage.db`)

```text
evidence_items
  id                    TEXT PRIMARY KEY      -- Kaizen event/section UUID
  user_id               TEXT NOT NULL
  surface               TEXT NOT NULL         -- 'event' | 'event_section' | 'draft' | 'file'
  event_type            TEXT                  -- verbatim h1 from Kaizen
  category              TEXT                  -- timeline category bucket
  state                 TEXT                  -- complete | pending | submitted | draft | merged
  date_occurred_on      TEXT
  end_date              TEXT
  description           TEXT
  linked_kc_tags        JSON                  -- ['Higher SLO1 KC1', ...]
  filled_in_by          TEXT
  filled_in_on          TEXT
  parent_event_id       TEXT                  -- for MSF/multi-section children, file → parent
  detail_url            TEXT                  -- canonical /events/view... URL
  last_seen_at          TEXT NOT NULL
  first_seen_at         TEXT NOT NULL

index_runs
  id                    INTEGER PRIMARY KEY AUTOINCREMENT
  user_id               TEXT NOT NULL
  started_at            TEXT NOT NULL
  finished_at           TEXT
  status                TEXT NOT NULL         -- ok | partial | drift | auth_required | failed
  rows_seen             INTEGER
  rows_written          INTEGER
  rows_drifted          INTEGER
  notes                 TEXT
```

`evidence_items` is upsert-on-`(user_id, id)`. A row missing on a later run does not get deleted — it gets a `last_seen_at` that lags, and Portfolio Health can decide how to treat ageing. (Hard-deleting could mask Kaizen outages as missing evidence.)

### De-duplication rules

- Same event UUID seen in two categories → one row (`category` becomes a JSON array if needed; v1 stores the first-seen category and an array column is a v1.1 follow-up).
- MSF parent + per-rater section UUIDs → one parent row + child rows, joined by `parent_event_id`.
- A `/files` entry whose parent event already indexes → still indexed as `surface = file`, joined via `parent_event_id`.
- A Kaizen saved draft and a later filed event are **not** auto-merged. The draft has its own UUID; if/when it becomes a filed event the filed event is indexed separately and the draft is left for the user to dismiss.

### Settings status row

The bot's `/settings` (or equivalent surface) gains one row:

> **Kaizen sync** — Last refresh: 2026-06-01 11:38 (ok). Items indexed: 412. [Refresh now]

`Refresh now` triggers a sync against the user's existing CDP session. Never asks for credentials in the prompt. Never restarts the Chrome session. Never writes to Kaizen.

### `/health` hook

Phase 2 of `docs/PORTFOLIO_HEALTH_SPEC.md` says "Auto-populate from existing PG filing activity". With Index v1 in place, the auto-populate source becomes:

1. Kaizen Portfolio Index (`evidence_items` table) — primary, when present and recent.
2. Existing `usage` / `case_archive` PG filing records — fallback when no index yet, or for users who haven't done a Kaizen sync.
3. Manual entry — same as today.

Switching the primary source from PG-filing-only to Kaizen-indexed is the difference between "we tell you about cases you filed _with us_" and "we tell you about your whole Kaizen portfolio".

### Safety boundaries (slice)

- Read-only. The slice imports nothing from `filer.py`, `browser_filer.py`, or `assessor_writeback.py`.
- Uses the already-authenticated CDP session created by the existing filer; never starts a new browser, never types credentials, never crosses `auth.kaizenep.com`.
- No supervisor side, no assessor surfaces, no `/inbox` indexing in v1.
- A failed sync surfaces as `auth_required` / `partial` / `drift` in `index_runs`; never crashes the bot.

---

## Sprint scorecard (definition of done)

| #   | Deliverable                                                                                                                                                                                                                  | Status            |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| 1   | This doc lands and is linked from `TASK.md` and `docs/plan.md`                                                                                                                                                               | done              |
| 2   | The adapter contract sections (routes, entities, source priority, extraction methods, page-render contract, gotchas, versioning) reconcile with `domain_skill/README.md` and `portfolio-structure.md` without contradictions | done              |
| 3   | Gap list (1–8 above) is acknowledged in `TASK.md` and queued for foreground live verification                                                                                                                                | done              |
| 4   | Quality-gates checklist exists and is referenced by the Index v1 build slice                                                                                                                                                 | done              |
| 5   | Index v1 schema (above) is the contract the implementing slice will follow                                                                                                                                                   | done              |
| 6   | `docs/PORTFOLIO_HEALTH_SPEC.md` Phase 2 auto-populate clause references the Index                                                                                                                                            | done              |
| 7   | No live Kaizen actions in this sprint's docs work                                                                                                                                                                            | met (this sprint) |
| 8   | No write codepath added                                                                                                                                                                                                      | met (this sprint) |

The docs/planning slice exits when (1)–(8) are checked. Foreground live
verification remains the next explicit proof gate before implementation.

## Proof gate

- `git diff --check` clean.
- `TASK.md` references this doc; existing addendum history preserved.
- `docs/plan.md` references this doc as the next phase boundary.
- `docs/PORTFOLIO_HEALTH_SPEC.md` Phase 2 references the Index v1 schema as the auto-populate source.
- No edits to bot runtime, filer, credential, deployment, or test code.
- No commit from this worker.

---

## References

- `backend/engine/providers/kaizen/domain_skill/README.md` — platform reference, verified 2026-05-16.
- `backend/engine/providers/kaizen/domain_skill/portfolio-structure.md` — entity and field shapes.
- `backend/engine/providers/kaizen/domain_skill/selectors.json` — selector inventory.
- `backend/engine/providers/kaizen/domain_skill/extract_portfolio.py` — prior autonomous-extractor prototype (predates this contract; the v1 slice supersedes it).
- `docs/PORTFOLIO_HEALTH_SPEC.md` — universal Portfolio Health + Pathway Guidance spec.
- `docs/roadmap/features-roadmap-2026-06.md` — feature roadmap that depends on this substrate (health chart, ARCP readiness, KC coverage).
- `docs/roadmap/portfolio-pathways-research-2026-06.md` — pathway requirements research for the eventual CESR overlay.
- `CLAUDE.md` (project) — filing routing discipline (write-side, out of this sprint's scope).
