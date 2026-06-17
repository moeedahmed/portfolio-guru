# Portfolio Guru Public Product Plan

Date: 2026-06-17
Owner: Founder / Portfolio Guru
Status: Product strategy and execution source of truth for turning Portfolio Guru from private beta into a viable public-facing product.

## Executive Decision

Portfolio Guru should become a web-fronted product with a Telegram-first action engine.

The public product should not be "a Telegram bot" in positioning. It should be:

> Portfolio Guru helps UK EM doctors turn messy shift notes into review-ready RCEM Kaizen portfolio drafts, then shows what evidence is missing before ARCP or Portfolio Pathway review.

Surface split:

- Web app: trust, onboarding, dashboard, pricing, account linking, portfolio health, case history, upgrade.
- Telegram: fastest daily workflow for voice/text/photo/document case capture and Kaizen draft filing.
- WhatsApp: later convenience route through the EMGurus gateway, not the first public surface.
- GStack/sidepanel: hackathon and supervisor-copilot demo layer, not the first mainstream user onboarding surface.

The product is about 80-90% ready as a working private/beta engine, but not 80-90% ready as a public product. The remaining work is packaging, onboarding, trust, instrumentation, and clean upgrade/conversion paths.

## Evidence Reviewed

Internal product evidence:

- `AGENTS.md`: private beta state, stack, safety boundaries, draft-only Kaizen filing, test commands.
- `TASK.md`: recent WhatsApp bridge, filing reliability, release readiness, health, channel-neutral contract, and known blocked/manual gates.
- `WORKFLOWS.md`: live Telegram workflow and state machine context.
- `docs/PRIVATE_BETA_LAUNCH.md`: 3-5 trainee beta scope, launch/rollback gates, user instructions.
- `docs/WEB_APP_SPEC.md`: EMGurus Hub module plan, Supabase schema, account-linking flow, Stripe path.
- `docs/PORTFOLIO_HEALTH_SPEC.md`: Portfolio Health plus ARCP/CESR pathway guidance.
- `docs/clinical-supervisor-architecture.md`: supervisor polling, notification, draft-only response flow.
- EMGurus Hub `src/modules/portfolio/*`: landing, dashboard, account link, ARCP Health, Stripe checkout hook, Supabase tables.
- EMGurus Hub `supabase/migrations/012_portfolio_module.sql`: portfolio users, cases, usage, credentials, link tokens, RLS model.
- Antigravity asset scan: `hackathon_asset_scan.md`.
- Claude Code Opus plan: Hermes hackathon two-week strategy and demo narrative.

External source evidence checked:

- Direct X fetch of Nous Research hackathon post: deadline, submission mechanics, judging criteria, prizes, earn/spend/run-operations theme.
- RCEM ePortfolio access page: ePortfolio is the official RCEM learning record and evidence platform for training progression, CPD, non-trainees, CESR/Portfolio Pathway, supervisors, and Kaizen login flow.
- Hermes Stripe Projects documentation: Hermes can provision SaaS services and manage provider billing through Stripe Projects.
- Stripe official agent-payments blog: agent payment infrastructure is explicitly aimed at controlled autonomous business spend, vertical SaaS, reconciliation, and guardrails.
- NVIDIA NemoClaw/NVIDIA technical blog: NemoClaw/Hermes framing supports self-improving agents with skills, memory, runtime controls, and stronger privacy/security guardrails.

## Current Product State

Strong:

- Real painful niche: UK EM doctors using RCEM Kaizen.
- Real working engine: Telegram intake, voice/photo/text/document, extraction, recommendation, draft preview, approval, Kaizen draft save.
- Real safety posture: draft-only, explicit approval, no supervisor auto-submit/signing.
- Real data model: Supabase portfolio tables, RLS, link tokens, case/usage/profile records.
- Real monetisation path: Stripe checkout/webhook/tier logic already exists or is partially wired.
- Real differentiated feature: Portfolio Health and pathway guidance can convert "I filed a case" into "am I ready for ARCP/CESR review?"
- Real hackathon story: earn via Stripe, spend via Stripe Skills/NVIDIA inference/assessor bounty, run real operations.

Weak:

- Public story still reads too much like "bot files forms" rather than "portfolio operating system for EM doctors".
- Web app exists, but it is not yet the canonical onboarding and trust surface.
- Telegram is excellent for action, weak for selling and explaining risk.
- WhatsApp bridge is useful, but not yet the product identity and should not be the first public promise.
- ARCP Health is useful but must be labelled directional and source-grounded; it cannot imply official RCEM/ARCP outcome assurance.
- There is no clean public launch funnel yet: landing -> signup -> link bot -> file first case -> see dashboard -> upgrade.
- Instrumentation for activation/conversion is thin: first case filed, first draft saved, first health view, upgrade attempt, failed filing, and retained usage should be tracked.

