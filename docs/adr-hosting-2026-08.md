# ADR: Stay self-hosted on the Mac Mini through paid beta

**Date:** 2026-08-18 · **Status:** Accepted · **Decider:** Moeed

## Decision

Keep Portfolio Guru on the Mac Mini with the GitHub Actions self-hosted runner.
Harden it rather than migrate. Revisit only when one of the trigger conditions
below fires.

## Context

Two co-founders are joining and a wider invite-only paid beta is approaching.
The question was whether "production-ready" requires moving to cloud hosting
(VPS / Cloud Run / similar) or hardening what exists.

The system is a long-lived Telegram polling process, a Stripe webhook server, a
headless Chrome driven over CDP for Kaizen form filling, encrypted SQLite, and
EU Vertex AI for extraction.

## Why we stayed

**The browser dependency is not the anchor it appears to be.** Filing does not
rely on a hand-logged-in Chrome profile. `kaizen_form_filer.py` opens a fresh
isolated browser context per filing, authenticates with that user's own stored
credentials, and caches session state per user. The Mac's Chrome is just a
generic browser at a URL, and `KAIZEN_CDP_URL` is the override seam. Moving it
means pointing that URL at a containerized Chrome, not rewriting the automation.
So the browser did not decide this either way.

**What actually decided it:**

- Migration costs 2–4 weeks and discards `deploy_mac.sh`, which does SHA-bound
  fast-forward-only deploys, post-deploy smoke checks, and automatic rollback.
  A cloud deploy must rebuild equivalent guarantees or the trade is a hardened
  pipeline for a naive one.
- The architecture tolerates downtime unusually well: Telegram polling loses no
  messages, Stripe retries webhooks for days, filed evidence lives on Kaizen.
- Beta scale is tiny. The 16GB M4 has ample headroom.
- The riskiest surface is Kaizen selector brittleness, which cloud does nothing
  to fix.
- Abandoned Railway and Render configs in the repo show a cloud path was already
  started and dropped once.

**What we accepted:** availability depends on one machine in one home. Power,
ISP, and hardware failure are unmitigated, and recovery currently depends on
Moeed. Judged acceptable for beta _given_ proven backups and a written runbook —
both of which were the price of this decision, not optional extras.

## Trigger conditions — migrate when any fires

1. Paying users exceed ~30–50, or filings queue against the concurrency cap.
2. Two or more user-visible outages in a month traceable to the machine or home.
3. An outage waits more than ~12h because only Moeed can act.
4. Legal/governance requires an availability commitment or processor structure
   that home hosting cannot evidence.
5. The Mac Mini is needed elsewhere, or shows hardware fault.

**Gating prerequisite:** confirm Kaizen/RCEM does not block datacenter IPs
before committing to any migration. Many NHS-adjacent systems do. One afternoon
with a UK VPS and a test account answers it. If Kaizen blocks datacenter IPs,
the cloud option changes shape entirely (UK residential egress or hybrid) and
the cost estimates below are void.

## If we migrate: the shape

- **Right shape:** one always-on VM, or one container host running the bot plus
  a Chrome sidecar exposing CDP on the container network.
- **Wrong shape:** Cloud Run, Lambda, or anything scale-to-zero. This is a
  long-lived process with a stateful browser and local files; serverless wants
  short stateless requests. The existing `render.yaml` / `railway.json` also
  encode a worse secrets model (raw env vars instead of BWS) and a different
  browser model (Playwright-bundled Chromium instead of CDP attach). Treat them
  as superseded by this ADR, not as a starting point.
- **Never:** a hosted third-party browser service. Doctors' RCEM credentials
  must not transit an external processor.

**Migration readiness checklist** (state as of 2026-08-18):

| Item                                                         | Status                             |
| ------------------------------------------------------------ | ---------------------------------- |
| `KAIZEN_CDP_URL` honoured everywhere (no hardcoded literals) | Done                               |
| Off-device backup proven restorable                          | Done                               |
| DR runbook written                                           | Done (`docs/disaster-recovery.md`) |
| macOS-isms in shell (`stat -f%z`, `lsof`, `launchctl`)       | Not ported                         |
| launchd → systemd/container restart policy                   | Not ported                         |
| BWS secret delivery on a non-Mac host                        | Not designed                       |
| `deploy_mac.sh` smoke + rollback equivalent                  | Not designed                       |
| Kaizen datacenter-IP tolerance                               | **Unknown — probe first**          |

## Cost

- **Now:** roughly £2–6/month in electricity; the Mac Mini is a sunk asset.
  Vertex, Supabase, BWS and Stripe costs are identical under either option.
- **If migrated:** roughly £15–50/month lean, £50–120/month on a hyperscaler,
  plus 2–4 weeks of labour that dwarfs a year of hosting fees.

All figures are unverified estimates. **Check live vendor pricing, and the
actual GCP and Supabase bills, before quoting any number to co-founders.**

## Consequences

- Availability is not contractually defensible. Do not promise uptime to paying
  users beyond what one home machine can deliver.
- Backup and recovery must stay proven, not assumed. Re-run the restore drill
  after any change to backup, secrets, or the data layer.
- The seam work makes migration a matter of weeks, not a rewrite, whenever a
  trigger fires.

## Related

- `docs/disaster-recovery.md` — what to do when it is down
- `scripts/restore_db.md` — restore procedure and the 2026-06/08 backup incident
- `docs/MAC_MINI_DEPLOYMENT.md` — how the current deploy works
