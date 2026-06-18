# Telegram Rich Message Rendering Guide

Practical rendering policy for the Hermes profile on the Portfolio Guru
test bot. Covers what is reliably available, what is conditionally
available (and how to verify), and the fallback rules that ensure every
message is intelligible even when rich rendering fails.

**Scope.** This guide applies to the test bot only — the live beta bot
uses the same python-telegram-bot stack but is a separate process with
its own token (`PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN`). The test bot token
(BWS secret name: `TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST`; OpenClaw/runtime
alias: `PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN`) is separate and must
never be shared with or co-polled alongside the live token. See
[`INTEGRATION_GUIDE.md`](INTEGRATION_GUIDE.md) for the full token
isolation rules.

Rendering choices in this guide never change the deterministic engine
boundary: form recommendation, fact extraction, and Kaizen draft saves
remain in the Portfolio Guru deterministic engine. This guide only
controls how the engine's `ChannelReply` objects and conversational
turns are presented to the user via Telegram message formatting.

---

## Parse modes

Telegram supports two structured parse modes: `HTML` and `MarkdownV2`.

**Use `HTML` for all generated messages.** `MarkdownV2` requires every
special character (`_ * [ ] ( ) ~ \` > # + - = | { } . !`) to be escaped
with a preceding `\`; generated text almost always contains unescaped
characters that silently corrupt the render. `HTML` is unambiguous and
easier to generate safely.

### Available HTML tags (all clients, all ptb versions)

| Tag | Renders as |
|---|---|
| `<b>text</b>` | **bold** |
| `<i>text</i>` | *italic* |
| `<u>text</u>` | underline |
| `<s>text</s>` | ~~strikethrough~~ |
| `<tg-spoiler>text</tg-spoiler>` | spoiler (blurred until tapped) |
| `<code>text</code>` | inline monospace |
| `<pre>text</pre>` | monospace block |
| `<a href="...">text</a>` | hyperlink |

None of these require a specific Bot API version beyond the baseline;
they are safe to use unconditionally.

---

## Inline keyboards

Use `InlineKeyboardMarkup` with one `InlineKeyboardButton` per row for
all confirmation, selection, and approval actions.

```
[✅ Approve]
[✏️ Edit draft]
[✕ Cancel]
```

Rules:
- **One action per button.** Do not combine two decisions in one label.
- **Maximum four rows.** More rows are cut off on small screens without
  scrolling; use a numbered list instead for long option sets.
- **`callback_data` is the stable action ID.** Labels are human-facing
  and may change; the engine dispatches on `action_id`, never on the
  label text. See `channel_actions.ChannelAction` for the contract.
- **Render via `channel_actions.to_telegram_keyboard`.** This function
  produces the correct `InlineKeyboardMarkup` from a `ChannelReply` and
  returns `None` when there are no actions, so you can pass it straight
  to `reply_text(..., reply_markup=...)`.

---

## Conditionally available features

These require a specific Bot API version or python-telegram-bot release.
Verify availability before depending on them.

### Expandable blockquotes (`<blockquote expandable>`)

Introduced in Bot API 9.0. Renders as a collapsible section: the user
sees a preview and taps to expand. Useful for long draft previews.

```html
<blockquote expandable>
<b>Draft preview</b>
Setting: Emergency Department, resus
Presentation: …
</blockquote>
```

**Verify ptb support before using.** As of `python-telegram-bot` v21.x,
wrapping for Bot API 9.x features may be partial. To check:

```python
from telegram import __version__ as ptb_version
# Compare against the changelog to confirm expandable blockquote support.
```

If the tag is sent to a client or ptb version that does not support it,
the tag is rendered as literal text. **Always have a fallback.**

**Fallback:** Send the first 200 characters of the draft as a plain
message and offer a "📋 Show full draft" inline button that triggers the
full text in a follow-up message.

### Message effects (Bot API 9.0+)

Cosmetic reaction effects on send (e.g. fireworks, confetti). These are
purely decorative and are never required for correctness. Do not use
for approval or confirmation flows where the user must understand the
outcome from the message alone.

---

## Simulated patterns (no native Telegram equivalent)

These patterns are built from emoji and text, not from Telegram-native
block types. Use them consistently so the UI is familiar, but never
describe them as "native Telegram blocks."

### Emoji task list

Use ✅ for completed items and ⬜ for pending items:

```
✅ Setting recorded
✅ Presentation recorded
⬜ Learning point — not supplied; will be blank in draft
```

### Section headings

Use `<b>Section Name</b>` followed by a blank line:

```html
<b>Draft preview</b>

<b>Form:</b> Case-Based Discussion (CBD)
<b>Date:</b> 30/06/2026
```

---

## Rendering policy by message context

### Draft preview

1. Try `<blockquote expandable>` with the full structured draft (HTML).
2. If not supported, send a truncated preview (≤200 chars) with a
   "📋 Show full draft" button.
3. Always follow with an inline keyboard: `[✅ Approve]`, `[✏️ Edit]`,
   `[✕ Cancel]`.
4. Include an emoji task list of any blank fields so the user sees what
   is missing before approving.

### Form recommendation

1. Short lead message: `🩺 I recommend <b>Case-Based Discussion (CBD)</b>.`
2. One inline button per recommended form (up to three).
3. If more than three forms apply, use a numbered list and ask the user
   to reply with the number.

### Capability overview / help

1. Plain bullet list with emoji markers (no keyboard needed).
2. End with: `📥 Send your case notes to start.`

### Acknowledgement of inbound content

1. Single short message: `📥 <b>Captured.</b> Adding to your case…`
2. No keyboard unless a decision is required.

### Error or refusal

1. Single message. No inline keyboard unless a recovery action is
   available.
2. Always state what the user can do next.

### Numbered-channel fallback (WhatsApp / SMS / plain text)

Use `channel_actions.render_numbered(reply)` which produces a
plain-text numbered list from a `ChannelReply`. This path requires no
Telegram library and is always available as a last resort.

---

## Fallback hierarchy

Apply these in order when a richer option fails or is unsupported:

1. **HTML parse mode** with inline keyboard.
2. **Plain text** with emoji structure markers + inline keyboard.
3. **Plain text** with numbered options (`Reply with 1 for CBD, 2 for DOPS`).
4. **Bare plain text** with the most important sentence only.

Never send a message that relies on rich rendering to be comprehensible.
The user must be able to act on step 4 alone.
