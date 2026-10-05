# Portfolio Guru test bot

The same Python Telegram bot runs as `@portfolio_guru_test_bot`, on the live Mac,
with its own checkout, service, environment and data. It admits only the configured
owner allowlist (Moeed by default). Use synthetic cases and dummy Kaizen logins.
Kaizen is an offline copy: connecting accepts a dummy login, filing completes with
an explicit test receipt and the would-be fields, and portfolio scans read nothing.
Nothing is saved to, submitted to or deleted from a real Kaizen account.

## Isolation

| Boundary | Staging behaviour |
| --- | --- |
| Token | Fixed BWS test secret; returned id and key must match. Live token is never loaded. |
| Users | Blocking first handler silently drops updates outside `PG_ALLOWED_USER_IDS`; empty/invalid configuration refuses startup. |
| Stores | `~/.openclaw/data/portfolio-guru-staging`; inherited individual store overrides and database URLs removed. Live directory, aliases and overlapping paths refused. |
| Runtime | `/tmp/portfolio-guru-staging-runtime.json`; separate log and lock. Live resources and aliases refused. |
| Services | Only `com.portfolioguru.staging-bot`; no webhook/connect server, port cleanup or shared Chrome. Own `backend/venv`. |
| Kaizen | Router returns an offline success; login/scan short-circuit. Central guard refuses real browser and HTTP paths, including helper processes. |
| External effects | Supabase, Stripe, heartbeat and OpenClaw secrets absent; dotenv disabled. Chasers, proactive messages, weekly digests, supervisor polls, payments and password-free sign-in off. |
| Shared services | Existing Fernet key and Vertex EU settings retained; databases and conversations stay separate. Telegram/Vertex/BWS traffic remains real. |
| Alerts | Fixed operator templates carry `[TEST BOT]`; operator remains Moeed. |

Unset `PG_ENV` keeps the existing live launch path. Set `PG_ENV=staging` through
the staging LaunchAgent, not by modifying the live service.

## First installation

On the live Mac, provide the same existing authorised `PG_VERTEX_SA_SECRET_ID`,
`PG_VERTEX_SA_CLIENT_EMAIL` and `PG_VERTEX_BWS_TOKEN_PATH` environment settings
used by live, then run `scripts/install_staging.sh`. It renders the template to
`~/Library/LaunchAgents/com.portfolioguru.staging-bot.plist` and does not load it.
No credentials are printed or written into the plist. `--load` explicitly loads
staging only; normally the first staging deploy starts it after preparing its clone.
Never load the service before the staging checkout has a staging-capable commit.

## Test first, then promote

From a clean feature branch, after the offline checks and a commit:

```bash
scripts/stage.sh deploy                         # defaults to HEAD
scripts/stage.sh smoke --sha <full-40-hex-sha>
# For a visible change: Moeed tries @portfolio_guru_test_bot and taps Ship.
scripts/stage.sh approve --sha <full-40-hex-sha> --note "Moeed tried the test bot and tapped Ship"
scripts/stage.sh status --sha <full-40-hex-sha>
scripts/release_loop.sh --mode prepare --risk telegram --effect "<doctor-visible effect>" --live-target portfolio_guru_bot
# Run the exact pinned ship command prepare prints, with its SHA:digest approval.
```

`deploy` requires a clean branch and publishes it with plain
`git push -u origin <branch>` only if HEAD is not already on an origin branch.
It fetches all origin branches, deploys the exact detached SHA into
`~/projects/portfolio-guru-staging`, installs the existing requirements in its own
venv, and checks stable PID plus exact runtime identity. It never pushes main.

`smoke` runs the existing focused CBD-ready-draft-to-Cancel journey from the
staging checkout, and checks its runtime before and after. It requires existing
Telethon session/API credentials in the calling environment (Moeed's own account).
The only allowed target and singleton recipient allowlist are
`portfolio_guru_test_bot`; conflicting target settings fail before sending.
`TELEGRAM_LIVE_APPROVED` is scoped to that QA child. The existing QA script retains
a redacted transcript under staging's `.artifacts/telegram-bot-qa/`.
This sends test-bot messages when explicitly run; it is never routine CI.

Proofs live in
`~/.openclaw/data/portfolio-guru-staging/staging-proofs/<sha>.json`.
`PORTFOLIO_GURU_STAGING_PROOF_DIR` relocates receipts for deterministic tests.
`PORTFOLIO_GURU_STAGING_DIR` relocates the plain staging clone.
Redeploying resets automated smoke and owner approval; rerunning smoke clears a
previous pass and approval before testing. Missing credentials or interrupted tests
cannot leave passing proof. Approval records the human Ship tap; agents must never
invent it. Internal changes use `--risk internal`, skip the human tap and promote
after automated smoke. Telegram/broad changes require the tap for that exact SHA.

Fresh `release_loop.sh --mode ship` checks deploy smoke and automated smoke for
its exact approved SHA before any remote mutation. Telegram/broad risk also checks
`moeed_approved=true`. Missing proof blocks and prints the exact `stage.sh` command.
The immutable release card and digest remain unchanged. Resume, attestation and
rollback retain their existing rules; a proof-only resume never pushes again.

## Recovery

A failed staging install/start/smoke restores the preceding staging-capable SHA,
reinstalls its requirements, restarts staging only and verifies that rollback.
A first install with no preceding staging-capable SHA leaves staging stopped.
The failure receipt is retained and promotion stays blocked.
To deliberately restore an older staging SHA without changing main, use
`scripts/deploy_staging.sh <previous-origin-sha>`; this resets its receipt, so
re-run automated smoke before attempting promotion again.

For urgent live recovery only, a non-empty single-line
`RELEASE_STAGING_OVERRIDE="<reason>"` bypasses staging proof. The release loop
prints the override loudly and records it in `.release/<sha>.ship.json` before
main moves. All existing card, CI, runtime, live-proof and rollback checks still
apply. Rollback itself needs no staging proof or new override.
