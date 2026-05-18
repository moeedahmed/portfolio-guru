# Active Task - Conversational Router Phase 2

## Objective

Run the conversational router in passive shadow mode so Portfolio Guru can learn from real ordinary text messages without changing user-visible behaviour.

## Scope

Phase 2 only:

- Call the standalone router for ordinary text messages.
- Log intent, confidence, signals, handler name, and message length.
- Preserve all existing handler decisions and replies.
- Do not use router output to control workflow yet.
- Add tests proving shadow logging is passive.

## Done

- `handle_case_input` schedules shadow routing for ordinary text.
- `handle_mid_conversation_text` schedules shadow routing for ordinary text.
- Shadow routing logs structured router output only.
- Router failures are caught and logged without affecting the user flow.
- Tests prove a deliberately wrong shadow intent does not override existing menu routing or mid-conversation behaviour.

## Guardrails

- No separate bot.
- No Kaizen filing behaviour changes.
- No billing or credential behaviour changes.
- No router-controlled Telegram replies yet.
- Existing buttons/workflows remain intact.

## Verification

- Focused router + flow-walker tests pass.
- Full offline pre-commit test gate must pass before commit.

## Next Phase

Phase 3: activate the router only for low-risk intents:

- `portfolio_question`
- `unknown`
- `account_or_billing`
- `setup_or_credentials`

Keep case creation and filing on existing deterministic paths until shadow logs prove reliability.
