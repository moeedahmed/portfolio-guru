# Mac Mini Deployment

Portfolio Guru runs from a clean deployment checkout on the Mac Mini:

`/Users/moeedahmed/projects/portfolio-guru`

GitHub is the source of truth. Do not edit the live checkout directly.

## First-Time Setup

On the Mac Mini:

```bash
cd /Users/moeedahmed/projects/portfolio-guru
bash scripts/install_launchd.sh
```

This installs the user launchd service:

`com.portfolioguru.bot`

Logs:

- Bot log: `/tmp/portfolio-guru-bot.log`
- launchd stdout: `~/Library/Logs/portfolio-guru/launchd.out.log`
- launchd stderr: `~/Library/Logs/portfolio-guru/launchd.err.log`

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
- Runner name: `mac-mini-portfolio-guru`
- Runner labels: `self-hosted`, `macOS`, `ARM64`, `portfolio-guru`, `mac-mini`
- Runner service: `actions.runner.moeedahmed-portfolio-guru.mac-mini-portfolio-guru`

No SSH deployment secrets are required. The previous SSH approach would not work
reliably with the Mac Mini's Tailscale-only `100.x` address because GitHub-hosted
runners cannot reach that private address by default.

Runner service check:

```bash
cd ~/actions-runner-portfolio-guru
./svc.sh status
```

The workflow runs directly on the Mac Mini and executes `scripts/deploy_mac.sh`.

## Runtime Proof

On startup, `backend/bot.py` logs the live git commit and branch before polling starts.
