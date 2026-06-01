# Portfolio Health + Pathway Guidance — Product Spec v2

**Status:** Product spec. Replaces the narrower ARCP Health design (now superseded).
**Last updated:** 2026-06-01
**Supersedes:** `docs/ARCP_HEALTH_DESIGN.md` — retained as historical design artefact.

---

## Product Decision

Portfolio Health and ARCP Readiness are two separate products that were conflated in the original spec. This spec separates them and adds a universal Pathway Guidance layer.

**Portfolio Health** = the universal evidence tracker. It answers: "What evidence do I have? What's missing? What domains are thin?"

**Pathway Guidance** = the interpretation layer on top. Same evidence, different lens depending on what you're building toward: ARCP, CESR, SAS appraisal, or specialty training application.

The original `/health` feature shipped a hardcoded RCEM SLO/KC radar chart. That was the wrong abstraction — it baked a single pathway into the base layer. This spec corrects that.

Full pathway research backing this spec: `docs/roadmap/portfolio-pathways-research-2026-06.md`.

---

## Architecture

```
                    ┌──────────────────────┐
                    │   Pathway Guidance    │  ← selectable overlay
                    │ ARCP │ CESR │ SAS │.. │
                    └──────────┬───────────┘
                               │ interprets same evidence
                    ┌──────────▼───────────┐
                    │   Portfolio Health    │  ← universal base
                    │  evidence inventory,  │
                    │  domain coverage,     │
                    │  status tracking      │
                    └──────────────────────┘
```

One user, same evidence inventory, different readiness view depending on selected pathway. Switching pathways does not re-enter evidence — it re-interprets it.

---

## Layer 1 — Portfolio Health (Universal)

### What it tracks

Six universal evidence domains (mapped from the pathway research — every UK doctor needs these regardless of stage):

| Domain | What counts | Evidence types |
|---|---|---|
| **Clinical** | Direct patient care evidence | WPBAs (CBD, DOPS, Mini-CEX, etc.), procedure log, clinical cases, ESLEs, reflections on cases |
| **CPD & Learning** | Continuing professional development | Courses, conferences, e-learning, journal club, formal study, exams (FRCEM, MRCP, etc.) |
| **Quality Improvement** | Audit, QI projects, service improvement | Audit cycles, QIAT, QIP, guideline development, pathway redesign |
| **Teaching** | Teaching and training others | Formal teaching sessions, course delivery, bedside teaching, feedback received, course organisation |
| **Leadership & Management** | Leadership, governance, management activities | Rota management, committee membership, complaint handling, risk/governance work, management projects |
| **Reflection** | Reflective practice | Reflective logs, case reflections, incident reflections, feedback reflections, career reflections |

### What it shows

- **Evidence inventory** — what you have, by domain and type, with dates
- **Domain coverage** — which domains are populated, which are thin or empty
- **Filing cadence** — are you filing regularly or in bursts?
- **Evidence status** — drafted, filed/saved, reviewed by supervisor, accepted
- **Age of evidence** — recent (<1 year), current (1–3 years), ageing (3–5 years), stale (>5 years)
- **Source** — Kaizen-filed, Portfolio Guru-drafted (not filed), manually entered, uploaded

### How evidence gets in

Four paths, no Kaizen dependency for the base layer:

1. **Auto-discovered** — Portfolio Guru drafts that were filed to Kaizen are tracked automatically (existing `usage` / `case_archive` data)
2. **Auto-discovered** — Portfolio Guru drafts that were previewed but not filed
3. **Manually entered** — user types or pastes a summary ("Attended ALS course, Dec 2025", "Led rota redesign, Jan 2026")
4. **Future** — file upload/ingestion (PDFs, certificates)

Evidence from paths 1 and 2 is source-tied (linked to the original case text/draft). Evidence from path 3 is user-entered.

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

### Pathway 3 — SAS / Career Grade

**Who:** SAS doctors (Specialty Doctor, Associate Specialist, Staff Grade) on NHS contracts. Annual appraisal + 5-year revalidation cycle.

