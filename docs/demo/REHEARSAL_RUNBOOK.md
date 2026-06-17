# Demo Rehearsal Runbook — Reset-State Golden Path

Date: 2026-06-17
Owner: Founder / Portfolio Guru
Scope: Deterministic rehearsal of the Hermes hackathon demo for Portfolio
Guru. Designed to be run twice in a row from a clean state without
incident.

Companion documents:

- [`HERO_CASE_2026-06-30.md`](HERO_CASE_2026-06-30.md) — the synthetic
  shift note used for the take.
- [`DEMO_SCRIPT_90S.md`](DEMO_SCRIPT_90S.md) — the 90-second narration.
- [`../STRIPE_LOCAL_PROOF.md`](../STRIPE_LOCAL_PROOF.md) — the Stripe
  in-process proof and the manual live-mode gate.
- [`../PORTFOLIO_HEALTH_SPEC.md`](../PORTFOLIO_HEALTH_SPEC.md) —
  Portfolio Health framing (directional planning aid only).

## Honesty key

Every step is labelled by what it actually is:

- `[demo]` — pre-seeded synthetic data or labelled demo card. Does not
  touch any external system.
- `[test]` — deterministic in-process test. No live external call.
- `[manual]` — runs only when a human is in the loop on the day.
- `[live-gated]` — requires founder credentials / explicit approval and
  is allowed to fail without breaking the take.

The take must never show a `[live-gated]` step as if it were `[demo]`.

## Pre-rehearsal reset

Run from `~/projects/portfolio-guru`.

1. `[manual]` Confirm the bot process is healthy: `bash scripts/preflight.sh`.
   The runbook depends on the bot being able to start cleanly. If
   preflight fails, fix the underlying issue before rehearsing — do not
   "demo around" a broken bot.
2. `[manual]` Confirm the Hub dev server is running locally if the
   demo will use the localhost surface, or that the staging URL is
   reachable if not.
3. `[test]` Run the always-on deterministic tests that prove the
   demo's load-bearing claims:

   ```bash
   cd backend && venv/bin/python3 -m pytest \
     tests/test_stripe_webhook_e2e.py \
     tests/test_stripe_handler.py \
     tests/test_demo_assets.py \
     -v
   ```

   All three must pass before the take. They prove:
   - The Stripe checkout → webhook → tier flip path is correct.
   - The Stripe handler branches (subscription update, payment failure)
     remain idempotent.
   - The hero case asset and rehearsal/demo copy contain no forbidden
     claims and no fabricated patient identifiers.

4. `[manual]` Reset the demo Telegram thread by clearing the last few
   messages or muting them in the recording UI. Do not delete chat
   history — that breaks PicklePersistence.
5. `[manual]` Open the Hub Portfolio module at `/portfolio/dashboard`
   in the recording window. Switch to the demo user.

## Golden path — steps used during the take

### 1. Web front door — onboarding / link surface `[demo]`

- Open `/portfolio` (public landing). Show the offer copy and safety
  language: "draft-only", "you approve before Kaizen", "no patient
  identifiers", "not RCEM endorsed", "Portfolio Health is a directional
  planning aid".
- Click through to `/portfolio/dashboard`. Show: tier badge, usage
  bar, link-status row, recent cases panel, Portfolio Health card.
- Click the **Link bot** entry in the side nav and read the bot
  handle aloud. (The handle is the one rendered by the Hub UI; the
  runbook intentionally does not hardcode the casing because it has
  been corrected in the codebase and the live UI is source of truth.)

### 2. Telegram-first case capture `[demo]`

- Paste the hero-case shift note from
  [`HERO_CASE_2026-06-30.md`](HERO_CASE_2026-06-30.md) into the
  Portfolio Guru bot.
- The bot replies with a form recommendation. Tap `CBD`.
- Show the structured fields the bot extracted (date, setting,
  presentation, reasoning, reflection, curriculum, KC). Highlight that
  any missing field stays blank rather than being invented.

### 3. Draft preview `[demo]`

- Read the preview message aloud, including the curriculum links and
  the explicit "draft only — nothing is filed yet" footer.
- Point at the `learning_needs` blank field and say "Portfolio Health
  will flag this".

### 4. Human approval gate `[manual]`

