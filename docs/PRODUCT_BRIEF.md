# Portfolio Guru — Product Brief

Read this first. It is the product "why": vision, users, principles, scope and
the decisions behind them. `AGENTS.md` is the engineering "how"; code, tests and
the live runtime are the truth for what is actually built. Compiled 2026-09-28
from the repo, git history, Brain, Hindsight and Hermes history (sources listed
at the end). Where sources disagree, the conflict is listed under Open
questions for Moeed, not resolved here.

> You did the case. We file the draft.

## Vision and problem

UK Emergency Medicine doctors lose evenings turning clinical work into portfolio
evidence (WPBAs, reflections, ARCP or CESR/Portfolio Pathway evidence) inside
Kaizen. Doctors can already draft with ChatGPT, so Portfolio Guru is **not a
writing tool**. Its edge is removing the whole filing load: capture evidence
where the doctor already is (text, voice, photo, document in a chat), pick the
right Kaizen form, draft simply in the doctor's own voice, attach the evidence,
and save a **Kaizen draft** after the doctor approves. Then help them see what
is still missing before ARCP or appraisal (Portfolio Health).

Job to be done: _"I've done the clinical work. Help me turn it into ARCP-ready
evidence without spending my evening fighting Kaizen."_

## Who it's for

- **First:** UK EM trainees on RCEM Kaizen (ACCS, Intermediate, Higher).
- **Also:** SAS / CESR-route doctors (labelled beta), who need appraisal and
  CESR-portfolio help rather than ARCP.
- **Later:** other specialties, colleges and platforms (the registry is
  multi-platform ready; only Kaizen is built).
- Private 1:1 chats only. Beta cohort: 20+ testers (July), "more than 30"
  (Moeed, 6 Sep 2026).

## Goals and what success looks like

- **Now:** a reliable end-to-end case flow. Feature freeze and stabilisation
  come before anything new (25 Sep 2026).
- **Activation** = one Kaizen draft saved (or a full preview-to-approval run).
  Sign-up alone does not count.
- **Beta targets** (June plan): 10 active users, 5 with two or more drafts
  saved, 2 paying or clearly willing to pay, 3 testimonials, filing failures
  visible and explainable.
- **Launch:** willingness to pay £9.99/month proven; legal documents signed off
  by a solicitor; payments switched on.

## Product principles

1. **Drafts only.** Never submit, sign off or send anything to a supervisor;
   never imply an official ARCP outcome. The doctor stays responsible.
2. **Explicit approval** before anything is written to Kaizen.
3. **Draft first.** Show the draft straight away; leave missing facts blank
   (never guessed) and name the gaps in one line. Unwritten reflections are
   saved blank, never in AI wording.
4. **Deterministic core.** Case state, form choice and filing live in tested
   code. Mapped forms file by exact, mapped browser steps; the AI browser
   fallback is off by default.
5. **Hold as little data as possible.** Clinical AI in the EU (Vertex, London);
   credentials encrypted and deleted by `/reset`; case text deleted after a
   successful save; consent before any upload.
6. **Three-second messages.** Every bot message is short, has one next step,
   and fits a phone screen (`docs/message-standard.md`).
7. **Quiet and useful.** Proactive messages only when something changes, capped
   in frequency, never on consecutive days.
8. **Runtime beats documents.** If docs disagree with code or the live bot, the
   live evidence wins and the docs get fixed.

## Scope

**In:** Telegram bot; text, voice, audio, photo, document input; form
recommendation for DOM-mapped Kaizen forms; draft preview, edit and approval;
Kaizen draft save (stored password or password-free connection); Portfolio
Health / ARCP view; opt-in sign-off chaser; `/unsigned`.

**Out or deferred:** supervisor submission (never); `/bulk` and `/chase`
commands (disabled, "coming soon"); WhatsApp and a Hermes conversational layer
(paused, revisit once stable); web front end / EM Gurus Hub link (dropped
Aug 2026); other colleges and platforms (after the EM profiles are finished);
cloud hosting (Mac mini through paid beta); public launch (until legal
sign-off).

## Key decisions (dated)

