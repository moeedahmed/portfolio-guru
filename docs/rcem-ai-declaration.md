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
- **Which forms.** All of them, not just reflective logs. RCEM's statement is
  scoped to reflective logs, but any entry drafted with model assistance was
  AI-assisted, so the broader default is the safer one. Seven form types carry
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
before approval, idempotency, the env overrides, and — via
`EXEMPT_FORM_TYPES` — that no new mapped form type can ship silently
undeclared.
