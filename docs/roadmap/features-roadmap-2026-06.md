# Portfolio Guru Roadmap — June 2026

## Current State

Portfolio Guru is an AI Telegram bot that helps UK EM trainees file Kaizen e-portfolio entries. It has ~10 active beta users on a Mac Mini running via GitHub Actions polling. The core filing engine (CDP Playwright to Kaizen) is production-grade — it covers **44 verified form types** with deterministic DOM mappings, handles date/filling quirks, curriculum KC ticking, assessor typeahead, and post-filing QA verification. The bot accepts text, voice (Whisper), photo (LLM OCR), and document (PDF/Word/PowerPoint) input, extracts structured WPBA data via DeepSeek, recommends form types with detailed clinical rationale, shows a preview for approval, then saves drafts to Kaizen. Authentication uses Fernet-encrypted SQLite credentials with best-effort Supabase mirroring. Usage metering, Stripe subscription support, and weekly nudges exist but haven't been pushed to users yet. The supervisor-facing subsystem (assessor queue polling, notification caching, supervisee activity workflows) is at the "partial prototype" stage. Filing-failure recovery is basic — users see a failed status but the bot doesn't auto-retry or guide them to specific fixes. The `/bulk`, `/unsigned`, and `/chase` commands are disabled behind early returns.

## Evidence / Research

### From the Codebase

- **44 form types** with full DOM mappings. The filing engine handles CBD, DOPS, Mini-CEX, ACAT, LAT, ACAF, STAT, MSF, QIAT, JCF, Teaching, PROC_LOG, SDL, US_Case, ESLE, Complaint, Serious Incident, EDU_ACT, Formal Course, REFLECT_LOG, TEACH_OBS, Audit, Research, EDU_MEETING, PDP, and ~25+ management/admin forms.
- **6 RCEM SLO domains** (SLO1–SLO12, Higher curriculum mapped exactly with 2025 Update KC labels). The extractor has an authoritative KC map and a measured "select 3–6 KCs, prefer specificity over breadth" drafting policy.
- **Stripe integration exists** with pro_plus (£9.99/mo) as the only sold tier. Free tier: 5 cases/mo. Pro tier: legacy (no new sign-ups). The webhook handler supports checkout.session.completed, subscription.deleted, subscription.updated, and invoice.payment_failed.
- **Supabase mirror is designed for read-only web access** — credentials, profile, usage, cases, and beta requests are mirrored best-effort. The link flow (telegram ↔ emgurus user) is built through portfolio_link_tokens.
- **Weekly nudge** (_compute_weekly_stats + static/LLM nudge text) runs on a file-based sentinel schedule. It sends to **all active users** based on portfolio_usage. It exists but isn't actively used by the current beta cohort.
- **Post-filing QA** exists (score_qa_buckets → GREEN/AMBER/RED bands, gap recording, fix logging) but runs inside the filer after every save. Reviews show AMBER is common on complex forms — gaps in curriculum KCs and date fields.
- **Assessor-facing subsystem** (supervisor_bot, supervisor_workflow, supervisor_poller, supervisor_scheduler, supervisor_notification_cache, assessor_drafter, assessor_writeback, assessor_session_store) is 50–60% built but largely untested with real users. This is the most "incomplete" subsystem.
- **Conversational gathering mode** (gathering_case workspace, extract_text_facts, vnext dialogue policy) is sophisticated but backend-only — users don't see an interactive multi-turn case builder.

### From Web Research (RCEM ARCP Requirements)

- **RCEM 2025–26 ARCP requirements** (from the official Intermediate/Higher guides):
  - **ESLE**: min 3 per year (including 1 in each placement, with PEM-focused ones)
  - **MSF**: min 1 per year, done in first 6 months
  - **ESR**: 1 per year
  - **CSR**: 1 per placement (both PEM and Adult if not with ES)
  - **CBD/DOPS**: RCEM emphasises **quality over quantity** — once a procedure is signed off independent, no further DOPS needed. Focus is on demonstrated capability, not count. The 2025 update transition adds more flexibility.
  - **Procedural log**: expected for all procedures performed, across training years.
  - **Management portfolio**: 4 mandatory assignments for ST3–6 (rota, risk/clinical governance, guideline/policy, complaint/critical incident).
  - **QIAT**: 1 per year minimum covering SLO11 (Quality Improvement) and SLO12 (Leadership & Management).
  - **Curriculum coverage**: must demonstrate evidence mapping to each SLO's KC.

