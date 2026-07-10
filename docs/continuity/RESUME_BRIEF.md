# Resume Brief — portfolio-guru

Generated: 2026-07-10T16:31:06+00:00
Status: ready

## Where we left off
- Repo: /Users/moeedahmed/projects/portfolio-guru
- Branch: main
- Last commit: 5f1a602 2026-07-10 Guard Telegram workflow state from free text
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
- 5f1a602 2026-07-10 Guard Telegram workflow state from free text
- d8a7f43 2026-07-09 Fix post-save incomplete-case routing
- 0a3b9b1 2026-07-09 Polish Telegram draft quality gates
- 8fbc5ab 2026-07-09 Focus Portfolio Guru on Telegram launch proof
- 82be937 2026-07-09 Continue WhatsApp recommendations to preview
- de7e06c 2026-07-09 Deduplicate Telegram setup callbacks
- b0caaf8 2026-07-09 Align Portfolio Guru Hermes WhatsApp route
- 7425081 2026-07-09 Improve Portfolio Guru WhatsApp workflow parity

## Repo context snapshot
### AGENTS.md — present, 3 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Safety
- ## Supported Forms

### CLAUDE.md — present, 3 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Safety
- ## Supported Forms

### TASK.md — present, 0 days old
Key headings:
- # Active Task — 14-Day Telegram Launch-Proof Sprint
- ## Decision
- ## Commercial Boundary
- ## Goal
- ## Scope This Sprint
- ## Parked
- ## Guardrails
- ## Current Build Actions
- ## Proof Before Re-Engagement

### docs/plan.md — present, 3 days old
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

### WORKFLOWS.md — present, 0 days old
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
