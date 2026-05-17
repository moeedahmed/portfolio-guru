# Kaizen RCEM Portfolio — Data Model

The model below was captured by walking the live site as Moeed Ahmed (Higher Trainee 2025 Update, Kingston Hospital) on 2026-05-16. Every entity name, field label, and count is copied verbatim from the rendered DOM, not inferred.

## Entities

```
User (trainee)
 ├── Locations           (e.g. Kingston Hospital — 6 Aug 2025 to 4 Aug 2026)
 ├── Programme           (e.g. Higher Specialty Training Programme — 6 Aug 2024 to 5 Aug 2031)
 ├── FTE %               (e.g. 100% from 6 Aug 2025)
 ├── Curricula attached  (Higher EM 2025 Update / Intermediate 2021 / Higher 2021 / PEM)
 └── Timeline
      └── Event                  (one row in /events/list/{category})
           ├── EventType         (e.g. "CBD - Case Based Discussion (2025 update)")
           ├── State             (Complete / Pending / Submitted / Draft / Merged)
           ├── Owner             (trainee)
           ├── Date occurred on
           ├── End date
           ├── Description       (optional free text)
           ├── Section[]         (each section = a Formly form filled by one party)
           │    ├── Section UUID (used in /events/view-section/{uuid})
           │    ├── Filled in by (Moeed Ahmed / assessor / supervisor)
           │    ├── Filled in on (date)
           │    ├── Field[]      (id is a stable UUID per event-type version)
           │    │    ├── Label
           │    │    ├── Value   (string, date, single-select, multi-select, textarea, file ref)
           │    │    └── Required (.icon-star)
           │    └── Tags         (.event-tag chips — one per linked KC/target)
           ├── Linked targets    (KCs in goalCtrl.goal.targets)
           ├── Linked files      (parent of File entries on /files)
           └── Audit log         (versioned — "version 35", "Show audit log")
```

A Curriculum is a tree:

```
Curriculum (e.g. Higher EM 2025 Update)
 └── SLO (Specialty Learning Outcome, e.g. "Higher SLO1: Care for physiologically stable adult patients…")
      └── KC (Key Capability — a Target on the SLO; e.g. "Higher SLO1 Key Capability 1: Be expert in assessing and managing…")
           ├── Linked events (count badge per KC)
           └── Link conditions ("How are events linked?" — defines which event-types/tags satisfy it)
```

Reports are projections of the Curriculum tree:

```
Report Template (e.g. "ACCS LO4 - Entrustment ratings only - (2021/2025 Update)")
 └── Nodes (one per SLO/KC, rendered as ng-repeat="node in nodes track by node._id")
      └── States (entrustment ratings, link counts, evidence summary)
```

## Event Types — verified inventory

The user has these types in their timeline (counts are total entries on this account, 2026-05-16):

### Assessments (138 items)

- CBD - Case Based Discussion (2025 update)
- DOPS - (ST3-ST6 - 2025 update)
- EM QIAT (2025 Update - v7)
- Leadership Assessment Tool - LAT (2025 Update - v9)
- Mini-CEX (2025 Update)

### Training Post & Supervisor (10 items)

- Add a Post
- Add a Supervisor

### Educational Review & Meetings (19 items)

- ARCP Form\*
- Educational Supervisor Report - ST4-ST6 - (Higher Specialty Training - 2025 Update)
- Educational Meeting: Supplementary Review
- FEGS - ST4 & ST5
- Educational Meeting ST3 - ST7
- End of Placement Report ST4-ST6 (Higher Training stage)

### Progression (1 item)

- Higher Progression form (trainees Higher – ST4-ST6 only) - 2023

### Procedural Logs (52 items)

- Procedural Log - ST3-ST6 (2025 Update)

### Reflections (85 items)

- Reflection on ESLE (2025 Update)
- Ultrasound Case Reflection (2025 Update)
- Personal Development Plan (PDP - 2021/2025)

### MSF (30 items)

- MSF: Multi-Source Feedback (parent)
- Section of MSF: Multi-Source Feedback for {rater-name} (one per rater)

### Teaching & Education (91 items)

- Educational Activity Attended (2025 Update)
- Teaching Delivered By Trainee (Update 2025)

### Research, Audit & QI (7 items)

- EM QIAT (2025 Update - v7)
- Presentation at a Journal Club (JCF - 2021)
- Research Activity
- QIAT (EM ST4 - ST6)

### Manage, Administer & Lead (29 items)

