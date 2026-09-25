# Portfolio Guru — Solo-Founder Release Standard

One card, one approval, one release.

Portfolio Guru has one operator. Asking him to approve the push, then the CI
wait, then the deploy, then the runtime check, then the live journey, then the
resume, spends his attention on mechanics and teaches him to approve without
reading. This standard collapses that into a single decision he can actually
make: he reads one card, approves its exact contents, and everything mechanical inside
that unchanged envelope proceeds without asking again.

It removes prompts, not boundaries. Nothing here weakens a live-send,
credential, spend or supervisor-facing guard.

## The card

`scripts/release_loop.sh --mode prepare` runs the offline gates, fetches current
`origin/main`, and verifies the currently live runtime against it. Only if all
three agree does it write one local card under the gitignored `.release/`, keyed
by the full `HEAD` SHA. The card records:

| Field                   | Why it is on the card                                            |
| ----------------------- | ---------------------------------------------------------------- |
| `schema_version`, `sha` | the exact commit the approval names                              |
| `surface`, `risk`       | what class of release this is                                    |
| `effect`                | one plain line: what changes for a doctor                        |
| `proof_mode`            | `automated` or `manual` — frozen here, not decided at ship time  |
| `live_target`           | the exact bot a live proof may touch, or null                    |
| `known_good_sha`        | the rollback target, verified live and frozen before approval    |
| `rollback_mode`         | `operator-triggered` — the card says rollback is never silent    |
| `exclusions`            | what this approval never covers                                  |
| `created_at`            | when the card was prepared                                       |

The approval token binds the full canonical card, not just source code. Its
SHA-256 digest covers every field: sorted JSON keys, compact separators,
ASCII escapes, UTF-8 and one trailing newline. Whitespace/key-order changes
are harmless; changing any value invalidates an existing approval. SHA-only,
dated and bare approvals fail closed. The card also freezes the singleton
`live_allowlist`, exact `rollback_parent_sha`, and absolute `bootstrap_git`,
`bootstrap_python`, and `bootstrap_bash` paths. These are internal execution
bindings, not additional decisions for the founder.

Cards carry no credentials, tokens, private content or patient data.
`scripts/release_card.py` refuses multi-line, oversized, control-character and
credential-shaped text before anything is written.

**A card is immutable for its SHA.** Re-preparing the same commit with the same
content is an ordinary repeat: the existing card is reused byte for byte, and an
approval already given for that SHA still stands. Re-preparing it with anything
different — effect, live target, proof mode, known-good SHA, exclusions,
rollback mode — is refused, and the existing card is left exactly as it was.
Otherwise one approval could quietly come to cover a release the operator never
read. A card someone edited by hand is refused for the same reason rather than
laundered into a fresh one. The refusal exits 2 and says which fields differ;
the fix is to commit the change and prepare that new SHA.

A prepare that verified the tree, the offline gates and the live runtime ends
`FINAL_RELEASE_STATE=release-ready`. Nothing else in the loop uses that word: a
prepare that wrote no card, and a `ship`/`attest`/`rollback` that refused a
missing or malformed approval, report `blocked`.

## What one approval covers

Approving the exact card — `--approved <40hex-sha>:<64hex-card-digest>` — covers, once:

- the push of that exact SHA to `main`;
- the CI `Tests` run and the Mac Mini deploy bound to it;
- exact checkout and runtime identity;
- the named proof on the card;
- an unchanged proof resume;
- bounded targeted rollback to the named known-good SHA if that proof fails.

It covers nothing else. The card's exclusions are explicit: supervisor
submission, credential or secret change, schema or data migration, pricing or
spend change, any new recipient or public announcement, history rewrite or force
push, and any **release** SHA other than the one named. The sole bounded exception
is the operator-triggered rollback commit: its parent must be the named release
and its complete tree must equal the frozen known-good tree.

**A changed SHA, target, risk, surface, effect, proof mode, rollback target or
exclusion needs a newly prepared card and a new approval.** An unchanged resume
reuses the original approval and must not produce a second prompt. Dated or bare
approvals are refused outright: `RELEASE_APPROVED=telegram-20260101` used to
cover a whole day's worth of releases, which is exactly the fragmentation this
standard is meant to remove without becoming a blanket.

## Proof mode is decided once, honestly

Telegram live proof is `automated` only when the Telethon session, API id and
hash, `TELEGRAM_BOT_USERNAME` and the live allowlist all name the one target the
card records. Credentials merely being present is not sanction.

- A **manual** card never sends automatically, even if credentials appear
  between prepare and ship. It closes with `--mode attest`.
- An **automated** card whose readiness later disappears reports proof pending
  and refuses to run the live child. It cannot be closed by manual attestation;
  restoring readiness or preparing and approving a new manual card is required.
- Escalating manual → automated needs a new card and a new approval.

The live guard `TELEGRAM_LIVE_APPROVED` is passed per-command to the live child
only and is never exported, so nothing else in the run inherits it. The direct
guard inside `scripts/telegram_bot_qa.sh` is unchanged and still refuses a live
run on its own.

The approved target is passed to that child explicitly, as `RELEASE_LIVE_TARGET`,
and the child captures it **before** it loads `backend/.env`. After that load it
checks the environment actually in force: the effective `TELEGRAM_BOT_USERNAME`
must still be the approved target, and the allowlist must still name it. A
dotenv file could otherwise have pointed an approved live proof at a bot the card
never named. A mismatch exits 21 without sending anything, and the loop reports
that as `proof-pending` — nothing was sent, so nothing failed — rather than as a
failed journey.

