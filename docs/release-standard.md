# Portfolio Guru — Solo-Founder Release Standard

One card, one approval, one release.

Portfolio Guru has one operator. Asking him to approve the push, then the CI
wait, then the deploy, then the runtime check, then the live journey, then the
resume, spends his attention on mechanics and teaches him to approve without
reading. This standard collapses that into a single decision he can actually
make: he reads one card, approves one SHA, and everything mechanical inside
that unchanged envelope proceeds without asking again.

It removes prompts, not boundaries. Nothing here weakens a live-send,
credential, spend or supervisor-facing guard.

## The card

`scripts/release_loop.sh --mode prepare` runs the offline gates and, only if the
tree is release-ready, writes one card under the gitignored `.release/`, keyed
by the full `HEAD` SHA. The card records:

| Field                   | Why it is on the card                                            |
| ----------------------- | ---------------------------------------------------------------- |
| `schema_version`, `sha` | the exact commit the approval names                              |
| `surface`, `risk`       | what class of release this is                                    |
| `effect`                | one plain line: what changes for a doctor                        |
| `proof_mode`            | `automated` or `manual` — frozen here, not decided at ship time  |
| `live_target`           | the exact bot a live proof may touch, or null                    |
| `known_good_sha`        | the rollback target, filled in at ship after it is verified live |
| `exclusions`            | what this approval never covers                                  |
| `created_at`            | when the card was prepared                                       |

Cards carry no credentials, tokens, private content or patient data.
`scripts/release_card.py` refuses multi-line, oversized, control-character and
credential-shaped text before anything is written.

## What one approval covers

Approving the card's SHA — `--approved <40hex>` — covers, once:

- the push of that exact SHA to `main`;
- the CI `Tests` run and the Mac Mini deploy bound to it;
- exact checkout and runtime identity;
- the named proof on the card;
- an unchanged proof resume;
- bounded targeted rollback to the named known-good SHA if that proof fails.

It covers nothing else. The card's exclusions are explicit: supervisor
submission, credential or secret change, schema or data migration, pricing or
spend change, any new recipient or public announcement, history rewrite or force
push, and any SHA other than the one named.

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
  and refuses to run the live child. It does not quietly become something else.
- Escalating manual → automated needs a new card and a new approval.

The live guard `TELEGRAM_LIVE_APPROVED` is passed per-command to the live child
only and is never exported, so nothing else in the run inherits it. The direct
guard inside `scripts/telegram_bot_qa.sh` is unchanged and still refuses a live
run on its own.

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

## Manual closure

```
scripts/release_loop.sh --surface telegram --mode attest --risk telegram \
  --approved <40hex> --result pass|fail --note "<one line, no secrets>"
```

Attest re-verifies the card, the SHA, surface, risk, `HEAD == origin/main == SHA`
and runtime identity, then writes a local attestation. It changes nothing
external. Its output says `manual proof attested by operator`; it never claims
the automated journey ran. It refuses internal risk, which has no manual journey,
and refuses to stand in for an automated card whose readiness is still complete.

## What this standard is not

It is not a second orchestration system. There is one release script, one card
store, and one local state directory. Pull requests remain optional and are not
part of the default flow.
