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

### 5. Commit locally

```bash
git status
git add <real source/doc files only>
git commit -m "clear message"
```

Do not separately push the feature branch or create a routine PR. The release
loop pushes the exact approved commit to `main`; use a PR only when the change
needs independent repository review beyond the normal release gate.

## Release closure (deterministic)

Once a fix is committed, close it through the one-card/one-approval entrypoint:

```bash
# Non-deploying checks, local ref refresh, and one gitignored approval card.
scripts/release_loop.sh --surface telegram --mode prepare --risk telegram \
  --effect "<one line about what changes for a doctor>" \
  --live-target portfolio_guru_bot

# After Moeed approves that exact card, execute its printed pinned bootstrap
# ship command unchanged. It binds both the source SHA and canonical card digest.
# If proof is pending, run the exact RESUME_COMMAND printed by ship.
# If a released change must be recovered, run the exact ROLLBACK_COMMAND;
# it reuses the same approved release SHA and never force-pushes.
```

What it wires (reusing existing pieces, not reimplementing them):

1. Prepare runs the offline gates, fetches current `origin/main`, proves the live runtime equals it, freezes the exact effect, proof mode, target, exclusions and known-good rollback SHA, and prints one compact card. It is immutable for that SHA: an identical re-prepare reuses the card, changed content is refused rather than rewritten under an approval already given.
2. Approval binds the full source SHA and canonical card digest; SHA-only, dated or bare approvals are refused. Ship/attest/rollback use the printed Git-object-pinned bootstrap command with absolute tool paths, not mutable checkout or cached scripts.
3. Ship re-verifies the immutable card and live baseline, pushes that exact SHA to `main`, and proves the `push` Tests run, later `workflow_run` deploy and Mac Mini runtime identity.
4. Risk proof is frozen on the card: `internal` needs no journey; `telegram` uses the exact guarded target only when the sanctioned Telethon route is ready, otherwise manual attestation; `broad` uses the strict 15-check route. The approved bot username is passed to the live child explicitly and re-checked after that child loads `backend/.env`, so a dotenv value cannot redirect an approved live proof.
5. Attest closes manual proof only, and proves the exact-SHA Tests run and the deploy that followed it for itself before reporting anything.
6. An unchanged resume reuses the same approval and never pushes again. A rollback also reuses it, creates one normal forward commit whose complete parent list is exactly the released SHA and whose tree matches the frozen known-good tree, and proves that rollback commit through CI, deploy and runtime without a live user journey. It creates that commit without changing the checkout, index or local branch. Repeats reconcile Git state and reuse the same commit; pinned code is read from approved Git objects into a private temporary directory, never from a mutable runner cache.

Missing/running/timeout/inaccessible proof exits 4 as `proof-pending`. Completed CI, deploy or journey failure is `blocked` and non-zero. Only an exact runtime-proved release is `live`; only an exact runtime-proved rollback is `rolled-back`; only a verified prepare is `release-ready`.

Telegram workflow fixes also carry the **setup consent path** gate. Treat the reported
bug as a symptom of the whole phone journey, not just the line that failed:
the adjacent prompts must read naturally on a phone, expose one obvious next
action, hide internal audit/runtime detail, keep stale buttons safe, and prove
the real launchd bot is running the committed code. `scripts/preflight.sh`
runs `scripts/setup_consent_path_check.py` for deterministic regressions; the manual
phone journey is captured in `scripts/dogfood_smoke.sh`.

`ship`, `attest`, and `rollback` check the exact SHA-plus-card-digest approval before any live or mutating action. The approval is bound to the persisted card, not to a date or a bare flag, so it cannot silently cover another release. Run `prepare` first; it either writes and prints the one reviewable card or explains why no card was created.

Every mode prints one machine-readable final state:
`FINAL_RELEASE_STATE=live|rolled-back|release-ready|proof-pending|blocked`. Printed proof or rollback commands are next gates, not completion proof; only collected CI, deploy, runtime and required journey evidence can make the state `live` or `rolled-back`.

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
