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

Required GitHub repository secrets:

- `MAC_MINI_HOST` — Tailscale/IP/DNS for the Mac Mini
- `MAC_MINI_USER` — macOS username, currently `moeedahmed`
- `MAC_MINI_SSH_KEY` — private SSH key allowed to connect to the Mac Mini
- `MAC_MINI_SSH_PORT` — optional; defaults to `22`

The workflow SSHes into the Mac Mini and runs `scripts/deploy_mac.sh`.

## Runtime Proof

On startup, `backend/bot.py` logs the live git commit and branch before polling starts.
