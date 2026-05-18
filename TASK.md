# Active Task - Phase 2.6 Message and Workflow Hardening

## Objective

Make Portfolio Guru's user-facing workflow messages less brittle before conversational routing is activated further, without letting the bot free-write safety-critical copy.

## Scope

Phase 2.6 only:

- Add a small message policy/template layer for high-value fixed and templated messages.
- Classify message surfaces as fixed, templated, or LLM-assisted.
- Keep deterministic workflow states, filing, billing, credentials, and safety warnings fixed.
- Tighten mobile copy for welcome, help/about, case prompt, captured ack, thin-case blocker, recommendation, AI unavailable, privacy nudge, and draft reply hint.
- Add tests proving plain fixed/templates do not leak raw Markdown markers.

## Done

- Phase 2 shadow routing remains implemented and passive.
- Source-grounding guard added for photo/image-derived recommendations and drafts.
- Image-derived draft regeneration keeps using the original input source.
- Regression tests cover the CPR/ALS/ROSC fabrication failure mode.
- Message policy layer added for the first high-value workflow surfaces.
- Snapshot tests updated for changed visible copy.

## Guardrails

- No separate bot.
- No Kaizen filing behaviour changes.
- No billing or credential behaviour changes.
- No router-controlled Telegram replies yet.
- Existing buttons/workflows remain intact.
- Text/voice-authored resuscitation cases must not be stripped just because they mention CPR/ROSC.
- No free-form LLM control of filing, billing, credentials, confirmations, or safety warnings.

## Verification

- Source-grounding tests pass.
- Focused extraction/conversation/flow tests pass.
- Message policy and snapshot tests pass.
- Full offline pre-commit test gate must pass before commit.

## Next Phase

Phase 3 stays paused until the source-grounding patch has survived real image/photo usage. Then activate the router only for low-risk intents:

- `portfolio_question`
- `unknown`
- `account_or_billing`
- `setup_or_credentials`

Keep case creation and filing on existing deterministic paths until shadow logs prove reliability.