**Framework:** GMC 6-domain revalidation framework + NHS SAS development guidance

**Overlay shows:**
- GMC domain coverage (CPD, QI, significant events, patient feedback, colleague feedback, complaints/compliments)
- Appraisal readiness — is the portfolio ready for annual appraisal?
- Revalidation cycle tracking
- SAS-specific: clinical leadership evidence, service development, management contributions
- Specialist Doctor grade progression — additional evidence needed beyond revalidation

**Non-goal:** No appraisal submission, no guarantee of revalidation.

### Pathway 4 — Trust Grade / Application Prep

**Who:** Clinical fellows, trust grades, FY3+, locum doctors building toward specialty training applications. No fixed curriculum.

**Framework:** Person specification scoring domains for EM specialty training applications (publications, teaching, QI, additional degrees, commitment to specialty, etc.)

**Overlay shows:**
- Domain coverage against EM ST1/ST3 application scoring criteria
- What's missing for maximum points
- Evidence gaps that could be filled with current clinical work
- Timeline: application windows, evidence deadlines
- "Application competitiveness" signal (conservative, clearly caveated)

**Non-goal:** No guarantee of shortlisting or interview, no claim to predict scoring.

### Future Pathways (post-PMF)

- **GP trainees** — RCGP curriculum + WPBA requirements. Lower priority — FourteenFish dominates GP.
- **Foundation** — FPCs + Horus/Turas. Low priority — Foundation portfolio is simpler, existing tools adequate.
- **IMT/CST/other specialties** — Extension beyond EM. Requires per-specialty curriculum mapping.

---

## User Journey

### First-time setup

1. User opens Portfolio Health (`/health` or button)
2. If no profile exists: "Welcome to Portfolio Health. I'll help you track your evidence and understand what's missing. First — what are you working towards?"
3. Pathway selector appears: Training (ARCP) / CESR / Portfolio Pathway / SAS / Career grade / Trust grade / Application prep / Just track my evidence (no pathway)
4. User selects pathway → optional details appear (training stage, ARCP date, etc.)
5. Initial scan of existing Portfolio Guru activity populates evidence inventory
6. Health summary shows: domain coverage, recent activity, pathway-specific readiness

### Ongoing use

- `/health` → quick summary with pathway overlay
- `/health domains` → domain breakdown
- `/health gaps` → what's missing, what to file next
- `Add evidence` → manual entry of CPD, teaching, QI, leadership items
- After each filing → health updates automatically (existing flow, enhanced)

### Pathway switching

- `/pathway` → select or change pathway
- Switching pathways does not delete evidence — it re-interprets it
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
pathway                   training_arcp | cesr_portfolio | sas_career | trust_grade_app
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
- [ ] Add pathway selector (Training / CESR / SAS / Trust grade / Generic)
- [ ] For non-training pathways, show domain-based health summary instead of SLO chart
- [ ] Keep the existing KC radar for Training/ARCP path
- [ ] Auto-populate from existing PG filing activity
- [ ] No Kaizen scrape, no new data sources

### Phase 3 — Manual evidence entry

- [ ] Quick-add flow for non-WPBA evidence (courses, teaching, QI, leadership)
- [ ] Evidence list view
- [ ] Status management (drafted → filed → reviewed → accepted)
- [ ] Domain-based gap analysis

### Phase 4 — Pathway-specific readiness

- [ ] ARCP: minimum-count trackers, SLO coverage %, ARCP date countdown
- [ ] CESR: WPBA count tracker (toward 36), 5-year evidence age warnings, evidence-equivalence signal
- [ ] SAS: GMC domain coverage, appraisal readiness
- [ ] Trust grade: application scoring domain coverage

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

`docs/ARCP_HEALTH_DESIGN.md` — the original ARCP Health / Portfolio Readiness spec. That spec conflated ARCP readiness with portfolio health, was RCEM/Kaizen-specific, and assumed a training-only audience. This v2 spec replaces it. The original is retained for historical reference.
