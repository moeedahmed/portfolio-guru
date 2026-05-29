# Bot Message Style Guide

## Principles

- **Mobile-first** — messages render on small screens. Keep paragraphs short (2-4 lines).
- **One action per message** — each bot message should have at most one primary action the user can take.
- **Emoji header** — every substantive bot message starts with an emoji that signals the message type.
- **Structure** — header line, then detail/summary, then action instruction. Separated by blank lines.
- **No Markdown tables** — they don't render well on mobile.
- **No internal codes** — user-facing text never contains form codes like "CBD", "DOPS", "PROC_LOG". Use "Case-Based Discussion", "Direct Observation of Procedural Skills", "Procedural Log".
- **No raw errors** — error details are logged server-side. The user sees a plain-English explanation and a recovery action.

## Emoji Categories

| Emoji | Meaning |
|---|---|
| 🩺 | Portfolio Guru identity / welcome / general |
| 📥 | Case captured / input received |
| ⚠️ | Warning or attention needed |
| ❌ | Error or failure |
| ✅ | Success / complete |
| 🔑 | Credentials / login / setup |
| 📤 | Saving / uploading / filing in progress |
| ✏️ | Edit / refine |
| 📋 | Form / WPBA reference |
| 💬 | Reply / chat action hint |
| 🔙 | Back navigation |

## Message Structure

```
{emoji} {Header line — what happened}

{Detail / summary — what the user needs to know}

{Action instruction — what the user should do next}
```

## Gathering Mode Messages

First capture:
```
📥 Got it.

Want to add a photo, voice note, or more detail?
```

After subsequent messages:
```
📥 Noted.

Add more or tap Done when ready.
```

Completion prompt (when user says "done" or taps button):
```
📥 Ready for the next step.
```

## Example Existing Messages

Welcome:
```
🩺 Portfolio Guru — RCEM portfolio drafts from rough notes.

Send a case by text, voice, photo, or document. I'll match it to the right form
(CBD, DOPS, Mini-CEX, ACAT, reflections, teaching, procedurals, and more)
and draft only after you choose.

I won't invent clinical detail. Missing fields stay blank, and nothing is filed
until you approve it.

Tap 🔗 Connect to start.
```

Connected welcome:
```
🩺 Portfolio Guru is ready.

Send what happened — text, voice, photo, or document. Rough notes are enough;
I'll only use what you send and won't invent clinical detail.
```

Draft saved:
```
✅ Kaizen draft saved
{Form name} saved as a Kaizen draft. 📅 {date}
{field count} fields completed.
```
