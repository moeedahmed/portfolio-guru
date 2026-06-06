# Development Workflow

## Roles

- **GitHub `main`**: clean master copy.
- **Laptop**: main development machine for Codex work.
- **Mac mini**: bot runtime/testing machine and occasional verification.

## Daily workflow

### 1. Start clean

```bash
git checkout main
git pull --ff-only
```

If this fails, do not force it. Check `git status` and resolve the local changes first.

### 2. Create a branch for each task

```bash
git checkout -b fix/short-task-name
```

Use prefixes:

- `fix/` for bugs
- `feature/` for new features
- `chore/` for docs, cleanup, tooling

### 3. Work only on that branch

Do not let Codex or Claude edit directly on `main`.

### 4. Run checks before pushing

```bash
./scripts/preflight.sh
```

This checks:

- current branch is not `main`
- branch is not behind its upstream, if one exists
- backend offline tests pass
- untracked files are shown clearly

### 5. Commit and push

```bash
git status
git add <real source/doc files only>
git commit -m "clear message"
git push -u origin HEAD
```

Then merge via GitHub or after review.

## Release closure (deterministic)

Once a fix is committed, close the release with the single deterministic
entrypoint instead of remembering separate test/push/deploy/restart commands:

```bash
# Safe readiness report. Never pushes, deploys, or restarts.
scripts/release_loop.sh --surface telegram --mode prepare

# Gated closure. Refuses without explicit approval and a clean, fast-forwardable
# tree. On approval it pushes main (CI deploys + restarts on the Mac Mini),
# then drives the deploy/restart proof and the dogfood checkpoint.
RELEASE_APPROVED=telegram-$(date -u +%Y%m%d) \
  scripts/release_loop.sh --surface telegram --mode ship
# or, interactively:
scripts/release_loop.sh --surface telegram --mode ship --approved
```

What it wires (reusing existing pieces, not reimplementing them):

1. Offline gate — `scripts/preflight.sh` + `scripts/telegram_qa_offline.sh`.
2. Commit must already be present (ship never creates commits; refuses if dirty).
3. Reconcile feature branch → `main` and push (fast-forward only).
4. Deploy + restart — the push to `main` triggers
   `.github/workflows/deploy-mac.yml` → `scripts/deploy_mac.sh` on the Mac Mini.
5. Dogfood checkpoint — `scripts/dogfood_smoke.sh`.

`ship` checks approval **before** any live or mutating action, so an unapproved
run is side-effect free. Approval is surface- and date-scoped, so a stale token
does not silently re-ship. Run `prepare` first; it tells you READY or BLOCKED
(and why).

## Files that should stay local/private

Do not commit:

- `.env`, `.env.local`, credentials, secrets
- Kaizen session/browser profile data
- local clinical ticket dumps
- patient-identifiable/private artefacts
- backup files like `*.bak-*`
- generated scratch JSON unless reviewed and intentionally included

Prefer storing private working artefacts outside the repo, for example:

```bash
~/portfolio-guru-private/
```

## Recovery if things feel out of sync

Run:

```bash
git status
git branch --show-current
git fetch origin
git rev-list --left-right --count HEAD...origin/main
```

Interpretation:

- `0 0` means local and GitHub are synced.
- first number > 0 means local has commits not on GitHub.
- second number > 0 means GitHub has commits local does not have.

When unsure, do not run force commands. Ask for review first.
