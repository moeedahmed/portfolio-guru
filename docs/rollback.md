# Rollback — Portfolio Guru

Repo-specific playbook. This repo does not own database migrations (no
`supabase/migrations` or equivalent directory in this checkout) — Supabase is
a best-effort mirror written to from `backend/`, so there is no expand/contract
migration policy here (see `AGENTS.md` and the shared continuity checker).

## App code issue (Mac Mini deploy)

Deploy already has automatic rollback built in
(`scripts/deploy_mac.sh`, run by `.github/workflows/deploy-mac.yml` on the
self-hosted Mac Mini runner):

1. Before pulling `main`, `deploy_mac.sh` records the currently-deployed
   commit as `PREV_COMMIT`.
2. After deploy, it runs a post-deploy smoke check.
3. If smoke fails, it automatically runs
   `git reset --hard "$PREV_COMMIT"` on the deploy checkout and restarts the
   `com.portfolioguru.bot` launchd service on the previous known-good commit.

Manual rollback (if auto-rollback did not trigger, or you need to roll back
after the fact):

```bash
# On the Mac Mini deploy checkout (APP_DIR, default
# /Users/moeedahmed/projects/portfolio-guru):
git fetch origin main
git log --oneline -5                       # find the last known-good commit
git reset --hard <last-known-good-commit>
cd backend && ./venv/bin/python3 -m pip install -q -r requirements.txt
launchctl kickstart -k "gui/$(id -u)/com.portfolioguru.bot"
tail -n 30 /tmp/portfolio-guru-bot.log      # confirm it boots on the expected commit
```

## Feature-level disable (no deploy/restart needed)

Some risky surfaces are flag- or handler-gated and can be disabled without a
redeploy:

- **Vertex AI extraction**: `PG_USE_VERTEX` flag (via `gemini_client.make_client()`)
  — unset/false falls back to the non-Vertex path.
- **Individual commands**: `/bulk` and `/chase` already ship disabled
  (early `return`, "coming soon" in `backend/bot.py`). The same
  early-return pattern is the fastest way to pull a misbehaving command
  without a full rollback — patch, run `scripts/verify_changed.sh`, then
  follow the normal release path (`scripts/release_loop.sh`).
- **Filing**: `filer_router.PLATFORM_REGISTRY` controls which platforms are
  live; removing/stubbing an entry disables filing for that platform without
  touching the rest of the bot.

## Billing issue (Stripe)

Stripe is **live**. Do not hand-edit Stripe state or Supabase billing rows.

1. Reconciliation logic and mode-guard live in `backend/stripe_handler.py`
   (`test_stripe_reconciliation.py`, `test_stripe_webhook_e2e.py` cover this
   offline).
2. If a webhook or reconciliation bug is shipped, roll back the app code
   (see above) — Stripe itself is the source of truth for billing state and
   is unaffected by an app-code rollback.
3. Re-run `bash scripts/verify_changed.sh` against the rollback target before
   trusting it.

## Fast checklist

- [ ] Last known-good commit identified (`git log --oneline`)
- [ ] `bash scripts/verify_release.sh` passes on the rollback target
- [ ] Core journeys re-checked: case capture -> recommendation -> draft
      preview -> Kaizen draft save; consent gate; Stripe webhook handling
- [ ] Root cause logged (commit message, `TASK.md`, or `docs/plan.md`)
- [ ] If auto-rollback fired, confirm `com.portfolioguru.bot` is running the
      expected commit: `tail -n 30 /tmp/portfolio-guru-bot.log`
