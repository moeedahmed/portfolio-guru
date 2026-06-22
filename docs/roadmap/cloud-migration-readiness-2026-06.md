# Portfolio Guru Cloud Migration Readiness

Status: blocked until filing is reliable.

Portfolio Guru should stay on the Mac Mini for build and test work until Kaizen draft filing is boringly reliable. Cloud migration is the final shipping step, not the current product move.

## Current Decision

Hold the cloud move.

The product risk is not where the bot runs. The product risk is whether a doctor can trust Portfolio Guru to create the right Kaizen draft every time, recover cleanly when Kaizen changes, and never save without approval.

## Readiness Criteria

Cloud migration can be reconsidered only when all of this is true:

- Priority forms have a green filing reliability matrix: `CBD`, `DOPS`, `MINI_CEX`, `ACAT`, `QIAT`, `TEACH`, `REFLECT_LOG`, `PROC_LOG`.
- No wrong or non-canonical form codes are emitted or accepted as product forms: especially no `CEX`, `CDD`, or `ALP`.
- Draft saves are approval-gated. No path submits, signs, approves, or saves without an explicit user action.
- Date handling is proven as UK `d/m/yyyy`.
- Failures produce useful user-facing recovery copy, not silent crashes or partial weirdness.
- The same representative pack passes on the local Mac Mini path and the future cloud/headless Chrome path.
- Hermes remains shadow/advisory until it renders engine-backed options cleanly and does not drift from deterministic form taxonomy.

## Local Gate

Run the offline matrix:

```bash
cd backend
venv/bin/python3 -m pytest tests/test_filing_reliability_matrix.py -q
venv/bin/python3 -m filing_reliability_matrix --json
```

This gate proves local routing, schema, form-code, draft-only, UK-date, and deterministic-path invariants. It does not prove live Kaizen reliability.

## Live Gate

Before cloud migration, run controlled live draft-save proof for each priority form or a deliberately smaller first-shipping subset agreed by the orchestrator:

- synthetic anonymised case
- draft saved visibly in Kaizen
- no supervisor submission
- draft deleted or cleaned up
- transcript and result recorded
- failure path checked where relevant

Only after the local and live gates are green should work begin on moving secrets, data, Chrome, CI, and launchd-equivalent runtime ownership into cloud infrastructure.
