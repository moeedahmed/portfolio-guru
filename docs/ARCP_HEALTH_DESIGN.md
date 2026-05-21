# Portfolio Readiness / ARCP Health Feature Spec

**Status:** Product spec and implementation spine. No build started.
**Last updated:** 2026-05-21

## Product Positioning

Portfolio Readiness is the planning layer around Portfolio Guru's existing WPBA drafting workflow. It helps UK doctors in training understand whether their portfolio evidence looks organised, balanced, and ready for supervisor review before ARCP.

The product does not promise ARCP success. It gives a structured, source-grounded readiness view: what evidence the doctor says they have, what looks weak or missing, what needs supervisor confirmation, and what to prepare next.

Moeed's own workflow can be used as a dogfood case, but the feature must be generic Portfolio Guru product work, not a Moeed-only tracker and not a Medic-internal automation.

## Target User

Primary user:

- UK doctors in training preparing for ARCP, initially EM/RCEM trainees because Portfolio Guru already supports RCEM WPBA drafting and SLO/KC mapping.

Secondary users:

- Doctors approaching supervisor meetings who need a concise evidence summary and action list.
- Trainees with fragmented records across Kaizen drafts, reflections, WPBAs, teaching, QI, audit, and supervisor feedback.

## Problem

Portfolio readiness is fragmented. A trainee may have cases, reflections, WPBAs, supervisor comments, curriculum outcomes, and deadlines, but still not know:

- which domains have evidence,
- which domains are weak or only uploaded but not accepted,
- which items need supervisor confirmation,
- what to prepare next,
- what can safely be drafted as reflection or discussion notes.

Portfolio Guru should turn that into a clear readiness view without taking control away from the doctor.

## Non-Goals

- No Kaizen login, scraping, import, or browser automation in the MVP.
- No automated Kaizen submission, sign-off request, supervisor message, or ARCP upload.
- No claim that Portfolio Guru guarantees ARCP success or satisfies a deanery, college, or supervisor.
- No hard-coded current ARCP dates, trainee deadlines, or RCEM requirements that can silently become wrong.
- No fully automated reflection generation from vague prompts. Drafts need source text.
- No generic task manager. Every item should relate to portfolio readiness, evidence, curriculum coverage, review status, or next preparation.
- No Medic-only internal workflow or user-specific private tracker.

## Safety Boundaries

- The doctor remains responsible for reviewing, correcting, and submitting any portfolio material.
- Every readiness score and suggestion must be labelled as a planning aid, not an ARCP decision.
- Never invent clinical details, supervisors, dates, procedures, outcomes, curriculum requirements, or evidence status.
- Any reflection draft, SLO/KC mapping, or gap label must be based on user-entered or user-uploaded source material.
- "Accepted by supervisor" is a separate manual status from "uploaded", "drafted", or "claimed".
- Requirement sets must be explicitly selected or confirmed by the user. If requirements are unknown or stale, show "needs confirmation" rather than pretending certainty.
- Clinical content, supervisor names, and patient details must not appear in analytics or logs.
- Kaizen remains draft-only in existing filing flows; this feature does not add live portfolio actions.

## User Journey

1. User opens Portfolio Readiness from Telegram or a future web dashboard.
2. Product asks for the minimum setup:
   - training programme or preset,
   - training stage/year,
   - curriculum/version if known,
   - ARCP or review date if known,
   - whether the user wants to start from a blank checklist or paste/upload existing evidence summaries.
3. User adds evidence manually:
   - WPBA/reflection title,
   - form or evidence type,
   - date,
   - source text or summary,
   - mapped SLO/KC/domain if known,
   - status: planned, drafted, uploaded, reviewed, accepted, rejected/needs work.
4. Product shows a readiness summary:
   - on track / needs attention / at risk / unknown,
   - high-risk gaps,
   - weak domains,
   - items needing supervisor confirmation,
   - next actions ordered by urgency and impact.
5. User can open a domain or evidence item to add detail, correct mapping, change status, or request draft wording.
6. Product can generate a review pack:
   - evidence list,
   - mapped domains,
   - gaps and uncertainties,
   - draft reflection/discussion notes,
   - supervisor meeting prompts.
7. User reviews and exports or copies the pack manually. No external submission happens.

## Manual MVP Scope

Manual MVP is user-entered first:

- Create or edit a readiness profile.
- Add, edit, and delete evidence items manually.
- Track evidence status independently from mapped curriculum coverage.
- Map evidence to domains/SLOs/KCs manually, with optional AI suggestions based only on source text.
- Show a traffic-light readiness dashboard.
- Show high-risk gaps, uncertain mappings, and supervisor-confirmation needs.
- Generate a review-before-use export pack.
- Use existing Portfolio Guru drafted WPBA data only where it is already available in the product; do not scrape Kaizen to discover more.

