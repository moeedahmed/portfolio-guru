# Active Task - Portfolio Readiness / ARCP Health Spec

## Objective

Turn the approved Portfolio Guru tracker/readiness direction into a restartable product spec and implementation spine.

The feature must be generic Portfolio Guru product work for UK doctors in training. Moeed's own workflow may be used only as a dogfood case.

## Current Slice

Documentation/spec only:

- expand the existing `docs/ARCP_HEALTH_DESIGN.md` into the canonical Portfolio Readiness / ARCP Health spec
- update `docs/plan.md` with the approved direction and next build slice
- add only a short `WORKFLOWS.md` cross-reference if useful
- do not build code, commit, push, restart, deploy, send Telegram, or run live Kaizen/browser actions

## Guardrails

- No Kaizen login, scraping, import, browser automation, or external submission in the MVP.
- No automated supervisor request or ARCP submission.
- No claim of ARCP success.
- No invented requirements, deadlines, supervisors, evidence, or clinical details.
- Manual/user-entered evidence first.
- Readiness output is a planning aid and must show reasons, gaps, and uncertainties.
- Do not revert unrelated edits.

## Done

- Approved direction captured from `/Users/moeedahmed/.openclaw/workspace/memory/plans/2026-05-21--portfolio-guru-tracker-feature-discovery.md`.
- Canonical spec expanded in `docs/ARCP_HEALTH_DESIGN.md`.
- `docs/plan.md` updated with Phase 2.9 restart pointer.

## Verification

- Markdown/read-back verification required for required sections and safety boundaries.
- Git diff inspection required.
- No backend tests expected unless code or machine-readable constants are introduced.

## Next

Build Phase 1 from `docs/ARCP_HEALTH_DESIGN.md`:

1. Add typed data contracts/enums for readiness profile, evidence status, mapping confidence, requirement set, and readiness summary.
2. Implement a pure readiness computation module with no Telegram, Kaizen, browser, or network dependency.
3. Add focused offline tests for status computation, unknown/stale requirements, accepted-vs-uploaded separation, and summary reasons.
4. Leave live bot behaviour unchanged.

## Carried Context - Conversational Router Dogfood

Do not lose the previous public-UX follow-up while this readiness spec becomes the active slice:

- Dogfood 5 anonymised cases in Medic Portfolio and Portfolio Guru.
- Score time-to-draft, taps/replies, correction burden, draft quality, and trust.
- Dogfood the pleural-effusion/possible-heart-failure case again and compare draft quality.
- If the quality rubric holds across 3-5 cases, move to draft-first behaviour only for high-confidence obvious cases.
