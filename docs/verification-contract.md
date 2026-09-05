# Portfolio Guru Verification Contract

Use this contract for every change. The existing offline gates stay authoritative; this document defines the extra evidence needed to prove the doctor-facing workflow.

## Risk classes

### 1. Tiny or internal

Examples: copy-neutral documentation, comments, or a tightly isolated helper.

- Run the smallest relevant pytest file or static check.
- Record the command and exit result.
- Independent review and product-surface evidence are optional unless a shared, filing, privacy, billing, or safety path is touched.
- For a shipped internal release, exact-SHA Tests, CI deploy, and Mac Mini runtime identity are still mandatory; no manual Telegram journey is required.

### 2. Meaningful user-facing

Examples: Telegram conversation state, case capture, form recommendation, draft preview, consent, billing, attachments, or filing behaviour.

- Run focused tests while iterating.
- Run `bash scripts/verify_changed.sh` as the completion gate.
- Exercise the affected real product path:
  - Telegram journeys: run `bash scripts/telegram_qa_offline.sh` to drive the real handler stack and retain its transcript.
  - Kaizen mapping or filing changes: exercise the deterministic Playwright/CDP path against the intended form. Live Kaizen access is an approval boundary; without approval, report that proof as pending rather than substituting browser-use or a mock.
- A verifier other than the builder must inspect the diff and the product-path evidence before completion.

### 3. Visual

Examples: Telegram message layout, button hierarchy, preview formatting, or Kaizen field placement.

- Meet the meaningful user-facing requirements.
- Capture screenshots from the affected real surface. Snapshot tests and transcripts remain useful regression proof but do not replace a screenshot when visual presentation changed.
- Private Telegram or Kaizen evidence must avoid patient data and credentials. Any live send or authenticated third-party action requires explicit approval.

### 4. Multi-step interaction

Examples: case capture through approval and draft save, or an attachment journey spanning several bot states.

- Meet the visual requirements when presentation changed.
- Capture a short video only when the complete state transition cannot be proven clearly with screenshots and transcripts.
- Never record credentials, patient data, or supervisor submission; Portfolio Guru remains draft-save only.

## Release boundary

`bash scripts/verify_release.sh` is the full offline release gate. Beyond it, closure runs on **one card and one approval** — see `docs/release-standard.md` for the standard itself. `scripts/release_loop.sh` requires `--risk internal|telegram|broad` for every mode:

1. `--mode prepare` passes offline verification and writes one card under the gitignored `.release/`, keyed by the full HEAD SHA and naming surface, risk, the one-line effect, the frozen proof mode, the exact live target, the fixed exclusions and the rollback target. It prints the card and one exact ship command, and mutates nothing;
2. commit, then give **one approval that names that exact SHA** — `--approved <40hex>`. It must equal the card SHA and current `HEAD`, and the CLI surface/risk must equal the card's. That single approval covers the push, CI, deploy, runtime identity, the named proof, an unchanged resume, and bounded targeted rollback to the named known-good SHA if the proof fails. Mechanical steps inside that envelope must not ask again;
3. before main moves, verify the currently live runtime against current `origin/main` and record that known-good SHA on the card. If it cannot be verified, block before any mutation;
4. push one full SHA to main and prove `HEAD == origin/main == pushed SHA`. The exact-SHA push is the remote copy; no second backup push is taken;
5. require a successful GitHub `Tests` workflow for that SHA with event `push`;
6. require a successful `Deploy Mac Mini` workflow for that SHA with event `workflow_run`, created/started no earlier than the selected Tests completion/update time;
7. require the Mac Mini checkout and runtime identity to equal that full SHA exactly;
8. collect risk proof in the mode frozen on the card: `internal` needs no manual journey, `telegram` needs the guarded focused text-case journey, and `broad` needs all 15 interactive dogfood checks PASS with Fail=0 and Skip=0.

Telegram proof is `automated` only when the Telethon session, API id/hash, `TELEGRAM_BOT_USERNAME` and the live allowlist all name the exact card target; otherwise the card is `manual`. A manual card never sends automatically, even if credentials appear afterwards, and an automated card whose readiness disappears reports pending rather than running the live child. The live guard is supplied per-command to the live child only and is never exported, and the offline gates run with fixed throwaway credentials that the deploy, runtime and live children never inherit. Manual proof closes with `--mode attest --approved <sha> --result pass|fail --note "<one line>"`, which re-verifies card, SHA, refs and runtime, records a local attestation, changes nothing external, and reports `manual proof attested by operator` — never automated proof.

Manual workflow dispatch is not evidence for an ordinary ship. A completed Tests/deploy or live-journey failure is `blocked` with exit 1, and the loop then prints the verified prior known-good SHA with a rollback instruction while stating that the released SHA stays live until a targeted rollback is actually run. Missing, running, timeout, inaccessible runtime, or unavailable protected live proof is `proof-pending` with exit 4; on a manual card that message names the missing manual proof, not a missing approval. After a pushed run becomes pending, the loop prints an exact secret-free `--release-sha <40hex>` resume command. Resume reuses the same approval and card, requires `HEAD == origin/main == supplied SHA`, re-verifies runtime, performs proof stages only, and never attempts a duplicate push. Pull requests are optional and are not part of the default flow. The ship approval does not weaken live-send guards: Telegram, Vertex AI, Kaizen, Stripe, and supervisor-facing actions remain protected.

## Completion record

The task or handoff must state: risk class, commands run, product path exercised, evidence location, independent-verifier verdict when required, and any proof still pending. Offline success must not be described as live proof.
