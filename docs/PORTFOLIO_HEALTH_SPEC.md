# Portfolio Health + Pathway Guidance — Product Spec v2

**Status:** Product spec. Replaces the narrower ARCP Health design (now superseded).
**Last updated:** 2026-06-30
**Supersedes:** `docs/ARCP_HEALTH_DESIGN.md` — retained as historical design artefact.

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
- **Confidence** — high only when a fresh Kaizen index and confirmed pathway
  are present; lower when the report falls back to Portfolio Guru history or a
  default pathway.
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

The deterministic health score and the report's action copy must never
contradict each other. The ARCP report merges the LLM narrative's free-text
`suggestions` into the "Next 3 useful filing actions"; that text is reconciled
against the score before display (`bot._reconcile_action_severity`):

- A **Green** (or not-enough-data **Grey**) report must not contain urgent /
  urgently / critical missing-evidence phrasing. ESLE/SLO8 suggestions are
  reframed as optional/confirmatory ("consider logging an ESLE if it isn't
  already evidenced elsewhere"); other urgent phrasing has the urgency stripped.
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

**Portfolio Health** = the universal evidence tracker. It answers: "What evidence do I have? What's missing? What domains are thin?"

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

### No pathway assumptions

Portfolio Health knows nothing about ARCP dates, RCEM SLOs, CESR requirements, or training stages. It is pure evidence inventory. This is the key architectural difference from the original ARCP Health spec.

### Health score

A simple, universal health signal independent of any pathway:

- **Green — Well covered:** evidence in 5–6 domains, balanced, recent items, regular cadence
- **Amber — Needs attention:** 3–4 domains, some gaps, or ageing evidence
- **Red — Thin:** ≤2 domains, large gaps, or mostly stale evidence
- **Grey — Unknown:** not enough data entered yet

The health score is always shown with the concrete reasons. Never a label alone.

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

### First-time setup

1. User opens Portfolio Health (`/health` or button)
2. If no profile exists: "Welcome to Portfolio Health. I'll help you track your evidence and understand what's missing. Are you on a training programme or working toward CESR?"
3. Pathway selector: Training (CCT) / CESR / Portfolio Pathway. ARCP is a
   yearly review checkpoint inside the Training (CCT) pathway, not a
   pathway in its own right.
4. User selects pathway → optional details (training stage + ARCP date for trainees; target application window for CESR)
5. Initial scan of existing PG activity populates evidence inventory
6. Health summary shows: domain coverage, recent activity, pathway-specific readiness

### Ongoing use

- `/health` → quick summary with pathway overlay
- `/health domains` → domain breakdown
- `/health gaps` → what's missing, what to file next
- `Add evidence` → manual entry of CPD, teaching, QI, leadership items
- After each filing → health updates automatically (existing flow, enhanced)

### Pathway switching

- `/pathway` → select or change between ARCP and CESR views
- Switching re-interprets the same evidence, no data loss
- A CESR candidate who later enters training can switch to ARCP view

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

- `/health` — compact summary card: health score, domain bar, top 2 gaps, next action
- `/health domains` — domain breakdown with counts and dates
- `/health gaps` — what's missing, ordered by impact
- `/pathway` — select or change pathway
- `Add evidence` button — quick manual entry flow
- After each WPBA filing → "Evidence added to Portfolio Health. [View health]"
- Weekly nudge (already exists) → enhanced with health context

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
