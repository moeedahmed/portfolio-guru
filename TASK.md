# Active Task - Phase 2.5 Source-Grounded Image Drafting

## Objective

Stop image/photo-derived cases from producing fabricated portfolio drafts before conversational routing is activated further.

## Scope

Phase 2.5 only:

- Tighten image extraction so screenshots/photos produce source-grounded facts only.
- Pass `input_source` into form recommendation and draft extraction.
- Add image-source prompt guards for recommendation and extraction.
- Strip high-risk unsupported resuscitation/cardiac narrative from image-derived draft fields.
- Add regression tests for the rib fracture / regional block fabrication incident.

## Done

- Phase 2 shadow routing remains implemented and passive.
- Source-grounding guard added for photo/image-derived recommendations and drafts.
- Image-derived draft regeneration keeps using the original input source.
- Regression tests cover the CPR/ALS/ROSC fabrication failure mode.

## Guardrails

- No separate bot.
- No Kaizen filing behaviour changes.
- No billing or credential behaviour changes.
- No router-controlled Telegram replies yet.
- Existing buttons/workflows remain intact.
- Text/voice-authored resuscitation cases must not be stripped just because they mention CPR/ROSC.

## Verification

- Source-grounding tests pass.
- Focused extraction/conversation/flow tests pass.
- Full offline pre-commit test gate must pass before commit.

## Next Phase

Phase 3 stays paused until the source-grounding patch has survived real image/photo usage. Then activate the router only for low-risk intents:

- `portfolio_question`
- `unknown`
- `account_or_billing`
- `setup_or_credentials`

Keep case creation and filing on existing deterministic paths until shadow logs prove reliability.
