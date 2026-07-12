# Private Beta Launch Cut

> Operator-facing runbook for cutting Portfolio Guru from internal dogfood to a
> small private beta. This is **not** a public launch. Do not link this product
> anywhere external.

Branch of record: `launch/private-beta-cut`.
Deploy host: Mac Mini, launchd service `com.portfolioguru.bot`.
Source of truth for filing routing: `backend/filer_router.py`.
Product page: https://emgurus.com/portfolio

Current focus (2026-07-09): Telegram-first launch proof. WhatsApp is paused,
not deleted. Re-engage beta users only after the Telegram golden path and
private-beta legal boundary are ready.

Legal readiness note:
`docs/legal/private-beta-readiness-2026-07-09.md`. The current conclusion is
that a tiny controlled private beta can continue with explicit consent and
draft-only boundaries, but public or paid launch remains blocked until draft
privacy/terms markers are closed.

---

## Launch Boundary

- **Audience.** 3–5 trusted UK EM trainees, hand-picked. Same WhatsApp/Signal
  thread as the operator. No promotion, no referral link, no public Telegram
  username share.
- **Bot account.** Existing production [`@portfolio_guru_bot`](https://t.me/portfolio_guru_bot). No new account.
- **Window.** Open-ended, but treat each week as a checkpoint. Pull the plug
  rather than letting it drift if rollback signals appear.
- **What this is.** A signed-up trainee can use the live bot with their real
  Kaizen credentials to draft real WPBA tickets. They review and submit
  manually inside Kaizen.
- **What this is not.** Marketing, onboarding-at-scale, paid tier, public
  signup, or a Royal College endorsement.

---

## Supported Beta Flows (Trainee)

Source: `WORKFLOWS.md` Flow 1, Flow 2, Flow 3, Flow 5.

1. `/start` → connect Kaizen (text-only credentials, Fernet-encrypted).
2. Send a clinical case as **text**, **voice note**, or **photo** of notes.
3. Bot extracts → recommends up to 3 form types → user picks one
   (or `❌ Cancel`).
4. Draft preview rendered → user `✅ Save as draft`, `✏️ Edit`,
   `✨ Quick improve`, or `❌ Cancel`.
5. On save: deterministic Playwright via browser-harness CDP files the draft
   on Kaizen. User reviews and submits/signs inside Kaizen manually.
6. `Cancel` / `Start fresh` returns the user to idle. Stale buttons recover with a
   `That earlier draft is no longer active.` message — never a dead end.

Forms in scope for beta filing: whatever `filer_router.py` marks as DOM-mapped.
Treat the assessor-discovered form set (CBD 2025, DOPS ST3–ST6 2025,
LAT 2025 v9, ACAT ACCS 2025, ACAF, STAT, MSF, MINI_CEX, JCF, QIAT — see
`FORM_UUIDS` in `backend/extractor.py`) as the working list; verify against
`filer_router.py` before promising a form to a beta user.

As of 2026-07-12, the non-deterministic browser-use fallback is off by
default (`PG_ENABLE_BROWSER_USE_FALLBACK` unset). A form with no DOM mapping
now fails cleanly for the user instead of silently escalating to browser-use
— do not set that env var for the beta cut unless a specific unmapped form
needs the fallback and the operator is watching it live.

---

## Controlled Supervisor Scope

Source: `WORKFLOWS.md` Flow 2A, `TASK.md` carried context,
`backend/assessor_writeback.py`, `backend/supervisor_bot.py`.

**Safe in beta:**

- Read-only notifications for `kaizen_role=="assessor"` users
  (`supervisor_scheduler.supervisor_poll_tick`, 5-minute tick, inert when no
  assessor users exist).
- Open ticket read-only via `assessor_reader.open_ticket_readonly`.
- Local-only intent capture → draft → preview → review/recapture/cancel
  (`assessor_drafter`, `assessor_session_store`).
- `Prepare Kaizen action plan (no write)` — planning surface only, never
  touches CDP.
- **CBD** save-draft via `execute_write_plan`, **only** behind the explicit
  `SUP|confirm-save-draft` confirmation, and **only** against a disposable /
  unfilled CBD ticket the operator controls.

**Hard-blocked everywhere in beta:**

- Submit, sign, approve, send, reject, delete on any assessor surface.
- Save-draft on any non-CBD assessor completion surface (DOPS, Mini-CEX,
  ESLE, QIAT, LAT, STAT, MSF, JCF, ACAF, ACAT) until each is mapped, bound,
  and tested.
- Live save-draft against an assessment row that is already filled — the
  runner returns a clean failure before any field write; do not retry.
- Any supervisor live action without the explicit `Yes, save as draft` tap.

If a beta user happens to be a clinical supervisor on Kaizen, tell them
upfront: only read-only notifications and local draft preparation are live
for them. CBD save-draft remains operator-driven for this cut.

---

## Hard No-Go Blockers

Refuse to launch / pull launch if any of these are true:

1. Live offline test gate is failing.
   `cd backend && venv/bin/python3 -m pytest tests/ -q
--ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py`
   must return all-green or known-skipped only.
2. `backend/filer_router.py` is missing routing entries for a form being
   shown to beta users (DOM map gap on a recommended form).
3. `launchctl print gui/$(id -u)/com.portfolioguru.bot` shows the service
   not running or a recent crash loop in
   `~/Library/Logs/portfolio-guru/launchd.err.log`.
4. Persistent Chrome session at `localhost:18800` is not reachable from the
   bot host (CDP attach fails → live filing dead).
5. BWS secrets unavailable on the Mac Mini (Telegram token, Google API key,
   Fernet key all load from BWS at startup).
6. Any uncommitted or unreviewed change to `assessor_writeback.py`,
   `supervisor_bot.py`, `filer.py`, `browser_filer.py`, or
   `filer_router.py`. Safety contracts live in these files; they ship via
   reviewed PR or not at all.
7. A code path is detected that submits/signs/approves/sends/rejects/deletes
   on Kaizen for either trainee or assessor flows. Source-scan invariants
   in the test suite already guard this — if they fail, do not launch.

---

## Rollback / Disable Path

**Stop the bot (preserves logs and DB):**

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.portfolioguru.bot.plist"
```

The bot will not auto-restart until bootstrap is run again. Beta users will
see Telegram messages go undelivered (the bot is offline, not crashed).

**Re-enable later:**

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.portfolioguru.bot.plist"
launchctl enable "gui/$(id -u)/com.portfolioguru.bot" 2>/dev/null || true
```

**Pause GitHub auto-deploy (prevents next push to `main` from redeploying):**

```bash
cd ~/actions-runner-portfolio-guru
./svc.sh stop
```

Resume with `./svc.sh start`. The runner service is
`actions.runner.moeedahmed-portfolio-guru.mac-mini-portfolio-guru`.

**Hard revert a bad commit on `main` (only if a deploy shipped a broken cut):**

1. Stop the bot (above).
2. On the laptop, open a PR that reverts the offending commit, land it via
   the normal review flow. Do **not** force-push to `main`.
3. Let the runner re-deploy from the reverted `main`.
4. Re-enable the bot.

**Per-user kill switch (one beta user is misbehaving / leaking PHI):**
Remove the user's credentials from the encrypted credential store via
`backend/credentials.py` helpers (operator-only, manual). Do not delete
their Kaizen drafts — those are theirs.

---

## Monitoring / Logs

Check on this cadence after each launch / re-launch:

**First 30 minutes (smoke window):**

- `tail -F /tmp/portfolio-guru-bot.log` — startup commit/branch line,
  PTB poll start, no `Traceback` or `ERROR` lines.
- `launchctl print gui/$(id -u)/com.portfolioguru.bot | head -25` —
  pid present, recent start, no `last exit code != 0` loop.
- Bot replies to `/start` from the operator account end-to-end.
- One real text-case dogfood: see `scripts/dogfood_smoke.sh`.

**First 2 hours (beta-active window):**

- `~/Library/Logs/portfolio-guru/launchd.err.log` — no new entries since
  startup, or only benign (e.g. Telegram network hiccups that recover).
- `/tmp/portfolio-guru-bot.log` — look for `filer`, `browser_filer`, or
  `assessor_writeback` errors. Any safety guardrail trip (e.g.
  `AssessorWriteBackUnavailable`, `not a CBD plan`, `ticket UUID mismatch`)
  should be benign — the bot refused to write and reported to the user.
- Beta user check-in: any failed filings, any unexpected error message,
  anything that "felt wrong".

**First 24 hours (steady-state):**

- Same logs, but scan for repeated errors against the same form or user.
- `/funnelreport` — journey proof: real users reaching preview, draft save, and
  repeat use. Use `/funnelreport all` only when synthetic/operator test traffic
  should be included. Both reports exclude Moeed's own operator dogfood
  traffic by default and append a `Revision: <branch>@<commit>` line so it is
  unambiguous which deployed build produced the numbers.
- `/filingreport` — Kaizen reliability: real filing attempts, success/partial
  rate, top failure categories, and recent failures. Use `/filingreport all`
  only when synthetic/operator test traffic should be included.
- `~/.openclaw/data/portfolio-guru/supervisor/` — if any assessor users
  signed in, supervisor state files appear here. Each user has their own
  file. Inspect only if a supervisor user reports a problem.
- Confirm no Kaizen submit/sign/approve/send/reject/delete actions have
  occurred. Source-scan tests assert this at build time; this is the
  runtime double-check via Kaizen's own activity log.

If anything outside these expected paths appears in logs, stop the bot
first, investigate second.

---

## Beta User Instructions

Send this verbatim (or close) to each beta user before they connect:

> **Portfolio Guru — Private Beta**
>
> What it does:
>
> - You send a case to the bot on Telegram — text, voice note, or photo of
>   your notes.
> - It suggests up to 3 WPBA types that fit (CBD, DOPS, LAT, etc.).
> - You pick one, review the draft it builds, edit anything you want, then
>   tell it to file.
> - It saves a **draft** on your Kaizen account. You open Kaizen, check it,
>   and submit / sign / send for supervisor review yourself.
>
> What it will not do:
>
> - It will never submit, sign, approve, send for review, reject, or delete
>   anything on Kaizen. Drafts only. You stay in control of the final step.
> - It will never store your Kaizen password in plain text. It will never
>   share your case with anyone outside the bot's pipeline.
>
> How to report a failure:
>
> - Tell the operator on the beta thread. Include:
>   - what you sent (text / voice / photo)
>   - what form you picked
>   - what the bot said back
>   - whether the draft did or did not appear in Kaizen
>   - a screenshot of the Telegram bubble if helpful
> - Do not paste patient-identifiable detail into the beta thread. The
>   operator can see the bot logs without you re-sending the case.
>
> Reset is safe, but it is a full local reset:
>
> - Use `Cancel` / `Start fresh` to leave a draft and return to idle.
> - Use `/reset` only when you want to clear Portfolio Guru's local state
>   and reconnect Kaizen. Cases already saved in Kaizen are unaffected.

---

## Verification Before Cutting Launch

The smallest meaningful gate. Run from the laptop on
`launch/private-beta-cut`:

```bash
cd backend
source venv/bin/activate
python -m pytest tests/ -q \
  --ignore=tests/test_e2e.py \
  --ignore=tests/test_e2e_live.py
```

Then on the Mac Mini, run the dogfood smoke checklist (no live Kaizen
writes by default):

```bash
bash scripts/dogfood_smoke.sh
```

Both green is the gate for the orchestrator to merge or push to the deploy
branch and let the self-hosted runner deploy.

---

## Open Hand-Offs To Orchestrator

These are intentionally not done by this branch — orchestrator owns them:

- Push `launch/private-beta-cut` (or merge into `main`) so the runner can
  deploy. Last three commits on `main` (Chrome 148 CDP fix, guarded CBD
  save-draft live runner, guarded assessor writeback planning) ship with
  this push.
- Restart launchd on the Mac Mini after deploy completes (or let the
  deploy script handle the bootstrap, then verify).
- Send beta users the message above.
- Decide whether to keep `/bulk`, `/unsigned`, `/chase` as coming-soon
  responses or hide them entirely from the menu for the beta window.
