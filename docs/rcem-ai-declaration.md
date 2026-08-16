# RCEM AI-use declaration

## The requirement

RCEM, _Position on the use of AI in Reflective Logs_ (September 2025) —
<https://rcem.ac.uk/wp-content/uploads/2025/09/RCEM-AI-statement.pdf>

Section 5: "If AI tools are used to support the reflective process, this
should be declared within the log (e.g., 'AI was used to help structure and
edit this reflection')." The statement also holds the resident doctor
responsible for the accuracy, authenticity and insightfulness of the
reflection.

The College gives an example sentence. It does not mandate wording.

## What Portfolio Guru does

Every entry Portfolio Guru drafts is AI-assisted, so every entry it saves
carries a declaration. Implementation: `backend/ai_declaration.py`.

- **Where it goes.** Appended to the entry's own narrative field — the
  reflection, or the closest reflective free-text field the form has. Not the
  Kaizen timeline Description, which is a one-line summary rather than part of
  the log.
- **How many times.** Once per entry, in one field. A form with six free-text
  boxes (REFLECT_LOG) gets one declaration, in the most reflective of them.
  Repeating it in every box would read as noise and make the entry look
  machine-stamped rather than declared.
- **Which forms.** All of them, not just reflective logs. RCEM's statement is
  scoped to reflective logs, but any entry drafted with model assistance was
  AI-assisted, so the broader default is the safer one. Seven of the 74 mapped form types carry
  nothing because they have no free-text control at all: STAT, JCF, AUDIT and
  RESEARCH (plus their `_2021` variants). These are attendance and register
  entries with no reflection to declare against.
- **When it is skipped.** If the target narrative field is empty. Writing a
  declaration into a field the doctor still has to complete would make an
  unanswered field look answered.
- **Consent.** The exact sentence is shown in the draft preview above the
  approval buttons, so approving the draft approves the declaration too.
- **Idempotent.** Re-filing a draft that already carries a declaration does not
  add a second one.

## Wording and configuration

Because the College may specify preferred wording later, both the label and the
sentence are runtime-overridable — no code change needed.

| Variable                  | Effect                                                     |
| ------------------------- | ---------------------------------------------------------- |
| `PG_AI_DECLARATION`       | `0`/`false`/`no`/`off` disables the declaration entirely   |
| `PG_AI_DECLARATION_LABEL` | Overrides the section label (default `AI use declaration`) |
| `PG_AI_DECLARATION_TEXT`  | Overrides the sentence                                     |

Default text:

> AI use declaration: AI was used to help structure and edit this entry. The
> content, accuracy and reflective insight are my own.

An email asking RCEM for preferred wording has been drafted. If they reply with
specific text, set `PG_AI_DECLARATION_TEXT` and update the default here.

## Tests

`backend/tests/test_ai_declaration.py`, wired into `scripts/verify_changed.sh`.
It pins the declaration landing in the filed entry, the preview showing it
before approval, one declaration per entry, idempotency, the env overrides,
and — via `EXEMPT_FORM_TYPES` — that no new mapped form type can ship silently
undeclared.

## Verification record (2026-08-16)

Risk class 3 (visual: draft preview changed) with filing behaviour touched.

| Evidence                                                   | Result                                                                                                                                                                              |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bash scripts/verify_changed.sh`                           | PASSED, 447 tests                                                                                                                                                                   |
| `bash scripts/preflight.sh`                                | Passed, 2416 tests (needs `FERNET_SECRET_KEY` in the shell)                                                                                                                         |
| `bash scripts/telegram_qa_offline.sh`                      | Passed. All 8 golden cases across both personas render the declaration in the approval preview through the real handler stack                                                       |
| Live Kaizen draft save (CBD + REFLECT_LOG, CDP/Playwright) | Saved. Read back from the live Kaizen DOM: declaration present once, in `reflection`, and in no other field. REFLECT_LOG has six free-text boxes and only the reflection carries it |

Still pending: a screenshot of the declaration on the real Telegram surface,
and independent review of the diff by someone other than the builder.

The two live test drafts (`[AIDECL-4fbe7244] Test entry.`) were deleted from
Kaizen after the read-back, each from its own `/events/view-section/<id>` page
and only after confirming the run token was on that page. The three unrelated
drafts in the account were left untouched. Nothing was ever submitted.

Note for anyone repeating this: the saved-drafts list pages at five rows, so
deleting two makes two older drafts appear. Compare drafts by id, not by count.