## Channel Decision

Recommended public product architecture:

1. Web-first public front door.
   - Use `/portfolio` on EMGurus Hub as the product explanation, trust, pricing, and signup route.
   - Make the web dashboard the user's home once linked.
   - Do not make the landing page over-polished before the workflow is smooth.

2. Telegram-first workflow engine.
   - Keep Portfolio Guru's core interaction in Telegram for now because doctors can send shift notes quickly.
   - Treat Telegram as the action layer: ingest, draft, approve, save to Kaizen, quick health summary.

3. WhatsApp as a later channel.
   - WhatsApp should sit behind the EMGurus gateway and route 1:1 portfolio workflows into Portfolio Guru.
   - Do not promise WhatsApp publicly until quoted-message selection and privacy/group boundaries are proven.

4. Web dashboard as trust and conversion.
   - Show monthly usage, recent filings, tier, ARCP/Portfolio Health, account link, and next best action.
   - This is where paid users should understand why Unlimited is worth it.

5. GStack/sidepanel as demo and supervisor wedge.
   - Use for the hackathon supervisor-copilot moment.
   - Do not make it required for trainee public launch.

## Product Positioning

Primary ICP:

- UK Emergency Medicine trainees using RCEM Kaizen who are behind or anxious about WPBAs, reflections, ARCP readiness, or supervisor review.

Secondary ICP:

- CESR / Portfolio Pathway EM doctors using RCEM Kaizen to organise evidence.

Avoid broadening yet:

- Do not support GP/IMT/CST/foundation/SAS outside RCEM Kaizen in the public v1.
- Do not market as a generic medical portfolio assistant.
- Do not imply official RCEM endorsement.

Offer:

- Free: 5 cases/month, Telegram filing, basic drafts, manual approval.
- Unlimited: unlimited filing, ARCP/Portfolio Health, draft review, case history, next best action, richer exports.

The wedge is not "AI reflection writer". The wedge is:

> Your shift notes become Kaizen drafts and your portfolio gaps stay visible.

## Public V1 Scope

Must ship:

- Public `/portfolio` landing page with precise offer, safety boundaries, pricing, and beta CTA.
- Authenticated `/portfolio/dashboard` with plan, usage, recent cases, link status, and next action.
- Telegram link flow: web generates token, bot consumes `/link`, dashboard updates.
- First-case onboarding: user should know exactly what to send and what will happen.
- Kaizen credential setup remains bot-side with clear safety copy.
- Draft-only Kaizen filing remains the hard safety boundary.
- ARCP/Portfolio Health: directional gap view, with clear "planning aid, not official decision" language.
- Stripe checkout to Unlimited in test/prod according to release gate.
- Admin/support view for failed filings and beta user state.
- Instrumentation for activation and conversion.

Should ship for hackathon/demo:

- Earn/spend ledger.
- Stripe earn leg.
- Stripe Skills/NVIDIA spend leg in test mode.
- SLO health grid hero visual.
- Supervisor GStack sidepanel that reads context and drafts/autofills feedback only after human approval.

Do not ship publicly yet:

- Real-money assessor payouts.
- Auto-submit/sign/send/reject/delete in Kaizen.
- Group chat interaction.
- Supervisor autopilot as a public promise.
- Multi-specialty support.
- Full custom dashboard rebuild outside EMGurus Hub.

## Smoothening Work

Highest ROI rough edges:

1. First-run clarity.
   - One clean route: web signup -> link Telegram -> connect Kaizen -> send first case.
   - Remove duplicate bot URL casing and stale copy (`portfolio_guru_bot` vs `PortfolioGuruBot`).

2. Trust language.
   - Put "draft-only", "you approve before Kaizen", "no patient identifiers", "not RCEM endorsed", and "planning aid only" in the right places.
   - Make it reassuring, not scary.

3. Health/dashboard clarity.
   - Current ARCP Health is useful but too narrow in naming. Product plan should move toward Portfolio Health with ARCP and CESR overlays.
   - For public v1, keep ARCP Health visible but phrase as beta/directional.

4. Activation metric.
   - A user is activated only when they have saved one Kaizen draft or completed a full preview-to-approval path.
   - Web signup alone is not activation.

5. Upgrade trigger.
   - The natural paywall is after value is proven: after a successful first draft, when viewing gaps, or after hitting free monthly limit.

6. Reliability proof.
   - File one hero case on the live path before claiming beta expansion.
   - Keep no-go gates from `docs/PRIVATE_BETA_LAUNCH.md`.

