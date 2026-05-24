# Clinical Supervisor — Build Sprint Brief

**Date:** 2026-05-23 | **Branch:** `feature/clinical-supervisor` (create from `main`)

## Objective

Build the Clinical Supervisor backend module for Portfolio Guru. This is **backend-only** — no front-end/Telegram entry-point changes.

The module detects new assessor tickets assigned to the supervisor's Kaizen account, reads ticket content (read-only), and produces structured assessor ticket data that the existing Telegram bot will later render in supervisor mode.

## Acceptance Criteria

1. `backend/supervisor_poller.py` — polls Kaizen "my assessments" page, diffs against a persisted state tracker, and identifies new unfilled tickets — including for queues that surface `state=None` on every row (Ahmed Mahdi's surface). Classification falls back to a per-row `fill_action` signal extracted from the Fill in anchor.
2. `backend/assessor_reader.py` — reads the unfilled assessor section of a ticket (read-only) and returns structured field data per form type
3. Complete assessor field schema for all 5 form types found in discovery (CBD, DOPS, Mini-CEX, QIAT, ESLE)
4. State tracker persists ticket IDs and status to disk (simple JSON file)
5. `backend/role_detector.py` — read-only MyTimeline probe that classifies a logged-in Kaizen account as `assessor` / `trainee` / `unknown` using the `"You cannot create any events!"` barrier text. Pure helper + async wrapper, no front-end wiring in this slice.
6. `backend/supervisor_workflow.py` — orchestration seam. Role cache with `set_role_if_better` (demotion-safe), `SupervisorNotificationPayload` (PHI-free), `run_supervisor_poll` callable orchestrator that gates on `kaizen_role=="assessor"`. Telegram render helpers ship as pure formatters; live handlers not wired yet.
7. `profile_store.kaizen_role` column persists the canonical role per Telegram user. `setup_password` calls `set_role_if_better` after a successful login.
8. All operations are **read-only** — never clicks Fill In, Save, Submit, or any write control
9. Tests pass (existing + new)

## Write Scope

Allowed:

- `backend/supervisor_poller.py` — new module
- `backend/assessor_reader.py` — new module (or extend `assessor_mapper.py`)
- `backend/form_schemas.py` — extend with assessor field schemas for all 5 form types
- `backend/assessor_mapper.py` — extend existing 395-line mapper
- `backend/state_tracker.py` — new lightweight persistence module
- `backend/role_detector.py` — new read-only MyTimeline role probe
- `backend/supervisor_workflow.py` — new orchestration seam (role cache + payload + poll driver + render helpers)
- `backend/profile_store.py` — additive `kaizen_role` column + helpers
- `backend/bot.py` — one narrow `set_role_if_better` call in `setup_password` only
- Tests in `backend/tests/`
- `docs/clinical-supervisor-architecture.md` — update with verified field mappings
- `docs/assessor-mapping/consultant-portfolio-map.md` — update queue / role detection status
- `WORKFLOWS.md` — Flow 2A status panel (current implementation status block)

**Do not touch:**

- `backend/bot.py` — no Telegram entry point changes
- Any front-end code
- `AGENTS.md`, `TASK.md` — product direction/doc changes only
- Deployment config (`railway.json`, `render.yaml`)
- Any existing Kaizen filing or write-side code

## Existing Assets

- `backend/assessor_mapper.py` (395 lines) — read-only assessor scaffolding with ticket list/detail/summary extraction, control classification, assessment row parsing
- `backend/kaizen_unsigned_scraper.py` (205 lines) — CDP-based browser connection + login
- `backend/form_schemas.py` — form-type definitions
- Discovery data (from today's live mapping):
  - 10 pending tickets in Ahmed Mahdi's queue
  - CBD unfilled shape known (Assessor Registration Number, Job title, Entrustment Scale, Feedback, Recommendation)
  - DOPS / Mini-CEX / QIAT / ESLE assessor fields known from completed tickets
  - ESLE is the most complex (30+ fields including NTS ratings)

## Architecture

```
supervisor_poller.py              assessor_reader.py
  └─ poll_assessment_queue()         └─ open_ticket_readonly()
  └─ diff_against_state()            └─ extract_assessor_section()
  └─ classify_ticket_status()        └─ return AssessorTicketData[]
       │  (state text → fill_action fallback)
       ▼                                    ▼
  state_tracker.py              assessor_mapper.py (extend)
  └─ TrackedState                  └─ extract_assessment_rows
  └─ load() / save()                   (records fill_action per row)
  └─ is_new_ticket()                └─ AssessorTicketSummary.fill_action
  └─ mark_seen()                       (True/False/None)

role_detector.py
  └─ classify_role_from_timeline_text()    pure helper, returns role
  └─ detect_role(page)                     async wrapper, navigates MyTimeline

supervisor_workflow.py (new — orchestration seam)
  ├─ normalize_role(raw)                   any provider string → canonical
  ├─ set_role_if_better(user, raw)         demotion-safe cache wrapper
  ├─ SupervisorNotificationPayload         PHI-free dataclass
  ├─ build_notification_payloads(...)      summaries → payloads, status-filtered
  ├─ render_supervisor_notification_text   PHI-free Telegram text
  ├─ render_supervisor_ticket_detail_text  PHI-OK render (after explicit Open)
  └─ run_supervisor_poll(user, page, …)    role-gate → poll → payload list

profile_store.py (extend)
  ├─ UserProfile.kaizen_role               new column, nullable
  ├─ store_kaizen_role / get_kaizen_role   raw read/write
  └─ _autoapply_userprofile_migrations()   import-time idempotent ALTER

bot.py (extend, narrow)
  └─ setup_password                        +1 line: set_role_if_better(...)
```

## Verification

```bash
cd ~/projects/portfolio-guru
pytest backend/tests/ -x -q --tb=short
```

## Safety Rules

- Never click Fill In, Save, Submit, or any write control in Kaizen
- Never modify existing filing, draft, or submit code
- Never use supervisor credentials (Ahmed Mahdi's account) to create or modify any data
- State tracker writes to a local JSON file only — never to Kaizen
- Polling must not fire alerts for already-seen tickets

## Known Blockers

- Unfilled CBD shape may require clicking "Fill In" to see blank assessor field labels — this was done once with explicit approval in Phase 2.7. The mapper already has `extract_assessor_completion_shape()` for filled tickets; unfilled raw label detection needs a read-only approach (DOM inspection without write interaction)
- ESLE complexity (30+ fields) — should be last form type wired up