- Management Experience (2021/2025)
- Management: Project Record (2021/2025)
- Leadership Assessment Tool - LAT (2025 Update - v9)
- Introduction of Equipment or Service (2021/2025)
- Management: Writing a Report (2021/2025)
- Critical Incident (2021/2025)

### e-Learning (30 items)

- RCEM Learning (one per module)

### Exams (3 items)

- Exam Result: {component}

### Documents (41 items)

- File UPLOAD - miscellaneous (2025 Update)
- File UPLOAD (miscellaneous)

### Absence / CCT (0 items on this account)

## Field shapes (verified from real entries)

Each event-type version defines its own Formly schema. Field IDs are UUIDs stable for that version; labels are stable across versions of the same type.

### CBD - Case Based Discussion (2025 update)

| Label                                                        | Type                 | Required | Field id (verified)                       |
| ------------------------------------------------------------ | -------------------- | -------- | ----------------------------------------- |
| Date occurred on                                             | date                 | yes      | `startDate`                               |
| End date                                                     | date                 | yes      | `endDate`                                 |
| Description (optional)                                       | textarea             | no       | `event-description`                       |
| Stage of training                                            | select               | yes      | `e0864e88-62cf-43aa-a9e5-51abd98a1cce`    |
| Date of event                                                | date                 | yes      | `5391f8de-de63-4db3-9e08-baaa2a380cfe`    |
| Case to be discussed                                         | textarea             | yes      | `60772a97-92eb-4dbe-a813-6a5293be82f9`    |
| Reflection of event                                          | textarea             | yes      | `610b5c60-99ac-4902-9407-22974d6a5799`    |
| Attach files                                                 | file                 | no       | (Upload button)                           |
| Who would you like to fill in the next section of this form? | typeahead (assessor) | yes      | `invites` (name `{section-uuid}:invites`) |
| Comment                                                      | textarea             | no       | (assessor section)                        |

### Procedural Log - ST3-ST6 (2025 Update)

| Label                                                  | Type                   | Notes                                                     |
| ------------------------------------------------------ | ---------------------- | --------------------------------------------------------- |
| Date of Activity                                       | date                   |                                                           |
| Stage of training                                      | select                 | e.g. `Higher / ST4 - ST6`                                 |
| Year of training                                       | select                 | `ST5`                                                     |
| ST4-ST6 Higher EM procedural skills list (2025 update) | single-select          | from the 17-entry procedural skills enum (see curriculum) |
| Age of patient                                         | numeric input          |                                                           |
| Reflective comments on procedure                       | textarea               |                                                           |
| 2021 curriculum (2025 update)                          | multi-select KC linker | values render as `.event-tag` chips                       |
| Attach files                                           | file                   |                                                           |
| Comment                                                | textarea               |                                                           |

### Reflection on ESLE (2025 Update)

| Label                                                                                                      | Type                                                                 |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Title of reflection                                                                                        | text                                                                 |
| Date of ESLE                                                                                               | date                                                                 |
| ESLE Category Assessed                                                                                     | multi-select (e.g. Management & Supervision, Teamwork & Cooperation) |
| Describe the circumstances. What did you do? What did others do?                                           | textarea                                                             |
| If you could replay the event, what would you have done differently?                                       | textarea                                                             |
| Why?                                                                                                       | textarea                                                             |
| How would the outcome be different if you replayed this event? How would you feel?                         | textarea                                                             |
| Focussing on what you would have done differently, what do you need to change for next time?               | textarea                                                             |
| What have you learned from the experience?                                                                 | textarea                                                             |
| Outline any further learning or development needs highlighted by the activity. How will you address these? | textarea                                                             |
| Procedural skills list (2025)                                                                              | select                                                               |
| Higher (ST4-ST6) EM Procedural List (2025 Update)                                                          | single-select (one procedural skill)                                 |
| 2021 EM curriculum (2025 Update)                                                                           | multi-select KC linker                                               |
| Attach files                                                                                               | file                                                                 |

### Add a Post (Training Post)

| Label            | Type                | Notes                                                                                                                 |
| ---------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Date occurred on | date                | placement start (e.g. 6 Aug 2025)                                                                                     |
| End date         | date                | placement end (e.g. 4 Aug 2026)                                                                                       |
| Stage            | derived from tags   | e.g. "ST5 Placement"                                                                                                  |
| Tags             | tag picker          | one or more specialty tags (e.g. "Emergency Medicine")                                                                |
| Post details     | hierarchical picker | top-level trust expands to sites/departments — "Select the top level trust name to EXPAND the list, and then CHECK …" |
| Locations        | derived             | trainee's location at the time                                                                                        |
| Programme        | derived             | the trainee's programme                                                                                               |

