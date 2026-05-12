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
