# Bot Message Style Guide

## Principles

- **Mobile-first** — messages render on small screens. Keep paragraphs short (2-4 lines).
- **One action per message** — each bot message should have at most one primary action the user can take.
- **Emoji header** — every substantive bot message starts with an emoji that signals the message type.
- **Structure** — header line, then detail/summary, then action instruction. Separated by blank lines.
- **No Markdown tables** — they don't render well on mobile.
- **No raw internal codes** — user-facing text never contains implementation codes like "PROC_LOG" or "MINI_CEX". Common clinician-facing acronyms such as CBD, DOPS, Mini-CEX and ACAT may appear in compact explanatory lists when they are clearer for RCEM users.
- **No raw errors** — error details are logged server-side. The user sees a plain-English explanation and a recovery action.
- **Draft-only framing** — all entries are described as saved to Kaizen as drafts, never as filed or submitted. Supervisor submission is never automatic.
- **Professional emoji only** — avoid decorative/consumer emoji (✨ sparkles, 🤖 robot, ⭐⭐ stars, 🎉 party). Prefer functional emoji that signal message type (✅, ⚠️, 📋, 📤).

## Emoji Categories

| Emoji | Meaning                                     |
| ----- | ------------------------------------------- |
| 🩺    | Portfolio Guru identity / welcome / general |
| 📥    | Case captured / input received              |
| ⚠️    | Warning or attention needed                 |
| ❌    | Error or failure                            |
| ✅    | Success / complete                          |
| 🔑    | Credentials / login / setup                 |
| 📤    | Saving / uploading / filing in progress     |
| ✏️    | Edit / refine                               |
| 📋    | Form / WPBA reference                       |
| 💬    | Reply / chat action hint                    |
| 💡    | Tip / improvement suggestion                |
| 🔙    | Back navigation                             |
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
📥 Captured. Add anything else before I draft this?
```

Buttons: `✅ Draft now` · `❌ Cancel`

When user taps ✅ Draft now, this message is edited in place to the full CAPTURED_ACK
("📥 Captured. I'll turn this into portfolio evidence…") and the keyboard is
removed; the form recommendation arrives as a new message.

When user taps ❌ Cancel, the captured case is discarded and the bot returns to
the standard ready state.

After subsequent messages:

```
📥 Noted. Add anything else before I draft this?
```

Completion prompt (when user says "done" or taps button):

```
📥 Ready for the next step.
```

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
✅ Kaizen draft saved
{Form name} saved as a Kaizen draft. 📅 {date}
{field count} fields completed.
📊 {case count} case(s) this month ({tier})
📎 Attachment not added: {reason}. Draft saved without it.
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