## Linked-target progression model

Every event can be linked to one or more **targets** (a KC) on one or more **goals** (an SLO). The link is created in two ways:

1. **From the event side:** the form has a `2021 EM curriculum (2025 Update)` (or equivalent) multi-select that picks one or more KCs. These render as `.event-tag` chips on the read-only view.
2. **From the goal side:** on `/goals/work/{goal-uuid}`, each KC has a "Link event to this target" / "Select existing event" / "Create new" button trio.

Counts shown on dashboard widgets, SLO list pages, and report nodes are the **number of (event, target) link rows** — not distinct events. One procedural log tagged to 4 KCs adds 1 to each of those 4 KC counters, and 4 to the SLO total (since the SLO aggregates its KCs).

`How are events linked?` (button on each target) opens a modal that explains the **link conditions** — which event-type and which tag values qualify. This is what the report engine queries.

## Higher EM curriculum (2025 Update) — SLO inventory

Clinical SLOs (one widget block):

| ID   | Title                                                                                                                     | Linked-event count (2026-05-16) |
| ---- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| SLO1 | Care for physiologically stable adult patients presenting to acute care across the full range of complexity (2025 Update) | 64                              |
| SLO2 | Support the ED team by answering clinical questions and making safe decisions (2025 Update)                               | 39                              |
| SLO3 | Identify sick adult patients, be able to resuscitate and stabilise and know when it is appropriate to stop (2025 Update)  | \*                              |
| SLO4 | Care for acutely injured patients across the full range of complexity (2025 Update)                                       | \*                              |
| SLO5 | Care for children of all ages in the ED, at all stages of development and children with complex needs (2025 Update)       | \*                              |
| SLO6 | Deliver key procedural skills (2025 Update)                                                                               | \*                              |
| SLO7 | Deal with complex and challenging situations in the workplace (2025 Update)                                               | \*                              |
| SLO8 | Lead the ED shift (2025 Update)                                                                                           | \*                              |

Generic SLOs (second widget block):

| ID    | Title                                                                                               | Linked-event count |
| ----- | --------------------------------------------------------------------------------------------------- | ------------------ |
| SLO9  | Support, supervise and educate (2025 Update)                                                        | 90                 |
| SLO10 | Participate in research and manage data appropriately (2025 Update)                                 | 24                 |
| SLO11 | Participate in and promote activity to improve the quality and safety of patient care (2025 Update) | 58                 |
| SLO12 | Lead & manage (2025 Update)                                                                         | \*                 |

Higher EM procedural skills list (third widget block) — these are _both_ curriculum nodes _and_ the enum used by the Procedural Log `procedural skills list` field:

- Paediatric Sedation (2025 Update) — 6
- Adult Sedation (2025 Update) — 15
- Advanced airway management (2025 Update) — 11
- Non-invasive ventilation (2025 Update) — 5
- Open Chest drain — 32
- Resuscitative thoracotomy — 10
- Lateral canthotomy
- DC cardioversion
- External pacing
- Pericardiocentesis
- ED management of life-threatening haemorrhage
- Emergency delivery
- Resuscitative Hysterotomy
- Fracture/Dislocation manipulation
- Large joint aspiration
- Point of care Ultrasound (pre-Aug 2025 evidence)
- POCUS: Echo in Life Support (ELS) (2025 Update)
- POCUS: Shock Assessment (2025 Update)
- POCUS: Focused Assessment for Abdominal Aortic Aneurysm (AAA) (2025 Update)
- POCUS: eFAST / Focussed Assessment for Free Fluid (FAFF) (2025 Update)
- Other (Higher EM procedural skills 2025 Update)

`*` = the widget truncated the visible block at scroll-fold; the values exist server-side but weren't captured in the screenshot. Pull live counts via `kaizen_dashboard_slo_counts()`.

## Report inventory

`/report_templates` is dominated by `ACCS …` entrustment reports (one per LO, with separate "Entrustment ratings only" and combined variants for the 2021/2025 transition):

