# Portfolio Health + Pathway Guidance — Product Spec v2.2

**Status:** Product spec. Replaces the narrower ARCP Health design (now superseded).
**Last updated:** 2026-09-03
**Supersedes:** `docs/ARCP_HEALTH_DESIGN.md` — retained as historical design artefact.

## Controlling Telegram UX decision (2026-09-03)

The everyday `/health` journey is action-first and deliberately minimal. A
doctor should understand one useful next step within five seconds: review
older unfinished drafts they control. Awaiting sign-off is secondary. Trust limits
stay concise, and system analysis or settings do not compete with those two
actions.

The queues preserve the existing 21-day inclusion filter. This is a neutral
triage threshold for highlighting older workflow items, not an overdue rule;
the counts do not claim to include every unfinished item.

This decision controls the current Telegram Health presentation wherever older
journey or surface descriptions in this document conflict with it. Assessment
calculations, evidence ordering, queue membership and the underlying detail
reports remain unchanged.

---

## Scope (Corrected 2026-06-01)

Portfolio Guru today serves **one platform (RCEM Kaizen) with two user groups**:

1. **EM Trainees** — on a training programme, annual ARCP, SLO/KC curriculum, stage-specific minimum counts
2. **CESR / Portfolio Pathway candidates** — non-training EM doctors using the same RCEM Kaizen platform to build evidence toward GMC specialist registration

Both groups file WPBAs into the same Kaizen. Both use RCEM's SLO/curriculum framework. What differs is: review cadence, evidence standard (KCs vs CiPs), and minimum requirements (annual counts vs 36-WPBA total).

Other pathways (GP, IMT, CST, SAS, foundation) are explicitly **out of scope for v1**. They will be added later when Portfolio Guru supports those platforms.

Full pathway research (including out-of-scope pathways for reference): `docs/roadmap/portfolio-pathways-research-2026-06.md`.

## Evidence Integrity Audit Addendum (2026-06-27)

The live `/health` feature must be treated as a source-grounded planning aid,
not an official readiness judgement. The report must disclose:

- **Scanned source** — read-only Kaizen index when available; otherwise
  Portfolio Guru filing history only.
- **Evidence window** — current implementation does not yet know the user's
  ARCP cycle month, LTFT extension, appraisal month, or target Portfolio
  Pathway application window.
- **Scan facts** — item count and source, refresh timestamp plus freshness or
  partiality, and whether the view is an indexed Kaizen scan or a limited local
  fallback. Do not collapse these separate facts into a confidence label.
- **Inference boundary** — missing domains are inferred from visible evidence
  in the scan and must not be presented as an official ARCP, Portfolio Pathway,
  appraisal, or revalidation outcome.

Source checks on 2026-06-27:

- RCEM Higher Training ARCP requirement guide is an official trainee source,
  but the PDF is not machine-readable through the current fetch path. Do not
  encode exact ARCP minimums from search summaries alone; verify against the
  PDF/manual extract before turning them into hard rules.
- RCEM's CESR/Portfolio Pathway page says the standard changed from CCT
  equivalence to demonstrating the Knowledge, Skills and Experience required
  for specialist registration, and that the framework reflects the 12 EM SLOs.
  It also emphasises ESLEs, core specialties, CPD, reflections, FRCEM, and
  using evidence collected for revalidation where relevant:
  https://rcem.ac.uk/certificate-of-eligibility-for-specialist-registration-cesr-and-combined-programme-cesr-cp/
- GMC Portfolio Pathway guidance says applicants have 24 months to submit once
  the application is opened, and that evidence gathering is a large undertaking
  that should be planned before submission:
  https://www.gmc-uk.org/registration-and-licensing/join-the-register/registration-applications/specialist-application-guides/specialist-registration-cesr-or-cegpr