- **Competing tools**:
  - **FourteenFish**: £49.99/yr for GP appraisal toolkit, with app-based CPD capture, surveys, ISO 27001. Primarily GP-focused. No Kaizen/KC mapping. Not EM-specific. No automatic form filling.
  - **Horus**: NHS Scotland e-portfolio platform. Browser-based, like Kaizen but older/less polished. Not a gap to chase.
  - **Kaizen itself**: The official RCEM platform. Slow AngularJS UI, no mobile app, manual data entry, poor date UX, no pre-fill, no intelligent extraction.
  - **No direct competitor** does what Portfolio Guru does — AI extraction + auto-filing for UK EM trainees. This is a clean niche.

### Key Pain Points (Inferred + From Code)

1. **Filing failures are opaque**: users see "partial" or "failed" status but the bot doesn't explain what went wrong (which field, why). The QA pass detects gaps but the user never sees them.
2. **No portfolio health awareness**: users don't know what they're missing — which SLOs have thin coverage, which forms are due, whether they're on track for ARCP.
3. **No multi-attachment support**: users can't send 3 photos of a case as one bundle, adding text later. The pending_case_bundle infrastructure exists but the UX is rough.
4. **Session caching / login speed**: every filing re-authenticates via the RCEM portal → Kaizen redirect. This takes 15–30 seconds before any form filling starts.
5. **Weekly nudges exist but aren't on**: the weekly_check.py and weekly_push() are built but not firing regularly for the beta cohort.
6. **No visible curriculum tracking**: the bot selects KCs per draft, but never shows the user their cumulative coverage.
7. **Disabled features block user growth**: `/bulk`, `/unsigned`, `/chase` are "coming soon" — users who want to manage their portfolio more actively hit a wall.
8. **WhatsApp is risky**: Meta's policy (Jan 2026) bans general-purpose AI chatbots as primary WhatsApp Business function. Telegram is the safer channel for now.

## Feature Candidates

### 1. Portfolio Health Chart (generated image in chat)

- **Effort:** M (3–5 days)
- **Impact:** High — every user benefits. ARCP anxiety is real. Seeing a visual snapshot of their portfolio builds confidence and engagement.
- **Dependency:** Usage tracking (exists). SLO curriculum mapping for coverage (extractor has it). Image generation tooling needed (Python matplotlib/plotly → PNG).
- **Why now:** Highest engagement per unit effort. Gives users immediate value every time they interact. Paired with "you've filed 5 CBDs, 3 DOPS this month; SLO3 coverage is light" — this is the killer retention feature.
- **Risks:** LLM image generation costs (need deterministic charting). KC mapping data from usage.db is partial — not all drafts log which KCs were ticked.

### 2. Filing Failure Recovery (auto-retry, guided fixes)

- **Effort:** M (3–5 days)
- **Impact:** High — filing failures erode trust. 10 beta users × 2 failures each = 20 bad experiences that could have been recovered.
- **Dependency:** The QA score_qa_buckets + gap logging infrastructure exists. Needs a retry loop + user-facing "this field didn't fill — want me to try again?".
- **Why now:** Directly addresses a current beta user pain point. Low-hanging improvement with existing infra.
- **Risks:** Over-retry could lock Kaizen account if login repeatedly fails. Need max-1-retry guard.

### 3. Storage-State Session Caching (faster login)

