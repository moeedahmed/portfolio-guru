# START HERE — Portfolio Guru

This repo is the clean source code for Portfolio Guru.

## Simple rule

**GitHub `main` is the master copy.**

Work on the laptop in a separate branch. Keep the Mac mini mainly for running/testing the bot.

## Starting work on the laptop

Open Terminal:

```bash
cd portfolio-guru
git checkout main
git pull --ff-only
git checkout -b fix/short-task-name
```

Replace `fix/short-task-name` with something meaningful, for example:

```bash
git checkout -b fix/kaizen-filing-confidence
```

Then open the folder in Codex.

## What to tell Codex

Use this prompt:

> First check git status. Do not work directly on main. If on main, create a new branch for this task. Make the smallest safe change. Run `bash scripts/verify_changed.sh` before finishing. Commit the real source/doc changes only. Do not commit `.env`, credentials, local ticket dumps, backup files, browser profiles, or clinical/private artefacts. Do not push or merge separately; prepare the one release card with `scripts/release_loop.sh` and summarise the exact SHA and card for approval.

## Before stopping work

Ask Codex to do:

```bash
git status
bash scripts/verify_changed.sh
git add <real files only>
git commit -m "short clear message"
scripts/release_loop.sh --surface telegram --mode prepare --risk telegram \
  --effect "<one line about what changes for a doctor>" \
  --live-target portfolio_guru_bot
```

Review and approve the single card printed by `prepare`. The release loop then owns the exact-SHA push, CI, deployment, proof, unchanged resume and bounded rollback. See `docs/release-standard.md`; do not add a routine feature-branch push or PR.

## Never commit

- `.env` files or credentials
- Kaizen login/session/browser files
- patient-identifiable or clinical private artefacts
- random local ticket dumps
- `.bak` backup files
- generated scratch JSON unless deliberately reviewed

## If confused

Run:

```bash
git status
```

If it says you are on `main` and have changes, stop and ask before continuing.
