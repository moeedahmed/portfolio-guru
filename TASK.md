# Active Task - Conversational Router Phase 1

## Objective

Add a tested, non-invasive conversational intent router so Portfolio Guru can later understand natural messages without breaking existing deterministic workflows.

## Scope

Build Phase 1 only:

- Create a router contract for ordinary user messages.
- Classify user intent into known route types.
- Return structured output only.
- Add focused tests for representative Portfolio Guru messages.
- Do not wire the router into live Telegram handling yet.

## Intent Types

- `new_case`
- `portfolio_question`
- `edit_draft`
- `file_to_kaizen`
- `account_or_billing`
- `setup_or_credentials`
- `unknown`

## Guardrails

- No separate bot.
- No Kaizen filing behaviour changes in Phase 1.
- No billing or credential behaviour changes in Phase 1.
- Buttons and existing workflows remain intact.
- Unknown input must produce a useful clarification route, not silence.

## Verification

Before this task is complete:

- New router tests pass.
- Existing relevant bot/extractor tests pass.
- Full offline pre-commit test gate passes before commit.

## Git

Branch: `feature/conversational-router`

Checkpoint before implementation:
- DeepSeek primary extractor work committed and pushed on `main`.
