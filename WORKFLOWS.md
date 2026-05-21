# Portfolio Guru — Agent Workflow Reference

> Last updated: 2026-03-07
> Optimised for agent consumption — no diagrams, pure structured text.
> Human-readable Mermaid diagrams live in Notion (Portfolio Guru page).
> Update this file whenever a flow changes.

---

## Conversation States

| State constant      | Meaning                                                      |
| ------------------- | ------------------------------------------------------------ |
| `IDLE`              | No active conversation. Waiting for any input.               |
| `AWAIT_FORM_CHOICE` | Form type buttons shown. Waiting for user to select a form.  |
| `AWAIT_APPROVAL`    | Draft preview shown. Waiting for File / Edit / Cancel.       |
| `AWAIT_EDIT_FIELD`  | Edit mode. Waiting for user to select which field to change. |
| `AWAIT_EDIT_VALUE`  | Edit mode. Waiting for user to provide the new field value.  |
| `AWAIT_USERNAME`    | Setup flow. Waiting for Kaizen username.                     |
| `AWAIT_PASSWORD`    | Setup flow. Waiting for Kaizen password.                     |

**Invariant:** Every path to `ConversationHandler.END` must call `context.user_data.clear()` first.

---

## Flow 1 — First-Time User

```
/start
→ Welcome message + [What is this? | Connect Kaizen | File a case]

[What is this?]
→ Explain message
→ Return to idle (no state change)

[Connect Kaizen]
→ has_credentials(user_id)?
  YES → "Kaizen already connected ✅" + [File a case] button
  NO  → Ask for Kaizen username → AWAIT_USERNAME
        → Ask for Kaizen password → AWAIT_PASSWORD
        → Encrypt + store in SQLite
        → "Connected ✅" + [File a case] button

[File a case]
→ has_credentials(user_id)?
  NO  → "Please connect Kaizen first" + [Connect Kaizen] button
  YES → "Send me your case" prompt → waiting for text/voice/photo
```

---

## Flow 2 — Core Filing (Happy Path)

```
User sends input (text / voice / photo)
→ classify_intent(text)
  CHITCHAT → friendly reply, return to idle
  QUESTION → answer_question(text), return to idle
  CASE     → proceed

Detect input type:
  text  → use as-is
  voice → whisper.transcribe() → text
  photo → vision.extract_from_image() → text

→ recommend_form_types(text)
  Returns list of FormTypeRecommendation objects (max 3)
  Each has: form_type, uuid, reason

→ Show buttons: one per recommended form + [❌ Cancel]
  State → AWAIT_FORM_CHOICE

User taps [❌ Cancel]
→ "Cancelled." context.user_data.clear() → END

User taps [form type]
→ extract_cbd_data(text, form_type)
  Returns CBDData with all fields:
  date, setting, presentation, clinical_reasoning,
  reflection (humanized), slos, key_capabilities
→ Store in context.user_data['draft']
→ Show draft preview message
→ Show [✅ File this draft | ✏️ Edit | ❌ Cancel]
   State → AWAIT_APPROVAL

[❌ Cancel]
→ context.user_data.clear() → END

[✏️ Edit]
→ Flow 3 (Edit Before Filing)

[✅ File this draft]
→ filer.file_form(draft, credentials)
  browser-use opens Kaizen
  navigates to: https://kaizenep.com/events/new-section/<UUID>
  fills all fields
  clicks "Save as Draft" ONLY — never submits to supervisor
  SUCCESS → "✅ Saved as draft in Kaizen" + [✅ Done | 📤 File another]
  FAILURE → "❌ Filing failed: <error>" + [🔁 Retry | ❌ Cancel]

[✅ Done]
→ context.user_data.clear() → END

[📤 File another]
→ context.user_data.clear() → "Send me your next case" → idle
```

---

## Flow 2A — Assess Ticket (Read-Only Mapping / Planned)

This is the assessor-side equivalent of Flow 2. It is not a persistent "mode";
it is a second entry point into the same review-and-approve engine.

