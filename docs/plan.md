# Portfolio Guru - Conversational Router Plan

## Goal

Make Portfolio Guru feel like a natural portfolio assistant while preserving the deterministic Kaizen workflows that already work.

The product should accept messy doctor-language, understand intent, ask one useful clarifying question when needed, then route into safe structured execution. Filing, billing, credential handling, and Kaizen submission remain deterministic and confirm-before-action.

## Product Decision

Keep one bot. Do not create a separate conversational bot.

Reason: a separate bot would duplicate auth, persistence, Kaizen credentials, billing, usage limits, support surface, and user memory. The better architecture is one Portfolio Guru bot with a conversational layer in front of the existing workflow engine.

## Architecture

### Layer 1 - Natural conversation intake

Accept ordinary messages, not just command/button flows.

Examples:
- "Had a difficult airway case, can this go in Kaizen?"
- "What forms would this support?"
- "File this as CBD and reflection."
- "Actually make it more concise."
- "Why is this asking me to pay?"

### Layer 2 - Intent router

Classify every non-command message into one intent:

- `new_case` - user is describing portfolio evidence
- `portfolio_question` - user asks advice about forms, curriculum, ARCP, or Kaizen
- `edit_draft` - user wants to revise an existing draft
- `file_to_kaizen` - user wants a draft filed
- `account_or_billing` - user asks about limits, tiers, payments, or access
- `setup_or_credentials` - user needs Kaizen connection help
- `unknown` - unclear message; ask one clarifying question

The router must return structured JSON only. No direct user-facing prose from the router.

### Layer 3 - Existing deterministic workflows

Do not rewrite the working machinery. Route into existing handlers:

- case extraction and recommendation
- draft generation
- improve/rewrite flows
- Kaizen credential setup
- Kaizen filing
- payment and usage-limit flows
- voice profile flows

Buttons remain available at confirmation points, but they stop being the only way to operate the bot.

### Layer 4 - Safety and recovery

Hard rules:

- Never file to Kaizen without explicit user confirmation.
- Never change billing or credentials without explicit confirmation.
- Never hallucinate portfolio facts; ask if the case lacks required detail.
- Never go silent on unknown input. Ask one useful clarifying question.
- If routing confidence is low, explain what the bot can do next in one short message.

## Implementation Phases

### Phase 0 - Checkpoint and branch

Done:
- DeepSeek model-pathway work committed.
- Main pushed to GitHub.
- New branch created for this work.

### Phase 1 - Router contract and tests

Add a small router module with:

- intent enum
- structured router result
- confidence score
- extracted signals, such as form type, action, and target draft
- fallback clarification text

Add tests for the highest-value messages:

- case description routes to `new_case`
- form advice routes to `portfolio_question`
- "file this" routes to `file_to_kaizen`
- "make it shorter" routes to `edit_draft`
- billing/access messages route to `account_or_billing`
- nonsense or underspecified messages route to `unknown`

No production routing changes in this phase.

### Phase 2 - Passive shadow mode

Run router in the background for ordinary text messages and log the intended route without changing user behaviour.

Purpose: prove the router understands real messages before handing it control.

Verification:
- existing tests pass
- shadow logs show correct intent on at least 20 representative prompts
- no user-facing workflow changes

### Phase 3 - Safe activation for low-risk intents

Activate router for:

- `portfolio_question`
- `unknown`
- `account_or_billing`
- `setup_or_credentials`

Keep case creation and filing on existing paths until the router proves stable.

Verification:
- ordinary "what can you do?" and "why am I blocked?" messages receive useful replies
- no disruption to existing button workflows

### Phase 4 - Case-intake activation

Route natural case descriptions into the existing case extraction flow.

The output should be the same structured draft/recommendation flow users already know, but the input can be natural text.

Verification:
- existing case flows still pass
- natural case prompts create drafts without requiring command/button setup first
- unclear cases ask one clarifying question instead of failing silently

### Phase 5 - Filing command activation

Allow natural filing instructions only when a draft already exists and the action is clear.

Examples:
- "file this as a CBD"
- "send this to Kaizen"
- "also make a reflection log"

Verification:
- filing still requires explicit confirmation
- wrong/ambiguous form requests ask a clarification
- no duplicate Kaizen drafts

## Non-Goals

- No separate bot.
- No rewrite of Kaizen filing.
- No removal of buttons.
- No free-form agent with permission to file autonomously.
- No migration away from current Telegram bot until the router proves value.

## Success Criteria

The change is successful when:

- a doctor can use the bot naturally without knowing commands
- existing deterministic workflows still pass tests
- unknown messages get helpful recovery responses
- Kaizen filing remains confirm-first and auditable
- fewer users hit dead-end silence or "dumb bot" behaviour

## First Build Slice

Build Phase 1 only:

1. Add the router contract and tests.
2. Do not wire it into live message handling yet.
3. Run the test suite.
4. Commit the branch checkpoint.
