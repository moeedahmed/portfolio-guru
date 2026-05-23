# Clinical Supervisor Mode — Architecture Brief

**Status:** Discovery complete. Ready for build sprint.
**Date:** 2026-05-23 (re-verified live 21:14 GMT+1)
**Product Direction Source:** `AGENTS.md` § Product Direction (lines 140-182)

**Verification log:** Live read-only mapping run 2026-05-23 21:14 GMT+1 against
Ahmed Mahdi's queue using `kaizen_unsigned_scraper._login_via_rcem` + Playwright
CDP at `localhost:18800`. Raw output, screenshots, and per-ticket field dumps
are stored locally at `docs/assessor-mapping/` (gitignored — contains trainee
PHI). No Fill In / Save / Submit / Sign clicks performed; safety contract held.

---

## Product Context

One bot, one codebase. Two entry points:

| Entry Point     | Who                 | Direction                    | Trigger                         |
| --------------- | ------------------- | ---------------------------- | ------------------------------- |
| `file_evidence` | Trainee             | Outbound (user initiates)    | User sends a case               |
| `assess_ticket` | Clinical Supervisor | Inbound (Kaizen pushes work) | Polling loop detects new ticket |

The mode is detected at auth time from Kaizen role. A pure supervisor account (Ahmed Mahdi) gets assessor mode by default. A trainee who is also a supervisor gets a role switcher.

---

## Discovery Data — Ahmed Mahdi's Assessor Queue

10 pending tickets, two distinct states:

### Unfilled (assessor hasn't responded)

Write controls visible: **Fill in** + **Save**. Trainee fields visible, assessor section blank.

- 3 CBDs are unfilled

### Already filled (someone completed the assessment)

No write controls. Assessor section visible directly with completed data.

- QIAT, ESLE, DOPS, 2 CBDs, Mini-CEX all completed

### Assessor Feedback Fields By Form Type

| Type                  | Assessor Fields                                                                                                                                                                                                                                                                                                                   |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CBD / DOPS / Mini-CEX | Assessor Registration Number, Job title, Entrustment Scale, Feedback, Recommendation for further learning                                                                                                                                                                                                                         |
| QIAT                  | Feedback on clinician performance, Learning points, Recommendation, SLO 11 performance level, Assessor name/registration/email/job title/responsibility/date                                                                                                                                                                      |
| ESLE                  | Records event sequence, Clinical cases covered, Key learning points, 10+ non-technical skills ratings (maintenance of standards, observations, workload management, supervision/feedback, team building, communication quality, authority/assertiveness, option generation, etc.), Summary of NTS evaluation, Learning Objectives |

CBD completion mapping was validated in Phase 2.7 (read-only + one approved Fill-in).

---

## Architecture

### Notification Layer (Polling Loop)

**Kaizen does not support webhooks.** The bot must poll.

Design decisions:

- **Polling interval:** 5 minutes (cheap enough; Kaizen isn't real-time critical)
- **State tracking:** A simple JSON file or SQLite table tracking `(ticket_id, status)` for the supervisor's account. The poll reads "my unassessored tickets" page, diffs against known state, and fires notifications for new rows.
- **Duplicate prevention:** The state tracker records seen ticket IDs. If a ticket already exists in Known state, skip.
- **Failure handling:** If Kaizen times out or returns an error, retry on next poll cycle. No false-positive "new ticket" alert from a transient error.

**Polling vs UX requirement:** The poll must _only_ fetch the tickets page and diff it. It must not pre-fetch ticket content or open any form. The ticket content is fetched on supervisor demand only (when they open a notification).

### Inbound Notification Flow

```
Kaizen tickets page → poller diffs → new ticket detected
  → Telegram notification to supervisor:
    "[type] from [trainee name] — [ticket ID snippet]
     Open? File later? Skip?"
  → Supervisor taps Open → bot fetches ticket detail (read-only, unfilled section only)
  → Bot renders ticket context in chat:
    - WPBA type
    - Trainee name
    - Case summary (trainee-entered fields)
    - Pending: assessor section (empty fields)
  → Supervisor gives response (voice or text):
    "Good clinical reasoning, signed off. One thing to improve — documentation of time-critical decisions."
  → Bot maps response to form fields:
    - Feedback → structured text
    - Entrustment scale → inferred from wording or asked explicitly
    - Recommendation → mapped
  → Bot shows filled preview with confidence labels
  → Supervisor approves → bot files as Kaizen draft (not submitted)
```

### Assessor Response → Form Field Mapping

The response format problem: supervisor gives free-text dictation, but the form needs structured fields (Entrustment Scale is a dropdown, Recommendation is a specific scale or text).

Approach:

1. **Prompt the supervisor** with a compact template after they see the ticket context:
   > "Feedback? Entrustment level (1-5)? Any specific recommendation?"
2. **LLM-assisted field extraction** from the dictation response using DeepSeek (cheap, good enough). If the supervisor says "excellent clinical reasoning, entrustment 4", map Entrustment Scale → 4, Feedback → "Excellent clinical reasoning."
3. **Low-confidence flagging:** If the LLM can't confidently extract Entrustment from the wording, ask explicitly.
4. **Preview before filing:** Always show the filled assessor section and get approval before hitting Kaizen.

### Shared Engine (80% Reuse)

The following are identical between filing and assessment:

- **Kaizen CDP session management** — same Playwright layer, same credential store, same login flow
- **DOM mapping system** — same selectors for form fields, same form-type detection (CBD/DOPS/Mini-CEX/ESLE/QIAT)
- **Form-type registry** — same field schemas, same curriculum area mappings
- **Playwright filing layer** — the `fill_and_save_draft` function works for any form type regardless of which role fills it. The assessor section is just more fields on the same form
- **Model config** — same DeepSeek model, same prompt structure
- **Deployment pipeline** — same Railway/deploy config, same env vars

### Architecture Differences

| Aspect         | Filing                              | Assessor                                                                           |
| -------------- | ----------------------------------- | ---------------------------------------------------------------------------------- |
| Trigger        | User sends case message             | Poller detects new ticket                                                          |
| Data direction | Outbound: extract → draft → file    | Inbound: fetch → display → map → file                                              |
| Form state     | Always start from blank form        | Start from partially-filled form (trainee section written, assessor section blank) |
| State machine  | Capture → Draft → Needs you → Filed | New → Opened → Responded → Preview → Filed                                         |
| Auth check     | Own Kaizen credentials              | Supervisor's Kaizen credentials (separate account)                                 |

---

## Implementation Order

### Sprint 1: Polling + Notification Foundation

- [ ] Create `backend/supervisor_poller.py` — lightweight polling module
- [ ] Read-only ticket detection from Kaizen "my assessments" page
- [ ] State tracker (ticket_id → status mapping, persisted to disk)
- [ ] Telegram notification when new ticket detected
- [ ] "Open / Skip / Later" buttons on notification
- [ ] Tests: poller detects new tickets, skips known tickets, handles Kaizen errors gracefully

### Sprint 2: Ticket Rendering

- [ ] Read unfilled ticket content from Kaizen (read-only, no form interaction)
- [ ] Render ticket context in Telegram: type, trainee, case summary, pending assessor fields
- [ ] Handle all 5 form types (CBD, DOPS, Mini-CEX, ESLE, QIAT)
- [ ] Tests: each form type renders correctly, PHI-free

### Sprint 3: Response → Field Mapping

- [ ] LLM-assisted field extraction from supervisor dictation
- [ ] Confidence flagging for unresolved fields (e.g. Entrustment not clear)
- [ ] Preview rendering with confidence labels
- [ ] Explicit approval gate before filing
- [ ] Tests: field extraction accuracy, confidence boundaries

### Sprint 4: Filing + Closing the Loop

- [ ] Wire assessor response into existing `fill_and_save_draft` / Kaizen filing layer
- [ ] Save as Kaizen draft (never submit)
- [ ] Mark ticket as responded in state tracker
- [ ] Proof report for assessor action
- [ ] Full integration test: poll → detect → notify → render → map → preview → file → confirm

### Sprint 5: Multi-Account + Role Detection

- [ ] Credential manager for multiple Kaizen accounts (trainee + supervisor)
- [ ] Role detection on auth (trainee vs assessor vs both)
- [ ] Role switcher for dual-role users
- [ ] Per-account polling scheduler

---

## Safety Contract

- **Read-only until explicit approval.** The poller only fetches the tickets list and diff page. It never opens a form or touches write controls until the supervisor explicitly chooses a ticket to respond to.
- **Draft only, not submitted.** The assessor response is saved as a Kaizen draft. The supervisor manually submits it. This matches the existing filing safety contract.
- **No auto-filing.** The bot never files an assessor response without showing the preview and getting approval.
- **No noise.** The poller doesn't dump all tickets at once. One notification per new ticket. No re-notification for already-seen tickets.
- **Colleague/consultant credentials are high-risk.** Ahmed Mahdi's account must not produce test artefacts, spam drafts, or accidental submissions.
- **ESLE complexity is a first-class risk.** The ESLE form has 30+ rating scales and free-text fields. The field extraction accuracy requirement is higher. ESLE should be the last form type wired up, after CBD/DOPS/Mini-CEX prove the flow.

---

## Open Questions

1. **Unfilled CBD shape — do we need to click Fill In to see the blank assessor section?** The Phase 2.7 mapping did this once with explicit approval. For the full build, we need a non-destructive way to detect blank field labels without clicking write controls.
2. **Polling interval vs Kaizen rate limits.** 5 minutes is conservative but unverified. Monitor early polls for rate-limit errors.
3. **Multi-session CDP management.** Can one Playwright session handle multiple Kaizen accounts with credential switching, or does each account need its own browser context?