```
Ticket appears in assessor portfolio
→ Portfolio Guru notification: "You have a ticket awaiting assessment"
→ User opens ticket from bot or Kaizen link
→ Bot extracts ticket content read-only
→ Bot shows:
   - ticket type
   - trainee/doctor details visible on the ticket
   - ticket fields and attachments metadata
   - Kaizen link as backup
→ Assessor gives intent:
   "Looks fine, sign it"
   "Ask them to add more reflection"
   "Mention clearer clinical reasoning"
   "Approve but add X"
→ Bot drafts assessor feedback/sign-off text
→ Bot shows missing assessor fields or risk notes if present
→ Bot shows draft response
→ User approves one named action
→ Bot submits/signs that one assessor action
```

Current implementation status:

```
Read-only mapping scaffold exists:
  backend/assessor_mapper.py

Allowed during mapping:
  - navigate to Assessments timeline
  - list visible assessment rows
  - open ticket detail pages
  - extract read-only fields, tags, state, visible buttons
  - output PHI-free ticket shapes for mapping

Not built yet:
  - notification polling
  - assessor feedback/sign-off field mapping
  - submit/sign action
  - Telegram assessor review UI
```

First mapped read-only ticket shape:

```
Ticket type: CBD - Case Based Discussion (2025 update)
Route: view-section
Visible read-only fields:
  - Date occurred on
  - End date
  - Case to be discussed
  - Attach files
Write-side controls detected but not clicked:
  - Fill in
  - Save
Implication:
  - The next mapping gap is the post-Fill in assessor form. Opening it may create
    or modify assessor-side state, so it stays blocked until an explicit
    one-ticket approval gate exists.
```

First mapped assessor completion shape after explicit approval:

```
Ticket type: CBD - Case Based Discussion (2025 update)
Route after Fill in: fillin
Assessor-side fields/inputs:
  - Assessor Registration Number
  - Job title
  - Entrustment Scale
  - Feedback
  - Recommendation for further learning or development
Write-side controls detected but not clicked:
  - Submit
  - Save as draft
Product implication:
  - Assessor action can use the same draft/review/approve engine as filing,
    but final submit must remain a one-ticket explicit approval action.
```

---

## Flow 3 — Edit Before Filing

```
Entry: user tapped [✏️ Edit] from AWAIT_APPROVAL state

→ "Which field to edit?" + field buttons:
  [Date | Setting | Presentation | Clinical reasoning | Reflection | SLOs | ❌ Back]
  State → AWAIT_EDIT_FIELD

[❌ Back]
→ Re-show draft preview + approval buttons
  State → AWAIT_APPROVAL

[field selected]
→ "Send me the new value for <field>"
  State → AWAIT_EDIT_VALUE

User sends text
→ Update context.user_data['draft'][field] = new_value
→ Re-show updated draft preview
→ Re-show [✅ File this draft | ✏️ Edit | ❌ Cancel]
  State → AWAIT_APPROVAL
```

---

## Flow 4 — Edit Previously Filed Draft (v2.1 — NOT YET BUILT)

```
User says "edit my last case" or similar
→ Query case_history table in SQLite by user_id
  FOUND     → Show summary + [Yes, this one | No, different one]
  NOT FOUND → "No matching case found. Describe it more."

[Yes, this one]
→ browser-use opens Kaizen draft URL
→ Show current field values
→ Enter Flow 3 (Edit)

[No, different one]
→ Prompt again
```

---

## Flow 5 — Reset / Recovery

```
User sends /reset (any state)
→ context.user_data.clear()
→ "Reset. Send me a case whenever you're ready."
→ END (returns to idle)

User sends a new case while mid-state (stuck)
→ classify_intent → CASE
→ If AWAIT_APPROVAL or AWAIT_EDIT_*:
    "Looks like a new case. Send /reset to start fresh."
→ If IDLE:
    proceed normally
```

---

## Form Type Decision Rules

Applied inside `recommend_form_types()` in `extractor.py`.

```
CBD — always include if any clinical case was managed by the trainee.

DOPS — include ONLY IF:
  - Trainee directly performed a procedure with their own hands
  - Procedure is invasive: LP, intubation, central line, chest drain, cardioversion, etc.
  - Do NOT include if trainee only observed or assisted

LAT — include ONLY IF:
  - Trainee explicitly led a team, coordinated a resus, or managed a major incident
  - Must be active leadership, not just being present as senior
  - Do NOT include for routine senior clinical decision-making

ACAT — include ONLY IF:
  - Description covers a full shift assessment or multiple patients across a shift
  - Not for a single case

ACAF — include ONLY IF:
  - Acute care assessment in a non-traditional setting explicitly described

Max 3 forms recommended per case.
Adjacent ≠ demonstrated. When in doubt, exclude.
```

