# Portfolio Guru Verification Contract

Use this contract for every change. The existing offline gates stay authoritative; this document defines the extra evidence needed to prove the doctor-facing workflow.

## Risk classes

### 1. Tiny or internal

Examples: copy-neutral documentation, comments, or a tightly isolated helper.

- Run the smallest relevant pytest file or static check.
- Record the command and exit result.
- Independent review and product-surface evidence are optional unless a shared, filing, privacy, billing, or safety path is touched.

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

`bash scripts/verify_release.sh` is the full offline release gate. Live Telegram, Vertex AI, Kaizen, Stripe, push, deployment, and supervisor-facing actions remain separate approval-gated proof.

## Completion record

The task or handoff must state: risk class, commands run, product path exercised, evidence location, independent-verifier verdict when required, and any proof still pending. Offline success must not be described as live proof.