MVP may include an EM/RCEM preset, but it must be presented as a selected preset requiring user confirmation, not as a definitive live requirements engine.

## Data Model

The MVP data model should be explicit enough to support Telegram first and future web surfaces.

### `readiness_profiles`

One active profile per user, with optional archived profiles for prior ARCP cycles.

```text
id
user_id
programme_label
training_stage
curriculum_label
requirement_set_id
review_date
review_date_source        manual | unknown
created_at
updated_at
archived_at
```

### `requirement_sets`

Curated or user-confirmed checklist definitions. These should be versioned and labelled with their source/uncertainty.

```text
id
name
programme_label
curriculum_label
version_label
source_label
status                    draft | active | retired
last_reviewed_at
```

### `requirements`

Individual checklist rows or domains.

```text
id
requirement_set_id
code
title
description
category
minimum_count             nullable
required_status           nullable
sort_order
```

### `evidence_items`

Manual evidence records and product-created WPBA draft references.

```text
id
user_id
profile_id
title
evidence_type             WPBA | reflection | teaching | audit | QI | feedback | other
form_type                 nullable
event_date
source_kind               manual | pasted_text | uploaded_file | portfolio_guru_draft
source_reference          nullable, no secrets
source_text               encrypted or redacted according to storage policy
summary
status                    planned | drafted | uploaded | reviewed | accepted | rejected | needs_work
status_note
created_at
updated_at
```

### `evidence_requirement_links`

Many-to-many mapping between evidence and requirements.

```text
id
evidence_item_id
requirement_id
mapping_source            user | assistant_suggested | imported_from_draft
confidence                high | medium | low | needs_confirmation
supporting_excerpt        nullable, short and source-grounded
confirmed_by_user_at
```

### `readiness_snapshots`

Computed summary saved for audit/debugging, not clinical truth.

```text
id
profile_id
computed_at
readiness_status          on_track | needs_attention | at_risk | unknown
summary_json              counts, gaps, warnings, next_actions
input_version_hash
```

## Surfaces

### Telegram MVP

- `/health` or `Portfolio Readiness` button opens a short status summary.
- Setup prompt asks for stage, review date, and requirement preset confirmation.
- Add evidence flow accepts short manual entries and pasted summaries.
- Evidence list shows compact rows with status and mapped domains.
- Gap view shows missing / weak / unclear domains.
- Export pack sends a copyable Markdown-style summary for supervisor review.

Telegram should stay concise and action-oriented. It is good for quick capture, status checks, and reminders, not dense portfolio browsing.

### Future Web Dashboard

- Readiness overview with status, review countdown, evidence counts, and high-risk gaps.
- Checklist/domain page with filters for missing, weak, accepted, and needs confirmation.
- Evidence table with search, status, date range, type, and mapping filters.
- Evidence detail editor for source text, summary, mappings, and status.
- Review pack builder/export.
- Settings for profile, curriculum preset, and archived ARCP cycles.

The web app should own dense comparison, bulk editing, and review pack generation. Telegram should link to the relevant web page when the task becomes too detailed.

## Telegram vs Future Web Boundary

Telegram:

- quick capture,
- one-screen readiness summary,
- add/update one evidence item,
- ask for a next action,
- generate short draft/reflection suggestions,
- nudge about missing high-risk items.

Future web:

- full dashboard,
- editable checklist,
- multi-item evidence management,
- export pack configuration,
- audit-style history,
- archived cycles.

Shared:

- same readiness profile,
- same evidence records,
- same requirement links,
- same safety warnings and status vocabulary.

## Readiness Logic

Readiness should be explainable and conservative.

Recommended status rules:

- `unknown`: profile is incomplete, requirement set unconfirmed, or too little evidence has been entered.
- `on_track`: no high-risk gaps, evidence has acceptable distribution, and key requirements are accepted or clearly reviewed.
- `needs_attention`: one or more weak/uncertain domains, uploaded-but-not-accepted evidence, or upcoming review date with unresolved actions.
- `at_risk`: missing required domains, many items only planned/drafted, review date near, or user-selected requirements are unconfirmed.

Every status must show the concrete reasons. The UI should never display a traffic-light label without the supporting gap list.

## Copy and Safety Warnings

Standard safety copy:

- "Portfolio Readiness is a planning aid. It does not guarantee ARCP outcome."
- "Check requirements with your training programme, supervisor, and current curriculum guidance."
- "Only mark evidence as accepted if a supervisor or portfolio reviewer has confirmed it."
- "Draft reflections and mappings are based on the source text you provide. Review and edit before using them."
- "No Kaizen import or submission happens from this view."

Tone:

- Clear, specific, non-alarmist.
- Prefer "needs confirmation" over "failed" when the system lacks evidence.
- Prefer "missing from the data you entered" over "missing from your portfolio" unless the product has authoritative portfolio data.

## Analytics and Proof Loops

Analytics must be PHI-free and should measure product value, not clinical content.

Events:

- readiness_profile_created
- readiness_profile_updated
- requirement_set_confirmed
- evidence_item_added
- evidence_status_changed
- evidence_mapping_confirmed
- readiness_summary_viewed
- readiness_gap_opened
- readiness_export_pack_generated
- readiness_next_action_completed

Allowed properties:

- anonymous user/account id,
- programme preset,
- training stage,
- requirement set id/version,
- counts by evidence type/status,
- readiness status,
- number of gaps,
- source kind category.

Disallowed properties:

- clinical narrative,
- patient details,
- supervisor names,
- Kaizen credentials,
- raw pasted/uploaded content.

Proof loops:

- Track whether users update evidence after viewing gaps.
- Track whether suggested next actions are completed.
- Dogfood with synthetic or anonymised evidence first.
- Compare user-reported confidence before and after review pack generation.
- Collect qualitative feedback on false gaps, confusing requirement labels, and supervisor-confirmation language.

## Acceptance Criteria

Spec acceptance:

- This document is the canonical product spec for Portfolio Readiness / ARCP Health.
- MVP explicitly excludes Kaizen scraping/import/submission.
- The feature is generic product-first, with Moeed's workflow only as dogfood.
- Data model separates evidence, requirements, mappings, profile, and computed readiness.
- Telegram and future web responsibilities are clearly separated.

Manual MVP acceptance:

- A user can create a readiness profile without connecting to Kaizen.
- A user can manually add evidence and map it to at least one requirement/domain.
- A user can mark evidence as planned, drafted, uploaded, reviewed, accepted, rejected, or needs work.
- The readiness summary shows status plus concrete reasons and never presents the label alone.
- The product labels unknown or stale requirements as needing confirmation.
- The product can generate a review pack that is clearly marked as draft/planning material.
- No flow logs clinical content in analytics.
- No flow opens Kaizen, scrapes Kaizen, submits to Kaizen, or sends supervisor requests.

## Phased Implementation Plan

### Phase 0 - Spec and restart spine

Status: current.

- Expand this product spec.
- Add restart pointers in `docs/plan.md`, `TASK.md`, and `WORKFLOWS.md`.
- Do not build code yet.

### Phase 1 - Data contracts and pure readiness engine

- Add typed models/constants for profiles, evidence items, statuses, requirement links, and readiness summary.
- Add a pure function that computes readiness from user-entered evidence plus a selected requirement set.
- Add offline tests for status computation, "unknown" handling, accepted-vs-uploaded separation, and no label-without-reasons summaries.
- No Telegram or web UI changes in this phase unless needed for tests.

### Phase 2 - Telegram manual MVP

- Add setup/check status flow for readiness profile.
- Add manual evidence entry and status update flow.
- Add compact gap summary and next-action list.
- Add export pack text generation with safety copy.
- Keep `/health` backward-compatible or migrate it behind a clear feature flag.

### Phase 3 - Assisted mapping and drafting

- Suggest SLO/KC/domain mappings from source text with excerpts and confidence labels.
- Draft reflection or supervisor discussion notes only from supplied source text.
- Require user confirmation before suggested mappings count as confirmed.

### Phase 4 - Future web dashboard

- Build full dashboard, checklist, evidence table, evidence editor, and export pack builder.
- Share the same data model and readiness engine with Telegram.
- Keep dense editing and archived cycles in web rather than Telegram.

### Phase 5 - Optional integrations after validation

- Consider document ingestion, calendar reminders, and limited import/export only after manual MVP proves demand.
- Any Kaizen import/scraping/browser work needs a separate approved safety spec and explicit user approval gates.

## First Implementation Slice

Build Phase 1 only:

1. Introduce machine-readable enums/models for readiness profile, evidence status, mapping confidence, requirement set, and readiness summary.
2. Implement a pure readiness computation module with no Telegram, Kaizen, browser, or network dependency.
3. Add focused offline tests for the safety-critical status rules.
4. Leave live bot behaviour unchanged.
