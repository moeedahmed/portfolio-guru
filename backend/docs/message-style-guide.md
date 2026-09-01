# Bot Message Style Guide

## Principles

- **Mobile-first** — messages render on small screens. Keep paragraphs short (2-4 lines).
- **One action per message** — each bot message should have at most one primary action the user can take.
- **Emoji header** — every substantive bot message starts with one functional emoji that signals the message type.
- **Structure** — header line, then detail/summary, then action instruction. Separated by blank lines.
- **No Markdown tables** — they don't render well on mobile.
- **No raw internal codes** — user-facing text never contains implementation codes like "PROC_LOG" or "MINI_CEX". Common clinician-facing acronyms such as CBD, DOPS, Mini-CEX and ACAT may appear in compact explanatory lists when they are clearer for RCEM users.
- **No raw errors** — error details are logged server-side. The user sees a plain-English explanation and a recovery action.
- **Draft-only framing** — all entries are described as saved to Kaizen as drafts, never as filed or submitted. Supervisor submission is never automatic.
- **Professional emoji only** — avoid decorative/consumer emoji (✨ sparkles, 🤖 robot, ⭐ stars, 🎉 party). Prefer functional emoji that signal message type (✅, ⚠️, 📋, 📤).
- **Emoji headings, plain rows** — use one functional emoji on the message header, and on secondary section headings only when the section changes meaning. Do not put emojis on every row, sentence, metadata label, or body line.
- **Buttons carry outcomes** — the message explains the object and context; the button names the action or result. Retain a functional emoji when it materially improves recognition, especially for primary progression and destructive actions; never add one as decoration.

## Mobile Button Standard

- Use the shortest unambiguous outcome label. One word is appropriate only when the message and action make the object unmistakable: `Forms`, `Back`, `Retry`, `Edit`.
- One or two words is the normal range, not a target to minimise at any cost: `Choose form`, `New case`, `Reflective log`, `Update draft`, `Writing style`.
- Put at most two compact buttons in a row. Pack two related compact actions together; reserve a full row for a long label or a consequential action.
- Identical actions use identical labels across journeys. Do not switch between variants such as `Try again` / `Retry` or `See all forms` / `Pick form manually`.
- Do not repeat the object in the button when the message already names it. Prefer `Back` over `Back to settings`, and `Edit` over `Keep editing this draft`.
- Do not use bare `Yes` / `No` where the outcome is safer. Use labels such as `Delete data` / `Keep data`.
- Keep protected actions explicit even when that needs more than two words or a full row. This includes saving to Kaizen, payment or upgrade, consent, credential connection, deletion/reset, and opening an external page. Preserve every confirmation gate.
- Form recommendations state the best fit in the message. The first row contains the compact form choices, for example `CBD` and `Reflective log`; the best-fit button keeps its existing action ID. Selecting any form starts drafting immediately, with no extra confirmation step.
- Gathering does not promise a draft before the form is selected: use `📋 Choose form` and `❌ Discard case`, then show the recommended form choices.
- Decorative emoji, including stars, do not belong in button labels.

Canonical compact labels:

- `FORM|show_all` → `Forms`
- Reflective Practice Log form choice → `Reflective log`
- Finish gathering and show form choices → `Choose form`
- Discard the captured case → `Discard case`
- Abandon form selection and return to a fresh case → `Restart`
- Back navigation → `Back`
- Retry/recovery → `Retry`
- Start another case → `New case`
- Draft refinement → `Edit` or `Improve`, according to the actual outcome
- Cancel without an external effect → `Cancel`
- Kaizen draft save → `Save to Kaizen`
- Credential setup → `Connect Kaizen` or `Reconnect Kaizen`
- External destinations → explicit labels such as `Open Kaizen` or `Open saved draft`

## Emoji Categories

| Emoji | Meaning                                     |
| ----- | ------------------------------------------- |
| 🩺    | Portfolio Guru identity / welcome / general |
| 📥    | Case captured / input received              |
| ⚠️    | Warning or attention needed                 |
| ❌    | Error or failure                            |
| ✅    | Success / complete                          |
| 🔑    | Credentials / login / setup                 |
| 🔗    | Link, reconnect or open external account    |
| 🔒    | Privacy / security note                     |
| 🔄    | Retry, reset, sync or refresh               |
| ⏳    | Waiting / in progress                       |
| 📤    | Saving / uploading / filing in progress     |
| 📎    | Attachment                                  |
| ✏️    | Edit / refine                               |
| ✍️    | Writing style                               |
| 📋    | Form / WPBA reference                       |
| 💬    | Reply / chat action hint                    |
| 💡    | Tip / improvement suggestion                |
| ⚙️    | Settings                                    |
| 🔙    | Back navigation                             |
| 🔎    | Evidence basis / inspect                    |
| 📈    | Activity snapshot                           |
| 💳    | Payment / upgrade                           |
| 📖    | Reading / learning from evidence            |
| 🗣️    | Voice profile / voice input                 |

