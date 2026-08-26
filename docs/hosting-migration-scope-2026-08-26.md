# Hosting migration — scope, not a decision

**Date:** 2026-08-26 · **Status:** Scoping · **Follows:** `docs/adr-hosting-2026-08.md`

The ADR of 18 Aug decided to stay on the Mac Mini through beta and named five
conditions that would change that. This document exists because one of them has
fired, and because the work done since has changed the cost of moving.

## Why now

**Trigger 4 — "legal/governance requires an availability commitment or processor
structure that home hosting cannot evidence."**

Three findings pushed it over:

1. The machine holds special-category health data and, more importantly, a
   plaintext token reaching 181 secrets — including the key that decrypts every
   doctor's Kaizen login and the service-role key to the London database. After
   the Supabase move, the Mac stops being where the data is and becomes where
   the _keys_ are. That is a worse thing to have in a house.
2. Encryption at rest and unattended restart are mutually exclusive on macOS.
   FileVault disables auto-login by design, so a power cut becomes a manual
   recovery requiring physical access. A hosted machine has neither problem.
3. The privacy policy and DPIA have to be true before a solicitor signs them.
   "Runs on a Mac in the founder's home, no availability commitment, recovery
   depends on one person" is defensible for a dogfood and awkward for a paid
   beta.

## The gating unknown — answer this before anything else

**Does Kaizen block datacentre IPs?** Many NHS-adjacent systems do. If it does,
the whole shape changes: UK residential egress, or a hybrid where filing stays
on a home machine and everything else moves. Every estimate below is void in
that case.

Cost to find out: one afternoon, a UK VPS by the hour, and a test Kaizen
account. Do not commit to a migration before this is answered.

## What the last week changed

The ADR estimated 2–4 weeks, and most of that was moving state. That work is now
done or nearly done independently:

| Concern                  | Aug 18                                     | Now                            |
| ------------------------ | ------------------------------------------ | ------------------------------ |
| Where the data lives     | SQLite on one disk                         | London Supabase (schema ready) |
| Where secrets live       | BWS, one token, 181 secrets                | BWS, scoping to 18 in progress |
| Backups                  | Local + GCS, silently broken for 53 nights | GCS `europe-west2`, verified   |
| Clinical content at rest | Plaintext, several stores                  | None                           |

What remains to move is the _runtime_, not the data. That is a materially
smaller job than the ADR assumed — call it **1–2 weeks**, contingent on the
Kaizen IP answer.

## What actually has to move

- **The bot process.** Long-lived Telegram polling. Straightforward.
- **Chrome over CDP.** A container sidecar exposing CDP on the internal network.
  `KAIZEN_CDP_URL` is already the override seam, so this is configuration rather
  than a rewrite — the ADR was right about that.
- **The Stripe webhook server** on port 8099, plus its public endpoint.
- **Scheduled jobs.** Backup, retention, heartbeat, sign-off chase — currently
  launchd agents, and the Healthchecks.io monitors must follow them.
- **The deploy pipeline.** This is the real cost. `deploy_mac.sh` does SHA-bound
  fast-forward-only deploys, post-deploy smoke, and automatic rollback. A cloud
  deploy that does not rebuild those guarantees is a downgrade wearing a cloud
  badge.

## What does not have to move

Data, secrets and backups are already off-machine or going there. Kaizen is
third-party. Telegram is third-party.

## Shape

Per the ADR, unchanged: **one always-on VM or container host, in a UK region**,
running the bot plus a Chrome sidecar. Not scale-to-zero — this is a long-lived
process with a stateful browser. Not a hosted third-party browser service, ever:
doctors' RCEM credentials must not transit an external processor.

UK regions that satisfy the residency story: GCP `europe-west2`, AWS
`eu-west-2`, Azure UK South, or a London VPS from Linode/DigitalOcean. Vertex AI
and the Supabase project are both already in London, so co-locating is the
obvious default.

Rough running cost for 4–8 GB with Chrome: **£25–50/month**, against £0 today.

## What it buys

Automatic restart after power loss. Disk encryption as the provider's problem.
Workload identity instead of a long-lived token on disk — the only real fix for
the secrets exposure. A named processor with a DPA to put in the ROPA. An
availability story that survives a question from a DPO.

## What it costs

Money, the pipeline rebuild, the Kaizen IP risk, and losing the convenience of
a browser you can watch on a screen.

## Open questions for Moeed

1. Does Kaizen block datacentre IPs? (blocking — answer first)
2. Managed container platform or a plain VM? A VM is closer to what exists and
   easier to reason about; a platform gives restart and health-checking for free.
3. Does the Mac Mini stay as a warm standby, or get repurposed?
4. Before or after the solicitor review? The legal documents have to describe
   whichever architecture is live when they are signed.