---

## Key Capabilities Selection Rules

Applied inside `extract_cbd_data()` in `extractor.py`.

```
SELECT a KC ONLY IF:
  - The trainee directly demonstrated it in this specific case narrative
  - There is explicit textual evidence (e.g. "I intubated" → SLO3 KC1)

DO NOT SELECT if:
  - It could plausibly apply but isn't mentioned
  - The patient needed it but the trainee didn't personally do it
  - It's adjacent to what was described

Over-selection is a bug. Under-selection is safer. Max ~4 KCs per case.
```

---

## Data Flow

```
User input (text/voice/photo)
  ↓
bot.py — conversation state machine, Telegram handler
  ↓
extractor.py — Gemini API calls (classify, recommend, extract, humanize)
  ↓
context.user_data['draft'] — CBDData object in memory
  ↓ (after ✅ File this draft)
filer.py — browser-use + Chromium → Kaizen ePortfolio (draft saved)

SQLite DB (~/.openclaw/data/portfolio-guru/portfolio_guru.db)
  ↔ bot.py (read credentials, write case history in v2.1)

BWS secrets (at startup only):
  - Telegram bot token
  - Google API key (Gemini)
  - Fernet encryption key (for credentials)
```

---

## Hard Constraints (never violate)

- NEVER submit a form to supervisor — draft save only, every time
- NEVER submit/sign an assessor ticket without explicit approval for that one ticket and reviewed response
- NEVER create drafts, sign, submit, delete, approve, reject, or send feedback while running assessor mapping
- NEVER store credentials in plaintext — always Fernet-encrypt before writing to DB
- NEVER open Kaizen before the user taps [✅ File this draft]
- NEVER select a KC unless the trainee directly demonstrated it in this case
- Date format for Kaizen input fields: `d/m/yyyy` (not ISO 8601)
- KC over-selection is a correctness bug — err on the side of fewer

---

## User-Facing Message Standard

Applies to every bot message in `backend/bot.py`. Safety-critical templates
(welcome, captured ack, recommendation, privacy nudge, AI unavailable, thin
case, draft reply hint) live in `backend/message_policy.py` and must not
drift from there.

### Shape

- One bot bubble per long async action. The first ack is edited in place as
  the action progresses — never followed by a separate "still working" bubble.
- One leading emoji per message, present-tense verb, en/em dashes for inline
  asides (`—`), ellipsis (`…`) for in-flight actions.
- Telegram's typing indicator (`_typing_until`) is the ambient progress
  signal for long work. Text reassurance is opt-in, sparing, and replaces
  the ack — it never adds a second line.

### Vocabulary by stage

| Stage                             | Photo                                                                       | Voice                                                        | Video                                                             | Document                                         | Kaizen save                                                                                                                                                                         |
| --------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ack                               | `📷 Reading image…` / `📷 Reading images…`                                  | `🎙️ Transcribing voice note…`                                | `🎬 Extracting audio from video…`                                 | `📄 Reading *{file_name}*…`                      | `📤 Saving {form_name} as a Kaizen draft…`                                                                                                                                          |
| Slow-progress (replaces ack)      | `📷 Still reading…` (after 8 s)                                             | —                                                            | —                                                                 | —                                                | `📤 Still saving {form_name} — Kaizen is loading the form…` then `📤 Filling fields in {form_name} — almost there…` then `📤 Verifying the save on Kaizen — this is the last step…` |
| Success (new case)                | `📷 Image read. Finding matching forms…`                                    | `🎙️ Voice note read. Finding matching forms…`                | n/a                                                               | `📄 *{file_name}* read. Finding matching forms…` | `✅ Saved as a Kaizen draft.`                                                                                                                                                       |
| Success (refining existing draft) | `📷 Got it — updating draft…`                                               | `🎙️ Got it — updating draft…`                                | `🎬 Got it — updating draft…`                                     | `📄 Got it — updating draft…`                    | n/a                                                                                                                                                                                 |
| Error                             | `⚠️ Couldn't read image. Try a clearer photo or describe the case in text.` | `⚠️ Couldn't transcribe voice note. Try again or send text.` | `⚠️ Couldn't extract audio from video. Try a voice note or text.` | `⚠️ Couldn't read that file. Try text instead.`  | `❌ Filing failed. Try again or start fresh.`                                                                                                                                       |

