# Portfolio Guru message standard

Every message the bot sends a doctor must be readable in about three seconds on
a phone, and never so terse that the doctor can't tell what happened or what to
do next. This applies to every new feature. `backend/tests/test_message_standard.py`
enforces the length budgets automatically.

## Shape

1. **Lead line**: one emoji, then what happened or what this screen is.
   Menus and choice screens use a bold title (`⚙️ Settings`, `🔗 *Connect Kaizen*`).
2. **Body**: at most about three short lines or bullets, one idea each.
   Labelled lines (`Plan: Unlimited`) beat sentences on status screens.
3. **Next step**: one clear action ("Tap Retry", "Send the case"), unless the
   buttons already make it obvious. Don't restate button labels in prose.

## Length budgets

Fixed text only; user data (drafts, case text, form names) and the shared
list of accepted inputs (`MODALITY_CLAUSE`) don't count.

| Kind                   | Examples                                        | Budget               |
| ---------------------- | ----------------------------------------------- | -------------------- |
| Confirmation           | "✅ Kaizen connected."                          | ≤ 120 characters     |
| Prompt or question     | ask for a detail, the username, a case          | ≤ 200                |
| Error or problem       | sign-in failed, AI unavailable                  | ≤ 220                |
| Menu, settings, choice | Settings, Connect Kaizen                        | ≤ 320 and ≤ 10 lines |
| Help or explainer      | /help, what can you do, setup guide             | ≤ 450                |
| Exempt                 | drafts, reports, consent/legal text, AI answers | reviewed separately  |

AI-written answers follow `FLEXIBLE_REPLY_STYLE_ENVELOPE` in `message_policy.py`.

## Not too short

Shortening must never lose:

- what happened, and what the doctor does next;
- safety facts: drafts only, nothing saved to Kaizen without approval, never
  submitted to a supervisor, credentials stored encrypted, deleted with /reset,
  anonymise patient details;
- the password option first and marked "(recommended)" when connecting Kaizen.

An error says what went wrong, that the doctor's case is safe (when it is), and
the next step.

## Language

- Plain British English, second person, "I" for the bot. Contractions are fine.
- No paragraph longer than two sentences. No helper sentences that restate the
  obvious ("Pick what you want to change").
- No internal words (session, token, CDP, selector, LLM, DOM). Use words doctors
  know: Kaizen, draft, form, case, supervisor.
- Calm and factual. No marketing, hype or exclamation stacks.

## Emoji and layout

- One leading emoji per message (🩺 for free-form answers). No decorative emoji
  (✨ 🤖 🎉 ⭐). Button emoji follow `test_action_label_emoji_policy.py`.
- A blank line between blocks. Bullets use "•". Bold only for titles and option
  names, and only in Markdown messages.

## Adding a message

- Put reusable copy in `MESSAGE_TEMPLATES` (`backend/message_policy.py`) or a
  named `*_TEXT` / `*_MSG` constant in `backend/bot.py`.
- Give every new template a kind in `test_message_standard.py`; the test fails
  until you do, and fails if the text goes over its budget.
