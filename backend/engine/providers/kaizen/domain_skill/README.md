# Kaizen ePortfolio (RCEM) — risr/advance

`https://kaizenep.com` — the Royal College of Emergency Medicine's training ePortfolio, powered by **risr/advance**. Trainees use it to log assessments (CBD, DOPS, Mini-CEX, LAT, QIAT), reflections, procedural logs, teaching, MSF, training posts, and ARCP submissions, and to map each entry against RCEM curriculum SLOs / Key Capabilities.

**Stack:** AngularJS 1.x SPA. Look for `ng-scope`, `ng-isolate-scope`, `ng-binding`, `ng-repeat` classes everywhere. Forms are built with the [Formly](https://github.com/formly-js/angular-formly) library; dropdowns use [ui-select](https://github.com/angular-ui/ui-select) (look for `.ui-select-container`); user-pickers use Twitter Typeahead via `sf-typeahead`. Bootstrap 3 panel/card grid.

**Auth host:** `auth.kaizenep.com` (OIDC interaction URLs like `/interaction/{uuid}/login`). Login form: `input[name="login"]`, `input[name="password"]`, `button[type="submit"]`. Hidden context: `org=org_rcem`, `client=kaizen`. Do not type credentials from screenshots — if the daemon lands on `auth.kaizenep.com`, stop and ask the user.

## Routes (canonical, verified 2026-05-16)

```
/dashboard                                       — 11 widgets (alerts, quick notes, profile, SLO/KC summaries)
/events/list/{category}                          — Timeline filtered to category (URL-encoded)
/events/view/{event-uuid}                        — Single-section event view (e.g. Procedural Log)
/events/view-section/{section-uuid}              — One section of a multi-section event (CBD body, MSF rater)
/events/new                                      — "What would you like to create?" picker
/events/new-section/{event-type-uuid}            — Empty form for a specific event type
/goals/list/{curriculum-name-encoded}            — All SLOs / goals in a curriculum
/goals/list/all                                  — Across all curricula
/goals/work/{goal-uuid}                          — One SLO with all its KCs (targets) and progress
/reports/view/organisation/coverage/{report-uuid}— Curriculum coverage report (hierarchical tree)
/report_templates                                — Reports index
/report_templates/run/{template-uuid}            — Run a report template
/files                                           — All uploaded files (rendered as event-row entries)
/faqs                                            — FAQs (also reachable as /#/faqs)
/profile/view                                    — Logged-in user's profile (Details, Emails, Information, Login info, Audit log)
/settings/offline                                — Offline mode + device settings
/settings/test                                   — Diagnostics
/activities                                      — Notification / activity feed
/inbox                                           — Trainee inbox
```

### Timeline categories

URL pattern: `/events/list/{category}`. Encode `&` as `%26`, space as `%20`, `,` as `%2C`.

| Category path                           | Label                                      | Event types observed                                                                                                                                                          |
| --------------------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `All`                                   | Timeline (All Events)                      | every type — 496 items on this account                                                                                                                                        |
| `Assessments`                           | Assessments — 138 items                    | CBD, DOPS (ST3-ST6), EM QIAT, Leadership Assessment Tool (LAT), Mini-CEX                                                                                                      |
| `Post%20%26%20Supervisor`               | Training Post and Supervisor — 10 items    | Add a Post, Add a Supervisor                                                                                                                                                  |
| `Educational%20Review%20%26%20Meetings` | Educational Review and Meetings — 19 items | ARCP Form\*, Educational Supervisor Report (ST4-ST6), Educational Meeting: Supplementary Review, FEGS ST4 & ST5, Educational Meeting ST3-ST7, End of Placement Report ST4-ST6 |
| `Progression`                           | Progression — 1 item                       | Higher Progression form (trainees Higher – ST4-ST6 only) - 2023                                                                                                               |
| `Procedural%20Logs`                     | Procedural Logs — 52 items                 | Procedural Log - ST3-ST6 (2025 Update)                                                                                                                                        |
| `Reflection`                            | Reflections — 85 items                     | Reflection on ESLE (2025 Update), Ultrasound Case Reflection (2025 Update), Personal Development Plan (PDP - 2021/2025)                                                       |
| `MSF`                                   | Multi-Source Feedback — 30 items           | "MSF: Multi-Source Feedback" parent + per-rater "Section of MSF: Multi-Source Feedback for {Name}" children                                                                   |
| `Teaching%20%26%20Education`            | Teaching and Education — 91 items          | Educational Activity Attended (2025 Update), Teaching Delivered By Trainee (Update 2025)                                                                                      |
| `Research%2C%20Audit%20%26%20QI`        | Research, Audit and QI — 7 items           | EM QIAT (2025 Update - v7), Presentation at a Journal Club (JCF - 2021), Research Activity, QIAT (EM ST4 - ST6)                                                               |
| `Manage%2C%20Administer%20%26%20Lead`   | Manage, Administer and Lead — 29 items     | Management Experience (2021/2025), Management: Project Record, LAT, Introduction of Equipment or Service, Management: Writing a Report, Critical Incident                     |
| `e-Learning`                            | e-Learning — 30 items                      | RCEM Learning                                                                                                                                                                 |
| `Exams`                                 | Exams — 3 items                            | Exam Result: …                                                                                                                                                                |
| `Absence`                               | Absence — 0 items on this account          | —                                                                                                                                                                             |
| `CCT`                                   | CCT — 0 items on this account              | —                                                                                                                                                                             |
| `Documents`                             | Documents — 41 items                       | File UPLOAD - miscellaneous (2025 Update), File UPLOAD (miscellaneous)                                                                                                        |

Default page size: 10 rows. Older items load via infinite scroll — see the core `interaction-skills/scrolling.md` if you need to paginate.

### Curricula

| Path                                                       | Label                                                              |
| ---------------------------------------------------------- | ------------------------------------------------------------------ |
| `Higher%20EM%20curriculum%20%282025%20Update%29`           | Higher EM curriculum (2025 Update) — current default for this user |
| `Intermediate%202021`                                      | Intermediate Curriculum 2021                                       |
| `Higher%202021`                                            | Higher Curriculum 2021                                             |
| `PEM%20Subspecialty%20REFORMATTED%20%20%28Aug16-July18%29` | PEM sub-specialty (legacy)                                         |

## Page rendering — wait before scraping

Pages render via AngularJS routing and lazy-load section data over XHR. After `goto_url(...)` and `wait_for_load()`, give the digest cycle time:

```python
wait_for_network_idle(timeout=15)
wait(3)   # AngularJS section render + Formly mount
```

If `h1` is still `"Add a Post"` instead of the real event-type heading, the section hasn't hydrated yet — wait longer.

## Timeline row shape

Every timeline page (including `/files`) renders each item as `.row.event-inner`:

```html
<div class="row event-inner">
  <div class="col-sm-7">
    <a router-link="ctrl.eventSection.route" href="/events/view-section/{uuid}">
      <h2 class="entry-title">
        <span
          kz-event-title=""
          event="ctrl.eventSection"
          event-id="{uuid}"
          for-user="ctrl.username"
        >
          <span data-ng-bind-html="title | kzHighlight"
            >CBD - Case Based Discussion (2025 update)</span
          >
        </span>
      </h2>
    </a>
  </div>
  <div class="col-sm-5 col-right">
    <ul class="list-inline list-unstyled">
      …states / dates / linked-event metadata…
    </ul>
  </div>
</div>
```

- Title selector: `h2.entry-title` inside `.row.event-inner`
- Per-row link can be `/events/view/{uuid}` (single-section events) **or** `/events/view-section/{uuid}` (multi-section). Don't strip the `-section` — it's load-bearing.
- Total count appears in the page body as `"NNN items"`. Pull it with a regex.

## Read-only event detail

`/events/view/{uuid}` or `/events/view-section/{uuid}` renders the event as a read-only form. Stable classes:

| Selector                                        | Holds                                                        |
| ----------------------------------------------- | ------------------------------------------------------------ |
| `h1`                                            | Event type, e.g. `CBD - Case Based Discussion (2025 update)` |
| `.form-text__heading`                           | Section heading inside the body                              |
| `.form-text__description`                       | Section sub-description                                      |
| `.form-text__form-group`                        | One field group (label + value)                              |
| `.form-text__control-label`                     | Field label                                                  |
| `.form-text__field-value`                       | Field value (rendered text)                                  |
| `.form-readonly`, `.form-readonly--fancy`       | Outer wrapper for read-only render                           |
| `.event-section-progress-state` / `…--complete` | "Complete" / "Pending" badge                                 |
| `.event-tag`                                    | Curriculum / KC tag chip (one per linked target)             |
| `.event-users`                                  | Who filled in the section (assessor)                         |

Verified fields on a real CBD (2025 update):

- Date occurred on, End date
- Case to be discussed (textarea)
- Reflection of event (textarea)
- Attach files
- Comment

Verified fields on a real Procedural Log - ST3-ST6 (2025 update):

- Date of Activity, Stage of training (e.g. `Higher / ST4 - ST6`), Year of training (e.g. `ST5`)
- ST4-ST6 Higher EM procedural skills list (single-select from the 2025 skills list)
- Age of patient (numeric)
- Reflective comments on procedure (textarea)
- 2021 curriculum (2025 update) — multi-select KC links (rendered as `.event-tag` chips)
- Attach files, Comment

Verified fields on a Reflection on ESLE (2025 Update):

- Title of reflection, Date of ESLE
- ESLE Category Assessed (multi-select)
- Describe the circumstances. What did you do? What did others do?
- If you could replay the event, what would you have done differently? + Why?
- How would the outcome be different if you replayed this event? How would you feel?
- Focussing on what you would have done differently, what do you need to change for next time?
- What have you learned from the experience?
- Outline any further learning or development needs highlighted by the activity. How will you address these?
- Procedural skills list (2025), Higher (ST4-ST6) EM Procedural List (2025 Update)
- 2021 EM curriculum (2025 Update) — KC links
- Attach files

## New-event form (write side)

1. `goto_url("https://kaizenep.com/events/new")`
2. Click the event-type link via `el.click()` — the `href` is `/`; the routing is `ng-click="newEventCtrl.getRouteForEventType(eventType._id)"`. Use `.click()` (Formly listens for the synthetic event).
3. You land on `/events/new-section/{event-type-uuid}` with a single `<form name="newForm">` mounted.

Form scaffold (stable across event types):

- `#startDate` — "Date occurred on", `placeholder="d/m/yyyy"`
- `#endDate` — "End date"
- `#event-description` — optional free-text description (`<textarea name="event-description">`)
- Custom fields use the **field UUID as `id` and `name`**, stable per event-type version.

CBD (2025 update) verified field UUIDs:

| Field                | Element                      | id / name                              |
| -------------------- | ---------------------------- | -------------------------------------- |
| Stage of training    | `<select>`                   | `e0864e88-62cf-43aa-a9e5-51abd98a1cce` |
| Date of event        | `<input type="text">` (date) | `5391f8de-de63-4db3-9e08-baaa2a380cfe` |
| Case to be discussed | `<textarea>`                 | `60772a97-92eb-4dbe-a813-6a5293be82f9` |
| Reflection of event  | `<textarea>`                 | `610b5c60-99ac-4902-9407-22974d6a5799` |

If RCEM ships `v8` of a form type, expect new UUIDs — always re-confirm by reading labels.

### Assessor picker

`<input id="invites" name="{section-uuid}:invites" sf-typeahead>` — Twitter Typeahead. Placeholder: `"Start typing to search"`. The listbox renders as `#invites_listbox`. Type the assessor's email and pick from the dropdown.

### Form toolbar buttons

- `<button>Add tags</button>` — opens the curriculum / KC picker
- `<button class="dropdown-toggle">Link to ...</button>` — drops down the four curricula
- `<button class="btn-info">Upload</button>` — file attachment chooser
- `<button class="btn-success btn-raised">Send to assessor</button>` — triggers `ng-click="eventCtrl.save(eventCtrl.form, 'publish')"` (this is publish/submit)

### CRITICAL: never use `fill_input` on Formly inputs

`fill_input` doubles characters in this app — each keystroke fires `input` + `change`, and Formly re-applies the model on the next digest, doubling everything you typed. Use the native value setter pattern instead:

```python
js("""
(() => {
  const set = (sel, val) => {
    const el = document.querySelector(sel);
    if (!el) throw new Error('missing ' + sel);
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.blur();
  };
  set('#60772a97-92eb-4dbe-a813-6a5293be82f9', 'Case body…');
  set('#610b5c60-99ac-4902-9407-22974d6a5799', 'Reflection…');
})()
""")
```

`agent_helpers.kaizen_set_field(...)` wraps this — prefer it.

## Goals / curriculum

`/goals/list/{curriculum}` lists SLOs. Each SLO has an `h3` title, a trailing count badge (number of linked events), and a link to `/goals/work/{goal-uuid}` for the SLO detail.

`/goals/work/{goal-uuid}` shows the SLO and all its KCs (one KC = one **target**):

- `[ng-repeat="target in goalCtrl.goal.targets track by target._id"]` — each Key Capability block.
- Inside: `h5.entry-title.text-gray` with the KC text, plus a numeric progress count and `How are events linked?` / `Link event to this target` / `Select existing event` / `Create new` buttons.
- Sidebar: `[ng-repeat="goal in list.items track by goal.doc._id"]` — navigation between sibling SLOs.

Higher EM (2025 Update) SLO inventory:

- Clinical: SLO1–SLO8 (e.g. SLO1 Care for physiologically stable adult patients, SLO3 Identify sick adult patients & resuscitate, SLO4 Care for acutely injured patients, SLO5 Care for children, SLO6 Deliver key procedural skills, SLO7 Deal with complex situations, SLO8 Lead the ED shift)
- Generic: SLO9 Support, supervise, educate / SLO10 Research / SLO11 Quality & safety / SLO12 Lead & manage
- Procedural skills list (sedation, advanced airway, NIV, chest drain, thoracotomy, lateral canthotomy, DC cardioversion, external pacing, pericardiocentesis, life-threatening haemorrhage, emergency delivery, hysterotomy, fracture/dislocation, large joint aspiration, POCUS family of skills)

## Reports

`/report_templates` lists every curriculum-coverage report (ACCS LO1–LO11 entrustment reports, etc.). Each row links to:

- `/reports/view/organisation/coverage/{report-uuid}` — organisation-wide coverage view
- `/report_templates/run/{template-uuid}` — per-user run

The coverage view is a hierarchical tree:

- `[ng-repeat="node in nodes track by node._id"]` — one node per SLO/KC.
- States rendered via `[ng-repeat="state in states track by $index"]`.

There are **no `<table>` elements** — everything is divs styled as cards/columns.

Verified report templates on this account:

- `2021 EM Curriculum` (id `4564c4f2-f649-41a5-a040-abffa0c3947d`)
- `Paediatric Emergency Medicine Sub-specialty Syllabus` (id `2af7a427-0cbf-4308-a284-3c493128bbbd`)
- `2021 EM Curriculum (2025 Update)` (id `8bc374b7-4b07-4e16-984a-4af6eae806ef`)

## Files

`/files` reuses the timeline rendering (same `.row.event-inner` / `h2.entry-title`), scoped to file-upload events. Each row's title is the filename (e.g. `Foo.pdf`) and the right column reads `created on: … linked to: {parent-event-title}`. 205 entries on this account.

## Dashboard widgets

11 widgets, all rendered as `.widget.panel.panel-default`. Stable titles:

1. "2025 Update on its way!" (announcement)
2. "Alerts" (RCEM ePortfolio team announcements)
3. "Quick notes" (note → draft event)
4. "Profile" (avatar, role, locations, programme, FTE)
5. "Higher Clinical Specialty Learning outcomes (2025 Update)" — SLO1–SLO8 + count
6. "Higher Generic Specialty Learning Outcomes (2025 Update)" — SLO9–SLO12 + count
7. "Higher EM procedural skills (2025 Update)" — procedural skill counts
8. "Create event" (button)
9. "All Higher Clinical SLO Entrustments 2025 update" (chart — shows first 100 only)
10. "Progression: Please read" (guidance)
11. "Need Help?" (links to FAQs / RCEM contact)

Counts on these widgets are **totals across all events that match each target's link conditions** — not the number of distinct events. Two procedural logs both tagged to KC1 + KC2 contribute 2 to KC1 and 2 to KC2.

## Common gotchas

- **`risr/advance` titlebar** — the page `<title>` often starts with `🟢` or `🔴` (the user's online/offline indicator). Strip the first emoji before comparing.
- **Title rot** — when you arrive at an event page, the `h1` may briefly read `"Add a Post"` (a default template) before the real type loads. Re-read after `wait(3)`.
- **Field IDs are UUIDs and stable per event-type _version_.** A version bump (e.g. `(2025 Update v7)` → `v8`) usually rotates UUIDs. Always re-confirm by reading labels.
- **`fill_input` doubles characters.** Use the native-setter pattern (`kaizen_set_field`). This is the single most important footgun on the site.
- **`/events/view-section/...` vs `/events/view/...`** — both exist. Don't normalize them; the section URL is what the timeline emits for multi-section events (MSF, Educational Meetings, etc.).
- **Auth interactions live at `auth.kaizenep.com`.** If you land there, you've been logged out — stop and ask the user.
- **Selectors with quoted `[href*="…"]`** — when running JS via `js("...")` from the shell, use `\"` inside attribute brackets, or you'll silently get a "not a valid selector" error from the CSS parser.
- **Deleting drafts requires SweetAlert2 class selector** — after clicking Delete (`a.text-danger`), the confirmation dialog uses SweetAlert2. Wait 2s, then click `button.confirm`. Do NOT match by text — class selector is the only reliable way.
- **Drafts live under `/activities`** — not the timeline. Access saved drafts at `https://kaizenep.com/activities`, click "Saved drafts" header to expand, then click the draft link (uses `/events/view-section/{uuid}` pattern, NOT `/events/fillin/{uuid}`).
- **Always clean up test drafts** — after creating test drafts during development, call `kaizen_delete_draft(uuid)` for each one. Verify by navigating to `/activities` and checking "Saved drafts" section is gone.

## Files in this skill

- `README.md` (this file) — site guide and gotchas
- `selectors.json` — every stable CSS selector found while mapping
- `portfolio-structure.md` — the data model: events, sections, fields, KC mapping
- `screenshots/*.png` — captured during exploration on 2026-05-16 by Moeed Ahmed (logged-in account: Moeed Ahmed, Higher Trainee 2025 Update, Kingston Hospital placement)