- **Effort:** L (1–2 weeks)
- **Impact:** Medium-High — every filing benefits. Current flow: login → navigate → fill → save → logout takes 25–50s. Cached session: 8–15s. Over 100 filings: saves ~30 minutes of wait time per user.
- **Dependency:** Storage mechanism for cookies/localStorage (SQLite or local file). Session expiry detection. Chrome CDP must stay up (it does, it's persistent).
- **Why now:** Filing speed is the biggest friction after "does it work". Faster = more filings = more usage.
- **Risks:** Session token could expire mid-filing. Kaizen's auth session TTL unknown (empirically ~30 min inactivity). CDP browser restart invalidates all sessions.

### 4. WhatsApp Integration

- **Effort:** XL (1–2 months)
- **Impact:** Medium — opens a second channel but Telegram already works. WhatsApp's 2026 policy bans general-purpose AI bots — portfolio filing would likely be caught by this restriction.
- **Dependency:** WhatsApp Business API approval (takes weeks). Meta commerce policy review. UK GDPR/DPA compliance assessment. Secure E2EE patient data handling.
- **Why now:** No. Telegram is the right channel for an AI-first bot in 2026. WhatsApp policy headwinds, regulatory risk around health data, and the low overlap between "WhatsApp primary user" and "EM trainee with portfolio filing pain" make this a poor investment.
- **Alternative:** Offer a web chat widget instead of WhatsApp — covers the "I don't want Telegram" crowd without Meta policy risk.

### 5. Web Companion Dashboard

- **Effort:** XL (1–2 months)
- **Impact:** High — Supabase mirror already syncs all data (credentials, usage, cases, profile). The dashboard data is ready, it just needs a frontend.
- **Dependency:** Supabase sync (already works). Portfolio web client or EM Gurus Hub integration.
- **Why not now:** The sync is unidirectional (bot→Supabase; no read-back). Building a dashboard before nailing the Telegram UX means splitting attention. Do this when the bot has 50+ users and the Telegram experience is polished.

### 6. Multiple-File Attachment Support

- **Effort:** S (1–2 days)
- **Impact:** Medium-High — users often have multiple images/docs for one case (e.g. ECG + referral letter + procedure note). The pending_case_bundle infra is 80% built.
- **Dependency:** Pending case bundle completion (exists in code but needs UX polish — "you have 2 images queued, send /done when ready").
- **Why now:** Low effort for decent UX improvement. Directly unblocks beta users who send photos and keep adding text.

### 7. ARCP Readiness Scoring

- **Effort:** L (1–2 weeks)
- **Impact:** Very High — this is the #1 anxiety for EM trainees. A simple score ("You're 62% ready for ARCP; missing: 1 MSF, 2 more ESLEs, SLO5 coverage") would be immensely valuable.
- **Dependency:** Form coverage mapping (exists). ARCP requirement data (known from RCEM guides — can be hardcoded per stage). KC coverage tracking per usage record (partially exists — usage.log doesn't store KCs ticked). Need to store KCs in usage.db or extract from case archive.
- **Why now:** Differentiator. No competitor offers this. Directly monetisable.
- **Risks:** RCEM requirements change annually. Maintaining the requirement mappings is a recurring cost. The scoring model is necessarily approximate — trainees have individual circumstances.

### 8. Supervisor-Facing Summary Reports

- **Effort:** XL (1–2 months)
- **Impact:** Medium — the supervisor bot subsystem is 50% built but complex (workflows, pollers, notifications, assessor schemas). Would help ESs review supervisees but requires both sides to use the system.
- **Dependency:** Supervisor workflow completion (assessor session store, writeback, notification cache). Multiple beta users with the same ES.
- **Why not now:** The supervisor side is the most incomplete subsystem. Building it out delays improvements that directly help individual users. Do this when there are active ES users asking for it.

### 9. Curriculum Progress Tracking (KC Coverage %)

- **Effort:** M (4–7 days)
- **Impact:** High — overlaps with ARCP scoring. KC coverage matters more than form count.
- **Dependency:** KC tick tracking per draft (extractor selects KCs, filer ticks them, but KCs per draft are not persisted in usage.db). Need to store KC mapping alongside filing records.
- **Why now:** High-value, medium-effort. Even a simple "KCs demonstrated: 14/52 → 27%" is useful to users. Combines naturally with the portfolio health chart.
- **Risks:** The SLO→KC mapping is curriculum-version dependent (2025 update vs 2021). Some KCs are inherently un-demonstrable via WPBA (they require ESLE or supervisor observation).

### 10. Monthly/Weekly Digest

- **Effort:** S (2–3 days)
- **Impact:** Medium — weekly nudges already exist in code but aren't active. Activating + enriching (add "KC coverage gained this week") is low effort.
- **Dependency:** Nudge infrastructure works. Weekly_push() needs to be scheduled (it's not on the PTB job queue).
- **Why now:** Low effort to activate. Keeps users engaged even when they're not actively filing.

### 11. Supabase Bidirectional Sync

- **Effort:** XL (1–2 months)
- **Impact:** Medium — currently Supabase is write-only from the bot. Bidirectional sync would let the web app change tiers, update profiles, or trigger filings.
- **Dependency:** Web companion dashboard (#5). Conflict resolution strategy. Backfill of existing data.
- **Why not now:** Over-engineered for 10 users. The current one-way mirror works for read-only web access.

### 12. Multi-Channel Shared Auth

- **Effort:** M (1 week)
- **Impact:** Low — only one channel (Telegram) is active. Adding WhatsApp or web auth before growing Telegram is premature.
- **Dependency:** Needs another channel actually working first.
- **Why not now:** Premature. Solve for one channel well.

### 13. Chat Export / Portfolio PDF

- **Effort:** L (1–2 weeks)
- **Impact:** Low-Medium — PDF export is lower value than ARCP scoring or health charts. Users want to see "am I on track", not "here's a PDF dump".
- **Dependency:** Filing archive with KCs.
- **Why not now:** Nice-to-have, not a pain point. Users can already view their Kaizen portfolio directly.

## Leanest Path to First Revenue

**Target: £9.99/month pro_plus tier (existing Stripe support)**

### Minimum Viable Commercial Product

1. **Free tier:** 5 cases/month (exists). Show portfolio health chart and filing stats for free (hooked users will want more).
2. **Pro_plus (£9.99/mo):** Unlimited filing + ARCP readiness scoring + weekly digest + KC coverage tracking.
3. **No discount tiers.** No "pro" legacy (grandfather existing beta users to pro_plus or give them 6 months free). Simpler is better.

### Why a junior doctor would pay £9.99/month

- **Time saved:** 15–20 min per filing × 10–20 filings/month = 2.5–7 hours saved.
- **ARCP anxiety reduction:** Knowing "you're 62% ready" vs blindly guessing.
- **It costs less than:** A single coffee per week. Cheaper than FourteenFish (£4.17/mo for GPs) for infinitely more value specific to EM trainees.
- **NHS pays nothing:** Junior doctors pay out of pocket. £9.99 must feel "trivially cheap" for the time saved.

### What to cut to get to MVP revenue

- **Don't build:** Web dashboard, WhatsApp, bidirectional sync, supervisor reports, chat export.
- **Do build:** Portfolio health chart, ARCP readiness scoring, KC coverage tracking, filing failure recovery, activate weekly digests.
- **Gates:** A free user hits 5 cases/month → "You've used your 5 free cases. Upgrade for £9.99/mo to continue filing and unlock ARCP tracking → [Upgrade]".

## Next 30 Days

**Goal: Reduce churn with visible portfolio value. Enable the first paid upgrade path.**

| Order | Feature | Effort | Why This Order |
|-------|---------|--------|----------------|
| 1 | **Portfolio Health Chart** | 4–5 days | Highest engagement per effort. Shows every user their progress visually. Paired with "file another case" CTA. |
| 2 | **Activate Weekly Nudges** | 1 day | Already built. Just schedule weekly_push() on the PTB job queue. Keeps users coming back. |
| 3 | **Filing Failure Recovery** | 3–5 days | Fixes the #1 trust issue. Add auto-retry (1 attempt), then guided "this field was empty — edit the draft?" flow. |
| 4 | **Multiple-File Attachment** | 1–2 days | Low-effort UX win. Wire up pending_case_bundle with `/done` signal. Handles the "I have 3 photos for one case" scenario. |
| 5 | **KC Coverage Tracking** | 4–5 days | Persist KCs selected per draft to usage.db. This unlocks ARCP readiness scoring next month. |

**Total effort:** 13–18 days of focused dev work. The rest of the month handles onboarding for new beta users, bug fixes, and preparing the Stripe checkout flow for the user-facing upgrade button.

**Assumptions:**
- Beta users are notified about new features via pinned messages in the test group.
- Filing stability (Kaizen login, DOM mappings) is maintained as bug fixes — no major Kaizen UI changes expected without notice.
- The 2025 RCEM curriculum update is already live in Kaizen; the transition is complete and no 2021→2025 migration is needed.

**Unsure about:**
- Whether Kaizen will change its AngularJS DOM in the next 3 months (third-party platform, no notice).
- Whether 10 beta users are enough to validate willingness-to-pay at £9.99/mo.
- Whether DeepSeek extraction quality degrades under real-world input diversity (voice accents, photo quality, document variety).
