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

> First check git status. Do not work directly on main. If on main, create a new branch for this task. Make the smallest safe change. Run the backend tests before finishing. Commit the real source/doc changes only. Do not commit `.env`, credentials, local ticket dumps, backup files, browser profiles, or clinical/private artefacts. Push the branch to GitHub and summarise what changed.

## Before stopping work

Ask Codex to do:

```bash
git status
./scripts/preflight.sh
git add <real files only>
git commit -m "short clear message"
git push -u origin HEAD
```

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