| Date       | Decision                                                                                                                                  | Source                                               |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 2026-02-07 | Origin idea: Telegram "Reflection Writer" bot, freemium                                                                                   | Hermes founder IDEAS.md                              |
| 2026-03-07 | Kaizen chosen as the target platform                                                                                                      | git history                                          |
| 2026-04-10 | Stripe payments wired; `/bulk`, `/chase`, unsigned marked "coming soon"                                                                   | git history                                          |
| 2026-05-12 | Self-hosted on the Mac mini, deploy from CI                                                                                               | git history                                          |
| 2026-05-13 | Position as a vertical "AI employee" for ARCP, not a generic AI agency                                                                    | Brain decision                                       |
| 2026-05-15 | Pricing: free 5/month + single £9.99/month Unlimited                                                                                      | git history                                          |
| 2026-05-23 | Shipped for ACCS, Intermediate, Higher                                                                                                    | Brain                                                |
| 2026-06-17 | Public plan: web front end + Telegram engine                                                                                              | docs/PUBLIC_PRODUCT_PLAN                             |
| 2026-06-25 | Locked: Vertex AI EU for all clinical AI; pricing; gated auto-deploy                                                                      | docs/roadmap/launch-blocker-checklist                |
| 2026-07-02 | Explicit-consent gate for health data                                                                                                     | git history                                          |
| 2026-07-06 | Core edge: whole filing load, not writing                                                                                                 | docs/portfolio-guru-core-edge-2026-07-06.md          |
| 2026-07-07 | Deterministic engine + thin channel shell                                                                                                 | Brain production-readiness                           |
| 2026-07-09 | Focus on Telegram; WhatsApp paused                                                                                                        | docs/PRIVATE_BETA_LAUNCH.md                          |
| 2026-07-12 | AI browser fallback off by default                                                                                                        | git history                                          |
| 2026-08-09 | Consent before any attachment upload to Kaizen                                                                                            | git history                                          |
| 2026-08-18 | Stay on the Mac mini through paid beta                                                                                                    | docs/adr-hosting-2026-08.md                          |
| 2026-08-24 | Data plan: London Supabase primary, delete case text on save, drop Hub link                                                               | docs/data-architecture-plan-2026-08-24.md; Hindsight |
| 2026-09-06 | One product: natural conversation on top of the deterministic engine; testers stay on the Python beta until a Hermes candidate matches it | Brain production-readiness                           |
| 2026-09-25 | Feature freeze and stabilisation; then finish EM profiles (ACCS, Intermediate, SAS) before other colleges                                 | Brain decision                                       |
| 2026-09-25 | Draft first, blank gaps, save available with gaps                                                                                         | Brain decision                                       |
| 2026-09-25 | Password-free Kaizen connection offered in beta                                                                                           | git history; docs/passwordless-kaizen-connect.md     |
| 2026-09-27 | Connect Kaizen offers both options, stored password recommended                                                                           | git history                                          |
| 2026-09-27 | Proactive messages quiet and event-driven, capped                                                                                         | Brain decision                                       |
| 2026-09-27 | Moeed's standing instruction is release approval                                                                                          | git history; AGENTS.md                               |
| 2026-09-28 | Open beta: everyone unlimited, payments off until public launch                                                                           | git history; Brain decision                          |

## Open questions for Moeed

Contradictions found between sources. Not resolved here.

1. **Stage.** AGENTS.md says "controlled dogfood, invite-only paid beta
   gated"; the 28 Sep decision says open beta for everyone. Which label is
   current, and is invite gating gone?
2. **Where data lives.** The 24 Aug plan makes London Supabase the primary
   store; AGENTS.md and later memory say SQLite on the Mac mini is primary and
   Supabase is a best-effort mirror (the London migration was never applied).
   Is the London plan still the goal?
3. **Paid tier name.** Code says `pro_plus` (and `/unsigned` is gated to it);
   users see "Unlimited". With payments off, is `/unsigned` open to everyone?
4. **"Chase".** `/chase` is disabled, yet the Portfolio Health sign-off chaser
   is on for beta. Are these the same feature?
5. **Channel.** WhatsApp (dedicated number) and a Hermes conversational bot
   were both planned, then paused. Still the post-stabilisation direction?
6. **Web front end.** The June plan made a web front end central; the August
   plan dropped the Hub link. Is any web surface still planned?
7. **Legal documents** still describe £9.99 pricing as in force; they need
   updating for the open beta before solicitor review.
8. **Retention.** A 180-day purge (July) versus delete-on-save (August): has
   delete-on-save fully replaced it in the live bot?

## Sources

Read: this repo (AGENTS.md, docs/, 969 commits), the live checkout (same as
main), Brain (product brief, production readiness, Portfolio Guru decision
pages, current-decisions index), Hindsight knowledge pages, Hermes profiles
(founder and portfolio-guru memories, plans, SOUL files). Not read: OpenClaw
(only backups, data and logs remain; its history was migrated into Brain),
Notion strategy pages, trainee chat transcripts (may contain patient data),
and this project's cloud memory (not reachable from this session).