- GMC Emergency Medicine Portfolio Pathway SSG (last updated 2025-02-04)
  frames assessment around Knowledge, Skills and Experience against the 12 EM
  SLOs. It notes LTFT/breaks may allow evidence from additional years or WTE,
  with gaps explained clearly. It also describes evidence such as FRCEM,
  core specialty experience, CPD with reflection, QI/service improvement,
  ESLEs, reflective case histories, courses, and structured evidence:
  https://www.gmc-uk.org/-/media/documents/sat---ssg--emergency-medicine-2021-curriculum---dc13727_pdf-87179601.pdf
- GMC revalidation supporting-information guidance is a separate non-training
  profile. Doctors must participate in annual appraisals covering whole
  practice and collect/reflect on six supporting information types over the
  revalidation cycle: CPD, quality improvement activity, significant events,
  patient/service-user feedback, colleague feedback, and compliments/complaints.
  Doctors in training usually have revalidation considered through ARCP, but
  still need supporting information for practice outside training posts:
  https://www.gmc-uk.org/registration-and-licensing/managing-your-registration/revalidation/guidance-on-supporting-information-for-revalidation/guidance-on-supporting-information-for-revalidation

## Status / copy consistency rule (2026-06-30)

Scope note (2026-09-01): this rule governs the legacy LLM-narrative ARCP
message only. The four `/health` views carry no score and no LLM narrative, so
there is nothing to reconcile — see "No universal health score" below.

The deterministic health score and the report's action copy must never
contradict each other. The ARCP report merges the LLM narrative's free-text
`suggestions` into the "Next 3 useful filing actions"; that text is reconciled
against the score before display (`bot._reconcile_action_severity`):