- Tap `Approve` in the Telegram preview message. Say aloud: "Without
  this tap, nothing is sent to Kaizen."
- The bot acknowledges that it will file as a draft only.

### 5. Kaizen draft save — live or mocked fallback `[live-gated | demo]`

- Primary take: `[live-gated]` Founder-supplied Kaizen credentials are
  already loaded in the running CDP session. The deterministic
  Playwright filer opens the form, fills it, and saves as draft only.
  If anything looks off, abort and use the fallback below.
- Fallback take: `[demo]` Switch to the rehearsal mock path. The
  deterministic in-process filer test that the bot uses for offline QA
  produces an equivalent "draft saved" message. Show it instead. The
  runbook explicitly allows this fallback; do not claim a live Kaizen
  save in this case.
- In both cases, narrate: "draft saved, supervisor submission is never
  automatic, the founder will review and submit manually if at all".

### 6. Dashboard evidence and Portfolio Health `[demo]`

- Return to `/portfolio/dashboard`. Show the recent-cases entry
  appearing (or, in the fallback take, show the pre-seeded synthetic
  case row).
- Click into Portfolio Health. Read the directional language. Point
  at the `Learning need not recorded` gap surfaced by the hero case.
- Say aloud: "Portfolio Health is a planning aid, not an official
  ARCP outcome and not RCEM-endorsed."

### 7. Stripe earn proof `[test | live-gated]`

- Primary narration: `[test]` Open the Agent Ledger at
  `/portfolio/ledger` and point at the Earn row labelled
  `Demo / Test`. State explicitly that the in-process E2E test in
  `backend/tests/test_stripe_webhook_e2e.py` proves the
  checkout → webhook → tier flip path deterministically, and that the
  manual Stripe-CLI proof lives in `docs/STRIPE_LOCAL_PROOF.md`.
- Optional live proof: `[live-gated]` Only attempt the Stripe-CLI
  forward if the founder has already started `stripe listen` and
  approves a `stripe trigger` on the day. The runbook treats this as
  optional, not load-bearing.

### 8. Agent ledger — spend / operations / safety `[demo]`

- Stay on `/portfolio/ledger`. Walk through the Spend, Operations,
  Safety sections. State that every figure is illustrative and labelled
  `Demo / Test`, that no real-money assessor payouts exist, and that
  the safety guardrails (draft-only, human approval, no public RCEM
  endorsement) hold even when the agent has every credential.

## Post-rehearsal checks

1. `[manual]` Watch the recording back end-to-end. Mute any segment
   that accidentally shows real credentials, real patient text, or
   real internal screens. Re-record if any forbidden claim slipped
   into narration.
2. `[test]` Re-run `tests/test_demo_assets.py`. The script and runbook
   must still pass the copy scan.
3. `[manual]` Park the final cut under
   `~/projects/portfolio-guru/docs/demo/recordings/` with a date stamp.
   Do not commit recordings to git.

## What this rehearsal does not prove

- Real Stripe production charges. The Stripe leg uses test-mode tokens
  and the deterministic E2E test.
- Real ARCP outcomes. Portfolio Health is a planning aid, not an RCEM
  ARCP / CESR / Portfolio Pathway endorsement.
- Real assessor payouts. There are no real-money payouts in this product.
- Multi-specialty coverage. Portfolio Guru v1 is UK EM + RCEM Kaizen
  only.

## Remaining blockers (Sprint 4b)

- `[live-gated]` Live Stripe-CLI proof requires foreground founder
  credentials, an active `cloudflared` tunnel for
  `stripe.solvorolabs.com`, and a matched `whsec_…` value (see
  `docs/STRIPE_LOCAL_PROOF.md`).
- `[live-gated]` Live Kaizen smoke requires founder-supplied Kaizen
  credentials and a manual approval tap; the worker does not own
  either.
- `[decision]` NVIDIA / Nemotron spend leg is currently labelled as a
  bounded demo entry. A go/no-go on real test-mode access is required
  before the take; if access is not clean, the ledger keeps the entry
  as a `Demo / Test` row and the script does not claim live inference
  spend.
- `[decision]` Public launch privacy / terms copy is out of scope for
  the hackathon take. Before any paid public launch, GDPR review and
  T&Cs must be in place per `docs/PUBLIC_PRODUCT_PLAN_2026-06-17.md`.
