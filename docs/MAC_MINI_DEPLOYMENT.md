# Mac Mini Deployment

Portfolio Guru runs from a clean deployment checkout on the LIVE Mac mini
(Tailscale `100.110.205.88`, the machine running `com.portfolioguru.bot` and
runner `hub-portfolio-guru`). The SPARE Mac mini (Tailscale `100.79.220.124`,
SSH alias `old-mac`) is retired: never run the bot there. Machine names change
often, so check the current one with `scutil --get ComputerName` or
`tailscale status` rather than trusting a stored name:

`/Users/moeedahmed/projects/portfolio-guru-live`

GitHub is the source of truth. Do not edit the live checkout directly.

## First-Time Setup

On the Mac Mini:

```bash
cd /Users/moeedahmed/projects/portfolio-guru-live
bash scripts/install_launchd.sh
```

This installs the user launchd service:

`com.portfolioguru.bot`

Logs:

- Bot log: `~/.openclaw/logs/portfolio-guru/bot.log` — the only log.
  `start-bot.sh` redirects the whole service into it, and launchd's
  stdout/stderr point at the same file, so service-level start failures land
  here too.
- Rotated at 10 MB, keeping 7 generations (`bot.log.1` … `bot.log.7`).

Logs moved off `/tmp` on 2026-08-18. macOS purges `/tmp`, so a reboot destroyed
the history exactly when it was most worth reading. Anything still in
`/tmp/portfolio-guru-bot.log*` or `~/Library/Logs/portfolio-guru/` is old
history — do not read it as current.

## Manual Deploy

```bash
cd /Users/moeedahmed/projects/portfolio-guru
bash scripts/deploy_mac.sh
```

The deploy script refuses to run if the Mac Mini checkout has local changes.

## GitHub Auto-Deploy

The workflow `.github/workflows/deploy-mac.yml` runs on pushes to `main`.

It runs on the Mac Mini self-hosted GitHub Actions runner:

- Runner directory: `~/actions-runner-portfolio-guru`
- Runner name: `hub-portfolio-guru` (the old `mac-mini-portfolio-guru` was unregistered on 2026-09-27)
- Runner labels: `self-hosted`, `macOS`, `ARM64`, `portfolio-guru`, `mac-mini`
- Runner service: `actions.runner.moeedahmed-portfolio-guru.hub-portfolio-guru`

No SSH deployment secrets are required. The previous SSH approach would not work
reliably with the Mac Mini's Tailscale-only `100.x` address because GitHub-hosted
runners cannot reach that private address by default.

Runner service check:

```bash
cd ~/actions-runner-portfolio-guru
./svc.sh status
```

The workflow runs directly on the Mac Mini and executes `scripts/deploy_mac.sh`.

Close-together pushes are safe. Every push whose Tests pass gets its own deploy
run; deploys take turns on the Mac Mini, and each one deploys its tested commit
only if that is newer than what is live (a commit a newer deploy already
includes finishes green with `SUPERSEDED_BY=<live sha>`). So the newest tested
`main` is always the last to land, and no thread needs to wait for others
before pushing. To check a push is live, confirm the live checkout contains it
(`git merge-base --is-ancestor <sha> HEAD` in the live checkout).

## Runtime Proof

On startup, `backend/bot.py` logs the live git commit and branch before polling starts.