- A **Green** (or not-enough-data **Grey**) report must not contain urgent /
  urgently / critical missing-evidence phrasing. It must also never contain
  crisis/remediation framing — "recovery plan", "severe lack" / "severe lack of
  portfolio progression", "remediation", "crisis", or anything implying failing
  progression. Such crisis suggestions are replaced wholesale with a neutral
  confirmatory action ("keep your existing evidence recent and confirm coverage
  before your next review"). ESLE/SLO8 suggestions are reframed as
  optional/confirmatory ("consider logging an ESLE if it isn't already
  evidenced elsewhere"); other urgent phrasing has the urgency stripped.
- **Amber / Red** reports keep priority/urgent wording, because there the
  urgency matches the engine's verdict. If ESLE evidence is genuinely
  readiness-affecting, the engine's domain coverage already lands the score at
  Amber or below, which is where priority ESLE wording belongs.

This keeps the rule deterministic: urgency is derived from the score, never from
the non-deterministic LLM phrasing alone.

Product consequence: `/health` should evolve into at least three profile
templates, not one universal red/amber/green report:

1. **Training (CCT) ARCP cycle** — requires training stage, ARCP month, cycle
   start/end, FT/LTFT/WTE context, curriculum version, and source-labelled
   minimums.
2. **Portfolio Pathway / CESR** — multi-year SLO/KSE evidence map, target
   application window, evidence currency, core specialty breadth, ESLE/CPD/QI/
   reflection/structured-report coverage.
3. **Annual appraisal / revalidation** — whole-practice annual appraisal view
   based on GMC supporting information, separate from ARCP and Portfolio
   Pathway readiness.

Career Guru remains a fourth, separate strategic layer. It should not reuse the
ARCP/CESR/appraisal verdict labels unless the user explicitly chooses that
profile.

---

## Product Decision

Portfolio Health and Pathway Readiness are two layers:

**Portfolio Health** = the universal evidence tracker. It answers: "What did
this scan see, what is unfinished, what can I act on myself, and what is waiting
on somebody else?"

**Pathway Guidance** = two RCEM views on the same Kaizen data:

- **ARCP view** — training-stage-specific, SLO/KC-level mapping, annual counts, ARCP date countdown
- **CESR view** — SLO/CiP-level mapping, 36-WPBA tracker, 5-year evidence window, equivalence signal

The original `/health` feature shipped a hardcoded RCEM SLO/KC radar chart for trainees. This spec adds the CESR view and separates health tracking from pathway interpretation.

---

## Architecture

```
                    ┌──────────────────────────┐
                    │    Pathway Guidance       │  ← RCEM-specific views
                    │  ARCP (trainee) │ CESR    │
                    └──────────┬───────────────┘
                               │ interprets same evidence
                    ┌──────────▼───────────────┐
                    │    Portfolio Health       │  ← universal base
                    │  evidence inventory,      │
                    │  domain coverage,         │
                    │  status tracking          │
                    └──────────────────────────┘
```

Same RCEM Kaizen evidence, two different readiness views. Switching between ARCP and CESR re-interprets the same inventory.

---

## Layer 1 — Portfolio Health (Universal)

### What it tracks

Six universal evidence domains (mapped from the pathway research — every UK doctor needs these regardless of stage):

| Domain                      | What counts                                   | Evidence types                                                                                       |
| --------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| **Clinical**                | Direct patient care evidence                  | WPBAs (CBD, DOPS, Mini-CEX, etc.), procedure log, clinical cases, ESLEs, reflections on cases        |
| **CPD & Learning**          | Continuing professional development           | Courses, conferences, e-learning, journal club, formal study, exams (FRCEM, MRCP, etc.)              |
| **Quality Improvement**     | Audit, QI projects, service improvement       | Audit cycles, QIAT, QIP, guideline development, pathway redesign                                     |
| **Teaching**                | Teaching and training others                  | Formal teaching sessions, course delivery, bedside teaching, feedback received, course organisation  |
| **Leadership & Management** | Leadership, governance, management activities | Rota management, committee membership, complaint handling, risk/governance work, management projects |
| **Reflection**              | Reflective practice                           | Reflective logs, case reflections, incident reflections, feedback reflections, career reflections    |

### What it shows

- **Evidence inventory** — what you have, by domain and type, with dates
- **Domain coverage** — which domains are populated, which are thin or empty
- **Filing cadence** — are you filing regularly or in bursts?
- **Evidence status** — drafted, filed/saved, reviewed by supervisor, accepted
- **Age of evidence** — recent (<1 year), current (1–3 years), ageing (3–5 years), stale (>5 years)
- **Source** — Kaizen-filed, Portfolio Guru-drafted (not filed), manually entered, uploaded

### How evidence gets in

Both RCEM pathways use the same Kaizen platform. Evidence comes from:

1. **Auto-discovered** — Portfolio Guru drafts that were filed to Kaizen are tracked automatically (existing `usage` / `case_archive` data)
2. **Auto-discovered** — Portfolio Guru drafts that were previewed but not filed
3. **Manually entered** — user types or pastes a summary ("Attended ALS course, Dec 2025", "Led rota redesign, Jan 2026")
4. **Future** — file upload/ingestion (PDFs, certificates)

Evidence from paths 1 and 2 is source-tied (linked to the original case text/draft). Evidence from path 3 is user-entered.

### Scan cadence and caching

Product decision, 2026-06-30:

- **Monthly automatic Portfolio Health** is the default proactive cadence.
- **Manual `/health` remains available any time** and should reuse the cached
  Kaizen index when it is fresh enough for the report.
- **Weekly automatic health checks are reserved for deadline mode**, for
  example 6–8 weeks before a known ARCP date or Portfolio Pathway application
  target.
- **Do not full-scan the same Kaizen portfolio on every health run.** Keep a
  cached read-only Kaizen index, dedupe evidence by stable event identity, and
  only trigger a full scan when the index is missing, stale, manually requested,
  or deadline mode requires a newer view.
- **After new Portfolio Guru filings**, update the local Portfolio Guru filing
  history immediately; do not trigger a full Kaizen scrape unless the cached
  index is stale or the user explicitly asks for a refresh.
- The health report should show the last successful scan time and the source
  used, so users can trust whether they are seeing a full Kaizen scan or a
  limited local view.
- **Activity snapshot SLO coverage is source-honest.** When the read-only
  Kaizen index already carries curriculum/KC tags (`linked_kc_tags`), derive
  SLO coverage from those indexed rows and label it as full indexed Kaizen
  coverage ("`x/12 SLOs visible in indexed Kaizen KC links`"). Only when no
  indexed KC tags exist does it fall back to the Portfolio Guru-linked wording
  ("touched by Portfolio Guru-linked evidence … not your full Kaizen
  strength"). This derivation reuses already-indexed rows and never triggers a
  sync or scrape just to draw the line.

### No pathway assumptions

Portfolio Health knows nothing about ARCP dates, RCEM SLOs, CESR requirements, or training stages. It is pure evidence inventory. This is the key architectural difference from the original ARCP Health spec.

### No universal health score (superseded 2026-09-01)

The universal layer previously carried a green/amber/red/grey signal. It no
longer does, and `compute_health_assessment` no longer computes one.

A colour is a readiness claim, and this layer verifies nothing against any
pathway's rules: the same evidence means different things to an ST4, a CESR
applicant and an SAS doctor. What it can state honestly are facts about the
scanned evidence — what is unfinished and since when, what each domain holds
and how recently, how the portfolio compares with itself, and what the scan
could not see. Those are what the views render.

Requirement counters still exist, but only where a **verified pathway overlay**
supplies them (Layer 2), labelled as that pathway's own rule. With no overlay,
nothing pathway-specific is rendered at all — an empty counter would read as an
unmet requirement to a doctor whose pathway has no such rule.

Related rules the universal layer holds to:

- The default `What to do next` view shows `Review your drafts` first and
  `Waiting on others` second, with their visible counts and no ranking,
  coverage, curriculum, scan-detail or review-setting copy.
- A partial or freshness-unconfirmed scan keeps one concise explicit
  limitation on the default view rather than implying completeness.
- Old evidence is named with its exact date and offered for review. Nothing is
  called overdue or stale, and no chase is instructed: no scanned field carries
  a deadline. Older drafts are neutrally described as potentially no longer
  worth completing.
- Coverage shows each of the six core category totals with last-12-month
  activity in its legacy-compatible detail report. It does not issue a
  largest-versus-smallest domain warning.
- The denominator is explicit: evidence outside the six core categories stays
  in the scanned-item total and is stated separately rather than disappearing.
- Curriculum spread is computed over tagged items only. Tagged and untagged
  scope is stated together, evidence types that cannot carry tags are named as
  outside the comparison, and `12/12 SLOs represented` explicitly says that
  presence does not assess adequacy.

---

## Layer 2 — Pathway Guidance (Selectable Overlay)

### How it works

The user selects a pathway. The pathway layer:

1. Loads the relevant framework (SLOs/KCs, GMC domains, person specification, etc.)
2. Maps the user's Portfolio Health evidence against that framework
3. Shows what's covered, what's missing, what's recommended
4. Provides pathway-specific deadlines, minimum counts, and readiness signals

Switching pathways re-interprets the same evidence. No data loss.

### Pathway 1 — Training / ARCP (RCEM)

**Who:** EM trainees (ACCS, CT, ST) on a Kaizen training programme

**Framework:** RCEM 2025 curriculum — 12 SLOs with KCs, plus ARCP minimum-count rules

**Overlay shows:**

- SLO coverage map (which KCs have evidence)
- ARCP minimums tracker (ESLEs: 3/yr, MSF: 1/yr, CSR: 1/placement, QIAT: 1/yr, etc.)
- Stage-specific requirements (intermediate vs higher)
- ARCP readiness signal with concrete reasons
- Countdown to ARCP date (user-entered)
- Supervisor meeting prep summary

**What's already built:** The current `/health` chart is a hardcoded version of this. The radar chart and KC coverage tracking can be reused — they just need to be gated behind the pathway selector rather than being the default view.

**Non-goal:** No automated ARCP submission, no guarantee of outcome, no scraping of deanery deadlines.

### Pathway 2 — CESR / Portfolio Pathway

**Who:** Non-training doctors seeking GMC specialist registration via the Portfolio Pathway (formerly CESR). EM but not in training programme. Includes trust grades, clinical fellows, SAS doctors aiming for consultant posts.

**Framework:** GMC Specialty Specific Guidance (SSG) for Emergency Medicine, mapped to RCEM curriculum high-level outcomes (CiPs/SLOs), plus RCEM's specific evidence requirements.

**Specific requirements (RCEM, from research):**

- Minimum 36 WPBAs: 12 DOPS + 12 Mini-CEX + 12 CBDs
- ESLEs across core specialties
- CPD + reflections
- FRCEM encouraged but not mandatory
- Evidence within last 5 years preferred
- Structured reports from consultants
- Specialist medical qualification + ≥6 months specialist training
- 24-month window once GMC application opened

**Overlay shows:**

- Evidence coverage against RCEM SLOs/CiPs (not individual KCs — CESR is assessed at higher level)
- WPBA count tracker (toward 36 minimum)
- Domain balance (are they heavy on CBDs but light on DOPS?)
- Age of evidence — flag items approaching the 5-year window
- Structured report coverage
- "Evidence equivalence" signal — how close to demonstrating consultant-level KSE
- Application readiness checklist

**Key difference from ARCP:** No annual deadline. No training programme. Self-directed. The anxiety is "is this the right evidence?" not "did I file enough by the deadline?"

**Non-goal:** No GMC application submission, no guarantee of CESR success, no claim that the evidence is complete without reviewer input.

### Future Pathways (v2+)

Other pathways (GP, IMT, CST, SAS, foundation) will be added when Portfolio Guru supports those platforms. Currently out of scope.

---

## User Journey

### Everyday Health

1. The doctor opens `/health` or the Portfolio Health button.
2. Health refreshes read-only evidence when required, then opens `What to do
   next`.
3. `Review your drafts` is the first action and opens the doctor-owned draft
   queue at its first page (`page 0` in the callback contract).
4. `Waiting on others` opens the independent awaiting-sign-off queue at its
   first page (`page 0` in the callback contract).
5. `About` explains only the source, workflow-state provenance, scan limits,
   automated-classification limit and read-only judgement boundary.
6. Every queue and About returns directly to Health. Pagination never changes
   the other queue's page.

Pathway selection, review-month settings and system analysis are outside this
everyday journey. Existing typed commands and settings routes remain separate;
switching a pathway still reinterprets the same evidence without deleting it.

---

## Data Model

Extends the existing Portfolio Guru data model. Portfolio Health is a new layer, not a replacement.

### `health_profiles`

One per user. Stores the selected pathway and pathway-specific config.

```text
id
user_id
pathway                    training_arcp | cesr_portfolio | sas_career | trust_grade_app | generic
pathway_config             JSON — pathway-specific fields (training_stage, arcp_date, target_specialty, etc.)
created_at
updated_at
```

### `evidence_items`

Manual and auto-discovered evidence records. Separate from the ARCP Health spec's evidence_items — these are simpler and pathway-agnostic.

```text
id
user_id
domain                    clinical | cpd | qi | teaching | leadership | reflection
evidence_type             wpba | course | audit | teaching_session | project | reflection_log | other
form_type                 nullable — CBD, DOPS, etc. if WPBA
title
summary
event_date
source                    kaizen_filed | pg_draft | manual_entry | file_upload
source_ref                link to PG draft, Kaizen URL, or null
status                    drafted | filed | reviewed | accepted | needs_work
created_at
updated_at
```

### `pathway_mappings`

Links evidence to pathway framework items. Different mapping sets per pathway.

```text
id
evidence_item_id
pathway                   training_arcp | cesr_portfolio
framework_item            e.g. "SLO3 KC1", "GMC_CPD", "PUBLICATION_DOMAIN"
mapping_source            auto | user_confirmed
confidence                high | medium | low | needs_confirmation
created_at
```

### `health_snapshots`

Computed summary, cached for performance.

```text
id
user_id
computed_at
pathway
health_score              green | amber | red | grey
domain_counts             JSON — counts per domain
pathway_readiness         JSON — pathway-specific readiness data
gap_summary               JSON — top gaps with reasons
next_actions              3–5 concrete suggested actions
```

---

## Surfaces

### Telegram (MVP)

- `/health` opens `What to do next`. Its text contains only:
  - `Review your drafts — N`, explaining that these are unfinished items the
    doctor controls and that older drafts may no longer be worth completing;
  - `Waiting on others — N`, advising review only when follow-up is still
    needed;
  - one concise read-only planning-aid boundary; and
  - one concise explicit partial/freshness limitation when applicable.
- The exact landing keyboard is:
  - first full-width row: `📝 Review drafts (N)`;
  - second row: `⏳ Awaiting (N)` and `ℹ️ About`.
- Draft and Awaiting queues open independently at their first page (`page 0` in
  callback data), paginate independently at five items per page, and retain
  direct Kaizen links. Their only
  non-pagination control is `🔙 Health`.
- `ℹ️ About` contains only the indexed/read-only source scope, the fact that
  draft and awaiting counts come from visible Kaizen workflow states,
  partial/stale limitations, the automated-classification limitation, the
  no-edit/file/chase/delete boundary, and the fact that Health is not a formal
  training or appraisal judgement. Its only control is `🔙 Health`.
- Actions, More, Coverage, Curriculum, Scan info and Review month are absent
  from new everyday navigation.

- `/pathway` — select or change pathway
- `Add evidence` button — quick manual entry flow
- After each WPBA filing → "Evidence added to Portfolio Health. [View health]"
- Weekly nudge (already exists) → enhanced with health context

Navigation is contextual. Evidence views are rendered once per scan and stored,
so a button press never re-derives them and paging cannot shift items between
pages. Buttons on messages older than this layout remain safe: legacy Actions,
combined `health_page`, `health_detail`, direct Coverage, Curriculum and Scan
callbacks still render their stored reports where practical, but those detail
views are legacy-only and offer only `🔙 Health`. An old `health_view|more`
callback maps to the new About view and its single Back control instead of
recreating the removed menu. A report this chat no longer holds offers
`🔄 Refresh health` rather than a dead end.

Legacy review-month callbacks and the typed `/arcp <month> <year>` route remain
compatible. Selecting a month only previews it; the existing health-profile
storage path is called only after explicit confirmation, and no selected month
or year enters telemetry. These controls are not exposed through the new Health
navigation.

Health interaction telemetry uses the existing PHI-free funnel logger. It may
record only allowlisted structural values for pane, queue, page and review-month
selection/confirmation events. It does not retain the chosen month or year. It
must never record message text, portfolio content, Kaizen links, credentials,
or any new raw identifier.

### Future Web Dashboard

- Full evidence table with filters and search
- Domain detail views
- Historical snapshots (how has health changed over time?)
- Export/summary for supervisor meetings or appraisals
- Dense editing — bulk status updates, re-mapping

---

## Safety Boundaries

- Portfolio Health is a planning aid. It does not guarantee ARCP, CESR, revalidation, or application success.
- Never invent clinical details, dates, supervisors, or evidence status.
- Framework requirements (ARCP counts, CESR minima, application criteria) are curated/preset, not scraped. They must be labelled with their source and last-reviewed date.
- "Accepted by supervisor" is always manual — never auto-inferred.
- Unknown or unconfirmed framework items show "needs confirmation", not a false positive.
- Pathway switching never deletes evidence.
- No automated submission to Kaizen, GMC, deanery, or recruitment portal.
- Clinical content, supervisor names, and patient details must not appear in analytics or health snapshots.
- Health funnel events contain structural navigation/setup metadata only; no
  message text, portfolio content, link, credential or new identifier is
  permitted.

---

## Implementation Phases

### Phase 1 — Spec and architecture (current)

- [x] Pathway research (`docs/roadmap/portfolio-pathways-research-2026-06.md`)
- [x] This spec — Portfolio Health + Pathway Guidance v2
- [ ] Deprecate `ARCP_HEALTH_DESIGN.md` — add retirement header pointing here
- [ ] Data model contracts in code (typed models, no I/O)
- [ ] Pure Portfolio Health engine — computes domain coverage and health score from evidence items
- [ ] Offline tests for health scoring logic

### Phase 2 — Refactor existing `/health`

- [ ] Extract the current hardcoded RCEM SLO/KC chart behind a pathway gate
- [x] Add pathway selector: Training (CCT) / CESR / Portfolio Pathway
      (ARCP is a checkpoint inside Training/CCT, not a standalone pathway)
- [x] Training (CCT) view: ARCP readiness check — ARCP risk, why, next
      3 urgent filing actions, strong/missing domains (KC radar + counts
      still pending)
- [x] CESR view: long-term Portfolio Pathway evidence plan — 36-WPBA
      tracker with DOPS/Mini-CEX/CBD breakdown, 3–12 month yearly action
      plan, domain balance, 5-year evidence-window framing (full SLO/CiP
      mapping still pending)
- [ ] Auto-populate from the Kaizen Portfolio Index (read-only sync) as the
      primary source; fall back to existing PG filing activity
      (`usage` / `case_archive`) when no index run is present yet, and to
      manual entry as today. Index contract and schema live in
      `docs/roadmap/kaizen-mapping-sprint-2026-06.md` → "First build slice —
      Kaizen Portfolio Index v1".
- [x] Real Kaizen display-name canonicalisation for Portfolio Health:
      versioned/long labels such as DOPS, Mini-CEX, CBD, educational activity,
      teaching, reflections, supervisor reports, documents and file uploads now
      map deterministically; unknown labels fail closed into an unscored
      `unclassified` bucket instead of inflating clinical evidence.
- [ ] No write to Kaizen. The Index is read-only; ingestion stays consent-
      and session-scoped per `docs/roadmap/kaizen-mapping-sprint-2026-06.md`
      → "Safety boundaries (slice)".

### Phase 3 — Manual evidence entry

- [ ] Quick-add flow for non-WPBA evidence (courses, teaching, QI, leadership)
- [ ] Evidence list view
- [ ] Status management (drafted → filed → reviewed → accepted)
- [ ] Domain-based gap analysis

### Phase 4 — Pathway-specific readiness

- [ ] ARCP: minimum-count trackers, training-stage-specific requirements, ARCP date countdown
- [ ] CESR: WPBA count toward 36, SLO/CiP evidence-equivalence signal, structured report coverage

### Phase 5 — Web dashboard (post-PMF)

---

## First Build Slice

Build Phase 1 only:

1. Deprecate `ARCP_HEALTH_DESIGN.md`
2. Add typed data models for `health_profiles`, `evidence_items`, `pathway_mappings`, `health_snapshots`
3. Implement a pure Portfolio Health engine — no Telegram, Kaizen, browser, or network dependency
4. Add offline tests for health scoring, domain coverage, and pathway-agnostic computation
5. Leave live bot behaviour unchanged

---

## Supersedes

`docs/ARCP_HEALTH_DESIGN.md` — the original ARCP Health / Portfolio Readiness spec. That spec conflated ARCP readiness with portfolio health and assumed a training-only audience. This v2 spec separates Portfolio Health from Pathway Guidance and adds the CESR view for RCEM non-training doctors.
