# Draft-to-review verification and release handoff

26 September 2026. Local candidate only; not deployed or verified live.

## Change

Doctor-supplied corrections and reflection replies now remain part of the case
evidence before reassessment. Both CBDData and FormDraft support direct edits;
cleared CBD fields are normalised and validated before replacing stored data.
The legacy edit route uses the same missing-fact checks as other draft replies.

A changed draft invalidates its previous stamped Save control and pending
attachment, curriculum and retry continuations. Legitimate failed-save retries
remain available. Missing doctor-owned facts stay blank and saving remains an
explicit, draft-only action.

Offline transcript verification now captures message edits and handler errors,
follows the existing Read/Attach choices, and requires every step plus the
intended final draft and Save/Cancel controls. Fault-injection tests prove that
failed drafting and exceptions after a progress reply cannot pass the gate.

## Verification

Baseline: `112d356a9d531e4fac86f3bae852d396daa634e8`.
Candidate branch: `codex/draft-review-20260926`.

The existing `bash scripts/verify_release.sh` completed with exit 0:

```text
915 passed, 146 warnings in 21.53s
886 passed, 1243 warnings in 131.87s (0:02:11)
verify:changed PASSED.
3 snapshots passed.
3885 passed, 3 skipped, 18 deselected, 1439 warnings in 343.81s (0:05:43)
verify:release PASSED.
```

All 10 golden-case transcripts passed. Three additional real-handler journeys
covered incomplete notes, reflection corrections, repeated detail, encrypted
state restoration, stale approval rejection, cancellation and a mocked save.
Independent review found no remaining concrete issue after the repairs.

The required `bash scripts/preflight.sh` also passed before committing: 3,885
passed, 3 skipped, 14 deselected, and 3 snapshots passed. Its read-only fetch
confirmed that `origin/main` still matched the baseline above. The different
deselection count comes from preflight explicitly excluding the Kaizen
integration file in addition to the normal offline marker exclusions.

Tests used the existing development virtualenv, disabled dotenv, blocked
external provider boundaries and temporary stores. Sequential runs with separate
short HOME/GNUPGHOME paths resolved earlier test-environment failures. Three
pre-existing Hermes integration checks remain unavailable in this test environment.
No test threshold, semantic coverage obligation or safety assertion was weakened.

Detailed local evidence is retained in
`.artifacts/draft-review-mission/verification-report.md` and
`.artifacts/draft-review-mission/resume/`. These ignored artifacts include logs,
transcripts, source hashes, failure reproductions and the independent review.
They are not required to run the committed regression tests.

## Remaining release boundary

No push, PR, deployment, live Telegram message, paid provider call, Kaizen save,
credential/provider change, service restart or production-data change was made.
The pinned runtime is unchanged. Encrypted state round-trip tests are not proof
of a real process restart; mocked extraction and filing are not live proof.

This handoff is not a release approval or an exact-SHA release card. If release
is later authorised, use the existing release procedure in
`docs/release-standard.md` and `docs/verification-contract.md`: reconcile the
current target and remote state, prepare the exact candidate, obtain its release
approval, and verify the approved runtime and focused journey. Preserve the
separate boundary against filing anything on Kaizen unless explicitly authorised.

The development work can be closed after the local commit; live activation is
a separate decision.

## Subsequent release preparation

Read-only inspection found the live beta service in `user/501`, with checkout,
runtime and process identity matching the baseline. The old release verifier
and deploy script assumed `gui/501`. The candidate now discovers exactly one
existing service owner, preserves that domain during deployment and rollback,
and refuses missing/ambiguous ownership or inspection errors before mutation.
Owner discovery permits a stopped registered service; runtime proof still
requires a live, matching PID and the exact checkout/runtime SHA.

Fault injection also reproduced two deployment failure paths that escaped
rollback: a failed runtime-verification command was followed by a success
message, and bootstrap/query failures exited before reaching the rollback
block. These failures now explicitly enter the existing rollback path.
The focused deployment/runtime suite passed 28 tests and independent review
found no remaining concrete blocker. Logs are under the local evidence
directory's `release/` subdirectory. This section records release preparation,
not a deployment or a new approval.

The repaired candidate also passed the complete offline release gate:
`verify:changed PASSED`, then 3,901 passed, 3 skipped, 18 deselected,
3 snapshots passed, and `verify:release PASSED`.