Offline gates run with fixed throwaway values (the same non-secret Fernet key as
the CI `Tests` job, plus `fake` token and API key) scoped to those child
processes with `env`. The deploy, runtime and live children never inherit them —
a fake credential must never be able to make a real proof pass.

## Failure and rollback truth

Deploy-time auto-rollback is unchanged. If the _named live proof_ fails after a
successful deploy, the loop:

1. reports `blocked`;
2. prints the prior known-good SHA that was verified live **before** `main`
   moved — never a guessed one;
3. states that the released SHA remains live until a targeted rollback is
   actually run.

It does not rewrite history and it does not claim a rollback it did not perform.
The receipt prints the exact rollback command rather than describing one; it is
read at the worst possible moment.

## Rollback

```
<the printed pinned bootstrap command> -- --surface telegram --mode rollback \
  --risk <class> --approved <released-sha>:<card-digest>
```

The card that authorised the release already names both ends of this: the
released SHA it approved, and the known-good SHA it verified live before `main`
moved. Rolling one back to the other is inside that envelope, so **it needs no
second approval** — and it is never automatic either. `rollback_mode` on the card
is `operator-triggered`: it happens because the operator ran that command.
Deploy-time health rollback inside `scripts/deploy_mac.sh` is a separate,
unchanged mechanism.

Before anything moves, rollback requires a clean tracked tree, an approval and
card naming the released SHA, `HEAD == origin/main ==` that SHA, a known-good SHA
that is a real ancestor of it, and a live runtime that is reconciled and reported
as the released SHA. Any mismatch blocks before mutation.

The recovery is **one normal forward commit**: its complete parent list is
exactly one parent, the released SHA, and its tree is exactly the known-good
SHA's. Checking only the first parent would have accepted a merge, which drags a
second line of history onto `main` under an approval that named one commit.
Nothing is reset, force-pushed, merged or checked out on `main`, and untracked
files are never touched. If the exact rollback commit cannot be produced, the
run refuses without changing the checkout.

### Pinned execution and non-mutating recovery

Ship, attest, rollback and resume use exactly the command printed on the card.
That command names absolute Git, Python and Bash paths. It reads the bootstrap
from the approved Git commit; the bootstrap reads the release loop and card
helper from that same commit, verifies their Git blob identities, and runs them
from a private unpredictable temporary directory with mode 0700. No mutable
`.release/runner` cache or checksum manifest is trusted or retained. The
known-good tree may predate all release helpers without breaking recovery.

This trusts the local Git object database and the original printed command;
it does not defend against a same-user process altering PATH/interpreter/shell
rc or the command invocation. Run the original command, not a checkout copy.

Rollback uses `git commit-tree` with deterministic metadata, the exact released
parent and the known-good tree. It never changes the working tree, index or
local branch: there is no `read-tree`, branch-move or tree-replacement crash
window. It pushes only the resulting commit, without force.

### The journal

State keyed by the released SHA progresses through `committed`, `pushed`, and
`proved`. An interruption after commit creation but before journalling produces
the same commit on rerun. An interruption after push but before recording it is
reconciled against remote main before any retry. A recorded commit is checked
against its complete parent list and expected tree before reuse. A rollback
already on main is reused even if the local journal was lost; an unexpected
remote commit blocks instead. Actual local bare-Git tests cover these cases,
including a known-good tree without the helpers and an untouched checkout.

Proof is the same exact-SHA pipeline as a release — CI `Tests`, the Mac Mini
deploy, runtime identity — keyed to the rollback commit. **No live journey runs
during a rollback**; it restores a tree that already passed its own proof and
must never be the reason a message reaches a real doctor.

Success is `FINAL_RELEASE_STATE=rolled-back`, and only after the runtime proves
the rollback commit. Everything short of that reports the truth: if CI or deploy
fails, `main` is the rollback commit and the rollback is not live; if the deploy
puts the released code back, the receipt says `main` is the rollback commit while
the runtime is still the released SHA, and that nothing on the Mac Mini has been
reverted. A created or pushed commit is never itself called a rollback.

## Manual closure

```
<the printed pinned bootstrap command> -- --surface telegram --mode attest \
  --risk telegram --approved <sha>:<card-digest> \
  --result pass|fail --note "<one line, no secrets>"
```

Attest is available only when the card itself freezes `proof_mode = manual`; an
automated card can never downgrade after approval. It re-verifies the card, the
SHA, surface, risk and `HEAD == origin/main == SHA`, then proves the automated
half of the release for itself — the exact-SHA `push` Tests run, the
`workflow_run` deploy that started after that Tests run completed, and runtime
identity — before recording anything. It cannot assume a `ship` run happened, so
it does not report `live` on the strength of one. A failed Tests or deploy run is
`blocked`; a missing or still-running one is `proof-pending`, and no attestation
is written in either case.

Only then does it write a local attestation, and it changes nothing external. Its
output says `manual proof attested by operator`; it never claims the automated
journey ran. It refuses internal risk, which has no manual journey, and refuses
to stand in for every automated card, whether readiness is currently complete or
not.

## What this standard is not

It is not a second orchestration system. There is one release script, one card
store, and one local state directory. Pull requests remain optional and are not
part of the default flow.
