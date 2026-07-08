# Resume Brief — portfolio-guru

Generated: 2026-07-07T23:32:26+00:00
Status: stale_or_needs_review

## Where we left off
- Repo: /Users/moeedahmed/projects/portfolio-guru
- Branch: main
- Last commit: 64e7f44 2026-07-08 Reconnect WhatsApp sidecar after first pairing
- Uncommitted changes: yes

## Immediate read before restarting
1. `AGENTS.md` for durable repo context
2. Product hub Status + Brief in Notion only for human-facing status, positioning, launch, or risk context
3. `TASK.md` for active sprint state, if present
4. This resume brief for drift warnings and repo state

## Warnings
- Repo has uncommitted local changes — next agent must review and either commit, revert, or summarise them before building from this checkout

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
- 64e7f44 2026-07-08 Reconnect WhatsApp sidecar after first pairing
- 01763db 2026-07-07 Add WhatsApp QR image handoff
- f16e8d8 2026-07-07 Pin WhatsApp Web version and browser identity to fix 405 before QR
- a99d1ca 2026-07-07 Drop non-user WhatsApp frames instead of crashing the relay
- 51ebe40 2026-07-07 Add isolated Baileys WhatsApp linked-device sidecar
- 57e8b25 2026-07-07 Add runnable WhatsApp linked-device connector shell
- 3becd45 2026-07-07 Add direct WhatsApp linked-device connector boundary
- ca225bd 2026-07-07 Run Kaizen browser automation quietly

## Uncommitted change summary
- M docs/continuity/RESUME_BRIEF.md

## Repo context snapshot
### AGENTS.md — present, 1 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Safety
- ## Supported Forms

### CLAUDE.md — present, 1 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Safety
- ## Supported Forms

### TASK.md — present, 10 days old
Key headings:
- # Active Task — Hermes Hackathon Production Cut
- ## Hackathon Objective
- ## Locked Product Decision
- ## Sprint 1 — Onboarding And Trust Surface
- ## Sprint 2 — Activation And Failure Telemetry
- ## Sprint 3 — Dashboard, Portfolio Health, And Stripe Proof
- ## Sprint 4 — Hackathon Business-Agent Ledger
- ## Sprint 4b — Demo / Rehearsal Kit
- ## Demo Case And Recording Plan
- ## Day-By-Day Plan
- ## Hackathon Done Criteria
- # Previous Active Task — Kaizen Mapping Sprint

### docs/plan.md — present, 0 days old
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
- ## Channel Boundary — Dedicated Portfolio Guru WhatsApp Connector
- ## Conversation States
- ## Flow 1 — First-Time User
- ## Flow 2 — Core Filing (Happy Path)
- ## Flow 2A — Assess Ticket (Read-Only Mapping / Planned)
- ## Flow 2B — Portfolio Readiness / ARCP Health (Planned)
- ## Flow 3 — Edit Before Filing
- ## Flow 4 — Edit Previously Filed Draft (v2.1 — NOT YET BUILT)
- ## Flow 5 — Reset / Recovery
- ## Form Type Decision Rules
- ## Key Capabilities Selection Rules

## Recommended restart path
Refresh the product hub/Brief and repo context before a new build sprint.

If this product has been idle for weeks, do not start implementation from memory. Refresh the product hub Status/Brief first, then create or update `TASK.md` for the next sprint.
