# Resume Brief — portfolio-guru

Generated: 2026-07-15T00:04:36+00:00
Status: ready

## Where we left off
- Repo: /Users/moeedahmed/projects/portfolio-guru
- Branch: main
- Last commit: 6219873 2026-07-12 feat(beta): make launch-proof metrics trustworthy
- Uncommitted changes: no

## Immediate read before restarting
1. `AGENTS.md` for durable repo context
2. Product hub Status + Brief in Notion only for human-facing status, positioning, launch, or risk context
3. `TASK.md` for active sprint state, if present
4. This resume brief for drift warnings and repo state

## Practical AGENTS.md check
- Useful for coding-agent/ACP work: yes
- No missing practical sections

## Release-loop readiness
- Verdict: present — deterministic closure entrypoint detected
- Scripts found: scripts/release_loop.sh
- Documented references: none
- Surface hints: telegram_bot: yes
- Live/deploy signals: railway.json, render.yaml, .github/workflows/deploy-mac.yml

## Recent commits
- 6219873 2026-07-12 feat(beta): make launch-proof metrics trustworthy
- 06886bd 2026-07-12 ci(verify): fold verify:changed into existing Tests workflow PR gate
- b2477d7 2026-07-12 docs(agents): require verify:changed/verify:release proof before done/release-ready
- 973cac7 2026-07-12 docs(verify): add repo-specific rollback playbook
- 29f3bd7 2026-07-12 feat(verify): add verify:changed/verify:release change-safety gate
- de81b48 2026-07-10 Polish Telegram response copy, templates, and mobile formatting
- 2becf64 2026-07-10 Refresh Portfolio Guru continuity brief
- 5f1a602 2026-07-10 Guard Telegram workflow state from free text

## Repo context snapshot
### AGENTS.md — present, 2 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Telemetry Provenance
- ## Safety
- ## Supported Forms

### CLAUDE.md — present, 2 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Telemetry Provenance
- ## Safety
- ## Supported Forms

### TASK.md — present, 2 days old
Key headings:
- # Active Task — 14-Day Telegram Launch-Proof Sprint
- ## 2026-07-12 — Beta narrowing / telemetry-provenance hardening
- ## Decision
- ## Commercial Boundary
- ## Goal
- ## Scope This Sprint
- ## Parked
- ## Guardrails
- ## Current Build Actions
- ## Proof Before Re-Engagement

### docs/plan.md — present, 7 days old
Key headings:
- # Portfolio Guru - Conversational Router Plan
- ## Goal
- ## Product Decision
- ## Channel Architecture Decision
- ## Architecture
- ### Layer 1 - Natural conversation intake
- ### Layer 2 - Intent router
- ### Layer 3 - Existing deterministic workflows
- ### Layer 4 - Safety and recovery
- ## Implementation Phases
- ### Phase 0 - Checkpoint and branch
- ### Phase 1 - Router contract and tests

### WORKFLOWS.md — present, 4 days old
Key headings:
- # Portfolio Guru — Agent Workflow Reference
- ## Current Product Focus — Telegram Launch Proof
- ## Channel Boundary — Dedicated Portfolio Guru WhatsApp Connector
- ### First-contact parity (WhatsApp opens like the Telegram bot)
- ## Conversation States
- ## Telegram Callback And State Map
- ### Controlled flexibility for free text
- ## Flow 1 — First-Time User
- ## Flow 2 — Core Filing (Happy Path)
- ## Flow 2A — Assess Ticket (Read-Only Mapping / Planned)
- ## Flow 2B — Portfolio Readiness / ARCP Health (Planned)
- ## Flow 3 — Edit Before Filing

## Recommended restart path
Continuity context looks sufficient; proceed with normal product-docs workflow.

If this product has been idle for weeks, do not start implementation from memory. Refresh the product hub Status/Brief first, then create or update `TASK.md` for the next sprint.