### Slow-progress contract

For OCR, transcription, or filing that may take longer than the user's
patience window, follow the `_run_image_progress` pattern in `backend/bot.py`:

1. Send the initial ack once.
2. Schedule one replacement edit after a delay (`asyncio.Event` + `wait_for`).
3. Set the event before the success/error edit so the reassurance is
   suppressed when work finishes in time.
4. Cancel the task on the way out and swallow the resulting `CancelledError`.

Never concatenate `"\n"`-joined status lines onto the ack — that reads as
the bot repeating itself.

### Errors

- One ⚠️ line per error. State the cause in present tense (`Couldn't read
image`), then a one-clause recovery (`Try a clearer photo or describe the
case in text.`). No stack traces, no apologies, no "please".
- Recovery clause is mode-aware: new-case flow says "describe the case in
  text", existing-draft flow says "Type your feedback instead", template
  review says "Try again or send text".

### Pre-save gate (Save as draft)

When the user taps **Save as draft** the bot checks the draft against the
DOPS quality gate before calling Kaizen. The gate has two tiers:

- **Blocking (genuinely unsafe / near-empty draft).** The procedure name is
  absent, or the narrative slot has no clinical substance at all (thin
  `case_observed` AND both `indication` and `trainee_performance` empty).
  The bot sends a fresh `🟡 *{form_name} needs a bit more detail before I
file it.*` message with the missing fields and the Save / Quick improve /
  Cancel keyboard. The reviewed draft preview stays visible.
- **Warning (recoverable gap, save proceeds).** Missing date, missing
  stage, one missing semantic block, or rough reflection wording. The bot
  sends a fresh `🟡 *Saving {form_name} despite some gaps.* Add these in
Kaizen after the save:` message and then calls the filer normally. The
  reviewed draft preview stays visible; the user remains in control of
  their explicit Save.

Both tiers always arrive as **new messages**, never by editing the
reviewed draft preview — the user just approved that preview and should
not lose the context they were looking at.

### Safety-critical templates

These stay fixed in `backend/message_policy.py` (`MessageClass.FIXED`):
welcome (connected/disconnected), "what is this?", file-case prompt,
captured ack, thin-case detail request, AI unavailable, photo privacy
nudge, and the draft reply hint. LLM-assisted prose is allowed only on
explicitly low-risk explanation/recovery paths (e.g. `answer_question`).

### Known minor inconsistencies (deferred)

The codebase has a handful of small wording drifts that are noted but not
worth a churn-PR on their own:

- `🎙️ Transcribing…` vs `🎙️ Transcribing voice note…` — same intent.
- `Try a clearer photo or text.` vs `Try a clearer photo or describe the
case in text.` — same intent, different verbosity.
- `Got it — updating template…` vs `Got it — updating draft…` — separate
  flow contexts, both correct as-is.

If you touch one of these surfaces for unrelated reasons, normalise to the
preferred copy in the table above while you're there.

---

## FORM_UUIDS (in extractor.py)

```python
FORM_UUIDS = {
    "CBD":      "3ce5989a-b61c-4c24-ab12-711bf928b181",  # CBD 2025 update
    "DOPS":     "159831f9-6d22-4e77-851b-87e30aee37a2",  # DOPS ST3-ST6 2025
    "LAT":      "eb1c7547-0f41-49e7-95de-8adffd849924",  # LAT 2025 v9
    "ACAT":     "6577ab06-8340-47e3-952a-708a5f800dcc",  # ACAT ACCS 2025
    "ACAF":     "15e67ae8-868b-4358-9b96-30a4a272f02c",
    "STAT":     "41ff54b8-35a7-414b-9bd6-97fb1c3eb189",
    "MSF":      "5f71ac04-ff45-44d2-b7a1-f8b921a8a4c8",
    "MINI_CEX": "647665f4-a992-4541-9e17-33ba6fd1d347",
    "JCF":      "3daa9559-3c31-4ab4-883c-9a991632a9ca",
    "QIAT":     "a0aa5cfc-57be-4622-b974-51d334268d57",
}
```

---

## Pending scope

See Notion → Portfolio Guru → Current State → v2.1 scope section.
