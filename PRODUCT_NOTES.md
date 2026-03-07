
## Versioning Standard (adopted 2026-03-07)

**Major versions (v1, v2, v3)** — complete rebuilds or fundamental architecture shifts.
- v1: initial bot (basic filing, no multimodal, no intent classifier)
- v2: current — multimodal input, intent classifier, form type recommendations, approval gate

**Minor versions (v2.1, v2.2, ...)** — a coherent batch of features, incremented only when that batch is fully shipped and verified end-to-end. Never used as a label for "future stuff" or "backlog".

**No patch versions** — bug fixes are part of the current minor version until it ships.

**Rule:** "v2.1 scope" or "next batch" = not yet shipped. "v2.1" = shipped and verified.

---

## v2.1 Scope (not yet shipped — captured 2026-03-07)

### Portfolio type selection
- First-time users select which portfolio system they use (Kaizen, SOAR, LLP, etc.)
- "Connect Kaizen" becomes "Connect Portfolio" with a type picker
- Each portfolio type gets its own filer module
- Start with Kaizen only, expand gradually

### Usage limits (monetisation gate)
- Free tier: limited number of filings per month (TBD — e.g. 5/month)
- After limit hit: prompt to upgrade
- Paid tier: unlimited filings
- Stripe integration when ready

### Credentials UX
- Never ask for credentials again once saved
- "Connect Kaizen" button checks first — if already connected, shows "Kaizen connected ✅" + option to change account
- Settings menu accessible at any time for: change portfolio, change credentials, view usage

### End of case flow
- After successful filing: "Are we done?" with buttons — Done / Edit this entry / File another case
- "Done" clears state fully
- "File another case" resets immediately to input prompt

### Case history
- Every filed case saved to SQLite with: date, form type, title/presentation, Kaizen URL
- User can say "edit my last case" or "edit the CBD from Tuesday"
- Bot retrieves from history, browser-use opens the draft for editing

---

## Production Architecture (captured 2026-03-07)

### Current state (v2 — beta, single user)
- SQLite at `~/.openclaw/data/portfolio-guru/portfolio_guru.db`
- In-memory conversation state (`context.user_data`) — per process, not persistent
- Direct browser-use call (synchronous, blocks during filing)
- No rate limiting, no usage caps
- Suitable for: ~10–20 beta testers with light, non-concurrent usage

### User isolation
Telegram's `user_id` (integer) is the isolation key. Every credential, draft, and state is keyed to it. User A's data cannot reach User B at any layer — `context.user_data` is per-user by default in python-telegram-bot, and the SQLite table has one row per `user_id`. Isolation is correct now and will remain correct after the Postgres migration.

### Migration path to production scale

**Database — SQLite → Postgres**
- Trigger: sustained concurrent usage beyond ~50 users or write contention errors
- Change: swap `store.py` SQLite backend for a Postgres connection (same schema, same queries)
- On Render: add Postgres add-on (~£5–7/month)
- Effort: one afternoon

**State persistence — in-memory → Postgres-backed**
- Trigger: horizontal scaling (multiple bot instances) or users losing state on restarts
- Change: use `PicklePersistence` or a custom Postgres-backed persistence layer
- Ensures mid-conversation state survives process restarts and multi-instance deploys

**Rate limiting**
- Per-user request counter in DB (or Redis)
- Cap Gemini API calls and Kaizen browser sessions per user per day
- Prevents one user from hammering the system

**Usage limits + billing gate (v2.1 scope)**
- Free tier: N filings/month (TBD)
- Paid tier: unlimited, gated via Stripe
- Counter stored in DB per `user_id`

**browser-use concurrency — the hard bottleneck**
- Each form filing = one headless Chromium instance navigating Kaizen
- Direct synchronous call works for low concurrency
- At scale: needs a task queue (Celery + Redis) + worker pool so filings are processed in background without blocking the bot for other users
- Kaizen is a human-navigated site, not an API — cannot parallelise infinitely without multiple VPS workers
- This is the most complex scaling step; defer until there is real concurrent load

### Credential security model
- Kaizen username + password encrypted with Fernet (symmetric key from BWS) before writing to DB
- Key never stored in code or repo — loaded at runtime from BWS
- On Render (prod): key loaded from environment variable, injected by Render from BWS at deploy time
- Plaintext password never written to disk or log at any point

### What does NOT need to change for public beta
- User isolation — already correct
- Credential encryption — already correct
- Conversation state machine — scales horizontally once backed by Postgres persistence
- Gemini API — stateless, scales naturally
- The bot logic itself — no hardcoded single-user assumptions
