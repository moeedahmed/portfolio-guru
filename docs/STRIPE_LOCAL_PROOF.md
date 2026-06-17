# Stripe Local Proof — Checkout → Webhook → Tier Flip

Sprint 3 demands a deterministic local proof for the Stripe path. Two
layers exist:

1. **In-process E2E tests (always-on, no credentials).**
   `backend/tests/test_stripe_webhook_e2e.py` exercises both the
   `POST /webhook/stripe` and `POST /api/create-checkout-session` routes
   via FastAPI `TestClient`. Stripe SDK boundaries (`construct_event`,
   `Subscription.retrieve`, `checkout.Session.create`) and Supabase auth
   are monkeypatched. The SQLite `user_profiles` table is asserted to
   flip from `free → pro_plus` (and back to `free` on
   `invoice.payment_failed`). Run:

   ```bash
   cd backend && venv/bin/python3 -m pytest tests/test_stripe_webhook_e2e.py -v
   ```

   This is the path CI runs and the path that proves the routing,
   tier logic, and idempotency are correct without any external
   account access.

2. **Live test-mode proof (manual, gated, runs only with Stripe CLI).**
   To prove the same path against a real Stripe test account, use the
   Stripe CLI to forward events to the local webhook server:

   ```bash
   # 1. Start the webhook server with test-mode env vars.
   bash backend/run_local.sh   # or run webhook_server directly
   # The server listens on http://localhost:8099/webhook/stripe.

   # 2. In a second terminal, forward Stripe test events.
   stripe listen --forward-to localhost:8099/webhook/stripe
   # Copy the printed whsec_... value into STRIPE_WEBHOOK_SECRET and
   # restart the server if it differs from the env-configured one.

   # 3. Trigger a synthetic checkout completion.
   stripe trigger checkout.session.completed \
     --override checkout_session:metadata[telegram_user_id]=<your_test_tg_id>
   ```

   Verify:
   - `bot logs show "Portfolio Guru funnel event=checkout_completed"`.
   - SQLite reflects the flip:
     `sqlite3 ~/.openclaw/data/portfolio-guru/usage.db "select telegram_user_id, tier from user_profiles where telegram_user_id=<your_test_tg_id>;"`.
   - If linked, the Supabase mirror reflects the same tier (best-effort,
     never blocks the local write).

## Blockers For Going Beyond Local Proof

The pieces below need foreground (human) authorisation. Claude Code
worker does not have them and should not attempt them:

- **Live Stripe test/live API keys**: live keys are in BWS but the
  worker must not start a production-effective Stripe session
  autonomously. Even test-mode triggers should be human-supervised
  because they create real Stripe objects on the connected account.
- **Public tunnel for `stripe.solvorolabs.com`**: `cloudflared` must be
  running so Stripe-hosted webhooks reach the bot. Configuring/
  restarting that tunnel is out of scope per `~/.claude/CLAUDE.md`.
- **Supabase service-role key**: required for `_verify_supabase_token`
  in `webhook_server.py` to validate the JWT and for the mirror to
  upsert `portfolio_users.tier`. The key lives in BWS and is read at
  runtime; tests stub it.
- **Stripe price IDs (`STRIPE_PRO_PRICE_ID`, `STRIPE_PRO_PLUS_PRICE_ID`)**:
  must match the price objects on the connected account. Tests stub
  these as `price_unlimited_test` etc.; live proof needs the real IDs
  pulled from the Stripe dashboard.

## What This Proves Today

- The webhook signature path, the tier flip, the duplicate-event guard,
  the `invoice.payment_failed` downgrade, the `customer.subscription.*`
  branches, and the checkout-session creation path are all covered by
  fast deterministic tests.
- The hub-side checkout hook (`useCreateCheckoutSession` in
  `src/modules/portfolio/hooks/usePortfolio.ts`) talks to the same
  endpoint and is exercised end-to-end up to the Stripe boundary.
- The Telegram notify side effect on upgrade is bypassed when
  `TELEGRAM_BOT_TOKEN` is unset, so tests do not call the Bot API.

## What This Does Not Prove

- Real Stripe charge or recurring billing semantics — by design, the
  worker does not initiate live payments.
- That the public tunnel routes a real Stripe webhook to the local
  server right now. That check belongs in the manual smoke step
  above, behind the cloudflared/launchd gate.