7. Support loop.
   - Every failed filing should produce a simple reportable reason and admin visibility.
   - Beta users should know exactly what screenshot/context to send back.

## Research Still Needed

Already covered enough for execution:

- Internal asset scan.
- Hackathon source verification.
- Current product docs/code scan.
- Public surface inventory.
- Official RCEM/ePortfolio positioning.
- Stripe/NVIDIA/Hermes relevance.

Still needed before broader public launch:

- 5-10 user interviews or structured beta calls with EM trainees.
- Competitor/workaround review focused on how trainees currently prepare portfolios, not generic SaaS competitors.
- Pricing willingness check: £9.99/month vs annual ARCP-season pricing vs one-off pack.
- Privacy/GDPR review for case text handling, deletion, export, and retention.
- Terms/privacy copy before any paid public launch.
- Actual beta analytics: first case completion rate, failed filing rate, repeat use, upgrade intent.

## Execution Plan

Phase 0: Hackathon proof, by 2026-06-30.

- Build the autonomous business-agent demo around earn/spend/run-operations.
- Use synthetic/anonymised cases.
- Keep Stripe spend and assessor bounty in test mode.
- Record deterministic demo; do not rely on flaky live external flows for the final take.

Phase 1: Private beta product hardening, next 1-2 weeks.

- Run the private-beta launch gate.
- Fix first-run and dashboard roughness.
- Verify one real Telegram -> Kaizen draft -> dashboard record path.
- Confirm Stripe checkout and tier flip.
- Keep beta to trusted EM doctors.

Phase 2: Paid beta, next 2-4 weeks after Phase 1.

- Open web landing with controlled CTA.
- Recruit through personal EM network and high-trust groups.
- Use Telegram for action, web for onboarding/dashboard/payment.
- Measure activation and repeat use before adding features.

Phase 3: Public launch, after proof.

- Publish clear case study: "from shift note to Kaizen draft".
- Add Portfolio Health/CESR framing.
- Add WhatsApp only after gateway privacy/routing is proven.

## Build Backlog

Immediate build tasks:

- Align public bot URL and copy across landing/dashboard/link flow.
- Add "first case" onboarding checklist to dashboard.
- Add credentials-status display without exposing encrypted values.
- Add activation events: signup, link token generated, bot linked, credentials connected, draft previewed, draft saved, health viewed, checkout started, checkout completed, filing failed.
- Make ARCP Health naming/copy safer: "planning aid", "directional", "cross-check against current RCEM guidance".
- Create a lightweight admin view for beta support: user, tier, linked status, cases this month, last failure.
- Add paid-beta launch checklist to docs with privacy and rollback gates.

Hackathon-only build tasks:

- Add earn/spend/net ledger.
- Wire Stripe Skills/NVIDIA spend test action.
- Build GStack supervisor sidepanel demo card.
- Script hero case and demo data.

## Success Metrics

Hackathon:

- 90-second demo completed by 2026-06-27.
- Submission staged by 2026-06-29.
- Submitted by midday on 2026-06-30.

Private beta:

- 5 trusted EM users invited.
- 3 users link Telegram and connect Kaizen.
- 2 users save at least one draft.
- Failed filing rate is visible and explainable.

Paid beta:

- 10 active users.
- 5 users save at least two drafts.
- 2 paid conversions or explicit willingness-to-pay confirmations.
- At least 3 qualitative testimonials or before/after workflow quotes.

## Agent Handoff Rules

Before any agent edits Portfolio Guru or EMGurus Hub for this workstream, read this file plus:

- `portfolio-guru/AGENTS.md`
- `portfolio-guru/TASK.md`
- `portfolio-guru/docs/PRIVATE_BETA_LAUNCH.md`
- `portfolio-guru/docs/WEB_APP_SPEC.md`
- `portfolio-guru/docs/PORTFOLIO_HEALTH_SPEC.md`
- `emgurus-hub/TASK.md`
- `emgurus-hub/src/modules/portfolio/*`

Do not change:

- Kaizen submission/signing safety boundaries.
- Credential handling.
- Live deploys, pushes, or paid external actions without explicit approval.
- Public promises around RCEM endorsement or guaranteed ARCP/CESR outcomes.

Default route:

- Product strategy and launch decisions: Founder / Products or Portfolio topic.
- Portfolio Guru bot engine: Portfolio Guru repo.
- Web dashboard/front door: EMGurus Hub repo.
- WhatsApp gateway plumbing: EMGurus/OpenClaw gateway route, not Portfolio Guru product strategy.
- Hackathon demo: keep separate from public beta unless the feature is genuinely useful after the contest.