## Message Structure

```
{emoji} {Header line — what happened}

{Detail / summary — what the user needs to know}

{Action instruction — what the user should do next}
```

## Gathering Mode Messages

First capture:

```
📥 Case captured.

Send another anonymised message to add details.

When you're ready, tap Choose form.
```

Buttons: `📋 Choose form` · `❌ Discard case`

Attachment-only capture:

```
📎 Image attached.

Add anonymised case details before choosing a form.

For ECGs, ultrasound, X-rays, wounds or procedure images, send your own interpretation/context before drafting.
```

Buttons: `📋 Choose form` · `❌ Discard case`

If the user selects `Choose form` before sending any readable case context, keep
the attachment saved and ask for anonymised text or voice context instead of
drafting from the attachment alone.

When the user selects `Choose form`, this message is edited in place to the full CAPTURED_ACK
("📥 Captured. I'll turn this into portfolio evidence…") and the keyboard is
removed; the form recommendation arrives as a new message.

When the user selects `Discard case`, the captured case is discarded and the bot returns to
the standard ready state.

After subsequent messages:

```
📥 Case captured.

Send another anonymised message to add details.

When you're ready, tap Choose form.
```

Completion prompt (when user says "done" or taps button):

```
📥 Ready for the next step.
```

Form recommendation:

```
📋 Best fit: Case-Based Discussion

- Case-Based Discussion: Best matches the clinical reasoning in this case.
- Reflective Practice Log: Fits if the main purpose is personal learning.

Select a form to draft it.
```

First row: `CBD` · `Reflection`

Second row: `Forms` · `Cancel`

## Example Existing Messages

Ready prompt (connected):

```
🩺 Ready.

Send an anonymised case as text, voice, photo, or document.
```

Step 1:

```
🔗 Step 1 of 3: connect Kaizen

What's your Kaizen username (email)?

🔒 _I'll store it encrypted and use it only to connect to Kaizen and save drafts you approve._
```

Connected welcome:

```
🩺 Portfolio Guru is ready.

Send an anonymised case: text, voice, photo, or document.

I'll suggest the form, prepare the draft, and ask before saving to Kaizen.
```

Draft saved:

```
✅ Draft saved in Kaizen
{Form name}
Date: {date}
Curriculum: {SLO/KC}
{field count} fields completed
{case count} case(s) this month

📎 Attachment not added
{reason}. Draft saved without the attachment.
```

Settings:

```
⚙️ Settings

Plan: Unlimited
Cases filed: 10 this month
Kaizen evidence: synced 5 Jul 2026 18:30 BST. Items indexed: 412

Writing style: Active
Drafts already use your writing style.

Portfolio defaults: HST Profile · CCT · 2025 Update

Pick what you want to change.
```

Help:

```
📖 Portfolio Guru — Help

How it works:
📝 Describe → 🔍 I pick the form → ✅ You approve → 📤 Saved as Kaizen draft

What you can send:
Text, voice note, photo, or document (PDF, PPTX, Word)

What I do:
Suggest the best form, extract all the fields, show you a draft to review
and edit, then save as a Kaizen draft when you approve.

Draft-only — entries are saved as Kaizen drafts. Supervisor submission is
never automatic.
```

## Health Wording

Portfolio Health is read-only portfolio evidence planning support. Avoid
language that implies a formal assessment, clinical evaluation, or guaranteed
outcome:

- Use "ARCP evidence review", not "ARCP readiness check".
- Use "suggested filing actions", not "urgent filing actions".
- Use "gap analysis" or "evidence review", not "readiness scoring".
- CESR pathway: "building toward application", not "on track for".

## Proof Report Wording

Proof reports are trust-layer summaries, not operational logs. Avoid raw
operational detail (source type, WPBA codes, internal state labels):

- Use "Draft saved" / "Save not confirmed" / "Filing stopped", not
  "Filed as draft" / "Failed / blocked".
- Use "Next: ..." for action guidance, not separate "Not done" lines.
- Use "Issue: ..." for blockers, not "Blocker: ...".
- Sanitise issues before display; raw exception text belongs in logs.
- Never mention "no supervisor request sent" or "no final submission made"
  in user-facing text — these are product invariants, not per-filing facts.