- ACCS Clinical Learning Outcome (LO1 - LO8 - 2021 EM curriculum)
- ACCS Clinical Learning Outcome (LO1 - LO8) - 2025 Update
- ACCS Generic Learning Outcome (LO9 - LO11 - 2021 EM curriculum)
- ACCS LO1 - Entrustment ratings only
- ACCS LO2 - Entrustment ratings only
- ACCS LO3 - Entrustment ratings only
- ACCS LO4 - Entrustment ratings only
- ACCS LO4 - Entrustment ratings only - (2021/2025 Update)
- ACCS LO5 - Entrustment ratings only
- ACCS LO6 - Entrustment ratings only
- … (continues through LO11)

Top-level organisation-wide coverage reports linked from the nav:

| Report                                               | UUID                                   |
| ---------------------------------------------------- | -------------------------------------- |
| 2021 EM Curriculum                                   | `4564c4f2-f649-41a5-a040-abffa0c3947d` |
| Paediatric Emergency Medicine Sub-specialty Syllabus | `2af7a427-0cbf-4308-a284-3c493128bbbd` |
| 2021 EM Curriculum (2025 Update)                     | `8bc374b7-4b07-4e16-984a-4af6eae806ef` |

## Data extraction patterns

### List timeline entries in a category

```python
goto_url("https://kaizenep.com/events/list/Assessments")
wait_for_load(); wait_for_network_idle(timeout=10); wait(2)
rows = js("""
(() => Array.from(document.querySelectorAll('.row.event-inner a[router-link]'))
  .map(a => ({
    title: a.querySelector('h2.entry-title')?.textContent.trim(),
    href:  a.href
  })))()
""")
```

### Read an event's read-only fields

```python
goto_url(event_url)
wait_for_load(); wait_for_network_idle(timeout=15); wait(3)
fields = js("""
(() => Array.from(document.querySelectorAll('.form-text__form-group'))
  .map(g => ({
    label: g.querySelector('.form-text__control-label')?.textContent.trim(),
    value: g.querySelector('.form-text__field-value')?.textContent.trim()
  })))()
""")
tags = js("""
(() => Array.from(document.querySelectorAll('.event-tag'))
  .map(t => t.textContent.trim()))()
""")
```

### Get dashboard SLO counts

```python
goto_url("https://kaizenep.com/dashboard")
wait_for_load(); wait_for_network_idle(timeout=10); wait(2)
counts = js("""
(() => {
  const widgets = Array.from(document.querySelectorAll('.widget.panel.panel-default'));
  return widgets.map(w => {
    const title = w.querySelector('.panel-title')?.textContent.replace(/Collapse|Expand|Fullscreen|Reload widget Content|widget/g, '').trim();
    const items = Array.from(w.querySelectorAll('.panel-body li, .panel-body a')).map(a => a.textContent.trim());
    return { title, items };
  });
})()
""")
```

### Drive the assessor-invite typeahead

```python
# 1. Set the email via native setter (avoid double-character bug)
kaizen_set_field("#invites", "supervisor.name@nhs.net")
wait(2)
# 2. The Twitter Typeahead listbox renders under #invites_listbox
js("document.querySelector('#invites_listbox .tt-suggestion')?.click()")
```

## Data accessibility notes

- Timeline lists load 10 rows at a time. Infinite scroll triggers via DOM intersection — use `scroll(...)` from interaction-skills if you need all 496 events.
- The audit log on each event is collapsed by default ("Show audit log" button). Click to expand; it lists every version with timestamp and editor.
- File downloads from `/files` open in a new tab; capture the `Content-Disposition` header via `cdp("Network.responseReceived", …)` if you need to mirror them.
- All dates render in `D MMM, YYYY` (e.g. `15 May, 2026`). The new-event date inputs accept and emit `d/m/yyyy`.
- The trainee's "🟢/🔴" online indicator in `<title>` reflects offline-mode toggle; ignore it for comparisons.

## Known event-type versioning quirks

- "QIAT (EM ST4 - ST6)" (Research category) and "EM QIAT (2025 Update - v7)" (Assessments + Research) are distinct event types that satisfy overlapping curriculum targets.
- "Leadership Assessment Tool - LAT (2025 Update - v9)" appears under both Assessments and Manage/Administer/Lead.
- "Educational Activity Attended (2025 Update)" is the single source of CPD logs — the older "2021" variant is no longer being created.
- Reflection types diverged in the 2025 Update: the older `Reflection on Complaints (2021)` / new `Reflection on Complaints (2025 Update)` and similar pairs both still appear in `/events/new`. Always pick the `(2025 Update)` variant for new entries unless filing legacy evidence.
