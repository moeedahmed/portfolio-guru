# Portfolio Guru — Product Brief

Read this first. It is the product "why": vision, users, principles, scope and
the decisions behind them. `AGENTS.md` is the engineering "how"; code, tests and
the live runtime are the truth for what is actually built. Compiled 2026-09-28
from the repo, git history, Brain, Hindsight and Hermes history (sources listed
at the end). Moeed answered the open questions on 2026-09-29; his answers are in
the decision log and the Resolved questions section.

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
- **Launch:** willingness to pay proven; legal documents signed off by a
  solicitor; payments switched on. After beta, new users get a 14-day free
  trial (replacing 5 free cases a month). Prices live in Stripe and are shown
  before payment, never fixed in the legal documents.

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
Health / ARCP view; opt-in sign-off chaser; `/unsigned` opens the "With assessor"
list (open to every beta user, including password-free connections).

**Out or deferred:** supervisor submission (never); `/beta`, `/link`, `/bulk`
and `/chase` commands (retired); a Hermes conversational layer (paused);
any web app or EM Gurus Hub link (none planned: WhatsApp is the next front
end, via Meta's official WhatsApp Business Platform); other colleges and platforms (after the EM profiles are finished);
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
| 2026-09-29 | Backup copy moves to the dedicated London Supabase project, keyed on Telegram id; old EM Gurus project kept until verified, never deleted | Moeed (decision cards)                               |
| 2026-09-29 | `/unsigned` open to all beta users                                                                                                        | Moeed (decision cards)                               |
| 2026-09-29 | Next front end: WhatsApp first, via Meta's official route; no web app                                                                     | Moeed (decision cards)                               |
| 2026-09-29 | Legal documents carry no fixed price; the price is shown before payment                                                                   | Moeed (decision cards)                               |
| 2026-09-29 | After beta: 14-day free trial replaces 5 free cases a month (not built; beta stays unlimited)                                            | Moeed (decision cards)                               |
| 2026-09-29 | Proactive reminders piloted on Moeed's account only, dry run first                                                                        | Moeed (decision cards)                               |
| 2026-09-29 | Stay on Supabase (Convex not adopted)                                                                                                     | Moeed (decision cards)                               |

## Resolved questions (2026-09-29)

1. **Stage.** Open beta: every user is an unlimited beta user and payments
   are off until public launch (28 Sep decision). Paid-beta gating is gone.
2. **Where data lives.** SQLite on the live Mac mini stays the primary store.
   The Supabase backup copy moves from the shared EM Gurus project (Ireland)
   to the dedicated Portfolio Guru project in London, keyed on Telegram id.
   Supabase stays the cloud database (no Convex).
3. **`/unsigned`.** Open to every beta user; after launch it follows the
   paid plan.
4. **"Chase".** Checked in code, not a new decision: `/chase` (messaging
   assessors) is retired. The Portfolio Health sign-off chaser is
   separate: it only reminds the doctor.
5. **Channel.** WhatsApp is the next front end, through Meta's official
   WhatsApp Business Platform. Meta business setup is being done by Moeed.
6. **Web front end.** None planned. The EM Gurus Hub link is retired.
7. **Legal documents.** No fixed price; the price is shown before payment.
   Free during beta, then a 14-day free trial.
8. **Retention.** Checked in code, not a new decision: the encrypted draft
   backup is deleted when Kaizen confirms the save (orphans expire after 7
   days), and the Supabase backup holds no clinical text, so the old 180-day
   purge has nothing left to clear.

## Sources

Read: this repo (AGENTS.md, docs/, 969 commits), the live checkout (same as
main), Brain (product brief, production readiness, Portfolio Guru decision
pages, current-decisions index), Hindsight knowledge pages, Hermes profiles
(founder and portfolio-guru memories, plans, SOUL files). Not read: OpenClaw
(only backups, data and logs remain; its history was migrated into Brain),
Notion strategy pages, trainee chat transcripts (may contain patient data),
and this project's cloud memory (not reachable from this session).
